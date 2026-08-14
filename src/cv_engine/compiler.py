import os
import re
import shutil
import logging
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings
from src.database.models import Job, MatchResult, CVSnapshot
from src.database.repository import save_cv_snapshot
from src.cv_engine.builder import build_cv_tex

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
        cmd = [
            pdflatex_bin,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "cv.tex"
        ]
        
        logger.info(f"Compilando CV con pdflatex en {temp_dir}...")
        result = subprocess.run(
            cmd,
            cwd=temp_dir,
            capture_output=True,
            text=True,
            check=False
        )
        
        # Si falló la compilación o no se generó el PDF
        if result.returncode != 0 or not os.path.exists(temp_pdf_path):
            log_tail = ""
            if os.path.exists(temp_log_path):
                try:
                    with open(temp_log_path, "r", encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()
                        # Extraer las últimas 25 líneas con errores
                        log_tail = "".join(lines[-25:])
                except Exception:
                    pass
            error_details = log_tail or result.stdout or result.stderr
            logger.error(f"Error compilando documento LaTeX con pdflatex:\n{error_details}")
            raise RuntimeError(f"Fallo en compilación de pdflatex (código {result.returncode}):\n{error_details}")
            
        # Segunda pasada rápida para resolver cualquier advertencia de rerun / hyperref
        subprocess.run(
            cmd,
            cwd=temp_dir,
            capture_output=True,
            text=True,
            check=False
        )
        
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
    english_markers = ["experience", "requirements", "skills", "responsibilities", "looking for", "years of", "full-time", "remote"]
    spanish_markers = ["experiencia", "requisitos", "habilidades", "responsabilidades", "buscamos", "años de", "jornada", "remoto"]
    
    en_count = sum(1 for w in english_markers if w in text)
    es_count = sum(1 for w in spanish_markers if w in text)
    
    if en_count > es_count:
        return "en"
    return "es"

def generate_cv_for_job(
    job: Job, 
    match_result: MatchResult, 
    language: Optional[str] = None
) -> CVSnapshot:
    """
    Genera y compila el CV adaptado (o base) para una vacante clasificada como Tier 1 o Tier 2.
    Registra el CVSnapshot correspondiente en la base de datos.
    """
    if language is None:
        language = detect_job_language(job)
        
    logger.info(f"Generando CV en idioma '{language}' para vacante {job.id} ('{job.title}' @ '{job.company}') [Tier {match_result.tier}]...")
    
    # 1. Renderizar código fuente LaTeX
    tex_content = build_cv_tex(match_result=match_result, language=language)
    
    # 2. Construir nombre del archivo de salida
    sanitized_company = re.sub(r'[^a-zA-Z0-9_-]', '_', job.company).strip('_')
    date_str = datetime.now().strftime("%Y%m%d")
    tier_label = f"T{match_result.tier}"
    filename = f"CV_Rigoberto_Barra_{sanitized_company}_{job.id}_{tier_label}_{language}_{date_str}.pdf"
    
    output_dir = os.path.join(settings.project_root, settings.output_pdf_dir.lstrip("./"))
    output_pdf_path = os.path.join(output_dir, filename)
    output_tex_path = os.path.splitext(output_pdf_path)[0] + ".tex"
    
    # 3. Compilar PDF
    compile_tex_to_pdf(tex_content, output_pdf_path)
    
    # 4. Registrar snapshot en base de datos
    snapshot = CVSnapshot(
        job_id=job.id,
        pdf_path=output_pdf_path,
        tex_path=output_tex_path
    )
    saved_snapshot = save_cv_snapshot(snapshot)
    
    logger.info(f"CVSnapshot ID={saved_snapshot.id} registrado para vacante {job.id}.")
    return saved_snapshot
