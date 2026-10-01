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
  funcs:     IF · COALESCE · NULLIF · UPPER · LOWER · TRIM · LTRIM · RTRIM ·
             LENGTH · SUBSTRING(s,from[,len]) · CONCAT · REPLACE · LEFT · RIGHT ·
             ABS · ROUND(x[,n]) · CEIL · FLOOR · POWER · SQRT · MOD ·
             NOW · CURRENT_DATE · CURRENT_TIMESTAMP ·
             REGEXMATCH · REGEXEXTRACT · WILDCARDMATCH
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
def calc_refs(expr: str) -> List[str]:
    """Field names referenced via get(...) or [...] (order-stable, deduped)."""
    out: List[str] = []
    try:
        for m in _re.finditer(r"\bget\s*\(\s*([^)]+?)\s*\)", expr or "", flags=_re.IGNORECASE):
            a = m.group(1).strip()
            if len(a) >= 2 and a[0] == a[-1] and a[0] in ("'", '"'):
                a = a[1:-1].replace(a[0] * 2, a[0])
            if a and a not in out:
                out.append(a)
        for m in _re.finditer(r"\[([A-Za-z_][A-Za-z0-9_.]*)\]", expr or ""):
            if m.group(1) not in out:
                out.append(m.group(1))
    except Exception:
        pass
    return out


# ── normalization (string-safe: surgery happens on placeholders) ─────────
_STR_PH = "@@STR%d@@"

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


def _normalize(expr: str) -> str:
    skel, lits = _split_strings(expr or "")

    def _lit(k: str) -> str:
        try:
            return lits[int(k)]
        except Exception:
            return ""

    def _getsub(m: _re.Match) -> str:
        a = m.group(1).strip()
        pm = _re.fullmatch(r"@@STR(\d+)@@", a)
        if pm:
            raw = _lit(pm.group(1)).strip()
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
                a = raw[1:-1].replace(raw[0] * 2, raw[0])
            else:
                a = raw
        else:
            a = a.strip("'\"")
        a = a.replace("'", "").replace('"', "")
        return "__get__('%s')" % a

    skel = _re.sub(r"\bget\s*\(\s*([^)]+?)\s*\)", _getsub, skel, flags=_re.IGNORECASE)
    skel = _re.sub(r"\[([A-Za-z_][A-Za-z0-9_.]*)\]", r"__get__('\1')", skel)
    skel = _re.sub(r"\bAND\b", " and ", skel, flags=_re.IGNORECASE)
    skel = _re.sub(r"\bOR\b", " or ", skel, flags=_re.IGNORECASE)
    skel = _re.sub(r"\bNOT\b", " not ", skel, flags=_re.IGNORECASE)
    skel = skel.replace("<>", "!=")
    skel = _re.sub(r"(?<![=!<>])=(?!=)", "==", skel)
    for _w, _p in (("TRUE", "True"), ("FALSE", "False"), ("NULL", "None")):
        skel = _re.sub(r"\b%s\b" % _w, _p, skel, flags=_re.IGNORECASE)
    for _fn in sorted(_FUNCTIONS):
        skel = _re.sub(r"\b%s\s*\(" % _fn, "__fn_%s__(" % _fn, skel, flags=_re.IGNORECASE)
    return _restore_strings(skel, lits)


# ── value helpers ─────────────────────────────────────────────────────────
def _num(v: Any) -> float:
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", ""))
    except Exception:
        raise CalcError("ليست رقماً: %s" % (v,))


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
    return a if _truthy(c) else b


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


def _fn_REPLACE(s, a, b):
    if s is None:
        return None
    return _str(s).replace(_str(a), _str(b))


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


_FUNCTIONS = {
    "IF", "COALESCE", "NULLIF", "UPPER", "LOWER", "TRIM", "LTRIM", "RTRIM",
    "LENGTH", "LEN", "SUBSTRING", "SUBSTR", "CONCAT", "REPLACE", "LEFT", "RIGHT",
    "ABS", "ROUND", "CEIL", "CEILING", "FLOOR", "POWER", "POW", "SQRT", "MOD",
    "NOW", "CURRENT_DATE", "CURRENT_TIMESTAMP",
    "REGEXMATCH", "REGEXEXTRACT", "WILDCARDMATCH",
}

_FN_ALIAS = {"LEN": "LENGTH", "SUBSTR": "SUBSTRING", "CEILING": "CEIL", "POW": "POWER"}


def _fn_table() -> Dict[str, Callable]:
    t: Dict[str, Callable] = {}
    g = globals()
    for name in _FUNCTIONS:
        impl = _FN_ALIAS.get(name, name)
        t["__fn_%s__" % name] = g["_fn_%s" % impl]
    return t


# ── evaluator ─────────────────────────────────────────────────────────────
def eval_calc(expr: str, get: Callable[[str], Any]) -> Any:
    """Evaluate one XSQL scalar expression against a row. Raises CalcError."""
    if expr is None or str(expr).strip() == "":
        raise CalcError("تعبير فارغ")
    try:
        tree = ast.parse(_normalize(str(expr)), mode="eval")
    except SyntaxError as e:
        raise CalcError("صيغة غير صالحة (%s)" % (e.msg if hasattr(e, "msg") else e,))
    env: Dict[str, Any] = {"__get__": get}
    env.update(_fn_table())
    return _eval(tree.body, env)


def eval_calc_row(expr: str, row: Dict[str, Any]) -> Any:
    """eval_calc with a plain-dict row (case-insensitive get)."""
    return eval_calc(expr, make_row_getter(row))


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
        if not isinstance(node.func, ast.Name) or node.func.id not in env:
            raise CalcError("دالة غير مسموحة")
        if node.keywords:
            raise CalcError("وسائط مسماة غير مدعومة")
        fn = env[node.func.id]
        _nm = node.func.id
        _nm = _nm[5:-2] if (_nm.startswith("__fn_") and _nm.endswith("__")) else _nm.strip("_")
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
