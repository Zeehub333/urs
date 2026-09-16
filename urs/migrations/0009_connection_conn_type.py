# Adds Connection.conn_type (نوع الاتصال: قاعدة بيانات | IoT) and backfills
# existing ZK device connections as IoT.

from django.db import migrations, models


def _backfill_conn_type(apps, schema_editor):
    Connection = apps.get_model("urs", "Connection")
    Connection.objects.filter(engine="zk").update(conn_type="iot")
    Connection.objects.exclude(engine="zk").update(conn_type="database")


class Migration(migrations.Migration):

    dependencies = [
        ('urs', '0008_connection_is_queryable'),
    ]

    operations = [
        migrations.AddField(
            model_name='connection',
            name='conn_type',
            field=models.CharField(
                choices=[('database', 'قاعدة بيانات'), ('iot', 'IoT')],
                default='database', max_length=20, verbose_name='نوع الاتصال',
                help_text='قاعدة بيانات (محركات SQL) أو IoT (أجهزة مثل البصمات)',
            ),
        ),
        migrations.RunPython(_backfill_conn_type, migrations.RunPython.noop),
    ]
