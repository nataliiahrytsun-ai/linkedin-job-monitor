"""Compliant live preflight for Milestone 1.

The runner fetches LinkedIn's robots.txt first and refuses to request the target
when the configured user agent is disallowed. It never logs in, supplies cookies,
uses proxies, or invokes stealth/browser features.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from dataclasses import asdict, dataclass
from importlib.metadata import version

from protego import Protego
from scrapling.fetchers import FetcherSession

DEFAULT_URL = (
    "https://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide"
    "?f_C=16691%2C30242966&trk=job-results_see-all-jobs-link"
    "&currentJobId=4434981246&position=39&pageNum=0"
)
ROBOTS_URL = "https://www.linkedin.com/robots.txt"
USER_AGENT = "linkedin-job-monitor-m1-spike"


@dataclass(frozen=True, slots=True)
class SpikeConfig:
    timeout_seconds: float = 20.0
    request_delay_seconds: float = 2.0
    max_pages: int = 2
    max_requests: int = 3
    concurrency: int = 1
    max_attempts_per_request: int = 1


def run_preflight(target_url: str, config: SpikeConfig) -> dict[str, object]:
    started = time.perf_counter()
    timings: list[float] = []
    with FetcherSession(
        timeout=config.timeout_seconds,
        retries=config.max_attempts_per_request,
        retry_delay=1,
        follow_redirects="safe",
        stealthy_headers=False,
        impersonate=None,
        headers={"User-Agent": USER_AGENT},
    ) as session:
        request_started = time.perf_counter()
        robots = session.get(ROBOTS_URL)
        timings.append(time.perf_counter() - request_started)

    robots_text = bytes(robots.body).decode("utf-8", errors="replace")
    allowed = Protego.parse(robots_text).can_fetch(target_url, USER_AGENT)
    result: dict[str, object] = {
        "classification": (
            "Not verified" if allowed else "Not feasible through compliant public access"
        ),
        "target_url": target_url,
        "target_requested": False,
        "robots": {
            "url": str(robots.url),
            "status": robots.status,
            "redirect_count": len(robots.history),
            "target_allowed": allowed,
        },
        "fetchers_tested": ["FetcherSession (HTTP), robots.txt preflight only"],
        "request_count": 1,
        "request_timings_seconds": [round(value, 3) for value in timings],
        "duration_seconds": round(time.perf_counter() - started, 3),
        "configuration": asdict(config),
        "python_version": platform.python_version(),
        "scrapling_version": version("scrapling"),
    }
    if not allowed:
        result["stop_reason"] = "robots.txt disallows the target for this user agent"
    else:
        result["stop_reason"] = "preflight only; target fetch requires a separately reviewed run"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    result = run_preflight(args.url, SpikeConfig())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 2 if result["robots"]["target_allowed"] is False else 0  # type: ignore[index]


if __name__ == "__main__":
    raise SystemExit(main())

