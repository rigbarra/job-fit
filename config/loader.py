"""
Módulo centralizado de carga de configuración.
Cachea el contenido de config.yaml en memoria para evitar re-lecturas por cada vacante.
"""

import logging
from pathlib import Path

import yaml

from config.settings import settings

logger = logging.getLogger(__name__)

_cached_config: dict | None = None


def load_config(force_reload: bool = False) -> dict:
    """
    Carga y cachea los parámetros del archivo config.yaml.

    Args:
        force_reload: Si True, fuerza la recarga desde disco ignorando el cache.

    Returns:
        dict: Configuración completa del proyecto.
    """
    global _cached_config

    if _cached_config is not None and not force_reload:
        return _cached_config

    config_path = Path(settings.project_root) / "config" / "config.yaml"
    try:
        with open(config_path, encoding="utf-8") as f:
            _cached_config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"No se pudo cargar el archivo de configuración en {config_path}: {e}")
        _cached_config = {
            "search_filters": {
                "keywords": ["Analytics Engineer", "Data Engineer"],
                "locations": ["Remote"],
                "limit_per_source": 10,
            },
            "sources": {"remotive": True, "indeed": False},
        }

    return _cached_config
