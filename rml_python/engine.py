"""
RML Report Engine & Query Builder — Production Ready
Compiles and executes dynamic reporting queries from frontend payloads.
Supports: filtering (Odoo-style), sorting, pagination (Oracle OFFSET/FETCH), grouping, SQL preview.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import datetime as _dt_mod
import decimal as _dec_mod
import re
from types import SimpleNamespace
from pathlib import Path as _Path
from .compiler import RMLReportCompiler, RMLColumn
from .oracle_engine import OracleEngine

# Re-export _q for secure quoting (import from oracle_engine to avoid duplication)
try:
    from .oracle_engine import _q  # type: ignore
except ImportError:
    def _q(ident: str) -> str:  # fallback
        import re as _re_qf
        s = str(ident)
        if _re_qf.match(r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$', s):
            return ".".join(f'"{p.replace(chr(34), chr(34)*2)}"' for p in s.split("."))
        return f'"{s.replace(chr(34), chr(34)*2)}"'

# ── Query Builder Helpers ───────────────────────────────────────────────────

# Process-wide TTL cache for raw API rows: {(kind, gid, TABLE): (epoch, [rows])}.
# A full device pull is far too slow to repeat per page view; TEMP staging per
# request stays session-safe (rebuilt from cached rows each time).

def _fmt_cell(v: Any) -> Any:
    """تبسيط عرض القيم: التاريخ بدون وقت منتصف الليل، وفصل التاريخ عن الوقت بمسافة.

    datetime/decimal على مستوى الوحدة (كانت تُستورد مع كل خلية — آلاف
    الاستيرادات لكل صفحة تكلف زمناً ملموساً عند الصفحات الكبيرة).
    """
    try:
        if isinstance(v, _dt_mod.datetime):
            if v.hour == 0 and v.minute == 0 and v.second == 0 and v.microsecond == 0:
                return v.strftime("%Y-%m-%d")
            return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, _dt_mod.date):
            return v.strftime("%Y-%m-%d")
        if isinstance(v, _dt_mod.time):
            return v.strftime("%H:%M:%S")
        if isinstance(v, _dec_mod.Decimal):
            return int(v) if v == int(v) else float(v)
    except Exception:
        pass
    return v

def _find_column_for_field(field: str, columns: Optional[List] = None) -> Optional[Any]:
    """Match a frontend field (alias, name, or expr) to its RMLColumn. Case-insensitive."""
    if not field or not columns:
        return None
    key = str(field).strip().lower()
    for c in columns:
        for cand in (getattr(c, "alias", None), getattr(c, "name", None), getattr(c, "expr", None)):
            if cand and str(cand).strip().lower() == key:
                return c
    return None


def _db_expr_for_field(field: str, columns: Optional[List] = None) -> str:
    """Resolve a frontend field name to a real DB identifier for WHERE/ORDER BY.
    Falls back to the raw field when no column matches (backward compat).
    For fk_lookup columns, returns the base FK expr (not the display subquery)."""
    col = _find_column_for_field(field, columns)
    if col is not None:
        base = (getattr(col, "expr", None) or getattr(col, "name", None) or field)
        base = str(base).strip()
        # If expr is qualified (table.col), keep as-is for _q to handle dots
        return base
    return str(field)


def _strip_over(text: str) -> str:
    """Remove OVER (...) window clauses (balanced) from SQL text."""
    out = str(text or "")
    while True:
        m = re.search(r"\bOVER\s*\(", out, re.IGNORECASE)
        if not m:
            break
        i = m.end() - 1
        depth = 0
        in_str = False
        j = i
        while j < len(out):
            ch = out[j]
            if ch == "'":
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if j >= len(out):
            break
        out = out[:m.start()] + out[j + 1:]
    return out


def _is_date_str(v) -> bool:
    """True for 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM[:SS]' strings."""
    return isinstance(v, str) and bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip())
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", v.strip()))


def _is_date_only(v) -> bool:
    """True for a bare 'YYYY-MM-DD' (no time part)."""
    return isinstance(v, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()))


def _norm_dt_val(v):
    """Normalize HTML datetime-local values for SQL binds.

    'YYYY-MM-DDTHH:MM' -> 'YYYY-MM-DD HH:MM:00' so TO_DATE formats match
    on both Oracle and Postgres. Separator-less dates also accepted:
    'YYYYMMDD' -> 'YYYY-MM-DD', 'DDMMYYYY' -> 'YYYY-MM-DD' (validated).
    """
    if not isinstance(v, str):
        return v
    s = v.strip().replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", s):
        return s + ":00"
    s8 = _parse_compact_date(s)
    if s8:
        return s8
    return s


def _parse_compact_date(s: str):
    """'YYYYMMDD' or 'DDMMYYYY' -> 'YYYY-MM-DD' (calendar-validated) or None."""
    try:
        s = str(s or "").strip()
        import datetime as _dt
        m = re.fullmatch(r"((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", s)
        if m:
            _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        m = re.fullmatch(r"(0[1-9]|[12]\d|3[01])(0[1-9]|1[0-2])((?:19|20)\d{2})", s)
        if m:
            _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    except Exception:
        pass
    return None


def _parse_year_only(v) -> Optional[str]:
    """'YYYY' (or any date form) -> 'YYYY' or None."""
    try:
        s = str(v or "").strip()
        m = re.fullmatch(r"((?:19|20)\d{2})", s)
        if m:
            return m.group(1)
        d = _norm_dt_val(s)
        m = re.fullmatch(r"((?:19|20)\d{2})-\d{2}-\d{2}.*", d)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _parse_year_month(v):
    """'YYYY-MM' (or any date form) -> (YYYY, MM) or None."""
    try:
        s = str(v or "").strip()
        m = re.fullmatch(r"((?:19|20)\d{2})-(0[1-9]|1[0-2])", s)
        if m:
            return m.group(1), m.group(2)
        d = _norm_dt_val(s)
        m = re.fullmatch(r"((?:19|20)\d{2})-(0[1-9]|1[0-2])-\d{2}.*", d)
        if m:
            return m.group(1), m.group(2)
    except Exception:
        pass
    return None


def _date_kind_of(dtype) -> str:
    """Classify an RML/DB type string: 'datetime' | 'date' | 'time' | ''."""
    t = str(dtype or "").upper()
    if "TIMESTAMP" in t or "DATETIME" in t:
        return "datetime"
    if "DATE" in t:
        return "date"
    if "TIME" in t:
        return "time"
    return ""


def _build_outer_where(filters: List[Dict[str, Any]], columns: Optional[List] = None,
                       start: int = 1, _join: str = "AND",
                       rules: Optional[List[Any]] = None) -> Tuple[str, Dict[str, Any]]:
    """WHERE over SELECT aliases (for computed/aggregate columns).

    Binds are :qN. Date-like values on windowed (native) columns are wrapped
    with TO_DATE. Formatted (TO_CHAR/||) outputs only support equals/contains
    as plain string comparison; other ops raise a clear Arabic error.
    Supports {any:[...]} OR groups (recursively).
    """
    clauses: List[str] = []
    params: Dict[str, Any] = {}

    def _wrap(ph: str, val, windowed: bool):
        if windowed and _is_date_str(val):
            v = val.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                return f"TO_DATE({ph}, 'YYYY-MM-DD')"
            return f"TO_DATE({ph}, 'YYYY-MM-DD HH24:MI:SS')"
        return ph

    for i, f in enumerate(filters, start=start):
        if isinstance(f, dict) and isinstance(f.get("any"), (list, tuple)):
            subs = [sf for sf in f["any"] if isinstance(sf, dict)]
            if len(subs) > 1:
                sub_where, sub_params = _build_outer_where(
                    subs, columns=columns, start=2000000 + i * 1000, _join="OR", rules=rules)
                if sub_where.startswith(" WHERE "):
                    clauses.append("(" + sub_where[len(" WHERE "):] + ")")
                    params.update(sub_params)
                continue
            elif len(subs) == 1:
                f = subs[0]
            else:
                continue
        if isinstance(f, dict) and isinstance(f.get("all"), (list, tuple)):
            subs = [sf for sf in f["all"] if isinstance(sf, dict)]
            if len(subs) > 1:
                sub_where, sub_params = _build_outer_where(
                    subs, columns=columns, start=2500000 + i * 1000, _join="AND", rules=rules)
                if sub_where.startswith(" WHERE "):
                    clauses.append("(" + sub_where[len(" WHERE "):] + ")")
                    params.update(sub_params)
                continue
            elif len(subs) == 1:
                f = subs[0]
            else:
                continue
        field = f.get("field") or f.get("column") or f.get("name")
        op = (f.get("op") or "equals").lower()
        if not field:
            continue
        col = _find_column_for_field(field, columns)
        alias = getattr(col, "alias", None) if col is not None else None
        qfield = _q(alias) if alias else _q(str(field))
        raw = ""
        if col is not None:
            raw = re.sub(r"(?i)^\s*DISTINCT\s+", "", (getattr(col, "expr", None) or getattr(col, "name", None) or "").strip())
        windowed = bool(re.search(r"\bOVER\s*\(", raw, re.IGNORECASE))
        formatted = bool(re.search(r"\bTO_CHAR\s*\(", raw, re.IGNORECASE) or "||" in raw)
        p = f"q{i}"
        val = f.get("value")
        if formatted and op not in ("equals", "=", "==", "eq", "contains", "like", "ilike",
                                   "startswith", "starts_with", "start", "begins", "begins_with",
                                   "endswith", "ends_with", "end", "ends",
                                   "not_contains", "notcontains", "notlike", "not_like",
                                   "not_equals", "notequals", "not_equal", "!=", "<>", "ne", "neq"):
            raise ValueError(f'لا يمكن مقارنة العمود المنسق "{alias or field}" بهذه العملية — قارن بالمساواة/الاحتواء على النص المعروض.')
        if op in ("equals", "=", "==", "eq"):
            clauses.append(f"{qfield}={_wrap(':'+p, val, windowed)}")
            params[p] = val
        elif op in ("contains", "like", "ilike"):
            clauses.append(f"{qfield} LIKE :{p}")
            params[p] = f"%{val or ''}%"
        elif op in ("startswith", "starts_with", "start", "begins", "begins_with"):
            clauses.append(f"{qfield} LIKE :{p}")
            params[p] = f"{val or ''}%"
        elif op in ("endswith", "ends_with", "end", "ends"):
            clauses.append(f"{qfield} LIKE :{p}")
            params[p] = f"%{val or ''}"
        elif op in ("not_contains", "notcontains", "notlike", "not_like"):
            clauses.append(f"{qfield} NOT LIKE :{p}")
            params[p] = f"%{val or ''}%"
        elif op in ("not_equals", "notequals", "not_equal", "!=", "<>", "ne", "neq"):
            clauses.append(f"{qfield} <> {_wrap(':'+p, val, windowed)}")
            params[p] = val
        elif op in ("before", "qabl", "قبل"):
            _v = _norm_dt_val(val)
            clauses.append(f"{qfield} < {_wrap(':'+p, _v, True)}")
            params[p] = _v
        elif op in ("after", "بعد"):
            _v = _norm_dt_val(val)
            clauses.append(f"{qfield} > {_wrap(':'+p, _v, True)}")
            params[p] = _v
        elif op in ("since", "from_date", "منذ"):
            _v = _norm_dt_val(val)
            clauses.append(f"{qfield} >= {_wrap(':'+p, _v, True)}")
            params[p] = _v
        elif op in ("inyear", "year", "in_year", "في_سنة"):
            _yv = _parse_year_only(val)
            if not _yv:
                raise ValueError(f'سنة غير صالحة "{val}" — أدخل YYYY (مثال 2026)')
            clauses.append(f"EXTRACT(YEAR FROM {qfield}) = :{p}")
            params[p] = int(_yv)
        elif op in ("inmonth", "month", "in_month", "في_شهر"):
            _ym = _parse_year_month(val)
            if not _ym:
                raise ValueError(f'شهر غير صالح "{val}" — أدخل YYYY-MM (مثال 2026-09)')
            p2 = f"q{i}_2"
            clauses.append(f"EXTRACT(YEAR FROM {qfield}) = :{p} AND EXTRACT(MONTH FROM {qfield}) = :{p2}")
            params[p] = int(_ym[0])
            params[p2] = int(_ym[1])
        elif op in ("inday", "day", "in_day", "في_يوم"):
            _v = _norm_dt_val(val)
            clauses.append(f"CAST({qfield} AS DATE) = TO_DATE(:{p}, 'YYYY-MM-DD')")
            params[p] = _v
        elif op in ("gt", ">"):
            clauses.append(f"{qfield} > {_wrap(':'+p, val, windowed)}")
            params[p] = val
        elif op in ("lt", "<"):
            clauses.append(f"{qfield} < {_wrap(':'+p, val, windowed)}")
            params[p] = val
        elif op in ("gte", ">=", "ge"):
            clauses.append(f"{qfield} >= {_wrap(':'+p, val, windowed)}")
            params[p] = val
        elif op in ("lte", "<=", "le"):
            clauses.append(f"{qfield} <= {_wrap(':'+p, val, windowed)}")
            params[p] = val
        elif op == "between":
            p2 = f"q{i}_2"
            v_from = f.get("valFrom")
            v_to = f.get("valTo")
            clauses.append(f"{qfield} BETWEEN {_wrap(':'+p, v_from, windowed)} AND {_wrap(':'+p2, v_to, windowed)}")
            params[p] = v_from
            params[p2] = v_to
        elif op in ("in", "in_list"):
            vals = val or []
            if not isinstance(vals, (list, tuple)):
                vals = [vals]
            phs = []
            for j, v in enumerate(vals):
                pj = f"{p}_{j}"
                phs.append(f":{pj}")
                params[pj] = v
            clauses.append(f"{qfield} IN ({', '.join(phs)})" if phs else "1=0")
        else:
            clauses.append(f"{qfield}={_wrap(':'+p, val, windowed)}")
            params[p] = val
    where = " WHERE " + f" {_join} ".join(clauses) if clauses else ""
    return where, params


def _resolve_filter_field(field: str, columns: Optional[List] = None,
                           fields: Optional[List] = None,
                           table_map: Optional[Dict[str, str]] = None,
                           conn_map: Optional[Dict[str, str]] = None,
                           rules: Optional[List[Any]] = None,
                           default_tables: Optional[Any] = None) -> str:
    """SQL fragment for WHERE/GROUP BY from a frontend field.

    Handles computed/aggregated column exprs: strips DISTINCT, and for
    windowed aggregates filters on the PARTITION BY base key
    (e.g. filter 'التاريخ' -> "T0"."BILL_DATE"). Plain aggregates without
    a partition key cannot be filtered (would need HAVING) -> clear error.
    """
    from .namespaces import _resolve_expression as _rx
    col = _find_column_for_field(field, columns)
    if col is None:
        # Direct base-field reference (e.g. from Report Setup modal):
        # qualify with its table alias in multi-table queries.
        try:
            fmap = {str(getattr(f, "name", "")).strip().lower(): f for f in (fields or []) if getattr(f, "name", None)}
            fl = fmap.get(str(field).strip().lower())
        except Exception:
            fl = None
        if fl is not None and table_map:
            tal = table_map.get(str(field).strip().lower())
            if tal:
                return f'{_q(tal)}.{_q(str(getattr(fl, "name", field)).strip())}'
            return _q(str(getattr(fl, "name", field)).strip())
        if fl is not None:
            return _q(str(getattr(fl, "name", field)).strip())
        return _q(str(field))
    raw = (getattr(col, "expr", None) or getattr(col, "name", None) or field).strip()
    raw = re.sub(r"(?i)^\s*DISTINCT\s+", "", raw)
    if rules and "$" in raw:
        from .rulevars import expand_rule_vars as _expand_rv0
        raw = _expand_rv0(raw, rules)
    if re.search(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", raw, re.IGNORECASE):
        pm = re.search(r"PARTITION\s+BY\s+(.+)$", raw, re.IGNORECASE | re.DOTALL)
        refs: set = set()
        if pm:
            part = pm.group(1).strip()
            if part.endswith(")"):
                part = part[:-1]
            fmap = {getattr(f, "name", "").lower(): f for f in (fields or []) if getattr(f, "name", None)}
            for r in re.findall(r"[\[{]([^\].\[{}]+)[\]}]", part):
                if r.strip().lower() in fmap:
                    refs.add(r.strip())
            for _qm in re.finditer(r"[\[{]([A-Za-z0-9_][A-Za-z0-9_.]*)[\]}]", part):
                _qp = [p.strip() for p in _qm.group(1).split(".")]
                if len(_qp) > 1 and _qp[-1].lower() in fmap:
                    refs.add(_qp[-1].strip())
            segs = re.split(r"('(?:[^']|'')*')", part)
            for i in range(0, len(segs), 2):
                for m in re.finditer(r'(?<!\.)"([A-Za-z_][A-Za-z0-9_]*)"(?!\.)', segs[i]):
                    if m.group(1).lower() in fmap:
                        refs.add(m.group(1))
        if len(refs) == 1:
            only = next(iter(refs))
            return _rx(f"[{only}]", fields, {}, set(), table_map, conn_map,
                       default_tables=default_tables)
        alias = getattr(col, "alias", None) or field
        raise ValueError(f'لا يمكن التصفية على العمود التجميعي "{alias}" — صفِّ على عمود التاريخ أو العمود الأساس بدلاً منه.')
    out = _rx(raw, fields, {}, set(), table_map, conn_map, default_tables=default_tables)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", out.strip() or ""):
        return _q(out.strip())
    return out


_AR_LETTER_RE = re.compile(r"[\u0600-\u06FF]")
_AR_STRIP_RE = re.compile(r"[\u064B-\u0652\u0640]")


def _ar_norm(value: Any) -> str:
    """توحيد النص العربي للمقارنة: أ/إ/آ→ا، ة→ه، ى→ي، حذف التشكيل والتطويل."""
    s = str(value if value is not None else "")
    s = _AR_STRIP_RE.sub("", s)
    return (s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
             .replace("ة", "ه").replace("ى", "ي"))


def _needs_ar_norm(value: Any) -> bool:
    """قيمة نصية تحوي عربية وتستحق المقارنة الموحدة؟"""
    return isinstance(value, str) and bool(_AR_LETTER_RE.search(value))


_NON_TEXT_HINTS = ("INT", "SERIAL", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL", "MONEY", "NUMBER", "DATE", "TIME", "BOOL")

def _field_is_texty(field_name: str, fmap: Dict[str, Any]) -> bool:
    """TRANSLATE() مخصص للأعمدة النصية فقط — الرقمية/التاريخ يُقارن مباشرة.

    unknown (no metadata) → True للحفاظ على السلوك القديم.
    """
    try:
        fl = (fmap or {}).get(str(field_name or "").strip().lower())
        if fl is None:
            return True
        t = str(getattr(fl, "data_type", "") or "").upper()
        return not any(k in t for k in _NON_TEXT_HINTS)
    except Exception:
        return True


def _type_str_of(col, field_name: str = "", fmap=None) -> str:
    """Data-type string of a column (RML col object/dict) or its field fallback, uppercased."""
    try:
        if col is not None:
            if isinstance(col, dict):
                for _k in ("data_type", "dataType", "data-type", "type"):
                    if col.get(_k):
                        return str(col.get(_k)).strip().upper()
            else:
                for _k in ("data_type", "dataType", "type"):
                    _v = getattr(col, _k, None)
                    if _v:
                        return str(_v).strip().upper()
        if field_name:
            fl = (fmap or {}).get(str(field_name).strip().lower())
            if fl is not None:
                return str(getattr(fl, "data_type", "") or "").upper()
    except Exception:
        pass
    return ""


_NUMERIC_HINTS = ("INT", "SERIAL", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL", "MONEY", "NUMBER")

def _col_is_numeric(col, field_name: str = "", fmap=None) -> bool:
    """True when the column/field is declared numeric (any subtype)."""
    try:
        return any(k in _type_str_of(col, field_name, fmap) for k in _NUMERIC_HINTS)
    except Exception:
        return False


def _int_bounds_for_type(t: str):
    """Exact (lo, hi) for integral subtypes; None for float/decimal/unknown.

    Prevents conversion-overflow errors (e.g. MSSQL tinyint vs 11-digit value):
    out-of-range values can never match → caller emits 1=0 instead of crashing.
    """
    try:
        t = str(t or "").upper()
        if "BIGINT" in t or "BIGSERIAL" in t or "INT8" in t:
            return (-9223372036854775808, 9223372036854775807)
        if "SMALLINT" in t or "SMALLSERIAL" in t or "INT2" in t:
            return (-32768, 32767)
        if "TINYINT" in t or "INT1" in t:
            return (0, 255)
        if "INT" in t or "SERIAL" in t or "INT4" in t or "INTEGER" in t:
            return (-2147483648, 2147483647)
    except Exception:
        pass
    return None


_NUM_LIT_RE = re.compile(r"^-?\d+(\.\d+)?$")

def _num_safe_for_compare(v, type_str: str = "") -> bool:
    """Literal safe to compare against a numeric column (no overflow/format error).

    Unknown subtype → BIGINT-range best effort (fixes huge values; tiny-subtype
    medium values keep legacy behavior). Float/decimal families: format-only.
    """
    try:
        if isinstance(v, bool):
            return True
        bounds = _int_bounds_for_type(type_str)
        if isinstance(v, int):
            return bounds is None or (bounds[0] <= v <= bounds[1])
        if isinstance(v, float):
            return bounds is None or (bounds[0] <= v <= bounds[1])
        if not isinstance(v, str):
            return False
        s = v.strip().replace("،", ",").replace(",", "")
        if not _NUM_LIT_RE.match(s):
            return False
        if bounds is None:
            return True
        if "." in s:
            return False  # decimals never match an integral subtype
        return bounds[0] <= int(s) <= bounds[1]
    except Exception:
        return False


def _date_safe_for_compare(v) -> bool:
    """Full date/datetime literal only (YYYY[-MM[-DD]] would still crash a DATE compare)."""
    try:
        if not isinstance(v, str):
            return False
        s = v.strip()
        if _is_date_only(s):
            return True
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", s):
            return True
        return False
    except Exception:
        return False


_BOOL_LITS = {"true", "false", "1", "0", "t", "f", "yes", "no", "y", "n", "on", "off"}

def _bool_col_kind(col, field_name: str = "", fmap=None) -> bool:
    try:
        return "BOOL" in _type_str_of(col, field_name, fmap)
    except Exception:
        return False


def _bool_safe_for_compare(v) -> bool:
    try:
        if isinstance(v, bool):
            return True
        if isinstance(v, (int, float)):
            return True
        return isinstance(v, str) and v.strip().lower() in _BOOL_LITS
    except Exception:
        return False


def _ar_col_sql(col_expr: str) -> str:
    """لفّ عمود نصي بمقارنة عربية موحدة (TRANSLATE يعمل على Oracle وPostgres)."""
    return f"TRANSLATE({col_expr}, 'أإآةى', 'اااهي')"


def _build_where(filters: List[Dict[str, Any]], start_idx: int = 1, columns: Optional[List] = None,
                 fields: Optional[List] = None, table_map: Optional[Dict[str, str]] = None,
                 conn_map: Optional[Dict[str, str]] = None,
                 _join: str = "AND", rules: Optional[List[Any]] = None,
                 default_tables: Optional[Any] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Build dynamic WHERE clause from Odoo-style search tags.
    Each filter: {field: str, op: str, value: Any, valFrom/valTo for between}
    Supports: equals, contains (LIKE %val%), gt (>), lt (<), between
    OR group: {any: [filter, ...]} -> (clause OR clause ...).
    Returns (where_sql, params_dict) with :pN binds.
    `columns` is used to resolve Arabic aliases to real DB identifiers; fk_lookup
    display search uses EXISTS on the reference table.
    """
    if not filters:
        return "", {}
    clauses: List[Dict[str, Any]] = []
    params: Dict[str, Any] = {}
    # DATE-typed columns need TO_DATE binds (Oracle has no implicit cast)
    _fmap = {str(getattr(f, "name", "")).lower(): f for f in (fields or []) if getattr(f, "name", None)}

    def _col_date_kind(col, field_name: str = "") -> str:
        try:
            if col is not None:
                # 0) explicit column data-type from the designer (overrides inference)
                _xt = str(getattr(col, "data_type", None) or "").strip().lower()
                if _xt in ("date", "datetime", "time"):
                    return _xt
                txt = (getattr(col, "expr", None) or getattr(col, "name", None) or "")
                parts = re.split(r"('(?:[^']|'')*')", str(txt))
                for i in range(0, len(parts), 2):
                    seg = parts[i]
                    cands = set(re.findall(r"[\[{]([^\].\[{}]+)[\]}]", seg))
                    # qualified refs [table.col] / [conn.table.col] -> date check by column part
                    for _qm in re.finditer(r"[\[{]([A-Za-z0-9_][A-Za-z0-9_.]*)[\]}]", seg):
                        _qp = [p.strip() for p in _qm.group(1).split(".")]
                        if len(_qp) > 1 and _qp[-1]:
                            cands.add(_qp[-1])
                    cands |= {m.group(1) for m in re.finditer(r'(?<!\.)"([A-Za-z_][A-Za-z0-9_]*)"(?!\.)', seg)}
                    for cnd in cands:
                        fl = _fmap.get(cnd.strip().lower())
                        if fl:
                            _k = _date_kind_of(getattr(fl, "data_type", ""))
                            if _k:
                                return _k
            if field_name:
                fl = _fmap.get(str(field_name).strip().lower())
                if fl:
                    return _date_kind_of(getattr(fl, "data_type", ""))
        except Exception:
            pass
        return ""

    def _col_is_date(col, field_name: str = "") -> bool:
        return bool(_col_date_kind(col, field_name))

    def _dbind(ph: str, val, is_date: bool) -> str:
        if is_date and isinstance(val, str):
            v = _norm_dt_val(val)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                return f"TO_DATE({ph}, 'YYYY-MM-DD')"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", v):
                return f"TO_DATE({ph}, 'YYYY-MM-DD HH24:MI:SS')"
        return ph
    for i, f in enumerate(filters, start=start_idx):
        # OR group -> (a OR b ...) with collision-free binds via high offset
        if isinstance(f, dict) and isinstance(f.get("any"), (list, tuple)):
            subs = [sf for sf in f["any"] if isinstance(sf, dict)]
            if len(subs) > 1:
                sub_where, sub_params = _build_where(
                    subs, start_idx=1000000 + i * 1000,
                    columns=columns, fields=fields, table_map=table_map,
                    conn_map=conn_map, _join="OR", rules=rules,
                    default_tables=default_tables)
                if sub_where.startswith(" WHERE "):
                    clauses.append("(" + sub_where[len(" WHERE "):] + ")")
                    params.update(sub_params)
                continue
            elif len(subs) == 1:
                f = subs[0]
            else:
                continue
        # AND group -> (a AND b ...) — nested conjunction (e.g. from "&" search)
        if isinstance(f, dict) and isinstance(f.get("all"), (list, tuple)):
            subs = [sf for sf in f["all"] if isinstance(sf, dict)]
            if len(subs) > 1:
                sub_where, sub_params = _build_where(
                    subs, start_idx=1500000 + i * 1000,
                    columns=columns, fields=fields, table_map=table_map,
                    conn_map=conn_map, _join="AND", rules=rules,
                    default_tables=default_tables)
                if sub_where.startswith(" WHERE "):
                    clauses.append("(" + sub_where[len(" WHERE "):] + ")")
                    params.update(sub_params)
                continue
            elif len(subs) == 1:
                f = subs[0]
            else:
                continue
        field = f.get("field") or f.get("column") or f.get("name")
        op = (f.get("op") or "equals").lower()
        if not field:
            continue
        # TRANSLATE() للأعمدة النصية فقط — وإلا Postgres يرفض translate(integer,...)
        _texty = _field_is_texty(field, _fmap)
        p = f"p{i}"
        col = _find_column_for_field(field, columns)
        # fk_lookup display search: match on referenced display value via EXISTS
        if (col is not None and getattr(col, "col_type", "") == "fk_lookup"
                and (getattr(col, "ref_table", None) or getattr(col, "ref_tables", []) and len(getattr(col, "ref_tables", [])) > 0)
                and (getattr(col, "ref_fk", None) or (getattr(col, "ref_tables", [])[0].get("fk") if getattr(col, "ref_tables", []) else None))
                and (getattr(col, "ref_display", None) or (getattr(col, "ref_tables", [])[0].get("display") if getattr(col, "ref_tables", []) else None))
                and op in ("equals", "=", "==", "eq", "contains", "like", "ilike",
                           "startswith", "starts_with", "start", "begins", "begins_with",
                           "endswith", "ends_with", "end", "ends",
                           "not_contains", "notcontains", "notlike", "not_like",
                           "not_equals", "notequals", "not_equal", "!=", "<>", "ne", "neq")):
            # Use first ref_table/ref_fk/ref_display for backward compat, or iterate all
            ref_table = getattr(col, "ref_table", None) or (getattr(col, "ref_tables", [])[0].get("table") if getattr(col, "ref_tables", []) else None)
            ref_fk = getattr(col, "ref_fk", None) or (getattr(col, "ref_tables", [])[0].get("fk") if getattr(col, "ref_tables", []) else None)
            ref_display = getattr(col, "ref_display", None) or (getattr(col, "ref_tables", [])[0].get("display") if getattr(col, "ref_tables", []) else None)
            if not ref_table or not ref_fk or not ref_display:
                # fallback to single attrs
                ref_table = getattr(col, "ref_table", None)
                ref_fk = getattr(col, "ref_fk", None)
                ref_display = getattr(col, "ref_display", None)
            # Build WHERE using the first table (or could iterate all for OR logic)
            base_expr = _db_expr_for_field(field, columns)
            try:
                qbase = _q(base_expr)
            except Exception:
                qbase = base_expr
            if op in ("contains", "like", "ilike"):
                if _needs_ar_norm(f.get("value")):
                    clauses.append(f"EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_ar_col_sql(_q(ref_display))} LIKE :{p})")
                    params[p] = f"%{_ar_norm(f.get('value',''))}%"
                else:
                    clauses.append(f"EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_q(ref_display)} LIKE :{p})")
                    params[p] = f"%{f.get('value','')}%"
            elif op in ("startswith", "starts_with", "start", "begins", "begins_with"):
                if _needs_ar_norm(f.get("value")):
                    clauses.append(f"EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_ar_col_sql(_q(ref_display))} LIKE :{p})")
                    params[p] = f"{_ar_norm(f.get('value',''))}%"
                else:
                    clauses.append(f"EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_q(ref_display)} LIKE :{p})")
                    params[p] = f"{f.get('value','')}%"
            elif op in ("endswith", "ends_with", "end", "ends"):
                if _needs_ar_norm(f.get("value")):
                    clauses.append(f"EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_ar_col_sql(_q(ref_display))} LIKE :{p})")
                    params[p] = f"%{_ar_norm(f.get('value',''))}"
                else:
                    clauses.append(f"EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_q(ref_display)} LIKE :{p})")
                    params[p] = f"%{f.get('value','')}"
            elif op in ("not_contains", "notcontains", "notlike", "not_like"):
                if _needs_ar_norm(f.get("value")):
                    clauses.append(f"NOT EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_ar_col_sql(_q(ref_display))} LIKE :{p})")
                    params[p] = f"%{_ar_norm(f.get('value',''))}%"
                else:
                    clauses.append(f"NOT EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_q(ref_display)} LIKE :{p})")
                    params[p] = f"%{f.get('value','')}%"
            elif op in ("not_equals", "notequals", "not_equal", "!=", "<>", "ne", "neq"):
                if _needs_ar_norm(f.get("value")):
                    clauses.append(f"NOT EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_ar_col_sql(_q(ref_display))} = :{p})")
                    params[p] = _ar_norm(f.get("value"))
                else:
                    clauses.append(f"NOT EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_q(ref_display)} = :{p})")
                    params[p] = f.get("value")
            else:
                if _needs_ar_norm(f.get("value")):
                    clauses.append(f"EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_ar_col_sql(_q(ref_display))} = :{p})")
                    params[p] = _ar_norm(f.get("value"))
                else:
                    clauses.append(f"EXISTS (SELECT 1 FROM {_q(ref_table)} WHERE {_q(ref_fk)} = {qbase} AND {_q(ref_display)} = :{p})")
                    params[p] = f.get("value")
            continue
        db_field = _db_expr_for_field(field, columns)
        qfield = _resolve_filter_field(field, columns, fields, table_map, conn_map, rules,
                                         default_tables=default_tables)
        is_date = _col_is_date(col, field)
        date_kind = _col_date_kind(col, field) if is_date else ""
        # Impossible-match guards: a value that can never match the column type
        # emits 1=0 (or 1=1 for negations) instead of a conversion-crashing compare
        # (e.g. 11-digit search on a TINYINT commission column in an OR group).
        _numcol = _col_is_numeric(col, field, _fmap)
        _tstr = _type_str_of(col, field, _fmap) if _numcol else ""
        _boolcol = _bool_col_kind(col, field, _fmap)
        if op in ("equals", "=", "==", "eq"):
            if _numcol and not _num_safe_for_compare(f.get("value"), _tstr):
                clauses.append("1=0")
            elif is_date and not _date_safe_for_compare(_norm_dt_val(f.get("value"))):
                clauses.append("1=0")
            elif _boolcol and not _bool_safe_for_compare(f.get("value")):
                clauses.append("1=0")
            else:
                _v = f.get("value") if _numcol or _boolcol else _norm_dt_val(f.get("value"))
                if date_kind == "datetime" and _is_date_only(_v):
                    # date-picker day on a TIMESTAMP column: whole-day range
                    # (works on Oracle and Postgres: TO_DATE + 1 day)
                    clauses.append(
                        f"{qfield} >= TO_DATE(:{p}, 'YYYY-MM-DD') "
                        f"AND {qfield} < TO_DATE(:{p}, 'YYYY-MM-DD') + 1")
                    params[p] = _v
                elif _needs_ar_norm(_v) and not is_date and _texty:
                    # مقارنة عربية موحدة (أيمن = أيمن رغم الهمزة)
                    clauses.append(f"{_ar_col_sql(qfield)}=:{p}")
                    params[p] = _ar_norm(_v)
                else:
                    clauses.append(f"{qfield}={_dbind(':'+p, _v, is_date)}")
                    params[p] = _v
        elif op in ("contains", "like", "ilike", "contains"):
            if _needs_ar_norm(f.get("value")) and _texty:
                clauses.append(f"{_ar_col_sql(qfield)} LIKE :{p}")
                params[p] = f"%{_ar_norm(f.get('value',''))}%"
            else:
                clauses.append(f"{qfield} LIKE :{p}")
                # Use %value% for contains
                params[p] = f"%{f.get('value','')}%"
        elif op in ("startswith", "starts_with", "start", "begins", "begins_with"):
            if _needs_ar_norm(f.get("value")) and _texty:
                clauses.append(f"{_ar_col_sql(qfield)} LIKE :{p}")
                params[p] = f"{_ar_norm(f.get('value',''))}%"
            else:
                clauses.append(f"{qfield} LIKE :{p}")
                params[p] = f"{f.get('value','')}%"
        elif op in ("endswith", "ends_with", "end", "ends"):
            if _needs_ar_norm(f.get("value")) and _texty:
                clauses.append(f"{_ar_col_sql(qfield)} LIKE :{p}")
                params[p] = f"%{_ar_norm(f.get('value',''))}"
            else:
                clauses.append(f"{qfield} LIKE :{p}")
                params[p] = f"%{f.get('value','')}"
        elif op in ("not_contains", "notcontains", "notlike", "not_like"):
            if _needs_ar_norm(f.get("value")) and _texty:
                clauses.append(f"{_ar_col_sql(qfield)} NOT LIKE :{p}")
                params[p] = f"%{_ar_norm(f.get('value',''))}%"
            else:
                clauses.append(f"{qfield} NOT LIKE :{p}")
                params[p] = f"%{f.get('value','')}%"
        elif op in ("not_equals", "notequals", "not_equal", "!=", "<>", "ne", "neq"):
            if _numcol and not _num_safe_for_compare(f.get("value"), _tstr):
                clauses.append("1=1")
            elif is_date and not _date_safe_for_compare(_norm_dt_val(f.get("value"))):
                clauses.append("1=1")
            elif _boolcol and not _bool_safe_for_compare(f.get("value")):
                clauses.append("1=1")
            else:
                _v = f.get("value") if _numcol or _boolcol else _norm_dt_val(f.get("value"))
                if _needs_ar_norm(_v) and not is_date and _texty:
                    clauses.append(f"{_ar_col_sql(qfield)}<>:{p}")
                    params[p] = _ar_norm(_v)
                else:
                    clauses.append(f"{qfield}<> {_dbind(':'+p, _v, is_date)}")
                    params[p] = _v
        elif op in ("gt", ">", "greater", "greater_than"):
            if _numcol and not _num_safe_for_compare(f.get("value"), _tstr):
                clauses.append("1=0")
            elif is_date and not _date_safe_for_compare(_norm_dt_val(f.get("value"))):
                clauses.append("1=0")
            else:
                _v = f.get("value") if _numcol else _norm_dt_val(f.get("value"))
                clauses.append(f"{qfield} > {_dbind(':'+p, _v, is_date)}")
                params[p] = _v
        elif op in ("lt", "<", "less", "less_than"):
            if _numcol and not _num_safe_for_compare(f.get("value"), _tstr):
                clauses.append("1=0")
            elif is_date and not _date_safe_for_compare(_norm_dt_val(f.get("value"))):
                clauses.append("1=0")
            else:
                _v = f.get("value") if _numcol else _norm_dt_val(f.get("value"))
                clauses.append(f"{qfield} < {_dbind(':'+p, _v, is_date)}")
                params[p] = _v
        elif op in ("gte", ">=", "ge"):
            if _numcol and not _num_safe_for_compare(f.get("value"), _tstr):
                clauses.append("1=0")
            elif is_date and not _date_safe_for_compare(_norm_dt_val(f.get("value"))):
                clauses.append("1=0")
            else:
                _v = f.get("value") if _numcol else _norm_dt_val(f.get("value"))
                clauses.append(f"{qfield} >= {_dbind(':'+p, _v, is_date)}")
                params[p] = _v
        elif op in ("lte", "<=", "le"):
            if _numcol and not _num_safe_for_compare(f.get("value"), _tstr):
                clauses.append("1=0")
            elif is_date and not _date_safe_for_compare(_norm_dt_val(f.get("value"))):
                clauses.append("1=0")
            else:
                _v = f.get("value") if _numcol else _norm_dt_val(f.get("value"))
                clauses.append(f"{qfield} <= {_dbind(':'+p, _v, is_date)}")
                params[p] = _v
        elif op == "between":
            p2 = f"p{i}_2"
            # Support valFrom/valTo or value as [from,to]
            v_from = f.get("valFrom", f.get("value", [None, None])[0] if isinstance(f.get("value"), (list, tuple)) else None)
            v_to = f.get("valTo", f.get("value", [None, None])[1] if isinstance(f.get("value"), (list, tuple)) else None)
            if _numcol and (not _num_safe_for_compare(v_from, _tstr) or not _num_safe_for_compare(v_to, _tstr)):
                clauses.append("1=0")
            elif is_date and (not _date_safe_for_compare(_norm_dt_val(v_from)) or not _date_safe_for_compare(_norm_dt_val(v_to))):
                clauses.append("1=0")
            else:
                v_from = v_from if _numcol else _norm_dt_val(v_from)
                v_to = v_to if _numcol else _norm_dt_val(v_to)
                if date_kind == "datetime" and _is_date_only(v_from) and _is_date_only(v_to):
                    # date-picker range on a TIMESTAMP column: include the whole end day
                    clauses.append(
                        f"{qfield} >= TO_DATE(:{p}, 'YYYY-MM-DD') "
                        f"AND {qfield} < TO_DATE(:{p2}, 'YYYY-MM-DD') + 1")
                else:
                    clauses.append(f"{qfield} BETWEEN {_dbind(':'+p, v_from, is_date)} AND {_dbind(':'+p2, v_to, is_date)}")
                params[p] = v_from
                params[p2] = v_to
        elif op in ("before", "qabl"):
            _v = _norm_dt_val(f.get("value"))
            if not _date_safe_for_compare(_v):
                clauses.append("1=0")
            else:
                clauses.append(f"{qfield} < {_dbind(':'+p, _v, True)}")
                params[p] = _v
        elif op in ("after",):
            _v = _norm_dt_val(f.get("value"))
            if not _date_safe_for_compare(_v):
                clauses.append("1=0")
            else:
                clauses.append(f"{qfield} > {_dbind(':'+p, _v, True)}")
                params[p] = _v
        elif op in ("since", "from_date"):
            _v = _norm_dt_val(f.get("value"))
            if not _date_safe_for_compare(_v):
                clauses.append("1=0")
            else:
                clauses.append(f"{qfield} >= {_dbind(':'+p, _v, True)}")
                params[p] = _v
        elif op in ("inyear", "year", "in_year"):
            _yv = _parse_year_only(f.get("value"))
            if not _yv:
                raise ValueError(f'سنة غير صالحة "{f.get("value")}" — أدخل YYYY (مثال 2026)')
            clauses.append(f"EXTRACT(YEAR FROM {qfield}) = :{p}")
            params[p] = int(_yv)
        elif op in ("inmonth", "month", "in_month"):
            _ym = _parse_year_month(f.get("value"))
            if not _ym:
                raise ValueError(f'شهر غير صالح "{f.get("value")}" — أدخل YYYY-MM (مثال 2026-09)')
            p2 = f"p{i}_2"
            clauses.append(f"EXTRACT(YEAR FROM {qfield}) = :{p} AND EXTRACT(MONTH FROM {qfield}) = :{p2}")
            params[p] = int(_ym[0])
            params[p2] = int(_ym[1])
        elif op in ("inday", "day", "in_day"):
            _v = _norm_dt_val(f.get("value"))
            clauses.append(f"CAST({qfield} AS DATE) = TO_DATE(:{p}, 'YYYY-MM-DD')")
            params[p] = _v
        elif op in ("in", "in_list"):
            # Handle IN clause with dynamic expansion (not bindable as single)
            vals = f.get("value") or []
            if not isinstance(vals, (list, tuple)):
                vals = [vals]
            if _numcol:
                vals = [v for v in vals if _num_safe_for_compare(v, _tstr)]
            elif is_date:
                vals = [v for v in vals if _date_safe_for_compare(_norm_dt_val(v))]
            elif _boolcol:
                vals = [v for v in vals if _bool_safe_for_compare(v)]
            placeholders = []
            for j, v in enumerate(vals):
                pj = f"{p}_{j}"
                placeholders.append(f":{pj}")
                params[pj] = v
            clauses.append(f"{qfield} IN ({', '.join(placeholders)})" if placeholders else "1=0")
        else:
            # Fallback to equals (مع توحيد عربي عند الحاجة)
            if _numcol and not _num_safe_for_compare(f.get("value"), _tstr):
                clauses.append("1=0")
            elif is_date and not _date_safe_for_compare(_norm_dt_val(f.get("value"))):
                clauses.append("1=0")
            elif _boolcol and not _bool_safe_for_compare(f.get("value")):
                clauses.append("1=0")
            elif _needs_ar_norm(f.get("value")) and _texty:
                clauses.append(f"{_ar_col_sql(qfield)}=:{p}")
                params[p] = _ar_norm(f.get("value"))
            else:
                clauses.append(f"{qfield}=:{p}")
                params[p] = f.get("value")
    where = " WHERE " + f" {_join} ".join(clauses) if clauses else ""
    return where, params

def _select_ordinal(matched, columns) -> int:
    """1-based position of a matched column in the SELECT list (0 = unknown).

    Reports often carry the same column twice (main + detail merge); the
    first occurrence wins so ORDER BY stays deterministic.
    """
    try:
        if matched is not None and columns:
            return list(columns).index(matched) + 1
    except Exception:
        pass
    return 0


def _dedupe_select_items(items) -> list:
    """Drop exact-duplicate SELECT items, keep first occurrence order."""
    seen, out = set(), []
    for it in (items or []):
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _build_order_by(sort, columns=None) -> str:
    """Build ORDER BY from sort payload: {column, direction} or list thereof.

    Orders by SELECT-list ordinal (1-based position). Ordinals are immune
    to duplicate aliases — e.g. MSSQL 209 "ambiguous column" when the same
    alias (تاريخ الحوالة ×2) appears twice — and valid in PG/Oracle/MSSQL,
    including computed/aggregated expressions. Falls back to the quoted
    alias when the column isn't in the SELECT list (backward compat)."""
    if not sort:
        return ""
    if isinstance(sort, dict):
        sort = [sort]
    parts = []
    for s in sort:
        col = s.get("column") or s.get("field") or s.get("name")
        direction = (s.get("direction") or s.get("dir") or "asc").upper()
        if direction not in ("ASC", "DESC"):
            direction = "ASC"
        if col:
            matched = _find_column_for_field(col, columns)
            pos = _select_ordinal(matched, columns)
            if pos > 0:
                parts.append(f"{pos} {direction}")
            else:
                order_key = matched.alias if matched is not None and getattr(matched, "alias", None) else col
                parts.append(f"{_q(order_key)} {direction}")
    return " ORDER BY " + ", ".join(parts) if parts else ""

def _apply_column_where(expr_sql: str, where_clause: Optional[str]) -> str:
    """Wrap a column expression with its per-column where_clause.
    <column ... where_clause="dept='IT'"> becomes
    CASE WHEN <where_clause> THEN (<expr>) ELSE NULL END
    Empty where_clause returns expr unchanged.
    """
    if not where_clause or not str(where_clause).strip():
        return expr_sql
    wc = str(where_clause).strip()
    return f"CASE WHEN {wc} THEN ({expr_sql}) ELSE NULL END"


def _is_pg_db(db) -> bool:
    """True when the live engine is Postgres (else Oracle dialect)."""
    try:
        return "postgres" in type(db).__name__.lower()
    except Exception:
        return False


def _norm_join_type(v) -> str:
    """Normalize join/cardinality: one_to_one (default) | one_to_many."""
    s = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
    if s in ("one_to_many", "one_many", "1_n", "1m", "many", "o2m"):
        return "one_to_many"
    return "one_to_one"


def _alias_by_table(fields: Optional[List[Any]], table_map: Optional[Dict[str, str]]) -> Dict[str, str]:
    """{NORM_TABLE: alias} derived from fields + table_map.

    Lets qualified refs ([table.col]/[conn.table.col]) resolve to their OWN
    table's alias instead of the bare-name lookup (wrong table on duplicates).
    """
    out: Dict[str, str] = {}
    if not table_map:
        return out
    try:
        for f in (fields or []):
            fn = (getattr(f, "name", None) or "").strip().lower()
            al = table_map.get(fn)
            if not al:
                continue
            ts = str(getattr(f, "table_source", None) or "")
            tn = ts.strip().replace('"', "").split(".")[-1].upper() if ts else ""
            if tn:
                out.setdefault(tn, al)
    except Exception:
        pass
    return out


def _build_select(columns: List[RMLColumn], fields: Optional[List] = None,
                   ns_registry: Optional[Dict[str, Any]] = None, _visited: Optional[set] = None,
                   table_map: Optional[Dict[str, str]] = None, dialect: str = "oracle",
                   conn_map: Optional[Dict[str, str]] = None,
                   rules: Optional[List[Any]] = None,
                   outer_table: Optional[str] = None,
                   default_schema: Optional[str] = None,
                   alias_by_table: Optional[Dict[str, str]] = None,
                   default_tables: Optional[Any] = None) -> str:
    """
    Build SELECT clause handling 4 column types + new fields/columns split:
    - <field name=DB col> defines base fields usable in direct display or computation.
      Referenced inside expressions as [name], [table.name], or
      [connection.table.name] (validated against <fields>).
    - <column alias name expr where_clause icon> defines computed/displayed columns.
    - ns.member tokens (e.g. hrRules.rule1) inline the referenced rule/column
      expression from the file declaring metadata namespace="ns".
    - direct:     expr is column/field name -> _q(expr)
    - computed:   expr is expression like "[salary] * 1.1" (field refs in [])
    - fk_lookup one_to_one:  (SELECT refDisplay FROM refTable WHERE refFk = main.expr) AS alias
    - fk_lookup one_to_many: aggregated list (LISTAGG Oracle / STRING_AGG Postgres) AS alias
    - aggregated: expr like "COUNT(*)" or "SUM([salary])" -> as alias
    - where_clause: wraps any of the above in CASE WHEN ... THEN ... ELSE NULL END
    - conn_map: {rml-local connection id: global id} for [conn.table.col] validation.
    - default_tables: normed default-table names (explicit <table_opts>
      is_default) — bare get(col) outside them must be conn.table.col.
    """
    from .namespaces import _resolve_expression, build_registry
    if ns_registry is None:
        need_ns = any(re.search(r"\[|[A-Za-z_]+\.[A-Za-z_]+|\$[A-Za-z_]+\.[A-Za-z_]+\$", (c.expr or "")) for c in (columns or []))
        ns_registry = build_registry() if need_ns else {}
    if _visited is None:
        _visited = set()
    _abt = alias_by_table or _alias_by_table(fields, table_map)
    if rules:
        from .rulevars import expand_rule_vars as _expand_rv
    parts = []
    for col in columns:
        alias_q = _q(col.alias)
        raw = (col.expr or "").strip()
        if not raw and getattr(col, "name", None):
            cname_clean = str(col.name).strip().lower()
            if any(str(getattr(f, "name", "") or "").strip().lower() == cname_clean for f in (fields or [])):
                raw = str(col.name).strip()
        base_sql: str
        if rules and "$" in raw:
            raw = _expand_rv(raw, rules)
        expr = _resolve_expression(raw, fields, ns_registry, _visited, table_map, conn_map, _abt,
                                     default_tables=default_tables) if raw else ""
        if not raw or raw.strip().upper() == "NULL":
            base_sql = "NULL"
        # fk_lookup: generate subquery from ref_tables (new) or single ref (backward compat)
        elif col.col_type == "fk_lookup":
            # Try new ref_tables list first, fall back to single refs
            ref_table = getattr(col, "ref_table", None)
            ref_fk = getattr(col, "ref_fk", None)
            ref_display = getattr(col, "ref_display", None)
            if not ref_table and getattr(col, "ref_tables", []):
                ref_table = getattr(col, "ref_tables", [{}])[0].get("table") if getattr(col, "ref_tables", []) else None
            if not ref_fk and getattr(col, "ref_tables", []):
                ref_fk = getattr(col, "ref_tables", [{}])[0].get("fk") if getattr(col, "ref_tables", []) else None
            if not ref_display and getattr(col, "ref_tables", []):
                ref_display = getattr(col, "ref_tables", [{}])[0].get("display") if getattr(col, "ref_tables", []) else None
            jt = _norm_join_type(getattr(col, "join_type", None) or getattr(col, "ref_type", None))
            # Schema-qualify the reference table from <fields> when known
            # (bare "TABLE" raises ORA-00942 when the login schema isn't the owner).
            # Prefer a schema-qualified source; else fall back to the query schema.
            ref_from = _q(ref_table) if ref_table else '""'
            try:
                _rn = (str(ref_table).strip().replace('"', "").split(".")[-1].upper() if ref_table else "")
                _bare = None
                for _f in (fields or []):
                    _ts = getattr(_f, "table_source", None) or ""
                    if _rn and _ts and str(_ts).strip().replace('"', "").split(".")[-1].upper() == _rn:
                        if "." in str(_ts):
                            ref_from = _q(_ts)
                            break
                        if _bare is None:
                            _bare = _ts
                else:
                    if _bare is not None:
                        ref_from = _q(_bare)
                        _ts = _bare
                    else:
                        _ts = ""
                if _ts is not None and "." not in str(_ts) and default_schema:
                    ref_from = _q(str(default_schema) + "." + str(_rn if _rn else ref_table))
            except Exception:
                pass
            # Correlation key: runtime-derived scope key when present (link
            # columns may differ: scope_key on the outer side vs ref_fk inside)
            key_col = getattr(col, "ref_scope_key", None) or ref_fk
            if re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', raw):
                key_sql = _q(raw)
            elif outer_table and key_col:
                # Qualified display ref (e.g. [conn.table.col]): correlate on the
                # OUTER scope key explicitly — otherwise both sides of "=" bind to
                # the inner table (tautology / ORA-01427).
                key_sql = f"{_q(outer_table)}.{_q(key_col)}"
            else:
                key_sql = expr
            if jt == "one_to_many":
                # Aggregate all matching display values (no row multiplication)
                if str(dialect or "").lower().startswith("pg"):
                    base_sql = (f"(SELECT STRING_AGG({_q(ref_display)}::TEXT, ', ' ORDER BY {_q(ref_display)}::TEXT) "
                                f"FROM {ref_from} WHERE {_q(ref_fk)} = {key_sql})")
                elif str(dialect or "").lower().startswith("mssql"):
                    base_sql = (f"(SELECT STRING_AGG(CAST({_q(ref_display)} AS NVARCHAR(MAX)), ', ') "
                                f"WITHIN GROUP (ORDER BY {_q(ref_display)}) "
                                f"FROM {ref_from} WHERE {_q(ref_fk)} = {key_sql})")
                else:
                    base_sql = (f"(SELECT LISTAGG({_q(ref_display)}, ', ') WITHIN GROUP (ORDER BY {_q(ref_display)}) "
                                f"FROM {ref_from} WHERE {_q(ref_fk)} = {key_sql})")
            else:
                base_sql = f"(SELECT {_q(ref_display)} FROM {ref_from} WHERE {_q(ref_fk)} = {key_sql})"
        elif col.col_type in ("aggregated", "computed"):
            if re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', raw):
                _known_f = {str(getattr(_f, "name", "") or "").strip().lower() for _f in (fields or [])}
                if (_known_f and raw.lower() not in _known_f) or raw.lower().startswith("col_") or raw.lower().startswith("dcol_"):
                    base_sql = "NULL"
                else:
                    base_sql = _q(raw)
            else:
                base_sql = expr
        else:  # direct
            if re.match(r'^[A-Za-z_][A-Za-z0-9_\.]*$', raw):
                if raw.strip().upper() == "NULL":
                    base_sql = raw
                else:
                    # Bare XSQL-style `table.col` resolved against <fields>
                    # above (staged TEMP aliases, quoted idents) wins over
                    # quoting the literal table name — but ONLY when the head
                    # is a known table; raw SQL passthrough is unchanged.
                    _use_rx = False
                    if "." in raw:
                        try:
                            from .namespaces import _norm_tname as _nt
                            _hd = raw.split(".")[0]
                            _known2 = {_nt(getattr(_f, "table_source", None) or "")
                                       for _f in (fields or [])}
                            try:
                                _known2 |= {str(_v or "").strip().lower() for _v in (table_map or {}).values()}
                                _known2 |= {_nt(_k) for _k in (_abt or {})}
                            except Exception:
                                pass
                            if _hd and _nt(_hd) in _known2:
                                _use_rx = True
                        except Exception:
                            pass
                        base_sql = expr if _use_rx else _q(raw)
                    else:
                        _known_f = {str(getattr(_f, "name", "") or "").strip().lower() for _f in (fields or [])}
                        if (_known_f and raw.lower() not in _known_f) or raw.lower().startswith("col_") or raw.lower().startswith("dcol_"):
                            base_sql = "NULL"
                        else:
                            base_sql = _q(raw)
            else:
                base_sql = expr
        # Apply per-column where_clause (resolved the same way). Keeps backward compat when empty.
        wc = getattr(col, "where_clause", None)
        if wc and str(wc).strip():
            from .namespaces import _resolve_expression as _rx
            wc = str(wc)
            if rules and "$" in wc:
                from .rulevars import expand_rule_vars as _expand_rv2
                wc = _expand_rv2(wc, rules)
            wc = _rx(wc, fields, ns_registry, _visited, table_map, conn_map, _abt,
                       default_tables=default_tables)
        base_sql = _apply_column_where(base_sql, wc)
        parts.append(f"{base_sql} AS {alias_q}")
    parts = _dedupe_select_items(parts)
    return ", ".join(parts) if parts else "*"

def _is_mssql_db(db) -> bool:
    """True when the live engine is a direct SQL Server connection."""
    try:
        return "sqlserverdirect" in type(db).__name__.lower()
    except Exception:
        return False


# ── Local-PG staging engine: REMOVED ─────────────────────────────────────
# Staging (rml_api_*/rml_union_* TEMP copies) is disabled by design: every
# queryable SQL source is read live and merged in Python. Only genuinely
# non-queryable sources (ZK devices, is_queryable=False) cannot run in SQL
# and fail loudly instead of being copied.


# Staging diagnostic sink (file + stderr). Hidden windows can swallow stderr,
# so we mirror every diagnostic line into <BASE_DIR>/logs/rml_stage_diag.log.
def _emit_staging_diag(header, body):
    try:
        import sys as _sys
        import traceback as _tb
        import datetime as _dtdiag
        try:
            _tsdiag = _dtdiag.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            _tsdiag = "?"
        try:
            from pathlib import Path as _P
            from config.settings import BASE_DIR as _BD
            _logdir = _P(_BD) / "logs"
            _logdir.mkdir(parents=True, exist_ok=True)
            with open(_logdir / "rml_stage_diag.log", "a", encoding="utf-8") as _fh:
                _fh.write("\n=== " + header + " === [" + _tsdiag + "]\n")
                _fh.write(str(body))
                if not str(body).endswith("\n"):
                    _fh.write("\n")
                _fh.flush()
        except Exception:
            pass
        try:
            _sys.stderr.write("\n=== " + header + " ===\n")
            _sys.stderr.write(str(body))
            if not str(body).endswith("\n"):
                _sys.stderr.write("\n")
            _sys.stderr.flush()
        except Exception:
            pass
    except Exception:
        pass


def _mssql_transpile_sql(sql: str, convert_binds: bool = True) -> str:
    """Transpile builder (Oracle-flavored) SQL to T-SQL.

    convert_binds=True rewrites :name/%s placeholders to pyodbc positional ?.
    convert_binds=False keeps :name (readable preview + binds list).
    String literals are never touched.
    """
    if not sql:
        return sql
    _parts = re.split(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")", str(sql))
    for _i in range(0, len(_parts), 2):
        _seg = _parts[_i]
        # TO_DATE binds → CAST (DATETIME2 keeps time part)
        _seg = re.sub(r"(?i)\bTO_DATE\s*\(\s*(:[A-Za-z_][A-Za-z0-9_]*)\s*,\s*'YYYY-MM-DD HH24:MI:SS'\s*\)",
                      r"CAST(\1 AS DATETIME2)", _seg)
        # whole-day range: TO_DATE + 1 → DATEADD (T-SQL forbids date + int)
        _seg = re.sub(r"(?i)\bTO_DATE\s*\(\s*(:[A-Za-z_][A-Za-z0-9_]*)\s*,\s*'YYYY-MM-DD'\s*\)\s*\+\s*1",
                      r"DATEADD(day, 1, CAST(\1 AS DATE))", _seg)
        _seg = re.sub(r"(?i)\bTO_DATE\s*\(\s*(:[A-Za-z_][A-Za-z0-9_]*)\s*,\s*'YYYY-MM-DD'\s*\)",
                      r"CAST(\1 AS DATE)", _seg)
        # CAST targets unknown to SQL Server
        _seg = re.sub(r"(?i)\bAS\s+TIMESTAMP\b", "AS DATETIME2", _seg)
        # Oracle NVL → ISNULL
        _seg = re.sub(r"(?i)\bNVL\s*\(", "ISNULL(", _seg)
        # date-bucket formatters (specific → generic order matters)
        _seg = re.sub(r"(?i)\bTO_CHAR\s*\(\s*DATE_TRUNC\s*\(\s*'month'\s*,(.+?)\)::date\s*,\s*'YYYY-MM-DD'\s*\)",
                      r"LEFT(CONVERT(varchar(10), \1, 23), 7)", _seg)
        _seg = re.sub(r"(?i)\bTO_CHAR\s*\(\s*DATE_TRUNC\s*\(\s*'year'\s*,(.+?)\)::date\s*,\s*'YYYY'\s*\)",
                      r"CAST(YEAR(\1) AS varchar(4))", _seg)
        _seg = re.sub(r"(?i)\bTO_CHAR\s*\(\s*TRUNC\s*\((.+?)\s*,\s*'MM'\s*\)\s*,\s*'YYYY-MM-DD'\s*\)",
                      r"LEFT(CONVERT(varchar(10), \1, 23), 7)", _seg)
        _seg = re.sub(r"(?i)\bTO_CHAR\s*\(\s*TRUNC\s*\((.+?)\s*,\s*'YYYY'\s*\)\s*,\s*'YYYY'\s*\)",
                      r"CAST(YEAR(\1) AS varchar(4))", _seg)
        _seg = re.sub(r"(?i)\bTO_CHAR\s*\(\s*TRUNC\s*\((.+?)\)\s*,\s*'YYYY-MM-DD'\s*\)",
                      r"CONVERT(varchar(10), \1, 23)", _seg)
        _seg = re.sub(r"(?i)\bTO_CHAR\s*\(\s*(.+?)::date\s*,\s*'YYYY-MM-DD'\s*\)",
                      r"CONVERT(varchar(10), \1, 23)", _seg)
        if convert_binds:
            # :name binds → positional ? (skip :: casts); %s → ? (views style)
            _seg = re.sub(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", "?", _seg)
            _seg = _seg.replace("%s", "?")
        _parts[_i] = _seg
    return "".join(_parts)


def _mssql_bind_values(sql: str, params) -> list:
    """Ordered values matching _mssql_transpile_sql(convert_binds=True) placeholders."""
    if params is None:
        return []
    if isinstance(params, (list, tuple)):
        return list(params)
    if not isinstance(params, dict):
        return []
    _parts = re.split(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")", str(sql))
    out = []
    for _i in range(0, len(_parts), 2):
        for _m in re.finditer(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", _parts[_i]):
            out.append(params.get(_m.group(1)))
        out.extend([None] * _parts[_i].count("%s"))
    return out


class SqlServerDirect:
    """Live SQL Server engine for direct (staging-free) report execution.

    Used only when EVERY involved table lives on ONE queryable sqlserver
    connection — the compiled query runs on the source itself via pyodbc.
    Interface mirrors OracleEngine/PostgresEngine (connect/_exec/disconnect).
    """

    def __init__(self, row):
        self.row = row
        self.conn = None
        try:
            self.gid = str(getattr(row, "id", "") or "")
        except Exception:
            self.gid = ""

    @staticmethod
    def _esc(v) -> str:
        return "{" + str(v or "").replace("}", "}}") + "}"

    def connect(self):
        import pyodbc
        if self.conn is not None:
            try:
                self.conn.rollback()
                return self.conn
            except Exception:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
        from rml_python.engine import RMLReportEngine as _RML
        row = self.row
        user = str(getattr(row, "user", "") or "")
        pwd = str(getattr(row, "password", "") or "")
        last = None
        for _srv, _dbn in _RML._sqlserver_targets(row):
            for _drv, _modern in _RML._sqlserver_drivers():
                try:
                    _parts = [f"DRIVER={{{_drv}}}", f"SERVER={_srv}", f"DATABASE={_dbn}",
                              f"UID={self._esc(user)}", f"PWD={self._esc(pwd)}"]
                    if _modern:
                        _parts += ["TrustServerCertificate=yes", "Connect Timeout=15"]
                    # HYC00 "Optional feature not implemented (SQLBindParameter)"
                    # surfaces across modern and legacy drivers with server-side
                    # prepared statements. Disable them globally; ANSI-style
                    # translation also avoids nvarchar binding quirks.
                    _parts += ["AutoTranslate=no", "UseProcForPrepare=0"]
                    cn = pyodbc.connect(";".join(_parts) + ";", timeout=15)
                    try:
                        cur = cn.cursor()
                        cur.execute("SET QUOTED_IDENTIFIER ON")
                        cur.close()
                    except Exception:
                        pass
                    self.conn = cn
                    return cn
                except Exception as e:
                    last = e
                    try:
                        import traceback as _tb
                        _emit_staging_diag(
                            "RML SqlServerDirect connect diagnostic",
                            f"driver: {_drv!r}  modern: {_modern}\n"
                            f"connect_parts: {';'.join(_parts)[:300]}\n"
                            + _tb.format_exc())
                    except Exception:
                        pass
                    continue
        raise ValueError(f"تعذر الاتصال المباشر بـ SQL Server: {str(last)[:200]}")

    def disconnect(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def _exec(self, sql: str, params=None, commit=False):
        if not getattr(self, "conn", None):
            self.connect()
        assert self.conn is not None
        exec_sql = _mssql_transpile_sql(sql, convert_binds=True)
        values = _mssql_bind_values(sql, params)
        cur = self.conn.cursor()
        try:
            cur.execute(exec_sql, values)
            if commit:
                self.conn.commit()
            return cur
        except Exception:
            try:
                cur.close()
            except Exception:
                pass
            raise


# ── Main Engine ──────────────────────────────────────────────────────────────

class RMLReportEngine:
    """
    Meta-Driven Dynamic RML Report Engine.
    Compiles RML metadata + columns into executable Oracle SQL.
    Usage:
        compiler = RMLReportCompiler("report.rml")
        engine = OracleEngine(dsn="...", user="...", password="...")
        rml_engine = RMLReportEngine(compiler, engine)
        result = rml_engine.execute(payload)  # payload from frontend
        sql = rml_engine.preview_sql(payload)
    """

    def __init__(self, compiler: RMLReportCompiler, db_engine: OracleEngine, databases: Optional[Dict[str, Any]] = None):
        self.compiler = compiler
        # Ensure metadata parsed
        self.metadata = compiler.rpt_metadata()
        self.columns = compiler.columns()
        try:
            self.fields = compiler.fields()
        except Exception:
            self.fields = []
        try:
            self.connections = compiler.connections()
        except Exception:
            self.connections = []
        try:
            self.charts = compiler.charts()
        except Exception:
            self.charts = []
        try:
            self.rules = compiler.rules()
        except Exception:
            self.rules = []
        try:
            self.report_links = compiler.links() if hasattr(compiler, "links") else []
        except Exception:
            self.report_links = []
        self.db = db_engine
        # Extra live engines by connection key (multi-DB reports); primary is self.db
        self.databases: Dict[str, Any] = dict(databases or {})
        self.primary_conn: Optional[str] = None
        # Per-query routing state (set by _prepare_from_and_columns)
        self._last_base_db = None
        self._last_merges: List[Dict[str, Any]] = []
        self._last_extra: List[Tuple[str, str]] = []
        self._dj_conns: Dict[str, Any] = {}
        # Per-execute diagnostics: merge stats {table: {keys, matched}} + timings
        self._merge_stats: Dict[str, Any] = {}
        self._timings: Dict[str, Any] = {}
        # Connection routing: map connection_id -> db_engine (for multi-DB reports)
        self._db_map: Dict[str, Any] = {}
        if self.connections:
            for conn in self.connections:
                # Try to load real Connection from Django if available
                try:
                    import django

                    from django.conf import settings

                    if django.conf.settings.configured:
                        from urs.models import Connection as DjangoConn

                        try:
                            dj = DjangoConn.objects.filter(id=int(conn.connection_id)).first()
                            if dj:
                                # Store for reference; actual execution still uses primary db unless is_local is False (read-only)
                                self._db_map[conn.id] = dj.to_dict()
                            else:
                                self._db_map[conn.id] = {"connection_id": conn.connection_id}
                        except Exception:
                            self._db_map[conn.id] = {"connection_id": conn.connection_id}
                except Exception:
                    self._db_map[conn.id] = {"connection_id": conn.connection_id}
        # Also map columns by connection_id for per-column routing
        self._column_conn_map = {c.id: c.connection_id for c in self.columns if c.connection_id}
        # Map fields by name for expr resolution + by connection
        self._field_map = {f.name: f for f in (self.fields or []) if getattr(f, "name", None)}
        self._fields_by_conn: Dict[str, List] = {}
        for f in (self.fields or []):
            self._fields_by_conn.setdefault(str(getattr(f, "connection_id", None) or ""), []).append(f)
        # Derive base table: designer's table_opts is_default wins, else first
        # <field table_source>, else first column expr prefix.
        self._default_table: Optional[str] = None
        try:
            _comp = getattr(self, "compiler", None)
            if _comp is not None and hasattr(_comp, "table_opts"):
                for _to in (_comp.table_opts() or []):
                    if (_to or {}).get("is_default") and (_to or {}).get("name"):
                        self._default_table = str(_to["name"]).split(".")[-1]
                        break
        except Exception:
            self._default_table = None
        if not self._default_table and self.fields:
            for f in self.fields:
                ts = getattr(f, "table_source", None)
                if ts and "." not in (ts or ""):
                    self._default_table = ts
                    break
                if ts and "." in ts:
                    self._default_table = ts.split(".")[-1]
                    break
        if not self._default_table and self.columns:
            # Try to infer table from first column's raw attrs
            first = self.columns[0]
            # If expr contains dot, table is prefix
            if "." in (first.expr or ""):
                self._default_table = first.expr.split(".")[0]
        # Validate columns
        if not self.columns:
            raise ValueError("RML has no <column> definitions")

        # Validate columns
        if not self.columns:
            raise ValueError("RML has no <column> definitions")
        # Cache for DB metadata lookups (join inference)
        self._cols_cache: Dict[str, Any] = {}

    # ── Multi-DB routing ────────────────────────────────────────────────
    # Fields carry conn_id (-> Django Connection). Tables on the same physical
    # DB as the base table use SQL JOINs; tables on other DBs are merged in
    # Python (professional cross-connection JOIN execution).

    @staticmethod
    def _db_identity(db) -> tuple:
        """Canonical physical-DB identity for same-DB comparison."""
        try:
            name = type(db).__name__.lower()
            if "postgres" in name:
                return ("pg", str(getattr(db, "host", "")), str(getattr(db, "port", "")),
                        str(getattr(db, "dbname", "")))
            if "sqlserverdirect" in name:
                try:
                    _r = getattr(db, "row", None)
                    return ("ms", str(getattr(_r, "host", "")), str(getattr(_r, "port", "")),
                            str(getattr(_r, "instance", "")), str(getattr(_r, "user", "")))
                except Exception:
                    return ("ms", str(getattr(db, "gid", "")))
            return ("ora", str(getattr(db, "dsn", "")))
        except Exception:
            return ("unknown", str(id(db)))

    def _global_conn_id(self, raw) -> Optional[str]:
        """Resolve a field/rml connection reference to a Django Connection id."""
        if raw is None or str(raw).strip() == "":
            return None
        key = str(raw).strip()
        if key in self._dj_conns:
            return key if self._dj_conns[key] is not None else None
        try:
            import django
            from django.conf import settings
            if django.conf.settings.configured:
                from urs.models import Connection as DjangoConn
                dj = DjangoConn.objects.filter(id=int(key)).first()
                if dj is not None:
                    self._dj_conns[key] = dj
                    return key
                # Fallback: rml-local id -> global connection_id
                for conn in (getattr(self, "connections", []) or []):
                    if str(getattr(conn, "id", "")) == key:
                        gid = str(getattr(conn, "connection_id", "") or "")
                        if gid:
                            dj2 = DjangoConn.objects.filter(id=int(gid)).first()
                            self._dj_conns[key] = dj2
                            return gid if dj2 is not None else None
                self._dj_conns[key] = None
                return None
        except Exception:
            pass
        return None

    def _dj_conn(self, key: Optional[str]):
        """Django Connection object for a global id (cached, None-safe)."""
        if not key:
            return None
        if key not in self._dj_conns:
            self._global_conn_id(key)
        return self._dj_conns.get(key)

    def _conn_key_of_table(self, table_norm: str) -> Optional[str]:
        """Global connection id most used by a table's fields (None = default DB)."""
        try:
            _im = getattr(self, "_iot_mirror", None) or {}
            _in = self._norm_table(table_norm)
            if _in and _in in _im:
                return str(_im[_in].get("gid") or "") or None
        except Exception:
            pass
        try:
            _um = self._union_partitions()
            if _um and self._norm_table(table_norm) in _um:
                return None  # مدموج UNION على الأساسي — يُوجَّه لقاعدة الأساس
        except Exception:
            pass
        counts: Dict[str, int] = {}
        for f in (getattr(self, "fields", []) or []):
            ts = getattr(f, "table_source", None)
            if not ts or self._norm_table(ts) != table_norm:
                continue
            gid = self._global_conn_id(getattr(f, "connection_id", None) or getattr(f, "conn_id", None))
            if gid:
                counts[gid] = counts.get(gid, 0) + 1
        if not counts:
            return None
        return sorted(counts.items(), key=lambda kv: -kv[1])[0][0]

    def _get_direct_gid(self) -> Optional[str]:
        """sqlserver gid when EVERY involved table lives on ONE queryable
        sqlserver connection (else None → normal execution: direct reads +
        Python merges, never staged).

        Pure metadata (cached Django lookups only) — safe to call anywhere.
        Opt-out per report: <rpt_metadata direct="0"> disables the shortcut.
        """
        try:
            try:
                _ra = (getattr(self, "metadata", None) or {}).get("raw_attrs") or {}
                for _k, _v in _ra.items():
                    if str(_k).lower() == "direct" and str(_v).strip().lower() in ("0", "false", "no"):
                        return None
            except Exception:
                pass
            try:
                if self._union_partitions():
                    return None
            except Exception:
                return None
            _norms = []
            for f in (getattr(self, "fields", []) or []):
                try:
                    t = self._norm_table(getattr(f, "table_source", None) or "")
                except Exception:
                    t = ""
                if t and t not in _norms:
                    _norms.append(t)
            try:
                _det = self.compiler.detail() if hasattr(self.compiler, "detail") else None
                _det = _det.to_dict() if _det is not None and not isinstance(_det, dict) else _det
                if _det and _det.get("table"):
                    t = self._norm_table(_det.get("table"))
                    if t and t not in _norms:
                        _norms.append(t)
            except Exception:
                pass
            if not _norms:
                return None
            _gids = set()
            for t in _norms:
                try:
                    _ck = self._conn_key_of_table(t)
                except Exception:
                    return None
                if not _ck:
                    return None
                try:
                    _row = self._dj_conn(_ck)
                except Exception:
                    return None
                if _row is None:
                    return None
                if str(getattr(_row, "engine", "") or "").lower() != "sqlserver":
                    return None
                try:
                    _fl = getattr(_row, "is_queryable", True)
                    _ok = False if _fl is False or str(_fl).strip().lower() in ("0", "false", "no", "none") else True
                except Exception:
                    _ok = True
                if not _ok:
                    return None
                _gids.add(str(_ck))
            if len(_gids) == 1:
                return next(iter(_gids))
        except Exception:
            pass
        return None

    def _mssql_db_for(self, gid: str):
        """Cached live SqlServerDirect wrapper for a global connection id."""
        try:
            _c = getattr(self, "_mssql_direct", None)
            if _c is None:
                self._mssql_direct = _c = {}
            if gid in _c and _c[gid] is not None:
                return _c[gid]
            row = self._dj_conn(gid)
            if row is None:
                raise ValueError(f"الاتصال ({gid}) غير موجود")
            w = SqlServerDirect(row)
            _c[gid] = w
            return w
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"تعذر تهيئة الاتصال المباشر ({gid}): {e}")

    def _db_for_conn(self, conn_key: Optional[str]):
        """Live engine for a connection key (primary db when None/unknown)."""
        if not conn_key:
            return self.db
        if str(conn_key) == str(getattr(self, "primary_conn", None)):
            return self.db
        if conn_key in (self.databases or {}):
            return self.databases[conn_key]
        # Also accept rml-local ids
        for conn in (getattr(self, "connections", []) or []):
            if str(getattr(conn, "id", "")) == conn_key:
                gid = str(getattr(conn, "connection_id", "") or "")
                if gid and gid in (self.databases or {}):
                    return self.databases[gid]
        # Direct live mode: every table on one queryable sqlserver connection —
        # query the source itself, never stage.
        try:
            if conn_key and str(conn_key) == str(self._get_direct_gid()):
                return self._mssql_db_for(conn_key)
        except Exception:
            pass
        # Queryable SQL Server on ANY gid: live direct reads (transpiled),
        # never staged — multi-connection reports merge in Python.
        try:
            if conn_key and self._direct_sql_gid(conn_key):
                _e2 = ""
                try:
                    _e2 = str(getattr(self._dj_conn(conn_key), "engine", "") or "").lower()
                except Exception:
                    _e2 = ""
                if _e2 == "sqlserver":
                    return self._mssql_db_for(conn_key)
        except Exception:
            pass
        # Non-SQL connections (e.g. ZK devices) have no live engine: their
        # tables are served from instant local mirrors (see _iot_mirror,
        # refreshed per request). Anything else unresolvable fails loudly.
        try:
            _live = self._live_engine_for_gid(conn_key)
            if _live is not None:
                return _live
        except Exception:
            pass
        raise ValueError(
            f'الجدول على اتصال غير مهيأ (id={conn_key}). '
            f'أضف الاتصال للتقرير وتحقق من إعدادات التنفيذ.')

    def _schema_for_table(self, table_norm: str, conn_key: Optional[str], default_schema: Optional[str]) -> Optional[str]:
        """Schema for a table: IoT mirror -> connection.schema -> report schema -> dialect default."""
        try:
            _im = getattr(self, "_iot_mirror", None) or {}
            _in = self._norm_table(table_norm)
            if _in and _in in _im:
                return str(_im[_in].get("schema") or "") or None
        except Exception:
            pass
        try:
            if self._staged_temp_of(table_norm):
                return None  # جدول مؤقت مرحّل — ظاهر في الجلسة دون مخطط
        except Exception:
            pass
        try:
            # SQL Server: source schema (or dbo) — never the PG report schema,
            # for any gid, not just single-connection direct mode.
            if conn_key:
                dj0 = self._dj_conn(conn_key)
                if dj0 is not None and str(getattr(dj0, "engine", "") or "").lower() == "sqlserver":
                    _ds = str(getattr(dj0, "schema", "") or "").strip()
                    return _ds or "dbo"
        except Exception:
            pass
        dj = self._dj_conn(conn_key)
        if dj is not None and getattr(dj, "schema", None) and str(dj.schema).strip():
            return str(dj.schema).strip()
        if default_schema and str(default_schema).strip():
            return str(default_schema).strip()
        return None

    @staticmethod
    def _norm_key_value(v):
        """Normalize join-key values across drivers (Decimal/datetime)."""
        try:
            from decimal import Decimal
            import datetime as _dt
            if isinstance(v, bool):
                return v
            if isinstance(v, Decimal):
                f = float(v)
                return int(f) if f.is_integer() else f
            if isinstance(v, float) and v.is_integer():
                return int(v)
            if isinstance(v, _dt.datetime):
                return v.date() if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0) else v
        except Exception:
            pass
        return v

    # ── Internal column references ([col] inside expressions) ─────────
    # A column may reference another COLUMN by [name]/[alias]; the referenced
    # raw expression is inlined (recursively, with cycle detection). When the
    # referenced column is a formatting wrapper (TO_CHAR(..)||suffix), only its
    # numeric core is inlined so arithmetic like [col_2]-[col_4] stays numeric.

    @staticmethod
    def _strip_format_wrapper(raw: str) -> str:
        """TO_CHAR(<core>, fmt) || 'lit' -> <core>; otherwise raw unchanged."""
        s = (raw or "").strip()
        m = re.match(r"TO_CHAR\s*\(", s, re.IGNORECASE)
        if not m:
            return raw
        i = m.end() - 1
        depth = 0
        in_str = False
        j = i
        while j < len(s):
            ch = s[j]
            if ch == "'":
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if j >= len(s):
            return raw
        inner_full = s[i + 1:j]
        rest = s[j + 1:].strip()
        if rest and not re.fullmatch(r"(\|\|.*)?", rest, re.DOTALL):
            return raw
        # Top-level comma split -> first arg is the core
        depth = 0
        in_str = False
        for k, ch in enumerate(inner_full):
            if ch == "'":
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "," and depth == 0:
                    return inner_full[:k].strip()
        return inner_full.strip()

    def _at_column_maps(self, columns=None, scope_extra=None):
        """(by_name, by_alias) over report columns for @Alias expansion."""
        if columns is None:
            columns = getattr(self, "columns", None)
        all_cols = list(columns or []) + list(scope_extra or [])
        by_name: Dict[str, Any] = {}
        by_alias: Dict[str, Any] = {}
        for c in all_cols:
            n = (getattr(c, "name", None) or "").strip().lower()
            a = (getattr(c, "alias", None) or "").strip().lower()
            if n and n not in by_name:
                by_name[n] = c
            if a and a not in by_alias and a not in by_name:
                by_alias[a] = c
        return by_name, by_alias

    # ── col_refname value references (@refname = final row value) ──────
    # Unlike @Alias/[...] (which splice the referenced column's EXPRESSION
    # into the caller), @refname resolves to the referenced column's final
    # per-row VALUE via nested derived tables. Columns without col_refname
    # keep the old merging behavior untouched.
    _REFNAME_TOKEN_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)")
    _REFNAME_SEG_RE = re.compile(r"('(?:[^']|'')*')")

    @staticmethod
    def _refname_of(col) -> str:
        try:
            v = str(getattr(col, "col_refname", "") or "").strip().lower()
        except Exception:
            return ""
        return v if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v or "") else ""

    def _refname_map(self, columns) -> Dict[str, Any]:
        """{refname_lower: column} — first wins on duplicates."""
        out: Dict[str, Any] = {}
        for c in (columns or []):
            r = self._refname_of(c)
            if r and r not in out:
                out[r] = c
        return out

    def _value_tokens(self, text, refmap) -> set:
        """@refname (or [refname]) tokens in text that name a known col_refname (literals skipped)."""
        found = set()
        if not text or not refmap:
            return found
        st = str(text)
        if "@" not in st and "[" not in st:
            return found
        for seg in re.split(self._REFNAME_SEG_RE, st)[0::2]:
            for m in self._REFNAME_TOKEN_RE.finditer(seg):
                k = str(m.group(1)).lower()
                if k in refmap:
                    found.add(k)
            for m in re.finditer(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", seg):
                k = str(m.group(1)).lower()
                if k in refmap:
                    found.add(k)
        return found

    def _rewrite_value_expr(self, text, alias_of) -> str:
        """Replace @refname (and [refname]) with the referenced column's quoted SELECT alias."""
        segs = re.split(self._REFNAME_SEG_RE, str(text or ""))
        for i in range(0, len(segs), 2):
            def _rep(m):
                key = str(m.group(1)).lower()
                return alias_of.get(key, m.group(0))
            def _rep_b(m):
                key = str(m.group(1)).lower()
                if key in alias_of:
                    return alias_of[key]
                return m.group(0)
            s = re.sub(r"(?<![\w$#@.\"'])@([A-Za-z_][A-Za-z0-9_]*)(?![\w])", _rep, segs[i])
            s = re.sub(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", _rep_b, s)
            segs[i] = s
        return "".join(segs)

    def _plan_value_refs(self, columns, extra_aliases=None):
        """Topo plan for @refname dependents. None when no value-refs exist.

        Returns {base, levels, resolved, alias_of} where levels are
        dependency-ordered column groups and resolved maps id(col) →
        rewritten SQL (expr + where_clause applied). Raises ValueError
        (Arabic) on self-reference, cycles, or non-SQL (python-eval)
        referenced columns.
        """
        cols = list(columns or [])
        refmap = self._refname_map(cols)
        if not refmap:
            return None
        _ids = {id(c) for c in cols}
        alias_of = {}
        for r, c in refmap.items():
            try:
                alias_of[r] = _q(getattr(c, "alias", None) or getattr(c, "name", None) or r)
            except Exception:
                alias_of[r] = '"%s"' % r
        for a in (extra_aliases or []):
            try:
                alias_of.setdefault(str(a or "").strip().lower(), _q(a))
            except Exception:
                pass
        deps: Dict[int, set] = {}
        uses = False
        for c in cols:
            toks = (self._value_tokens(getattr(c, "expr", "") or "", refmap)
                    | self._value_tokens(getattr(c, "where_clause", "") or "", refmap))
            mine = self._refname_of(c)
            own = set()
            for t in toks:
                tgt = refmap[t]
                if id(tgt) == id(c) or t == mine:
                    raise ValueError("مرجع ذاتي للقيمة: @%s يشير لنفس العمود" % t)
                own.add(t)
            if own:
                uses = True
            deps[id(c)] = own
        if not uses:
            return None
        # referenced columns must be SQL-materializable (no Python eval)
        for r, tgt in refmap.items():
            try:
                _traw = "%s %s" % (getattr(tgt, "expr", "") or "", getattr(tgt, "where_clause", "") or "")
            except Exception:
                _traw = ""
            if "__py_" in str(_traw) or re.search(
                    r"(?i)\b(XLOOKUP|VLOOKUP|FILTER|SUMIF|SUMIFS|COUNTIF|COUNTBLANK|COUNTBY|SUMBY|SERIAL|ROWNUM|ROW)\s*\(", str(_traw)):
                using = sorted({getattr(c, "alias", None) or getattr(c, "name", "")
                                for c in cols if r in deps.get(id(c), set())})
                raise ValueError("المرجع @%s يحتاج تقييم Python (جدول %s) — القيمة المرجعية تعمل على أعمدة SQL فقط"
                                 % (r, ("، ".join([u for u in using if u]) or "?")))
        # Kahn levels (dependents after their dependencies)
        _cid = {id(c): c for c in cols}
        _indeg = {i: set(v) for i, v in deps.items()}
        levels, _done = [], set()
        while True:
            # nodes whose refname-deps are all done, in report order
            cand = [i for i in _indeg if i not in _done and all(
                (id(refmap[d]) in _done) for d in _indeg[i])]
            try:
                cand.sort(key=lambda i: cols.index(_cid[i]))
            except Exception:
                pass
            if not cand:
                break
            # only dependents form levels; base columns stay level-less
            _lvl = [i for i in cand if _indeg[i]]
            _done.update(cand)
            if _lvl:
                levels.append([_cid[i] for i in _lvl])
        if any(i not in _done for i in _indeg):
            _cyc = sorted({r for i, ds in _indeg.items() if i not in _done for r in ds})
            raise ValueError("مرجع دائري بين قيم الأعمدة: %s" % (" ← ".join(_cyc) or "?"))
        base = [c for c in cols if not deps.get(id(c))]
        resolved = {}
        for lvl in levels:
            for c in lvl:
                try:
                    _raw = self._rewrite_value_expr(
                        getattr(c, "expr", None) or getattr(c, "name", "") or "", alias_of)
                    _wc = getattr(c, "where_clause", None) or ""
                    if _wc and str(_wc).strip():
                        _wc = self._rewrite_value_expr(str(_wc), alias_of)
                        _raw = _apply_column_where(_raw, _wc)
                    resolved[id(c)] = _raw
                except ValueError:
                    raise
                except Exception as e:
                    raise ValueError("تعذر حل مرجع القيمة في '%s': %s" % (
                        getattr(c, "alias", None) or getattr(c, "name", ""), str(e)[:120]))
        return {"base": base, "levels": levels, "resolved": resolved, "alias_of": alias_of}

    def _inline_col_ref(self, col, stack, by_name, by_alias):
        """Inline [column] refs in one column expr (recursive, cycle-loud)."""
        try:
            stack = list(stack)
        except Exception:
            stack = []
        raw = (getattr(col, "expr", None) or getattr(col, "name", None) or "").strip()
        cid = getattr(col, "id", None) or getattr(col, "name", "")

        def sub(m):
            inner = m.group(1).strip()
            key = inner.lower()
            tgt = by_name.get(key) or by_alias.get(key)
            if tgt is None:
                return m.group(0)  # a field (validated later) or unknown
            tid = getattr(tgt, "id", None) or getattr(tgt, "name", "")
            if tid == stack[-1]:
                return m.group(0)  # same-name self mention = field ref, not recursion
            if tid in stack:
                chain = " ← ".join(stack + [tid])
                raise ValueError(f"مرجع دائري بين الأعمدة: {chain}")
            inlined = self._inline_col_ref(tgt, stack + [tid], by_name, by_alias)
            return "(" + self._strip_format_wrapper(inlined) + ")"

        out = re.sub(r"[\[{]([^\].\[{}]+)[\]}]", sub, raw)
        return self._sub_at_refs(out, by_name, by_alias, stack)

    def _sub_at_refs(self, text, by_name, by_alias, stack):
        """Expand @Alias (computed columns) in free text. Literal-safe.

        get(@A) splices the wrapper (like get([A])→[A]); @A inlines
        parenthesized. Unknown @tokens stay for the field pass ([...]/DB).
        Cycles raise loudly. Idempotent (output holds no @Alias).
        """
        if not text or "@" not in str(text):
            return text
        keys = sorted(set(by_name) | set(by_alias), key=len, reverse=True)
        if not keys:
            return text
        alt = "|".join(re.escape(k) for k in keys)

        def _tgt(key):
            # @refname (value reference) is owned by the derived-table pass —
            # never inline-merge its expression. Everything else as before.
            try:
                _kl = str(key or "").lower()
                for _d in (by_name, by_alias):
                    for _cc in list((_d or {}).values()):
                        if self._refname_of(_cc) == _kl:
                            return None
            except Exception:
                pass
            return by_name.get(key) or by_alias.get(key)

        def _expand(tgt):
            tid = getattr(tgt, "id", None) or getattr(tgt, "name", "")
            if stack and tid == stack[-1]:
                return None  # self mention = field ref, leave it
            if tid in stack:
                chain = " ← ".join(list(stack) + [tid])
                raise ValueError(f"مرجع دائري بين الأعمدة: {chain}")
            return self._inline_col_ref(tgt, tuple(stack) + (tid,), by_name, by_alias)

        def _getat(m):
            tgt = _tgt(m.group(1).lower())
            if tgt is None:
                return m.group(0)
            inner = _expand(tgt)
            return ("(" + inner + ")") if inner is not None else m.group(0)

        def _genat(m):
            tgt = _tgt(m.group(1).lower())
            if tgt is None:
                return m.group(0)
            inner = _expand(tgt)
            return ("(" + inner + ")") if inner is not None else m.group(0)

        segs = re.split(r"('(?:[^']|'')*')", str(text))
        for i in range(0, len(segs), 2):
            seg = segs[i]
            seg = re.sub(r"(?<![\w$#\.\"'\u0600-\u06FF])get\s*\(\s*@(" + alt + r")\s*\)",
                         _getat, seg, flags=re.IGNORECASE)
            dparts = re.split(r'("[^"]*")', seg)
            for di in range(0, len(dparts), 2):
                dparts[di] = re.sub(
                    r"(?<![\w$#@.\"'\u0600-\u06FF])@(" + alt + r")(?![\w\u0600-\u06FF])",
                    _genat, dparts[di], flags=re.IGNORECASE)
            segs[i] = "".join(dparts)
        return "".join(segs)

    def _expand_at_aliases(self, text):
        """Expand @ColumnAlias in free text (general_where etc.). Idempotent."""
        if not text or "@" not in str(text):
            return text
        by_name, by_alias = self._at_column_maps()
        return self._sub_at_refs(str(text), by_name, by_alias, ())

    def _wrap_value_levels(self, level0_select, from_q, where_clause, group_clause,
                             order_clause, paginate_clause, report_aliases, levels, resolved):
        """Nest derived tables for @refname dependents; outer keeps report order.

        level0_select already holds base (+extra) items. Each level adds its
        rewritten items over the previous level (SELECT prev.*, ...). The
        final outer projects the report aliases in order, then ORDER
        (ordinals — still valid) + pagination apply once, outermost.
        """
        inner = "%s FROM %s%s%s" % (level0_select, from_q, where_clause, group_clause)
        for _li, _lvl in enumerate(levels):
            _items = []
            for _c in _lvl:
                try:
                    _items.append("%s AS %s" % (resolved[id(_c)], _q(
                        getattr(_c, "alias", None) or getattr(_c, "name", None) or "")))
                except Exception as e:
                    raise ValueError("تعذر بناء '%s': %s" % (
                        getattr(_c, "alias", None) or getattr(_c, "name", ""), str(e)[:120]))
            inner = "SELECT _t%d.*, %s FROM (%s) _t%d" % (_li, ", ".join(_items), inner, _li)
        outer_cols = ", ".join(_q(a) for a in report_aliases)
        return "SELECT %s FROM (%s) _tv%s%s" % (outer_cols, inner, order_clause, paginate_clause)

    def _inline_column_refs(self, columns, scope_extra=None):
        """Return column copies with [column]/@Alias refs inlined (fields untouched)."""
        import dataclasses
        by_name, by_alias = self._at_column_maps(columns, scope_extra)
        out = []
        for c in (columns or []):
            cid = getattr(c, "id", None) or getattr(c, "name", "")
            try:
                new_raw = self._inline_col_ref(c, [cid], by_name, by_alias)
            except ValueError:
                raise
            _wc0 = getattr(c, "where_clause", None) or ""
            new_wc = _wc0
            if _wc0 and "@" in str(_wc0):
                try:
                    new_wc = self._sub_at_refs(str(_wc0), by_name, by_alias, [cid])
                except ValueError:
                    raise
                except Exception:
                    new_wc = _wc0
            _kw = {}
            if new_raw != (c.expr or ""):
                _kw["expr"] = new_raw
            if new_wc != _wc0:
                _kw["where_clause"] = new_wc
            out.append(dataclasses.replace(c, **_kw) if _kw else c)
        return out

    # ── Multi-table JOIN support ────────────────────────────────────────
    # When a report references fields from more than one table, the engine
    # interprets it as a JOIN instead of failing with "invalid identifier":
    # - bare (non-aggregated) refs  -> auto LEFT JOIN on inferred key
    # - aggregates over a secondary table -> grain-preserving correlated
    #   subquery (so SUM/COUNT of the base table are never inflated)

    _AGG_FUNCS = ("SUM", "COUNT", "AVG", "MIN", "MAX")

    @staticmethod
    def _norm_table(t: str) -> str:
        """Upper-case table name without schema prefix/quotes (for compare)."""
        s = str(t or "").strip().replace('"', "")
        if "." in s:
            s = s.split(".")[-1]
        return s.upper()

    @staticmethod
    def _bracket_refs(text: str) -> List[str]:
        """Field names referenced as [name] or {name} (skips [ns.member])."""
        return re.findall(r"[\[{]([^\].\[{}]+)[\]}]", str(text or ""))

    def _refs_in_text(self, text: str, field_table: Dict[str, str]) -> set:
        """Field keys referenced in raw SQL: [refs]/{refs} + bare/quoted identifiers.

        Supports qualified refs [table.col] / [conn.table.col] (resolved to
        their field key). Single-quoted literals are ignored;
        already-qualified (dot-adjacent) tokens are ignored.
        """
        found: set = set()
        for r in self._bracket_refs(text):
            if r.strip().lower() in field_table:
                found.add(r.strip().lower())
        for m in re.finditer(r"[\[{]([A-Za-z0-9_][A-Za-z0-9_.]*)[\]}]", str(text or "")):
            inner = m.group(1).strip()
            if "." in inner:
                key = self._match_qualified(inner, field_table)
                if key:
                    found.add(key)
        # strip single-quoted literals
        segs = re.split(r"('(?:[^']|'')*')", str(text or ""))
        try:
            from .namespaces import _BARE2_RE as _bare2re
        except Exception:
            _bare2re = None
        for i in range(0, len(segs), 2):
            seg = segs[i]
            # get(...) transparency (mirrors _resolve_expression): detect refs
            # inside the wrapper exactly as if it weren't there; a 3-part
            # conn.table.col maps to table.col for table-first binding.
            def _get_scan_rep(_m):
                _in = (_m.group(1) or "").strip()
                if re.search(r"[\[\]{}'\"]", _in):
                    return _in
                _pp = [p.strip() for p in _in.split(".")]
                if len(_pp) == 3 and all(_pp):
                    return f"{_pp[1]}.{_pp[2]}"
                return _in
            scan = seg
            for _gi in range(4):
                _ns, _nn = re.subn(r"(?<![\w$#\.\"'\u0600-\u06FF])get\s*\(([^()]*)\)",
                                   _get_scan_rep, scan, flags=re.IGNORECASE)
                if not _nn:
                    break
                scan = _ns
            # @refs (internal columns): @name / @tbl.col / @conn.tbl.col —
            # same binding as brackets. Emails (user@host), @@globals and
            # quoted spans never match.
            for _dseg in re.split(r'("[^"]*")', scan)[0::2]:
                for _am in re.finditer(
                        r"(?<![\w$#@.\"'\u0600-\u06FF])@([A-Za-z_0-9\u0600-\u06FF][A-Za-z_0-9\u0600-\u06FF \t.]*)",
                        _dseg):
                    _araw = _am.group(1)
                    _abody = _araw.strip()
                    while _abody:
                        _at = _abody.rstrip(".").strip()
                        _hit = None
                        if _at:
                            if "." not in _at:
                                if _at.lower() in field_table:
                                    _hit = _at.lower()
                            else:
                                try:
                                    _hit = self._match_qualified(_at, field_table)
                                except Exception:
                                    _hit = None
                                if _hit is None:
                                    # 3-part with rejected conn still reveals
                                    # its table for routing (resolution
                                    # raises the conn error loudly later).
                                    try:
                                        _pp = [p.strip() for p in _at.split(".") if p.strip() != ""]
                                        if len(_pp) == 3:
                                            for _f in (getattr(self, "fields", []) or []):
                                                _n = getattr(_f, "name", None)
                                                if _n and str(_n).strip().lower() == _pp[2].lower() and \
                                                        self._norm_table(getattr(_f, "table_source", None) or "") == self._norm_table(_pp[1]):
                                                    _hit = str(_n).strip().lower()
                                                    break
                                    except Exception:
                                        pass
                        if _hit is not None:
                            found.add(_hit)
                            break
                        _ap = _abody.split()
                        if len(_ap) <= 1:
                            break
                        _abody = " ".join(_ap[:-1])
            for m in re.finditer(r'(?<!\.)"([A-Za-z_][A-Za-z0-9_]*)"(?!\.)', scan):
                if m.group(1).lower() in field_table:
                    found.add(m.group(1).lower())
            names = sorted(field_table.keys(), key=len, reverse=True)
            if names:
                alt = "|".join(re.escape(n) for n in names)
                for m in re.finditer(r"(?<!\.)\b(" + alt + r")\b(?!\.)", scan, re.IGNORECASE):
                    found.add(m.group(1).lower())
            # bare table.field with explicit table binding (longest-match,
            # Arabic-aware): `tbl.col` binds to tbl even when `col` also
            # exists in other tables (mirrors the SQL emitter, which binds
            # table-first via <fields>, not via the last-wins flat map).
            if _bare2re is not None:
                try:
                    _all_fields = list(getattr(self, "fields", []) or [])
                except Exception:
                    _all_fields = []
                try:
                    # Build from <fields> directly (NOT from the flat map: its
                    # last-wins values drop tables whose fields were all
                    # overwritten by same-named fields of other tables).
                    _known_t = {self._norm_table(str(getattr(_f, "table_source", "") or ""))
                                for _f in _all_fields}
                except Exception:
                    _known_t = set()
                for _bm in _bare2re.finditer(scan):
                    _hd, _words = _bm.group(1), _bm.group(2)
                    try:
                        if _bm.end() < len(scan) and scan[_bm.end()] == "(":
                            continue
                        if self._norm_table(_hd) not in _known_t:
                            continue
                    except Exception:
                        continue
                    _toks = str(_words or "").split()
                    for _k in range(len(_toks), 0, -1):
                        _cand = " ".join(_toks[:_k]).strip().lower()
                        if not _cand or _cand == "*":
                            continue
                        if _cand not in field_table:
                            continue
                        try:
                            _owned = any(
                                str(getattr(_f, "name", "") or "").strip().lower() == _cand
                                and self._norm_table(getattr(_f, "table_source", "") or "")
                                == self._norm_table(_hd)
                                for _f in _all_fields)
                        except Exception:
                            _owned = False
                        if _owned:
                            found.add(_cand)
                            break
        return found

    def _link_index(self) -> List[Dict[str, str]]:
        """All <links> as dicts (cached per engine instance)."""
        if getattr(self, "_link_index_cache", None) is None:
            try:
                links = self.compiler.links() if hasattr(self.compiler, "links") else []
            except Exception:
                links = []
            self._link_index_cache = [l for l in (links or []) if isinstance(l, dict)]
        return self._link_index_cache or []

    def _find_link(self, t1: str, t2: str) -> Optional[Dict[str, str]]:
        """Link between two normalized tables (either direction) or None."""
        a, b = self._norm_table(t1), self._norm_table(t2)
        if not a or not b:
            return None
        for l in self._link_index():
            fa, ta = self._norm_table(l.get("from_table")), self._norm_table(l.get("to_table"))
            if (fa == a and ta == b) or (fa == b and ta == a):
                return l
        return None

    def _transitive_link_error(self, base_norm: str, s: str, sec_all) -> Optional[str]:
        """Loud guidance when a secondary links only via another secondary.

        The planner joins every secondary DIRECTLY to the base table — a
        chain (base ↔ mid ↔ leaf) is not followed. Without this guard the
        leaf either hits the generic inference error or, worse, silently
        joins on a coincidental shared-name key (e.g. ID ↔ id) matching
        nothing → empty columns with no error.
        """
        try:
            if self._find_link(base_norm, s) is not None:
                return None
            _trans = [t for t in (sec_all or []) if t != s and self._find_link(s, t) is not None]
            if _trans:
                return (f'الجدول "{s}" مربوط بالجدول "{_trans[0]}" وليس بالجدول الأساسي "{base_norm}" — '
                        f'المخطط الحالي سلسلة: "{base_norm}" ↔ "{_trans[0]}" ↔ "{s}". '
                        f'المحرك يربط كل جدول بالأساسي مباشرة فقط ولا يتبع السلاسل، '
                        f'لذلك لا تُجلب بيانات "{s}". '
                        f'الحل: أضف رابطاً مباشراً بين "{s}" و"{base_norm}" '
                        f'(وإن كانت القيمة مضمّنة داخل نص استخدم match="contains" أو match="regex" مع pattern)، '
                        f'أو اجعل "{_trans[0]}" هو الجدول الافتراضي.')
        except Exception:
            pass
        return None

    def _link_match_spec(self, base_norm: str, sec_norm: str) -> Dict[str, str]:
        """{match, pattern} of the link between two tables.

        Modes are symmetric (no orientation): exact (col = col),
        contains (either value inside the other), regex (extract `pattern`
        from both sides, compare extracts). Unknown modes raise loudly.
        """
        try:
            link = self._find_link(base_norm, sec_norm)
        except Exception:
            link = None
        match = str((link or {}).get("match") or "exact").strip().lower()
        if match not in ("exact", "contains", "regex"):
            raise ValueError(
                f'نوع مطابقة غير معروف "{match}" في الربط بين "{base_norm}" و"{sec_norm}" — '
                f'المسموح: exact | contains | regex.')
        pattern = str((link or {}).get("pattern") or "")
        if match == "regex":
            if not pattern:
                raise ValueError(
                    f'الربط regex بين "{base_norm}" و"{sec_norm}" يتطلب pattern — '
                    f'مثال: pattern="(\\d+)".')
            try:
                re.compile(pattern)
            except Exception as e:
                raise ValueError(
                    f'نمط regex غير صالح في الربط بين "{base_norm}" و"{sec_norm}": {e}')
        return {"match": match, "pattern": pattern}

    @staticmethod
    def _link_needs_python(match: str, base_db) -> bool:
        """True when a link match mode cannot run in SQL on the base dialect.

        Only regex on MSSQL (no regex engine) forces the Python merge path;
        pg uses substring-from, oracle REGEXP_SUBSTR, contains works everywhere.
        """
        try:
            if str(match or "exact").strip().lower() == "regex":
                try:
                    return bool(_is_mssql_db(base_db))
                except Exception:
                    return False
        except Exception:
            pass
        return False

    @staticmethod
    def _link_join_on(match: str, pattern: str, lon: str, ron: str, base_db) -> Optional[str]:
        """ON-clause predicate for a link match mode. None → legacy/Python path.

        lon/ron: quoted "alias"."col" fragments (sec left, base right).
        Patterns interpolate as ''-escaped literals (no bind plumbing in plans).
        """
        m = str(match or "exact").strip().lower()
        if m == "exact":
            return None  # legacy equality path (incl. CAST-mismatch logic)
        try:
            pg = _is_pg_db(base_db)
        except Exception:
            pg = False
        try:
            ms = _is_mssql_db(base_db)
        except Exception:
            ms = False
        if m == "contains":
            if pg:
                l, r = f"({lon})::TEXT", f"({ron})::TEXT"
                return (f"(POSITION({r} IN {l}) > 0 OR POSITION({l} IN {r}) > 0)"
                        f" AND LENGTH({l}) > 0 AND LENGTH({r}) > 0")
            if ms:
                l, r = f"CAST({lon} AS NVARCHAR(4000))", f"CAST({ron} AS NVARCHAR(4000))"
                return (f"(CHARINDEX({r}, {l}) > 0 OR CHARINDEX({l}, {r}) > 0)"
                        f" AND LEN({l}) > 0 AND LEN({r}) > 0")
            l, r = f"TO_CHAR({lon})", f"TO_CHAR({ron})"
            return (f"(INSTR({l}, {r}) > 0 OR INSTR({r}, {l}) > 0)"
                    f" AND LENGTH({l}) > 0 AND LENGTH({r}) > 0")
        if m == "regex":
            pat = str(pattern or "").replace("'", "''")
            if not pat:
                return None
            if pg:
                return (f"(substring(({lon})::TEXT from '{pat}') = "
                        f"substring(({ron})::TEXT from '{pat}') AND "
                        f"substring(({lon})::TEXT from '{pat}') IS NOT NULL)")
            if ms:
                return None  # no regex engine → Python merge
            return (f"(REGEXP_SUBSTR(TO_CHAR({lon}), '{pat}', 1, 1, NULL, 1) = "
                    f"REGEXP_SUBSTR(TO_CHAR({ron}), '{pat}', 1, 1, NULL, 1) AND "
                    f"REGEXP_SUBSTR(TO_CHAR({lon}), '{pat}', 1, 1, NULL, 1) IS NOT NULL)")
        return None

    def _analyze_lookup(self, col, scope_norm: str):
        """Decide lookup handling for a column — no stored props needed.

        A single display ref in the expression suffices:
        - qualified `[conn.T.C]` / `[T.C]` → T is explicit;
        - bare `[C]` → T is inferred from the links of the column's position
          (detail scope = sub table, master scope = default table): the linked
          table holding C. The scope table itself always wins for bare names.

        Returns (status, payload):
          ('explicit', None) — complete stored attrs; legacy path handles it
          ('derived', {...}) — effective props (display ref + scope↔T link)
          ('unlinked', [tables]) — foreign ref but no link; link the tables
          ('ambiguous', [tables]) — bare name in several linked tables; qualify [T.C]
          ('none', None) — in-scope/direct/unknown (legacy behavior)
        """
        try:
            if getattr(col, "ref_table", None) and getattr(col, "ref_fk", None) and getattr(col, "ref_display", None):
                return ("explicit", None)
            raw = (getattr(col, "expr", None) or getattr(col, "name", None) or "").strip()
            if not raw or "$" in raw:
                return ("none", None)
            scope_norm = self._norm_table(scope_norm)
            fields = getattr(self, "fields", []) or []
            try:
                from .namespaces import _unwrap_get_for_plan as _uget
                raw = _uget(raw)
            except Exception:
                pass

            def _fields_with(name, table=None):
                out = []
                for _f in fields:
                    if str(getattr(_f, "name", "") or "").lower() != str(name).lower():
                        continue
                    if table is not None and self._norm_table(getattr(_f, "table_source", "") or "") != table:
                        continue
                    out.append(_f)
                return out

            def _orient(tn):
                link = self._find_link(scope_norm, tn)
                if not link:
                    return None
                # Fuzzy links cannot drive exact lookup proxies.
                if str(link.get("match") or "exact").strip().lower() != "exact":
                    return None
                fa = self._norm_table(link.get("from_table"))
                if fa == scope_norm:
                    skey, rfk = (link.get("from_col") or ""), (link.get("to_col") or "")
                else:
                    skey, rfk = (link.get("to_col") or ""), (link.get("from_col") or "")
                if not skey or not rfk:
                    return None
                jt = _norm_join_type(link.get("rel_type") or link.get("relType") or link.get("rel") or "")
                return {"scope_key": skey, "scope_table": scope_norm, "join_type": jt}

            toks = re.findall(r"\[([A-Za-z0-9_][A-Za-z0-9_.]*)\]", raw)
            if len(toks) != 1:
                return ("none", None)  # zero or mixed refs -> legacy behavior
            tok = toks[0].strip()
            parts = [p.strip() for p in tok.split(".") if p.strip() != ""]
            if len(parts) >= 2:
                # qualified: [T.C] or [conn.T.C] (T = parts[-2], C = parts[-1])
                tbl, disp = parts[-2], parts[-1]
                tn = self._norm_table(tbl)
                if not tn or tn == scope_norm or not _fields_with(disp, tn):
                    return ("none", None)
                o = _orient(tn)
                if not o:
                    return ("unlinked", [tbl])
                link = self._find_link(scope_norm, tn)
                fa = self._norm_table(link.get("from_table"))
                o.update({"ref_table": tbl,
                          "ref_fk": (link.get("to_col") or "") if fa == scope_norm else (link.get("from_col") or ""),
                          "ref_display": disp})
                return ("derived", o)
            # bare [C]: scope wins; else the linked table holding C
            name = parts[0]
            if not _fields_with(name):
                return ("none", None)  # unknown -> legacy (loud DB error as before)
            if _fields_with(name, scope_norm):
                return ("none", None)  # scope table wins -> direct
            tables = sorted({self._norm_table(getattr(_f, "table_source", "") or "")
                             for _f in _fields_with(name)} - {scope_norm, ""})
            if not tables:
                return ("none", None)
            linked = [t for t in tables if self._find_link(scope_norm, t)]
            if len(linked) == 1:
                tn = linked[0]
                o = _orient(tn)
                if not o:
                    return ("unlinked", [tn])
                src = next((str(getattr(_f, "table_source", "") or "")
                            for _f in _fields_with(name) if self._norm_table(getattr(_f, "table_source", "") or "") == tn), tn)
                link = self._find_link(scope_norm, tn)
                fa = self._norm_table(link.get("from_table"))
                o.update({"ref_table": src.split(".")[-1] if "." in src else src,
                          "ref_fk": (link.get("to_col") or "") if fa == scope_norm else (link.get("from_col") or ""),
                          "ref_display": name})
                return ("derived", o)
            if len(linked) > 1:
                return ("ambiguous", linked)
            return ("unlinked", tables)
        except Exception:
            return ("none", None)

    def _with_derived_lookup(self, col, scope_norm: str):
        """Column copy with runtime-derived lookup props (or the same object)."""
        status, d = self._analyze_lookup(col, scope_norm)
        if status != "derived" or not d:
            return col
        import dataclasses
        return dataclasses.replace(
            col, col_type="fk_lookup", ref_table=d["ref_table"], ref_fk=d["ref_fk"],
            ref_display=d["ref_display"], join_type=d["join_type"],
            ref_scope_key=d["scope_key"], ref_scope_table=d["scope_table"])

    def _ref_tables_in_text(self, text: str, prefer: Optional[str] = None) -> set:
        """Norm tables referenced in raw SQL (for JOIN planning).

        Qualified refs ([table.col]/[conn.table.col]) resolve to their OWN
        table via <fields> — NOT via the bare-name map (which binds to the
        last-imported table on duplicates, e.g. RT_BILL_NO in MST and DTL,
        causing spurious JOINs + fan-out). Bare refs fall back to the map,
        preferring `prefer` (base/detail table) when the field exists there.
        """
        ftm = self._field_table_map()
        fields = getattr(self, "fields", []) or []
        out: set = set()
        qkeys: set = set()
        for m in re.finditer(r"[\[{]([A-Za-z0-9_][A-Za-z0-9_.]*)[\]}]", str(text or "")):
            inner = m.group(1).strip()
            if "." not in inner:
                continue
            parts = [p.strip() for p in inner.split(".") if p.strip() != ""]
            fobj = None
            if len(parts) == 2:
                _t, _c = parts
                for _f in fields:
                    if str(getattr(_f, "name", "") or "").lower() == _c.lower() and \
                       self._norm_table(getattr(_f, "table_source", "") or "") == self._norm_table(_t):
                        fobj = _f
                        break
            elif len(parts) == 3:
                _ct, _t, _c = parts
                try:
                    _cmap = self._conn_map()
                except Exception:
                    _cmap = {}
                for _f in fields:
                    if str(getattr(_f, "name", "") or "").lower() != _c.lower():
                        continue
                    if self._norm_table(getattr(_f, "table_source", "") or "") != self._norm_table(_t):
                        continue
                    _conns = {str(getattr(_f, a, "") or "") for a in ("conn_id", "connection_id")}
                    if _ct in _conns or _cmap.get(_ct) in _conns or _ct in set(_cmap.values()):
                        fobj = _f
                        break
            if fobj is not None:
                out.add(self._norm_table(getattr(fobj, "table_source", "") or ""))
                qkeys.add(str(getattr(fobj, "name", "") or "").lower())
        # Bare XSQL-style `table.field` refs (no brackets): same longest-match
        # logic the SQL emitter uses, so JOIN planning sees exactly what the
        # emitter will resolve (shared helper in namespaces.py — no drift).
        try:
            from .namespaces import bare_ref_tables as _bare_rt
            for _tn, _fk in _bare_rt(str(text or ""), fields).items():
                out.add(_tn)
                qkeys.add(_fk)
        except Exception:
            pass
        for r in self._refs_in_text(text, ftm):
            if r in qkeys:
                continue
            if r in ftm:
                _t = ftm[r]
                if prefer and _t != prefer:
                    for _f in fields:
                        if str(getattr(_f, "name", "") or "").lower() == r and \
                           self._norm_table(getattr(_f, "table_source", "") or "") == prefer:
                            _t = prefer
                            break
                out.add(_t)
        return out

    def _field_table_map(self) -> Dict[str, str]:
        """Map field lower-name -> normalized table name."""
        m: Dict[str, str] = {}
        for f in (getattr(self, "fields", []) or []):
            n = getattr(f, "name", None)
            ts = getattr(f, "table_source", None)
            if n and ts:
                m[str(n).strip().lower()] = self._norm_table(ts)
        return m

    def _conn_map(self) -> Dict[str, str]:
        """{rml-local connection id: global connection id} for [conn...] validation."""
        if getattr(self, "_conn_map_cache", None) is None:
            m: Dict[str, str] = {}
            try:
                for c in (getattr(self, "connections", []) or []):
                    lid = str(getattr(c, "id", "") or "").strip()
                    gid = str(getattr(c, "connection_id", "") or "").strip()
                    if lid:
                        m[lid] = gid or lid
            except Exception:
                pass
            self._conn_map_cache = m
        return self._conn_map_cache or {}

    def _rx_defaults(self) -> set:
        """Normed tables explicitly marked is_default in <table_opts> (cached).

        Only explicit markings count: bare `get(col)` outside these tables
        must be written conn.table.col. Files without markings keep the
        legacy behavior (empty set = no enforcement).
        """
        try:
            cached = getattr(self, "_rx_defaults_cache", None)
            if cached is not None:
                return cached
        except Exception:
            pass
        out: set = set()
        try:
            from .namespaces import default_norms_from_opts
            comp = getattr(self, "compiler", None)
            if comp is not None and hasattr(comp, "table_opts"):
                out = default_norms_from_opts(comp.table_opts() or [])
        except Exception:
            out = set()
        try:
            self._rx_defaults_cache = out
        except Exception:
            pass
        return out

    def _match_qualified(self, inner: str, field_table: Dict[str, str]) -> Optional[str]:
        """Resolve a dotted [table.col] / [conn.table.col] ref to its field key.

        Table-first matching against <fields> (table_source / conn_id);
        returns None when it is not a field reference (e.g. [ns.member] —
        resolved later by the namespace registry).
        """
        parts = [p.strip() for p in str(inner or "").split(".") if p.strip() != ""]
        if len(parts) == 2:
            table, col = parts
            key = col.lower()
            for f in (getattr(self, "fields", []) or []):
                n = getattr(f, "name", None)
                if n and str(n).strip().lower() == key and \
                        self._norm_table(getattr(f, "table_source", None) or "") == self._norm_table(table):
                    return key
            return None
        if len(parts) == 3:
            ctok, table, col = parts
            key = col.lower()
            cmap = self._conn_map()
            from .namespaces import _field_conns, _conn_accepted
            for f in (getattr(self, "fields", []) or []):
                n = getattr(f, "name", None)
                if n and str(n).strip().lower() == key and \
                        self._norm_table(getattr(f, "table_source", None) or "") == self._norm_table(table) and \
                        _conn_accepted(ctok, _field_conns(f), cmap):
                    return key
            return None
        return None

    def _table_columns(self, table: str, schema: Optional[str], db=None) -> Dict[str, str]:
        """{COLUMN_UPPER: dtype} for a table (cached per DB). Works on Oracle + Postgres."""
        norm = self._norm_table(table)
        db = db if db is not None else getattr(self, "db", None)
        key = f"{self._db_identity(db)}|{(schema or '').upper()}.{norm}"
        if key in self._cols_cache:
            return self._cols_cache[key]
        try:
            _srec = None
            _stg = getattr(self, "_api_stage", None) or {}
            if norm in _stg:
                _srec = _stg[norm]
            else:
                _un = getattr(self, "_api_union", None) or {}
                if norm in _un:
                    _srec = _un[norm]
            if _srec:
                # مرحّل (API أو UNION): الأعمدة الدقيقة من حقول التقرير (تطابق DDL المؤقت)
                _sm = {_n.upper(): (_t or "TEXT").upper() for _n, _t in (_srec.get("cols") or [])}
                self._cols_cache[key] = dict(_sm)
                try:
                    if not hasattr(self, "_cols_orig") or self._cols_orig is None:
                        self._cols_orig = {}
                    self._cols_orig[key] = {_n.upper(): _n for _n, _t in (_srec.get("cols") or [])}
                except Exception:
                    pass
                return dict(_sm)
        except Exception:
            pass
        try:
            _im = getattr(self, "_iot_mirror", None) or {}
            if norm in _im:
                # Instant IoT mirror: columns are exactly the report fields
                # (they match the att-shaped mirror DDL by construction).
                _fm = {}
                for _f in (getattr(self, "fields", []) or []):
                    try:
                        if self._norm_table(getattr(_f, "table_source", "") or "") == norm:
                            _fn = str(getattr(_f, "name", "") or "")
                            if _fn:
                                _fm[_fn.upper()] = str(getattr(_f, "data_type", None) or "TEXT").upper()
                    except Exception:
                        continue
                if _fm:
                    self._cols_cache[key] = dict(_fm)
                    try:
                        if not hasattr(self, "_cols_orig") or self._cols_orig is None:
                            self._cols_orig = {}
                        self._cols_orig[key] = {str(getattr(_f, "name", "") or "").upper(): str(getattr(_f, "name", "") or "")
                                                for _f in (getattr(self, "fields", []) or [])
                                                if self._norm_table(getattr(_f, "table_source", "") or "") == norm
                                                and getattr(_f, "name", None)}
                    except Exception:
                        pass
                    return dict(_fm)
        except Exception:
            pass
        try:
            _api = self._api_table_info(norm)
        except Exception:
            _api = None
        _ms_gid = None
        try:
            _ms_gid = self._conn_key_of_table(norm)
        except Exception:
            _ms_gid = None
        _ms_live = False
        if _ms_gid and self._direct_sql_gid(_ms_gid):
            try:
                _ms_row = self._dj_conn(_ms_gid)
                _ms_live = (_ms_row is not None
                            and str(getattr(_ms_row, "engine", "") or "").lower() == "sqlserver")
            except Exception:
                _ms_live = False
        if _ms_live:
            # Live source introspection (no staging): true-case columns + types.
            try:
                _w = self._mssql_db_for(str(_ms_gid))
                _cur = _w._exec(
                    "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
                    [schema, norm])
                try:
                    _rows = list(_cur.fetchall() or [])
                finally:
                    try:
                        _cur.close()
                    except Exception:
                        pass
                _lc = {str(r[0]).upper(): str(r[1]).upper() for r in _rows}
                _lo = {str(r[0]).upper(): str(r[0]) for r in _rows}
                if _lc:
                    self._cols_cache[key] = dict(_lc)
                    try:
                        if not hasattr(self, "_cols_orig") or self._cols_orig is None:
                            self._cols_orig = {}
                        self._cols_orig[key] = dict(_lo)
                    except Exception:
                        pass
                    return dict(_lc)
            except Exception:
                pass
        # fall through to report-fields fallback below when introspection fails
        try:
            _fm = {}
            for _f in (getattr(self, "fields", []) or []):
                try:
                    if self._norm_table(getattr(_f, "table_source", None) or "") == norm:
                        _fn = str(getattr(_f, "name", "") or "")
                        if _fn:
                            _fm[_fn.upper()] = str(getattr(_f, "data_type", None) or "TEXT").upper()
                except Exception:
                    continue
            if _fm:
                self._cols_cache[key] = dict(_fm)
                return dict(_fm)
        except Exception:
            pass
        if _api:
            # جدول API (مثل البصمة) قبل الترحيل: أعمدة منطقية ثابتة بدون وصول للجهاز
            _static = self._api_static_columns()
            _sm = None
            for _tn, _cm in _static.items():
                if str(_tn).upper() == norm:
                    _sm = dict(_cm)
                    break
            if _sm is None:
                try:
                    _rw = _api.get("row")
                    _lbl = str(getattr(_rw, "name", "") or _api.get("gid") or "")
                except Exception:
                    _lbl = str(_api.get("gid") or "")
                _avail = ", ".join(sorted(_static.keys())) or "—"
                raise ValueError(
                    f"الجدول '{norm.lower()}' غير متوفر على المصدر '{_lbl}' — "
                    f"الجداول المتاحة: {_avail}.")
            self._cols_cache[key] = dict(_sm)
            try:
                if not hasattr(self, "_cols_orig") or self._cols_orig is None:
                    self._cols_orig = {}
                self._cols_orig[key] = {str(_n).upper(): str(_n) for _n in _sm}
            except Exception:
                pass
            return dict(_sm)
        cols: Dict[str, str] = {}
        orig: Dict[str, str] = {}
        try:
            is_pg = "postgres" in type(db).__name__.lower() if db else False
            if not db or not getattr(db, "conn", None):
                try:
                    db.connect()
                except Exception:
                    pass
            if is_pg:
                cur = db._exec(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema=:s AND table_name=:t",
                    {"s": (schema or "public"), "t": norm.lower()},
                )
            else:
                cur = db._exec(
                    "SELECT column_name, data_type FROM all_tab_columns "
                    "WHERE owner=:o AND table_name=:t ORDER BY column_id",
                    {"o": (schema or "").upper(), "t": norm},
                )
            try:
                for r in cur.fetchall():
                    cols[str(r[0]).upper()] = str(r[1]).upper()
                    orig[str(r[0]).upper()] = str(r[0])
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
        except Exception:
            pass
        self._cols_cache[key] = cols
        try:
            if not hasattr(self, "_cols_orig") or self._cols_orig is None:
                self._cols_orig = {}
            self._cols_orig[key] = orig
        except Exception:
            pass
        return cols

    def _orig_col(self, table_norm: str, upper_name: str, db=None, schema: Optional[str] = None) -> str:
        """Original-case column name for SQL quoting (DB truth wins).

        Prefers the database's own spelling (critical for case-sensitive
        engines like Postgres), then the RML field spelling, then a dialect
        fallback.
        """
        want = str(upper_name or "").upper()
        try:
            _db = db if db is not None else getattr(self, "db", None)
            _key = f"{self._db_identity(_db)}|{(schema or '').upper()}.{table_norm}"
            _oc = getattr(self, "_cols_orig", {}) or {}
            if _key in _oc and want in _oc[_key]:
                return _oc[_key][want]
        except Exception:
            pass
        try:
            for f in (getattr(self, "fields", []) or []):
                if self._norm_table(getattr(f, "table_source", "") or "") == table_norm \
                        and str(getattr(f, "name", "")).upper() == want:
                    return str(getattr(f, "name"))
        except Exception:
            pass
        try:
            _db = db if db is not None else getattr(self, "db", None)
            _key = f"{self._db_identity(_db)}|{(schema or '').upper()}.{table_norm}"
            _oc = getattr(self, "_cols_orig", {}) or {}
            if _key in _oc and want in _oc[_key]:
                return _oc[_key][want]
        except Exception:
            pass
        try:
            _db = db if db is not None else getattr(self, "db", None)
            if _db is not None and "postgres" in type(_db).__name__.lower():
                return str(upper_name).lower()
        except Exception:
            pass
        return str(upper_name)

    def _fk_keys(self, sec_norm: str, base_norm: str, schema: Optional[str], db=None) -> List[Tuple[str, str]]:
        """[(sec_col, base_col)] FKs from sec -> base (Oracle + Postgres)."""
        out: List[Tuple[str, str]] = []
        try:
            db = db if db is not None else getattr(self, "db", None)
            is_pg = "postgres" in type(db).__name__.lower() if db else False
            if is_pg:
                cur = db._exec(
                    "SELECT kcu.column_name, ccu.column_name FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu ON kcu.constraint_schema=tc.constraint_schema "
                    "AND kcu.constraint_name=tc.constraint_name "
                    "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_schema=tc.constraint_schema "
                    "AND ccu.constraint_name=tc.constraint_name "
                    "WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema=:s AND tc.table_name=:t "
                    "AND LOWER(ccu.table_name)=LOWER(:b)",
                    {"s": (schema or "public"), "t": sec_norm.lower(), "b": (base_norm or "").lower()},
                )
            else:
                cur = db._exec(
                    "SELECT acc.column_name, bcc.column_name FROM all_constraints ac "
                    "JOIN all_cons_columns acc ON acc.owner=ac.owner AND acc.constraint_name=ac.constraint_name "
                    "JOIN all_cons_columns bcc ON bcc.owner=ac.r_owner AND bcc.constraint_name=ac.r_constraint_name "
                    "AND bcc.position=acc.position "
                    "JOIN all_constraints rc ON rc.owner=ac.r_owner AND rc.constraint_name=ac.r_constraint_name "
                    "WHERE ac.owner=:o AND ac.table_name=:s AND ac.constraint_type='R' AND rc.table_name=:b",
                    {"o": (schema or "").upper(), "s": sec_norm, "b": base_norm},
                )
            try:
                for r in cur.fetchall():
                    out.append((str(r[0]).upper(), str(r[1]).upper()))
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
        except Exception:
            pass
        return out

    def _sqlserver_source_of(self, table_norm):
        """(row, gid, sch, tbl) — live SQL Server location of a table, else None."""
        try:
            _gid = self._conn_key_of_table(table_norm)
        except Exception:
            return None
        if not _gid:
            return None
        try:
            row = self._dj_conn(_gid)
        except Exception:
            return None
        if row is None or str(getattr(row, "engine", "") or "").lower() != "sqlserver":
            return None
        sch, tbl = "", ""
        try:
            for _f in (getattr(self, "fields", []) or []):
                if self._norm_table(getattr(_f, "table_source", None) or "") == table_norm:
                    _ts = str(getattr(_f, "table_source", "") or "")
                    if "." in _ts:
                        sch = _ts.split(".")[0]
                        tbl = _ts.split(".")[-1]
                    else:
                        tbl = _ts
                    break
        except Exception:
            pass
        if not tbl:
            return None
        if not sch:
            sch = str(getattr(row, "schema", "") or "").strip() or "dbo"
        return (row, str(_gid), sch, tbl)

    def _mssql_fk_keys(self, row, sch, tbl):
        """[(col, ref_schema, ref_table, ref_col)] FKs where parent = sch.tbl (upper)."""
        out = []
        try:
            user = str(getattr(row, "user", "") or "")
            pwd = str(getattr(row, "password", "") or "")
            for _srv, _dbn in self._sqlserver_targets(row):
                for _drv, _modern in self._sqlserver_drivers():
                    try:
                        import pyodbc
                        _parts = [f"DRIVER={{{_drv}}}", f"SERVER={_srv}", f"DATABASE={_dbn}",
                                  f"UID={user}", f"PWD={pwd}"]
                        if _modern:
                            _parts += ["TrustServerCertificate=yes", "Connect Timeout=10"]
                        # See SqlServerDirect.connect() — these flags are
                        # the canonical pyodbc workaround for HYC00 on
                        # SQLBindParameter across all driver versions.
                        _parts += ["AutoTranslate=no", "UseProcForPrepare=0"]
                        _cn = pyodbc.connect(";".join(_parts) + ";", timeout=10)
                        try:
                            _cur = _cn.cursor()
                            _cur.execute(
                                "SELECT pc.name, OBJECT_SCHEMA_NAME(fk.referenced_object_id), "
                                "OBJECT_NAME(fk.referenced_object_id), rc.name "
                                "FROM sys.foreign_keys fk "
                                "JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id "
                                "JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id "
                                "AND pc.column_id = fkc.parent_column_id "
                                "JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id "
                                "AND rc.column_id = fkc.referenced_column_id "
                                "WHERE OBJECT_SCHEMA_NAME(fk.parent_object_id) = ? "
                                "AND OBJECT_NAME(fk.parent_object_id) = ? "
                                "ORDER BY fkc.constraint_column_id",
                                (str(sch or "dbo"), str(tbl)))
                            for _r in (_cur.fetchall() or []):
                                out.append((str(_r[0]).upper(), str(_r[1]).upper(),
                                            str(_r[2]).upper(), str(_r[3]).upper()))
                            try:
                                _cur.close()
                            except Exception:
                                pass
                        finally:
                            try:
                                _cn.close()
                            except Exception:
                                pass
                        return out
                    except Exception:
                        try:
                            import traceback as _tb
                            _emit_staging_diag(
                                "RML _mssql_fk_keys diagnostic",
                                f"driver: {_drv!r}  sch/tbl: {sch}/{tbl}\n"
                                + _tb.format_exc())
                        except Exception:
                            pass
                        continue
        except Exception:
            pass
        return out

    @staticmethod
    def _key_rank(name: str) -> int:
        """Rank shared-column join-key candidates (document numbers first)."""
        n = str(name).upper()
        if n.endswith("_NO"):
            return 0
        if n.endswith("_ID"):
            return 1
        if n.endswith("_CODE"):
            return 2
        if n.endswith("_NUM"):
            return 3
        return 4

    @staticmethod
    def _dtype_cat(t: str) -> str:
        """Coarse type category so cross-DB comparisons work (NUMBER vs numeric)."""
        s = f" {str(t or '').upper()} "
        if any(k in s for k in ("DATE", "TIME")):
            return "DATE"
        if any(k in s for k in ("NUMBER", "INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "SERIAL", "MONEY")):
            return "NUM"
        return "TXT"

    # ── Hybrid SQL×API channel ──────────────────────────────────────
    # مصادر غير SQL (مثل أجهزة البصمة ZK — Connection.is_queryable=False)
    # لا تقبل SQL. عند التنفيذ تُجلب بياناتها عبر API الخاص بالمصدر وتُرحَّل
    # إلى جداول مؤقتة (TEMPORARY) على قاعدة SQL الأساسية، فتعمل عليها كل
    # آليات المحرك الحالية (JOIN/فلترة/فرز/ترقيم/تجميع) دون تغيير —
    # قناة تنفيذ واحدة وصيغة RML موحدة.

    API_SQL_FAMILY = ("postgres", "oracle", "mysql", "sqlserver", "sqlite")

    # TTL cache for API device fetches (seconds). A full device pull is slow
    # (tens of thousands of records over the ZK protocol), so raw rows are
    # cached per (connection, table); TEMP staging per request stays session-safe.
    # Prefer the biotime_sql connection (local SQL mirror) over live device pulls.
    def _api_static_columns():
        """{TABLE_UPPER: {COL_UPPER: dtype}} لمصادر API — بدون أي وصول للأجهزة."""
        try:
            from odex.engines.zk import ZK_TABLE_COLUMNS as _z
            _out = {}
            for _t, _cols in dict(_z or {}).items():
                _out[str(_t).upper()] = {str(_n).upper(): str(_ty).upper() for _n, _ty in (_cols or [])}
            if _out:
                return _out
        except Exception:
            pass
        return {
            "USERS": {"UID": "INTEGER", "USER_ID": "VARCHAR", "NAME": "VARCHAR",
                      "PRIVILEGE": "INTEGER", "CARD": "VARCHAR", "GROUP_ID": "VARCHAR"},
            "ATTENDANCE": {"UID": "INTEGER", "USER_ID": "VARCHAR", "TIMESTAMP": "TIMESTAMP",
                           "STATUS": "INTEGER", "PUNCH": "INTEGER"},
        }

    @staticmethod
    def _pg_col_type(rml_type) -> str:
        t = str(rml_type or "").upper().strip()
        if t in ("INTEGER", "INT", "SERIAL"):
            return "bigint"
        if t in ("NUMERIC", "DECIMAL", "FLOAT", "DOUBLE"):
            return "numeric"
        if t in ("TIMESTAMP", "DATETIME"):
            return "timestamp"
        if t == "DATE":
            return "date"
        if t == "TIME":
            return "time"
        if t == "BOOLEAN":
            return "boolean"
        return "text"

    # ---- Local PG staging (routing + lazy accessor) -----------------
    def _resolve_local_pg_row(self):
        """Removed with staging — always None (no local TEMP target exists)."""
        return None

    def _local_pg_engine(self):
        """Staging is removed — this accessor always fails loudly."""
        raise ValueError(
            "الترحيل المؤقت (staging) معطّل: لا تُنسخ الجداول إلى جداول مؤقتة — "
            "كل مصدر SQL يُقرأ مباشرة ويُدمج في Python. المصادر غير القابلة "
            "للاستعلام (أجهزة البصمة) لا يمكن تشغيلها في SQL.")

    @staticmethod
    def _stage_engine_of(db) -> str:
        """'oracle' | 'postgres' | 'mssql' — dialect tag of a live DB wrapper."""
        try:
            _nm = (type(db).__name__ or "").lower()
            _mod = (type(db).__module__ or "").lower()
            if "oracle" in _nm or "oracle" in _mod:
                return "oracle"
            if "sqlserver" in _nm or "sqlserver" in _mod or "mssql" in _nm or "mssql" in _mod:
                return "mssql"
        except Exception:
            pass
        return "postgres"

    def _api_table_info(self, table_norm):
        """None أو {gid, engine, row} عندما يكون الجدول على مصدر API (غير SQL)."""
        gid = self._conn_key_of_table(table_norm)
        if not gid:
            return None
        if not self._is_api_gid(gid):
            return None
        try:
            row = self._dj_conn(gid)
        except Exception:
            row = None
        if row is None:
            return None
        eng = str(getattr(row, "engine", "") or "").lower()
        return {"gid": gid, "engine": eng, "row": row}

    def _api_involved_tables(self):
        """جداول التقرير المرتبطة بمصادر API — بدون أي وصول للأجهزة (آمن للpreview)."""
        out = {}
        seen = []
        for f in (getattr(self, "fields", []) or []):
            ts = getattr(f, "table_source", None)
            if not ts:
                continue
            t = self._norm_table(ts)
            if t and t not in seen:
                seen.append(t)
        try:
            _det = self.compiler.detail() if hasattr(self.compiler, "detail") else None
            _det = _det.to_dict() if _det is not None and not isinstance(_det, dict) else _det
            if _det and _det.get("table"):
                t = self._norm_table(_det.get("table"))
                if t and t not in seen:
                    seen.append(t)
        except Exception:
            pass
        for t in seen:
            try:
                info = self._api_table_info(t)
            except Exception:
                info = None
            if info:
                out[t] = info
        return out

    def _direct_sql_gid(self, gid) -> bool:
        """True when a global connection id is directly SQL-readable live.

        Queryable SQL Server reads via SqlServerDirect (transpiled) and is
        merged in Python — never staged. Only genuinely non-queryable
        sources (ZK devices, is_queryable=False, unknown engines) are NOT
        direct.
        """
        try:
            if not gid:
                return False
            row = self._dj_conn(gid)
            if row is None:
                return False
            try:
                _fl = getattr(row, "is_queryable", True)
                flag = False if _fl is False or str(_fl).strip().lower() in ("0", "false", "no", "none") else True
            except Exception:
                flag = True
            if not flag:
                return False
            eng = str(getattr(row, "engine", "") or "").lower()
            return eng in (tuple(self.API_SQL_FAMILY or ()))
        except Exception:
            return False

    def _is_api_gid(self, gid) -> bool:
        """True when a global connection id is an API source (not SQL-queryable).

        Queryable sqlserver is NOT api: it is read live and merged in Python,
        even across different connections — no staging copies.
        """
        if not gid:
            return False
        try:
            row = self._dj_conn(gid)
        except Exception:
            return False
        if row is None:
            return False
        try:
            return not self._direct_sql_gid(gid)
        except Exception:
            return False

    def _instance_db_key(self, gid):
        """Physical-DB identity for union partitioning — never connects."""
        try:
            _g = str(gid).strip() if gid is not None and str(gid).strip() != "" else ""
        except Exception:
            _g = ""
        try:
            if _g:
                for _c in (getattr(self, "connections", []) or []):
                    if str(getattr(_c, "id", "") or "") == _g:
                        _gg = str(getattr(_c, "connection_id", "") or "").strip()
                        if _gg:
                            _g = _gg
                        break
            _db0 = getattr(self, "db", None)
            if not _g or _g == str(getattr(self, "primary_conn", None) or ""):
                return ("selfdb",) if _db0 is None else self._db_identity(_db0)
            _dbs = getattr(self, "databases", None) or {}
            if _g in _dbs:
                return self._db_identity(_dbs[_g])
            return ("ext", _g)
        except Exception:
            return ("ext", str(gid))

    def _union_partitions(self):
        """{ORIG_NORM: [[(field, gid), ...], ...]} for tables spanning >1 physical DB.

        Cached, Django-only lookups (no device/DB I/O) — safe for preview.
        """
        if getattr(self, "_api_union_map", None) is not None:
            return self._api_union_map
        _map = {}
        try:
            _by_norm = {}
            for _f in (getattr(self, "fields", []) or []):
                _ts = getattr(_f, "table_source", None)
                if not _ts:
                    continue
                _t = self._norm_table(_ts)
                if not _t:
                    continue
                try:
                    _gid = self._global_conn_id(getattr(_f, "connection_id", None) or getattr(_f, "conn_id", None))
                except Exception:
                    _gid = None
                _by_norm.setdefault(_t, []).append((_f, _gid))
            for _t, _items in _by_norm.items():
                _parts = {}
                for _f, _gid in _items:
                    try:
                        _k = self._instance_db_key(_gid)
                    except Exception:
                        _k = ("ext", str(_gid))
                    _parts.setdefault(_k, []).append((_f, _gid))
                if len(_parts) > 1:
                    _map[_t] = list(_parts.values())
        except Exception:
            pass
        self._api_union_map = _map
        return _map

    def _validate_no_exact_dupes(self):
        """Reject same field + same table + same connection imported twice.

        Cross-connection duplicates are legal (UNION); same-connection
        duplicates of a whole table are meaningless — the designer blocks
        them, and the engine refuses them loudly here as well.
        """
        try:
            _flds = getattr(self, "fields", []) or []
        except Exception:
            return
        _seen = {}
        for _f in _flds:
            _nm = str(getattr(_f, "name", "") or "").strip().lower()
            if not _nm:
                continue
            try:
                _tb = self._norm_table(getattr(_f, "table_source", None) or "")
            except Exception:
                _tb = ""
            _cx = str(getattr(_f, "connection_id", None) or getattr(_f, "conn_id", None) or "").strip()
            _k = (_nm, _tb, _cx)
            if _k in _seen:
                raise ValueError(
                    f"الحقل '{getattr(_f, 'name', '')}' مكرر في الجدول "
                    f"'{getattr(_f, 'table_source', '') or ''}' على نفس الاتصال "
                    f"({_cx or '—'}) — تكرار نفس الجدول بنفس الاتصال غير مسموح.")
            _seen[_k] = True

    def _staged_temp_of(self, norm):
        """No staging exists — always None (sources keep their own names)."""
        return None
        return None

    def _render_api_warnings(self):
        try:
            _w = getattr(self, "_api_warnings", None) or []
            _out = []
            for _e in _w:
                if not isinstance(_e, dict):
                    continue
                _m = str(_e.get("message") or "")
                _n = int(_e.get("skipped") or 0)
                if _m:
                    _out.append(_m + (f" — عدد القيم: {_n}" if _n > 1 else ""))
            return _out
        except Exception:
            return []

    @staticmethod
    def _sqlserver_targets(row) -> list:
        """Candidate (server, dbname) routes: explicit custom port first, then instance."""
        host = str(getattr(row, "host", "") or "").strip()
        inst = str(getattr(row, "instance_name", "") or "").strip()
        if "\\" in inst:
            _srv, _, _in = inst.rpartition("\\")
            _in = _in.strip()
            if not host and _srv.strip():
                host = _srv.strip()
            inst = _in
        try:
            port = int(getattr(row, "port", 0) or 1433)
        except (TypeError, ValueError):
            port = 1433
        out = []
        if inst and port != 1433:
            out.append((f"{host},{port}", None))
        if inst:
            out.append((f"{host}\\{inst}", None))
        if not out:
            out.append((f"{host},{port or 1433}", None))
        dbn = str(getattr(row, "instance", "") or "master").strip() or "master"
        return [(s, dbn) for s, _ in out]

    @staticmethod
    def _sqlserver_drivers() -> list:
        return [("ODBC Driver 18 for SQL Server", True),
                ("ODBC Driver 17 for SQL Server", True),
                ("SQL Server", False)]

    STAGE_MAX_ROWS = 500000

    def _split_and_top(text):
        """Split on top-level AND (quote/paren aware) for static filter pushdown."""
        parts, depth, q, cur = [], 0, None, []
        i, n = 0, len(text or "")
        while i < n:
            ch = text[i]
            if q:
                cur.append(ch)
                if ch == q:
                    if q == "'" and i + 1 < n and text[i + 1] == "'":
                        cur.append(text[i + 1])
                        i += 1
                    else:
                        q = None
                i += 1
                continue
            if ch in ("'", '"'):
                q = ch
                cur.append(ch)
                i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            if depth == 0 and (text[i:i + 3].upper() == "AND" and
                               (i == 0 or not text[i - 1].isalnum() and text[i - 1] != "_") and
                               (i + 3 >= n or not text[i + 3].isalnum() and text[i + 3] != "_")):
                parts.append("".join(cur))
                cur = []
                i += 3
                continue
            cur.append(ch)
            i += 1
        parts.append("".join(cur))
        return [p.strip() for p in parts if p.strip()]

    def _fetch_sql_partition(self, table_norm, gid, fields, report_schema):
        """Fetch ALL rows of a SQL table partition (chunked SELECT, no staging)."""
        _db = self._db_for_conn(gid)
        try:
            _ssch = self._schema_for_table(table_norm, gid, report_schema)
        except Exception:
            _ssch = report_schema
        try:
            _disp = None
            for _f in (getattr(self, "fields", []) or []):
                if self._norm_table(getattr(_f, "table_source", None) or "") == table_norm:
                    _d = str(getattr(_f, "table_source")).strip().strip('"')
                    _disp = _d.split(".")[-1] if "." in _d else _d
                    break
            _tname = _disp or table_norm.lower()
        except Exception:
            _tname = table_norm.lower()
        _from = f"{_q(_ssch)}.{_q(_tname)}" if _ssch else _q(_tname)
        _sel = ", ".join(_q(str(getattr(_f, "name", ""))) for _f in fields)
        _rows = []
        _page = 1
        while True:
            _pag, _prm = self._paginate_clause(_page, 5000, {}, _db)
            try:
                _cur = self._exec_on(_db, f"SELECT {_sel} FROM {_from}{_pag}", _prm)
            except Exception as _e:
                raise ValueError(
                    f"تعذر قراءة الجدول '{_tname}' من الاتصال ({gid or 'الأساسي'}): {_e}")
            try:
                _desc = [d[0] for d in (_cur.description or [])] if _cur.description else []
                _low = [str(_c).lower() for _c in _desc]
                _batch = _cur.fetchall() or []
                for _r in _batch:
                    if isinstance(_r, dict):
                        _rows.append({str(_k): _v for _k, _v in _r.items()})
                    else:
                        _rows.append(dict(zip(_low, list(_r))))
                if len(_batch) < 5000:
                    break
            finally:
                try:
                    _cur.close()
                except Exception:
                    pass
            _page += 1
        return _rows

        try:
            _cb = getattr(self, "_progress_cb", None)
            if callable(_cb):
                _cb(info)
        except Exception:
            pass

    def _report_progress(self, info):
        try:
            _cb = getattr(self, "_progress_cb", None)
            if callable(_cb):
                _cb(info)
        except Exception:
            pass

    def _reject_direct_sql_unions(self, _umap) -> None:
        """Fail loudly when one table spans 2+ directly-readable SQL DBs.

        Staging is disabled for readable SQL sources and there is no
        cross-DB UNION machinery — copying rows or silently misrouting would
        be worse than an explicit error.
        """
        try:
            for _norm, _parts in ((_umap or {}).items()):
                if len(_parts or []) < 2:
                    continue
                _readable = True
                for _part in (_parts or []):
                    for _, _g in (_part or []):
                        if not _g:
                            continue
                        try:
                            if not self._direct_sql_gid(_g):
                                _readable = False
                                break
                        except Exception:
                            _readable = False
                            break
                    if not _readable:
                        break
                if _readable:
                    raise ValueError(
                        f"الجدول '{str(_norm).lower()}' موزع على اتصالين مختلفين — "
                        f"الترحيل المؤقت معطّل: وحّد الجدول على اتصال واحد أو افصل التقارير.")
        except ValueError:
            raise
        except Exception:
            pass

    # ── Instant IoT mirrors (no periodic sync) ──────────────────────────
    # When a report table lives on an IoT connection whose driver exists in
    # odex/engines (e.g. zk), the device data is pulled LIVE during this
    # request into the default local mirror table (iot_<engine>_<endpoint>)
    # and the query runs against that mirror — no 15-minute sync script.
    _IOT_AT_ENDPOINTS = ("att",)

    @staticmethod
    def _iot_driver(engine):
        """Driver module for an IoT engine name, or None (unsupported)."""
        try:
            eng = str(engine or "").strip().lower()
        except Exception:
            return None
        if not eng:
            return None
        try:
            import importlib as _il
            _mod = _il.import_module("odex.engines." + eng)
            if getattr(_mod, "ZKEngine", None) is not None or getattr(_mod, "Engine", None) is not None:
                return _mod
        except Exception:
            pass
        return None

    def _live_engine_for_gid(self, gid):
        """Live engine for a global connection id outside `databases`.

        Builds the connection's OWN engine (postgres via the RML pipeline
        class, sqlserver direct) and caches it in `databases` — never a
        shared staging copy.
        """
        try:
            if not gid:
                return None
            if gid in (self.databases or {}):
                return self.databases[gid]
            row = self._dj_conn(gid)
            if row is None:
                return None
            eng = str(getattr(row, "engine", "") or "").lower()
            if eng == "sqlserver":
                try:
                    w = self._mssql_db_for(gid)
                    try:
                        (self.databases or {})[gid] = w
                    except Exception:
                        pass
                    return w
                except Exception:
                    return None
            if eng in ("postgres", "postgresql"):
                try:
                    from urs.views import PostgresEngine as _VPG
                except Exception:
                    return None
                try:
                    _port = int(getattr(row, "port", 0) or 5432)
                except (TypeError, ValueError):
                    _port = 5432
                try:
                    w = _VPG(host=str(getattr(row, "host", "") or ""),
                             dbname=str(getattr(row, "instance", "") or "urs"),
                             user=str(getattr(row, "user", "") or ""),
                             password=str(getattr(row, "password", "") or ""),
                             port=_port)
                    try:
                        (self.databases or {})[gid] = w
                    except Exception:
                        pass
                    return w
                except Exception:
                    return None
        except Exception:
            pass
        return None

    def _iot_tables(self):
        """{NORM: gid} for report tables on IoT connections (any flag)."""
        out = {}
        try:
            for f in (getattr(self, "fields", []) or []):
                try:
                    ts = getattr(f, "table_source", None)
                    if not ts:
                        continue
                    t = self._norm_table(ts)
                    if not t or t in out:
                        continue
                    gid = self._global_conn_id(getattr(f, "connection_id", None)
                                               or getattr(f, "conn_id", None))
                    if not gid:
                        continue
                    try:
                        row = self._dj_conn(gid)
                    except Exception:
                        row = None
                    if row is None:
                        continue
                    eng = str(getattr(row, "engine", "") or "").lower()
                    ctype = str(getattr(row, "conn_type", "") or "").lower()
                    if ctype == "iot" or eng in ("zk",):
                        if self._iot_driver(eng) is not None or ctype == "iot":
                            out[t] = str(gid)
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _iot_register(self, table_norm, gid, pull: bool):
        """Ensure the default local mirror for an IoT table; optionally pull.

        - Finds (or provisions) the IoTMirror row: default local connection
          = first is_local postgres Connection; auto_sync=False so the
          15-minute sweeper never touches instant mirrors.
        - pull=True: full device pull + upsert NOW (blocking, in-request).
        - Registers self._iot_mirror[NORM] = {gid, schema, table} for routing.
        """
        try:
            from urs.models import Connection as _C, IoTMirror as _M
            from urs.iot_sync import (mirror_table_name, mirror_schema,
                                      ensure_mirror_table, local_pg, fetch_union,
                                      _norm_att_row, _MIRROR_UPSERT, _q as _iq)
        except Exception as _imp:
            raise ValueError(
                "تهيئة مرآة IoT تتطلب بيئة Django الكاملة "
                f"(تعذر الاستيراد: {_imp}).")
        norm = self._norm_table(table_norm)
        row = self._dj_conn(gid)
        if row is None:
            raise ValueError(f"الاتصال ({gid}) غير موجود — لا يمكن بناء مرآة IoT.")
        eng = str(getattr(row, "engine", "") or "").lower()
        if self._iot_driver(eng) is None:
            raise ValueError(
                f"المحرك '{eng or '?'}' غير مدعوم لحظياً — لا يوجد محرك له في odex/engines.")
        ep = norm.lower()
        if ep not in self._IOT_AT_ENDPOINTS:
            try:
                ep = str(getattr(row, "endpoint", "") or "att").strip().lower() or "att"
            except Exception:
                ep = "att"
        if ep not in self._IOT_AT_ENDPOINTS:
            raise ValueError(
                f"نقطة البيانات '{ep}' للجدول '{norm.lower()}' غير مدعومة لحظياً — "
                "المدعوم: att.")
        table = mirror_table_name(eng, ep)
        m = _M.objects.select_related("local_connection").filter(
            connection_id=getattr(row, "id", None), endpoint=ep).first()
        if m is None:
            local = _C.objects.filter(is_local=True, engine="postgres").order_by("id").first()
            if local is None:
                raise ValueError(
                    "لا يوجد اتصال محلي (is_local + postgres) لاستضافة مرآة IoT — "
                    "أنشئ اتصالاً محلياً أو صف IoTMirror يدوياً.")
            m = _M.objects.create(
                connection_id=getattr(row, "id", None), endpoint=ep,
                local_connection=local, table_name=table,
                auto_sync=False, interval_min=15, clear_device=False,
                status="idle")
        else:
            table = m.table_name or table
            local = m.local_connection
        if local is None or str(getattr(local, "engine", "") or "").lower() != "postgres":
            raise ValueError("الاتصال المحلي للمرآة يجب أن يكون postgres.")
        schema = (mirror_schema(local) or "").strip() or "public"
        if pull:
            self._report_progress({"stage": "iot", "table": norm.lower(),
                                   "text": f"سحب لحظي من أجهزة {norm.lower()}…"})
            data = fetch_union(row, ep)
            normed = []
            for r in (data.get("rows") or []):
                try:
                    n = _norm_att_row(r if isinstance(r, dict) else {},
                                      str(r.get("device_ip", "") or ""))
                except Exception:
                    n = None
                if n:
                    normed.append(n)
            pg = local_pg(local)
            try:
                ensure_mirror_table(pg, schema, table)
                if normed:
                    cur = pg.cursor()
                    try:
                        sql_up = _MIRROR_UPSERT.format(schema=_iq(schema), table=_iq(table))
                        for i in range(0, len(normed), 1000):
                            cur.executemany(sql_up, normed[i:i + 1000])
                        pg.commit()
                    finally:
                        cur.close()
            finally:
                try:
                    pg.close()
                except Exception:
                    pass
            try:
                import datetime as _dt
                _M.objects.filter(id=m.id).update(
                    status="done", progress_pct=100, rows_pulled=len(normed),
                    last_sync_at=_dt.datetime.now(_dt.timezone.utc), last_error="")
            except Exception:
                pass
            self._report_progress({"stage": "iot", "table": norm.lower(),
                                   "text": f"المرآة {table}: {len(normed)} صف لحظياً.",
                                   "rows": len(normed)})
        try:
            _map = getattr(self, "_iot_mirror", None) or {}
            _map[norm] = {"gid": str(getattr(local, "id", "")),
                          "schema": schema, "table": table}
            self._iot_mirror = _map
        except Exception:
            pass
        return str(getattr(local, "id", ""))

    def _ensure_api_staged(self, refresh=False):
        """No TEMP staging. Two validations + instant IoT mirrors.

        - Queryable SQL sources are read live and merged in Python.
        - One table on 2+ readable SQL sources -> loud error (no UNION copy).
        - IoT tables with a supported odex/engines driver -> instant pull
          into the default local mirror (this request, no sync script).
        - Other non-readable sources -> loud error.
        """
        try:
            _umap = self._union_partitions()
        except Exception:
            _umap = {}
        self._reject_direct_sql_unions(_umap)
        try:
            _iot = self._iot_tables()
        except Exception:
            _iot = {}
        try:
            _api_tables = self._api_involved_tables()
        except Exception:
            _api_tables = {}
        _api_tables = {t: i for t, i in (_api_tables or {}).items() if t not in (_umap or {})}
        _rest = {t: i for t, i in _api_tables.items() if t not in (_iot or {})}
        if _rest or _umap:
            _names = sorted(set(list(_rest.keys()) + list((_umap or {}).keys())))
            raise ValueError(
                "الجداول التالية على مصادر غير قابلة للاستعلام SQL "
                f"({', '.join(str(_n).lower() for _n in _names)}) ولا يوجد لها محرك IoT — "
                "وجّه التقرير لاتصال قاعدة بيانات قابل للاستعلام.")
        for _t, _g in (_iot or {}).items():
            self._iot_register(_t, _g, pull=True)
        self._api_staged_done = True
        self._api_stage = {}
        self._api_union = {}
        self._api_warnings = []

    def _uniq_ratio(self, db, table_norm: str, schema: Optional[str], col: str) -> Optional[float]:
        """Distinct ratio of a column (0..1) for join-key ranking; None on failure."""
        try:
            sch_q = _q(schema) if schema else ""
            tq = f"{sch_q + '.' if sch_q else ''}{_q(self._orig_col(table_norm, col, db, schema))}"
            cur = self._exec_on(db, f"SELECT COUNT(*), COUNT(DISTINCT {_q(self._orig_col(table_norm, col, db, schema))}) FROM {tq}", {})
            try:
                row = cur.fetchone()
                if not row or not row[0]:
                    return 0.0
                return float(row[1] or 0) / float(row[0])
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
        except Exception:
            return None

    def _join_col_cats(self, base_norm, bcol, sec_norm, scol):
        """(base_cat, sec_cat) coarse type categories of two join columns (for CAST)."""
        bcat = scat = ""
        try:
            for f in (getattr(self, "fields", []) or []):
                _fn = str(getattr(f, "name", "") or "")
                _ts = self._norm_table(getattr(f, "table_source", None) or "")
                if _ts == base_norm and _fn.upper() == str(bcol).upper():
                    bcat = self._dtype_cat(getattr(f, "data_type", None))
                elif _ts == sec_norm and _fn.upper() == str(scol).upper():
                    scat = self._dtype_cat(getattr(f, "data_type", None))
                if bcat and scat:
                    break
        except Exception:
            pass
        return bcat, scat

    def _infer_join_key(self, base_norm: str, sec_norm: str, schema: Optional[str], db=None,
                        sec_schema: Optional[str] = None, sec_db=None) -> Optional[Tuple[str, str]]:
        """Infer (base_col, sec_col) join key: diagram links, FK metadata, shared names."""
        # 1) Explicit diagram links (<links>) win — incl. different column names
        try:
            for l in (getattr(self, "report_links", []) or []):
                ft = self._norm_table(l.get("from_table") or "")
                tt = self._norm_table(l.get("to_table") or "")
                fc = (l.get("from_col") or "").strip()
                tc = (l.get("to_col") or "").strip()
                if ft == base_norm and tt == sec_norm and fc and tc:
                    return (fc.upper(), tc.upper())
                if ft == sec_norm and tt == base_norm and fc and tc:
                    return (tc.upper(), fc.upper())
        except Exception:
            pass
        sec_schema = sec_schema if sec_schema is not None else schema
        sec_db = sec_db if sec_db is not None else db
        # 1b) Source-DB constraints for staged SQL Server tables (staged copies
        # carry no FKs) — both sides must come from the SAME connection.
        try:
            _ms = self._sqlserver_source_of(sec_norm)
            _mb = self._sqlserver_source_of(base_norm)
            if _ms is not None and _mb is not None and _ms[1] and _ms[1] == _mb[1]:
                for (_srow, _sgid, _ssch, _stbl, _flip) in (
                        (_ms[0], _ms[1], _ms[2], _ms[3], False),
                        (_mb[0], _mb[1], _mb[2], _mb[3], True)):
                    _other = _mb if not _flip else _ms
                    for (_c, _rs, _rt, _rc) in self._mssql_fk_keys(_srow, _ssch, _stbl):
                        if _rt == str(_other[3]).upper():
                            # _flip=False: FK lives on sec side → (base=ref, sec=col)
                            # _flip=True:  FK lives on base side → (base=col, sec=ref)
                            return ((_c, _rc) if _flip else (_rc, _c))
        except Exception:
            pass
        fks = self._fk_keys(sec_norm, base_norm, sec_schema, sec_db)
        if fks:
            return (fks[0][1], fks[0][0])
        fks_rev = self._fk_keys(base_norm, sec_norm, schema, db)
        if fks_rev:
            return (fks_rev[0][0], fks_rev[0][1])
        base_cols = self._table_columns(base_norm, schema, db)
        sec_cols = self._table_columns(sec_norm, sec_schema, sec_db)
        if not base_cols or not sec_cols:
            return None
        shared = [c for c in base_cols if c in sec_cols
                  and self._dtype_cat(base_cols[c]) == self._dtype_cat(sec_cols[c])]
        # Never join on dates/audit columns — too dangerous
        shared = [c for c in shared if self._dtype_cat(base_cols[c]) != "DATE"]
        if not shared:
            return None
        # Rank: data uniqueness first (min of both sides), then name heuristics.
        # A true link key is near-unique on at least one side (e.g. BILL_NO).
        # Probe name-plausible candidates in ONE scan per table.
        ranked = sorted(shared, key=lambda c: (self._key_rank(c), c))
        cands = [c for c in ranked if self._key_rank(c) <= 2][:16] or ranked[:4]
        if not cands:
            return None

        def _ratios(dbo, tnorm, sch):
            out = {}
            try:
                sch_q = _q(sch) if sch else ""
                tname = self._orig_col(tnorm, tnorm, dbo, sch)
                # table orig: fields first
                for f in (getattr(self, "fields", []) or []):
                    if self._norm_table(getattr(f, "table_source", "") or "") == tnorm:
                        d = str(getattr(f, "table_source")).strip().strip('"')
                        tname = d.split(".")[-1] if "." in d else d
                        break
                tq = f"{sch_q + '.' if sch_q else ''}{_q(tname)}"
                parts = ", ".join([f"COUNT(DISTINCT {self._orig_col(tnorm, c, dbo, sch) and _q(self._orig_col(tnorm, c, dbo, sch))})" for c in cands])
                cur = self._exec_on(dbo, f"SELECT COUNT(*), {parts} FROM {tq}", {})
                try:
                    row = cur.fetchone() or []
                    total = float(row[0] or 0)
                    for i, c in enumerate(cands):
                        d = float((row[i + 1] if len(row) > i + 1 else 0) or 0)
                        out[c] = (d / total) if total else 0.0
                finally:
                    try:
                        cur.close()
                    except Exception:
                        pass
            except Exception:
                pass
            return out

        rb = _ratios(db, base_norm, schema)
        rs = _ratios(sec_db, sec_norm, sec_schema)
        scored = []
        for c in cands:
            if c in rb or c in rs:
                score = min(rb.get(c, 0), rs.get(c, 0))
            else:
                score = -1  # unknown — keep as last resort
            scored.append((score, c))
        scored.sort(key=lambda x: (-x[0], self._key_rank(x[1]), x[1]))
        return (scored[0][1], scored[0][1])

    @staticmethod
    def _trailing_match(a: str, b: str) -> int:
        """Length of longest common trailing underscore-part run (UPPER)."""
        pa = str(a).upper().split("_")
        pb = str(b).upper().split("_")
        n = 0
        while n < len(pa) and n < len(pb) and pa[-(n + 1)] == pb[-(n + 1)]:
            n += 1
        return n

    def _match_date_col(self, base_field: str, sec_norm: str, schema: Optional[str], db=None) -> Optional[str]:
        """Find the DATE column of sec table matching a base field name."""
        sec_cols = self._table_columns(sec_norm, schema, db)
        cands = [(c, self._trailing_match(c, base_field)) for c, t in sec_cols.items() if "DATE" in t]
        cands = [(c, s) for c, s in cands if s >= 2]
        if not cands:
            return None
        cands.sort(key=lambda x: (-x[1], x[0]))
        return cands[0][0]

    def _split_agg_call(self, text: str, start: int) -> Optional[Tuple[str, str, int]]:
        """Parse FUNC(...) at index start (after func name). Returns (inner, rest, end)."""
        i = text.find("(", start)
        if i < 0:
            return None
        depth = 0
        in_str = False
        for j in range(i, len(text)):
            ch = text[j]
            if ch == "'" and (j == 0 or text[j - 1] != "'"):
                in_str = not in_str
            if in_str:
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return (text[i + 1:j], text[j + 1:], j + 1)
        return None

    def _rewrite_cross_agg(self, raw: str, field_table: Dict[str, str], base_norm: str,
                           sec_display: Dict[str, str], sec_alias: Dict[str, str],
                           schema_norm: Optional[str], schema_q: Optional[str],
                           base_alias: str, db=None, sec_schemas: Optional[Dict[str, str]] = None,
                           sec_displays: Optional[Dict[str, str]] = None) -> Tuple[str, set]:
        """Rewrite aggregates over secondary tables as correlated subqueries.

        `SUM([S_AMT]) OVER (PARTITION BY [B_DATE])` ->
        `(SELECT SUM("S_AMT") FROM sch.S WHERE S."S_D" = "T0"."B_DATE")`
        Returns (new_raw, still_needed_join_tables). Bare (non-aggregated)
        secondary refs are left for LEFT JOIN.
        """
        needed: set = set()
        out = raw
        sub_spans: list = []  # inserted subquery ranges (self-contained; skip in JOIN scan)
        # JOIN fallback: secondary tables in the original expr (used if rewrite gives up)
        fallback = {field_table[r] for r in self._refs_in_text(raw, field_table)
                    if field_table.get(r) not in (None, base_norm)}
        pat = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", re.IGNORECASE)
        pos = 0
        while True:
            m = pat.search(out, pos)
            if not m:
                break
            func = m.group(1).upper()
            parsed = self._split_agg_call(out, m.start())
            if not parsed:
                needed |= fallback
                break
            inner, rest, end = parsed
            # Optional OVER (PARTITION BY ...) — find its matching close paren
            part_fields: List[str] = []
            over_end = end
            rm = re.match(r"\s*OVER\s*\(", rest, re.IGNORECASE)
            if rm:
                oparen = end + rest.find("(")
                depth = 1
                k = oparen + 1
                in_str = False
                while k < len(out) and depth > 0:
                    ch = out[k]
                    if ch == "'":
                        in_str = not in_str
                    elif not in_str:
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                    k += 1
                if depth != 0:
                    needed |= fallback
                    break
                over_clause = out[oparen + 1:k - 1]
                pm = re.match(r"\s*PARTITION\s+BY\s+(.*)$", over_clause, re.IGNORECASE | re.DOTALL)
                if not pm:
                    needed |= fallback
                    break  # OVER without PARTITION BY — give up on this column
                part_fields = sorted(self._refs_in_text(pm.group(1), field_table))
                over_end = k
            inner_refs = sorted(self._refs_in_text(inner, field_table))
            if func == "COUNT" and inner.strip() == "*":
                inner_tables: set = set()
            else:
                inner_tables = {field_table[r.lower()] for r in inner_refs if r.lower() in field_table}
            # Only rewrite single-secondary-table aggregates
            if len(inner_tables) != 1:
                if not inner_tables:
                    pos = over_end  # base-only call — skip past it and keep scanning
                    continue
                # Mixed tables inside one aggregate: fall back to JOIN, keep original expr
                out = raw
                for r in self._refs_in_text(raw, field_table):
                    t = field_table.get(r)
                    if t and t != base_norm:
                        needed.add(t)
                return out, needed
            sec_norm = next(iter(inner_tables))
            if sec_norm == base_norm:
                pos = over_end  # base-only call — skip past it and keep scanning
                continue
            # Partition anchors must ALL be known base fields
            if not part_fields:
                needed |= fallback
                break  # no grain anchor — JOIN fallback, DB decides
            anchors_ok = all(field_table.get(p.lower()) == base_norm for p in part_fields)
            if not anchors_ok:
                needed |= fallback
                break
            # Map each anchor base field -> sec DATE column
            where_parts = []
            ok = True
            for a in part_fields:
                ds = self._match_date_col(a, sec_norm, (sec_schemas or {}).get(sec_norm, schema_norm), db)
                if not ds:
                    ok = False
                    break
                where_parts.append(f'{_q(sec_alias[sec_norm])}.{_q(ds)} = {_q(base_alias)}.{_q(a.upper())}')
            if not ok:
                needed |= fallback
                break
            # Inner refs stay as-is: the global table_map pass qualifies them to
            # the subquery's own alias (bound in FROM below).
            # NVL for SUM/AVG/COUNT so empty sets yield 0 (keeps arithmetic valid)
            _agg = f"{func}({inner})"
            if func in ("SUM", "AVG", "COUNT"):
                _agg = f"NVL({_agg}, 0)"
            sec_t = (sec_displays or sec_display).get(sec_norm, sec_norm)
            sec_sch = (sec_schemas or {}).get(sec_norm, schema_norm)
            sec_sch_q = _q(sec_sch) if sec_sch else ""
            sub = (f"(SELECT {_agg} FROM {sec_sch_q + '.' if sec_sch_q else ''}{_q(sec_t)} "
                   f"{_q(sec_alias[sec_norm])} WHERE {' AND '.join(where_parts)})")
            out = out[:m.start()] + sub + out[over_end:]
            sub_spans.append((m.start(), m.start() + len(sub)))
            pos = m.start() + len(sub)  # continue after the inserted subquery
        # Remaining secondary refs (bare, outside rewritten subqueries) still need JOIN
        masked = out
        for a, b in sorted(sub_spans, reverse=True):
            masked = masked[:a] + (" " * (b - a)) + masked[b:]
        for r in self._refs_in_text(masked, field_table):
            t = field_table.get(r)
            if t and t != base_norm:
                needed.add(t)
        return out, needed

    def _used_tables(self, columns, filters, sort, group_by, field_table, general_where="") -> set:
        """Tables referenced by columns/filters/sort/group/general-where.

        general_where may reference internal fields (no column binds them),
        so its tables must be routed/JOINed too — otherwise the reference
        dangles at runtime.
        """
        texts: List[str] = []
        for c in (columns or []):
            texts.append(getattr(c, "expr", "") or "")
            texts.append(getattr(c, "where_clause", "") or "")
        if general_where:
            texts.append(str(general_where))
        extra_fields: List[str] = []
        for f in (filters or []):
            extra_fields.append(str(f.get("field") or f.get("column") or f.get("name") or ""))
        ss = sort if isinstance(sort, list) else ([sort] if sort else [])
        for s in ss:
            if isinstance(s, dict):
                extra_fields.append(str(s.get("column") or s.get("field") or s.get("name") or ""))
        if group_by:
            extra_fields.append(str(group_by))
        for fld in extra_fields:
            texts.append(fld)
            try:
                col = _find_column_for_field(fld, columns)
            except Exception:
                col = None
            if col is not None:
                texts.append(getattr(col, "expr", "") or "")
        used: set = set()
        for t in texts:
            for r in self._refs_in_text(t, field_table):
                tb = field_table.get(r)
                if tb:
                    used.add(tb)
            key = str(t).strip().lower()
            if key in field_table:
                used.add(field_table[key])
        return used

    def _plan_cache_key(self, active_table, filters, sort, group_by) -> str:
        """Structural cache key (fields/ops only — never values)."""
        import json as _json

        def _shape(f):
            if isinstance(f, dict) and isinstance(f.get("any"), (list, tuple)):
                return {"any": [_shape(x) for x in f["any"] if isinstance(x, dict)]}
            if isinstance(f, dict):
                return [f.get("field"), f.get("column"), f.get("name"), f.get("op")]
            return None

        try:
            return _json.dumps([active_table, [_shape(f) for f in (filters or [])], sort, group_by],
                               sort_keys=True, default=str)
        except Exception:
            return str(id(filters)) + str(active_table)

    def _plan_structure(self, active_table, filters, sort, group_by) -> Dict[str, Any]:
        """Full routing plan (cached structurally).

        Same-DB secondaries -> SQL JOINs/subqueries; other-DB secondaries ->
        Python merge specs. Raises clear Arabic errors for unsupported cases
        (unresolvable keys, remote sort/group, mixed cross-DB expressions).
        """
        import dataclasses
        if not hasattr(self, "_plan_cache") or self._plan_cache is None:
            self._plan_cache = {}
        key = self._plan_cache_key(active_table, filters, sort, group_by)
        if key in self._plan_cache:
            return self._plan_cache[key]
        plan = self._build_plan(active_table, filters, sort, group_by, dataclasses)
        self._plan_cache[key] = plan
        return plan

    def _same_db(self, a, b) -> bool:
        try:
            return self._db_identity(a) == self._db_identity(b)
        except Exception:
            return False

    def _build_plan(self, active_table, filters, sort, group_by, dataclasses) -> Dict[str, Any]:
        from_table = active_table or self._default_table
        if not from_table:
            raise ValueError('لم يتم تحديد الجدول. أضف table_source في الحقول أو حدد activeTable في الطلب.')
        report_schema = self.metadata.get("schema")
        base_norm = self._norm_table(from_table)
        base_conn = self._conn_key_of_table(base_norm)
        base_db = self._db_for_conn(base_conn)
        self._last_base_db = base_db
        base_schema = self._schema_for_table(base_norm, base_conn, report_schema)
        base_schema_norm = str(base_schema).strip().upper() if base_schema else None
        base_schema_q = _q(base_schema) if base_schema else ""
        fields = getattr(self, "fields", []) or []
        field_table = self._field_table_map()
        inlined = self._inline_column_refs(self.columns)
        try:
            _gw_text = self.compiler.general_where() if hasattr(self.compiler, "general_where") else ""
        except Exception:
            _gw_text = ""
        try:
            # @Alias first: routing/JOINs must see through to the real fields
            _gw_text = self._expand_at_aliases(_gw_text) if _gw_text else ""
        except ValueError:
            raise
        except Exception:
            pass
        used = self._used_tables(inlined, filters, sort, group_by, field_table, _gw_text or "")
        sec_all = sorted(t for t in used if t != base_norm)
        # Route secondaries: local (same physical DB) vs remote
        local_sec: List[str] = []
        remote_sec: Dict[str, Any] = {}
        for s in sec_all:
            _terr = self._transitive_link_error(base_norm, s, sec_all)
            if _terr:
                raise ValueError(_terr)
            sconn = self._conn_key_of_table(s)
            sdb = self._db_for_conn(sconn)
            _mspec = self._link_match_spec(base_norm, s)
            _sec_schema = self._schema_for_table(s, sconn, report_schema)
            if self._same_db(sdb, base_db) and not self._link_needs_python(_mspec["match"], base_db):
                local_sec.append(s)
            else:
                remote_sec[s] = {"conn": sconn, "db": sdb, "schema": _sec_schema, "table": s,
                                 "key": self._infer_join_key(base_norm, s, base_schema_norm, base_db, _sec_schema, sdb),
                                 "match": _mspec["match"], "pattern": _mspec["pattern"]}
                if not remote_sec[s]["key"]:
                    raise ValueError(
                        f'تعذر الاستدلال على مفتاح الربط بين "{from_table}" و"{s}" عبر الاتصالات. '
                        f'أضف عموداً مشتركاً (مثل رقم المستند) في الجدولين.')
        # general_where across DBs: remote conjuncts become two-phase
        # filter dicts (base IN lists); the rest stays in base SQL.
        try:
            _gw_base, _gw_remote, _gw_deferred = self._split_general_where(
                _gw_text or "", base_norm, local_sec, remote_sec, field_table)
        except ValueError:
            raise
        except Exception:
            _gw_base, _gw_remote, _gw_deferred = (_gw_text or ""), [], []
        # Sort / group-by must be base-local (cross-DB ordering impossible in SQL)
        def _field_tables_of(text):
            ts = set()
            for r in self._refs_in_text(text, field_table):
                ts.add(field_table[r])
            col = _find_column_for_field(text, inlined)
            if col is not None:
                for r in self._refs_in_text(getattr(col, "expr", "") or "", field_table):
                    ts.add(field_table[r])
            key = str(text or "").strip().lower()
            if key in field_table:
                ts.add(field_table[key])
            return ts

        def _check_routable(text, kind):
            for t in _field_tables_of(text):
                if t == base_norm:
                    continue
                if t in local_sec:
                    continue
                if t in remote_sec:
                    raise ValueError(
                        f'لا يمكن {kind} على عمود من اتصال آخر ("{text}") في SQL — '
                        f'رشّح/رتّب على أعمدة الاتصال الأساسي.')
                # unknown table (no fields?) -> leave for DB error
        ss = sort if isinstance(sort, list) else ([sort] if sort else [])
        for s in ss:
            if isinstance(s, dict):
                _check_routable(str(s.get("column") or s.get("field") or s.get("name") or ""), "الفرز")
        if group_by:
            _check_routable(str(group_by), "التجميع")
        # Base FROM (IoT mirrors emit their local schema.table)
        try:
            _btmp = self._staged_temp_of(base_norm)
        except Exception:
            _btmp = None
        try:
            _bmir = (getattr(self, "_iot_mirror", None) or {}).get(base_norm)
        except Exception:
            _bmir = None
        if _btmp:
            base_q = _q(_btmp)
        elif _bmir:
            base_q = f"{_q(str(_bmir.get('schema') or ''))}.{_q(str(_bmir.get('table') or base_norm))}"
        elif base_schema and "." not in from_table:
            base_q = f"{_q(base_schema)}.{_q(from_table)}"
        else:
            base_q = _q(from_table)
        if not sec_all:
            return {"from_table": from_table, "base_norm": base_norm, "base_conn": base_conn,
                    "base_db": base_db, "base_schema": base_schema,
                    "base_disp": (from_table if "." not in from_table else from_table.split(".")[-1]),
                    "from_q": base_q,
                    "columns": inlined, "table_map": None, "merges": [], "extra": [],
                    "strip": set(), "local_sec": [], "remote": {}, "base_alias": None,
                    "gw_base": _gw_text or "", "gw_remote": [], "gw_deferred": []}
        # Aliases (base always aliased when other tables involved)
        base_alias = "T0"
        aliases = {s: f"T{i + 1}" for i, s in enumerate(sorted(set(local_sec)))}
        disp: Dict[str, str] = {}
        for f in fields:
            ts = getattr(f, "table_source", None)
            if ts:
                d = str(ts).strip().strip('"')
                if "." in d:
                    d = d.split(".")[-1]
                disp.setdefault(self._norm_table(d), d)
        disp.setdefault(base_norm, from_table if "." not in from_table else from_table.split(".")[-1])
        for s in sorted(set(local_sec)):
            disp.setdefault(s, s)
        # Display names for remote tables too (original case from fields)
        for s in remote_sec:
            if s not in disp:
                for f in fields:
                    ts = getattr(f, "table_source", None)
                    if ts and self._norm_table(ts) == s:
                        d = str(ts).strip().strip('"')
                        if "." in d:
                            d = d.split(".")[-1]
                        disp[s] = d
                        break
                disp.setdefault(s, s)
        # Staged tables (API singles + UNIONs): SQL must reference the TEMP table
        # (same session, no schema). Display/alias maps stay intact; only emission names switch.
        # IoT mirrors: SQL references the local mirror schema.table likewise.
        try:
            _im = getattr(self, "_iot_mirror", None) or {}
            for _sn in list(disp.keys()):
                try:
                    if _sn in _im:
                        disp[_sn] = str(_im[_sn].get("table") or _sn)
                        continue
                    _tt = self._staged_temp_of(_sn)
                    if _tt:
                        disp[_sn] = _tt
                except Exception:
                    pass
        except Exception:
            pass
        table_map = {fn: (aliases[t] if t in aliases else base_alias)
                     for fn, t in field_table.items()
                     if t == base_norm or t in aliases}
        # duplicate field names across tables: bare refs bind to the base
        # (default) table — avoids spurious many-side JOINs + fan-out.
        try:
            _byt: Dict[str, set] = {}
            for _f in (fields or []):
                _fn2 = str(getattr(_f, "name", "") or "").strip().lower()
                _tt2 = self._norm_table(getattr(_f, "table_source", "") or "")
                if _fn2 and _tt2:
                    _byt.setdefault(_fn2, set()).add(_tt2)
            for _fn2, _ts2 in _byt.items():
                if len(_ts2) > 1 and base_norm in _ts2 and base_alias and _fn2 in table_map:
                    table_map[_fn2] = base_alias
        except Exception:
            pass
        # Per-column: remote merge specs vs local rewrite
        new_cols = []
        join_needed: set = set()
        merges: List[Dict[str, Any]] = []
        for col in inlined:
            raw = (getattr(col, "expr", None) or getattr(col, "name", None) or "").strip()
            refs = self._ref_tables_in_text(raw, prefer=base_norm)
            remote_refs = {t for t in refs if t in remote_sec}
            if remote_refs:
                spec = self._plan_remote_column(col, raw, refs, remote_sec, base_norm, base_alias,
                                                disp, field_table, fields, group_by)
                if spec is None:
                    raise ValueError(
                        f'لا يمكن حساب العمود "{getattr(col, "alias", "")}" عبر اتصالين في تعبير واحد — '
                        f'بسّط التعبير أو انقل الحساب لعمود منفصل.')
                merges.append(spec)
                new_cols.append(dataclasses.replace(col, expr="NULL"))
                continue
            refs_tables = refs
            if not (refs_tables - {base_norm}):
                new_cols.append(col)
                continue
            new_raw, need = self._rewrite_cross_agg(
                raw, field_table, base_norm, disp, aliases, base_schema_norm, base_schema_q, base_alias,
                db=base_db, sec_schemas={s: self._schema_for_table(s, self._conn_key_of_table(s), report_schema) for s in local_sec},
                sec_displays=disp)
            join_needed |= {t for t in need if t in local_sec}
            # Remote refs surviving a local rewrite = unsupported mix
            if {t for t in self._refs_in_text(new_raw, field_table) if field_table.get(t) in remote_sec}:
                raise ValueError(
                    f'لا يمكن حساب العمود "{getattr(col, "alias", "")}" عبر اتصالين في تعبير واحد — '
                    f'بسّط التعبير أو انقل الحساب لعمود منفصل.')
            new_cols.append(dataclasses.replace(col, expr=new_raw) if new_raw != raw else col)
        # Tables referenced only by general_where still need their JOIN
        # (no column binds them, so the per-column loop above skips them).
        if _gw_text:
            for r in self._refs_in_text(_gw_text, field_table):
                t = field_table.get(r)
                if t and t in local_sec:
                    join_needed.add(t)
        joins = []
        for s in sorted(join_needed):
            ssch = self._schema_for_table(s, self._conn_key_of_table(s), report_schema)
            ssch_q = _q(ssch) if ssch else ""
            key = self._infer_join_key(base_norm, s, base_schema_norm, base_db, ssch)
            if not key:
                raise ValueError(
                    f'تعذر الاستدلال على مفتاح الربط بين "{from_table}" و"{disp.get(s, s)}". '
                    f'أضف عموداً مشتركاً (مثل رقم المستند) في الجدولين.')
            bcol, scol = key
            # True-case column spelling (critical for case-sensitive engines like Postgres)
            try:
                _sdb = self._db_for_conn(self._conn_key_of_table(s))
            except Exception:
                _sdb = base_db
            bcol = self._orig_col(base_norm, bcol, base_db, base_schema) or bcol
            scol = self._orig_col(s, scol, _sdb, ssch) or scol
            sec_q = f"{ssch_q + '.' if ssch_q else ''}{_q(disp.get(s, s))}"
            _lon = f"{_q(aliases[s])}.{_q(scol)}"
            _ron = f"{_q(base_alias)}.{_q(bcol)}"
            try:
                _bcat, _scat = self._join_col_cats(base_norm, bcol, s, scol)
            except Exception:
                _bcat = _scat = ""
            _mspec = self._link_match_spec(base_norm, s)
            if _mspec["match"] != "exact":
                # Fuzzy link: predicate per match mode (never plain equality).
                if _bcat == "DATE" or _scat == "DATE":
                    raise ValueError(
                        'الربط ' + _mspec["match"] + f' بين "{base_norm}" و"{s}" '
                        'لا يعمل على أعمدة التاريخ — استخدم ربطاً تاماً.')
                _pred = self._link_join_on(_mspec["match"], _mspec["pattern"], _lon, _ron, base_db)
                if not _pred:
                    raise ValueError(
                        'الربط ' + _mspec["match"] + f' بين "{base_norm}" و"{s}" '
                        'غير قابل للتنفيذ SQL على هذه القاعدة.')
                joins.append(f"LEFT JOIN {sec_q} {_q(aliases[s])} ON {_pred}")
                continue
            if _bcat and _scat and _bcat != _scat and _bcat != "DATE" and _scat != "DATE":
                # text = integer & friends: compare as text instead of failing
                if _is_pg_db(base_db):
                    _lon, _ron = f"CAST({_lon} AS TEXT)", f"CAST({_ron} AS TEXT)"
                elif _is_mssql_db(base_db):
                    _lon, _ron = f"CAST({_lon} AS NVARCHAR(4000))", f"CAST({_ron} AS NVARCHAR(4000))"
                else:
                    _lon, _ron = f"CAST({_lon} AS VARCHAR2(4000))", f"CAST({_ron} AS VARCHAR2(4000))"
            joins.append(f"LEFT JOIN {sec_q} {_q(aliases[s])} ON {_lon} = {_ron}")
        # Extra selects: base keys/anchors for remote merges (stripped later; original case)
        extra: List[Tuple[str, str]] = []
        strip: set = set()
        rj = 0
        for m in merges:
            if m["kind"] == "direct":
                al = f"_rmlj{rj}"; rj += 1
                m["key_alias"] = al
                _bk = self._orig_col(base_norm, m["base_key"], base_db, base_schema)
                extra.append((f"{_q(base_alias)}.{_q(_bk)}", al))
                strip.add(al)
            else:
                al = f"_rmla{rj}"; rj += 1
                m["anchor_alias"] = al
                _ab = self._orig_col(base_norm, m["anchor_base"], base_db, base_schema)
                extra.append((f"{_q(base_alias)}.{_q(_ab)}", al))
                strip.add(al)
        from_q = f"{base_q} {_q(base_alias)}" + ("" if not joins else " " + " ".join(joins))
        if _gw_deferred:
            # Deferred GW evaluates on merged rows, so the table must actually
            # merge (i.e. at least one displayed column binds it for matching).
            _merged_tables = {m.get("table") for m in (merges or [])}
            for _dd in _gw_deferred:
                if isinstance(_dd, dict) and _dd.get("sec") not in _merged_tables:
                    raise ValueError(
                        f'الشرط العام المؤجل على "{_dd.get("sec")}" يتطلب عموداً معروضاً من نفس الجدول '
                        f'لإتمام المطابقة — اعرض عموداً منه أولاً.')
        return {"from_table": from_table, "base_norm": base_norm, "base_conn": base_conn,
                "base_db": base_db, "base_schema": base_schema, "base_disp": disp.get(base_norm, base_norm),
                "from_q": from_q,
                "columns": new_cols, "table_map": table_map, "merges": merges, "extra": extra,
                "strip": strip, "local_sec": sorted(set(local_sec)), "remote": remote_sec,
                "base_alias": base_alias,
                "gw_base": _gw_base, "gw_remote": _gw_remote,
                "gw_deferred": _gw_deferred}

    def _plan_remote_column(self, col, raw: str, refs_tables: set, remote_sec: Dict[str, Any],
                            base_norm: str, base_alias: str, disp: Dict[str, str],
                            field_table: Dict[str, str], fields, group_by) -> Optional[Dict[str, Any]]:
        """Plan a column touching remote table(s): direct-lookup or grouped-aggregate spec."""
        remotes = sorted(t for t in refs_tables if t in remote_sec)
        if len(remotes) != 1:
            return None
        sec = remotes[0]
        info = remote_sec[sec]
        alias = getattr(col, "alias", None) or getattr(col, "name", "")
        # Strip one formatting wrapper for classification
        core = self._strip_format_wrapper(raw)
        # Direct lookup: core reduces to a single remote field
        core_refs = {field_table[r] for r in self._refs_in_text(core, field_table)}
        if core_refs == {sec}:
            bare = [r for r in self._refs_in_text(core, field_table)]
            # exactly one distinct field?
            fields_hit = {r for r in bare}
            if len(fields_hit) == 1:
                # canonical original-case name
                fkey = next(iter(fields_hit))
                fobj = next((f for f in (fields or []) if str(getattr(f, "name", "")).lower() == fkey), None)
                fname = str(getattr(fobj, "name", fkey)) if fobj is not None else fkey
                _mspec = self._link_match_spec(base_norm, sec)
                return {"kind": "direct", "alias": alias, "table": sec, "conn": info["conn"],
                        "db": info["db"], "schema": info["schema"], "disp": disp.get(sec, sec),
                        "field": fname, "base_key": info["key"][0],
                        "match": _mspec["match"], "pattern": _mspec["pattern"]}
        # Grouped aggregate over remote (windowed w/ base anchor, or plain agg + group anchor)
        for m in list(re.finditer(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", core, re.IGNORECASE)):
            parsed = self._split_agg_call(core, m.start())
            if not parsed:
                continue
            func, inner, rest, end = parsed[0], parsed[1], parsed[2], parsed[3] if len(parsed) > 3 else (None, None, None, None)
            break
        else:
            return None
        # NOTE: _split_agg_call returns (inner, rest, end); func from match
        func = None
        inner = None
        for m in list(re.finditer(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", core, re.IGNORECASE)):
            parsed = self._split_agg_call(core, m.start())
            if parsed:
                func, inner = m.group(1).upper(), parsed[0]
                # OVER part lives in `rest`/beyond; find anchors from whole core
                break
        if not func:
            return None
        inner_refs = {field_table[r] for r in self._refs_in_text(inner, field_table)}
        if inner.strip() != "*" and inner_refs != {sec}:
            return None
        # Anchor: PARTITION BY base fields, else GROUP BY base field
        anchors = []
        pm = re.search(r"PARTITION\s+BY\s+(.+)$", core, re.IGNORECASE | re.DOTALL)
        src = pm.group(1) if pm else ""
        if src:
            # trim trailing OVER-close paren content already balanced? take refs only
            anchors = [r for r in self._refs_in_text(src, field_table)
                       if field_table.get(r) == base_norm]
            if self._refs_in_text(src, field_table) - set(anchors) - {r for r in self._refs_in_text(src, field_table) if field_table.get(r) == base_norm}:
                pass
            others = {field_table.get(r) for r in self._refs_in_text(src, field_table)} - {base_norm}
            if others:
                return None
        elif group_by:
            gb = _find_column_for_field(str(group_by), None)  # not available; use raw group field below
            anchors = []
        if not anchors and group_by:
            gf = str(group_by)
            # group field may be alias -> resolve via self.columns
            gcol = _find_column_for_field(gf, getattr(self, "columns", []))
            graw = (getattr(gcol, "expr", None) or gf) if gcol is not None else gf
            grefs = [r for r in self._refs_in_text(graw, field_table) if field_table.get(r) == base_norm]
            anchors = grefs[:1]
        if len(anchors) != 1:
            return None
        abase = anchors[0]
        # canonical base field name (original case)
        fobj = next((f for f in (fields or []) if str(getattr(f, "name", "")).lower() == abase), None)
        abase_name = str(getattr(fobj, "name", abase)) if fobj is not None else abase
        ds = self._match_date_col(abase, sec, info["schema"], info["db"])
        if ds:
            ds = self._orig_col(sec, ds, info["db"], info["schema"])
        if not ds:
            # fallback: anchor directly on join key when partition field IS the key
            if abase.upper() == info["key"][0].upper() or abase.upper() == info["key"][1].upper():
                # join-key anchored aggregate -> group by remote key, map via base key
                ds = info["key"][1]
            else:
                return None
        _mspec = self._link_match_spec(base_norm, sec)
        return {"kind": "agg", "alias": alias, "table": sec, "conn": info["conn"], "db": info["db"],
                "schema": info["schema"], "disp": disp.get(sec, sec), "func": func, "inner": inner,
                "anchor_base": abase_name, "anchor_sec": ds, "base_key": info["key"][0],
                "key_anchor": ds is None,
                "match": _mspec["match"], "pattern": _mspec["pattern"]}
    def _prepare_from_and_columns(self, active_table, filters, sort, group_by):
        """Compat wrapper: (from_q, columns, table_map) from the routing plan."""
        plan = self._plan_structure(active_table, filters, sort, group_by)
        self._reject_deferred(plan, "في هذا المسار")
        return plan["from_q"], plan["columns"], plan["table_map"]

    @staticmethod
    def _chunk(lst, n=500):
        return [lst[i:i + n] for i in range(0, len(lst), n)]

    def _exec_on(self, db, sql, params):
        """Execute on any engine (connects on demand)."""
        try:
            if not getattr(db, "conn", None):
                db.connect()
        except Exception:
            pass
        return db._exec(sql, params or {})

    def _remote_field_of_filter(self, f, plan):
        """Resolve a filter to (remote_sec or None, remote_field_or_None).

        Handles direct remote field names and aliases of merged columns.
        """
        remote = plan.get("remote") or {}
        if not remote:
            return None, None
        field_table = self._field_table_map()
        fld = str(f.get("field") or f.get("column") or f.get("name") or "")
        tables = set()
        for r in self._refs_in_text(fld, field_table):
            tables.add(field_table[r])
        try:
            col = _find_column_for_field(fld, plan.get("columns") or self.columns)
        except Exception:
            col = None
        if col is not None:
            for r in self._refs_in_text(getattr(col, "expr", "") or "", field_table):
                # skip refs already rewritten away (NULL placeholders carry none)
                tables.add(field_table[r])
        key = fld.strip().lower()
        if key in field_table:
            tables.add(field_table[key])
        # merged alias -> underlying remote spec
        for m in (plan.get("merges") or []):
            if str(m.get("alias", "")).lower() == key or str(m.get("alias", "")) == fld:
                tables.add(m["table"])
        remotes = {t for t in tables if t in remote}
        if not remotes:
            return None, None
        if len(remotes) > 1:
            raise ValueError(
                f'لا يمكن دمج فلتر واحد عبر اتصالين ({fld}) — افصل الشروط.')
        sec = next(iter(remotes))
        # underlying remote field: merged alias wins, else direct field refs
        for m in (plan.get("merges") or []):
            if m["table"] == sec and (str(m.get("alias", "")).lower() == key or str(m.get("alias", "")) == fld):
                if m["kind"] == "direct":
                    return sec, m["field"]
                # aggregate alias filtered -> not semi-joinable; handled as error downstream
                raise ValueError(
                    f'لا يمكن تصفية العمود التجميعي البعيد "{fld}" هنا — اعرضه أولاً ثم رشّح.')
        # direct remote field name
        cands = [r for r in self._refs_in_text(fld, field_table) if field_table.get(r) == sec]
        if col is not None:
            cands += [r for r in self._refs_in_text(getattr(col, "expr", "") or "", field_table)
                      if field_table.get(r) == sec]
        if key in field_table and field_table[key] == sec:
            cands.append(key)
        uniq = []
        for cnd in cands:
            if cnd not in uniq:
                uniq.append(cnd)
        if len(uniq) != 1:
            raise ValueError(
                f'تعذر تحديد عمود بعيد واحد للفلتر على "{fld}" — حدد عموداً واحداً.')
        return sec, uniq[0]

    @staticmethod
    def _split_top_and(text):
        """Split on top-level AND (quote/paren aware; BETWEEN..AND kept whole)."""
        parts, depth, cur = [], 0, []
        in_q = False
        seg_between = False
        i, n = 0, len(text or "")
        while i < n:
            ch = text[i]
            if in_q:
                cur.append(ch)
                if ch == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        cur.append(text[i + 1])
                        i += 1
                    else:
                        in_q = False
                i += 1
                continue
            if ch == "'":
                in_q = True
                cur.append(ch)
                i += 1
                continue
            if ch == "(":
                depth += 1
                cur.append(ch)
                i += 1
                continue
            if ch == ")":
                depth = max(0, depth - 1)
                cur.append(ch)
                i += 1
                continue
            if depth == 0 and (ch == "A" or ch == "a"):
                _m = re.match(r"AND(?![\w\u0600-\u06FF])", text[i:], re.IGNORECASE)
                _pre_ok = (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_"))
                if _m and _pre_ok:
                    if seg_between:
                        cur.append(text[i:i + 3])
                        seg_between = False
                    else:
                        parts.append("".join(cur))
                        cur = []
                    i += 3
                    continue
            if depth == 0 and (ch == "B" or ch == "b"):
                _m = re.match(r"BETWEEN(?![\w\u0600-\u06FF])", text[i:], re.IGNORECASE)
                _pre_ok = (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_"))
                if _m and _pre_ok:
                    seg_between = True
            cur.append(ch)
            i += 1
        parts.append("".join(cur))
        return [p.strip() for p in parts if str(p or "").strip() != ""]

    @staticmethod
    def _strip_outer_parens(s):
        """Remove balanced outer paren pairs repeatedly."""
        s = str(s or "").strip()
        while len(s) >= 2 and s[0] == "(" and s[-1] == ")":
            depth = 0
            ok = True
            for i, ch in enumerate(s):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if depth == 0 and i < len(s) - 1:
                    ok = False
                    break
            if not ok or depth != 0:
                break
            s = s[1:-1].strip()
        return s

    def _split_general_where(self, gw_text, base_norm, local_sec, remote_sec, field_table):
        """Split general_where into (base_sql_text, remote_filter_dicts, deferred).

        Top-level AND conjuncts touching exactly ONE cross-DB table convert
        to {field, op, value} dicts (consumed by the two-phase IN rewrite);
        single-table OR/NOT-free shapes also convert (IS NULL, IN, any-groups).
        Conjuncts on fuzzy-linked (contains/regex) tables cannot become key
        lists, so they are DEFERRED: [{sec, pred, text}] evaluated in Python
        after the merge. Raises loudly when a remote ref cannot convert
        (multi-table mixes, cross-DB comparisons, unparsable shapes) —
        silent mis-filtering is worse.
        """
        remote_tables = set((remote_sec or {}).keys())
        if not gw_text or not remote_tables:
            return gw_text or "", [], []
        local_set = set(local_sec or []) | {base_norm}
        base_parts, remote_dicts, deferred = [], [], []
        for part in self._split_top_and(gw_text):
            core = self._strip_outer_parens(part)
            try:
                _refs = {r for r in self._refs_in_text(core, field_table) if field_table.get(r)}
            except Exception:
                _refs = set()
            tables = {field_table[r] for r in _refs}
            remotes = sorted(t for t in tables if t in remote_tables)
            if not remotes:
                base_parts.append(part)
                continue
            try:
                _skel = re.sub(r"('(?:[^']|'')*')", "''", core)
                _has_or = re.search(r"(?<![\w\u0600-\u06FF])(OR|NOT)(?![\w\u0600-\u06FF])", _skel, re.IGNORECASE)
            except Exception:
                _has_or = True
            _others = sorted(t for t in tables if t != remotes[0] or len(remotes) > 1)
            if _others or len(remotes) > 1:
                raise ValueError(
                    f'الشرط العام يدمج جدولاً من اتصال آخر ({", ".join(remotes)}) مع شرط مركب — '
                    f'غير مدعوم: انقل الشرط إلى فلاتر المشغل أو إلى عمود من الاتصال الأساسي.')
            sec = remotes[0]
            try:
                _mspec = self._link_match_spec(base_norm, sec)
            except Exception:
                _mspec = {"match": "exact", "pattern": ""}
            _match = str(_mspec.get("match") or "exact").strip().lower()
            if _match != "exact":
                # Fuzzy link: key lists are meaningless across the match
                # function — defer to post-merge Python evaluation.
                _pred = self._gw_parse_pred(core, sec)
                if _pred is None:
                    raise ValueError(
                        f'الشرط العام على الجدول "{sec}" المربوط {_match} بصيغة غير مدعومة ({core[:80]}) — '
                        f'المسموح: مقارنات (= > < >= <= !=) و IS NULL و IN و AND/OR/NOT على أعمدة {sec} فقط.')
                deferred.append({"sec": sec, "pred": _pred, "text": core, "_gw_deferred": True})
                continue
            if _has_or:
                # Single-table OR/NOT shape: one remote WHERE with a group.
                _pred = self._gw_parse_pred(core, sec)
                _flts = self._gw_pred_to_filters(_pred) if _pred is not None else None
                if _pred is None or _flts is None:
                    raise ValueError(
                        f'الشرط العام يدمج جدولاً من اتصال آخر ({", ".join(remotes)}) مع شرط مركب — '
                        f'غير مدعوم: انقل الشرط إلى فلاتر المشغل أو إلى عمود من الاتصال الأساسي.')
                remote_dicts.extend(_flts)
                continue
            _fd = self._gw_remote_dict(core, sec)
            if _fd is None:
                # last chance: IS NULL / nested shapes via the pred parser
                _pred = self._gw_parse_pred(core, sec)
                _flts = self._gw_pred_to_filters(_pred) if _pred is not None else None
                if _pred is None or _flts is None:
                    raise ValueError(
                        f'الشرط العام على الجدول "{sec}" بصيغة غير مدعومة عبر الاتصالات ({core[:80]}) — '
                        f'المسموح: عمود = قيمة (أو > < >= <= !=)، IS NULL، IN — أو انقله لفلاتر المشغل.')
                remote_dicts.extend(_flts)
                continue
            remote_dicts.append(_fd)
        return (" AND ".join(base_parts), remote_dicts, deferred)

    @staticmethod
    def _gw_lit(raw):
        """Parse a general_where literal: quoted/N-quoted string | int | float, else None."""
        s = str(raw or "").strip()
        if len(s) >= 3 and s[0] in ("N", "n") and s[1] == "'" and s[-1] == "'":
            s = s[1:]
        if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
            try:
                return s[1:-1].replace("''", "'")
            except Exception:
                return None
        if re.fullmatch(r"-?\d+", s or ""):
            try:
                return int(s)
            except Exception:
                return None
        if re.fullmatch(r"-?(\d+\.\d*|\.\d+)", s or ""):
            try:
                return float(s)
            except Exception:
                return None
        return None

    @staticmethod
    def _gw_split_list(raw):
        """Comma-split an IN-list litigating quoted spans; None when malformed."""
        parts, cur, in_q = [], [], False
        i, n = 0, len(raw or "")
        while i < n:
            ch = raw[i]
            if in_q:
                cur.append(ch)
                if ch == "'":
                    if i + 1 < n and raw[i + 1] == "'":
                        cur.append(raw[i + 1])
                        i += 1
                    else:
                        in_q = False
                i += 1
                continue
            if ch == "'":
                in_q = True
                cur.append(ch)
                i += 1
                continue
            if ch == ",":
                parts.append("".join(cur))
                cur = []
                i += 1
                continue
            cur.append(ch)
            i += 1
        if in_q:
            return None
        parts.append("".join(cur))
        return parts

    @staticmethod
    def _gw_norm_side(s):
        """Normalize a GW operand: strip formula markers (=) + redundant parens.

        Column-expr expansion yields (=get(...)) / ((=get(...))) — unwrap to
        get(...) so ref extraction sees through. Function wraps (SUM(x))
        survive intact and stay rejected downstream.
        """
        t = str(s or "").strip()
        for _ in range(10):
            t0 = t
            while t.startswith("="):
                t = t[1:].strip()
            t = RMLReportEngine._strip_outer_parens(t)
            if t == t0:
                break
        return t

    def _gw_ref_key(self, ref_text, sec):
        """Single remote field key for a ref string, or None.

        Accepts [..] / @.. / get(..) / =get(..) single refs; exactly one
        field whose table is `sec`, else None.
        """
        try:
            ref_text = self._gw_norm_side(ref_text)
            ft = self._field_table_map()
            keys = {r for r in self._refs_in_text(str(ref_text or ""), ft) if ft.get(r)}
            if len(keys) != 1:
                return None
            key = next(iter(keys))
            try:
                _same = self._norm_table(ft.get(key) or "") == self._norm_table(sec or "")
            except Exception:
                _same = (ft.get(key) or "") == (sec or "")
            if not _same:
                return None
            return key
        except Exception:
            return None

    @staticmethod
    def _gw_find_compare(core):
        """First top-level comparison outside quotes/parens.

        Returns (op, left, right) with op in equals/not_equals/gt/lt/gte/lte,
        or None. Shared by _gw_remote_dict and the GW predicate parser.
        """
        depth, in_q = 0, False
        i, n, at = 0, len(core or ""), -1
        _op, _oplen = None, 0
        while i < n:
            ch = core[i]
            if in_q:
                if ch == "'":
                    if i + 1 < n and core[i + 1] == "'":
                        i += 1
                    else:
                        in_q = False
                i += 1
                continue
            if ch == "'":
                in_q = True
                i += 1
                continue
            if ch == "(":
                depth += 1
                i += 1
                continue
            if ch == ")":
                depth = max(0, depth - 1)
                i += 1
                continue
            if depth == 0:
                if core.startswith("!=", i) or core.startswith("<>", i):
                    _op, _oplen, at = "not_equals", 2, i
                    break
                if core.startswith(">=", i) or core.startswith("<=", i):
                    _op, _oplen, at = ("gte" if core[i] == ">" else "lte"), 2, i
                    break
                if ch == "=":
                    _op, _oplen, at = "equals", 1, i
                    break
                if ch in (">", "<"):
                    _op, _oplen, at = ("gt" if ch == ">" else "lt"), 1, i
                    break
            i += 1
        if _op is None or at < 0:
            return None
        _left, _right = core[:at].strip(), core[at + _oplen:].strip()
        if not _left or not _right:
            return None
        return _op, _left, _right

    def _gw_remote_dict(self, core, sec):
        """One remote conjunct -> {field, op, value} dict, or None."""
        # IS [NOT] NULL shape: ref IS NULL | ref IS NOT NULL
        _nullm = re.fullmatch(r"(?s)\s*(.+?)\s+IS\s+(NOT\s+)?NULL\s*", str(core or "").strip(), re.IGNORECASE)
        if _nullm:
            _r1 = self._gw_norm_side(_nullm.group(1))
            if re.search(r"[\(\)]", _r1) and not re.fullmatch(r"(?i)get\s*\(.+\)", _r1):
                return None
            _key = self._gw_ref_key(_r1, sec)
            if _key is None:
                return None
            return {"field": _key, "op": "is_not_null" if _nullm.group(2) else "is_null"}
        # IN-list shape first: ref IN (v1, v2, ...)
        _inm = re.fullmatch(r"(?s)\s*(.+?)\s+IN\s*\((.+)\)\s*", str(core or "").strip())
        if _inm:
            _r1 = self._gw_norm_side(_inm.group(1))
            if re.search(r"[\(\)]", _r1) and not re.fullmatch(r"(?i)get\s*\(.+\)", _r1):
                return None
            _key = self._gw_ref_key(_r1, sec)
            _items = self._gw_split_list(_inm.group(2))
            if _key is None or not _items:
                return None
            _vals = [self._gw_lit(self._gw_norm_side(x)) for x in _items]
            if any(v is None for v in _vals):
                return None
            return {"field": _key, "op": "in", "value": _vals}
        _fc = self._gw_find_compare(core)
        if _fc is None:
            return None
        _op, _left, _right = _fc
        _left, _right = self._gw_norm_side(_left), self._gw_norm_side(_right)
        if re.search(r"[\(\)]", _left) and not re.fullmatch(r"(?i)get\s*\(.+\)", _left):
            return None  # function-wrapped refs keep SQL semantics: unsupported
        _key = self._gw_ref_key(_left, sec)
        if _key is None:
            return None
        _val = self._gw_lit(_right)
        if _val is None:
            return None
        return {"field": _key, "op": _op, "value": _val}

    @staticmethod
    def _split_top_or(text):
        """Split on top-level OR (quote/paren aware; mirrors _split_top_and)."""
        parts, depth, cur = [], 0, []
        in_q = False
        i, n = 0, len(text or "")
        while i < n:
            ch = text[i]
            if in_q:
                cur.append(ch)
                if ch == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        cur.append(text[i + 1])
                        i += 1
                    else:
                        in_q = False
                i += 1
                continue
            if ch == "'":
                in_q = True
                cur.append(ch)
                i += 1
                continue
            if ch == "(":
                depth += 1
                cur.append(ch)
                i += 1
                continue
            if ch == ")":
                depth = max(0, depth - 1)
                cur.append(ch)
                i += 1
                continue
            if depth == 0 and (ch == "O" or ch == "o"):
                _m = re.match(r"OR(?![\w\u0600-\u06FF])", text[i:], re.IGNORECASE)
                _pre_ok = (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_"))
                if _m and _pre_ok:
                    parts.append("".join(cur))
                    cur = []
                    i += 2
                    continue
            cur.append(ch)
            i += 1
        parts.append("".join(cur))
        return [p.strip() for p in parts if str(p or "").strip() != ""]

    def _gw_parse_pred(self, text, sec):
        """Parse a single-remote-table GW fragment into a predicate AST, or None.

        AST: ("and", [...]) | ("or", [...]) | ("not", sub)
           | ("cmp", refkey, op, literal, raw_left)
           | ("null", refkey, negated, raw_left)
           | ("in", refkey, [literals], negated, raw_left)
        Every ref must resolve to `sec` and every right-hand side must be a
        literal (a second column ref = cross-DB comparison => None => loud).
        LIKE / function-wrapped refs stay SQL-side (None => loud downstream).
        """
        frag = self._strip_outer_parens(str(text or "").strip())
        if not frag:
            return None
        ors = self._split_top_or(frag)
        if len(ors) > 1:
            subs = [self._gw_parse_pred(o, sec) for o in ors]
            if any(s is None for s in subs):
                return None
            return ("or", subs)
        ands = self._split_top_and(frag)
        if len(ands) > 1:
            subs = [self._gw_parse_pred(a, sec) for a in ands]
            if any(s is None for s in subs):
                return None
            return ("and", subs)
        _notm = re.match(r"(?is)^NOT\s+(.+)$", frag)
        if _notm:
            sub = self._gw_parse_pred(_notm.group(1), sec)
            return ("not", sub) if sub is not None else None
        return self._gw_parse_leaf(frag, sec)

    def _gw_parse_leaf(self, frag, sec):
        """One predicate leaf (no top-level AND/OR/NOT) -> AST or None."""
        s = str(frag or "").strip()
        _nm = re.fullmatch(r"(?s)\s*(.+?)\s+IS\s+(NOT\s+)?NULL\s*", s, re.IGNORECASE)
        if _nm:
            _left = self._gw_norm_side(_nm.group(1))
            if re.search(r"[\(\)]", _left) and not re.fullmatch(r"(?i)get\s*\(.+\)", _left):
                return None
            _key = self._gw_ref_key(_left, sec)
            if _key is None:
                return None
            return ("null", _key, bool(_nm.group(2)), _left)
        _im = re.fullmatch(r"(?s)\s*(.+?)\s+(NOT\s+)?IN\s*\((.+)\)\s*", s, re.IGNORECASE)
        if _im:
            _left = self._gw_norm_side(_im.group(1))
            if re.search(r"[\(\)]", _left) and not re.fullmatch(r"(?i)get\s*\(.+\)", _left):
                return None
            _key = self._gw_ref_key(_left, sec)
            _items = self._gw_split_list(_im.group(3))
            if _key is None or not _items:
                return None
            _vals = [self._gw_lit(self._gw_norm_side(x)) for x in _items]
            if any(v is None for v in _vals):
                return None
            return ("in", _key, _vals, bool(_im.group(2)), _left)
        try:
            _nos = re.sub(r"('(?:[^']|'')*')", "''", s)
            if re.search(r"(?<![\w\u0600-\u06FF])LIKE(?![\w\u0600-\u06FF])", _nos, re.IGNORECASE):
                return None
        except Exception:
            return None
        _fc = self._gw_find_compare(s)
        if _fc is None:
            return None
        _op, _left, _right = _fc
        _left, _right = self._gw_norm_side(_left), self._gw_norm_side(_right)
        if re.search(r"[\(\)]", _left) and not re.fullmatch(r"(?i)get\s*\(.+\)", _left):
            return None
        _key = self._gw_ref_key(_left, sec)
        _val = self._gw_lit(_right)
        if _key is None or _val is None:
            return None
        return ("cmp", _key, _op, _val, _left)

    def _gw_pred_to_filters(self, pred):
        """AST -> player-style filter dicts for server pushdown, or None.

        ("not", ...) has no pushdown form (De Morgan omitted on purpose):
        None here still evaluates fine post-merge for deferred predicates.
        """
        try:
            kind = pred[0]
        except Exception:
            return None
        if kind == "cmp":
            _, key, op, lit, _raw = pred
            return [{"field": key, "op": op, "value": lit}]
        if kind == "null":
            _, key, neg, _raw = pred
            return [{"field": key, "op": "is_not_null" if neg else "is_null"}]
        if kind == "in":
            _, key, vals, neg, _raw = pred
            return [{"field": key, "op": "not_in" if neg else "in", "value": list(vals)}]
        if kind == "and":
            out = []
            for s in pred[1]:
                c = self._gw_pred_to_filters(s)
                if c is None:
                    return None
                out.extend(c)
            return out
        if kind == "or":
            grp = []
            for s in pred[1]:
                if s[0] == "and":
                    c = self._gw_pred_to_filters(s)
                    if c is None:
                        return None
                    grp.append({"all": c} if len(c) > 1 else c[0])
                else:
                    c = self._gw_pred_to_filters(s)
                    if c is None or len(c) != 1:
                        return None
                    grp.append(c[0])
            return [{"any": grp}]
        return None

    @staticmethod
    def _gw_num(x):
        """Numeric value when x is int/float/numeric-string, else None."""
        try:
            if isinstance(x, bool):
                return float(x)
            if isinstance(x, (int, float)):
                return float(x)
            s = str(x).strip()
            if re.fullmatch(r"-?(\d+(\.\d*)?|\.\d+)", s or ""):
                return float(s)
        except Exception:
            pass
        return None

    @classmethod
    def _gw_vals_equal(cls, a, b):
        na, nb = cls._gw_num(a), cls._gw_num(b)
        if na is not None and nb is not None:
            return na == nb
        return str(a) == str(b)

    @classmethod
    def _gw_vals_cmp(cls, a, b):
        """Three-way compare (-1/0/1) or None when incomparable.

        Numbers compare numerically; dates compare by ISO date part;
        everything else lexicographically on str().
        """
        try:
            import datetime as _dt
            _ad = isinstance(a, (_dt.date, _dt.datetime))
            _bd = isinstance(b, (_dt.date, _dt.datetime))
            if _ad or _bd:
                sa = a.isoformat()[:19] if _ad else str(b if _ad else a)[:19]
                sb = b.isoformat()[:19] if _bd else str(a if _bd else b)[:19]
                return (sa > sb) - (sa < sb)
        except Exception:
            pass
        na, nb = cls._gw_num(a), cls._gw_num(b)
        if na is not None and nb is not None:
            return (na > nb) - (na < nb)
        try:
            sa, sb = str(a), str(b)
            return (sa > sb) - (sa < sb)
        except Exception:
            return None

    def _gw_pred_eval(self, pred, lookup):
        """Three-valued eval (True/False/None=unknown); keep rows evaluating True.

        lookup(refkey, raw_left) -> merged-row value (None when unmatched).
        SQL WHERE semantics: unknown filters the row out.
        """
        kind = pred[0]
        if kind == "and":
            unk = False
            for s in pred[1]:
                v = self._gw_pred_eval(s, lookup)
                if v is False:
                    return False
                if v is None:
                    unk = True
            return None if unk else True
        if kind == "or":
            unk = False
            for s in pred[1]:
                v = self._gw_pred_eval(s, lookup)
                if v is True:
                    return True
                if v is None:
                    unk = True
            return None if unk else False
        if kind == "not":
            v = self._gw_pred_eval(pred[1], lookup)
            return None if v is None else (not v)
        if kind == "null":
            _, key, neg, raw = pred
            v = lookup(key, raw)
            return (v is not None) if neg else (v is None)
        if kind == "in":
            _, key, vals, neg, raw = pred
            v = lookup(key, raw)
            if v is None:
                return None
            hit = any(self._gw_vals_equal(v, x) for x in vals)
            return (not hit) if neg else hit
        if kind == "cmp":
            _, key, op, lit, raw = pred
            v = lookup(key, raw)
            if v is None:
                return None
            if op == "equals":
                return self._gw_vals_equal(v, lit)
            if op == "not_equals":
                return not self._gw_vals_equal(v, lit)
            c = self._gw_vals_cmp(v, lit)
            if c is None:
                return None
            return {"gt": c > 0, "lt": c < 0, "gte": c >= 0, "lte": c <= 0}.get(op)
        return None

    @staticmethod
    def _gw_walk_leaves(pred):
        """Yield (refkey, raw_left) of every leaf in a predicate AST."""
        try:
            kind = pred[0]
        except Exception:
            return
        if kind in ("cmp", "null", "in"):
            yield (pred[1], pred[-1])
        elif kind in ("and", "or"):
            for s in pred[1]:
                for leaf in RMLReportEngine._gw_walk_leaves(s):
                    yield leaf
        elif kind == "not":
            for leaf in RMLReportEngine._gw_walk_leaves(pred[1]):
                yield leaf

    def _gw_make_row_getter(self, columns, keyset, merges=None):
        """Build a merged-row value getter with memoized ref->alias binding.

        Returns (getter, alias_of). getter(row_low, refkey, raw) reads the
        case-folded row dict; alias_of(refkey, raw) resolves the expected
        row key (None when unresolvable -> loud error upstream, never silent).
        `merges` (plan specs) is REQUIRED for remote-merged columns: the
        plan NULLs their expr (expr="NULL"), so expr matching cannot see
        them — their spec field->alias map can.
        """
        cache: Dict[tuple, Optional[str]] = {}
        _mmap: Dict[str, str] = {}
        try:
            for _m in (merges or []):
                _mf = str(_m.get("field") or "").strip().lower()
                _ma = str(_m.get("alias") or "").strip().lower()
                if _mf and _ma:
                    _mmap.setdefault(_mf, _ma)
        except Exception:
            pass

        def _alias(refkey, raw):
            ck = (str(refkey or ""), str(raw or ""))
            if ck in cache:
                return cache[ck]
            found = None
            cands = []
            if raw:
                cands.append(str(raw).strip())
                _g = str(raw).strip()
                if _g.lower().startswith("get(") and _g.endswith(")"):
                    cands.append(_g[4:-1].strip())
            if refkey:
                cands.append(str(refkey))
                if str(refkey).startswith("@"):
                    cands.append(str(refkey)[1:])
            for _c in cands:
                _cl = str(_c).strip().lower()
                if not _cl:
                    continue
                if _cl in keyset:
                    found = _cl
                    break
                if "." in _cl:
                    _last = _cl.rsplit(".", 1)[-1].strip().strip("[]")
                    if _last and _last in keyset:
                        found = _last
                        break
                col = None
                for _v in (_c, "=" + str(_c).strip()):
                    # expr stored with formula marker (=get(...)) while GW
                    # carries the bare get(...) — try both spellings.
                    try:
                        col = _find_column_for_field(_v, columns)
                    except Exception:
                        col = None
                    if col is not None:
                        break
                if col is not None:
                    _al = str(getattr(col, "alias", "") or getattr(col, "name", "") or "").strip().lower()
                    if _al and _al in keyset:
                        found = _al
                        break
            if found is None and _mmap:
                # remote-merged columns: bind via spec field->alias
                _probes = []
                if refkey:
                    _probes.append(str(refkey).strip().lower())
                    if str(refkey).startswith("@"):
                        _probes.append(str(refkey)[1:].strip().lower())
                if raw:
                    _g0 = str(raw).strip()
                    if _g0.lower().startswith("get(") and _g0.endswith(")"):
                        _g0 = _g0[4:-1].strip()
                    for _pp in _g0.split("."):
                        _pp = _pp.strip().lower()
                        if _pp:
                            _probes.append(_pp)
                for _pr in _probes:
                    if _pr in _mmap and _mmap[_pr] in keyset:
                        found = _mmap[_pr]
                        break
            cache[ck] = found
            return found

        def _get(row_low, refkey, raw):
            a = _alias(refkey, raw)
            if not a:
                return None
            try:
                return row_low.get(a)
            except Exception:
                return None

        return _get, _alias

    def _gw_pred_unbound(self, pred, keyset, columns, merges=None):
        """First leaf ref that cannot bind to a report row key, or ''."""
        try:
            _get, _alias = self._gw_make_row_getter(columns, keyset, merges)
        except Exception:
            return "?"
        for _rk, _raw in self._gw_walk_leaves(pred):
            try:
                if _alias(_rk, _raw) is None:
                    return str(_raw or _rk or "?")[:80]
            except Exception:
                return str(_raw or _rk or "?")[:80]
        return ""

    def _reject_deferred(self, plan, where):
        """Loud guard for flows that cannot apply deferred (post-merge) GW."""
        _d = (plan or {}).get("gw_deferred") or []
        if _d:
            secs = sorted({str(d.get("sec") or "") for d in _d if isinstance(d, dict)})
            raise ValueError(
                f'الشرط العام على ({", ".join(secs)}) من اتصال آخر {where} غير مدعوم — '
                f'نفّذ التقرير بالمسار العادي.')

    def _apply_remote_filters(self, filters, plan):
        """Rewrite filters touching remote tables into base-key IN lists.

        Also consumes plan["gw_remote"] (general_where conjuncts extracted
        at plan time) so cross-DB general conditions filter identically.
        """
        try:
            _gwf = list((plan or {}).get("gw_remote") or [])
        except Exception:
            _gwf = []
        filters = list(filters or []) + _gwf
        remote = plan.get("remote") or {}
        if not remote:
            return list(filters or [])
        base_norm = plan["base_norm"]
        field_table = self._field_table_map()
        out = []
        for f in (filters or []):
            if isinstance(f, dict) and f.get("_gw_deferred"):
                continue  # deferred GW never renders to SQL (post-merge only)
            members = [sf for sf in f.get("any", []) if isinstance(sf, dict)] \
                if isinstance(f, dict) and isinstance(f.get("any"), (list, tuple)) else [f]
            if not members:
                continue
            # tables touched (excluding merged-alias internals already NULLed)
            per_sec: Dict[str, list] = {}
            plain: list = []
            mixed_base = False
            for m in members:
                sec, _ = self._remote_field_of_filter(m, plan)
                if sec is None:
                    # base/local member?
                    ts = set()
                    fld = str(m.get("field") or m.get("column") or m.get("name") or "")
                    for r in self._refs_in_text(fld, field_table):
                        ts.add(field_table[r])
                    try:
                        col = _find_column_for_field(fld, plan.get("columns") or self.columns)
                    except Exception:
                        col = None
                    if col is not None:
                        for r in self._refs_in_text(getattr(col, "expr", "") or "", field_table):
                            ts.add(field_table[r])
                    if ts - {base_norm} - set(plan.get("local_sec") or []):
                        raise ValueError(
                            f'لا يمكن دمج فلتر واحد عبر اتصالين ({fld}) — افصل الشروط.')
                    plain.append(m)
                else:
                    per_sec.setdefault(sec, []).append(m)
            if len(per_sec) > 1 or (per_sec and plain and isinstance(f, dict) and isinstance(f.get("any"), (list, tuple))):
                # any-group spanning remote+base or two remotes -> unsupported
                if len(per_sec) > 1:
                    raise ValueError('لا يمكن دمج مجموعة "أو" عبر اتصالين — افصل الشروط.')
                # single remote + base members inside one OR group -> unsupported
                raise ValueError('لا يمكن دمج مجموعة "أو" بين جدولين — افصل الشروط.')
            if not per_sec:
                out.append(f)
                continue
            sec, members_r = next(iter(per_sec.items()))
            info = remote[sec]
            _fms = self._link_match_spec(base_norm, sec)
            if _fms["match"] != "exact":
                raise ValueError(
                    'الفلترة على الجدول "' + sec + '" المربوط ' + _fms["match"] +
                    ' غير مدعومة — اعرض العمود ثم رشّح، أو استخدم ربطاً تاماً.')
            # map members to underlying remote fields (original case)
            mapped = []
            for m in members_r:
                _, sfield = self._remote_field_of_filter(m, plan)
                nm = dict(m)
                nm["field"] = self._orig_col(sec, sfield, info["db"], info["schema"]) if sfield else nm.get("field")
                mapped.append(nm)
            rcols = self._table_columns(sec, info["schema"], info["db"])
            if isinstance(f, dict) and isinstance(f.get("any"), (list, tuple)):
                _w_in = [{"any": mapped}]
            elif isinstance(f, dict) and isinstance(f.get("all"), (list, tuple)):
                _w_in = [{"all": mapped}]
            else:
                _w_in = mapped
            wc, wp = self._where_for_table(_w_in, sec, rcols, info["db"], info["schema"])
            if wc is None:
                raise ValueError(
                    f'تعذر ترجمة الفلتر على الجدول البعيد "{sec}" — تحقق من أسماء الأعمدة.')
            bcol, scol = info["key"]
            _scol = self._orig_col(sec, scol, info["db"], info["schema"])
            rdb = info["db"]
            cur = self._exec_on(rdb, f'SELECT DISTINCT {_q(_scol)} FROM {self._remote_from(info)} WHERE {wc[len(" WHERE "):]}', wp)
            try:
                keys = [r[0] for r in cur.fetchall() if r[0] is not None]
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
            # base key field name for the IN list (original case)
            bfield = None
            for fld in (getattr(self, "fields", []) or []):
                if self._norm_table(getattr(fld, "table_source", "") or "") == base_norm \
                        and str(getattr(fld, "name", "")).upper() == bcol.upper():
                    bfield = getattr(fld, "name")
                    break
            if bfield is None:
                bfield = self._orig_col(base_norm, bcol, plan.get("base_db"), plan.get("base_schema"))
            out.append({"field": bfield or bcol, "op": "in", "value": keys})
        return out

    def _remote_from(self, info) -> str:
        try:
            _t = info.get("table") if isinstance(info, dict) else None
            _tmp = self._staged_temp_of(_t) if _t else None
            if _tmp:
                return _q(_tmp)
        except Exception:
            pass
        try:
            _t = info.get("table") if isinstance(info, dict) else None
            _im = getattr(self, "_iot_mirror", None) or {}
            _in = self._norm_table(_t) if _t else ""
            if _in and _in in _im:
                _msch = str(_im[_in].get("schema") or "")
                return f"{(_q(_msch) + '.') if _msch else ''}{_q(str(_im[_in].get('table') or _t))}"
        except Exception:
            pass
        sch_q = _q(info["schema"]) if info.get("schema") else ""
        _t = info.get("disp") or self._orig_col(info["table"], info["table"], info.get("db"), info.get("schema"))
        return f"{sch_q + '.' if sch_q else ''}{_q(_t)}"

    def _fuzzy_inner_field(self, spec, sec_norm: str) -> str:
        """Plain remote field behind a fuzzy-agg inner (else loud error)."""
        inner = str(spec.get("inner") or "").strip()
        if inner == "*":
            return "*"
        cand = inner.strip()
        for _w in ("[]", "{}", '""'):
            if len(cand) >= 2 and cand[0] == _w[0] and cand[-1] == _w[1]:
                cand = cand[1:-1].strip()
                break
        for f in (getattr(self, "fields", []) or []):
            if self._norm_table(getattr(f, "table_source", "") or "") == sec_norm \
                    and str(getattr(f, "name", "") or "").lower() == cand.lower():
                return str(getattr(f, "name"))
        raise ValueError(
            f'التجميع "{spec.get("func")}" مع ربط {spec.get("match")} يتطلب حقلاً بعيداً صريحاً — '
            f'بسّط التعبير الداخلي ("{inner}").')

    @staticmethod
    def _rx_extract(rx, value):
        """Extract via a COMPILED pattern (group 1 or full match), else None."""
        if value is None or rx is None:
            return None
        try:
            m = rx.search(str(value))
        except Exception:
            return None
        if not m:
            return None
        try:
            if m.lastindex:
                return m.group(1)
            return m.group(0)
        except Exception:
            return None

    def _fuzzy_key_index(self, mode, rx, pairs):
        """Index over [(match_raw, out_raw)] stored spellings for fuzzy keys.

        regex: {extract(match): [out...]}; contains: alternation over
        distinct match texts + text -> [out...]. Lookup returns out raws.
        Built for DISTINCT key combos (round 1) so round 2 hauls only rows
        whose keys actually match some base row.
        """
        mode = str(mode or "exact").strip().lower()
        if mode == "regex":
            groups = {}
            for mraw, oraw in (pairs or []):
                if mraw is None or oraw is None:
                    continue
                try:
                    e = self._rx_extract(rx, mraw)
                except Exception:
                    e = None
                if e is None:
                    continue
                lst = groups.setdefault(e, [])
                if oraw not in lst:
                    lst.append(oraw)
            return {"kind": "regex", "groups": groups, "rx": rx}
        vals, byval = [], {}
        for mraw, oraw in (pairs or []):
            if mraw is None or oraw is None:
                continue
            ss = str(mraw)
            if not ss:
                continue
            if ss not in byval:
                byval[ss] = []
                vals.append(ss)
            if oraw not in byval[ss]:
                byval[ss].append(oraw)
        return {"kind": "contains", "alts": self._contains_alternations(vals),
                "byval": byval}

    def _fuzzy_lookup(self, index, base_norm):
        """[out_raw...] matching one normalized base value (dedup, ordered)."""
        out = []
        if not index or base_norm is None:
            return out
        try:
            if index.get("kind") == "regex":
                try:
                    e = self._rx_extract(index.get("rx"), base_norm)
                except Exception:
                    e = None
                if e is None:
                    return out
                return list((index.get("groups") or {}).get(e) or [])
            bs = str(base_norm)
            if not bs:
                return out
            byval = index.get("byval") or {}
            for rxc in (index.get("alts") or []):
                try:
                    m = rxc.search(bs)
                except Exception:
                    m = None
                if m:
                    for o in (byval.get(m.group(0)) or []):
                        if o not in out:
                            out.append(o)
                    return out
            # reverse direction only when tier 1 missed (parity, rare)
            for ss, lst in byval.items():
                if ss and bs in ss:
                    for o in lst:
                        if o not in out:
                            out.append(o)
                    return out
        except Exception:
            pass
        return out

    @staticmethod
    def _contains_alternations(sec_vals, chunk=2000):
        """Compiled literal-alternation regexes (multi-substring in C).

        Chunked so huge value lists stay compilable; order preserved so
        the first chunk wins like the old table-order scan.
        """
        import re as _re_c
        out, buf, buflen = [], [], 0
        for v in (sec_vals or []):
            try:
                e = _re_c.escape(v)
            except Exception:
                continue
            buf.append(e)
            buflen += len(e)
            if len(buf) >= chunk or buflen > 100000:
                try:
                    out.append(_re_c.compile("|".join(buf)))
                except Exception:
                    pass
                buf, buflen = [], 0
        if buf:
            try:
                out.append(_re_c.compile("|".join(buf)))
            except Exception:
                pass
        return out

    def _contains_index(self, frows, col):
        """(alts, all_by_val, norms) for one secondary column.

        alts: compiled alternations in scan order (longest-first twin is
        built lazily by mates); all_by_val: text -> [rows];
        norms: [(norm, row)] for the rare reverse-direction fallback.
        """
        vals, all_by_val, norms = [], {}, []
        for sr in (frows or []):
            try:
                nv = self._norm_key_value(sr.get(col))
            except Exception:
                continue
            if nv is None:
                continue
            ss = str(nv)
            if not ss:
                continue
            norms.append((nv, sr))
            if ss not in all_by_val:
                all_by_val[ss] = []
                vals.append(ss)
            all_by_val[ss].append(sr)
        return (self._contains_alternations(vals), all_by_val, norms)

    @staticmethod
    def _contains_hit_fast(bv, index):
        """First secondary row containing bv (or contained in it).

        Tier 1: one C scan per base row over the alternation (secondary
        inside base — the sane direction). Tier 2 (no tier-1 hit only):
        reverse scan preserving the old symmetric semantics.
        """
        if bv is None or not index:
            return None
        bs = str(bv)
        if not bs:
            return None
        alts, all_by_val, norms = index
        for rxc in (alts or []):
            try:
                m = rxc.search(bs)
            except Exception:
                m = None
            if m:
                lst = (all_by_val or {}).get(m.group(0))
                if lst:
                    return lst[0]
                break
        for sv, sr in (norms or []):
            if sv is None:
                continue
            ss = str(sv)
            if ss and bs in ss:
                return sr
        return None

    def _contains_mates_fast(self, bv, index):
        """All secondary rows containing bv (or contained in it)."""
        if bv is None or not index:
            return []
        bs = str(bv)
        if not bs:
            return []
        alts, all_by_val, norms = index
        out, seen = [], set()
        for rxc in (alts or []):
            try:
                it = rxc.finditer(bs)
            except Exception:
                continue
            for m in it:
                for sr in ((all_by_val or {}).get(m.group(0)) or []):
                    if id(sr) not in seen:
                        seen.add(id(sr))
                        out.append(sr)
        if not out:
            for sv, sr in (norms or []):
                if sv is None:
                    continue
                ss = str(sv)
                if ss and bs in ss and id(sr) not in seen:
                    seen.add(id(sr))
                    out.append(sr)
        return out

    @staticmethod
    def _fuzzy_num(v):
        """Numeric coercion for fuzzy aggregates (non-numeric ignored)."""
        if v is None or isinstance(v, bool):
            return None
        try:
            if isinstance(v, (int, float)):
                return v
            s = str(v).strip().replace(",", "").replace(" ", "")
            if not s:
                return None
            return float(s) if ("." in s or "e" in s.lower()) else int(s)
        except Exception:
            return None

    def _fuzzy_agg_value(self, spec, sec_norm: str, mates) -> Any:
        """Aggregate over fuzzy-matched secondary rows (mirrors SQL defaults)."""
        func = str(spec.get("func") or "").upper()
        inner = self._fuzzy_inner_field(spec, sec_norm)
        if func == "COUNT":
            if inner == "*":
                return len(mates)
            return sum(1 for m in mates if m.get(inner) is not None)
        vals = [self._fuzzy_num(m.get(inner)) for m in mates]
        vals = [v for v in vals if v is not None]
        if func == "SUM":
            return sum(vals) if vals else 0
        if func == "AVG":
            return (sum(vals) / len(vals)) if vals else 0
        if func == "MIN":
            return min(vals) if vals else None
        if func == "MAX":
            return max(vals) if vals else None
        return None

    @staticmethod
    def _text_keys(keys):
        """(use_cast, params) for remote key IN-lists.

        Text-compare (CAST(col AS TEXT) + str params) kills the
        `character varying = bigint` class when int keys meet varchar
        columns. Temporal/bool values bypass the cast (their text forms
        differ per dialect) and compare natively as before.
        """
        import datetime as _dt
        vals = list(keys or [])
        for v in vals:
            if v is None:
                continue
            if isinstance(v, (bool, _dt.datetime, _dt.date, _dt.time)):
                return False, vals
        return True, ["" if v is None else str(v) for v in vals]

    @staticmethod
    def _text_cast(col_q, rdb) -> str:
        """CAST(col AS text-ish) so int base keys compare with varchar keys.

        Kills the whole `character varying = bigint` class on remote key
        comparisons regardless of which side declared which type.
        """
        try:
            if _is_mssql_db(rdb):
                return f"CAST({col_q} AS NVARCHAR(4000))"
        except Exception:
            pass
        try:
            if _is_pg_db(rdb):
                return f"CAST({col_q} AS TEXT)"
        except Exception:
            pass
        try:
            _tn = type(rdb).__name__.lower()
            if "pgshim" in _tn or _tn.startswith("pg"):
                return f"CAST({col_q} AS TEXT)"
        except Exception:
            pass
        return f"CAST({col_q} AS VARCHAR2(4000))"

    def _fuzzy_pushdown_fetch(self, specs, sec, scol, rows, rdb, info, qfn, need, rx, mode):
        """Server-side fuzzy pre-filter; returns full rows or None (fallback).

        Instead of hauling every secondary row for Python matching (fatal on
        million-row tables), push the predicate to the secondary DB so only
        candidate rows travel:
        - regex on pg: WHERE substring(scol FROM 'pat') IN (base extracts);
          oracle: REGEXP_SUBSTR equivalent. MSSQL has no regex engine and
          POSIX lookahead ((?=/(?!) is rejected by pg too -> None (haul).
        - contains on pg/mssql/oracle: WHERE POSITION/CHARINDEX/INSTR(col
          IN literal) over base-side values (chunked).
        Base-side values come from the page rows only, so the candidate set
        stays tiny. Returns None when pushdown cannot apply.
        """
        try:
            pg = bool(_is_pg_db(rdb))
        except Exception:
            pg = False
        try:
            ms = bool(_is_mssql_db(rdb))
        except Exception:
            ms = False
        if not pg and not ms:
            try:
                _tn = type(rdb).__name__.lower()
                if "pgshim" in _tn or _tn.startswith("pg"):
                    pg = True
            except Exception:
                pass
        mode = str(mode or "exact").strip().lower()
        if mode not in ("regex", "contains"):
            return None
        # base-side distinct norms per role
        _keys, _anchors = [], {}
        for s in (specs or []):
            try:
                if s.get("kind") == "direct":
                    for r in (rows or []):
                        try:
                            _v = self._norm_key_value(r.get(s.get("key_alias")))
                        except Exception:
                            _v = None
                        if _v is not None and _v not in _keys:
                            _keys.append(_v)
                else:
                    _ac = s.get("anchor_sec")
                    _lst = _anchors.setdefault(_ac, [])
                    for r in (rows or []):
                        try:
                            _v = self._norm_key_value(r.get(s.get("anchor_alias")))
                        except Exception:
                            _v = None
                        if _v is not None and _v not in _lst:
                            _lst.append(_v)
            except Exception:
                continue
        if not _keys and not any(_anchors.values()):
            return []
        _rfrom = self._remote_from(info)
        _fcols = ", ".join([qfn(c) for c in (need or [])])
        _out = []

        def _fetch(where, params):
            _cur = self._exec_on(rdb, f"SELECT {_fcols} FROM {_rfrom} WHERE {where}", params or {})
            try:
                _out.extend(dict(zip((need or []), rec)) for rec in _cur.fetchall())
            finally:
                try:
                    _cur.close()
                except Exception:
                    pass

        if mode == "regex":
            if ms:
                return None
            try:
                _pat = str((specs[0].get("pattern") if specs else "") or "")
            except Exception:
                _pat = ""
            if not _pat:
                return None
            if "(?=" in _pat or "(?!" in _pat or "(?<" in _pat:
                return None  # POSIX engine rejects lookaround -> haul fallback
            _pq = _pat.replace("'", "''")
            if pg:
                _ext = lambda _c: f"substring({_c} FROM '{_pq}')"
            else:
                _ext = lambda _c: f"REGEXP_SUBSTR({_c}, '{_pq}', 1, 1, NULL, 1)"
            _conds, _prm, _i = [], {}, 0
            _exts_done = set()

            def _add_ext(col, vals):
                nonlocal _i
                _es = []
                for _v in (vals or []):
                    try:
                        _e = self._rx_extract(rx, _v)
                    except Exception:
                        _e = None
                    if _e is None or _e in _exts_done:
                        continue
                    _exts_done.add(_e)
                    _es.append(_e)
                if _es:
                    _phs = ", ".join([f":fe{_i + j}" for j in range(len(_es))])
                    for _j, _e in enumerate(_es):
                        _prm[f"fe{_i + _j}"] = str(_e)
                    _i += len(_es)
                    _conds.append(f"{_ext(self._text_cast(qfn(col), rdb))} IN ({_phs})")

            _add_ext(scol, _keys)
            for _ac, _vals in _anchors.items():
                if _ac and _ac != scol:
                    _add_ext(_ac, _vals)
            if not _conds:
                return []
            try:
                _fetch(" OR ".join(f"({_c})" for _c in _conds), _prm)
            except Exception:
                return None  # e.g. pattern rejected server-side -> haul fallback
            return _out

        # contains: base-side literals searched server-side (one scan per
        # chunk; chunks are wide so a normal page resolves in ONE query)
        _lits = []
        for _v in list(_keys or []) + [x for _lst in _anchors.values() for x in _lst]:
            try:
                _s = str(_v)
            except Exception:
                continue
            if _s and _s not in _lits:
                _lits.append(_s)
        if not _lits:
            return []
        _cols = [scol] + [a for a in _anchors if a and a != scol]
        try:
            for _ch in self._chunk(_lits, 2000):
                _conds, _prm, _i = [], {}, 0
                for _lit in _ch:
                    for _c in _cols:
                        _qc = self._text_cast(qfn(_c), rdb)
                        if pg:
                            _conds.append(f"POSITION({_qc} IN CAST(:fc{_i} AS TEXT)) > 0")
                        elif ms:
                            _conds.append(f"CHARINDEX({_qc}, CAST(:fc{_i} AS NVARCHAR(4000))) > 0")
                        else:
                            _conds.append(f"INSTR(CAST(:fc{_i} AS VARCHAR2(4000)), {_qc}) > 0")
                        _prm[f"fc{_i}"] = str(_lit)
                        _i += 1
                _fetch(" OR ".join(_conds), _prm)
        except Exception:
            return None
        return _out

    def _apply_merges(self, rows, plan):
        """Fill remote-merge columns (post-fetch, pre-format). Strips helper keys."""
        merges = plan.get("merges") or []
        strip = set(plan.get("strip") or set())
        if not merges and not strip:
            return rows
        remote = plan.get("remote") or {}
        base_norm = plan.get("base_norm")
        # group specs by remote table
        by_table: Dict[str, list] = {}
        for m in merges:
            by_table.setdefault(m["table"], []).append(m)
        cache: Dict[str, Any] = {}
        for sec, specs in by_table.items():
            info = remote.get(sec)
            if not info:
                continue
            rdb = info["db"]
            bcol, scol = info["key"]

            def _Q(name):
                return _q(self._orig_col(sec, name, rdb, info.get("schema")))

            _fmode = str((specs[0].get("match") if specs else None) or "exact").strip().lower()
            if _fmode != "exact":
                # Fuzzy link (contains/regex): one full secondary scan, then
                # indexed matching — O(secondary + base) instead of
                # O(base x secondary) regex evaluations per pair.
                # regex: group secondary rows by extract(scol) once; each base
                # row extracts once + dict lookup. First match wins (direct);
                # aggregates run over the matched subset in Python.
                # contains: secondary norms precomputed once; substring test
                # per base row (C-speed, no regex).
                import re as _re_fz
                _fpat = str(specs[0].get("pattern") or "")
                _rx = None
                if _fmode == "regex":
                    try:
                        _rx = _re_fz.compile(_fpat)
                    except Exception as _rxe:
                        raise ValueError(
                            f'نمط regex غير صالح في الربط مع "{sec}": {_rxe}')
                _need = {scol}
                for s in specs:
                    if s.get("kind") == "direct":
                        _need.add(s["field"])
                    else:
                        _need.add(s["anchor_sec"])
                        _need.add(self._fuzzy_inner_field(s, sec))
                _need = sorted(_need)
                _rfrom = self._remote_from(info)
                # Pushdown first: let the secondary DB filter candidates
                # (vital on million-row tables). Falls back to key haul.
                try:
                    _pd = self._fuzzy_pushdown_fetch(specs, sec, scol, rows, rdb, info,
                                                     _Q, list(_need), _rx, _fmode)
                except Exception:
                    _pd = None
                if _pd is not None:
                    _frows = list(_pd)
                    try:
                        self._merge_stats[str(sec)] = {"pushdown": True,
                                                       "fetched": len(_frows)}
                    except Exception:
                        pass
                    _pushdown_hit = True
                else:
                    _pushdown_hit = False
                if not _pushdown_hit:
                    # Round 1 (narrow): DISTINCT key combos only — never haul
                    # the whole wide table when a subset matches.
                    _anchor_cols = sorted({s["anchor_sec"] for s in specs if s.get("kind") != "direct"})
                    _key_cols = [scol] + [a for a in _anchor_cols if a != scol]
                    _kcols = ", ".join([_Q(c) for c in _key_cols])
                    _kcur = self._exec_on(rdb, f"SELECT DISTINCT {_kcols} FROM {_rfrom}", {})
                    try:
                        _krows = [dict(zip(_key_cols, rec)) for rec in _kcur.fetchall()]
                    finally:
                        try:
                            _kcur.close()
                        except Exception:
                            pass
                    try:
                        _kid_scol = self._fuzzy_key_index(
                            _fmode, _rx, [(kr.get(scol), kr.get(scol)) for kr in _krows])
                        _kid_anchor = {}
                        for _ac0 in _anchor_cols:
                            _kid_anchor[_ac0] = self._fuzzy_key_index(
                                _fmode, _rx, [(kr.get(_ac0), kr.get(scol)) for kr in _krows])
                        _matched_raws = []
                        for r in rows:
                            for s in specs:
                                try:
                                    if s.get("kind") == "direct":
                                        _rr0 = self._fuzzy_lookup(
                                            _kid_scol, self._norm_key_value(r.get(s.get("key_alias"))))
                                    else:
                                        _rr0 = self._fuzzy_lookup(
                                            _kid_anchor.get(s["anchor_sec"]),
                                            self._norm_key_value(r.get(s.get("anchor_alias"))))
                                except Exception:
                                    _rr0 = []
                                for _o in (_rr0 or []):
                                    if _o not in _matched_raws:
                                        _matched_raws.append(_o)
                    except Exception:
                        _matched_raws = []
                    try:
                        _all_raws = {kr.get(scol) for kr in _krows}
                    except Exception:
                        _all_raws = set()
                    try:
                        self._merge_stats[str(sec)] = {"keys": len(_all_raws),
                                                       "matched": len(_matched_raws)}
                    except Exception:
                        pass
                    # Round 2: full rows for matched keys only (chunked IN);
                    # empty match set skips the haul entirely.
                    _frows = []
                    if _matched_raws:
                        _fcols = ", ".join([_Q(c) for c in _need])
                        if len(_matched_raws) < len(_all_raws):
                            for _ch in self._chunk(list(_matched_raws), 500):
                                _phs = ", ".join([f":mk{i}" for i in range(len(_ch))])
                                _cast, _vals = self._text_keys(_ch)
                                _prm = {f"mk{i}": _v for i, _v in enumerate(_vals)}
                                _sc = self._text_cast(_Q(scol), rdb) if _cast else _Q(scol)
                                _fcur = self._exec_on(
                                    rdb, f"SELECT {_fcols} FROM {_rfrom} WHERE {_sc} IN ({_phs})", _prm)
                                try:
                                    _frows.extend(dict(zip(_need, rec)) for rec in _fcur.fetchall())
                                finally:
                                    try:
                                        _fcur.close()
                                    except Exception:
                                        pass
                        else:
                            _fcur = self._exec_on(rdb, f"SELECT {_fcols} FROM {_rfrom}", {})
                            try:
                                _frows = [dict(zip(_need, rec)) for rec in _fcur.fetchall()]
                            finally:
                                try:
                                    _fcur.close()
                                except Exception:
                                    pass
                # secondary index: norms (+ regex extracts) computed ONCE
                _sec_norms = []  # [(norm, row)]
                _sec_groups = {}  # col -> {extract: [rows]} (regex only)
                for _sr in _frows:
                    try:
                        _sec_norms.append((self._norm_key_value(_sr.get(scol)), _sr))
                    except Exception:
                        continue
                # contains: one compiled literal-alternation per column =
                # C-speed multi-substring search instead of a Python
                # base x secondary nested loop.
                _contains_idx = {}  # col -> (alts, all_by_val, norms)
                if _fmode == "contains":
                    _contains_idx[scol] = self._contains_index(_frows, scol)
                if _fmode == "regex":
                    _g = {}
                    for _nv, _sr in _sec_norms:
                        try:
                            _e = self._rx_extract(_rx, _nv)
                        except Exception:
                            _e = None
                        if _e is not None:
                            _g.setdefault(_e, []).append(_sr)
                    _sec_groups[scol] = _g
                    for s in specs:
                        if s.get("kind") != "direct":
                            _ac = s["anchor_sec"]
                            if _ac not in _sec_groups:
                                _ga = {}
                                for _sr in _frows:
                                    try:
                                        _av2 = self._norm_key_value(_sr.get(_ac))
                                        _e2 = self._rx_extract(_rx, _av2)
                                    except Exception:
                                        _e2 = None
                                    if _e2 is not None:
                                        _ga.setdefault(_e2, []).append(_sr)
                                _sec_groups[_ac] = _ga
                for r in rows:
                    for s in specs:
                        if s.get("kind") == "direct":
                            _bv = self._norm_key_value(r.get(s.get("key_alias")))
                            _hit = None
                            if _bv is not None:
                                if _fmode == "regex":
                                    try:
                                        _be = self._rx_extract(_rx, _bv)
                                    except Exception:
                                        _be = None
                                    _lst = _sec_groups.get(scol, {}).get(_be) if _be is not None else None
                                    _hit = _lst[0] if _lst else None
                                else:
                                    _hit = self._contains_hit_fast(_bv, _contains_idx.get(scol))
                            r[s["alias"]] = (_hit.get(s["field"]) if _hit is not None else None)
                        else:
                            _av = self._norm_key_value(r.get(s.get("anchor_alias")))
                            _mates = []
                            if _av is not None:
                                if _fmode == "regex":
                                    try:
                                        _ae = self._rx_extract(_rx, _av)
                                    except Exception:
                                        _ae = None
                                    if _ae is not None:
                                        _mates = list(_sec_groups.get(s["anchor_sec"], {}).get(_ae) or [])
                                else:
                                    _ac2 = s["anchor_sec"]
                                    if _ac2 not in _contains_idx:
                                        _contains_idx[_ac2] = self._contains_index(_frows, _ac2)
                                    _mates = self._contains_mates_fast(_av, _contains_idx.get(_ac2))
                            r[s["alias"]] = self._fuzzy_agg_value(s, sec, _mates)
                continue

            key_alias = next((s.get("key_alias") for s in specs if s.get("key_alias")), None)
            direct_fields = sorted({s["field"] for s in specs if s["kind"] == "direct"})
            agg_specs = [s for s in specs if s["kind"] == "agg"]
            # NOTE: raw key values were normalized with _norm_key_value; normalize fetched too
            if direct_fields and key_alias:
                base_keys = []
                for r in rows:
                    v = self._norm_key_value(r.get(key_alias))
                    if v is not None and v not in base_keys:
                        base_keys.append(v)
                fmap: Dict[Any, dict] = {}
                if base_keys:
                    cols_sql = ", ".join([_Q(scol)] + [_Q(c) for c in direct_fields])
                    for chunk in self._chunk(base_keys):
                        phs = ", ".join([f":rk{i}" for i in range(len(chunk))])
                        _cast, _vals = self._text_keys(chunk)
                        prm = {f"rk{i}": v for i, v in enumerate(_vals)}
                        _sc = self._text_cast(_Q(scol), rdb) if _cast else _Q(scol)
                        cur = self._exec_on(rdb, f"SELECT {cols_sql} FROM {self._remote_from(info)} WHERE {_sc} IN ({phs})", prm)
                        try:
                            for rec in cur.fetchall():
                                fmap[self._norm_key_value(rec[0])] = rec[1:]
                        finally:
                            try:
                                cur.close()
                            except Exception:
                                pass
                for r in rows:
                    hit = fmap.get(self._norm_key_value(r.get(key_alias)))
                    for s in specs:
                        if s["kind"] != "direct":
                            continue
                        idx = direct_fields.index(s["field"])
                        r[s["alias"]] = hit[idx] if hit is not None else None
            else:
                for s in specs:
                    if s["kind"] == "direct":
                        r_alias = s["alias"]
                        for r in rows:
                            r[r_alias] = None
            for s in agg_specs:
                ds = self._orig_col(sec, s["anchor_sec"], rdb, info.get("schema"))
                # anchor values from rows
                avals = []
                for r in rows:
                    v = self._norm_key_value(r.get(s["anchor_alias"]))
                    if v is not None and v not in avals:
                        avals.append(v)
                amap: Dict[Any, Any] = {}
                if avals:
                    inner_sql = re.sub(r"[\[{]([^\].\[{}]+)[\]}]", lambda m: _Q(m.group(1)), s["inner"])
                    # bare tokens of remote fields -> quoted original case,
                    # OUTSIDE quoted spans only (never re-wrap "T"."COL")
                    fnames = [str(getattr(f, "name", "")) for f in (getattr(self, "fields", []) or [])
                              if self._norm_table(getattr(f, "table_source", "") or "") == s["table"] and getattr(f, "name", None)]
                    if fnames:
                        alt = "|".join(sorted(set(re.escape(x) for x in fnames), key=len, reverse=True))
                        _parts = re.split(r'("[^"]*")', inner_sql)
                        for _qi in range(0, len(_parts), 2):
                            _parts[_qi] = re.sub(r"(?<!\.)\b(" + alt + r")\b(?!\.)",
                                                 lambda m: _Q(m.group(1)), _parts[_qi], flags=re.IGNORECASE)
                        inner_sql = "".join(_parts)
                    # already-quoted idents: normalize to original case
                    def _req(m):
                        _nm = m.group(1)
                        return _Q(_nm)
                    _qp = re.split(r'("[^"]*")', inner_sql)
                    for _qi in range(1, len(_qp), 2):
                        _im = re.fullmatch(r'"([A-Za-z_][A-Za-z0-9_]*)"', _qp[_qi])
                        if _im:
                            _qp[_qi] = _Q(_im.group(1))
                    inner_sql = "".join(_qp)
                    func = s["func"]
                    agg = f"{func}({inner_sql})"
                    for chunk in self._chunk(avals):
                        phs = ", ".join([f":ak{i}" for i in range(len(chunk))])
                        _cast, _vals = self._text_keys(chunk)
                        prm = {f"ak{i}": v for i, v in enumerate(_vals)}
                        _dc = self._text_cast(_Q(ds), rdb) if _cast else _Q(ds)
                        cur = self._exec_on(
                            rdb, f"SELECT {_Q(ds)}, {agg} FROM {self._remote_from(info)} "
                                 f"WHERE {_dc} IN ({phs}) GROUP BY {_Q(ds)}", prm)
                        try:
                            for rec in cur.fetchall():
                                amap[self._norm_key_value(rec[0])] = rec[1]
                        finally:
                            try:
                                cur.close()
                            except Exception:
                                pass
                default = 0 if s["func"] in ("SUM", "AVG", "COUNT") else None
                for r in rows:
                    hit = amap.get(self._norm_key_value(r.get(s["anchor_alias"])))
                    r[s["alias"]] = hit if hit is not None else default
        for r in rows:
            for k in list(strip):
                r.pop(k, None)
        return rows

    def _exec_deferred_stream(self, cur, cols, exec_plan, strip_extra, page, page_size):
        """Stream base rows in chunks: merge + post-merge GW per chunk.

        The deferred path cannot paginate in SQL (the filter needs merged
        rows), so the full base set is scanned — but only the requested
        page is materialized; memory stays ~one chunk + one page.
        Returns (formatted_page_rows, total_matches). SQL WHERE semantics:
        a row is kept iff every deferred predicate evaluates True.
        """
        import time as _tmod
        _CH = 20000
        _t_rows = _tmod.time()
        _deferred = [d for d in (exec_plan.get("gw_deferred") or []) if isinstance(d, dict)]
        _pcols = exec_plan.get("columns") or []
        _exp_keys = {str(getattr(c, "alias", "") or getattr(c, "name", "") or "").lower()
                     for c in _pcols}
        _exp_keys |= {str(m.get("alias") or "").lower()
                      for m in (exec_plan.get("merges") or []) if m.get("alias")}
        _strip = {str(_k) for _k in (strip_extra or set())}
        _strip |= {str(_k).lower() for _k in (strip_extra or set())}
        _all = str(page_size).lower() == "all"
        try:
            _ps_n = None if _all else max(1, int(page_size))
            _p0 = 1 if _all else max(1, int(page or 1))
        except Exception:
            _ps_n, _p0 = None, 1
        _start = 0 if _all else (_p0 - 1) * _ps_n
        _end = None if _all else _start + _ps_n
        _get = None
        _allkeys = set(_exp_keys)
        _matched = 0
        _scanned = 0
        _out = []
        try:
            _tname = str(exec_plan.get("from_table") or "").lower()
        except Exception:
            _tname = ""
        while True:
            try:
                _batch = cur.fetchmany(_CH)
            except Exception:
                break
            if not _batch:
                break
            _rr = [dict(zip(cols, _r)) for _r in _batch]
            _scanned += len(_rr)
            _t_merge = _tmod.time()
            _mrows = self._apply_merges(_rr, exec_plan)
            try:
                self._timings["merge_ms"] = int(self._timings.get("merge_ms") or 0) + int((_tmod.time() - _t_merge) * 1000)
            except Exception:
                pass
            if _get is None:
                # First chunk: lock ref->alias bindings (loud when a GW ref
                # binds no report column — never silently drop rows).
                for _r0 in _mrows:
                    try:
                        _allkeys |= set(str(_k).lower() for _k in dict(_r0 or {}).keys())
                    except Exception:
                        pass
                _get, _al = self._gw_make_row_getter(
                    _pcols, _allkeys, exec_plan.get("merges"))
                for _dd in _deferred:
                    _miss = self._gw_pred_unbound(
                        _dd.get("pred"), _allkeys, _pcols, exec_plan.get("merges"))
                    if _miss:
                        raise ValueError(
                            f'تعذر ربط الشرط العام المؤجل ({_miss}) بأعمدة التقرير — '
                            f'اعرض العمود أولاً.')
            _t_gw = _tmod.time()
            for _r in _mrows:
                try:
                    _rl = {str(_k).lower(): _v for _k, _v in dict(_r or {}).items()}
                except Exception:
                    continue
                _lk = lambda _rk, _raw, _g=_get, _w=_rl: _g(_w, _rk, _raw)
                _okr = True
                for _dd in _deferred:
                    try:
                        _pv = self._gw_pred_eval(_dd.get("pred"), _lk)
                    except Exception:
                        _pv = None
                    if _pv is not True:
                        _okr = False
                        break
                if not _okr:
                    continue
                if _end is None or (_start <= _matched < _end):
                    if _strip:
                        for _k in _strip:
                            _r.pop(_k, None)
                    _out.append({k: _fmt_cell(v) for k, v in _r.items()})
                _matched += 1
            # free the chunk BEFORE the next fetch allocates (peak = 1 chunk)
            try:
                del _batch, _rr, _mrows
            except Exception:
                pass
            try:
                self._timings["gw_post_ms"] = int(self._timings.get("gw_post_ms") or 0) + int((_tmod.time() - _t_gw) * 1000)
            except Exception:
                pass
            try:
                self._report_progress({"stage": "rows", "table": _tname,
                                       "text": f"جلب وترشيح... {_scanned} (مطابق {_matched})",
                                       "rows": _scanned, "matched": _matched})
            except Exception:
                pass
        try:
            self._timings["rows_ms"] = int((_tmod.time() - _t_rows) * 1000)
            self._timings["rows_n"] = _scanned
        except Exception:
            pass
        return _out, _matched

    # ── Deferred-GW acceleration: E-driven semi-join pre-filter ──────────
    # A deferred condition lives on the (usually SMALL) remote table.  Fetch
    # the DISTINCT remote link-keys satisfying it (E, capped), then narrow
    # the base fetch server-side to rows that can possibly survive the
    # post-merge eval.  Post-merge eval stays the SOLE arbiter of exactness:
    # the wrapper is a proven superset (or absent), never a filter.
    _GW_PREFILTER_MAX_KEYS = 1000
    _GW_EKEY_FETCH_CAP = 5001
    _GW_EXTRACT_MAX_KEYS = 5000
    # regex patterns eligible for server-side trailing-run extraction
    # (normalized, no spaces). ONLY these shapes — anything else keeps the
    # exact full-scan path. They all mean "trailing digit run".
    _GW_TRAILING_PATTERNS = (r"(\d+)(?!.*\d)", r"([0-9]+)(?!.*[0-9])")

    @staticmethod
    def _gw_stringy(dtype) -> bool:
        """String-like RML field type? (textual pre-filter compares textually)."""
        t = str(dtype or "").upper()
        return any(k in t for k in ("CHAR", "TEXT", "CLOB", "STRING", "NVARCHAR", "VARCHAR", "NCHAR"))

    def _gw_base_field_type(self, base_norm, base_key) -> str:
        """RML field type for a base link-key field, or '' when unknown."""
        try:
            for f in (getattr(self, "fields", []) or []):
                if self._norm_table(getattr(f, "table_source", "") or "") != (base_norm or ""):
                    continue
                if str(getattr(f, "name", "") or "").upper() == str(base_key or "").upper():
                    return str(getattr(f, "type", "") or "")
        except Exception:
            pass
        return ""

    def _gw_entry_keys(self, entry, plan):
        """Usable remote key set for one deferred entry: (keys, had_null).

        (None, False) = not pushable/failed/too big -> caller full-scans.
        regex links: keys are PYTHON extracts (exact engine semantics, zero
        dialect divergence). had_null = a NULL raw key satisfied the cond
        (match-impotent, but forbids early-empty).
        """
        try:
            sec = entry.get("sec")
            flts = self._gw_pred_to_filters(entry.get("pred"))
            if not flts:
                return None, False
            remote = plan.get("remote") or {}
            info = remote.get(sec)
            if not info or not info.get("key"):
                return None, False
            base_norm = plan.get("base_norm")
            rdb = info.get("db")
            rcols = self._table_columns(sec, info.get("schema"), rdb)
            wc, wp = self._where_for_table(flts, sec, rcols, rdb, info.get("schema"))
            if not wc:
                return None, False
            _scol = self._orig_col(sec, info["key"][1], rdb, info.get("schema"))
            _rfrom = self._remote_from(info)
            try:
                _ms = bool(_is_mssql_db(rdb))
            except Exception:
                _ms = False
            try:
                _pg = bool(_is_pg_db(rdb))
            except Exception:
                _pg = False
            _n = int(self._GW_EKEY_FETCH_CAP)
            if _ms:
                _q1 = f"SELECT DISTINCT TOP {_n} {_q(_scol)} FROM {_rfrom}{wc}"
            elif _pg:
                _q1 = f"SELECT DISTINCT {_q(_scol)} FROM {_rfrom}{wc} LIMIT {_n}"
            else:
                _q1 = f"SELECT DISTINCT {_q(_scol)} FROM {_rfrom}{wc} FETCH FIRST {_n} ROWS ONLY"
            _cur = self._exec_on(rdb, _q1, dict(wp or {}))
            try:
                _raw = [_r[0] for _r in _cur.fetchall()]
            finally:
                try:
                    _cur.close()
                except Exception:
                    pass
            if len(_raw) >= _n:
                return None, False  # capped -> completeness unproven -> full scan
            _had_null = any(_v is None for _v in _raw)
            try:
                _mspec = self._link_match_spec(base_norm, sec)
            except Exception:
                _mspec = {"match": "exact", "pattern": ""}
            _match = str(_mspec.get("match") or "exact").strip().lower()
            if _match == "regex":
                try:
                    _rx = re.compile(str(_mspec.get("pattern") or ""))
                except Exception:
                    return None, False
                _outs, _seen = [], set()
                for _v in _raw:
                    if _v is None:
                        continue
                    try:
                        _ex = self._rx_extract(_rx, _v)
                    except Exception:
                        _ex = None
                    if _ex is None or _ex in _seen:
                        continue
                    _seen.add(_ex)
                    _outs.append(_ex)
                if len(_outs) > int(self._GW_EXTRACT_MAX_KEYS):
                    return None, False
                return _outs, _had_null
            _keys = [_v for _v in _raw if _v is not None]
            if len(_keys) > int(self._GW_PREFILTER_MAX_KEYS):
                return None, False
            return _keys, _had_null
        except Exception:
            return None, False

    def _gw_prefilter_wrap(self, sql, params, exec_plan):
        """(sql, params, applied, early_empty) with E-driven pre-filter.

        Rules per deferred entry (ANDed): usable E (pushable, complete,
        string-typed, expressible) narrows; empty E (+NULL-rejecting pred)
        empties the whole fetch; anything else stays with the post-merge
        arbiter. Any failure -> (sql, params, False, False).
        """
        import time as _tmod
        _t0 = _tmod.time()
        _skip_reason = [""]
        def _skip(reason):
            # first skip reason wins (tells WHY the narrow-down did not fire)
            try:
                if not _skip_reason[0]:
                    _skip_reason[0] = reason
                    self._timings["gw_prefilter_skip"] = reason
            except Exception:
                pass
        try:
            self._timings["gw_prefilter"] = False
            self._timings["gw_ekeys_n"] = -1
        except Exception:
            pass
        _entries = [d for d in (exec_plan.get("gw_deferred") or []) if isinstance(d, dict)]
        if not _entries:
            return sql, params, False, False
        try:
            self._report_progress({"stage": "gw_ekeys",
                                   "text": "قراءة مفاتيح الجدول البعيد المطابقة للشرط..."})
        except Exception:
            pass
        try:
            _bdb = exec_plan.get("base_db")
            _bms = bool(_is_mssql_db(_bdb))
        except Exception:
            _bms = False
        try:
            _bpg = bool(_is_pg_db(_bdb))
        except Exception:
            _bpg = False
        _ct = "NVARCHAR(4000)" if _bms else ("TEXT" if _bpg else "VARCHAR2(4000)")
        base_norm = exec_plan.get("base_norm")
        remote = exec_plan.get("remote") or {}
        _conds = []
        _binds = {}
        _seq = [0]

        def _np(val):
            _seq[0] += 1
            _k = f"dp{_seq[0]}"
            _binds[_k] = val
            return _k

        try:
            for _en in _entries:
                _sec = _en.get("sec")
                _pred = _en.get("pred")
                _nullkeeps = self._gw_pred_eval(_pred, lambda rk, raw: None) is True
                _keys, _had_null = self._gw_entry_keys(_en, exec_plan)
                if _keys is None:
                    _skip("ekeys")
                    continue
                if _had_null:
                    _skip("null-key")
                    continue
                try:
                    _n0 = len(_keys)
                    if int(self._timings.get("gw_ekeys_n") or -1) < _n0:
                        self._timings["gw_ekeys_n"] = _n0
                except Exception:
                    pass
                if not _keys:
                    if not _nullkeeps:
                        try:
                            self._timings["gw_ekeys_ms"] = int((_tmod.time() - _t0) * 1000)
                            self._timings["gw_ekeys_n"] = 0
                            self._timings["gw_early_empty"] = True
                        except Exception:
                            pass
                        return sql, params, False, True
                    _skip("null-keeps")
                    continue
                if _nullkeeps:
                    _skip("null-keeps")
                    continue
                if any(not isinstance(_v, str) for _v in _keys):
                    _skip("non-string-keys")
                    continue
                try:
                    _mspec = self._link_match_spec(base_norm, _sec)
                except Exception:
                    _skip("link-spec")
                    continue
                _match = str(_mspec.get("match") or "exact").strip().lower()
                if _match not in ("exact", "contains", "regex"):
                    _skip("bad-match")
                    continue
                _helpers = []
                for _sp in (exec_plan.get("merges") or []):
                    if _sp.get("table") != _sec:
                        continue
                    _hh = _sp.get("key_alias") if _sp.get("kind") == "direct" else _sp.get("anchor_alias")
                    _bk = _sp.get("base_key") if _sp.get("kind") == "direct" else _sp.get("anchor_base")
                    if _hh and _bk and all(_hh != h for h, _ in _helpers):
                        _helpers.append((_hh, _bk))
                if not _helpers:
                    _skip("no-helpers")
                    continue
                if any(not self._gw_stringy(self._gw_base_field_type(base_norm, _bk)) for _hh, _bk in _helpers):
                    _skip("non-string-base")
                    continue
                if _match == "contains" and len(_helpers) * len(_keys) > 200:
                    # OR-chain would be a plan/RAM monster (helpers x keys
                    # CHARINDEX pairs) -> exact full-scan path instead.
                    _skip("chain-too-wide")
                    continue
                _parts = []
                for _hh, _bk in _helpers:
                    _h = f'"t".{_q(_hh)}'
                    if _match == "exact":
                        _phs = ", ".join(f"CAST(:{_np(_v)} AS {_ct})" for _v in _keys)
                        _parts.append(f"CAST({_h} AS {_ct}) IN ({_phs})")
                    elif _match == "contains":
                        _ors = []
                        for _v in _keys:
                            _k = _np(_v)
                            if _bms:
                                _ors.append(f"(CHARINDEX(CAST(:{_k} AS {_ct}), CAST({_h} AS {_ct})) > 0 OR CHARINDEX(CAST({_h} AS {_ct}), CAST(:{_k} AS {_ct})) > 0)")
                            elif _bpg:
                                _ors.append(f"(POSITION(CAST(:{_k} AS {_ct}) IN CAST({_h} AS {_ct})) > 0 OR POSITION(CAST({_h} AS {_ct}) IN CAST(:{_k} AS {_ct})) > 0)")
                            else:
                                _ors.append(f"(INSTR(CAST({_h} AS {_ct}), CAST(:{_k} AS {_ct})) > 0 OR INSTR(CAST(:{_k} AS {_ct}), CAST({_h} AS {_ct})) > 0)")
                        _parts.append("(" + " OR ".join(_ors) + ")")
                    else:  # regex, trailing-run family ONLY: server-side
                        # extract + hash IN (ONE predicate per 1000 keys —
                        # never a CHARINDEX monster). Gates: whitelisted
                        # pattern shape + ASCII-only E (equivalence with the
                        # Python extract rests on exactly these two facts).
                        _praw = ""
                        try:
                            _praw = re.sub(r"\s+", "", str(_mspec.get("pattern") or ""))
                        except Exception:
                            _praw = ""
                        _ascii_e = False
                        try:
                            _ascii_e = bool(_keys) and all(
                                isinstance(_v, str) and re.fullmatch(r"[0-9]+", _v) for _v in _keys)
                        except Exception:
                            _ascii_e = False
                        if _praw not in self._GW_TRAILING_PATTERNS or not _ascii_e:
                            _skip("no-server-extract")
                            _parts = None
                            break
                        _ins = []
                        for _ci in range(0, len(_keys), 1000):
                            _ch = _keys[_ci:_ci + 1000]
                            _phs = ", ".join(f"CAST(:{_np(_v)} AS {_ct})" for _v in _ch)
                            if _bms:
                                # LAST digit-run anywhere (cut trailing
                                # non-digits, then take the run). RTRIM
                                # defeats LEN()'s trailing-space blindness;
                                # control chars need no guard (cut by digit).
                                _rh = f"RTRIM({_h})"
                                _d = f"PATINDEX('%[0-9]%', REVERSE({_rh}))"
                                _core = f"LEFT({_rh}, LEN({_rh}) - {_d} + 1)"
                                _ext = (f"CASE WHEN {_d} > 0 THEN RIGHT({_core}, "
                                        f"PATINDEX('%[^0-9]%', REVERSE({_core}) + 'X') - 1) END")
                                _ins.append(f"{_ext} IN ({_phs})")
                            elif _bpg:
                                _ext = f"substring({_h} FROM '([0-9]+)[^0-9]*$')"
                                _ins.append(f"{_ext} IN ({_phs})")
                            else:
                                _ext = f"REGEXP_SUBSTR({_h}, '([0-9]+)[^0-9]*$', 1, 1, NULL, 1)"
                                _ins.append(f"{_ext} IN ({_phs})")
                        _parts.append("(" + " OR ".join(_ins) + ")")
                if not _parts:
                    _skip("no-parts")
                    continue
                _conds.append("(" + " OR ".join(_parts) + ")")
        except Exception:
            _skip("exception")
            return sql, params, False, False
        try:
            self._timings["gw_ekeys_ms"] = int((_tmod.time() - _t0) * 1000)
        except Exception:
            pass
        if not _conds:
            _skip("no-cond")
            return sql, params, False, False
        _gsql = f"SELECT * FROM ({sql}) t WHERE " + " AND ".join(_conds)
        _gparams = dict(params or {})
        _gparams.update(_binds)
        # probe before the heavy stream (syntax/dialect/shape validation)
        try:
            _pc = self._exec_on(_bdb, f"SELECT 1 FROM ({_gsql}) t WHERE 1=0", dict(_gparams))
            try:
                _pc.fetchall()
            finally:
                try:
                    _pc.close()
                except Exception:
                    pass
        except Exception:
            _skip("probe-fail")
            return sql, params, False, False
        try:
            self._timings["gw_prefilter"] = True
            self._timings["gw_ekeys_n"] = _seq[0]
        except Exception:
            pass
        return _gsql, _gparams, True, False

    # ── SQL Compilation ─────────────────────────────────────────────────

    def _paginate_clause(self, page, page_size, params: Dict[str, Any], db=None) -> Tuple[str, Dict[str, Any]]:
        """Oracle OFFSET/FETCH vs Postgres LIMIT/OFFSET (mutates a params copy)."""
        params = dict(params)
        clause = ""
        if page_size != "all":
            try:
                ps = int(page_size)
                p = max(1, int(page))
                offset = (p - 1) * ps
                _db = db if db is not None else getattr(self, "db", None)
                _is_pg = "postgres" in type(_db).__name__.lower() if _db is not None else False
                if _is_pg:
                    clause = " LIMIT :lim OFFSET :off"
                else:
                    clause = " OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY"
                params["off"] = offset
                params["lim"] = ps
            except (ValueError, TypeError):
                pass
        return clause, params

    def _route_one(self, f: Dict[str, Any], columns) -> str:
        """Route a filter: 'base' (WHERE), 'outer' (alias wrapper) or 'core'.

        'core' = formatted (TO_CHAR) output compared numerically -> the numeric
        core is injected into the inner select and compared there.
        Group dicts {any:[...]}/{all:[...]} route by their worst sub-route.
        """
        _subs = (f.get("any") if isinstance(f.get("any"), (list, tuple))
                 else f.get("all") if isinstance(f.get("all"), (list, tuple)) else None)
        if _subs is not None:
            _routes = {self._route_one(sf, columns) for sf in _subs if isinstance(sf, dict)}
            if "core" in _routes:
                return "core"
            if "outer" in _routes:
                return "outer"
            return "base"
        fld = f.get("field") or f.get("column") or f.get("name") or ""
        col = _find_column_for_field(fld, columns)
        if col is None:
            return "base"
        raw = re.sub(r"(?i)^\s*DISTINCT\s+",
                     "", (getattr(col, "expr", None) or getattr(col, "name", None) or "").strip())
        if not re.search(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(|\bOVER\s*\(", raw, re.IGNORECASE):
            return "base"
        formatted = bool(re.search(r"\bTO_CHAR\s*\(", raw, re.IGNORECASE) or "||" in raw)
        op = (f.get("op") or "equals").lower()
        if formatted and op not in ("equals", "=", "==", "eq", "contains", "like", "ilike"):
            return "core"
        if re.search(r"\bOVER\s*\(", raw, re.IGNORECASE):
            vals = [f.get("value"), f.get("valFrom"), f.get("valTo")]
            if any(_is_date_str(v) for v in vals):
                return "base"  # partition-key path (indexed, exact)
        return "outer"

    def _split_filters(self, filters, columns) -> Tuple[list, list]:
        """Split filters into (base_where, outer_alias) lists."""
        base, outer = [], []
        for f in (filters or []):
            if isinstance(f, dict) and isinstance(f.get("any"), (list, tuple)):
                subs = [sf for sf in f["any"] if isinstance(sf, dict)]
                if not subs:
                    continue
                routes = {self._route_one(sf, columns) for sf in subs}
                if "core" in routes or "outer" in routes:
                    outer.append(f)
                else:
                    base.append(f)
            elif isinstance(f, dict) and isinstance(f.get("all"), (list, tuple)):
                subs = [sf for sf in f["all"] if isinstance(sf, dict)]
                if not subs:
                    continue
                routes = {self._route_one(sf, columns) for sf in subs}
                if "core" in routes or "outer" in routes:
                    outer.append(f)
                else:
                    base.append(f)
            else:
                (outer if self._route_one(f, columns) in ("outer", "core") else base).append(f)
        return base, outer

    def _core_expr(self, col, fields, table_map):
        """Numeric core SQL of a formatted (TO_CHAR) column for filtering.

        Returns resolved SQL or raises a clear Arabic error.
        """
        from .namespaces import _resolve_expression as _rx
        raw = re.sub(r"(?i)^\s*DISTINCT\s+",
                     "", (getattr(col, "expr", None) or getattr(col, "name", None) or "").strip())
        m = re.search(r"\bTO_CHAR\s*\(", raw, re.IGNORECASE)
        core_raw = None
        if m:
            parsed = self._split_agg_call(raw, m.start())
            if parsed:
                inner = parsed[0]
                depth = 0
                in_str = False
                cut = None
                for k, ch in enumerate(inner):
                    if ch == "'":
                        in_str = not in_str
                    elif not in_str:
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                        elif ch == "," and depth == 0:
                            cut = k
                            break
                core_raw = inner[:cut].strip() if cut is not None else inner.strip()
        if not core_raw:
            alias = getattr(col, "alias", None) or "العمود"
            raise ValueError(f'لا يمكن مقارنة العمود المنسق "{alias}" بهذه العملية — قارن بالمساواة/الاحتواء على النص المعروض.')
        out = _rx(core_raw, fields, {}, set(), table_map, self._conn_map(),
                    default_tables=self._rx_defaults())
        if re.search(r"\bTO_CHAR\s*\(", out, re.IGNORECASE) or "||" in out:
            alias = getattr(col, "alias", None) or "العمود"
            raise ValueError(f'لا يمكن مقارنة العمود المنسق "{alias}" بهذه العملية — قارن بالمساواة/الاحتواء على النص المعروض.')
        return out

    def _outer_plan(self, filters, sort, group_by, active_table):
        """Prepare outer-filter execution: (prep_cols, table_map, base, outer, extra, outer_cols)."""
        plan = self._plan_structure(active_table, filters, sort, group_by)
        self._reject_deferred(plan, "في هذا المسار")
        filters2 = self._apply_remote_filters(filters, plan)
        return self._outer_plan2(plan, filters2)

    def _outer_plan2(self, plan, filters):
        """Pure part of _outer_plan on already remote-resolved filters."""
        import dataclasses
        prep_cols = plan["columns"]
        table_map = plan["table_map"]
        fields = getattr(self, "fields", []) or []
        base_f, outer_f = self._split_filters(filters, prep_cols)
        extra = []
        synth = []

        def _mk(sf):
            fld = sf.get("field") or sf.get("column") or sf.get("name") or ""
            col = _find_column_for_field(fld, prep_cols)
            core_sql = self._core_expr(col, fields, table_map)
            alias = f"_flt{len(extra)}"
            extra.append((core_sql, alias))
            synth.append(dataclasses.replace(col, id=f"flt{len(extra)}", name=alias, alias=alias, expr=alias))
            return dict(sf, field=alias)

        out2 = []
        for f in outer_f:
            if isinstance(f, dict) and isinstance(f.get("any"), (list, tuple)):
                subs2 = [(_mk(sf) if (isinstance(sf, dict) and self._route_one(sf, prep_cols) == "core") else sf)
                         for sf in f["any"]]
                out2.append(dict(f, any=subs2))
            else:
                out2.append(_mk(f) if self._route_one(f, prep_cols) == "core" else f)
        return prep_cols, table_map, base_f, out2, extra, list(prep_cols) + synth

    def _compile_wrapped(self, filters, sort, page, page_size, group_by, active_table):
        """Compile data SQL, wrapping computed-column filters in an outer query.

        Returns (sql, params, strip_extra_aliases).
        """
        plan = self._plan_structure(active_table, filters, sort, group_by)
        filters2 = self._apply_remote_filters(filters, plan)
        _, _, base_f, outer_f, extra, outer_cols = self._outer_plan2(plan, filters2)
        sql, params = self._compile_sql2(plan, base_f, sort, 1 if outer_f else page,
                                         "all" if outer_f else page_size, group_by, extra)
        strip_extra = [a for _, a in extra]
        if not outer_f:
            return sql, params, strip_extra
        outer_where, outer_params = _build_outer_where(outer_f, outer_cols, rules=getattr(self, "rules", []))
        if not outer_where:
            return sql, params, strip_extra
        params = dict(params)
        params.update(outer_params)
        pag, params = self._paginate_clause(page, page_size, params, plan["base_db"])
        try:
            _ms_outer = _is_mssql_db(plan.get("base_db")) and bool((pag or "").strip())
        except Exception:
            _ms_outer = False
        if _ms_outer:
            # outer OFFSET needs its own ORDER BY on T-SQL
            return f"SELECT * FROM ({sql}) t WHERE {outer_where[len(' WHERE '):]} ORDER BY 1" + pag, params, strip_extra
        return f"SELECT * FROM ({sql}) t WHERE {outer_where[len(' WHERE '):]}" + pag, params, strip_extra

    def _report_distinct(self) -> bool:
        """صفوف مميزة فقط؟ — من <rpt_metadata distinct> (يُضبط من المصمم)."""
        try:
            comp = getattr(self, "compiler", None)
            if comp is None or not hasattr(comp, "rpt_metadata"):
                return False
            v = (comp.rpt_metadata() or {}).get("distinct", False)
            return v is True or str(v).strip().lower() in ("1", "true", "yes", "y")
        except Exception:
            return False

    def _distinct_on_keys(self, columns, fields, table_map, conn_map, alias_by_table=None):
        """تعبيرات SQL للأعمدة المعلّمة بمنع التكرار (DISTINCT ON) — postgres فقط.

        تُقبل الأعمدة المباشرة/المحسوبة فقط (تُتجاهل التجميعية وfk_lookup).
        """
        from .namespaces import _resolve_expression, build_registry
        keys = []
        for col in (columns or []):
            try:
                flag = getattr(col, "is_distinct", False)
            except Exception:
                flag = False
            if not flag:
                continue
            try:
                ctype = str(getattr(col, "col_type", "direct") or "direct")
            except Exception:
                ctype = "direct"
            if ctype not in ("direct", "computed"):
                continue
            raw = (getattr(col, "expr", None) or getattr(col, "name", None) or "").strip()
            if not raw:
                continue
            # مفاتيح DISTINCT ON قد تحوي $rule.var$ (يوم العمل) — وسّعها قبل الحل
            _rules = getattr(self, "rules", []) or []
            if _rules and "$" in raw:
                from .rulevars import expand_rule_vars as _expand_rv
                raw = _expand_rv(raw, _rules)
            try:
                reg = build_registry() if re.search(r"\[|[A-Za-z_]+\.[A-Za-z_]+", raw) else {}
                keys.append(_resolve_expression(raw, fields, reg, set(), table_map, conn_map,
                                                alias_by_table or _alias_by_table(fields, table_map),
                                                default_tables=self._rx_defaults()))
            except Exception:
                continue
        seen, out = set(), []
        for k in keys:
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def _plan_alias_map(self, plan) -> Dict[str, str]:
        """{NORM_TABLE: alias} reconstructed from a routing plan.

        Mirrors the aliases enumeration used at plan time
        ({s: T{i+1}} over sorted local_sec + base_alias), so qualified refs
        ([table.col]/[conn.table.col]) bind to their OWN table's alias even
        when the column name exists in several tables.
        """
        out: Dict[str, str] = {}
        try:
            if (plan or {}).get("base_alias") and (plan or {}).get("base_norm"):
                out[plan["base_norm"]] = plan["base_alias"]
            for i, s in enumerate((plan or {}).get("local_sec") or []):
                out.setdefault(s, f"T{i + 1}")
        except Exception:
            pass
        return out

    def _general_where_sql(self, table_map=None, conn_map=None, gw_text=None) -> str:
        """Report-level <general_where> condition resolved to SQL (ANDed into master WHERE).

        [field] refs (+ $rule.var$) allowed, resolved with the query's table
        aliases. `gw_text` (plan-split base text, remote conjuncts already
        extracted) wins over the raw compiler text. Returns '' when absent.
        Raises a clear Arabic error when invalid.
        """
        try:
            if gw_text is None:
                comp = getattr(self, "compiler", None)
                raw = ""
                if comp is not None and hasattr(comp, "general_where"):
                    raw = comp.general_where() or ""
            else:
                raw = gw_text or ""
            raw = str(raw).strip()
            if not raw:
                return ""
            # @Alias (computed columns) first — same expansion the plan saw,
            # so routing and compilation agree. Idempotent.
            try:
                raw = self._expand_at_aliases(raw)
            except ValueError:
                raise
            except Exception:
                pass
            # Aggregates/windowed exprs are illegal in WHERE (need HAVING or
            # the player's outer-wrap filters) — fail loudly, not at the DB.
            try:
                _gw_nolit = re.sub(r"('(?:[^']|'')*')", "''", raw)
                if re.search(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(|\bOVER\s*\(", _gw_nolit, re.IGNORECASE):
                    raise ValueError(
                        "الشرط العام لا يقبل أعمدة تجميعية — رشّح بها من فلاتر المشغل (المتقدمة).")
            except ValueError:
                raise
            except Exception:
                pass
            # T-SQL leftovers from pasted SQL: GO batch separators (even glued
            # like "GO)") and doubled brackets [[..]] — normalize before resolve.
            raw = re.sub(r"(?im)^\s*GO(?=\s*(\)|;|$))", "", raw)
            raw = raw.replace("[[", "[").replace("]]", "]")
            raw = raw.strip()
            if not raw:
                return ""
            from .namespaces import _resolve_expression, build_registry
            reg = build_registry() if re.search(r"\[|\$[A-Za-z_]+\.[A-Za-z_]+\$", raw) else {}
            _rules = getattr(self, "rules", []) or []
            if _rules and "$" in raw:
                from .rulevars import expand_rule_vars as _erv
                raw = _erv(raw, _rules)
            return _resolve_expression(raw, getattr(self, "fields", []) or [], reg, set(),
                                       table_map, conn_map or self._conn_map(),
                                       default_tables=self._rx_defaults())
        except Exception as e:
            raise ValueError(f"الشرط العام للتقرير غير صالح: {e}")

    def _apply_general_where(self, where_clause: str, table_map=None, conn_map=None, gw_text=None) -> str:
        """AND the report-level general condition into a WHERE clause (literals only, no binds)."""
        gw = self._general_where_sql(table_map, conn_map, gw_text)
        if not gw:
            return where_clause
        if where_clause and where_clause.strip():
            return where_clause + f" AND ({gw})"
        return f" WHERE ({gw})"

    def _reject_dependent_scope(self, filters, group_by, plan, valplan, columns):
        """WHERE/GROUP BY run at the base level — dependent aliases (@refname
        columns) don't exist there yet. Fail loudly with guidance instead of
        a cryptic DB error. Sorting is fine (applies outermost)."""
        try:
            _dep = set()
            for _lvl in (valplan or {}).get("levels") or []:
                for _c in _lvl:
                    for _k in (getattr(_c, "alias", None), getattr(_c, "name", None)):
                        if _k and str(_k).strip():
                            _dep.add(str(_k).strip().lower())
            if not _dep:
                return
            _hits = set()

            def _scan_fields(items):
                for _f in (items or []):
                    if isinstance(_f, dict):
                        for _k in ("field", "column", "name", "expr"):
                            _v = _f.get(_k)
                            if isinstance(_v, str) and _v.strip().lower() in _dep:
                                _hits.add(_v.strip())
                        _scan_fields(_f.get("any") or [])
                        _scan_fields(_f.get("all") or [])

            _scan_fields(filters)
            if isinstance(group_by, str) and group_by.strip().lower() in _dep:
                _hits.add(group_by.strip())
            _gw_texts = []
            try:
                _gw_texts.append(plan.get("gw_base") or "")
            except Exception:
                pass
            try:
                _gw_texts.append(self.compiler.general_where()
                                 if hasattr(self.compiler, "general_where") else "")
            except Exception:
                pass
            try:
                _rm = self._refname_map(columns)
                for _t in _gw_texts:
                    for _tok in self._value_tokens(_t, _rm):
                        _hits.add("@" + _tok)
            except Exception:
                pass
            if _hits:
                raise ValueError("الترشيح/التجميع على (%s) غير مدعوم هنا — هذه أعمدة محسوبة بقيم (@refname) تُبنى فوق النتائج؛ رشّح على الأعمدة الأساسية أو من فلاتر المشغل بعد الجلب"
                                 % "، ".join(sorted(_hits)))
        except ValueError:
            raise
        except Exception:
            pass

    def _compile_value_wrapped(self, plan, columns, table_map, where_clause, group_clause,
                               order_clause, paginate_clause, params, page_size, valplan,
                               extra_selects=None):
        """Assemble the nested derived tables for @refname dependents."""
        from_q = plan["from_q"]
        base = valplan["base"]
        select_clause = _build_select(base, getattr(self, "fields", []), table_map=table_map,
                                        dialect=("pg" if _is_pg_db(plan.get("base_db"))
                                                 else ("mssql" if _is_mssql_db(plan.get("base_db")) else "oracle")),
                                        conn_map=self._conn_map(),
                                        rules=getattr(self, "rules", []),
                                        alias_by_table=self._plan_alias_map(plan),
                                        default_tables=self._rx_defaults())
        for _ex, _al in (extra_selects or []):
            select_clause += f", {_ex} AS {_q(_al)}"
        for _ex, _al in (plan.get("extra") or []):
            select_clause += f", {_ex} AS {_q(_al)}"
        _sel_kw = "SELECT DISTINCT" if self._report_distinct() else "SELECT"
        _don_ord0 = ""
        _don = []
        if _is_pg_db(plan.get("base_db")):
            try:
                _abt = self._plan_alias_map(plan)
                _don = self._distinct_on_keys(base, getattr(self, "fields", []),
                                              table_map, self._conn_map(), _abt)
            except Exception:
                _don = []
        if _don:
            _sel_kw = f"DISTINCT ON ({', '.join(_don)})"
            _don_ord0 = " ORDER BY " + ", ".join(f"{k} ASC" for k in _don)
        _report_aliases = [(getattr(c, "alias", None) or getattr(c, "name", None) or "")
                           for c in columns]
        return self._wrap_value_levels(
            "%s %s" % (_sel_kw, select_clause), from_q, where_clause,
            "%s%s" % (group_clause, _don_ord0), order_clause, paginate_clause,
            _report_aliases, valplan["levels"], valplan["resolved"]), params

    def _compile_sql2(self, plan, filters, sort, page, page_size, group_by, extra_selects=None):
        """Build SELECT from a routing plan (no re-planning)."""
        from_q = plan["from_q"]
        columns = plan["columns"]
        table_map = plan["table_map"]
        _abt_plan = self._plan_alias_map(plan)
        select_clause = _build_select(columns, getattr(self, "fields", []), table_map=table_map,
                                        dialect=("pg" if _is_pg_db(plan.get("base_db"))
                                                 else ("mssql" if _is_mssql_db(plan.get("base_db")) else "oracle")),
                                        conn_map=self._conn_map(),
                                        rules=getattr(self, "rules", []),
                                        alias_by_table=_abt_plan,
                                        default_tables=self._rx_defaults())
        for _ex, _al in (extra_selects or []):
            select_clause += f", {_ex} AS {_q(_al)}"
        for _ex, _al in (plan.get("extra") or []):
            select_clause += f", {_ex} AS {_q(_al)}"
        where_clause, where_params = _build_where(filters or [], columns=columns,
                                                 fields=getattr(self, "fields", []), table_map=table_map,
                                                 conn_map=self._conn_map(), rules=getattr(self, "rules", []),
                                                 default_tables=self._rx_defaults())
        where_clause = self._apply_general_where(where_clause, table_map, self._conn_map(),
                                                  plan.get("gw_base"))
        group_clause = ""
        if group_by:
            group_clause = f" GROUP BY {_resolve_filter_field(group_by, columns, getattr(self, 'fields', []), table_map, self._conn_map(), getattr(self, 'rules', []), default_tables=self._rx_defaults())}"
        order_clause = _build_order_by(sort, columns=columns)
        try:
            _ms_base = _is_mssql_db(plan.get("base_db"))
        except Exception:
            _ms_base = False
        if _ms_base and page_size != "all" and not (order_clause or "").strip():
            # T-SQL forbids OFFSET without ORDER BY (unlike Postgres);
            # DISTINCT ON never applies to mssql so ORDER BY 1 is always safe.
            order_clause = " ORDER BY 1"
        paginate_clause, params = self._paginate_clause(page, page_size, dict(where_params), plan["base_db"])
        # @refname value references → nested derived tables (final VALUES,
        # not merged expressions). No dependents: today's path untouched.
        _valplan = self._plan_value_refs(
            columns, extra_aliases=[_al for _ex, _al in ((extra_selects or []) + list(plan.get("extra") or []))])
        if _valplan is not None:
            self._reject_dependent_scope(filters, group_by, plan, _valplan, columns)
            return self._compile_value_wrapped(
                plan, columns, table_map, where_clause, group_clause,
                order_clause, paginate_clause, params, page_size, _valplan,
                extra_selects=extra_selects)
        _sel_kw = "SELECT DISTINCT" if self._report_distinct() else "SELECT"
        _don = []
        if _is_pg_db(plan.get("base_db")):
            try:
                _don = self._distinct_on_keys(columns, getattr(self, "fields", []),
                                              table_map, self._conn_map(), _abt_plan)
            except Exception:
                _don = []
        if _don:
            # DISTINCT ON يتطلب أن يبدأ ORDER BY بنفس الأعمدة — مع احترام
            # اتجاه فرز المستخدم عندما يفرز بأحدها (التواريخ لم ترتب قبل هذا)
            def _nq(s):
                return re.sub(r'[\s"]', "", str(s or "")).lower()
            _don_set = {_nq(k) for k in _don}
            _udir, _umatch = "ASC", set()
            try:
                _scol = sort.get("column") if isinstance(sort, dict) else None
                _sdir = str((sort or {}).get("direction") or "asc").strip().upper()
                _udir_all = "DESC" if _sdir == "DESC" else "ASC"
                if _scol:
                    _sres = _resolve_filter_field(
                        _scol, columns, getattr(self, "fields", []),
                        table_map, self._conn_map(),
                        default_tables=self._rx_defaults())
                    for k in _don:
                        if _nq(k) == _nq(_sres):
                            _umatch.add(_nq(k))
                    if _umatch:
                        _udir = _udir_all
            except Exception:
                pass
            _don_ord = [f"{k} {_udir if _nq(k) in _umatch else 'ASC'}" for k in _don]
            _rest = ""
            if order_clause and order_clause.strip().upper().startswith("ORDER BY"):
                _items = [p.strip() for p in order_clause.strip()[len("ORDER BY"):].split(",") if p.strip()]
                _keep = []
                for it in _items:
                    _core = re.sub(r"\s+(ASC|DESC)\s*$", "", it, flags=re.IGNORECASE)
                    if _nq(_core) in _don_set:
                        continue
                    _keep.append(it)
                _rest = ", ".join(_keep)
            _sel_kw = f"DISTINCT ON ({', '.join(_don)})"
            _sel_ord = " ORDER BY " + ", ".join(_don_ord) + (f", {_rest}" if _rest else "")
            sql = f"SELECT {_sel_kw} {select_clause} FROM {from_q}{where_clause}{group_clause}{_sel_ord}{paginate_clause}"
        else:
            sql = f"{_sel_kw} {select_clause} FROM {from_q}{where_clause}{group_clause}{order_clause}{paginate_clause}"
        return sql, params

    def _compile_sql(
        self,
        filters: List[Dict[str, Any]] | None = None,
        sort: Optional[Dict[str, Any] | List[Dict[str, Any]]] = None,
        page: int = 1,
        page_size: int | str = 50,
        group_by: Optional[str] = None,
        active_table: Optional[str] = None,
        extra_selects: Optional[List[Tuple[str, str]]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Compile final Oracle SQL with secure quoting and bind params.
        extra_selects: [(expr_sql, alias)] appended to SELECT (core injection).
        Returns (sql, params)
        """
        plan = self._plan_structure(active_table, filters, sort, group_by)
        self._reject_deferred(plan, "في معاينة SQL")
        filters2 = self._apply_remote_filters(filters, plan)
        return self._compile_sql2(plan, filters2, sort, page, page_size, group_by, extra_selects)

    # ── Public API ──────────────────────────────────────────────────────

    def preview_sql(self, payload: Dict[str, Any]) -> str:
        """
        Generate final executable Oracle SQL for UI preview (without executing).
        Payload from frontend Report Player:
        {
            activeTable: str,
            filters: [{field, op, value, valFrom, valTo}],
            sort: {column, direction} or [{...}],
            page: int, pageSize: int|'all',
            groupBy: str,
            columns: optional override
        }
        """
        self._validate_no_exact_dupes()
        try:
            _api_tabs = self._api_involved_tables()
        except Exception:
            _api_tabs = {}
        try:
            _un_map = self._union_partitions()
        except Exception:
            _un_map = {}
        _staged = getattr(self, "_api_stage", None) or {}
        if _api_tabs or _staged or _un_map:
            _lines = ["-- قناة التنفيذ: SQL موحدة (ترحيل API ← جداول مؤقتة)"]
            if _api_tabs:
                _lines.append("-- مصادر API:")
                for _t, _inf in _api_tabs.items():
                    try:
                        _rw = _inf.get("row")
                        _nm = str(getattr(_rw, "name", "") or _inf.get("gid") or "")
                    except Exception:
                        _nm = str(_inf.get("gid") or "")
                    _lines.append(f"--   {_t.lower()} ← {_nm} [{_inf.get('engine') or '?'}] → TEMP عند التنفيذ")
            if _un_map:
                _lines.append("-- جداول مدموجة UNION عبر الاتصالات:")
                for _t, _parts in _un_map.items():
                    try:
                        _cset = set()
                        for _part in (_parts or []):
                            for _, _g in (_part or []):
                                _cset.add(str(_g) if _g else 'الأساسي')
                        _conns = sorted(_cset)
                    except Exception:
                        _conns = []
                    _lines.append(f"--   {_t.lower()} ← {len(_parts or [])} مصادر ({', '.join(_conns)}) → TEMP واحد عند التنفيذ")
            if _staged:
                _lines.append("-- جداول مؤقتة مرحّلة:")
                for _t, _v in _staged.items():
                    _lines.append(f"--   {_t.lower()} → {_v.get('temp')}")
            _lines.append("-- ملاحظة: المعاينة تعرض الخطة فقط — نفّذ التقرير لعرض الصفوف.")
            return "\n".join(_lines)
        return self._preview_compile(payload)

    def _ensure_stage_names_only(self):
        """No staging exists — clear the maps so previews show source names."""
        self._api_stage = {}
        self._api_union = {}
        self._api_warnings = []
        try:
            _umap = self._union_partitions()
        except Exception:
            _umap = {}
        self._reject_direct_sql_unions(_umap)

    def preview_real_sql(self, payload: Dict[str, Any]) -> str:
        """The exact SQL execute() would run (stage TEMP names) — without staging or executing.

        Safe for huge tables: only deterministic names are computed, no rows move.
        In direct live mode the query targets the source itself (transpiled, binds kept).
        """
        self._validate_no_exact_dupes()
        _dg = None
        try:
            _dg = self._get_direct_gid()
        except Exception:
            _dg = None
        if not _dg:
            self._ensure_stage_names_only()
        sql = self._preview_compile(payload)
        if _dg:
            try:
                sql = _mssql_transpile_sql(sql, convert_binds=False)
            except Exception:
                pass
        return sql

    def _preview_compile(self, payload: Dict[str, Any]) -> str:
        filters = payload.get("filters") or payload.get("activeFilters") or []
        # Handle columnFilters from Excel-like grid: {col: [values]} -> convert to IN
        column_filters = payload.get("columnFilters") or {}
        for col, vals in column_filters.items():
            if vals:
                filters.append({"field": col, "op": "in", "value": vals})

        sort = payload.get("sort") or payload.get("activeSort")
        # Handle legacy: sort as {column, direction}
        if isinstance(sort, dict) and sort.get("column") is None and sort.get("field"):
            sort = {"column": sort["field"], "direction": sort.get("direction", "asc")}

        page = payload.get("page") or payload.get("currentPage") or 1
        page_size = payload.get("pageSize") or payload.get("page_size") or 50
        group_by = payload.get("groupBy") or payload.get("group_by") or payload.get("activeGroupColumn")
        active_table = payload.get("activeTable") or payload.get("table")

        sql, params, _strip = self._compile_wrapped(
            filters=filters,
            sort=sort,
            page=page,
            page_size=page_size,
            group_by=group_by,
            active_table=active_table,
        )
        # For preview, inline params as comments (not for execution)
        # Return SQL with binds shown
        preview = sql
        if params:
            preview += f"\n-- Binds: {params}"
        return preview

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compile and execute query, return paginated result.
        Returns: {rows, total, page, pageSize, sql, columns}
        """
        self._validate_no_exact_dupes()
        import time as _tmod
        self._timings = {}
        self._merge_stats = {}
        _t_all = _tmod.time()
        # Distributed query path: when the report spans multiple connections
        # we avoid the (very slow) staging copy and instead fetch each
        # connection independently, then join in Python. The user opts in
        # explicitly so we never break legacy reports by accident.
        try:
            if payload.get("distributed") or getattr(self, "_force_distributed", False):
                return self._execute_distributed(payload)
        except Exception:
            pass
        try:
            _refresh = bool(payload.get("refreshApi") or payload.get("refresh_api"))
        except Exception:
            _refresh = False
        self._ensure_api_staged(refresh=_refresh)
        filters = payload.get("filters") or payload.get("activeFilters") or []
        column_filters = payload.get("columnFilters") or {}
        for col, vals in column_filters.items():
            if vals:
                filters.append({"field": col, "op": "in", "value": vals})

        sort = payload.get("sort") or payload.get("activeSort")
        page = payload.get("page") or payload.get("currentPage") or 1
        page_size = payload.get("pageSize") or payload.get("page_size") or 50
        group_by = payload.get("groupBy") or payload.get("group_by") or payload.get("activeGroupColumn")
        active_table = payload.get("activeTable") or payload.get("table")

        # Compile SQL (computed-column filters wrapped in an outer query)
        _t_plan = _tmod.time()
        exec_plan = self._plan_structure(active_table, filters, sort, group_by)
        self._timings["plan_ms"] = int((_tmod.time() - _t_plan) * 1000)
        base_db = exec_plan["base_db"]
        filters2 = self._apply_remote_filters(filters, exec_plan)
        _pc, _ptm, base_f, outer_f, extra, outer_cols = self._outer_plan2(exec_plan, filters2)
        _deferred = [d for d in (exec_plan.get("gw_deferred") or []) if isinstance(d, dict)]
        if _deferred and outer_f:
            raise ValueError(
                'الشرط العام المؤجل على جدول باتصال آخر لا يجتمع مع فلاتر محسوبة — '
                'رشّح العمود المحسوب من المشغل بعد العرض.')
        _need_all = bool(outer_f or _deferred)
        sql, params = self._compile_sql2(
            exec_plan, base_f, sort, 1 if _need_all else page,
            "all" if _need_all else page_size, group_by, extra)
        _gsql, _gparams, _gwpre, _gwempty = (sql, params, False, False)
        if _deferred:
            # E-driven pre-filter (and early-empty): the condition narrows
            # the base fetch instead of only filtering after it.
            try:
                _gsql, _gparams, _gwpre, _gwempty = self._gw_prefilter_wrap(sql, params, exec_plan)
            except Exception:
                _gsql, _gparams, _gwpre, _gwempty = sql, params, False, False
        strip_extra = [a for _, a in extra]
        if outer_f:
            _ow0, _op0 = _build_outer_where(outer_f, outer_cols, rules=getattr(self, "rules", []))
            if _ow0:
                params = dict(params)
                params.update(_op0)
                pag, params = self._paginate_clause(page, page_size, params, base_db)
                sql = f"SELECT * FROM ({sql}) t WHERE {_ow0[len(' WHERE '):]}" + pag
        strip_extra = set(strip_extra) | set(exec_plan.get("strip") or set())

        # Get total count (without pagination) for pagination bar
        # (same FROM incl. JOINs so filters match; outer filters wrapped too)
        where_clause, where_params = _build_where(base_f, columns=exec_plan["columns"],
                                                 fields=getattr(self, "fields", []),
                                                 table_map=exec_plan["table_map"],
                                                 conn_map=self._conn_map(), rules=getattr(self, "rules", []),
                                                 default_tables=self._rx_defaults())
        where_clause = self._apply_general_where(where_clause, exec_plan["table_map"], self._conn_map(),
                                                   exec_plan.get("gw_base"))
        from_q = exec_plan["from_q"]
        group_clause = f" GROUP BY {_resolve_filter_field(group_by, exec_plan['columns'], getattr(self, 'fields', []), exec_plan['table_map'], self._conn_map(), getattr(self, 'rules', []), default_tables=self._rx_defaults())}" if group_by else ""
        if outer_f:
            _ow, _op = _build_outer_where(outer_f, outer_cols, rules=getattr(self, "rules", []))
            if _ow:
                _inner, _ip = self._compile_sql2(
                    exec_plan, base_f, sort=None, page=1, page_size="all",
                    group_by=group_by, extra_selects=extra)
                _ip = dict(_ip)
                _ip.update(_op)
                count_sql_simple = f"SELECT COUNT(*) as cnt FROM ({_inner}) t WHERE {_ow[len(' WHERE '):]}"
                count_params_simple = _ip
            else:
                count_sql_simple = f"SELECT COUNT(*) as cnt FROM {from_q}{where_clause}{group_clause}"
                count_params_simple = where_params
        else:
            _need_distinct_count = False
            try:
                _need_distinct_count = bool(self._report_distinct()) or (
                    bool(_is_pg_db(exec_plan.get("base_db"))) and bool(
                        self._distinct_on_keys(exec_plan["columns"], getattr(self, "fields", []),
                                              exec_plan["table_map"], self._conn_map())))
            except Exception:
                _need_distinct_count = bool(self._report_distinct())
            if _need_distinct_count and not group_clause:
                # العدّ يطابق SELECT DISTINCT / DISTINCT ON (عبر استعلام فرعي)
                _csql, _cp = self._compile_sql2(
                    exec_plan, base_f, None, 1, "all", None, extra)
                count_sql_simple = f"SELECT COUNT(*) as cnt FROM ({_csql}) t"
                count_params_simple = _cp
            else:
                count_sql_simple = f"SELECT COUNT(*) as cnt FROM {from_q}{where_clause}{group_clause}"
                count_params_simple = where_params

        # Execute with robust resource management
        result_rows: List[Dict[str, Any]] = []
        total = 0
        try:
            _skip_total = bool(payload.get("skipTotal") or payload.get("skip_total"))
        except Exception:
            _skip_total = False
        if _deferred:
            # The stream computes the post-filter total itself; the base
            # COUNT(*) would double the heavy I/O for nothing.
            _skip_total = True
        try:
            # Ensure connection (on the routed base DB)
            if not getattr(base_db, "conn", None):
                try:
                    base_db.connect()
                except Exception:
                    pass
            # Get total (skippable: the player already knows it on page 2+ and
            # COUNT(*) over huge filtered tables is often the slowest query)
            if _skip_total:
                total = -1
            else:
                try:
                    if getattr(base_db, "conn", None):
                        base_db.conn.rollback()  # clear any poisoned txn so the REAL error surfaces
                except Exception:
                    pass
                self._report_progress({"stage": "count", "text": "حساب عدد السجلات..."})
                _t_count = _tmod.time()
                cur = self._exec_on(base_db, count_sql_simple, count_params_simple)
                try:
                    row = cur.fetchone()
                    total = row[0] if row else 0
                    # Handle GROUP BY case where count returns multiple rows
                    if group_by and row and len(row) > 1:
                        # If grouped, total is number of groups
                        cur2 = self._exec_on(base_db, f"SELECT COUNT(*) FROM (SELECT 1 FROM {from_q}{where_clause}{group_clause})", where_params)
                        try:
                            total = cur2.fetchone()[0]
                        finally:
                            try: cur2.close()
                            except: pass
                finally:
                    try: cur.close()
                    except: pass
                self._timings["count_ms"] = int((_tmod.time() - _t_count) * 1000)

            # Deferred GW: unlimited rows (user-confirmed) — the streaming
            # fetch below bounds memory by processing in chunks, so no cap.

            # Get paged rows (raw -> cross-DB merges -> strip helpers -> format)
            try:
                if getattr(base_db, "conn", None):
                    base_db.conn.rollback()  # clear any poisoned txn so the REAL error surfaces
            except Exception:
                pass
            self._report_progress({"stage": "rows", "table": str(exec_plan.get("from_table") or "").lower(),
                                   "text": "جلب الصفوف..."})
            _t_rows = _tmod.time()
            if _gwempty:
                result_rows, total = [], 0
                try:
                    self._timings["gw_early_empty"] = True
                    self._timings["rows_n"] = 0
                except Exception:
                    pass
                cur = None
            else:
                cur = self._exec_on(base_db, _gsql, _gparams)
            try:
                cols = [d[0].lower() for d in cur.description] if cur is not None and cur.description else []
                if not cols:
                    result_rows = []
                elif _deferred:
                    # Streaming path: chunked merge+filter, only the requested
                    # page materialized (unlimited rows, bounded memory).
                    result_rows, total = self._exec_deferred_stream(
                        cur, cols, exec_plan, strip_extra, page, page_size)
                else:
                    _strip = {str(_k) for _k in (strip_extra or set())}
                    _strip |= {str(_k).lower() for _k in (strip_extra or set())}
                    _fetched = cur.fetchall()
                    # zip first, merges second, strip helpers third, format last
                    # (merges read the helper aliases _rmljN/_rmlaN as join
                    # keys — stripping them before _apply_merges leaves every
                    # remote column None with no error)
                    _rr = []
                    _append = _rr.append
                    for _r in _fetched:
                        _d = dict(zip(cols, _r))
                        _append(_d)
                    self._timings["rows_ms"] = int((_tmod.time() - _t_rows) * 1000)
                    self._timings["rows_n"] = len(_rr)
                    _t_merge = _tmod.time()
                    raw_rows = self._apply_merges(_rr, exec_plan)
                    self._timings["merge_ms"] = int((_tmod.time() - _t_merge) * 1000)
                    if _strip:
                        for _d in raw_rows:
                            for _k in _strip:
                                _d.pop(_k, None)
                    result_rows = []
                    _refcols = [(str(getattr(c, "col_refname", None) or getattr(c, "colRefname", None) or "").strip(),
                                 getattr(c, "alias", None) or getattr(c, "name", None))
                                for c in (exec_plan.get("columns") or [])
                                if getattr(c, "col_refname", None) or getattr(c, "colRefname", None)]
                    for _r in raw_rows:
                        _row_d = {k: _fmt_cell(v) for k, v in _r.items()}
                        for _rn, _al in _refcols:
                            if _rn:
                                _val = _row_d.get(_al)
                                if _val is None and _al:
                                    _val = _row_d.get(str(_al).lower())
                                _row_d[_rn] = _val
                                _row_d["@" + _rn] = _val
                        result_rows.append(_row_d)
            finally:
                try: cur.close()
                except: pass

        except Exception as e:
            # Secure rollback on error
            try:
                if getattr(base_db, "conn", None):
                    base_db.conn.rollback()
            except: pass
            raise RuntimeError(f"Report execution failed: {e}\nSQL: {sql}\nParams: {params}") from e

        # Handle pagination 'all'
        if page_size == "all":
            page_size_val = total
            total_pages = 1
        else:
            try:
                page_size_val = int(page_size)
                total_pages = (total + page_size_val - 1) // page_size_val if page_size_val else 1
            except:
                page_size_val = 50
                total_pages = 1

        _groups_list = [g.to_dict() for g in (self.compiler.groups() if hasattr(self.compiler, "groups") else [])]
        try:
            _meta_out = dict(self.metadata) if isinstance(self.metadata, dict) else {}
        except Exception:
            _meta_out = {}
        # duplicate groups inside metadata for player header (reads metadata.groups)
        _meta_out["groups"] = _groups_list
        try:
            self._timings["total_ms"] = int((_tmod.time() - _t_all) * 1000)
            self._timings["merge_stats"] = dict(getattr(self, "_merge_stats", {}) or {})
            _rn = ""
            try:
                _rn = str((self.metadata or {}).get("name") or "")
            except Exception:
                _rn = ""
            _tline = (f"RML timings [{_rn}]: " + " ".join(
                f"{k}={v}" for k, v in self._timings.items() if k != "merge_stats")
                + (f" merge_stats={self._timings['merge_stats']}" if self._timings.get("merge_stats") else ""))
            print(_tline)
            try:
                # stdout is swallowed by hidden server windows -> mirror to
                # logs/rml_stage_diag.log (same sink as staging diagnostics).
                _emit_staging_diag(f"RML timings [{_rn}]", _tline)
            except Exception:
                pass
        except Exception:
            pass
        return {
            "rows": result_rows,
            "total": total,
            "timings": dict(getattr(self, "_timings", {}) or {}),
            "page": int(page),
            "pageSize": page_size_val,
            "totalPages": total_pages,
            "sql": sql,
            "params": params,
            "gw_post_filtered": bool(_deferred),
            "warnings": self._render_api_warnings(),
            "columns": [c.to_dict() for c in self.columns],
            "fields": [f.to_dict() for f in getattr(self, "fields", [])],
            "rules": [r.to_dict() for r in getattr(self, "rules", [])],
            "groups": _groups_list,
            "metadata": _meta_out,
            "connections": [c.to_dict() for c in self.connections],
            "charts": [c.to_dict() for c in self.charts],
            "summary": self._summarize(payload.get("summary") or [], filters, active_table),
            "detail": (lambda d: d.to_dict() if d is not None and not isinstance(d, dict) else d)(
                self.compiler.detail() if hasattr(self.compiler, "detail") else None),
        }

    # ── Distinct values (status-map helper: unique records of one column) ──

    def distinct_values(self, column_alias: str, limit: int = 100, search: str = None) -> Dict[str, Any]:
        """Return distinct raw values of one display column (by alias or name).

        Queries ALL records (SELECT DISTINCT over the full table), not the page.
        Optional search filters server-side (LIKE %search%).
        Returns {values:[...], column, truncated, sql}. Used by the designer
        status-map modal and the player column-filter menu.
        """
        self._validate_no_exact_dupes()
        self._ensure_api_staged()
        key = str(column_alias or "").strip()
        if not key:
            raise ValueError("column required")
        cols = self._inline_column_refs(self.columns)
        target = None
        kl = key.lower()
        for c in cols:
            if str(getattr(c, "alias", "") or "").strip().lower() == kl or \
               str(getattr(c, "name", "") or "").strip().lower() == kl:
                target = c
                break
        plan = self._plan_structure(None, [], None, None)
        self._reject_deferred(plan, "في القيم المميزة")
        from_q = plan["from_q"]
        table_map = plan.get("table_map")
        try:
            _is_pg = _is_pg_db(plan.get("base_db"))
        except Exception:
            _is_pg = False
        try:
            _is_ms = _is_mssql_db(plan.get("base_db"))
        except Exception:
            _is_ms = False
        # Fast path: column backed by ONE base-table field →
        # SELECT DISTINCT <expr> FROM <base> only (no JOINs over millions of rows).
        _fast_sel = None
        _fast_from = None
        try:
            if target is not None and str(getattr(target, "col_type", "") or "direct") == "direct":
                _ftm = self._field_table_map()
                _rk = self._refs_in_text(
                    getattr(target, "expr", None) or getattr(target, "name", None) or "", _ftm)
                _rts = {_ftm[k] for k in _rk if k in _ftm}
                if len(_rts) == 1 and next(iter(_rts)) == plan.get("base_norm"):
                    _sel1 = _build_select(
                        [target], getattr(self, "fields", []), table_map=None,
                        dialect=("pg" if _is_pg else ("mssql" if _is_ms else "oracle")),
                        conn_map=self._conn_map(), rules=getattr(self, "rules", []),
                        alias_by_table=None, default_tables=self._rx_defaults())
                    _btmp = self._staged_temp_of(plan.get("base_norm"))
                    if _btmp:
                        _bq = _q(_btmp)
                    else:
                        _bsch = plan.get("base_schema")
                        _bdp = plan.get("base_disp") or plan.get("base_norm")
                        _bq = (f"{_q(_bsch)}." if _bsch else "") + _q(_bdp)
                    _fast_sel, _fast_from = _sel1, _bq
        except Exception:
            _fast_sel = None
        if target is None:
            # fallback: حقل مصدر مباشر (يُستخدم لاختيار قيم المطابقة في سياسات القواعد)
            from .compiler import RMLColumn as _RC
            fld = None
            for f in (getattr(self, "fields", []) or []):
                if str(getattr(f, "name", "") or "").strip().lower() == kl:
                    fld = f
                    break
            if fld is None:
                raise ValueError(f'العمود "{key}" غير موجود في التقرير')
            target = _RC(id="__fld", name=str(fld.name), alias=str(fld.name),
                         expr=f"[{fld.name}]", col_type="direct")
        select_clause = _build_select([target], getattr(self, "fields", []), table_map=table_map,
                                      dialect=("pg" if _is_pg else ("mssql" if _is_mssql_db(plan.get("base_db")) else "oracle")),
                                      conn_map=self._conn_map(),
                                      rules=getattr(self, "rules", []),
                                      alias_by_table=self._plan_alias_map(plan),
                                      default_tables=self._rx_defaults())
        try:
            if isinstance(limit, str) and str(limit).strip().lower() in ("all", "unlimited"):
                n = None  # unlimited: no FETCH cap
            else:
                n = max(1, min(int(limit or 100), 2000))
        except (TypeError, ValueError):
            n = 100
        _aq = _q(getattr(target, "alias", None) or key)
        _inner = f"SELECT DISTINCT {select_clause} FROM {from_q}"
        params: Dict[str, Any] = {}
        _where = ""
        if search is not None and str(search).strip() != "":
            _where = f" WHERE {_aq} LIKE :s"
            params["s"] = f"%{str(search).strip()}%"
        if _fast_sel is not None:
            _where_f = ""
            if search is not None and str(search).strip() != "":
                _where_f = f" WHERE {_fast_sel} LIKE :s"
            if _is_pg:
                sql = f"SELECT DISTINCT {_fast_sel} FROM {_fast_from}{_where_f} ORDER BY 1" + ("" if n is None else " LIMIT :lim")
            else:
                sql = f"SELECT DISTINCT {_fast_sel} FROM {_fast_from}{_where_f} ORDER BY 1" + ("" if n is None else " OFFSET 0 ROWS FETCH NEXT :lim ROWS ONLY")
        elif _is_pg:
            sql = f"SELECT * FROM ({_inner}) t{_where} ORDER BY 1" + ("" if n is None else " LIMIT :lim")
        else:
            sql = f"SELECT * FROM ({_inner}) t{_where} ORDER BY 1" + ("" if n is None else " OFFSET 0 ROWS FETCH NEXT :lim ROWS ONLY")
        if n is not None:
            params["lim"] = n
        try:
            cur = self._exec_on(plan["base_db"], sql, params)
            fetched = cur.fetchall() if hasattr(cur, "fetchall") else []
        except Exception as e:
            raise RuntimeError(f"تعذر جلب القيم الفريدة: {e}")
        vals = []
        for r in fetched:
            try:
                v = r[0] if not isinstance(r, dict) else list(r.values())[0]
            except Exception:
                v = r
            if v is None:
                continue
            v = _fmt_cell(v)
            if isinstance(v, str):
                v = v.strip()
            if v == "" or v in vals:
                continue
            vals.append(v)
            if n is not None and len(vals) >= n:
                break
        return {"values": vals, "column": key, "truncated": (n is not None and len(fetched) >= n), "sql": sql}

    # ── Detail rows (master-detail expandable) ──────────────────────────

    def _detail_plan(self) -> Dict[str, Any]:
        """Shared preamble for detail_rows / detail_search.

        Validates <detail> columns, splits cross-connection fk_lookup proxies,
        and builds FROM + SELECT. Returns dict with det/table/dkey/dcols/
        base_cols/fk_lookup_cols/from_q/schema/_ddb.
        """
        self._validate_no_exact_dupes()
        self._ensure_api_staged()
        det = self.compiler.detail() if hasattr(self.compiler, "detail") else None
        det = det.to_dict() if det is not None and not isinstance(det, dict) else det
        if not det:
            raise ValueError("التقرير لا يحتوي على تعريف تفاصيل (<detail>)")
        table = det.get("table")
        dkey = det.get("detail")
        if not table or not dkey:
            raise ValueError("تعريف التفاصيل ناقص (table/detail)")
        dcols = self.compiler.detail().columns if hasattr(self.compiler.detail(), "columns") else []
        try:
            dcols = self._inline_column_refs(dcols, scope_extra=self.columns)
        except ValueError:
            raise
        fields = getattr(self, "fields", []) or []
        # Detail table may live on another connection -> route its DB
        _dtnorm = self._norm_table(table)
        _dconn = self._conn_key_of_table(_dtnorm)
        _ddb = self._db_for_conn(_dconn)
        schema = self._schema_for_table(_dtnorm, _dconn, self.metadata.get("schema"))
        try:
            _dtmp = self._staged_temp_of(_dtnorm)
        except Exception:
            _dtmp = None
        try:
            _dmir = (getattr(self, "_iot_mirror", None) or {}).get(_dtnorm)
        except Exception:
            _dmir = None
        if _dtmp:
            from_q = _q(_dtmp)
        elif _dmir:
            _dsch = str(_dmir.get("schema") or "")
            from_q = f"{(_q(_dsch) + '.') if _dsch else ''}{_q(str(_dmir.get('table') or table))}"
        else:
            from_q = f"{_q(schema)}.{_q(table)}" if schema and "." not in table else _q(table)
        # Runtime lookup derivation: columns need NO stored lookup props —
        # effective fk_lookup props come from the expression's display ref +
        # the detail↔ref link (default/sub roles and one_to_one/one_to_many
        # rel drive scalar vs aggregated SQL downstream).
        dcols = [self._with_derived_lookup(c, _dtnorm) for c in dcols]
        # Detail select must reference the detail table only (relaxed: allow refs resolvable via links)
        for _dc in dcols:
            _rt = (getattr(_dc, "expr", None) or getattr(_dc, "name", None) or "").strip()
            _other = self._ref_tables_in_text(_rt, prefer=_dtnorm) - {_dtnorm}
            if _other:
                _allowed = True
                for ref_tbl in _other:
                    if getattr(_dc, "col_type", "") != "fk_lookup":
                        _allowed = False
                        break
                    mc_ref_tables = getattr(_dc, "ref_tables", [])
                    mc_ref_table = getattr(_dc, "ref_table", None)
                    if mc_ref_tables:
                        if not any(str(t.get("table") or "").upper() == str(ref_tbl).upper() for t in mc_ref_tables):
                            _allowed = False
                            break
                    elif mc_ref_table and str(mc_ref_table).upper() != str(ref_tbl).upper():
                        _allowed = False
                        break
                if not _allowed:
                    # Precise guidance: analyze WHY derivation failed (no link vs
                    # ambiguous bare name) — no hand-written properties needed.
                    try:
                        _st, _info = self._analyze_lookup(_dc, _dtnorm)
                    except Exception:
                        _st, _info = "none", None
                    _al = getattr(_dc, "alias", "")
                    if _st == "ambiguous" and _info:
                        raise ValueError(
                            f'عمود التفاصيل "{_al}" غامض — الاسم موجود في ({"، ".join(_info)}) — '
                            f'حدد الجدول في التعبير ([الجدول.العمود]).')
                    _tnames = sorted({str(t) for t in (_info if _st == "unlinked" and _info else _other) if t})
                    raise ValueError(
                        f'عمود التفاصيل "{_al}" يشير لجدول آخر بلا رابط — '
                        f'اربط جدول التفاصيل بجدول ({"، ".join(_tnames)}) من ورقة الحقول/الروابط.')
        # Separate fk_lookup columns that reference tables on different connections
        fk_lookup_cols = []
        base_cols = []
        for col in dcols:
            if getattr(col, "col_type", "") == "fk_lookup":
                # Auto-extract ref_table and ref_display from expression if not set
                # Expression format: [connection.table.column] e.g., [7.IAS_ITM_MST.I_NAME]
                ref_tables = getattr(col, "ref_tables", []) or []
                ref_table = getattr(col, "ref_table", None)
                ref_display = getattr(col, "ref_display", None)

                if not ref_table and not ref_display:
                    expr = getattr(col, "expr", None) or getattr(col, "name", None) or ""
                    # Try to parse [conn.table.column] or [table.column] format
                    import re
                    m = re.match(r'^\[?(\d+)?\.?([A-Za-z_][A-Za-z0-9_\.]*)\.([A-Za-z_][A-Za-z0-9_]*)\]?$', expr.strip())
                    if m:
                        if not ref_table:
                            ref_table = m.group(2)
                        if not ref_display:
                            ref_display = m.group(3)
                        # Set them on the column for later use
                        if not getattr(col, "ref_table", None):
                            col.ref_table = ref_table
                        if not getattr(col, "ref_display", None):
                            col.ref_display = ref_display
                        if not ref_tables:
                            col.ref_tables = [{"table": ref_table, "fk": getattr(col, "ref_fk", None), "display": ref_display}]

                if ref_table:
                    ref_norm = self._norm_table(ref_table)
                    ref_conn = self._conn_key_of_table(ref_norm)
                    # If reference table is on different connection than detail table, handle in Python
                    if ref_conn != _dconn:
                        # Add a proxy column to select the FK from detail table
                        # We'll replace it with display value in Python
                        fk_col_name = getattr(col, "ref_scope_key", None) or getattr(col, "ref_fk", None)
                        if fk_col_name:
                            from .compiler import RMLColumn
                            proxy_col = RMLColumn(
                                id=f"{col.id}_fk_proxy",
                                name=fk_col_name,
                                alias=col.alias,  # keep same alias for result mapping
                                data_type=None,
                                expr=fk_col_name,
                                col_type="direct",
                                connection_id=None,
                                where_clause=None,
                                icon=None,
                                is_amount=False,
                                currency_field=None,
                            )
                            fk_lookup_cols.append((col, proxy_col, ref_norm, ref_conn))
                            base_cols.append(proxy_col)
                        continue
            base_cols.append(col)
        # Build select clause for base columns only (fk_lookup handled in Python)
        select_clause = _build_select(base_cols, fields,
                                        dialect=("pg" if _is_pg_db(_ddb) else ("mssql" if _is_mssql_db(_ddb) else "oracle")),
                                        conn_map=self._conn_map(),
                                        rules=getattr(self, "rules", []),
                                        outer_table=table,
                                        default_schema=schema,
                                        default_tables=self._rx_defaults())
        return {"det": det, "table": table, "dkey": dkey, "dcols": dcols,
                "base_cols": base_cols, "fk_lookup_cols": fk_lookup_cols,
                "from_q": from_q, "schema": schema, "_ddb": _ddb,
                "select_clause": select_clause, "fields": fields}

    def detail_rows(self, master_value, page: int = 1, page_size: int | str = 50) -> Dict[str, Any]:
        """Fetch sub-records of the <detail> table for one master key value.

        Returns {rows, total, page, pageSize, totalPages, sql, columns, detail}.
        Handles cross-connection fk_lookup by resolving lookups in Python.
        """
        plan = self._detail_plan()
        det, table, dkey = plan["det"], plan["table"], plan["dkey"]
        dcols, base_cols = plan["dcols"], plan["base_cols"]
        fk_lookup_cols = plan["fk_lookup_cols"]
        from_q, select_clause, _ddb = plan["from_q"], plan["select_clause"], plan["_ddb"]
        where_clause = f" WHERE {_q(dkey)}=:dv"
        params: Dict[str, Any] = {"dv": master_value}
        # Total
        cur = self._exec_on(_ddb, f"SELECT COUNT(*) as cnt FROM {from_q}{where_clause}", params)
        try:
            row = cur.fetchone()
            total = row[0] if row else 0
        finally:
            try:
                cur.close()
            except Exception:
                pass
        # Page
        paginate_clause, params = self._paginate_clause(page, page_size, params, _ddb)
        sql = f"SELECT {select_clause} FROM {from_q}{where_clause}{paginate_clause}"
        cur = self._exec_on(_ddb, sql, params)
        try:
            cols = [d[0].lower() for d in cur.description] if cur.description else []
            result_rows = [{k: _fmt_cell(v) for k, v in zip(cols, r)} for r in cur.fetchall()] if cols else []
        finally:
            try:
                cur.close()
            except Exception:
                pass
        # Resolve cross-connection fk_lookup columns in Python
        for col, proxy_col, ref_norm, ref_conn in fk_lookup_cols:
            ref_db = self._db_for_conn(ref_conn)
            ref_schema = self._schema_for_table(ref_norm, ref_conn, self.metadata.get("schema"))
            ref_table_q = f"{_q(ref_schema)}.{_q(ref_norm)}" if ref_schema and "." not in ref_norm else _q(ref_norm)
            # Get ref_fk and ref_display for this column
            ref_fk = getattr(col, "ref_fk", None)
            ref_display = getattr(col, "ref_display", None)
            ref_tables = getattr(col, "ref_tables", []) or []
            if not ref_fk and ref_tables:
                ref_fk = ref_tables[0].get("fk")
            if not ref_display and ref_tables:
                ref_display = ref_tables[0].get("display")
            if not ref_fk or not ref_display:
                continue
            # Collect unique FK values from result rows (proxy alias == col alias)
            fk_values = set()
            alias_lower = (col.alias or col.name or "").lower()
            for row in result_rows:
                fk_val = row.get(alias_lower)
                if fk_val is not None:
                    fk_values.add(str(fk_val))
            # Query reference table for display values
            lookup_map = {}
            if fk_values:
                placeholders = ",".join([":v" + str(i) for i in range(len(fk_values))])
                ref_sql = f"SELECT {_q(ref_fk)}, {_q(ref_display)} FROM {ref_table_q} WHERE {_q(ref_fk)} IN ({placeholders})"
                ref_params = {"v" + str(i): v for i, v in enumerate(fk_values)}
                ref_cur = self._exec_on(ref_db, ref_sql, ref_params)
                try:
                    for ref_row in ref_cur.fetchall():
                        lookup_map[str(ref_row[0])] = ref_row[1]
                finally:
                    try:
                        ref_cur.close()
                    except Exception:
                        pass
            # Merge lookup values into result rows (proxy alias == col alias, overwrite in place)
            for row in result_rows:
                fk_val = row.get(alias_lower)
                if fk_val is not None and str(fk_val) in lookup_map:
                    row[alias_lower] = lookup_map[str(fk_val)]
        try:
            page_size_val = total if page_size == "all" else int(page_size)
        except (ValueError, TypeError):
            page_size_val = 50
        return {
            "rows": result_rows,
            "total": total,
            "page": int(page or 1),
            "pageSize": page_size_val,
            "totalPages": (total + page_size_val - 1) // page_size_val if page_size_val else 1,
            "sql": sql,
            "columns": [c.to_dict() for c in dcols],
            "detail": det,
        }

    def detail_search(self, q, limit_keys: int = 900) -> Dict[str, Any]:
        """Find master key values whose DETAIL rows match a search text.

        Searches all detail output columns (direct + computed + same-connection
        fk_lookup display values; cross-connection fk proxies by raw FK).
        Classification mirrors the player smart search: number → numeric equals
        (+ text contains), date → date equals (+ text contains), else text contains.
        Returns {master_values, count, truncated, sql}.
        """
        plan = self._detail_plan()
        dkey, base_cols = plan["dkey"], plan["base_cols"]
        from_q, select_clause, _ddb = plan["from_q"], plan["select_clause"], plan["_ddb"]
        fields = plan["fields"]
        qs = str(q or "").strip()
        if not qs:
            return {"master_values": [], "count": 0, "truncated": False, "sql": ""}
        # ── classify ──
        kind = "text"
        qnum = None
        qdate = None
        if re.fullmatch(r"-?\d+(\.\d+)?", qs):
            kind = "number"
            try:
                qnum = float(qs) if "." in qs else int(qs)
            except Exception:
                qnum = None
        else:
            _m8 = re.fullmatch(r"(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", qs)
            _md = re.fullmatch(r"\d{4}-\d{2}-\d{2}", qs)
            if _m8:
                qdate = f"{qs[0:4]}-{qs[4:6]}-{qs[6:8]}"
                kind = "date"
            elif _md:
                qdate = qs
                kind = "date"
        is_pg = _is_pg_db(_ddb)
        # ── column kinds (by output alias) ──
        _fmap = {str(getattr(f, "name", "")).lower(): f for f in (fields or []) if getattr(f, "name", None)}
        col_kinds = []  # (alias, kind)
        for _c in base_cols:
            _al = getattr(_c, "alias", None) or getattr(_c, "name", "") or ""
            if not _al:
                continue
            _ct = str(getattr(_c, "col_type", "") or "direct")
            if _ct == "fk_lookup" or str(getattr(_c, "id", "") or "").endswith("_fk_proxy"):
                col_kinds.append((_al, "text"))
                continue
            if _ct in ("computed", "aggregated"):
                col_kinds.append((_al, "text"))
                continue
            # direct: field data type (prefer a field on the detail table)
            _raw = (getattr(_c, "expr", None) or getattr(_c, "name", None) or "")
            _keys = set(re.findall(r"[\[{]([^\].\[{}]+)[\]}]", str(_raw)))
            for _qm in re.finditer(r"[\[{]([A-Za-z0-9_][A-Za-z0-9_.]*)[\]}]", str(_raw)):
                _qp = [p.strip() for p in _qm.group(1).split(".") if p.strip()]
                if _qp:
                    _keys.add(_qp[-1])
            _dt = ""
            for _k in _keys:
                _fl = _fmap.get(str(_k).strip().lower())
                if _fl:
                    _dt = _date_kind_of(getattr(_fl, "data_type", ""))
                    _t = str(getattr(_fl, "data_type", "") or "").upper()
                    if _dt:
                        break
                    if any(w in _t for w in ("INT", "NUM", "NUMBER", "DECIMAL", "FLOAT", "DOUBLE")):
                        _dt = "number"
                        break
            if _dt == "number":
                col_kinds.append((_al, "number"))
            elif _dt in ("date", "datetime"):
                col_kinds.append((_al, "date"))
            else:
                col_kinds.append((_al, "text"))
        # ── conditions on output aliases ──
        try:
            _ms_txt = _is_mssql_db(_ddb)
        except Exception:
            _ms_txt = False

        def _txt(_al):
            if _ms_txt:
                return f"CAST({_q(_al)} AS NVARCHAR(MAX))"
            return f"CAST({_q(_al)} AS TEXT)" if is_pg else f"TO_CHAR({_q(_al)})"
        conds, params = [], {}
        if kind == "number" and qnum is not None:
            for i, (_al, _k) in enumerate(col_kinds):
                if _k == "number":
                    conds.append(f"{_q(_al)} = :n{i}")
                    params[f"n{i}"] = qnum
            pat = f"%{qs}%"
            for i, (_al, _k) in enumerate(col_kinds):
                if _k == "text":
                    conds.append(f"UPPER({_txt(_al)}) LIKE :t{i}")
                    params[f"t{i}"] = pat.upper() if hasattr(pat, "upper") else pat
        elif kind == "date" and qdate:
            for i, (_al, _k) in enumerate(col_kinds):
                if _k in ("date",):
                    conds.append(f"CAST({_q(_al)} AS DATE) = TO_DATE(:d{i}, 'YYYY-MM-DD')")
                    params[f"d{i}"] = qdate
            pat = f"%{qs}%"
            for i, (_al, _k) in enumerate(col_kinds):
                if _k == "text":
                    conds.append(f"UPPER({_txt(_al)}) LIKE :t{i}")
                    params[f"t{i}"] = pat.upper() if hasattr(pat, "upper") else pat
        else:
            pat = f"%{qs}%".upper()
            for i, (_al, _k) in enumerate(col_kinds):
                conds.append(f"UPPER({_txt(_al)}) LIKE :t{i}")
                params[f"t{i}"] = pat
        if not conds:
            return {"master_values": [], "count": 0, "truncated": False, "sql": ""}
        inner = f"SELECT {_q(dkey)} AS \"__mkey\", {select_clause} FROM {from_q}"
        where = " OR ".join(f"({c})" for c in conds)
        try:
            _ms_ddb = _is_mssql_db(_ddb)
        except Exception:
            _ms_ddb = False
        if _ms_ddb:
            sql = f"SELECT DISTINCT TOP {int(limit_keys) + 1} \"__mkey\" FROM ({inner}) t WHERE {where}"
        elif is_pg:
            sql = f"SELECT DISTINCT \"__mkey\" FROM ({inner}) t WHERE {where} LIMIT {int(limit_keys) + 1}"
        else:
            sql = f"SELECT DISTINCT \"__mkey\" FROM ({inner}) WHERE {where} AND ROWNUM <= {int(limit_keys) + 1}"
        cur = self._exec_on(_ddb, sql, params)
        try:
            fetched = cur.fetchall() if hasattr(cur, "fetchall") else []
        finally:
            try:
                cur.close()
            except Exception:
                pass
        vals = []
        for r in fetched:
            v = r[0] if not isinstance(r, dict) else list(r.values())[0]
            if v is None:
                continue
            try:
                import datetime as _dtm
                if isinstance(v, (_dtm.date, _dtm.datetime)):
                    v = v.isoformat()
            except Exception:
                pass
            if v not in vals:
                vals.append(v)
        truncated = len(vals) > int(limit_keys)
        return {"master_values": vals[:int(limit_keys)], "count": len(vals),
                "truncated": truncated, "sql": sql}

    # ── Inferred joins (diagram: effective JOINs incl. non-saved) ─────

    def inferred_joins(self) -> List[Dict[str, Any]]:
        """JOINs between the report's tables: explicit <links> + inferred.

        Each item: {from_table, from_col, to_table, to_col, from_conn, to_conn,
        inferred: bool}. Inference: diagram links, FK metadata, shared names.
        """
        out: List[Dict[str, Any]] = []
        try:
            fields = getattr(self, "fields", []) or []
            tables: List[str] = []
            for f in fields:
                ts = getattr(f, "table_source", None)
                if ts:
                    t = self._norm_table(ts)
                    if t and t not in tables:
                        tables.append(t)
            try:
                det = self.compiler.detail() if hasattr(self.compiler, "detail") else None
                det = det.to_dict() if det is not None and not isinstance(det, dict) else det
                if det and det.get("table"):
                    t = self._norm_table(det.get("table"))
                    if t and t not in tables:
                        tables.append(t)
            except Exception:
                pass
            disp: Dict[str, str] = {}
            for f in fields:
                ts = getattr(f, "table_source", None)
                if ts:
                    d = str(ts).strip().strip('"')
                    if "." in d:
                        d = d.split(".")[-1]
                    disp.setdefault(self._norm_table(d), d)
            seen = set()
            try:
                for l in (getattr(self, "report_links", []) or []):
                    ft = self._norm_table(l.get("from_table") or "")
                    tt = self._norm_table(l.get("to_table") or "")
                    if ft and tt:
                        seen.add((ft, tt))
                        seen.add((tt, ft))
            except Exception:
                pass
            try:
                _det = self.compiler.detail() if hasattr(self.compiler, "detail") else None
                _det = _det.to_dict() if _det is not None and not isinstance(_det, dict) else _det
                _dtn = self._norm_table((_det or {}).get("table") or "")
            except Exception:
                _dtn = ""
            for i in range(len(tables)):
                for j in range(i + 1, len(tables)):
                    a, b = tables[i], tables[j]
                    if (a, b) in seen:
                        continue
                    try:
                        aconn = self._conn_key_of_table(a)
                        bconn = self._conn_key_of_table(b)
                        adb = self._db_for_conn(aconn)
                        bdb = self._db_for_conn(bconn)
                        asch = self._schema_for_table(a, aconn, self.metadata.get("schema"))
                        bsch = self._schema_for_table(b, bconn, self.metadata.get("schema"))
                        _rel = "one_to_many" if (_dtn and (a == _dtn or b == _dtn)) else "one_to_one"
                        if self._same_db(adb, bdb):
                            key = self._infer_join_key(a, b, asch, adb, bsch)
                            if not key:
                                continue
                            out.append({"from_table": disp.get(a, a), "from_col": key[0],
                                        "to_table": disp.get(b, b), "to_col": key[1],
                                        "from_conn": aconn or "", "to_conn": bconn or "",
                                        "rel_type": _rel, "relType": _rel,
                                        "inferred": True})
                        else:
                            key = self._infer_join_key(a, b, asch, adb, bsch, bdb)
                            if not key:
                                continue
                            out.append({"from_table": disp.get(a, a), "from_col": key[0],
                                        "to_table": disp.get(b, b), "to_col": key[1],
                                        "from_conn": aconn or "", "to_conn": bconn or "",
                                        "rel_type": _rel, "relType": _rel,
                                        "inferred": True})
                    except Exception:
                        continue
            # explicit links first-class (inferred=False)
            try:
                for l in (getattr(self, "report_links", []) or []):
                    _lr = (l.get("rel_type") or l.get("relType") or l.get("rel") or "")
                    if not _lr and _dtn:
                        _fn = self._norm_table(l.get("from_table") or "")
                        _tn = self._norm_table(l.get("to_table") or "")
                        _lr = "one_to_many" if (_fn == _dtn or _tn == _dtn) else "one_to_one"
                    out.append({"from_table": l.get("from_table", ""), "from_col": l.get("from_col", ""),
                                "to_table": l.get("to_table", ""), "to_col": l.get("to_col", ""),
                                "from_conn": l.get("from_conn", "") or "", "to_conn": l.get("to_conn", "") or "",
                                "rel_type": _lr or "one_to_one", "relType": _lr or "one_to_one",
                                "match": l.get("match") or "exact", "pattern": l.get("pattern") or "",
                                "inferred": False})
            except Exception:
                pass
        except Exception:
            pass
        return out

    # ── Group values for sidebar (ALL records, server-side) ───────────────
    def group_values(self, column_alias: str, filters: List[Dict[str, Any]] | None = None,
                     bucket=None, limit: int = 500,
                     active_table: str | None = None) -> Dict[str, Any]:
        """Group keys + counts over ALL matching records (not the page).

        bucket: None (exact values) | 'day' | 'month' | 'year' (date truncation)
                | number (numeric range size → returns lo bounds).
        Returns {groups:[{value,count}], column, bucket, truncated}.
        """
        self._validate_no_exact_dupes()
        self._ensure_api_staged()
        key = str(column_alias or "").strip()
        if not key:
            raise ValueError("column required")
        try:
            n = max(1, min(int(limit or 500), 500))
        except (TypeError, ValueError):
            n = 500
        _gp = self._plan_structure(active_table, filters, None, key)
        self._reject_deferred(_gp, "في قيم التجميع")
        _gf = self._apply_remote_filters(filters, _gp)
        gfields = getattr(self, "fields", [])
        where_clause, params = _build_where(_gf or [], columns=_gp["columns"], fields=gfields,
                                            table_map=_gp["table_map"], conn_map=self._conn_map(), rules=getattr(self, "rules", []),
                                            default_tables=self._rx_defaults())
        _gdb = _resolve_filter_field(key, _gp["columns"], gfields, _gp["table_map"], self._conn_map(), getattr(self, "rules", []),
                                     default_tables=self._rx_defaults())
        gdb = _gp["base_db"]
        try:
            _is_pg = _is_pg_db(gdb)
        except Exception:
            _is_pg = False
        params = dict(params)
        if bucket is None:
            gexpr = _gdb
        elif isinstance(bucket, str) and bucket.lower() in ("day", "month", "year"):
            if not self._is_date_expr(key, _gp["columns"], gfields):
                raise ValueError(f'التجميع الزمني يتطلب عمود تاريخ — "{key}" ليس تاريخاً')
            b = bucket.lower()
            if _is_pg:
                if b == "day":
                    gexpr = f"TO_CHAR(({_gdb})::date, 'YYYY-MM-DD')"
                elif b == "month":
                    gexpr = f"TO_CHAR(DATE_TRUNC('month', {_gdb})::date, 'YYYY-MM-DD')"
                else:
                    gexpr = f"TO_CHAR(DATE_TRUNC('year', {_gdb})::date, 'YYYY')"
            else:
                if b == "day":
                    gexpr = f"TO_CHAR(TRUNC({_gdb}), 'YYYY-MM-DD')"
                elif b == "month":
                    gexpr = f"TO_CHAR(TRUNC({_gdb}, 'MM'), 'YYYY-MM-DD')"
                else:
                    gexpr = f"TO_CHAR(TRUNC({_gdb}, 'YYYY'), 'YYYY')"
        else:
            try:
                size = float(bucket)
            except (TypeError, ValueError):
                raise ValueError(f"حجم تجميع غير صالح: {bucket}")
            if size <= 0:
                raise ValueError("حجم التجميع يجب أن يكون أكبر من صفر")
            gexpr = f"(FLOOR({_gdb} / :bs) * :bs)"
            params["bs"] = size
        sql = f"SELECT {gexpr} AS gv, COUNT(*) as cnt FROM {_gp['from_q']}{where_clause} GROUP BY {gexpr} ORDER BY cnt DESC"
        if _is_pg:
            sql += " LIMIT :lim"
        else:
            sql += " OFFSET 0 ROWS FETCH NEXT :lim ROWS ONLY"
        params["lim"] = n
        try:
            cur = self._exec_on(gdb, sql, params)
            try:
                fetched = cur.fetchall() if hasattr(cur, "fetchall") else []
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
        except Exception as e:
            raise RuntimeError(f"Grouping failed: {e}") from e
        out = []
        for r in fetched:
            try:
                v, c = (list(r.values())[0], list(r.values())[1]) if isinstance(r, dict) else (r[0], r[1])
            except Exception:
                continue
            if v is None:
                continue
            v = _fmt_cell(v)
            try:
                import decimal as _dec
                if isinstance(v, _dec.Decimal):
                    v = int(v) if v == int(v) else float(v)
            except Exception:
                pass
            out.append({"value": v, "count": int(c or 0)})
        return {"groups": out, "column": key, "bucket": bucket, "truncated": len(fetched) >= n}

    @staticmethod
    def _is_date_expr(field: str, columns=None, fields=None) -> bool:
        """True when the field behind a display column is DATE/TIMESTAMP typed."""
        from .namespaces import _resolve_expression as _rx
        col = _find_column_for_field(field, columns)
        raw = ""
        if col is not None:
            raw = str(getattr(col, "expr", None) or getattr(col, "name", None) or "")
        fmap = {str(getattr(f, "name", "")).lower(): f for f in (fields or []) if getattr(f, "name", None)}
        refs = set(re.findall(r"[\[{]([^\].\[{}]+)[\]}]", raw or ""))
        for _qm in re.finditer(r"[\[{]([A-Za-z0-9_][A-Za-z0-9_.]*)[\]}]", raw or ""):
            _qp = [p.strip() for p in _qm.group(1).split(".")]
            if len(_qp) > 1 and _qp[-1]:
                refs.add(_qp[-1])
        if not refs and col is None:
            fl = fmap.get(str(field).strip().lower())
            if fl:
                refs = {str(getattr(fl, "name", field))}
        if not refs:
            return False
        for r in refs:
            fl = fmap.get(str(r).strip().lower())
            if not fl:
                return False
            if _date_kind_of(getattr(fl, "data_type", "")) not in ("date", "datetime"):
                return False
        return True

    # ── Grouping helper for sidebar ───────────────────────────────────
    def groups(self, group_by: str, filters: List[Dict[str, Any]] | None = None, active_table: str | None = None) -> List[Dict[str, Any]]:
        """Get group metrics for sidebar: [{value, count}, ...]"""
        self._validate_no_exact_dupes()
        self._ensure_api_staged()
        _gp = self._plan_structure(active_table, filters, None, group_by)
        self._reject_deferred(_gp, "في التجميع")
        _gf = self._apply_remote_filters(filters, _gp)
        gfields = getattr(self, "fields", [])
        where_clause, params = _build_where(_gf, columns=_gp["columns"], fields=gfields, table_map=_gp["table_map"], conn_map=self._conn_map(), rules=getattr(self, "rules", []), default_tables=self._rx_defaults())
        _gdb = _resolve_filter_field(group_by, _gp["columns"], gfields, _gp["table_map"], self._conn_map(), getattr(self, "rules", []), default_tables=self._rx_defaults())
        sql = f"SELECT {_gdb}, COUNT(*) as cnt FROM {_gp['from_q']}{where_clause} GROUP BY {_gdb} ORDER BY cnt DESC"
        gdb = _gp["base_db"]
        try:
            cur = self._exec_on(gdb, sql, params)
            try:
                cols = [d[0].lower() for d in cur.description] if cur.description else []
                return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
            finally:
                try: cur.close()
                except: pass
        except Exception as e:
            raise RuntimeError(f"Grouping failed: {e}") from e

    # ── Summary totals (chart compare mode) ─────────────────────────────

    @staticmethod
    def _is_number_dtype(dtype: Optional[str]) -> bool:
        t = str(dtype or "").upper()
        return any(k in t for k in ("NUMBER", "INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC")) or t.strip() == ""

    def _summary_item(self, col, fields, field_table, base_norm):
        """Derive (sql_expr, table_norm) grand-total expression for a column.

        Windowed `F(...) OVER (...)` -> F(...) (OVER stripped); bare numeric
        field -> SUM(field); COUNT(*) stays. Returns (None, None) when not
        derivable (e.g. dates).
        """
        from .namespaces import _resolve_expression as _rx
        raw = (getattr(col, "expr", None) or getattr(col, "name", None) or "").strip()
        no_over = _strip_over(raw)
        fmap = {str(getattr(f, "name", "")).lower(): f for f in (fields or []) if getattr(f, "name", None)}
        found = []
        for m in re.finditer(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", no_over, re.IGNORECASE):
            parsed = self._split_agg_call(no_over, m.start())
            if parsed:
                found.append((m.group(1).upper(), parsed[0]))
        if found:
            func, inner = found[0]
            if func == "COUNT" and inner.strip() == "*":
                return "COUNT(*)", base_norm
            refs = {r for r in self._refs_in_text(inner, field_table)}
            if not refs:
                return None, None
            tables = {field_table[r] for r in refs}
            if len(tables) != 1:
                return None, None
            for r in refs:
                fl = fmap.get(r)
                if fl and not self._is_number_dtype(getattr(fl, "data_type", None)):
                    return None, None
            inner_sql = _rx(inner, fields, {}, set(), None,
                              default_tables=self._rx_defaults())
            if func in ("SUM", "AVG", "COUNT"):
                return f"NVL({func}({inner_sql}), 0)", next(iter(tables))
            return f"{func}({inner_sql})", next(iter(tables))
        # Bare single numeric field -> SUM
        refs = {r for r in self._refs_in_text(no_over, field_table)}
        if len(refs) == 1:
            r = next(iter(refs))
            fl = fmap.get(r)
            if fl and self._is_number_dtype(getattr(fl, "data_type", None)) and field_table[r] == base_norm:
                return f"SUM({_q(fmap[r].name)})", base_norm
        if no_over.strip().upper() == "COUNT(*)":
            return "COUNT(*)", base_norm
        return None, None

    # ---- Distributed execution -------------------------------------------
    def _execute_distributed(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run multi-connection reports without copying rows to a staging
        area: fetch each connection independently with its own live engine,
        then join in Python and stream merged rows.

        Required shape:
        - activeTable / base is the primary (the FROM table); live engine
          via _db_for_conn / _mssql_db_for / _resolve_live_db.
        - Each secondary table (in columns / links / merges / table_opts)
          gets its own live engine; rows are joined on the link's
          (base_col, sec_col) key.
        - The merge spec (in exec_plan['merges']) drives the per-row key
          fetch, but here we batch the join by streaming the primary
          fetchmany and preloading a {key: row} dict per secondary.

        Returns the same shape as execute() (rows, total, page, …).
        """
        from collections import defaultdict
        self._report_progress({"stage": "distributed", "text": "بدء التنفيذ الموزع…"})
        filters = payload.get("filters") or payload.get("activeFilters") or []
        sort = payload.get("sort") or payload.get("activeSort")
        page = int(payload.get("page") or 1)
        page_size = payload.get("pageSize") or payload.get("page_size") or 50
        try:
            page_size = int(page_size)
        except Exception:
            page_size = 50
        active_table = payload.get("activeTable") or payload.get("table")
        # Build plan WITHOUT routing base_db through staging. We use the
        # original self.db so direct connections work normally.
        try:
            self._plan_cache = {}
        except Exception:
            pass
        exec_plan = self._plan_structure(active_table, filters, sort, None)
        base_norm = exec_plan.get("base_norm")
        if not base_norm:
            raise ValueError("لم يتم تحديد الجدول الأساسي.")
        if exec_plan.get("gw_remote") or exec_plan.get("gw_deferred"):
            raise ValueError(
                "الشرط العام على جدول باتصال آخر غير مدعوم في التنفيذ الموزع — "
                "نفّذ التقرير بالمسار العادي.")
        primary_db, primary_engine = self._resolve_live_db(exec_plan.get("base_conn"))
        if primary_db is None:
            raise ValueError(
                "تعذر إيجاد اتصال مباشر للجدول الأساسي — راجع إعدادات الاتصال.")

        # Identify secondaries: tables in columns + links not equal to base.
        sec_specs = self._distributed_secondary_specs(exec_plan, base_norm)
        # Build SQL for primary (filter-only, no JOIN with secondaries).
        primary_sql, primary_params, _ = self._compile_distributed_primary(
            exec_plan, base_norm, filters)
        self._report_progress({"stage": "primary_fetch", "table": base_norm.lower(),
                               "text": f"جلب {base_norm.lower()} من الاتصال الأساسي…"})

        primary_rows = self._stream_primary(primary_db, primary_engine, primary_sql,
                                            primary_params, page, page_size)
        total_primary = len(primary_rows) if isinstance(primary_rows, list) else (
            primary_rows.get("total") if isinstance(primary_rows, dict) else 0)
        if isinstance(primary_rows, dict):
            primary_rows = primary_rows.get("rows") or []

        # Preload each secondary by its join key in one shot per request.
        # Each secondary may live on its own connection.
        sec_indexes = []
        for spec in sec_specs:
            self._report_progress({"stage": "secondary_fetch",
                                   "table": spec["norm"].lower(),
                                   "text": f"جلب {spec['norm'].lower()} ({spec.get('gid', '?')})…"})
            try:
                idx = self._index_secondary(spec, primary_rows)
                sec_indexes.append(idx)
            except Exception as _se:
                self._report_progress({"stage": "secondary_fetch_error",
                                       "table": spec["norm"].lower(),
                                       "text": str(_se)[:200]})
                sec_indexes.append({})

        self._report_progress({"stage": "merge", "rows": len(primary_rows),
                               "text": "دمج النتائج…"})
        merged = self._merge_lazy(primary_rows, sec_indexes, sec_specs, sort)

        if isinstance(merged, list):
            result_rows = [{k: _fmt_cell(v) for k, v in _r.items()} for _r in merged]
            total = max(total_primary, len(merged))
        else:
            result_rows = []
            total = 0

        total_pages = (total + page_size - 1) // page_size if page_size else 1
        try:
            _groups_list = [g.to_dict() for g in (self.compiler.groups() if hasattr(self.compiler, "groups") else [])]
        except Exception:
            _groups_list = []
        try:
            _meta_out = dict(self.metadata) if isinstance(self.metadata, dict) else {}
        except Exception:
            _meta_out = {}
        _meta_out["groups"] = _groups_list
        return {
            "rows": result_rows,
            "total": total,
            "page": int(page),
            "pageSize": page_size,
            "totalPages": total_pages,
            "sql": primary_sql,
            "params": primary_params,
            "warnings": self._render_api_warnings(),
            "columns": [c.to_dict() for c in self.columns],
            "fields": [f.to_dict() for f in getattr(self, "fields", [])],
            "rules": [r.to_dict() for r in getattr(self, "rules", [])],
            "groups": _groups_list,
            "metadata": _meta_out,
            "connections": [c.to_dict() for c in self.connections],
            "charts": [c.to_dict() for c in self.charts],
            "summary": {},
            "detail": (lambda d: d.to_dict() if d is not None and not isinstance(d, dict) else d)(
                self.compiler.detail() if hasattr(self.compiler, "detail") else None),
            "distributed": True,
        }

    def _resolve_live_db(self, conn_key):
        """Resolve a live engine for a connection key (no staging)."""
        if not conn_key:
            return self.db, self._stage_engine_of(self.db)
        # Same DB as primary?
        try:
            if str(conn_key) == str(getattr(self, "primary_conn", None)):
                return self.db, self._stage_engine_of(self.db)
        except Exception:
            pass
        if conn_key in (self.databases or {}):
            return self.databases[conn_key], self._stage_engine_of(self.databases[conn_key])
        # Try direct SqlServerDirect.
        try:
            if conn_key == self._get_direct_gid():
                return self._mssql_db_for(conn_key), "mssql"
        except Exception:
            pass
        # Fallback: live engine for the connection's OWN database.
        try:
            _live = self._live_engine_for_gid(conn_key)
            if _live is not None:
                try:
                    _eng2 = str(getattr(self._dj_conn(conn_key), "engine", "") or "").lower()
                except Exception:
                    _eng2 = ""
                return _live, ("mssql" if _eng2 == "sqlserver" else "postgres")
        except Exception:
            pass
        return None, ""

    def _distributed_secondary_specs(self, exec_plan, base_norm):
        """Return [(norm, conn_key, link)] for each cross-DB secondary."""
        out = []
        try:
            remote = exec_plan.get("remote") or {}
            for sec_norm, info in remote.items():
                if sec_norm == base_norm:
                    continue
                if not info:
                    continue
                try:
                    _dms = self._link_match_spec(base_norm, sec_norm)
                except Exception:
                    _dms = {"match": "exact", "pattern": ""}
                out.append({
                    "norm": sec_norm,
                    "gid": str(info.get("gid") or info.get("conn_key") or ""),
                    "key": info.get("key"),
                    "info": info,
                    "match": _dms["match"],
                    "pattern": _dms["pattern"],
                })
        except Exception:
            pass
        # Same-DB fuzzy secondaries (no SQL JOIN under the distributed
        # runner) → fetch live + Python match.
        try:
            for _ls in (exec_plan.get("local_sec") or []):
                if _ls == base_norm or any(s.get("norm") == _ls for s in out):
                    continue
                try:
                    _lms = self._link_match_spec(base_norm, _ls)
                except Exception:
                    continue
                if _lms["match"] == "exact":
                    continue
                try:
                    _lkey = self._infer_join_key(base_norm, _ls, None, None, None)
                except Exception:
                    _lkey = None
                if not _lkey:
                    continue
                out.append({"norm": _ls,
                            "gid": str(exec_plan.get("base_conn") or ""),
                            "key": _lkey, "info": {},
                            "match": _lms["match"], "pattern": _lms["pattern"]})
        except Exception:
            pass
        # Also look at merge specs (cross-DB secondaries from Python merges).
        try:
            merges = exec_plan.get("merges") or []
            for m in merges:
                t = m.get("table")
                if not t or t == base_norm:
                    continue
                _existing = next((s for s in out if s.get("norm") == t), None)
                if _existing:
                    continue
                out.append({"norm": t, "gid": "", "key": None, "info": {"gid": ""}})
        except Exception:
            pass
        return out

    def _compile_distributed_primary(self, exec_plan, base_norm, filters):
        """Compile a base-only SELECT (no cross-DB joins). Returns (sql, params, plan)."""
        base_cols = []
        try:
            fields = getattr(self, "fields", []) or []
            for f in fields:
                if self._norm_table(getattr(f, "table_source", "") or "") == base_norm:
                    base_cols.append(str(getattr(f, "name", "") or ""))
        except Exception:
            pass
        # Distinct base columns actually requested.
        _want = []
        seen = set()
        try:
            for c in (exec_plan.get("columns") or []):
                for r in self._refs_in_text(getattr(c, "expr", "") or "", {}):
                    if r.lower() in {x.lower() for x in base_cols} and r.lower() not in seen:
                        _want.append(r); seen.add(r.lower())
        except Exception:
            pass
        if not _want:
            _want = base_cols
        base_schema = exec_plan.get("base_schema") or ""
        sch_q = f"{_q(base_schema)}." if base_schema else ""
        # T-SQL safe identifier quoting for primary.
        _qsel = ", ".join([_q(c) for c in _want]) if _want else "*"
        sql = f"SELECT {_qsel} FROM {sch_q}{_q(base_norm)}"
        where_clause, where_params = _build_where(
            filters, columns=exec_plan.get("columns") or self.columns,
            fields=getattr(self, "fields", []),
            table_map=exec_plan.get("table_map") or {},
            conn_map=self._conn_map(), rules=getattr(self, "rules", []),
            default_tables=self._rx_defaults(),
        )
        where_clause = self._apply_general_where(
            where_clause, exec_plan.get("table_map") or {}, self._conn_map(),
            exec_plan.get("gw_base"))
        if where_clause:
            sql += where_clause
        return sql, where_params, exec_plan

    def _stream_primary(self, db, engine, sql, params, page, page_size):
        """Fetch primary rows directly from its live engine.

        SQL Server: stream via pyodbc + fetchmany (no full copy).
        Postgres / Oracle: standard cursor.fetchall.
        """
        if engine == "mssql":
            try:
                db.connect()
            except Exception:
                pass
            import pyodbc as _p
            # Apply LIMIT via OFFSET/FETCH (sqlserver 2012+).
            try:
                _offset = max(0, (int(page) - 1) * int(page_size))
                sql_paged = sql + f" ORDER BY {_q('1')} OFFSET {_offset} ROWS FETCH NEXT {int(page_size)} ROWS ONLY"
            except Exception:
                sql_paged = sql
            cur = db.conn.cursor()
            try:
                exec_sql = _mssql_transpile_sql(sql_paged, convert_binds=True)
                values = _mssql_bind_values(sql_paged, params)
                cur.execute(exec_sql, values)
                names = [d[0] for d in (cur.description or [])]
                rows = [dict(zip(names, r)) for r in cur.fetchall()]
            finally:
                try: cur.close()
                except Exception: pass
            return {"rows": rows, "total": len(rows)}
        # Default path: standard cursor.
        try:
            db.connect()
        except Exception:
            pass
        cur = self._exec_on(db, sql, params)
        try:
            names = [d[0] for d in (cur.description or [])] if cur.description else []
            rows = [dict(zip(names, r)) for r in cur.fetchall()]
        finally:
            try: cur.close()
            except Exception: pass
        return {"rows": rows, "total": len(rows)}

    def _index_secondary(self, spec, primary_rows):
        """Bulk-fetch the secondary table keyed by every distinct key in
        primary_rows. Returns {key_value: row_or_None}.

        When the link key is not known (no join), we return {} (skip).
        """
        info = spec.get("info") or {}
        key = spec.get("key")
        if not key:
            return {}
        bcol, scol = (key[0], key[1]) if isinstance(key, (list, tuple)) else ("", "")
        if not bcol or not scol:
            return {}
        try:
            _im = str(spec.get("match") or "exact").strip().lower()
        except Exception:
            _im = "exact"
        if _im != "exact":
            # Fuzzy link: full secondary scan; _merge_lazy applies the
            # Python predicate per primary row (first hit wins).
            sec_norm = spec["norm"]
            sec_db, sec_engine = self._resolve_live_db(spec.get("gid"))
            if sec_db is None:
                return {}
            try:
                sec_db.connect()
            except Exception:
                pass
            _fcur = self._exec_on(sec_db, f"SELECT * FROM {_q(sec_norm)}", {})
            try:
                _names = [d[0] for d in (_fcur.description or [])] if _fcur.description else []
                _srows = [dict(zip(_names, r)) for r in _fcur.fetchall()]
            finally:
                try:
                    _fcur.close()
                except Exception:
                    pass
            return {"__fuzzy__": True, "rows": _srows, "match": _im,
                    "pattern": str(spec.get("pattern") or ""),
                    "bcol": bcol, "scol": scol}
        # Collect distinct base key values from primary_rows.
        keys = []
        seen = set()
        for r in primary_rows:
            v = r.get(bcol)
            if v is None:
                continue
            try:
                if v in seen:
                    continue
                seen.add(v)
                keys.append(v)
            except Exception:
                continue
        if not keys:
            return {}
        sec_norm = spec["norm"]
        sec_db, sec_engine = self._resolve_live_db(spec.get("gid"))
        if sec_db is None:
            return {}
        # Build SELECT * FROM sec WHERE scol IN (?, ?, …) — text-compared
        # so int keys match varchar columns (and vice versa).
        ph = "%s"
        placeholders = ", ".join([ph] * len(keys))
        _cast, _vals = self._text_keys(keys)
        _sc = self._text_cast(_q(scol), sec_db) if _cast else _q(scol)
        sql = f"SELECT * FROM {_q(sec_norm)} WHERE {_sc} IN ({placeholders})"
        params = list(_vals)
        try:
            sec_db.connect()
        except Exception:
            pass
        cur = self._exec_on(sec_db, sql, params)
        try:
            names = [d[0] for d in (cur.description or [])] if cur.description else []
            sec_rows = [dict(zip(names, r)) for r in cur.fetchall()]
        finally:
            try: cur.close()
            except Exception: pass
        idx = {}
        for r in sec_rows:
            v = r.get(scol)
            if v is not None and v not in idx:
                idx[v] = r
        return idx

    def _merge_lazy(self, primary_rows, sec_indexes, sec_specs, sort):
        """Merge primary rows with preloaded secondary indexes, with progress."""
        total = len(primary_rows)
        step = max(1, total // 50) if total else 1
        merged = []
        for i, r in enumerate(primary_rows):
            for spec, idx in zip(sec_specs, sec_indexes):
                key = spec.get("key")
                if not key:
                    continue
                bcol = key[0] if isinstance(key, (list, tuple)) else ""
                if not bcol:
                    continue
                if isinstance(idx, dict) and idx.get("__fuzzy__"):
                    try:
                        from .xsql import fuzzy_link_match as _flm
                        _bv = r.get(idx.get("bcol") or bcol)
                        _hit = None
                        if _bv is not None:
                            for _sr in (idx.get("rows") or []):
                                try:
                                    if _flm(idx.get("match"), idx.get("pattern"),
                                            _bv, _sr.get(idx.get("scol"))):
                                        _hit = _sr
                                        break
                                except Exception:
                                    continue
                        if _hit:
                            prefix = f"{spec['norm']}."
                            for kn, kv in _hit.items():
                                r[prefix + kn] = kv
                    except Exception:
                        pass
                    continue
                v = r.get(bcol)
                if v is None:
                    continue
                sec_row = idx.get(v) if isinstance(idx, dict) else None
                if not sec_row:
                    continue
                # Merge columns: prefix to avoid name collision.
                prefix = f"{spec['norm']}."
                for kn, kv in sec_row.items():
                    r[prefix + kn] = kv
            merged.append(r)
            if (i % step) == 0:
                self._report_progress({"stage": "merge", "rows": i + 1,
                                       "total": total,
                                       "text": f"دمج… {i+1}/{total}"})
        # Optional sort.
        if sort:
            try:
                col = sort.get("column") if isinstance(sort, dict) else None
                direction = (sort.get("direction") if isinstance(sort, dict) else "asc") or "asc"
                if col and col in (merged[0] if merged else {}):
                    merged.sort(key=lambda x: (x.get(col) is None, x.get(col)),
                                reverse=(direction.lower() == "desc"))
            except Exception:
                pass
        return merged

    def _summarize(self, aliases, filters, active_table) -> Dict[str, Any]:
        """Grand totals {alias: number} for chart compare mode (best effort).

        Fan-out safe: base summaries use join-free FROM; secondary summaries
        use EXISTS semi-joins (same DB) or two-phase key fetches (cross-DB).
        """
        out: Dict[str, Any] = {}
        if not aliases:
            return out
        try:
            plan = self._plan_structure(active_table, filters, None, None)
            self._reject_deferred(plan, "في الملخصات")
            filters2 = self._apply_remote_filters(filters, plan)
            fields = getattr(self, "fields", []) or []
            field_table = self._field_table_map()
            base_norm = plan["base_norm"]
            base_db = plan["base_db"]
            base_schema = plan.get("base_schema")
            base_schema_q = _q(base_schema) if base_schema else ""
            cols_for_summary = self._inline_column_refs(self.columns)
            # base filters (post remote-resolution) for semi-joins
            def _base_tables_of(fld, columns):
                ts = set()
                for r in self._refs_in_text(str(fld or ""), field_table):
                    ts.add(field_table[r])
                try:
                    col = _find_column_for_field(fld, columns)
                except Exception:
                    col = None
                if col is not None:
                    for r in self._refs_in_text(getattr(col, "expr", "") or "", field_table):
                        ts.add(field_table[r])
                return ts
            base_only_filters = []
            for f in (filters2 or []):
                members = [sf for sf in f.get("any", []) if isinstance(sf, dict)] \
                    if isinstance(f, dict) and isinstance(f.get("any"), (list, tuple)) else [f]
                if all(not (_base_tables_of(m.get("field") or m.get("column") or m.get("name") or "", cols_for_summary) - {base_norm}) for m in members):
                    base_only_filters.append(f)
            for alias in aliases:
                try:
                    col = _find_column_for_field(alias, cols_for_summary)
                    if col is None:
                        continue
                    expr, tbl = self._summary_item(col, fields, field_table, base_norm)
                    if not expr or not tbl:
                        continue
                    if tbl == base_norm:
                        _bd = plan.get("base_disp") or base_norm
                        from_q = f"{base_schema_q + '.' if base_schema_q else ''}{_q(_bd)}"
                        wc, wp = _build_where(base_only_filters, columns=cols_for_summary, fields=fields, table_map=None, conn_map=self._conn_map(), rules=getattr(self, "rules", []), default_tables=self._rx_defaults())
                        cur = self._exec_on(base_db, f"SELECT {expr} as s FROM {from_q}{wc}", wp)
                    else:
                        # Secondary table: same-DB -> EXISTS semi-join; cross-DB -> two-phase keys
                        sconn = self._conn_key_of_table(tbl)
                        sdb = self._db_for_conn(sconn)
                        ssch = self._schema_for_table(tbl, sconn, self.metadata.get("schema"))
                        ssch_q = _q(ssch) if ssch else ""
                        sdisp = tbl
                        for fld in fields:
                            if self._norm_table(getattr(fld, "table_source", "") or "") == tbl:
                                d = str(getattr(fld, "table_source")).strip().strip('"')
                                sdisp = d.split(".")[-1] if "." in d else d
                                break
                        if self._same_db(sdb, base_db):
                            key = self._infer_join_key(base_norm, tbl, base_schema, base_db, ssch)
                            if not key:
                                continue
                            try:
                                _sm = self._link_match_spec(base_norm, tbl)
                            except Exception:
                                _sm = {"match": "exact"}
                            if _sm["match"] != "exact":
                                continue  # best effort: no fuzzy semi-joins in summaries
                            bcol, scol = key
                            bwc, bwp = _build_where(base_only_filters, columns=cols_for_summary, fields=fields, table_map=None, conn_map=self._conn_map(), rules=getattr(self, "rules", []), default_tables=self._rx_defaults())
                            # qualify base refs inside EXISTS subquery to base table
                            _bd = plan.get("base_disp") or base_norm
                            bfrom = f"{base_schema_q + '.' if base_schema_q else ''}{_q(_bd)}"
                            sub = f"SELECT 1 FROM {bfrom} WHERE {_q(bcol)} = {_q('sx')}.{_q(scol)}{bwc}"
                            sfrom = f"{ssch_q + '.' if ssch_q else ''}{_q(sdisp)} {_q('sx')}"
                            cur = self._exec_on(sdb, f"SELECT {expr} as s FROM {sfrom} WHERE EXISTS({sub})", bwp)
                        else:
                            # Cross-DB: fetch matching base keys, then aggregate remotely
                            key = self._infer_join_key(base_norm, tbl, base_schema, base_db, ssch, sdb)
                            if not key:
                                continue
                            try:
                                _sm2 = self._link_match_spec(base_norm, tbl)
                            except Exception:
                                _sm2 = {"match": "exact"}
                            if _sm2["match"] != "exact":
                                continue  # best effort: no fuzzy two-phase in summaries
                            bcol, scol = key
                            _bd2 = plan.get("base_disp") or base_norm
                            bfrom = f"{base_schema_q + '.' if base_schema_q else ''}{_q(_bd2)}"
                            bwc, bwp = _build_where(base_only_filters, columns=cols_for_summary, fields=fields, table_map=None, conn_map=self._conn_map(), rules=getattr(self, "rules", []), default_tables=self._rx_defaults())
                            c0 = self._exec_on(base_db, f"SELECT DISTINCT {_q(bcol)} FROM {bfrom}{bwc}", bwp)
                            try:
                                bkeys = [r[0] for r in c0.fetchall() if r[0] is not None]
                            finally:
                                try:
                                    c0.close()
                                except Exception:
                                    pass
                            if not bkeys:
                                out[alias] = 0
                                continue
                            parts, prm = [], {}
                            for _ci, _ch in enumerate(self._chunk(bkeys)):
                                phs = ", ".join([f":sk{_ci}_{i}" for i in range(len(_ch))])
                                _cast, _vals = self._text_keys(_ch)
                                for i, _v in enumerate(_vals):
                                    prm[f"sk{_ci}_{i}"] = _v
                                _sc = self._text_cast(_q(scol), sdb) if _cast else _q(scol)
                                parts.append(f"{_sc} IN ({phs})")
                            rwc = " WHERE " + " OR ".join(f"({p})" for p in parts)
                            cur = self._exec_on(sdb, f"SELECT {expr} as s FROM {ssch_q + '.' if ssch_q else ''}{_q(sdisp)}{rwc}", prm)
                    try:
                        row = cur.fetchone()
                        out[alias] = float(row[0]) if row and row[0] is not None else None
                    finally:
                        try:
                            cur.close()
                        except Exception:
                            pass
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _where_for_table(self, filters, table_norm: str, table_cols: Dict[str, str], db=None, schema: Optional[str] = None, _pfx: str = "", _joiner: str = " AND "):
        """WHERE over a secondary table: keep filters mappable by same-name columns.

        Returns (where, params) or (None, None) when a filter cannot map.
        {"any": [...]} / {"all": [...]} groups render parenthesized OR/AND
        (recursively). _pfx namespaces binds, _joiner joins siblings.
        """
        clauses = []
        params = {}
        for _gf in (filters or []):
            if isinstance(_gf, dict) and (isinstance(_gf.get("any"), (list, tuple)) or isinstance(_gf.get("all"), (list, tuple))):
                _is_or = isinstance(_gf.get("any"), (list, tuple))
                _members = list(_gf.get("any") if _is_or else _gf.get("all"))
                if not _members or any(not isinstance(_m, dict) for _m in _members):
                    return None, None
                _gp = f"{_pfx}g{len(clauses) + 1}_"
                _sw, _sp = self._where_for_table(_members, table_norm, table_cols, db, schema,
                                                 _pfx=_gp, _joiner=(" OR " if _is_or else " AND "))
                if not _sw:
                    return None, None
                clauses.append("(" + _sw[len(" WHERE "):] + ")")
                params.update(_sp or {})
        _singles = [f for f in (filters or []) if not (isinstance(f, dict) and (isinstance(f.get("any"), (list, tuple)) or isinstance(f.get("all"), (list, tuple))))]
        for i, f in enumerate(_singles, start=1):
            fld = str(f.get("field") or f.get("column") or f.get("name") or "")
            op = (f.get("op") or "equals").lower()
            # resolve filter field -> base field names
            names = {r for r in self._refs_in_text(fld, self._field_table_map())}
            try:
                col = _find_column_for_field(fld, self.columns)
            except Exception:
                col = None
            if col is not None:
                names |= {r for r in self._refs_in_text(getattr(col, "expr", "") or "", self._field_table_map())}
            key = fld.strip().lower()
            ftm = self._field_table_map()
            if key in ftm:
                names.add(key)
            # map each name to same-normalized column in target table
            mapped = []
            ok = True
            for n in names:
                target = n.upper()
                if target not in table_cols:
                    ok = False
                    break
                mapped.append(target)
            if not ok or not mapped:
                return None, None
            qcol = _q(self._orig_col(table_norm, mapped[0], db, schema))
            _dtype2 = table_cols.get(mapped[0], "")
            is_date = bool(_date_kind_of(_dtype2))
            date_kind2 = _date_kind_of(_dtype2)
            p = f"{_pfx}q{i}"

            def _db(ph, val):
                if is_date and isinstance(val, str):
                    v = _norm_dt_val(val)
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                        return f"TO_DATE({ph}, 'YYYY-MM-DD')"
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", v):
                        return f"TO_DATE({ph}, 'YYYY-MM-DD HH24:MI:SS')"
                return ph

            if op in ("equals", "=", "==", "eq"):
                _v2 = _norm_dt_val(f.get("value"))
                if date_kind2 == "datetime" and _is_date_only(_v2):
                    clauses.append(
                        f"{qcol} >= TO_DATE(:{p}, 'YYYY-MM-DD') "
                        f"AND {qcol} < TO_DATE(:{p}, 'YYYY-MM-DD') + 1")
                else:
                    clauses.append(f"{qcol}={_db(':'+p, _v2)}")
                params[p] = _v2
            elif op in ("gt", ">"):
                _v2 = _norm_dt_val(f.get("value"))
                clauses.append(f"{qcol} > {_db(':'+p, _v2)}")
                params[p] = _v2
            elif op in ("lt", "<"):
                _v2 = _norm_dt_val(f.get("value"))
                clauses.append(f"{qcol} < {_db(':'+p, _v2)}")
                params[p] = _v2
            elif op in ("gte", ">=", "ge"):
                _v2 = _norm_dt_val(f.get("value"))
                clauses.append(f"{qcol} >= {_db(':'+p, _v2)}")
                params[p] = _v2
            elif op in ("lte", "<=", "le"):
                _v2 = _norm_dt_val(f.get("value"))
                clauses.append(f"{qcol} <= {_db(':'+p, _v2)}")
                params[p] = _v2
            elif op == "between":
                v_from = _norm_dt_val(f.get("valFrom"))
                v_to = _norm_dt_val(f.get("valTo"))
                if date_kind2 == "datetime" and _is_date_only(v_from) and _is_date_only(v_to):
                    clauses.append(
                        f"{qcol} >= TO_DATE(:{p}, 'YYYY-MM-DD') "
                        f"AND {qcol} < TO_DATE(:{p+'_2'}, 'YYYY-MM-DD') + 1")
                else:
                    clauses.append(f"{qcol} BETWEEN {_db(':'+p, v_from)} AND {_db(':'+p+'_2', v_to)}")
                params[p] = v_from
                params[p + "_2"] = v_to
            elif op in ("contains", "like", "ilike"):
                clauses.append(f"{qcol} LIKE :{p}")
                params[p] = f"%{f.get('value', '')}%"
            elif op in ("in", "in_list"):
                vals = f.get("value") or []
                if not isinstance(vals, (list, tuple)):
                    vals = [vals]
                phs = []
                for j, v in enumerate(vals):
                    pj = f"{p}_{j}"
                    phs.append(f":{pj}")
                    params[pj] = v
                clauses.append(f"{qcol} IN ({', '.join(phs)})" if phs else "1=0")
            elif op in ("not_equals", "notequals", "not_equal", "!=", "<>", "ne", "neq"):
                clauses.append(f"{qcol} <> {_db(':'+p, f.get('value'))}")
                params[p] = f.get("value")
            elif op in ("not_contains", "notcontains", "notlike", "not_like"):
                clauses.append(f"{qcol} NOT LIKE :{p}")
                params[p] = f"%{f.get('value', '')}%"
            elif op in ("startswith", "starts_with", "start", "begins", "begins_with"):
                clauses.append(f"{qcol} LIKE :{p}")
                params[p] = f"{f.get('value', '')}%"
            elif op in ("endswith", "ends_with", "end", "ends"):
                clauses.append(f"{qcol} LIKE :{p}")
                params[p] = f"%{f.get('value', '')}"
            elif op in ("not_in", "not_in_list"):
                vals = f.get("value") or []
                if not isinstance(vals, (list, tuple)):
                    vals = [vals]
                phs = []
                for j, v in enumerate(vals):
                    pj = f"{p}_{j}"
                    phs.append(f":{pj}")
                    params[pj] = v
                clauses.append(f"{qcol} NOT IN ({', '.join(phs)})" if phs else "1=1")
            elif op in ("is_null", "isnull", "null"):
                clauses.append(f"{qcol} IS NULL")
            elif op in ("is_not_null", "notnull", "not_null"):
                clauses.append(f"{qcol} IS NOT NULL")
            else:
                clauses.append(f"{qcol}={_db(':'+p, f.get('value'))}")
                params[p] = f.get("value")
        where = " WHERE " + _joiner.join(clauses) if clauses else ""
        return where, params
