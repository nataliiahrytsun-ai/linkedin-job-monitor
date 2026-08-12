"""Derived review state for the current content version of each job."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from django.db.models import Case, CharField, F, Q, QuerySet, Value, When

from jobs.models import JobPosting


class JobReviewState(StrEnum):
    NEW = "new"
    UPDATED = "updated"
    REVIEWED = "reviewed"


def annotate_review_state(jobs: QuerySet[JobPosting]) -> QuerySet[JobPosting]:
    """Attach one display-only review state without storing a duplicate status."""
    return jobs.annotate(
        review_state=Case(
            When(
                last_reviewed_content_hash__isnull=True,
                then=Value(JobReviewState.NEW.value),
            ),
            When(
                last_reviewed_content_hash=F("content_hash"),
                then=Value(JobReviewState.REVIEWED.value),
            ),
            default=Value(JobReviewState.UPDATED.value),
            output_field=CharField(),
        )
    )


def filter_by_review_state(
    jobs: QuerySet[JobPosting], review_state: object
) -> QuerySet[JobPosting]:
    """Apply NEW, UPDATED, or their UNREVIEWED union to a job queryset."""
    if review_state == "new":
        return jobs.filter(last_reviewed_content_hash__isnull=True)
    if review_state == "updated":
        return jobs.filter(last_reviewed_content_hash__isnull=False).exclude(
            last_reviewed_content_hash=F("content_hash")
        )
    if review_state == "unreviewed":
        return jobs.filter(
            Q(last_reviewed_content_hash__isnull=True)
            | (
                Q(last_reviewed_content_hash__isnull=False)
                & ~Q(last_reviewed_content_hash=F("content_hash"))
            )
        )
    return jobs


def mark_job_reviewed(job: Any) -> bool:
    """Acknowledge only the exact content version loaded for a successful detail view."""
    updated = JobPosting.objects.filter(
        pk=job.pk,
        content_hash=job.content_hash,
    ).update(last_reviewed_content_hash=job.content_hash)
    return updated == 1
