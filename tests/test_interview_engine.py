import os
from src.database.models import Job
from src.interview_engine.generator import generate_interview_prep


def test_generate_interview_prep(mocker, tmp_path):
    """Verifica la generación de la guía de entrevista en formato Markdown."""
    mock_md_content = """# 🎯 Guía de Preparación de Entrevista Técnica: Senior Data Engineer en Netflix

## 1. 🏢 Análisis de la Empresa
Buscan un Data Engineer sénior para arquitecturas de procesamiento masivo.

## 2. 💻 Preguntas Técnicas
- **Pregunta:** ¿Cómo manejas el data skew en PySpark?
- **Respuesta Clave:** Usando salting y repartition.
"""

    mock_provider = mocker.Mock()
    mock_provider.generate.return_value = mock_md_content
    mocker.patch("src.interview_engine.generator.get_llm_provider", return_value=mock_provider)

    job = Job(
        id=77,
        title="Senior Data Engineer",
        company="Netflix",
        location="Remote",
        description="Massive scale data engineering with PySpark.",
        url="https://example.com/job/77",
        source="test"
    )

    out_file = generate_interview_prep(job, output_dir=str(tmp_path))
    assert os.path.exists(out_file)
    with open(out_file, encoding="utf-8") as f:
        content = f.read()
    assert "Senior Data Engineer" in content
    assert "Netflix" in content
    assert "PySpark" in content
