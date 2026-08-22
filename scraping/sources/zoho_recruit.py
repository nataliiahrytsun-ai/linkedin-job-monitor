"""Read complete public Zoho Recruit career-site snapshots from embedded JSON."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import quote, urlsplit, urlunsplit

from lxml import etree  # type: ignore[import-untyped]
from lxml import html as lxml_html

from discovery.network import validate_public_url
from scraping.sources.base import SourceBatch, SourceCompany, SourceError, SourceRecord

DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_PUBLIC_CAREER_SITE_JOBS = 750
USER_AGENT = "linkedin-job-monitor/1.0 (public Zoho Recruit career-site adapter)"
_JOB_ID = re.compile(r"^[1-9][0-9]{8,24}$")
_SPACE = re.compile(r"\s+")

type ZohoRecruitHttpGet = Callable[[str, float], str | bytes]


class _ScraplingResponse(Protocol):
    status: int
    body: bytes | bytearray | memoryview
    url: object


class _ScraplingSession(Protocol):
    def get(self, url: str) -> _ScraplingResponse: ...


@dataclass(frozen=True, slots=True)
class ZohoRecruitSourceLocation:
    """Canonical public listing location for one Zoho Recruit career page."""

    host: str
    page_name: str
    listing_url: str


def zoho_recruit_source_from_url(source_jobs_url: str | None) -> ZohoRecruitSourceLocation:
    """Validate a public Zoho Recruit career-site route and canonicalize to its listing."""
    if source_jobs_url is None or not source_jobs_url.strip():
        raise SourceError("Zoho Recruit company jobs URL is missing")
    try:
        parsed = urlsplit(source_jobs_url.strip())
        port = parsed.port
    except ValueError as error:
        raise SourceError("Zoho Recruit company jobs URL is invalid") from error

    host = (parsed.hostname or "").casefold().rstrip(".")
    segments = tuple(segment for segment in parsed.path.split("/") if segment)
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or len(segments) < 2
        or segments[0].casefold() != "jobs"
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", segments[1])
    ):
        raise SourceError(
            "Zoho Recruit company jobs URL must use a public HTTPS /jobs/<page> route"
        )
    page_name = segments[1]
    return ZohoRecruitSourceLocation(
        host=host,
        page_name=page_name,
        listing_url=urlunsplit(("https", host, f"/jobs/{page_name}", "", "")),
    )


def _clean_text(value: object, *, field: str, requests_made: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceError(
            f"Zoho Recruit field {field} must be text or null",
            requests_made=requests_made,
        )
    cleaned = _SPACE.sub(" ", value).strip()
    return cleaned or None


def _required_text(value: object, *, field: str, requests_made: int) -> str:
    cleaned = _clean_text(value, field=field, requests_made=requests_made)
    if cleaned is None:
        raise SourceError(
            f"Zoho Recruit job is missing {field}",
            requests_made=requests_made,
        )
    return cleaned


def _parse_document(body: str | bytes, *, requests_made: int) -> etree._Element:
    try:
        raw = body.encode("utf-8") if isinstance(body, str) else body
        return lxml_html.fromstring(raw)
    except (etree.ParserError, etree.XMLSyntaxError, TypeError, ValueError) as error:
        raise SourceError(
            "Zoho Recruit listing response is not valid HTML",
            requests_made=requests_made,
        ) from error


def _single_input(document: etree._Element, input_id: str, *, requests_made: int) -> str:
    nodes = document.xpath(f'//input[@id="{input_id}"]')
    if len(nodes) != 1:
        raise SourceError(
            f"Zoho Recruit listing must contain exactly one {input_id} payload",
            requests_made=requests_made,
        )
    value = nodes[0].get("value")
    if not isinstance(value, str) or not value.strip():
        raise SourceError(
            f"Zoho Recruit {input_id} payload is empty",
            requests_made=requests_made,
        )
    return value


def _json_payload(
    document: etree._Element,
    input_id: str,
    *,
    requests_made: int,
) -> object:
    raw = _single_input(document, input_id, requests_made=requests_made)
    try:
        return cast(object, json.loads(raw))
    except json.JSONDecodeError as error:
        raise SourceError(
            f"Zoho Recruit {input_id} payload is malformed JSON",
            requests_made=requests_made,
        ) from error


def _mapping(value: object, *, field: str, requests_made: int) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise SourceError(
            f"Zoho Recruit {field} payload must be an object",
            requests_made=requests_made,
        )
    return cast(dict[str, object], value)


def _validate_platform_contract(
    document: etree._Element,
    *,
    source: ZohoRecruitSourceLocation,
    meta: Mapping[str, object],
    page_json: Mapping[str, object],
    module_meta: object,
    requests_made: int,
) -> None:
    script_sources = tuple(
        str(node.get("src") or "").casefold() for node in document.xpath("//script[@src]")
    )
    if not any("static.zohocdn.com/recruit/" in src for src in script_sources):
        raise SourceError(
            "Zoho Recruit listing is missing its platform asset signature",
            requests_made=requests_made,
        )
    if len(document.xpath('//*[@id="career-website-main"]')) != 1:
        raise SourceError(
            "Zoho Recruit listing is missing its career-site root",
            requests_made=requests_made,
        )

    if meta.get("source") != "CareerSite" or meta.get("chatbotName") != "zohorecruit":
        raise SourceError(
            "Zoho Recruit listing metadata has an unsupported platform contract",
            requests_made=requests_made,
        )
    recruit_home = _required_text(
        meta.get("recruit_home"), field="meta.recruit_home", requests_made=requests_made
    )
    recruit_home_url = urlsplit(recruit_home)
    if (
        recruit_home_url.scheme.casefold() != "https"
        or not (recruit_home_url.hostname or "").casefold().startswith("www.zoho.")
        or recruit_home_url.path.rstrip("/").casefold() != "/recruit"
    ):
        raise SourceError(
            "Zoho Recruit listing metadata has an invalid Recruit home",
            requests_made=requests_made,
        )
    portal_user = _mapping(
        meta.get("portal_user"), field="meta.portal_user", requests_made=requests_made
    )
    if portal_user.get("is_loggedin") is not False:
        raise SourceError(
            "Zoho Recruit listing is not an unauthenticated public snapshot",
            requests_made=requests_made,
        )
    meta_list_url = _required_text(
        meta.get("list_url"), field="meta.list_url", requests_made=requests_made
    )
    try:
        meta_source = zoho_recruit_source_from_url(meta_list_url)
    except SourceError as error:
        raise SourceError(
            "Zoho Recruit listing metadata contains an invalid list URL",
            requests_made=requests_made,
        ) from error
    if meta_source.listing_url != source.listing_url:
        raise SourceError(
            "Zoho Recruit listing metadata belongs to a different career page",
            requests_made=requests_made,
        )

    detail = _mapping(page_json.get("detail"), field="pageJson.detail", requests_made=requests_made)
    section = _mapping(detail.get("section"), field="pageJson.section", requests_made=requests_made)
    blocks = section.get("data")
    if not isinstance(blocks, list):
        raise SourceError(
            "Zoho Recruit pageJson jobs layout is malformed",
            requests_made=requests_made,
        )
    enabled_jobs = [
        block
        for block in blocks
        if isinstance(block, dict)
        and block.get("blocktype") == "jobs"
        and block.get("isenabled") is True
    ]
    if len(enabled_jobs) != 1:
        raise SourceError(
            "Zoho Recruit listing does not expose exactly one complete jobs block",
            requests_made=requests_made,
        )

    if not isinstance(module_meta, list) or not any(
        isinstance(item, dict) and item.get("api_name") == "Job_Openings"
        for item in module_meta
    ):
        raise SourceError(
            "Zoho Recruit module metadata is missing Job_Openings",
            requests_made=requests_made,
        )


def _has_pagination(document: etree._Element) -> bool:
    for node in document.xpath("//a[@href]"):
        label = _SPACE.sub(" ", " ".join(node.itertext())).strip().casefold()
        rel = str(node.get("rel") or "").casefold().split()
        href = str(node.get("href") or "")
        query = urlsplit(href).query.casefold()
        if (
            "next" in rel
            or label in {"next", "next page", "older"}
            or re.search(r"(?:^|&)(?:page|offset|start|cursor)=", query)
        ):
            return True
    return False


def _location(job: Mapping[str, object], *, requests_made: int) -> str | None:
    parts: list[str] = []
    for field in ("City", "State", "Country"):
        value = _clean_text(job.get(field), field=field, requests_made=requests_made)
        if value is not None and value.casefold() not in {part.casefold() for part in parts}:
            parts.append(value)
    return ", ".join(parts) or None


def _job_record(
    value: object,
    *,
    source: ZohoRecruitSourceLocation,
    requests_made: int,
) -> SourceRecord:
    job = _mapping(value, field="jobs item", requests_made=requests_made)
    source_job_id = _required_text(job.get("id"), field="id", requests_made=requests_made)
    if not _JOB_ID.fullmatch(source_job_id):
        raise SourceError(
            "Zoho Recruit job id is not a stable record ID",
            requests_made=requests_made,
        )
    if job.get("Publish") is not True:
        raise SourceError(
            "Zoho Recruit jobs payload contains a non-published record",
            requests_made=requests_made,
        )

    posting_title = _clean_text(
        job.get("Posting_Title"), field="Posting_Title", requests_made=requests_made
    )
    opening_name = _clean_text(
        job.get("Job_Opening_Name"), field="Job_Opening_Name", requests_made=requests_made
    )
    title = posting_title or opening_name
    if title is None:
        raise SourceError(
            "Zoho Recruit job is missing its title",
            requests_made=requests_made,
        )

    remote = job.get("Remote_Job")
    if remote is not None and not isinstance(remote, bool):
        raise SourceError(
            "Zoho Recruit field Remote_Job must be boolean or null",
            requests_made=requests_made,
        )
    return {
        "source": "zoho_recruit",
        "source_job_id": source_job_id,
        "source_job_url": f"{source.listing_url}/{quote(source_job_id, safe='')}",
        "title": title,
        "location": _location(job, requests_made=requests_made),
        "country": _clean_text(job.get("Country"), field="Country", requests_made=requests_made),
        "city": _clean_text(job.get("City"), field="City", requests_made=requests_made),
        "workplace_type": "Remote" if remote is True else None,
        "employment_type": _clean_text(
            job.get("Job_Type"), field="Job_Type", requests_made=requests_made
        ),
        "compensation_text": _clean_text(
            job.get("Salary"), field="Salary", requests_made=requests_made
        ),
        "published_at": _clean_text(
            job.get("Date_Opened"), field="Date_Opened", requests_made=requests_made
        ),
        "description": _clean_text(
            job.get("Job_Description"), field="Job_Description", requests_made=requests_made
        ),
        "job_function": None,
        "seniority_level": None,
        "industry": _clean_text(
            job.get("Industry"), field="Industry", requests_made=requests_made
        ),
    }


def parse_zoho_recruit_listing(
    body: str | bytes,
    *,
    source: ZohoRecruitSourceLocation,
    requests_made: int,
) -> tuple[SourceRecord, ...]:
    """Parse one complete embedded Zoho Recruit snapshot or fail closed."""
    document = _parse_document(body, requests_made=requests_made)
    if _has_pagination(document):
        raise SourceError(
            "Zoho Recruit listing exposes unhandled pagination",
            requests_made=requests_made,
        )

    jobs_payload = _json_payload(document, "jobs", requests_made=requests_made)
    meta = _mapping(
        _json_payload(document, "meta", requests_made=requests_made),
        field="meta",
        requests_made=requests_made,
    )
    page_json = _mapping(
        _json_payload(document, "pageJson", requests_made=requests_made),
        field="pageJson",
        requests_made=requests_made,
    )
    module_meta = _json_payload(document, "moduleMeta", requests_made=requests_made)
    _validate_platform_contract(
        document,
        source=source,
        meta=meta,
        page_json=page_json,
        module_meta=module_meta,
        requests_made=requests_made,
    )

    if not isinstance(jobs_payload, list):
        raise SourceError(
            "Zoho Recruit jobs payload must be an array",
            requests_made=requests_made,
        )
    if not jobs_payload:
        raise SourceError(
            "Zoho Recruit listing contains no verifiable jobs snapshot",
            requests_made=requests_made,
        )
    if len(jobs_payload) >= MAX_PUBLIC_CAREER_SITE_JOBS:
        raise SourceError(
            "Zoho Recruit listing reached the public career-site limit; completeness is unknown",
            requests_made=requests_made,
        )

    records: list[SourceRecord] = []
    seen_ids: set[str] = set()
    for value in jobs_payload:
        record = _job_record(value, source=source, requests_made=requests_made)
        source_job_id = cast(str, record["source_job_id"])
        if source_job_id in seen_ids:
            raise SourceError(
                "Zoho Recruit jobs payload contains a duplicate stable ID",
                requests_made=requests_made,
            )
        seen_ids.add(source_job_id)
        records.append(record)
    return tuple(records)


class ZohoRecruitSourceAdapter:
    """Collect one public server-rendered Zoho Recruit snapshot or fail closed."""

    def __init__(
        self,
        *,
        http_get: ZohoRecruitHttpGet | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive, excluding bool")
        self._http_get = http_get
        self._timeout_seconds = float(timeout_seconds)

    def _fetch_with_http_get(
        self,
        *,
        company: SourceCompany,
        http_get: ZohoRecruitHttpGet,
    ) -> SourceBatch:
        source = zoho_recruit_source_from_url(company.source_jobs_url)
        requests_made = 1
        try:
            body = http_get(source.listing_url, self._timeout_seconds)
        except Exception as error:
            raise SourceError(
                "Zoho Recruit listing request failed",
                requests_made=requests_made,
            ) from error
        records = parse_zoho_recruit_listing(
            body,
            source=source,
            requests_made=requests_made,
        )
        return SourceBatch(records=records, requests_made=requests_made)

    def fetch(self, *, company: SourceCompany) -> SourceBatch:
        if self._http_get is not None:
            return self._fetch_with_http_get(company=company, http_get=self._http_get)

        source = zoho_recruit_source_from_url(company.source_jobs_url)
        validate_public_url(source.listing_url)
        from scrapling.fetchers import FetcherSession

        with FetcherSession(
            http3=False,
            timeout=self._timeout_seconds,
            retries=1,
            retry_delay=0,
            follow_redirects="safe",
            max_redirects=5,
            stealthy_headers=False,
            impersonate=None,
            proxies=None,
            proxy=None,
            proxy_auth=None,
            proxy_rotator=None,
            headers={"Accept": "text/html", "User-Agent": USER_AGENT},
        ) as session:
            typed_session = cast(_ScraplingSession, session)

            def session_get(url: str, _timeout_seconds: float) -> bytes:
                response = typed_session.get(url)
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"Zoho Recruit returned HTTP {response.status}")
                final = zoho_recruit_source_from_url(str(response.url))
                if final.listing_url != source.listing_url:
                    raise RuntimeError("Zoho Recruit redirected outside the configured career page")
                return bytes(response.body)

            return self._fetch_with_http_get(company=company, http_get=session_get)
