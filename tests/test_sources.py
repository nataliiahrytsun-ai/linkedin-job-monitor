from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import Literal

import pytest

from scraping.sources.base import SourceBatch, SourceError
from scraping.sources.darwinbox import DarwinboxSourceAdapter
from scraping.sources.dreamjobs import DreamJobsSourceAdapter
from scraping.sources.fixture import FixtureSourceAdapter
from scraping.sources.generic import (
    FakeJobExtractionProvider,
    GenericSourceAdapter,
    candidate_id_for_url,
)
from scraping.sources.jazzhr import JazzHRSourceAdapter
from scraping.sources.lever import LeverSourceAdapter
from scraping.sources.registry import (
    UnknownSourceError,
    executable_source_keys,
    get_source_adapter,
    registered_source_keys,
    source_unavailability_message,
    user_deletable_source_keys,
    user_selectable_source_keys,
    user_visible_source_keys,
)
from scraping.sources.zoho_recruit import ZohoRecruitSourceAdapter

GENERIC_FIXTURES = Path(__file__).parent / "fixtures" / "generic"


@dataclass
class CompanyStub:
    source: str
    source_jobs_url: str | None = None


def test_source_batch_is_immutable_and_validates_request_count() -> None:
    batch = SourceBatch(records=({"source": "fixture"},), requests_made=0)

    assert batch.records == ({"source": "fixture"},)
    assert batch.requests_made == 0
    with pytest.raises(FrozenInstanceError):
        batch.requests_made = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="non-negative int"):
        SourceBatch(records=(), requests_made=-1)
    with pytest.raises(ValueError, match="excluding bool"):
        SourceBatch(records=(), requests_made=True)


def test_registry_normalizes_source_and_selects_fixture_adapter() -> None:
    adapter = get_source_adapter(CompanyStub(source="  FiXtUrE  "))

    assert isinstance(adapter, FixtureSourceAdapter)


def test_registry_normalizes_source_and_selects_lever_adapter() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  LeVeR  ",
            source_jobs_url="https://jobs.lever.co/olo",
        )
    )

    assert isinstance(adapter, LeverSourceAdapter)


def test_registry_keeps_darwinbox_registered_visible_selectable_and_executable() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  DaRwInBoX  ",
            source_jobs_url="https://tenant.darwinbox.com/ms/candidate/careers",
        )
    )

    assert isinstance(adapter, DarwinboxSourceAdapter)
    assert "darwinbox" in registered_source_keys()
    assert "darwinbox" in user_visible_source_keys()
    assert "darwinbox" in user_selectable_source_keys()
    assert "darwinbox" in executable_source_keys()
    assert source_unavailability_message(" DaRwInBoX ") is None


def test_registry_keeps_jazzhr_registered_visible_selectable_and_executable() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  JaZzHr  ",
            source_jobs_url="https://example.applytojob.com/apply",
        )
    )

    assert isinstance(adapter, JazzHRSourceAdapter)
    assert "jazzhr" in registered_source_keys()
    assert "jazzhr" in user_visible_source_keys()
    assert "jazzhr" in user_selectable_source_keys()
    assert "jazzhr" in executable_source_keys()
    assert source_unavailability_message(" JaZzHr ") is None


def test_registry_keeps_dreamjobs_registered_visible_selectable_and_executable() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  DrEaMjObS  ",
            source_jobs_url="https://careers.datasentics.com/jobs",
        )
    )

    assert isinstance(adapter, DreamJobsSourceAdapter)
    assert "dreamjobs" in registered_source_keys()
    assert "dreamjobs" in user_visible_source_keys()
    assert "dreamjobs" in user_selectable_source_keys()
    assert "dreamjobs" in executable_source_keys()
    assert source_unavailability_message(" DrEaMjObS ") is None


def test_registry_distinguishes_registered_and_user_selectable_sources() -> None:
    registered_keys = registered_source_keys()
    visible_keys = user_visible_source_keys()
    selectable_keys = user_selectable_source_keys()
    executable_keys = executable_source_keys()

    assert isinstance(registered_keys, tuple)
    assert isinstance(visible_keys, tuple)
    assert isinstance(selectable_keys, tuple)
    assert isinstance(executable_keys, tuple)
    assert "fixture" in registered_keys
    assert "fixture" in executable_keys
    assert "fixture" not in visible_keys
    assert "fixture" not in selectable_keys
    assert "darwinbox" in registered_keys
    assert "darwinbox" in visible_keys
    assert "darwinbox" in selectable_keys
    assert "darwinbox" in executable_keys
    assert "lever" in registered_keys
    assert "lever" in visible_keys
    assert "lever" in selectable_keys
    assert "lever" in executable_keys
    assert "jazzhr" in registered_keys
    assert "jazzhr" in visible_keys
    assert "jazzhr" in selectable_keys
    assert "jazzhr" in executable_keys
    assert "dreamjobs" in registered_keys
    assert "dreamjobs" in visible_keys
    assert "dreamjobs" in selectable_keys
    assert "dreamjobs" in executable_keys
    assert set(selectable_keys) <= set(registered_keys)
    assert set(executable_keys) <= set(registered_keys)


def test_linkedin_is_visible_but_not_registered_selectable_or_executable() -> None:
    assert "linkedin" in user_visible_source_keys()
    assert "linkedin" not in registered_source_keys()
    assert "linkedin" not in user_selectable_source_keys()
    assert "linkedin" not in executable_source_keys()
    assert source_unavailability_message(" LiNkEdIn ") == (
        "Technical adapter ready · Production disabled · "
        "Requires approved LinkedIn access"
    )

    with pytest.raises(UnknownSourceError, match="linkedin"):
        get_source_adapter(
            CompanyStub(
                source="linkedin",
                source_jobs_url="https://www.linkedin.com/jobs/example-jobs",
            )
        )


def test_registry_keeps_generic_hidden_but_registered_for_explicit_source() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source=" Generic ",
            source_jobs_url="https://example.com/careers",
        )
    )

    assert isinstance(adapter, GenericSourceAdapter)
    assert "generic" in registered_source_keys()
    assert "generic" in executable_source_keys()
    assert "generic" not in user_visible_source_keys()
    assert "generic" not in user_selectable_source_keys()
    assert "generic" in user_deletable_source_keys()
    assert "fixture" not in user_deletable_source_keys()
    assert "zoho_recruit" in user_deletable_source_keys()


def test_registry_keeps_zoho_recruit_registered_selectable_and_executable() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source=" Zoho_Recruit ",
            source_jobs_url="https://jobs.example.com/jobs/Careers",
        )
    )

    assert isinstance(adapter, ZohoRecruitSourceAdapter)
    assert "zoho_recruit" in registered_source_keys()
    assert "zoho_recruit" in user_visible_source_keys()
    assert "zoho_recruit" in user_selectable_source_keys()
    assert "zoho_recruit" in executable_source_keys()


def test_generic_source_adapter_returns_valid_batch_from_minimal_fixture() -> None:
    job_url = "https://example.com/jobs/123"
    candidate_id = candidate_id_for_url(job_url)

    class DummyResponse:
        status = 200
        body = (
            b"<html><body><a href='https://example.com/jobs/123'>"
            b"Senior Analyst</a></body></html>"
        )

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(
        provider=FakeJobExtractionProvider(mapping={candidate_id: "Senior Analyst"}),
        session_factory=lambda timeout_seconds: DummySession(),
    )
    batch = adapter.fetch(
        company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers")
    )

    assert batch.requests_made == 1
    assert batch.records == (
        {
            "source": "generic",
            "source_job_id": candidate_id,
            "title": "Senior Analyst",
            "source_job_url": job_url,
        },
    )


def test_generic_source_adapter_uses_deterministic_jobs_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GENERIC_AI_OPENAI_API_KEY", raising=False)
    body = (GENERIC_FIXTURES / "abylon_careers.html").read_bytes()

    class DummyResponse:
        status = 200

        def __init__(self) -> None:
            self.body = body

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://abylon.io/career/"
            return DummyResponse()

    adapter = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: DummySession(),
    )
    batch = adapter.fetch(
        company=CompanyStub(source="generic", source_jobs_url="https://abylon.io/career/")
    )

    expected_titles = {
        "https://abylon.io/career/ai-solutions-architect-enterprise-ai/": (
            "AI Solutions Architect"
        ),
        "https://abylon.io/career/data-platform-engineer-azure-databricks-devops/": (
            "Data Platform Engineer"
        ),
        "https://abylon.io/career/junior-business-analyst-london-abylon/": (
            "Junior Business Analyst — London"
        ),
        "https://abylon.io/career/senior-data-engineer/": "Senior Data Engineer",
    }
    assert batch.requests_made == 1
    assert len(batch.records) == 4
    assert {
        record["source_job_url"]: record["title"] for record in batch.records
    } == expected_titles
    assert all(record["source"] == "generic" for record in batch.records)
    assert all(
        record["source_job_id"] == candidate_id_for_url(str(record["source_job_url"]))
        for record in batch.records
    )


def test_generic_source_adapter_uses_safe_url_slug_title_fallback() -> None:
    job_url = "https://example.com/career/senior-data-engineer/"

    class DummyResponse:
        status = 200
        body = b"<html><body><a href='/career/senior-data-engineer/'>Apply now</a></body></html>"

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    batch = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: DummySession(),
    ).fetch(company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers"))

    assert batch.records == (
        {
            "source": "generic",
            "source_job_id": candidate_id_for_url(job_url),
            "title": "Senior Data Engineer",
            "source_job_url": job_url,
        },
    )


def test_generic_source_adapter_rejects_candidate_without_safe_title() -> None:
    class DummyResponse:
        status = 200
        body = b"<html><body><a href='/jobs/12345'>Apply now</a></body></html>"

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(session_factory=lambda timeout_seconds: DummySession())

    with pytest.raises(SourceError, match="no validated jobs"):
        adapter.fetch(
            company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers")
        )


def test_generic_source_adapter_fails_closed_on_http_error() -> None:
    class DummyResponse:
        status = 503
        body = b"<html><body>Service unavailable</body></html>"

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(session_factory=lambda timeout_seconds: DummySession())
    with pytest.raises(SourceError, match="HTTP 503"):
        adapter.fetch(company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers"))


def test_generic_source_adapter_fails_closed_when_no_candidates_are_found() -> None:
    class DummyResponse:
        status = 200
        body = b"<html><body><a href='/privacy'>Privacy</a></body></html>"

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(session_factory=lambda timeout_seconds: DummySession())
    with pytest.raises(SourceError, match="no public job-like candidates"):
        adapter.fetch(company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers"))


def test_generic_source_adapter_fails_closed_on_pagination_signal() -> None:
    class DummyResponse:
        status = 200
        body = (
            b"<html><body>"
            b"<a href='https://example.com/jobs/123'>Role</a>"
            b"<a rel='next' href='https://example.com/careers?page=2'>Next</a>"
            b"</body></html>"
        )

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(session_factory=lambda timeout_seconds: DummySession())
    with pytest.raises(SourceError, match="pagination"):
        adapter.fetch(company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers"))


def test_generic_source_adapter_fails_closed_when_provider_returns_no_validated_jobs() -> None:
    class DummyResponse:
        status = 200
        body = (
            b"<html><body><a href='https://example.com/jobs/123'>"
            b"Senior Analyst</a></body></html>"
        )

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(
        provider=FakeJobExtractionProvider(mapping={}),
        session_factory=lambda timeout_seconds: DummySession(),
    )
    with pytest.raises(SourceError, match="no validated jobs"):
        adapter.fetch(company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers"))


def test_registry_rejects_unknown_source_safely() -> None:
    with pytest.raises(UnknownSourceError, match="not-permitted") as caught:
        get_source_adapter(CompanyStub(source=" Not-Permitted "))

    assert caught.value.requests_made == 0


def test_fixture_adapter_returns_offline_source_batch(tmp_path: Path) -> None:
    fixture_path = tmp_path / "jobs.json"
    fixture_path.write_text(
        '[{"source": "fixture", "title": "Analyst"}]', encoding="utf-8"
    )

    batch = FixtureSourceAdapter(fixture_path).fetch(
        company=CompanyStub(
            source="fixture",
            source_jobs_url="https://unused.example.test/jobs",
        )
    )

    assert batch.records == ({"source": "fixture", "title": "Analyst"},)
    assert batch.requests_made == 0
