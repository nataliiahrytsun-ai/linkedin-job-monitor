"""Django settings for the source-neutral job monitor bootstrap."""

from __future__ import annotations

import os
from math import isfinite
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def _bounded_int_setting(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ImproperlyConfigured(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ImproperlyConfigured(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _positive_float_setting(name: str, *, default: float) -> float:
    raw_value = os.environ.get(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ImproperlyConfigured(f"{name} must be a number") from error
    if not isfinite(value) or value <= 0:
        raise ImproperlyConfigured(f"{name} must be greater than zero")
    return value


def _boolean_setting(name: str, *, default: bool = False) -> bool:
    raw_value = os.environ.get(name, "1" if default else "0").strip().lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean")


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-local-development-only"
DEBUG = True
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "companies.apps.CompaniesConfig",
    "jobs.apps.JobsConfig",
    "scrape_runs.apps.ScrapeRunsConfig",
    "discovery.apps.DiscoveryConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "job_monitor.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "job_monitor.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "timeout": _positive_float_setting(
                "JOB_MONITOR_SQLITE_TIMEOUT_SECONDS",
                default=30.0,
            ),
        },
    }
}
database_override = os.environ.get("JOB_MONITOR_SQLITE_PATH")
if database_override:
    DATABASES["default"]["NAME"] = Path(database_override)

JOB_MONITOR_BACKGROUND_MAX_WORKERS = _bounded_int_setting(
    "JOB_MONITOR_BACKGROUND_MAX_WORKERS",
    default=2,
    minimum=1,
    maximum=2,
)

SOURCE_DISCOVERY_SEARCH_PROVIDER = (
    os.environ.get("SOURCE_DISCOVERY_SEARCH_PROVIDER", "tavily").strip().lower()
)
SOURCE_DISCOVERY_TAVILY_API_KEY = os.environ.get(
    "SOURCE_DISCOVERY_TAVILY_API_KEY", ""
).strip()
SOURCE_DISCOVERY_TAVILY_KEYLESS_DIAGNOSTIC = _boolean_setting(
    "SOURCE_DISCOVERY_TAVILY_KEYLESS_DIAGNOSTIC"
)
SOURCE_DISCOVERY_BRAVE_API_KEY = os.environ.get("SOURCE_DISCOVERY_BRAVE_API_KEY", "").strip()
SOURCE_DISCOVERY_SEARCH_TIMEOUT_SECONDS = _positive_float_setting(
    "SOURCE_DISCOVERY_SEARCH_TIMEOUT_SECONDS", default=10.0
)
SOURCE_DISCOVERY_SEARCH_RETRIES = _bounded_int_setting(
    "SOURCE_DISCOVERY_SEARCH_RETRIES", default=1, minimum=1, maximum=3
)
SOURCE_DISCOVERY_SEARCH_MAX_QUERIES = _bounded_int_setting(
    "SOURCE_DISCOVERY_SEARCH_MAX_QUERIES", default=10, minimum=2, maximum=10
)
SOURCE_DISCOVERY_SEARCH_MAX_RESULTS = _bounded_int_setting(
    "SOURCE_DISCOVERY_SEARCH_MAX_RESULTS", default=6, minimum=1, maximum=10
)
SOURCE_DISCOVERY_SEARCH_MAX_RESPONSE_BYTES = _bounded_int_setting(
    "SOURCE_DISCOVERY_SEARCH_MAX_RESPONSE_BYTES",
    default=1_000_000,
    minimum=10_000,
    maximum=5_000_000,
)
SOURCE_DISCOVERY_TOTAL_TIMEOUT_SECONDS = _positive_float_setting(
    "SOURCE_DISCOVERY_TOTAL_TIMEOUT_SECONDS", default=45.0
)
SOURCE_DISCOVERY_MAX_REQUESTS = _bounded_int_setting(
    "SOURCE_DISCOVERY_MAX_REQUESTS", default=8, minimum=1, maximum=20
)
SOURCE_DISCOVERY_MAX_DEPTH = _bounded_int_setting(
    "SOURCE_DISCOVERY_MAX_DEPTH", default=2, minimum=0, maximum=4
)
SOURCE_DISCOVERY_MAX_REDIRECTS = _bounded_int_setting(
    "SOURCE_DISCOVERY_MAX_REDIRECTS", default=4, minimum=0, maximum=8
)
SOURCE_DISCOVERY_MAX_BODY_BYTES = _bounded_int_setting(
    "SOURCE_DISCOVERY_MAX_BODY_BYTES", default=2_000_000, minimum=10_000, maximum=5_000_000
)
SOURCE_DISCOVERY_TIMEOUT_SECONDS = _positive_float_setting(
    "SOURCE_DISCOVERY_TIMEOUT_SECONDS", default=10.0
)

fixture_path_override = os.environ.get("JOB_MONITOR_FIXTURE_PATH")
JOB_MONITOR_FIXTURE_PATH = (
    Path(fixture_path_override)
    if fixture_path_override
    else BASE_DIR / "data" / "fixtures" / "demo_jobs.json"
)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Vienna"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
