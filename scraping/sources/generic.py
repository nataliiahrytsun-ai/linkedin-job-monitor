"""Low-level public job extraction contracts for generic source fallback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html  # type: ignore[import-untyped]
from lxml.html import HtmlElement  # type: ignore[import-untyped]

from discovery.network import UnsafeUrlError, canonicalize_url, validate_public_url
from scraping.sources.base import SourceBatch, SourceConfiguration, SourceError

_JOB_PATH_HINTS = (
    "job",
    "jobs",
    "career",
    "careers",
    "position",
    "positions",
    "vacancy",
    "vacancies",
    "opening",
    "openings",
    "role",
    "roles",
    "opportunity",
    "opportunities",
)
_JOB_TEXT_HINTS = (
    "job",
    "jobs",
    "career",
    "careers",
    "position",
    "positions",
    "vacancy",
    "vacancies",
    "apply now",
    "hiring",
    "opening",
    "open role",
    "role",
    "roles",
    "opportunity",
    "opportunities",
    "join us",
)
_BLOCKED_PATH_HINTS = (
    "privacy",
    "terms",
    "cookie",
    "cookies",
    "login",
    "signin",
    "sign-in",
    "signup",
    "sign-up",
    "account",
    "social",
    "linkedin",
    "facebook",
    "twitter",
    "instagram",
    "youtube",
    "email",
    "mailto",
    "about",
    "company",
    "contact",
    "blog",
    "benefits",
    "culture",
    "team",
    "teams",
    "talent",
    "community",
    "newsletter",
    "work-with-us",
    "after-applying",
    "candidate-privacy",
    "applicant-privacy",
    "how-we-work",
)
_BLOCKED_TEXT_HINTS = (
    "privacy policy",
    "terms of service",
    "cookie policy",
    "login",
    "sign in",
    "sign up",
    "linkedin",
    "facebook",
    "twitter",
    "instagram",
    "about us",
    "contact us",
    "work with us",
    "what to do after applying",
    "after applying",
)
_MAX_NEARBY_TEXT = 240
_MAX_DETERMINISTIC_TITLE = 200
_PAGINATION_QUERY_KEYS = frozenset({"page", "offset", "start", "startrow", "cursor"})
_LOCALE_QUERY_KEYS = frozenset({"lang", "language", "locale"})
_JOB_ID_QUERY_KEYS = frozenset({"id", "job", "jobid", "job_id", "positionid", "vacancyid"})
_UNSUITABLE_TITLE_LABELS = frozenset(
    {
        "apply",
        "apply now",
        "career",
        "careers",
        "details",
        "job",
        "job details",
        "jobs",
        "learn more",
        "open position",
        "open positions",
        "open role",
        "open roles",
        "read more",
        "view job",
        "view jobs",
        "view all jobs",
        "all jobs",
        "search jobs",
        "browse jobs",
        "find jobs",
        "explore jobs",
        "see jobs",
        "sitemap",
        "visit career page",
        "visit careers page",
        "view position",
        "view role",
    }
)
_NAVIGATION_CLASS_TOKENS = frozenset(
    {
        "breadcrumb",
        "footer",
        "header",
        "language",
        "locale",
        "menu",
        "nav",
        "navigation",
        "pager",
        "pagination",
        "sitemap",
    }
)
_COLLECTION_NAVIGATION_TOKENS = frozenset(
    {
        "categories",
        "category",
        "facet",
        "facets",
        "filter",
        "filters",
        "locations",
    }
)
_LISTING_ROUTE_TOKENS = frozenset(
    {
        "alljobs",
        "browse",
        "filter",
        "filters",
        "search",
        "viewalljobs",
    }
)
_CAREER_CATEGORY_LABELS = frozenset(
    {
        "business",
        "design",
        "engineering",
        "early careers",
        "graduates",
        "internships",
        "internships and early careers",
        "marketing",
        "operations",
        "product",
        "sales",
        "students",
        "technology",
    }
)


@dataclass(frozen=True, slots=True)
class GenericCandidate:
    """A deterministic public-career candidate extracted from DOM HTML."""

    candidate_id: str
    url: str
    anchor_text: str | None
    nearby_text: str | None


@dataclass(frozen=True, slots=True)
class ExtractedJob:
    """An LLM-classified position mapped back to a deterministic candidate."""

    candidate_id: str
    title: str


@dataclass(frozen=True, slots=True)
class JobExtractionResult:
    """Structured output from a provider for a specific candidate set."""

    jobs: tuple[ExtractedJob, ...]


class JobExtractionProvider(Protocol):
    def extract_jobs(self, *, candidates: Sequence[GenericCandidate]) -> JobExtractionResult: ...


class GenericExtractionError(ValueError):
    """A fail-closed generic extraction error."""


class CandidateValidationError(GenericExtractionError):
    """Provider output failed deterministic validation."""


class GenericCandidateExtractorError(GenericExtractionError):
    """Candidate extraction failed before provider invocation."""


def candidate_id_for_url(url: str) -> str:
    """Create a deterministic stable ID bound to a canonical URL."""
    canonical = canonicalize_url(url)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest[:32]


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    compact = " ".join(value.split())
    return compact or None


def _contains_any(text: str | None, hints: Sequence[str]) -> bool:
    if text is None:
        return False
    lowered = text.casefold()
    return any(hint in lowered for hint in hints)


def _is_blocked_path(url: str) -> bool:
    path = urlsplit(url).path.casefold()
    return _contains_any(path, _BLOCKED_PATH_HINTS)


def _is_navigation_anchor(anchor: HtmlElement) -> bool:
    """Reject links owned by page navigation rather than vacancy listings."""
    parent = anchor.getparent()
    while parent is not None:
        tag = getattr(parent, "tag", "").casefold()
        if tag in {"aside", "footer", "header", "nav"}:
            return True
        # Theme-wide classes on the document root (for example a footer breakpoint
        # on ``body``) do not make every content link navigation.
        if tag in {"body", "html"}:
            parent = parent.getparent()
            continue
        attributes = " ".join(
            str(parent.get(name) or "") for name in ("class", "id", "role")
        ).casefold()
        attribute_tokens = set(re.findall(r"[a-z0-9]+", attributes))
        if attribute_tokens & _NAVIGATION_CLASS_TOKENS:
            return True
        parent = parent.getparent()
    return False


def _normalized_label(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _is_navigation_label(value: str | None) -> bool:
    label = _normalized_label(value)
    if not label:
        return False
    if label in _UNSUITABLE_TITLE_LABELS:
        return True
    if label.startswith(("visit career", "visit careers", "view all job", "all job")):
        return True
    return label in {"language", "sitemap", "next", "next page", "previous", "previous page"}


def _has_structural_job_evidence(anchor: HtmlElement) -> bool:
    node: HtmlElement | None = anchor
    while node is not None:
        attributes = " ".join(str(node.get(name) or "") for name in ("class", "id", "role"))
        compact = re.sub(r"[^a-z0-9]+", "", attributes.casefold())
        has_job = any(token in compact for token in ("job", "position", "vacancy", "opening"))
        has_record = any(token in compact for token in ("card", "item", "result", "row", "title"))
        if has_job and has_record:
            return True
        node = node.getparent()
    return False


def _is_collection_navigation_anchor(anchor: HtmlElement) -> bool:
    node: HtmlElement | None = anchor
    while node is not None:
        tag = getattr(node, "tag", "").casefold()
        # Theme-wide classes on the document root must not turn all vacancy
        # links into collection-navigation links.
        if tag in {"body", "html"}:
            node = node.getparent()
            continue
        attributes = " ".join(str(node.get(name) or "") for name in ("class", "id", "role"))
        tokens = set(re.findall(r"[a-z0-9]+", attributes.casefold()))
        compact = re.sub(r"[^a-z0-9]+", "", attributes.casefold())
        collection_tokens = tokens & _COLLECTION_NAVIGATION_TOKENS
        # ``role=list`` is also commonly used for ordinary vacancy result
        # collections, so a lone list marker is not navigation by itself.
        if collection_tokens - {"category", "list"}:
            return True
        # A taxonomy marker on an individual result (for example
        # ``category-career``) describes that result; it is not a category
        # navigation container. Require stronger navigation structure when
        # category/list markers are present.
        if "category" in collection_tokens and tokens & {"list", "menu", "nav", "navigation"}:
            return True
        if "list" in collection_tokens and tokens & {"menu", "nav", "navigation"}:
            return True
        if any(
            token in compact
            for token in ("categorylist", "facetlist", "filterlist", "locationlist")
        ):
            return True
        node = node.getparent()
    return False


def _looks_like_job_link(
    url: str,
    anchor_text: str | None,
    nearby_text: str | None,
    *,
    anchor: HtmlElement,
    base_url: str,
    repeated_route: bool,
) -> bool:
    if _is_blocked_path(url):
        return False

    parsed = urlsplit(url)
    path = parsed.path.casefold()
    query_keys = {key.casefold() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    if query_keys & (_LOCALE_QUERY_KEYS | _PAGINATION_QUERY_KEYS):
        return False
    normalized_anchor = _normalized_label(anchor_text)
    if _is_navigation_label(anchor_text) and normalized_anchor not in {"apply", "apply now"}:
        return False
    if _is_collection_navigation_anchor(anchor):
        return False

    base = urlsplit(base_url)
    same_listing_path = (
        parsed.hostname == base.hostname
        and path.rstrip("/") == base.path.casefold().rstrip("/")
    )
    if same_listing_path and not query_keys & _JOB_ID_QUERY_KEYS:
        return False

    if path in {"/apply", "/job/apply", "/jobs/apply", "/career/apply", "/careers/apply"}:
        return False
    if path.endswith("/apply") or "/apply/" in path or path == "/apply":
        return False

    segments = [segment for segment in path.split("/") if segment]
    job_root_segments = {
        "jobs",
        "job",
        "careers",
        "career",
        "positions",
        "position",
        "vacancies",
        "vacancy",
        "openings",
        "opening",
        "roles",
        "role",
        "opportunities",
        "opportunity",
    }
    if segments and all(segment in job_root_segments for segment in segments):
        return False

    if path.rstrip("/") in {"/career", "/careers"}:
        return False

    if segments and segments[-1] in _LISTING_ROUTE_TOKENS:
        return False

    structural_evidence = _has_structural_job_evidence(anchor)
    numeric_evidence = any(segment.isdigit() for segment in segments) or bool(
        query_keys & _JOB_ID_QUERY_KEYS
    )
    detail_route = any(
        segment in set(_JOB_PATH_HINTS)
        for segment in segments[:-1]
    ) and bool(segments and segments[-1] not in job_root_segments | _LISTING_ROUTE_TOKENS)
    meaningful_title = _is_safe_deterministic_title(anchor_text)
    meaningful_slug = _title_from_url_slug(url) is not None

    if detail_route and (
        meaningful_title or meaningful_slug or structural_evidence or numeric_evidence
    ):
        return True
    if meaningful_title and (structural_evidence or repeated_route):
        return True

    combined = " ".join(part for part in (anchor_text, nearby_text) if part)
    if not combined:
        return False

    if _contains_any(combined, _BLOCKED_TEXT_HINTS):
        return False

    return (
        _contains_any(combined, _JOB_TEXT_HINTS)
        and meaningful_title
        and (structural_evidence or numeric_evidence)
    )


def _context_text(anchor: HtmlElement) -> str | None:
    parent = anchor.getparent()
    if parent is None:
        return None
    candidate = " ".join(
        part.strip() for part in parent.itertext() if part and part.strip()
    )
    return _clean_text(candidate[:_MAX_NEARBY_TEXT])


def _anchor_title(anchor: HtmlElement) -> str | None:
    title_nodes = anchor.xpath(
        './/*[contains(translate(concat(@class, " ", @id), '
        '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "title")]'
    )
    values = tuple(
        value
        for node in title_nodes
        if (value := _clean_text(" ".join(node.itertext()))) is not None
    )
    if len(values) == 1:
        return values[0]
    return _clean_text(
        " ".join(part.strip() for part in anchor.itertext() if part and part.strip())
    )


def _build_candidate_from_anchor(
    anchor: HtmlElement,
    *,
    base_url: str,
    repeated_route: bool,
) -> GenericCandidate | None:
    if _is_navigation_anchor(anchor):
        return None
    href = getattr(anchor, "get", lambda *_args, **_kwargs: None)("href")
    if not href or not isinstance(href, str):
        return None
    candidate_url = href.strip()
    if not candidate_url or candidate_url.startswith("#"):
        return None
    if candidate_url.startswith(("mailto:", "tel:", "javascript:")):
        return None

    try:
        resolved = canonicalize_url(urljoin(base_url, candidate_url))
    except (TypeError, ValueError, UnsafeUrlError):
        return None

    anchor_text = _anchor_title(anchor)
    if anchor_text is not None and anchor_text.casefold() in _CAREER_CATEGORY_LABELS:
        return None
    nearby_text = _context_text(anchor)
    if not _looks_like_job_link(
        resolved,
        anchor_text,
        nearby_text,
        anchor=anchor,
        base_url=base_url,
        repeated_route=repeated_route,
    ):
        return None

    candidate_id = candidate_id_for_url(resolved)
    return GenericCandidate(
        candidate_id=candidate_id,
        url=resolved,
        anchor_text=anchor_text,
        nearby_text=nearby_text,
    )


def extract_generic_candidates(
    html: str,
    *,
    base_url: str = "https://example.com",
) -> tuple[GenericCandidate, ...]:
    """Extract deterministic public job candidates from a careers page."""
    if not isinstance(html, str):
        raise GenericCandidateExtractorError("HTML must be provided as a string")

    document = lxml_html.fromstring(html)
    candidates_by_url: dict[str, GenericCandidate] = {}
    anchors = tuple(document.xpath(".//a[@href]"))
    route_counts: dict[tuple[str, str], int] = {}
    route_keys: dict[int, tuple[str, str]] = {}
    for anchor in anchors:
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        try:
            parsed = urlsplit(canonicalize_url(urljoin(base_url, href)))
        except (TypeError, ValueError, UnsafeUrlError):
            continue
        segments = tuple(segment for segment in parsed.path.split("/") if segment)
        if len(segments) < 2:
            continue
        key = ((parsed.hostname or "").casefold(), "/".join(segments[:-1]).casefold())
        route_keys[id(anchor)] = key
        route_counts[key] = route_counts.get(key, 0) + 1

    for anchor in anchors:
        route_key = route_keys.get(id(anchor))
        candidate = _build_candidate_from_anchor(
            anchor,
            base_url=base_url,
            repeated_route=route_key is not None and route_counts.get(route_key, 0) >= 2,
        )
        if candidate is None:
            continue
        candidates_by_url.setdefault(candidate.url, candidate)

    return tuple(sorted(candidates_by_url.values(), key=lambda item: item.url))


def validate_extracted_jobs(
    candidates: Sequence[GenericCandidate],
    jobs: Sequence[ExtractedJob],
) -> tuple[ExtractedJob, ...]:
    """Reject provider output that invents candidates or empty titles."""
    if len(set(candidate.candidate_id for candidate in candidates)) != len(candidates):
        raise CandidateValidationError("candidate IDs must be unique")

    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    seen_ids: set[str] = set()
    validated: list[ExtractedJob] = []

    for job in jobs:
        if job.candidate_id not in candidates_by_id:
            raise CandidateValidationError(
                f"unknown candidate_id in provider output: {job.candidate_id}"
            )
        if job.candidate_id in seen_ids:
            raise CandidateValidationError(
                f"duplicate candidate_id in provider output: {job.candidate_id}"
            )

        title = job.title.strip()
        if not title:
            raise CandidateValidationError(f"empty title for candidate_id {job.candidate_id}")

        validated.append(ExtractedJob(candidate_id=job.candidate_id, title=title))
        seen_ids.add(job.candidate_id)

    return tuple(validated)


def _is_safe_deterministic_title(value: str | None) -> bool:
    title = _clean_text(value)
    if title is None or len(title) > _MAX_DETERMINISTIC_TITLE:
        return False
    lowered = title.casefold()
    if lowered in _UNSUITABLE_TITLE_LABELS or _contains_any(lowered, _BLOCKED_TEXT_HINTS):
        return False
    if "://" in title or title.startswith(("/", "\\")):
        return False
    return any(character.isalpha() for character in title)


def _title_from_url_slug(url: str) -> str | None:
    path_segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if not path_segments:
        return None

    slug = unquote(path_segments[-1])
    if not slug or "." in slug:
        return None
    tokens = re.split(r"[-_]+", slug)
    if not tokens or any(not token.isalnum() for token in tokens):
        return None

    title = " ".join(tokens).title()
    return title if _is_safe_deterministic_title(title) else None


def _extract_deterministic_jobs(
    candidates: Sequence[GenericCandidate],
) -> tuple[ExtractedJob, ...]:
    jobs: list[ExtractedJob] = []
    for candidate in candidates:
        anchor_title = _clean_text(candidate.anchor_text)
        title = (
            anchor_title
            if _is_safe_deterministic_title(anchor_title)
            else _title_from_url_slug(candidate.url)
        )
        if title is None:
            continue
        jobs.append(ExtractedJob(candidate_id=candidate.candidate_id, title=title))
    return validate_extracted_jobs(candidates, jobs)


class FakeJobExtractionProvider:
    """Test stub that returns a fixed mapping for candidate IDs."""

    def __init__(
        self,
        *,
        mapping: dict[str, str] | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self.mapping = mapping or {}
        self.raise_error = raise_error

    def extract_jobs(self, *, candidates: Sequence[GenericCandidate]) -> JobExtractionResult:
        if self.raise_error is not None:
            raise self.raise_error

        ordered_jobs = [
            ExtractedJob(
                candidate_id=candidate.candidate_id,
                title=self.mapping[candidate.candidate_id],
            )
            for candidate in candidates
            if candidate.candidate_id in self.mapping
        ]
        return JobExtractionResult(jobs=tuple(ordered_jobs))


class ProviderConfigurationError(GenericExtractionError):
    """The provider cannot operate because configuration is missing or invalid."""


class ProviderResponseError(GenericExtractionError):
    """The provider responded with malformed or untrustworthy structured data."""


def _response_html(response: Any) -> str:
    body = getattr(response, "body", b"")
    if isinstance(body, bytes | bytearray):
        return body.decode("utf-8", errors="replace")
    if isinstance(body, memoryview):
        return bytes(body).decode("utf-8", errors="replace")
    return str(body)


def _generic_detail_metadata(html: str) -> dict[str, str | None]:
    """Read unambiguous public job metadata from semantic HTML and JobPosting JSON-LD."""
    empty: dict[str, str | None] = {
        "title": None, "location": None, "city": None, "country": None, "workplace_type": None,
        "employment_type": None, "compensation_text": None, "seniority_level": None,
        "published_at": None, "description": None, "job_function": None, "industry": None,
    }
    document = lxml_html.fromstring(html)

    # Semantic HTML fallback. JSON-LD below remains authoritative when present.
    headline_nodes = document.xpath(
        '//*[@itemprop="headline"][self::h1 or self::h2] | //h1'
    )
    headline_values = tuple(
        value
        for node in headline_nodes
        if (value := _clean_text(" ".join(node.itertext()))) is not None
    )
    if headline_values:
        empty["title"] = headline_values[0]

    description_nodes = document.xpath(
        '//*[@itemprop="articleBody"] | //*[@itemprop="description"]'
    )
    if len(description_nodes) == 1:
        empty["description"] = _clean_text(
            "\n".join(
                part.strip()
                for part in description_nodes[0].itertext()
                if part and part.strip()
            )
        )

    employment_nodes = document.xpath(
        '//*[contains(translate(concat(@class, " ", @id), '
        '"ABCDEFGHIJKLMNOPQRSTUVWXYZ_", "abcdefghijklmnopqrstuvwxyz-"), '
        '"employment-type")]'
    )
    if len(employment_nodes) == 1:
        empty["employment_type"] = _clean_text(
            " ".join(employment_nodes[0].itertext())
        )

    experience_nodes = document.xpath(
        '//*[contains(translate(concat(@class, " ", @id), '
        '"ABCDEFGHIJKLMNOPQRSTUVWXYZ_", "abcdefghijklmnopqrstuvwxyz-"), '
        '"experience-level") '
        'or contains(translate(concat(@class, " ", @id), '
        '"ABCDEFGHIJKLMNOPQRSTUVWXYZ_", "abcdefghijklmnopqrstuvwxyz-"), '
        '"seniority")]'
    )
    if len(experience_nodes) == 1:
        empty["seniority_level"] = _clean_text(
            " ".join(experience_nodes[0].itertext())
        )

    salary_nodes = document.xpath(
        '//*[contains(translate(concat(@class, " ", @id), '
        '"ABCDEFGHIJKLMNOPQRSTUVWXYZ_", "abcdefghijklmnopqrstuvwxyz-"), '
        '"salary") '
        'or contains(translate(concat(@class, " ", @id), '
        '"ABCDEFGHIJKLMNOPQRSTUVWXYZ_", "abcdefghijklmnopqrstuvwxyz-"), '
        '"compensation")]'
    )
    if len(salary_nodes) == 1:
        empty["compensation_text"] = _clean_text(
            " ".join(salary_nodes[0].itertext())
        )

    location_nodes = document.xpath(
        '//*[contains(translate(concat(@class, " ", @id), '
        '"ABCDEFGHIJKLMNOPQRSTUVWXYZ_", "abcdefghijklmnopqrstuvwxyz-"), '
        '"locations")]'
    )
    if len(location_nodes) == 1:
        location = _clean_text(" ".join(location_nodes[0].itertext()))
        if location:
            location = re.sub(r"\s*\|\s*", " | ", location)
            empty["location"] = location

    text_content = _clean_text(" ".join(document.itertext())) or ""

    if empty["location"] is None:
        match = re.search(
            r"\bLocation:\s*([^\n\r]+?)(?=\s+(?:About|Publication Date:|Ref\. No:)|$)",
            text_content,
            flags=re.IGNORECASE,
        )
        if match:
            location = _clean_text(match.group(1))
            if location:
                # Some career pages inline stylesheet text immediately after
                # the visible location. Keep only the human-readable value.
                location = re.split(
                    r"\s+(?:#[A-Za-z_][A-Za-z0-9_.-]*|\.[A-Za-z_][A-Za-z0-9_.-]*\s*\{)",
                    location,
                    maxsplit=1,
                )[0].strip()
                empty["location"] = location or None

    if empty["published_at"] is None:
        match = re.search(
            r"\bPublication Date:\s*([A-Za-z]{3}\s+\d{1,2},\s+\d{4})",
            text_content,
            flags=re.IGNORECASE,
        )
        if match:
            from datetime import UTC, datetime

            published_text = _clean_text(match.group(1))
            if published_text:
                try:
                    empty["published_at"] = datetime.strptime(
                        published_text, "%b %d, %Y"
                    ).replace(tzinfo=UTC)
                except ValueError:
                    pass

    postings: list[dict[str, Any]] = []
    for node in document.xpath('//script[@type="application/ld+json"]'):
        try:
            value = json.loads(node.text or "")
        except json.JSONDecodeError:
            continue
        values = value.get("@graph", value) if isinstance(value, dict) else value
        if isinstance(values, dict):
            values = [values]
        if isinstance(values, list):
            postings.extend(
                x for x in values if isinstance(x, dict) and x.get("@type") == "JobPosting"
            )
    if len(postings) != 1:
        return empty
    posting = postings[0]
    def text(key: str) -> str | None:
        value = posting.get(key)
        return _clean_text(value) if isinstance(value, str) else None
    empty["title"] = text("title") or empty["title"]
    location = posting.get("jobLocation")
    address = location.get("address") if isinstance(location, dict) else None
    if isinstance(address, dict):
        locality = address.get("addressLocality")
        country = address.get("addressCountry")
        text_value = _clean_text(locality) if isinstance(locality, str) else None
        empty["city"] = text_value
        empty["country"] = _clean_text(country) if isinstance(country, str) else None
        empty["location"] = (
            ", ".join(x for x in (text_value, empty["country"]) if x)
            or empty["location"]
        )
    location_type = text("jobLocationType")
    if location_type and location_type.casefold() == "telecommute":
        empty["workplace_type"] = "remote"
    empty["employment_type"] = text("employmentType") or empty["employment_type"]
    empty["published_at"] = text("datePosted") or empty["published_at"]
    empty["description"] = text("description") or empty["description"]
    empty["job_function"] = text("occupationalCategory") or empty["job_function"]
    empty["industry"] = text("industry") or empty["industry"]
    salary = posting.get("baseSalary")
    if isinstance(salary, dict):
        amount = salary.get("value")
        currency = salary.get("currency")
        if isinstance(amount, str | int | float):
            suffix = f" {currency}" if isinstance(currency, str) else ""
            empty["compensation_text"] = f"{amount}{suffix}"
    return empty


def _public_job_search_form_target(document: HtmlElement, *, page_url: str) -> str | None:
    page = urlsplit(page_url)
    for form in document.xpath(".//form[@action]"):
        if (form.get("method") or "get").casefold() != "get":
            continue
        inputs = tuple(form.xpath(".//input[@name]"))
        field_names = {
            str(field.get("name") or "").strip().casefold()
            for field in inputs
        }
        query_fields = field_names & {"keyword", "keywords", "q", "query"}
        if not query_fields:
            continue
        semantics = " ".join(
            str(form.get(name) or "") for name in ("class", "id", "name", "role")
        ).casefold()
        if "search" not in semantics and "job" not in semantics:
            continue
        try:
            action_url = canonicalize_url(urljoin(page_url, str(form.get("action"))))
        except (TypeError, ValueError, UnsafeUrlError):
            continue
        action = urlsplit(action_url)
        if (action.scheme, action.hostname, action.port) != (
            page.scheme,
            page.hostname,
            page.port,
        ):
            continue
        parameters = list(parse_qsl(action.query, keep_blank_values=True))
        existing_names = {name.casefold() for name, _value in parameters}
        allowed_empty_fields = query_fields | (
            field_names & {"location", "locations", "locationsearch"}
        )
        parameters.extend(
            (name, "") for name in sorted(allowed_empty_fields - existing_names)
        )
        return canonicalize_url(
            urlunsplit(
                (action.scheme, action.netloc, action.path, urlencode(parameters), "")
            )
        )
    return None


def _pagination_context(node: HtmlElement) -> bool:
    current: HtmlElement | None = node
    while current is not None:
        attributes = " ".join(
            str(current.get(name) or "") for name in ("class", "id", "role")
        ).casefold()
        if any(token in attributes for token in ("pager", "pagination", "paginator")):
            return True
        current = current.getparent()
    return False


def _pagination_url_pattern(url: str) -> bool:
    parsed = urlsplit(url)
    query_keys = {key.casefold() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    if query_keys & _PAGINATION_QUERY_KEYS:
        return True
    path = parsed.path.casefold()
    return bool(
        re.search(r"/page/\d+/?$", path)
        or re.search(r"/\d+/?$", path)
    )


def _strong_pagination_url_pattern(url: str) -> bool:
    parsed = urlsplit(url)
    query_keys = {key.casefold() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    return bool(
        query_keys & _PAGINATION_QUERY_KEYS
        or re.search(r"/page/\d+/?$", parsed.path.casefold())
    )


def _pagination_targets(
    document: HtmlElement,
    *,
    listing_url: str,
) -> tuple[tuple[str, ...], bool]:
    targets: list[str] = []
    detected = False
    for node in document.xpath(".//*[@href or @rel or @aria-label]"):
        label = _normalized_label(" ".join(node.itertext()))
        rel = (node.get("rel") or "").casefold().split()
        aria_label = _normalized_label(node.get("aria-label"))
        href = node.get("href")
        explicit_next = (
            "next" in rel
            or label in {"next", "next page", "older"}
            or aria_label in {"next", "next page"}
        )
        pagination_context = _pagination_context(node)
        if explicit_next or pagination_context:
            detected = True
        if not isinstance(href, str) or not href.strip():
            continue
        try:
            resolved = canonicalize_url(urljoin(listing_url, href))
        except (TypeError, ValueError, UnsafeUrlError):
            continue
        query_keys = {
            key.casefold()
            for key, _value in parse_qsl(urlsplit(resolved).query, keep_blank_values=True)
        }
        if query_keys & _LOCALE_QUERY_KEYS and not pagination_context:
            continue
        supported_pattern = _strong_pagination_url_pattern(resolved) or (
            pagination_context and _pagination_url_pattern(resolved)
        )
        if supported_pattern:
            detected = True
        if explicit_next or (pagination_context and supported_pattern) or supported_pattern:
            targets.append(resolved)
    return tuple(dict.fromkeys(targets)), detected


def _same_listing_family(*, initial_url: str, current_url: str, target_url: str) -> bool:
    initial = urlsplit(initial_url)
    current = urlsplit(current_url)
    target = urlsplit(target_url)
    if (target.scheme, target.hostname, target.port) != (
        initial.scheme,
        initial.hostname,
        initial.port,
    ):
        return False
    target_path = target.path.rstrip("/") or "/"
    initial_path = initial.path.rstrip("/") or "/"
    current_path = current.path.rstrip("/") or "/"
    if target_path in {initial_path, current_path}:
        return True
    if target_path.startswith(f"{initial_path}/") or target_path.startswith(f"{current_path}/"):
        return True
    return bool(re.search(r"/page/\d+/?$", target.path.casefold()))


class GenericSourceAdapter:
    """Execute a public generic-careers page through the source-neutral adapter contract."""

    def __init__(
        self,
        *,
        provider: JobExtractionProvider | None = None,
        session_factory: Any | None = None,
        timeout_seconds: float = 20.0,
        max_pages: int = 20,
        max_requests: int = 20,
        max_detail_requests: int = 50,
        total_timeout_seconds: float = 45.0,
    ) -> None:
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("max_requests must be a positive integer")
        if type(max_detail_requests) is not int or max_detail_requests < 0:
            raise ValueError("max_detail_requests must be a non-negative integer")
        if total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be positive")
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.max_pages = max_pages
        self.max_requests = max_requests
        self.max_detail_requests = max_detail_requests
        self.total_timeout_seconds = total_timeout_seconds
        self._session_factory = session_factory
        self._detail_skip_urls: frozenset[str] = frozenset()

    def set_detail_skip_urls(self, urls: set[str]) -> None:
        """Skip detail pages already sufficiently enriched by earlier runs."""
        self._detail_skip_urls = frozenset(
            url for url in urls if isinstance(url, str) and url.strip()
        )

    def _open_session(self: GenericSourceAdapter) -> Any:
        if self._session_factory is not None:
            return self._session_factory(timeout_seconds=self.timeout_seconds)
        try:
            from scrapling.fetchers import FetcherSession
        except ModuleNotFoundError as exc:  # pragma: no cover - environment dependency
            raise SourceError(
                "Scrapling is required to fetch a generic public careers page"
            ) from exc
        return FetcherSession(
            timeout=self.timeout_seconds,
            retries=1,
            retry_delay=0,
            follow_redirects="safe",
            stealthy_headers=False,
            impersonate=None,
            headers={"User-Agent": "linkedin-job-monitor/generic-fallback"},
        )

    def fetch(self, *, company: SourceConfiguration) -> SourceBatch:
        source_url = (company.source_jobs_url or "").strip()
        if not source_url:
            raise SourceError("Generic company jobs URL is missing")

        requests_made = 0
        try:
            canonical_source_url = canonicalize_url(source_url)
            validate_public_url(canonical_source_url)
            started = time.monotonic()
            current_url = canonical_source_url
            trusted_listing_url = canonical_source_url
            requested_urls: set[str] = set()
            content_hashes: set[str] = set()
            candidates_by_url: dict[str, GenericCandidate] = {}
            with self._open_session() as session:
                for page_index in range(self.max_pages):
                    if time.monotonic() - started >= self.total_timeout_seconds:
                        raise SourceError(
                            "Generic fallback pagination exceeded its total time limit",
                            requests_made=requests_made,
                        )
                    if requests_made >= self.max_requests:
                        raise SourceError(
                            "Generic fallback pagination exceeded its request limit",
                            requests_made=requests_made,
                        )
                    validate_public_url(current_url)
                    requested_urls.add(current_url)
                    response = session.get(current_url)
                    requests_made += 1
                    if getattr(response, "status", 200) >= 400:
                        raise SourceError(
                            "Generic fallback fetch failed with HTTP "
                            f"{getattr(response, 'status', 200)}",
                            requests_made=requests_made,
                        )
                    final_url = canonicalize_url(str(getattr(response, "url", current_url)))
                    validate_public_url(final_url)
                    if not _same_listing_family(
                        initial_url=trusted_listing_url,
                        current_url=current_url,
                        target_url=final_url,
                    ):
                        raise SourceError(
                            "Generic fallback pagination left the trusted listing family",
                            requests_made=requests_made,
                        )
                    html = _response_html(response)
                    page_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
                    if page_hash in content_hashes:
                        raise SourceError(
                            "Generic fallback pagination repeated page content",
                            requests_made=requests_made,
                        )
                    content_hashes.add(page_hash)
                    document = lxml_html.fromstring(html)
                    page_candidates = extract_generic_candidates(html, base_url=final_url)
                    new_candidates = 0
                    for candidate in page_candidates:
                        if candidate.url not in candidates_by_url:
                            candidates_by_url[candidate.url] = candidate
                            new_candidates += 1
                    if not page_candidates and not candidates_by_url:
                        search_target = _public_job_search_form_target(
                            document,
                            page_url=final_url,
                        )
                        if search_target is not None and search_target not in requested_urls:
                            validate_public_url(search_target)
                            trusted_listing_url = search_target
                            current_url = search_target
                            continue
                    if page_index > 0 and (not page_candidates or new_candidates == 0):
                        raise SourceError(
                            "Generic fallback pagination continuation produced no new jobs",
                            requests_made=requests_made,
                        )
                    pagination_targets, pagination_detected = _pagination_targets(
                        document,
                        listing_url=final_url,
                    )
                    if not pagination_detected:
                        break
                    next_url: str | None = None
                    for target in pagination_targets:
                        if not _same_listing_family(
                            initial_url=trusted_listing_url,
                            current_url=final_url,
                            target_url=target,
                        ):
                            raise SourceError(
                                "Generic fallback rejected external or unrelated pagination URL",
                                requests_made=requests_made,
                            )
                        if target not in requested_urls and target != current_url:
                            next_url = target
                            break
                    if next_url is None:
                        if pagination_targets:
                            break
                        raise SourceError(
                            "Generic fallback detected unsupported pagination",
                            requests_made=requests_made,
                        )
                    if page_index + 1 >= self.max_pages:
                        raise SourceError(
                            "Generic fallback pagination exceeded its page limit",
                            requests_made=requests_made,
                        )
                    current_url = next_url
                else:  # pragma: no cover - guarded by the explicit page-limit branch
                    raise SourceError(
                        "Generic fallback pagination exceeded its page limit",
                        requests_made=requests_made,
                    )
            candidates = tuple(candidates_by_url.values())
            if not candidates:
                raise SourceError(
                    "Generic fallback found no public job-like candidates",
                    requests_made=requests_made,
                )

            if self.provider is None:
                validated_jobs = _extract_deterministic_jobs(candidates)
            else:
                provider_result = self.provider.extract_jobs(candidates=candidates)
                validated_jobs = validate_extracted_jobs(candidates, provider_result.jobs)
            if not validated_jobs:
                raise SourceError(
                    "Generic fallback produced no validated jobs",
                    requests_made=requests_made,
                )

            candidate_map = {candidate.candidate_id: candidate for candidate in candidates}
            metadata_by_id: dict[str, dict[str, str | None]] = {}
            # Injected sessions are deterministic listing fixtures; detail fetching is
            # exercised only by the production transport or explicit integration tests.
            if self._session_factory is None:
                detail_requests_made = 0
                with self._open_session() as detail_session:
                    for job in validated_jobs:
                        if detail_requests_made >= self.max_detail_requests:
                            break
                        detail_url = candidate_map[job.candidate_id].url
                        if detail_url in self._detail_skip_urls:
                            continue
                        if urlsplit(detail_url).hostname != urlsplit(canonical_source_url).hostname:
                            continue
                        try:
                            validate_public_url(detail_url)
                            detail_response = detail_session.get(detail_url)
                            requests_made += 1
                            detail_requests_made += 1
                            if getattr(detail_response, "status", 200) < 400:
                                final_detail_url = canonicalize_url(
                                    str(getattr(detail_response, "url", detail_url))
                                )
                                if (
                                    urlsplit(final_detail_url).hostname
                                    == urlsplit(canonical_source_url).hostname
                                ):
                                    metadata_by_id[job.candidate_id] = _generic_detail_metadata(
                                        _response_html(detail_response)
                                    )
                        except (KeyError, UnsafeUrlError, ValueError, TypeError):
                            continue
            records = tuple(cast(dict[str, object], {
                "source": "generic", "source_job_id": job.candidate_id, "title": job.title,
                "source_job_url": candidate_map[job.candidate_id].url,
                **metadata_by_id.get(job.candidate_id, {}),
            }) for job in validated_jobs)
            return SourceBatch(records=records, requests_made=requests_made)
        except SourceError:
            raise
        except (GenericExtractionError, UnsafeUrlError, ValueError, TypeError) as exc:
            raise SourceError(
                f"Generic fallback failed to validate extraction output: {exc}",
                requests_made=requests_made,
            ) from exc


def _has_unhandled_pagination(document: HtmlElement, *, listing_url: str) -> bool:
    _targets, detected = _pagination_targets(document, listing_url=listing_url)
    return detected


_PROVIDER_ALLOWED_CANDIDATE_FIELDS = (
    "candidate_id",
    "url",
    "anchor_text",
    "nearby_text",
)

_PROVIDER_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["candidate_id", "title"],
                "additionalProperties": False,
            },
            "minItems": 0,
        }
    },
    "required": ["jobs"],
    "additionalProperties": False,
}


class OpenAIJobExtractionProvider:
    """Small OpenAI-compatible provider boundary for generic candidate classification."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        client: Any | None = None,
    ) -> None:
        resolved_key = api_key or os.getenv("GENERIC_AI_OPENAI_API_KEY")
        if resolved_key is None or not resolved_key.strip():
            raise ProviderConfigurationError("GENERIC_AI_OPENAI_API_KEY is required")

        self.api_key = resolved_key
        self.model = model or os.getenv("GENERIC_AI_OPENAI_MODEL", "gpt-4o-mini")
        timeout = timeout_seconds if timeout_seconds is not None else float(
            os.getenv("GENERIC_AI_OPENAI_TIMEOUT_SECONDS", "30")
        )
        self.timeout_seconds = timeout
        self._client = client

        if self._client is None:
            try:
                from openai import OpenAI  # type: ignore[import-not-found]
            except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
                raise ProviderConfigurationError(
                    "openai package is required to use OpenAIJobExtractionProvider"
                ) from exc
            self._client = OpenAI(api_key=self.api_key, timeout=self.timeout_seconds)

    def _build_request_payload(
        self,
        candidates: Sequence[GenericCandidate],
    ) -> dict[str, list[dict[str, str | None]]]:
        items: list[dict[str, str | None]] = []
        for candidate in candidates:
            item: dict[str, str | None] = {
                "candidate_id": candidate.candidate_id,
                "url": candidate.url,
                "anchor_text": candidate.anchor_text,
                "nearby_text": candidate.nearby_text,
            }
            extra_keys = set(item) - set(_PROVIDER_ALLOWED_CANDIDATE_FIELDS)
            if extra_keys:
                raise ProviderResponseError(f"unsupported payload fields: {sorted(extra_keys)}")
            items.append(item)
        return {"candidates": items}

    def _parse_provider_output(self, response: Any) -> dict[str, Any]:
        if hasattr(response, "output_text"):
            text = response.output_text
            if text is not None:
                payload = json.loads(text)
                return cast(dict[str, Any], payload)
        if hasattr(response, "choices"):
            choices = response.choices
            if choices:
                message = choices[0].message
                if message is not None:
                    content = message.content
                    if isinstance(content, str):
                        payload = json.loads(content)
                        return cast(dict[str, Any], payload)
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and "text" in item:
                                payload = json.loads(item["text"])
                                return cast(dict[str, Any], payload)
        if hasattr(response, "model_dump"):
            data = response.model_dump()
            if isinstance(data, dict):
                return data
        if isinstance(response, Mapping):
            return dict(response)
        raise ProviderResponseError("provider returned no usable structured output")

    def _extract_jobs_from_payload(self, payload: Mapping[str, Any]) -> tuple[ExtractedJob, ...]:
        if not isinstance(payload, Mapping):
            raise ProviderResponseError("provider response must decode to an object")

        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise ProviderResponseError("provider response must include a jobs list")

        parsed_jobs: list[ExtractedJob] = []
        for job in jobs:
            if not isinstance(job, Mapping):
                raise ProviderResponseError("provider job item must be an object")
            if "url" in job:
                raise ProviderResponseError("provider output cannot include authoritative URLs")
            candidate_id = job.get("candidate_id")
            title = job.get("title")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                raise ProviderResponseError("provider output must include a non-empty candidate_id")
            if not isinstance(title, str) or not title.strip():
                raise ProviderResponseError(
                    f"provider output must include a non-empty title for {candidate_id}"
                )
            parsed_jobs.append(ExtractedJob(candidate_id=candidate_id, title=title.strip()))

        return tuple(parsed_jobs)

    def extract_jobs(self, *, candidates: Sequence[GenericCandidate]) -> JobExtractionResult:
        if not candidates:
            return JobExtractionResult(jobs=())

        client = self._client
        if client is None:
            raise ProviderConfigurationError("provider client is not configured")

        request_payload = self._build_request_payload(candidates)
        try:
            if hasattr(client, "responses"):
                response = client.responses.create(
                    model=self.model,
                    input=[
                        {
                            "role": "system",
                            "content": (
                                "You are classifying public job candidates. "
                                "Return only JSON matching the provided schema. "
                                "Do not invent jobs, titles, or URLs. Use candidate_id only."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(request_payload),
                        },
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "job_extraction_result",
                            "schema": _PROVIDER_RESPONSE_SCHEMA,
                        },
                    },
                )
            elif hasattr(client, "chat") and hasattr(client.chat, "completions"):
                response = client.chat.completions.create(
                    model=self.model,
                    temperature=0.0,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are classifying public job candidates. "
                                "Return only JSON matching the schema. "
                                "Do not invent jobs, titles, or URLs. Use candidate_id only."
                            ),
                        },
                        {"role": "user", "content": json.dumps(request_payload)},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "job_extraction_result",
                            "schema": _PROVIDER_RESPONSE_SCHEMA,
                        },
                    },
                )
            else:
                raise ProviderConfigurationError("provider client is not supported")
        except Exception as exc:
            raise ProviderResponseError(f"provider request failed: {exc}") from exc

        payload = self._parse_provider_output(response)
        parsed_jobs = self._extract_jobs_from_payload(payload)
        validated = validate_extracted_jobs(candidates, parsed_jobs)
        return JobExtractionResult(jobs=validated)


def extract_jobs_from_html(
    html: str,
    *,
    base_url: str,
    provider: JobExtractionProvider,
) -> tuple[ExtractedJob, ...]:
    """Minimal generic extraction pipeline for Phase A.1 tests."""
    candidates = extract_generic_candidates(html, base_url=base_url)
    result = provider.extract_jobs(candidates=candidates)
    return validate_extracted_jobs(candidates, result.jobs)
