import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from spikes import linkedin_live_pagination as live

PAGE_1_URL = "https://www.linkedin.com/jobs/search?page=1"
PAGE_2_URL = "https://www.linkedin.com/jobs/search?page=2"
PAGE_3_URL = "https://www.linkedin.com/jobs/search?page=3"
PAGE_4_URL = "https://www.linkedin.com/jobs/search?page=4"
PAGE_5_URL = "https://www.linkedin.com/jobs/search?page=5"


class FakeRedirect:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        html: str,
        status: int = 200,
        history: Sequence[FakeRedirect] = (),
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.url = url
        self.body = html.encode()
        self.status = status
        self.history = history
        self.headers = headers or {}


class FakeFetcher:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.requested_urls: list[str] = []

    def get(self, url: str) -> live.ResponseLike:
        self.requested_urls.append(url)
        return self.responses[url]


def jobs_html(job_ids: Sequence[str], next_url: str | None = None, marker: str = "") -> str:
    cards = "".join(
        f"""
        <li class="jobs-search-results__list-item" data-job-id="{job_id}">
          <a class="base-card__full-link" href="/jobs/view/test-{job_id}">
            <h3 class="base-search-card__title">Job {job_id}</h3>
          </a>
        </li>
        """
        for job_id in job_ids
    )
    next_link = f'<a rel="next" href="{next_url}">Next</a>' if next_url else ""
    return f"<html><body>{marker}<ul>{cards}</ul>{next_link}</body></html>"


def write_preflight(
    path: Path, *, target_url: str = PAGE_1_URL, target_allowed: bool
) -> None:
    path.write_text(
        json.dumps(
            {
                "target_url": target_url,
                "target_requested": False,
                "robots": {
                    "url": "https://www.linkedin.com/robots.txt",
                    "status": 200,
                    "redirect_count": 0,
                    "target_allowed": target_allowed,
                },
            }
        ),
        encoding="utf-8",
    )


def run(
    responses: dict[str, FakeResponse],
    *,
    config: live.LivePaginationConfig | None = None,
) -> tuple[dict[str, object], FakeFetcher, list[float]]:
    fetcher = FakeFetcher(responses)
    sleeps: list[float] = []
    result = live.run_live_pagination(
        start_url=PAGE_1_URL,
        fetcher=fetcher,
        config=config,
        sleep=sleeps.append,
        clock=lambda: 0.0,
    )
    return result, fetcher, sleeps


def test_without_confirmation_makes_no_requests(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class ForbiddenSession:
        def __init__(self, **_: object) -> None:
            raise AssertionError("FetcherSession must not be created")

    monkeypatch.setattr(live, "FetcherSession", ForbiddenSession)
    preflight_path = tmp_path / "preflight.json"
    write_preflight(preflight_path, target_allowed=False)

    exit_code = live.main(
        [
            "--url",
            PAGE_1_URL,
            "--robots-preflight-result",
            str(preflight_path),
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert result["stop_reason"] == "confirmation_required"
    assert result["requests"] == 0
    assert result["requested_urls"] == []
    assert result["robots_preflight"]["target_allowed"] is False
    assert result["robots_warning"] == "robots.txt disallows the target for ordinary operation"


def test_invalid_robots_result_makes_no_requests(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class ForbiddenSession:
        def __init__(self, **_: object) -> None:
            raise AssertionError("FetcherSession must not be created")

    monkeypatch.setattr(live, "FetcherSession", ForbiddenSession)

    exit_code = live.main(["--url", PAGE_1_URL, "--confirm-live-test"])
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert result["stop_reason"] == "robots_preflight_invalid"
    assert result["requests"] == 0
    assert result["requested_urls"] == []


def test_mismatched_robots_target_makes_no_requests(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class ForbiddenSession:
        def __init__(self, **_: object) -> None:
            raise AssertionError("FetcherSession must not be created")

    monkeypatch.setattr(live, "FetcherSession", ForbiddenSession)
    preflight_path = tmp_path / "preflight.json"
    write_preflight(preflight_path, target_url=PAGE_2_URL, target_allowed=False)

    exit_code = live.main(
        [
            "--url",
            PAGE_1_URL,
            "--confirm-live-test",
            "--robots-preflight-result",
            str(preflight_path),
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert result["stop_reason"] == "robots_preflight_invalid"
    assert result["requests"] == 0
    assert result["requested_urls"] == []


@pytest.mark.parametrize(
    ("max_pages", "max_requests", "request_delay_seconds"),
    [
        (5, 4, 2.0),
        (4, 5, 2.0),
        (4, 4, 1.99),
    ],
)
def test_hard_limits_cannot_be_relaxed(
    max_pages: int, max_requests: int, request_delay_seconds: float
) -> None:
    with pytest.raises(ValueError):
        live.LivePaginationConfig(
            max_pages=max_pages,
            max_requests=max_requests,
            request_delay_seconds=request_delay_seconds,
        )


def test_three_distinct_pages_are_fetched_sequentially() -> None:
    result, fetcher, sleeps = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL, html=jobs_html(["1001"], PAGE_2_URL)
            ),
            PAGE_2_URL: FakeResponse(
                url=PAGE_2_URL, html=jobs_html(["1002"], PAGE_3_URL)
            ),
            PAGE_3_URL: FakeResponse(url=PAGE_3_URL, html=jobs_html(["1003"])),
        }
    )

    assert result["found_job_ids"] == ["1001", "1002", "1003"]
    assert result["stop_reason"] == "no_next_page"
    assert result["pages"] == 3
    assert result["requests"] == 3
    assert result["http_statuses"] == [200, 200, 200]
    assert fetcher.requested_urls == [PAGE_1_URL, PAGE_2_URL, PAGE_3_URL]
    assert sleeps == [2.0, 2.0]


def test_four_page_limit_prevents_a_fifth_request() -> None:
    result, fetcher, sleeps = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL, html=jobs_html(["1001"], PAGE_2_URL)
            ),
            PAGE_2_URL: FakeResponse(
                url=PAGE_2_URL, html=jobs_html(["1002"], PAGE_3_URL)
            ),
            PAGE_3_URL: FakeResponse(
                url=PAGE_3_URL, html=jobs_html(["1003"], PAGE_4_URL)
            ),
            PAGE_4_URL: FakeResponse(
                url=PAGE_4_URL, html=jobs_html(["1004"], PAGE_5_URL)
            ),
        }
    )

    assert result["found_job_ids"] == ["1001", "1002", "1003", "1004"]
    assert result["stop_reason"] == "max_pages"
    assert result["pages"] == 4
    assert result["requests"] == 4
    assert fetcher.requested_urls == [PAGE_1_URL, PAGE_2_URL, PAGE_3_URL, PAGE_4_URL]
    assert PAGE_5_URL not in fetcher.requested_urls
    assert sleeps == [2.0, 2.0, 2.0]


def test_stops_when_second_page_has_no_new_ids() -> None:
    result, fetcher, _ = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL, html=jobs_html(["1001"], PAGE_2_URL)
            ),
            PAGE_2_URL: FakeResponse(url=PAGE_2_URL, html=jobs_html(["1001"])),
        }
    )

    assert result["stop_reason"] == "no_new_job_ids"
    assert fetcher.requested_urls == [PAGE_1_URL, PAGE_2_URL]


def test_stops_before_requesting_repeated_url() -> None:
    result, fetcher, _ = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL, html=jobs_html(["1001"], PAGE_1_URL)
            )
        }
    )

    assert result["stop_reason"] == "repeated_url"
    assert result["requests"] == 1
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_stops_on_identical_content() -> None:
    repeated_html = jobs_html(["1001"], PAGE_2_URL)
    result, fetcher, _ = run(
        {
            PAGE_1_URL: FakeResponse(url=PAGE_1_URL, html=repeated_html),
            PAGE_2_URL: FakeResponse(url=PAGE_2_URL, html=repeated_html),
        }
    )

    assert result["stop_reason"] == "repeated_content"
    assert fetcher.requested_urls == [PAGE_1_URL, PAGE_2_URL]


@pytest.mark.parametrize(
    "marker",
    [
        "<script>const captchaState = 'security verification';</script>",
        '<script src="https://static.example.test/captcha.js"></script>',
        "<!-- captcha challenge -->",
    ],
)
def test_incidental_captcha_text_is_not_classified_as_captcha(marker: str) -> None:
    result, fetcher, _ = run(
        {PAGE_1_URL: FakeResponse(url=PAGE_1_URL, html=jobs_html([], marker=marker))}
    )

    assert result["stop_reason"] == "no_new_job_ids"
    assert result["block_reason"] is None
    assert result["block_evidence"] is None
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_job_cards_with_incidental_captcha_text_are_processed() -> None:
    marker = "<script>window.captchaResource = '/captcha.js';</script>"
    result, fetcher, _ = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL,
                html=jobs_html(["1001"], marker=marker),
            )
        }
    )

    assert result["found_job_ids"] == ["1001"]
    assert result["stop_reason"] == "no_next_page"
    assert result["block_reason"] is None
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_hidden_captcha_container_is_not_sufficient_evidence() -> None:
    marker = '<div class="captcha-challenge" style="display: none">CAPTCHA</div>'
    result, fetcher, _ = run(
        {PAGE_1_URL: FakeResponse(url=PAGE_1_URL, html=jobs_html([], marker=marker))}
    )

    assert result["stop_reason"] == "no_new_job_ids"
    assert result["block_reason"] is None
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_hidden_security_verification_text_is_not_sufficient_evidence() -> None:
    marker = "<main><div hidden>Security Verification</div><h1>Public jobs</h1></main>"
    result, fetcher, _ = run(
        {PAGE_1_URL: FakeResponse(url=PAGE_1_URL, html=jobs_html([], marker=marker))}
    )

    assert result["stop_reason"] == "no_new_job_ids"
    assert result["block_reason"] is None
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_stops_when_there_is_no_next_page() -> None:
    result, fetcher, _ = run(
        {PAGE_1_URL: FakeResponse(url=PAGE_1_URL, html=jobs_html(["1001"]))}
    )

    assert result["stop_reason"] == "no_next_page"
    assert result["requests"] == 1
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_stops_at_max_pages() -> None:
    result, fetcher, _ = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL, html=jobs_html(["1001"], PAGE_2_URL)
            )
        },
        config=live.LivePaginationConfig(max_pages=1),
    )

    assert result["stop_reason"] == "max_pages"
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_stops_at_max_requests() -> None:
    result, fetcher, _ = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL, html=jobs_html(["1001"], PAGE_2_URL)
            )
        },
        config=live.LivePaginationConfig(max_requests=1),
    )

    assert result["stop_reason"] == "max_requests"
    assert fetcher.requested_urls == [PAGE_1_URL]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_blocking_status_stops_without_another_request(status: int) -> None:
    result, fetcher, sleeps = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL,
                html=jobs_html(["1001"], PAGE_2_URL),
                status=status,
            )
        }
    )

    assert result["stop_reason"] == f"http_{status}"
    assert result["requests"] == 1
    assert fetcher.requested_urls == [PAGE_1_URL]
    assert sleeps == []


def test_other_http_error_stops_without_another_request() -> None:
    result, fetcher, _ = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL,
                html=jobs_html(["1001"], PAGE_2_URL),
                status=503,
            )
        }
    )

    assert result["stop_reason"] == "http_error_503"
    assert result["requests"] == 1
    assert fetcher.requested_urls == [PAGE_1_URL]


@pytest.mark.parametrize("marker", ["login", "authwall", "checkpoint"])
def test_blocking_redirect_stops_without_another_request(marker: str) -> None:
    blocked_url = f"https://www.linkedin.com/{marker}/blocked"
    result, fetcher, _ = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL,
                html=jobs_html(["1001"], PAGE_2_URL),
                status=302,
                headers={"Location": blocked_url},
            )
        }
    )

    assert result["stop_reason"] == f"redirect_{marker}"
    assert result["redirects"] == [[blocked_url]]
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_access_denied_body_stops_without_another_request() -> None:
    result, fetcher, _ = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL, html=jobs_html([], marker="Access Denied")
            )
        }
    )

    assert result["stop_reason"] == "access_denied"
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_visible_captcha_form_stops_with_safe_evidence() -> None:
    html = jobs_html(
        [],
        PAGE_2_URL,
        marker='<form id="captcha-form"><input name="captcha-token"></form>',
    )
    result, fetcher, _ = run({PAGE_1_URL: FakeResponse(url=PAGE_1_URL, html=html)})

    assert result["stop_reason"] == "captcha"
    assert result["block_reason"] == "captcha"
    assert result["block_evidence"] == "visible CAPTCHA form"
    assert result["requests"] == 1
    assert fetcher.requested_urls == [PAGE_1_URL]


@pytest.mark.parametrize(
    ("marker", "evidence"),
    [
        (
            '<iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>',
            "visible CAPTCHA iframe or provider challenge",
        ),
        (
            '<div class="captcha-challenge">Complete the challenge</div>',
            "visible CAPTCHA challenge container",
        ),
    ],
)
def test_visible_captcha_iframe_or_challenge_stops(
    marker: str, evidence: str
) -> None:
    html = jobs_html([], PAGE_2_URL, marker=marker)
    result, fetcher, _ = run({PAGE_1_URL: FakeResponse(url=PAGE_1_URL, html=html)})

    assert result["stop_reason"] == "captcha"
    assert result["block_reason"] == "captcha"
    assert result["block_evidence"] == evidence
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_security_verification_page_without_jobs_is_captcha() -> None:
    html = (
        "<html><head><title>Security Verification</title></head>"
        "<body><main><h1>Verify you are human</h1></main></body></html>"
    )
    result, fetcher, _ = run({PAGE_1_URL: FakeResponse(url=PAGE_1_URL, html=html)})

    assert result["stop_reason"] == "captcha"
    assert result["block_reason"] == "captcha"
    assert result["block_evidence"] == "page title explicitly requests security verification"
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_visible_main_security_verification_text_is_captcha() -> None:
    html = "<html><body><main><h1>Verify you are human</h1></main></body></html>"
    result, fetcher, _ = run({PAGE_1_URL: FakeResponse(url=PAGE_1_URL, html=html)})

    assert result["stop_reason"] == "captcha"
    assert result["block_reason"] == "captcha"
    assert result["block_evidence"] == "visible main text requests security verification"
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_consent_interstitial_stops_without_another_request() -> None:
    result, fetcher, _ = run(
        {
            PAGE_1_URL: FakeResponse(
                url=PAGE_1_URL,
                html=jobs_html([], marker="Consent interstitial"),
            )
        }
    )

    assert result["stop_reason"] == "consent_interstitial"
    assert fetcher.requested_urls == [PAGE_1_URL]


def test_robots_warning_with_confirmation_allows_four_fake_pages(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    responses = {
        PAGE_1_URL: FakeResponse(
            url=PAGE_1_URL, html=jobs_html(["1001"], PAGE_2_URL)
        ),
        PAGE_2_URL: FakeResponse(
            url=PAGE_2_URL, html=jobs_html(["1002"], PAGE_3_URL)
        ),
        PAGE_3_URL: FakeResponse(
            url=PAGE_3_URL, html=jobs_html(["1003"], PAGE_4_URL)
        ),
        PAGE_4_URL: FakeResponse(
            url=PAGE_4_URL, html=jobs_html(["1004"], PAGE_5_URL)
        ),
    }

    class FakeSession:
        instances = 0

        def __init__(self, **_: object) -> None:
            type(self).instances += 1

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
            return responses[url]

    preflight_path = tmp_path / "preflight.json"
    write_preflight(preflight_path, target_allowed=False)
    monkeypatch.setattr(live, "FetcherSession", FakeSession)
    sleeps: list[float] = []

    exit_code = live.main(
        [
            "--url",
            PAGE_1_URL,
            "--confirm-live-test",
            "--robots-preflight-result",
            str(preflight_path),
        ],
        sleep=sleeps.append,
        clock=lambda: 0.0,
    )
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert result["stop_reason"] == "max_pages"
    assert result["requests"] == 4
    assert result["requested_urls"] == [PAGE_1_URL, PAGE_2_URL, PAGE_3_URL, PAGE_4_URL]
    assert result["robots_preflight"]["target_allowed"] is False
    assert result["robots_warning"] == "robots.txt disallows the target for ordinary operation"
    assert FakeSession.instances == 4
    assert sleeps == [2.0, 2.0, 2.0]


def test_cli_uses_plain_bounded_fetcher_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    captured_options: dict[str, object] = {}

    class FakeSession:
        instances = 0

        def __init__(self, **options: object) -> None:
            captured_options.update(options)
            type(self).instances += 1

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
            return FakeResponse(url=url, html=jobs_html(["1001"]))

    approval_path = tmp_path / "preflight.json"
    write_preflight(approval_path, target_allowed=True)
    monkeypatch.setattr(live, "FetcherSession", FakeSession)

    exit_code = live.main(
        [
            "--url",
            PAGE_1_URL,
            "--confirm-live-test",
            "--robots-preflight-result",
            str(approval_path),
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert result["requests"] == 1
    assert result["robots_warning"] is None
    assert FakeSession.instances == 1
    assert captured_options == {
        "http3": False,
        "timeout": 20.0,
        "retries": 1,
        "retry_delay": 0,
        "follow_redirects": False,
        "max_redirects": 0,
        "stealthy_headers": False,
        "impersonate": None,
        "proxies": None,
        "proxy": None,
        "proxy_auth": None,
        "proxy_rotator": None,
        "headers": {"User-Agent": live.USER_AGENT},
    }


def test_plain_fetcher_uses_a_fresh_session_for_each_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[object] = []

    class FakeSession:
        def __init__(self, **_: object) -> None:
            instances.append(self)

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
            return FakeResponse(url=url, html=jobs_html(["1001"]))

    monkeypatch.setattr(live, "FetcherSession", FakeSession)
    fetcher = live.PlainSessionFetcher(live.LivePaginationConfig())

    fetcher.get(PAGE_1_URL)
    fetcher.get(PAGE_2_URL)

    assert len(instances) == 2
    assert instances[0] is not instances[1]
