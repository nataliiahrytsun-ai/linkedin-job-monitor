from typing import ClassVar

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("jobs", "0004_jobposting_last_reviewed_content_hash")
    ]

    operations: ClassVar[list[migrations.AddField]] = [
        migrations.AddField(
            model_name="jobposting",
            name="compensation_text",
            field=models.TextField(blank=True, default=None, null=True),
        ),
    ]
