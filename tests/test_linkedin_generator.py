from unittest.mock import MagicMock, patch
from pathlib import Path

from src.linkedin_engine.generator import extract_market_insights_from_db, generate_linkedin_content


def test_extract_market_insights_from_db():
    """Valida que la extracción de insights de mercado desde la BD devuelva un texto coherente."""
    text = extract_market_insights_from_db(limit_jobs=5)
    assert isinstance(text, str)
    assert len(text) > 0


@patch("src.linkedin_engine.generator.get_llm_provider")
def test_generate_linkedin_content(mock_get_provider, tmp_path):
    """Valida que la generación de contenido cree el archivo Markdown correspondiente."""
    mock_provider = MagicMock()
    mock_provider.generate.return_value = "# 🚀 Estrategia de Contenido LinkedIn\n\n## 📌 1. Debate de Arquitectura\n\nTest content"
    mock_get_provider.return_value = mock_provider

    with patch("src.linkedin_engine.generator._resolve_vault", return_value=tmp_path):
        obsidian_file, content = generate_linkedin_content(custom_topic="dbt vs SQL")
        assert obsidian_file.exists()
        assert obsidian_file.name == "Ideas_LinkedIn.md"
        assert "Estrategia de Contenido LinkedIn" in content
        assert mock_provider.generate.called
