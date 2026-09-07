import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from sqlmodel import Session, select

from config.settings import load_config, settings
from src.agent.providers import get_llm_provider
from src.agent.quota import call_with_retry
from src.cv_engine.builder import load_profile
from src.database.models import Job, MatchResult
from src.database.repository import engine
from src.obsidian_exporter import _resolve_vault

logger = logging.getLogger(__name__)

LINKEDIN_SYSTEM_PROMPT = """
Eres un Staff Data Engineer, Arquitecto de Datos y Estratega de Contenido Técnico con más de 12 años de experiencia liderando plataformas de datos en empresas de alto crecimiento.

Tu misión es generar una guía de contenidos y 4 borradores de publicaciones para LinkedIn de ALTÍSIMO IMPACTO y CRITERIO TÉCNICO SENIOR, basadas en las demandas reales del mercado laboral actual (Data Engineering y Analytics Engineering en Chile y LATAM).

### 🎯 PRINCIPIOS DE REDACCIÓN & SEO EN LINKEDIN:
1. **Criterio Senior / Trade-offs:** No hagas tutoriales para principiantes ni listados genéricos. Enfócate en decisiones de arquitectura, costos ocultos (FinOps), qué problemas resuelve cada herramienta y cuándo NO usarla.
2. **SEO & Palabras Clave:** Incorpora de manera natural términos técnicos de alta búsqueda (ej. *dbt, Apache Iceberg, Snowflake, Databricks, PySpark, Data Contracts, Medallion Architecture, CI/CD, Kafka, Polars, DuckDB*).
3. **Estructura de Alto Engagement:**
   - **Hook inicial (1-2 líneas):** Provocativo, que rompa mitos o plantee una pregunta difícil.
   - **Cuerpo con formato limpio:** Párrafos cortos, viñetas claras y ejemplos concretos.
   - **Llamada a la acción (CTA):** Pregunta final abierta para abrir debate entre otros ingenieros senior y leads.
   - **Hashtags:** 4 a 5 hashtags técnicos estratégicos.
4. **Idioma:** Español fluido profesional con la terminología técnica estándar en inglés.
"""

LINKEDIN_USER_PROMPT_TEMPLATE = """
A partir de la siguiente información del mercado laboral actual y del perfil del candidato, genera la guía completa de publicaciones de LinkedIn en formato Markdown:

### 📊 DATA DE VACANTES ACTIVAS EN EL MERCADO (TECNOLOGÍAS Y REQUISITOS DEMANDADOS):
{market_context}

### 👤 PERFIL Y EXPERIENCIA DEL CANDIDATO:
- **Nombre:** {candidate_name}
- **Rol:** {candidate_title}
- **Herramientas Clave:** {candidate_skills}
- **Experiencia Destacada:** {candidate_experience}

---

### ESTRUCTURA OBLIGATORIA DEL DOCUMENTO MARKDOWN:

# 🚀 Estrategia de Contenido LinkedIn — Data & Analytics Engineering
> *Generado automáticamente a partir de las vacantes y tecnologías más cotizadas del mercado.*

---

## 📌 1. Debate de Arquitectura & Trade-offs (Senior/Staff Level)
- **Tema:** [Tema polémico/actual basado en las vacantes, ej: Lakehouse vs DW, Iceberg vs Parquet nativo, Streaming vs Batch]
- **Objetivo SEO:** [Palabras clave posicionadas]
### 📝 Borrador listo para publicar:
[Texto completo del post con hook, desarrollo con 3 puntos clave, CTA y hashtags]

---

## 📌 2. Librerías & Tendencias en Herramientas Modernas
- **Tema:** [Librería o herramienta en alza requerida en ofertas, ej: dbt Mesh/Semantic Layer, Polars/DuckDB, Dagster, Debezium CDC]
- **Objetivo SEO:** [Palabras clave posicionadas]
### 📝 Borrador listo para publicar:
[Texto completo del post con hook, caso de uso práctico, ventajas vs alternativas, CTA y hashtags]

---

## 📌 3. Lección de Trinchera, Confiabilidad & FinOps (Battle-Tested)
- **Tema:** [Dolor real de producción: Optimización de costos cloud, incidentes de calidad de datos, Slim CI para pipelines, Data Contracts]
- **Objetivo SEO:** [Palabras clave posicionadas]
### 📝 Borrador listo para publicar:
[Texto completo del post con hook, lección aprendida / solución aplicada, métricas de impacto, CTA y hashtags]

---

## 📌 4. Análisis de Tendencias del Mercado Laboral (Thought Leadership)
- **Tema:** [Qué están buscando las empresas de datos en 2026: qué separa a un Mid de un Senior/Lead en base a las ofertas reales]
- **Objetivo SEO:** [Palabras clave posicionadas]
### 📝 Borrador listo para publicar:
[Texto completo del post con hook basado en datos de mercado, desglose de habilidades más valoradas, CTA y hashtags]

---

## 📚 5. Fuentes de Autoridad & Lecturas Recomendadas
Incluye 4 a 6 enlaces a blogs y publicaciones de ingeniería reconocidas internacionalmente que respalden estos temas (ej: Netflix TechBlog, Uber Engineering, Airbnb Data, SeattleDataGuy, Benn Stancil, Databricks Blog, Snowflake Engineering).

---

## 💡 6. Tips de Algoritmo y Publicación para Máximo Alcance
- Mejores horarios recomendados.
- Cómo responder los primeros comentarios para activar el algoritmo.
- Dónde colocar links externos (primer comentario vs cuerpo).
"""


def extract_market_insights_from_db(limit_jobs: int = 25) -> str:
    """
    Extrae patrones de tecnologías más demandadas, títulos y requerimientos
    de las vacantes con mejor compatibilidad almacenadas en SQLite.
    """
    with Session(engine) as session:
        # Priorizar vacantes con MatchResult alto o recientes
        statement = (
            select(Job, MatchResult)
            .outerjoin(MatchResult, Job.id == MatchResult.job_id)
            .order_by(Job.id.desc())
            .limit(limit_jobs)
        )
        rows = session.exec(statement).all()

        if not rows:
            return "Tendencias generales: SQL, Python, dbt, Snowflake, Databricks, AWS, PySpark, Airflow, CI/CD."

        jobs_summary = []
        tech_counter: dict[str, int] = {}
        gaps_counter: dict[str, int] = {}

        for job, match in rows:
            score_str = f"Fit {match.score:.0f}%" if match else "Sin evaluar"
            tier_str = f"Tier {match.tier}" if match else "N/A"
            jobs_summary.append(f"- **{job.title}** @ {job.company} ({job.location} | {score_str} | {tier_str})")

            if match and match.key_technologies:
                try:
                    techs = json.loads(match.key_technologies)
                    if isinstance(techs, list):
                        for t in techs:
                            tech_counter[t] = tech_counter.get(t, 0) + 1
                except Exception:
                    pass

            if match and match.missing_keywords:
                try:
                    gaps = json.loads(match.missing_keywords)
                    if isinstance(gaps, list):
                        for g in gaps:
                            gaps_counter[g] = gaps_counter.get(g, 0) + 1
                except Exception:
                    pass

        top_techs = sorted(tech_counter.items(), key=lambda x: x[1], reverse=True)[:10]
        top_gaps = sorted(gaps_counter.items(), key=lambda x: x[1], reverse=True)[:8]

        tech_text = ", ".join([f"{t} ({c})" for t, c in top_techs]) if top_techs else "dbt, Snowflake, PySpark, AWS, Databricks, Airflow"
        gaps_text = ", ".join([f"{g} ({c})" for g, c in top_gaps]) if top_gaps else "Data Governance, Iceberg, FinOps, Streaming"

        context_lines = [
            f"**Tecnologías más demandadas en vacantes recientes:** {tech_text}",
            f"**Gaps y habilidades diferenciadoras recurrentes:** {gaps_text}",
            "\n**Muestra de ofertas analizadas:**",
            *jobs_summary[:12]
        ]

        return "\n".join(context_lines)


def generate_linkedin_content(custom_topic: str | None = None) -> tuple[Path, str]:
    """
    Genera la estrategia y borradores de posts para LinkedIn y guarda el archivo en Obsidian Vault.

    Returns:
        tuple[Path, str]: (Ruta del archivo Markdown generado, Contenido generado)
    """
    logger.info("Recopilando datos de mercado del repositorio para contenido LinkedIn...")
    market_context = extract_market_insights_from_db()
    if custom_topic:
        market_context = f"**Tema prioritario solicitado por el usuario:** {custom_topic}\n\n" + market_context

    profile = load_profile()
    candidate_name = profile.get("name", "Data & Analytics Engineer")
    candidate_title = profile.get("title", "Senior Data Engineer / Analytics Engineer")

    skills_list = profile.get("skills", {})
    if isinstance(skills_list, dict):
        all_skills = [f"{cat}: {', '.join(items)}" for cat, items in skills_list.items() if isinstance(items, list)]
        candidate_skills = " | ".join(all_skills)
    elif isinstance(skills_list, list):
        candidate_skills = ", ".join([str(s) for s in skills_list])
    else:
        candidate_skills = "SQL, Python, dbt, Snowflake, Cloud Data Platforms"

    experiences = profile.get("experience", [])
    exp_summaries = []
    for exp in experiences[:3]:
        company = exp.get("company", "")
        role = exp.get("role", "")
        bullets = exp.get("bullets", [])
        bullet_text = bullets[0] if bullets else ""
        exp_summaries.append(f"{role} en {company}: {bullet_text}")
    candidate_exp = "; ".join(exp_summaries) if exp_summaries else "Experiencia liderando pipelines y arquitecturas de datos modernas."

    user_prompt = LINKEDIN_USER_PROMPT_TEMPLATE.format(
        market_context=market_context,
        candidate_name=candidate_name,
        candidate_title=candidate_title,
        candidate_skills=candidate_skills,
        candidate_experience=candidate_exp,
    )

    provider = get_llm_provider()

    def _call():
        return provider.generate(
            system_prompt=LINKEDIN_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

    logger.info(f"Consultando LLM ({provider.__class__.__name__}) para generar ideas de LinkedIn...")
    raw_response = call_with_retry(_call)

    # Limpiar bloques think si existieran
    clean_markdown = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL).strip()
    if clean_markdown.startswith("```json"):
        clean_markdown = clean_markdown[7:].rstrip("`").strip()
    elif clean_markdown.startswith("```markdown"):
        clean_markdown = clean_markdown[11:].rstrip("`").strip()
    elif clean_markdown.startswith("```"):
        clean_markdown = clean_markdown[3:].rstrip("`").strip()

    # Si el LLM devolvió un JSON {"document": "...", ...} o estructura anidada, transformarlo a Markdown limpio
    if clean_markdown.startswith("{") and clean_markdown.endswith("}"):
        try:
            parsed = json.loads(clean_markdown)
            if isinstance(parsed, dict):
                # Caso 1: clave simple con markdown dentro
                for k in ["document", "content", "markdown", "text", "output"]:
                    if k in parsed and isinstance(parsed[k], str):
                        clean_markdown = parsed[k]
                        break
                else:
                    # Caso 2: estructura anidada guia_contenido / secciones
                    guia = parsed.get("guia_contenido", parsed)
                    lines = [f"# {guia.get('titulo', '🚀 Estrategia de Contenido LinkedIn — Data & Analytics Engineering')}", "", "> [!TIP] *Estrategia generada a partir de las vacantes y tecnologías más demandadas del mercado.*", ""]
                    
                    secciones = guia.get("secciones", [])
                    for sec in secciones:
                        lines.append("---")
                        lines.append(f"## 📌 {sec.get('titulo', 'Publicación')}")
                        if sec.get("tema"):
                            lines.append(f"- **Tema:** {sec.get('tema')}")
                        if sec.get("objetivo_seo"):
                            lines.append(f"- **Objetivo SEO:** `{sec.get('objetivo_seo')}`")
                        lines.append("")
                        lines.append("### 📝 Borrador listo para publicar:")
                        lines.append(sec.get("borrador", "").strip())
                        lines.append("")

                    fuentes = guia.get("fuentes_autoridad", [])
                    if fuentes:
                        lines.append("---")
                        lines.append("## 📚 5. Fuentes de Autoridad & Lecturas Recomendadas")
                        for f in fuentes:
                            lines.append(f"- 🔗 {f}")
                        lines.append("")

                    tips = guia.get("tips_algoritmo", {})
                    if tips and isinstance(tips, dict):
                        lines.append("---")
                        lines.append("## 💡 6. Tips de Algoritmo y Publicación para Máximo Alcance")
                        for k, v in tips.items():
                            lines.append(f"- **{k.capitalize()}:** {v}")
                        lines.append("")

                    clean_markdown = "\n".join(lines)
        except Exception as e:
            logger.warning(f"No se pudo parsear JSON a Markdown: {e}")

    # Guardar en Obsidian Vault si está disponible
    config = load_config()
    obsidian_cfg = config.get("obsidian", {})
    vault_dir = _resolve_vault(None, obsidian_cfg)
    vault_dir.mkdir(parents=True, exist_ok=True)

    obsidian_file = vault_dir / "Ideas_LinkedIn.md"
    obsidian_file.write_text(clean_markdown, encoding="utf-8")
    logger.info(f"Ideas de LinkedIn guardadas en Obsidian: {obsidian_file}")

    # Guardar también copia en output/linkedin/ como respaldo
    backup_dir = Path(settings.project_root) / "output" / "linkedin"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_file = backup_dir / "Ideas_LinkedIn.md"
    backup_file.write_text(clean_markdown, encoding="utf-8")

    return obsidian_file, clean_markdown


if __name__ == "__main__":
    generate_linkedin_content()
