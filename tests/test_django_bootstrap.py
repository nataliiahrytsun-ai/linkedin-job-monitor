from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANAGE_PY = PROJECT_ROOT / "manage.py"
EXPECTED_APPS = {
    "companies.apps.CompaniesConfig",
    "jobs.apps.JobsConfig",
    "scrape_runs.apps.ScrapeRunsConfig",
}


def _isolated_environment(database_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
    environment["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
    return environment


def _run_manage(*arguments: str, database_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MANAGE_PY), *arguments],
        cwd=PROJECT_ROOT,
        env=_isolated_environment(database_path),
        check=False,
        capture_output=True,
        text=True,
    )


def test_django_settings_import_and_configuration(tmp_path: Path) -> None:
    settings = importlib.import_module("job_monitor.settings")

    assert set(settings.INSTALLED_APPS) >= EXPECTED_APPS
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert settings.BASE_DIR / "db.sqlite3" == PROJECT_ROOT / "db.sqlite3"
    assert not (PROJECT_ROOT / "db.sqlite3").exists()


def test_django_setup_succeeds(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=PROJECT_ROOT,
        env=_isolated_environment(tmp_path / "setup.sqlite3"),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_manage_check_succeeds(tmp_path: Path) -> None:
    result = _run_manage("check", database_path=tmp_path / "check.sqlite3")

    assert result.returncode == 0, result.stderr
    assert "System check identified no issues" in result.stdout


def test_migrate_succeeds_with_temporary_database(tmp_path: Path) -> None:
    database_path = tmp_path / "migrate.sqlite3"
    result = _run_manage("migrate", "--noinput", database_path=database_path)

    assert result.returncode == 0, result.stderr
    assert database_path.exists()
    assert not (PROJECT_ROOT / "db.sqlite3").exists()
