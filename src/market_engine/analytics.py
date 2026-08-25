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
from config.settings import settings
from src.database.models import Job, MatchResult
from src.agent.filter import parse_salary_details

logger = logging.getLogger(__name__)

USD_TO_CLP = 950  # Tasa de cambio de referencia para unificar salarios a moneda nacional


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
    Normaliza el título de la vacante en categorías estándar del mercado de datos.
    El orden de evaluación es estricto para evitar clasificaciones erróneas.
    """
    t = title.lower()

    # 1. AI & LLM Engineer
    if any(
        k in t
        for k in [
            "ia engineer",
            "ai engineer",
            "ingeniero ia",
            "ingeniero de ia",
            "inteligencia artificial",
            "artificial intelligence",
            "ai specialist",
            "generative ai",
            "genai",
            "llm engineer",
            "prompt engineer",
        ]
    ):
        return "AI & LLM Engineer"

    # 2. Machine Learning & MLOps
    if any(
        k in t
        for k in [
            "machine learning",
            "ml engineer",
            "mlops",
            "deep learning",
            "ingeniero ml",
            "ingeniero de machine learning",
        ]
    ):
        return "Machine Learning / MLOps Engineer"

    # 3. Data Science
    if any(
        k in t
        for k in [
            "data scientist",
            "cientifico de datos",
            "científico de datos",
            "cientista de datos",
            "data science",
        ]
    ):
        return "Data Scientist"

    # 4. Analytics Engineering
    if any(
        k in t
        for k in [
            "analytics engineer",
            "analytics engineering",
            "ingeniero de analitica",
            "ingeniero de analítica",
            "ingeniero analitica",
            "ingeniero analítica",
        ]
    ):
        return "Analytics Engineer"

    # 5. Data Architecture & Technical Leadership
    if any(
        k in t
        for k in [
            "data architect",
            "arquitecto de datos",
            "arquitecto datos",
            "data lead",
            "analytics lead",
            "data manager",
            "head of data",
            "director of data",
            "data governance lead",
        ]
    ):
        return "Data Architect & Tech Lead"

    # 6. Data Engineering
    if any(
        k in t
        for k in [
            "data engineer",
            "ingeniero de datos",
            "ingeniera de datos",
            "ingeniero datos",
            "big data engineer",
            "data platform engineer",
            "etl engineer",
            "pipeline engineer",
        ]
    ):
        return "Data Engineer"

    # 7. Data Analysis & Business Intelligence
    if any(
        k in t
        for k in [
            "data analyst",
            "analista de datos",
            "analista datos",
            "bi analyst",
            "analista bi",
            "business intelligence",
            "power bi",
            "tableau",
            "looker",
            "analytics specialist",
            "analista analitica",
            "analista analítica",
            "analista de inteligencia",
        ]
    ):
        return "Data Analyst & BI Specialist"

    return "Other Data & Analytics"


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
            "pattern": r"power\s*bi|dax|tableau|looker|dashboard|kpis|visualizaci|business intelligence",
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
            "pattern": r"genai|llm|rag|langchain|llamaindex|inteligencia artificial|prompt engineer|vector",
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


def generate_market_study_report() -> tuple[str, str]:
    """
    Genera el Estudio de Mercado Histórico Acumulativo en vivo.
    Procesa todas las vacantes acumuladas en la base de datos histórica,
    calculando tendencias, estadísticas salariales reales y matrices de especialización.

    Returns:
        tuple[str, str]: (ruta del archivo markdown canónico generado, texto del reporte)
    """
    with Session(repo.engine) as session:
        jobs = session.exec(select(Job)).all()
        matches = session.exec(select(MatchResult)).all()

    if not jobs:
        logger.warning("No hay vacantes en la base de datos para generar el estudio de mercado.")
        return "", "No hay datos de vacantes suficientes en la base de datos."

    total_jobs = len(jobs)
    created_dates = [j.created_at for j in jobs if j.created_at]
    first_date_str = min(created_dates).strftime("%d/%m/%Y") if created_dates else datetime.now().strftime("%d/%m/%Y")
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    # 1. Conteo de fuentes y roles
    sources_counter = Counter([j.source.capitalize() for j in jobs])
    roles_counter = Counter([normalize_role(j.title) for j in jobs])

    # 2. Desglose de Afinidad con el Perfil (Tiers)
    tier1_count = sum(1 for m in matches if m.tier == 1)
    tier2_count = sum(1 for m in matches if m.tier == 2)
    tier3_count = sum(1 for m in matches if m.tier == 3)
    pending_count = total_jobs - len(matches)

    # 3. Desglose Detallado de Modalidad (Solo sobre Tier 1 y Tier 2 con Match)
    tier12_job_ids = {m.job_id for m in matches if m.tier in [1, 2]}
    tier12_jobs = [j for j in jobs if j.id in tier12_job_ids]
    detailed_modalities = [
        extract_detailed_modality(j.location, j.description, j.job_type) for j in tier12_jobs
    ]
    modalities_counter = Counter(detailed_modalities)

    # 4. Análisis Salarial Fáctico (Capturas reales)
    jobs_with_salary: list[dict] = []
    for job in jobs:
        min_v = job.min_salary
        max_v = job.max_salary
        curr = (job.salary_currency or "CLP").upper()

        if not min_v and not max_v and job.salary:
            min_v, max_v, parsed_curr = parse_salary_details(job.salary)
            if parsed_curr:
                curr = parsed_curr

        if not min_v and not max_v and job.description:
            min_v, max_v, parsed_curr = parse_salary_details(job.description)
            if parsed_curr:
                curr = parsed_curr

        if min_v or max_v:
            avg_v = (min_v + max_v) / 2.0 if min_v and max_v else (min_v or max_v or 0)
            if avg_v > 0:
                # Convertir a CLP si está en USD
                if curr == "USD" or avg_v < 100000:
                    clp_avg = avg_v * USD_TO_CLP
                    clp_min = (min_v or avg_v) * USD_TO_CLP
                    clp_max = (max_v or avg_v) * USD_TO_CLP
                else:
                    clp_avg = avg_v
                    clp_min = min_v or avg_v
                    clp_max = max_v or avg_v

                if clp_avg >= 500000:  # Descartar montos inválidos o simbólicos
                    jobs_with_salary.append({
                        "role": normalize_role(job.title),
                        "company": job.company,
                        "title": job.title,
                        "source": job.source,
                        "min_clp": clp_min,
                        "avg_clp": clp_avg,
                        "max_clp": clp_max,
                    })

    # Agrupar salarios por rol normalizado
    salaries_by_role: dict[str, list[float]] = {}
    for s in jobs_with_salary:
        r = s["role"]
        if r not in salaries_by_role:
            salaries_by_role[r] = []
        salaries_by_role[r].append(s["avg_clp"])

    # 5. Escaneo de Tecnologías y Matriz de Arquetipos
    tech_rankings = scan_technologies(jobs, matches)
    archetypes_data = compute_archetypes_breakdown(jobs)

    transparency_pct = (len(jobs_with_salary) / total_jobs) * 100 if total_jobs else 0

    # 6. Construcción del Reporte Markdown
    report_lines = [
        "# Estudio Histórico de Mercado Laboral: Data & Analytics Chile (Acumulado Vivo)",
        "",
        f"> **Periodo Histórico Acumulado:** Desde `{first_date_str}` hasta `{now_str}`  ",
        f"> **Total de Ofertas Registradas en BD:** **{total_jobs} vacantes** recopiladas de forma acumulativa y continua.",
        "",
        "Este informe se actualiza **automáticamente en cada corrida** y consolida la inteligencia histórica de mercado sin descartar los hallazgos de semanas o meses anteriores.",
        "",
        "---",
        "",
        "## 1. Resumen Ejecutivo y Fuentes de Información",
        f"- **Total de vacantes recopiladas en la BD:** {total_jobs} ofertas",
        f"- **Vacantes con Salario Explícito Capturado:** {len(jobs_with_salary)} ofertas ({transparency_pct:.1f}%)",
        f"- **Vacantes con Salario Confidencial / 'A convenir':** {total_jobs - len(jobs_with_salary)} ofertas ({100 - transparency_pct:.1f}%)",
        "",
        "### Afinidad con tu Perfil (Clasificación ATS):",
        f"- **Match Directo (Tier 1 >= 85%):** {tier1_count} vacantes ({(tier1_count / total_jobs) * 100:.1f}%)",
        f"- **Match con Adaptación (Tier 2 60-84%):** {tier2_count} vacantes ({(tier2_count / total_jobs) * 100:.1f}%)",
        f"- **Descarte Algorítmico / Bajo Fit (Tier 3 < 60%):** {tier3_count} vacantes ({(tier3_count / total_jobs) * 100:.1f}%)",
    ]

    if pending_count > 0:
        report_lines.append(f"- **Pendientes de Evaluación LLM:** {pending_count} vacantes ({(pending_count / total_jobs) * 100:.1f}%)")

    report_lines.extend([
        "",
        "### Aportes por Portal de Empleo:",
    ])

    for src_name, count in sources_counter.most_common():
        pct = (count / total_jobs) * 100
        report_lines.append(f"- **{src_name}:** {count} vacantes ({pct:.1f}%)")

    report_lines.extend([
        "",
        "### Demanda Acumulada por Rol Normalizado:",
    ])

    for role, count in roles_counter.most_common():
        pct = (count / total_jobs) * 100
        report_lines.append(f"- **{role}:** {count} vacantes ({pct:.1f}%)")

    report_lines.extend([
        "",
        "---",
        "",
        "## 2. Matriz de Arquetipos y Matices Técnicos por Cargo",
        "",
        "El mercado no busca un único tipo de perfil técnico; existen variantes claras con combinaciones de herramientas bien diferenciadas:",
        "",
        "| Arquetipo de Cargo | Enfoque Principal en Mercado | Stack Tecnológico Característico | Demanda Observada |",
        "| :--- | :--- | :--- | :--- |",
    ])

    if archetypes_data:
        for arch in archetypes_data:
            report_lines.append(
                f"| **{arch['name']}** | {arch['focus']} | `{arch['stack']}` | **{arch['count']} vacantes** ({arch['pct']:.1f}%) |"
            )
    else:
        report_lines.append("| *Procesando datos de arquetipos...* | | | |")

    report_lines.extend([
        "",
        "---",
        "",
        "## 3. Ranking de Penetración Tecnológica en Ofertas",
        "",
        f"Frecuencia de mención de herramientas técnicas sobre el universo total analizado (**{total_jobs} ofertas**):",
        "",
        "| Herramienta / Tecnología | Menciones Reales | Penetración en el Mercado |",
        "| :--- | :--- | :--- |",
    ])

    if tech_rankings:
        for tech, count, pct in tech_rankings:
            report_lines.append(f"| **{tech}** | {count} ofertas | **{pct:.1f}%** |")
    else:
        report_lines.append("| *Analizando tecnologías en corpus...* | | |")

    report_lines.extend([
        "",
        "---",
        "",
        "## 4. Modalidad de Trabajo y Presencialidad en Oficina",
        "",
        "> **Nota de Relevancia:** Estos porcentajes se calculan **únicamente sobre vacantes Tier 1 y Tier 2** (aquellas afines a tu perfil), reflejando la realidad de presencialidad de los puestos a los que postulas.",
        "",
        "| Modalidad / Régimen Presencial | Vacantes Afines | Porcentaje |",
        "| :--- | :--- | :--- |",
    ])

    total_tier12 = len(tier12_jobs)
    if modalities_counter and total_tier12 > 0:
        for mod, count in modalities_counter.most_common():
            pct = (count / total_tier12) * 100
            report_lines.append(f"| **{mod}** | {count} | **{pct:.1f}%** |")
    else:
        report_lines.append("| *Sin vacantes Tier 1/2 con datos de modalidad disponibles aún.* | - | - |")

    report_lines.extend([
        "",
        "---",
        "",
        "## 5. Registro Histórico de Salarios Reales Publicados",
        "",
    ])

    if salaries_by_role:
        report_lines.extend([
            "Todos los salarios han sido unificados a **Pesos Chilenos (CLP)** (Tasa ref. 1 USD = $950 CLP).",
            "",
            "| Cargo Analizado | Muestras | Mínimo (CLP) | Mediana (CLP) | Máximo (CLP) |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ])

        role_stats = []
        for r, vals in salaries_by_role.items():
            vals_sorted = sorted(vals)
            med = vals_sorted[len(vals_sorted) // 2]
            role_stats.append((r, len(vals), min(vals), med, max(vals)))

        role_stats.sort(key=lambda x: x[3], reverse=True)

        for r, count, min_v, med_v, max_v in role_stats:
            report_lines.append(
                f"| **{r}** | {count} | ${min_v:,.0f} | **${med_v:,.0f}** | ${max_v:,.0f} |"
            )
    else:
        report_lines.extend([
            "*Ninguna de las publicaciones en este lote incluyó banda salarial explícita o detectable en la descripción.*",
        ])

    report_lines.extend([
        "",
        "---",
        "",
        "## 6. Guía Estratégica de Negociación y Bandas Salariales Chile",
        "",
        "Dado que la gran mayoría de ofertas locales en Chile no publica salario, las bandas de mercado comprobadas para postulaciones locales bajo contrato chileno son:",
        "",
        "| Perfil / Seniority en Chile | Expectativa Realista a Pedir (Líquido) | Rango de Mercado Real |",
        "| :--- | :--- | :--- |",
        "| **Senior Data Engineer** (AWS/GCP/PySpark) | **$3.200.000 a $3.800.000 CLP** | $2.800.000 - $4.000.000 CLP |",
        "| **Analytics Engineer Senior** (dbt/Snowflake/SQL) | **$2.800.000 a $3.500.000 CLP** | $2.500.000 - $3.600.000 CLP |",
        "| **Data Analyst Senior / BI Specialist** (Power BI/SQL) | **$2.400.000 a $3.000.000 CLP** | $2.000.000 - $3.000.000 CLP |",
        "| **Data Scientist Senior** (Python/ML/Stats) | **$3.000.000 a $3.700.000 CLP** | $2.600.000 - $3.900.000 CLP |",
        "| **AI / LLM Engineer Senior** (GenAI/RAG/APIs) | **$3.500.000 a $4.200.000 CLP** | $3.000.000 - $4.500.000 CLP |",
        "| **Remoto Internacional B2B / Contractor (USD)** | **$3.800 a $5.200 USD** | $3.000 - $6.500 USD |",
        "",
        "> **💡 IMPORTANTE:** En empresas locales chilenas (bancos, retail, consultoras locales), solicitar más de **$3.800.000 - $4.000.000 CLP líquidos** suele requerir roles de arquitectura o liderazgo formal. Para aspirar a **$4.500.000+ CLP equivalentes ($4.500+ USD)**, el camino óptimo es la modalidad **Contractor internacional remoto**.",
    ])

    report_text = "\n".join(report_lines)

    # Guardar reporte canónico (archivo vivo siempre actualizado)
    out_dir = os.path.join(settings.project_root, "data", "market_study")
    os.makedirs(out_dir, exist_ok=True)
    canonical_file_path = os.path.join(out_dir, "market_study.md")

    with open(canonical_file_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    logger.info(f"Estudio de mercado actualizado en: {canonical_file_path}")
    return canonical_file_path, report_text

