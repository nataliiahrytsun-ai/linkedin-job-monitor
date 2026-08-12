from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANAGE_PY = PROJECT_ROOT / "manage.py"
WORKING_DATABASE_PATH = (PROJECT_ROOT / "db.sqlite3").resolve()
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


def _run_python(*, code: str, database_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=_isolated_environment(database_path),
        check=False,
        capture_output=True,
        text=True,
    )


def test_django_settings_import_and_configuration(tmp_path: Path) -> None:
    database_path = tmp_path / "settings.sqlite3"
    code = (
        "from pathlib import Path; "
        "from django.conf import settings; "
        f"expected={EXPECTED_APPS!r}; "
        "assert set(settings.INSTALLED_APPS) >= expected; "
        "assert settings.DATABASES['default']['ENGINE'] == "
        "'django.db.backends.sqlite3'; "
        "assert settings.DATABASES['default']['OPTIONS']['timeout'] == 30.0; "
        "assert settings.JOB_MONITOR_BACKGROUND_MAX_WORKERS == 2; "
        "assert settings.TIME_ZONE == 'Europe/Vienna'; "
        "assert settings.USE_TZ is True; "
        "configured=Path(settings.DATABASES['default']['NAME']).resolve(); "
        f"temporary=Path({str(database_path)!r}).resolve(); "
        f"working=Path({str(WORKING_DATABASE_PATH)!r}).resolve(); "
        "assert configured == temporary; "
        "assert configured != working"
    )
    result = _run_python(code=code, database_path=database_path)

    assert result.returncode == 0, result.stderr
    assert not database_path.exists()


def test_concurrency_settings_accept_safe_overrides(tmp_path: Path) -> None:
    environment = _isolated_environment(tmp_path / "settings-overrides.sqlite3")
    environment["JOB_MONITOR_BACKGROUND_MAX_WORKERS"] = "1"
    environment["JOB_MONITOR_SQLITE_TIMEOUT_SECONDS"] = "12.5"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from django.conf import settings; "
                "assert settings.JOB_MONITOR_BACKGROUND_MAX_WORKERS == 1; "
                "assert settings.DATABASES['default']['OPTIONS']['timeout'] == 12.5"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_worker_setting_rejects_more_than_two(tmp_path: Path) -> None:
    environment = _isolated_environment(tmp_path / "settings-invalid.sqlite3")
    environment["JOB_MONITOR_BACKGROUND_MAX_WORKERS"] = "3"
    result = subprocess.run(
        [sys.executable, "-c", "from django.conf import settings; settings.DATABASES"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "JOB_MONITOR_BACKGROUND_MAX_WORKERS must be between 1 and 2" in result.stderr


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

    verification_code = (
        "from pathlib import Path; "
        "import django; "
        "django.setup(); "
        "from django.conf import settings; "
        "from django.db import connection; "
        "from django.db.migrations.recorder import MigrationRecorder; "
        "configured=Path(settings.DATABASES['default']['NAME']).resolve(); "
        f"temporary=Path({str(database_path)!r}).resolve(); "
        f"working=Path({str(WORKING_DATABASE_PATH)!r}).resolve(); "
        "assert configured == temporary; "
        "assert configured != working; "
        "tables=set(connection.introspection.table_names()); "
        "expected_tables={'companies_company','jobs_jobposting',"
        "'scrape_runs_scraperun','django_migrations'}; "
        "assert tables >= expected_tables; "
        "applied=set(MigrationRecorder(connection).applied_migrations()); "
        "expected_migrations={('companies','0001_initial'),"
        "('jobs','0001_initial'),('scrape_runs','0001_initial')}; "
        "assert applied >= expected_migrations"
    )
    verification = _run_python(code=verification_code, database_path=database_path)

    assert verification.returncode == 0, verification.stderr
