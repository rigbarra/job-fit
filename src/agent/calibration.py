import logging
import re
from datetime import UTC, datetime, timedelta
from sqlmodel import Session, select

from config.settings import load_config
from src.database.models import Job, MatchResult
from src.database.repository import (
    engine,
    get_reviewed_calibration_job_ids,
    init_db,
    save_calibration_feedback,
)

logger = logging.getLogger(__name__)


def detect_filter_phase(rationale: str | None, score: float) -> tuple[str, str]:
    """
    Analiza la justificación y score para determinar qué fase o regla frenó la vacante.
    Retorna (código_fase, descripción_legible).
    """
    if not rationale:
        return ("UNKNOWN", "Sin justificación registrada")

    rat_lower = rationale.lower()

    if "publicada hace" in rat_lower:
        return ("AGE", "Ventana de antigüedad móvil (max_job_age_days)")

    if "no contiene palabras clave de datos" in rat_lower:
        return ("TITLE_KEYWORDS", "Título no contiene palabras clave requeridas (title_keywords_any)")

    if "término excluido" in rat_lower or "rol de liderazgo" in rat_lower or "excluido" in rat_lower:
        return ("TITLE_BLACKLIST", "Título contiene término excluido (title_blacklist)")

    if "descripción no contiene" in rat_lower:
        return ("DESCRIPTION_KEYWORDS", "Descripción sin tecnologías mínimas (description_keywords_any)")

    if "presencial" in rat_lower or "híbrida/física" in rat_lower or "elegibilidad territorial" in rat_lower:
        return ("LOCATION_MODALITY", "Modalidad presencial no viable o territorio fuera de Chile/LATAM")

    if score > 10.0 and score < 75.0:
        return ("LLM_SCORE", f"Evaluación LLM no alcanzó umbral de notificación ({score:.1f} / 100 pts)")

    if "ubicación fuera de chile" in rat_lower:
        return ("LOCATION_MODALITY", "Ubicación internacional fuera del scope")

    return ("OTHER", rationale[:80])


def get_candidates_for_calibration(
    days: int = 7, limit: int = 20, include_reviewed: bool = False
) -> list[tuple[Job, MatchResult, str, str]]:
    """
    Obtiene vacantes recientes candidatas para calibración, priorizando
    aquellas de Chile y zonas limítrofes (falsos negativos potenciales).
    """
    init_db()
    cutoff = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(days=days)
    reviewed_ids = set() if include_reviewed else get_reviewed_calibration_job_ids()

    with Session(engine) as session:
        statement = (
            select(Job, MatchResult)
            .join(MatchResult, Job.id == MatchResult.job_id)
            .where(Job.created_at >= cutoff)
            .order_by(Job.id.desc())
        )
        results = session.exec(statement).all()

    candidates: list[tuple[Job, MatchResult, str, str]] = []

    for job, match in results:
        if job.id in reviewed_ids:
            continue

        phase_code, phase_desc = detect_filter_phase(match.rationale, match.score)

        # Dar prioridad a:
        # 1. Vacantes en Chile o con palabras clave de datos que fueron descartadas
        # 2. Vacantes evaluadas por LLM con score medio (30 - 74)
        is_chile = "chile" in (job.location or "").lower() or (job.country or "").lower() == "chile"
        is_borderline_llm = 25.0 <= match.score < 75.0
        is_age_discard = phase_code == "AGE"

        priority_score = 0
        if is_chile:
            priority_score += 10
        if is_age_discard:
            priority_score += 8
        if is_borderline_llm:
            priority_score += 6
        if phase_code in ("TITLE_KEYWORDS", "DESCRIPTION_KEYWORDS"):
            priority_score += 4

        candidates.append((priority_score, job, match, phase_code, phase_desc))

    # Ordenar por prioridad descendente
    candidates.sort(key=lambda x: x[0], reverse=True)

    return [(job, match, code, desc) for _, job, match, code, desc in candidates[:limit]]


def extract_tech_preview(description: str) -> list[str]:
    """Extrae una lista breve de tecnologías detectadas en el texto para previsualización."""
    keywords = [
        "python", "sql", "power bi", "tableau", "dbt", "aws", "gcp", "azure",
        "bigquery", "snowflake", "spark", "pyspark", "airflow", "dagster",
        "databricks", "looker", "docker", "git", "genai", "llm", "rag"
    ]
    desc_lower = description.lower()
    return [kw for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", desc_lower)]


def format_clean_description(raw: str) -> list[str]:
    """Limpia tags HTML y formatea la descripción en líneas de texto legibles."""
    if not raw:
        return []
    clean = re.sub(r"</?(li|p|br|h\d|div)[^>]*>", "\n", raw, flags=re.IGNORECASE)
    clean = re.sub(r"<[^>]+>", "", clean)
    return [
        line.strip()
        for line in clean.splitlines()
        if line.strip() and not line.strip().startswith("###") and not line.strip().startswith("---")
    ]


def run_interactive_calibration(limit: int = 15, days: int = 7) -> dict:
    """
    Ejecuta el ciclo de calibración interactivo en terminal para que el usuario
    califique ofertas descartadas o en zona gris y genere recomendaciones.
    """
    candidates = get_candidates_for_calibration(days=days, limit=limit)

    if not candidates:
        print("\n✨ ¡No hay vacantes pendientes de revisión en los últimos {} días!".format(days))
        print("Todas las vacantes recientes ya han sido revisadas o no hay datos nuevos.")
        return {"total_reviewed": 0, "interested": 0, "discarded": 0}

    print("\n" + "=" * 80)
    print("🎯 MODO DE CALIBRACIÓN INTERACTIVA DE FIT (job-fit)")
    print("=" * 80)
    print(f"Revisando hasta {len(candidates)} vacantes de los últimos {days} días.")
    print("Objetivo: Enseñar al sistema qué vacantes te interesan para calibrar filtros y prompts.")
    print("Comandos: [1] Me interesa | [2] Bien descartada | [d] Ver descripción completa | [3] Saltar | [q] Salir")
    print("=" * 80)

    reviewed_count = 0
    interested_jobs: list[tuple[Job, MatchResult, str, str, str]] = []
    discarded_jobs: list[tuple[Job, MatchResult, str, str, str]] = []

    for idx, (job, match, phase_code, phase_desc) in enumerate(candidates, 1):
        print(f"\n[{idx}/{len(candidates)}] " + "-" * 70)
        print(f"📌 Cargo:     \033[1m{job.title}\033[0m")
        print(f"🏢 Empresa:   {job.company}")
        print(f"📍 Ubicación: {job.location} | Modalidad: {job.modality or 'No especificada'} | Fuente: {job.source}")
        if job.salary:
            print(f"💰 Salario:   {job.salary}")

        techs = extract_tech_preview(job.description or "")
        if techs:
            print(f"🛠️  Stack:     {', '.join(techs)}")

        print(f"🛑 Estado:    Score {match.score:.1f} / 100 (Tier {match.tier})")
        print(f"🔍 Motivo:    {match.rationale}")

        desc_lines = format_clean_description(job.description or "")
        if desc_lines:
            print("📋 Requisitos / Resumen:")
            for line in desc_lines[:5]:
                print(f"   • {line}")
            if len(desc_lines) > 5:
                print(f"   ... ({len(desc_lines) - 5} líneas más, presiona 'd' para ver completa)")

        print(f"🔗 Enlace:    {job.url}")
        print("-" * 75)

        while True:
            try:
                choice = input("👉 ¿Te interesa esta vacante? [1=Sí / 2=No / d=Ver completa / 3=Saltar / q=Salir]: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                choice = "q"

            if choice == "d":
                print("\n" + "─" * 35 + " DESCRIPCIÓN COMPLETA " + "─" * 35)
                for line in desc_lines:
                    print(f"  {line}")
                print("─" * 92 + "\n")
                continue

            if choice in ("1", "2", "3", "q"):
                break
            print("Entrada no válida. Escribe 1, 2, d, 3 o q.")

        if choice == "q":
            print("\nFinalizando sesión de calibración...")
            break

        if choice == "3":
            print("Omitida.")
            continue

        # Opción 1 o 2: comentario opcional
        try:
            user_comment = input("💬 Comentario breve de contexto (Opcional, presiona ENTER para omitir): ").strip()
        except (KeyboardInterrupt, EOFError):
            user_comment = ""

        decision = "INTERESTED" if choice == "1" else "DISCARDED"

        save_calibration_feedback(
            job_id=job.id,
            decision=decision,
            filter_phase=phase_code,
            user_comment=user_comment or None,
        )

        reviewed_count += 1
        if decision == "INTERESTED":
            interested_jobs.append((job, match, phase_code, phase_desc, user_comment))
            print("✅ Registrada como: ME INTERESA (Falso Negativo a corregir)")
        else:
            discarded_jobs.append((job, match, phase_code, phase_desc, user_comment))
            print("🚫 Registrada como: BIEN DESCARTADA (Verdadero Negativo confirmado)")

    # ──────────────────────────────────────────────────────────────────────────
    # Reporte de Diagnóstico y Recomendaciones de Calibración
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("📊 REPORTE DE DIAGNÓSTICO DE CALIBRACIÓN")
    print("=" * 80)
    print(f"Total vacantes calificadas en esta sesión: {reviewed_count}")
    print(f"  • Marcadas como 'Me interesa':  {len(interested_jobs)}")
    print(f"  • Marcadas como 'Descartar':    {len(discarded_jobs)}")
    print("-" * 80)

    if interested_jobs:
        print("\n🔍 ANÁLISIS DE BARRERAS (¿Por qué no te llegaron las que sí te interesaban?):")

        # Agrupar por fase
        phase_counts: dict[str, list[tuple[Job, MatchResult, str, str, str]]] = {}
        for item in interested_jobs:
            p_code = item[2]
            phase_counts.setdefault(p_code, []).append(item)

        for p_code, items in phase_counts.items():
            first = items[0]
            print(f"\n📁 Barrera: {first[3]} ({len(items)} casos)")
            for j, m, _, _, comment in items:
                note_str = f" [Tu nota: '{comment}']" if comment else ""
                print(f"   - {j.title} @ {j.company} (ID: {j.id}){note_str}")

        print("\n💡 RECOMENDACIONES TÉCNICAS SUGERIDAS:")
        if "AGE" in phase_counts:
            print("  1. [Ventana de Antigüedad]: Detectamos ofertas vigentes en Get on Board/LinkedIn frenadas por antigüedad.")
            print("     -> Sugerencia: Aumentar 'max_job_age_days' en config/config.yaml (ej. a 4 o 7 días).")

        if "TITLE_KEYWORDS" in phase_counts:
            titles_missed = [item[0].title for item in phase_counts["TITLE_KEYWORDS"]]
            print(f"  2. [Filtro de Título]: Cargos válidos fueron frenados por falta de keyword: {titles_missed}")
            print("     -> Sugerencia: Añadir palabras clave relevantes a 'title_keywords_any' en config/config.yaml.")

        if "DESCRIPTION_KEYWORDS" in phase_counts:
            print("  3. [Stack en Descripción]: Se frenaron vacantes cuya descripción no coincidió con el stack mínimo.")
            print("     -> Sugerencia: Ampliar herramientas en 'description_keywords_any' en config/config.yaml.")

        if "LLM_SCORE" in phase_counts:
            print("  4. [Evaluación LLM]: Gemini 3.8 Flash asignó un score menor a 75 a cargos que sí querías.")
            print("     -> Sugerencia: Calibrar prompts.py o profile.yaml para suavizar penalizaciones en esas tecnologías.")

        if "LOCATION_MODALITY" in phase_counts:
            print("  5. [Modalidad/Ubicación]: Se frenaron ofertas por modalidad híbrida o territorio.")
            print("     -> Sugerencia: Revisar si la empresa permite flexibilidad remota.")
    else:
        print("\n🎉 Todas las vacantes revisadas fueron confirmadas como descartes correctos.")
        print("Los filtros no tienen falsos negativos en este lote.")

    print("\n" + "=" * 80 + "\n")

    return {
        "total_reviewed": reviewed_count,
        "interested": len(interested_jobs),
        "discarded": len(discarded_jobs),
    }
