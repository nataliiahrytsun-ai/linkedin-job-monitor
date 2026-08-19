from __future__ import annotations

from pathlib import Path

from scraping.sources.generic import extract_generic_candidates

FIXTURES = Path(__file__).parent / "fixtures" / "generic"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _precision_recall(expected: set[str], observed: set[str]) -> dict[str, float | int | str]:
    true_positive = len(expected & observed)
    false_positive = len(observed - expected)
    false_negative = len(expected - observed)
    precision_total = true_positive + false_positive
    recall_total = true_positive + false_negative
    precision = true_positive / precision_total if precision_total else "N/A"
    recall = true_positive / recall_total if recall_total else "N/A"
    return {
        "tp": true_positive,
        "fp": false_positive,
        "fn": false_negative,
        "precision": precision,
        "recall": recall,
    }


def test_real_structure_baseline_metrics() -> None:
    fixtures: list[tuple[str, str, str, set[str]]] = [
        (
            "real_sentry_careers",
            "https://sentry.io",
            fixture("real_sentry_careers.html"),
            {
                "https://sentry.io/careers/01e24f35-a936-4dd1-a700-31e872c50da8/",
                "https://sentry.io/careers/26c3535f-fcaa-4419-8272-43faa00582f1/",
                "https://sentry.io/careers/35193bfb-bb6b-4479-85be-0945e5b94a3e/",
                "https://sentry.io/careers/95d2eeab-291d-40ad-97a2-86b104f3c7ad/",
            },
        ),
        (
            "real_automattic_jobs",
            "https://automattic.com",
            fixture("real_automattic_jobs.html"),
            {
                "https://automattic.com/jobs/12345/",
                "https://automattic.com/jobs/12346/",
                "https://automattic.com/jobs/12347/?utm_source=careers",
            },
        ),
        (
            "real_notion_jobs",
            "https://www.notion.com",
            fixture("real_notion_jobs.html"),
            {
                "https://jobs.ashbyhq.com/notion/05e14247-17c4-4e98-9a13-53828a4e2f13",
                "https://jobs.ashbyhq.com/notion/b21fef72-4864-4a3e-a627-91557a0f8a36",
                "https://jobs.ashbyhq.com/notion/6ccbc30c-2de0-4395-af14-3641cd15961b",
                "https://jobs.ashbyhq.com/notion/297b4ece-765f-4eea-b1b8-46057cb6501f",
            },
        ),
        (
            "real_generic_openings",
            "https://www.example.com",
            fixture("real_generic_openings.html"),
            {
                "https://www.example.com/openings/123",
                "https://www.example.com/opportunities/456",
                "https://www.example.com/role/789",
            },
        ),
        (
            "real_noisy_corporate_page",
            "https://www.example.com",
            fixture("real_noisy_corporate_page.html"),
            {
                "https://www.example.com/roles/engineering-manager-1",
                "https://www.example.com/positions/senior-analyst-2",
            },
        ),
    ]

    overall_expected: set[str] = set()
    overall_observed: set[str] = set()
    fixture_metrics: list[dict[str, object]] = []

    for name, base_url, html, expected in fixtures:
        observed = {
            candidate.url
            for candidate in extract_generic_candidates(html, base_url=base_url)
        }
        metrics = _precision_recall(expected, observed)
        overall_expected |= expected
        overall_observed |= observed
        fixture_metrics.append({
            "fixture": name,
            "expected": expected,
            "observed": observed,
            "metrics": metrics,
        })
        assert observed == expected, (
            f"{name} baseline mismatch: expected={sorted(expected)} "
            f"observed={sorted(observed)}"
        )
        assert metrics["precision"] == 1.0, f"{name} precision not 1.0: {metrics}"
        assert metrics["recall"] == 1.0, f"{name} recall not 1.0: {metrics}"

    overall = _precision_recall(overall_expected, overall_observed)
    assert overall["precision"] == 1.0, overall
    assert overall["recall"] == 1.0, overall
    assert fixture_metrics
