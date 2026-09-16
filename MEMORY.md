# ذاكرة المشروع — تقرير الأعمال المفصل

> نظام URS / Odex ERP — الموارد البشرية والتقارير والنماذج والإعدادات
> تاريخ آخر تحديث: 2026-09-12 — المسار: `C:\urs2`

---

## 1. نظرة عامة على البيئة

| المكوّن | التفاصيل |
|---|---|
| إطار العمل | Django 4.1 + Django Templates (RTL عربي) + Tailwind CDN + Chart.js |
| قاعدة البيانات | PostgreSQL على `172.16.10.101:5432` — المستخدم `postgres` — قاعدة `urs` |
| ملف الإعدادات | `C:\urs2\odex\system\human_resources\metadata.json` |
| المنافذ | Django‏ `8004` — ملاحظة: كانت توجد عمليات مكررة قديمة على `8004` تم إيقافها |
| محركات اللغات | `fmlk_engine/` (النماذج) — `rml_python/` (التقارير) — `cml_engine/` (الإعدادات) |
| القوالب | `urs/templates/` + `urs/templates/partials/` — كلها ترث `base.html` |

---

## 2. سجل الأعمال (بالترتيب الزمني)

### 2.1 تنظيف واجهة تفاصيل التطبيق من التفاصيل التقنية
- الملف: `urs/templates/app_detail.html`
- حُذف: كلمات `FML/RML`، أسماء الملفات والجداول وعدد الحقول/التبويبات/الأعمدة، زر `API JSON`، قسم `SQL Preview` وأزرار `SQL INSERT`، النصوص التقنية (`Forms Player (tabs...)` / `Report Player (Excel-like)`)، الأرقام الثابتة (أصبحت ديناميكية).
- المتبقي للمستخدم: الاسم العربي + الوصف + زر `معاينة/فتح` + عدادات مبسطة.

### 2.2 تطوير محلل FMLK (`fmlk_engine/compiler.py`)
- **محرك التحقق**: `FMLKField.validation` أصبح `Dict` يحمل `pattern/regex` و`min_length` و`max_length` و`min` و`max` — يُقرأ من عنصر `<validation>` فرعي أو خصائص مباشرة، ويُمرر للواجهة (`validation` + `validationRules` + `pattern`) مع تحقق خلفي برسائل عربية (`engine.py: validate`).
- **المفاتيح الخارجية**: `ref_table/ref_fk/ref_display` تُعرض في `to_dict()` مع `refEndpoint/lookupEndpoint`، ونقطتا نهاية جديدتان:
  - `GET /api/fmlk/lookup?fml=&field=&limit=&search=` — بيانات مرجعية ديناميكية (DB مع fallback)
  - `GET /api/fmlk/lookups?fml=` — قائمة الحقول المرجعية

### 2.3 نقل المشغّلين إلى تطبيق urs
- أُنشئ `urs/templates/forms_player.html` و`urs/templates/report_player.html` بنفس الشريط الجانبي (تنقل النماذج/التقارير + تمييز العنصر النشط).
- مسارات مدمجة: `/app/<app>/form/<file>/` و`/app/<app>/report/<file>/` قبل مسار التفاصيل.
- API بروكسي داخل Django (يعمل على نفس المضيف دون الحاجة 8003/8005): كامل `api/fmlk/*` و`api/rml/*`، مع `ALLOWED_HOSTS=['*']`.
- `app_detail.html` أصبحت روابطها تفتح المشغلات المدمجة.

### 2.4 توليد 50 نموذج FML للموارد البشرية
- المجلد: `odex/system/human_resources/` (ملف `metadata.json` + ‏50 ملف `.fmlk`، صفر أخطاء عبر `FMLKFormCompiler`).
- التوزيع: بيانات الموظفين (15) — الهيكل التنظيمي (7) — الحضور والمناوبات (6) — الإجازات (5) — الرواتب (6) — التوظيف (5) — الأداء (4) — الخدمات الذاتية (2).
- كل ملف: `<fml_metadata>` + `<tabs>` + `<fields>` بأنواع مطابقة (`text/date/time/number/email/phone/select`) و`refTable` للعلاقات و`<validation>` للقواعد.

### 2.5 تقرير الاختبار + دمج ميزات مشغل التقارير
- الملف: `odex/system/human_resources/hr_test_report.rml` (12 عمودًا: مباشر + `fk_lookup` للقسم/المنصب).
- دُمجت كل ميزات القالب المرسل في `report_player.html`: بحث تفاعلي، Presets، تجميع جانبي، تبديل الجداول، قائمة العمود (فرز/تجميع/تصفية checkbox)، تصفية متقدمة، منشئ استعلام Low-Code، ترقيم صفحات، تصدير CSV، طباعة — ثم حُذف شريط المعاينة التقني (Pipeline + صندوق SQL الداكن) لاحقًا.

### 2.6 قاعدة البيانات `172.16.10.101/urs`
- فتح `pg_hba.conf` للشبكات (`0.0.0.0/0` + `172.16.10.0/24` + `192.168.0.0/16`) مع `listen_addresses='*'` ثم `CREATE DATABASE urs`.
- `config/settings.py` و`db_init.py` أصبحا يستخدمان `172.16.10.101/urs` — `migrate` + مزامنة 20 تطبيقًا.
- إنشاء مخطط `HR_SYS`: 50 جدولًا من ملفات FML + جداول مرجعية (`hr_departments` ب6 أقسام، `hr_positions` ب6 مناصب) + الجدول الرئيسي `HR_SYS.employees` معبأ بـ 50 موظفًا (`EMP0001..EMP0050`) + `VIEW public.employees` للتوافق.
- `PostgresEngine` في `urs/views.py` (تحويل binds أوراكل `:name` إلى `%(name)s`) وحقنها في مسارات FML/RML بدل `OracleEngine`.
- إصلاحان حرجان: إزالة النقطة من `الراتب الأساسي (ر.س)` (كانت تكسر `_q()`)، وتحويل `COUNT(*)` المجرد إلى عمود مباشر.
- حُذفت البيانات الوهمية (Mock) من الواجهة والخادم — الاعتماد على DB الحقيقية فقط.

### 2.7 أزرار الإنشاء + المفضلة + الأزرار الملونة
- زرّا الشريط الجانبي: **إضافة نموذج** (`POST /api/apps/<app>/fml/create/` ينشئ `.fmlk` + جدول `HR_SYS`) و**إضافة تقرير** (معالج 4 خطوات → 5 لاحقًا).
- حُذفت البطاقة المنقطة الإرشادية.
- نظام المفضلة: نجمة ☆ بجانب كل نموذج (حفظ `localStorage`) + قسم **النماذج المفضلة** + قسم **جميع النماذج** بأزرار ملونة مستطيلة دائم التوسع مع عدّاد حي (`ظاهر / إجمالي`).

### 2.8 جدول الاتصالات + هيكل RML الجديد + معالج التقارير
- نموذج `Connection` (الهجرة `0004_connection`): `name/host/port/user/password/instance/engine(oracle/sqlserver/zk/mysql/postgres)/is_local/schema/description` — `is_local=True` قابل للترحيل والتحديث وإلا قراءة فقط. CRUD كامل + اختبار اتصال + واجهة إدارة من الشريط الجانبي.
- هيكل RML الجديد: `<rml_metadata icon/description>` + `<rml_connections>` (ترقيم تلقائي) + `<rml_chart>` (متعدد) + `<columns connection_id>` — مع حذف الأسماء التقنية من الواجهات (الاسم العربي فقط).
- معالج إنشاء التقارير (4 خطوات): الأساسية (الاسم/الأيقونة/البرمجي) + الاتصالات + الحقول + المخطط — مع حفظ RML فعلي.

### 2.9 فصل الحقول عن الأعمدة في RML
- `<field name type conn_id table_source>` (الاسم = عمود DB، للعرض المباشر أو الحساب) مقابل `<column alias name expr where_clause icon>` (محسوب ويُعرض).
- المحرك يبني SELECT مع `CASE WHEN` لشرط العمود، والقوائم/البطاقات تعرض الحقول والأعمدة والمخططات + زر **عرض التقرير**.

### 2.10 إصلاح البحث والتصفية الفارغة
- السبب: الواجهة ترسل المسميات العربية والمحرك يبني `WHERE "اسم الموظف"` غير الموجود.
- الحل من الجهتين: `_find_column_for_field/_db_expr_for_field` في المحرك (مع `EXISTS` لأعمدة `fk_lookup`) + `resolveDbField()` في الواجهة. تحقق: فلتر عربي = فلتر إنجليزي (10 نتائج).

### 2.11 صندوق بحث الترويسة + إخفاء البطاقة الإرشادية
- ترويسة `app_detail` أصبحت: الاسم + صندوق بحث أنيق (أيقونة + عدّاد نتائج + مسح) + فلاتر الكل/النماذج/التقارير — يُفلتر الشريط والقوائم لحظيًا.

### 2.12 حذف العدادات + إبعاد المصطلحات التقنية + أسماء قواعد البيانات
- حُذفت كل عدادات الحقول/النماذج/التقارير من `app_detail` والمشغلين (بقي عدّاد نتائج البحث المؤقت فقط).
- أُزيلت المصطلحات (`FMLK/RML/SQL/Player/API/HR_SYS/schema/...`) من كل النصوص المرئية مع بقاء القيم الوظيفية (القيم الافتراضية تُملأ خلفيًا).
- محدد الجداول وقوائم المنشئ عربية بالكامل.

### 2.13 محرك الإعدادات CML (`cml_engine/` الجديد)
- `compiler.py`: `CMLCompiler` يحلل `<cml_metadata>` + `<controls>` (الحقول) + `<rules>` (قواعد العمل بأنواع required/unique/regex/min/max/...).
- `engine.py`: `CMLEngine` يطبق الافتراضيات ويتحقق برسائل عربية (منها فحص التفرد عبر السجلات).
- ملفات النظام `odex/system/settings/`: `companies.cml` (11 حقلًا/4 قواعد) + `branches.cml` (9/3) + `users.cml` (9/4) + `permissions.cml` (8/4).
- صفحة `/settings/` مصنفة (النظام/التطبيقات) + صفحة تفاصيل تعرض الضوابط مصنفة بالفئات مع تحقق وحفظ (`<file>.values.json`) — وقسم القواعد مخفي من العرض ومطبق خلفيًا فقط.
- لاحقًا: توحيد تصميمها مع الرئيسية + قسم **إعدادات سريعة** بدل الصندوق الفارغ + إخفاء عداد الحقول.

### 2.14 الوضع الداكن + القالب الموحد
- `base.html` + `partials/` (التنقل، الشريط العلوي، التذييل، الدعم): كل الصفحات الخمس ترثه (`extends-first` مؤكد، صفر تكرار لكود الثيم) — أي تعديل تصميم مستقبلي من مكان واحد.
- مفاتيح التبديل في الشريطين الجانبيين والأزرار القمرية، مع حفظ `localStorage`.

### 2.15 إعادة بناء مشغل الادخالات (forms_player)
- التسمية: النماذج → الادخالات في كل الواجهات (عدا قوالب التصفية المحفوظة).
- حُذف صندوق معاينة SQL نهائيًا.
- شريط علوي بثلاث مجموعات بأزرار أيقونية: (يمين) إضافة/استيراد CSV/تعديل المحدد/حذف المحدد/حفظ/حفظ وجديد — (وسط) التنقل بين السجلات — (يسار) قائمة/شبكة، تصدير، طباعة، مشاركة، إعدادات (إظهار/إخفاء الأعمدة).
- الجسم: جدول قابل للتحديد بميزات جدول التقارير (قائمة العمود فرز/تجميع/تصفية، بحث، شريط تجميع، ترقيم صفحات) + عرض بطاقات + نافذة نموذج مبوبة للإضافة/التعديل.
- إصلاحان حرجيان: الصفوف تُرجع بالمسميات فأُضيفت قراءة بالمسمى أولًا (`cellVal`)، وأُضيف `__pk_id` تلقائيًا في المحرك (مع حل احتياطي للجداول بلا `id`) فعملت دورة CRUD كاملة.
- استُبدلت كل `alert()` بإشعارات علوية `notify()` (نجاح/خطأ/تنبيه) من القالب الأساسي — صفر `alert` في القوالب.

### 2.16 مراجعة RML: إزالة التكرار + `[]` + `namespace`
- الكاتب يكتب صفة واحدة فقط (`connection_id` للأعمدة، `conn_id` للحقول) — صفر `connectionId` في الملفات.
- أسماء الحقول داخل التعبيرات بين `[ ]` (تُتحقق من `<fields>` بخطأ واضح، وتُتجاهل داخل النصوص).
- `metadata` تقبل `namespace`، وقسم `<rules>` جديد — المرجع `ns.rule` يُضمّن تعبير القاعدة/العمود من الملف المعلن للنطاق (RML أو قيمة CML حرفية) مع كشف الدوائر، وتُترك `table.column` العادية كما هي.
- المعالج: حقل النطاق + محرر قواعد + تلميح `[ ]`؛ الملف المهاجر يعمل بنفس الناتج.

### 2.17 الشريط الجانبي يعرض CML كقواعد أعمال
- `_cml_scan()` يرفق `rules_list`، وقسم **قواعد الأعمال** في شريط الإعدادات يسرد كل قاعدة (الاسم + الملف الأب) وتربط بملفها، وتعمل مع البحث.

### 2.18 سجل أنواع الحقول العشرين (FMLK)
- `fmlk_engine/field_types.py`: 5 تقليدية (نص قصير/طويل، رقم، تاريخ ووقت، ثنائي) + 15 تشعبية (مرتبطة، متعدد، بحث وربط، متداخل، جدول ديناميكي، إعادة ترتيب، شجرة، شارات، متعدد الشكل، جلب مباشر، تعبئة تلقائية، محسوب، مصفوفة، وسوم مقيدة، متسلسل) — مع تطبيع المسميات البديلة وسمات الإعداد (`parent_field`، `calc_expr`...).
- `forms_player` يرسم ودجت لكل نوع (JSON للمُركبة، سحب وإفلات، بحث حي، حساب تلقائي) مع توافق خلفي (`date` القديمة تعرض منتقي تاريخ).
- API جديد: `GET /api/fmlk/field-types` (20 نوعًا) — تحقق `node --check` للسكربت.

### 2.19 معالج التقارير: تبويب الحقول قبل الأعمدة + الاستيراد
- 5 خطوات (الحقول قبل الأعمدة): تبويب حقول بقائمة مدمجة + **مودال إنشاء/تعديل حقل** (الاسم/النوع/الاتصال/الجدول مع اقتراح الجداول) + **استيراد حقول** (اتصال ← جدول ← أعمدة بتحديد، مع تخطي المكرر).
- الأعمدة = التعبير فقط (إكمال تلقائي `[field]` أثناء الكتابة) + العرض (أيقونة/اسم) + الشرط بمثال `[f1]=[f2]`.
- خلفية جديدة: `tables/` و`columns/` لأي اتصال postgres (مع رفض الحقن)، ونقطة `rml/update/` للكتابة فوق ملف موجود + زر **تعديل** على كل بطاقة يفتح المعالج معبأً.
- تحقق: إنشاء → تعديل → تنفيذ (`SELECT "full_name" AS "الاسم المعدل"...`) كلها `200`.

### 2.20 إصلاح ترميز/تنفيذ التقرير العربي (hr_test_report)
- السبب: `_q()` كان يقسم أي اسم على النقطة فحوّل `الراتب (ر.س)` إلى `"ر"."س"` (خطأ SQL)، وبناء الجمل كان Oracle (`OFFSET..FETCH`) بينما DB postgres، واستجابات JSON كانت تهرب العربية (`\uXXXX`).
- الحل: `_q()` في `rml_python/oracle_engine.py` (والاحتياطي في `engine.py`) يقسم `a.b` فقط لمعرفات DB اللاتينية ويقتبس الاسم العربي كاملًا؛ ترقيم postgres (`LIMIT :lim OFFSET :off`) عند `self.db` من نوع Postgres؛ `JsonResponse(..., json_dumps_params={"ensure_ascii": False})` في `api_rml_preview/execute` (`urs/views.py`).
- تحقق: `manage.py check` سليم؛ `POST /api/rml/execute {hr_test_report.rml/human_resources}` → `200` بعربية مقروءة؛ `GET /app/human_resources/` → `200 text/html; charset=utf-8`. القوالب وDB (`UTF8`) والبيانات سليمة — لا موجيباكي.

---

## 3. خريطة المسارات (`config/urls.py`)

| المسار | الوظيفة |
|---|---|
| `/` | الرئيسية |
| `/app/<app>/` , `/app/<app>/form/<file>/` , `/app/<app>/report/<file>/` | التفاصيل والمشغلان المدمجان |
| `/settings/` , `/settings/<scope>/<app>/<file>/` | الإعدادات والتفاصيل |
| `/api/apps/` , `/api/apps/<app>/files/` , `/api/apps/sync/` | التطبيقات |
| `/api/apps/<app>/fml/create/` , `/api/apps/<app>/rml/create/` , `/api/apps/<app>/rml/update/` | إنشاء/تحديث الملفات |
| `/api/connections/` , `/create/` , `/<id>/update|delete|test/` , `/<id>/tables/` , `/<id>/tables/<table>/columns/` | الاتصالات والاستكشاف |
| `/api/settings/` , `/api/cml/metadata|validate|save|values` | الإعدادات |
| `/api/fmlk/metadata|lookup|lookups|field-types|preview_insert|create|update|delete|records|record` | النماذج |
| `/api/rml/metadata|preview|execute` | التقارير |
| `/my-reports/` , `/dashboards/` , `/my-docs/` | تقاريري ولوحاتي ومستنداتي |
| `/api/my-docs/list|save|<id>/|delete|thumb/` | مستنداتي (مشفرة MDE1) |
| `/api/dml/xlsx|pdf|doc-pdf` | التصدير (بث/عربي/مستند) |
| `/api/apps/<app>/rules|policy_save|policy_delete` | قواعد الأعمال والسياسات |

---

## 4. البيانات الأولية

- **50 نموذج FML** (15 بيانات موظفين، 7 هيكل، 6 حضور، 5 إجازات، 6 رواتب، 5 توظيف، 4 أداء، 2 خدمة ذاتية).
- **تقرير** `hr_test_report.rml` (12 عمودًا + مخططان + `namespace="hr"` + قاعدة `rule1`).
- **اتصالات مزروعة**: `urs_local` (postgres الرئيسي) + `oracle_prod` + `zk_device` + `sqlserver_fin` + `mysql_web`.
- **قاعدة `urs`**: مخطط `HR_SYS` (50 جدول نماذج + مرجعية + `employees` بـ50 صفًا) + `public.employees` للتوافق.

---

## 5. قرارات معمارية

1. كل القوالب ترث `base.html` — لا تكرار لكود الثيم/الإشعارات.
2. لا مصطلحات تقنية ولا عدادات في النصوص المرئية؛ القيم الوظيفية تُملأ خلفيًا.
3. لا بيانات وهمية — DB الحقيقية فقط مع رسائل خطأ ظاهرة عند الفشل.
4. `col_type="direct"` تُحذف عند الكتابة ليستدل المترجم النوع (مع توافق قراءة القديم).
5. `ns.member` يُحل فقط عند تطابق نطاق معلن؛ غير ذلك يبقى SQL عاديًا (`table.column`).
6. المراجع `[field]` تُتحقق إلزاميًا؛ النصوص المقتبسة لا تُمس.

---

## 6. مشاكل معروفة وحدود

- نموذجا `01/02` يفشل عرضهما: جدول `countries` المرجعي غير موجود (يُعرض الخطأ داخل الجدول).
- الاستكشاف الحي للجداول يدعم postgres فقط؛ باقي المحركات فحص منفذ.
- `pageSize=1000` يُحمّل كل السجلات للشبكة client-side (مناسب لأحجام HR الحالية).
- سيرفر `8004` سبق أن علق بنسخ قديمة مكررة — الحل: إيقاف العمليات وإعادة التشغيل.
- ملفات الاختبار المؤقتة (`_tmp_*`، سكربتات `Temp\2\opencode`) حُذفت بعد كل تحقق.

---

## 7. التشغيل والتحقق السريع

```bat
python manage.py check
python manage.py runserver 127.0.0.1:8004
```
- `/` `/app/human_resources/` `/settings/` + المشغلان → `200`
- `/api/fmlk/field-types` → 20 نوعًا — `/api/connections/1/tables/` → 51 جدولًا

### 2.21 أنواع الربط one_to_one / one_to_many + أعمدة فرعية + مودال التعبير
- **المصمم/المعالج** (`odex/system/settings/modals/rml_wizard_modal.html` + `rml_wizard_script.html`): تبويب الأعمدة فيه شريط فرعي (الأعمدة الرئيسية/الأعمدة الفرعية `colSubNav` + `setColSubTab`) يظهر للتحليلي والمستند (`detail`/`doc`)؛ كل عمود (رئيسي وفرعي بنفس النموذج) يحمل مرجع الحقل (`refTable/refFk/refDisplay`) + نوع الربط (`join_type`: واحد لواحد/واحد لمتعدد)؛ التفاصيل تحمل `rel_type`؛ مودال التعبير (`exprBuilderModal` + `openExprBuilder`) يختار الاتصال ← الجدول ← الحقل من الحقول المستوردة ويدرج `[name]`؛ قراءة المرجع عند التعديل تدعم `refTables[0]` (`_refT/_refF/_refD`) فلا يضيع المرجع.
- **الملف والكومبايلر** (`rml_python/compiler.py` + `urs/views.py`): الأعمدة تكتب `join_type` (تطبيع `one_many→one_to_many` عبر `_norm_join_type` في مساري `create/update`)؛ `<detail rel_type>` (افتراضي `one_to_many`)؛ `<links><link rel_type>` — التعرف تلقائي: نفس المستوى (خارج جدول التفاصيل) = `one_to_one` باتجاهين، ومستويات مختلفة (جدول رئيسي مقابل فرعي) = `one_to_many` (`links()` + `_detail_table_norm`).
- **المشغل** (`urs/templates/report_player.html`): التشكيل حسب النوع والعلاقة — إجمالي جدول مسطح؛ تحليلي صفوف قابلة للطي؛ مستند سجل-بسجل (`pageSize=1`) مع جدول فرعي أسفله (`loadDocDetail`)؛ شارة نوع الربط (`apiRelLabel`) بجانب مرجع الربط في كل عرض فرعي؛ `fk_lookup` متعدد يُجمع (`STRING_AGG`/`LISTAGG` في `rml_python/engine.py:_build_select`).
- تحقق: `manage.py check` سليم؛ `node --check` لسكربت المعالج سليم؛ دورة إنشاء ← تعديل ← قراءة لتقرير تحليلي (`join_type` + `rel_type` + `links` auto=`one_to_many`) كلها `200` والمرجع محفوظ (ملفات `_tmp_*` المؤقتة حُذفت).

### 2.22 البحث والإكمال التلقائي على كل الحقول المرجعية
- **حقول المرجع** (`rml_wizard_script.html`): `refTable/refFk/refDisplay` في الأعمدة الرئيسية والفرعية أصبحت بزر عدسة — الجداول تُقترح من الاتصال (`openRefTableSuggest` + `refLoadTables` مع كاش)، والأعمدة من جدول المرجع المحدد (`openRefColSuggest` + `refLoadCols`)، والاتصال يؤخذ من العمود أو أول اتصال مختار (`refConnFor`) مع تنبيه عربي عند غيابه.
- **مودال التعبير** (`openExprBuilder` بمعامل `kind`): يعمل الآن للتعبير الرئيسي والفرعي والقواعد (`rule`) وشروط الأعمدة (`where`) — `submitExprBuilder` يفرّع حسب النوع.
- **الإكمال التلقائي أثناء الكتابة** (`exprAuto` + `#exprACBox`): عند كتابة `[` + حروف في أي حقل تعبير (عمود/فرعي/قاعدة/شرط) تظهر قائمة الحقول المستوردة المطابقة (البادئة أولًا) — اختيار بالفأرة أو `Enter/Tab` وتنقل بالأسهم و`Esc` للإغلاق، والإدراج يحدّث النموذج (`exprACSet`) دون إعادة رسم.
- مخطط البيانات كان سليمًا أصلًا (بحث جداول + قوائم أعمدة + نوع ربط) فلم يُمس.
- تحقق: `node --check` سليم؛ اختبار منطق الإكمال بمحاكاة DOM (`[bas`→`[basic_salary]` + تحديث النموذج، لا تطابق→إخفاء، هدف `where`→`where_clause`)؛ `/app/human_resources/` والمصمم والمشغل → `200`.

### 2.23 صيغة التعبير المؤهلة [connection.table.column] + المودال الشامل
- **المحلل** (`rml_python/namespaces.py:_resolve_expression`): يدعم الآن `[name]`، `[table.name]`، `[conn.table.name]` مع تحقق ضد `<fields>` (اسم الحقل + `table_source` + `conn_id` عبر `conn_map` التي تربط رقم الاتصال المحلي في الملف بالـ global id). خطأ عربي واضح عند جدول/اتصال/حقل غير معروف.
- **المحرك** (`rml_python/engine.py`): `table_map` ينقل الأسماء المؤهلة إلى جداولها في `SELECT/JOIN/WHERE`؛ `_orig_col` يجلب الحالة الحقيقية من الـ DB (حرجة للـ Postgres) للاقتران؛ `conn_map` تُمرر في كل استدعاءات `_resolve_expression` / `_build_select` / `_build_where` / `_resolve_filter_field` / `_core_expr` / `groups` / `detail_rows` — صفر مراجع فاقدة.
- **المودال الشامل** (`rml_wizard_script.html:exprBuilderModal`): يعمل على 4 أنواع هدف (تعبير رئيسي، تعبير فرعي، تعبير قاعدة، جملة شرط) — يختار اتصال ← جدول ← حقل مستورد ويُدرج `[name]` (يضاف لاحقاً `conn.table.col` تلقائياً عند الكتابة اليدوية؛ الإكمال التلقائي `exprAuto` يُظهر `[name]` فقط واليدوية تدعم الصيغة الثلاثية).
- **أزرار بحث المرجع**: `refTable/refFk/refDisplay` في الأعمدة الرئيسية والفرعية أصبحت بعدسة — تبحث جداول الاتصال (`openRefTableSuggest`) وأعمدة جدول المرجع (`openRefColSuggest`) مع كاش وتحقق اتصال/جدول.
- **إصلاح Postgres case**: `_infer_join_key` يستدعي `_orig_col` على مفتاح الربط الحقيقي فيكسر `T1.ID` → `T1.id` في الـ `ON` clause — استعلامات Oracle/Postgres تعمل بدون خطأ `column ... does not exist`.
- تحقق: `manage.py check` سليم؛ إنشاء/معاينة/تنفيذ تقرير مؤهل (`[1.employees.full_name]`) → JOIN تلقائي مع الحالة الصحيحة → `200`؛ التقارير القائمة (`sales_report`، `invoice_report`، `hr_test_report`) → `200`؛ الملفات المؤقتة حُذفت.

### 2.24 مرايا IoT (zk) + أجهزة البصمة
- اتصال نوع `iot` بمحرك `zk`: حقول `devices` (قائمة `IP:Port`) + `endpoint` (افتراضي `att`)؛ `Connection.device_list/to_dict`؛ `host/port` مخفيان (`visibleIf="conn_type!=iot"`).
- اسم المرآة `iot_<engine>_<endpoint>` (مثال `iot_zk_att` على الاتصال المحلي) + فهرس مركب `ix_iotzkatt_emp_ts (emp_no,punch_ts)`؛ مزامنة تلقائية عبر `UrsConfig.ready()` + `_sweeper_tick`؛ مودال إنشاء/تحديث مرآة من إدارة الاتصالات.
- تقارير `fp_logs.rml` + `emp_logs.rml` تعمل على المرآة (سكيما `public` مستنتجة)؛ `emp_logs` يحسب دخول/خروج عبر قاعدة `fp_calc(start/end/is_across/ignore=60)` مع `DISTINCT ON (emp_no,workday)` + ذيل فجر `MAX/COALESCE` بلا فجوة/تكرار.

### 2.25 مصمم التقارير Workbench
- أوراق: الاتصالات/الحقول/القواعد/الأعمدة/المجموعات/التفاصيل/المخططات/المستندات + مخطط سحب بأسهم + أدوار (افتراضي/فرعي) + ربط تلقائي.
- شجرة المجموعات: نقل فرع ككتلة (`delta`) + `wbGroupDrop` — تحقق `ALL SUBTREE CHECKS PASSED`.
- تبويب الأعمدة: ترتيب `↑↓` + مودال توسيع (`exprExpandModal`) + إكمال `[conn.table.field]` + 50 دالة SQL + إدراج عند المؤشر؛ منطق `where_clause` بصيغة `CASE WHEN <شرط> THEN (<تعبير>) ELSE NULL END`.

### 2.26 قواعد الأعمال والسياسات (`$rule.var$`)
- `<rule><sources><variables><policies>` داخل RML؛ الاستدعاء `$rule.var$` يتوسع إلى `CASE` (`rml_python/rulevars.py`)؛ `priority` الأصغر يفوز + `is_default` واحدة Fallback وإلا `NULL`.
- الأنواع (`text/number/date/time/expression/boolean/choice`) تُحدد في تهيئة القاعدة فقط؛ كشف تداخل السياسات + دمج مكرر + حد 100؛ سايدبار قواعد + مودال سياسات (اسم/أولوية/افتراضية/تحذير تداخل فوري) + API‏ `api_app_rules/policy_save/policy_delete`.
- الأخطاء: `SQL/Python` ← مودال نسخ + `chatgpt.com/?q=`، و`RML/FMLK` ← `toast`.

### 2.27 البحث الذكي في المشغلات
- رقمي → رقمية فقط، نصي → نصية فقط، `20260911/11092026` → تاريخ، نطاق `01092026-11092026` → between، `09:00` → وقت؛ `,` = أو و `&` = و عبر `{any}/{all}`.
- ينطبق على `report_player` و`forms_player` (محلي).

### 2.28 تقاريري + لوحات المعلومات
- `/my-reports/`: المفضلة (`localStorage:my_reports` + نجمة `toggleMyReport`) بجريد 3 أعمدة + مخطط مصغر (`/api/rml/groups`).
- `/dashboards/`: مربعات مخططات (`localStorage:dash_charts`) + شجرة اختيار (تطبيق ← تقرير ← مخطط) + `DASH_PICK` رقمي + مخطط مخصص `＋` للأعمدة.
- التنقل: لوحات المعلومات أول عنصر؛ حُذفت الرئيسية/التقارير المفضلة الميتة؛ رجوع ذكي `goBackSmart(referrer)`.

### 2.29 مستنداتي (`/my-docs/`)
- حفظ نسخة HTML (`printLastHtml`) + مصغرة A4 (`420x594`) + شبكة بطاقات + عرض iframe (`sandbox allow-same-origin allow-modals` + إعفاء `xframe`) + API‏ (`list/save/view/delete/thumb`).
- التشفير صيغة `MDE1`: `MAGIC|salt16|iv16|AES-256-CBC|hmac32` بمفتاحين مشتقين `PBKDF2-HMAC-SHA256 200k` من `SECRET_KEY`؛ ملفات Fernet القديمة تُفتح وتُرقّى تلقائيًا؛ سكربت مستقل `open_doc.py` (ترويسة ← اشتقاق ← HMAC ← فك ← متصفح).
- إجراءات البطاقة: عرض + تنزيل HTML + طباعة (iframe مؤقت) فعالة؛ توقيع واعتماد + إرسال معطلتان بشارة `قريبًا` (بانتظار الآلية وتطبيق التخويل).

### 2.30 التوبار المركزي + حالة الاتصال
- التوبار مركزي `h-11` ثابت في `base.html` (+ `block topbar`)؛ حالة الاتصال (`connStatusPill` من `last_check_ok`)؛ المشغلات مضغوطة (`w-8`, `px-2 py-1`, `w-60`)؛ حُذفت شارة `مباشر`/تبويب `SQL`.
- إخفاء/إظهار الأعمدة (`hiddenCols/rml_hidden_*` + مدير أعمدة) محترم في الطباعة والتصدير.
- `invoice_report.rml`: عدد القطع أصبح `SUM` فرعيًا على `BILL_NO` (تحقق `2630031210148913 → 2`) + `distinct` رقم الفاتورة.

### 2.31 التصدير والطباعة
- `/api/dml/xlsx`: بث مباشر بلا سقف (`dml_python/xlsx.py`)؛ `/api/dml/pdf`: reportlab عربي (`dml_python/pdf.py`)؛ `/api/dml/doc-pdf`: Chrome headless؛ تقدم XHR + زر PDF في بانل الطباعة فقط.
- `DATA_UPLOAD_MAX_MEMORY_SIZE=50MB`.

### 2.32 دليل المستخدم
- `USER_GUIDE_AR.md` (جذر المشروع): دليل عربي للمستخدم النهائي (تنقل/تقارير/تقاريري/لوحات/مستنداتي/إعدادات/اتصالات IoT/قواعد أعمال).

### 2.33 مصمم النماذج FMLK + تبويبة التفاصيل + لوحة المستندات
- `forms-designer/` أصبح معالج FMLK مستقل (`forms_wizard_modal/script.html`): بيانات النموذج + ورقة الجداول (أساسي + متفرعة one-to-many: جدول/اسم/عمود أساسي PK/عمود ربط FK/أعمدة الشبكة) + ورقة المدخلات (الوجهة/الحقل/اسم العرض/الأيقونة/مودال نوع البيانات/التبويب/مطلوب/محسوب+تعبير `[col]`/قابل للإدخال) + ورقة التبويبات (بدل المجموعات — تُبنى من category كما في ملفات FMLK) + ورقة المستندات (DML مربوطة بـ source=base) — بلا مخططات؛ الحفظ عبر `models/design/` (overwrite للتعديل) + ترحيل اختياري.
- مودال النوع: شبكة `field-types` + خيارات (قيمة/تسمية/لون للحالات) + مصادر `[table.column]` + مرجع (lookup/datamodal) + أب (cascading) + فاصل/سماح (list/multiselect).
- compiler: حقل `icon`/`destination` + لون `<option color>` + قسم `<details>` (FMLKDetail) + نوعان `status` (radio ملونة) و `datamodal` (شبكة منبثقة)؛ `api_fmlk_metadata` ترجع `details`.
- engine/views للنماذج: `_fmlk_resolve_db` (اتصال النموذج المحلي القابل لـ SQL بدل الثابت) + `create_record` يعيد `id` عبر `RETURNING` (لربط التفاصيل) + `api/fmlk/branch` و `branch/save` (استبدال صفوف المتفرع لمفتاح الأساسي).
- المشغل: أيقونة بجانب التسمية + `status` radio + `datamodal` شبكة بحث + تبويبة التفاصيل (multi-grid أسفل النموذج عند الحفظ/التعديل، تُحفظ بعد الأساسي) + إخفاء مدخلات الوجهات المتفرعة من الرئيسي والشبكة + لوحة `docPrintPanel` (مستندات DML للسجل الحالي: عرض/طباعة/حفظ/PDF) — وحُذف زر `printGrid` القديم.
