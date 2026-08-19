import json
import logging
import os
from typing import Any

from curl_cffi import requests

from config.settings import settings
from src.agent.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, MatchEvaluation
from src.agent.quota import LLMQuotaManager, RateLimitError
from src.database.models import Job, MatchResult

logger = logging.getLogger(__name__)


def normalize_llm_json(raw_json_str: str) -> dict[str, Any]:
    """
    Parsea y normaliza respuestas JSON del LLM manejando sinónimos de campos comunes
    y variaciones de estructura devueltas por modelos gratuitos (ej. match_score -> score).
    """
    data = json.loads(raw_json_str)

    if not isinstance(data, dict):
        raise ValueError(f"El LLM retornó un tipo no JSON dict: {type(data)}")

    # 1. Normalizar score (score, match_score, matchScore, match_percentage, etc.)
    score_val = None
    for key in ["score", "match_score", "matchScore", "match_percentage", "compatibility_score"]:
        if key in data and isinstance(data[key], (int, float)):
            score_val = float(data[key])
            break
    if score_val is None:
        score_val = 0.0

    # 2. Normalizar rationale (rationale, evaluation, justification, summary, explanation)
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

    # 4. Normalizar adapted_summary
    adapted_summary = (
        data.get("adapted_summary") or data.get("adaptedSummary") or data.get("resumen_adaptado")
    )
    if not isinstance(adapted_summary, str):
        adapted_summary = None

    # 5. Normalizar adapted_bullets (dict, list[dict], or list[str])
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
        "adapted_summary": adapted_summary,
        "adapted_bullets": normalized_bullets if normalized_bullets else None,
    }


def evaluate_job(job: Job, profile_path: str | None = None) -> MatchResult:
    """
    Evalúa la compatibilidad de una vacante frente al perfil del candidato
    usando la API de OpenRouter y controlando cuotas.
    """
    if (
        not settings.openrouter_api_key
        or settings.openrouter_api_key == "tu_api_key_de_openrouter_aqui"
    ):
        raise RuntimeError(
            "OpenRouter API: OPENROUTER_API_KEY no está configurada. Configúrala en el archivo .env."
        )

    if not profile_path:
        profile_path = os.path.join(settings.project_root, "config", "profile.yaml")

    try:
        with open(profile_path, encoding="utf-8") as f:
            profile_text = f.read()
    except Exception as e:
        logger.error(f"No se pudo cargar el perfil del candidato en {profile_path}: {e}")
        raise FileNotFoundError(f"Perfil del candidato no encontrado: {profile_path}")

    user_prompt = USER_PROMPT_TEMPLATE.format(
        candidate_profile=profile_text, job_description=job.description
    )

    def _make_openrouter_call():
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/rigbarra/job-fit",
            "X-Title": "Job Fit",
        }

        payload = {
            "model": settings.openrouter_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=60,
            )

            if response.status_code == 429:
                raise RateLimitError("OpenRouter API: 429 Too Many Requests")
            elif response.status_code != 200:
                raise RuntimeError(
                    f"OpenRouter API retornó error {response.status_code}: {response.text}"
                )

            return response.json()

        except Exception as err:
            if isinstance(err, RateLimitError):
                raise err
            raise RuntimeError(f"Error de red/conexión con OpenRouter: {err}")

    logger.info(
        f"OpenRouter: Evaluando vacante '{job.title}' @ '{job.company}' usando el modelo '{settings.openrouter_model}'..."
    )

    response_data = LLMQuotaManager.call_with_retry(_make_openrouter_call)

    try:
        content_text = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        logger.error(f"OpenRouter: Payload de respuesta inesperado: {response_data}")
        raise ValueError(f"Respuesta inesperada de OpenRouter: {e}")

    try:
        normalized_data = normalize_llm_json(content_text)
        evaluation = MatchEvaluation.model_validate(normalized_data)
    except Exception as e:
        logger.error(f"OpenRouter: Error de validación de esquema en la respuesta JSON: {e}")
        logger.debug(f"Texto JSON crudo recibido: {content_text}")
        raise ValueError(f"La respuesta de OpenRouter no cumple con el esquema requerido: {e}")

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
        if evaluation.adapted_bullets
        else None
    )

    return MatchResult(
        job_id=job.id,
        score=score,
        tier=tier,
        rationale=evaluation.rationale,
        missing_keywords=missing_keywords_json,
        adapted_summary=evaluation.adapted_summary,
        adapted_bullets=adapted_bullets_json,
    )
