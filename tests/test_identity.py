from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime

from scraping.identity import compute_content_hash, compute_dedupe_key
from scraping.normalization import NormalizedJobPosting, normalize_job_posting


def normalized_job(**overrides: object) -> NormalizedJobPosting:
    values: dict[str, object] = {
        "source": "customer_feed",
        "source_job_id": "job-42",
        "title": "Delivery Manager",
        "country": "Austria",
        "city": "Vienna",
        "location": "Vienna, Austria",
        "workplace_type": "hybrid",
        "published_at": datetime(2026, 8, 6, 10, 30, tzinfo=UTC),
        "description": "Deliver projects.\nWork with customers.",
        "source_job_url": "https://jobs.example.com/openings/42?lang=en",
    }
    values.update(overrides)
    return normalize_job_posting(**values)  # type: ignore[arg-type]


def test_content_hash_is_deterministic_and_hexadecimal() -> None:
    job = normalized_job()

    first = compute_content_hash(job)
    second = compute_content_hash(job)

    assert first == second
    assert first == "a1571df1d88abfcaf8479b6e25a87840ab20cde5e51611014508cd34e494aee6"
    assert re.fullmatch(r"[0-9a-f]{64}", first)


def test_equivalent_raw_data_has_same_content_hash() -> None:
    first = normalized_job(
        source=" CUSTOMER_FEED ",
        title=" Delivery   Manager ",
        description="Deliver projects.\r\nWork with customers.  ",
        source_job_url="HTTPS://JOBS.EXAMPLE.COM:443/openings/42?lang=en#apply",
    )
    second = normalized_job()

    assert compute_content_hash(first) == compute_content_hash(second)


def test_meaningful_content_change_changes_content_hash() -> None:
    job = normalized_job()

    assert compute_content_hash(job) != compute_content_hash(
        replace(job, description="Different responsibilities")
    )


def test_source_and_source_job_id_do_not_affect_content_hash() -> None:
    job = normalized_job()
    different_identity = replace(job, source="another_feed", source_job_id="other-id")

    assert compute_content_hash(job) == compute_content_hash(different_identity)


def test_null_is_explicit_and_differs_from_nonempty_content() -> None:
    job = normalized_job(employment_type=None)

    assert compute_content_hash(job) != compute_content_hash(
        replace(job, employment_type="full-time")
    )


def test_id_first_dedupe_key_uses_source_and_id_not_url() -> None:
    job = normalized_job()
    same_identity_different_url = replace(
        job, source_job_url="https://jobs.example.com/changed"
    )

    assert compute_dedupe_key(job) == compute_dedupe_key(same_identity_different_url)


def test_url_is_fallback_identity_when_id_is_missing() -> None:
    first = normalized_job(source_job_id=None)
    second = normalized_job(source_job_id=None)

    assert compute_dedupe_key(first) == compute_dedupe_key(second)
    assert re.fullmatch(r"[0-9a-f]{64}", compute_dedupe_key(first))


def test_different_sources_produce_different_dedupe_keys() -> None:
    first = normalized_job(source="feed_one")
    second = normalized_job(source="feed_two")

    assert compute_dedupe_key(first) != compute_dedupe_key(second)


def test_dedupe_key_matches_documented_id_payload() -> None:
    import hashlib

    job = normalized_job(source="feed", source_job_id="123")
    expected = hashlib.sha256(b"id\x00feed\x00123").hexdigest()

    assert compute_dedupe_key(job) == expected


def test_dedupe_key_matches_documented_url_payload() -> None:
    import hashlib

    job = normalized_job(
        source="feed",
        source_job_id=None,
        source_job_url="https://jobs.example.com/123",
    )
    expected = hashlib.sha256(b"url\0feed\0https://jobs.example.com/123").hexdigest()

    assert compute_dedupe_key(job) == expected
