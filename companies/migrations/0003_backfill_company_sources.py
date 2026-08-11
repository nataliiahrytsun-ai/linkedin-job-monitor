from __future__ import annotations

from django.db import migrations

APPROVED_LEGACY_SOURCES = frozenset({"fixture", "lever"})


def _normalized_source(value: object) -> str:
    return str(value or "").strip().lower()


def backfill_company_sources(apps, schema_editor) -> None:
    Company = apps.get_model("companies", "Company")
    CompanySource = apps.get_model("companies", "CompanySource")
    JobPosting = apps.get_model("jobs", "JobPosting")
    ScrapeRun = apps.get_model("scrape_runs", "ScrapeRun")

    if CompanySource.objects.exists():
        raise RuntimeError(
            "CompanySource backfill requires an empty additive source table"
        )

    companies = {
        company.pk: company
        for company in Company.objects.order_by("pk").iterator()
    }

    for job in JobPosting.objects.order_by("pk").iterator():
        company = companies.get(job.company_id)
        if company is None:
            raise RuntimeError(
                f"JobPosting {job.pk} references missing Company {job.company_id}"
            )
        company_source = _normalized_source(company.source)
        job_source = _normalized_source(job.source)
        if job_source != company_source:
            raise RuntimeError(
                "JobPosting provenance mismatch: "
                f"job {job.pk} source {job_source!r} does not match "
                f"Company {company.pk} source {company_source!r}"
            )

    for run in ScrapeRun.objects.order_by("pk").iterator():
        if run.company_id not in companies:
            raise RuntimeError(
                f"ScrapeRun {run.pk} references missing Company {run.company_id}"
            )

    source_ids_by_company: dict[int, int] = {}
    for company_id, company in companies.items():
        source_key = _normalized_source(company.source)
        is_approved = source_key in APPROVED_LEGACY_SOURCES
        source_url = company.source_jobs_url or None
        company_source = CompanySource.objects.create(
            company_id=company_id,
            source=source_key,
            source_jobs_url=source_url,
            approval_status="approved" if is_approved else "needs_review",
            is_active=is_approved,
        )
        source_ids_by_company[company_id] = company_source.pk

    for company_id, company_source_id in source_ids_by_company.items():
        JobPosting.objects.filter(company_id=company_id).update(
            company_source_id=company_source_id
        )
        ScrapeRun.objects.filter(company_id=company_id).update(
            company_source_id=company_source_id
        )


def reverse_company_source_backfill(apps, schema_editor) -> None:
    CompanySource = apps.get_model("companies", "CompanySource")
    JobPosting = apps.get_model("jobs", "JobPosting")
    ScrapeRun = apps.get_model("scrape_runs", "ScrapeRun")

    JobPosting.objects.update(company_source_id=None)
    ScrapeRun.objects.update(company_source_id=None)
    CompanySource.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("companies", "0002_companysource"),
        ("jobs", "0002_jobposting_company_source"),
        ("scrape_runs", "0002_scraperun_company_source"),
    ]

    operations = [  # noqa: RUF012
        migrations.RunPython(
            backfill_company_sources,
            reverse_company_source_backfill,
        ),
    ]
