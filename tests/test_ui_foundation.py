from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import django  # type: ignore[import-untyped]
import pytest
from django.test import SimpleTestCase, override_settings  # type: ignore[import-untyped]
from django.test.utils import (  # type: ignore[import-untyped]
    setup_test_environment,
    teardown_test_environment,
)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "job_monitor.settings")


@pytest.fixture(scope="module", autouse=True)
def django_template_test_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    database_path = Path(tmp_path_factory.mktemp("ui-database")) / "ui.sqlite3"
    os.environ["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
    django.setup()
    setup_test_environment()
    yield
    teardown_test_environment()


@override_settings(ALLOWED_HOSTS=["testserver"])
class HomePageTests(SimpleTestCase):  # type: ignore[misc]
    def test_home_renders_expected_templates_and_foundation_content(self) -> None:
        response = self.client.get("/")
        html = response.content.decode()

        assert response.status_code == 200
        self.assertTemplateUsed(response, "home.html")
        self.assertTemplateUsed(response, "base.html")
        assert "Job Monitor" in html
        assert html.count("<h1") == 1
        assert all(
            section in html for section in ("Companies", "Jobs", "Scrape runs", "Dashboard")
        )

    def test_home_has_responsive_semantic_navigation_and_local_css(self) -> None:
        response = self.client.get("/")
        html = response.content.decode()

        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
        assert '<nav aria-label="Primary">' in html
        assert '<main id="main-content"' in html
        assert '<link rel="stylesheet" href="/static/css/app.css">' in html
        assert 'class="skip-link" href="#main-content"' in html

    def test_only_available_sections_link_to_real_routes(self) -> None:
        html = self.client.get("/").content.decode()

        assert 'href="/companies/"' in html
        assert 'href="/jobs/"' in html
        assert 'href="/scrape-runs/' not in html
        assert html.count("Coming next") == 2
        assert html.count('aria-disabled="true"') == 1

    def test_unknown_url_still_returns_not_found(self) -> None:
        response = self.client.get("/not-a-real-page/")

        assert response.status_code == 404

    def test_home_does_not_query_database_or_start_pipeline(self) -> None:
        with (
            patch("scraping.pipeline.run_fixture_pipeline") as pipeline,
            patch(
                "scraping.background.ControlledBackgroundExecutor.submit_fixture_pipeline"
            ) as background_submit,
        ):
            response = self.client.get("/")

        assert response.status_code == 200
        pipeline.assert_not_called()
        background_submit.assert_not_called()
