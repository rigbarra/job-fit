import logging
import os
import re
from datetime import datetime

from config.settings import settings
from src.cover_engine.builder import build_cover_letter_tex
from src.cv_engine.compiler import compile_tex_to_pdf, detect_job_language, sanitize_filename
from src.database.models import Job

logger = logging.getLogger(__name__)


def generate_cover_letter_for_job(
    job: Job,
    language: str | None = None,
    output_dir: str | None = None,
) -> tuple[str, str]:
    """
    Genera el archivo TeX y compila el PDF de la Carta de Presentación adaptada para una vacante.

    Returns:
        tuple[str, str]: (Ruta absoluta del PDF generado, Ruta absoluta del archivo .tex)
    """
    job_lang = language or detect_job_language(job)
    tex_content = build_cover_letter_tex(job, language=job_lang)

    out_directory = output_dir or settings.output_pdf_dir
    os.makedirs(out_directory, exist_ok=True)

    timestamp = datetime.now().strftime("%y%m%d")
    company_clean = sanitize_filename(job.company, max_words=3, max_len=20)
    title_clean = sanitize_filename(job.title, max_words=4, max_len=30)

    filename_base = f"CoverLetter_Rigoberto_Barra_{title_clean}_{company_clean}_{timestamp}"
    tex_path = os.path.join(out_directory, f"{filename_base}.tex")
    pdf_path = os.path.join(out_directory, f"{filename_base}.pdf")

    # Guardar el archivo TeX
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex_content)

    logger.info(f"CoverEngine: Compilando Carta de Presentación en {pdf_path}...")
    compiled_pdf = compile_tex_to_pdf(tex_content, pdf_path)

    return compiled_pdf, tex_path
