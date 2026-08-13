"""One process-local bounded executor shared by HTTP entry points."""

from django.conf import settings

from scraping.background import ControlledBackgroundExecutor

background_executor = ControlledBackgroundExecutor(
    max_workers=settings.JOB_MONITOR_BACKGROUND_MAX_WORKERS
)
