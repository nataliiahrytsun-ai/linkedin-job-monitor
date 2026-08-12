# ruff: noqa: RUF012

from django.db import migrations, models
from django.db.models import F


def backfill_reviewed_content_hash(apps, schema_editor):
    JobPosting = apps.get_model("jobs", "JobPosting")
    JobPosting.objects.update(last_reviewed_content_hash=F("content_hash"))


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0003_remove_jobposting_uniq_job_dedupe_key_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobposting",
            name="last_reviewed_content_hash",
            field=models.CharField(blank=True, default=None, max_length=64, null=True),
        ),
        migrations.RunPython(backfill_reviewed_content_hash, migrations.RunPython.noop),
    ]
