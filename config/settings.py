from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM Settings
    openrouter_api_key: str | None = Field(None, validation_alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field("google/gemma-3-27b-it:free", validation_alias="OPENROUTER_MODEL")
    llm_max_calls_per_day: int = Field(150, validation_alias="LLM_MAX_CALLS_PER_DAY")
    llm_max_calls_per_minute: int = Field(10, validation_alias="LLM_MAX_CALLS_PER_MINUTE")

    # Notifications
    discord_webhook_url: str | None = Field(None, validation_alias="DISCORD_WEBHOOK_URL")

    # Database
    database_url: str = Field("sqlite:///data/db/job_fit.db", validation_alias="DATABASE_URL")

    # Paths
    output_pdf_dir: str = Field("./data/generated_cvs", validation_alias="OUTPUT_PDF_DIR")
    templates_dir: str = Field("./templates/cv", validation_alias="TEMPLATES_DIR")

    # Project Root
    project_root: Path = Path(__file__).resolve().parent.parent

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
