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
    logger.info(f"Score: {match_result.score:.1f} / 100 pts | Tier: {match_result.tier}")

    logger.info("--- 2. Generando CV Adaptado (PDF) ---")
    cv_snapshot = generate_cv_for_job(job, match_result)
    cv_pdf = cv_snapshot.pdf_path
    logger.info(f"CV generado: {cv_pdf}")

    # Copiar exclusivamente este CV puntual a la carpeta Descargas de Windows
    import os
    import shutil
    from pathlib import Path
    from src.obsidian_exporter import get_windows_downloads_dir, to_windows_display_path
    downloads_dir = get_windows_downloads_dir()
    win_pdf_path = None
    if downloads_dir and downloads_dir.exists() and cv_pdf and os.path.exists(cv_pdf):
        dest_file = downloads_dir / Path(cv_pdf).name
        shutil.copy2(cv_pdf, dest_file)
        win_pdf_path = to_windows_display_path(dest_file)

    # Sincronizar automáticamente la tarjeta en Obsidian
    try:
        from src.obsidian_exporter import sync_obsidian_vault
        sync_obsidian_vault()
    except Exception as ex:
        logger.warning(f"No se pudo sincronizar Obsidian: {ex}")

    print("\n¡Postulación preparada exitosamente!")
    if win_pdf_path:
        print(f"📥 CV adaptado descargado en tu carpeta de Windows:\n   {win_pdf_path}\n")
    else:
        print(f"📄 CV PDF generado en:\n   {cv_pdf}\n")


def handle_interview(args):
    """Genera la Guía de Entrevista Técnica para una vacante."""
    init_db()
    job = _get_or_create_job(args.target)
    if not job:
        logger.error(f"No se encontró la vacante para '{args.target}'")
        sys.exit(1)

    logger.info(f"--- Generando Guía de Entrevista Técnica para '{job.title}' @ '{job.company}' ---")
    prep_md = generate_interview_prep(job)
    print(f"\nGuía de Entrevista Técnica generada exitosamente en:\n{prep_md}")


def handle_cover_letter(args):
    """Genera la Carta de Presentación en PDF para una vacante."""
    init_db()
    job = _get_or_create_job(args.target)
    if not job:
        logger.error(f"No se encontró la vacante para '{args.target}'")
        sys.exit(1)

    logger.info(f"--- Generando Carta de Presentación para '{job.title}' @ '{job.company}' ---")
    cover_pdf, cover_tex = generate_cover_letter_for_job(job)
    print(f"\nCarta de Presentación PDF generada en:\n{cover_pdf}")


from src.market_engine.analytics import generate_market_study_report


def handle_market_study(args):
    """Genera el informe de Estudio de Mercado y Estadísticas Salariales."""
    init_db()
    logger.info("--- Generando Estudio de Mercado y Análisis Salarial ---")
    file_path, report_text = generate_market_study_report()
    if file_path:
        print(f"\nReporte de Mercado Markdown generado en:\n{file_path}\n")
        print(report_text)
        print("\n" + "=" * 60)
        print("💡 Dashboard Interactivo Streamlit (Catppuccin Dark):")
        print("   Para abrir el informe visual en tu navegador, ejecuta:")
        print("   .venv/bin/streamlit run src/market_engine/app.py")
        print("=" * 60 + "\n")

    if getattr(args, "web", False):
        import subprocess
        logger.info("Lanzando Dashboard de Streamlit...")
        subprocess.run([sys.executable, "-m", "streamlit", "run", "src/market_engine/app.py"])


def handle_clean(args):
    """Ejecuta la purga de archivos y datos temporales con antigüedad mayor a N días."""
    init_db()
    days = getattr(args, "days", 90)
    logger.info(f"--- Ejecutando Purga y Limpieza de Datos Temporales (> {days} días) ---")
    from src.cleaner import clean_all_temporary_data
    summary = clean_all_temporary_data(days=days)
    print("\n¡Limpieza completada!")
    print(f"  • CVs/TeX eliminados en disco: {summary['cv_files_removed']}")
    print(f"  • Guías de entrevista eliminadas: {summary['interview_files_removed']}")
    print(f"  • Archivos temporales en tmp: {summary['tmp_files_removed']}")
    print(f"  • Snapshots de CV eliminados en BD: {summary['db_cv_snapshots_removed']}")
    print(f"  • Vacantes descartadas antiguas eliminadas en BD: {summary['db_tier3_jobs_removed']}\n")


def handle_obsidian(args):
    """Sincroniza la base de datos de empleos con el Vault de Obsidian (Markdown + Kanban)."""
    init_db()
    logger.info("--- Sincronizando Vault de Obsidian (Kanban + Markdown) ---")
    from src.obsidian_exporter import sync_obsidian_vault
    res = sync_obsidian_vault()
    print("\n¡Sincronización con Obsidian completada!")
    print(f"  • Fichas exportadas en jobs/: {res['cards_created']}")
    print(f"  • Tablero Kanban actualizado en: {res['kanban_file']}\n")


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
    p_market.add_argument("--web", action="store_true", help="Lanza el dashboard visual interactivo en Streamlit")
    p_market.set_defaults(func=handle_market_study)

    # Comando: clean
    p_clean = subparsers.add_parser("clean", help="Limpia archivos y registros temporales antiguos (> 90 días)")
    p_clean.add_argument("--days", type=int, default=90, help="Días de antigüedad máxima para mantener data temporal (default: 90)")
    p_clean.set_defaults(func=handle_clean)

    # Comando: sync-obsidian
    p_obsidian = subparsers.add_parser("sync-obsidian", help="Exporta postulaciones a Obsidian Vault y Kanban")
    p_obsidian.set_defaults(func=handle_obsidian)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

