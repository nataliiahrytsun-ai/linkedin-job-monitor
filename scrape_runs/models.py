"""Scrape run history models."""

from __future__ import annotations

from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ScrapeRun(models.Model):
    """Execution history and aggregate counters for one company run."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    id = models.BigAutoField(primary_key=True)
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.PROTECT,
        related_name="scrape_runs",
    )
    company_source = models.ForeignKey(
        "companies.CompanySource",
        on_delete=models.PROTECT,
        related_name="scrape_runs",
        null=True,
        blank=True,
        default=None,
    )
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True, default=None)
    status = models.CharField(
        max_length=16,
        choices=Status,
        default=Status.RUNNING,
        db_index=True,
    )
    jobs_found = models.PositiveIntegerField(default=0)
    jobs_created = models.PositiveIntegerField(default=0)
    jobs_updated = models.PositiveIntegerField(default=0)
    requests_made = models.PositiveIntegerField(default=0)
    duration_seconds = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        default=None,
    )
    error_message = models.TextField(blank=True, default="")

    class Meta:
        constraints: ClassVar[list[Any]] = [
            models.UniqueConstraint(
                fields=("company",),
                condition=Q(status="running"),
                name="uniq_running_run_company",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        Q(status="running")
                        & Q(finished_at__isnull=True)
                        & Q(duration_seconds__isnull=True)
                    )
                    | (
                        ~Q(status="running")
                        & Q(finished_at__isnull=False)
                        & Q(duration_seconds__isnull=False)
                    )
                ),
                name="run_terminal_fields_valid",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=("company", "-started_at"),
                name="run_company_started_idx",
            ),
            models.Index(
                fields=("status", "-started_at"),
                name="run_status_started_idx",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Reject contradictory transitional Company and CompanySource ownership."""
        if self.company_source_id is not None:
            source_company_id = (
                type(self).company_source.field.related_model.objects.only("company_id")
                .get(pk=self.company_source_id)
                .company_id
            )
            if self.company_id != source_company_id:
                raise ValidationError(
                    {"company_source": "Company source must belong to the run company."}
                )
        super().save(*args, **kwargs)
