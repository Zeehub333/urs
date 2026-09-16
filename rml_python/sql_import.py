"""Import an ad-hoc SQL SELECT into RML wizard structures (fields/columns/links/where).

Covers the engine's contract (see rml_python/engine.py::_build_select and
rml_python/namespaces.py::_resolve_expression):
  - <field name=DB column> referenced as [name] / [table.name] / [conn.table.name]
  - <column alias=display, expr, col_type direct|computed|aggregated>
  - <general_where> ANDed into the master WHERE (same [field] refs)
  - <links> from JOIN ... ON equalities

Supported SQL surface (single statement):
  SELECT [DISTINCT] item [, ...] FROM [schema.]table [[AS] alias]
    [JOIN ... [ON cond] ...] [WHERE ...] [GROUP BY ...] [HAVING ...]
    [ORDER BY ...] [LIMIT/OFFSET/FETCH ...]
  Items: bare/qualified columns, AS + implicit aliases, quoted "aliases",
  functions incl. nesting, CASE WHEN, literals, *, t.* (needs expander).
Out of scope (reported in warnings): UNION/CTE/WITH, window OVER, subqueries
in SELECT/WHERE (kept verbatim), multi-DB, ORDER/LIMIT semantics, GROUP BY
(RML groups are header spanning, not SQL grouping).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

AGG_FUNCS = {"COUNT", "SUM", "AVG", "MIN", "MAX"}
DATE_FUNCS = {"TO_DATE", "TO_TIMESTAMP", "DATE_TRUNC", "TRUNC", "CURRENT_DATE",
              "SYSDATE", "GETDATE", "NOW", "CURDATE"}
COMPUTED_HINT = re.compile(r"(\+|-|\*|/|\|\||\bCASE\b|\bDECODE\b|\bNVL\b|\bCOALESCE\b)",
                           re.IGNORECASE)

KEYWORDS = {
    "SELECT", "DISTINCT", "ALL", "FROM", "WHERE", "GROUP", "BY", "HAVING", "ORDER",
    "LIMIT", "OFFSET", "FETCH", "FOR", "AS", "AND", "OR", "NOT", "IN", "IS", "NULL",
    "LIKE", "ILIKE", "BETWEEN", "EXISTS", "CASE", "WHEN", "THEN", "ELSE", "END",
    "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "ON", "USING",
    "UNION", "INTERSECT", "EXCEPT", "WITH", "ASC", "DESC", "NULLS", "FIRST", "LAST",
    "OVER", "PARTITION", "ROWS", "RANGE", "UNBOUNDED", "PRECEDING", "FOLLOWING",
    "CURRENT", "ROW", "ONLY", "TOP", "TRUE", "FALSE", "CAST", "EXTRACT",
}


# ── low-level scanning (quote/comment/paren aware) ───────────────────────────

_STR_RE_SRC = r"(?:[Nn])?'(?:''|[^'])*'"

# Data-type names are never column refs (T-SQL/Oracle/PG). A real column that
# happens to share a type name must be quoted in SQL anyway.
TYPES = {
    "INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "DECIMAL", "NUMERIC",
    "FLOAT", "REAL", "MONEY", "SMALLMONEY", "CHAR", "VARCHAR", "NCHAR",
    "NVARCHAR", "TEXT", "NTEXT", "DATE", "TIME", "DATETIME", "DATETIME2",
    "SMALLDATETIME", "DATETIMEOFFSET", "BIT", "UNIQUEIDENTIFIER", "BINARY",
    "VARBINARY", "IMAGE", "SQL_VARIANT", "XML",
}

def strip_comments(sql: str) -> str:
    out, i, n = [], 0, len(sql)
    q = None
    while i < n:
        ch = sql[i]
        if q:
            out.append(ch)
            if ch == q:
                if q == "'" and i + 1 < n and sql[i + 1] == "'":
                    out.append(sql[i + 1])
                    i += 1
                else:
                    q = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            q = ch
            out.append(ch)
            i += 1
            continue
        if ch == "[":
            j = sql.find("]", i + 1)
            j = n - 1 if j < 0 else j
            out.append(sql[i:j + 1])
            i = j + 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i + 2)
            i = n if j < 0 else j
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_top_commas(s: str) -> List[str]:
    """Split on commas at paren-depth 0, quote aware."""
    parts, depth, q, cur = [], 0, None, []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if q:
            cur.append(ch)
            if ch == q:
                if q == "'" and i + 1 < n and s[i + 1] == "'":
                    cur.append(s[i + 1])
                    i += 1
                else:
                    q = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            q = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "[":
            j = s.find("]", i + 1)
            j = n - 1 if j < 0 else j
            cur.append(s[i:j + 1])
            i = j + 1
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
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


_CLAUSE_RES = [
    ("where", re.compile(r"\bWHERE\b", re.IGNORECASE)),
    ("group_by", re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE)),
    ("having", re.compile(r"\bHAVING\b", re.IGNORECASE)),
    ("order_by", re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)),
    ("limit", re.compile(r"\b(LIMIT|OFFSET|FETCH)\b", re.IGNORECASE)),
]


def split_clauses(sql: str) -> Dict[str, str]:
    """Split SELECT..FROM..WHERE..GROUP BY..HAVING..ORDER BY..LIMIT at depth 0."""
    bounds: List[Tuple[str, int, int]] = []  # (name, kw_start, body_start)
    depth, q, i, n = 0, None, 0, len(sql)
    first_from = None
    while i < n:
        ch = sql[i]
        if q:
            if ch == q:
                if q == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 1
                else:
                    q = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            q = ch
            i += 1
            continue
        if ch == "[":
            j = sql.find("]", i + 1)
            i = n if j < 0 else j + 1
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
            if first_from is None:
                m = re.match(r"FROM\b", sql[i:], re.IGNORECASE)
                if m and re.match(r"SELECT\b", sql[:i].strip().split()[-1] + " ", re.IGNORECASE) is None:
                    pass
                if m:
                    # ensure this FROM belongs to the outer SELECT (a SELECT precedes it)
                    head = sql[:i]
                    if re.search(r"\bSELECT\b", head, re.IGNORECASE):
                        first_from = (i, i + m.end())
            for name, rx in _CLAUSE_RES:
                m = rx.match(sql[i:])
                if m and (name != "where" or first_from is not None or True):
                    if name == "where" and first_from is None:
                        # WHERE before any FROM at depth 0 → still a clause boundary
                        pass
                    bounds.append((name, i, i + m.end()))
                    i += m.end()
                    break
            else:
                i += 1
                continue
            continue
        i += 1
    out: Dict[str, str] = {}
    if first_from is None:
        raise ValueError("No top-level FROM found — only single SELECT..FROM supported")
    sel_body = sql[:first_from[0]]
    msel = re.search(r"\bSELECT\b", sel_body, re.IGNORECASE)
    if not msel:
        raise ValueError("Statement must start with SELECT")
    out["select"] = sel_body[msel.end():]
    if re.search(r"\bDISTINCT\b", out["select"][:40], re.IGNORECASE):
        out["distinct"] = "1"
        out["select"] = re.sub(r"^\s*DISTINCT\b", "", out["select"][:40] + out["select"][40:],
                               count=1, flags=re.IGNORECASE)
    prev_end = first_from[1]
    prev_name = "from"
    ordered = sorted(bounds, key=lambda b: b[1])
    for name, ks, bs in ordered:
        if ks < first_from[0]:
            continue
        out[prev_name] = sql[prev_end:ks]
        prev_name, prev_end = name, bs
    out[prev_name] = sql[prev_end:]
    return {k: v.strip().strip(";").strip() for k, v in out.items()}


# ── identifiers ──────────────────────────────────────────────────────────────

def unquote_ident(tok: str) -> str:
    t = (tok or "").strip()
    if len(t) >= 2 and ((t[0] == '"' and t[-1] == '"') or (t[0] == "`" and t[-1] == "`")):
        return t[1:-1].replace('""', '"')
    if len(t) >= 2 and t[0] == "[" and t[-1] == "]":
        return t[1:-1]
    if len(t) >= 2 and t[0] == "'" and t[-1] == "'" and len(t.replace("''", "")) >= 2:
        return t
    return t


def is_string_lit(tok: str) -> bool:
    t = (tok or "").strip()
    return re.match(r"(?:[Nn])?'(?:''|[^'])*'$", t, re.DOTALL) is not None


def split_dotted(ref: str) -> List[str]:
    """Split a.b.c on dots outside quotes/brackets."""
    parts, cur, q = [], [], None
    i, n = 0, len(ref)
    while i < n:
        ch = ref[i]
        if q:
            cur.append(ch)
            if ch == q:
                q = None
            i += 1
            continue
        if ch in ('"', "`"):
            q = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "[":
            j = ref.find("]", i + 1)
            j = n - 1 if j < 0 else j
            cur.append(ref[i:j + 1])
            i = j + 1
            continue
        if ch == ".":
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return [unquote_ident(p) for p in parts]


_IDENT_RE = re.compile(r"[A-Za-z_\u0600-\u06FF][A-Za-z0-9_$\u0600-\u06FF]*")


def extract_col_refs(expr: str) -> List[str]:
    """Dotted/plain column refs in an expression (skips strings, keywords, func names)."""
    refs: List[str] = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if expr[j] == "'":
                    if j + 1 < n and expr[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue
        if ch in ('"', "`"):
            j = expr.find(ch, i + 1)
            i = n if j < 0 else j + 1
            continue
        if ch == "[":
            j = expr.find("]", i + 1)
            i = n if j < 0 else j + 1
            continue
        m = _IDENT_RE.match(expr[i:])
        if not m:
            i += 1
            continue
        word = m.group(0)
        j = i + len(word)
        # N'...' unicode literal prefix — skip the whole string, not the N
        if len(word) == 1 and word in ("N", "n") and j < n and expr[j] == "'":
            k = j + 1
            while k < n:
                if expr[k] == "'":
                    if k + 1 < n and expr[k + 1] == "'":
                        k += 2
                        continue
                    break
                k += 1
            i = k + 1
            continue
        # dotted chain
        chain = [word]
        k = j
        while True:
            kk = k
            while kk < n and expr[kk] in (" ", "\t"):
                kk += 1
            if kk < n and expr[kk] == ".":
                kk += 1
                while kk < n and expr[kk] in (" ", "\t"):
                    kk += 1
                m2 = _IDENT_RE.match(expr[kk:])
                if m2:
                    chain.append(m2.group(0))
                    k = kk + len(m2.group(0))
                    continue
            break
        # function call? word followed by '('
        kk = k
        while kk < n and expr[kk] in (" ", "\t"):
            kk += 1
        if kk < n and expr[kk] == "(":
            i = kk + 1
            continue
        if len(chain) == 1 and word.upper() in KEYWORDS:
            i = j
            continue
        if len(chain) == 1 and word.upper() in TYPES:
            i = j
            continue
        if len(chain) == 1 and re.fullmatch(r"\d+(\.\d+)?", word):
            i = j
            continue
        refs.append(".".join(chain))
        i = k
    # dedupe, keep order
    seen, out = set(), []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


# ── SELECT items / tables ────────────────────────────────────────────────────

def parse_select_item(item: str) -> Tuple[str, str]:
    """-> (expr, alias). Alias: AS x | "x" | trailing bare word (not after )/quote/keyword)."""
    s = item.strip()
    m = re.match(r"^(.*)\s+[Aa][Ss]\s+(.+)$", s, re.DOTALL)
    if m:
        body, alias = m.group(1).strip(), m.group(2).strip()
        return body, unquote_ident(alias)
    # trailing quoted alias
    m = re.match(r'^(.*)\s+"((?:[^"]|"")+)"\s*$', s, re.DOTALL)
    if m and m.group(1).strip():
        return m.group(1).strip(), m.group(2).replace('""', '"')
    m = re.match(r"^(.*)\s+`([^`]+)`\s*$", s, re.DOTALL)
    if m and m.group(1).strip():
        return m.group(1).strip(), m.group(2)
    # trailing bare-word alias (not a keyword/number, body must not end with )/quote)
    # trailing bare-word alias (not a keyword/number). A call/literal/column
    # followed by a bare word IS an alias (SUM(x) total, 'a' b, col c);
    # an operator/comma/dot/star tail means the word belongs to the expression.
    m = re.match(r"^(.*\S)\s+([A-Za-z_\u0600-\u06FF][A-Za-z0-9_$\u0600-\u06FF]*)\s*$", s, re.DOTALL)
    if m:
        body, cand = m.group(1).strip(), m.group(2)
        if cand.upper() not in KEYWORDS and not re.fullmatch(r"\d+(\.\d+)?", cand):
            if not re.search(r"(==|!=|<>|<=|>=|<|>|=|\+|-|\*|/|,|\.)$", body):
                return body, cand
    return s, ""


_TABLE_FACTOR_RE = re.compile(
    r"^\s*(?:(?P<schema>[A-Za-z_][\w$#]*|\"[^\"]+\"|`[^`]+`)\s*\.\s*)?"
    r"(?P<table>[A-Za-z_][\w$#]*|\"[^\"]+\"|`[^`]+`|\[[^\]]+\])"
    r"(?:\s+(?:[Aa][Ss]\s+)?(?P<alias>[A-Za-z_][\w$#]*|\"[^\"]+\"|`[^`]+`|\[[^\]]+\]))?\s*$")


def parse_table_factor(text: str) -> Optional[Dict[str, Any]]:
    """Parse one FROM/JOIN table factor (not subqueries — caller handles those)."""
    m = _TABLE_FACTOR_RE.match(text.strip())
    if not m:
        return None
    schema = unquote_ident(m.group("schema") or "")
    table = unquote_ident(m.group("table") or "")
    alias = unquote_ident(m.group("alias") or "")
    if not table or table.upper() in KEYWORDS:
        return None
    return {"schema": schema, "table": table, "alias": alias or table}


_JOIN_SPLIT_RE = re.compile(
    r"\b((?:NATURAL\s+)?(?:LEFT|RIGHT|FULL|INNER|CROSS)?\s*(?:OUTER\s+)?JOIN)\b", re.IGNORECASE)


def _balanced_outer(text: str) -> Optional[str]:
    """Inner text if wrapped in ONE balanced paren pair spanning all, else None."""
    s = (text or "").strip()
    if len(s) < 2 or not s.startswith("(") or not s.endswith(")"):
        return None
    depth, q, i, n = 0, None, 0, len(s)
    while i < n:
        ch = s[i]
        if q:
            if ch == q:
                q = None
            i += 1
            continue
        if ch in ("'", '"'):
            q = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and i != n - 1:
                return None
        i += 1
    return s[1:-1] if depth == 0 else None


def _unparen_table(text: str) -> Optional[Dict[str, Any]]:
    """A parenthesized BARE table (no SELECT inside) → table factor, else None."""
    inner = _balanced_outer(text)
    if inner is None:
        return None
    if re.search(r"\bSELECT\b", inner, re.IGNORECASE):
        return None
    return parse_table_factor(inner.strip())


_DEPTH0_KEYWORDS = ("LEFT", "RIGHT", "FULL", "INNER", "OUTER", "CROSS", "NATURAL",
                    "JOIN", "ON", "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT",
                    "OFFSET", "UNION", "EXCEPT", "INTERSECT")


def _subquery_source(inner: str) -> Optional[Dict[str, str]]:
    """Underlying {schema, table} behind a subquery, else None.

    Only single real-table sources (first depth-0 FROM factor); nested
    subqueries, multi-table inners and bare-table parens → None.
    """
    s = (inner or "").strip()
    if not re.search(r"\bSELECT\b", s, re.IGNORECASE):
        return None
    m = re.search(r"\bFROM\b", s, re.IGNORECASE)
    if not m:
        return None
    # depth of FROM must be 0 relative to the subquery
    depth, q, d = 0, None, 0
    i = 0
    while i < m.start():
        ch = s[i]
        if q:
            if ch == q:
                q = None
        elif ch in ("'", '"'):
            q = ch
        elif ch == "(":
            d += 1
        elif ch == ")":
            d = max(0, d - 1)
        i += 1
    if d != 0 or q:
        return None
    rest = s[m.end():].strip()
    if rest.startswith("("):
        return None
    tok = re.split(r"\s+", rest, maxsplit=1)[0] if rest else ""
    tok = tok.rstrip(",;")
    parts = [unquote_ident(p) for p in tok.split(".")]
    if not parts or not parts[-1] or parts[-1].upper() in KEYWORDS:
        return None
    if len(parts) >= 3:
        return None
    table = parts[-1]
    schema = parts[-2] if len(parts) == 2 else ""
    if not re.fullmatch(r"[A-Za-z_][\w$#]*", table):
        return None
    if schema and not re.fullmatch(r"[A-Za-z_][\w$#]*", schema):
        return None
    return {"schema": schema, "table": table}


def _entry_table(t: Dict[str, Any]) -> str:
    """Effective real table: direct, else subquery-resolved source, else alias."""
    if (t or {}).get("table"):
        return t["table"]
    if (t or {}).get("derived") and (t or {}).get("source_table"):
        return t["source_table"]
    return (t or {}).get("alias") or ""


def _entry_schema(t: Dict[str, Any], default: str = "") -> str:
    if (t or {}).get("schema"):
        return t["schema"]
    if (t or {}).get("derived") and (t or {}).get("source_schema"):
        return t["source_schema"]
    return default


def parse_from(from_clause: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """-> (tables, join_ons, warnings). tables: [{schema,table,alias,derived}]."""
    tables: List[Dict[str, Any]] = []
    join_ons: List[Dict[str, Any]] = []
    warnings: List[str] = []
    chunks = _JOIN_SPLIT_RE.split(from_clause)
    # chunks[0] = first factor; then (JOIN-kw, factor-with-ON)...
    first = chunks[0].strip()
    # subquery factor? (parenthesized bare table tolerated first)
    rest = first
    sub = None
    _pt = _unparen_table(rest)
    if _pt is not None:
        _pt["derived"] = False
        tables.append(_pt)
    elif rest.startswith("("):
        depth, q, i = 0, None, 0
        while i < len(rest):
            ch = rest[i]
            if q:
                if ch == q:
                    q = None
                i += 1
                continue
            if ch in ("'", '"'):
                q = ch
                i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        inner, tail = rest[1:i], rest[i + 1:].strip()
        am = re.match(r"^(?:[Aa][Ss]\s+)?([A-Za-z_][\w$#]*)$", tail)
        alias = am.group(1) if am else "sub"
        sub = {"inner": inner, "alias": alias}
        _src0 = _subquery_source(inner)
        _ent0: Dict[str, Any] = {"schema": "", "table": "", "alias": alias, "derived": True,
                                 "sub_sql": inner}
        if _src0:
            _ent0["source_schema"] = _src0["schema"]
            _ent0["source_table"] = _src0["table"]
        tables.append(_ent0)
    else:
        tf = parse_table_factor(first)
        if not tf:
            # عامل واحد غير مفهوم — لا تُسقط الاستيراد كله: سجّل وحذّر وتابع بلا جداول
            warnings.append(f"تعذر تحليل جدول FROM: {first[:80]} — تُستورد الأعمدة كنصوص بلا حقول")
            return tables, join_ons, warnings
        tf["derived"] = False
        tables.append(tf)
    idx = 1
    while idx < len(chunks):
        kw = chunks[idx]
        kind = re.sub(r"\s+", " ", kw.strip().upper())
        factor_on = chunks[idx + 1] if idx + 1 < len(chunks) else ""
        idx += 2
        # split factor vs ON at depth 0
        m = re.search(r"\bON\b", factor_on, re.IGNORECASE)
        on_text, factor_text = "", factor_on
        if m:
            # find depth-0 ON
            depth, q, p = 0, None, 0
            hit = -1
            while p < len(factor_on):
                ch = factor_on[p]
                if q:
                    if ch == q:
                        q = None
                    p += 1
                    continue
                if ch in ("'", '"', "`"):
                    q = ch
                    p += 1
                    continue
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth = max(0, depth - 1)
                if depth == 0:
                    mm = re.match(r"ON\b", factor_on[p:], re.IGNORECASE)
                    if mm:
                        hit = p
                        break
                p += 1
            if hit >= 0:
                factor_text, on_text = factor_on[:hit], factor_on[hit + 2:]
            else:
                on_text = ""
        factor_text = factor_text.strip()
        if factor_text.startswith("("):
            # JOIN على استعلام فرعي: حلّ المصدر (alias → جدول حقيقي) بدل التجاهل
            _jinner = _balanced_outer(re.sub(r"\s+[Aa][Ss]\s+[A-Za-z_][\w$#]*\s*$", "", factor_text.strip()))
            _jtail = ""
            _jm = re.search(r"\)\s*(?:[Aa][Ss]\s+)?([A-Za-z_][\w$#]*)\s*$", factor_text.strip())
            if _jm:
                _jtail = _jm.group(1)
            _jsrc = _subquery_source(_jinner) if _jinner else None
            if _jinner is not None and _jsrc:
                tf = {"schema": "", "table": "", "alias": _jtail or "sub", "derived": True,
                      "sub_sql": _jinner, "source_schema": _jsrc["schema"],
                      "source_table": _jsrc["table"]}
                tables.append(tf)
                if kind == "CROSS JOIN":
                    continue
                if on_text.strip():
                    join_ons.append({"table": _jsrc["table"], "alias": tf["alias"], "using": "",
                                     "on": on_text.strip(), "kind": kind})
                else:
                    warnings.append(f"JOIN بلا ON ({tf['alias']} ← {_jsrc['table']}) — يُتجاهل في الروابط")
                continue
            warnings.append(f"JOIN على استعلام فرعي — مراجعه تُمرر خاماً (سطّحه لجداول حقيقية عند الحاجة): {factor_text[:60]}")
            continue
        # USING (...) → equality on same-named cols (needs both sides; keep as warning + skip)
        mu = re.match(r"^(.*?)\bUSING\b\s*\(([^)]+)\)\s*$", factor_text + " " + on_text, re.IGNORECASE | re.DOTALL)
        tf = parse_table_factor(re.sub(r"\bUSING\b.*$", "", factor_text, flags=re.IGNORECASE).strip() or factor_text)
        if not tf:
            warnings.append(f"تعذر تحليل جدول في JOIN: {factor_text[:60]} — يُتجاهل مع روابطه")
            continue
        tf["derived"] = False
        tables.append(tf)
        is_natural = "NATURAL" in kind
        is_cross = kind == "CROSS JOIN"
        if is_cross:
            # ضرب ديكارتي بلا شرط — يُستورد الجدول ولا رابط له (بطبيعة CROSS)
            continue
        if is_natural:
            # NATURAL: مساواة تلقائية على الأعمدة المشتركة (تُحسم عند توفر introspection، وإلا تحذير)
            join_ons.append({"table": tf["table"], "alias": tf["alias"], "using": "__natural__",
                             "on": "", "kind": kind})
            continue
        if mu and mu.group(2).strip():
            join_ons.append({"table": tf["table"], "alias": tf["alias"], "using": mu.group(2).strip(),
                             "on": "", "kind": kind})
        elif on_text.strip():
            join_ons.append({"table": tf["table"], "alias": tf["alias"], "using": "",
                             "on": on_text.strip(), "kind": kind})
        else:
            warnings.append(f"JOIN بلا ON ({tf['table']}) — يُتجاهل في الروابط")
    return tables, join_ons, warnings


# ── expression → [field] refs ────────────────────────────────────────────────

def _rewrite_refs(expr: str, field_of: Callable[[str, str], Optional[str]],
                  warn: List[str]) -> str:
    """Replace table.col / col with [..] refs via field_of(table_or_None, col).

    field_of returns the bracket body (e.g. 'col' or 'table.col') or None to
    leave the reference untouched (literals/keywords already excluded upstream).
    """

    def _rep(m: re.Match) -> str:
        full = m.group(0)
        dots = [p.strip() for p in full.split(".")]
        dots = [unquote_ident(p) for p in dots]
        if len(dots) == 1 and (dots[0].upper() in KEYWORDS or dots[0].upper() in TYPES):
            return full
        if len(dots) == 1 and dots[0].upper() in _func_names:
            return full
        if len(dots) == 1:
            body = field_of(None, dots[0])
        elif len(dots) == 2:
            body = field_of(dots[0], dots[1])
        else:
            body = field_of(dots[-2], dots[-1])
            if body is None:
                warn.append(f"مرجع مؤهل عميق يُترك كما هو: {full[:40]}")
                return full
        if body is None:
            return full
        return f"[{body}]"

    # strings protected by placeholder pass
    lits: List[str] = []

    def _stash(m: re.Match) -> str:
        lits.append(m.group(0))
        return "\x00{%d}\x00" % (len(lits) - 1)

    tmp = re.sub(_STR_RE_SRC, _stash, expr)
    # function names (word followed by '(') are never column refs — but their
    # ARGUMENTS still are, so only the name itself is skipped here.
    _func_names = {m.group(1).upper()
                   for m in re.finditer(r"\b([A-Za-z_][\w$#]*)\s*\(", tmp)}
    tmp = re.sub(r"\b([A-Za-z_][\w$#]*\s*\.\s*)*(?:[A-Za-z_][\w$#]*|\"[^\"]+\"|`[^`]+`|\[[^\]]+\])", _rep, tmp)
    tmp = re.sub(r"\b([A-Za-z_][\w$#]*\s*\.\s*)*(?:[A-Za-z_][\w$#]*|\"[^\"]+\"|`[^`]+`|\[[^\]]+\])", _rep, tmp)

    def _unstash(m: re.Match) -> str:
        return lits[int(m.group(1))]

    return re.sub(r"\x00\{(\d+)\}\x00", _unstash, tmp)


# ── main entry ───────────────────────────────────────────────────────────────

def _pretty_alias(col: str) -> str:
    c = unquote_ident(col)
    c = re.sub(r"_+", " ", c).strip()
    return c[:1].upper() + c[1:] if c else col


def _infer_col_type(expr: str) -> str:
    up = expr.upper()
    if re.search(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", up):
        return "aggregated"
    if COMPUTED_HINT.search(expr):
        return "computed"
    return "direct"


def _infer_field_type(expr: str) -> str:
    up = expr.strip().upper()
    if re.fullmatch(r"-?\d+(\.\d+)?", expr.strip()):
        return "NUMERIC"
    if re.search(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", up):
        return "NUMERIC"
    if re.search(r"\b(TO_DATE|TO_TIMESTAMP|DATE_TRUNC|TRUNC|CURRENT_DATE|SYSDATE|GETDATE|NOW|CAST\s*\([^)]*\bAS\s+DATE)", up):
        return "DATE"
    if re.match(r"'.*'$", expr.strip(), re.DOTALL):
        return "VARCHAR"
    return "VARCHAR"


def _split_batches(sql: str) -> Tuple[str, bool]:
    """Split T-SQL GO batches — parse the first non-empty one (warn if more)."""
    parts = [p for p in re.split(r"(?im)^\s*GO\s*$", sql or "") if p.strip()]
    if not parts:
        return "", False
    return parts[0], len(parts) > 1


def _strip_with(sql: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Strip a leading WITH cte AS (...), ... prefix → (rest, [{alias}]).

    CTE bodies are NOT parsed for columns here; their aliases are registered as
    derived sources so outer refs pass through silently (one warning total).
    """
    m = re.match(r"\s*WITH\b", sql, re.IGNORECASE)
    if not m:
        return sql, []
    i, n = m.end(), len(sql)
    ctes: List[Dict[str, Any]] = []
    while True:
        while i < n and sql[i] in (" ", "\t", "\r", "\n", ","):
            i += 1
        m2 = re.match(r"([A-Za-z_][\w$#]*)\s*(?:\([^()]*\))?\s+[Aa][Ss]\s*\(", sql[i:])
        if not m2:
            break
        name = m2.group(1)
        j = i + m2.end() - 1
        depth, q, k = 0, None, j
        while k < n:
            ch = sql[k]
            if q:
                if ch == q:
                    q = None
                k += 1
                continue
            if ch in ("'", '"'):
                q = ch
                k += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if depth != 0:
            break
        ctes.append({"alias": name})
        i = k + 1
        # more CTEs? need a comma ahead (before the outer SELECT)
        rest_ahead = sql[i:]
        mc = re.match(r"\s*,", rest_ahead)
        if mc:
            i += mc.end()
            continue
        break
    return sql[i:], ctes


def parse_sql(sql: str, conn_id: str = "", default_schema: str = "",
              expand_star: Optional[Callable[[str, str], List[Dict[str, str]]]] = None,
              describe: Optional[Callable[[str, str], List[str]]] = None,
              fk_of: Optional[Callable[[str, str], List[Dict[str, str]]]] = None
              ) -> Dict[str, Any]:
    """Parse SELECT → wizard structures.

    expand_star(schema, table) -> [{name, type}] used for * / t.* (optional;
    without it star items are skipped with a warning).
    Every real FROM table also contributes ALL its columns as fields (via
    expand_star/describe) — referenced or not.
    fk_of(schema, table) -> [{column, ref_table, ref_column}] used to derive
    join links from DB constraints for table pairs without explicit ON.
    Returns {tables, fields, columns, links, general_where, distinct, warnings}.
    """
    warnings: List[str] = []
    cleaned = strip_comments(sql or "")
    if not cleaned.strip():
        raise ValueError("استعلام فارغ")
    cleaned, multi_batch = _split_batches(cleaned)
    if multi_batch:
        warnings.append("عدة دفعات GO — يُحلل الأول فقط")
    if not cleaned.strip():
        raise ValueError("استعلام فارغ")
    cleaned, ctes = _strip_with(cleaned)
    if ctes:
        warnings.append("CTE ‏(%s) — مراجعها تُمرر خاماً (سطّحها لجداول حقيقية عند الحاجة)" % "، ".join(c["alias"] for c in ctes))
    if re.match(r"^\s*\(", cleaned):
        raise ValueError("يبدأ بقوس — الصق SELECT مباشرة بلا تغليف")
    clauses = split_clauses(cleaned)
    tables, join_ons, w0 = parse_from(clauses.get("from", ""))
    warnings.extend(w0)
    for _cte in ctes:
        if _cte.get("alias") and not any((t.get("alias") or "").upper() == _cte["alias"].upper() for t in tables):
            tables.append({"schema": "", "table": "", "alias": _cte["alias"], "derived": True, "cte": True})

    # alias map: alias-or-name (upper) -> table entry
    alias_map: Dict[str, Dict[str, Any]] = {}
    for t in tables:
        if t.get("derived"):
            alias_map[(t.get("alias") or "").upper()] = t
        else:
            if t.get("table"):
                alias_map[t["table"].upper()] = t
            if t.get("alias"):
                alias_map[t["alias"].upper()] = t

    fields: List[Dict[str, Any]] = []
    field_keys = set()

    def add_field(col: str, table_entry: Optional[Dict[str, Any]], ftype: str = "VARCHAR"):
        key = ((table_entry or {}).get("table") or (table_entry or {}).get("alias") or "?",
               col, str(conn_id))
        if key in field_keys:
            return
        field_keys.add(key)
        fields.append({
            "name": col,
            "type": ftype,
            "conn_id": str(conn_id),
            "table_source": ((table_entry or {}).get("table") or (table_entry or {}).get("alias") or ""),
        })

    def field_body(tbl_or_none: Optional[str], col: str) -> Optional[str]:
        """Bracket body for a reference, registering the field as a side effect."""
        candidates = []
        if tbl_or_none:
            t = alias_map.get(tbl_or_none.upper())
            if t is None:
                # unknown qualifier — leave raw + warn once
                warnings.append(f"جدول غير معروف في المرجع: {tbl_or_none}.{col} — يُترك خامًا")
                alias_map[tbl_or_none.upper()] = {"__unknown": True}
                return None
            if t.get("__unknown"):
                return None
            if t.get("derived") and not t.get("source_table"):
                # subquery/CTE source — covered by the single factor warning; pass raw silently
                return None
            candidates = [t]
        else:
            holders = [t for t in tables
                       if not t.get("derived") and not t.get("__unknown")]
            if not re.fullmatch(r"[A-Za-z_][\w$#]*", col or ""):
                # non-Latin bare word (e.g. Arabic): brackets can't carry it —
                # keep raw SQL (works if the DB really has it) + warn once
                warnings.append(f"اسم غير لاتيني بلا تأهيل: {col} — يُترك خامًا (المحرك لا يقبل [ ] فيها مسافات/عربي)")
                return None
            if len(holders) == 1:
                candidates = holders
            elif any(t.get("derived") for t in tables):
                # derived sources present — silence (their factor warning covers it)
                return None
            else:
                # ambiguous bare col with several tables → keep raw + warn
                warnings.append(f"مرجع مجرد ملتبس مع عدة جداول: {col} — يُترك خامًا")
                return None
        t = candidates[0]
        real_table = _entry_table(t) or t.get("alias") or ""
        if not real_table:
            # derived subquery output
            add_field(col, {"table": t.get("alias") or "", "alias": t.get("alias") or ""}, "VARCHAR")
            return col
        _rsch = _entry_schema(t)
        if t.get("derived") and t.get("source_table") and _rsch:
            _tsrc = f"{_rsch}.{real_table}"
            add_field(col, {"table": _tsrc, "alias": _tsrc}, "VARCHAR")
        else:
            add_field(col, t, "VARCHAR")
        return f"{real_table}.{col}"

    raw_items = [s for s in split_top_commas(clauses.get("select", "")) if s.strip()]
    if not raw_items:
        raise ValueError("لا عناصر في SELECT")
    columns: List[Dict[str, Any]] = []
    auto_n = 0

    for item in raw_items:
        expr_raw, alias_raw = parse_select_item(item)
        expr_s = expr_raw.strip()
        # star handling
        if expr_s == "*" or re.fullmatch(r"[A-Za-z_][\w$#]*\.\*", expr_s):
            star_t = None
            if expr_s != "*":
                pre = expr_s[:-2]
                star_t = alias_map.get(pre.upper())
            if expr_s == "*":
                targets = [t for t in tables
                           if (not t.get("derived") or t.get("source_table")) and not t.get("__unknown")]
            else:
                if not star_t:
                    warnings.append(f"جدول غير معروف في {expr_s} — يُتجاهل")
                    continue
                targets = [star_t]
            if expr_s == "*":
                targets = [t for t in tables
                           if (not t.get("derived") or t.get("source_table")) and not t.get("__unknown")]
            expanded = 0
            for t in targets:
                _et = _entry_table(t)
                _es = _entry_schema(t, default_schema)
                cols = expand_star(_es, _et) if expand_star and _et else []
                if not cols:
                    warnings.append(f"تعذر توسيع {'*' if expr_s == '*' else expr_s} (لا اتصال مُمرر) — أضف الأعمدة يدويًا")
                    continue
                for c in cols:
                    cn, ct = c.get("name"), (c.get("type") or "VARCHAR")
                    if t.get("derived") and t.get("source_table") and _es:
                        _ts2 = f"{_es}.{_et}"
                        add_field(cn, {"table": _ts2, "alias": _ts2}, ct)
                    else:
                        add_field(cn, t, ct)
                    auto_n += 1
                    columns.append({"name": f"col_{cn}", "alias": cn,
                                    "expr": f"[{_et}.{cn}]",
                                    "dataType": ct, "col_type": "direct"})
                    expanded += 1
            continue
        refs = extract_col_refs(expr_s)
        is_literal_only = (not refs) and (is_string_lit(expr_s) or re.fullmatch(r"-?\d+(\.\d+)?", expr_s) is not None)
        if is_literal_only:
            auto_n += 1
            columns.append({"name": f"col_{auto_n}", "alias": alias_raw or expr_s.strip("'")[:40],
                            "expr": expr_s,
                            "dataType": "NUMERIC" if re.fullmatch(r"-?\d+(\.\d+)?", expr_s.strip()) else "VARCHAR",
                            "col_type": "computed"})
            continue
        # register plain columns as fields (typed guess), rewrite expr to [..]
        for r in refs:
            parts = split_dotted(r)
            if len(parts) == 1:
                # bare: single-table shortcut handled inside field_body (may warn)
                field_body(None, parts[0])
            elif len(parts) >= 2:
                field_body(parts[-2], parts[-1])
        expr_rw = _rewrite_refs(expr_s, field_body, warnings)
        alias = alias_raw or (refs[-1].split(".")[-1] if refs else f"col_{len(columns) + 1}")
        alias = unquote_ident(alias)
        ctype = _infer_col_type(expr_s)
        # refine field types from expression context
        if ctype == "aggregated":
            for r in refs:
                _bump_field_type(fields, r, "NUMERIC")
        columns.append({"name": f"col_{unquote_ident(refs[-1].split('.')[-1]) if refs else (len(columns) + 1)}",
                        "alias": alias, "expr": expr_rw,
                        "dataType": _infer_field_type(expr_s), "col_type": ctype})

    # WHERE → general_where
    general_where = ""
    if clauses.get("where"):
        general_where = _rewrite_refs(clauses["where"], field_body, warnings)

    # Every real FROM table contributes ALL its columns as fields (referenced or not)
    if expand_star is not None or describe is not None:
        for t in tables:
            if t.get("__unknown"):
                continue
            _rt0 = _entry_table(t)
            if not _rt0 or (t.get("derived") and not t.get("source_table")):
                continue
            sch = _entry_schema(t, default_schema)
            _allcols: List[Dict[str, str]] = []
            try:
                if expand_star is not None:
                    _allcols = expand_star(sch, _rt0) or []
                if not _allcols and describe is not None:
                    _allcols = [{"name": _n, "type": "VARCHAR"}
                                for _n in (describe(sch, _rt0) or [])]
            except Exception:
                _allcols = []
            if not _allcols:
                warnings.append(f"تعذر جلب أعمدة الجدول {_rt0} — أُضيفت المراجع المذكورة فقط")
                continue
            _k0 = len(field_keys)
            _ft0 = t
            if t.get("derived") and t.get("source_table") and sch:
                _ts0 = f"{sch}.{_rt0}"
                _ft0 = {"table": _ts0, "alias": _ts0}
            for c in _allcols:
                if c.get("name"):
                    add_field(c.get("name"), _ft0, c.get("type") or "VARCHAR")
            _added = len(field_keys) - _k0
            if _added:
                warnings.append(f"أُضيفت كل أعمدة {_rt0}: {len(_allcols)} عمود ({_added} جديد)")

    # JOIN ON equalities → links
    links: List[Dict[str, Any]] = []
    for j in join_ons:
        jkind = j.get("kind") or "INNER JOIN"
        if j.get("using") == "__natural__":
            main_t0 = (tables[0].get("table") or tables[0].get("alias")) if tables else ""
            other0, oalias0 = j.get("table") or "", j.get("alias") or ""
            shared0: List[str] = []
            if describe is not None and main_t0 and other0:
                try:
                    _msch = next((t.get("schema") or "" for t in tables
                                  if (t.get("table") or t.get("alias")) == main_t0), "")
                    _osch = next((t.get("schema") or "" for t in tables
                                  if (t.get("table") or t.get("alias")) == (other0 or oalias0)), "")
                    mcols = {c.lower() for c in (describe(_msch or default_schema or "", main_t0) or [])}
                    _omap: Dict[str, str] = {}
                    for _c in (describe(_osch or default_schema or "", other0) or []):
                        _omap[_c.lower()] = _c
                    shared0 = [_omap.get(c, c) for c in sorted(mcols & set(_omap.keys()))]
                except Exception as ex:
                    warnings.append(f"تعذر اشتقاق NATURAL ({other0}): {str(ex)[:80]}")
            if not shared0:
                warnings.append(f"NATURAL JOIN مع {other0 or '?'} بلا أعمدة مشتركة مكتشفة — حدد ON صراحة")
                continue
            for cu in shared0:
                links.append({"from_table": main_t0, "from_col": cu,
                              "to_table": other0, "to_col": cu,
                              "from_conn": str(conn_id), "to_conn": str(conn_id),
                              "rel_type": "one_to_one", "join_kind": jkind})
            continue
        one = j.get("on") or ""
        jkind = j.get("kind") or "INNER JOIN"
        if j.get("using"):
            cols_u = [c.strip() for c in j["using"].split(",") if c.strip()]
            # USING needs both sides: main table = first table
            main_t = _entry_table(tables[0]) if tables else ""
            for cu in cols_u:
                links.append({"from_table": main_t, "from_col": unquote_ident(cu),
                              "to_table": j.get("table") or "", "to_col": unquote_ident(cu),
                              "from_conn": str(conn_id), "to_conn": str(conn_id),
                              "rel_type": "one_to_one", "join_kind": jkind})
            continue
        for part in re.split(r"\bAND\b", one, flags=re.IGNORECASE):
            m = re.match(r"^\s*(.+?)\s*=\s*(.+?)\s*$", part.strip(), re.DOTALL)
            if not m:
                if part.strip():
                    warnings.append(f"شرط JOIN غير مساواة يُتجاهل في الروابط: {part.strip()[:60]}")
                continue
            l, r_ = m.group(1).strip(), m.group(2).strip()
            # skip literal sides
            if is_string_lit(l) or is_string_lit(r_) or re.fullmatch(r"-?\d+(\.\d+)?", l) or re.fullmatch(r"-?\d+(\.\d+)?", r_):
                continue
            lr, rr = split_dotted(l), split_dotted(r_)
            if len(lr) < 2 or len(rr) < 2:
                warnings.append(f"رابط بأعمدة مجردة يُتجاهل (حدد الجدول): {part.strip()[:60]}")
                continue
            lt = (alias_map.get(lr[-2].upper()) or {})
            rt = (alias_map.get(rr[-2].upper()) or {})
            links.append({"from_table": _entry_table(lt) or lr[-2], "from_col": lr[-1],
                          "to_table": _entry_table(rt) or rr[-2], "to_col": rr[-1],
                          "from_conn": str(conn_id), "to_conn": str(conn_id),
                          "rel_type": "one_to_one", "join_kind": jkind})

    # FK pass: table pairs without an explicit link inherit joins from DB constraints
    if fk_of is not None:
        def _tnorm(_t):
            return str(_t or "").split(".")[-1].strip().upper()

        _pair_have = set()
        for l in links:
            a, b = _tnorm(l.get("from_table")), _tnorm(l.get("to_table"))
            if a and b:
                _pair_have.add(frozenset((a, b)))
        _real = [t for t in tables
                 if (not t.get("derived") or t.get("source_table")) and not t.get("__unknown")
                 and _entry_table(t)]
        _fk_n = 0
        for _i, _ta in enumerate(_real):
            for _tb in _real[_i + 1:]:
                _na, _nb = _tnorm(_entry_table(_ta)), _tnorm(_entry_table(_tb))
                if not _na or not _nb or _na == _nb:
                    continue
                if frozenset((_na, _nb)) in _pair_have:
                    continue
                for _src, _dst in ((_ta, _tb), (_tb, _ta)):
                    try:
                        _fks = fk_of(_entry_schema(_src, default_schema), _entry_table(_src)) or []
                    except Exception:
                        _fks = []
                    for _f in _fks:
                        if _tnorm((_f or {}).get("ref_table")) != _tnorm(_entry_table(_dst)):
                            continue
                        _fc, _rc = str((_f or {}).get("column") or ""), str((_f or {}).get("ref_column") or "")
                        if not _fc or not _rc:
                            continue
                        links.append({"from_table": _entry_table(_src), "from_col": _fc,
                                      "to_table": _entry_table(_dst), "to_col": _rc,
                                      "from_conn": str(conn_id), "to_conn": str(conn_id),
                                      "rel_type": "many_to_one", "join_kind": "INNER JOIN",
                                      "fk_auto": True, "verified_fk": True})
                        _fk_n += 1
                _pair_have.add(frozenset((_na, _nb)))
        if _fk_n:
            warnings.append(f"اشتُقت {_fk_n} روابط من قيود قاعدة البيانات (FK)")

    # leftovers → warnings
    for k, label in (("group_by", "GROUP BY"), ("having", "HAVING"), ("order_by", "ORDER BY"),
                     ("limit", "LIMIT/OFFSET")):
        if clauses.get(k):
            warnings.append(f"{label} يُتجاهل في الاستيراد (الترقيم والفرز من المشغل): {clauses[k][:60]}")
    for t in tables:
        if t.get("derived") and not t.get("source_table"):
            warnings.append(f"جدول فرعي ({t.get('alias')}) — حقوله تُستورد كأعمدة محسوبة بلا مصدر")
        elif t.get("derived") and t.get("source_table"):
            warnings.append(f"المستعار {t.get('alias')} ← {_entry_schema(t)}.{_entry_table(t)} (حُل من الاستعلام الفرعي)")

    # fix field types for direct-qualified refs already added as VARCHAR (keep simple)
    return {"tables": [{"schema": _entry_schema(t), "table": _entry_table(t),
                        "alias": t.get("alias") or "", "derived": bool(t.get("derived")),
                        "source_table": t.get("source_table") or "", "source_schema": t.get("source_schema") or ""}
                       for t in tables],
            "fields": fields, "columns": columns, "links": links,
            "general_where": general_where,
            "distinct": bool(clauses.get("distinct")),
            "warnings": warnings}


def _bump_field_type(fields: List[Dict[str, Any]], ref: str, ftype: str):
    base = ref.split(".")[-1]
    for f in fields:
        if f.get("name") == base and f.get("type") == "VARCHAR":
            f["type"] = ftype
