from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Configuración Universal y Agnóstica de LLM
    llm_api_key: str | None = Field(None, validation_alias="LLM_API_KEY")
    llm_model: str = Field("gemini-3.6-flash", validation_alias="LLM_MODEL")
    llm_base_url: str | None = Field(None, validation_alias="LLM_BASE_URL")
    llm_provider: str | None = Field(None, validation_alias="LLM_PROVIDER")

    # Retrocompatibilidad para entornos anteriores
    openai_api_base: str = Field("https://api.openai.com/v1", validation_alias="OPENAI_API_BASE")
    openrouter_api_key: str | None = Field(None, validation_alias="OPENROUTER_API_KEY")
    openrouter_model: str | None = Field(None, validation_alias="OPENROUTER_MODEL")

    # Control de Cuotas y Rate Limiting del LLM
    llm_max_calls_per_day: int = Field(150, validation_alias="LLM_MAX_CALLS_PER_DAY")
    llm_max_calls_per_minute: int = Field(10, validation_alias="LLM_MAX_CALLS_PER_MINUTE")

    # Notificaciones
    discord_webhook_url: str | None = Field(None, validation_alias="DISCORD_WEBHOOK_URL")

    # Base de Datos
    database_url: str = Field("sqlite:///data/db/job_fit.db", validation_alias="DATABASE_URL")

    # Rutas
    output_pdf_dir: str = Field("./data/generated_cvs", validation_alias="OUTPUT_PDF_DIR")
    templates_dir: str = Field("./templates/cv", validation_alias="TEMPLATES_DIR")

    # Project Root
    project_root: Path = Path(__file__).resolve().parent.parent

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

# ---------------------------------------------------------------------------
# Carga de config.yaml — fusionado desde config/loader.py (eliminado)
# Cache en memoria para evitar re-lecturas de disco en cada vacante procesada.
# ---------------------------------------------------------------------------
_cached_config: dict | None = None


def load_config(force_reload: bool = False) -> dict:
    """Carga y cachea los parámetros del archivo config.yaml."""
    global _cached_config
    if _cached_config is not None and not force_reload:
        return _cached_config

    config_path = Path(settings.project_root) / "config" / "config.yaml"
    try:
        with open(config_path, encoding="utf-8") as f:
            _cached_config = yaml.safe_load(f)
    except Exception:
        _cached_config = {
            "search_filters": {
                "keywords": ["Analytics Engineer", "Data Engineer"],
                "locations": ["Remote"],
                "limit_per_source": 10,
            },
            "sources": {"remotive": True, "indeed": False},
        }
    return _cached_config
