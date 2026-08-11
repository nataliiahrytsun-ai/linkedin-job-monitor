"""Company configuration models."""

from __future__ import annotations

from typing import Any, ClassVar

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
        """Normalize the transitional legacy adapter key when present."""
        normalized_source = (self.source or "").strip().lower()
        self.source = normalized_source
        if self.source_jobs_url == "":
            self.source_jobs_url = None
        super().save(*args, **kwargs)


class CompanySource(models.Model):
    """One configured or discovered vacancy source for a company."""

    class ApprovalStatus(models.TextChoices):
        DISCOVERED = "discovered", "Discovered"
        NEEDS_REVIEW = "needs_review", "Needs review"
        APPROVED = "approved", "Approved"
        BLOCKED = "blocked", "Blocked"
        REJECTED = "rejected", "Rejected"

    id = models.BigAutoField(primary_key=True)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    source = models.CharField(max_length=50, blank=True, default="")
    source_jobs_url = models.URLField(
        max_length=2048,
        null=True,
        blank=True,
        default=None,
    )
    approval_status = models.CharField(
        max_length=16,
        choices=ApprovalStatus,
        default=ApprovalStatus.DISCOVERED,
        db_index=True,
    )
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar[list[Any]] = [
            models.UniqueConstraint(
                fields=("company", "source", "source_jobs_url"),
                name="uniq_company_source_config",
            ),
            models.UniqueConstraint(
                fields=("company", "source"),
                condition=Q(source_jobs_url__isnull=True),
                name="uniq_company_source_no_url",
            ),
            models.CheckConstraint(
                condition=Q(is_active=False) | Q(approval_status="approved"),
                name="active_source_is_approved",
            ),
            models.CheckConstraint(
                condition=~Q(approval_status="approved") | ~Q(source=""),
                name="approved_source_has_key",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Normalize the adapter key while preserving unresolved discoveries."""
        self.source = (self.source or "").strip().lower()
        if self.source_jobs_url == "":
            self.source_jobs_url = None
        super().save(*args, **kwargs)
