# Generated for DML/RML hybrid SQL×API channel
# Adds Connection.is_queryable (قابل للاستعلام عبر SQL) and marks
# existing non-SQL sources (e.g. zk fingerprint devices) as API-only.

from django.db import migrations, models


def _mark_non_sql_not_queryable(apps, schema_editor):
    Connection = apps.get_model("urs", "Connection")
    Connection.objects.filter(engine="zk").update(is_queryable=False)


class Migration(migrations.Migration):

    dependencies = [
        ('urs', '0007_preset'),
    ]

    operations = [
        migrations.AddField(
            model_name='connection',
            name='is_queryable',
            field=models.BooleanField(
                default=True, verbose_name='قابل للاستعلام',
                help_text='إذا True يُستعلم عنه عبر SQL مباشرة، وإلا عبر API الخاص بالمصدر (مثل أجهزة البصمة)',
            ),
        ),
        migrations.RunPython(_mark_non_sql_not_queryable, migrations.RunPython.noop),
    ]
