import json
import logging
import os
import re
from typing import Any

import yaml

from config.settings import settings, get_profile_path, load_config
from src.agent.prompts import build_system_prompt, USER_PROMPT_TEMPLATE, MatchEvaluation
from src.agent.providers import get_llm_provider
from src.agent.quota import RateLimitError, call_with_retry
from src.cv_engine.builder import load_profile
from src.cv_engine.compiler import detect_job_language
from src.database.models import Job, MatchResult

logger = logging.getLogger(__name__)


def normalize_llm_json(raw_json_str: str) -> dict[str, Any]:
    """
    Parsea y normaliza respuestas JSON del LLM manejando sinónimos de campos comunes
    y variaciones de estructura devueltas por modelos (ej. match_score -> score).
    """
    if not isinstance(raw_json_str, str):
        raise ValueError(f"El LLM retornó un tipo no texto: {type(raw_json_str)}")

    # Limpiar bloques <think>...</think> y bloques markdown ```json ... ``` si existieran
    clean_str = re.sub(r"<think>.*?</think>", "", raw_json_str, flags=re.DOTALL).strip()
    if "```json" in clean_str:
        clean_str = clean_str.split("```json")[1].split("```")[0].strip()
    elif "```" in clean_str:
        clean_str = clean_str.split("```")[1].split("```")[0].strip()

    data = json.loads(clean_str)

    if not isinstance(data, dict):
        raise ValueError(f"El LLM retornó un tipo no JSON dict: {type(data)}")

    # 1. Normalizar score
    score_val = None
    for key in ["score", "match_score", "matchScore", "match_percentage", "compatibility_score"]:
        if key in data and isinstance(data[key], (int, float)):
            score_val = float(data[key])
            break
    if score_val is None:
        score_val = 0.0

    # 2. Normalizar rationale
    rationale_val = ""
    for key in ["rationale", "evaluation", "justification", "explanation", "summary", "reason"]:
        if key in data and isinstance(data[key], str):
            rationale_val = data[key]
            break

    # 3. Normalizar missing_keywords
    missing_kw = []
    for key in ["missing_keywords", "missingKeywords", "missing_skills", "missing_technologies"]:
        if key in data:
            if isinstance(data[key], list):
                missing_kw = [str(x) for x in data[key]]
            elif isinstance(data[key], str):
                missing_kw = [x.strip() for x in data[key].split(",") if x.strip()]
            break

    # 4. Normalizar strengths y gaps (opcionales)
    strengths_val = data.get("strengths", [])
    if isinstance(strengths_val, str):
        strengths_val = [strengths_val]

    gaps_val = data.get("gaps", [])
    if isinstance(gaps_val, str):
        gaps_val = [gaps_val]

    # 5. Normalizar dimension_scores (opcional)
    dim_scores = data.get("dimension_scores")
    if not isinstance(dim_scores, dict):
        dim_scores = None

    # 6. Normalizar adapted_title
    adapted_title = data.get("adapted_title") or data.get("adaptedTitle")
    if not isinstance(adapted_title, str):
        adapted_title = None

    # 7. Normalizar adapted_summary
    adapted_summary = (
        data.get("adapted_summary") or data.get("adaptedSummary") or data.get("resumen_adaptado")
    )
    if not isinstance(adapted_summary, str):
        adapted_summary = None

    # 7. Normalizar adapted_bullets
    raw_bullets = (
        data.get("adapted_bullets") or data.get("adaptedBullets") or data.get("bullets_adaptadas")
    )
    normalized_bullets = {}
    if isinstance(raw_bullets, dict):
        normalized_bullets = {str(k): str(v) for k, v in raw_bullets.items()}
    elif isinstance(raw_bullets, list):
        for item in raw_bullets:
            if isinstance(item, dict):
                orig = (
                    item.get("clave") or item.get("original") or item.get("original_bullet") or ""
                )
                adap = (
                    item.get("valor")
                    or item.get("adapted")
                    or item.get("adapted_bullet")
                    or (list(item.values())[-1] if item else "")
                )
                if orig and adap:
                    normalized_bullets[str(orig)] = str(adap)
            elif isinstance(item, str):
                normalized_bullets[item] = item

    return {
        "score": score_val,
        "rationale": rationale_val,
        "missing_keywords": missing_kw,
        "strengths": strengths_val,
        "gaps": gaps_val,
        "dimension_scores": dim_scores,
        "adapted_title": adapted_title,
        "adapted_summary": adapted_summary,
        "adapted_bullets": normalized_bullets if normalized_bullets else None,
    }


def evaluate_job(
    job: Job,
    profile_path: str | None = None,
    language: str | None = None,
) -> MatchResult:
    """
    Evalúa la compatibilidad de una vacante frente al perfil del candidato
    usando el proveedor de LLM configurado (OpenRouter, Gemini o OpenAI).
    """
    # 1. Detectar idioma de la oferta laboral
    job_lang = language or detect_job_language(job)
    lang_display = "ESPAÑOL" if job_lang == "es" else "ENGLISH"

    # 2. Cargar el perfil del candidato en el idioma exacto de la vacante
    try:
        profile_data = load_profile(language=job_lang, profile_path=profile_path)
        profile_text = yaml.dump(profile_data, allow_unicode=True, sort_keys=False)
    except Exception as e:
        logger.error(f"No se pudo cargar el perfil del candidato ({job_lang}): {e}")
        if not profile_path:
            profile_path = str(get_profile_path())
        with open(profile_path, encoding="utf-8") as f:
            profile_text = f.read()

    # 3. Construir prompt con metadatos territoriales y de modalidad
    modality = job.modality
    if not modality:
        from src.agent.filter import extract_modality_and_country
        modality, _, _ = extract_modality_and_country(job.location or "", job.description or "", getattr(job, "source", ""))

    user_prompt = USER_PROMPT_TEMPLATE.format(
        job_language=lang_display,
        candidate_profile=profile_text,
        job_title=job.title,
        job_company=job.company or "Empresa Confidencial",
        job_location=job.location or "No especificada",
        job_modality=modality or "No especificada",
        job_description=job.description,
    )

    # 4. Obtener proveedor de LLM según configuración
    provider = get_llm_provider()

    def _make_llm_call():
        return provider.generate(build_system_prompt(load_config()), user_prompt)

    logger.info(
        f"LLM [{settings.llm_provider.upper() if settings.llm_provider else 'AUTO'}]: Evaluando '{job.title}' @ '{job.company}' [{lang_display}]..."
    )

    content_text = call_with_retry(_make_llm_call)

    try:
        normalized_data = normalize_llm_json(content_text)
        evaluation = MatchEvaluation.model_validate(normalized_data)
    except Exception as e:
        logger.error(f"Error de validación de esquema en respuesta LLM: {e}")
        logger.debug(f"Texto JSON crudo recibido: {content_text}")
        raise ValueError(f"La respuesta del LLM no cumple con el esquema requerido: {e}")

    score = evaluation.score
    if score >= 85.0:
        tier = 1
    elif score >= 60.0:
        tier = 2
    else:
        tier = 3

    missing_keywords_json = json.dumps(evaluation.missing_keywords, ensure_ascii=False)
    adapted_bullets_json = (
        json.dumps(evaluation.adapted_bullets, ensure_ascii=False)
        if hasattr(evaluation, "adapted_bullets") and evaluation.adapted_bullets
        else None
    )

    key_technologies_json = (
        json.dumps(evaluation.key_technologies, ensure_ascii=False)
        if evaluation.key_technologies
        else None
    )

    return MatchResult(
        job_id=job.id,
        score=score,
        tier=tier,
        rationale=evaluation.rationale,
        missing_keywords=missing_keywords_json,
        recommended_salary_ask=evaluation.recommended_salary_ask,
        key_technologies=key_technologies_json,
        seniority_level=evaluation.seniority_level,
        adapted_title=evaluation.adapted_title,
        adapted_summary=evaluation.adapted_summary,
        adapted_bullets=adapted_bullets_json,
    )
