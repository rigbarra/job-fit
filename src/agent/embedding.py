import logging
from typing import Any
import yaml
from pathlib import Path

from config.settings import settings, get_profile_path

logger = logging.getLogger(__name__)

_model_instance = None
_cached_candidate_text = None
_cached_candidate_vector = None


def get_embedding_model() -> Any | None:
    """Obtiene o inicializa la instancia singleton del modelo local FastEmbed."""
    global _model_instance
    if _model_instance is None:
        try:
            from fastembed import TextEmbedding

            logger.info("Cargando modelo local de Embeddings (fastembed: BAAI/bge-small-en-v1.5)...")
            _model_instance = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        except Exception as e:
            logger.warning(f"No se pudo inicializar fastembed local: {e}. Se omitirá el pre-filtro vectorial.")
            return None
    return _model_instance


def get_candidate_profile_text() -> str:
    """Construye un resumen técnico representativo del perfil desde profile.yaml."""
    profile_path = get_profile_path()
    try:
        with open(profile_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return "Senior Data Engineer & Analytics Engineer specializing in Python, SQL, dbt, Dagster, Docker, AWS, GCP, Power BI."

    es_data = data.get("es", {})
    summary = es_data.get("summary", "")
    title = es_data.get("title", "Data Engineer | Analytics Engineer")

    # Extraer items de skills
    skills_raw = data.get("skills", {})
    skills_str = []
    if isinstance(skills_raw, dict):
        for cat_data in skills_raw.values():
            if isinstance(cat_data, dict):
                skills_str.append(cat_data.get("items", ""))
            elif isinstance(cat_data, list):
                skills_str.extend(cat_data)

    skills_joined = ", ".join(filter(None, skills_str))
    return f"{title}. {summary} Key skills: {skills_joined}"


def compute_semantic_similarity(job_text: str) -> float | None:
    """
    Calcula la similitud coseno semántica (0.0 a 100.0 pts) entre el perfil del candidato
    y el texto de la vacante utilizando el modelo vectorial local.
    """
    global _cached_candidate_text, _cached_candidate_vector

    model = get_embedding_model()
    if not model or not job_text or not job_text.strip():
        return None

    try:
        import numpy as np

        cand_text = get_candidate_profile_text()

        # Cachear vector del candidato
        if _cached_candidate_vector is None or _cached_candidate_text != cand_text:
            _cached_candidate_text = cand_text
            cand_embeds = list(model.embed([cand_text]))
            _cached_candidate_vector = cand_embeds[0]

        job_embeds = list(model.embed([job_text[:1500]]))
        v_job = job_embeds[0]
        v_cand = _cached_candidate_vector

        norm_cand = np.linalg.norm(v_cand)
        norm_job = np.linalg.norm(v_job)

        if norm_cand == 0 or norm_job == 0:
            return 0.0

        similarity = float(np.dot(v_cand, v_job) / (norm_cand * norm_job))
        score_pts = max(0.0, min(100.0, similarity * 100.0))
        return score_pts
    except Exception as e:
        logger.warning(f"Error procesando similitud semántica vectorial: {e}")
        return None
