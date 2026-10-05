"""FormulasMigrate — XSQL/formula functions → PostgreSQL functions.

Source registries: fmlk_engine.calc._FUNCTIONS (formula evaluator) +
rml_python.xsql._FUNCTIONS (XSQL linear functions).

What migrates: SCALAR formulas (text/math/date/logical/pattern) become
``CREATE OR REPLACE FUNCTION <schema>.fn_<name>(...)`` LANGUAGE sql
IMMUTABLE STRICT wrappers around native Postgres equivalents.

What does NOT migrate (listed by generate_ddl as skipped):
  - aggregates (SUM/AVG/MIN/MAX/COUNT/...) — set context, use native SQL
  - lookups (XLOOKUP/VLOOKUP/FILTER/GET/SUMIF/COUNTIF/.../ROWNUM) — need
    query context; run them through the XSQL runner instead

Also home of translate_formula()/expr_to_sql() used by rulevars so
report expressions accept SQL functions, =formulas and XSQL: subqueries.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

FN_PREFIX = "fn_"

# formula -> {pg: template with {a},{b},{c},{d}, args: [sql types],
#             returns: sql type, note: str}. Arg$ placeholders are $1..$n
# (templates use {a}=$1, {b}=$2 ... for readability).
SCALAR: Dict[str, Dict[str, Any]] = {
    # logical
    "IF": {"pg": "CASE WHEN {a} THEN {b} ELSE {c} END", "args": ["BOOLEAN", "TEXT", "TEXT"],
           "returns": "TEXT", "note": ""},
    "AND": {"pg": "{a} AND {b}", "args": ["BOOLEAN", "BOOLEAN"], "returns": "BOOLEAN", "note": ""},
    "OR": {"pg": "{a} OR {b}", "args": ["BOOLEAN", "BOOLEAN"], "returns": "BOOLEAN", "note": ""},
    "NOT": {"pg": "NOT {a}", "args": ["BOOLEAN"], "returns": "BOOLEAN", "note": ""},
    "TRUE": {"pg": "TRUE", "args": [], "returns": "BOOLEAN", "note": ""},
    "FALSE": {"pg": "FALSE", "args": [], "returns": "BOOLEAN", "note": ""},
    "IFERROR": {"pg": "COALESCE({a}, {b})", "args": ["TEXT", "TEXT"], "returns": "TEXT",
                "note": "null-only (PG has no expression exceptions)"},
    "IFNA": {"pg": "COALESCE({a}, {b})", "args": ["TEXT", "TEXT"], "returns": "TEXT", "note": ""},
    "IFS": {"pg": "CASE WHEN {a} THEN {b} ELSE {c} END", "args": ["BOOLEAN", "TEXT", "TEXT"],
            "returns": "TEXT", "note": "first pair only; chain for more"},
    "SWITCH": {"pg": "CASE {a} WHEN {b} THEN {c} ELSE NULL END", "args": ["TEXT", "TEXT", "TEXT"],
               "returns": "TEXT", "note": "single pair; chain for more"},
    "COALESCE": {"pg": "COALESCE({a}, {b})", "args": ["TEXT", "TEXT"], "returns": "TEXT", "note": ""},
    "NULLIF": {"pg": "NULLIF({a}, {b})", "args": ["TEXT", "TEXT"], "returns": "TEXT", "note": ""},
    # text
    "UPPER": {"pg": "UPPER({a})", "args": ["TEXT"], "returns": "TEXT", "note": ""},
    "LOWER": {"pg": "LOWER({a})", "args": ["TEXT"], "returns": "TEXT", "note": ""},
    "TRIM": {"pg": "TRIM({a})", "args": ["TEXT"], "returns": "TEXT", "note": ""},
    "LTRIM": {"pg": "LTRIM({a})", "args": ["TEXT"], "returns": "TEXT", "note": ""},
    "RTRIM": {"pg": "RTRIM({a})", "args": ["TEXT"], "returns": "TEXT", "note": ""},
    "LENGTH": {"pg": "LENGTH({a})", "args": ["TEXT"], "returns": "INTEGER", "note": ""},
    "LEN": {"pg": "LENGTH({a})", "args": ["TEXT"], "returns": "INTEGER", "note": "alias of LENGTH"},
    "SUBSTRING": {"pg": "SUBSTRING({a} FROM {b} FOR {c})", "args": ["TEXT", "INTEGER", "INTEGER"],
                  "returns": "TEXT", "note": ""},
    "SUBSTR": {"pg": "SUBSTRING({a} FROM {b} FOR {c})", "args": ["TEXT", "INTEGER", "INTEGER"],
               "returns": "TEXT", "note": "alias of SUBSTRING"},
    "MID": {"pg": "SUBSTRING({a} FROM {b} FOR {c})", "args": ["TEXT", "INTEGER", "INTEGER"],
            "returns": "TEXT", "note": "1-based like Excel"},
    "CONCAT": {"pg": "CONCAT({a}, {b})", "args": ["TEXT", "TEXT"], "returns": "TEXT", "note": ""},
    "CONCATENATE": {"pg": "CONCAT({a}, {b})", "args": ["TEXT", "TEXT"], "returns": "TEXT",
                    "note": "alias of CONCAT"},
    "REPLACE": {"pg": "REPLACE({a}, {b}, {c})", "args": ["TEXT", "TEXT", "TEXT"],
                "returns": "TEXT", "note": ""},
    "LEFT": {"pg": "LEFT({a}, {b})", "args": ["TEXT", "INTEGER"], "returns": "TEXT", "note": ""},
    "RIGHT": {"pg": "RIGHT({a}, {b})", "args": ["TEXT", "INTEGER"], "returns": "TEXT", "note": ""},
    "REPT": {"pg": "REPEAT({a}, {b})", "args": ["TEXT", "INTEGER"], "returns": "TEXT", "note": ""},
    "EXACT": {"pg": "({a} = {b})", "args": ["TEXT", "TEXT"], "returns": "BOOLEAN",
              "note": "case-sensitive ="},

    "FIND": {"pg": "STRPOS({b}, {a})", "args": ["TEXT", "TEXT"], "returns": "INTEGER",
             "note": "case-sensitive; args (needle, haystack)"},
    "SEARCH": {"pg": "STRPOS(LOWER({b}), LOWER({a}))", "args": ["TEXT", "TEXT"],
               "returns": "INTEGER", "note": "case-insensitive; 0 when absent like PG"},
    "PROPER": {"pg": "INITCAP({a})", "args": ["TEXT"], "returns": "TEXT", "note": ""},
    "TEXTJOIN": {"pg": "ARRAY_TO_STRING({c}, {a})", "args": ["TEXT", "TEXT", "TEXT[]"],
                 "returns": "TEXT", "note": "args (delim, ignore_empty, array)"},
    "VALUE": {"pg": "CAST({a} AS NUMERIC)", "args": ["TEXT"], "returns": "NUMERIC",
              "note": "errors on non-numeric (no TRY_CAST in expression)"},
    "NUMBERVALUE": {"pg": "CAST({a} AS NUMERIC)", "args": ["TEXT"], "returns": "NUMERIC", "note": ""},
    "CHAR": {"pg": "CHR({a})", "args": ["INTEGER"], "returns": "TEXT", "note": ""},
    "UNICHAR": {"pg": "CHR({a})", "args": ["INTEGER"], "returns": "TEXT", "note": ""},
    "CODE": {"pg": "ASCII({a})", "args": ["TEXT"], "returns": "INTEGER", "note": ""},
    "UNICODE": {"pg": "ASCII({a})", "args": ["TEXT"], "returns": "INTEGER", "note": ""},
    "CLEAN": {"pg": "REGEXP_REPLACE({a}, '[\\x00-\\x1F]', '', 'g')", "args": ["TEXT"],
              "returns": "TEXT", "note": ""},
    "TEXT": {"pg": "TO_CHAR({b}, {a})", "args": ["TEXT", "TEXT"], "returns": "TEXT",
             "note": "args (format, value) — PG format models"},
    # math
    "ABS": {"pg": "ABS({a})", "args": ["NUMERIC"], "returns": "NUMERIC", "note": ""},
    "ROUND": {"pg": "ROUND({a}, {b})", "args": ["NUMERIC", "INTEGER"], "returns": "NUMERIC", "note": ""},
    "CEIL": {"pg": "CEIL({a})", "args": ["NUMERIC"], "returns": "NUMERIC", "note": ""},
    "CEILING": {"pg": "CEIL({a})", "args": ["NUMERIC"], "returns": "NUMERIC", "note": "alias of CEIL"},
    "FLOOR": {"pg": "FLOOR({a})", "args": ["NUMERIC"], "returns": "NUMERIC", "note": ""},
    "POWER": {"pg": "POWER({a}, {b})", "args": ["NUMERIC", "NUMERIC"], "returns": "NUMERIC", "note": ""},
    "POW": {"pg": "POWER({a}, {b})", "args": ["NUMERIC", "NUMERIC"], "returns": "NUMERIC",
            "note": "alias of POWER"},
    "SQRT": {"pg": "SQRT({a})", "args": ["NUMERIC"], "returns": "NUMERIC", "note": ""},
    "MOD": {"pg": "MOD({a}, {b})", "args": ["NUMERIC", "NUMERIC"], "returns": "NUMERIC", "note": ""},
    "MIN": {"pg": "LEAST({a}, {b})", "args": ["NUMERIC", "NUMERIC"], "returns": "NUMERIC",
            "note": "scalar form (aggregates stay native SQL)"},
    "MAX": {"pg": "GREATEST({a}, {b})", "args": ["NUMERIC", "NUMERIC"], "returns": "NUMERIC",
            "note": "scalar form (aggregates stay native SQL)"},
    "INT": {"pg": "FLOOR({a})", "args": ["NUMERIC"], "returns": "NUMERIC",
            "note": "rounds toward -inf like Excel"},
    "TRUNC": {"pg": "TRUNC({a})", "args": ["NUMERIC"], "returns": "NUMERIC", "note": ""},
    "EXP": {"pg": "EXP({a})", "args": ["NUMERIC"], "returns": "NUMERIC", "note": ""},
    "LN": {"pg": "LN({a})", "args": ["NUMERIC"], "returns": "NUMERIC", "note": ""},
    "LOG": {"pg": "LOG({b}, {a})", "args": ["NUMERIC", "NUMERIC"], "returns": "NUMERIC",
            "note": "args (number, base)"},
    "PI": {"pg": "PI()", "args": [], "returns": "NUMERIC", "note": ""},
    "SUBSTITUTE": {"pg": "REPLACE({a}, {b}, {c})", "args": ["TEXT", "TEXT", "TEXT"],
                   "returns": "TEXT", "note": "first-occurrence only in Excel; PG replaces all"},
    # date/time
    "NOW": {"pg": "NOW()", "args": [], "returns": "TIMESTAMP", "note": ""},
    "TODAY": {"pg": "CURRENT_DATE", "args": [], "returns": "DATE", "note": ""},
    "CURRENT_DATE": {"pg": "CURRENT_DATE", "args": [], "returns": "DATE", "note": ""},
    "CURRENT_TIMESTAMP": {"pg": "CURRENT_TIMESTAMP", "args": [], "returns": "TIMESTAMP", "note": ""},
    "DATE": {"pg": "MAKE_DATE({a}, {b}, {c})", "args": ["INTEGER", "INTEGER", "INTEGER"],
             "returns": "DATE", "note": "args (year, month, day)"},
    "DATEVALUE": {"pg": "CAST({a} AS DATE)", "args": ["TEXT"], "returns": "DATE", "note": ""},
    "DAY": {"pg": "EXTRACT(DAY FROM {a})::INTEGER", "args": ["DATE"], "returns": "INTEGER", "note": ""},
    "MONTH": {"pg": "EXTRACT(MONTH FROM {a})::INTEGER", "args": ["DATE"], "returns": "INTEGER",
              "note": ""},
    "YEAR": {"pg": "EXTRACT(YEAR FROM {a})::INTEGER", "args": ["DATE"], "returns": "INTEGER",
             "note": ""},
    "DAYS": {"pg": "({b}::DATE - {a}::DATE)", "args": ["DATE", "DATE"], "returns": "INTEGER",
             "note": "args (start, end)"},
    "DATEDIF": {"pg": "({c}::DATE - {a}::DATE)", "args": ["DATE", "TEXT", "DATE"],
                "returns": "INTEGER", "note": "'D' unit only"},
    "EDATE": {"pg": "({a}::DATE + MAKE_INTERVAL(months => {b}))::DATE",
              "args": ["DATE", "INTEGER"], "returns": "DATE", "note": ""},
    "EOMONTH": {"pg": "(DATE_TRUNC('month', {a}::DATE) + INTERVAL '1 month' - INTERVAL '1 day')::DATE",
                "args": ["DATE", "INTEGER"], "returns": "DATE",
                "note": "months arg moves the month first"},
    "HOUR": {"pg": "EXTRACT(HOUR FROM {a})::INTEGER", "args": ["TIMESTAMP"],
             "returns": "INTEGER", "note": ""},
    "MINUTE": {"pg": "EXTRACT(MINUTE FROM {a})::INTEGER", "args": ["TIMESTAMP"],
               "returns": "INTEGER", "note": ""},
    "SECOND": {"pg": "EXTRACT(SECOND FROM {a})::INTEGER", "args": ["TIMESTAMP"],
               "returns": "INTEGER", "note": ""},
    "TIME": {"pg": "MAKE_TIME({a}, {b}, {c})", "args": ["INTEGER", "INTEGER", "NUMERIC"],
             "returns": "TIME", "note": ""},
    "TIMEVALUE": {"pg": "CAST({a} AS TIME)", "args": ["TEXT"], "returns": "TIME", "note": ""},
    "WEEKDAY": {"pg": "(EXTRACT(DOW FROM {a})::INTEGER + 1)", "args": ["DATE"],
                "returns": "INTEGER", "note": "Sunday=1 like Excel"},
    "WEEKNUM": {"pg": "EXTRACT(WEEK FROM {a})::INTEGER", "args": ["DATE"],
                "returns": "INTEGER", "note": "ISO week"},
    "ISOWEEKNUM": {"pg": "EXTRACT(WEEK FROM {a})::INTEGER", "args": ["DATE"],
                   "returns": "INTEGER", "note": ""},
    # pattern
    "REGEXMATCH": {"pg": "({a} ~ {b})", "args": ["TEXT", "TEXT"], "returns": "BOOLEAN", "note": ""},
    "REGEXEXTRACT": {"pg": "SUBSTRING({a} FROM {b})", "args": ["TEXT", "TEXT"],
                     "returns": "TEXT", "note": "first group or full match"},
    "WILDCARDMATCH": {
        "pg": "({a} ~ ('^(?:' || REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE({b}, '([.+^${}()|[\\\\\\]])', '\\\\\\1', 'g'), '\\*', '.*', 'g'), '\\?', '.', 'g') || ')$'))",
        "args": ["TEXT", "TEXT"], "returns": "BOOLEAN",
        "note": "full-string *, ? semantics"},
}

# Aggregates: valid native SQL, meaningless as scalar functions.
SKIP_AGGREGATES = {"SUM", "AVERAGE", "AVG", "COUNT", "COUNTA", "PRODUCT"}
# Row/query context needed — run via the XSQL runner, not as DB functions.
SKIP_CONTEXT = {"SUMIF", "SUMIFS", "COUNTIF", "COUNTBLANK", "COUNTBY", "SUMBY",
                "XLOOKUP", "VLOOKUP", "FILTER", "GET", "SERIAL", "ROWNUM", "ROW",
                "DAYS360", "YEARFRAC", "NETWORKDAYS", "WORKDAY",
                "NETWORKDAYS.INTL", "WORKDAY.INTL", "DOLLAR", "FIXED"}

_SKIP_REASONS = {**{k: "aggregate — use native SQL" for k in SKIP_AGGREGATES},
                 **{k: "needs query context — use the XSQL runner" for k in SKIP_CONTEXT}}

# Function names that can never run inside a DB expression (need Python eval).
PYTHON_ONLY = {"XLOOKUP", "VLOOKUP", "FILTER", "GET", "ROWNUM", "ROW", "SERIAL",
               "SUMIF", "SUMIFS", "COUNTIF", "COUNTBLANK", "COUNTBY", "SUMBY"}

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REF = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\]")
_FUNCALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(")
_DANGEROUS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|EXECUTE|VACUUM|CLUSTER)\b",
    re.IGNORECASE)


def pg_name(formula: str) -> str:
    """DB function name for a formula: fn_<lower>. Raises on bad input."""
    n = str(formula or "").strip().upper()
    if not _IDENT.match(n.replace(".", "_")):
        raise ValueError("bad formula name: %r" % (formula,))
    return FN_PREFIX + n.lower().replace(".", "_")


def _q_ident(name: str) -> str:
    return '"%s"' % str(name).replace('"', '""')


def function_ddl(schema: str, formula: str) -> Dict[str, Any]:
    """One CREATE OR REPLACE FUNCTION statement dict. Raises KeyError if skipped."""
    spec = SCALAR[str(formula or "").strip().upper()]
    sch = str(schema or "").strip() or "public"
    if not _IDENT.match(sch):
        raise ValueError("bad schema: %r" % (schema,))
    name = pg_name(formula)
    args = spec["args"]
    decl = ", ".join("a%d %s" % (i + 1, t) for i, t in enumerate(args))
    vals = {"a": "$1", "b": "$2", "c": "$3", "d": "$4"}
    body = "SELECT " + spec["pg"].format(**vals)
    sql = ("CREATE OR REPLACE FUNCTION %s.%s(%s) RETURNS %s "
           "LANGUAGE sql IMMUTABLE STRICT AS $$ %s $$;" % (
               _q_ident(sch), _q_ident(name), decl, spec["returns"], body))
    return {"name": str(formula).strip().upper(), "pg_name": name, "schema": sch,
            "args": args, "returns": spec["returns"], "native": False,
            "note": spec.get("note") or "", "sql": sql}


def _native_names() -> set:
    """Lowercase native-PG names (no alias needed — they resolve directly)."""
    try:
        return {str(n or "").strip().lower() for n, _t, _h in NATIVE_PG if str(n or "").strip()}
    except Exception:
        return set()


def alias_ddl(schema: str, formula: str) -> Optional[Dict[str, Any]]:
    """Bare-name alias calling the fn_ twin: `name(args)` → `fn_name(args)`.

    Lets callers skip the fn_ prefix. Returns None when the name is a
    native Postgres function (already resolves) — and the executor skips
    the alias when a same-name function already exists in the schema.
    Pure, no DB.
    """
    n = str(formula or "").strip().upper()
    spec = SCALAR[n]
    if n.lower() in _native_names():
        return None
    sch = str(schema or "").strip() or "public"
    if not _IDENT.match(sch):
        raise ValueError("bad schema: %r" % (schema,))
    bare = n.lower().replace(".", "_")
    args = spec["args"]
    decl = ", ".join("a%d %s" % (i + 1, t) for i, t in enumerate(args))
    call = ", ".join("$%d" % (i + 1) for i in range(len(args)))
    sql = ("CREATE OR REPLACE FUNCTION %s.%s(%s) RETURNS %s "
           "LANGUAGE sql IMMUTABLE STRICT AS $$ SELECT %s.%s(%s) $$;" % (
               _q_ident(sch), _q_ident(bare), decl, spec["returns"],
               _q_ident(sch), _q_ident(FN_PREFIX + bare), call))
    return {"name": bare, "pg_name": bare, "schema": sch, "args": args,
            "returns": spec["returns"], "alias_of": FN_PREFIX + bare,
            "note": "bare alias — call without fn_ prefix", "sql": sql}


def generate_ddl(schema: str, only: Optional[List[str]] = None,
                 alias: bool = True) -> Dict[str, Any]:
    """{functions: [...], aliases: [...], skipped: [{name, reason}]} — pure, no DB."""
    want = None
    if only:
        want = {str(x or "").strip().upper() for x in only if str(x or "").strip()}
    fns, aliases, skipped = [], [], []
    names = sorted(want) if want else sorted(set(SCALAR) | set(_SKIP_REASONS))
    for n in names:
        if n in SCALAR:
            try:
                fns.append(function_ddl(schema, n))
                if alias:
                    try:
                        _al = alias_ddl(schema, n)
                        if _al is not None:
                            aliases.append(_al)
                    except Exception as e:
                        skipped.append({"name": n.lower(), "reason": "alias: %s" % str(e)[:120]})
            except Exception as e:
                skipped.append({"name": n, "reason": str(e)[:150]})
        else:
            skipped.append({"name": n,
                            "reason": _SKIP_REASONS.get(n, "unknown formula")})
    return {"functions": fns, "aliases": aliases, "skipped": skipped,
            "total": len(fns), "aliases_total": len(aliases),
            "skipped_total": len(skipped)}


def translate_formula(expr: str) -> str:
    """`=FORMULA([refs])` → Postgres SQL: [a.b]→"b", known names kept.

    Unknown function names pass through (native PG / custom / migrated
    fn_*). Python-only names raise with a pointer to the XSQL runner.
    """
    s = str(expr or "").strip()
    if s.startswith("=") and not s.startswith("=="):
        s = s[1:].strip()
    if not s:
        raise ValueError("empty formula")
    for m in _FUNCALL.finditer(s):
        if str(m.group(1)).upper() in PYTHON_ONLY:
            raise ValueError("%s needs query context — run it via the XSQL runner, "
                             "not inside an expression" % m.group(1).upper())

    def _ref(m):
        return _q_ident(str(m.group(1)).split(".")[-1])

    s = _REF.sub(_ref, s)
    _assert_safe_sql(s)
    return s


def _assert_safe_sql(s: str) -> None:
    """Reject DDL/DML/statements — expressions are SELECT-fragments only."""
    if not s or not s.strip():
        raise ValueError("empty expression")
    if ";" in s:
        raise ValueError("one expression only (no ; statements)")
    if _DANGEROUS.search(s or ""):
        raise ValueError("DDL/DML keywords are not allowed inside expressions")
    depth = 0
    instr = False
    q = ""
    for ch in s:
        if instr:
            if ch == q:
                instr = False
            continue
        if ch in ("'", '"'):
            instr, q = True, ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced parentheses")
    if depth != 0:
        raise ValueError("unbalanced parentheses")


# Native Postgres functions for autocomplete (beyond the formula map):
# (name, template, hint). Templates use | for the first cursor stop.
NATIVE_PG = [
    ("CONCAT", "CONCAT(|)", "دمج نصوص"), ("CONCAT_WS", "CONCAT_WS(|, , )", "دمج بفاصل"),
    ("UPPER", "UPPER(|)", "أحرف كبيرة"), ("LOWER", "LOWER(|)", "أحرف صغيرة"),
    ("TRIM", "TRIM(|)", "إزالة الفراغات"), ("LTRIM", "LTRIM(|)", "فراغات اليسار"),
    ("RTRIM", "RTRIM(|)", "فراغات اليمين"), ("LENGTH", "LENGTH(|)", "طول النص"),
    ("CHAR_LENGTH", "CHAR_LENGTH(|)", "طول النص"), ("SUBSTRING", "SUBSTRING(|, 1, 5)", "جزء من نص"),
    ("LEFT", "LEFT(|, 3)", "أول أحرف"), ("RIGHT", "RIGHT(|, 3)", "آخر أحرف"),
    ("REPLACE", "REPLACE(|, , )", "استبدال"), ("SPLIT_PART", "SPLIT_PART(|, , )", "جزء مفصول"),
    ("STRPOS", "STRPOS(|, )", "موضع نص"), ("REPEAT", "REPEAT(|, )", "تكرار"),
    ("REVERSE", "REVERSE(|)", "عكس النص"), ("LPAD", "LPAD(|, , )", "حشو يسار"),
    ("RPAD", "RPAD(|, , )", "حشو يمين"), ("INITCAP", "INITCAP(|)", "أول كل كلمة كبير"),
    ("REGEXP_REPLACE", "REGEXP_REPLACE(|, , )", "استبدال بنمط"),
    ("OVERLAY", "OVERLAY(|, , , )", "تركيب نص"),
    ("ABS", "ABS(|)", "قيمة مطلقة"), ("ROUND", "ROUND(|, )", "تقريب"),
    ("CEIL", "CEIL(|)", "سقف"), ("FLOOR", "FLOOR(|)", "أرضية"),
    ("POWER", "POWER(|, )", "أس"), ("SQRT", "SQRT(|)", "جذر"),
    ("MOD", "MOD(|, )", "باقي القسمة"), ("TRUNC", "TRUNC(|, )", "قطع عشري"),
    ("EXP", "EXP(|)", "أس طبيعي"), ("LN", "LN(|)", "لوغاريتم طبيعي"),
    ("LOG", "LOG(|, )", "لوغاريتم"), ("GREATEST", "GREATEST(|, )", "الأكبر"),
    ("LEAST", "LEAST(|, )", "الأصغر"),
    ("NOW", "NOW()", "الآن"), ("CURRENT_DATE", "CURRENT_DATE", "تاريخ اليوم"),
    ("CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP", "الطابع الآن"),
    ("EXTRACT", "EXTRACT(| FROM )", "جزء من تاريخ"), ("DATE_TRUNC", "DATE_TRUNC(|, )", "قطع تاريخ"),
    ("MAKE_DATE", "MAKE_DATE(|, , )", "تاريخ من أجزاء"), ("MAKE_TIME", "MAKE_TIME(|, , )", "وقت من أجزاء"),
    ("AGE", "AGE(|, )", "فرق تاريخي"), ("TO_CHAR", "TO_CHAR(|, )", "تنسيق نصي"),
    ("TO_DATE", "TO_DATE(|, )", "نص إلى تاريخ"), ("TO_TIMESTAMP", "TO_TIMESTAMP(|, )", "نص إلى طابع"),
    ("COALESCE", "COALESCE(|, )", "أول غير فارغ"), ("NULLIF", "NULLIF(|, )", "فراغ عند التساوي"),
    ("SUM", "SUM(|)", "مجموع"), ("AVG", "AVG(|)", "متوسط"),
    ("MIN", "MIN(|)", "أصغر"), ("MAX", "MAX(|)", "أكبر"), ("COUNT", "COUNT(|)", "عدد"),
    ("STRING_AGG", "STRING_AGG(|, )", "تجميع نصي"), ("ARRAY_AGG", "ARRAY_AGG(|)", "تجميع مصفوفة"),
    ("ROW_NUMBER", "ROW_NUMBER() OVER (|)", "ترقيم صفوف"),
    ("RANK", "RANK() OVER (|)", "رتبة"), ("DENSE_RANK", "DENSE_RANK() OVER (|)", "رتبة كثيفة"),
    ("LAG", "LAG(|)", "قيمة سابقة"), ("LEAD", "LEAD(|)", "قيمة لاحقة"),
]


def native_entries() -> List[Dict[str, str]]:
    """[{fn, tpl, hint, kind:'native'}] — native Postgres catalog."""
    return [{"fn": n, "tpl": t, "hint": h, "kind": "native"} for n, t, h in NATIVE_PG]


_XSQL_HINTS = {
    "SUMIF": "مجموع مشروط", "SUMIFS": "مجموع بشروط", "COUNTIF": "عدد مشروط",
    "COUNTBLANK": "عدد الفارغ", "COUNTA": "عدد غير الفارغ", "IF": "شرط (يُترجم CASE WHEN)",
    "FILTER": "قيمة أول مطابق", "XLOOKUP": "بحث وإرجاع", "VLOOKUP": "بحث مرتب وإرجاع",
    "GET": "قيمة حقل — خارج الافتراضي", "SUMBY": "مجموع النافذة", "COUNTBY": "عدد النافذة",
    "SERIAL": "تسلسلي", "REGEXMATCH": "مطابقة نمط", "WILDCARDMATCH": "مطابقة * ؟",
    "REGEXEXTRACT": "استخراج نمط",
}


def _tpl(name: str, n_args: int) -> str:
    if n_args <= 0:
        return "%s()" % name
    inner = ", ".join(["|"] + [""] * (n_args - 1)).rstrip(", ")
    return "%s(%s)" % (name, inner)


def autocomplete_defaults() -> List[Dict[str, str]]:
    """Static entries: scalar formulas + XSQL-only functions.

    [{fn, tpl, hint, kind}] — kind ∈ formula|xsql. Migrated/custom DB
    functions are appended per-connection by the autocomplete endpoint.
    """
    out = []
    for name in sorted(SCALAR):
        spec = SCALAR[name]
        out.append({"fn": name, "tpl": _tpl(name, len(spec["args"])),
                    "hint": "صيغة" + (" — " + spec["note"] if spec.get("note") else ""),
                    "kind": "formula"})
    for name in sorted(set(_SKIP_REASONS) - set(SCALAR)):
        out.append({"fn": name, "tpl": _tpl(name, 1),
                    "hint": "XSQL — " + _XSQL_HINTS.get(name, _SKIP_REASONS.get(name, "")),
                    "kind": "xsql"})
    return out


_CREATE_FN_RE = re.compile(r"^\s*CREATE\s+OR\s+REPLACE\s+FUNCTION\s+", re.IGNORECASE)
_FN_NAME_RE = re.compile(
    r"^\s*CREATE\s+OR\s+REPLACE\s+FUNCTION\s+(?:\"([^\"]+)\"\.\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*))",
    re.IGNORECASE)
_BLOCK_OUTSIDE_BODY = re.compile(
    r"\b(DROP|DELETE\s+FROM|TRUNCATE|ALTER\s+(?!.*FUNCTION)|COPY\s|GRANT\s|REVOKE\s|VACUUM|CLUSTER|EXECUTE\s|CREATE\s+(?!OR\s+REPLACE\s+FUNCTION))\b",
    re.IGNORECASE)


def _strip_fn_bodies(s: str) -> str:
    """Replace $$...$$ bodies and '...' literals with placeholders."""
    s = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$.*?\$\1\$", " ", s, flags=re.DOTALL)
    return re.sub(r"'(?:[^']|'')*'", "''", s)


def validate_ddl(ddl: str) -> Dict[str, str]:
    """Validate a custom CREATE OR REPLACE FUNCTION statement.

    Returns {schema, name}. Raises ValueError otherwise. Exactly one
    statement; dangerous keywords outside the body are rejected. (Bodies
    are the author's responsibility — execution is master-gated.)
    """
    s = str(ddl or "").strip().rstrip(";").strip()
    if not s:
        raise ValueError("empty DDL")
    if not _CREATE_FN_RE.match(s):
        raise ValueError("only CREATE OR REPLACE FUNCTION is allowed")
    m = _FN_NAME_RE.match(s)
    if not m:
        raise ValueError("cannot parse function name (use schema.name)")
    sch = m.group(1) or m.group(3) or ""
    name = m.group(2) or m.group(4) or m.group(5) or ""
    if not sch or not name:
        raise ValueError("schema-qualified name required (schema.fn_name)")
    outer = _strip_fn_bodies(s)
    if ";" in outer:
        raise ValueError("one statement only (no ; outside the body)")
    if _BLOCK_OUTSIDE_BODY.search(outer):
        raise ValueError("DDL/DML keywords are not allowed outside the function body")
    return {"schema": sch, "name": name}


_CALL_RE = re.compile(
    r"^\s*(?:([A-Za-z_][A-Za-z0-9_]*)\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\(.*\)\s*$",
    re.DOTALL)


def validate_call(expr: str) -> str:
    """`[=][schema.]name(args)` → normalized call. Raises ValueError."""
    s = str(expr or "").strip()
    if s.startswith("=") and not s.startswith("=="):
        s = s[1:].strip()
    m = _CALL_RE.match(s or "")
    if not m:
        raise ValueError("custom function must look like name(args) or schema.name(args)")
    _assert_safe_sql(s)
    return s


def expr_to_sql(expr: str) -> str:
    """Report expression → SQL fragment. Three kinds:
    - `XSQL: SELECT ...` → scalar-subquery wrapper (validated SELECT only;
      python-only functions rejected with a runner pointer);
    - `=formula` → translate_formula();
    - anything else → raw SQL passthrough (validated, leading = stripped).
    """
    s = str(expr or "").strip()
    if not s:
        raise ValueError("empty expression")
    if s[:5].upper() == "XSQL:":
        inner = s[5:].strip()
        if not re.match(r"(?is)^\s*SELECT\b", inner):
            raise ValueError("XSQL: expression must be a SELECT query")
        for m in _FUNCALL.finditer(inner):
            if str(m.group(1)).upper() in PYTHON_ONLY:
                raise ValueError("%s needs the XSQL runner (Python eval) — "
                                 "a DB expression cannot run it" % m.group(1).upper())
        if ";" in inner:
            raise ValueError("one query only (no ; statements)")
        _assert_safe_sql(inner)
        return "( %s )" % inner
    if s.startswith("=") and not s.startswith("=="):
        return translate_formula(s)
    _assert_safe_sql(s)
    return s
