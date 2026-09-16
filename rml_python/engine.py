"""
RML Report Engine & Query Builder — Production Ready
Compiles and executes dynamic reporting queries from frontend payloads.
Supports: filtering (Odoo-style), sorting, pagination (Oracle OFFSET/FETCH), grouping, SQL preview.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import re
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
_API_ROWS_CACHE: Dict[str, Any] = {}

def _fmt_cell(v: Any) -> Any:
    """تبسيط عرض القيم: التاريخ بدون وقت منتصف الليل، وفصل التاريخ عن الوقت بمسافة."""
    try:
        import datetime as _dt
        if isinstance(v, _dt.datetime):
            if v.hour == 0 and v.minute == 0 and v.second == 0 and v.microsecond == 0:
                return v.strftime("%Y-%m-%d")
            return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, _dt.date):
            return v.strftime("%Y-%m-%d")
        if isinstance(v, _dt.time):
            return v.strftime("%H:%M:%S")
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
                           rules: Optional[List[Any]] = None) -> str:
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
            for r in re.findall(r"\[([^\].\[]+)\]", part):
                if r.strip().lower() in fmap:
                    refs.add(r.strip())
            for _qm in re.finditer(r"\[([A-Za-z0-9_][A-Za-z0-9_.]*)\]", part):
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
            return _rx(f"[{only}]", fields, {}, set(), table_map, conn_map)
        alias = getattr(col, "alias", None) or field
        raise ValueError(f'لا يمكن التصفية على العمود التجميعي "{alias}" — صفِّ على عمود التاريخ أو العمود الأساس بدلاً منه.')
    out = _rx(raw, fields, {}, set(), table_map, conn_map)
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


def _ar_col_sql(col_expr: str) -> str:
    """لفّ عمود نصي بمقارنة عربية موحدة (TRANSLATE يعمل على Oracle وPostgres)."""
    return f"TRANSLATE({col_expr}, 'أإآةى', 'اااهي')"


def _build_where(filters: List[Dict[str, Any]], start_idx: int = 1, columns: Optional[List] = None,
                 fields: Optional[List] = None, table_map: Optional[Dict[str, str]] = None,
                 conn_map: Optional[Dict[str, str]] = None,
                 _join: str = "AND", rules: Optional[List[Any]] = None) -> Tuple[str, Dict[str, Any]]:
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
                    cands = set(re.findall(r"\[([^\].\[]+)\]", seg))
                    # qualified refs [table.col] / [conn.table.col] -> date check by column part
                    for _qm in re.finditer(r"\[([A-Za-z0-9_][A-Za-z0-9_.]*)\]", seg):
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
                    conn_map=conn_map, _join="OR", rules=rules)
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
                    conn_map=conn_map, _join="AND", rules=rules)
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
        qfield = _resolve_filter_field(field, columns, fields, table_map, conn_map, rules)
        is_date = _col_is_date(col, field)
        date_kind = _col_date_kind(col, field) if is_date else ""
        if op in ("equals", "=", "==", "eq"):
            _v = _norm_dt_val(f.get("value"))
            if date_kind == "datetime" and _is_date_only(_v):
                # date-picker day on a TIMESTAMP column: whole-day range
                # (works on Oracle and Postgres: TO_DATE + 1 day)
                clauses.append(
                    f"{qfield} >= TO_DATE(:{p}, 'YYYY-MM-DD') "
                    f"AND {qfield} < TO_DATE(:{p}, 'YYYY-MM-DD') + 1")
                params[p] = _v
            elif _needs_ar_norm(_v) and not is_date:
                # مقارنة عربية موحدة (أيمن = أيمن رغم الهمزة)
                clauses.append(f"{_ar_col_sql(qfield)}=:{p}")
                params[p] = _ar_norm(_v)
            else:
                clauses.append(f"{qfield}={_dbind(':'+p, _v, is_date)}")
                params[p] = _v
        elif op in ("contains", "like", "ilike", "contains"):
            if _needs_ar_norm(f.get("value")):
                clauses.append(f"{_ar_col_sql(qfield)} LIKE :{p}")
                params[p] = f"%{_ar_norm(f.get('value',''))}%"
            else:
                clauses.append(f"{qfield} LIKE :{p}")
                # Use %value% for contains
                params[p] = f"%{f.get('value','')}%"
        elif op in ("startswith", "starts_with", "start", "begins", "begins_with"):
            if _needs_ar_norm(f.get("value")):
                clauses.append(f"{_ar_col_sql(qfield)} LIKE :{p}")
                params[p] = f"{_ar_norm(f.get('value',''))}%"
            else:
                clauses.append(f"{qfield} LIKE :{p}")
                params[p] = f"{f.get('value','')}%"
        elif op in ("endswith", "ends_with", "end", "ends"):
            if _needs_ar_norm(f.get("value")):
                clauses.append(f"{_ar_col_sql(qfield)} LIKE :{p}")
                params[p] = f"%{_ar_norm(f.get('value',''))}"
            else:
                clauses.append(f"{qfield} LIKE :{p}")
                params[p] = f"%{f.get('value','')}"
        elif op in ("not_contains", "notcontains", "notlike", "not_like"):
            if _needs_ar_norm(f.get("value")):
                clauses.append(f"{_ar_col_sql(qfield)} NOT LIKE :{p}")
                params[p] = f"%{_ar_norm(f.get('value',''))}%"
            else:
                clauses.append(f"{qfield} NOT LIKE :{p}")
                params[p] = f"%{f.get('value','')}%"
        elif op in ("not_equals", "notequals", "not_equal", "!=", "<>", "ne", "neq"):
            _v = _norm_dt_val(f.get("value"))
            if _needs_ar_norm(_v) and not is_date:
                clauses.append(f"{_ar_col_sql(qfield)}<>:{p}")
                params[p] = _ar_norm(_v)
            else:
                clauses.append(f"{qfield}<> {_dbind(':'+p, _v, is_date)}")
                params[p] = _v
        elif op in ("gt", ">", "greater", "greater_than"):
            _v = _norm_dt_val(f.get("value"))
            clauses.append(f"{qfield} > {_dbind(':'+p, _v, is_date)}")
            params[p] = _v
        elif op in ("lt", "<", "less", "less_than"):
            _v = _norm_dt_val(f.get("value"))
            clauses.append(f"{qfield} < {_dbind(':'+p, _v, is_date)}")
            params[p] = _v
        elif op in ("gte", ">=", "ge"):
            _v = _norm_dt_val(f.get("value"))
            clauses.append(f"{qfield} >= {_dbind(':'+p, _v, is_date)}")
            params[p] = _v
        elif op in ("lte", "<=", "le"):
            _v = _norm_dt_val(f.get("value"))
            clauses.append(f"{qfield} <= {_dbind(':'+p, _v, is_date)}")
            params[p] = _v
        elif op == "between":
            p2 = f"p{i}_2"
            # Support valFrom/valTo or value as [from,to]
            v_from = f.get("valFrom", f.get("value", [None, None])[0] if isinstance(f.get("value"), (list, tuple)) else None)
            v_to = f.get("valTo", f.get("value", [None, None])[1] if isinstance(f.get("value"), (list, tuple)) else None)
            v_from = _norm_dt_val(v_from)
            v_to = _norm_dt_val(v_to)
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
            clauses.append(f"{qfield} < {_dbind(':'+p, _v, True)}")
            params[p] = _v
        elif op in ("after",):
            _v = _norm_dt_val(f.get("value"))
            clauses.append(f"{qfield} > {_dbind(':'+p, _v, True)}")
            params[p] = _v
        elif op in ("since", "from_date"):
            _v = _norm_dt_val(f.get("value"))
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
            placeholders = []
            for j, v in enumerate(vals):
                pj = f"{p}_{j}"
                placeholders.append(f":{pj}")
                params[pj] = v
            clauses.append(f"{qfield} IN ({', '.join(placeholders)})" if placeholders else "1=0")
        else:
            # Fallback to equals (مع توحيد عربي عند الحاجة)
            if _needs_ar_norm(f.get("value")):
                clauses.append(f"{_ar_col_sql(qfield)}=:{p}")
                params[p] = _ar_norm(f.get("value"))
            else:
                clauses.append(f"{qfield}=:{p}")
                params[p] = f.get("value")
    where = " WHERE " + f" {_join} ".join(clauses) if clauses else ""
    return where, params

def _build_order_by(sort, columns=None) -> str:
    """Build ORDER BY from sort payload: {column, direction} or list thereof.
    Orders by the SELECT-list alias (always valid in Oracle, even for
    computed/aggregated expressions like DISTINCT/SUM..OVER)."""
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
                   alias_by_table: Optional[Dict[str, str]] = None) -> str:
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
        raw = (col.expr or col.name or "").strip()
        if rules and "$" in raw:
            raw = _expand_rv(raw, rules)
        expr = _resolve_expression(raw, fields, ns_registry, _visited, table_map, conn_map, _abt)
        base_sql: str
        if raw.strip().upper() == "NULL":
            # Merge placeholder for cross-DB columns (filled post-fetch)
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
                else:
                    base_sql = (f"(SELECT LISTAGG({_q(ref_display)}, ', ') WITHIN GROUP (ORDER BY {_q(ref_display)}) "
                                f"FROM {ref_from} WHERE {_q(ref_fk)} = {key_sql})")
            else:
                base_sql = f"(SELECT {_q(ref_display)} FROM {ref_from} WHERE {_q(ref_fk)} = {key_sql})"
        elif col.col_type in ("aggregated", "computed"):
            if re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', raw):
                base_sql = _q(raw)
            else:
                base_sql = expr
        else:  # direct
            if re.match(r'^[A-Za-z_][A-Za-z0-9_\.]*$', raw):
                base_sql = raw if raw.strip().upper() == "NULL" else _q(raw)
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
            wc = _rx(wc, fields, ns_registry, _visited, table_map, conn_map, _abt)
        base_sql = _apply_column_where(base_sql, wc)
        parts.append(f"{base_sql} AS {alias_q}")
    return ", ".join(parts) if parts else "*"

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
        # API-backed connection (e.g. ZK — is_queryable=False): its tables are
        # staged as TEMP tables on the primary SQL DB, so route there.
        try:
            _dj = self._dj_conn(conn_key) if conn_key else None
            if _dj is not None and getattr(self, "db", None) is not None:
                try:
                    _fl = getattr(_dj, "is_queryable", True)
                    _f = False if _fl is False or str(_fl).strip().lower() in ("0", "false", "no", "none") else True
                except Exception:
                    _f = True
                _e = str(getattr(_dj, "engine", "") or "").lower()
                if not _f or _e not in self.API_SQL_FAMILY:
                    return self.db
        except Exception:
            pass
        # Staged tables (API/ZK/sqlserver mirrors) physically live on the primary
        # DB — route by staged gid instead of raising.
        try:
            _st = getattr(self, "_api_stage", None) or {}
            for _norm, _spec in _st.items():
                _gi = ((_spec or {}).get("info") or {}).get("gid")
                if _gi is not None and str(_gi) == str(conn_key):
                    return self.db
        except Exception:
            pass
        raise ValueError(
            f'الجدول على اتصال غير مهيأ (id={conn_key}). '
            f'أضف الاتصال للتقرير وتحقق من إعدادات التنفيذ.')

    def _schema_for_table(self, table_norm: str, conn_key: Optional[str], default_schema: Optional[str]) -> Optional[str]:
        """Schema for a table: connection.schema -> report schema -> dialect default."""
        try:
            if self._staged_temp_of(table_norm):
                return None  # جدول مؤقت مرحّل — ظاهر في الجلسة دون مخطط
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

    def _inline_column_refs(self, columns, scope_extra=None):
        """Return column copies with [column] refs inlined (fields untouched)."""
        import dataclasses
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

        def resolve_col(col, stack):
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
                inlined = resolve_col(tgt, stack + [tid])
                return "(" + self._strip_format_wrapper(inlined) + ")"

            return re.sub(r"\[([^\].\[]+)\]", sub, raw)

        out = []
        for c in (columns or []):
            cid = getattr(c, "id", None) or getattr(c, "name", "")
            try:
                new_raw = resolve_col(c, [cid])
            except ValueError:
                raise
            out.append(dataclasses.replace(c, expr=new_raw) if new_raw != (c.expr or "") else c)
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
        """Field names referenced as [name] (skips [ns.member])."""
        return re.findall(r"\[([^\].\[]+)\]", str(text or ""))

    def _refs_in_text(self, text: str, field_table: Dict[str, str]) -> set:
        """Field keys referenced in raw SQL: [refs] + bare/quoted identifiers.

        Supports qualified refs [table.col] / [conn.table.col] (resolved to
        their field key). Single-quoted literals are ignored;
        already-qualified (dot-adjacent) tokens are ignored.
        """
        found: set = set()
        for r in self._bracket_refs(text):
            if r.strip().lower() in field_table:
                found.add(r.strip().lower())
        for m in re.finditer(r"\[([A-Za-z0-9_][A-Za-z0-9_.]*)\]", str(text or "")):
            inner = m.group(1).strip()
            if "." in inner:
                key = self._match_qualified(inner, field_table)
                if key:
                    found.add(key)
        # strip single-quoted literals
        segs = re.split(r"('(?:[^']|'')*')", str(text or ""))
        for i in range(0, len(segs), 2):
            seg = segs[i]
            for m in re.finditer(r'(?<!\.)"([A-Za-z_][A-Za-z0-9_]*)"(?!\.)', seg):
                if m.group(1).lower() in field_table:
                    found.add(m.group(1).lower())
            names = sorted(field_table.keys(), key=len, reverse=True)
            if names:
                alt = "|".join(re.escape(n) for n in names)
                for m in re.finditer(r"(?<!\.)\b(" + alt + r")\b(?!\.)", seg, re.IGNORECASE):
                    found.add(m.group(1).lower())
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
        for m in re.finditer(r"\[([A-Za-z0-9_][A-Za-z0-9_.]*)\]", str(text or "")):
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
            _api = self._api_table_info(norm)
        except Exception:
            _api = None
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
        """(row, gid, sch, tbl) — original SQL Server location of a staged table, else None."""
        try:
            info = self._api_table_info(table_norm)
        except Exception:
            info = None
        if not info or str((info or {}).get("engine") or "").lower() != "sqlserver":
            return None
        row = (info or {}).get("row")
        if row is None:
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
        return (row, str((info or {}).get("gid") or ""), sch, tbl)

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
    API_STAGE_TTL = 1800

    @staticmethod
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

    @staticmethod
    def _stage_engine_of(db) -> str:
        """'oracle' | 'postgres' — staging TEMP-table dialect for a DB wrapper."""
        try:
            _nm = (type(db).__name__ or "").lower()
            _mod = (type(db).__module__ or "").lower()
            if "oracle" in _nm or "oracle" in _mod:
                return "oracle"
        except Exception:
            pass
        return "postgres"

    @staticmethod
    def _stage_col_type(rml_type, engine: str) -> str:
        """Staging column type per engine (Oracle has no TIME/BOOLEAN/TEXT)."""
        t = str(rml_type or "").upper().strip()
        if engine == "oracle":
            if t in ("INTEGER", "INT", "SERIAL", "BOOLEAN"):
                return "NUMBER(10)"
            if t in ("NUMERIC", "DECIMAL", "FLOAT", "DOUBLE"):
                return "NUMBER"
            if t in ("TIMESTAMP", "DATETIME"):
                return "TIMESTAMP"
            if t == "DATE":
                return "DATE"
            return "VARCHAR2(4000)"
        return RMLReportEngine._pg_col_type(rml_type)

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

    def _is_api_gid(self, gid) -> bool:
        """True when a global connection id is an API source (not SQL-queryable).

        sqlserver counts as API here too: this engine emits postgres/oracle SQL,
        so SQL Server tables are fetched via pyodbc and staged as PG TEMP
        (same bridge as ZK mirrors) instead of being queried directly.
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
            _fl = getattr(row, "is_queryable", True)
            flag = False if _fl is False or str(_fl).strip().lower() in ("0", "false", "no", "none") else True
        except Exception:
            flag = True
        eng = str(getattr(row, "engine", "") or "").lower()
        if eng == "sqlserver":
            return True
        return (not flag) or (eng not in self.API_SQL_FAMILY)

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
        """TEMP table backing a staged (API) or unioned table norm, else None."""
        try:
            _n = self._norm_table(norm)
        except Exception:
            return None
        try:
            _st = getattr(self, "_api_stage", None) or {}
            if _n in _st:
                return (_st[_n] or {}).get("temp")
        except Exception:
            pass
        try:
            _un = getattr(self, "_api_union", None) or {}
            if _n in _un:
                return (_un[_n] or {}).get("temp")
        except Exception:
            pass
        return None

    def _coerce_api_value(self, value, rml_type, col_name="", table_name=""):
        if value is None:
            try:
                if str(rml_type or "").upper().strip() in ("TIMESTAMP", "DATETIME", "DATE", "TIME"):
                    self._note_api_quarantine(table_name, col_name, "(فارغة من الجهاز)")
            except Exception:
                pass
            return None
        _t = str(rml_type or "").upper().strip()
        if isinstance(value, bool):
            # SQL Server BIT arrives as bool: 1/0 for NUM targets, native for BOOLEAN
            if _t == "BOOLEAN":
                return value
            if self._dtype_cat(_t) == "NUM":
                return 1 if value else 0
        if _t in ("TIMESTAMP", "DATETIME", "DATE", "TIME"):
            try:
                import datetime as _dt
                if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
                    return value
                _s = str(value).strip()
                if _t == "TIME":
                    try:
                        return _dt.time.fromisoformat(_s)
                    except Exception:
                        pass
                try:
                    _d = _dt.datetime.fromisoformat(_s)
                except ValueError:
                    _d = _dt.datetime.strptime(_s, "%Y-%m-%d %H:%M:%S")
                if _t == "DATE":
                    return _d.date() if isinstance(_d, _dt.datetime) else _d
                if _t == "TIME":
                    return _d.time() if isinstance(_d, _dt.datetime) else _d
                return _d
            except Exception:
                raise ValueError(
                    f"قيمة التاريخ/الوقت '{value}' في العمود '{col_name}' "
                    f"(جدول '{table_name}') غير صالحة.")
        return value

    def _note_api_quarantine(self, table_norm, col_name, value):
        """Record a quarantined API value (kept as NULL): first sample + count."""
        try:
            _w = getattr(self, "_api_warnings", None)
            if _w is None:
                self._api_warnings = _w = []
            _key = (str(table_norm).lower(), str(col_name).lower())
            for _e in _w:
                if isinstance(_e, dict) and _e.get("key") == _key:
                    _e["skipped"] = int(_e.get("skipped") or 0) + 1
                    return
            _w.append({"key": _key,
                       "message": f"قيم غير صالحة في العمود '{col_name}' "
                                  f"(جدول '{str(table_norm).lower()}') حُوّلت إلى فارغ"
                                  f" — مثال: '{str(value)[:60]}'",
                       "skipped": 1})
        except Exception:
            pass

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

    def _stage_max_rows(self) -> int:
        """Staging safety gate; per-report override via <rpt_metadata stage_max_rows="..">."""
        try:
            _ra = (getattr(self, "metadata", None) or {}).get("raw_attrs") or {}
            for _k, _v in _ra.items():
                if str(_k).lower() in ("stage_max_rows", "stagemaxrows", "max_stage_rows"):
                    return max(0, int(str(_v)))
        except Exception:
            pass
        return int(self.STAGE_MAX_ROWS)

    @staticmethod
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

    def _static_where_for(self, table_norm, want_cols):
        """Static general_where conditions applicable to ONE table (for pushdown).

        Bracket refs [t.c]/[c] must resolve to this table; bare idents must belong
        only to it; literals-only conditions apply everywhere. $rule.var$ expanded.
        GO batch lines are dropped. Returns SQL WHERE fragment (T-SQL brackets) or "".
        """
        try:
            gw = self.compiler.general_where() if hasattr(self.compiler, "general_where") else ""
        except Exception:
            gw = ""
        gw = str(gw or "")
        if not gw.strip():
            return ""
        if self.rules and "$" in gw:
            try:
                from .rulevars import expand_rule_vars as _expand_rv
                gw = _expand_rv(gw, self.rules)
            except Exception:
                pass
        lines = [l for l in gw.splitlines() if not re.match(r"^\s*GO\s*$", l, re.IGNORECASE)]
        gw = " ".join(lines)
        my_cols = {str(c).lower() for c in (want_cols or [])}
        others: set = set()
        try:
            for _f in (getattr(self, "fields", []) or []):
                _ts = str(getattr(_f, "table_source", "") or "")
                if _ts and self._norm_table(_ts) != table_norm:
                    others.add(str(getattr(_f, "name", "") or "").lower())
        except Exception:
            pass
        keep = []
        for cond in self._split_and_top(gw):
            c = cond.strip()
            if not c:
                continue
            refs = re.findall(r"\[([^\]]+)\]", c)
            ok = True
            for r in refs:
                parts = [p.strip() for p in r.split(".")]
                if len(parts) == 2:
                    if self._norm_table(parts[0]) != table_norm:
                        ok = False
                        break
                elif len(parts) == 1:
                    if parts[0].lower() not in my_cols:
                        ok = False
                        break
                else:
                    ok = False
                    break
            if not ok:
                continue
            # bare identifiers must all be mine (or SQL noise)
            _bare = re.findall(r"[A-Za-z_\u0600-\u06FF][\w$\u0600-\u06FF]*", re.sub(r"\[[^\]]*\]|'(?:''|[^'])*'", " ", c))
            bad = False
            for w in _bare:
                uw = w.upper()
                if uw in ("AND", "OR", "NOT", "IN", "IS", "NULL", "LIKE", "BETWEEN", "EXISTS",
                          "CASE", "WHEN", "THEN", "ELSE", "END", "TRUE", "FALSE"):
                    continue
                if re.fullmatch(r"-?\d+(\.\d+)?", w):
                    continue
                if w.lower() in my_cols or w.lower() in others:
                    if w.lower() not in my_cols:
                        bad = True
                        break
                    continue
                # function call or unknown token → only safe if it looks like a function
                rest = c[c.upper().find(uw) + len(w):] if uw in c.upper() else ""
                if not re.match(r"\s*\(", rest):
                    bad = True
                    break
            if bad:
                continue
            # rewrite [t.c]/[c] → T-SQL [c]
            def _rw(m):
                inner = m.group(1)
                parts = [p.strip() for p in inner.split(".")]
                return "[" + parts[-1] + "]"
            keep.append(re.sub(r"\[([^\]]+)\]", _rw, c))
        return " AND ".join(keep)

    def _fetch_sqlserver_rows(self, table_norm, info, refresh=False):
        """Fetch rows of a SQL Server table via pyodbc (for PG-TEMP staging).

        Schema resolved from the report field's table_source, else the
        connection's schema, else dbo. Cached like API rows (API_STAGE_TTL).
        """
        _ckey = ("api_rows", str(info.get("gid") or ""), str(table_norm).upper())
        if not refresh:
            try:
                _hit = _API_ROWS_CACHE.get(_ckey)
                if _hit:
                    import time as _tm
                    if _tm.time() - float(_hit[0]) < self.API_STAGE_TTL:
                        return [dict(_r) for _r in _hit[1]]
            except Exception:
                pass
        row = info.get("row")
        plan = self._sqlserver_plan(table_norm, info, row)
        rows: list = []
        for _names, _batch in self._iter_sqlserver_batches(
                row, plan["user"], plan["pwd"], plan["sch"], plan["tbl"],
                plan["sellist"], plan["where"]):
            rows.extend(_batch)
        try:
            import time as _tm2
            _API_ROWS_CACHE[_ckey] = (_tm2.time(), [dict(_r) for _r in rows])
        except Exception:
            pass
        return rows

    def _sqlserver_plan(self, table_norm, info, row):
        """Resolve fetch plan for one sqlserver table: creds/schema/cols/filter."""
        user = str(getattr(row, "user", "") or "")
        pwd = str(getattr(row, "password", "") or "")
        sch, tbl = "", self._norm_table(table_norm)
        want: list = []
        try:
            for _f in (getattr(self, "fields", []) or []):
                if self._norm_table(getattr(_f, "table_source", None) or "") == tbl:
                    _fn = str(getattr(_f, "name", "") or "")
                    if _fn and _fn not in want:
                        want.append(_fn)
            if not want:
                raise ValueError(f"لا حقول للتقرير على الجدول '{tbl.lower()}' — أضف حقوله أولاً")
            for _f in (getattr(self, "fields", []) or []):
                if self._norm_table(getattr(_f, "table_source", None) or "") == tbl:
                    _ts = str(getattr(_f, "table_source", "") or "")
                    if "." in _ts:
                        sch = _ts.split(".")[0]
                    break
        except ValueError:
            raise
        except Exception:
            pass
        if not sch:
            sch = str(getattr(row, "schema", "") or "").strip() or "dbo"
        sellist = ", ".join(f"[{c}]" for c in want)
        where = self._static_where_for(table_norm, want)
        return {"user": user, "pwd": pwd, "sch": sch, "tbl": tbl,
                "want": want, "sellist": sellist, "where": where}
        try:
            import time as _tm2
            _API_ROWS_CACHE[_ckey] = (_tm2.time(), [dict(_r) for _r in rows])
        except Exception:
            pass
        return rows

    def _iter_sqlserver_batches(self, row, user, pwd, sch, tbl, sellist, where):
        """Yield (names, batch_dicts) streaming a SQL Server table (fetchmany).

        COUNT-gate applies when no static filter; raises the gate error directly.
        Connection/cursor closed when exhausted or on error.
        """
        last = None
        _gate_stop = False
        for _srv, _dbn in self._sqlserver_targets(row):
            for _drv, _modern in self._sqlserver_drivers():
                try:
                    import pyodbc
                    _parts = [f"DRIVER={{{_drv}}}", f"SERVER={_srv}", f"DATABASE={_dbn}",
                              f"UID={user}", f"PWD={pwd}"]
                    if _modern:
                        _parts += ["TrustServerCertificate=yes", "Connect Timeout=15"]
                    _cn = pyodbc.connect(";".join(_parts) + ";", timeout=15)
                    try:
                        _cur = _cn.cursor()
                        if not where:
                            _cur.execute(f"SELECT COUNT(*) FROM [{sch}].[{tbl}]")
                            _n = (_cur.fetchone() or [0])[0] or 0
                            if int(_n) > int(self._stage_max_rows()):
                                raise ValueError(
                                    f"الجدول '{tbl.lower()}' ضخم ({int(_n)} صف) بلا شرط تصفية — "
                                    f"ضع شرطاً عاماً (general_where) في التقرير لتقليص السحب، "
                                    f"مثل [{tbl.lower()}.<العمود>] = ...")
                        _cur.execute(f"SELECT {sellist} FROM [{sch}].[{tbl}]"
                                     + (f" WHERE {where}" if where else ""))
                        _names = [d[0] for d in (_cur.description or [])]
                        while True:
                            _batch = _cur.fetchmany(2000)
                            if not _batch:
                                break
                            yield _names, [dict(zip(_names, r)) for r in _batch]
                        try:
                            _cur.close()
                        except Exception:
                            pass
                    finally:
                        try:
                            _cn.close()
                        except Exception:
                            pass
                    return
                except Exception as e:
                    _m = str(e)
                    if "الجدول" in _m and "ضخم" in _m:
                        raise
                    if "IM002" in _m or "Data source name not found" in _m:
                        last = e
                        continue
                    last = e
        raise ValueError(f"تعذر جلب {sch}.{tbl} من SQL Server: {str(last)[:200]}")

    def _fetch_api_rows(self, table_norm, info, refresh=False):
        """Fetch ALL rows of an API table via its source API (no staging).

        Results are cached per (connection, table) for API_STAGE_TTL seconds —
        a full device pull is far too slow to repeat on every page view.
        refresh=True bypasses the cache (forced fresh pull).
        """
        try:
            _gid = str(info.get("gid") or "")
        except Exception:
            _gid = ""
        _ckey = ("api_rows", _gid, str(table_norm).upper())
        if not refresh:
            try:
                _hit = _API_ROWS_CACHE.get(_ckey)
                if _hit:
                    import time as _tm
                    if _tm.time() - float(_hit[0]) < self.API_STAGE_TTL:
                        return [dict(_r) for _r in _hit[1]]
            except Exception:
                pass
        eng = str(info.get("engine") or "").lower()
        row = info.get("row")
        try:
            _label = str(getattr(row, "name", "") or info.get("gid") or "")
        except Exception:
            _label = str(info.get("gid") or "")
        if eng == "sqlserver":
            return self._fetch_sqlserver_rows(table_norm, info, refresh)
        if eng != "zk":
            raise ValueError(
                f"المصدر '{_label}' من نوع '{eng or '?'}' غير قابل للاستعلام SQL "
                f"ولا يوجد له API مدعوم — حدّث الاتصال أو وجّه التقرير لاتصال SQL.")
        try:
            from odex.engines.zk import ZKEngine
        except Exception as _e:
            raise ValueError(f"تعذر تحميل محرك أجهزة البصمة: {_e}")
        _static = self._api_static_columns()
        if table_norm not in _static:
            _avail = ", ".join(sorted(_static.keys())) or "—"
            raise ValueError(
                f"الجدول '{table_norm.lower()}' غير متوفر على جهاز البصمة '{_label}' — "
                f"الجداول المتاحة: {_avail}.")
        try:
            _host = str(getattr(row, "host", "") or "")
            _port = int(getattr(row, "port", 0) or 4370)
        except Exception:
            _host, _port = str(getattr(row, "host", "") or ""), 4370
        try:
            _zk = ZKEngine(ip=_host, port=_port, timeout=15, password=0)
            _zk.connect()
        except Exception as _e:
            raise ValueError(
                f"تعذر الاتصال بجهاز البصمة '{_label}' ({_host}:{_port}) — "
                f"تحقق من الشبكة والجهاز. ({_e})")
        # سحبة واحدة كاملة: list_all يسحب سجل الجهاز كله في كل استدعاء،
        # فالترقيم هنا O(n²) على البروتوكول — نسحب مرة واحدة فقط.
        # tolerant_zk_decode يجعل سجلات التواريخ الفاسدة None بدل إسقاط السحبة كلها.
        try:
            try:
                from odex.engines.zk import tolerant_zk_decode as _tzd
            except Exception:
                _tzd = None
            import contextlib as _cl
            _cm = _tzd() if _tzd else _cl.nullcontext()
            with _cm:
                _api_rows = list(_zk.list_all(table_norm.lower(), limit=1000000) or [])
            try:
                import time as _tm
                _API_ROWS_CACHE[_ckey] = (_tm.time(), [dict(_r) for _r in _api_rows])
            except Exception:
                pass
            return _api_rows
        except Exception as _e:
            raise ValueError(f"تعذر قراءة الجدول '{table_norm.lower()}' من جهاز البصمة '{_label}': {_e}")
        finally:
            try:
                _zk.disconnect()
            except Exception:
                pass

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

    @staticmethod
    def _stage_phy_name(temp, coldefs) -> str:
        """Stable persistent stage name: base + columns hash (report edits auto-isolate)."""
        import hashlib as _hl
        _hs = _hl.md5("|".join(
            f"{str(_n).lower()}:{str(_t or '').upper()}" for _n, _t in (coldefs or [])
        ).encode()).hexdigest()[:8]
        return (re.sub(r"[^a-z0-9_]", "_", str(temp).lower()) + "_" + _hs)[:100]

    @staticmethod
    def _stage_normtype(t) -> str:
        s = str(t or "").lower().strip()
        if s.startswith("timestamp"):
            return "timestamp"
        if s.startswith("time"):
            return "time"
        if "varchar" in s or s == "character varying":
            return "text"
        return s

    def _stage_table_ready(self, db, phy, coldefs) -> bool:
        """True if a persistent stage table exists with exactly these columns AND types.

        Type check defeats stale reuse (e.g. a TEXT Delivered from before the
        field was corrected to INTEGER). A meta rows=-1 means an interrupted
        fill → never ready (self-healing refill).
        """
        try:
            _cur = db.conn.cursor()
            try:
                _cur.execute("SELECT to_regclass(%s)", (str(phy),))
                _row = _cur.fetchone()
                if not _row or not _row[0]:
                    return False
            finally:
                try:
                    _cur.close()
                except Exception:
                    pass
            _cur2 = db.conn.cursor()
            try:
                _cur2.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name=%s AND table_schema = ANY (current_schemas(false))",
                    (str(phy),))
                _have = {str(r[0]).lower(): self._stage_normtype(r[1]) for r in (_cur2.fetchall() or [])}
            finally:
                try:
                    _cur2.close()
                except Exception:
                    pass
            _want = {str(_n).lower(): self._stage_normtype(_t) for _n, _t in (coldefs or [])}
            if not _want or set(_want) != set(_have):
                return False
            if any(_have.get(_k) != _v for _k, _v in _want.items()):
                return False
            try:
                _cur3 = db.conn.cursor()
                try:
                    _cur3.execute("SELECT rows FROM rml_stage_meta WHERE stage=%s", (str(phy),))
                    _mr = _cur3.fetchone()
                    if _mr is not None and int(_mr[0] or 0) < 0:
                        return False
                finally:
                    try:
                        _cur3.close()
                    except Exception:
                        pass
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _stage_touch_meta(self, db, phy, rows=None):
        """Upsert stage registry (for TTL cleanup). Best-effort."""
        try:
            _cur = db.conn.cursor()
            try:
                _cur.execute(
                    "CREATE TABLE IF NOT EXISTS rml_stage_meta "
                    "(stage TEXT PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT now(), rows BIGINT DEFAULT 0)")
                _cur.execute(
                    "INSERT INTO rml_stage_meta (stage, created_at, rows) VALUES (%s, now(), %s) "
                    "ON CONFLICT (stage) DO UPDATE SET created_at=EXCLUDED.created_at, rows=EXCLUDED.rows",
                    (str(phy), int(rows or 0)))
                db.conn.commit()
            finally:
                try:
                    _cur.close()
                except Exception:
                    pass
        except Exception:
            try:
                db.conn.rollback()
            except Exception:
                pass

    def _stage_gc(self, db, ttl_hours=24, limit=20):
        """Drop stage tables older than TTL. Best-effort, postgres only."""
        try:
            if self._stage_engine_of(db) != "postgres":
                return
            _cur = db.conn.cursor()
            try:
                _cur.execute(
                    "SELECT stage FROM rml_stage_meta WHERE created_at < now() - (%s || ' hours')::interval "
                    "ORDER BY created_at LIMIT %s", (str(int(ttl_hours)), int(limit)))
                _old = [str(r[0]) for r in (_cur.fetchall() or []) if r and r[0]]
                for _st in _old:
                    if not re.fullmatch(r"[a-z0-9_]{1,100}", _st):
                        continue
                    try:
                        _cur.execute(f'DROP TABLE IF EXISTS {_q(_st)}')
                        _cur.execute("DELETE FROM rml_stage_meta WHERE stage=%s", (_st,))
                    except Exception:
                        try:
                            db.conn.rollback()
                        except Exception:
                            pass
                        continue
                db.conn.commit()
            finally:
                try:
                    _cur.close()
                except Exception:
                    pass
        except Exception:
            try:
                db.conn.rollback()
            except Exception:
                pass

    def _write_temp_table(self, db, temp, coldefs, data, label, refresh=False):
        """CREATE staging table + bulk INSERT on the primary DB (any engine).

        postgres: PERSISTENT shared table rml_<..>_<colhash> (cross-request reuse;
          refresh or missing/changed table refills; 24h TTL via rml_stage_meta).
        oracle: PRIVATE TEMPORARY TABLE ORA$PTT_* ON COMMIT PRESERVE DEFINITION
          (18c+, session-private; dropped automatically at session end).
        """
        try:
            if not getattr(db, "conn", None):
                try:
                    db.connect()
                except Exception as _e:
                    raise ValueError(f"تعذر الاتصال بقاعدة SQL الأساسية لترحيل البيانات: {_e}")
            _eng = self._stage_engine_of(db)
            if _eng == "oracle":
                # Global Temporary Table (works 8i+; Private Temp needs 18c+).
                # Definition persists and is REUSED across runs; rows stay
                # session-private. Name carries a columns-hash so differing
                # column sets never collide on a stale definition.
                import hashlib as _hl
                _hs = _hl.md5(",".join(str(_n).lower() for _n, _t in coldefs).encode()).hexdigest()[:6]
                _temp = (re.sub(r"[^a-z0-9_]", "_", str(temp).lower()) + "_" + _hs)[:100]
            else:
                _temp = temp
            _cur = db.conn.cursor()
            try:
                if _eng == "oracle":
                    _collist = ", ".join(f"{_q(_n)} {_t}" for _n, _t in coldefs)
                    try:
                        _cur.execute(
                            f"CREATE GLOBAL TEMPORARY TABLE {_q(_temp)} ({_collist}) "
                            f"ON COMMIT PRESERVE ROWS")
                    except Exception as _ce:
                        _msg = str(_ce)
                        if "-00955" in _msg or "already used" in _msg.lower():
                            pass  # definition reused from a previous run
                        elif "-01031" in _msg or "insufficient privileges" in _msg.lower():
                            try:
                                _who = getattr(db, "user", "") or "?"
                            except Exception:
                                _who = "?"
                            raise ValueError(
                                f"حساب أوراكل ({_who}) لا يملك صلاحية إنشاء الجداول المؤقتة — "
                                f"نفّذ مرة واحدة كـ DBA: GRANT CREATE TABLE TO {_who}. "
                                f"(تعريف الجدول المؤقت يُنشأ مرة واحدة ويُعاد استخدامه، "
                                f"والصفوف خاصة بكل جلسة ولا تظهر للآخرين.)")
                        elif "-01950" in _msg:
                            raise ValueError(
                                "لا توجد حصة تخزين (quota) لحساب أوراكل لإنشاء الجداول المؤقتة — "
                                "راجع الـ DBA.")
                        else:
                            raise
                    _cur.execute(f"DELETE FROM {_q(_temp)}")
                    if data:
                        import datetime as _dtm
                        _ph = ", ".join(f":{i + 1}" for i in range(len(coldefs)))
                        _clean = []
                        for _r in data:
                            _row = []
                            for _v in (list(_r) if not isinstance(_r, dict) else list(_r.values())):
                                if isinstance(_v, bool):
                                    _row.append(int(_v))
                                elif isinstance(_v, _dtm.time):
                                    _row.append(_v.isoformat())
                                else:
                                    _row.append(_v)
                            _clean.append(tuple(_row))
                        for _i in range(0, len(_clean), 1000):
                            _cur.executemany(
                                f"INSERT INTO {_q(_temp)} ({', '.join(_q(_n) for _n, _t in coldefs)}) "
                                f"VALUES ({_ph})", _clean[_i:_i + 1000])
                    db.conn.commit()
                else:
                    # Persistent shared stage (cross-request): stable name + columns hash.
                    # Reuse skips the whole fetch; refresh (or missing/changed table) refills.
                    _phy = self._stage_phy_name(_temp, coldefs)
                    _collist = ", ".join(f"{_q(_n)} {_t}" for _n, _t in coldefs)
                    if not refresh and self._stage_table_ready(db, _phy, coldefs):
                        self._report_progress({"stage": "cached", "table": str(label).lower(),
                                               "text": f"استخدام مرحلة مخزنة: {str(label).lower()}…"})
                        return _phy
                    try:
                        _cur.execute(f"DROP TABLE IF EXISTS {_q(_phy)}")
                        _cur.execute(f"CREATE TABLE {_q(_phy)} ({_collist})")
                    except Exception as _dce:
                        _dm = str(_dce)
                        if "permission denied" in _dm.lower() or "42501" in _dm:
                            raise ValueError(
                                "حساب قاعدة البيانات لا يملك صلاحية إنشاء جداول الترحيل — "
                                "نفّذ مرة واحدة كـ DBA: GRANT CREATE ON SCHEMA public TO <user>.")
                        raise
                    if data:
                        _cur.executemany(
                            f"INSERT INTO {_q(_phy)} ({', '.join(_q(_n) for _n, _t in coldefs)}) "
                            f"VALUES ({', '.join(['%s'] * len(coldefs))})", data)
                    db.conn.commit()
                    # -1 = filling in progress (streaming path sets the real count
                    # afterwards); an interrupted fill is never treated as ready.
                    self._stage_touch_meta(db, _phy, len(data) if data else -1)
                    return _phy
            except Exception as _e:
                try:
                    db.conn.rollback()
                except Exception:
                    pass
                raise ValueError(f"تعذر ترحيل بيانات '{label}' إلى جدول مؤقت: {_e}")
            finally:
                try:
                    _cur.close()
                except Exception:
                    pass
            return _temp
        except ValueError:
            raise
        except Exception as _e:
            raise ValueError(f"فشل ترحيل الجدول '{label}': {_e}")

    def _stage_union_table(self, table_norm, parts, db, report_schema, refresh=False):
        """UNION ALL instances of one table across connections into ONE TEMP table.

        parts: [[(field, gid), ...], ...] — one partition per physical source.
        Returns (temp, cols). Fetches records from every connection completely.
        """
        # union of RML field names (first-seen order) — temp DDL + link validation
        _cols = []
        _seen = set()
        for _part in parts:
            for _f, _gid in _part:
                _n = str(getattr(_f, "name", "") or "")
                if _n and _n.lower() not in _seen:
                    _seen.add(_n.lower())
                    _cols.append((_n, getattr(_f, "data_type", None)))
        if not _cols:
            raise ValueError(f"لا توجد حقول معرفة للجدول '{table_norm.lower()}' في التقرير.")
        _names = {str(_n).lower() for _n, _t in _cols}
        try:
            for _l in (getattr(self, "report_links", []) or []):
                if not isinstance(_l, dict):
                    continue
                for _k, _side in (("from_table", "from_col"), ("to_table", "to_col")):
                    try:
                        if self._norm_table(_l.get(_k) or "") == table_norm:
                            _lc = str(_l.get(_side) or "").strip().lower()
                            if _lc and _lc not in _names:
                                raise ValueError(
                                    f"الرابط يشير للعمود '{_l.get(_side)}' في الجدول "
                                    f"'{table_norm.lower()}' وهو غير موجود في حقول التقرير — "
                                    f"أضف العمود أولاً.")
                    except ValueError:
                        raise
                    except Exception:
                        pass
        except ValueError:
            raise
        except Exception:
            pass
        _temp = re.sub(r"[^a-z0-9_]", "_", f"rml_union_{table_norm}".lower())
        _coldefs = [(_n, self._stage_col_type(_t, self._stage_engine_of(db))) for _n, _t in _cols]
        _low_names = [str(_n).lower() for _n, _t in _cols]
        _cols_retU = [(str(_n), str(_t or "")) for _n, _t in _cols]
        if ((not refresh) and self._stage_engine_of(db) == "postgres"
                and self._stage_table_ready(db, self._stage_phy_name(_temp, _coldefs), _coldefs)):
            self._report_progress({"stage": "cached", "table": str(table_norm).lower(),
                                   "text": f"استخدام مرحلة مخزنة: {str(table_norm).lower()}…"})
            return self._stage_phy_name(_temp, _coldefs), _cols_retU
        # resolve partition sources first (no I/O beyond cached Django lookups)
        _jobs = []
        for _part in parts:
            _pgid = None
            for _, _g in _part:
                if _g:
                    _pgid = _g
                    break
            _part_fields = [_f for _f, _g in _part]
            if _pgid and self._is_api_gid(_pgid):
                try:
                    _row0 = self._dj_conn(_pgid)
                except Exception:
                    _row0 = None
                _eng0 = str(getattr(_row0, "engine", "") or "").lower() if _row0 is not None else ""
                _jobs.append(("api", _part_fields, table_norm,
                              {"gid": _pgid, "engine": _eng0, "row": _row0}))
            else:
                _jobs.append(("sql", _part_fields, table_norm, _pgid))
        # API partitions: fetch distinct devices in parallel (separate sessions);
        # same device sequentially (single-session devices). SQL stays main-thread.
        _raws = {}
        _api_idx = [i for i, _j in enumerate(_jobs) if _j[0] == "api"]
        if _api_idx:
            _groups = {}
            for _i in _api_idx:
                _, _, _, _inf = _jobs[_i]
                _rw = (_inf or {}).get("row")
                _hk = (str(getattr(_rw, "host", "") or ""),
                       str(getattr(_rw, "port", 0) or ""))
                _groups.setdefault(_hk, []).append(_i)

            def _run_group(_idxs):
                _out = {}
                for _i in _idxs:
                    _, _, _tn2, _inf2 = _jobs[_i]
                    _out[_i] = self._fetch_api_rows(_tn2, _inf2, refresh)
                return _out

            if len(_groups) > 1:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=min(len(_groups), 4)) as _ex:
                    _futs = { _ex.submit(_run_group, _idxs): _idxs for _idxs in _groups.values() }
                    for _fu in _futs:
                        _raws.update(_fu.result())
            else:
                _raws.update(_run_group(next(iter(_groups.values()))))
        _data = []
        for _i, _job in enumerate(_jobs):
            if _job[0] == "api":
                for _r in (_raws.get(_i) or []):
                    _lm = {str(_k).lower(): _v for _k, _v in dict(_r or {}).items()}
                    _frow = []
                    for _fn, _ft in _coldefs:
                        try:
                            _frow.append(self._coerce_api_value(
                                _lm.get(str(_fn).lower()), _ft, _fn, table_norm.lower()))
                        except ValueError:
                            self._note_api_quarantine(table_norm, _fn, _lm.get(str(_fn).lower()))
                            _frow.append(None)
                    _data.append(tuple(_frow))
            else:
                _, _part_fields, _, _pgid3 = _job
                _rows = self._fetch_sql_partition(table_norm, _pgid3, _part_fields, report_schema)
                for _r in (_rows or []):
                    _lm = {str(_k).lower(): _v for _k, _v in dict(_r or {}).items()}
                    _frow = [_lm.get(_ln) for _ln in _low_names]
                    _data.append(tuple(_frow))
        _eff = self._write_temp_table(db, _temp, _coldefs, _data, table_norm.lower(), refresh=refresh)
        return _eff, [(str(_n), str(_t or "")) for _n, _t in _cols]

    def _report_progress(self, info):
        try:
            _cb = getattr(self, "_progress_cb", None)
            if callable(_cb):
                _cb(info)
        except Exception:
            pass

    def _stage_sqlserver_streamed(self, table_norm, info, db, refresh=False):
        """Stage a SQL Server table streaming fetchmany→executemany (bounded RAM).

        Validates RML fields against the first fetched batch (missing → warning,
        NULLs), creates the TEMP table empty, then streams batches in.
        Returns (temp, cols) like _stage_api_table.
        """
        _flds = [f for f in (getattr(self, "fields", []) or [])
                 if self._norm_table(getattr(f, "table_source", None) or "") == table_norm]
        if not _flds:
            raise ValueError(f"لا توجد حقول معرفة للجدول '{table_norm.lower()}' في التقرير.")
        _coldefs = [(str(getattr(_f, "name", "")),
                     self._stage_col_type(getattr(_f, "data_type", None), self._stage_engine_of(db)))
                    for _f in _flds]
        _gid = str(info.get("gid") or "x")
        _temp = re.sub(r"[^a-z0-9_]", "_", f"rml_api_{_gid}_{table_norm}".lower())
        _cols_ret = [(str(getattr(_f, "name", "")), str(getattr(_f, "data_type", None) or "")) for _f in _flds]
        if ((not refresh) and self._stage_engine_of(db) == "postgres"
                and self._stage_table_ready(db, self._stage_phy_name(_temp, _coldefs), _coldefs)):
            _phy0 = self._stage_phy_name(_temp, _coldefs)
            self._report_progress({"stage": "cached", "table": str(table_norm).lower(),
                                   "text": f"استخدام مرحلة مخزنة: {str(table_norm).lower()}…"})
            return _phy0, _cols_ret
        _eff = self._write_temp_table(db, _temp, _coldefs, [], table_norm.lower(), refresh=refresh)
        _plan = self._sqlserver_plan(table_norm, info, info.get("row"))
        _cur = db.conn.cursor()
        _use_ev = (self._stage_engine_of(db) == "postgres")
        try:
            from psycopg2.extras import execute_values as _ev
        except Exception:
            _ev = None
            _use_ev = False
        try:
            _ins = (f'INSERT INTO {_q(_eff)} ({", ".join(_q(_n) for _n, _t in _coldefs)}) '
                    f'VALUES ({", ".join(["%s"] * len(_coldefs))})')
            _ev_sql = (f'INSERT INTO {_q(_eff)} ({", ".join(_q(_n) for _n, _t in _coldefs)}) VALUES %s')
            _first = True
            _staged_rows = 0
            for _names, _batch in self._iter_sqlserver_batches(
                    info.get("row"), _plan["user"], _plan["pwd"], _plan["sch"],
                    _plan["tbl"], _plan["sellist"], _plan["where"]):
                if _first:
                    _first = False
                    _have = {str(_k).lower() for _k in (_names or [])}
                    _miss = [str(getattr(_f, "name", "")) for _f in _flds
                             if str(getattr(_f, "name", "") or "").lower() not in _have]
                    if _miss:
                        try:
                            _w = getattr(self, "_api_warnings", None)
                            if _w is None:
                                self._api_warnings = _w = []
                            _w.append({"key": (str(table_norm).lower(), ""),
                                       "message": f"أعمدة غير موجودة في جدول SQL Server "
                                                  f"'{table_norm.lower()}': {'، '.join(_miss)} — ستظهر فارغة",
                                       "skipped": 0})
                        except Exception:
                            pass
                _clean = []
                for _r in (_batch or []):
                    _low = {str(_k).lower(): _v for _k, _v in dict(_r or {}).items()}
                    _row = []
                    for _fn, _ft in _coldefs:
                        try:
                            _row.append(self._coerce_api_value(
                                _low.get(str(_fn).lower()), _ft, _fn, table_norm.lower()))
                        except ValueError:
                            self._note_api_quarantine(table_norm, _fn, _low.get(str(_fn).lower()))
                            _row.append(None)
                    _clean.append(tuple(_row))
                if _clean:
                    if _use_ev and _ev is not None:
                        _ev(_cur, _ev_sql, _clean, page_size=1000)
                    else:
                        _cur.executemany(_ins, _clean)
                    _staged_rows += len(_clean)
                    self._report_progress({"stage": "rows", "table": str(table_norm).lower(),
                                           "rows": _staged_rows,
                                           "text": f"ترحيل {str(table_norm).lower()}… {_staged_rows:,}"})
            db.conn.commit()
            self._stage_touch_meta(db, _eff, _staged_rows)
        except Exception as _e:
            try:
                db.conn.rollback()
            except Exception:
                pass
            raise ValueError(f"تعذر ترحيل بيانات '{table_norm.lower()}': {_e}")
        finally:
            try:
                _cur.close()
            except Exception:
                pass
        return _eff, [(str(getattr(_f, "name", "")), str(getattr(_f, "data_type", None) or "")) for _f in _flds]

    def _stage_api_table(self, table_norm, info, db, refresh=False):
        """Fetch one API table via its source API and stage as TEMP table. Returns (temp, cols)."""
        _eng_info = str((info or {}).get("engine") or "").lower()
        _static = self._api_static_columns()
        _flds = [f for f in (getattr(self, "fields", []) or [])
                 if self._norm_table(getattr(f, "table_source", None) or "") == table_norm]
        if not _flds:
            raise ValueError(f"لا توجد حقول معرفة للجدول '{table_norm.lower()}' في التقرير.")
        if _eng_info == "sqlserver":
            # streaming stage: validate on first batch, INSERT per batch (no full list in RAM)
            return self._stage_sqlserver_streamed(table_norm, info, db, refresh)
        _gid = str(info.get("gid") or "x")
        _temp = re.sub(r"[^a-z0-9_]", "_", f"rml_api_{_gid}_{table_norm}".lower())
        _coldefs = [(str(getattr(_f, "name", "")), self._stage_col_type(getattr(_f, "data_type", None), self._stage_engine_of(db))) for _f in _flds]
        _cols_ret2 = [(str(getattr(_f, "name", "")), str(getattr(_f, "data_type", None) or "")) for _f in _flds]
        if ((not refresh) and self._stage_engine_of(db) == "postgres"
                and self._stage_table_ready(db, self._stage_phy_name(_temp, _coldefs), _coldefs)):
            self._report_progress({"stage": "cached", "table": str(table_norm).lower(),
                                   "text": f"استخدام مرحلة مخزنة: {str(table_norm).lower()}…"})
            return self._stage_phy_name(_temp, _coldefs), _cols_ret2
        _avail_cols = {str(_n).lower() for _n in (_static.get(table_norm) or {})}
        for _f in _flds:
            if str(getattr(_f, "name", "") or "").lower() not in _avail_cols:
                raise ValueError(
                    f"العمود '{getattr(_f, 'name', '')}' غير موجود في جدول الجهاز "
                    f"'{table_norm.lower()}' — الأعمدة المتاحة: "
                    f"{', '.join(sorted(_avail_cols))}.")
        _api_rows = self._fetch_api_rows(table_norm, info, refresh)
        _data = []
        for _r in (_api_rows or []):
            _low = {str(_k).lower(): _v for _k, _v in dict(_r or {}).items()}
            _row = []
            for _fn, _ft in _coldefs:
                try:
                    _row.append(self._coerce_api_value(_low.get(str(_fn).lower()), _ft, _fn, table_norm.lower()))
                except ValueError:
                    self._note_api_quarantine(table_norm, _fn, _low.get(str(_fn).lower()))
                    _row.append(None)
            _data.append(tuple(_row))
        _eff = self._write_temp_table(db, _temp, _coldefs, _data, table_norm.lower(), refresh=refresh)
        return _eff, _cols_ret2

    def _ensure_api_staged(self, refresh=False):
        """Stage API tables and multi-source UNION tables as TEMP tables (once per instance).

        - Single-source API tables -> rml_api_<gid>_<table> (existing path).
        - One table on 2+ physical sources -> ONE rml_union_<table> with rows
          from every connection completely (UNION ALL semantics).
        """
        if getattr(self, "_api_staged_done", False) and not refresh:
            return
        self._api_staged_done = True
        self._api_stage = {}
        self._api_union = {}
        self._api_warnings = []
        try:
            _api_tables = self._api_involved_tables()
        except Exception:
            _api_tables = {}
        try:
            _umap = self._union_partitions()
        except Exception:
            _umap = {}
        # union-owned norms are staged only by the union path (never single)
        _api_tables = {t: i for t, i in _api_tables.items() if t not in _umap}
        if not _api_tables and not _umap:
            return
        _db = getattr(self, "db", None)
        if _db is None:
            raise ValueError(
                "تعذر ترحيل البيانات مؤقتاً: لا توجد قاعدة SQL أساسية — "
                "وجّه التقرير لاتصال قاعدة بيانات (postgres/oracle).")
        try:
            self._stage_gc(_db)
        except Exception:
            pass
        try:
            _rschema = self.metadata.get("schema") if isinstance(getattr(self, "metadata", None), dict) else None
        except Exception:
            _rschema = None
        _staged = {}
        _tbl_items = list(_api_tables.items())
        for _ti, (_norm, _info) in enumerate(_tbl_items):
            self._report_progress({"stage": "table", "table": str(_norm).lower(),
                                   "i": _ti + 1, "of": len(_tbl_items),
                                   "text": f"ترحيل الجدول {_ti + 1}/{len(_tbl_items)}: {str(_norm).lower()}…"})
            _temp, _cols = self._stage_api_table(_norm, _info, _db, refresh)
            _staged[_norm] = {"temp": _temp, "cols": _cols, "info": _info}
            try:
                _ckey = f"{self._db_identity(_db)}|.{_temp.upper()}"
                self._cols_cache[_ckey] = {_n.upper(): (_t or "TEXT").upper() for _n, _t in _cols}
                if not hasattr(self, "_cols_orig") or self._cols_orig is None:
                    self._cols_orig = {}
                self._cols_orig[_ckey] = {_n.upper(): _n for _n, _t in _cols}
            except Exception:
                pass
        self._api_stage = _staged
        _unions = {}
        for _norm, _parts in _umap.items():
            _temp, _cols = self._stage_union_table(_norm, _parts, _db, _rschema, refresh)
            _unions[_norm] = {"temp": _temp, "cols": _cols, "parts": len(_parts)}
            try:
                _ckey = f"{self._db_identity(_db)}|.{_temp.upper()}"
                self._cols_cache[_ckey] = {_n.upper(): (_t or "TEXT").upper() for _n, _t in _cols}
                if not hasattr(self, "_cols_orig") or self._cols_orig is None:
                    self._cols_orig = {}
                self._cols_orig[_ckey] = {_n.upper(): _n for _n, _t in _cols}
            except Exception:
                pass
        self._api_union = _unions
        # NOTE: field/link names stay ORIGINAL everywhere (resolution, filters,
        # links, display). Only SQL emission maps staged tables to TEMP names
        # (disp override, base_q/from_q, _remote_from, _schema_for_table).

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

    def _used_tables(self, columns, filters, sort, group_by, field_table) -> set:
        """Tables referenced by columns/filters/sort/group (via [refs], aliases, field names)."""
        texts: List[str] = []
        for c in (columns or []):
            texts.append(getattr(c, "expr", "") or "")
            texts.append(getattr(c, "where_clause", "") or "")
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
        used = self._used_tables(inlined, filters, sort, group_by, field_table)
        sec_all = sorted(t for t in used if t != base_norm)
        # Route secondaries: local (same physical DB) vs remote
        local_sec: List[str] = []
        remote_sec: Dict[str, Any] = {}
        for s in sec_all:
            sconn = self._conn_key_of_table(s)
            sdb = self._db_for_conn(sconn)
            if self._same_db(sdb, base_db):
                local_sec.append(s)
            else:
                sec_schema = self._schema_for_table(s, sconn, report_schema)
                remote_sec[s] = {"conn": sconn, "db": sdb, "schema": sec_schema, "table": s,
                                 "key": self._infer_join_key(base_norm, s, base_schema_norm, base_db, sec_schema, sdb)}
                if not remote_sec[s]["key"]:
                    raise ValueError(
                        f'تعذر الاستدلال على مفتاح الربط بين "{from_table}" و"{s}" عبر الاتصالات. '
                        f'أضف عموداً مشتركاً (مثل رقم المستند) في الجدولين.')
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
        # Base FROM
        try:
            _btmp = self._staged_temp_of(base_norm)
        except Exception:
            _btmp = None
        if _btmp:
            base_q = _q(_btmp)
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
                    "strip": set(), "local_sec": [], "remote": {}, "base_alias": None}
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
        try:
            for _sn in list(disp.keys()):
                try:
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
            if _bcat and _scat and _bcat != _scat and _bcat != "DATE" and _scat != "DATE":
                # text = integer & friends: compare as text instead of failing
                if _is_pg_db(base_db):
                    _lon, _ron = f"CAST({_lon} AS TEXT)", f"CAST({_ron} AS TEXT)"
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
        return {"from_table": from_table, "base_norm": base_norm, "base_conn": base_conn,
                "base_db": base_db, "base_schema": base_schema, "base_disp": disp.get(base_norm, base_norm),
                "from_q": from_q,
                "columns": new_cols, "table_map": table_map, "merges": merges, "extra": extra,
                "strip": strip, "local_sec": sorted(set(local_sec)), "remote": remote_sec,
                "base_alias": base_alias}

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
                return {"kind": "direct", "alias": alias, "table": sec, "conn": info["conn"],
                        "db": info["db"], "schema": info["schema"], "disp": disp.get(sec, sec),
                        "field": fname, "base_key": info["key"][0]}
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
        return {"kind": "agg", "alias": alias, "table": sec, "conn": info["conn"], "db": info["db"],
                "schema": info["schema"], "disp": disp.get(sec, sec), "func": func, "inner": inner,
                "anchor_base": abase_name, "anchor_sec": ds, "base_key": info["key"][0],
                "key_anchor": ds is None}
    def _prepare_from_and_columns(self, active_table, filters, sort, group_by):
        """Compat wrapper: (from_q, columns, table_map) from the routing plan."""
        plan = self._plan_structure(active_table, filters, sort, group_by)
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

    def _apply_remote_filters(self, filters, plan):
        """Rewrite filters touching remote tables into base-key IN lists."""
        remote = plan.get("remote") or {}
        if not remote:
            return list(filters or [])
        base_norm = plan["base_norm"]
        field_table = self._field_table_map()
        out = []
        for f in (filters or []):
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
            # map members to underlying remote fields (original case)
            mapped = []
            for m in members_r:
                _, sfield = self._remote_field_of_filter(m, plan)
                nm = dict(m)
                nm["field"] = self._orig_col(sec, sfield, info["db"], info["schema"]) if sfield else nm.get("field")
                mapped.append(nm)
            rcols = self._table_columns(sec, info["schema"], info["db"])
            wc, wp = self._where_for_table(mapped, sec, rcols, info["db"], info["schema"])
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
        sch_q = _q(info["schema"]) if info.get("schema") else ""
        _t = info.get("disp") or self._orig_col(info["table"], info["table"], info.get("db"), info.get("schema"))
        return f"{sch_q + '.' if sch_q else ''}{_q(_t)}"

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
                        prm = {f"rk{i}": v for i, v in enumerate(chunk)}
                        cur = self._exec_on(rdb, f"SELECT {cols_sql} FROM {self._remote_from(info)} WHERE {_Q(scol)} IN ({phs})", prm)
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
                    inner_sql = re.sub(r"\[([^\].\[]+)\]", lambda m: _Q(m.group(1)), s["inner"])
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
                        prm = {f"ak{i}": v for i, v in enumerate(chunk)}
                        cur = self._exec_on(
                            rdb, f"SELECT {_Q(ds)}, {agg} FROM {self._remote_from(info)} "
                                 f"WHERE {_Q(ds)} IN ({phs}) GROUP BY {_Q(ds)}", prm)
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
        out = _rx(core_raw, fields, {}, set(), table_map, self._conn_map())
        if re.search(r"\bTO_CHAR\s*\(", out, re.IGNORECASE) or "||" in out:
            alias = getattr(col, "alias", None) or "العمود"
            raise ValueError(f'لا يمكن مقارنة العمود المنسق "{alias}" بهذه العملية — قارن بالمساواة/الاحتواء على النص المعروض.')
        return out

    def _outer_plan(self, filters, sort, group_by, active_table):
        """Prepare outer-filter execution: (prep_cols, table_map, base, outer, extra, outer_cols)."""
        plan = self._plan_structure(active_table, filters, sort, group_by)
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
                                                alias_by_table or _alias_by_table(fields, table_map)))
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

    def _general_where_sql(self, table_map=None, conn_map=None) -> str:
        """Report-level <general_where> condition resolved to SQL (ANDed into master WHERE).

        [field] refs (+ $rule.var$) allowed, resolved with the query's table
        aliases. Returns '' when absent. Raises a clear Arabic error when invalid.
        """
        try:
            comp = getattr(self, "compiler", None)
            raw = ""
            if comp is not None and hasattr(comp, "general_where"):
                raw = comp.general_where() or ""
            raw = str(raw).strip()
            if not raw:
                return ""
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
                                       table_map, conn_map or self._conn_map())
        except Exception as e:
            raise ValueError(f"الشرط العام للتقرير غير صالح: {e}")

    def _apply_general_where(self, where_clause: str, table_map=None, conn_map=None) -> str:
        """AND the report-level general condition into a WHERE clause (literals only, no binds)."""
        gw = self._general_where_sql(table_map, conn_map)
        if not gw:
            return where_clause
        if where_clause and where_clause.strip():
            return where_clause + f" AND ({gw})"
        return f" WHERE ({gw})"

    def _compile_sql2(self, plan, filters, sort, page, page_size, group_by, extra_selects=None):
        """Build SELECT from a routing plan (no re-planning)."""
        from_q = plan["from_q"]
        columns = plan["columns"]
        table_map = plan["table_map"]
        _abt_plan = self._plan_alias_map(plan)
        select_clause = _build_select(columns, getattr(self, "fields", []), table_map=table_map,
                                        dialect="pg" if _is_pg_db(plan.get("base_db")) else "oracle",
                                        conn_map=self._conn_map(),
                                        rules=getattr(self, "rules", []),
                                        alias_by_table=_abt_plan)
        for _ex, _al in (extra_selects or []):
            select_clause += f", {_ex} AS {_q(_al)}"
        for _ex, _al in (plan.get("extra") or []):
            select_clause += f", {_ex} AS {_q(_al)}"
        where_clause, where_params = _build_where(filters or [], columns=columns,
                                                 fields=getattr(self, "fields", []), table_map=table_map,
                                                 conn_map=self._conn_map(), rules=getattr(self, "rules", []))
        where_clause = self._apply_general_where(where_clause, table_map, self._conn_map())
        group_clause = ""
        if group_by:
            group_clause = f" GROUP BY {_resolve_filter_field(group_by, columns, getattr(self, 'fields', []), table_map, self._conn_map(), getattr(self, 'rules', []))}"
        order_clause = _build_order_by(sort, columns=columns)
        paginate_clause, params = self._paginate_clause(page, page_size, dict(where_params), plan["base_db"])
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
                        table_map, self._conn_map())
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
        """Populate _api_stage/_api_union with deterministic TEMP names — zero I/O.

        Same names _ensure_api_staged would create (same coldefs → same hash),
        so the compiled SQL is byte-identical to what execute() runs. Never
        touches source devices and never creates tables. Leaves
        _api_staged_done False so a later execute() still stages for real.
        """
        self._api_stage = {}
        self._api_union = {}
        self._api_warnings = []
        try:
            _api_tables = self._api_involved_tables()
        except Exception:
            _api_tables = {}
        try:
            _umap = self._union_partitions()
        except Exception:
            _umap = {}
        _api_tables = {t: i for t, i in _api_tables.items() if t not in _umap}
        if not _api_tables and not _umap:
            return
        _db = getattr(self, "db", None)
        if _db is None:
            raise ValueError(
                "تعذر ترحيل البيانات مؤقتاً: لا توجد قاعدة SQL أساسية — "
                "وجّه التقرير لاتصال قاعدة بيانات (postgres/oracle).")
        _eng = self._stage_engine_of(_db)
        try:
            _ident = self._db_identity(_db)
        except Exception:
            _ident = "?"

        def _remember(_temp, _cols):
            try:
                _ckey = f"{_ident}|.{_temp.upper()}"
                self._cols_cache[_ckey] = {_n.upper(): (_t or "TEXT").upper() for _n, _t in _cols}
                if not hasattr(self, "_cols_orig") or self._cols_orig is None:
                    self._cols_orig = {}
                self._cols_orig[_ckey] = {_n.upper(): _n for _n, _t in _cols}
            except Exception:
                pass

        _staged = {}
        for _norm, _info in _api_tables.items():
            _flds = [f for f in (getattr(self, "fields", []) or [])
                     if self._norm_table(getattr(f, "table_source", None) or "") == _norm]
            if not _flds:
                raise ValueError(f"لا توجد حقول معرفة للجدول '{str(_norm).lower()}' في التقرير.")
            _coldefs = [(str(getattr(_f, "name", "")),
                         self._stage_col_type(getattr(_f, "data_type", None), _eng))
                        for _f in _flds]
            _cols = [(str(getattr(_f, "name", "")), str(getattr(_f, "data_type", None) or ""))
                     for _f in _flds]
            _gid = str((_info or {}).get("gid") or "x")
            _temp = re.sub(r"[^a-z0-9_]", "_", f"rml_api_{_gid}_{_norm}".lower())
            if _eng == "oracle":
                import hashlib as _hl
                _hs = _hl.md5(",".join(str(_n).lower() for _n, _t in _coldefs).encode()).hexdigest()[:6]
                _phy = (re.sub(r"[^a-z0-9_]", "_", str(_temp).lower()) + "_" + _hs)[:100]
            else:
                _phy = self._stage_phy_name(_temp, _coldefs)
            _staged[_norm] = {"temp": _phy, "cols": _cols, "info": _info}
            _remember(_phy, _cols)
        self._api_stage = _staged
        _unions = {}
        for _norm, _parts in _umap.items():
            _cols = []
            _seen = set()
            for _part in (_parts or []):
                for _f, _gid in (_part or []):
                    _n = str(getattr(_f, "name", "") or "")
                    if _n and _n.lower() not in _seen:
                        _seen.add(_n.lower())
                        _cols.append((_n, getattr(_f, "data_type", None)))
            if not _cols:
                raise ValueError(f"لا توجد حقول معرفة للجدول '{str(_norm).lower()}' في التقرير.")
            _temp = re.sub(r"[^a-z0-9_]", "_", f"rml_union_{_norm}".lower())
            _coldefs = [(_n, self._stage_col_type(_t, _eng)) for _n, _t in _cols]
            _cols2 = [(str(_n), str(_t or "")) for _n, _t in _cols]
            if _eng == "oracle":
                import hashlib as _hl2
                _hs2 = _hl2.md5(",".join(str(_n).lower() for _n, _t in _coldefs).encode()).hexdigest()[:6]
                _phy = (re.sub(r"[^a-z0-9_]", "_", str(_temp).lower()) + "_" + _hs2)[:100]
            else:
                _phy = self._stage_phy_name(_temp, _coldefs)
            _unions[_norm] = {"temp": _phy, "cols": _cols2, "parts": len(_parts or [])}
            _remember(_phy, _cols2)
        self._api_union = _unions

    def preview_real_sql(self, payload: Dict[str, Any]) -> str:
        """The exact SQL execute() would run (stage TEMP names) — without staging or executing.

        Safe for huge tables: only deterministic names are computed, no rows move.
        """
        self._validate_no_exact_dupes()
        self._ensure_stage_names_only()
        return self._preview_compile(payload)

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
        exec_plan = self._plan_structure(active_table, filters, sort, group_by)
        base_db = exec_plan["base_db"]
        filters2 = self._apply_remote_filters(filters, exec_plan)
        _pc, _ptm, base_f, outer_f, extra, outer_cols = self._outer_plan2(exec_plan, filters2)
        sql, params = self._compile_sql2(
            exec_plan, base_f, sort, 1 if outer_f else page,
            "all" if outer_f else page_size, group_by, extra)
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
                                                 conn_map=self._conn_map(), rules=getattr(self, "rules", []))
        where_clause = self._apply_general_where(where_clause, exec_plan["table_map"], self._conn_map())
        from_q = exec_plan["from_q"]
        group_clause = f" GROUP BY {_resolve_filter_field(group_by, exec_plan['columns'], getattr(self, 'fields', []), exec_plan['table_map'], self._conn_map(), getattr(self, 'rules', []))}" if group_by else ""
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
            # Ensure connection (on the routed base DB)
            if not getattr(base_db, "conn", None):
                try:
                    base_db.connect()
                except Exception:
                    pass
            # Get total
            try:
                if getattr(base_db, "conn", None):
                    base_db.conn.rollback()  # clear any poisoned txn so the REAL error surfaces
            except Exception:
                pass
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

            # Get paged rows (raw -> cross-DB merges -> strip helpers -> format)
            try:
                if getattr(base_db, "conn", None):
                    base_db.conn.rollback()  # clear any poisoned txn so the REAL error surfaces
            except Exception:
                pass
            cur = self._exec_on(base_db, sql, params)
            try:
                cols = [d[0].lower() for d in cur.description] if cur.description else []
                raw_rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
                raw_rows = self._apply_merges(raw_rows, exec_plan)
                for _k in strip_extra:
                    for _r in raw_rows:
                        _r.pop(_k, None)
                        _r.pop(str(_k).lower(), None)
                result_rows = [{k: _fmt_cell(v) for k, v in _r.items()} for _r in raw_rows]
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
        return {
            "rows": result_rows,
            "total": total,
            "page": int(page),
            "pageSize": page_size_val,
            "totalPages": total_pages,
            "sql": sql,
            "params": params,
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
        from_q = plan["from_q"]
        table_map = plan.get("table_map")
        try:
            _is_pg = _is_pg_db(plan.get("base_db"))
        except Exception:
            _is_pg = False
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
                                      dialect="pg" if _is_pg else "oracle",
                                      conn_map=self._conn_map(),
                                      rules=getattr(self, "rules", []),
                                      alias_by_table=self._plan_alias_map(plan))
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
        if _is_pg:
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
        if _dtmp:
            from_q = _q(_dtmp)
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
                                        dialect="pg" if _is_pg_db(_ddb) else "oracle",
                                        conn_map=self._conn_map(),
                                        rules=getattr(self, "rules", []),
                                        outer_table=table,
                                        default_schema=schema)
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
            _keys = set(re.findall(r"\[([^\].\[]+)\]", str(_raw)))
            for _qm in re.finditer(r"\[([A-Za-z0-9_][A-Za-z0-9_.]*)\]", str(_raw)):
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
        def _txt(_al):
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
        if is_pg:
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
        _gf = self._apply_remote_filters(filters, _gp)
        gfields = getattr(self, "fields", [])
        where_clause, params = _build_where(_gf or [], columns=_gp["columns"], fields=gfields,
                                            table_map=_gp["table_map"], conn_map=self._conn_map(), rules=getattr(self, "rules", []))
        _gdb = _resolve_filter_field(key, _gp["columns"], gfields, _gp["table_map"], self._conn_map(), getattr(self, "rules", []))
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
        refs = set(re.findall(r"\[([^\].\[]+)\]", raw or ""))
        for _qm in re.finditer(r"\[([A-Za-z0-9_][A-Za-z0-9_.]*)\]", raw or ""):
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
        _gf = self._apply_remote_filters(filters, _gp)
        gfields = getattr(self, "fields", [])
        where_clause, params = _build_where(_gf, columns=_gp["columns"], fields=gfields, table_map=_gp["table_map"], conn_map=self._conn_map(), rules=getattr(self, "rules", []))
        _gdb = _resolve_filter_field(group_by, _gp["columns"], gfields, _gp["table_map"], self._conn_map(), getattr(self, "rules", []))
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
            inner_sql = _rx(inner, fields, {}, set(), None)
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
                        wc, wp = _build_where(base_only_filters, columns=cols_for_summary, fields=fields, table_map=None, conn_map=self._conn_map(), rules=getattr(self, "rules", []))
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
                            bcol, scol = key
                            bwc, bwp = _build_where(base_only_filters, columns=cols_for_summary, fields=fields, table_map=None, conn_map=self._conn_map(), rules=getattr(self, "rules", []))
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
                            bcol, scol = key
                            _bd2 = plan.get("base_disp") or base_norm
                            bfrom = f"{base_schema_q + '.' if base_schema_q else ''}{_q(_bd2)}"
                            bwc, bwp = _build_where(base_only_filters, columns=cols_for_summary, fields=fields, table_map=None, conn_map=self._conn_map(), rules=getattr(self, "rules", []))
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
                                for i, _v in enumerate(_ch):
                                    prm[f"sk{_ci}_{i}"] = _v
                                parts.append(f"{_q(scol)} IN ({phs})")
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

    def _where_for_table(self, filters, table_norm: str, table_cols: Dict[str, str], db=None, schema: Optional[str] = None):
        """WHERE over a secondary table: keep filters mappable by same-name columns.

        Returns (where, params) or (None, None) when a filter cannot map.
        """
        clauses = []
        params = {}
        for i, f in enumerate(filters or [], start=1):
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
            p = f"q{i}"

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
            else:
                clauses.append(f"{qcol}={_db(':'+p, f.get('value'))}")
                params[p] = f.get("value")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return where, params
