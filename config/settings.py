import os
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # LLM Settings
    gemini_api_key: Optional[str] = Field(None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", validation_alias="GEMINI_MODEL")
    llm_max_calls_per_day: int = Field(150, validation_alias="LLM_MAX_CALLS_PER_DAY")
    llm_max_calls_per_minute: int = Field(10, validation_alias="LLM_MAX_CALLS_PER_MINUTE")

    # Notifications
    discord_webhook_url: Optional[str] = Field(None, validation_alias="DISCORD_WEBHOOK_URL")

    # Database
    database_url: str = Field("sqlite:///data/db/job_fit.db", validation_alias="DATABASE_URL")

    # Paths
    output_pdf_dir: str = Field("./data/generated_cvs", validation_alias="OUTPUT_PDF_DIR")
    templates_dir: str = Field("./templates/cv", validation_alias="TEMPLATES_DIR")

    # External APIs
    adzuna_app_id: Optional[str] = Field(None, validation_alias="ADZUNA_APP_ID")
    adzuna_api_key: Optional[str] = Field(None, validation_alias="ADZUNA_API_KEY")

    # Project Root
    project_root: Path = Path(__file__).resolve().parent.parent

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
