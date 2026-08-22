"""Read complete public JazzHR/applytojob snapshots through the source contract."""

from __future__ import annotations

import html as stdlib_html
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from lxml import etree  # type: ignore[import-untyped]
from lxml import html as lxml_html

from scraping.sources.base import SourceBatch, SourceCompany, SourceError, SourceRecord

APPLYTOJOB_HOST_SUFFIX = ".applytojob.com"
DEFAULT_TIMEOUT_SECONDS = 20.0
USER_AGENT = "linkedin-job-monitor/1.0 (public JazzHR postings adapter)"
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
_SPACE = re.compile(r"\s+")
_REF_PREFIX = re.compile(r"^Ref\s*:\s*", re.IGNORECASE)

type JazzHRHttpGet = Callable[[str, float], str | bytes]


class _ScraplingResponse(Protocol):
    status: int
    body: bytes | bytearray | memoryview
    url: str


class _ScraplingSession(Protocol):
    def get(self, url: str) -> _ScraplingResponse: ...


@dataclass(frozen=True, slots=True)
class JazzHRSourceLocation:
    """Canonical public listing location for one JazzHR tenant."""

    host: str
    listing_url: str


@dataclass(frozen=True, slots=True)
class _ListingJob:
    source_job_id: str
    url: str
    title: str


def _applytojob_host(parsed_url: str, *, context: str) -> tuple[str, object]:
    try:
        parsed = urlsplit(parsed_url)
        port = parsed.port
    except ValueError as error:
        raise SourceError(f"JazzHR {context} URL is invalid") from error
    host = (parsed.hostname or "").lower()
    tenant = host[: -len(APPLYTOJOB_HOST_SUFFIX)] if host.endswith(APPLYTOJOB_HOST_SUFFIX) else ""
    if (
        parsed.scheme.lower() != "https"
        or not tenant
        or "." in tenant
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise SourceError(
            f"JazzHR {context} URL must use a public https://<tenant>.applytojob.com host"
        )
    return host, parsed


def jazzhr_source_from_url(source_jobs_url: str | None) -> JazzHRSourceLocation:
    """Validate a public JazzHR listing URL and return its canonical form."""
    if source_jobs_url is None or not source_jobs_url.strip():
        raise SourceError("JazzHR company jobs URL is missing")
    host, _parsed_object = _applytojob_host(
        source_jobs_url.strip(), context="company jobs"
    )
    path = urlsplit(source_jobs_url.strip()).path
    normalized_path = path.rstrip("/") or "/"
    if normalized_path not in {"/apply", "/apply/jobs"}:
        raise SourceError("JazzHR company jobs URL must use /apply or /apply/jobs")
    return JazzHRSourceLocation(host=host, listing_url=f"https://{host}/apply")


def jazzhr_job_id_from_url(
    job_url: str,
    *,
    expected_host: str | None = None,
) -> str:
    """Extract the stable opaque ID from current or legacy JazzHR detail URLs."""
    host, _parsed_object = _applytojob_host(job_url, context="job detail")
    if expected_host is not None and host != expected_host.lower():
        raise SourceError("JazzHR job detail URL belongs to a different tenant")
    path_parts = [unquote(part) for part in urlsplit(job_url).path.strip("/").split("/")]
    if len(path_parts) == 3 and path_parts[0] == "apply" and path_parts[1] != "jobs":
        opaque_id = path_parts[1]
        if not path_parts[2]:
            raise SourceError("JazzHR current job detail URL is missing its slug")
    elif len(path_parts) == 4 and path_parts[:3] == ["apply", "jobs", "details"]:
        opaque_id = path_parts[3]
    else:
        raise SourceError("JazzHR job detail URL has an unsupported path")
    if not _OPAQUE_ID.fullmatch(opaque_id):
        raise SourceError("JazzHR job detail URL is missing a stable opaque job ID")
    return opaque_id


def _canonical_job_url(job_url: str, *, expected_host: str) -> str:
    jazzhr_job_id_from_url(job_url, expected_host=expected_host)
    parsed = urlsplit(job_url)
    return urlunsplit(("https", expected_host, parsed.path.rstrip("/"), "", ""))


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _SPACE.sub(" ", stdlib_html.unescape(value)).strip()
    return cleaned or None


def _parse_document(body: str | bytes, *, context: str, requests_made: int) -> etree._Element:
    try:
        raw = body.encode() if isinstance(body, str) else body
        return lxml_html.fromstring(raw)
    except (etree.ParserError, etree.XMLSyntaxError, TypeError, ValueError) as error:
        raise SourceError(
            f"JazzHR {context} response is not valid HTML",
            requests_made=requests_made,
        ) from error


def _raise_if_blocked(
    document: etree._Element,
    *,
    context: str,
    requests_made: int,
) -> None:
    title = _clean_text(" ".join(document.xpath("//title//text()"))) or ""
    headings = " ".join(
        value
        for node in document.xpath("//h1|//h2")
        if (value := _clean_text(" ".join(node.itertext()))) is not None
    )
    prominent = f"{title} {headings}".casefold()
    blocked_phrases = (
        "access denied",
        "captcha",
        "security verification",
        "sign in",
        "log in",
        "just a moment",
    )
    structural_block = bool(
        document.xpath(
            '//*[contains(translate(@id, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
            '"abcdefghijklmnopqrstuvwxyz"), "captcha") '
            'or contains(translate(@class, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
            '"abcdefghijklmnopqrstuvwxyz"), "captcha") '
            'or contains(translate(@id, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
            '"abcdefghijklmnopqrstuvwxyz"), "authwall") '
            'or contains(translate(@class, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
            '"abcdefghijklmnopqrstuvwxyz"), "authwall")]'
            '|//form[contains(translate(@action, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
            '"abcdefghijklmnopqrstuvwxyz"), "login") '
            'or contains(translate(@action, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
            '"abcdefghijklmnopqrstuvwxyz"), "signin")]'
        )
    )
    has_public_job_content = bool(
        document.xpath('//script[@type="application/ld+json"]')
        or document.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), '
            '" jobs-list ")]'
        )
    )
    if any(phrase in prominent for phrase in blocked_phrases) or (
        structural_block and not has_public_job_content
    ):
        raise SourceError(
            f"JazzHR {context} returned an access restriction or challenge page",
            requests_made=requests_made,
        )


def _has_unhandled_pagination(document: etree._Element, *, listing_url: str) -> bool:
    for node in document.xpath("//a[@href]"):
        href = urljoin(listing_url, node.get("href"))
        parsed = urlsplit(href)
        label = (_clean_text(" ".join(node.itertext())) or "").casefold()
        rel = (node.get("rel") or "").casefold().split()
        query = parsed.query.casefold()
        path = parsed.path.casefold()
        if (
            "next" in rel
            or label in {"next", "next page", "older"}
            or re.search(r"(?:^|&)(?:page|offset|start)=", query)
            or re.search(r"/page/\d+/?$", path)
        ):
            return True
    return False


def _listing_jobs(
    document: etree._Element,
    *,
    source: JazzHRSourceLocation,
    requests_made: int,
) -> tuple[_ListingJob, ...]:
    if _has_unhandled_pagination(document, listing_url=source.listing_url):
        raise SourceError(
            "JazzHR listing exposes pagination that was not fully traversed",
            requests_made=requests_made,
        )

    jobs: list[_ListingJob] = []
    by_id: dict[str, _ListingJob] = {}
    for node in document.xpath("//a[@href]"):
        absolute_url = urljoin(source.listing_url, node.get("href"))
        try:
            source_job_id = jazzhr_job_id_from_url(
                absolute_url,
                expected_host=source.host,
            )
        except SourceError:
            continue
        title = _clean_text(" ".join(node.itertext()))
        if title is None:
            raise SourceError(
                "JazzHR listing job link is missing a title",
                requests_made=requests_made,
            )
        job = _ListingJob(
            source_job_id=source_job_id,
            url=_canonical_job_url(absolute_url, expected_host=source.host),
            title=title,
        )
        previous = by_id.get(source_job_id)
        if previous is not None:
            if previous.title != job.title:
                raise SourceError(
                    "JazzHR listing contains one stable job ID with conflicting jobs",
                    requests_made=requests_made,
                )
            continue
        by_id[source_job_id] = job
        jobs.append(job)

    has_listing_structure = bool(
        document.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), " jobs-list ")]'
            '|//ul[contains(concat(" ", normalize-space(@class), " "), " list-group ")]'
        )
    )
    if not jobs:
        visible = (_clean_text(" ".join(document.itertext())) or "").casefold()
        explicit_empty = any(
            phrase in visible
            for phrase in ("no jobs are currently available", "no open positions", "no jobs found")
        )
        if not has_listing_structure or not explicit_empty:
            raise SourceError(
                "JazzHR listing structure is malformed or contains no verifiable snapshot",
                requests_made=requests_made,
            )
    elif not has_listing_structure:
        raise SourceError(
            "JazzHR listing structure is malformed",
            requests_made=requests_made,
        )
    return tuple(jobs)


def _schema_types(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(cast(list[str], value))
    return ()


def _jobposting_objects(value: object) -> Iterable[Mapping[str, object]]:
    if isinstance(value, list):
        for item in value:
            yield from _jobposting_objects(item)
    elif isinstance(value, dict):
        typed = cast(dict[str, object], value)
        if "JobPosting" in _schema_types(typed.get("@type")):
            yield typed
        graph = typed.get("@graph")
        if graph is not None:
            yield from _jobposting_objects(graph)


def _jobposting_json(
    document: etree._Element,
    *,
    expected_host: str,
    expected_job_id: str,
    requests_made: int,
) -> Mapping[str, object] | None:
    postings: list[Mapping[str, object]] = []
    for raw in document.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            value = cast(object, json.loads(raw))
        except (json.JSONDecodeError, TypeError) as error:
            raise SourceError(
                "JazzHR detail contains malformed JSON-LD",
                requests_made=requests_made,
            ) from error
        postings.extend(_jobposting_objects(value))
    if not postings:
        return None
    if len(postings) == 1:
        return postings[0]

    matching: list[Mapping[str, object]] = []
    for posting in postings:
        candidate_url = posting.get("url")
        if not isinstance(candidate_url, str):
            continue
        try:
            candidate_id = jazzhr_job_id_from_url(
                candidate_url,
                expected_host=expected_host,
            )
        except SourceError:
            continue
        if candidate_id == expected_job_id:
            matching.append(posting)
    if len(matching) == 1:
        return matching[0]
    raise SourceError(
        "JazzHR detail contains ambiguous JobPosting JSON-LD objects",
        requests_made=requests_made,
    )


def _html_description(value: object, *, requests_made: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceError(
            "JazzHR detail is missing its full description",
            requests_made=requests_made,
        )
    try:
        fragment = lxml_html.fragment_fromstring(stdlib_html.unescape(value), create_parent=True)
    except (etree.ParserError, etree.XMLSyntaxError, TypeError, ValueError) as error:
        raise SourceError(
            "JazzHR detail description contains malformed HTML",
            requests_made=requests_made,
        ) from error
    for block in fragment.xpath(".//p|.//li|.//div|.//h1|.//h2|.//h3|.//br"):
        block.tail = "\n" + (block.tail or "")
    lines = [
        cleaned
        for line in fragment.text_content().splitlines()
        if (cleaned := _clean_text(line)) is not None
    ]
    description = "\n".join(lines)
    if not description:
        raise SourceError(
            "JazzHR detail is missing its full description",
            requests_made=requests_made,
        )
    return description


def _address_parts(
    value: object,
    *,
    requests_made: int,
) -> tuple[str | None, str | None, str | None]:
    if value is None:
        return None, None, None
    locations = value if isinstance(value, list) else [value]
    if any(not isinstance(item, dict) for item in locations):
        raise SourceError(
            "JazzHR detail jobLocation must contain structured places",
            requests_made=requests_made,
        )
    city: str | None = None
    country: str | None = None
    display_parts: list[str] = []
    seen: set[str] = set()
    for location in cast(list[dict[str, object]], locations):
        address = location.get("address")
        if address is None:
            continue
        if not isinstance(address, dict):
            raise SourceError(
                "JazzHR detail address must be an object",
                requests_made=requests_made,
            )
        for field in ("addressLocality", "addressRegion", "addressCountry"):
            raw = address.get(field)
            if raw is not None and not isinstance(raw, str):
                raise SourceError(
                    f"JazzHR detail {field} must be a string when present",
                    requests_made=requests_made,
                )
            part = _clean_text(raw)
            if part is None or _REF_PREFIX.match(part):
                continue
            key = part.casefold()
            if key not in seen:
                seen.add(key)
                display_parts.append(part)
            if field == "addressLocality" and city is None:
                city = part
            elif field == "addressCountry" and country is None:
                country = part
    return ", ".join(display_parts) or None, city, country


def _optional_schema_text(
    posting: Mapping[str, object],
    field: str,
    *,
    requests_made: int,
) -> str | None:
    value = posting.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceError(
            f"JazzHR detail {field} must be a string when present",
            requests_made=requests_made,
        )
    return _clean_text(value)


def _employment_type(value: object, *, requests_made: int) -> str | None:
    if value is None:
        return None
    values = value if isinstance(value, list) else [value]
    if any(not isinstance(item, str) for item in values):
        raise SourceError(
            "JazzHR detail employmentType must be a string or string array",
            requests_made=requests_made,
        )
    normalized = [
        cleaned.replace("_", " ").title()
        for item in cast(list[str], values)
        if (cleaned := _clean_text(item)) is not None
    ]
    return ", ".join(normalized) or None


def _single_html_element(
    document: etree._Element,
    xpath: str,
    *,
    field: str,
    requests_made: int,
) -> etree._Element:
    nodes = document.xpath(xpath)
    if len(nodes) != 1 or not isinstance(nodes[0], etree._Element):
        raise SourceError(
            f"JazzHR HTML fallback requires one unambiguous {field}",
            requests_made=requests_made,
        )
    return cast(etree._Element, nodes[0])


def _element_text(node: etree._Element) -> str | None:
    return _clean_text(" ".join(node.itertext()))


def _html_canonical_url(
    document: etree._Element,
    *,
    listing_job: _ListingJob,
    source: JazzHRSourceLocation,
    requests_made: int,
) -> str:
    hrefs = {
        value
        for node in document.xpath('//link[@rel="canonical"][@href]')
        if (value := _clean_text(node.get("href"))) is not None
    }
    if not hrefs:
        return listing_job.url
    if len(hrefs) != 1:
        raise SourceError(
            "JazzHR HTML fallback has ambiguous canonical URLs",
            requests_made=requests_made,
        )
    canonical = next(iter(hrefs))
    try:
        canonical_id = jazzhr_job_id_from_url(
            canonical,
            expected_host=source.host,
        )
    except SourceError as error:
        raise SourceError(
            "JazzHR HTML fallback canonical URL is invalid",
            requests_made=requests_made,
        ) from error
    if canonical_id != listing_job.source_job_id:
        raise SourceError(
            "JazzHR HTML fallback canonical URL has a conflicting stable job ID",
            requests_made=requests_made,
        )
    return _canonical_job_url(canonical, expected_host=source.host)


def _html_description_element(
    document: etree._Element,
    *,
    requests_made: int,
) -> etree._Element:
    description = _single_html_element(
        document,
        '//*[@id="job-description"]',
        field="job description",
        requests_made=requests_made,
    )
    if description.xpath("ancestor::form|.//form") or description.xpath(
        'ancestor::*[@id="job-application-form-container" '
        'or @id="resumator-application-form"]'
        '|.//*[@id="job-application-form-container" '
        'or @id="resumator-application-form"]'
    ):
        raise SourceError(
            "JazzHR HTML fallback job description overlaps the application form",
            requests_made=requests_made,
        )
    return description


def _html_description_text(
    description: etree._Element,
    *,
    requests_made: int,
) -> str:
    inner_html = (description.text or "") + "".join(
        lxml_html.tostring(child, encoding="unicode", method="html")
        for child in description
    )
    return _html_description(inner_html, requests_made=requests_made)


def _html_attribute(
    attributes: etree._Element,
    *,
    element_id: str,
    requests_made: int,
) -> str | None:
    nodes = attributes.xpath(f'.//*[@id="{element_id}"]')
    if len(nodes) > 1:
        raise SourceError(
            f"JazzHR HTML fallback has ambiguous {element_id} values",
            requests_made=requests_made,
        )
    if not nodes:
        return None
    return _element_text(cast(etree._Element, nodes[0]))


def _html_location(
    attributes: etree._Element,
    *,
    requests_made: int,
) -> str | None:
    nodes = attributes.xpath(
        './/*[@id="resumator-job-location" '
        'or contains(concat(" ", normalize-space(@class), " "), " location ")]'
    )
    if len(nodes) > 1:
        raise SourceError(
            "JazzHR HTML fallback has ambiguous location values",
            requests_made=requests_made,
        )
    if nodes:
        raw = _element_text(cast(etree._Element, nodes[0]))
    else:
        ref_nodes = [
            child
            for child in attributes.xpath("./*")
            if (text := _element_text(cast(etree._Element, child))) is not None
            if _REF_PREFIX.match(text)
        ]
        if len(ref_nodes) > 1:
            raise SourceError(
                "JazzHR HTML fallback has ambiguous location values",
                requests_made=requests_made,
            )
        raw = _element_text(cast(etree._Element, ref_nodes[0])) if ref_nodes else None
    if raw is None:
        return None
    without_ref = re.sub(r"^Ref\s*:\s*[^,]+,\s*", "", raw, flags=re.IGNORECASE)
    return _clean_text(without_ref)


def _html_detail_record(
    document: etree._Element,
    *,
    listing_job: _ListingJob,
    source: JazzHRSourceLocation,
    requests_made: int,
) -> SourceRecord:
    job_header = _single_html_element(
        document,
        '//*[contains(concat(" ", normalize-space(@class), " "), " job-header ")]',
        field="job header",
        requests_made=requests_made,
    )
    title_node = _single_html_element(
        job_header,
        ".//h2[not(ancestor::form)]",
        field="job title",
        requests_made=requests_made,
    )
    title = _element_text(title_node)
    if title is None:
        raise SourceError(
            "JazzHR HTML fallback job title is empty",
            requests_made=requests_made,
        )
    if title != listing_job.title:
        raise SourceError(
            "JazzHR listing and HTML fallback titles conflict",
            requests_made=requests_made,
        )
    attributes = _single_html_element(
        job_header,
        './/*[contains(concat(" ", normalize-space(@class), " "), '
        '" job-attributes-container ")]',
        field="job attributes",
        requests_made=requests_made,
    )
    description = _html_description_element(
        document,
        requests_made=requests_made,
    )
    return {
        "source": "jazzhr",
        "source_job_id": listing_job.source_job_id,
        "source_job_url": _html_canonical_url(
            document,
            listing_job=listing_job,
            source=source,
            requests_made=requests_made,
        ),
        "title": title,
        "location": _html_location(attributes, requests_made=requests_made),
        "country": None,
        "city": None,
        "workplace_type": None,
        "employment_type": _html_attribute(
            attributes,
            element_id="resumator-job-employment",
            requests_made=requests_made,
        ),
        "compensation_text": None,
        "published_at": None,
        "description": _html_description_text(
            description,
            requests_made=requests_made,
        ),
        "job_function": _html_attribute(
            attributes,
            element_id="resumator-job-department",
            requests_made=requests_made,
        ),
        "seniority_level": _html_attribute(
            attributes,
            element_id="resumator-job-experience",
            requests_made=requests_made,
        ),
        "industry": None,
    }


def _detail_record(
    document: etree._Element,
    *,
    listing_job: _ListingJob,
    source: JazzHRSourceLocation,
    requests_made: int,
) -> SourceRecord:
    posting = _jobposting_json(
        document,
        expected_host=source.host,
        expected_job_id=listing_job.source_job_id,
        requests_made=requests_made,
    )
    if posting is None:
        return _html_detail_record(
            document,
            listing_job=listing_job,
            source=source,
            requests_made=requests_made,
        )
    title = _optional_schema_text(posting, "title", requests_made=requests_made)
    if title is None:
        raise SourceError("JazzHR detail is missing its title", requests_made=requests_made)
    if title != listing_job.title:
        raise SourceError(
            "JazzHR listing and detail titles conflict",
            requests_made=requests_made,
        )
    schema_url = _optional_schema_text(posting, "url", requests_made=requests_made)
    if schema_url is None:
        raise SourceError(
            "JazzHR detail is missing its canonical job URL",
            requests_made=requests_made,
        )
    if jazzhr_job_id_from_url(schema_url, expected_host=source.host) != listing_job.source_job_id:
        raise SourceError(
            "JazzHR detail canonical URL has a conflicting stable job ID",
            requests_made=requests_made,
        )
    source_job_url = _canonical_job_url(schema_url, expected_host=source.host)
    location, city, country = _address_parts(
        posting.get("jobLocation"),
        requests_made=requests_made,
    )
    location_type = _optional_schema_text(
        posting,
        "jobLocationType",
        requests_made=requests_made,
    )
    workplace_type = (
        "remote"
        if location_type and location_type.casefold() == "telecommute"
        else None
    )
    return {
        "source": "jazzhr",
        "source_job_id": listing_job.source_job_id,
        "source_job_url": source_job_url,
        "title": title,
        "location": location,
        "country": country,
        "city": city,
        "workplace_type": workplace_type,
        "employment_type": _employment_type(
            posting.get("employmentType"),
            requests_made=requests_made,
        ),
        "compensation_text": None,
        "published_at": _optional_schema_text(
            posting,
            "datePosted",
            requests_made=requests_made,
        ),
        "description": _html_description(
            posting.get("description"),
            requests_made=requests_made,
        ),
        "job_function": _optional_schema_text(
            posting,
            "occupationalCategory",
            requests_made=requests_made,
        ),
        "seniority_level": None,
        "industry": _optional_schema_text(
            posting,
            "industry",
            requests_made=requests_made,
        ),
    }


class JazzHRSourceAdapter:
    """Collect one complete server-rendered JazzHR snapshot or fail closed."""

    def __init__(
        self,
        *,
        http_get: JazzHRHttpGet | None = None,
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
        http_get: JazzHRHttpGet,
    ) -> SourceBatch:
        source = jazzhr_source_from_url(company.source_jobs_url)
        requests_made = 0

        def get(url: str, *, context: str) -> str | bytes:
            nonlocal requests_made
            requests_made += 1
            try:
                return http_get(url, self._timeout_seconds)
            except Exception as error:
                raise SourceError(
                    f"JazzHR {context} request failed",
                    requests_made=requests_made,
                ) from error

        listing_body = get(source.listing_url, context="listing")
        listing_document = _parse_document(
            listing_body,
            context="listing",
            requests_made=requests_made,
        )
        _raise_if_blocked(
            listing_document,
            context="listing",
            requests_made=requests_made,
        )
        listing_jobs = _listing_jobs(
            listing_document,
            source=source,
            requests_made=requests_made,
        )

        records: list[SourceRecord] = []
        for listing_job in listing_jobs:
            detail_body = get(listing_job.url, context="detail")
            detail_document = _parse_document(
                detail_body,
                context="detail",
                requests_made=requests_made,
            )
            _raise_if_blocked(
                detail_document,
                context="detail",
                requests_made=requests_made,
            )
            records.append(
                _detail_record(
                    detail_document,
                    listing_job=listing_job,
                    source=source,
                    requests_made=requests_made,
                )
            )
        return SourceBatch(records=tuple(records), requests_made=requests_made)

    def fetch(self, *, company: SourceCompany) -> SourceBatch:
        if self._http_get is not None:
            return self._fetch_with_http_get(company=company, http_get=self._http_get)

        source = jazzhr_source_from_url(company.source_jobs_url)
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
                    raise RuntimeError(f"JazzHR returned HTTP {response.status}")
                final = urlsplit(str(response.url))
                if final.scheme.lower() != "https" or final.hostname != source.host:
                    raise RuntimeError("JazzHR redirected outside the configured tenant")
                return bytes(response.body)

            return self._fetch_with_http_get(company=company, http_get=session_get)
