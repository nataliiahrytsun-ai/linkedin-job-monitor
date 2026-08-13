"""Read public DreamJobs career snapshots through the source-neutral contract."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lxml import etree  # type: ignore[import-untyped]
from lxml import html as lxml_html

from scraping.sources.base import SourceBatch, SourceCompany, SourceError, SourceRecord

DREAMJOBS_API_URL = "https://api.dream.jobs"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_REQUESTS = 1000
USER_AGENT = "linkedin-job-monitor/1.0 (public DreamJobs career adapter)"

LIST_QUERY = """
query opportunitiesList($filter: OpportunitiesFilterInput, $pagination: PaginationInput) {
  opportunities(filter: $filter, pagination: $pagination) {
    paginationInfo { itemsTotalCount }
    opportunities {
      id title company { id name integrationType }
      locations { region countryCode city country }
      salary { min max currency }
      workEnvironment type
    }
  }
}
""".strip()

DETAIL_QUERY = """
query opportunityDetail($filter: OpportunityInput) {
  opportunity(filter: $filter) {
    id company { id name integrationType }
    externalUrl type title
    locations { region countryCode city country }
    salary { min max currency }
    workEnvironment rawDescription
  }
}
""".strip()

type DreamJobsHttpRequest = Callable[
    [str, str, Mapping[str, str], Mapping[str, object] | None, float], str | bytes
]


class _ScraplingResponse(Protocol):
    status: int
    body: bytes | bytearray | memoryview
    url: object


class _ScraplingSession(Protocol):
    def get(self, url: str, *, headers: Mapping[str, str]) -> _ScraplingResponse: ...

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, object],
    ) -> _ScraplingResponse: ...


@dataclass(frozen=True, slots=True)
class DreamJobsSourceLocation:
    """Validated public career URL and tenant identity discovered from its HTML."""

    listing_url: str
    host: str


def dreamjobs_source_from_url(source_jobs_url: str | None) -> DreamJobsSourceLocation:
    """Validate and canonicalize a public HTTPS DreamJobs career listing URL."""
    value = source_jobs_url.strip() if source_jobs_url is not None else ""
    if not value:
        raise SourceError("DreamJobs company jobs URL is missing")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise SourceError("DreamJobs company jobs URL is invalid") from error
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path.rstrip("/") != "/jobs"
    ):
        raise SourceError("DreamJobs company jobs URL must be an HTTPS /jobs URL")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"activeOpportunityId", "page", "similarVacancyId"}
    ]
    host = parsed.hostname.lower()
    return DreamJobsSourceLocation(
        listing_url=urlunsplit(("https", host, "/jobs", urlencode(query), "")),
        host=host,
    )


def _decode_json(body: str | bytes, *, context: str, requests_made: int) -> object:
    try:
        return cast(object, json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise SourceError(
            f"DreamJobs {context} response must contain valid JSON",
            requests_made=requests_made,
        ) from error


def _object(value: object, *, field: str, requests_made: int) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SourceError(
            f"DreamJobs field {field} must be an object",
            requests_made=requests_made,
        )
    return cast(dict[str, object], value)


def _array(value: object, *, field: str, requests_made: int) -> list[object]:
    if not isinstance(value, list):
        raise SourceError(
            f"DreamJobs field {field} must be an array",
            requests_made=requests_made,
        )
    return cast(list[object], value)


def _optional_text(value: object, *, field: str, requests_made: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceError(
            f"DreamJobs field {field} must be a string when present",
            requests_made=requests_made,
        )
    cleaned = " ".join(value.split())
    return cleaned or None


def _parse_html(body: str | bytes, *, requests_made: int) -> etree._Element:
    try:
        document = lxml_html.fromstring(body)
    except (etree.ParserError, etree.XMLSyntaxError, TypeError, ValueError) as error:
        raise SourceError(
            "DreamJobs listing response is not valid HTML",
            requests_made=requests_made,
        ) from error
    title = " ".join(document.xpath("string(//title)").split()).casefold()
    body_text = " ".join(document.xpath("string(//body)").split()).casefold()
    if "access denied" in title or document.xpath('//*[@id="captcha-container"]'):
        raise SourceError(
            "DreamJobs listing returned an access restriction or challenge",
            requests_made=requests_made,
        )
    if document.xpath('//form[contains(@action, "/login")]') and "jobs" not in body_text:
        raise SourceError(
            "DreamJobs listing returned an access restriction or challenge",
            requests_made=requests_made,
        )
    return document


def _embedded_listing(
    document: etree._Element, *, requests_made: int
) -> tuple[str, dict[str, object], int]:
    scripts = document.xpath('//script[@id="__NEXT_DATA__"]/text()')
    if len(scripts) != 1:
        raise SourceError(
            "DreamJobs page must contain one Next.js data snapshot",
            requests_made=requests_made,
        )
    root = _object(
        _decode_json(scripts[0], context="listing", requests_made=requests_made),
        field="__NEXT_DATA__",
        requests_made=requests_made,
    )
    props = _object(root.get("props"), field="props", requests_made=requests_made)
    page_props = _object(props.get("pageProps"), field="pageProps", requests_made=requests_made)
    client_name = _optional_text(
        page_props.get("clientName") or props.get("clientName"),
        field="clientName",
        requests_made=requests_made,
    )
    if client_name is None:
        raise SourceError(
            "DreamJobs page is missing its tenant identity", requests_made=requests_made
        )
    dehydrated = _object(
        page_props.get("dehydratedState"),
        field="dehydratedState",
        requests_made=requests_made,
    )
    queries = _array(
        dehydrated.get("queries"), field="dehydratedState.queries", requests_made=requests_made
    )
    has_configuration = False
    listing: dict[str, object] | None = None
    page_size: int | None = None
    for raw_query in queries:
        query = _object(raw_query, field="query", requests_made=requests_made)
        query_key = query.get("queryKey")
        state = _object(query.get("state"), field="query.state", requests_made=requests_made)
        data = state.get("data")
        if isinstance(query_key, list) and query_key:
            first_key = query_key[0]
            if isinstance(first_key, str) and first_key.startswith("clientConfiguration-"):
                has_configuration = isinstance(data, dict)
            elif first_key == "opportunitiesList":
                listing = _object(data, field="opportunitiesList.data", requests_made=requests_made)
                if len(query_key) > 1 and isinstance(query_key[1], dict):
                    pagination = cast(dict[str, object], query_key[1]).get("pagination")
                    if isinstance(pagination, dict):
                        candidate = cast(dict[str, object], pagination).get("items")
                        if type(candidate) is int and candidate > 0:
                            page_size = candidate
    api_asset = document.xpath(
        '//*[@src or @href][contains(@src, "api.dream.jobs/static/") '
        'or contains(@href, "api.dream.jobs/static/")]'
    )
    if (
        root.get("page") != "/jobs"
        or not has_configuration
        or not api_asset
        or listing is None
        or page_size is None
    ):
        raise SourceError(
            "Configured page does not expose the verified DreamJobs technical signals",
            requests_made=requests_made,
        )
    return client_name, listing, page_size


def _graphql_data(value: object, *, context: str, requests_made: int) -> dict[str, object]:
    root = _object(value, field=f"{context} response", requests_made=requests_made)
    if root.get("errors") is not None:
        raise SourceError(
            f"DreamJobs {context} returned GraphQL errors",
            requests_made=requests_made,
        )
    return _object(root.get("data"), field=f"{context}.data", requests_made=requests_made)


def _listing_page(value: object, *, requests_made: int) -> tuple[list[dict[str, object]], int]:
    data = _object(value, field="listing data", requests_made=requests_made)
    opportunities = _object(
        data.get("opportunities"), field="opportunities", requests_made=requests_made
    )
    pagination = _object(
        opportunities.get("paginationInfo"),
        field="opportunities.paginationInfo",
        requests_made=requests_made,
    )
    total = pagination.get("itemsTotalCount")
    if type(total) is not int or total < 0:
        raise SourceError(
            "DreamJobs listing total must be a non-negative integer",
            requests_made=requests_made,
        )
    raw_jobs = _array(
        opportunities.get("opportunities"),
        field="opportunities.opportunities",
        requests_made=requests_made,
    )
    jobs = [_object(item, field="opportunity", requests_made=requests_made) for item in raw_jobs]
    return jobs, total


def _location(value: object, *, requests_made: int) -> tuple[str | None, str | None, str | None]:
    items = _array(value, field="locations", requests_made=requests_made)
    display: list[str] = []
    seen: set[str] = set()
    first_city: str | None = None
    first_country: str | None = None
    for item in items:
        location = _object(item, field="location", requests_made=requests_made)
        city = _optional_text(
            location.get("city"), field="location.city", requests_made=requests_made
        )
        region = _optional_text(
            location.get("region"), field="location.region", requests_made=requests_made
        )
        country = _optional_text(
            location.get("country"), field="location.country", requests_made=requests_made
        )
        first_city = first_city or city
        first_country = first_country or country
        label = ", ".join(part for part in (city, region, country) if part)
        if label and label.casefold() not in seen:
            seen.add(label.casefold())
            display.append(label)
    return " | ".join(display) or None, first_city, first_country


def _description(value: object, *, requests_made: int) -> str | None:
    raw = _optional_text(value, field="rawDescription", requests_made=requests_made)
    if raw is None:
        return None
    try:
        fragment = lxml_html.fragment_fromstring(raw, create_parent=True)
    except (etree.ParserError, etree.XMLSyntaxError, TypeError, ValueError) as error:
        raise SourceError(
            "DreamJobs detail description contains malformed HTML",
            requests_made=requests_made,
        ) from error
    for block in fragment.xpath(".//p|.//li|.//div|.//h1|.//h2|.//h3|.//br"):
        block.tail = "\n" + (block.tail or "")
    lines = [
        cleaned
        for line in fragment.text_content().splitlines()
        if (cleaned := " ".join(line.split()))
    ]
    return "\n".join(lines) or None


def _record(
    detail: dict[str, object], *, source: DreamJobsSourceLocation, requests_made: int
) -> SourceRecord:
    source_job_id = _optional_text(detail.get("id"), field="id", requests_made=requests_made)
    title = _optional_text(detail.get("title"), field="title", requests_made=requests_made)
    if source_job_id is None or title is None:
        raise SourceError(
            "DreamJobs detail is missing stable identity or title", requests_made=requests_made
        )
    location, city, country = _location(detail.get("locations"), requests_made=requests_made)
    work_environment = _optional_text(
        detail.get("workEnvironment"), field="workEnvironment", requests_made=requests_made
    )
    types = _array(detail.get("type"), field="type", requests_made=requests_made)
    employment = []
    for item in types:
        text = _optional_text(item, field="type item", requests_made=requests_made)
        if text:
            employment.append(text.replace("_", " ").title())
    _object(detail.get("company"), field="company", requests_made=requests_made)
    return {
        "source": "dreamjobs",
        "source_job_id": source_job_id,
        "source_job_url": f"{source.listing_url}?activeOpportunityId={source_job_id}",
        "title": title,
        "location": location,
        "country": country,
        "city": city,
        "workplace_type": {"REMOTE": "remote", "HYBRID": "hybrid", "ON_SITE": "onsite"}.get(
            work_environment or ""
        ),
        "employment_type": ", ".join(employment) or None,
        "published_at": None,
        "description": _description(detail.get("rawDescription"), requests_made=requests_made),
        "job_function": None,
        "seniority_level": None,
        "industry": None,
    }


class DreamJobsSourceAdapter:
    """Collect a complete bounded DreamJobs snapshot or fail closed."""

    def __init__(
        self,
        *,
        http_request: DreamJobsHttpRequest | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_requests: int = DEFAULT_MAX_REQUESTS,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive, excluding bool")
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("max_pages must be a positive int, excluding bool")
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("max_requests must be a positive int, excluding bool")
        self._http_request = http_request
        self._timeout_seconds = float(timeout_seconds)
        self._max_pages = max_pages
        self._max_requests = max_requests

    def _fetch(self, *, company: SourceCompany, request: DreamJobsHttpRequest) -> SourceBatch:
        source = dreamjobs_source_from_url(company.source_jobs_url)
        requests_made = 0

        def call(
            method: str,
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, object] | None,
            context: str,
        ) -> str | bytes:
            nonlocal requests_made
            if requests_made >= self._max_requests:
                raise SourceError(
                    "DreamJobs request safety limit reached", requests_made=requests_made
                )
            requests_made += 1
            try:
                return request(method, url, headers, payload, self._timeout_seconds)
            except SourceError:
                raise
            except Exception as error:
                raise SourceError(
                    f"DreamJobs {context} request failed", requests_made=requests_made
                ) from error

        html_body = call("GET", source.listing_url, {"Accept": "text/html"}, None, "listing")
        document = _parse_html(html_body, requests_made=requests_made)
        client_name, embedded, page_size = _embedded_listing(document, requests_made=requests_made)
        jobs, total = _listing_page(embedded, requests_made=requests_made)
        seen_ids: set[str] = set()
        listings: list[dict[str, object]] = []

        def add_page(page_jobs: list[dict[str, object]]) -> None:
            for job in page_jobs:
                job_id = _optional_text(job.get("id"), field="id", requests_made=requests_made)
                if job_id is None:
                    raise SourceError(
                        "DreamJobs listing job is missing its stable ID",
                        requests_made=requests_made,
                    )
                if job_id not in seen_ids:
                    seen_ids.add(job_id)
                    listings.append(job)

        add_page(jobs)
        pages_needed = max(1, (total + page_size - 1) // page_size)
        if pages_needed > self._max_pages:
            raise SourceError(
                "DreamJobs page safety limit is below the advertised snapshot",
                requests_made=requests_made,
            )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "graphql-require-preflight": "true",
            "jobnoone-webclientid": client_name,
        }
        for page in range(2, pages_needed + 1):
            payload: Mapping[str, object] = {
                "query": LIST_QUERY,
                "variables": {"filter": {}, "pagination": {"items": page_size, "page": page}},
            }
            raw = call("POST", DREAMJOBS_API_URL, headers, payload, "listing page")
            data = _graphql_data(
                _decode_json(raw, context="listing page", requests_made=requests_made),
                context="listing page",
                requests_made=requests_made,
            )
            page_jobs, page_total = _listing_page(data, requests_made=requests_made)
            if page_total != total or not page_jobs:
                raise SourceError(
                    "DreamJobs pagination is incomplete or inconsistent",
                    requests_made=requests_made,
                )
            before = len(seen_ids)
            add_page(page_jobs)
            if len(seen_ids) == before:
                raise SourceError(
                    "DreamJobs pagination returned no new job IDs", requests_made=requests_made
                )
        if len(seen_ids) != total:
            raise SourceError(
                "DreamJobs snapshot did not contain every advertised job",
                requests_made=requests_made,
            )

        records: list[SourceRecord] = []
        for listing in listings:
            job_id = cast(
                str, _optional_text(listing.get("id"), field="id", requests_made=requests_made)
            )
            payload = {"query": DETAIL_QUERY, "variables": {"filter": {"id": job_id}}}
            raw = call("POST", DREAMJOBS_API_URL, headers, payload, "detail")
            data = _graphql_data(
                _decode_json(raw, context="detail", requests_made=requests_made),
                context="detail",
                requests_made=requests_made,
            )
            detail = _object(
                data.get("opportunity"), field="opportunity", requests_made=requests_made
            )
            if detail.get("id") != job_id:
                raise SourceError(
                    "DreamJobs detail returned a conflicting stable ID", requests_made=requests_made
                )
            records.append(_record(detail, source=source, requests_made=requests_made))
        return SourceBatch(records=tuple(records), requests_made=requests_made)

    def fetch(self, *, company: SourceCompany) -> SourceBatch:
        if self._http_request is not None:
            return self._fetch(company=company, request=self._http_request)
        source = dreamjobs_source_from_url(company.source_jobs_url)
        from scrapling.fetchers import FetcherSession

        with FetcherSession(
            http3=False,
            timeout=self._timeout_seconds,
            retries=2,
            retry_delay=1,
            follow_redirects="safe",
            max_redirects=5,
            stealthy_headers=False,
            impersonate=None,
            proxies=None,
            proxy=None,
            proxy_auth=None,
            headers={"User-Agent": USER_AGENT},
        ) as session:
            typed_session = cast(_ScraplingSession, session)

            def request(
                method: str,
                url: str,
                headers: Mapping[str, str],
                payload: Mapping[str, object] | None,
                _timeout: float,
            ) -> bytes:
                if method == "GET":
                    response = typed_session.get(url, headers=headers)
                    final = urlsplit(str(response.url))
                    if final.scheme.lower() != "https" or final.hostname != source.host:
                        raise RuntimeError(
                            "DreamJobs listing redirected outside the configured host"
                        )
                elif method == "POST" and url == DREAMJOBS_API_URL and payload is not None:
                    response = typed_session.post(url, headers=headers, json=payload)
                else:
                    raise RuntimeError("DreamJobs transport rejected an unexpected request")
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"DreamJobs returned HTTP {response.status}")
                return bytes(response.body)

            return self._fetch(company=company, request=request)
