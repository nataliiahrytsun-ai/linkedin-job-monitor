from types import TracebackType
from typing import ClassVar, Self

import pytest

from spikes import linkedin_spike


class FakeResponse:
    body = b"User-agent: *\nDisallow: /\n"
    status = 200
    url = linkedin_spike.ROBOTS_URL
    history: ClassVar[list[object]] = []


class FakeFetcherSession:
    requested_urls: ClassVar[list[str]] = []

    def __init__(self, **_: object) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def get(self, url: str) -> FakeResponse:
        self.requested_urls.append(url)
        assert url == linkedin_spike.ROBOTS_URL
        return FakeResponse()


def test_preflight_stops_without_requesting_target(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeFetcherSession.requested_urls = []
    monkeypatch.setattr(linkedin_spike, "FetcherSession", FakeFetcherSession)

    result = linkedin_spike.run_preflight(linkedin_spike.DEFAULT_URL, linkedin_spike.SpikeConfig())

    assert FakeFetcherSession.requested_urls == [linkedin_spike.ROBOTS_URL]
    assert result["target_requested"] is False
    assert result["classification"] == "Not feasible through compliant public access"
    assert result["request_count"] == 1
