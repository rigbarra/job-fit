import json

from src.cover_engine.builder import build_cover_letter_tex
from src.cv_engine.builder import load_profile
from src.database.models import Job


def test_build_cover_letter_tex(mocker):
    """Verifica la generación del template TeX para la carta de presentación."""
    mock_llm_json = {
        "salutation": "Estimado Equipo de Reclutamiento de Accenture,",
        "opening_paragraph": "Me presento con gran entusiasmo para la posición de Senior Data Engineer.",
        "body_paragraph": "Tengo amplia experiencia construyendo pipelines con Python, PySpark y dbt.",
        "achievement_bullets": [
            "Liderazgo en migraciones de pipelines legacy a cloud.",
            "Optimización de modelos de datos Kimball."
        ],
        "connection_paragraph": "Accenture destaca en transformación digital.",
        "closing_paragraph": "Quedo a su disposición para conversar.",
        "closing_valediction": "Atentamente,"
    }

    mock_provider = mocker.Mock()
    mock_provider.generate.return_value = json.dumps(mock_llm_json, ensure_ascii=False)
    mocker.patch("src.cover_engine.builder.get_llm_provider", return_value=mock_provider)

    job = Job(
        id=50,
        title="Senior Data Engineer",
        company="Accenture",
        location="Chile",
        description="Requerimos Python, PySpark y dbt.",
        url="https://example.com/job/50",
        source="test"
    )

    tex = build_cover_letter_tex(job, language="es")
    assert "Accenture" in tex
    profile = load_profile(language="es")
    assert profile["name"] in tex
    assert "Senior Data Engineer" in tex
    assert "Atentamente" in tex
