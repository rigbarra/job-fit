from src.scraper.indeed import IndeedScraper
from src.scraper.linkedin import LinkedInScraper
from src.scraper.remotive import RemotiveScraper


def test_remotive_scraper(mocker):
    """Prueba unitaria de RemotiveScraper usando mocks de red."""
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

    mocker.patch("src.scraper.remotive.requests.get", return_value=mock_response)

    scraper = RemotiveScraper()
    jobs = scraper.fetch_jobs(keywords=["Analytics Engineer"], locations=["Chile"], limit=5)

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
    mock_search_html = """
    <html>
      <head>
        <script>
          window.mosaic.providerData["mosaic-provider-jobcards"] = {
            "metaData": {
              "mosaicProviderJobCardsModel": {
                "results": [
                  {
                    "jobkey": "indkey999",
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

    mocker.patch("src.scraper.base.is_duplicate", return_value=False)

    # Mock de requests.get en base.py (donde WebScraper lo invoca)
    mock_get = mocker.patch("src.scraper.base.requests.get")
    mock_get.side_effect = [mock_search_res, mock_detail_res]
    mocker.patch("time.sleep")

    scraper = IndeedScraper()
    jobs = scraper.fetch_jobs(keywords=["Lead Data Engineer"], locations=["Remote"], limit=1)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Lead Data Engineer"
    assert job.company == "Indeed Inc"
    assert job.location == "Remote"
    assert job.salary == "$150k"
    assert job.source == "indeed"
    assert "Requisitos: Spark" in job.description


def test_linkedin_scraper(mocker):
    """Prueba unitaria de LinkedInScraper usando mocks de red."""
    mock_search_html = """
    <ul class="jobs-search__results-list">
      <li class="base-card">
        <h3 class="base-search-card__title">Senior Analytics Engineer</h3>
        <h4 class="base-search-card__subtitle">Darwin AI</h4>
        <span class="job-search-card__location">Santiago, Chile</span>
        <a class="base-card__full-link" href="https://cl.linkedin.com/jobs/view/senior-analytics-engineer-12345?position=1&pageNum=0"></a>
        <time datetime="2026-08-19T08:00:00Z"></time>
      </li>
    </ul>
    """

    mock_detail_html = """
    <html>
      <body>
        <div class="show-more-less-html__markup">
          Buscamos un Senior Analytics Engineer con experiencia en dbt, Python, SQL y AWS Redshift.
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

    mocker.patch("src.scraper.base.is_duplicate", return_value=False)
    mock_get = mocker.patch("src.scraper.base.requests.get")
    mock_get.side_effect = [mock_search_res, mock_detail_res]
    mocker.patch("time.sleep")

    scraper = LinkedInScraper()
    jobs = scraper.fetch_jobs(keywords=["Senior Analytics Engineer"], locations=["Chile"], limit=1)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Senior Analytics Engineer"
    assert job.company == "Darwin AI"
    assert job.location == "Santiago, Chile"
    assert job.source == "linkedin"
    assert job.url == "https://cl.linkedin.com/jobs/view/senior-analytics-engineer-12345"
    assert "dbt, Python, SQL" in job.description
