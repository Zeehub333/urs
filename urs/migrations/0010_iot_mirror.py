# Adds Connection.devices (أجهزة IoT إضافية JSON) + IoTMirror model
# (مرايا الجداول المحلية iot_<engine>_<endpoint> مع تقدم المزامنة).

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('urs', '0009_connection_conn_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='connection',
            name='devices',
            field=models.TextField(
                blank=True, default='[]', verbose_name='أجهزة إضافية',
                help_text='JSON: أجهزة IoT إضافية بنفس الاتصال [{"host": "ip", "port": 4370}] — تُجلب مع الجهاز الرئيسي (UNION ALL)',
            ),
        ),
        migrations.CreateModel(
            name='IoTMirror',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('endpoint', models.CharField(default='att', max_length=50, verbose_name='نقطة البيانات')),
                ('table_name', models.CharField(max_length=100, verbose_name='اسم الجدول')),
                ('auto_sync', models.BooleanField(default=True, verbose_name='مزامنة تلقائية')),
                ('interval_min', models.IntegerField(default=15, verbose_name='الفاصل (دقيقة)')),
                ('clear_device', models.BooleanField(
                    default=True, verbose_name='مسح سجل الجهاز',
                    help_text='مسح سجلات الأجهزة بعد المزامنة الناجحة لتسريع المزامنات التالية')),
                ('status', models.CharField(
                    choices=[('idle', 'خامل'), ('running', 'جارٍ المزامنة'), ('done', 'مكتمل'), ('error', 'خطأ')],
                    default='idle', max_length=20, verbose_name='الحالة')),
                ('progress_pct', models.IntegerField(default=0, verbose_name='التقدم %')),
                ('devices_total', models.IntegerField(default=0, verbose_name='عدد الأجهزة')),
                ('devices_done', models.IntegerField(default=0, verbose_name='أجهزة منجزة')),
                ('rows_pulled', models.IntegerField(default=0, verbose_name='صفوف مسحوبة')),
                ('rows_new', models.IntegerField(default=0, verbose_name='صفوف جديدة')),
                ('watermarks', models.TextField(
                    blank=True, default='{}', verbose_name='علامات التقدم',
                    help_text='JSON: آخر punch_ts مُزامَن لكل جهاز {ip: ts}')),
                ('last_sync_at', models.DateTimeField(blank=True, null=True, verbose_name='آخر مزامنة')),
                ('last_error', models.TextField(blank=True, verbose_name='آخر خطأ')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('connection', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='iot_mirrors',
                    to='urs.connection', verbose_name='اتصال IoT')),
                ('local_connection', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='iot_mirror_targets',
                    to='urs.connection', verbose_name='الاتصال المحلي')),
            ],
            options={
                'ordering': ['-updated_at'],
                'verbose_name': 'مرآة IoT',
                'verbose_name_plural': 'مرايا IoT',
                'unique_together': {('connection', 'endpoint', 'local_connection')},
            },
        ),
    ]
