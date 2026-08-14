from src.scraper.indeed import IndeedScraper
from src.scraper.remotive import RemotiveScraper


def test_remotive_scraper(mocker):
    """Prueba unitaria de RemotiveScraper usando mocks de red."""
    # 1. Configurar Mock de respuesta de la API REST
    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "job-count": 1,
        "jobs": [
            {
                "id": 1001,
                "url": "https://remotive.com/remote-jobs/data/analytics-engineer-1001",
                "title": "Analytics Engineer",
                "company_name": "Data Analytics Corp",
                "candidate_required_location": "Chile",
                "salary": "$4000 - $6000 USD",
                "job_type": "full_time",
                "publication_date": "2026-08-14T09:00:00",
                "description": "We need a dbt specialist.",
            }
        ],
    }

    # Inyectar mock en la llamada requests.get de RemotiveScraper
    mocker.patch("src.scraper.remotive.requests.get", return_value=mock_response)

    scraper = RemotiveScraper()
    jobs = scraper.fetch_jobs(keywords=["Analytics Engineer"], locations=["Chile"], limit=5)

    # 2. Aserciones de datos extraídos y mapeados
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Analytics Engineer"
    assert job.company == "Data Analytics Corp"
    assert job.location == "Chile"
    assert job.salary == "$4000 - $6000 USD"
    assert job.job_type == "full_time"
    assert job.source == "remotive"
    assert job.description == "We need a dbt specialist."


def test_indeed_scraper(mocker):
    """Prueba unitaria de IndeedScraper simulando búsqueda y descarga de descripción."""
    # 1. HTML simulado del resultado de búsqueda de Indeed con script Mosaic
    mock_search_html = """
    <html>
      <head>
        <script>
          window.mosaic.providerData["mosaic-provider-jobcards"] = {
            "metaData": {
              "mosaicProviderJobCardsModel": {
                "results": [
                  {
                    "jk": "indkey999",
                    "title": "Lead Data Engineer",
                    "company": "Indeed Inc",
                    "formattedLocation": "Remote",
                    "salarySnippet": {"text": "$150k"},
                    "pubDate": 1786689600000
                  }
                ]
              }
            }
          };
        </script>
      </head>
      <body></body>
    </html>
    """

    # HTML simulado de la página de detalles de vacante
    mock_detail_html = """
    <html>
      <body>
        <div id="jobDescriptionText">
          Requisitos: Spark, SQL, Airflow y Python.
        </div>
      </body>
    </html>
    """

    mock_search_res = mocker.Mock()
    mock_search_res.status_code = 200
    mock_search_res.text = mock_search_html

    mock_detail_res = mocker.Mock()
    mock_detail_res.status_code = 200
    mock_detail_res.text = mock_detail_html

    # Mock de deduplicación para que no salte el filtro de duplicados
    mocker.patch("src.scraper.indeed.is_duplicate", return_value=False)

    # Mockear las llamadas secuenciales de requests.get (1° búsqueda, 2° detalle descripción)
    mock_get = mocker.patch("src.scraper.indeed.requests.get")
    mock_get.side_effect = [mock_search_res, mock_detail_res]

    # Mockear time.sleep para que las pruebas corran instantáneamente
    mocker.patch("time.sleep")

    scraper = IndeedScraper()
    jobs = scraper.fetch_jobs(keywords=["Lead Data Engineer"], locations=["Remote"], limit=1)

    # 2. Aserciones
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Lead Data Engineer"
    assert job.company == "Indeed Inc"
    assert job.location == "Remote"
    assert job.salary == "$150k"
    assert job.source == "indeed"
    assert "Requisitos: Spark" in job.description
