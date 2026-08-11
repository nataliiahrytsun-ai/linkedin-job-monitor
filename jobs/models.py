"""Source-neutral job posting models."""

from __future__ import annotations

from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class JobPosting(models.Model):
    """A normalized public job posting and its immutable source provenance."""

    class WorkplaceType(models.TextChoices):
        REMOTE = "remote", "Remote"
        HYBRID = "hybrid", "Hybrid"
        ONSITE = "onsite", "Onsite"

    class Status(models.TextChoices):
        ACTIVE = "active", "Aktiv"
        NOT_FOUND = "not_found", "Nicht mehr gefunden"
        CLOSED = "closed", "Geschlossen"

    id = models.BigAutoField(primary_key=True)
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.PROTECT,
        related_name="job_postings",
    )
    company_source = models.ForeignKey(
        "companies.CompanySource",
        on_delete=models.PROTECT,
        related_name="job_postings",
        null=True,
        blank=True,
        default=None,
    )
    source = models.CharField(max_length=50)
    source_job_id = models.CharField(max_length=255, null=True, blank=True, default=None)
    title = models.CharField(max_length=512, null=True, blank=True, default=None)
    country = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        default=None,
        db_index=True,
    )
    city = models.CharField(max_length=255, null=True, blank=True, default=None)
    location = models.CharField(max_length=512, null=True, blank=True, default=None)
    workplace_type = models.CharField(
        max_length=16,
        choices=WorkplaceType,
        null=True,
        blank=True,
        default=None,
        db_index=True,
    )
    employment_type = models.CharField(max_length=128, null=True, blank=True, default=None)
    seniority_level = models.CharField(max_length=128, null=True, blank=True, default=None)
    job_function = models.CharField(max_length=255, null=True, blank=True, default=None)
    industry = models.CharField(max_length=255, null=True, blank=True, default=None)
    published_at = models.DateTimeField(null=True, blank=True, default=None, db_index=True)
    description = models.TextField(null=True, blank=True, default=None)
    source_job_url = models.URLField(max_length=2048, null=True, blank=True, default=None)
    content_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16,
        choices=Status,
        default=Status.ACTIVE,
        db_index=True,
    )
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    dedupe_key = models.CharField(max_length=64)
    consecutive_successful_misses = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints: ClassVar[list[Any]] = [
            models.UniqueConstraint(
                fields=("company", "source", "dedupe_key"),
                name="uniq_job_dedupe_key",
            ),
            models.UniqueConstraint(
                fields=("company", "source", "source_job_id"),
                condition=Q(source_job_id__isnull=False) & ~Q(source_job_id=""),
                name="uniq_job_source_id",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(source_job_id__isnull=False) & ~Q(source_job_id=""))
                    | (Q(source_job_url__isnull=False) & ~Q(source_job_url=""))
                ),
                name="job_has_source_identity",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=("company", "status"),
                name="job_company_status_idx",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Normalize source and reject provenance changes on existing rows."""
        normalized_source = (self.source or "").strip().lower()
        if not normalized_source:
            raise ValidationError({"source": "Source must not be empty."})

        if self.pk is not None and not self._state.adding:
            original_source = type(self).objects.only("source").get(pk=self.pk).source
            if normalized_source != original_source:
                raise ValidationError(
                    {"source": "Source provenance cannot be changed after creation."}
                )

        self.source = normalized_source
        self.source_job_id = (self.source_job_id or "").strip() or None
        self.source_job_url = (self.source_job_url or "").strip() or None
        if self.source_job_id is None and self.source_job_url is None:
            raise ValidationError(
                "A job posting requires source_job_id or source_job_url."
            )
        super().save(*args, **kwargs)
