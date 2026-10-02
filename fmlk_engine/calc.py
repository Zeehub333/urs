"""
XSQL scalar calculator for computed (محسوب) form fields — fmlk_engine/calc.py.

Evaluates one expression per record WITHOUT eval()/exec() (ast whitelist),
so designer-written formulas can never run arbitrary code. The same grammar
is mirrored in JS (window.fmlkCalcEval in forms_player.html) for live display;
server-side evaluation here is authoritative and its value is stored static.

Supported surface:
  refs:      get(name) · get(conn.table.col) · [name]  (case-insensitive lookup)
  literals:  numbers · 'strings' · "strings" · TRUE/FALSE/NULL
  ops:       + - * / % ** ^(power) · = == != <> > >= < <= · AND OR NOT · & (concat)
  funcs:     Excel-style — IF · AND · OR · XOR · NOT · TRUE · FALSE · IFERROR ·
             IFNA · IFS · SWITCH · COALESCE · NULLIF · UPPER · LOWER · TRIM ·
             LENGTH · SUBSTRING/MID · CONCAT/CONCATENATE · REPLACE(pos) ·
             SUBSTITUTE · LEFT · RIGHT · REPT · EXACT · FIND · SEARCH · PROPER ·
             TEXTJOIN · VALUE · NUMBERVALUE · CHAR · CODE · CLEAN · DOLLAR ·
             FIXED · TEXT · ABS · ROUND · CEIL · FLOOR · POWER · SQRT · MOD ·
             SUM · AVERAGE · MIN · MAX · COUNT · PRODUCT · INT · TRUNC ·
             EXP · LN · LOG · PI · NOW · TODAY · DATE · DATEDIF · DATEVALUE ·
             DAY · DAYS · DAYS360 · EDATE · EOMONTH · HOUR · MINUTE · SECOND ·
             TIME · TIMEVALUE · WEEKDAY · WEEKNUM · ISOWEEKNUM · NETWORKDAYS ·
             WORKDAY · YEAR ·              YEARFRAC · MONTH · REGEXMATCH · REGEXEXTRACT ·
             WILDCARDMATCH (dates are real dates; date arithmetic is Excel-serial)
  rows:      ROWNUM()/ROW() — رقم الصف الحالي (1-based) في الجريد/الفرع،
             و'*' افتراضياً بلا سياق صف
  Empty/NULL numerics coerce to 0 in arithmetic (form-friendly); division or
  sqrt of invalid input yields None (stored NULL) instead of raising.
"""
from __future__ import annotations
import ast
import datetime as _dt
import math as _math
import re as _re
from typing import Any, Callable, Dict, List, Optional


class CalcError(ValueError):
    """Deterministic formula problem (message is user-facing)."""


# ── row getter ────────────────────────────────────────────────────────────
def make_row_getter(row: Dict[str, Any]) -> Callable[[str], Any]:
    """Case-insensitive get(): exact → lower → last dotted segment → its lower."""
    row = row or {}

    def _get(key: str) -> Any:
        k = str(key or "")
        if k in row:
            return row[k]
        lm = {str(q).lower(): q for q in row}
        lk = k.lower()
        if lk in lm:
            return row[lm[lk]]
        last = k.split(".")[-1]
        if last in row:
            return row[last]
        ll = last.lower()
        if ll in lm:
            return row[lm[ll]]
        return None

    return _get


# ── refs declared by an expression ───────────────────────────────────────
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _latin_digits(s: str) -> str:
    """Arabic-Indic/Persian digits → ASCII; Arabic separators → ./empty."""
    try:
        return str(s).translate(_AR_DIGITS).replace("٬", "").replace("٫", ".")
    except Exception:
        return str(s)


def calc_refs(expr: str) -> List[str]:
    """Field names referenced via get(...) or [...] (order-stable, deduped).

    Identifiers may be Latin or Arabic (any Unicode word chars).
    """
    out: List[str] = []
    try:
        for m in _re.finditer(r"\bget\s*\(\s*([^)]+?)\s*\)", expr or "", flags=_re.IGNORECASE):
            a = m.group(1).strip()
            if len(a) >= 2 and a[0] == a[-1] and a[0] in ("'", '"'):
                a = a[1:-1].replace(a[0] * 2, a[0])
            if a and a not in out:
                out.append(a)
        for m in _re.finditer(r"\[([^\W\d][\w.]*)\]", expr or ""):
            if m.group(1) not in out:
                out.append(m.group(1))
    except Exception:
        pass
    return out


# ── normalization (string-safe: surgery happens on placeholders) ─────────
_STR_PH = "@@STR%d@@"
_GET_PH = "@@GET%d@@"

def _split_gets(text: str) -> tuple:
    """Pull whole get(...) spans out (no nested parens); returns (skeleton, [spans])."""
    spans: List[str] = []

    def _sub(m: _re.Match) -> str:
        spans.append(m.group(0))
        return _GET_PH % (len(spans) - 1)

    return _re.sub(r"\bget\s*\([^)]*\)", _sub, text, flags=_re.IGNORECASE), spans


def _split_strings(text: str) -> tuple:
    """Pull '...'/"..." literals out; returns (skeleton, [literals])."""
    lits: List[str] = []
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            j = i + 1
            buf = [ch]
            while j < n:
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch:
                        buf.append("\\" + ch)  # '' داخل النص → \' صالح لبايثون
                        j += 2
                        continue
                    buf.append(ch)
                    j += 1
                    break
                buf.append(text[j])
                j += 1
            lits.append("".join(buf))
            out.append(_STR_PH % (len(lits) - 1))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out), lits


def _restore_strings(text: str, lits: List[str]) -> str:
    for k, v in enumerate(lits):
        text = text.replace(_STR_PH % k, v)
    return text


def _fn_env_name(name: str) -> str:
    return "__fn_" + _re.sub(r"[^A-Za-z0-9_]", "_", name) + "__"


def _normalize(expr: str) -> str:
    skel, lits = _split_strings(expr or "")
    skel, gets = _split_gets(skel)

    def _lit(k: str) -> str:
        try:
            return lits[int(k)]
        except Exception:
            return ""

    skel = _re.sub(r"\[([^\W\d][\w.]*)\]", r"__get__('\1')", skel)
    # bare AND/OR/NOT only (call forms AND()/OR()/NOT() stay functions)
    skel = _re.sub(r"\bAND\b(?!\s*\()", " and ", skel, flags=_re.IGNORECASE)
    skel = _re.sub(r"\bOR\b(?!\s*\()", " or ", skel, flags=_re.IGNORECASE)
    skel = _re.sub(r"\bNOT\b(?!\s*\()", " not ", skel, flags=_re.IGNORECASE)
    skel = skel.replace("<>", "!=")
    skel = _re.sub(r"(?<![=!<>])=(?!=)", "==", skel)
    for _w, _p in (("TRUE", "True"), ("FALSE", "False"), ("NULL", "None")):
        skel = _re.sub(r"\b%s\b" % _w, _p, skel, flags=_re.IGNORECASE)
    for _fn in sorted(_FUNCTIONS, key=len, reverse=True):
        skel = _re.sub(r"\b%s\s*\(" % _re.escape(_fn), _fn_env_name(_fn) + "(", skel,
                        flags=_re.IGNORECASE)
    # restore get(...) spans, converting each to __get__('key')
    for _k, _g in enumerate(gets):
        _m = _re.fullmatch(r"\bget\s*\(\s*([^)]*?)\s*\)", _g, flags=_re.IGNORECASE)
        _a = (_m.group(1).strip() if _m else "")
        _pm = _re.fullmatch(r"@@STR(\d+)@@", _a)
        if _pm:
            _raw = _lit(_pm.group(1)).strip()
            if len(_raw) >= 2 and _raw[0] == _raw[-1] and _raw[0] in ("'", '"'):
                _a = _raw[1:-1].replace(_raw[0] * 2, _raw[0])
            else:
                _a = _raw
        else:
            _a = _a.strip("'\"")
        _a = _a.replace("'", "").replace('"', "")
        skel = skel.replace(_GET_PH % _k, "__get__('%s')" % _a)
    return _restore_strings(skel, lits)


# ── value helpers ─────────────────────────────────────────────────────────
_EXCEL_EPOCH = _dt.date(1899, 12, 30)


def _date_serial(d: _dt.date) -> float:
    return float((d - _EXCEL_EPOCH).days)


def _serial_date(n: float) -> Optional[_dt.date]:
    try:
        return _EXCEL_EPOCH + _dt.timedelta(days=int(n))
    except Exception:
        return None


def _num(v: Any) -> float:
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return 0.0
    if isinstance(v, str):
        v = _latin_digits(v)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, _dt.datetime):
        base = _dt.datetime(1899, 12, 30)
        return (v.replace(tzinfo=None) - base).total_seconds() / 86400.0
    if isinstance(v, _dt.date):
        return _date_serial(v)
    if isinstance(v, _dt.time):
        return (v.hour * 3600 + v.minute * 60 + v.second + v.microsecond / 1e6) / 86400.0
    if isinstance(v, _dt.timedelta):
        return v.total_seconds() / 86400.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", ""))
    except Exception:
        raise CalcError("ليست رقماً: %s" % (v,))


def _to_date(v: Any) -> Optional[_dt.date]:
    """Anything date-like → date (serials/strings parsed); else None."""
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return _serial_date(float(v))
    s = str(v).strip()
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s).date()
    except Exception:
        pass
    for _f in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y",
               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return _dt.datetime.strptime(s[:19], _f).date()
        except Exception:
            continue
    return None


def _to_time(v: Any) -> Optional[_dt.time]:
    if v is None:
        return None
    if isinstance(v, _dt.time):
        return v
    if isinstance(v, _dt.datetime):
        return v.time().replace(microsecond=0)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        secs = int(round(float(v) * 86400.0)) % 86400
        return _dt.time(secs // 3600, (secs % 3600) // 60, secs % 60)
    s = str(v).strip()
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s).time().replace(microsecond=0)
    except Exception:
        pass
    for _f in ("%H:%M:%S", "%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return _dt.datetime.strptime(s, _f).time().replace(microsecond=0)
        except Exception:
            continue
    return None


def _add_months(d: _dt.date, m: int) -> _dt.date:
    tot = d.year * 12 + (d.month - 1) + int(m)
    y, mi = divmod(tot, 12)
    mi += 1
    import calendar as _cal
    last = _cal.monthrange(y, mi)[1]
    return _dt.date(y, mi, min(d.day, last))


def _str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _truthy(v: Any) -> bool:
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip() != ""
    return True


# ── functions (all names UPPER here; lookup is case-insensitive) ──────────
def _fn_IF(c, a, b):
    # (تُقيّم عادة كسولاً عبر _eval_lazy؛ هذه النسخة للاستدعاء المباشر)
    return a if _truthy(c) else b


def _fn_AND(*a):
    return all(_truthy(v) for v in a)


def _fn_OR(*a):
    return any(_truthy(v) for v in a)


def _fn_XOR(*a):
    return sum(1 for v in a if _truthy(v)) % 2 == 1


def _fn_NOT(v):
    return not _truthy(v)


def _fn_TRUE():
    return True


def _fn_FALSE():
    return False


def _fn_IFERROR(v, alt):
    return alt if v is None else v


def _fn_IFNA(v, alt):
    # لا يوجد #N/A مميز في المحرك (المفقود NULL/0) — كـ IFERROR
    return alt if v is None else v


def _fn_COALESCE(*a):
    for v in a:
        if v is not None and not (isinstance(v, str) and v == ""):
            return v
    return None


def _fn_NULLIF(a, b):
    return None if a == b else a


def _fn_UPPER(s):
    return None if s is None else _str(s).upper()


def _fn_LOWER(s):
    return None if s is None else _str(s).lower()


def _fn_TRIM(s):
    return None if s is None else _str(s).strip()


def _fn_LTRIM(s):
    return None if s is None else _str(s).lstrip()


def _fn_RTRIM(s):
    return None if s is None else _str(s).rstrip()


def _fn_LENGTH(s):
    return None if s is None else len(_str(s))


def _fn_SUBSTRING(s, frm, ln=None):
    if s is None:
        return None
    t = _str(s)
    try:
        f = int(float(frm)) - 1
    except Exception:
        raise CalcError("SUBSTRING: الموضع رقم")
    if f < 0:
        f = 0
    if ln is None:
        return t[f:]
    try:
        n = int(float(ln))
    except Exception:
        raise CalcError("SUBSTRING: الطول رقم")
    return t[f:f + n]


def _fn_CONCAT(*a):
    return "".join("" if v is None else _str(v) for v in a)


def _fn_REPLACE(s, start, num, new):
    # Excel positional: REPLACE(old, start_num(1-based), num_chars, new_text)
    if s is None:
        return None
    try:
        st = max(int(float(start)), 1) - 1
        k = max(int(float(num)), 0)
    except Exception:
        raise CalcError("REPLACE: الموضع والعدد أرقام")
    t = _str(s)
    return t[:st] + _str(new) + t[st + k:]


def _fn_LEFT(s, n):
    if s is None:
        return None
    try:
        return _str(s)[:int(float(n))]
    except Exception:
        raise CalcError("LEFT: العدد رقم")


def _fn_RIGHT(s, n):
    if s is None:
        return None
    try:
        return _str(s)[-int(float(n)):] if int(float(n)) else ""
    except Exception:
        raise CalcError("RIGHT: العدد رقم")


def _fn_ABS(x):
    return abs(_num(x))


def _fn_ROUND(x, n=0):
    return round(_num(x), int(float(n)))


def _fn_CEIL(x):
    return _math.ceil(_num(x))


def _fn_CEILING(x):
    return _math.ceil(_num(x))


def _fn_FLOOR(x):
    return _math.floor(_num(x))


def _fn_POWER(x, y):
    return _num(x) ** _num(y)


def _fn_POW(x, y):
    return _num(x) ** _num(y)


def _fn_SQRT(x):
    v = _num(x)
    if v < 0:
        return None
    return _math.sqrt(v)


def _fn_MOD(x, y):
    d = _num(y)
    if d == 0:
        return None
    return _num(x) % d


def _fn_NOW():
    return _dt.datetime.now().replace(microsecond=0)


def _fn_CURRENT_DATE():
    return _dt.date.today()


def _fn_CURRENT_TIMESTAMP():
    return _dt.datetime.now().replace(microsecond=0)


def _fn_REGEXMATCH(s, pat):
    if s is None:
        return False
    try:
        return _re.search(_str(pat), _str(s)) is not None
    except _re.error as e:
        raise CalcError("REGEXMATCH: نمط غير صالح (%s)" % (e,))


def _fn_REGEXEXTRACT(s, pat, g=0):
    if s is None:
        return None
    try:
        m = _re.search(_str(pat), _str(s))
    except _re.error as e:
        raise CalcError("REGEXEXTRACT: نمط غير صالح (%s)" % (e,))
    if not m:
        return None
    try:
        gi = int(float(g))
    except Exception:
        gi = 0
    try:
        return m.group(gi)
    except Exception:
        return m.group(0)


def _fn_WILDCARDMATCH(s, wc):
    if s is None:
        return False
    pat = "".join(".*" if c == "*" else "." if c == "?" else _re.escape(c) for c in _str(wc))
    return _re.fullmatch(pat, _str(s), flags=_re.DOTALL) is not None


# ── Excel text ────────────────────────────────────────────────────────────
def _fn_MID(s, start, ln):
    return _fn_SUBSTRING(s, start, ln)


def _fn_CONCATENATE(*a):
    return _fn_CONCAT(*a)


def _fn_REPT(s, n):
    if s is None:
        return None
    try:
        k = int(float(n))
    except Exception:
        raise CalcError("REPT: العدد رقم")
    if k < 0:
        return None
    return _str(s) * min(k, 10000)


def _fn_EXACT(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return a == b
    return _str(a) == _str(b)


def _fn_FIND(f, w, start=1):
    return _find_impl(f, w, start, True)


def _fn_SEARCH(f, w, start=1):
    return _find_impl(f, w, start, False)


def _find_impl(f, w, start, sensitive):
    if f is None or w is None:
        return None
    try:
        st = max(int(float(start)), 1) - 1
    except Exception:
        raise CalcError("موضع البدء رقم")
    t, p = _str(w), _str(f)
    if not sensitive:
        t, p = t.lower(), p.lower()
    ix = t.find(p, st)
    return None if ix < 0 else ix + 1


def _fn_SUBSTITUTE(s, old, new, inst=None):
    if s is None:
        return None
    t, a, b = _str(s), _str(old), _str(new)
    if inst is None:
        return t.replace(a, b)
    try:
        k = int(float(inst))
    except Exception:
        raise CalcError("SUBSTITUTE: رقم التكرار عدد")
    if k < 1 or a == "":
        return t
    parts = t.split(a)
    if k >= len(parts):
        return t
    return a.join(parts[:k]) + b + a.join(parts[k:])


def _fn_PROPER(s):
    if s is None:
        return None
    return _re.sub(r"[^\W\d_]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(),
                   _str(s), flags=_re.UNICODE)


def _fn_TEXTJOIN(delim, ignore_empty, *a):
    d = _str(delim)
    out = []
    for v in a:
        t = "" if v is None else _str(v)
        if t == "" and _truthy(ignore_empty):
            continue
        out.append(t)
    return d.join(out)


def _fn_VALUE(s):
    if s is None or (isinstance(s, str) and s.strip() == ""):
        return None
    try:
        return float(str(s).strip().replace(",", ""))
    except Exception:
        return None


def _fn_NUMBERVALUE(s, dec=".", grp=","):
    if s is None:
        return None
    try:
        t = _str(s).strip()
        if grp:
            t = t.replace(_str(grp), "")
        if dec and dec != ".":
            t = t.replace(_str(dec), ".")
        return float(t.replace(",", ""))
    except Exception:
        return None


def _fn_CHAR(n):
    try:
        k = int(float(n))
    except Exception:
        raise CalcError("CHAR: الرقم عدد")
    if 0 <= k <= 0x10FFFF:
        try:
            return chr(k)
        except Exception:
            pass
    return None


def _fn_UNICHAR(n):
    return _fn_CHAR(n)


def _fn_CODE(s):
    t = _str(s) if s is not None else ""
    return ord(t[0]) if t else None


def _fn_UNICODE(s):
    return _fn_CODE(s)


def _fn_CLEAN(s):
    if s is None:
        return None
    return "".join(c for c in _str(s) if ord(c) >= 32)


def _fmt_num(v: float, dec: int, commas: bool) -> str:
    neg = v < 0
    v = abs(v)
    t = f"{v:,.{dec}f}" if commas else f"{v:.{dec}f}"
    return ("-" if neg else "") + t


def _fn_DOLLAR(n, dec=2):
    try:
        d = int(float(dec))
    except Exception:
        raise CalcError("DOLLAR: الخانات رقم")
    return "$" + _fmt_num(_num(n), max(d, 0), True)


def _fn_FIXED(n, dec=2, no_commas=False):
    try:
        d = int(float(dec))
    except Exception:
        raise CalcError("FIXED: الخانات رقم")
    return _fmt_num(_num(n), max(d, 0), not _truthy(no_commas))


def _fn_TEXT(v, fmt):
    if v is None:
        return ""
    f = _str(fmt)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    try:
        x = _num(v)
    except CalcError:
        return _str(v)
    m = _re.search(r"\.(0+)", f)
    dec = len(m.group(1)) if m else 0
    if "%" in f:
        return _fmt_num(x * 100, dec, "," in f) + "%"
    return _fmt_num(x, dec, "," in f)


# ── Excel math (variadic) ─────────────────────────────────────────────────
def _nums(args, skip_blank=True):
    out = []
    for v in args:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            if skip_blank:
                continue
            out.append(0.0)
        else:
            out.append(_num(v))
    return out


def _fn_SUM(*a):
    return sum(_nums(a, skip_blank=False))


def _fn_AVERAGE(*a):
    ns = _nums(a)
    return (sum(ns) / len(ns)) if ns else None


def _fn_AVG(*a):
    return _fn_AVERAGE(*a)


def _fn_MIN(*a):
    ns = _nums(a)
    return min(ns) if ns else 0


def _fn_MAX(*a):
    ns = _nums(a)
    return max(ns) if ns else 0


def _fn_COUNT(*a):
    n = 0
    for v in a:
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, str) and v.strip() == "":
            continue
        if isinstance(v, (int, float)):
            n += 1
            continue
        try:
            _num(v)
            n += 1
        except CalcError:
            pass
    return n


def _fn_PRODUCT(*a):
    ns = _nums(a)
    if not ns:
        return None
    r = 1.0
    for v in ns:
        r *= v
    return r


def _fn_INT(x):
    return _math.floor(_num(x))


def _fn_TRUNC(x, n=0):
    v = _num(x)
    k = int(float(n))
    m = 10.0 ** k
    return _math.trunc(v * m) / m


def _fn_EXP(x):
    try:
        return _math.exp(_num(x))
    except Exception:
        return None


def _fn_LN(x):
    v = _num(x)
    return _math.log(v) if v > 0 else None


def _fn_LOG(x, base=10):
    v, b = _num(x), _num(base)
    try:
        if v <= 0 or b <= 0 or b == 1:
            return None
        return _math.log(v, b)
    except Exception:
        return None


def _fn_PI():
    return _math.pi


# ── Excel dates ───────────────────────────────────────────────────────────
def _fn_TODAY():
    return _dt.date.today()


def _fn_DATE(y, m, d):
    try:
        return _dt.date(int(float(y)), int(float(m)), int(float(d)))
    except Exception:
        return None


def _fn_YEAR(v):
    d = _to_date(v)
    return d.year if d else None


def _fn_MONTH(v):
    d = _to_date(v)
    return d.month if d else None


def _fn_DAY(v):
    d = _to_date(v)
    return d.day if d else None


def _fn_DAYS(e, s):
    a, b = _to_date(e), _to_date(s)
    return (a - b).days if a and b else None


def _fn_DATEDIF(s, e, unit):
    a, b = _to_date(s), _to_date(e)
    if not a or not b:
        return None
    u = _str(unit).strip().upper()
    if u == "D":
        return (b - a).days
    if u == "M":
        return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)
    if u == "Y":
        return (b.year - a.year) - (1 if (b.month, b.day) < (a.month, a.day) else 0)
    if u == "MD":
        return (b - _add_months(a, (b.year - a.year) * 12 + (b.month - a.month)
                                - (1 if b.day < a.day else 0))).days
    if u == "YM":
        return ((b.year - a.year) * 12 + (b.month - a.month)
                - (1 if b.day < a.day else 0)) % 12
    if u == "YD":
        try:
            ann = a.replace(year=b.year)
        except ValueError:
            ann = a.replace(year=b.year, day=28)
        return (b - ann).days
    raise CalcError("DATEDIF: الوحدة D/M/Y/MD/YM/YD")


def _days360(a: _dt.date, b: _dt.date, european: bool) -> int:
    d1 = min(a.day, 30) if not european else (30 if a.day == 31 else a.day)
    d2 = min(b.day, 30) if (not european or b.day != 31 or a.day < 30) else 30
    if not european and b.day == 31 and a.day < 30:
        d2 = 31
    elif european:
        d2 = 30 if b.day == 31 else b.day
    return (b.year - a.year) * 360 + (b.month - a.month) * 30 + (d2 - d1)


def _fn_DAYS360(s, e, method=False):
    a, b = _to_date(s), _to_date(e)
    if not a or not b:
        return None
    return _days360(a, b, _truthy(method))


def _fn_EDATE(s, m):
    a = _to_date(s)
    if not a:
        return None
    try:
        return _add_months(a, int(float(m)))
    except Exception:
        return None


def _fn_EOMONTH(s, m):
    a = _to_date(s)
    if not a:
        return None
    try:
        t = _add_months(a.replace(day=1), int(float(m)))
    except Exception:
        return None
    import calendar as _cal
    return t.replace(day=_cal.monthrange(t.year, t.month)[1])


def _fn_TIME(h, mi, se):
    try:
        tot = int(float(h)) * 3600 + int(float(mi)) * 60 + int(float(se))
    except Exception:
        return None
    tot %= 86400
    return _dt.time(tot // 3600, (tot % 3600) // 60, tot % 60)


def _fn_HOUR(v):
    t = _to_time(v)
    return t.hour if t else None


def _fn_MINUTE(v):
    t = _to_time(v)
    return t.minute if t else None


def _fn_SECOND(v):
    t = _to_time(v)
    return t.second if t else None


def _fn_TIMEVALUE(s):
    return _to_time(s)


def _fn_DATEVALUE(s):
    return _to_date(s)


def _fn_WEEKDAY(v, ret=1):
    d = _to_date(v)
    if not d:
        return None
    try:
        t = int(float(ret))
    except Exception:
        raise CalcError("WEEKDAY: النوع 1/2/3")
    if t == 1:
        return (d.weekday() + 1) % 7 + 1
    if t == 2:
        return d.weekday() + 1
    if t == 3:
        return d.weekday()
    raise CalcError("WEEKDAY: النوع 1/2/3")


def _fn_WEEKNUM(v, ret=1):
    d = _to_date(v)
    if not d:
        return None
    try:
        t = int(float(ret))
    except Exception:
        t = 1
    jan1 = _dt.date(d.year, 1, 1)
    off = (jan1.weekday() + 1) % 7 if t == 1 else jan1.weekday()
    return ((d - jan1).days + off) // 7 + 1


def _fn_ISOWEEKNUM(v):
    d = _to_date(v)
    return d.isocalendar()[1] if d else None


def _weekend_set(w) -> set:
    std = {1: {5, 6}, 2: {6, 0}, 3: {0, 1}, 4: {1, 2}, 5: {2, 3}, 6: {3, 4}, 7: {4, 5},
           11: {6}, 12: {0}, 13: {1}, 14: {2}, 15: {3}, 16: {4}, 17: {5}}
    if w is None:
        return {5, 6}
    if isinstance(w, bool):
        return {5, 6}
    if isinstance(w, (int, float)):
        return set(std.get(int(w), {5, 6}))
    s = str(w).strip()
    try:
        return set(std.get(int(float(s)), {5, 6}))
    except Exception:
        pass
    if len(s) == 7 and set(s) <= {"0", "1"}:
        return {k for k, c in enumerate(s) if c == "1"}
    return {5, 6}


def _split_wd_args(args):
    """INTL args: (s, e, [weekend,] holidays...) → (weekend_set, [dates])."""
    if not args:
        return {5, 6}, []
    first, rest = args[0], list(args[1:])
    looks_wd = False
    if isinstance(first, bool):
        looks_wd = False
    elif isinstance(first, (int, float)):
        looks_wd = True
    else:
        s = str(first).strip()
        looks_wd = (len(s) == 7 and set(s) <= {"0", "1"})
        if not looks_wd:
            try:
                int(float(s))
                looks_wd = True
            except Exception:
                looks_wd = False
    if looks_wd:
        dates = [_to_date(v) for v in rest]
        return _weekend_set(first), [d for d in dates if d]
    dates = [_to_date(v) for v in args]
    return {5, 6}, [d for d in dates if d]


def _fn_NETWORKDAYS(s, e, *h):
    a, b = _to_date(s), _to_date(e)
    if not a or not b:
        return None
    hol = {d for d in (_to_date(v) for v in h) if d}
    return _netdays(a, b, {5, 6}, hol)


def _fn_NETWORKDAYS_INTL(s, e, *a):
    d1, d2 = _to_date(s), _to_date(e)
    if not d1 or not d2:
        return None
    wd, hol = _split_wd_args(list(a))
    return _netdays(d1, d2, wd, set(hol))


def _netdays(a: _dt.date, b: _dt.date, wd: set, hol: set) -> int:
    step = 1 if b >= a else -1
    n, d = 0, a
    guard = 0
    while (d <= b if step > 0 else d >= b) and guard < 200000:
        if d.weekday() not in wd and d not in hol:
            n += step
        d += _dt.timedelta(days=step)
        guard += 1
    return n


def _fn_WORKDAY(s, days, *h):
    a = _to_date(s)
    if not a:
        return None
    hol = {d for d in (_to_date(v) for v in h) if d}
    return _workday(a, days, {5, 6}, hol)


def _fn_WORKDAY_INTL(s, days, *a):
    d1 = _to_date(s)
    if not d1:
        return None
    wd, hol = _split_wd_args(list(a))
    return _workday(d1, days, wd, set(hol))


def _workday(a: _dt.date, days, wd: set, hol: set):
    try:
        n = int(float(days))
    except Exception:
        raise CalcError("WORKDAY: الأيام عدد")
    step = 1 if n >= 0 else -1
    d = a
    guard = 0
    while n != 0 and guard < 200000:
        d += _dt.timedelta(days=step)
        guard += 1
        if d.weekday() not in wd and d not in hol:
            n -= step
    return d


def _fn_YEARFRAC(s, e, basis=0):
    a, b = _to_date(s), _to_date(e)
    if not a or not b:
        return None
    try:
        bs = int(float(basis))
    except Exception:
        bs = 0
    if a > b:
        a, b = b, a
    if bs == 1:
        tot, y = 0.0, a.year
        cur = a
        while cur < b:
            nxt = min(_dt.date(y + 1, 1, 1), b + _dt.timedelta(days=1))
            leap = 366 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 365
            tot += max((nxt - cur).days, 0) / leap
            cur, y = nxt, y + 1
        return tot
    if bs == 2:
        return (b - a).days / 360.0
    if bs == 3:
        return (b - a).days / 365.0
    euro = (bs == 4)
    return _days360(a, b, euro) / 360.0


def _fn_YEAR(v):
    d = _to_date(v)
    return d.year if d else None


def _fn_MONTH(v):
    d = _to_date(v)
    return d.month if d else None


def _fn_DAY(v):
    d = _to_date(v)
    return d.day if d else None


_FUNCTIONS = {
    # logical
    "IF", "AND", "OR", "XOR", "NOT", "TRUE", "FALSE", "IFERROR", "IFNA", "IFS", "SWITCH",
    "COALESCE", "NULLIF",
    # text
    "UPPER", "LOWER", "TRIM", "LTRIM", "RTRIM", "LENGTH", "LEN", "SUBSTRING", "SUBSTR",
    "MID", "CONCAT", "CONCATENATE", "REPLACE", "SUBSTITUTE", "LEFT", "RIGHT", "REPT",
    "EXACT", "FIND", "SEARCH", "PROPER", "TEXTJOIN", "VALUE", "NUMBERVALUE",
    "CHAR", "UNICHAR", "CODE", "UNICODE", "CLEAN", "DOLLAR", "FIXED", "TEXT",
    # math
    "ABS", "ROUND", "CEIL", "CEILING", "FLOOR", "POWER", "POW", "SQRT", "MOD",
    "SUM", "AVERAGE", "AVG", "MIN", "MAX", "COUNT", "PRODUCT", "INT", "TRUNC",
    "EXP", "LN", "LOG", "PI",
    # date/time
    "NOW", "TODAY", "CURRENT_DATE", "CURRENT_TIMESTAMP",
    "DATE", "DATEDIF", "DATEVALUE", "DAY", "DAYS", "DAYS360", "EDATE", "EOMONTH",
    "HOUR", "ISOWEEKNUM", "MINUTE", "MONTH", "NETWORKDAYS", "NETWORKDAYS.INTL",
    "SECOND", "TIME", "TIMEVALUE", "WEEKDAY", "WEEKNUM",
    "WORKDAY", "WORKDAY.INTL", "YEAR", "YEARFRAC",
    # pattern
    "REGEXMATCH", "REGEXEXTRACT", "WILDCARDMATCH",
    # rows
    "ROWNUM", "ROW",
}

_FN_ALIAS = {"LEN": "LENGTH", "SUBSTR": "SUBSTRING", "MID": "SUBSTRING",
             "CONCATENATE": "CONCAT", "CEILING": "CEIL", "POW": "POWER",
             "AVG": "AVERAGE"}


_ROWNUM_NAMES = {"ROWNUM", "ROW"}


def _fn_table() -> Dict[str, Callable]:
    t: Dict[str, Callable] = {}
    g = globals()
    for name in _FUNCTIONS:
        if name in _LAZY or name in _ROWNUM_NAMES:
            continue  # كسول / سياقي: يُحقن عند التقييم لا من الجدول الثابت
        pyname = _re.sub(r"[^A-Za-z0-9_]", "_", _FN_ALIAS.get(name, name))
        t[_fn_env_name(name)] = g["_fn_" + pyname]
    return t


_LAZY = {"IF", "IFERROR", "IFNA", "IFS", "SWITCH"}


def _lazy_name(fid: str) -> Optional[str]:
    if fid.startswith("__fn_") and fid.endswith("__"):
        inner = fid[5:-2]
        for name in _LAZY:
            if inner == _re.sub(r"[^A-Za-z0-9_]", "_", name):
                return name
    return None


def _eval_lazy(name: str, args: List[ast.AST], env: Dict[str, Any]) -> Any:
    if name == "IF":
        if len(args) != 3:
            raise CalcError("IF: ثلاثة وسائط (شرط، قيمة، بديل)")
        return _eval(args[1], env) if _truthy(_eval(args[0], env)) else _eval(args[2], env)
    if name in ("IFERROR", "IFNA"):
        if len(args) != 2:
            raise CalcError("%s: وسيطان (قيمة، بديل)" % name)
        try:
            _v = _eval(args[0], env)
        except Exception:
            _v = None
        # None لغة المحرك للإخفاق (قسمة صفر/مفقود) — تُلتقط كخطأ أيضاً
        return _eval(args[1], env) if _v is None else _v
    if name == "IFS":
        if len(args) < 2 or len(args) % 2:
            raise CalcError("IFS: أزواج شرط/قيمة")
        for k in range(0, len(args), 2):
            if _truthy(_eval(args[k], env)):
                return _eval(args[k + 1], env)
        return None
    if name == "SWITCH":
        if len(args) < 2:
            raise CalcError("SWITCH: تعبير + زوج واحد على الأقل")
        v = _eval(args[0], env)
        rest = args[1:]
        dflt = rest[-1] if len(rest) % 2 else None
        pairs = rest[:-1] if len(rest) % 2 else rest
        for k in range(0, len(pairs), 2):
            if _eq(v, _eval(pairs[k], env)):
                return _eval(pairs[k + 1], env)
        return _eval(dflt, env) if dflt is not None else None
    raise CalcError("دالة غير مسموحة")


# ── evaluator ─────────────────────────────────────────────────────────────
def eval_calc(expr: str, get: Callable[[str], Any], rownum: Any = None) -> Any:
    """Evaluate one XSQL scalar expression against a row. Raises CalcError.

    rownum: رقم الصف الحالي (1-based) لسياقات الجريد/الفروع —
    دالة ROWNUM()‎ ترده، وبلا سياق ترد الرمز '*' افتراضياً.
    """
    if expr is None or str(expr).strip() == "":
        raise CalcError("تعبير فارغ")
    try:
        tree = ast.parse(_normalize(str(expr)), mode="eval")
    except SyntaxError as e:
        raise CalcError("صيغة غير صالحة (%s)" % (e.msg if hasattr(e, "msg") else e,))

    def _rownum_fn(*a: Any) -> Any:
        if a:
            raise CalcError("ROWNUM: بلا وسائط")
        return rownum if rownum is not None else "*"

    env: Dict[str, Any] = {"__get__": get}
    env.update(_fn_table())
    for _rn in _ROWNUM_NAMES:
        env[_fn_env_name(_rn)] = _rownum_fn
    return _eval(tree.body, env)


def eval_calc_row(expr: str, row: Dict[str, Any], rownum: Any = None) -> Any:
    """eval_calc with a plain-dict row (case-insensitive get)."""
    return eval_calc(expr, make_row_getter(row), rownum)


def _eval(node: ast.AST, env: Dict[str, Any]) -> Any:  # noqa: C901
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool)) or node.value is None:
            return node.value
        raise CalcError("قيمة غير مدعومة")
    if isinstance(node, ast.Name):
        if node.id in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[node.id]
        raise CalcError("مرجع غير معروف '%s' — استخدم get(%s)" % (node.id, node.id))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise CalcError("دالة غير مسموحة")
        if node.keywords:
            raise CalcError("وسائط مسماة غير مدعومة")
        _lz = _lazy_name(node.func.id)
        if _lz:
            return _eval_lazy(_lz, node.args, env)
        if node.func.id not in env:
            raise CalcError("دالة غير مسموحة")
        _nm = node.func.id
        _nm = _nm[5:-2] if (_nm.startswith("__fn_") and _nm.endswith("__")) else _nm.strip("_")
        fn = env[node.func.id]
        try:
            return fn(*[_eval(a, env) for a in node.args])
        except CalcError:
            raise
        except Exception as e:
            raise CalcError("خطأ في %s (%s)" % (_nm or "الدالة", e))
    if isinstance(node, ast.BinOp):
        l, r = _eval(node.left, env), _eval(node.right, env)
        op = node.op
        if isinstance(op, ast.Add):
            try:
                return _num(l) + _num(r)
            except CalcError:
                if l is None and r is None:
                    return None
                return _str(l) + _str(r)
        if isinstance(op, ast.BitAnd):
            return _str(l) + _str(r)
        if isinstance(op, ast.Sub):
            return _num(l) - _num(r)
        if isinstance(op, ast.Mult):
            return _num(l) * _num(r)
        if isinstance(op, (ast.Div,)):
            d = _num(r)
            if d == 0:
                return None
            return _num(l) / d
        if isinstance(op, ast.FloorDiv):
            d = _num(r)
            if d == 0:
                return None
            return _num(l) // d
        if isinstance(op, ast.Mod):
            d = _num(r)
            if d == 0:
                return None
            return _num(l) % d
        if isinstance(op, (ast.Pow, ast.BitXor)):
            try:
                return _num(l) ** _num(r)
            except Exception:
                return None
        if isinstance(op, (ast.BitOr, ast.LShift, ast.RShift)):
            raise CalcError("عامل غير مدعوم")
        raise CalcError("عامل غير مدعوم")
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -_num(v)
        if isinstance(node.op, ast.UAdd):
            return +_num(v)
        if isinstance(node.op, ast.Not):
            return not _truthy(v)
        if isinstance(node.op, ast.Invert):
            raise CalcError("عامل غير مدعوم")
        raise CalcError("عامل غير مدعوم")
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            res: Any = True
            for v in node.values:
                res = _eval(v, env)
                if not _truthy(res):
                    return res
            return res
        if isinstance(node.op, ast.Or):
            res = False
            for v in node.values:
                res = _eval(v, env)
                if _truthy(res):
                    return res
            return res
        raise CalcError("عامل منطقي غير مدعوم")
    if isinstance(node, ast.Compare):
        l = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            r = _eval(comp, env)
            ok = _compare(op, l, r)
            if not ok:
                return False
            l = r
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, env) if _truthy(_eval(node.test, env)) else _eval(node.orelse, env)
    raise CalcError("تركيب غير مدعوم")


def _compare(op: ast.AST, l: Any, r: Any) -> bool:
    if isinstance(op, (ast.Eq,)):
        return _eq(l, r)
    if isinstance(op, (ast.NotEq,)):
        return not _eq(l, r)
    if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
        if l is None or r is None:
            return False
        try:
            lf, rf = _num(l), _num(r)
        except CalcError:
            lf, rf = _str(l), _str(r)
            return _str_cmp(op, lf, rf)
        if isinstance(op, ast.Lt):
            return lf < rf
        if isinstance(op, ast.LtE):
            return lf <= rf
        if isinstance(op, ast.Gt):
            return lf > rf
        return lf >= rf
    if isinstance(op, (ast.In, ast.NotIn)):
        raise CalcError("IN غير مدعومة في الحقل المحسوب")
    raise CalcError("مقارنة غير مدعومة")


def _str_cmp(op: ast.AST, lf: str, rf: str) -> bool:
    if isinstance(op, ast.Lt):
        return lf < rf
    if isinstance(op, ast.LtE):
        return lf <= rf
    if isinstance(op, ast.Gt):
        return lf > rf
    return lf >= rf


def _eq(l: Any, r: Any) -> bool:
    if l is None or r is None:
        return l is None and r is None
    if isinstance(l, bool) or isinstance(r, bool):
        return bool(l) is bool(r)
    if isinstance(l, (int, float)) and isinstance(r, (int, float)):
        return l == r
    try:
        if str(l).strip() != "" and str(r).strip() != "":
            return _num(l) == _num(r)
    except CalcError:
        pass
    return _str(l) == _str(r)
