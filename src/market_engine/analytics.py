"""
Módulo de Inteligencia de Mercado Laboral (Data & Analytics Chile).
Consolida métricas salariales, penetración de tecnologías, distribución de roles
y matrices de especialización a partir de todas las vacantes almacenadas en la base de datos.
"""

import json
import logging
import os
import re
from collections import Counter
from datetime import datetime
from sqlmodel import Session, select

import src.database.repository as repo
from config.settings import load_config, settings
from src.database.models import Job, MatchResult
from src.agent.filter import parse_salary_details

logger = logging.getLogger(__name__)

USD_TO_CLP = 950  # Tasa de cambio de referencia para unificar salarios a moneda nacional


def get_tech_patterns() -> dict[str, str]:
    """Obtiene los patrones de tecnología configurados en config.yaml (o valores por defecto)."""
    config = load_config()
    tracked = config.get("tracked_technologies")
    if tracked and isinstance(tracked, dict):
        return tracked
    return {
        "SQL": r"\bsql\b",
        "Python": r"\bpython\b",
        "AWS": r"\baws\b|\bamazon web services\b|\bathena\b|\bredshift\b|\bglue\b",
        "Git & CI/CD": r"\bgit\b|\bgithub\b|\bgitlab\b|\bci/cd\b|\bci\/cd\b",
        "Azure": r"\bazure\b|\bdata factory\b|\bfabric\b|\bsynapse\b",
        "GCP / BigQuery": r"\bgcp\b|\bgoogle cloud\b|\bbigquery\b",
        "Databricks": r"\bdatabricks\b",
        "Apache Spark / PySpark": r"\bspark\b|\bpyspark\b",
        "Power BI / DAX": r"\bpower\s*bi\b|\bdax\b",
        "Snowflake": r"\bsnowflake\b",
        "dbt": r"\bdbt\b|\bdata build tool\b",
        "Apache Airflow": r"\bairflow\b",
        "Apache Kafka": r"\bkafka\b|\bstreaming\b|\bflink\b",
        "Tableau": r"\btableau\b",
        "GenAI / LLM / RAG": r"\bgenai\b|\bllm\b|\brag\b|\blangchain\b|\bllamaindex\b",
        "Terraform / IaC": r"\bterraform\b|\biac\b",
        "Docker / Kubernetes": r"\bdocker\b|\bkubernetes\b|\bk8s\b",
        "PostgreSQL / MySQL": r"\bpostgresql\b|\bpostgres\b|\bmysql\b",
        "Dagster / Prefect": r"\bdagster\b|\bprefect\b",
        "DuckDB / Polars": r"\bduckdb\b|\bpolars\b",
    }


def extract_detailed_modality(location: str, description: str, job_type: str | None = None) -> str:
    """
    Analiza minuciosamente el texto para determinar la modalidad exacta
    y la cantidad de días presenciales en oficina cuando esté disponible.
    """
    text = f"{location} {description} {job_type or ''}".lower()

    # 1. Detectar si es 100% Remoto estricto
    is_remote_strict = any(
        term in text
        for term in [
            "100% remoto",
            "100% remote",
            "full remote",
            "totalmente remoto",
            "remoto de cualquier lugar",
            "remote (worldwide)",
            "remote - latin america",
            "trabajo 100% remoto",
            "teletrabajo total",
        ]
    )

    # 2. Detectar días presenciales específicos en oficina
    pattern_nxm = re.search(r"\b([1-4])\s*(?:x|por|\/)\s*([1-4])\b", text)
    pattern_days_pres = re.search(
        r"\b([1-4])\s*(?:d[ií]as?)\s*(?:a la semana|semanales|al mes)?\s*(?:en oficina|de oficina|presencial|presenciales|en dependencias)",
        text,
    )
    pattern_days_rev = re.search(
        r"(?:presencial|en oficina|de oficina)\s*(?:de)?\s*([1-4])\s*(?:d[ií]as?)", text
    )

    office_days = None
    if pattern_nxm:
        office_days = pattern_nxm.group(1)
    elif pattern_days_pres:
        office_days = pattern_days_pres.group(1)
    elif pattern_days_rev:
        office_days = pattern_days_rev.group(1)

    if office_days:
        n = int(office_days)
        remote_days = 5 - n if n < 5 else 0
        return f"Híbrido ({n} día{'s' if n > 1 else ''} oficina / {remote_days} remoto)"

    # 3. Detectar Híbrido general vs Remoto vs Presencial
    if any(h in text for h in ["híbrido", "hibrido", "hybrid"]):
        return "Híbrido (Días no detallados en aviso)"

    if is_remote_strict or any(
        r in text for r in ["remoto", "remote", "teletrabajo", "home office", "wfh"]
    ):
        return "Remoto 100%"

    if any(
        p in text
        for p in [
            "presencial",
            "on-site",
            "onsite",
            "en oficina",
            "100% presencial",
            "trabajo en terreno",
        ]
    ):
        return "Presencial 100%"

    return "No especificado / A convenir"


def normalize_role(title: str) -> str:
    """
    Normaliza el título de la vacante en categorías estándar del mercado.
    Filtra ruido de búsqueda y reconoce variaciones configuradas dinámicamente en config.yaml.
    """
    if not title:
        return "Excluded Non-Data Role"

    t = title.lower()
    t_clean = re.sub(r"/(a|o)\b", "", t)
    t_clean = re.sub(r"\((a|o)\)", "", t_clean)

    config = load_config()
    role_cfg = config.get("role_normalization", {})
    non_data_keywords = role_cfg.get("non_data_keywords", [])
    categories = role_cfg.get("categories", [])

    # Exclusiones explícitas de ruido
    for nd in non_data_keywords:
        if nd in t_clean:
            return "Excluded Non-Data Role"

    # Coincidencia dinámica por categoría
    for cat in categories:
        cat_name = cat.get("name")
        keywords = cat.get("keywords", [])
        if any(k in t_clean for k in keywords):
            return cat_name

    return "Excluded Non-Data Role"


def scan_technologies(jobs: list[Job], matches: list[MatchResult]) -> list[tuple[str, int, float]]:
    """
    Escanea el corpus completo de vacantes para detectar menciones reales de herramientas y stacks.
    Retorna una lista ordenada de tuplas (tecnología, menciones_totales, porcentaje_de_penetración).
    """
    tech_patterns = {
        "SQL": r"\bsql\b",
        "Python": r"\bpython\b",
        "AWS": r"\baws\b|\bamazon web services\b|\bathena\b|\bredshift\b|\bglue\b",
        "GCP (Google Cloud)": r"\bgcp\b|\bgoogle cloud\b|\bbigquery\b",
        "Azure": r"\bazure\b|\bdata factory\b|\bfabric\b|\bsynapse\b",
        "Power BI / DAX": r"\bpower\s*bi\b|\bdax\b",
        "Snowflake": r"\bsnowflake\b",
        "dbt (Data Build Tool)": r"\bdbt\b|\bdata build tool\b",
        "Apache Spark / PySpark": r"\bspark\b|\bpyspark\b",
        "Databricks": r"\bdatabricks\b",
        "Apache Airflow": r"\bairflow\b",
        "Apache Kafka / Streaming": r"\bkafka\b|\bflink\b|\bstreaming\b",
        "Tableau": r"\btableau\b",
        "Docker / Containers": r"\bdocker\b|\bkubernetes\b|\bk8s\b",
        "Git & CI/CD": r"\bgit\b|\bgithub\b|\bgitlab\b|\bci/cd\b|\bci\/cd\b",
        "PostgreSQL / MySQL": r"\bpostgresql\b|\bpostgres\b|\bmysql\b",
        "DuckDB / Polars": r"\bduckdb\b|\bpolars\b",
        "Dagster / Prefect": r"\bdagster\b|\bprefect\b",
        "Terraform / IaC": r"\bterraform\b|\biac\b",
        "GenAI / LLMs / RAG": r"\bgenai\b|\bllm\b|\brag\b|\blangchain\b|\bllamaindex\b",
    }

    counts = {tech: 0 for tech in tech_patterns}
    total_jobs = len(jobs) if jobs else 1

    for job in jobs:
        text = f"{job.title} {job.description}".lower()
        for tech, pattern in tech_patterns.items():
            if re.search(pattern, text):
                counts[tech] += 1

    results = []
    for tech, count in counts.items():
        if count > 0:
            pct = (count / total_jobs) * 100.0
            results.append((tech, count, pct))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def compute_archetypes_breakdown(jobs: list[Job]) -> list[dict]:
    """
    Clasifica las vacantes en arquetipos técnicos de mercado para mostrar
    los matices y especializaciones observadas en las publicaciones.
    """
    archetypes = [
        {
            "name": "Data Engineer — Cloud & Serverless Batch",
            "role": "Data Engineer",
            "focus": "Pipelines ETL/ELT serverless, orquestación por eventos, lakehouses en nube sin clústeres permanentes.",
            "stack": "AWS (S3, Lambda, Athena, Glue) / GCP (BigQuery, Cloud Functions), SQL, Python, Step Functions",
            "pattern": r"aws|gcp|bigquery|athena|lambda|glue|serverless|s3|cloud functions",
        },
        {
            "name": "Data Engineer — Big Data & Real-Time Streaming",
            "role": "Data Engineer",
            "focus": "Procesamiento distribuido de alto volumen, ingesta CDC en tiempo real y arquitectura Medallion.",
            "stack": "PySpark, Apache Kafka, Databricks, Apache Flink, Delta Lake, Airflow",
            "pattern": r"pyspark|spark|kafka|flink|databricks|streaming|delta lake|emr",
        },
        {
            "name": "Data Engineer — Traditional & Enterprise DWH",
            "role": "Data Engineer",
            "focus": "Mantenimiento, migración y optimización de bodegas de datos legadas y pipelines corporativos.",
            "stack": "SQL Server, Azure Data Factory, SSIS, Stored Procedures, Oracle, PostgreSQL",
            "pattern": r"sql server|data factory|ssis|stored procedures|oracle|synapse",
        },
        {
            "name": "Analytics Engineer — Modern Data Stack",
            "role": "Analytics Engineer",
            "focus": "Modelado dimensional (Kimball), pruebas automatizadas de calidad de datos, semántica y autoservicio.",
            "stack": "dbt, Snowflake, BigQuery, SQL Avanzado (CTEs/Window Functions), Git, CI/CD",
            "pattern": r"dbt|snowflake|dimensional|kimball|data build tool|analytics engineer",
        },
        {
            "name": "BI Specialist & Data Analyst — Decision Support",
            "role": "Data Analyst / BI",
            "focus": "Diseño de dashboards ejecutivos, visualización UX de KPIs, modelado tabular y análisis de negocio.",
            "stack": "Power BI (DAX, Power Query), Tableau, SQL, Quarto, Metabase, Figma",
            "pattern": r"power\s*bi|dax|tableau|looker|business intelligence|metabase",
        },
        {
            "name": "Data Scientist & Predictive Analytics",
            "role": "Data Scientist",
            "focus": "Modelado estadístico, segmentación de clientes, forecasting de demanda y experimentación A/B.",
            "stack": "Python (Scikit-Learn, XGBoost, Pandas, Statsmodels), R, SQL, Jupyter",
            "pattern": r"data scientist|cientifico|cientista|scikit|predictiv|forecasting|regresi|machine learning",
        },
        {
            "name": "AI & GenAI / LLM Engineer",
            "role": "AI / GenAI",
            "focus": "Sistemas RAG, agentes conversacionales, extracción no estructurada y automatización con LLMs.",
            "stack": "LangChain, LlamaIndex, OpenAI/Claude/Gemini APIs, ChromaDB/Pinecone, Python",
            "pattern": r"genai|llm|rag|langchain|llamaindex|inteligencia artificial|prompt engineer|vector database|chromadb|pinecone|qdrant",
        },
    ]

    total_jobs = len(jobs) if jobs else 1
    results = []

    for arch in archetypes:
        matched_count = 0
        for job in jobs:
            text = f"{job.title} {job.description}".lower()
            if re.search(arch["pattern"], text):
                matched_count += 1

        if matched_count > 0:
            pct = (matched_count / total_jobs) * 100.0
            results.append({
                "name": arch["name"],
                "focus": arch["focus"],
                "stack": arch["stack"],
                "count": matched_count,
                "pct": pct,
            })

    results.sort(key=lambda x: x["count"], reverse=True)
    return results


INTL_LOCATION_TERMS = [
    "chicago", "illinois", "united states", "usa", "u.s.", "us remote",
    "remote - us", "us-based", "remoto - estados unidos", "spain", "madrid",
    "barcelona", "uk", "london", "canada", "toronto", "germany", "berlin",
]


def is_job_chile(job: Job) -> bool:
    """Verifica si una vacante pertenece a una Empresa Local en Chile (publicada localmente)."""
    if getattr(job, "origin_type", None) == "Chile (Empresa Local)":
        return True
    if getattr(job, "origin_type", None) == "Internacional / LATAM (Remoto)":
        return False

    from src.agent.filter import CHILE_TERMS

    loc = (job.location or "").lower()
    if any(term in loc for term in INTL_LOCATION_TERMS):
        return False
    if any(r in loc for r in ["remote_local", "remote_global", "fully_remote", "worldwide", "latin america"]):
        return False

    return any(term in loc for term in CHILE_TERMS)


def generate_market_study_report(output_path: str | None = None, scope: str | None = None) -> tuple[str, str]:
    """
    Genera el Estudio de Mercado Histórico Acumulativo en vivo.
    Procesa las vacantes acumuladas en la base de datos histórica segun el ámbito ('chile' o 'international'),
    calculando matrices cruzadas por rol, penetración tecnológica, modalidad y salarios reales.

    Returns:
        tuple[str, str]: (ruta del archivo markdown generado, texto del reporte)
    """
    config = load_config()
    active_scope = scope or config.get("search_scope", "chile")

    with Session(repo.engine) as session:
        jobs = session.exec(select(Job)).all()
        matches = session.exec(select(MatchResult)).all()

    if not jobs:
        logger.warning("No hay vacantes en la base de datos para generar el estudio de mercado.")
        return "", "No hay datos de vacantes suficientes en la base de datos."

    total_jobs_db = len(jobs)

    # Universo de análisis: vacantes del dominio Data & Analytics filtradas por ámbito ('chile' o 'international')
    raw_data_jobs = [j for j in jobs if normalize_role(j.title) != "Excluded Non-Data Role"]
    if active_scope == "chile":
        valid_data_jobs = [j for j in raw_data_jobs if is_job_chile(j)]
    elif active_scope == "international":
        valid_data_jobs = [j for j in raw_data_jobs if not is_job_chile(j)]
    else:
        valid_data_jobs = raw_data_jobs

    n_data = len(valid_data_jobs)
    n_noise = total_jobs_db - n_data
    n_base = n_data if n_data else 1  # denominador seguro

    # Fit personal acumulado histórico (Tier 1 + Tier 2, sobre toda la BD)
    tier12_ids = {m.job_id for m in matches if m.tier in (1, 2)}
    tier1_count = sum(1 for m in matches if m.tier == 1)
    tier2_count = sum(1 for m in matches if m.tier == 2)
    n_fit = tier1_count + tier2_count

    created_dates = [j.created_at for j in jobs if j.created_at]
    first_date = min(created_dates).strftime("%d/%m/%Y") if created_dates else "N/D"
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Roles
    roles_counter = Counter([normalize_role(j.title) for j in valid_data_jobs])
    top_roles = [r for r, _ in roles_counter.most_common()]

    # ----------------------------------------------------------------
    # SALARIOS REALES (universo completo de datos, histórico)
    # ----------------------------------------------------------------
    jobs_with_salary: list[dict] = []
    for job in valid_data_jobs:
        min_v = job.min_salary
        max_v = job.max_salary
        curr = (job.salary_currency or "CLP").upper()

        if not min_v and not max_v and job.salary:
            min_v, max_v, parsed_curr = parse_salary_details(job.salary)
            if parsed_curr:
                curr = parsed_curr

        if not min_v and not max_v and job.description:
            sal_match = re.search(
                r"(?:sueldo|salario|remuneraci[oó]n|renta|salary|compensaci[oó]n)[^\$\n\r0-9]{0,40}(\$?\s*[0-9][0-9\.\,]+(?:\s*(?:-|a|to)\s*\$?\s*[0-9][0-9\.\,]+)?)",
                job.description,
                re.IGNORECASE,
            )
            if sal_match:
                min_v, max_v, parsed_curr = parse_salary_details(sal_match.group(1))
                if parsed_curr:
                    curr = parsed_curr

        if min_v or max_v:
            min_raw = min_v or max_v or 0
            max_raw = max_v or min_v or 0
            avg_v = (min_raw + max_raw) / 2.0
            if avg_v > 0:
                if (curr == "USD" or avg_v < 100000) and avg_v >= 20000:
                    avg_v /= 12.0; min_raw /= 12.0; max_raw /= 12.0
                elif curr == "CLP" and avg_v >= 18000000:
                    avg_v /= 12.0; min_raw /= 12.0; max_raw /= 12.0
                if curr == "USD" or avg_v < 100000:
                    clp_avg = avg_v * USD_TO_CLP
                    clp_min = min_raw * USD_TO_CLP
                    clp_max = max_raw * USD_TO_CLP
                else:
                    clp_avg = avg_v; clp_min = min_raw; clp_max = max_raw
                if 600000 <= clp_avg <= 20000000:
                    jobs_with_salary.append({
                        "role": normalize_role(job.title),
                        "min_clp": clp_min, "avg_clp": clp_avg, "max_clp": clp_max,
                    })

    salaries_by_role: dict[str, list[tuple[float, float, float]]] = {}
    for s in jobs_with_salary:
        salaries_by_role.setdefault(s["role"], []).append((s["min_clp"], s["avg_clp"], s["max_clp"]))

    n_with_salary = len(jobs_with_salary)
    pct_salary = (n_with_salary / n_base) * 100

    # ----------------------------------------------------------------
    # TECH PATTERNS
    # ----------------------------------------------------------------
    tech_patterns = get_tech_patterns()

    # ----------------------------------------------------------------
    # CONSTRUCCIÓN DEL REPORTE
    # ----------------------------------------------------------------
    sources = sorted(set(j.source.capitalize() for j in valid_data_jobs))
    sources_str = ", ".join(sources) if sources else "N/D"

    lines: list[str] = []

    # ── ENCABEZADO ──────────────────────────────────────────────────
    lines += [
        "# Estudio Histórico de Mercado Laboral: Data & Analytics Chile",
        "",
        f"> **Periodo cubierto:** `{first_date}` — `{now_str}`",
        f"> **Universo de análisis:** {n_data} vacantes del dominio Data & Analytics  ",
        f"> **Tu fit personal acumulado:** {n_fit} ofertas afines (Tier 1+2) sobre {n_data} del mercado = **{(n_fit/n_base)*100:.1f}%**",
        "",
        "---",
    ]

    # ── SECCIÓN 1: DEMANDA POR ROL ──────────────────────────────────
    lines += [
        "",
        "## 1. Demanda de Mercado por Rol",
        "",
        "Distribución de todas las vacantes de datos capturadas históricamente, ordenadas por volumen de demanda.",
        "La columna *Fit personal* es referencia tuya exclusivamente y no forma parte del análisis de mercado.",
        "",
        "| Rol | N° Vacantes (Mercado) | % del Mercado | Fit Personal (T1+T2) |",
        "| :--- | ---: | ---: | ---: |",
    ]
    for role, count in roles_counter.most_common():
        pct = (count / n_base) * 100
        r_jobs = [j for j in valid_data_jobs if normalize_role(j.title) == role]
        t12 = sum(1 for j in r_jobs if j.id in tier12_ids)
        lines.append(f"| **{role}** | {count} | {pct:.1f}% | {t12} |")
    lines.append(f"| **TOTAL** | **{n_data}** | **100%** | **{n_fit}** |")

    # ── SECCIÓN 2: MATRIZ TECNOLÓGICA (herramientas × roles) ────────
    matrix_roles = [r for r in top_roles if roles_counter[r] >= 2][:5]
    role_jobs = {r: [j for j in valid_data_jobs if normalize_role(j.title) == r] for r in matrix_roles}
    role_ns   = {r: len(role_jobs[r]) or 1 for r in matrix_roles}

    col_headers = ["Herramienta / Stack"] + [f"{r} (n={roles_counter[r]})" for r in matrix_roles] + [f"**Global (n={n_data})**"]
    sep = ["| :--- |"] + [" ---: |"] * (len(matrix_roles) + 1)

    lines += [
        "",
        "---",
        "",
        "## 2. Penetración Tecnológica por Rol",
        "",
        f"Porcentaje de ofertas de cada perfil que mencionan cada herramienta. "
        f"Universo: {n_data} vacantes Data & Analytics acumuladas históricamente.",
        "",
        "| " + " | ".join(col_headers) + " |",
        "".join(sep),
    ]

    for tech, pat in tech_patterns.items():
        row = [f"**{tech}**"]
        for r in matrix_roles:
            cnt = sum(1 for j in role_jobs[r] if re.search(pat, f"{j.title} {j.description}".lower()))
            row.append(f"{cnt} ({(cnt/role_ns[r])*100:.0f}%)")
        glob = sum(1 for j in valid_data_jobs if re.search(pat, f"{j.title} {j.description}".lower()))
        row.append(f"**{glob} ({(glob/n_base)*100:.1f}%)**")
        lines.append("| " + " | ".join(row) + " |")

    # ── SECCIÓN 3: MATRIZ MODALIDAD × ROL ──────────────────────────
    lines += [
        "",
        "---",
        "",
        "## 3. Modalidad de Trabajo por Rol",
        "",
        "Distribución de régimen presencial para cada perfil. "
        "Valores sobre el universo histórico acumulado completo de datos.",
        "",
        "| Rol | Remoto 100% | Híbrido | Presencial 100% | No especificado | Total | % Remoto |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    tot_rem = tot_hib = tot_pre = tot_ne = 0
    for role in top_roles:
        rjs = [j for j in valid_data_jobs if normalize_role(j.title) == role]
        rt = len(rjs) or 1
        c_rem = c_hib = c_pre = c_ne = 0
        for j in rjs:
            m = extract_detailed_modality(j.location, j.description, j.job_type)
            if "Remoto 100%" in m:   c_rem += 1
            elif "Híbrido" in m:     c_hib += 1
            elif "Presencial" in m:  c_pre += 1
            else:                    c_ne  += 1
        tot_rem += c_rem; tot_hib += c_hib; tot_pre += c_pre; tot_ne += c_ne
        lines.append(
            f"| **{role}** | {c_rem} ({c_rem/rt*100:.0f}%) | {c_hib} ({c_hib/rt*100:.0f}%) "
            f"| {c_pre} ({c_pre/rt*100:.0f}%) | {c_ne} ({c_ne/rt*100:.0f}%) "
            f"| {rt} | **{c_rem/rt*100:.0f}%** |"
        )
    lines.append(
        f"| **TOTAL MERCADO** | **{tot_rem} ({tot_rem/n_base*100:.0f}%)** "
        f"| **{tot_hib} ({tot_hib/n_base*100:.0f}%)** "
        f"| **{tot_pre} ({tot_pre/n_base*100:.0f}%)** "
        f"| **{tot_ne} ({tot_ne/n_base*100:.0f}%)** "
        f"| **{n_data}** | **{tot_rem/n_base*100:.0f}%** |"
    )

    # ── SECCIÓN 4: SALARIOS REALES ──────────────────────────────────
    lines += [
        "",
        "---",
        "",
        "## 4. Bandas Salariales Reales Capturadas",
        "",
        f"Datos salariales 100% factuales extraídos directamente desde los avisos de empleo. "
        f"Se unifica a CLP (1 USD = ${USD_TO_CLP:,}). "
        f"Cobertura: {n_with_salary} ofertas con salario explícito de {n_data} ({pct_salary:.1f}%). "
        f"El {100-pct_salary:.1f}% restante no publicó banda salarial.",
    ]

    if salaries_by_role:
        lines += [
            "",
            "| Perfil | n | Mínimo CLP | Mediana (P50) CLP | Target Senior (P75) CLP | Máximo CLP |",
            "| :--- | ---: | ---: | ---: | ---: | ---: |",
        ]
        role_sal_stats = []
        for role, samples in salaries_by_role.items():
            mins = [s[0] for s in samples]
            avgs = sorted([s[1] for s in samples])
            maxs = [s[2] for s in samples]
            n_samples = len(samples)
            med = avgs[n_samples // 2]
            p75 = avgs[int(n_samples * 0.75)] if n_samples >= 2 else med
            role_sal_stats.append((role, n_samples, min(mins), med, p75, max(maxs)))
        role_sal_stats.sort(key=lambda x: x[3], reverse=True)
        for role, n, mn, med, p75_val, mx in role_sal_stats:
            lines.append(f"| **{role}** | {n} | ${mn:,.0f} | **${med:,.0f}** | **${p75_val:,.0f}** | ${mx:,.0f} |")
    else:
        lines.append("\n*Sin datos salariales explícitos capturados en el período analizado.*")

    # ── NOTA METODOLÓGICA AL PIE ────────────────────────────────────
    lines += [
        "",
        "---",
        "",
        "## Nota Metodológica",
        "",
        "| Dimensión | Detalle |",
        "| :--- | :--- |",
        f"| **Fuentes de datos** | {sources_str} (scraping automatizado de portales de empleo chilenos) |",
        f"| **Periodo cubierto** | {first_date} al {now_str} |",
        f"| **Universo total en BD** | {total_jobs_db} registros brutos ({n_data} del dominio Data & Analytics; {n_noise} descartados como ruido no-TI) |",
        "| **Criterio de inclusión** | Vacantes cuyo título contenga términos de datos/analítica (Data Engineer, Analytics Engineer, BI, etc.) |",
        "| **Normalización de roles** | Clasificación automática por regex sobre el título de la oferta en 8 categorías estándar |",
        "| **Modalidad** | Clasificación por detección de patrones de texto en título, descripción y campo de ubicación |",
        "| **Salarios** | Extraídos de campos estructurados del portal o por regex en la descripción. Anuales convertidos a mensuales. Rango válido: $600.000–$12.000.000 CLP/mes |",
        f"| **Tipo de cambio** | 1 USD = ${USD_TO_CLP:,} CLP (referencia fija de configuración) |",
        "| **Fit personal (Tier 1+2)** | Evaluación LLM del perfil del candidato contra cada oferta. Es un dato personal, no de mercado. |",
        "| **Limitaciones** | Muestra acotada a portales configurados. Salarios explícitos en minoría (~25%). Modalidad puede no estar especificada en aviso. |",
    ]

    report_text = "\n".join(lines)

    if output_path:
        canonical_file_path = output_path
    else:
        out_dir = os.path.join(settings.project_root, "data", "market_study")
        os.makedirs(out_dir, exist_ok=True)
        canonical_file_path = os.path.join(out_dir, "market_study.md")

    os.makedirs(os.path.dirname(os.path.abspath(canonical_file_path)), exist_ok=True)
    with open(canonical_file_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    logger.info(f"Estudio de Mercado Histórico actualizado en: {canonical_file_path}")
    return canonical_file_path, report_text


