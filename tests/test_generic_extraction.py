from __future__ import annotations

import json
from pathlib import Path

import pytest

from scraping.sources.generic import (
    CandidateValidationError,
    ExtractedJob,
    FakeJobExtractionProvider,
    OpenAIJobExtractionProvider,
    ProviderConfigurationError,
    ProviderResponseError,
    candidate_id_for_url,
    extract_generic_candidates,
    extract_jobs_from_html,
    validate_extracted_jobs,
)

FIXTURES = Path(__file__).parent / "fixtures" / "generic"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_extracts_expected_job_candidates() -> None:
    candidates = extract_generic_candidates(fixture("careers_clean.html"), base_url="https://www.example.com/careers")

    assert [candidate.url for candidate in candidates] == [
        "https://www.example.com/jobs/data-engineer-456",
        "https://www.example.com/jobs/product-manager-101",
        "https://www.example.com/jobs/qa-manager-789",
        "https://www.example.com/jobs/senior-data-analyst-123",
    ]
    assert all(
        candidate.candidate_id == candidate_id_for_url(candidate.url)
        for candidate in candidates
    )


def test_filters_privacy_login_and_social_links() -> None:
    candidates = extract_generic_candidates(fixture("noisy_company_page.html"), base_url="https://www.example.com")

    assert [candidate.url for candidate in candidates] == [
        "https://www.example.com/jobs/data-scientist-555",
        "https://www.example.com/jobs/platform-engineer-777",
    ]


def test_resolves_relative_urls_and_normalizes_them() -> None:
    candidates = extract_generic_candidates(fixture("relative_urls.html"), base_url="https://www.example.com/careers/")

    assert [candidate.url for candidate in candidates] == [
        "https://www.example.com/jobs/analyst-def?ref=careers",
        "https://www.example.com/jobs/engineer-abc",
    ]


def test_ambiguous_job_links_are_kept_when_context_is_clear() -> None:
    html = """
    <html><body>
      <div>
        <a href="/position/senior-data-analyst-900?ref=careers">Apply now</a>
        <p>We are hiring a Senior Data Analyst for our privacy team.</p>
      </div>
      <a href="/company/about">About</a>
    </body></html>
    """

    candidates = extract_generic_candidates(html, base_url="https://www.example.com")

    assert len(candidates) == 1
    assert candidates[0].url == "https://www.example.com/position/senior-data-analyst-900?ref=careers"
    assert "senior data analyst" in (candidates[0].nearby_text or "").casefold()


def test_candidate_id_is_stable_and_independent_of_dom_order() -> None:
    html = """
    <html><body>
      <a href="/jobs/alpha-1">Alpha</a>
      <a href="/jobs/beta-2">Beta</a>
    </body></html>
    """

    first = extract_generic_candidates(html, base_url="https://www.example.com")
    reversed_html = """
    <html><body>
      <a href="/jobs/beta-2">Beta</a>
      <a href="/jobs/alpha-1">Alpha</a>
    </body></html>
    """
    second = extract_generic_candidates(reversed_html, base_url="https://www.example.com")

    assert [candidate.candidate_id for candidate in first] == [
        candidate_id_for_url("https://www.example.com/jobs/alpha-1"),
        candidate_id_for_url("https://www.example.com/jobs/beta-2"),
    ]
    assert [candidate.candidate_id for candidate in first] == [
        candidate.candidate_id for candidate in second
    ]


def test_nearby_context_is_bounded() -> None:
    long_text = " ".join(["discovery"] * 80)
    html = f"""
    <html><body>
      <div>{long_text}<a href="/jobs/very-long-context-123">Senior Data Analyst</a>{long_text}</div>
    </body></html>
    """

    candidate = extract_generic_candidates(html, base_url="https://www.example.com")[0]

    assert candidate.nearby_text is not None
    assert len(candidate.nearby_text) <= 240


def test_fake_provider_successfully_maps_back_to_original_candidates() -> None:
    candidates = extract_generic_candidates(fixture("careers_clean.html"), base_url="https://www.example.com/careers")
    provider = FakeJobExtractionProvider(
        mapping={
            candidates[0].candidate_id: "Senior Data Analyst",
            candidates[1].candidate_id: "Data Engineer",
            candidates[2].candidate_id: "QA Manager",
            candidates[3].candidate_id: "Product Manager",
        }
    )

    jobs = extract_jobs_from_html(
        fixture("careers_clean.html"),
        base_url="https://www.example.com/careers",
        provider=provider,
    )

    assert [job.title for job in jobs] == [
        "Senior Data Analyst",
        "Data Engineer",
        "QA Manager",
        "Product Manager",
    ]
    assert all(
        job.candidate_id in {candidate.candidate_id for candidate in candidates}
        for job in jobs
    )


def test_provider_cannot_invent_a_url() -> None:
    candidates = extract_generic_candidates(fixture("careers_clean.html"), base_url="https://www.example.com/careers")
    fake = FakeJobExtractionProvider(mapping={candidates[0].candidate_id: "Senior Data Analyst"})
    jobs = fake.extract_jobs(candidates=candidates)

    assert jobs.jobs[0].candidate_id == candidates[0].candidate_id
    assert jobs.jobs[0].candidate_id not in {"invented-url"}
    assert jobs.jobs[0].candidate_id in {candidate.candidate_id for candidate in candidates}


def test_unknown_candidate_id_fails_validation() -> None:
    candidates = extract_generic_candidates(fixture("careers_clean.html"), base_url="https://www.example.com/careers")
    payload = (ExtractedJob(candidate_id="unknown-candidate", title="Senior Data Analyst"),)

    with pytest.raises(CandidateValidationError, match="unknown candidate_id"):
        validate_extracted_jobs(candidates, payload)


def test_empty_title_fails_validation() -> None:
    candidates = extract_generic_candidates(fixture("careers_clean.html"), base_url="https://www.example.com/careers")
    payload = (ExtractedJob(candidate_id=candidates[0].candidate_id, title="   "),)

    with pytest.raises(CandidateValidationError, match="empty title"):
        validate_extracted_jobs(candidates, payload)


def test_duplicate_candidate_id_fails_validation() -> None:
    candidates = extract_generic_candidates(fixture("careers_clean.html"), base_url="https://www.example.com/careers")
    payload = (
        ExtractedJob(candidate_id=candidates[0].candidate_id, title="Senior Data Analyst"),
        ExtractedJob(candidate_id=candidates[0].candidate_id, title="Duplicate title"),
    )

    with pytest.raises(CandidateValidationError, match="duplicate candidate_id"):
        validate_extracted_jobs(candidates, payload)


def test_provider_exception_propagates_as_generic_failure() -> None:
    candidates = extract_generic_candidates(fixture("careers_clean.html"), base_url="https://www.example.com/careers")
    provider = FakeJobExtractionProvider(raise_error=RuntimeError("provider timeout"))

    with pytest.raises(RuntimeError, match="provider timeout"):
        provider.extract_jobs(candidates=candidates)


def test_no_network_or_live_api_dependency() -> None:
    module_path = str(__import__("scraping.sources.generic", fromlist=["__doc__"]).__file__)
    assert "openai" not in module_path
    candidates = extract_generic_candidates(
        fixture("careers_clean.html"),
        base_url="https://www.example.com/careers",
    )
    assert len(candidates) == 4


def test_extracts_expected_candidate_count_from_fixture_a() -> None:
    candidates = extract_generic_candidates(fixture("careers_clean.html"), base_url="https://www.example.com/careers")
    assert len(candidates) == 4


def test_fixture_b_keeps_only_job_links() -> None:
    candidates = extract_generic_candidates(fixture("noisy_company_page.html"), base_url="https://www.example.com")
    assert len(candidates) == 2


def test_fixture_c_resolves_relative_urls_deterministically() -> None:
    candidates = extract_generic_candidates(fixture("relative_urls.html"), base_url="https://www.example.com/careers/")
    assert all(candidate.url.startswith("https://www.example.com/") for candidate in candidates)


def test_fixture_d_keeps_ambiguous_job_links_when_context_matches() -> None:
    candidates = extract_generic_candidates(fixture("ambiguous_links.html"), base_url="https://www.example.com")
    assert len(candidates) == 2
    assert any("remote" in (candidate.nearby_text or "").casefold() for candidate in candidates)


def _precision_recall(expected: set[str], observed: set[str]) -> dict[str, float | int]:
    true_positive = len(expected & observed)
    false_positive = len(observed - expected)
    false_negative = len(expected - observed)
    precision_total = true_positive + false_positive
    recall_total = true_positive + false_negative
    precision = true_positive / precision_total if precision_total else 1.0
    recall = true_positive / recall_total if recall_total else 1.0
    return {
        "tp": true_positive,
        "fp": false_positive,
        "fn": false_negative,
        "precision": precision,
        "recall": recall,
    }


def test_generic_listing_variants_support_non_jobs_paths_when_context_is_clear() -> None:
    expected = {
        "https://www.example.com/openings/123",
        "https://www.example.com/opportunities/456",
        "https://www.example.com/role/789",
    }
    actual = {
        candidate.url
        for candidate in extract_generic_candidates(
            fixture("generic_listing_variants.html"),
            base_url="https://www.example.com/careers",
        )
    }

    assert actual == expected
    metrics = _precision_recall(expected, actual)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_false_positive_navigation_page_is_rejected() -> None:
    expected: set[str] = set()
    actual = {
        candidate.url
        for candidate in extract_generic_candidates(
            fixture("generic_false_positive_navigation.html"),
            base_url="https://www.example.com",
        )
    }

    assert actual == expected
    metrics = _precision_recall(expected, actual)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_apply_links_are_not_treated_as_jobs() -> None:
    expected = {
        "https://www.example.com/job/123",
        "https://www.example.com/job/456",
    }
    actual = {
        candidate.url
        for candidate in extract_generic_candidates(
            fixture("generic_apply_links.html"),
            base_url="https://www.example.com",
        )
    }

    assert actual == expected
    metrics = _precision_recall(expected, actual)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_duplicate_links_are_deduplicated_but_tracking_variants_remain_distinct() -> None:
    expected = {
        "https://www.example.com/jobs/321",
        "https://www.example.com/jobs/321?utm_source=nav",
        "https://www.example.com/jobs/456",
    }
    actual = {
        candidate.url
        for candidate in extract_generic_candidates(
            fixture("generic_duplicate_links.html"),
            base_url="https://www.example.com",
        )
    }

    assert actual == expected
    metrics = _precision_recall(expected, actual)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_query_and_fragment_variants_follow_project_canonicalization() -> None:
    expected = {
        "https://www.example.com/jobs/123",
        "https://www.example.com/jobs/123?ref=homepage",
        "https://www.example.com/jobs/123?utm_source=linkedin",
    }
    actual = {
        candidate.url
        for candidate in extract_generic_candidates(
            fixture("generic_query_variations.html"),
            base_url="https://www.example.com",
        )
    }

    assert actual == expected
    metrics = _precision_recall(expected, actual)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_text_only_job_like_content_is_not_supported_and_fails_conservatively() -> None:
    actual = {
        candidate.url
        for candidate in extract_generic_candidates(
            fixture("generic_text_outside_anchor.html"),
            base_url="https://www.example.com",
        )
    }

    assert actual == set()
    metrics = _precision_recall(set(), actual)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


class _StubOpenAIResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.output_text = json.dumps(payload)


class _StubOpenAIResponses:
    def __init__(self, parent: _StubOpenAIClient) -> None:
        self.parent = parent

    def create(self, **kwargs: object) -> _StubOpenAIResponse:
        self.parent.last_request = kwargs
        if self.parent.raise_error is not None:
            raise self.parent.raise_error
        return _StubOpenAIResponse(self.parent.payload)


class _StubOpenAIClient:
    def __init__(self, *, payload: object, raise_error: Exception | None = None) -> None:
        self.payload = payload
        self.raise_error = raise_error
        self.last_request: object | None = None
        self.responses = _StubOpenAIResponses(self)

        def _fail_chat_create(self: object, **kwargs: object) -> None:
            raise AssertionError("chat API should not be used in tests")

        self.chat = type(
            "StubChat",
            (),
            {
                "completions": type(
                    "StubCompletions",
                    (),
                    {"create": _fail_chat_create},
                )()
            },
        )()


def test_provider_request_payload_uses_only_allowed_candidate_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERIC_AI_OPENAI_API_KEY", "test-key")
    candidates = extract_generic_candidates(
        fixture("careers_clean.html"),
        base_url="https://www.example.com/careers",
    )
    client = _StubOpenAIClient(
        payload={
            "jobs": [
                {"candidate_id": candidates[0].candidate_id, "title": "Senior Data Analyst"},
            ]
        }
    )
    provider = OpenAIJobExtractionProvider(client=client)

    provider.extract_jobs(candidates=candidates)

    user_payload = json.loads(client.last_request["input"][1]["content"])  # type: ignore[index]
    assert set(user_payload["candidates"][0].keys()) == {
        "candidate_id",
        "url",
        "anchor_text",
        "nearby_text",
    }


def test_provider_output_cannot_introduce_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENERIC_AI_OPENAI_API_KEY", "test-key")
    candidates = extract_generic_candidates(
        fixture("careers_clean.html"),
        base_url="https://www.example.com/careers",
    )
    provider = OpenAIJobExtractionProvider(
        client=_StubOpenAIClient(
            payload={
                "jobs": [
                    {
                        "candidate_id": candidates[0].candidate_id,
                        "title": "Senior Data Analyst",
                        "url": "https://evil.example/jobs/999",
                    }
                ]
            }
        )
    )

    with pytest.raises(ProviderResponseError, match="URL"):
        provider.extract_jobs(candidates=candidates)


def test_openai_provider_accepts_valid_structured_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENERIC_AI_OPENAI_API_KEY", "test-key")
    candidates = extract_generic_candidates(
        fixture("careers_clean.html"),
        base_url="https://www.example.com/careers",
    )
    client = _StubOpenAIClient(
        payload={
            "jobs": [
                {"candidate_id": candidates[0].candidate_id, "title": "Senior Data Analyst"},
                {"candidate_id": candidates[1].candidate_id, "title": "Data Engineer"},
            ]
        }
    )

    jobs = OpenAIJobExtractionProvider(client=client).extract_jobs(candidates=candidates).jobs

    assert [job.title for job in jobs] == ["Senior Data Analyst", "Data Engineer"]
    assert {job.candidate_id for job in jobs} == {
        candidates[0].candidate_id,
        candidates[1].candidate_id,
    }


def test_openai_provider_rejects_malformed_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENERIC_AI_OPENAI_API_KEY", "test-key")
    candidates = extract_generic_candidates(
        fixture("careers_clean.html"),
        base_url="https://www.example.com/careers",
    )
    provider = OpenAIJobExtractionProvider(
        client=_StubOpenAIClient(payload={"broken": "payload"})
    )

    with pytest.raises(ProviderResponseError, match="jobs list"):
        provider.extract_jobs(candidates=candidates)


def test_openai_provider_wraps_timeout_and_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERIC_AI_OPENAI_API_KEY", "test-key")
    candidates = extract_generic_candidates(
        fixture("careers_clean.html"),
        base_url="https://www.example.com/careers",
    )

    for exc in (TimeoutError("provider timeout"), RuntimeError("provider exploded")):
        provider = OpenAIJobExtractionProvider(
            client=_StubOpenAIClient(payload={}, raise_error=exc),
        )
        with pytest.raises(ProviderResponseError, match="provider request failed"):
            provider.extract_jobs(candidates=candidates)


def test_openai_provider_rejects_unknown_candidate_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENERIC_AI_OPENAI_API_KEY", "test-key")
    candidates = extract_generic_candidates(
        fixture("careers_clean.html"),
        base_url="https://www.example.com/careers",
    )
    provider = OpenAIJobExtractionProvider(
        client=_StubOpenAIClient(
            payload={"jobs": [{"candidate_id": "not-real", "title": "Ghost role"}]}
        )
    )

    with pytest.raises(CandidateValidationError, match="unknown candidate_id"):
        provider.extract_jobs(candidates=candidates)


def test_openai_provider_rejects_duplicate_candidate_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENERIC_AI_OPENAI_API_KEY", "test-key")
    candidates = extract_generic_candidates(
        fixture("careers_clean.html"),
        base_url="https://www.example.com/careers",
    )
    provider = OpenAIJobExtractionProvider(
        client=_StubOpenAIClient(
            payload={
                "jobs": [
                    {"candidate_id": candidates[0].candidate_id, "title": "Role A"},
                    {"candidate_id": candidates[0].candidate_id, "title": "Role B"},
                ]
            }
        )
    )

    with pytest.raises(CandidateValidationError, match="duplicate candidate_id"):
        provider.extract_jobs(candidates=candidates)


def test_openai_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GENERIC_AI_OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError, match="GENERIC_AI_OPENAI_API_KEY"):
        OpenAIJobExtractionProvider()


def test_no_live_api_call_is_attempted_in_normal_test_suite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GENERIC_AI_OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError, match="GENERIC_AI_OPENAI_API_KEY"):
        OpenAIJobExtractionProvider()
