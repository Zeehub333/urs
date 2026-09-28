# مثال: نظام صرافة وتحويلات مبني بمحركات URS2

وثيقة تصميم عملية: كيف تُبنى **الإدخالات (نماذج FMLK)** و**تقارير الحوالات (تقارير RML)**
بنفس المحركات الموجودة في هذا المشروع، وبنفس أسلوب الملفات الحقيقية
(`tr_networks.fmlk`، `imported_targets.fmlk`، `delivered_transfers.rml`).

## 1) الفكرة العامة

| الطبقة | المحرك | الملف | مثال حقيقي |
|---|---|---|---|
| الاتصالات | `urs.models.Connection` + `config/dbconf.py` | `odex/system/settings/connections.fmlk` | `ies2026` (SQL Server)، `rex_local` (PG) |
| الإدخالات | `fmlk_engine` (نماذج) | `*.fmlk` | `tr_networks.fmlk`، `imported_targets.fmlk` |
| التقارير | `rml_python` (تقارير) | `*.rml` | `delivered_transfers.rml` |
| العرض | `forms_player.html` / `report_player.html` | — | بحث، ترشيح، ترقيم كسول |

القاعدة الذهبية:

- **كل جدول يعيش في اتصال واحد** → التقرير يعمل `direct` (مباشر، بلا ترحيل).
- **تقرير يجمع اتصالين** → المحرك يرحّل نسخة إلى PG المحلي (`public` schema)
  أو يعمل `distributed=true` (جلب مستقل من كل اتصال + دمج في Python).

## 2) الاتصالات المقترحة للمثال

| id | الاسم | المحرك | قاعدة البيانات | الاستخدام |
|---|---|---|---|---|
| 2 | `ies2026` | sqlserver | `R2026` / `dbo` | جداول العمليات (الحوالات) |
| 6 | `rex_local` | postgres | `postgres` / `ies202601` | الجداول المرجعية (الشبكات، الجهات) |
| 1 | `urs_local` | postgres | `urs` / `main_hq_2026` | الترحيل المحلي + التقارير النهائية |

## 3) الإدخالات (FMLK): 5 نماذج

### 3.1 نموذج العملات `ex_currencies.fmlk`

```xml
<?xml version="1.0" encoding="utf-8"?>
<fml>
  <fml_metadata name="ex_currencies" displayName="العملات" table="currencies"
      model_type="form" schema="ies202601" connection="rex_local"
      category="صرافة" icon="fa-coins"/>
  <tabs>
    <tab id="البيانات الأساسية" name="البيانات الأساسية" alias="البيانات الأساسية" sort_order="1"/>
  </tabs>
  <fields>
    <field id="1" name="id" alias="المعرف" dataType="INTEGER" inputType="number"
        primary_key="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="2" name="code" alias="الرمز" dataType="VARCHAR" inputType="text"
        required="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="3" name="name" alias="الاسم" dataType="VARCHAR" inputType="text"
        required="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="4" name="rate" alias="سعر الصرف" dataType="NUMERIC" inputType="number"
        tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
  </fields>
</fml>
```

### 3.2 نموذج الشبكات `ex_networks.fmlk` (مطابق لأسلوب `tr_networks.fmlk`)

```xml
<?xml version="1.0" encoding="utf-8"?>
<fml>
  <fml_metadata name="ex_networks" displayName="شبكات الصرف" table="networks"
      model_type="form" schema="ies202601" connection="rex_local"
      category="صرافة" icon="fa-network-wired"/>
  <tabs>
    <tab id="البيانات الأساسية" name="البيانات الأساسية" alias="البيانات الأساسية" sort_order="1"/>
  </tabs>
  <fields>
    <field id="1" name="id" alias="رقم الشبكة" dataType="INTEGER" inputType="number"
        tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="2" name="name" alias="الاسم" dataType="VARCHAR" inputType="text"
        required="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="3" name="classification" alias="الفئة" dataType="VARCHAR" inputType="select"
        tab="البيانات الأساسية" destination="main" category="البيانات الأساسية">
      <options>
        <option value="0">داخلية</option>
        <option value="1">خارجية</option>
      </options>
    </field>
    <field id="4" name="commission_rate" alias="نسبة العمولة %" dataType="NUMERIC" inputType="number"
        tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
  </fields>
</fml>
```

### 3.3 نموذج العملاء `ex_customers.fmlk`

```xml
<fml>
  <fml_metadata name="ex_customers" displayName="العملاء" table="customers"
      model_type="form" schema="dbo" connection="ies2026"
      category="صرافة" icon="fa-users"/>
  <tabs>
    <tab id="البيانات الأساسية" name="البيانات الأساسية" alias="البيانات الأساسية" sort_order="1"/>
  </tabs>
  <fields>
    <field id="1" name="id" alias="المعرف" dataType="INTEGER" inputType="number"
        primary_key="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="2" name="full_name" alias="الاسم الكامل" dataType="VARCHAR" inputType="text"
        required="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="3" name="national_id" alias="رقم الهوية" dataType="VARCHAR" inputType="text"
        tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="4" name="phone" alias="الهاتف" dataType="VARCHAR" inputType="text"
        tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
  </fields>
</fml>
```

### 3.4 نموذج الحوالة الواردة `ex_incoming.fmlk` (الحركة الأساسية)

```xml
<fml>
  <fml_metadata name="ex_incoming" displayName="حوالة واردة" table="tblIncomingTransfers"
      model_type="form" schema="dbo" connection="ies2026"
      category="الحوالات" icon="fa-money-bill-transfer"/>
  <tabs>
    <tab id="البيانات الأساسية" name="البيانات الأساسية" alias="البيانات الأساسية" sort_order="1"/>
    <tab id="الصرف" name="الصرف" alias="الصرف" sort_order="2"/>
  </tabs>
  <fields>
    <field id="1" name="PublicNumber" alias="الرقم العام" dataType="VARCHAR" inputType="text"
        required="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="2" name="SenderName" alias="المرسل" dataType="VARCHAR" inputType="text"
        tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="3" name="ReceiverName" alias="المستلم" dataType="VARCHAR" inputType="text"
        required="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="4" name="Amount" alias="المبلغ" dataType="NUMERIC" inputType="number"
        required="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="5" name="CurrencyID" alias="العملة" dataType="INTEGER" inputType="select"
        tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"
        refTable="ies202601.currencies" refFk="id" refDisplay="name">
      <options><option>[ies202601.currencies.name]</option></options>
    </field>
    <field id="6" name="Delivered" alias="تم الصرف؟" dataType="INTEGER" inputType="boolean"
        tab="الصرف" destination="main" category="الصرف"/>
    <field id="7" name="Notes" alias="ملاحظات" dataType="VARCHAR" inputType="text"
        tab="الصرف" destination="main" category="الصرف"/>
  </fields>
</fml>
```

> لاحظ `refTable/refFk/refDisplay` + `<option>[...]</option>`: نفس أسلوب
> `imported_targets.fmlk` السطر 12–16 — قائمة منسدلة تُجلب من جدول آخر
> (حتى من اتصال آخر عبر `api/fmlk/options?conn=&schema=`).

### 3.5 نموذج التأكيد `ex_confirm.fmlk` (مطابق لأسلوب `imported_targets.fmlk`)

```xml
<fml>
  <fml_metadata name="ex_confirm" displayName="تأكيدات الصرف" table="confirmed_transfers"
      model_type="form" schema="ies202601" connection="rex_local"
      category="الحوالات" icon="fa-circle-check"/>
  <tabs>
    <tab id="البيانات الأساسية" name="البيانات الأساسية" alias="البيانات الأساسية" sort_order="1"/>
  </tabs>
  <fields>
    <field id="1" name="id" alias="المعرف" dataType="INTEGER" inputType="number"
        primary_key="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="2" name="express_number" alias="الرقم المرجعي" dataType="VARCHAR" inputType="text"
        required="true" tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
    <field id="3" name="network_id" alias="الشبكة" dataType="INTEGER" inputType="select"
        tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"
        refTable="ies202601.networks" refFk="id" refDisplay="name">
      <options><option>[ies202601.networks.name]</option></options>
    </field>
    <field id="4" name="disbursement_destination" alias="جهة الصرف" dataType="VARCHAR" inputType="text"
        tab="البيانات الأساسية" destination="main" category="البيانات الأساسية"/>
  </fields>
</fml>
```

## 4) التقارير (RML): 3 تقارير للحوالات

### 4.1 تقرير الحوالات المنصرفة `ex_delivered.rml` (اتصال واحد → مباشر)

نفس بنية `delivered_transfers.rml`: كل الحقول `conn_id="2"` على SQL Server،
فيعمل `direct` بلا ترحيل.

```xml
<?xml version="1.0" encoding="utf-8"?>
<rml>
  <rpt_metadata name="ex_delivered" displayName="تقرير الحوالات المنصرفة"
      category="الحوالات" schema="dbo" icon="fa-chart-bar"
      connection="ORCL_PROD" namespace="ex" stage_max_rows="1000000"/>
  <rml_connections>
    <connection id="1" connection_id="2"/>
  </rml_connections>
  <fields>
    <field id="1" name="PublicNumber" type="VARCHAR" conn_id="2" table_source="tblIncomingTransfers"/>
    <field id="2" name="SenderName" type="VARCHAR" conn_id="2" table_source="tblIncomingTransfers"/>
    <field id="3" name="ReceiverName" type="VARCHAR" conn_id="2" table_source="tblIncomingTransfers"/>
    <field id="4" name="Amount" type="NUMERIC" conn_id="2" table_source="tblIncomingTransfers"/>
    <field id="5" name="Delivered" type="INTEGER" conn_id="2" table_source="tblIncomingTransfers"/>
    <field id="6" name="ProvinceName" type="VARCHAR" conn_id="2" table_source="tblProvinces"/>
  </fields>
  <columns>
    <column id="1" name="col1" alias="الرقم العام" expr="[[tblIncomingTransfers.PublicNumber]]" dataType="VARCHAR" join_type="one_to_one" icon="fa-hashtag"/>
    <column id="2" name="col2" alias="المرسل" expr="[[tblIncomingTransfers.SenderName]]" dataType="VARCHAR" join_type="one_to_one" icon="fa-hashtag"/>
    <column id="3" name="col3" alias="المستلم" expr="[[tblIncomingTransfers.ReceiverName]]" dataType="VARCHAR" join_type="one_to_one" icon="fa-hashtag"/>
    <column id="4" name="col4" alias="المبلغ" expr="[[tblIncomingTransfers.Amount]]" dataType="NUMERIC" join_type="one_to_one" icon="fa-hashtag"/>
    <column id="5" name="col5" alias="المحافظة" expr="[[tblProvinces.ProvinceName]]" dataType="VARCHAR" join_type="one_to_one" icon="fa-hashtag"/>
  </columns>
  <links>
    <link id="1" from_table="tblIncomingTransfers" from_col="ProvinceID" to_table="tblProvinces" to_col="ID" rel_type="one_to_one"/>
  </links>
  <table_opts>
    <table name="tblIncomingTransfers" conn="2" is_default="1"/>
    <table name="tblProvinces" conn="2"/>
  </table_opts>
  <general_where>([[tblIncomingTransfers.Delivered]] = 1)</general_where>
</rml>
```

نقاط مهمة (مأخوذة من سلوك المحرك الفعلي):

- `[[table.column]]` تُحل تلقائياً؛ `[...]` صالحة أيضاً.
- `general_where` يُدفع للخادم (`WHERE [Delivered] = 1`) — ضع فيه شرط التصفية
  دائماً للجداول الكبيرة (حماية `stage_max_rows` ترفض السحب الكامل).
- `table_opts` تحدد الجدول الافتراضي (`is_default="1"`) واتصال كل جدول.
- `links` تحدد مفاتيح الربط صراحة (أولوية على الاستدلال التلقائي).

### 4.2 تقرير التأكيدات `ex_confirmed.rml` (اتصالان → ترحيل محلي)

حوالات MSSQL + تأكيدات PG: المحرك ينسخ الجداول إلى `public` على PG المحلي
(`urs/dev`) ثم ينفذ SQL واحد هناك.

```xml
<?xml version="1.0" encoding="utf-8"?>
<rml>
  <rpt_metadata name="ex_confirmed" displayName="تقرير تأكيدات الحوالات"
      category="الحوالات" schema="dbo" icon="fa-chart-bar"
      connection="ORCL_PROD" namespace="ex" stage_max_rows="500000" direct="0"/>
  <rml_connections>
    <connection id="1" connection_id="2"/>
    <connection id="2" connection_id="6"/>
  </rml_connections>
  <fields>
    <field id="1" name="PublicNumber" type="VARCHAR" conn_id="2" table_source="tblIncomingTransfers"/>
    <field id="2" name="Amount" type="NUMERIC" conn_id="2" table_source="tblIncomingTransfers"/>
    <field id="3" name="express_number" type="VARCHAR" conn_id="6" table_source="confirmed_transfers"/>
    <field id="4" name="network_id" type="INTEGER" conn_id="6" table_source="confirmed_transfers"/>
  </fields>
  <columns>
    <column id="1" name="col1" alias="الرقم العام" expr="[[tblIncomingTransfers.PublicNumber]]" dataType="VARCHAR" join_type="one_to_one" icon="fa-hashtag"/>
    <column id="2" name="col2" alias="الرقم المرجعي للتأكيد" expr="[[confirmed_transfers.express_number]]" dataType="VARCHAR" join_type="one_to_one" icon="fa-hashtag"/>
  </columns>
  <links>
    <link id="1" from_table="tblIncomingTransfers" from_col="PublicNumber" to_table="confirmed_transfers" to_col="express_number" rel_type="one_to_one"/>
  </links>
  <table_opts>
    <table name="tblIncomingTransfers" conn="2" is_default="1"/>
    <table name="confirmed_transfers" conn="6"/>
  </table_opts>
  <general_where>([[tblIncomingTransfers.Delivered]] = 1)</general_where>
</rml>
```

> **أنواع المطابقة في `<link>`** (`match` + `pattern`):
> - `match="exact"` (الافتراضي): `from_col = to_col`.
> - `match="contains"`: إحدى القيمتين داخل الأخرى — مثالي عندما يحمل عمود الملاحظات نصاً مثل «رقم الاكسبرس 202645226783» والعمود الآخر الرقم منفرداً:
>   ```xml
>   <link from_table="tblIncomingTransfers" from_col="notes" to_table="confirmed_transfers" to_col="express_number" match="contains"/>
>   ```
> - `match="regex" pattern="(\d+)"`: استخراج النمط من الطرفين ومقارنة المستخرج — أدق من الاحتواء عندما يتضمن النص أرقاماً أخرى.
> - يعمل في JOIN واحد (PG/Oracle) ودمج Python عبر الاتصالات؛ `regex` على MSSQL يُحوَّل تلقائياً لمسار Python. الفلترة المباشرة على جدول مربوط ضبابياً غير مدعومة (اعرض العمود ثم رشّح).

> `direct="0"` يجبر الترحيل. البديل الأسرع للجداول الضخمة:
> `payload.distributed=true` (جلب مستقل من كل اتصال + دمج Python + شريط تقدم).

### 4.3 تقرير ملخص يومي `ex_daily.rml` (تجميع + مبلغ)

```xml
<rml>
  <rpt_metadata name="ex_daily" displayName="الملخص اليومي للحوالات"
      category="الحوالات" schema="dbo" icon="fa-chart-bar"
      connection="ORCL_PROD" namespace="ex"/>
  <rml_connections>
    <connection id="1" connection_id="2"/>
  </rml_connections>
  <fields>
    <field id="1" name="TheDate" type="DATE" conn_id="2" table_source="tblIncomingTransfers"/>
    <field id="2" name="Amount" type="NUMERIC" conn_id="2" table_source="tblIncomingTransfers"/>
    <field id="3" name="CurrencyID" type="INTEGER" conn_id="2" table_source="tblIncomingTransfers"/>
    <field id="4" name="CurrencyName" type="VARCHAR" conn_id="2" table_source="tblCurrencies"/>
  </fields>
  <columns>
    <column id="1" name="col1" alias="التاريخ" expr="[[tblIncomingTransfers.TheDate]]" dataType="DATE" join_type="one_to_one" icon="fa-hashtag"/>
    <column id="2" name="col2" alias="العملة" expr="[[tblCurrencies.CurrencyName]]" dataType="VARCHAR" join_type="one_to_one" icon="fa-hashtag"/>
    <column id="3" name="col3" alias="إجمالي المبلغ" expr="SUM([[tblIncomingTransfers.Amount]])" dataType="NUMERIC" join_type="one_to_one" icon="fa-hashtag"/>
  </columns>
  <links>
    <link id="1" from_table="tblIncomingTransfers" from_col="CurrencyID" to_table="tblCurrencies" to_col="ID" rel_type="one_to_one"/>
  </links>
  <table_opts>
    <table name="tblIncomingTransfers" conn="2" is_default="1"/>
    <table name="tblCurrencies" conn="2"/>
  </table_opts>
</rml>
```

## 5) خطوات البناء بالمصممين (بدون كتابة XML يدوياً)

1. **الاتصالات**: `settings` → `connections.fmlk` — عرّف `ies2026` و`rex_local`
   (المحرك يوجّه كل حقل حسب `conn_id`).
2. **الإدخالات**: `forms-designer` → أنشئ `ex_currencies` → `ex_networks` →
   `ex_customers` → `ex_incoming` → `ex_confirm`. استخدم `select` + `refTable`
   للربط بين النماذج (الشبكة، العملة).
3. **التقارير**: `report-designer` →
   - زر **استيراد من جدول**: يولّد `fields` من أعمدة SQL Server/PG مباشرة.
   - زر **تحليل تلقائي**: يقترح `links` من الـFKs والأسماء المشتركة.
   - زر **fetch query**: يعاين SQL الفعلي قبل الحفظ.
   - زر **حفظ**: كتابة فورية للملف (بدون جلب).
   - زر **حفظ واختبار**: مسودة `__draft_*.rml` + مهمة جلب + شريط تقدم واحد،
     ثم نشر أو نشر-رغم-الفشل.
4. **التشغيل**: `report_player` — ترشيح متقدم (AND/OR)، تحميل كسول،
   `pageSize=all` للتصدير، `distributed=true` للتقارير متعددة الاتصالات.

## 6) قواعد ذهبية من سلوك المحرك الفعلي

1. **عمود التصفية يجب أن يكون حقلاً معرّفاً**: شرط `general_where` على عمود
   غير معرّف في `fields` يُسقط بصمت → ثم يرفض المحرك السحب ("الجدول ضخم
   بلا شرط تصفية"). عرّف `Delivered` كحقل أولاً.
2. **اكتب المراجع `[[table.column]]`** (أقواس مزدوجة) — المحرك يطبعها
   `[column]` صالحة لـT-SQL.
3. **النصوص فقط لـ`TRANSLATE()`**: البحث الذكي يطبّع التوحيد العربي على
   الأعمدة النصية فقط؛ القيم المستحيلة على رقمي/تاريخ (`31010001035`
   على `TINYINT`) تُترجم `1=0` بدل أن تفجر SQL Server.
4. **MSSQL بلا DDL = ترحيل محلي**: حساب بلا `CREATE TABLE` على SQL Server
   يعني أن أي `staging` بعيد مستحيل — الجداول تُنسخ إلى `public` على PG
   المحلي، والاستعلام النهائي ينفذ هناك.
5. **المراقبة**: كل كتابة/قراءة تُسجّل `POST /api/monitor/touch/` بهوية الجهاز
   (`COMPUTERNAME` + IP) — مفيد لتدقيق من صرف أي حوالة.
