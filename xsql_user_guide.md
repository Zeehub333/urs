# XSQL — دليل المستخدم

XSQL (Extended SQL) هي لغة استعلام مدمجة في URS2 تتيح لك:

- الإشارة إلى جداول من **اتصالات مختلفة** (PostgreSQL, SQL Server, Oracle) في استعلام واحد.
- استخدام **دوال خطية** (Spreadsheet-style) مثل `SUMIF` و `COUNTIF` و `XLOOKUP` بدلاً من صياغة شروط `WHERE` معقدة.
- الـcompiler يحوّل هذه اللغة إلى SQL أصلي على كل محرك DB، ثم يدمج النتائج في Python — **بدون نسخ ضخم للبيانات**.

المسار التقني:

```
XSQL → Lexer → Parser → Compiler → per-connection SQL → fetch → merge
```

---

## 1) التركيب اللغوي (Syntax)

### 1.1 الجملة الأساسية

```sql
SELECT <expressions>
FROM <namespace>.<table> [AS alias]
[INNER|LEFT|RIGHT JOIN <namespace>.<table> [AS alias] ON <predicate>]
[WHERE <expression>]
[GROUP BY <column>[, <column>...]]
[ORDER BY <column> [ASC|DESC]]
[LIMIT <n>]
```

### 1.2 Namespaces

كل namespace = اتصال queryable واحد (PostgreSQL / SQL Server / Oracle). شكل المرجع:

| الشكل | المعنى | مثال |
|---|---|---|
| `<col>` | عمود من الـFROM الأساسي (يُحل وقت الترجمة) | `id` |
| `<tbl>.<col>` | عمود من جدول | `tbl.x` |
| `<ns>.<tbl>.<col>` | عمود من جدول في namespace محدد | `conn_a.tbl.x` |
| `<ns>.<tbl>` | جدول من namespace محدد | `conn_a.tbl` |
| `<schema>.<tbl>` | جدول من schema محلي على الـprimary | `main_hq_2026.tbl` |

مثال:
```sql
SELECT t.id, c.name
FROM ies2026.dbo.tblIncomingTransfers AS t
INNER JOIN rex_local.ies202601.confirmed_transfers AS c
    ON t.PublicNumber = c.express_number;
```

### 1.3 التعابير (Expressions)

تدعم XSQL:

- **عوامل حسابية**: `+`, `-`, `*`, `/`, `%`
- **عوامل مقارنة**: `=`, `!=`, `<>`, `>`, `<`, `>=`, `<=`
- **عوامل منطقية**: `AND`, `OR`, `NOT`
- **أقواس** لتجميع الأولويات

مثال:
```sql
SELECT id, amount * 1.15 AS with_tax
FROM conn_a.tbl
WHERE (status = 1 OR status = 2) AND amount > 1000;
```

### 1.4 Literals

- **أعداد**: `42`, `3.14`
- **نصوص**: `'hello'`, `"world"`
- **NULL**: `NULL`

### 1.5 التعليقات

```sql
-- هذا تعليق سطر واحد
/* هذا تعليق
   متعدد الأسطر */
```

---

## 2) الدوال الخطية (Linear Functions)

كل دالة من القائمة التالية **تُترجم إلى SQL أصلي** على محرك DB الهدف قدر الإمكان، مع الحفاظ على نفس semantics.

### 2.1 الدوال التجميعية البسيطة

| الدالة | الوصف | الترجمة إلى SQL |
|---|---|---|
| `SUM(x)` | مجموع | `SUM(x)` |
| `MIN(x)` | أصغر قيمة | `MIN(x)` |
| `MAX(x)` | أكبر قيمة | `MAX(x)` |
| `AVG(x)` | متوسط | `AVG(x)` |
| `COUNT(x)` | عدد الصفوف | `COUNT(x)` |

مثال:
```sql
SELECT SUM(amount) AS total, AVG(amount) AS avg_amt, COUNT(*) AS n
FROM conn_a.tbl;
```

### 2.2 الدوال الشرطية (Conditional Aggregates)

هذه الدوال **تمنع كتابة شروط WHERE متكررة** عند رغبتك في حساب تجميع شرطي.

#### `SUMIF(sum_range, criteria_expr, criteria_value)`

مجموع `sum_range` للصفوف التي تطابق فيها `criteria_expr` قيمة `criteria_value`.

**مثال عملي:**
```sql
SELECT
    SUMIF(amount, status, 1) AS delivered_total,
    SUMIF(amount, status, 0) AS pending_total
FROM conn_a.tblIncomingTransfers;
```

**الترجمة إلى SQL (PostgreSQL/SQL Server):**
```sql
SELECT
    SUM(CASE WHEN "status" = 1 THEN "amount" END) AS delivered_total,
    SUM(CASE WHEN "status" = 0 THEN "amount" END) AS pending_total
FROM "tbl";
```

#### `COUNTIF(criteria_expr, criteria_value)`

عدد الصفوف التي تطابق فيها `criteria_expr` قيمة `criteria_value`.

**مثال:**
```sql
SELECT
    COUNTIF(status, 1) AS delivered_count,
    COUNTIF(status, 0) AS pending_count
FROM conn_a.tblIncomingTransfers;
```

**الترجمة:**
```sql
SELECT
    SUM(CASE WHEN "status" = 1 THEN 1 ELSE 0 END) AS delivered_count,
    SUM(CASE WHEN "status" = 0 THEN 1 ELSE 0 END) AS pending_count
FROM "tbl";
```

#### `SUMIFS(sum_range, c1_range, c1_val, c2_range, c2_val, ...)`

مجموع `sum_range` للصفوف التي تطابق **جميع** الشروط.

**مثال — مجموع مبالغ USD المُسلّمة فقط:**
```sql
SELECT
    SUMIFS(amount, currency, 'USD', status, 1) AS usd_delivered_total,
    SUMIFS(amount, currency, 'EUR', status, 1) AS eur_delivered_total
FROM conn_a.tblIncomingTransfers;
```

**الترجمة:**
```sql
SELECT
    SUM(CASE WHEN "currency" = 'USD' AND "status" = 1 THEN "amount" END) AS usd_delivered_total,
    SUM(CASE WHEN "currency" = 'EUR' AND "status" = 1 THEN "amount" END) AS eur_delivered_total
FROM "tbl";
```

#### `COUNTBLANK(range)`

عدد الصفوف التي فيها `range` فارغ (NULL أو `''`).

```sql
SELECT COUNTBLANK(notes) AS missing_notes
FROM conn_a.tblIncomingTransfers;
```

**الترجمة:**
```sql
SELECT
    SUM(CASE WHEN "notes" IS NULL OR "notes" = '' THEN 1 ELSE 0 END) AS missing_notes
FROM "tbl";
```

#### `COUNTA(range)`

عدد القيم غير الفارغة (مثل `COUNTA` في Excel).

```sql
SELECT COUNTA(receiver_phone) AS phones_collected
FROM conn_a.tblIncomingTransfers;
```

**الترجمة:**
```sql
SELECT COUNT("receiver_phone") AS phones_collected FROM "tbl";
```

### 2.3 دوال النوافذ والتسلسل (Window & Series)

#### `SUMBY(amt_col, group_col)`

مجموع `amt_col` على مستوى تكرار `group_col` — أي إجمالي المجموعة مكرراً في كل صف (نافذة، لا تجميع يُخفي الصفوف).

**مثال:**
```sql
SELECT
    dept,
    amount,
    SUMBY(amount, dept) AS dept_total
FROM conn_a.tblIncomingTransfers;
```

**الترجمة (نفسها في Postgres وSQL Server):**
```sql
SELECT
    "dept",
    "amount",
    SUM("amount") OVER (PARTITION BY "dept") AS dept_total
FROM "tbl";
```

#### `COUNTBY(group_col)`

عدد الصفوف على مستوى تكرار `group_col` — حجم كل مجموعة في كل صف.

**مثال:**
```sql
SELECT
    dept,
    COUNTBY(dept) AS dept_size
FROM conn_a.tblIncomingTransfers;
```

**الترجمة:**
```sql
SELECT
    "dept",
    COUNT(*) OVER (PARTITION BY "dept") AS dept_size
FROM "tbl";
```

#### `SERIAL(start, end, step)`

تعبئة سلسلة أرقام أو تواريخ/أوقات: من `start` إلى `end` بخطوة `step` (الوسيط الثالث هو **حجم الخطوة**، مثل `GENERATE_SERIES`).

**مثال أرقام:**
```sql
SELECT SERIAL(1, 10, 2) AS n;
```

**الترجمة حسب المحرك:**
```sql
-- Postgres (وأي محرك غير SQL Server):
SELECT generate_series(1, 10, 2) AS n;
-- SQL Server 2022+ (سلاسل رقمية فقط):
SELECT GENERATE_SERIES(1, 10, 2) AS n;
```

> ملاحظة: صيغة التواريخ (`SERIAL('2024-01-01', '2024-02-01', '7 days')`) تعمل على Postgres فقط؛ SQL Server لا يدعم السلاسل الزمنية في `GENERATE_SERIES`.

### 2.4 علامة `=` قبل التعبير (مثل إكسل)

مثل إكسل تماماً: العمود الذي يبدأ بـ `=` يُفهم كتعبير، وبدونها قيمة ثابتة:

```sql
SELECT
    =amount * 2 AS doubled,
    =SUMBY(amount, dept) AS dept_total,
    500 AS fixed_value
FROM conn_a.tbl;
```

`500` ثابت، و`=amount * 2` تعبير. الصيغتان `amount * 2` و`=amount * 2` متكافئتان (التوافق القديم محفوظ). نفس القاعدة في أعمدة تقارير RML: `=[tbl.col] * 2` يُحل كتعبير.

### 2.5 دوال التحكم (Control)

#### `IF(condition, then_value, else_value)`

تعيد `then_value` إذا كان الشرط محققاً، وإلا `else_value`.

**مثال:**
```sql
SELECT
    IF(SUM(amount) > 1000000, 'high_volume', 'normal') AS volume_band,
    IF(COUNT(*) = 0, 'no_data', 'has_data') AS data_status
FROM conn_a.tblIncomingTransfers;
```

**الترجمة:**
```sql
SELECT
    CASE WHEN (SUM("amount") > 1000000) THEN 'high_volume' ELSE 'normal' END AS volume_band,
    CASE WHEN (COUNT(*) = 0) THEN 'no_data' ELSE 'has_data' END AS data_status
FROM "tbl";
```

### 2.6 الدوال متعددة الاتصالات (Cross-Connection)

هذه الدوال تتطلب **بحث في اتصال آخر** أثناء معالجة كل صف، لذلك تُنفّذ في Python بعد جلب البيانات.

#### `XLOOKUP(value, lookup_range, return_range, [not_found])`

تبحث عن `value` في `lookup_range` وتُرجع القيمة المقابلة من `return_range`.

**مثال — جلب اسم الشبكة لكل حوالة:**
```sql
SELECT
    t.PublicNumber,
    XLOOKUP(t.network_id, networks.id, networks.name, 'unknown') AS network_name
FROM conn_a.tblIncomingTransfers AS t;
```

**ملاحظة:** الـcompiler يولّد marker column، والـview يستبدلها بقيم حقيقية عبر fetch على اتصال `networks`.

#### `VLOOKUP(value, lookup_range, return_range, [not_found])`

مثل `XLOOKUP` لكن مع افتراض أن `lookup_range` مفروز (أسرع للبيانات الكبيرة).

```sql
SELECT
    t.id,
    VLOOKUP(t.id, lookup.id, lookup.value, 0) AS lookup_value
FROM conn_a.tbl AS t;
```

#### `FILTER(range, criteria_expr, criteria_value)`

تُرجع القيمة من `range` لأول صف يطابق الشرط (مثل FILTER في Excel).

```sql
SELECT
    FILTER(prices, product, 'A') AS price_of_A,
    FILTER(quantities, product, 'B') AS qty_of_B
FROM conn_a.products;
```

### 2.7 مطابقة النصوص: regex و wildcard

#### `WILDCARDMATCH(text, 'a*b?')`

مطابقة كاملة بنمط wildcard: `*` أي مقطع، `?` حرف واحد. تُترجم إلى `LIKE` — تعمل على كل القواعد (بما فيها MSSQL)، وتصلح في `WHERE` أيضاً.

```sql
SELECT
    notes,
    WILDCARDMATCH(notes, 'رقم الاكسبرس *') AS has_express_no
FROM conn_a.tblIncomingTransfers
WHERE WILDCARDMATCH(notes, 'رقم الاكسبرس *');
```

#### `REGEXMATCH(text, pattern)`

`True` عندما يوجد النمط داخل النص (`re.search`). على Postgres تُترجم إلى `~` (وتصلح في `WHERE`)؛ على MSSQL تُحسب في Python بعد الجلب (لا تصلح في `WHERE` على MSSQL).

**ملاحظة الشرطة المائلة:** اكتب `\d` مباشرة (المحلّل يحفظ الشرطة)؛ `\\` تعني شرطة حرفية.

```sql
SELECT
    notes,
    REGEXMATCH(notes, '\d{10,}') AS has_long_number
FROM conn_a.tblIncomingTransfers;
```

#### `REGEXEXTRACT(text, pattern)`

تستخرج أول مجموعة إمساك `(\d+)` — أو المطابقة الكاملة بلا مجموعات — أو `NULL`. على Postgres تُترجم إلى `substring(text from pattern)`؛ على MSSQL تُحسب في Python.

```sql
SELECT
    notes,
    REGEXEXTRACT(notes, '(\d{10,})') AS express_no
FROM conn_a.tblIncomingTransfers;
```

---

## 3) استعلامات JOIN متعددة الاتصالات

### 3.1 البنية

```sql
SELECT <columns>
FROM <ns_a>.<table_a> AS <alias_a>
[JOIN_TYPE] JOIN <ns_b>.<table_b> AS <alias_b>
    ON <alias_a>.<col_a> = <alias_b>.<col_b>
[WHERE ...]
```

### 3.2 كيف يعمل التنفيذ

1. **الـcompiler** يولّد SQL منفصل لكل اتصال:
   - Primary SQL على `ns_a` (مع الـWHERE، الـGROUP BY، الـORDER BY).
   - Secondary SQL بسيط لكل JOIN: `SELECT * FROM table_b`.
2. **الـview** يجلب كل SQL على اتصاله.
3. **Python merge** يفهرس secondary على عمود الربط (`secondary_col`)، ثم لكل صف primary يجلب `secondary_row[secondary_col]`.

### 3.3 مثال كامل — الحوالات مع التأكيدات

```sql
SELECT
    t.PublicNumber,
    t.Amount,
    c.express_number AS confirmation_ref,
    t.Delivered
FROM ies2026.dbo.tblIncomingTransfers AS t
INNER JOIN rex_local.ies202601.confirmed_transfers AS c
    ON t.PublicNumber = c.express_number
WHERE t.Delivered = 1
LIMIT 1000;
```

### 3.4 الملاحظات المهمة

- **كل alias من اتصال مختلف**: يمكنك استخدام `FROM conn_a.tbl AS a` ثم `JOIN conn_b.tbl AS b ON a.id = b.id` — الـcompiler يتعرف تلقائياً.
- **schema ضمني**: إذا لم يحدد المرجع schema (مثل `FROM conn_a.tbl`)، يستخدم الـengine الـdefault schema للاتصال.
- **LIMIT**: يُطبق على primary fetch فقط. الـmerge يحفظ الترتيب.
- **الـperformance**: الـsecondary يُجلب كاملاً (SELECT *). للجداول الضخمة، استخدم `WHERE` في الاستعلام.

---

## 4) استدعاءات HTTP

### 4.1 تجميع استعلام (Compile)

**`POST /api/xsql/compile/`**

```json
{
  "sql": "SELECT a.id FROM conn_a.tbl AS a INNER JOIN conn_b.lookup AS b ON a.id = b.id",
  "namespaces": {
    "conn_a": "2",
    "conn_b": "6"
  }
}
```

**Response:**
```json
{
  "ok": true,
  "primary_sql": "SELECT \"id\" FROM \"tbl\"",
  "primary_conn": "conn_a",
  "primary_columns": ["id"],
  "secondaries": [
    {"conn_key": "conn_b", "schema": null, "table": "lookup",
     "sql": "SELECT * FROM \"lookup\""}
  ],
  "merges": [
    {"primary_col": "id", "secondary_conn": "conn_b",
     "secondary_table": "lookup", "secondary_col": "id", "side": "INNER"}
  ],
  "final_columns": ["id"]
}
```

### 4.2 تنفيذ استعلام (Execute)

**`POST /api/xsql/execute/`**

نفس body، response:

```json
{
  "ok": true,
  "rows": [{"id": 123, "amount": 500, "lookup.id": 123}, ...],
  "columns": ["id", "amount", "lookup.id"],
  "sql_per_conn": {
    "conn_a": "SELECT \"id\" FROM \"tbl\"",
    "conn_b": "SELECT * FROM \"lookup\""
  }
}
```

### 4.3 mapping الـnamespaces

الـbody يحوي `namespaces` (dict) يربط كل namespace alias في SQL بـ connection id حقيقي:

```json
{
  "namespaces": {
    "ies2026": "2",
    "rex_local": "6",
    "urs_local": "1"
  }
}
```

أو ببساطة مرر `conn_keys` (قائمة IDs) ويستبدل الـengine بـ`ns_<id>`:

```json
{
  "conn_keys": ["2", "6"]
}
```

يصبح الـSQL:
```sql
SELECT * FROM ns_2.tbl INNER JOIN ns_6.lookup ON ...
```

---

## 5) أمثلة تطبيقية

### 5.1 ملخص يومي بحوالات مفلترة

```sql
SELECT
    TheDate,
    CurrencyName,
    SUM(amount) AS daily_total,
    SUMIF(amount, status, 1) AS delivered,
    COUNTIF(amount, status, 0) AS pending,
    IF(SUM(amount) > 500000, 'busy', 'quiet') AS day_intensity
FROM ies2026.dbo.tblIncomingTransfers
WHERE TheDate >= '2026-01-01'
GROUP BY TheDate, CurrencyName
ORDER BY TheDate DESC
LIMIT 30;
```

### 5.2 كشف التحويلات عالية القيمة المُعلّقة

```sql
SELECT
    PublicNumber,
    SenderName,
    ReceiverName,
    Amount,
    CurrencyName,
    DATEDIFF(day, EnterTime, GETDATE()) AS days_pending
FROM ies2026.dbo.tblIncomingTransfers
WHERE Delivered = 0
    AND Amount > 10000
    AND DATEDIFF(day, EnterTime, GETDATE()) > 7
ORDER BY Amount DESC
LIMIT 50;
```

### 5.3 نسبة التأكيد لكل شبكة

```sql
SELECT
    n.name AS network_name,
    COUNT(t.PublicNumber) AS total_received,
    COUNT(c.express_number) AS total_confirmed,
    CASE
        WHEN COUNT(t.PublicNumber) = 0 THEN 0
        ELSE COUNT(c.express_number) * 100.0 / COUNT(t.PublicNumber)
    END AS confirmation_rate_pct
FROM ies2026.dbo.tblIncomingTransfers AS t
INNER JOIN rex_local.ies202601.networks AS n
    ON t.OutgoingNetworkID = n.id
LEFT JOIN rex_local.ies202601.confirmed_transfers AS c
    ON t.PublicNumber = c.express_number
WHERE t.Delivered = 1
GROUP BY n.name
ORDER BY confirmation_rate_pct ASC;
```

### 5.4 ملخص متعدد العملات مع تحويل (مثال متقدم)

```sql
SELECT
    CurrencyName,
    SUM(amount) AS original_amount,
    IF(currency = 'USD', SUM(amount) * 1.0,
       IF(currency = 'EUR', SUM(amount) * 1.1,
          IF(currency = 'SAR', SUM(amount) * 0.27, SUM(amount)))) AS usd_equivalent,
    COUNT(*) AS transactions
FROM ies2026.dbo.tblIncomingTransfers
WHERE TheDate >= '2026-01-01' AND Delivered = 1
GROUP BY CurrencyName, currency
ORDER BY usd_equivalent DESC;
```

### 5.5 البحث عبر اتصالين (XLOOKUP)

```sql
SELECT
    t.PublicNumber,
    t.Amount,
    XLOOKUP(t.network_id, n.id, n.name, 'UNKNOWN') AS network_name,
    XLOOKUP(t.AgentID, a.id, a.name, 'UNKNOWN') AS agent_name
FROM ies2026.dbo.tblIncomingTransfers AS t
WHERE t.Delivered = 1
LIMIT 100;
```

---

## 6) مقارنة مع RML الكلاسيكي

| الميزة | RML (`*.rml`) | XSQL |
|---|---|---|
| الملفات | XML ثابت، حقول + جداول + links | استعلام واحد نصي |
| تعدد الاتصالات | عبر `rml_connections` + links | namespace واحد لكل اتصال |
| ترحيل البيانات | عبر `_ensure_api_staged` | لا ترحيل — `distributed=true` في الـRML |
| الدوال الشرطية | صياغة طويلة في `general_where` | `SUMIF/COUNTIF/SUMIFS` مدمجة |
| صياغة JOIN | عبر `<links>` XML | `INNER JOIN ... ON ...` SQL |
| إضافة حقل جديد | تعديل XML | `t.new_col` في SELECT |
| المعاينة | عبر `forms_player` | عبر `/api/xsql/compile/` (يُرجع SQL لكل اتصال) |

**اختر XSQL** إذا:
- تريد استعلامات سريعة التطوير (بدون XML).
- تحتاج دوال خطية مثل `SUMIF`.
- تستعلم من اتصالين أو أكثر بدون نسخ البيانات.
- تريد اختبار استعلام بدون حفظه في ملف.

**اختر RML** إذا:
- تريد تعريف ثابت قابل للنشر كمحتوى `my_reports`.
- تحتاج charts و groups و totals متقدمة.
- لديك joins معقدة عبر روابط ثابتة.

---

## 7) أفضل الممارسات

1. **استخدم namespaces في mapping الـbody** — لا تعتمد على `conn_keys` التلقائي في الإنتاج:
   ```json
   {"namespaces": {"prod_pg": "1", "mssql": "2", "reporting_pg": "6"}}
   ```

2. **اختبر بـcompile قبل execute** — `compile` يكشف أخطاء syntax بسرعة:
   ```
   POST /api/xsql/compile/  → {primary_sql, merges, secondaries}
   ```

3. **استخدم aliases لتجنب تعارض الأسماء**:
   ```sql
   FROM conn_a.tbl AS a INNER JOIN conn_b.tbl AS b ON a.id = b.id
   ```

4. **استخدم `LIMIT` للتحكم في حجم النتائج**:
   ```sql
   SELECT ... FROM huge_table LIMIT 10000;
   ```

5. **لـmulti-connection JOIN، تأكد من index عمود الربط** على كلا الجانبين — وإلا الـmerge يصبح بطيئاً.

6. **احفظ استعلامات XSQL في ملفات `*.xsql`** ضمن `odex/system/<app>/` كمرجع (الـengine لا يقرأها مباشرة، لكنها مفيدة كـdocumentation).

---

## 8) استكشاف الأخطاء

| الخطأ | السبب المحتمل | الحل |
|---|---|---|
| `parse error: Expected kw 'FROM'` | نص غير معروف أو خطأ syntax | تحقق من الفواصل والأقواس |
| `compile error: name 'c' is not defined` | خطأ داخلي في الـcompiler | أرسل الـSQL للدعم الفني |
| `primary conn '' not queryable` | الـnamespace غير موجود في mapping الـbody | تأكد من `namespaces: {"conn_a": "2"}` |
| `compile error: Unknown function` | اسم دالة خطأ | استخدم قائمة الدوال المعتمدة فقط |
| نتيجة فارغة رغم بيانات موجودة | شرط WHERE خاطئ أو عمود غير موجود | استخدم `/api/xsql/compile/` لمعاينة SQL |
| `IM002: Data source name not found` | الـdriver المثبت غير متوافق | ثبّت Microsoft ODBC Driver 17 or 18 |

---

## 9) المرجع السريع (Cheatsheet)

```sql
-- Single connection
SELECT id, name FROM conn_a.tbl WHERE status = 1;

-- Aggregation with linear functions
SELECT
    COUNT(*) AS total,
    SUMIF(amount, status, 1) AS delivered,
    COUNTIF(amount, status, 0) AS pending,
    IF(SUM(amount) > 1e6, 'big', 'small') AS scale
FROM conn_a.tbl;

-- Multi-connection JOIN
SELECT a.id, b.name
FROM conn_a.tbl AS a
INNER JOIN conn_b.lookup AS b ON a.id = b.id
WHERE a.delivered = 1;

-- XLOOKUP for cross-connection enrichment
SELECT
    t.id,
    XLOOKUP(t.network_id, n.id, n.name, 'N/A') AS network_name
FROM conn_a.transfers AS t;

-- Compound predicate
SELECT *
FROM conn_a.tbl
WHERE (status = 1 AND amount > 1000)
   OR (status = 2 AND currency = 'USD');
```

---

## 10) استدعاءات cURL

```bash
# Compile
curl -X POST http://127.0.0.1:8004/api/xsql/compile/ \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT SUMIF(amount, status, 1) AS t FROM conn_a.tbl",
    "namespaces": {"conn_a": "2"}
  }'

# Execute
curl -X POST http://127.0.0.1:8004/api/xsql/execute/ \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT t.id, t.amount FROM conn_a.tbl AS t WHERE t.delivered = 1 LIMIT 100",
    "namespaces": {"conn_a": "2"}
  }'
```

---

## 11) الملفات المرتبطة

- `rml_python/xsql.py` — Lexer, Parser, Compiler
- `urs/views.py` — `api_xsql_compile`, `api_xsql_execute`
- `config/urls.py` — URL patterns
- `odex/system/transaction_reports/example.xsql` — ملف مثال
- `rml_python/engine.py` — يستخدم `_xsql_resolve_conn` للـdistributed fetch
