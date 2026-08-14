import os
import json
import logging
from typing import Optional
from curl_cffi import requests

from config.settings import settings
from src.database.models import Job, MatchResult
from src.agent.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, MatchEvaluation
from src.agent.quota import LLMQuotaManager, RateLimitError

logger = logging.getLogger(__name__)

def evaluate_job(job: Job, profile_path: Optional[str] = None) -> MatchResult:
    """
    Evalúa la compatibilidad de una vacante frente al perfil del candidato
    usando la API de OpenRouter y controlando cuotas.
    """
    # Validar clave de API antes de proceder
    if not settings.openrouter_api_key or settings.openrouter_api_key == "tu_api_key_de_openrouter_aqui":
        raise RuntimeError("OpenRouter API: OPENROUTER_API_KEY no está configurada. Configúrala en el archivo .env.")
    # 1. Cargar el perfil del candidato
    if not profile_path:
        profile_path = os.path.join(settings.project_root, "config", "profile.yaml")
        
    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            profile_text = f.read()
    except Exception as e:
        logger.error(f"No se pudo cargar el perfil del candidato en {profile_path}: {e}")
        raise FileNotFoundError(f"Perfil del candidato no encontrado: {profile_path}")

    # 2. Construir los prompts
    user_prompt = USER_PROMPT_TEMPLATE.format(
        candidate_profile=profile_text,
        job_description=job.description
    )

    # 3. Definir la función que hará el request HTTP POST a OpenRouter
    def _make_openrouter_call():
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/rigbarra/job-fit",
            "X-Title": "Job Fit"
        }
        
        # Payload OpenAI-compatible para OpenRouter, especificando formato JSON
        payload = {
            "model": settings.openrouter_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {
                "type": "json_object"
            },
            "temperature": 0.1
        }
        
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 429:
                raise RateLimitError("OpenRouter API: 429 Too Many Requests")
            elif response.status_code != 200:
                raise RuntimeError(f"OpenRouter API retornó error {response.status_code}: {response.text}")
                
            return response.json()
            
        except Exception as err:
            if isinstance(err, RateLimitError):
                raise err
            raise RuntimeError(f"Error de red/conexión con OpenRouter: {err}")

    logger.info(f"OpenRouter: Evaluando vacante '{job.title}' @ '{job.company}' usando el modelo '{settings.openrouter_model}'...")
    
    # 4. Ejecutar llamada con retry y control de RPM/cuotas
    response_data = LLMQuotaManager.call_with_retry(_make_openrouter_call)
    
    # 5. Obtener texto del JSON del completions payload
    try:
        content_text = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        logger.error(f"OpenRouter: Payload de respuesta inesperado: {response_data}")
        raise ValueError(f"Respuesta inesperada de OpenRouter: {e}")

    # 6. Parsear y validar según el esquema Pydantic
    try:
        evaluation = MatchEvaluation.model_validate_json(content_text)
    except Exception as e:
        logger.error(f"OpenRouter: Error de validación de esquema en la respuesta JSON: {e}")
        logger.debug(f"Texto JSON crudo recibido: {content_text}")
        raise ValueError(f"La respuesta de OpenRouter no cumple con el esquema requerido: {e}")

    # 7. Clasificar en Tiers según score
    score = evaluation.score
    if score >= 85.0:
        tier = 1
    elif score >= 60.0:
        tier = 2
    else:
        tier = 3

    missing_keywords_json = json.dumps(evaluation.missing_keywords, ensure_ascii=False)
    adapted_bullets_json = json.dumps(evaluation.adapted_bullets, ensure_ascii=False) if evaluation.adapted_bullets else None

    # 8. Retornar MatchResult
    return MatchResult(
        job_id=job.id,
        score=score,
        tier=tier,
        rationale=evaluation.rationale,
        missing_keywords=missing_keywords_json,
        adapted_summary=evaluation.adapted_summary,
        adapted_bullets=adapted_bullets_json
    )
