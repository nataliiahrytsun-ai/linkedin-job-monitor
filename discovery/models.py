"""Durable audit trail for source discovery."""

from typing import Any, ClassVar

from django.db import models


class DiscoveryRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        CONNECTED = "connected", "Connected"
        ALREADY_CONNECTED = "already_connected", "Already connected"
        NEEDS_REVIEW = "needs_review", "Needs review"
        UNSUPPORTED = "unsupported", "Unsupported"
        NOT_FOUND = "not_found", "Not found"
        FAILED = "failed", "Failed"

    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="discovery_runs"
    )
    query = models.CharField(max_length=255)
    supplied_domain = models.CharField(max_length=253, blank=True, default="")
    official_website_url = models.URLField(max_length=2048, null=True, blank=True)
    careers_url = models.URLField(max_length=2048, null=True, blank=True)
    status = models.CharField(max_length=24, choices=Status, default=Status.RUNNING)
    summary = models.TextField(blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=("company", "-started_at"), name="discovery_company_time_idx")
        ]


class DiscoveryCandidate(models.Model):
    class Kind(models.TextChoices):
        OFFICIAL_SITE = "official_site", "Official site"
        CAREERS = "careers", "Careers page"
        SOURCE = "source", "Vacancy source"

    class Decision(models.TextChoices):
        SELECTED = "selected", "Selected"
        CONNECTED = "connected", "Connected"
        ALREADY_CONNECTED = "already_connected", "Already connected"
        NEEDS_REVIEW = "needs_review", "Needs review"
        UNSUPPORTED = "unsupported", "Unsupported"
        REJECTED = "rejected", "Rejected"

    class Origin(models.TextChoices):
        EXISTING_SOURCE = "existing_source", "Existing source"
        CURRENT_DISCOVERY = "current_discovery", "Found in current discovery"
        PREVIOUS_DISCOVERY = "previous_discovery", "Found in previous discovery"
        ADAPTER_SEARCH = "adapter_search", "Found by adapter-specific search"

    class OfficialSiteEligibility(models.TextChoices):
        OFFICIAL_SITE = "official_site", "Official site"
        NOT_OFFICIAL_SITE = "not_official_site", "Not an official site"
        UNCERTAIN = "uncertain", "Uncertain"

    class JobSourceEligibility(models.TextChoices):
        SUPPORTED_ATS = "supported_ats", "Supported ATS"
        UNSUPPORTED_ATS = "unsupported_ats", "Unsupported ATS"
        EXTERNAL_JOB_BOARD = "external_job_board", "External job board"
        COMPANY_JOBS_PAGE = "company_jobs_page", "Company jobs page"
        POSSIBLE_JOB_SOURCE = "possible_job_source", "Possible job source"
        NOT_A_JOB_SOURCE = "not_a_job_source", "Not a job source"
        UNCERTAIN = "uncertain", "Uncertain"

    run = models.ForeignKey(DiscoveryRun, on_delete=models.CASCADE, related_name="candidates")
    kind = models.CharField(max_length=16, choices=Kind)
    discovered_url = models.URLField(max_length=2048)
    canonical_url = models.URLField(max_length=2048)
    platform = models.CharField(max_length=80, blank=True, default="")
    confidence = models.PositiveSmallIntegerField(default=0)
    job_source_confidence = models.PositiveSmallIntegerField(default=0)
    evidence = models.JSONField(default=list)
    redirects = models.JSONField(default=list)
    supported = models.BooleanField(default=False)
    decision = models.CharField(max_length=24, choices=Decision)
    reason = models.TextField(blank=True, default="")
    origin = models.CharField(
        max_length=24,
        choices=Origin,
        default=Origin.CURRENT_DISCOVERY,
    )
    official_site_eligibility = models.CharField(
        max_length=24,
        choices=OfficialSiteEligibility,
        default=OfficialSiteEligibility.UNCERTAIN,
    )
    job_source_eligibility = models.CharField(
        max_length=24,
        choices=JobSourceEligibility,
        default=JobSourceEligibility.UNCERTAIN,
    )
    is_ignored = models.BooleanField(default=False)
    company_source = models.ForeignKey(
        "companies.CompanySource", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list[Any]] = [
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0) & models.Q(confidence__lte=100),
                name="discovery_confidence_range",
            ),
            models.CheckConstraint(
                condition=models.Q(job_source_confidence__gte=0)
                & models.Q(job_source_confidence__lte=100),
                name="discovery_job_source_confidence_range",
            ),
            models.UniqueConstraint(
                fields=("run", "kind", "platform", "canonical_url"),
                name="uniq_discovery_candidate",
            ),
        ]


class DiscoveryAdapterCheck(models.Model):
    class Status(models.TextChoices):
        FOUND = "found", "Found"
        ALREADY_CONNECTED = "already_connected", "Already connected"
        NOT_FOUND = "not_found", "Not found"
        NOT_CHECKED = "not_checked", "Not checked"
        SEARCH_FAILED = "search_failed", "Search failed"
        VALIDATION_FAILED = "validation_failed", "Validation failed"

    run = models.ForeignKey(
        DiscoveryRun,
        on_delete=models.CASCADE,
        related_name="adapter_checks",
    )
    platform = models.CharField(max_length=80)
    status = models.CharField(max_length=24, choices=Status)
    reason = models.TextField(blank=True, default="")
    candidate = models.ForeignKey(
        DiscoveryCandidate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="adapter_checks",
    )
    company_source = models.ForeignKey(
        "companies.CompanySource",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discovery_adapter_checks",
    )

    class Meta:
        constraints: ClassVar[list[Any]] = [
            models.UniqueConstraint(
                fields=("run", "platform"),
                name="uniq_discovery_adapter_check",
            )
        ]
