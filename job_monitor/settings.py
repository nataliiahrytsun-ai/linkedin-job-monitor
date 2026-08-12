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
