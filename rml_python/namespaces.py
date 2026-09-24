"""
Namespace registry + cross-file reference resolution for RML/CML.

A file declares addressability via <..._metadata namespace="hrRules">.
Expressions may then reference:
  - [field_name]        → validated DB field from the same file's <fields>
  - ns.rule_or_column   → computed fragment from the namespaced file

Resolution is single-attr canonical: writers emit only `connection_id`
(columns) / `conn_id` (fields); readers still accept legacy variants.
"""
from __future__ import annotations
import pathlib
import re
from typing import Dict, List, Optional, Tuple, Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

FIELD_REF_RE = re.compile(r"\[([A-Za-z0-9_][A-Za-z0-9_.]*)\]|\{([A-Za-z0-9_][A-Za-z0-9_.]*)\}")
NS_REF_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")

# Bare XSQL-style refs: `table.field` (no brackets).
# Word runs may contain spaces (Arabic field names); hard delimiters are
# , ; ( ) = < > ! and arithmetic ops — field names never contain those.
# NOTE: 3-part `conn.table.field` is intentionally NOT matched here: the file
# converter always emits 2-part `table.field` (table+field fully identify the
# field; a leading segment is a schema name or a typo and must stay literal,
# exactly like the emitter below treats it).
_BARE2_RE = re.compile(
    r"(?<![\w$#\"'\u0600-\u06FF.\]])"
    r"([A-Za-z_][A-Za-z0-9_$#]*)"
    r"\.([A-Za-z_0-9\u0600-\u06FF]+(?:[ \t]+[A-Za-z_0-9\u0600-\u06FF]+)*)"
)


def _bare_longest_field(words_text: str, table_head: str, by_name: Dict[str, List[Any]]) -> Optional[Tuple[Any, str, str]]:
    """Longest leading field-name match of `words_text` inside `table_head`.

    Returns (field_obj, canonical_key, dropped_remainder) or None.
    `*` and empty candidates never match (SELECT t.* stays literal).
    """
    toks = str(words_text or "").split()
    for i in range(len(toks), 0, -1):
        cand = " ".join(toks[:i])
        if not cand or cand == "*":
            continue
        cands = by_name.get(cand.strip().lower(), [])
        for f in cands:
            if _norm_tname(getattr(f, "table_source", None) or "") == _norm_tname(table_head):
                key = str(getattr(f, "name", cand)).strip().lower()
                return f, key, " ".join(toks[i:])
    return None


def bare_ref_tables(text: str, fields: Optional[List[Any]]) -> Dict[str, str]:
    """{table_norm: field_key} for bare `table.field` refs in `text`.

    Shared by the execution planner so routing (JOIN vs merge) sees the same
    references the SQL emitter resolves. Single-quoted literals, double-quoted
    spans and [...]/{...} spans are ignored. Unknown tables/fields are
    ignored (literal SQL passthrough, same as the emitter).
    """
    out: Dict[str, str] = {}
    if not text or not fields:
        return out
    try:
        by_name: Dict[str, List[Any]] = {}
        for f in (fields or []):
            n = getattr(f, "name", None)
            if n:
                by_name.setdefault(str(n).strip().lower(), []).append(f)
        segs = re.split(r"('(?:[^']|'')*')", str(text or ""))
        for qi in range(0, len(segs), 2):
            seg = segs[qi]
            for part in re.split(r'(\{[^{}]*\}|\[[^\]]*\]|"[^"]*")', seg)[0::2]:
                for m in _BARE2_RE.finditer(part):
                    head, words = m.group(1), m.group(2)
                    hit = _bare_longest_field(words, head, by_name)
                    if hit is None:
                        continue
                    f, key, _dropped = hit
                    out.setdefault(_norm_tname(getattr(f, "table_source", None) or head), key)
    except Exception:
        pass
    return out


def _candidate_dirs() -> List[pathlib.Path]:
    roots = []
    for base in [REPO_ROOT / "odex" / "system", REPO_ROOT / "system"]:
        if base.exists():
            roots.append(base)
    return roots


def build_registry() -> Dict[str, Tuple[str, pathlib.Path]]:
    """Scan RML + CML files for metadata namespace attributes.

    Returns {namespace_lower: (kind, path)} where kind in ("rml", "cml").
    First match wins; files without namespace are skipped.
    """
    registry: Dict[str, Tuple[str, pathlib.Path]] = {}

    def _metadata_ns(path: pathlib.Path, kind: str) -> Optional[str]:
        try:
            from xml.etree import ElementTree as ET
            root = ET.fromstring(path.read_text(encoding="utf-8").lstrip("\ufeff"))
            for el in root.iter():
                if el.tag.lower() in ("rpt_metadata", "cml_metadata", "fml_metadata"):
                    for k, v in el.attrib.items():
                        if k.lower() in ("namespace", "ns") and v and v.strip():
                            return v.strip()
        except Exception:
            pass
        return None

    for base in _candidate_dirs():
        for path in sorted(base.glob("*.rml")) + sorted(base.glob("*/*.rml")):
            ns = _metadata_ns(path, "rml")
            if ns and ns.lower() not in registry:
                registry[ns.lower()] = ("rml", path)
        for path in sorted(base.glob("*.cml")) + sorted(base.glob("*/*.cml")):
            ns = _metadata_ns(path, "cml")
            if ns and ns.lower() not in registry:
                registry[ns.lower()] = ("cml", path)
    # rml engine examples fallback
    ex_dir = REPO_ROOT / "rml_python" / "examples"
    if ex_dir.exists():
        for path in sorted(ex_dir.glob("*.rml")):
            ns = _metadata_ns(path, "rml")
            if ns and ns.lower() not in registry:
                registry[ns.lower()] = ("rml", path)
    return registry


def _norm_tname(t: str) -> str:
    """Upper-case table name without schema prefix/quotes (for compare)."""
    s = str(t or "").strip().replace('"', "")
    if "." in s:
        s = s.split(".")[-1]
    return s.upper()


def _quote_literal(value: str) -> str:
    v = str(value).strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", v):
        return v
    if v.lower() in ("true", "false", "null"):
        return v.upper()
    return "'" + v.replace("'", "''") + "'"


def _field_conns(f: Any) -> set:
    """All connection tokens stored on a <field> (conn_id / connection_id / ...)."""
    out = set()
    for attr in ("connection_id", "conn_id", "connectionId", "connection"):
        try:
            v = getattr(f, attr, None)
        except Exception:
            v = None
        if v is not None and str(v).strip() != "":
            out.add(str(v).strip())
    return out


def _conn_accepted(tok: str, fconns: set, conn_map: Optional[Dict[str, str]]) -> bool:
    """True when a [conn...] qualifier matches the field's connection.

    Accepts the rml-local number (mapped via conn_map to the global id),
    the global id itself, or the literal stored value.
    """
    tok = str(tok or "").strip()
    if not tok:
        return False
    if tok in fconns:
        return True
    if conn_map:
        try:
            mapped = conn_map.get(tok)
        except Exception:
            mapped = None
        if mapped is not None and str(mapped).strip() in fconns:
            return True
        # reverse: token is global, field stores the local id
        rev = {str(v).strip(): k for k, v in conn_map.items()}
        if tok in rev and str(rev[tok]).strip() in fconns:
            return True
    return False


def _split_top_commas(s: str) -> Optional[List[str]]:
    """Split on top-level commas (paren-aware, quote-aware for ' and ")."""
    parts, depth, cur = [], 0, []
    k = 0
    n = len(s)
    while k < n:
        ch = s[k]
        if ch == "'":
            j = k + 1
            while j < n:
                if s[j] == "'":
                    if j + 1 < n and s[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            cur.append(s[k:j + 1 if j < n else n])
            k = j + 1 if j < n else n
            continue
        if ch == '"':
            j = s.find('"', k + 1)
            if j < 0:
                cur.append(s[k:])
                break
            cur.append(s[k:j + 1])
            k = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        k += 1
    parts.append("".join(cur))
    return parts


_IF_CALL_RE = re.compile(r"(?<![\w$#\.\"'\u0600-\u06FF])IF\s*\(", re.IGNORECASE)


def _transpile_if_calls(text: str, _depth: int = 0) -> str:
    """Rewrite `IF(cond, a, b)` → `CASE WHEN cond THEN a ELSE b END`.

    Operates on the full text with a literal-aware balanced scan so string
    args (N'...') survive intact. Nested IF() resolves across rounds (each
    round removes exactly one IF(). Malformed calls (wrong arity, unbalanced
    parens) are left untouched — same DB error as before this feature.
    """
    if not text or _depth > 8:
        return text
    m = _IF_CALL_RE.search(text)
    if not m:
        return text
    depth, k, n = 1, m.end(), len(text)
    while k < n and depth > 0:
        ch = text[k]
        if ch == "'":
            j = k + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            k = j + 1 if j < n else n
            continue
        if ch == '"':
            j = text.find('"', k + 1)
            k = n if j < 0 else j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        k += 1
    if depth != 0:
        return text
    args = _split_top_commas(text[m.end():k - 1])
    if len(args) != 3 or not args[0].strip():
        return text[:k] + _transpile_if_calls(text[k:], _depth + 1)
    repl = f"CASE WHEN {args[0].strip()} THEN {args[1].strip()} ELSE {args[2].strip()} END"
    return _transpile_if_calls(text[:m.start()] + repl + text[k:], _depth + 1)


def resolve_ns_member(ns: str, member: str, registry: Dict[str, Tuple[str, pathlib.Path]],
                      visited: Optional[set] = None) -> Optional[str]:
    """Resolve `ns.member` to a SQL fragment, or None if not a known namespace.

    - RML: looks for <rule name=member> first, then <column name|alias=member>;
      the referenced expression is resolved recursively (fields of its own file).
    - CML: looks for <rule name=member> with a usable `value` (default/equals);
      substitutes the literal. Rules without values raise ValueError.
    Raises ValueError on unknown member inside a KNOWN namespace or on cycles.
    """
    if visited is None:
        visited = set()
    entry = registry.get(ns.lower())
    if entry is None:
        return None
    kind, path = entry
    key = (str(path), member.lower())
    if key in visited:
        raise ValueError(f"Circular namespace reference: {ns}.{member}")
    visited = visited | {key}

    if kind == "rml":
        from .compiler import RMLReportCompiler
        comp = RMLReportCompiler(path=path)
        try:
            cmap = {str(c.id).strip(): str(c.connection_id).strip()
                    for c in comp.connections()
                    if str(getattr(c, "id", "") or "").strip()}
        except Exception:
            cmap = {}
        # rule first
        for r in comp.rules():
            if r.name.lower() == member.lower():
                if not (r.expr or "").strip():
                    raise ValueError(f"Rule '{member}' in namespace '{ns}' has no expression")
                return _resolve_expression(r.expr, comp.fields(), registry, visited, None, cmap)
        # then computed/display column
        for c in comp.columns():
            if (c.name and c.name.lower() == member.lower()) or (c.alias and c.alias.lower() == member.lower()):
                if not (c.expr or "").strip():
                    raise ValueError(f"Column '{member}' in namespace '{ns}' has no expression")
                return _resolve_expression(c.expr, comp.fields(), registry, visited, None, cmap)
        raise ValueError(f"Unknown member '{member}' in namespace '{ns}' ({path.name})")

    # kind == "cml"
    from cml_engine.compiler import CMLCompiler
    comp = CMLCompiler(path=path)
    for r in comp.rules():
        if r.name.lower() == member.lower():
            if r.value is None or str(r.value).strip() == "":
                raise ValueError(f"CML rule '{member}' in namespace '{ns}' has no value to substitute")
            return _quote_literal(str(r.value))
    raise ValueError(f"Unknown rule '{member}' in namespace '{ns}' ({path.name})")


def _resolve_expression(expr_text: str, fields: Optional[List[Any]],
                        registry: Optional[Dict[str, Tuple[str, pathlib.Path]]] = None,
                        visited: Optional[set] = None,
                        table_map: Optional[Dict[str, str]] = None,
                        conn_map: Optional[Dict[str, str]] = None,
                        alias_by_table: Optional[Dict[str, str]] = None) -> str:
    """Resolve [field] refs (validated) and ns.member refs inside an expression.

    Supported bracket forms (validated against the file's <fields>):
    - `[name]` — field by name (must be defined).
    - `[table.name]` — field `name` whose table_source is `table`
      (when the head is a known namespace, `ns.member` resolution wins).
    - `[conn.table.name]` — additionally checks the field's connection:
      the head accepts the rml-local connection number (mapped through
      conn_map), the global connection id, or the stored value itself.
    - `[ns.member]` resolves via the namespace registry.
    - Bare `ns.member` tokens resolve only when `ns` is a known namespace;
      otherwise left untouched (regular table.column SQL).
    - String literals (single quotes) are never touched.
    - table_map: optional {field_lower: table_alias} — when given, refs are
      emitted qualified as "alias"."COL" (for multi-table/JOIN queries).
    - A leading `=` (Excel-style formula marker) is stripped: `=expr` means
      "this column is an expression". Previously such input errored at the DB.
    """
    if not expr_text:
        return expr_text or ""
    _stripped = str(expr_text).lstrip()
    if _stripped.startswith("=") and not _stripped.startswith("=="):
        expr_text = _stripped[1:]
    # IF(cond, a, b) → CASE WHEN cond THEN a ELSE b END (XSQL-style).
    # Full-text pre-pass (runs before literal splitting because args often
    # contain N'...' literals). Literal-aware balanced scan; unknown arity
    # or unbalanced parens stay literal (DB error, as before this feature).
    expr_text = _transpile_if_calls(expr_text)
    # T-SQL ISNULL(a, b) → COALESCE (works on Postgres + Oracle + SQL Server)
    expr_text = re.sub(r"(?i)\bISNULL\s*\(", "COALESCE(", expr_text)
    # T-SQL CAST targets → portable (DATETIME unknown to Postgres/Oracle)
    expr_text = re.sub(r"(?i)\bAS\s+DATETIME\b", "AS TIMESTAMP", expr_text)
    expr_text = re.sub(r"(?i)\bAS\s+SMALLDATETIME\b", "AS TIMESTAMP", expr_text)
    if registry is None:
        registry = build_registry()
    if visited is None:
        visited = set()
    field_map = {f.name.lower(): f.name for f in (fields or []) if getattr(f, "name", None)}
    alias_map = {k.lower(): v for k, v in (table_map or {}).items()} if table_map else {}
    by_name: Dict[str, List[Any]] = {}
    for f in (fields or []):
        n = getattr(f, "name", None)
        if n:
            by_name.setdefault(str(n).strip().lower(), []).append(f)

    def _emit(key: str, f: Optional[Any] = None) -> str:
        if table_map and key in table_map:
            # Qualified refs ([table.col]/[conn.table.col]) must use the alias of
            # THEIR OWN table — bare-name lookup binds to the wrong table when the
            # column exists in several tables (e.g. RT_BILL_NO in MST and DTL).
            if f is not None and alias_by_table:
                tn = _norm_tname(getattr(f, "table_source", None) or "")
                if tn and tn in alias_by_table:
                    return f'{_q_ident(alias_by_table[tn])}.{_q_ident(field_map[key])}'
            return f'{_q_ident(table_map[key])}.{_q_ident(field_map[key])}'
        return _q_ident(field_map[key])

    def _find_in_table(col: str, table: str) -> Optional[Any]:
        """First field named `col` whose table_source matches `table` (None if none).

        Fallback مرايا IoT: [att.col] تُقبل على جدول المرآة iot_<engine>_att
        (تقارير قديمة كُتبت قبل المرايا).
        """
        tnorm = _norm_tname(table)
        cands = by_name.get(str(col).strip().lower(), [])
        for f in cands:
            if _norm_tname(getattr(f, "table_source", None) or "") == tnorm:
                return f
        if tnorm in ("ATT", "USERS", "ATTENDANCE"):
            for f in cands:
                ts = _norm_tname(getattr(f, "table_source", None) or "")
                if ts.startswith("IOT_") and ts.endswith("_" + tnorm):
                    return f
        return None

    def _q_ident(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def _qualify_bare(seg: str) -> str:
        """Qualify bare/quoted field tokens when table_map is given.

        Skips tokens already qualified (dot-adjacent) and SQL keywords
        (they never match a field name).
        """
        if not alias_map:
            return seg

        def _rep_quoted(m: re.Match) -> str:
            name = m.group(1)
            key = name.lower()
            if key in field_map and key in alias_map:
                return f'{_q_ident(alias_map[key])}.{_q_ident(field_map[key])}'
            return m.group(0)

        seg = re.sub(r'(?<!\.)"([A-Za-z_][A-Za-z0-9_]*)"(?!\.)', _rep_quoted, seg)
        names = sorted(field_map.keys(), key=len, reverse=True)
        if not names:
            return seg
        alt = "|".join(re.escape(n) for n in names)

        def _rep_bare(m: re.Match) -> str:
            key = m.group(1).lower()
            if key in alias_map:
                return f'{_q_ident(alias_map[key])}.{_q_ident(field_map[key])}'
            return m.group(1)

        # Apply bare-word matching only OUTSIDE double-quoted spans
        # (so "T0"."COL" is never re-wrapped).
        qparts = re.split(r'("[^"]*")', seg)
        for qi in range(0, len(qparts), 2):
            qparts[qi] = re.sub(r"(?<!\.)\b(" + alt + r")\b(?!\.)", _rep_bare, qparts[qi], flags=re.IGNORECASE)
        return "".join(qparts)

    # split out single-quoted literals so we never rewrite inside them
    parts = re.split(r"('(?:[^']|'')*')", expr_text)
    for i in range(0, len(parts), 2):
        seg = parts[i]
        # get(...) transparent wrapper (XSQL-style field access).
        # A single bare name is re-bracketed (`get(col)` → `[col]`) so it
        # keeps the exact validated/quoted semantics of `[col]`; dotted or
        # bracketed inners splice as-is (`get(tbl.col)` → `tbl.col`,
        # `get([tbl.col])` → `[tbl.col]`). Not preceded by word/dot chars
        # (so `target.get(x)` and `budget(` stay literal). Nested get()
        # unwraps inside-out (cap 4 rounds). Args with parens (get(SUM(x)))
        # or quotes are left untouched — same passthrough as before.
        def _get_rep(_m: re.Match) -> str:
            _inner = (_m.group(1) or "").strip()
            if not _inner:
                return _m.group(0)
            if _inner[0] in ("'", '"'):
                return _m.group(0)
            if re.search(r"[\[\]{}()]", _inner):
                return _inner
            # single name (Latin or Arabic, may contain spaces) → bracket it
            # so validation/quoting match [name] exactly; dotted stays bare
            # for the table-aware passes below.
            if "." not in _inner and re.fullmatch(
                    r"[A-Za-z_0-9\u0600-\u06FF][A-Za-z_0-9\u0600-\u06FF \t]*", _inner):
                return f"[{_inner}]"
            return _inner

        for _gi in range(4):
            _nseg, _nn = re.subn(
                r"(?<![\w$#\.\"'\u0600-\u06FF])get\s*\(([^()]*)\)",
                _get_rep,
                seg, flags=re.IGNORECASE)
            if not _nn:
                break
            seg = _nseg
        # T-SQL leftovers: alias.[col] / alias."col" where alias is not a known
        # table — bind when col matches exactly one report field, else a clear error.
        _known_tables = {_norm_tname(getattr(f, "table_source", None) or "")
                         for f in (fields or [])}
        try:
            _known_tables |= {str(_v or "").strip().lower() for _v in (table_map or {}).values()}
            _known_tables |= {_norm_tname(_k) for _k in (alias_by_table or {})}
        except Exception:
            pass

        def _qsub(m: re.Match) -> str:
            qual, col = m.group(1), m.group(2).strip()
            if _norm_tname(qual) in _known_tables or qual.strip().lower() in _known_tables:
                return m.group(0)  # known table — handled by the passes below
            cands = by_name.get(col.strip().lower(), [])
            if len(cands) == 1:
                return _emit(str(getattr(cands[0], "name", col)).strip().lower(), cands[0])
            if not cands:
                raise ValueError(
                    f"المرجع '{qual}.[{col}]' يشير لجدول/مستعار غير معروف '{qual}' "
                    f"ولا يوجد حقل باسم '{col}' — استورده من تبويب الحقول أو صحح العمود")
            actual = sorted({_norm_tname(getattr(x, "table_source", None) or "") or "؟" for x in cands})
            raise ValueError(
                f"المرجع '{qual}.[{col}]' ملتبس — '{col}' موجود في ({'، '.join(actual)}): "
                f"استخدم [الجدول.{col}] صراحة")
        seg = re.sub(r"([A-Za-z_][A-Za-z0-9_$#]*)\.\[([^\]]+)\]", _qsub, seg)
        seg = re.sub(r'([A-Za-z_][A-Za-z0-9_$#]*)\."([^"]+)"', _qsub, seg)
        # T-SQL/Arabic leftovers the Latin-only FIELD_REF_RE cannot see:
        # ["T"."C"] / [T."C"] → [T.C], collapse [[..]], then resolve any
        # bracket holding quotes or non-Latin (pure [1] subscripts untouched).
        seg = re.sub(r'\["([^"]+)"\."([^"]+)"\]', r'[\1.\2]', seg)
        seg = re.sub(r'\[([A-Za-z_][\w$#]*)\."([^"]+)"\]', r'[\1.\2]', seg)
        seg = re.sub(r'\{"([^"]+)"\."([^"]+)"\}', r'{\1.\2}', seg)
        seg = re.sub(r'\{([A-Za-z_][\w$#]*)\."([^"]+)"\}', r'{\1.\2}', seg)
        while '[[' in seg:
            seg = seg.replace('[[', '[')
        while ']]' in seg:
            seg = seg.replace(']]', ']')

        def _left_do(inner, _o, _c):
            inner = inner.strip()
            pts = [p.strip().strip('"') for p in inner.split(".")]
            if len(pts) == 1:
                key = pts[0].lower()
                cands = by_name.get(key, [])
                if len(cands) == 1:
                    return _emit(str(getattr(cands[0], "name", pts[0])).strip().lower(), cands[0])
                if not cands:
                    raise ValueError(
                        f"مرجع غير معروف '{_o}{inner}{_c}' — استورد الحقل من تبويب الحقول أولاً")
                actual = sorted({_norm_tname(getattr(x, "table_source", None) or "") or "؟"
                                 for x in cands})
                raise ValueError(
                    f"المرجع '{_o}{inner}{_c}' ملتبس — موجود في ({'، '.join(actual)}): "
                    f"استخدم {_o}الجدول.{inner}{_c} صراحة")
            if len(pts) == 2:
                head, member = pts
                if head.lower() in (registry or {}):
                    resolved = resolve_ns_member(head, member, registry, visited)
                    if resolved is None:
                        raise ValueError(f"Unknown namespace '{head}' in {_o}{inner}{_c}")
                    return f"({resolved})"
                f = _find_in_table(member, head)
                if f is not None:
                    return _emit(str(getattr(f, "name", member)).strip().lower(), f)
                if member.strip().lower() in field_map:
                    actual = sorted({_norm_tname(getattr(x, "table_source", None) or "") or "؟"
                                     for x in by_name[member.strip().lower()]})
                    raise ValueError(
                        f"الحقل '{_o}{member}{_c}' موجود في ({'، '.join(actual)}) وليس في '{head}' — "
                        f"استخدم {_o}{actual[0]}.{member}{_c} أو استورد الحقل من '{head}'")
                raise ValueError(
                    f"مرجع غير معروف '{_o}{inner}{_c}' — للجداول استورد الحقل من '{head}' "
                    f"(تبويب الحقول ← استيراد من جدول)")
            raise ValueError(
                f"صيغة مرجع غير مدعومة '{_o}{inner}{_c}' — الصيغ: {_o}الحقل{_c} أو {_o}الجدول.الحقل{_c}")

        def _left_sub(m: re.Match) -> str:
            return _left_do(m.group(1), "[", "]")

        def _left_sub_b(m: re.Match) -> str:
            return _left_do(m.group(1), "{", "}")
        seg = re.sub(r"\[([^\]]*(?:\"|[^\x00-\x7F])[^\]]*)\]", _left_sub, seg)
        seg = re.sub(r"\{([^\}]*(?:\"|[^\x00-\x7F])[^\}]*)\}", _left_sub_b, seg)
        # {field} / {table.field} / {conn.table.field} — same as [...] collision-free
        def _field_sub(m: re.Match) -> str:
            inner = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
            parts = [p.strip() for p in inner.split(".")]
            if len(parts) == 1:
                key = inner.lower()
                if key not in field_map:
                    known = ", ".join(sorted(field_map.values())) or "—"
                    raise ValueError(f"Unknown field '[{inner}]' — defined fields: {known}")
                # Qualify unambiguous fields (same as [table.field]): a bare
                # name is only valid when its table needs no alias. Multi-table
                # queries otherwise fail with "could not be bound".
                _cands = by_name.get(key, [])
                _f1 = _cands[0] if len(_cands) == 1 else None
                return _emit(key, _f1)
            if len(parts) == 2:
                head, member = parts
                # Known namespace wins (backward compat for [ns.member])
                if head.lower() in (registry or {}):
                    resolved = resolve_ns_member(head, member, registry, visited)
                    if resolved is None:
                        raise ValueError(f"Unknown namespace '{head}' in [{inner}]")
                    return f"({resolved})"
                f = _find_in_table(member, head)
                if f is not None:
                    return _emit(str(getattr(f, "name", member)).strip().lower(), f)
                if member.strip().lower() in field_map:
                    actual = sorted({_norm_tname(getattr(x, "table_source", None) or "") or "؟"
                                     for x in by_name[member.strip().lower()]})
                    raise ValueError(
                        f"الحقل '[{member}]' موجود في ({'، '.join(actual)}) وليس في '{head}' — "
                        f"استخدم [{actual[0]}.{member}] أو استورد الحقل من '{head}'")
                raise ValueError(
                    f"مرجع غير معروف '[{inner}]' — للجداول استورد الحقل من '{head}' "
                    f"(تبويب الحقول ← استيراد من جدول)")
            if len(parts) == 3:
                ctok, table, member = parts
                f = _find_in_table(member, table)
                if f is None:
                    if member.strip().lower() in field_map:
                        actual = sorted({_norm_tname(getattr(x, "table_source", None) or "") or "؟"
                                         for x in by_name[member.strip().lower()]})
                        raise ValueError(
                            f"الحقل '[{member}]' موجود في ({'، '.join(actual)}) وليس في '{table}' — "
                            f"استورد الحقل من '{table}' أولاً")
                    raise ValueError(
                        f"مرجع غير معروف '[{inner}]' — استورد الحقل '{member}' من جدول '{table}' "
                        f"(تبويب الحقول ← استيراد من جدول)")
                if not _conn_accepted(ctok, _field_conns(f), conn_map):
                    have = sorted(_field_conns(f)) or ["؟"]
                    raise ValueError(
                        f"الحقل '{member}' في جدول '{table}' مربوط بالاتصال ({'، '.join(have)}) "
                        f"وليس '{ctok}' — اختر الاتصال الصحيح من المودال")
                return _emit(str(getattr(f, "name", member)).strip().lower(), f)
            raise ValueError(
                f"صيغة مرجع غير مدعومة '[{inner}]' — الصيغ: [الحقل] أو [الجدول.الحقل] "
                f"أو [الاتصال.الجدول.الحقل] (وكذلك بـ {{}} بدل [])")
        seg = FIELD_REF_RE.sub(_field_sub, seg)

        def _ns_sub(m: re.Match) -> str:
            ns, member = m.group(1), m.group(2)
            resolved = resolve_ns_member(ns, member, registry, visited)
            if resolved is None:
                return m.group(0)  # not a namespace → regular SQL (table.column)
            return f"({resolved})"
        seg = NS_REF_RE.sub(_ns_sub, seg)

        # Apply only outside "..." spans (emitted "alias"."COL" must survive).
        _qparts = re.split(r'("[^"]*")', seg)
        for _qi in range(0, len(_qparts), 2):
            _qp = _qparts[_qi]

            def _bare_rep(m: re.Match, _qp: str = _qp) -> str:
                # Bare XSQL-style `table.field` (no brackets): resolve against
                # <fields> exactly like [table.field] does. Precedence: explicit
                # [...]/{...} already resolved above; known file namespaces keep
                # NS_REF_RE's verdict (handled just above). Unknown table heads
                # stay literal (today's passthrough for raw SQL); unknown fields
                # on a KNOWN table also stay literal (never raise here — the DB
                # reports genuinely unknown columns, same as raw SQL today).
                head, words = m.group(1), m.group(2)
                if head.lower() in (registry or {}):
                    return m.group(0)
                if _norm_tname(head) not in _known_tables:
                    return m.group(0)
                # function call, e.g. schema.func( — leave for the DB
                k = m.end()
                if k < len(_qp) and _qp[k] == "(":
                    return m.group(0)
                hit = _bare_longest_field(words, head, by_name)
                if hit is None:
                    return m.group(0)
                f, key, dropped = hit
                return _emit(key, f) + ((" " + dropped) if dropped else "")

            _qparts[_qi] = _BARE2_RE.sub(_bare_rep, _qp)
        seg = "".join(_qparts)
        seg = _qualify_bare(seg)
        parts[i] = seg
    return "".join(parts)
