from django.db import models

class Preset(models.Model):
    """
    Saved filter presets per app (persisted, shared).
    DB table `presets`: id, type, app, name, filter (jsonb).
    """
    type = models.CharField(max_length=20, default="filter", verbose_name="النوع")
    app = models.CharField(max_length=100, verbose_name="التطبيق")
    name = models.CharField(max_length=200, verbose_name="الاسم")
    filter = models.JSONField(default=list, verbose_name="الفلاتر")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "presets"
        ordering = ["app", "type", "name"]
        verbose_name = "نموذج محفوظ"
        verbose_name_plural = "النماذج المحفوظة"

    def __str__(self):
        return f"{self.app}/{self.type}: {self.name}"

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "app": self.app,
            "name": self.name,
            "filter": self.filter,
        }

class App(models.Model):
    """
    ERP App — synced from system/<app>/metadata.json
    metadata.json: {name, ar, icon, version, description, category, color}
    """
    name = models.CharField(max_length=100, unique=True, help_text="Folder name, e.g., developer_mode")
    name_ar = models.CharField(max_length=200, verbose_name="الاسم العربي")
    name_en = models.CharField(max_length=200, blank=True, verbose_name="English Name")
    icon = models.CharField(max_length=100, default="fa-cube", help_text="FontAwesome class, e.g., fa-code")
    icon_bg = models.CharField(max_length=100, default="bg-gray-900", help_text="Tailwind bg class")
    icon_color = models.CharField(max_length=100, default="text-white", help_text="Tailwind text color")
    version = models.CharField(max_length=20, default="1.0.0")
    description = models.TextField(blank=True)
    description_ar = models.TextField(blank=True, verbose_name="الوصف العربي")
    category = models.CharField(max_length=50, blank=True, default="عام")
    is_active = models.BooleanField(default=True)
    is_new = models.BooleanField(default=False, verbose_name="جديد")
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "تطبيق"
        verbose_name_plural = "التطبيقات"

    def __str__(self):
        return f"{self.name} — {self.name_ar}"

    def to_dict(self):
        return {
            "name": self.name,
            "ar": self.name_ar,
            "en": self.name_en,
            "icon": self.icon,
            "icon_bg": self.icon_bg,
            "icon_color": self.icon_color,
            "version": self.version,
            "description": self.description_ar or self.description,
            "category": self.category,
            "is_active": self.is_active,
            "is_new": self.is_new,
        }

class Report(models.Model):
    """
    RML Report — for Report Player integration
    """
    name = models.CharField(max_length=100, unique=True)
    display_name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    rml_path = models.CharField(max_length=500, blank=True, help_text="Path to .rml file")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.display_name

    class Meta:
        verbose_name = "تقرير"
        verbose_name_plural = "التقارير"

class FilePermission(models.Model):
    """
    Per-file permissions for RML/FML — mirrors permissions_engine FilePermissions
    Each .rml/.fmlk file has its own ACL.
    """
    FILE_TYPE_CHOICES = [("rml", "RML Report"), ("fml", "FML Form"), ("fmlk", "FMLK Form")]
    file_name = models.CharField(max_length=200, unique=True, help_text="e.g., emp_report.rml or hr_form.fmlk")
    file_type = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES, default="rml")
    owner = models.CharField(max_length=100, blank=True, help_text="user:admin")
    aces_json = models.JSONField(default=list, help_text="List of ACEs: [{principal, actions, effect}]")
    inherit = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "صلاحية ملف"
        verbose_name_plural = "صلاحيات الملفات"

    def __str__(self):
        return f"{self.file_name} ({self.file_type})"

    def to_file_permissions(self):
        from permissions_engine.engine import FilePermissions, ACE
        return FilePermissions(
            file=self.file_name,
            file_type=self.file_type,
            owner=self.owner,
            inherit=self.inherit,
            aces=[ACE(**a) for a in self.aces_json],
        )

    @classmethod
    def from_file_permissions(cls, fp):
        obj, _ = cls.objects.update_or_create(
            file_name=fp.file,
            defaults={
                "file_type": fp.file_type,
                "owner": fp.owner or "",
                "aces_json": [a.to_dict() for a in fp.aces],
                "inherit": fp.inherit,
            },
        )
        return obj

class UserDriver(models.Model):
    """
    Per-user driver file — auto-generated by PermissionsEngine analyzing RML/FML.
    Contains filtered list of files + actions the user is allowed to perform,
    plus column/field-level filtering based on their roles.
    Stored in DB and as JSON file on disk for fast loading.
    """
    user = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE, related_name="driver", verbose_name="المستخدم"
    )
    driver_json = models.JSONField(default=dict, verbose_name="ملف التشغيل")
    # Also store as file path for legacy
    file_path = models.CharField(max_length=500, blank=True, verbose_name="مسار الملف")
    generated_at = models.DateTimeField(auto_now=True)
    # Stats for quick display
    rml_count = models.IntegerField(default=0, verbose_name="عدد التقارير")
    fml_count = models.IntegerField(default=0, verbose_name="عدد النماذج")
    permissions_hash = models.CharField(max_length=64, blank=True, verbose_name="بصمة الصلاحيات")

    class Meta:
        verbose_name = "ملف تشغيل المستخدم"
        verbose_name_plural = "ملفات تشغيل المستخدمين"

    def __str__(self):
        return f"Driver — {self.user.username} ({self.rml_count} RML, {self.fml_count} FML)"

    def to_dict(self):
        return {
            "user": self.user.username,
            "rml_count": self.rml_count,
            "fml_count": self.fml_count,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "driver": self.driver_json,
        }


def _sanitize_schema_part(value: str) -> str:
    """Keep [a-z0-9_], lowercase, for Postgres schema/table parts."""
    import re

    v = (value or "").strip().lower()
    v = re.sub(r"[^a-z0-9_]", "_", v)
    v = re.sub(r"_+", "_", v).strip("_")
    return v or "app"


def build_schema_name(company_code: str, branch_code: str, year) -> str:
    """Naming rule: company_branch_year, lowercase. e.g. cmp_main_2026."""
    c = _sanitize_schema_part(company_code)
    b = _sanitize_schema_part(branch_code)
    try:
        y = str(int(year))
    except Exception:
        import datetime

        y = str(datetime.date.today().year)
    return f"{c}_{b}_{y}"


class Country(models.Model):
    """دولة + الدولة الافتراضية (واحدة فقط)."""

    code = models.CharField(max_length=10, unique=True, verbose_name="الرمز", help_text="SA, EG, AE")
    name = models.CharField(max_length=100, verbose_name="الاسم")
    name_en = models.CharField(max_length=100, blank=True, verbose_name="English Name")
    phone_code = models.CharField(max_length=10, blank=True, verbose_name="مفتاح الاتصال")
    is_default = models.BooleanField(default=False, verbose_name="افتراضية")
    is_active = models.BooleanField(default=True, verbose_name="نشطة")

    class Meta:
        ordering = ["name"]
        verbose_name = "دولة"
        verbose_name_plural = "الدول"

    def __str__(self):
        return f"{self.name} ({self.code})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Country.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)

    def to_dict(self):
        return {"id": self.id, "code": self.code, "name": self.name, "name_en": self.name_en,
                "phone_code": self.phone_code, "is_default": self.is_default, "is_active": self.is_active}


class City(models.Model):
    """مدينة تابعة لدولة."""

    name = models.CharField(max_length=100, verbose_name="الاسم")
    name_en = models.CharField(max_length=100, blank=True, verbose_name="English Name")
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="cities", verbose_name="الدولة")
    is_active = models.BooleanField(default=True, verbose_name="نشطة")

    class Meta:
        ordering = ["country__name", "name"]
        verbose_name = "مدينة"
        verbose_name_plural = "المدن"
        unique_together = [("country", "name")]

    def __str__(self):
        return f"{self.name} — {self.country.code}"

    def to_dict(self):
        return {"id": self.id, "name": self.name, "name_en": self.name_en,
                "country": self.country_id, "country_code": self.country.code if self.country_id else "",
                "is_active": self.is_active}


class Currency(models.Model):
    """عملة + العملة الافتراضية + السعر (مقابل العملة الأساسية)."""

    code = models.CharField(max_length=10, unique=True, verbose_name="الرمز", help_text="SAR, USD, EGP")
    name = models.CharField(max_length=100, verbose_name="الاسم")
    symbol = models.CharField(max_length=10, blank=True, verbose_name="الرمز المختصر")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=1.0, verbose_name="السعر")
    is_default = models.BooleanField(default=False, verbose_name="افتراضية")
    is_active = models.BooleanField(default=True, verbose_name="نشطة")

    class Meta:
        ordering = ["code"]
        verbose_name = "عملة"
        verbose_name_plural = "العملات"

    def __str__(self):
        return f"{self.code} — {self.name}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Currency.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)

    def to_dict(self):
        return {"id": self.id, "code": self.code, "name": self.name, "symbol": self.symbol,
                "exchange_rate": float(self.exchange_rate), "is_default": self.is_default,
                "is_active": self.is_active}


class Company(models.Model):
    """شركة — بوابة الإعداد الأولى. السكيما تُبنى لكل فرع: company_branch_year."""

    code = models.CharField(max_length=30, unique=True, verbose_name="رمز الشركة", help_text="CMP, cmp1")
    name = models.CharField(max_length=200, verbose_name="اسم الشركة")
    name_en = models.CharField(max_length=200, blank=True, verbose_name="English Name")
    tax_number = models.CharField(max_length=30, blank=True, verbose_name="الرقم الضريبي")
    country = models.ForeignKey(Country, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="الدولة")
    city = models.ForeignKey(City, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="المدينة")
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="العملة")
    fiscal_year = models.IntegerField(default=2026, verbose_name="السنة المالية")
    is_active = models.BooleanField(default=True, verbose_name="نشطة")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "شركة"
        verbose_name_plural = "الشركات"

    def __str__(self):
        return f"{self.code} — {self.name}"

    def to_dict(self):
        return {"id": self.id, "code": self.code, "name": self.name, "name_en": self.name_en,
                "tax_number": self.tax_number, "country": self.country_id, "city": self.city_id,
                "currency": self.currency_id, "fiscal_year": self.fiscal_year, "is_active": self.is_active,
                "branches": [b.to_dict() for b in self.branches.all()] if hasattr(self, "branches") else []}


class Branch(models.Model):
    """فرع — كل فرع له سكيما مستقلة company_branch_year."""

    BRANCH_TYPES = [("main", "رئيسي"), ("sub", "فرعي"), ("warehouse", "مستودع"), ("pos", "نقطة بيع")]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="branches", verbose_name="الشركة")
    code = models.CharField(max_length=30, verbose_name="رمز الفرع", help_text="MAIN, RYD01")
    name = models.CharField(max_length=200, verbose_name="اسم الفرع")
    city = models.ForeignKey(City, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="المدينة")
    branch_type = models.CharField(max_length=20, choices=BRANCH_TYPES, default="main", verbose_name="النوع")
    schema_name = models.CharField(max_length=100, blank=True, verbose_name="السكима", help_text="company_branch_year")
    is_active = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        ordering = ["company__code", "code"]
        verbose_name = "فرع"
        verbose_name_plural = "الفروع"
        unique_together = [("company", "code")]

    def __str__(self):
        return f"{self.company.code}/{self.code} → {self.schema_name or '—'}"

    def compute_schema(self) -> str:
        return build_schema_name(self.company.code, self.code, self.company.fiscal_year)

    def save(self, *args, **kwargs):
        if not self.schema_name:
            # company must exist to compute; if company_id only, fetch code/year
            try:
                comp = self.company if self.company_id and hasattr(self.company, "code") else Company.objects.get(pk=self.company_id)
                self.schema_name = build_schema_name(comp.code, self.code, comp.fiscal_year)
            except Exception:
                pass
        super().save(*args, **kwargs)

    def to_dict(self):
        return {"id": self.id, "company": self.company_id,
                "company_code": self.company.code if self.company_id else "",
                "code": self.code, "name": self.name, "city": self.city_id,
                "branch_type": self.branch_type, "schema_name": self.schema_name, "is_active": self.is_active}


class Connection(models.Model):
    """
    جدول الاتصالات - يخزن معلومات الاتصال بقواعد البيانات المختلفة
    يستخدم في التقارير لربط كل تقرير باتصال واحد أو أكثر
    is_local: إذا True قابل للترحيل والتحديث (migratable/upsertable) وإلا للقراءة فقط
    """

    ENGINE_CHOICES = [
        ("oracle", "Oracle"),
        ("sqlserver", "SQL Server"),
        ("zk", "ZK"),
        ("mysql", "MySQL"),
        ("postgres", "PostgreSQL"),
    ]

    CONN_TYPE_CHOICES = [
        ("database", "قاعدة بيانات"),
        ("iot", "IoT"),
    ]

    DB_ENGINES = ["postgres", "oracle", "sqlserver", "mysql"]
    IOT_ENGINES = ["zk"]

    name = models.CharField(max_length=100, unique=True, verbose_name="الاسم", help_text="معرف الاتصال")
    host = models.CharField(max_length=255, default="172.16.10.101", verbose_name="المضيف")
    port = models.IntegerField(default=5432, verbose_name="المنفذ")
    user = models.CharField(max_length=100, default="postgres", verbose_name="المستخدم")
    password = models.CharField(max_length=255, default="postgres", verbose_name="كلمة المرور")
    instance = models.CharField(
        max_length=100, blank=True, verbose_name="المثيل", help_text="اسم قاعدة البيانات أو SID"
    )
    instance_name = models.CharField(
        max_length=100, blank=True, default="", verbose_name="اسم مثيل SQL Server",
        help_text="SQL Server فقط (مثال SQLEXPRESS) — يبني عنوان الخادم host\\instance"
    )
    engine = models.CharField(max_length=20, choices=ENGINE_CHOICES, default="postgres", verbose_name="المحرك")
    conn_type = models.CharField(
        max_length=20, choices=CONN_TYPE_CHOICES, default="database", verbose_name="نوع الاتصال",
        help_text="قاعدة بيانات (محركات SQL) أو IoT (أجهزة مثل البصمات)"
    )
    is_local = models.BooleanField(
        default=True, verbose_name="محلي", help_text="إذا True قابل للترحيل والتحديث وإلا للقراءة فقط"
    )
    is_queryable = models.BooleanField(
        default=True, verbose_name="قابل للاستعلام",
        help_text="إذا True يُستعلم عنه عبر SQL مباشرة، وإلا عبر API الخاص بالمصدر (مثل أجهزة البصمة)"
    )
    schema = models.CharField(max_length=100, blank=True, verbose_name="المخطط", help_text="Schema مثل HR_SYS")
    description = models.TextField(blank=True, verbose_name="الوصف")
    devices = models.TextField(
        blank=True, default="[]", verbose_name="أجهزة إضافية",
        help_text="JSON: أجهزة IoT إضافية بنفس الاتصال ['ip:port'] أو [{'host': 'ip', 'port': 4370}] — تُجلب مع الجهاز الرئيسي (UNION ALL)"
    )
    endpoint = models.CharField(
        max_length=50, default="att", verbose_name="نقطة البيانات",
        help_text="نقطة بيانات IoT الافتراضية لهذا الاتصال: att (حضور) / users (مستخدمون) / attendance (خام)"
    )
    last_check_at = models.DateTimeField(null=True, blank=True, verbose_name="آخر اتصال")
    last_check_by = models.CharField(max_length=100, blank=True, verbose_name="بواسطة")
    last_check_ok = models.BooleanField(null=True, blank=True, verbose_name="نتيجة آخر فحص")
    last_check_error = models.TextField(blank=True, default="", verbose_name="خطأ آخر فحص")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "اتصال"
        verbose_name_plural = "الاتصالات"

    def __str__(self):
        return f"{self.name} ({self.engine}://{self.host}:{self.port}/{self.instance})"

    def device_list(self):
        """[(host, port)] — أجهزة الاتصال للمزامنة (UNION ALL).

        - IoT: قائمة `devices` فقط (IP:Port لكل جهاز) — حقولا host/port
          مخفيان لهذا النوع ولا يُستخدمان.
        - database: الجهاز الرئيسي (host/port) + الأجهزة الإضافية.
        """
        iot_only = str(getattr(self, "conn_type", "") or "") == "iot"
        out = []
        if not iot_only:
            try:
                if (self.host or "").strip():
                    out.append((str(self.host).strip(), int(self.port or 4370)))
            except Exception:
                pass
        try:
            import json as _json
            raw = self.devices or "[]"
            devs = _json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(devs, dict):
                devs = [devs]
            for d in (devs or []):
                if isinstance(d, str):
                    h, _, p = d.partition(":")
                    try:
                        out.append((h.strip(), int(p or 4370)))
                    except Exception:
                        pass
                elif isinstance(d, dict):
                    h = str(d.get("host") or d.get("ip") or "").strip()
                    if not h:
                        continue
                    try:
                        p = int(d.get("port") or 4370)
                    except Exception:
                        p = 4370
                    out.append((h, p))
        except Exception:
            pass
        # dedupe preserving order
        seen, uniq = set(), []
        for h, p in out:
            if (h, p) not in seen:
                seen.add((h, p))
                uniq.append((h, p))
        return uniq

    def to_dict(self):
        d = {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "instance": self.instance,
            "instance_name": self.instance_name,
            "engine": self.engine,
            "conn_type": self.conn_type,
            "connType": self.conn_type,
            "is_local": self.is_local,
            "is_queryable": self.is_queryable,
            "schema": self.schema,
            "endpoint": self.endpoint or "att",
            "description": self.description,
            "last_check_at": self.last_check_at.isoformat() if self.last_check_at else None,
            "last_check_by": self.last_check_by,
            "last_check_ok": self.last_check_ok,
            "last_check_error": self.last_check_error or "",
        }
        try:
            import json as _json
            raw = self.devices or "[]"
            d["devices"] = _json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            d["devices"] = []
        try:
            d["device_list"] = [[h, p] for h, p in self.device_list()]
        except Exception:
            d["device_list"] = []
        return d


class IoTMirror(models.Model):
    """مرآة محلية لاتصال IoT: جدول iot_<engine>_<endpoint> على اتصال محلي + مزامنة خلفية.

    table_name: iot_<engine>_<endpoint> — يُنشأ تلقائياً ويُزامَن من كل أجهزة الاتصال.
    """

    STATUS_CHOICES = [
        ("idle", "خامل"),
        ("running", "جارٍ المزامنة"),
        ("done", "مكتمل"),
        ("error", "خطأ"),
    ]

    connection = models.ForeignKey(
        Connection, on_delete=models.CASCADE, related_name="iot_mirrors", verbose_name="اتصال IoT")
    endpoint = models.CharField(max_length=50, default="att", verbose_name="نقطة البيانات")
    local_connection = models.ForeignKey(
        Connection, on_delete=models.CASCADE, related_name="iot_mirror_targets",
        verbose_name="الاتصال المحلي")
    table_name = models.CharField(max_length=100, verbose_name="اسم الجدول")
    auto_sync = models.BooleanField(default=True, verbose_name="مزامنة تلقائية")
    interval_min = models.IntegerField(default=15, verbose_name="الفاصل (دقيقة)")
    clear_device = models.BooleanField(
        default=True, verbose_name="مسح سجل الجهاز",
        help_text="مسح سجلات الأجهزة بعد المزامنة الناجحة لتسريع المزامنات التالية")
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="idle", verbose_name="الحالة")
    progress_pct = models.IntegerField(default=0, verbose_name="التقدم %")
    devices_total = models.IntegerField(default=0, verbose_name="عدد الأجهزة")
    devices_done = models.IntegerField(default=0, verbose_name="أجهزة منجزة")
    rows_pulled = models.IntegerField(default=0, verbose_name="صفوف مسحوبة")
    rows_new = models.IntegerField(default=0, verbose_name="صفوف جديدة")
    watermarks = models.TextField(
        blank=True, default="{}", verbose_name="علامات التقدم",
        help_text="JSON: آخر punch_ts مُزامَن لكل جهاز {ip: ts}")
    last_sync_at = models.DateTimeField(null=True, blank=True, verbose_name="آخر مزامنة")
    last_error = models.TextField(blank=True, verbose_name="آخر خطأ")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "مرآة IoT"
        verbose_name_plural = "مرايا IoT"
        unique_together = [("connection", "endpoint", "local_connection")]

    def __str__(self):
        return f"{self.table_name} ← {self.connection_id}/{self.endpoint}"

    def to_dict(self):
        import json as _json
        try:
            wm = _json.loads(self.watermarks or "{}")
        except Exception:
            wm = {}
        return {
            "id": self.id,
            "connection_id": self.connection_id,
            "connection": self.connection.name if self.connection_id else "",
            "endpoint": self.endpoint,
            "local_connection_id": self.local_connection_id,
            "local_connection": self.local_connection.name if self.local_connection_id else "",
            "table_name": self.table_name,
            "auto_sync": self.auto_sync,
            "interval_min": self.interval_min,
            "clear_device": self.clear_device,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "devices_total": self.devices_total,
            "devices_done": self.devices_done,
            "rows_pulled": self.rows_pulled,
            "rows_new": self.rows_new,
            "watermarks": wm,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "last_error": self.last_error,
        }
