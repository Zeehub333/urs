# Generated for Connection.last_check_error (topbar tooltip on red dot)
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("urs", "0013_connection_instance_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="connection",
            name="last_check_error",
            field=models.TextField(blank=True, default="", verbose_name="خطأ آخر فحص"),
        ),
    ]
