"""Source-neutral normalization for public job posting data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import SplitResult, urlsplit, urlunsplit

_WORKPLACE_TYPES = frozenset({"remote", "hybrid", "onsite"})


@dataclass(frozen=True, slots=True)
class NormalizedJobPosting:
    """Canonical job content passed from source adapters to persistence."""

    source: str
    source_job_id: str | None
    title: str | None
    country: str | None
    city: str | None
    location: str | None
    workplace_type: str | None
    employment_type: str | None
    compensation_text: str | None
    seniority_level: str | None
    job_function: str | None
    industry: str | None
    published_at: datetime | None
    description: str | None
    source_job_url: str | None


def normalize_optional_text(value: str | None) -> str | None:
    """Trim and collapse whitespace in an optional short text value."""
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def normalize_description(value: str | None) -> str | None:
    """Normalize line endings and line-edge whitespace without folding lines."""
    if value is None:
        return None
    normalized_newlines = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized_lines = "\n".join(line.strip() for line in normalized_newlines.split("\n"))
    normalized = normalized_lines.strip()
    return normalized or None


def normalize_published_at(value: datetime | None) -> datetime | None:
    """Return a UTC datetime; a naive input is explicitly interpreted as UTC."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def canonicalize_source_job_url(value: str | None) -> str | None:
    """Apply conservative, source-neutral canonicalization to an HTTP(S) URL."""
    cleaned = value.strip() if value is not None else ""
    if not cleaned:
        return None

    try:
        parsed = urlsplit(cleaned)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("source_job_url must be a valid absolute HTTP(S) URL") from error

    if scheme not in {"http", "https"} or hostname is None:
        raise ValueError("source_job_url must be an absolute HTTP(S) URL")

    host = hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    port_suffix = f":{port}" if port is not None and not default_port else ""
    userinfo = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
    canonical = SplitResult(
        scheme=scheme,
        netloc=f"{userinfo}{host}{port_suffix}",
        path=parsed.path,
        query=parsed.query,
        fragment="",
    )
    return urlunsplit(canonical)


def normalize_job_posting(
    *,
    source: str,
    source_job_id: str | None = None,
    title: str | None = None,
    country: str | None = None,
    city: str | None = None,
    location: str | None = None,
    workplace_type: str | None = None,
    employment_type: str | None = None,
    compensation_text: str | None = None,
    seniority_level: str | None = None,
    job_function: str | None = None,
    industry: str | None = None,
    published_at: datetime | None = None,
    description: str | None = None,
    source_job_url: str | None = None,
) -> NormalizedJobPosting:
    """Normalize raw adapter values and validate the source-neutral identity."""
    normalized_source = source.strip().lower()
    if not normalized_source:
        raise ValueError("source must not be empty")

    cleaned_source_job_id = source_job_id.strip() if source_job_id is not None else ""
    normalized_source_job_id: str | None = cleaned_source_job_id or None
    normalized_source_job_url = canonicalize_source_job_url(source_job_url)
    if normalized_source_job_id is None and normalized_source_job_url is None:
        raise ValueError("source_job_id or source_job_url is required")

    normalized_workplace_type = normalize_optional_text(workplace_type)
    if normalized_workplace_type is not None:
        normalized_workplace_type = normalized_workplace_type.lower()
        if normalized_workplace_type not in _WORKPLACE_TYPES:
            raise ValueError(
                "workplace_type must be remote, hybrid, onsite, or None"
            )

    return NormalizedJobPosting(
        source=normalized_source,
        source_job_id=normalized_source_job_id,
        title=normalize_optional_text(title),
        country=normalize_optional_text(country),
        city=normalize_optional_text(city),
        location=normalize_optional_text(location),
        workplace_type=normalized_workplace_type,
        employment_type=normalize_optional_text(employment_type),
        compensation_text=normalize_optional_text(compensation_text),
        seniority_level=normalize_optional_text(seniority_level),
        job_function=normalize_optional_text(job_function),
        industry=normalize_optional_text(industry),
        published_at=normalize_published_at(published_at),
        description=normalize_description(description),
        source_job_url=normalized_source_job_url,
    )
