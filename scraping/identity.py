"""Stable content and identity hashes for normalized job postings."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from typing import Any

from scraping.normalization import NormalizedJobPosting

_CONTENT_FIELDS = (
    "title",
    "country",
    "city",
    "location",
    "workplace_type",
    "employment_type",
    "seniority_level",
    "job_function",
    "industry",
    "published_at",
    "description",
    "source_job_url",
)


def _content_payload(job: NormalizedJobPosting) -> dict[str, Any]:
    payload = {field: getattr(job, field) for field in _CONTENT_FIELDS}
    if job.published_at is not None:
        if job.published_at.tzinfo is None or job.published_at.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        payload["published_at"] = job.published_at.astimezone(UTC).isoformat()
    return payload


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def compute_content_hash(job: NormalizedJobPosting) -> str:
    """Hash canonical normalized content, excluding source identity and lifecycle."""
    canonical_json = json.dumps(
        _content_payload(job),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(canonical_json.encode("utf-8"))


def compute_dedupe_key(job: NormalizedJobPosting) -> str:
    """Prefer source ID and fall back to canonical URL for source-local identity."""
    if not job.source:
        raise ValueError("source must not be empty")
    if job.source_job_id is not None:
        payload = f"id\0{job.source}\0{job.source_job_id}"
    elif job.source_job_url is not None:
        payload = f"url\0{job.source}\0{job.source_job_url}"
    else:
        raise ValueError("source_job_id or source_job_url is required")
    return _sha256(payload.encode("utf-8"))
