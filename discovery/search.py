"""Structured search-provider boundary; discovery never parses result HTML."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlencode

from django.conf import settings


class SearchConfigurationError(RuntimeError):
    pass


class SearchProviderError(RuntimeError):
    pass


class TavilyRateLimitError(SearchProviderError):
    pass


class TavilyTimeoutError(SearchProviderError):
    pass


class TavilyHttpError(SearchProviderError):
    pass


class TavilyInvalidResponseError(SearchProviderError):
    pass


class TavilyResponseTooLargeError(SearchProviderError):
    pass


class TavilyEmptyResultsError(SearchProviderError):
    pass


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    description: str = ""
    score: float | None = None


class SearchProvider(Protocol):
    def search(self, query: str, *, limit: int = 5) -> tuple[SearchResult, ...]: ...


@dataclass(frozen=True, slots=True)
class StaticSearchProvider:
    """Deterministic offline provider for tests and local diagnostics."""

    results: tuple[SearchResult, ...] = ()

    def search(self, query: str, *, limit: int = 5) -> tuple[SearchResult, ...]:
        del query
        return self.results[:limit]


class _Response(Protocol):
    status: int
    headers: dict[object, object]
    body: bytes | bytearray | memoryview


class _Session(Protocol):
    def get(self, url: str) -> _Response: ...

    def post(self, url: str, **kwargs: Any) -> _Response: ...


def _bounded_query(query: str) -> str:
    bounded = " ".join(query.split()[:50])[:400]
    if not bounded:
        raise SearchProviderError("Search query is empty")
    return bounded


class TavilySearchProvider:
    """Production Tavily JSON provider; keyless access is diagnostic-only."""

    endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        *,
        api_key: str = "",
        allow_keyless: bool = False,
        timeout_seconds: float = 10.0,
        retries: int = 1,
        max_results: int = 6,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        self._api_key = api_key.strip()
        if not self._api_key and not allow_keyless:
            raise SearchConfigurationError(
                "SOURCE_DISCOVERY_TAVILY_API_KEY is required; enable the explicit "
                "keyless diagnostic mode only for bounded diagnostics"
            )
        self._allow_keyless = allow_keyless
        self.timeout_seconds = timeout_seconds
        self.retries = max(1, retries)
        self.max_results = max(1, min(max_results, 10))
        self.max_response_bytes = max_response_bytes

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "job-monitor-source-discovery/1.0",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        else:
            headers["X-Tavily-Access-Mode"] = "keyless"
        return headers

    def search(self, query: str, *, limit: int = 5) -> tuple[SearchResult, ...]:
        from scrapling.fetchers import FetcherSession

        result_limit = max(1, min(limit, self.max_results))
        payload = {
            "query": _bounded_query(query),
            "search_depth": "basic",
            "max_results": result_limit,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        try:
            with FetcherSession(
                http3=False,
                timeout=self.timeout_seconds,
                # Scrapling 0.4.8 treats retries as total attempts.
                retries=self.retries,
                retry_delay=0,
                follow_redirects=False,
                max_redirects=0,
                stealthy_headers=False,
                impersonate=None,
                proxies=None,
                proxy=None,
                proxy_auth=None,
                proxy_rotator=None,
                headers=self._headers(),
            ) as session:
                response = cast(_Session, session).post(self.endpoint, json=payload)
                # Materialize every Scrapling-backed value before closing the session.
                status = int(response.status)
                headers = {
                    str(key).lower(): str(value) for key, value in response.headers.items()
                }
                declared_length = headers.get("content-length", "")
                if declared_length.isdigit() and int(declared_length) > self.max_response_bytes:
                    raise TavilyResponseTooLargeError(
                        "Tavily response exceeded the configured size limit"
                    )
                body = bytes(response.body)
        except TavilyResponseTooLargeError:
            raise
        except Exception as error:
            if "timeout" in type(error).__name__.lower() or isinstance(error, TimeoutError):
                raise TavilyTimeoutError("Tavily search timed out") from None
            raise SearchProviderError("Tavily search is unavailable") from None
        if len(body) > self.max_response_bytes:
            raise TavilyResponseTooLargeError(
                "Tavily response exceeded the configured size limit"
            )
        if status in {429, 432, 433}:
            raise TavilyRateLimitError("Tavily search request limit was reached")
        if status != 200:
            raise TavilyHttpError(f"Tavily search returned HTTP {status}")
        try:
            payload_data = json.loads(body)
            raw_results = payload_data.get("results")
        except (AttributeError, TypeError, ValueError):
            raise TavilyInvalidResponseError("Tavily returned invalid JSON") from None
        if not isinstance(raw_results, list):
            raise TavilyInvalidResponseError("Tavily returned an invalid result list")
        results: list[SearchResult] = []
        for item in raw_results[:result_limit]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            raw_score = item.get("score")
            score = float(raw_score) if isinstance(raw_score, int | float) else None
            results.append(
                SearchResult(
                    title=str(item.get("title", "")),
                    url=item["url"],
                    description=str(item.get("content", "")),
                    score=score,
                )
            )
        return tuple(results)


class BraveSearchProvider:
    """Optional legacy JSON provider, selected explicitly and requiring a key."""

    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, *, api_key: str, timeout_seconds: float = 10.0) -> None:
        if not api_key.strip():
            raise SearchConfigurationError(
                "SOURCE_DISCOVERY_BRAVE_API_KEY is required when Brave is selected"
            )
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, *, limit: int = 5) -> tuple[SearchResult, ...]:
        from scrapling.fetchers import FetcherSession

        parameters = {"q": _bounded_query(query), "count": max(1, min(limit, 10))}
        url = f"{self.endpoint}?{urlencode(parameters)}"
        try:
            with FetcherSession(
                http3=False,
                timeout=self.timeout_seconds,
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
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                    "User-Agent": "job-monitor-source-discovery/1.0",
                },
            ) as session:
                response = cast(_Session, session).get(url)
                status = int(response.status)
                body = bytes(response.body)
        except Exception as error:
            raise SearchProviderError("Brave search request failed") from error
        if status != 200:
            raise SearchProviderError(f"Brave search returned HTTP {status}")
        try:
            payload = json.loads(body)
            raw_results = (payload.get("web") or {}).get("results") or []
        except (AttributeError, TypeError, ValueError) as error:
            raise SearchProviderError("Brave search returned an invalid response") from error
        if not isinstance(raw_results, list):
            raise SearchProviderError("Brave search returned an invalid result list")
        return tuple(
            SearchResult(
                title=str(item.get("title", "")),
                url=item["url"],
                description=str(item.get("description", "")),
            )
            for item in raw_results[:limit]
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        )


def configured_search_provider() -> SearchProvider:
    provider = settings.SOURCE_DISCOVERY_SEARCH_PROVIDER
    if provider == "tavily":
        return TavilySearchProvider(
            api_key=settings.SOURCE_DISCOVERY_TAVILY_API_KEY,
            allow_keyless=settings.SOURCE_DISCOVERY_TAVILY_KEYLESS_DIAGNOSTIC,
            timeout_seconds=settings.SOURCE_DISCOVERY_SEARCH_TIMEOUT_SECONDS,
            retries=settings.SOURCE_DISCOVERY_SEARCH_RETRIES,
            max_results=settings.SOURCE_DISCOVERY_SEARCH_MAX_RESULTS,
            max_response_bytes=settings.SOURCE_DISCOVERY_SEARCH_MAX_RESPONSE_BYTES,
        )
    if provider == "brave":
        return BraveSearchProvider(
            api_key=settings.SOURCE_DISCOVERY_BRAVE_API_KEY,
            timeout_seconds=settings.SOURCE_DISCOVERY_SEARCH_TIMEOUT_SECONDS,
        )
    raise SearchConfigurationError("Unsupported SOURCE_DISCOVERY_SEARCH_PROVIDER")
