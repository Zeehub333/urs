"""
FMLK field-type registry — 8 traditional + 15 relational field types.

Each entry describes one inputType: Arabic label, description, usage example,
category (traditional = single-table simple data, relational = cross-table),
and the extra XML config attributes the form player understands.
"""
from __future__ import annotations
from typing import Dict, List

# Canonical inputType -> aliases accepted in <field inputType="...">
ALIASES: Dict[str, List[str]] = {
    "text": ["text", "string", "single_line", "singleline"],
    "textarea": ["textarea", "paragraph", "longtext", "multiline"],
    "number": ["number", "integer", "int", "decimal", "float"],
    "datetime": ["datetime", "date_time", "date", "time"],
    "boolean": ["boolean", "checkbox", "bool", "check"],
    "select": ["select", "dropdown", "choice", "list", "combobox"],
    "password": ["password", "passwd", "pwd", "secret", "passcode"],
    "list-input": ["list-input", "list_input", "listinput", "iplist", "ip_list", "string_list", "value_list", "multi_text"],
    "cascading_select": ["cascading_select", "cascading", "cascade", "dependent_select", "linked_select", "linkedselect"],
    "multiselect": ["multiselect", "multi_select", "multiselect_tags", "select2", "tags", "tag"],
    "lookup": ["lookup", "search_select", "searchselect", "fk_search"],
    "status": ["status", "radio", "radio_buttons", "radiobuttons", "state"],
    "datamodal": ["datamodal", "data_modal", "grid_select", "gridselect", "modal_list", "table_select"],
    "subform": ["subform", "inline_form", "inlineform", "sub_form"],
    "grid": ["grid", "dynamic_grid", "table_input", "dynamicgrid"],
    "reorder": ["reorder", "drag_drop", "dragdrop", "sortable"],
    "tree": ["tree", "hierarchical", "tree_selector", "treeselector"],
    "badges": ["badges", "cross_reference", "m2m", "many_to_many", "badges_manager"],
    "polymorphic": ["polymorphic", "polymorphic_select", "morph"],
    "livesync": ["livesync", "live_sync", "live-sync", "realtime_fetch"],
    "autopopulate": ["autopopulate", "auto_populate", "conditional_populate"],
    "calculated": ["calculated", "calc", "aggregate_calc", "computed"],
    "matrix": ["matrix", "permission_matrix", "nested_matrix"],
    "restricted_tags": ["restricted_tags", "tags_restricted", "constrained_tags"],
    "cascade_grid": ["cascade_grid", "cascading_lookup", "multilevel_lookup", "cascade_lookup"],
}

# Extra XML attributes understood per canonical type (case-insensitive in XML)
CONFIG_ATTRS: Dict[str, List[str]] = {
    "list-input": ["separator", "placeholder_add"],
    "cascading_select": ["parent_field"],
    "multiselect": ["allow_new"],
    "password": [],
    "lookup": [],
    "status": [],
    "datamodal": [],
    "subform": ["sub_fields"],
    "grid": ["grid_columns", "grid_rows"],
    "reorder": [],
    "tree": ["tree_parent"],
    "badges": [],
    "polymorphic": ["poly_types"],
    "livesync": ["sync_source"],
    "autopopulate": ["sync_source", "auto_condition"],
    "calculated": ["calc_expr"],
    "matrix": ["matrix_rows", "matrix_cols"],
    "restricted_tags": ["allow_new"],
    "cascade_grid": [],
}

TYPES: List[Dict] = [
    # ── 8 traditional ──
    {"key": "text", "ar": "نص قصير", "en": "Text", "category": "traditional",
     "description": "نصوص قصيرة ومحدودة مثل الأسماء والعناوين والبريد الإلكتروني.",
     "usage": "إدخال اسم الموظف أو البريد الإلكتروني."},
    {"key": "textarea", "ar": "نص طويل", "en": "Textarea", "category": "traditional",
     "description": "مساحة أكبر لفقرات متعددة الأسطر والنصوص الطويلة.",
     "usage": "كتابة الوصف الوظيفي أو الملاحظات أو تفاصيل الشكوى."},
    {"key": "number", "ar": "رقم", "en": "Number", "category": "traditional",
     "description": "قيم عددية صحيحة أو عشرية مع حد أدنى وأقصى اختياري.",
     "usage": "إدخال الكمية المتوفرة من منتج في المخزون."},
    {"key": "datetime", "ar": "تاريخ ووقت", "en": "Date & Time", "category": "traditional",
     "description": "اختيار تاريخ أو وقت من تقويم منسدل.",
     "usage": "تسجيل تاريخ ميلاد العميل أو وقت إنشاء الفاتورة."},
    {"key": "boolean", "ar": "اختيار ثنائي", "en": "Checkbox", "category": "traditional",
     "description": "زر تفعيل أو إلغاء يمثل قيمة منطقية (صح / خطأ).",
     "usage": "تحديد ما إذا كان الحساب نشطاً أم لا."},
    {"key": "select", "ar": "صندوق اختيار", "en": "Select", "category": "traditional",
     "description": "قائمة منسدلة ببيانات محددة ثابتة (options) يختار منها المستخدم قيمة واحدة.",
     "usage": "اختيار نوع المحرك من قائمة محددة: postgres/oracle/sqlserver."},
    {"key": "password", "ar": "كلمة مرور", "en": "Password", "category": "traditional",
     "description": "إدخال مخفي (نقاط) لكلمات المرور — لا تظهر في السجلات أبداً بل نجوم عشوائية الطول، ولا تُحفظ النجوم عند التعديل.",
     "usage": "كلمة مرور اتصال قاعدة البيانات."},
    {"key": "list-input", "ar": "قائمة قيم", "en": "List Input", "category": "traditional",
     "description": "إدخال عدة قيم حرة كوسوم (IPs، أرقام، أسماء) تُحفظ كمصفوفة JSON.",
     "usage": "إدخال IPs أجهزة البصمة المتعددة لاتصال IoT واحد.", "attrs": ["separator"]},
    # ── 15 relational ──
    {"key": "cascading_select", "ar": "قائمة مرتبطة", "en": "Cascading Select", "category": "relational",
     "description": "محتواها يعتمد على اختيار حقل سابق ويربط جدولين بتسلسل.",
     "usage": "اختيار الدولة فيتغير حقل المدينة لجلب مدنها فقط.", "attrs": ["parent_field"]},
    {"key": "multiselect", "ar": "اختيار متعدد", "en": "Multi-Select", "category": "relational",
     "description": "اختيار عدة عناصر من جدول آخر عبر جدول وسيط (Many-to-Many).",
     "usage": "اختيار مهارات الموظف المتعددة.", "attrs": ["allow_new"]},
    {"key": "lookup", "ar": "بحث وربط", "en": "Lookup", "category": "relational",
     "description": "بحث مصغر في جدول خارجي لجلب المفتاح وربطه كمفتاح أجنبي.",
     "usage": "اختيار العميل أثناء إنشاء فاتورة لجلب رقمه التعريفي."},
    {"key": "status", "ar": "حالات (أزرار)", "en": "Status Radios", "category": "relational",
     "description": "الحالات كأزرار اختيار (radio) ملونة بدل القائمة المنسدلة.",
     "usage": "حالة الطلب: جديد / معتمد / مرفوض — كل حالة بلون."},
    {"key": "datamodal", "ar": "قائمة شبكية", "en": "Data Grid Modal", "category": "relational",
     "description": "اختيار قيمة من شبكة بيانات منبثقة قابلة للبحث (للقوائم الكبيرة).",
     "usage": "اختيار الصنف من شبكة أصناف قابلة للبحث بدل dropdown طويل."},
    {"key": "subform", "ar": "نموذج متداخل", "en": "Subform", "category": "relational",
     "description": "واجهة فرعية لإدخال سجلات متعددة دفعة واحدة بعلاقة One-to-Many.",
     "usage": "بنود المنتجات داخل الفاتورة الواحدة.", "attrs": ["sub_fields"]},
    {"key": "grid", "ar": "جدول ديناميكي", "en": "Dynamic Grid", "category": "relational",
     "description": "شبكة صفوف وأعمدة لإدخال بيانات متشابهة مرتبطة بجدول فرعي.",
     "usage": "جدول الحصص الدراسية الأسبوعية.", "attrs": ["grid_columns", "grid_rows"]},
    {"key": "reorder", "ar": "إعادة ترتيب", "en": "Reorder", "category": "relational",
     "description": "تنظيم مرئي بالسحب والإفلات يحدث حقل الترتيب تلقائياً.",
     "usage": "إعادة ترتيب مراحل المشروع."},
    {"key": "tree", "ar": "شجرة هرمية", "en": "Tree Selector", "category": "relational",
     "description": "عرض العلاقات الذاتية على شكل شجرة متفرعة لاختيار عقدة.",
     "usage": "اختيار القسم التابع للموظف في هيكل متداخل."},
    {"key": "badges", "ar": "شارات ربط", "en": "Badges", "category": "relational",
     "description": "الكيانات المرتبطة كبطاقات تُضاف أو تُزال لتحديث جدول الربط.",
     "usage": "ربط مشروع بعدة فرق عمل."},
    {"key": "polymorphic", "ar": "ربط متعدد الشكل", "en": "Polymorphic", "category": "relational",
     "description": "ربط السجل بأكثر من جدول مختلف حسب نوع الكيان المختار.",
     "usage": "تعليق يرتبط بالمنتجات أو المقالات.", "attrs": ["poly_types"]},
    {"key": "livesync", "ar": "جلب مباشر", "en": "Live Sync", "category": "relational",
     "description": "يعرض بيانات من جدول آخر فور اختيار المفتاح المرتبط دون حفظ.",
     "usage": "عرض رصيد العميل فور اختيار اسمه.", "attrs": ["sync_source"]},
    {"key": "autopopulate", "ar": "تعبئة تلقائية", "en": "Auto-Populate", "category": "relational",
     "description": "يملأ نفسه من جدول رئيسي مرتبط بناءً على شرط.",
     "usage": "جلب العنوان المسجل للعميل حسب الفرع.", "attrs": ["sync_source", "auto_condition"]},
    {"key": "calculated", "ar": "حقل محسوب", "en": "Calculated", "category": "relational",
     "description": "يحسب قيمته تلقائياً من بيانات جدول فرعي.",
     "usage": "إجمالي الفاتورة من جمع البنود.", "attrs": ["calc_expr"]},
    {"key": "matrix", "ar": "مصفوفة صلاحيات", "en": "Matrix", "category": "relational",
     "description": "مصفوفة تربط المستخدمين بالأدوار والصلاحيات.",
     "usage": "صلاحيات القراءة والكتابة والحذف على عدة جداول.", "attrs": ["matrix_rows", "matrix_cols"]},
    {"key": "restricted_tags", "ar": "وسوم مقيدة", "en": "Restricted Tags", "category": "relational",
     "description": "وسوم لا تقبل إلا القيم الموجودة في جدول مرجعي.",
     "usage": "تصنيفات المقالات من جدول معتمد.", "attrs": ["allow_new"]},
    {"key": "cascade_grid", "ar": "جلب متسلسل", "en": "Cascade Grid", "category": "relational",
     "description": "حقول متتالية بمستويات هرمية صارمة من جداول منفصلة.",
     "usage": "الشركة ← المصنع ← خط الإنتاج ← الآلة."},
]

_BY_KEY = {t["key"]: t for t in TYPES}
_ALIAS_TO_KEY = {}
for _k, _aliases in ALIASES.items():
    for _a in _aliases:
        _ALIAS_TO_KEY[_a.lower()] = _k


def normalize_input_type(raw) -> str:
    """Map any accepted inputType spelling to the canonical key (fallback: text)."""
    if raw is None:
        return "text"
    key = _ALIAS_TO_KEY.get(str(raw).strip().lower())
    if key:
        return key
    return "text"


def get_type(key: str):
    return _BY_KEY.get(key)


def list_types():
    return [dict(t) for t in TYPES]
