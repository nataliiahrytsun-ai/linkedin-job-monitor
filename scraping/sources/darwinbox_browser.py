"""Plain headful-browser transport for public Darwinbox careers pages."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import quote, urlsplit

from playwright.sync_api import Page, Response

DEFAULT_BROWSER_TIMEOUT_MS = 20_000
_ALL_JOBS_PATH = "/ms/candidateapi/job/alljobs"
_CHALLENGE_MARKERS = (
    "attention required",
    "captcha",
    "cloudflare",
    "turnstile",
)


class DarwinboxBrowserLocation(Protocol):
    """Location fields required by the browser transport."""

    @property
    def scheme(self) -> str: ...

    @property
    def host(self) -> str: ...

    @property
    def company_id(self) -> str: ...


type BrowserFetch = Callable[..., object]


class DarwinboxBrowserTransportError(RuntimeError):
    """Browser collection failed without yielding a complete data operation."""

    def __init__(self, message: str, *, requests_made: int) -> None:
        super().__init__(message)
        self.requests_made = requests_made


@dataclass(slots=True)
class _Capture:
    bodies: list[bytes] = field(default_factory=list)
    response_ids: set[int] = field(default_factory=set)
    error: str | None = None
    error_requests_made: int | None = None

    def fail(self, message: str, *, requests_made: int | None = None) -> None:
        self.error = message
        self.error_requests_made = (
            len(self.bodies) if requests_made is None else requests_made
        )

    def add(self, response: Response) -> None:
        if id(response) in self.response_ids:
            return
        self.response_ids.add(id(response))
        if not 200 <= response.status < 300:
            self.fail(
                f"Darwinbox browser data request returned HTTP {response.status}",
                requests_made=len(self.bodies) + 1,
            )
            return
        try:
            self.bodies.append(response.body())
        except Exception:
            self.fail(
                "Darwinbox browser could not read the data response",
                requests_made=len(self.bodies) + 1,
            )


def _default_browser_fetch(url: str, **kwargs: Any) -> object:
    from scrapling.fetchers import DynamicFetcher

    return DynamicFetcher.fetch(url, **kwargs)


def _is_listing_response(response: Response) -> bool:
    return urlsplit(response.url).path == _ALL_JOBS_PATH


def _listing_progress(bodies: list[bytes]) -> tuple[int, int]:
    seen: set[str] = set()
    seen_pages: set[tuple[str, ...]] = set()
    total: int | None = None
    for index, body in enumerate(bodies, start=1):
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
            raise DarwinboxBrowserTransportError(
                "Darwinbox browser listing response must contain valid JSON",
                requests_made=len(bodies),
            ) from error
        if not isinstance(payload, dict):
            raise DarwinboxBrowserTransportError(
                "Darwinbox browser listing response root must be an object",
                requests_made=len(bodies),
            )
        if payload.get("status") != "success":
            raise DarwinboxBrowserTransportError(
                "Darwinbox browser listing response status is not success",
                requests_made=len(bodies),
            )
        current_total = payload.get("job_counts")
        data = payload.get("data")
        if type(current_total) is not int or current_total < 0 or not isinstance(data, list):
            raise DarwinboxBrowserTransportError(
                "Darwinbox browser listing response has an invalid snapshot shape",
                requests_made=len(bodies),
            )
        if total is None:
            total = current_total
        elif current_total != total:
            raise DarwinboxBrowserTransportError(
                "Darwinbox job_counts changed during browser pagination",
                requests_made=len(bodies),
            )
        page_ids: list[str] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise DarwinboxBrowserTransportError(
                    "Darwinbox browser listing job requires a string id",
                    requests_made=len(bodies),
                )
            job_id = cast(str, item["id"]).strip()
            if not job_id:
                raise DarwinboxBrowserTransportError(
                    "Darwinbox browser listing job requires a non-empty id",
                    requests_made=len(bodies),
                )
            page_ids.append(job_id)
        signature = tuple(page_ids)
        if signature in seen_pages and len(seen) < current_total:
            raise DarwinboxBrowserTransportError(
                "Darwinbox browser pagination repeated a page before completion",
                requests_made=index,
            )
        seen_pages.add(signature)
        before = len(seen)
        seen.update(page_ids)
        if len(seen) == before and len(seen) < current_total:
            reason = "empty page" if not page_ids else "no new job IDs"
            raise DarwinboxBrowserTransportError(
                f"Darwinbox browser pagination returned {reason} before completion",
                requests_made=index,
            )
        if len(seen) > current_total:
            raise DarwinboxBrowserTransportError(
                "Darwinbox browser listing exceeded job_counts",
                requests_made=index,
            )
    return len(seen), total or 0


def _visible_load_more(page: Page) -> Any | None:
    candidates = (
        page.get_by_role("button", name=re.compile(r"load more", re.I)),
        page.get_by_text(re.compile(r"load more", re.I), exact=False),
    )
    for candidate in candidates:
        if candidate.count() and candidate.first.is_visible():
            return candidate.first
    return None


def _challenge_present(page: Page) -> bool:
    try:
        text = f"{page.title()} {page.locator('body').inner_text()}".casefold()
    except Exception:
        return False
    return any(marker in text for marker in _CHALLENGE_MARKERS)


class DarwinboxBrowserTransport:
    """Collect Darwinbox data emitted by a normal, temporary headful Chrome run."""

    def __init__(self, *, browser_fetch: BrowserFetch = _default_browser_fetch) -> None:
        self._browser_fetch = browser_fetch

    def _fetch(
        self,
        url: str,
        capture: _Capture,
        page_action: Callable[[Page], None],
        response_predicate: Callable[[Response], bool],
        timeout_seconds: float,
    ) -> None:
        def setup(page: Page) -> None:
            page.on(
                "response",
                lambda response: capture.add(response)
                if response_predicate(response)
                else None,
            )

        try:
            self._browser_fetch(
                url,
                headless=False,
                real_chrome=True,
                google_search=False,
                disable_resources=False,
                network_idle=False,
                load_dom=True,
                timeout=max(1, int(timeout_seconds * 1000)),
                retries=1,
                user_data_dir="",
                page_setup=setup,
                page_action=page_action,
            )
        except Exception as error:
            raise DarwinboxBrowserTransportError(
                capture.error or "Darwinbox headful browser navigation failed",
                requests_made=(
                    len(capture.bodies)
                    if capture.error_requests_made is None
                    else capture.error_requests_made
                ),
            ) from error
        if capture.error:
            raise DarwinboxBrowserTransportError(
                capture.error,
                requests_made=(
                    0
                    if capture.error_requests_made is None
                    else capture.error_requests_made
                ),
            )

    def listing_pages(
        self,
        location: DarwinboxBrowserLocation,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> tuple[bytes, ...]:
        company_id = quote(location.company_id, safe="")
        url = (
            f"{location.scheme}://{location.host}/ms/candidatev2/{company_id}"
            "/careers/allJobs"
        )
        capture = _Capture()

        def action(page: Page) -> None:
            if not capture.bodies:
                with suppress(Exception):
                    page.wait_for_load_state(
                        "networkidle", timeout=max(1, int(timeout_seconds * 1000))
                    )
            if capture.error:
                return
            if _challenge_present(page):
                capture.fail(
                    "Darwinbox careers page presented an access-control challenge"
                )
                return
            if not capture.bodies:
                capture.fail(
                    "Darwinbox careers page did not emit an initial listing response"
                )
                return
            for _click_number in range(1, max_pages):
                try:
                    unique_count, total = _listing_progress(capture.bodies)
                except DarwinboxBrowserTransportError as error:
                    capture.fail(str(error), requests_made=error.requests_made)
                    return
                if unique_count >= total:
                    return
                button = _visible_load_more(page)
                if button is None:
                    capture.fail(
                        "Darwinbox listing ended before the complete snapshot was loaded"
                    )
                    return
                try:
                    with page.expect_response(
                        _is_listing_response,
                        timeout=max(1, int(timeout_seconds * 1000)),
                    ) as response_info:
                        button.click()
                    capture.add(response_info.value)
                except Exception:
                    capture.fail(
                        "Darwinbox Load More did not produce a listing response"
                    )
                    return

        self._fetch(url, capture, action, _is_listing_response, timeout_seconds)
        if not capture.bodies:
            raise DarwinboxBrowserTransportError(
                capture.error or "Darwinbox browser captured no listing response",
                requests_made=0,
            )
        return tuple(capture.bodies)

    def detail(
        self,
        location: DarwinboxBrowserLocation,
        source_job_id: str,
        *,
        timeout_seconds: float,
    ) -> bytes:
        company_id = quote(location.company_id, safe="")
        job_id = quote(source_job_id, safe="")
        url = (
            f"{location.scheme}://{location.host}/ms/candidatev2/{company_id}"
            f"/careers/jobDetails/{job_id}?from=all"
        )
        capture = _Capture()
        detail_path = f"/ms/candidateapi/job/{job_id}"

        def is_detail_response(response: Response) -> bool:
            return urlsplit(response.url).path == detail_path

        def action(page: Page) -> None:
            if not capture.bodies:
                with suppress(Exception):
                    page.wait_for_load_state(
                        "networkidle", timeout=max(1, int(timeout_seconds * 1000))
                    )
            if capture.error:
                return
            if _challenge_present(page):
                capture.fail(
                    "Darwinbox job detail presented an access-control challenge"
                )
            elif not capture.bodies:
                capture.fail(
                    "Darwinbox job detail page did not emit its public data response"
                )

        self._fetch(url, capture, action, is_detail_response, timeout_seconds)
        if len(capture.bodies) != 1:
            raise DarwinboxBrowserTransportError(
                capture.error or "Darwinbox browser captured an ambiguous detail response",
                requests_made=len(capture.bodies),
            )
        return capture.bodies[0]
