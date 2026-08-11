"""Read public Lever postings through the source-neutral adapter contract."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import quote, unquote, urlencode, urlsplit

from scraping.sources.base import SourceBatch, SourceCompany, SourceError, SourceRecord

LEVER_JOBS_HOST = "jobs.lever.co"
LEVER_API_HOST = "api.lever.co"
LEVER_API_ROOT = f"https://{LEVER_API_HOST}/v0/postings"
DEFAULT_PAGE_SIZE = 100
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_PAGES = 100
USER_AGENT = "linkedin-job-monitor/1.0 (public Lever postings adapter)"

type LeverHttpGet = Callable[[str, float], str | bytes]


class _ScraplingResponse(Protocol):
    status: int
    body: bytes | bytearray | memoryview


class _ScraplingSession(Protocol):
    def get(self, url: str) -> _ScraplingResponse: ...


def lever_site_from_url(source_jobs_url: str | None) -> str:
    """Return the single Lever site slug from a supported public jobs URL."""
    if source_jobs_url is None or not source_jobs_url.strip():
        raise SourceError("Lever company jobs URL is missing")

    try:
        parsed = urlsplit(source_jobs_url.strip())
        port = parsed.port
    except ValueError as error:
        raise SourceError("Lever company jobs URL is invalid") from error

    if (
        parsed.scheme != "https"
        or parsed.hostname != LEVER_JOBS_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise SourceError("Lever company jobs URL must use https://jobs.lever.co")

    path = parsed.path
    if not path.startswith("/"):
        raise SourceError("Lever company jobs URL must contain one site slug")
    without_leading_slash = path[1:]
    if without_leading_slash.endswith("/"):
        without_leading_slash = without_leading_slash[:-1]
    path_parts = without_leading_slash.split("/")
    if len(path_parts) != 1 or not path_parts[0]:
        raise SourceError("Lever company jobs URL must contain one site slug")

    site = unquote(path_parts[0])
    if not site or "/" in site or any(character.isspace() for character in site):
        raise SourceError("Lever company jobs URL contains an invalid site slug")
    return site


def _default_http_get(url: str, timeout_seconds: float) -> bytes:
    """Make one plain Scrapling HTTP attempt and return its raw response body."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != LEVER_API_HOST:
        raise RuntimeError("Lever requests must target the public HTTPS API")

    from scrapling.fetchers import FetcherSession

    with FetcherSession(
        http3=False,
        timeout=timeout_seconds,
        retries=1,
        retry_delay=0,
        follow_redirects=False,
        max_redirects=0,
        stealthy_headers=False,
        impersonate=None,
        proxies=None,
        proxy=None,
        proxy_auth=None,
        proxy_rotator=None,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    ) as session:
        response = cast(_ScraplingSession, session).get(url)

    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"Lever API returned HTTP {response.status}")
    return bytes(response.body)


def _optional_string(
    value: object,
    *,
    field: str,
    requests_made: int,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceError(
            f"Lever field {field} must be a string when present",
            requests_made=requests_made,
        )
    return value


def _categories(
    posting: Mapping[str, object], *, requests_made: int
) -> Mapping[str, object]:
    value = posting.get("categories")
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SourceError(
            "Lever field categories must be an object when present",
            requests_made=requests_made,
        )
    return cast(dict[str, object], value)


def _location(
    posting: Mapping[str, object],
    categories: Mapping[str, object],
    *,
    requests_made: int,
) -> str | None:
    location = _optional_string(
        categories.get("location"),
        field="categories.location",
        requests_made=requests_made,
    )
    if location:
        return location

    all_locations = posting.get("allLocations")
    if all_locations is None:
        return location
    if not isinstance(all_locations, list) or any(
        not isinstance(item, str) for item in all_locations
    ):
        raise SourceError(
            "Lever field allLocations must be an array of strings when present",
            requests_made=requests_made,
        )
    nonempty_locations = [item for item in all_locations if item]
    return ", ".join(nonempty_locations) or None


def _workplace_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    return {
        "remote": "remote",
        "hybrid": "hybrid",
        "on-site": "onsite",
        "onsite": "onsite",
    }.get(normalized)


def _published_at(value: object, *, requests_made: int) -> str | None:
    if value is None:
        return None
    if type(value) is not int:
        raise SourceError(
            "Lever field createdAt must be integer milliseconds when present",
            requests_made=requests_made,
        )
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError) as error:
        raise SourceError(
            "Lever field createdAt is outside the supported datetime range",
            requests_made=requests_made,
        ) from error


def _description(posting: Mapping[str, object], *, requests_made: int) -> str | None:
    description = _optional_string(
        posting.get("descriptionPlain"),
        field="descriptionPlain",
        requests_made=requests_made,
    )
    additional = _optional_string(
        posting.get("additionalPlain"),
        field="additionalPlain",
        requests_made=requests_made,
    )
    parts = [part.strip() for part in (description, additional) if part and part.strip()]
    return "\n\n".join(parts) or None


def _posting_to_record(posting: object, *, requests_made: int) -> SourceRecord:
    if not isinstance(posting, dict) or any(not isinstance(key, str) for key in posting):
        raise SourceError(
            "Each Lever posting must be an object",
            requests_made=requests_made,
        )
    typed_posting = cast(dict[str, object], posting)

    source_job_id = typed_posting.get("id")
    if not isinstance(source_job_id, str) or not source_job_id.strip():
        raise SourceError(
            "Lever field id must be a non-empty string",
            requests_made=requests_made,
        )
    source_job_id = source_job_id.strip()

    categories = _categories(typed_posting, requests_made=requests_made)
    department = _optional_string(
        categories.get("department"),
        field="categories.department",
        requests_made=requests_made,
    )
    team = _optional_string(
        categories.get("team"),
        field="categories.team",
        requests_made=requests_made,
    )
    workplace = _optional_string(
        typed_posting.get("workplaceType"),
        field="workplaceType",
        requests_made=requests_made,
    )

    return {
        "source": "lever",
        "source_job_id": source_job_id,
        "source_job_url": _optional_string(
            typed_posting.get("hostedUrl"),
            field="hostedUrl",
            requests_made=requests_made,
        ),
        "title": _optional_string(
            typed_posting.get("text"),
            field="text",
            requests_made=requests_made,
        ),
        "location": _location(
            typed_posting,
            categories,
            requests_made=requests_made,
        ),
        "country": _optional_string(
            typed_posting.get("country"),
            field="country",
            requests_made=requests_made,
        ),
        "city": None,
        "workplace_type": _workplace_type(workplace),
        "employment_type": _optional_string(
            categories.get("commitment"),
            field="categories.commitment",
            requests_made=requests_made,
        ),
        "published_at": _published_at(
            typed_posting.get("createdAt"),
            requests_made=requests_made,
        ),
        "description": _description(typed_posting, requests_made=requests_made),
        "job_function": department or team,
        "seniority_level": None,
        "industry": None,
    }


class LeverSourceAdapter:
    """Collect a complete, deduplicated snapshot from Lever's public API."""

    def __init__(
        self,
        *,
        http_get: LeverHttpGet = _default_http_get,
        page_size: int = DEFAULT_PAGE_SIZE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> None:
        if type(page_size) is not int or page_size < 1:
            raise ValueError("page_size must be a positive int, excluding bool")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive, excluding bool")
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("max_pages must be a positive int, excluding bool")
        self._http_get = http_get
        self._page_size = page_size
        self._timeout_seconds = float(timeout_seconds)
        self._max_pages = max_pages

    def fetch(self, *, company: SourceCompany) -> SourceBatch:
        site = lever_site_from_url(company.source_jobs_url)
        records: list[SourceRecord] = []
        seen_ids: set[str] = set()
        requests_made = 0
        skip = 0

        for _page_number in range(self._max_pages):
            query = urlencode(
                {"mode": "json", "limit": self._page_size, "skip": skip}
            )
            url = f"{LEVER_API_ROOT}/{quote(site, safe='')}?{query}"
            requests_made += 1
            try:
                body = self._http_get(url, self._timeout_seconds)
            except Exception as error:
                raise SourceError(
                    "Lever API request failed",
                    requests_made=requests_made,
                ) from error

            try:
                page = cast(object, json.loads(body))
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
                raise SourceError(
                    "Lever API response must contain valid JSON",
                    requests_made=requests_made,
                ) from error
            if not isinstance(page, list):
                raise SourceError(
                    "Lever API response root must be an array",
                    requests_made=requests_made,
                )
            if not page:
                return SourceBatch(records=tuple(records), requests_made=requests_made)

            new_records = 0
            for posting in page:
                record = _posting_to_record(posting, requests_made=requests_made)
                source_job_id = cast(str, record["source_job_id"])
                if source_job_id in seen_ids:
                    continue
                seen_ids.add(source_job_id)
                records.append(record)
                new_records += 1

            if new_records == 0:
                raise SourceError(
                    "Lever pagination returned no new job IDs",
                    requests_made=requests_made,
                )
            if len(page) < self._page_size:
                return SourceBatch(records=tuple(records), requests_made=requests_made)
            skip += len(page)

        raise SourceError(
            "Lever pagination safety page limit reached before the end",
            requests_made=requests_made,
        )
