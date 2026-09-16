# Adds Connection.endpoint (نقطة بيانات IoT الافتراضية: att/users/attendance).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('urs', '0010_iot_mirror'),
    ]

    operations = [
        migrations.AddField(
            model_name='connection',
            name='endpoint',
            field=models.CharField(
                default='att', max_length=50, verbose_name='نقطة البيانات',
                help_text='نقطة بيانات IoT الافتراضية لهذا الاتصال: att (حضور) / users (مستخدمون) / attendance (خام)',
            ),
        ),
    ]
