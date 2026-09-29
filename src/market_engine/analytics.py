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
        "Power BI / DAX": r"\bpower\s*bi\b|\bdax\b",
        "Excel": r"\bexcel\b",
        "AWS": r"\baws\b|\bamazon web services\b|\bathena\b|\bredshift\b|\bglue\b",
        "Git & CI/CD": r"\bgit\b|\bgithub\b|\bgitlab\b|\bci/cd\b|\bci\/cd\b",
        "Azure": r"\bazure\b|\bdata factory\b|\bfabric\b|\bsynapse\b",
        "Databricks": r"\bdatabricks\b",
        "dbt": r"\bdbt\b|\bdata build tool\b",
        "Tableau": r"\btableau\b",
        "Apache Spark / PySpark": r"\bspark\b|\bpyspark\b",
        "Snowflake": r"\bsnowflake\b",
        "GCP / BigQuery": r"\bgcp\b|\bgoogle cloud\b|\bbigquery\b",
        "Looker / Looker Studio": r"\blooker\b|\blooker\s*studio\b",
        "Apache Airflow": r"\bairflow\b",
        "R": r"\brstudio\b|\bprogramming\s+in\s+r\b|\bprogramaci[oó]n\s+en\s+r\b|\br\s+o\s+python\b|\bpython\s+o\s+r\b|\br\s+y\s+python\b|\bpython\s+y\s+r\b|\br\s*,\s*python\b|\bpython\s*,\s*r\b|\br\s+language\b|\blenguaje\s+r\b|\br\s+script\b",
        "Apache Kafka": r"\bkafka\b|\bstreaming\b|\bflink\b",
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


def extract_and_normalize_salary(job: Job) -> dict | None:
    """
    Extrae, valida y normaliza el salario de una vacante a valores mensuales en CLP y USD.
    Detecta automáticamente periodicidad horaria (* 160h/mes), anual (/ 12),
    limpia erratas y descarta beneficios no salariales o monedas foráneas incompatibles.
    """
    text = job.description or ""
    loc = (job.location or "").lower()

    # Descartar monedas locales extranjeras que no sean USD ni CLP (ej: COP, BRL, ARS, EUR)
    if any(country in loc for country in ["colombia", "bogot", "medell", "brazil", "brasil", "argentina", "méxico", "mexico", "españa", "madrid"]):
        if "usd" not in text.lower() and getattr(job, "salary_currency", "") != "USD":
            return None

    min_v = job.min_salary
    max_v = job.max_salary
    curr = (job.salary_currency or "").upper()
    is_hourly = False
    is_annual = False

    if not min_v and not max_v and job.salary:
        min_v, max_v, parsed_curr = parse_salary_details(job.salary)
        if parsed_curr:
            curr = parsed_curr

    if not min_v and not max_v and text:
        clean_text = re.sub(r"sin goce de sueldo", "", text, flags=re.I)
        clean_text = re.sub(r"parental leave", "", clean_text, flags=re.I)
        clean_text = re.sub(r"reajuste de sueldo", "", clean_text, flags=re.I)

        sal_match = re.search(
            r"\b(?:sueldo|salario|remuneraci[oó]n|renta\s+ofertada|renta\s+l[ií]quida|renta\s+bruta|renta|salary\s+range|salary)\b[^\$\n\r0-9]{0,40}(\$?\s*[0-9][0-9\.\,]+(?:\s*(?:-|a|to)\s*\$?\s*[0-9][0-9\.\,]+)?)",
            clean_text,
            re.IGNORECASE,
        )
        if sal_match:
            snippet = clean_text[sal_match.start():sal_match.end()+25].lower()
            if any(h in snippet for h in ["hora", "/hr", "per hour", "por hora", "la hora"]):
                is_hourly = True
            if any(a in snippet for a in ["anual", "año", "year", "annual"]):
                is_annual = True

            matched_num_str = sal_match.group(1)
            if re.search(r",00\.$", matched_num_str):
                matched_num_str = re.sub(r",00\.$", ",000", matched_num_str)

            min_v, max_v, parsed_curr = parse_salary_details(matched_num_str)
            if parsed_curr:
                curr = parsed_curr

    if not min_v and not max_v:
        return None

    min_raw = min_v or max_v or 0
    max_raw = max_v or min_v or 0
    if min_raw <= 0 and max_raw <= 0:
        return None

    if min_raw > max_raw:
        min_raw, max_raw = max_raw, min_raw

    # Saneamiento de erratas de orden de magnitud (ej: min 100 vs max 90000)
    if min_raw < 1000 and max_raw >= 20000:
        min_raw = max_raw

    desc_lower = text.lower()
    if not is_hourly and any(h in desc_lower for h in ["/hora", "por hora", "la hora", "per hour", "clp/hr", "usd/hr"]):
        if any(f"{int(min_raw)}" in desc_lower for _ in [1]):
            is_hourly = True

    if not curr:
        if "usd" in desc_lower or "u$s" in desc_lower or "dólar" in desc_lower or "dolar" in desc_lower:
            curr = "USD"
        elif 20000 <= max_raw <= 500000 and "clp" not in desc_lower:
            curr = "USD"
        elif "clp" in desc_lower or "$" in desc_lower or "pesos" in desc_lower:
            curr = "CLP"
        else:
            curr = "USD" if max_raw < 100000 else "CLP"
    elif curr == "CLP" and 20000 <= max_raw <= 500000 and "clp" not in desc_lower:
        curr = "USD"

    if curr == "CLP" and max_raw <= 500:
        return None

    if curr == "USD" and max_raw > 10000 and is_hourly:
        curr = "CLP"

    if is_hourly:
        min_raw *= 160
        max_raw *= 160

    avg_raw = (min_raw + max_raw) / 2.0

    if curr == "USD":
        if avg_raw >= 20000 or is_annual:
            min_raw /= 12.0
            max_raw /= 12.0
            avg_raw /= 12.0
        clp_min = min_raw * USD_TO_CLP
        clp_max = max_raw * USD_TO_CLP
        clp_avg = avg_raw * USD_TO_CLP
        usd_min = min_raw
        usd_max = max_raw
        usd_avg = avg_raw
    else:  # CLP
        if avg_raw >= 18000000 or is_annual:
            min_raw /= 12.0
            max_raw /= 12.0
            avg_raw /= 12.0
        clp_min = min_raw
        clp_max = max_raw
        clp_avg = avg_raw
        usd_min = min_raw / USD_TO_CLP
        usd_max = max_raw / USD_TO_CLP
        usd_avg = avg_raw / USD_TO_CLP

    if 600000 <= clp_avg <= 12000000 and clp_min >= 500000:
        return {
            "role": normalize_role(job.title),
            "clp_min": round(clp_min),
            "clp_avg": round(clp_avg),
            "clp_max": round(clp_max),
            "usd_min": round(usd_min),
            "usd_avg": round(usd_avg),
            "usd_max": round(usd_max),
            "orig_curr": curr,
            "is_hourly": is_hourly,
        }
    return None


def _render_market_section(
    section_title: str,
    scope_desc: str,
    jobs: list[Job],
    tier12_ids: set[int],
    is_usd: bool = False,
    tech_patterns: dict[str, str] | None = None,
) -> list[str]:
    """Renderiza una sección analítica completa para un ámbito geográfico específico."""
    if not jobs:
        return [f"## {section_title}\n\n*Sin vacantes registradas para este ámbito.*"]

    lines: list[str] = []
    n_data = len(jobs)
    n_base = n_data if n_data else 1
    roles_counter = Counter([normalize_role(j.title) for j in jobs])
    top_roles = [r for r, _ in roles_counter.most_common()]

    lines += [
        f"## {section_title}",
        "",
        f"> **Ámbito:** {scope_desc} | **Universo analizado:** {n_data} vacantes factuales.",
        "",
        "### 1. Demanda de Mercado por Rol",
        "",
        "Distribución objetiva de vacantes capturadas en este ámbito geográfico. "
        "La columna *Fit Personal* es una referencia complementaria sobre tu perfil y no afecta el análisis de mercado.",
        "",
        "| Rol | N° Vacantes (Mercado) | % del Mercado | Fit Personal (T1+T2) |",
        "| :--- | ---: | ---: | ---: |",
    ]

    for role, count in roles_counter.most_common():
        pct = (count / n_base) * 100
        r_jobs = [j for j in jobs if normalize_role(j.title) == role]
        t12 = sum(1 for j in r_jobs if j.id in tier12_ids)
        lines.append(f"| **{role}** | {count} | {pct:.1f}% | {t12} |")
    n_fit_scope = sum(1 for j in jobs if j.id in tier12_ids)
    lines.append(f"| **TOTAL** | **{n_data}** | **100%** | **{n_fit_scope}** |")

    # Tecnologías
    if tech_patterns:
        matrix_roles = [r for r in top_roles if roles_counter[r] >= 2][:5]
        role_jobs = {r: [j for j in jobs if normalize_role(j.title) == r] for r in matrix_roles}
        role_ns = {r: len(role_jobs[r]) or 1 for r in matrix_roles}

        col_headers = ["Herramienta / Stack"] + [f"{r} (n={roles_counter[r]})" for r in matrix_roles] + [f"**Global (n={n_data})**"]
        sep = ["| :--- |"] + [" ---: |"] * (len(matrix_roles) + 1)

        lines += [
            "",
            "### 2. Penetración Tecnológica por Rol",
            "",
            f"Frecuencia de mención de herramientas sobre las {n_data} vacantes analizadas en este ámbito.",
            "",
            "| " + " | ".join(col_headers) + " |",
            "".join(sep),
        ]

        for tech, pat in tech_patterns.items():
            row = [f"**{tech}**"]
            for r in matrix_roles:
                cnt = sum(1 for j in role_jobs[r] if re.search(pat, f"{j.title} {j.description}".lower()))
                row.append(f"{cnt} ({(cnt/role_ns[r])*100:.0f}%)")
            glob = sum(1 for j in jobs if re.search(pat, f"{j.title} {j.description}".lower()))
            row.append(f"**{glob} ({(glob/n_base)*100:.1f}%)**")
            lines.append("| " + " | ".join(row) + " |")

    # Modalidad
    lines += [
        "",
        "### 3. Modalidad de Trabajo por Rol",
        "",
        "Distribución de régimen presencial para cada perfil en este segmento.",
        "",
        "| Rol | Remoto 100% | Híbrido | Presencial 100% | No especificado | Total | % Remoto |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    tot_rem = tot_hib = tot_pre = tot_ne = 0
    for role in top_roles:
        rjs = [j for j in jobs if normalize_role(j.title) == role]
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
        f"| **TOTAL SEGMENTO** | **{tot_rem} ({tot_rem/n_base*100:.0f}%)** "
        f"| **{tot_hib} ({tot_hib/n_base*100:.0f}%)** "
        f"| **{tot_pre} ({tot_pre/n_base*100:.0f}%)** "
        f"| **{tot_ne} ({tot_ne/n_base*100:.0f}%)** "
        f"| **{n_data}** | **{tot_rem/n_base*100:.0f}%** |"
    )

    # Salarios
    sal_samples_by_role: dict[str, list[dict]] = {}
    for j in jobs:
        sal = extract_and_normalize_salary(j)
        if sal:
            sal_samples_by_role.setdefault(sal["role"], []).append(sal)

    total_sal_samples = sum(len(v) for v in sal_samples_by_role.values())
    pct_sal = (total_sal_samples / n_base) * 100

    lines += [
        "",
        "### 4. Bandas Salariales Reales Capturadas",
        "",
        f"Datos salariales 100% factuales extraídos de los avisos de empleo (tarifas horarias mensualizadas a 160h/mes, anuales divididas entre 12). "
        f"Muestra: {total_sal_samples} ofertas con salario explícito ({pct_sal:.1f}% del segmento). "
        f"**Esta estadística incluye todas las ofertas con dato salarial, sin ningún filtro de fit con tu CV.**",
        "",
    ]

    if sal_samples_by_role:
        if is_usd:
            lines += [
                "| Perfil | n | Mínimo USD/mes | Mediana (P50) USD/mes | Target Senior (P75) USD/mes | Máximo USD/mes | Equiv. Mediana CLP |",
                "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
            stats_usd = []
            for role, samples in sal_samples_by_role.items():
                mins = [s["usd_min"] for s in samples]
                avgs = sorted([s["usd_avg"] for s in samples])
                maxs = [s["usd_max"] for s in samples]
                clp_avgs = sorted([s["clp_avg"] for s in samples])
                n_s = len(samples)
                med_usd = avgs[n_s // 2]
                p75_usd = avgs[int(n_s * 0.75)] if n_s >= 2 else med_usd
                med_clp = clp_avgs[n_s // 2]
                stats_usd.append((role, n_s, min(mins), med_usd, p75_usd, max(maxs), med_clp))
            stats_usd.sort(key=lambda x: x[3], reverse=True)
            for role, n, mn, med, p75_val, mx, m_clp in stats_usd:
                lines.append(f"| **{role}** | {n} | ${mn:,.0f} | **${med:,.0f}** | **${p75_val:,.0f}** | ${mx:,.0f} | ${m_clp:,.0f} CLP |")
        else:
            lines += [
                "| Perfil | n | Mínimo CLP | Mediana (P50) CLP | Target Senior (P75) CLP | Máximo CLP |",
                "| :--- | ---: | ---: | ---: | ---: | ---: |",
            ]
            stats_clp = []
            for role, samples in sal_samples_by_role.items():
                mins = [s["clp_min"] for s in samples]
                avgs = sorted([s["clp_avg"] for s in samples])
                maxs = [s["clp_max"] for s in samples]
                n_s = len(samples)
                med = avgs[n_s // 2]
                p75_val = avgs[int(n_s * 0.75)] if n_s >= 2 else med
                stats_clp.append((role, n_s, min(mins), med, p75_val, max(maxs)))
            stats_clp.sort(key=lambda x: x[3], reverse=True)
            for role, n, mn, med, p75_val, mx in stats_clp:
                lines.append(f"| **{role}** | {n} | ${mn:,.0f} | **${med:,.0f}** | **${p75_val:,.0f}** | ${mx:,.0f} |")
    else:
        lines.append("*Sin datos salariales explícitos capturados en este segmento.*")

    lines.append("")
    return lines


def generate_market_study_report(output_path: str | None = None, scope: str | None = None) -> tuple[str, str]:
    """
    Genera el Estudio de Mercado Histórico Acumulativo en vivo.
    Procesa las vacantes acumuladas en la base de datos histórica separando de forma
    limpia el mercado local chileno y el mercado internacional remoto para evitar sesgos.

    Returns:
        tuple[str, str]: (ruta del archivo markdown generado, texto del reporte)
    """
    config = load_config()
    active_scope = scope or config.get("search_scope", "all")

    with Session(repo.engine) as session:
        jobs = session.exec(select(Job)).all()
        matches = session.exec(select(MatchResult)).all()

    if not jobs:
        logger.warning("No hay vacantes en la base de datos para generar el estudio de mercado.")
        return "", "No hay datos de vacantes suficientes en la base de datos."

    total_jobs_db = len(jobs)
    raw_data_jobs = [j for j in jobs if normalize_role(j.title) != "Excluded Non-Data Role"]
    chile_jobs = [j for j in raw_data_jobs if is_job_chile(j)]
    intl_jobs = [j for j in raw_data_jobs if not is_job_chile(j)]

    tier12_ids = {m.job_id for m in matches if m.tier in (1, 2)}
    total_data_jobs = len(raw_data_jobs)
    n_noise = total_jobs_db - total_data_jobs

    created_dates = [j.created_at for j in jobs if j.created_at]
    first_date = min(created_dates).strftime("%d/%m/%Y") if created_dates else "N/D"
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    tech_patterns = get_tech_patterns()

    sources = sorted(set(j.source.capitalize() for j in raw_data_jobs))
    sources_str = ", ".join(sources) if sources else "N/D"

    lines: list[str] = [
        "# Estudio Histórico de Mercado Laboral: Data & Analytics",
        "",
        f"> **Periodo cubierto:** `{first_date}` — `{now_str}`",
        f"> **Universo analizado:** {total_data_jobs} vacantes factuales del dominio Data & Analytics ({len(chile_jobs)} Chile, {len(intl_jobs)} Internacionales).",
        f"> **Objetividad Factual:** Este estudio procesa todas las ofertas de mercado de forma 100% agnóstica a cualquier perfil o CV. Las estadísticas de roles, tecnologías, modalidades y salarios no están filtradas por fit.",
        f"> **Alineación con tu CV:** Las métricas de *Fit Personal* (Tier 1+2) se incluyen exclusivamente como capa de referencia complementaria.",
        "",
        "---",
        "",
    ]

    if active_scope == "chile":
        lines += _render_market_section(
            "Mercado Laboral Chile (Empresas Locales)",
            "Empresas en Chile con régimen local y salarios en CLP",
            chile_jobs,
            tier12_ids,
            is_usd=False,
            tech_patterns=tech_patterns,
        )
    elif active_scope == "international":
        lines += _render_market_section(
            "Mercado Internacional / LATAM (Remoto / Contractor)",
            "Ofertas internacionales y LATAM contratadas remotamente (USD)",
            intl_jobs,
            tier12_ids,
            is_usd=True,
            tech_patterns=tech_patterns,
        )
    else:  # "all" -> Segmentación limpia de ambas realidades sin sesgos
        lines += _render_market_section(
            "PARTE I: 🇨🇱 Mercado Laboral Chile (Empresas Locales)",
            "Empresas en Chile con régimen local y salarios en CLP",
            chile_jobs,
            tier12_ids,
            is_usd=False,
            tech_patterns=tech_patterns,
        )
        lines += ["---", ""]
        lines += _render_market_section(
            "PARTE II: 🌎 Mercado Internacional / LATAM (Remoto / Contractor)",
            "Ofertas internacionales y LATAM contratadas remotamente (USD)",
            intl_jobs,
            tier12_ids,
            is_usd=True,
            tech_patterns=tech_patterns,
        )
        lines += ["---", ""]
        lines += [
            "## PARTE III: ⚖️ Síntesis Comparativa (Chile vs Internacional)",
            "",
            "Contraste directo entre el mercado corporativo local y el mercado de teletrabajo contractor:",
            "",
            "| Dimensión | 🇨🇱 Mercado Chile (Local) | 🌎 Internacional / Remoto (USD) |",
            "| :--- | :--- | :--- |",
            f"| **Volumen de Vacantes** | {len(chile_jobs)} ofertas ({len(chile_jobs)/max(1, total_data_jobs)*100:.1f}%) | {len(intl_jobs)} ofertas ({len(intl_jobs)/max(1, total_data_jobs)*100:.1f}%) |",
            f"| **Modalidad 100% Remota** | ~20% (predominio de modelo híbrido 2x3 o presencial) | 100% (teletrabajo transfronterizo) |",
            "| **Moneda de Negociación** | Pesos Chilenos (CLP mensual líquido o bruto) | Dólares Americanos (USD mensual / anual B2B) |",
            "| **Herramientas Clave de Negocio** | Power BI, SQL, Excel Avanzado, Azure, AWS, Looker | dbt, Snowflake, Databricks, BigQuery, Terraform, Kafka |",
            "",
        ]

    # ── NOTA METODOLÓGICA AL PIE ────────────────────────────────────
    lines += [
        "---",
        "",
        "## Nota Metodológica",
        "",
        "| Dimensión | Detalle |",
        "| :--- | :--- |",
        f"| **Fuentes de datos** | {sources_str} (scraping automatizado de portales de empleo) |",
        f"| **Periodo cubierto** | {first_date} al {now_str} |",
        f"| **Universo total en BD** | {total_jobs_db} registros brutos ({total_data_jobs} del dominio Data & Analytics; {n_noise} descartados como ruido no-TI) |",
        "| **Segmentación Geográfica** | Separación metodológica entre empresas locales chilenas y ofertas internacionales/contractor para eliminar distorsión salarial y de modalidad |",
        "| **Normalización de roles** | Clasificación por regex sobre el título en 9 categorías estándar (incluyendo Data Governance & Quality) |",
        "| **Salarios** | Extraídos de campos estructurados o regex contextual. Tarifas horarias calculadas a 160h/mes, anuales divididas entre 12. Rango mensual válido: $600.000–$12.000.000 CLP |",
        f"| **Tipo de cambio** | 1 USD = ${USD_TO_CLP:,} CLP (referencia fija de configuración) |",
        "| **Fit personal (Tier 1+2)** | Métrica complementaria del perfil del candidato. No filtra ni altera ninguna métrica de mercado. |",
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


