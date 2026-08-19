"""Low-level public job extraction contracts for generic source fallback."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import unquote, urljoin, urlsplit

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
_PAGINATION_QUERY_RE = re.compile(r"(?:^|&)(?:page|offset|start|cursor)=", flags=re.IGNORECASE)
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
        "view position",
        "view role",
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


def _looks_like_job_link(url: str, anchor_text: str | None, nearby_text: str | None) -> bool:
    if _is_blocked_path(url):
        return False

    path = urlsplit(url).path.casefold()
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

    if path.startswith("/careers/") and not any(character.isdigit() for character in path):
        return False

    if _contains_any(path, _JOB_PATH_HINTS):
        return True

    combined = " ".join(part for part in (anchor_text, nearby_text) if part)
    if not combined:
        return False

    if _contains_any(combined, _BLOCKED_TEXT_HINTS):
        return False

    return _contains_any(combined, _JOB_TEXT_HINTS)


def _context_text(anchor: HtmlElement) -> str | None:
    parent = anchor.getparent()
    if parent is None:
        return None
    candidate = " ".join(
        part.strip() for part in parent.itertext() if part and part.strip()
    )
    return _clean_text(candidate[:_MAX_NEARBY_TEXT])


def _build_candidate_from_anchor(anchor: HtmlElement, *, base_url: str) -> GenericCandidate | None:
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

    anchor_text = _clean_text(
        " ".join(part.strip() for part in anchor.itertext() if part and part.strip())
    )
    nearby_text = _context_text(anchor)
    if not _looks_like_job_link(resolved, anchor_text, nearby_text):
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

    for anchor in document.xpath(".//a[@href]"):
        candidate = _build_candidate_from_anchor(anchor, base_url=base_url)
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


class GenericSourceAdapter:
    """Execute a public generic-careers page through the source-neutral adapter contract."""

    def __init__(
        self,
        *,
        provider: JobExtractionProvider | None = None,
        session_factory: Any | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self._session_factory = session_factory

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
            with self._open_session() as session:
                response = session.get(canonical_source_url)
                requests_made += 1
            if getattr(response, "status", 200) >= 400:
                raise SourceError(
                    f"Generic fallback fetch failed with HTTP {getattr(response, 'status', 200)}",
                    requests_made=requests_made,
                )

            body = getattr(response, "body", b"")
            if isinstance(body, bytes | bytearray):
                html = body.decode("utf-8", errors="replace")
            elif isinstance(body, memoryview):
                html = bytes(body).decode("utf-8", errors="replace")
            else:
                html = str(body)
            document = lxml_html.fromstring(html)
            if _has_unhandled_pagination(document, listing_url=canonical_source_url):
                raise SourceError(
                    "Generic fallback listing exposes pagination that was not fully traversed",
                    requests_made=requests_made,
                )
            candidates = extract_generic_candidates(html, base_url=canonical_source_url)
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
            records = tuple(
                cast(
                    dict[str, object],
                    {
                        "source": "generic",
                        "source_job_id": job.candidate_id,
                        "title": job.title,
                        "source_job_url": candidate_map[job.candidate_id].url,
                    },
                )
                for job in validated_jobs
            )
            return SourceBatch(records=records, requests_made=requests_made)
        except SourceError:
            raise
        except (GenericExtractionError, UnsafeUrlError, ValueError, TypeError) as exc:
            raise SourceError(
                f"Generic fallback failed to validate extraction output: {exc}",
                requests_made=requests_made,
            ) from exc


def _has_unhandled_pagination(document: HtmlElement, *, listing_url: str) -> bool:
    for node in document.xpath(".//a[@href]"):
        href = urljoin(listing_url, node.get("href") or "")
        parsed = urlsplit(href)
        label = (_clean_text(" ".join(node.itertext())) or "").casefold()
        rel = (node.get("rel") or "").casefold().split()
        aria_label = (node.get("aria-label") or "").casefold()
        path = parsed.path.casefold()
        query = parsed.query.casefold()
        if (
            "next" in rel
            or label in {"next", "next page", "older"}
            or aria_label in {"next", "next page"}
            or _PAGINATION_QUERY_RE.search(query)
            or re.search(r"/page/\d+/?$", path)
        ):
            return True
    return False


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
