"""Network-free pagination orchestration for synthetic spike pages."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from spikes.extraction import JobCard, extract_job_cards, unique_cards


@dataclass(frozen=True, slots=True)
class PaginationPage:
    """One page returned by an injected, local-only page source."""

    url: str
    html: str
    next_url: str | None


class LocalPageSource(Protocol):
    """Interface implemented by an in-memory or fixture-backed page source."""

    def get_page(self, url: str) -> PaginationPage:
        """Return a local page for ``url`` without performing network I/O."""
        ...


@dataclass(frozen=True, slots=True)
class PaginationResult:
    cards: tuple[JobCard, ...]
    job_ids: frozenset[str]
    pages_fetched: int
    requests_made: int
    stop_reason: str


def run_local_pagination(
    *,
    start_url: str,
    source: LocalPageSource,
    max_pages: int,
    max_requests: int,
) -> PaginationResult:
    """Traverse locally supplied pages until a deterministic stop condition fires."""
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if max_requests < 1:
        raise ValueError("max_requests must be at least 1")

    current_url = start_url
    seen_urls: set[str] = set()
    seen_content_hashes: set[str] = set()
    seen_job_ids: set[str] = set()
    cards: list[JobCard] = []
    pages_fetched = 0
    requests_made = 0

    while True:
        if pages_fetched >= max_pages:
            stop_reason = "max_pages"
            break
        if requests_made >= max_requests:
            stop_reason = "max_requests"
            break
        if current_url in seen_urls:
            stop_reason = "repeated_url"
            break

        seen_urls.add(current_url)
        page = source.get_page(current_url)
        requests_made += 1
        pages_fetched += 1

        if page.url != current_url and page.url in seen_urls:
            stop_reason = "repeated_url"
            break
        seen_urls.add(page.url)

        content_hash = hashlib.sha256(page.html.encode("utf-8")).hexdigest()
        if content_hash in seen_content_hashes:
            stop_reason = "repeated_content"
            break
        seen_content_hashes.add(content_hash)

        page_cards = extract_job_cards(page.html, base_url=page.url)
        page_job_ids = {
            card.linkedin_job_id for card in page_cards if card.linkedin_job_id is not None
        }
        new_job_ids = page_job_ids - seen_job_ids
        cards = unique_cards([*cards, *page_cards])
        seen_job_ids.update(page_job_ids)

        if not new_job_ids:
            stop_reason = "no_new_job_ids"
            break
        if page.next_url is None:
            stop_reason = "no_next_page"
            break
        current_url = page.next_url

    return PaginationResult(
        cards=tuple(cards),
        job_ids=frozenset(seen_job_ids),
        pages_fetched=pages_fetched,
        requests_made=requests_made,
        stop_reason=stop_reason,
    )
