import os
import json
import logging
import yaml
from typing import Optional
import google.generativeai as genai

from config.settings import settings
from src.database.models import Job, MatchResult
from src.agent.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, MatchEvaluation
from src.agent.quota import GeminiQuotaManager

logger = logging.getLogger(__name__)

# Configurar API de Gemini
if settings.gemini_api_key and settings.gemini_api_key != "tu_api_key_gratuita_aqui":
    genai.configure(api_key=settings.gemini_api_key)
else:
    logger.warning("Gemini API: GEMINI_API_KEY no está configurada o usa el valor por defecto. Las llamadas de LLM fallarán si no se usa mock.")

def evaluate_job(job: Job, profile_path: Optional[str] = None) -> MatchResult:
    """
    Evalúa la compatibilidad de una vacante frente al perfil del candidato
    usando la API de Gemini (Free Tier) y controlando cuotas.
    """
    # 1. Cargar el perfil del candidato
    if not profile_path:
        profile_path = os.path.join(settings.project_root, "config", "profile.yaml")
        
    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            # Mantener el texto original en formato YAML para pasarlo al LLM
            profile_text = f.read()
    except Exception as e:
        logger.error(f"No se pudo cargar el perfil del candidato en {profile_path}: {e}")
        raise FileNotFoundError(f"Perfil del candidato no encontrado: {profile_path}")

    # 2. Construir los prompts
    user_prompt = USER_PROMPT_TEMPLATE.format(
        candidate_profile=profile_text,
        job_description=job.description
    )

    # 3. Configurar el modelo de generación
    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=SYSTEM_PROMPT
    )

    # 4. Definir la función que ejecutará la llamada (para envolverla con reintentos)
    def _make_gemini_call():
        return model.generate_content(
            user_prompt,
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": MatchEvaluation,
                "temperature": 0.1  # Baja temperatura para consistencia
            }
        )

    logger.info(f"Gemini: Iniciando evaluación de la vacante '{job.title}' @ '{job.company}'...")
    
    # 5. Ejecutar llamada con retry y control de RPM/cuota diaria
    response = GeminiQuotaManager.call_with_retry(_make_gemini_call)
    
    # 6. Parsear y validar el JSON devuelto según el esquema Pydantic
    try:
        evaluation = MatchEvaluation.model_validate_json(response.text)
    except Exception as e:
        logger.error(f"Gemini: Error de validación de esquema en la respuesta JSON: {e}")
        logger.debug(f"Respuesta errónea del LLM: {response.text}")
        raise ValueError(f"La respuesta del LLM no coincide con la estructura requerida: {e}")

    # 7. Determinar Tier en base a la puntuación
    score = evaluation.score
    if score >= 85.0:
        tier = 1  # Match Alto
    elif score >= 60.0:
        tier = 2  # Match Medio (con retoques)
    else:
        tier = 3  # Descarte

    # Convertir listas y dicts a JSON string para guardar en BD de forma estructurada
    missing_keywords_json = json.dumps(evaluation.missing_keywords, ensure_ascii=False)
    adapted_bullets_json = json.dumps(evaluation.adapted_bullets, ensure_ascii=False) if evaluation.adapted_bullets else None

    # 8. Retornar el modelo de base de datos MatchResult armado
    return MatchResult(
        job_id=job.id,
        score=score,
        tier=tier,
        rationale=evaluation.rationale,
        missing_keywords=missing_keywords_json,
        adapted_summary=evaluation.adapted_summary,
        adapted_bullets=adapted_bullets_json
    )
