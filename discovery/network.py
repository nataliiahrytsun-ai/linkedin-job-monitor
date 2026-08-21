"""Bounded public-web crawler with fail-closed SSRF checks."""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol, cast
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit


class UnsafeUrlError(ValueError):
    pass


class CrawlError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    url: str
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class CrawledPage:
    requested_url: str
    url: str
    body: str
    links: tuple[str, ...]
    depth: int
    redirects: tuple[str, ...] = ()
    navigation_links: tuple[str, ...] = ()


class HttpTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse: ...


type FrontierPriority = Callable[[str, str, str, int], tuple[int, ...]]
type FrontierAdmission = Callable[[str, str, str, int], bool]


class _ScraplingResponse(Protocol):
    status: int
    url: object
    headers: Mapping[object, object]
    body: bytes | bytearray | memoryview


class _ScraplingSession(Protocol):
    def get(self, url: str) -> _ScraplingResponse: ...


def canonicalize_url(url: str) -> str:
    clean, _fragment = urldefrag(url.strip())
    parsed = urlsplit(clean)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        raise UnsafeUrlError("Only absolute HTTP(S) URLs are allowed")
    try:
        port = parsed.port
    except ValueError as error:
        raise UnsafeUrlError("URL has an invalid port") from error
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL credentials are not allowed")
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def _public_addresses(host: str) -> frozenset[str]:
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeUrlError("Local hosts are not allowed")
    try:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as error:
        raise UnsafeUrlError("Host could not be resolved") from error
    addresses = frozenset(cast(str, record[4][0]) for record in records)
    if not addresses:
        raise UnsafeUrlError("Host did not resolve")
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            raise UnsafeUrlError("Host resolves to a non-public address")
    return addresses


def validate_public_url(url: str) -> tuple[str, frozenset[str]]:
    canonical = canonicalize_url(url)
    host = cast(str, urlsplit(canonical).hostname)
    return canonical, _public_addresses(host)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None


class ScraplingTransport:
    def __init__(self) -> None:
        self._active_session: _ScraplingSession | None = None

    @contextmanager
    def session(self, *, timeout_seconds: float) -> Iterator[ScraplingTransport]:
        """Keep one installed-Scrapling session active for the complete crawl."""
        from scrapling.fetchers import FetcherSession

        if self._active_session is not None:
            raise RuntimeError("Scrapling transport session is already active")
        context = FetcherSession(
            http3=False,
            timeout=timeout_seconds,
            # Scrapling 0.4.8 interprets this as total attempts, not extra retries.
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
            headers={"User-Agent": "job-monitor-source-discovery/1.0"},
        )
        with context as active_session:
            try:
                self._active_session = cast(_ScraplingSession, active_session)
                yield self
            finally:
                self._active_session = None

    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        del timeout_seconds
        if self._active_session is None:
            raise RuntimeError("Scrapling transport requires an active crawl session")
        response = self._active_session.get(url)
        headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        body = bytes(response.body)
        return HttpResponse(
            status=int(response.status),
            url=str(getattr(response, "url", url)),
            headers=headers,
            body=body,
        )


class BoundedCrawler:
    def __init__(
        self,
        *,
        transport: HttpTransport,
        max_requests: int = 8,
        max_depth: int = 2,
        max_redirects: int = 4,
        max_body_bytes: int = 2_000_000,
        timeout_seconds: float = 10.0,
        total_timeout_seconds: float = 45.0,
        resolver: Callable[[str], frozenset[str]] = _public_addresses,
        frontier_priority: FrontierPriority | None = None,
        frontier_admission: FrontierAdmission | None = None,
    ) -> None:
        self.transport = transport
        self.max_requests = max_requests
        self.max_depth = max_depth
        self.max_redirects = max_redirects
        self.max_body_bytes = max_body_bytes
        self.timeout_seconds = timeout_seconds
        self.total_timeout_seconds = total_timeout_seconds
        self.resolver = resolver
        self.frontier_priority = frontier_priority
        self.frontier_admission = frontier_admission
        self._requests_made = 0
        self.errors: list[str] = []
        self._started_at = 0.0

    def _remaining_seconds(self) -> float:
        remaining = self.total_timeout_seconds - (time.monotonic() - self._started_at)
        if remaining <= 0:
            raise TimeoutError("Bounded crawl exceeded its total time limit")
        return min(self.timeout_seconds, remaining)

    def _safe(self, url: str) -> tuple[str, frozenset[str]]:
        canonical = canonicalize_url(url)
        return canonical, self.resolver(cast(str, urlsplit(canonical).hostname))

    def _fetch(self, url: str, *, transport: HttpTransport) -> tuple[HttpResponse, tuple[str, ...]]:
        current = url
        redirects: list[str] = []
        for _ in range(self.max_redirects + 1):
            if self._requests_made >= self.max_requests:
                raise CrawlError("Request limit reached")
            current, before = self._safe(current)
            self._requests_made += 1
            try:
                response = transport.get(current, timeout_seconds=self._remaining_seconds())
            except TimeoutError as error:
                raise CrawlError("Page request timed out") from error
            except Exception as error:
                raise CrawlError("Page request failed") from error
            after = self.resolver(cast(str, urlsplit(current).hostname))
            if before != after:
                raise UnsafeUrlError("DNS changed during request")
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise CrawlError("Redirect response has no Location")
                current = urljoin(current, location)
                redirects.append(canonicalize_url(current))
                continue
            if not 200 <= response.status < 300:
                raise CrawlError(f"Page returned HTTP {response.status}")
            content_type = response.headers.get("content-type", "text/html").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise CrawlError("Page is not HTML")
            length = response.headers.get("content-length")
            if length and length.isdigit() and int(length) > self.max_body_bytes:
                raise CrawlError("Response exceeds the body-size limit")
            if len(response.body) > self.max_body_bytes:
                raise CrawlError("Response exceeds the body-size limit")
            final_url, _ = self._safe(response.url)
            return (
                HttpResponse(response.status, final_url, response.headers, response.body),
                tuple(redirects),
            )
        raise CrawlError("Redirect limit exceeded")

    def _crawl(
        self, seeds: tuple[str, ...], *, transport: HttpTransport
    ) -> tuple[CrawledPage, ...]:
        self._requests_made = 0
        self._started_at = time.monotonic()
        self.errors = []
        queue: list[tuple[tuple[int, ...], int, str, int]] = [
            ((-1,), index, seed, 0) for index, seed in enumerate(seeds)
        ]
        next_frontier_index = len(queue)
        seen: set[str] = set()
        pages: list[CrawledPage] = []
        keywords = (
            "career",
            "jobs",
            "vacan",
            "work-with",
            "work_with",
            "karriere",
            "stellen",
            "kariéra",
            "prace",
        )
        while queue and len(pages) < self.max_requests:
            _priority, _frontier_index, raw_url, depth = queue.pop(0)
            try:
                requested = canonicalize_url(raw_url)
            except UnsafeUrlError:
                continue
            if requested in seen:
                continue
            seen.add(requested)
            try:
                response, redirects = self._fetch(requested, transport=transport)
            except (CrawlError, UnsafeUrlError) as error:
                self.errors.append(str(error))
                continue
            body = response.body.decode("utf-8", errors="replace")
            parser = _LinkParser()
            parser.feed(body)
            links: list[str] = []
            navigation_links: list[str] = []
            frontier: list[tuple[tuple[int, ...], int, str, int]] = []
            for href, text in parser.links:
                absolute = urljoin(response.url, href)
                searchable = f"{absolute} {text}".lower()
                default_admitted = any(keyword in searchable for keyword in keywords) or any(
                    host in searchable
                    for host in (
                        "lever.co",
                        "applytojob.com",
                        "dream.jobs",
                        "darwinbox",
                        "greenhouse.io",
                        "workable.com",
                        "smartrecruiters.com",
                        "teamtailor",
                    )
                )
                if default_admitted or self.frontier_admission is not None:
                    try:
                        canonical = canonicalize_url(absolute)
                    except UnsafeUrlError:
                        continue
                    if self.frontier_admission is not None and not self.frontier_admission(
                        canonical, text, response.url, depth
                    ):
                        continue
                    if self.frontier_admission is not None:
                        navigation_links.append(canonical)
                    if (urlsplit(canonical).hostname or "") in {
                        "dream.jobs",
                        "www.dream.jobs",
                        "business.dream.jobs",
                        "api.dream.jobs",
                    }:
                        continue
                    links.append(canonical)
                    if depth < self.max_depth and canonical not in seen:
                        priority = (
                            self.frontier_priority(canonical, text, response.url, depth)
                            if self.frontier_priority is not None
                            else ()
                        )
                        frontier.append(
                            (priority, next_frontier_index, canonical, depth + 1)
                        )
                        next_frontier_index += 1
            queue.extend(frontier)
            queue.sort(key=lambda item: (item[0], item[1]))
            pages.append(
                CrawledPage(
                    requested,
                    response.url,
                    body,
                    tuple(dict.fromkeys(links)),
                    depth,
                    redirects,
                    tuple(dict.fromkeys(navigation_links)),
                )
            )
        return tuple(pages)

    def crawl(self, seeds: tuple[str, ...]) -> tuple[CrawledPage, ...]:
        session_factory = getattr(self.transport, "session", None)
        if callable(session_factory):
            with session_factory(timeout_seconds=self.timeout_seconds) as transport:
                return self._crawl(seeds, transport=cast(HttpTransport, transport))
        return self._crawl(seeds, transport=self.transport)
