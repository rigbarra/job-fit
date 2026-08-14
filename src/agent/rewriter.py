"""
Módulo de reescritura y adaptación de contenidos de CV.
Contiene utilidades de transformación y normalización de textos generados por el LLM.
"""

import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

def parse_adapted_bullets(bullets_json_or_dict: Optional[str | Dict[str, str]]) -> Dict[str, str]:
    """
    Normaliza y parsea el diccionario de viñetas adaptadas desde una cadena JSON o dict.
    
    Args:
        bullets_json_or_dict: Cadena JSON o dict con mapeo original -> adaptado.
        
    Returns:
        Dict[str, str]: Diccionario normalizado.
    """
    if not bullets_json_or_dict:
        return {}
    if isinstance(bullets_json_or_dict, dict):
        return bullets_json_or_dict
    try:
        data = json.loads(bullets_json_or_dict)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning(f"Error parseando adapted_bullets: {e}")
    return {}

def format_bullet_diff(original: str, adapted: str) -> str:
    """
    Genera una representación visual de la diferencia entre la viñeta original y la adaptada.
    """
    return f"[-] Original: {original}\n[+] Adaptada: {adapted}"
