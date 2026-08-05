"""Explicitly gated, bounded diagnostic runner for permitted LinkedIn pagination tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import FetcherSession
from scrapling.parser import Selector

from spikes.extraction import extract_job_cards

USER_AGENT = "linkedin-job-monitor-pagination-diagnostic"
NEXT_PAGE_SELECTOR = (
    "a[rel='next'][href], "
    "a.jobs-search-pagination__button--next[href], "
    "a[aria-label='Next'][href]"
)
BLOCKING_STATUSES = {401, 403, 429}
REDIRECT_MARKERS = {
    "login": ("/login", "/uas/login"),
    "authwall": ("/authwall",),
    "checkpoint": ("/checkpoint",),
}
CAPTCHA_MARKERS = ("captcha", "security verification")
ACCESS_DENIED_MARKERS = ("access denied", "not authorized to access")
CONSENT_MARKERS = ("consent interstitial", "before accessing linkedin", "sign in to linkedin")


class RedirectLike(Protocol):
    @property
    def url(self) -> object: ...


class ResponseLike(Protocol):
    @property
    def status(self) -> int: ...

    @property
    def url(self) -> object: ...

    @property
    def body(self) -> bytes: ...

    @property
    def history(self) -> Sequence[RedirectLike]: ...

    @property
    def headers(self) -> Mapping[str, str]: ...


class PageFetcher(Protocol):
    def get(self, url: str) -> ResponseLike: ...


@dataclass(frozen=True, slots=True)
class LivePaginationConfig:
    max_pages: int = 4
    max_requests: int = 4
    request_delay_seconds: float = 2.0
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_pages <= 4:
            raise ValueError("max_pages must be between 1 and 4")
        if not 1 <= self.max_requests <= 4:
            raise ValueError("max_requests must be between 1 and 4")
        if self.request_delay_seconds < 2.0:
            raise ValueError("request_delay_seconds must be at least 2")


class PlainSessionFetcher:
    """Use a fresh plain session per request so response cookies are never reused."""

    def __init__(self, config: LivePaginationConfig) -> None:
        self.config = config

    def get(self, url: str) -> ResponseLike:
        with FetcherSession(
            http3=False,
            timeout=self.config.timeout_seconds,
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
            headers={"User-Agent": USER_AGENT},
        ) as session:
            return cast(PageFetcher, session).get(url)


@dataclass(frozen=True, slots=True)
class RobotsPreflightResult:
    target_url: str
    target_allowed: bool
    robots_url: str | None
    status: int | None
    redirect_count: int | None

    def diagnostic(self) -> dict[str, object]:
        return {
            "target_url": self.target_url,
            "target_allowed": self.target_allowed,
            "robots_url": self.robots_url,
            "status": self.status,
            "redirect_count": self.redirect_count,
        }

    @property
    def warning(self) -> str | None:
        if self.target_allowed:
            return None
        return "robots.txt disallows the target for ordinary operation"


def _diagnostic_result(
    *,
    requested_urls: list[str],
    http_statuses: list[int],
    redirects: list[list[str]],
    found_job_ids: set[str],
    pages: int,
    requests: int,
    started: float,
    clock: Callable[[], float],
    stop_reason: str,
) -> dict[str, object]:
    return {
        "requested_urls": requested_urls,
        "http_statuses": http_statuses,
        "redirects": redirects,
        "found_job_ids": sorted(found_job_ids),
        "pages": pages,
        "requests": requests,
        "duration_seconds": round(clock() - started, 3),
        "stop_reason": stop_reason,
    }


def empty_diagnostic(
    stop_reason: str,
    *,
    duration_seconds: float = 0.0,
    robots: RobotsPreflightResult | None = None,
) -> dict[str, object]:
    """Return the JSON shape used when a safety gate stops before fetching."""
    return {
        "requested_urls": [],
        "http_statuses": [],
        "redirects": [],
        "found_job_ids": [],
        "pages": 0,
        "requests": 0,
        "duration_seconds": round(duration_seconds, 3),
        "stop_reason": stop_reason,
        "robots_preflight": robots.diagnostic() if robots else None,
        "robots_warning": robots.warning if robots else None,
    }


def _is_linkedin_https_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    is_linkedin_host = hostname == "linkedin.com" or hostname.endswith(".linkedin.com")
    return parsed.scheme == "https" and is_linkedin_host


def _redirect_reason(urls: Sequence[str]) -> str | None:
    for url in urls:
        path = urlparse(url).path.casefold()
        for label, markers in REDIRECT_MARKERS.items():
            if any(marker in path for marker in markers):
                return f"redirect_{label}"
    return None


def _header(response: ResponseLike, name: str) -> str | None:
    wanted = name.casefold()
    return next(
        (str(value) for key, value in response.headers.items() if str(key).casefold() == wanted),
        None,
    )


def _body_block_reason(html: str, *, has_job_cards: bool) -> str | None:
    folded = html.casefold()
    if any(marker in folded for marker in CAPTCHA_MARKERS):
        return "captcha"
    if any(marker in folded for marker in ACCESS_DENIED_MARKERS):
        return "access_denied"
    if not has_job_cards and any(marker in folded for marker in CONSENT_MARKERS):
        return "consent_interstitial"
    return None


def _explicit_next_url(html: str, page_url: str) -> str | None:
    page = Selector(content=html, url=page_url)
    links = page.css(NEXT_PAGE_SELECTOR)
    if not links:
        return None
    href = links[0].attrib.get("href")
    if not isinstance(href, str) or not href:
        return None
    candidate = urljoin(page_url, href)
    if not _is_linkedin_https_url(candidate):
        return None
    if "/jobs/view/" in urlparse(candidate).path.casefold():
        return None
    return candidate


def run_live_pagination(
    *,
    start_url: str,
    fetcher: PageFetcher,
    config: LivePaginationConfig | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, object]:
    """Fetch at most four list pages through an injected sequential fetcher."""
    config = config or LivePaginationConfig()
    started = clock()
    requested_urls: list[str] = []
    http_statuses: list[int] = []
    redirects: list[list[str]] = []
    found_job_ids: set[str] = set()
    seen_urls: set[str] = set()
    seen_content_hashes: set[str] = set()
    pages = 0
    requests = 0
    current_url = start_url
    stop_reason = "invalid_start_url"

    if not _is_linkedin_https_url(start_url):
        return _diagnostic_result(
            requested_urls=requested_urls,
            http_statuses=http_statuses,
            redirects=redirects,
            found_job_ids=found_job_ids,
            pages=pages,
            requests=requests,
            started=started,
            clock=clock,
            stop_reason=stop_reason,
        )

    while True:
        if pages >= config.max_pages:
            stop_reason = "max_pages"
            break
        if requests >= config.max_requests:
            stop_reason = "max_requests"
            break
        if current_url in seen_urls:
            stop_reason = "repeated_url"
            break
        if requests:
            sleep(config.request_delay_seconds)

        seen_urls.add(current_url)
        requested_urls.append(current_url)
        try:
            response = fetcher.get(current_url)
        except Exception as exc:  # The diagnostic must fail closed and emit JSON.
            requests += 1
            stop_reason = f"fetch_error_{type(exc).__name__}"
            break
        requests += 1
        pages += 1

        status = response.status
        final_url = str(response.url)
        history_urls = [str(item.url) for item in response.history]
        location = _header(response, "location")
        location_url = urljoin(current_url, location) if location else None
        redirect_urls = [*history_urls, *([location_url] if location_url else [])]
        http_statuses.append(status)
        redirects.append(redirect_urls)

        if status in BLOCKING_STATUSES:
            stop_reason = f"http_{status}"
            break
        if status >= 400:
            stop_reason = f"http_error_{status}"
            break

        redirect_targets = [*history_urls, *([location_url] if location_url else [])]
        redirect_reason = _redirect_reason(redirect_targets)
        if redirect_reason is not None:
            stop_reason = redirect_reason
            break
        if 300 <= status < 400:
            stop_reason = "redirect_other" if location_url else "redirect_without_location"
            break
        if final_url != current_url and final_url in seen_urls:
            stop_reason = "repeated_url"
            break
        seen_urls.add(final_url)

        html = bytes(response.body).decode("utf-8", errors="replace")
        content_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
        if content_hash in seen_content_hashes:
            stop_reason = "repeated_content"
            break
        seen_content_hashes.add(content_hash)

        page_cards = extract_job_cards(html, base_url=final_url)
        block_reason = _body_block_reason(html, has_job_cards=bool(page_cards))
        if block_reason is not None:
            stop_reason = block_reason
            break

        page_job_ids = {
            card.linkedin_job_id for card in page_cards if card.linkedin_job_id is not None
        }
        new_job_ids = page_job_ids - found_job_ids
        found_job_ids.update(page_job_ids)
        if not new_job_ids:
            stop_reason = "no_new_job_ids"
            break

        next_url = _explicit_next_url(html, final_url)
        if next_url is None:
            stop_reason = "no_next_page"
            break
        current_url = next_url

    return _diagnostic_result(
        requested_urls=requested_urls,
        http_statuses=http_statuses,
        redirects=redirects,
        found_job_ids=found_job_ids,
        pages=pages,
        requests=requests,
        started=started,
        clock=clock,
        stop_reason=stop_reason,
    )


def read_robots_preflight(
    path: Path | None, target_url: str
) -> RobotsPreflightResult | None:
    """Read a saved result from the unchanged preflight for this exact URL."""
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    robots = payload.get("robots")
    if (
        payload.get("target_url") != target_url
        or payload.get("target_requested") is not False
        or not isinstance(robots, dict)
        or not isinstance(robots.get("target_allowed"), bool)
    ):
        return None
    robots_url = robots.get("url")
    status = robots.get("status")
    redirect_count = robots.get("redirect_count")
    return RobotsPreflightResult(
        target_url=target_url,
        target_allowed=robots["target_allowed"],
        robots_url=robots_url if isinstance(robots_url, str) else None,
        status=status if isinstance(status, int) else None,
        redirect_count=redirect_count if isinstance(redirect_count, int) else None,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--confirm-live-test", action="store_true")
    parser.add_argument("--robots-preflight-result", type=Path)
    args = parser.parse_args(argv)
    started = clock()
    robots = read_robots_preflight(args.robots_preflight_result, args.url)

    if robots is None:
        result = empty_diagnostic(
            "robots_preflight_invalid", duration_seconds=clock() - started
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2
    if not args.confirm_live_test:
        result = empty_diagnostic(
            "confirmation_required",
            duration_seconds=clock() - started,
            robots=robots,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2

    config = LivePaginationConfig()
    result = run_live_pagination(
        start_url=args.url,
        fetcher=PlainSessionFetcher(config),
        config=config,
        sleep=sleep,
        clock=clock,
    )
    result["robots_preflight"] = robots.diagnostic()
    result["robots_warning"] = robots.warning
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
