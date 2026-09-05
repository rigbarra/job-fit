import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime

from config.settings import settings
from src.cv_engine.builder import build_cv_tex, load_profile
from src.database.models import CVSnapshot, Job, MatchResult
from src.database.repository import save_cv_snapshot

logger = logging.getLogger(__name__)


def compile_tex_to_pdf(tex_content: str, output_pdf_path: str) -> str:
    """
    Compila código fuente LaTeX a PDF utilizando pdflatex en un entorno temporal aislado.

    Args:
        tex_content: Código fuente .tex completo.
        output_pdf_path: Ruta de destino final para el archivo .pdf.

    Returns:
        str: Ruta absoluta del PDF generado.
    """
    pdflatex_bin = shutil.which("pdflatex")
    if not pdflatex_bin:
        error_msg = "pdflatex no está instalado en el sistema. Asegúrate de instalar TeX Live (ej: apt-get install texlive-latex-base texlive-latex-extra)."
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_tex_path = os.path.join(temp_dir, "cv.tex")
        temp_pdf_path = os.path.join(temp_dir, "cv.pdf")
        temp_log_path = os.path.join(temp_dir, "cv.log")

        # Escribir código LaTeX en archivo temporal
        with open(temp_tex_path, "w", encoding="utf-8") as f:
            f.write(tex_content)

        # Ejecutar pdflatex (primera pasada)
        cmd = [pdflatex_bin, "-interaction=nonstopmode", "-halt-on-error", "cv.tex"]

        logger.info(f"Compilando CV con pdflatex en {temp_dir}...")
        result = subprocess.run(cmd, cwd=temp_dir, capture_output=True, text=True, check=False)

        # Si falló la compilación o no se generó el PDF
        if result.returncode != 0 or not os.path.exists(temp_pdf_path):
            log_tail = ""
            if os.path.exists(temp_log_path):
                try:
                    with open(temp_log_path, encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()
                        # Extraer las últimas 25 líneas con errores
                        log_tail = "".join(lines[-25:])
                except Exception:
                    pass
            error_details = log_tail or result.stdout or result.stderr
            logger.error(f"Error compilando documento LaTeX con pdflatex:\n{error_details}")
            raise RuntimeError(
                f"Fallo en compilación de pdflatex (código {result.returncode}):\n{error_details}"
            )

        # Segunda pasada rápida para resolver cualquier advertencia de rerun / hyperref
        subprocess.run(cmd, cwd=temp_dir, capture_output=True, text=True, check=False)

        # Asegurar directorio de salida y copiar artefactos
        dest_pdf_dir = os.path.dirname(os.path.abspath(output_pdf_path))
        os.makedirs(dest_pdf_dir, exist_ok=True)

        shutil.copyfile(temp_pdf_path, output_pdf_path)

        # Guardar también el código .tex generado junto al PDF para trazabilidad
        output_tex_path = os.path.splitext(output_pdf_path)[0] + ".tex"
        shutil.copyfile(temp_tex_path, output_tex_path)

        logger.info(f"PDF generado y guardado exitosamente en: {output_pdf_path}")
        return output_pdf_path


def detect_job_language(job: Job) -> str:
    """
    Detecta de forma heurística si una vacante está redactada en inglés o español.
    """
    text = f"{job.title} {job.description}".lower()
    english_markers = [
        "experience",
        "requirements",
        "skills",
        "responsibilities",
        "looking for",
        "years of",
        "full-time",
        "remote",
    ]
    spanish_markers = [
        "experiencia",
        "requisitos",
        "habilidades",
        "responsabilidades",
        "buscamos",
        "años de",
        "jornada",
        "remoto",
    ]

    en_count = sum(1 for w in english_markers if w in text)
    es_count = sum(1 for w in spanish_markers if w in text)

    if en_count > es_count:
        return "en"
    return "es"


def sanitize_filename(name: str, max_words: int = 4, max_len: int = 35) -> str:
    """Sanitiza nombres de archivo eliminando caracteres especiales, limitando palabras y longitud."""
    clean = re.sub(r"[^\w\s-]", "", name or "", flags=re.UNICODE)
    words = clean.strip().split()
    if len(words) > max_words:
        clean = " ".join(words[:max_words])
    slug = re.sub(r"[-\s]+", "_", clean).strip("_")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("_")
    return slug or "General"


def generate_cv_for_job(
    job: Job, match_result: MatchResult, language: str | None = None
) -> CVSnapshot:
    """
    Genera y compila el CV adaptado (o base) para una vacante clasificada como Tier 1 o Tier 2.
    Registra el CVSnapshot correspondiente en la base de datos.
    """
    if language is None:
        language = detect_job_language(job)

    logger.info(
        f"Generando CV en idioma '{language}' para vacante {job.id} ('{job.title}' @ '{job.company}') [Tier {match_result.tier}]..."
    )

    # 1. Renderizar código fuente LaTeX
    tex_content = build_cv_tex(match_result=match_result, language=language, job=job)

    # 2. Construir nombre del archivo: {yymmdd}_CV_{cand_slug}_{cargo}_{empresa}.pdf
    date_str = datetime.now().strftime("%y%m%d")
    role_slug = sanitize_filename(job.title, max_words=4, max_len=30)
    company_slug = sanitize_filename(job.company, max_words=3, max_len=20)
    profile = load_profile(language=language)
    cand_parts = profile.get("name", "Candidate").strip().split()
    cand_slug = f"{cand_parts[0][0]}{cand_parts[-1]}" if len(cand_parts) > 1 else (cand_parts[0] if cand_parts else "Candidate")
    filename = f"{date_str}_CV_{cand_slug}_{role_slug}_{company_slug}.pdf"

    output_dir = os.path.join(settings.project_root, settings.output_pdf_dir.lstrip("./"))
    output_pdf_path = os.path.join(output_dir, filename)
    output_tex_path = os.path.splitext(output_pdf_path)[0] + ".tex"

    # 3. Compilar PDF
    compile_tex_to_pdf(tex_content, output_pdf_path)

    # 4. Registrar snapshot en base de datos
    snapshot = CVSnapshot(job_id=job.id, pdf_path=output_pdf_path, tex_path=output_tex_path)
    saved_snapshot = save_cv_snapshot(snapshot)

    logger.info(f"CVSnapshot ID={saved_snapshot.id} registrado para vacante {job.id}.")
    return saved_snapshot
