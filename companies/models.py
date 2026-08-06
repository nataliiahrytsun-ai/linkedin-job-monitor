"""Company configuration models."""

from __future__ import annotations

from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Company(models.Model):
    """A customer or supplier monitored through a configured public source."""

    class CompanyType(models.TextChoices):
        CLIENT = "client", "Kunde"
        SUPPLIER = "supplier", "Supplier"
        OTHER = "other", "Sonstige"

    class ScrapeStatus(models.TextChoices):
        NEVER = "never", "Never"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255)
    source = models.CharField(max_length=50)
    source_jobs_url = models.URLField(max_length=2048, null=True, blank=True, default=None)
    company_type = models.CharField(
        max_length=16,
        choices=CompanyType,
        default=CompanyType.OTHER,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_scraped_at = models.DateTimeField(null=True, blank=True, default=None)
    last_scrape_status = models.CharField(
        max_length=16,
        choices=ScrapeStatus,
        default=ScrapeStatus.NEVER,
        db_index=True,
    )

    class Meta:
        constraints: ClassVar[list[Any]] = [
            models.UniqueConstraint(
                fields=("source", "source_jobs_url"),
                condition=Q(source_jobs_url__isnull=False) & ~Q(source_jobs_url=""),
                name="uniq_company_source_url",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=("is_active", "company_type"),
                name="company_active_type_idx",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Normalize the adapter key for every ordinary ORM save."""
        normalized_source = (self.source or "").strip().lower()
        if not normalized_source:
            raise ValidationError({"source": "Source must not be empty."})
        self.source = normalized_source
        if self.source_jobs_url == "":
            self.source_jobs_url = None
        super().save(*args, **kwargs)
