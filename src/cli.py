import argparse
import sys
import logging

from src.database.repository import get_job_by_id, init_db, save_job
from src.database.models import Job
from src.agent.evaluator import evaluate_job
from src.cv_engine.compiler import generate_cv_for_job
from src.cover_engine.compiler import generate_cover_letter_for_job
from src.interview_engine.generator import generate_interview_prep

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("job-fit-cli")


def handle_scrape(args):
    """Ejecuta el pipeline principal de ingesta y scraping."""
    from src.main import main as run_main
    run_main()


def handle_apply(args):
    """Genera la evaluación, el CV adaptado y la Carta de Presentación para una vacante."""
    init_db()
    job = _get_or_create_job(args.target)
    if not job:
        logger.error(f"No se encontró la vacante para '{args.target}'")
        sys.exit(1)

    logger.info(f"--- 1. Evaluando Fit para '{job.title}' @ '{job.company}' ---")
    match_result = evaluate_job(job)
    logger.info(f"Score: {match_result.score}% | Tier: {match_result.tier}")

    logger.info("--- 2. Generando CV Adaptado (PDF) ---")
    cv_snapshot = generate_cv_for_job(job, match_result)
    cv_pdf = cv_snapshot.pdf_path
    logger.info(f"CV generado: {cv_pdf}")

    logger.info("--- 3. Generando Carta de Presentación (PDF) ---")
    cover_pdf, cover_tex = generate_cover_letter_for_job(job)
    logger.info(f"Carta de Presentación generada: {cover_pdf}")

    print("\n✅ ¡Aplicación preparada exitosamente!")
    print(f"📄 CV PDF: {cv_pdf}")
    print(f"✉️ Carta de Presentación PDF: {cover_pdf}")


def handle_interview(args):
    """Genera la Guía de Entrevista Técnica para una vacante."""
    init_db()
    job = _get_or_create_job(args.target)
    if not job:
        logger.error(f"No se encontró la vacante para '{args.target}'")
        sys.exit(1)

    logger.info(f"--- Generando Guía de Entrevista Técnica para '{job.title}' @ '{job.company}' ---")
    prep_md = generate_interview_prep(job)
    print(f"\n🎯 Guía de Entrevista Técnica generada exitosamente en:\n{prep_md}")


def handle_cover_letter(args):
    """Genera la Carta de Presentación en PDF para una vacante."""
    init_db()
    job = _get_or_create_job(args.target)
    if not job:
        logger.error(f"No se encontró la vacante para '{args.target}'")
        sys.exit(1)

    logger.info(f"--- Generando Carta de Presentación para '{job.title}' @ '{job.company}' ---")
    cover_pdf, cover_tex = generate_cover_letter_for_job(job)
    print(f"\n✉️ Carta de Presentación PDF generada en:\n{cover_pdf}")


from src.market_engine.analytics import generate_market_study_report


def handle_market_study(args):
    """Genera el informe de Estudio de Mercado y Estadísticas Salariales."""
    init_db()
    logger.info("--- Generando Estudio de Mercado y Análisis Salarial ---")
    file_path, report_text = generate_market_study_report()
    if file_path:
        print(f"\n📊 Reporte de Mercado generado exitosamente en:\n{file_path}\n")
        print(report_text)
    else:
        print(f"\n⚠️ {report_text}")


def _get_or_create_job(target: str) -> Job | None:
    if target.isdigit():
        return get_job_by_id(int(target))
    elif target.startswith("http"):
        # Crear un objeto Job ad-hoc para la URL/descripción
        return Job(
            title="Oferta Personalizada",
            company="Empresa Destino",
            location="Chile / Remote",
            description=f"Postulación directa para la URL: {target}",
            url=target,
            source="manual",
        )
    else:
        return Job(
            title="Oferta Personalizada",
            company="Empresa Destino",
            location="Chile / Remote",
            description=target,
            url="https://manual-application.local",
            source="manual",
        )


def main():
    parser = argparse.ArgumentParser(prog="job-fit", description="CLI de gestión de postulaciones laboral para Data Engineering")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Comando: scrape
    p_scrape = subparsers.add_parser("scrape", help="Ejecuta la ingesta de ofertas en Chile y LATAM")
    p_scrape.set_defaults(func=handle_scrape)

    # Comando: apply
    p_apply = subparsers.add_parser("apply", help="Genera CV + Carta de Presentación adaptada para una vacante")
    p_apply.add_argument("target", help="ID de la vacante en la BD o URL/descripción")
    p_apply.set_defaults(func=handle_apply)

    # Comando: interview
    p_interview = subparsers.add_parser("interview", help="Genera la Guía de Entrevista Técnica para una vacante")
    p_interview.add_argument("target", help="ID de la vacante en la BD o URL/descripción")
    p_interview.set_defaults(func=handle_interview)

    # Comando: cover-letter
    p_cover = subparsers.add_parser("cover-letter", help="Genera únicamente la Carta de Presentación")
    p_cover.add_argument("target", help="ID de la vacante en la BD o URL/descripción")
    p_cover.set_defaults(func=handle_cover_letter)

    # Comando: market-study
    p_market = subparsers.add_parser("market-study", help="Genera reporte analítico de sueldos y tendencias de mercado")
    p_market.set_defaults(func=handle_market_study)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
