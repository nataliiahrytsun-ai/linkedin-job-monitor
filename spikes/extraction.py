"""Small, fixture-testable extraction helpers for the LinkedIn spike."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from scrapling.parser import Selector

from spikes import selectors

_JOB_ID_PATTERNS = (
    re.compile(r"urn:li:jobPosting:(\d+)"),
    re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?:[/?#]|$)"),
    re.compile(r"(?:currentJobId|jobId)=(\d+)(?:&|$)"),
    re.compile(r"^\d+$"),
)


@dataclass(frozen=True, slots=True)
class JobCard:
    linkedin_job_id: str | None
    title: str | None
    company: str | None
    location: str | None
    published_at: str | None
    job_url: str | None


@dataclass(frozen=True, slots=True)
class JobDetail:
    description: str | None
    seniority_level: str | None
    employment_type: str | None
    job_function: str | None
    industries: str | None


def _clean(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def _first_text(node: Selector, css: str) -> str | None:
    selected = node.css(css)
    if not selected:
        return None
    return _clean(selected[0].text)


def _all_text(node: Selector, css: str) -> str | None:
    selected = node.css(css)
    if not selected:
        return None
    return _clean(" ".join(selected[0].xpath(".//text()").getall()))


def _visible_link_text(link: Selector) -> str | None:
    text_nodes = link.xpath(
        ".//text()["
        "not(ancestor::*[@hidden]) and "
        "not(ancestor::*[@aria-hidden='true']) and "
        "not(ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' sr-only ')]) and "
        "not(ancestor::*[contains(translate(@style, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz'), 'display:none')]) and "
        "not(ancestor::*[contains(translate(@style, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz'), 'visibility:hidden')])"
        "]"
    ).getall()
    return _clean(" ".join(text_nodes))


def _link_title(link: Selector) -> str | None:
    return (
        _first_text(link, selectors.JOB_TITLE)
        or _first_text(link, selectors.JOB_LINK_SCREEN_READER_TITLE)
        or _clean(link.attrib.get("aria-label"))
        or _visible_link_text(link)
    )


def _validated_linkedin_job_url(href: str | None, base_url: str) -> str | None:
    if not href:
        return None
    absolute_url = urljoin(base_url, href)
    parsed = urlparse(absolute_url)
    hostname = (parsed.hostname or "").casefold()
    is_linkedin_host = hostname == "linkedin.com" or hostname.endswith(".linkedin.com")
    if parsed.scheme not in {"http", "https"} or not is_linkedin_host:
        return None
    return absolute_url


def extract_linkedin_job_id(*values: str | None) -> str | None:
    """Return the first numeric LinkedIn job ID found in attributes or URLs."""
    for value in values:
        if not value:
            continue
        for pattern in _JOB_ID_PATTERNS:
            match = pattern.search(value)
            if match:
                return match.group(1) if match.lastindex else match.group(0)
    return None


def extract_job_cards(html: str, base_url: str = "https://www.linkedin.com") -> list[JobCard]:
    page = Selector(content=html, url=base_url)
    cards: list[JobCard] = []
    seen_job_ids: set[str] = set()

    for node in page.css(selectors.JOB_CARD):
        link_nodes = node.css(selectors.JOB_LINK)
        link_node = link_nodes[0] if link_nodes else None
        href = _clean(link_node.attrib.get("href")) if link_node is not None else None
        absolute_url = urljoin(base_url, href) if href else None
        id_candidates = [node.attrib.get(attribute) for attribute in selectors.JOB_ID_ATTRIBUTES]
        job_id = extract_linkedin_job_id(*id_candidates, absolute_url)
        if job_id is not None and job_id in seen_job_ids:
            continue
        cards.append(
            JobCard(
                linkedin_job_id=job_id,
                title=_first_text(node, selectors.JOB_TITLE)
                or (_link_title(link_node) if link_node is not None else None),
                company=_first_text(node, selectors.JOB_COMPANY),
                location=_first_text(node, selectors.JOB_LOCATION),
                published_at=_clean(node.css(selectors.JOB_PUBLISHED_AT).get()),
                job_url=absolute_url,
            )
        )
        if job_id is not None:
            seen_job_ids.add(job_id)

    for link_node in page.css(selectors.JOB_LINK_FALLBACK):
        href = _clean(link_node.attrib.get("href"))
        absolute_url = _validated_linkedin_job_url(href, base_url)
        job_id = extract_linkedin_job_id(absolute_url)
        if absolute_url is None or job_id is None or not job_id.isdigit():
            continue
        if job_id in seen_job_ids:
            continue
        cards.append(
            JobCard(
                linkedin_job_id=job_id,
                title=_link_title(link_node),
                company=None,
                location=None,
                published_at=None,
                job_url=absolute_url,
            )
        )
        seen_job_ids.add(job_id)

    return cards


def extract_job_detail(html: str, base_url: str = "https://www.linkedin.com") -> JobDetail:
    page = Selector(content=html, url=base_url)
    criteria: dict[str, str] = {}
    for item in page.css(selectors.DETAIL_CRITERIA):
        label = _first_text(item, selectors.DETAIL_CRITERIA_LABEL)
        value = _first_text(item, selectors.DETAIL_CRITERIA_VALUE)
        if label and value:
            criteria[label.casefold()] = value

    def criterion(*labels: str) -> str | None:
        return next(
            (criteria[label.casefold()] for label in labels if label.casefold() in criteria),
            None,
        )

    return JobDetail(
        description=_all_text(page, selectors.DETAIL_DESCRIPTION),
        seniority_level=criterion("Seniority level"),
        employment_type=criterion("Employment type"),
        job_function=criterion("Job function"),
        industries=criterion("Industries", "Industry"),
    )


def unique_cards(cards: Iterable[JobCard]) -> list[JobCard]:
    """Deduplicate by LinkedIn ID, then by stable normalized public URL."""
    result: list[JobCard] = []
    seen: set[str] = set()
    for card in cards:
        parsed = urlparse(card.job_url or "")
        fallback = f"url:{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.netloc else None
        id_key = f"id:{card.linkedin_job_id}" if card.linkedin_job_id else None
        keys = {key for key in (id_key, fallback) if key}
        if not keys or seen.isdisjoint(keys):
            result.append(card)
            seen.update(keys)
    return result


def should_stop_pagination(
    *,
    page_number: int,
    request_count: int,
    new_job_ids: set[str],
    max_pages: int,
    max_requests: int,
) -> bool:
    """Bound pagination and stop when a page contributes no new stable IDs."""
    return (
        page_number >= max_pages
        or request_count >= max_requests
        or not new_job_ids
    )
