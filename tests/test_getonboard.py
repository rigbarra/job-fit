from src.scraper.getonboard import GetOnBoardScraper


def test_getonboard_fetch_jobs(mocker):
    """Verifica que GetOnBoardScraper extraiga vacantes y resuelva la empresa por ID."""
    mock_cat_response = mocker.Mock()
    mock_cat_response.status_code = 200
    mock_cat_response.json.return_value = {
        "data": [
            {
                "id": "job-1",
                "attributes": {
                    "title": "Senior Data Engineer",
                    "description": "<p>Stack: SQL, Python, dbt</p>",
                    "projects": "Data Platform Migration",
                    "functions": "Build pipelines",
                    "remote": True,
                    "remote_modality": "remote_local",
                    "countries": ["Chile"],
                    "published_at": 1787273580,
                    "min_salary": 4000,
                    "max_salary": 6000,
                    "company": {"data": {"id": 1001, "type": "company"}},
                },
                "links": {"public_url": "https://www.getonbrd.com/jobs/senior-de-test"},
            }
        ]
    }

    mock_comp_response = mocker.Mock()
    mock_comp_response.status_code = 200
    mock_comp_response.json.return_value = {
        "data": {"attributes": {"name": "Tech Corp Chile"}}
    }

    def side_effect(url, **kwargs):
        if "companies" in url:
            return mock_comp_response
        return mock_cat_response

    mocker.patch("src.scraper.getonboard.requests.get", side_effect=side_effect)

    scraper = GetOnBoardScraper()
    jobs = scraper.fetch_jobs(keywords=["Data Engineer"], locations=["Chile"], limit=5)

    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "Senior Data Engineer"
    assert j.company == "Tech Corp Chile"
    assert j.source == "getonboard"
    assert "Chile" in j.location
    assert "USD $4000 - $6000" in j.salary
