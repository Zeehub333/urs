from django.db import models


class NLPQueryLog(models.Model):
    """سجل استعلامات المحرك وتحليلات النوايا والطبقة الوسيطة (Command Layer)"""
    query_text = models.CharField(max_length=500, verbose_name="نص الاستعلام الأصلي")
    normalized_text = models.CharField(max_length=500, blank=True, verbose_name="النص المعالج")
    workspace_id = models.CharField(max_length=100, blank=True, default="", verbose_name="مساحة العمل")
    detected_intent = models.CharField(max_length=50, blank=True, default="", verbose_name="النية المكتشفة")
    confidence = models.FloatField(default=0.0, verbose_name="نسبة الثقة")
    entities = models.JSONField(default=dict, blank=True, verbose_name="الكيانات المستخرجة")
    command_ir = models.JSONField(default=dict, blank=True, verbose_name="طبقة الأوامر الناتجة (Command IR)")
    execution_time_ms = models.FloatField(default=0.0, verbose_name="وقت التنفيذ (ms)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="وقت الاستعلام")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "سجل استعلام NLP"
        verbose_name_plural = "سجلات استعلامات NLP"

    def __str__(self):
        return f"{self.query_text} [{self.detected_intent} - {self.confidence:.2f}]"


class NLPSynonym(models.Model):
    """قاموس المرادفات وتصحيح الأخطاء الشائعة للمسميات والكيانات"""
    CATEGORY_CHOICES = [
        ("item", "صنف"),
        ("metric", "مؤشر / KPI"),
        ("report", "تقرير"),
        ("app", "تطبيق"),
        ("action", "أمر / إجراء"),
    ]
    raw_term = models.CharField(max_length=150, verbose_name="الكلمة المدخلة / المرادف")
    canonical_term = models.CharField(max_length=150, verbose_name="الاسم المعتمد في النظام")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="item", verbose_name="التصنيف")
    is_active = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        unique_together = [("raw_term", "category")]
        verbose_name = "مرادف NLP"
        verbose_name_plural = "قاموس مرادفات NLP"

    def __str__(self):
        return f"{self.raw_term} -> {self.canonical_term} ({self.category})"


class NLPCachedEntity(models.Model):
    """تخزين مؤقت للكيانات المفهرسة لكل مساحة عمل لسرعة الاستجابة"""
    workspace_id = models.CharField(max_length=100, verbose_name="مساحة العمل")
    entity_type = models.CharField(max_length=50, verbose_name="نوع الكيان (item, report, app)")
    code = models.CharField(max_length=100, blank=True, default="", verbose_name="الرمز / المعرف")
    name = models.CharField(max_length=255, verbose_name="الاسم الأصلي")
    normalized_name = models.CharField(max_length=255, verbose_name="الاسم المعالج للبحث")
    meta_json = models.JSONField(default=dict, blank=True, verbose_name="بيانات إضافية")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")

    class Meta:
        indexes = [
            models.Index(fields=["workspace_id", "entity_type", "normalized_name"]),
        ]
        verbose_name = "كيان مفهرس NLP"
        verbose_name_plural = "الكيانات المفهرسة NLP"

    def __str__(self):
        return f"[{self.workspace_id}] {self.entity_type}: {self.name}"
