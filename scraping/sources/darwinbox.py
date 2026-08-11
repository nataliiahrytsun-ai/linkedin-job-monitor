"""Read complete public Darwinbox job snapshots through the source contract."""

from __future__ import annotations

import html
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from urllib.parse import quote, unquote, urlencode, urlsplit

from scraping.sources.base import SourceBatch, SourceCompany, SourceError, SourceRecord

DEFAULT_PAGE_SIZE = 10
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_PAGES = 100
DARWINBOX_HOST_SUFFIX = ".darwinbox.com"
USER_AGENT = "linkedin-job-monitor/1.0 (public Darwinbox jobs adapter)"

type DarwinboxMethod = Literal["GET", "POST"]
type DarwinboxRequest = Callable[
    [DarwinboxMethod, str, Mapping[str, object] | None, float], str | bytes
]


class _ScraplingResponse(Protocol):
    status: int
    body: bytes | bytearray | memoryview


class _ScraplingSession(Protocol):
    def get(self, url: str) -> _ScraplingResponse: ...

    def post(self, url: str, *, json: dict[str, object]) -> _ScraplingResponse: ...


@dataclass(frozen=True, slots=True)
class DarwinboxSourceLocation:
    """Validated tenant and public company identifier from a careers URL."""

    scheme: str
    host: str
    company_id: str


def darwinbox_source_from_url(source_jobs_url: str | None) -> DarwinboxSourceLocation:
    """Parse the public route shapes proven by the reference Darwinbox tenant."""
    if source_jobs_url is None or not source_jobs_url.strip():
        raise SourceError("Darwinbox company jobs URL is missing")
    try:
        parsed = urlsplit(source_jobs_url.strip())
        port = parsed.port
    except ValueError as error:
        raise SourceError("Darwinbox company jobs URL is invalid") from error

    host = parsed.hostname or ""
    if (
        parsed.scheme not in {"http", "https"}
        or not host.endswith(DARWINBOX_HOST_SUFFIX)
        or host == DARWINBOX_HOST_SUFFIX[1:]
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise SourceError("Darwinbox jobs URL must use a Darwinbox tenant host")

    parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    if parts == ["ms", "candidate", "careers"]:
        company_id = "main"
    elif (
        len(parts) >= 4
        and parts[:2] == ["ms", "candidatev2"]
        and parts[3] == "careers"
    ):
        company_id = parts[2]
    else:
        raise SourceError("Darwinbox jobs URL has an unsupported careers path")

    if not company_id or "/" in company_id or any(char.isspace() for char in company_id):
        raise SourceError("Darwinbox jobs URL contains an invalid company identifier")
    return DarwinboxSourceLocation(parsed.scheme, host, company_id)


def _default_request(
    method: DarwinboxMethod,
    url: str,
    json_body: Mapping[str, object] | None,
    timeout_seconds: float,
) -> bytes:
    """Make one conservative public request with Scrapling and no browser state."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (
        parsed.scheme not in {"http", "https"}
        or not host.endswith(DARWINBOX_HOST_SUFFIX)
        or not parsed.path.startswith("/ms/candidateapi/job/")
    ):
        raise RuntimeError("Darwinbox requests must target a public candidate API")

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
        typed_session = cast(_ScraplingSession, session)
        if method == "POST":
            if json_body is None:
                raise RuntimeError("Darwinbox POST requests require a JSON body")
            response = typed_session.post(url, json=dict(json_body))
        else:
            if json_body is not None:
                raise RuntimeError("Darwinbox GET requests cannot contain a JSON body")
            response = typed_session.get(url)

    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"Darwinbox API returned HTTP {response.status}")
    return bytes(response.body)


def _json_object(body: str | bytes, *, context: str, requests_made: int) -> dict[str, object]:
    try:
        payload = cast(object, json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise SourceError(
            f"Darwinbox {context} response must contain valid JSON",
            requests_made=requests_made,
        ) from error
    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
        raise SourceError(
            f"Darwinbox {context} response root must be an object",
            requests_made=requests_made,
        )
    typed = cast(dict[str, object], payload)
    if typed.get("status") != "success":
        raise SourceError(
            f"Darwinbox {context} response status is not success",
            requests_made=requests_made,
        )
    return typed


def _optional_string(value: object, *, field: str, requests_made: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceError(
            f"Darwinbox field {field} must be a string when present",
            requests_made=requests_made,
        )
    return value.strip() or None


def _first_text(
    posting: Mapping[str, object], fields: tuple[str, ...], *, requests_made: int
) -> str | None:
    for field in fields:
        value = _optional_string(posting.get(field), field=field, requests_made=requests_made)
        if value:
            return value
    return None


def _location(posting: Mapping[str, object], *, requests_made: int) -> str | None:
    direct = _optional_string(
        posting.get("locations"), field="locations", requests_made=requests_made
    )
    if direct:
        return direct
    values = posting.get("officelocations_without_area")
    if values is None:
        return None
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise SourceError(
            "Darwinbox field officelocations_without_area must be an array of strings",
            requests_made=requests_made,
        )
    return ", ".join(item.strip() for item in values if item.strip()) or None


def _published_at(posting: Mapping[str, object], *, requests_made: int) -> str | None:
    value = posting.get("posted_on")
    if value is None or value == "":
        value = posting.get("created_on")
    if value is None or value == "":
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).isoformat()
        except ValueError as error:
            raise SourceError(
                "Darwinbox posted date must be an ISO datetime when present",
                requests_made=requests_made,
            ) from error
    if type(value) in {int, float}:
        numeric = cast(int | float, value)
        seconds = numeric / 1000 if numeric > 10_000_000_000 else numeric
        try:
            return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
        except (OSError, OverflowError, ValueError) as error:
            raise SourceError(
                "Darwinbox posted date is outside the supported range",
                requests_made=requests_made,
            ) from error
    raise SourceError(
        "Darwinbox posted date has an unsupported type",
        requests_made=requests_made,
    )


def _description(posting: Mapping[str, object], *, requests_made: int) -> str | None:
    value = _optional_string(posting.get("jd"), field="jd", requests_made=requests_made)
    return html.unescape(value) if value else None


def _posting_id(posting: object, *, requests_made: int) -> tuple[dict[str, object], str]:
    if not isinstance(posting, dict) or any(not isinstance(key, str) for key in posting):
        raise SourceError(
            "Each Darwinbox job must be an object", requests_made=requests_made
        )
    typed = cast(dict[str, object], posting)
    source_job_id = _optional_string(
        typed.get("id"), field="id", requests_made=requests_made
    )
    if source_job_id is None:
        raise SourceError(
            "Darwinbox field id must be a non-empty string",
            requests_made=requests_made,
        )
    return typed, source_job_id


def _posting_to_record(
    posting: Mapping[str, object],
    *,
    source_job_id: str,
    source_url: DarwinboxSourceLocation,
    requests_made: int,
) -> SourceRecord:
    title = _first_text(
        posting,
        ("title", "designation_display_name", "designation_name"),
        requests_made=requests_made,
    )
    if title is None:
        raise SourceError(
            "Darwinbox job requires a title", requests_made=requests_made
        )
    company_id = quote(source_url.company_id, safe="")
    job_id = quote(source_job_id, safe="")
    return {
        "source": "darwinbox",
        "source_job_id": source_job_id,
        "source_job_url": (
            f"{source_url.scheme}://{source_url.host}/ms/candidatev2/{company_id}"
            f"/careers/jobDetails/{job_id}?from=all"
        ),
        "title": title,
        "location": _location(posting, requests_made=requests_made),
        "country": _optional_string(
            posting.get("country"), field="country", requests_made=requests_made
        ),
        "city": None,
        "workplace_type": None,
        "employment_type": _first_text(
            posting, ("emp_type_name", "emp_sub_type_name", "emp_type"), requests_made=requests_made
        ),
        "published_at": _published_at(posting, requests_made=requests_made),
        "description": _description(posting, requests_made=requests_made),
        "job_function": _first_text(
            posting,
            ("department_name_only", "department_name", "department"),
            requests_made=requests_made,
        ),
        "seniority_level": None,
        "industry": None,
    }


class DarwinboxSourceAdapter:
    """Collect a complete, deduplicated Darwinbox snapshot or fail closed."""

    def __init__(
        self,
        *,
        request: DarwinboxRequest = _default_request,
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
        self._request = request
        self._page_size = page_size
        self._timeout_seconds = float(timeout_seconds)
        self._max_pages = max_pages

    def _request_body(
        self,
        method: DarwinboxMethod,
        url: str,
        json_body: Mapping[str, object] | None,
        *,
        requests_made: int,
    ) -> str | bytes:
        try:
            return self._request(method, url, json_body, self._timeout_seconds)
        except Exception as error:
            raise SourceError(
                "Darwinbox public API request failed", requests_made=requests_made
            ) from error

    def _detail(
        self,
        *,
        source_url: DarwinboxSourceLocation,
        source_job_id: str,
        requests_made: int,
    ) -> dict[str, object]:
        query = urlencode({"companyId": source_url.company_id})
        url = (
            f"{source_url.scheme}://{source_url.host}/ms/candidateapi/job/"
            f"{quote(source_job_id, safe='')}?{query}"
        )
        body = self._request_body(
            "GET", url, None, requests_made=requests_made
        )
        payload = _json_object(body, context="detail", requests_made=requests_made)
        message = payload.get("message")
        if not isinstance(message, dict):
            raise SourceError(
                "Darwinbox detail message must be an object",
                requests_made=requests_made,
            )
        jobs = message.get("job")
        if not isinstance(jobs, list) or not jobs:
            raise SourceError(
                "Darwinbox detail job must be a non-empty array",
                requests_made=requests_made,
            )
        detail, detail_id = _posting_id(jobs[0], requests_made=requests_made)
        if detail_id != source_job_id:
            raise SourceError(
                "Darwinbox detail job ID does not match the requested job",
                requests_made=requests_made,
            )
        return detail

    def fetch(self, *, company: SourceCompany) -> SourceBatch:
        source_url = darwinbox_source_from_url(company.source_jobs_url)
        query = urlencode({"companyId": source_url.company_id})
        listing_url = (
            f"{source_url.scheme}://{source_url.host}/ms/candidateapi/job/alljobs?{query}"
        )
        records: list[SourceRecord] = []
        seen_ids: set[str] = set()
        seen_pages: set[tuple[str, ...]] = set()
        expected_total: int | None = None
        requests_made = 0

        for page_number in range(1, self._max_pages + 1):
            request_payload: dict[str, object] = {
                "companyId": source_url.company_id,
                "page": page_number,
                "sort_option": "new",
                "limit": self._page_size,
            }
            requests_made += 1
            body = self._request_body(
                "POST", listing_url, request_payload, requests_made=requests_made
            )
            payload = _json_object(
                body, context="listing", requests_made=requests_made
            )
            total = payload.get("job_counts")
            if type(total) is not int or total < 0:
                raise SourceError(
                    "Darwinbox job_counts must be a non-negative integer",
                    requests_made=requests_made,
                )
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise SourceError(
                    "Darwinbox job_counts changed during pagination",
                    requests_made=requests_made,
                )

            page = payload.get("data")
            if not isinstance(page, list):
                raise SourceError(
                    "Darwinbox listing data must be an array",
                    requests_made=requests_made,
                )
            parsed_jobs = [
                _posting_id(job, requests_made=requests_made) for job in page
            ]
            signature = tuple(source_job_id for _, source_job_id in parsed_jobs)
            if signature in seen_pages and len(seen_ids) < total:
                raise SourceError(
                    "Darwinbox pagination repeated a page before completion",
                    requests_made=requests_made,
                )
            seen_pages.add(signature)

            new_jobs: list[tuple[dict[str, object], str]] = []
            page_new_ids: set[str] = set()
            for item in parsed_jobs:
                source_job_id = item[1]
                if source_job_id in seen_ids or source_job_id in page_new_ids:
                    continue
                page_new_ids.add(source_job_id)
                new_jobs.append(item)
            if not new_jobs and len(seen_ids) < total:
                reason = "empty page" if not page else "no new job IDs"
                raise SourceError(
                    f"Darwinbox pagination returned {reason} before completion",
                    requests_made=requests_made,
                )
            for posting, source_job_id in new_jobs:
                seen_ids.add(source_job_id)
                if _description(posting, requests_made=requests_made) is None:
                    requests_made += 1
                    detail = self._detail(
                        source_url=source_url,
                        source_job_id=source_job_id,
                        requests_made=requests_made,
                    )
                    posting = {**posting, **detail}
                records.append(
                    _posting_to_record(
                        posting,
                        source_job_id=source_job_id,
                        source_url=source_url,
                        requests_made=requests_made,
                    )
                )

            if len(seen_ids) > total:
                raise SourceError(
                    "Darwinbox listing returned more unique jobs than job_counts",
                    requests_made=requests_made,
                )
            if len(seen_ids) == total:
                return SourceBatch(records=tuple(records), requests_made=requests_made)

        raise SourceError(
            "Darwinbox pagination safety page limit reached before completion",
            requests_made=requests_made,
        )
