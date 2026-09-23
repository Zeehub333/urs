"""XSQL — Extended SQL for URS2.

A single-query language that:
- References tables from multiple queryable connections (each connection is a
  namespace: ``conn_a.tbl``, ``conn_b.tbl``, ``main_hq_2026.tbl``).
- Supports spreadsheet-style linear functions in expressions
  (SUMIF, COUNTIF, SUMIFS, XLOOKUP, VLOOKUP, FILTER, IF, ...) by emitting a
  per-connection SQL plus a Python merge step.
- Returns one unified rows payload identical to RML's execute() so the
  existing player works without changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 1) Tokenizer + parser (minimal, hand-written, RML-friendly)
# ---------------------------------------------------------------------------

_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "ASC", "DESC",
    "LIMIT", "OFFSET", "AS", "AND", "OR", "NOT", "IS", "NULL", "INNER",
    "JOIN", "LEFT", "RIGHT", "OUTER", "ON", "HAVING",
}
# Linear / spreadsheet functions stay as plain identifiers in the lexer so
# the parser can treat them as function calls.
_FUNCTIONS = {
    "SUM", "MIN", "MAX", "AVG", "COUNT", "SUMIF", "SUMIFS",
    "COUNTBLANK", "COUNTIF", "COUNTA", "IF", "FILTER",
    "XLOOKUP", "VLOOKUP",
}


@dataclass
class Token:
    kind: str            # "id" | "num" | "str" | "op" | "kw" | "comma" | "lparen" | "rparen" | "dot"
    value: str
    pos: int


def _tokenize(text: str) -> List[Token]:
    tokens: List[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == ",":
            tokens.append(Token("comma", ",", i)); i += 1; continue
        if ch == "(":
            tokens.append(Token("lparen", "(", i)); i += 1; continue
        if ch == ")":
            tokens.append(Token("rparen", ")", i)); i += 1; continue
        if ch == ".":
            tokens.append(Token("dot", ".", i)); i += 1; continue
        if ch in "<>=!+-*/%":
            j = i + 1
            while j < n and text[j] in "<>=!+-*/%":
                j += 1
            tokens.append(Token("op", text[i:j], i))
            i = j
            continue
        if ch in "'\"":
            quote = ch
            j = i + 1
            buf: List[str] = []
            while j < n and text[j] != quote:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1]); j += 2
                else:
                    buf.append(text[j]); j += 1
            tokens.append(Token("str", "".join(buf), i))
            i = j + 1
            continue
        if ch.isdigit():
            j = i + 1
            while j < n and (text[j].isdigit() or text[j] == "."):
                j += 1
            tokens.append(Token("num", text[i:j], i))
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            # Don't include '.' in identifiers — column refs like tbl.col are
            # split into tbl, '.', col so the parser can consume the dot
            # explicitly via `_accept_dot()`.
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            up = word.upper()
            if up in _KEYWORDS:
                tokens.append(Token("kw", up, i))
            else:
                tokens.append(Token("id", word, i))
            i = j
            continue
        # Unknown char: skip
        i += 1
    return tokens


# ---------------------------------------------------------------------------
# 2) AST nodes (just enough to support SELECT ... FROM ... [JOIN ...] [WHERE ...]
#    with linear functions inside expressions)
# ---------------------------------------------------------------------------


@dataclass
class FuncCall:
    name: str
    args: List["Expr"]


@dataclass
class ColumnRef:
    parts: List[str]   # e.g. ["conn_a", "tbl", "col"] or ["tbl", "col"] or ["col"]


@dataclass
class Literal:
    value: Any


@dataclass
class BinOp:
    op: str
    left: "Expr"
    right: "Expr"


@dataclass
class UnaryOp:
    op: str
    operand: "Expr"


Expr = FuncCall | ColumnRef | Literal | BinOp | UnaryOp


@dataclass
class JoinClause:
    side: str              # "INNER" | "LEFT" | "RIGHT"
    target: ColumnRef      # e.g. conn_b.tbl
    on: BinOp               # join predicate


@dataclass
class ParsedQuery:
    select: List[Tuple[Expr, Optional[str]]]   # (expr, alias)
    from_table: ColumnRef
    joins: List[JoinClause] = field(default_factory=list)
    where: Optional[Expr] = None
    group_by: List[Expr] = field(default_factory=list)
    order_by: List[Tuple[Expr, str]] = field(default_factory=list)
    limit: Optional[int] = None


# ---------------------------------------------------------------------------
# 3) Parser
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: List[Token]):
        self.toks = tokens
        self.i = 0

    def peek(self, off: int = 0) -> Optional[Token]:
        j = self.i + off
        return self.toks[j] if j < len(self.toks) else None

    def eat(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def accept(self, kind: str, value: Optional[str] = None) -> Optional[Token]:
        t = self.peek()
        if t is None:
            return None
        if t.kind != kind:
            return None
        if value is not None and t.value.upper() != value.upper():
            return None
        return self.eat()

    def expect(self, kind: str, value: Optional[str] = None) -> Token:
        t = self.accept(kind, value)
        if t is None:
            got = self.peek()
            raise SyntaxError(
                f"Expected {kind} {value or ''!r} at pos {got.pos if got else '?'}, "
                f"got {got.kind if got else 'EOF'} ({got.value if got else ''})")
        return t

    def parse(self) -> ParsedQuery:
        self.expect("kw", "SELECT")
        select_items = self._parse_select_list()
        self.expect("kw", "FROM")
        from_table = self._parse_column_ref()
        # Optional table alias on the primary: FROM tbl AS a
        if self.accept("kw", "AS"):
            self.expect("id")
        joins: List[JoinClause] = []
        while True:
            # Accept a join prefix: plain JOIN, or LEFT/RIGHT/INNER [JOIN] [OUTER].
            side = "INNER"
            side_consumed = False
            for kw in ("LEFT", "RIGHT", "INNER"):
                if self.accept("kw", kw):
                    side = kw
                    side_consumed = True
                    break
            # If a side keyword was consumed, look for OUTER and/or JOIN.
            if side_consumed:
                self.accept("kw", "OUTER")
                self.accept("kw", "JOIN")
            elif self.accept("kw", "JOIN"):
                side_consumed = True
            if not side_consumed:
                break
            joins.append(self._parse_join(side))
        where: Optional[Expr] = None
        if self.accept("kw", "WHERE"):
            where = self._parse_expression()
        group_by: List[Expr] = []
        if self.accept("kw", "GROUP"):
            self.expect("kw", "BY")
            group_by.append(self._parse_expression())
            while self._accept_comma():
                group_by.append(self._parse_expression())
        order_by: List[Tuple[Expr, str]] = []
        if self.accept("kw", "ORDER"):
            self.expect("kw", "BY")
            expr = self._parse_expression()
            direction = "ASC"
            if self.accept("kw", "DESC"):
                direction = "DESC"
            elif self.accept("kw", "ASC"):
                direction = "ASC"
            order_by.append((expr, direction))
        limit: Optional[int] = None
        if self.accept("kw", "LIMIT"):
            tnum = self.expect("num")
            limit = int(tnum.value)
        return ParsedQuery(select_items, from_table, joins, where, group_by, order_by, limit)

    def accept_kw_in(self, values: Tuple[str, ...]) -> bool:
        for v in values:
            if self.accept("kw", v):
                # Push back since accept already consumed; for our join parser we use a different approach
                # so we keep this returning False to avoid double-consume issues.
                return True
        return False

    def _parse_join(self, side: str = "INNER") -> JoinClause:
        # Join prefix already consumed. Parse: <table_ref> [AS alias] ON <left> = <right>.
        target = self._parse_column_ref()
        # Optional table alias: AS alias (we don't track aliases in v1; compiler
        # resolves columns by name within the target table).
        if self.accept("kw", "AS"):
            self.expect("id")
        self.expect("kw", "ON")
        # Use `_parse_comparison` (not `_parse_expression`) for both sides
        # because `_parse_equality` would greedily consume the `=` operator.
        on_left = self._parse_comparison()
        op = self.expect("op", "=").value
        on_right = self._parse_comparison()
        return JoinClause(side=side, target=target, on=BinOp(op, on_left, on_right))

    def _parse_alias_token(self) -> Optional[str]:
        """Accept either an identifier or a quoted string ("..." or '...') as alias."""
        t = self.peek()
        if t is None:
            return None
        if t.kind == "id":
            self.eat()
            return t.value
        if t.kind == "str":
            self.eat()
            return t.value
        return None

    def _parse_select_list(self) -> List[Tuple[Expr, Optional[str]]]:
        items: List[Tuple[Expr, Optional[str]]] = []
        expr = self._parse_expression()
        alias: Optional[str] = None
        if self.accept("kw", "AS"):
            alias = self._parse_alias_token()
        items.append((expr, alias))
        while self._accept_comma():
            expr = self._parse_expression()
            alias = None
            if self.accept("kw", "AS"):
                alias = self._parse_alias_token()
            items.append((expr, alias))
        return items

    def _parse_column_ref(self) -> ColumnRef:
        parts: List[str] = []
        # Accept either an identifier or a quoted string as a part
        # (SQL Server uses `[name]`; we also accept "name" / 'name').
        t = self.peek()
        if t is None:
            raise SyntaxError("Expected identifier, got EOF")
        if t.kind == "id":
            self.eat()
            parts.append(t.value)
        elif t.kind == "str":
            self.eat()
            parts.append(t.value)
        else:
            raise SyntaxError(f"Expected identifier at pos {t.pos}, got {t.kind} {t.value!r}")
        while self._accept_dot():
            nxt = self.peek()
            if nxt is None or nxt.kind not in ("id", "str"):
                raise SyntaxError(f"Expected identifier after '.', got {nxt.kind if nxt else 'EOF'}")
            self.eat()
            parts.append(nxt.value)
        return ColumnRef(parts)

    def _parse_expression(self) -> Expr:
        return self._parse_or()

    def _parse_or(self) -> Expr:
        left = self._parse_and()
        while self.accept("kw", "OR"):
            right = self._parse_and()
            left = BinOp("OR", left, right)
        return left

    def _parse_and(self) -> Expr:
        left = self._parse_equality()
        while self.accept("kw", "AND"):
            right = self._parse_equality()
            left = BinOp("AND", left, right)
        return left

    def _parse_equality(self) -> Expr:
        left = self._parse_comparison()
        while True:
            t = self.peek()
            if t is not None and t.kind == "op" and t.value == "=":
                self.eat()
                right = self._parse_comparison()
                left = BinOp("=", left, right); continue
            if t is not None and t.kind == "op" and t.value in ("!=", "<>"):
                self.eat()
                right = self._parse_comparison()
                left = BinOp("!=", left, right); continue
            break
        return left

    def _parse_comparison(self) -> Expr:
        left = self._parse_additive()
        while True:
            for op in (">", "<", ">=", "<="):
                if self.accept("op", op):
                    right = self._parse_additive()
                    left = BinOp(op, left, right)
                    break
            else:
                break
        return left

    def _parse_additive(self) -> Expr:
        left = self._parse_multiplicative()
        while True:
            if self.accept("op", "+"):
                right = self._parse_multiplicative()
                left = BinOp("+", left, right); continue
            if self.accept("op", "-"):
                right = self._parse_multiplicative()
                left = BinOp("-", left, right); continue
            break
        return left

    def _parse_multiplicative(self) -> Expr:
        left = self._parse_unary()
        while True:
            if self.accept("op", "*"):
                right = self._parse_unary()
                left = BinOp("*", left, right); continue
            if self.accept("op", "/"):
                right = self._parse_unary()
                left = BinOp("/", left, right); continue
            if self.accept("op", "%"):
                right = self._parse_unary()
                left = BinOp("%", left, right); continue
            break
        return left

    def _parse_unary(self) -> Expr:
        if self.accept("op", "-"):
            operand = self._parse_primary()
            return UnaryOp("-", operand)
        if self.accept("kw", "NOT"):
            operand = self._parse_primary()
            return UnaryOp("NOT", operand)
        return self._parse_primary()

    def _parse_primary(self) -> Expr:
        t = self.peek()
        if t is None:
            raise SyntaxError("Unexpected EOF in expression")
        if t.kind == "lparen":
            self.eat()
            e = self._parse_expression()
            self.expect("rparen")
            return e
        if t.kind == "num":
            self.eat()
            v = t.value
            return Literal(float(v) if "." in v else int(v))
        if t.kind == "str":
            # If the string is followed by `.`, treat it as a column-reference
            # segment (`"ns"."tbl"."col"`); otherwise it's a literal value.
            nxt = self.toks[self.i + 1] if self.i + 1 < len(self.toks) else None
            if nxt is not None and nxt.kind == "dot":
                # Full column reference, possibly multi-part.
                parts = [t.value]
                self.eat()
                while self._accept_dot():
                    nxt2 = self.peek()
                    if nxt2 is None or nxt2.kind not in ("id", "str"):
                        raise SyntaxError(f"Expected identifier after '.', got {nxt2.kind if nxt2 else 'EOF'}")
                    self.eat()
                    parts.append(nxt2.value)
                return ColumnRef(parts)
            # Otherwise: string literal. If the *following* token is a comma,
            # an alias keyword (AS), or FROM/WHERE/GROUP/ORDER — and we're at
            # top of a SELECT item — treat it as an identifier (column name).
            if nxt is not None and nxt.kind in ("comma", "kw"):
                kw = nxt.value.upper() if nxt.kind == "kw" else ""
                if nxt.kind == "comma" or kw in ("AS", "FROM", "WHERE", "GROUP", "ORDER", "LIMIT", "JOIN", "ON", "LEFT", "RIGHT", "INNER"):
                    self.eat()
                    return ColumnRef([t.value])
            self.eat()
            return Literal(t.value)
        if t.kind == "kw" and t.value.upper() == "NULL":
            self.eat()
            return Literal(None)
        if t.kind == "id":
            name = t.value
            # Lookahead: if the next token is `(` then it's a function call
            # (works for SUMIF, COUNTIF, NOW, CURRENT_TIMESTAMP, etc.).
            nxt = self.toks[self.i + 1] if self.i + 1 < len(self.toks) else None
            if nxt is not None and nxt.kind == "lparen":
                self.eat()
                self.expect("lparen")
                args = self._parse_func_args()
                self.expect("rparen")
                return FuncCall(name.upper(), args)
            self.eat()
            parts = [name]
            while self._accept_dot():
                nxt_id = self.expect("id")
                parts.append(nxt_id.value)
            return ColumnRef(parts)
        if t.kind == "kw":
            # Functions are not in the keyword set; treat as identifier
            self.eat()
            parts = [t.value]
            while self._accept_dot():
                nxt = self.expect("id")
                parts.append(nxt.value)
            return ColumnRef(parts)
        # Allow function name in any case (not just _KEYWORDS), so SUMIF/COUNTIF/etc
        # parse even though they're listed as keywords in the lexer.
        if t.kind in ("id", "kw"):
            self.eat()
            self.expect("lparen")
            args = self._parse_func_args()
            self.expect("rparen")
            return FuncCall(t.value.upper(), args)
        raise SyntaxError(f"Unexpected token at pos {t.pos}: {t.kind} {t.value!r}")
        raise SyntaxError(f"Unexpected token at pos {t.pos}: {t.kind} {t.value!r}")

    def _parse_func_args(self) -> List[Expr]:
        # Empty argument list is allowed for functions like NOW(), CURRENT_TIMESTAMP.
        t = self.peek()
        if t is not None and t.kind == "rparen":
            return []
        args: List[Expr] = [self._parse_expression()]
        while True:
            t = self.peek()
            if t is None or t.kind != "comma":
                break
            self.eat()  # consume comma
            args.append(self._parse_expression())
        return args

    def _accept_comma(self) -> bool:
        t = self.peek()
        if t is None or t.kind != "comma":
            return False
        self.eat()
        return True

    def _accept_dot(self) -> bool:
        t = self.peek()
        if t is None or t.kind != "dot":
            return False
        self.eat()
        return True

    def _accept_op(self, op: str) -> bool:
        t = self.peek()
        if t is None or t.kind != "op":
            return False
        if t.value != op:
            return False
        self.eat()
        return True


def parse(text: str) -> ParsedQuery:
    return _Parser(_tokenize(text)).parse()


# ---------------------------------------------------------------------------
# 4) Compiler — turn XSQL into per-connection SQL + a Python merge plan.
# ---------------------------------------------------------------------------


@dataclass
class ConnectionPlan:
    """SQL to run on one connection plus the column list expected back."""
    conn_key: str
    schema: Optional[str]
    table: str
    sql: str
    columns: List[str]
    aliases: Dict[str, str]            # expr_alias -> sql_fragment
    needs_python_eval: bool = False   # when SUMIF/COUNTIF etc. were inlined


@dataclass
class CompiledQuery:
    primary: ConnectionPlan
    secondaries: List[ConnectionPlan] = field(default_factory=list)
    merges: List[Dict[str, str]] = field(default_factory=list)  # [{primary:col, secondary:conn, secondary_col:...}]
    final_select: List[str] = field(default_factory=list)
    final_columns: List[str] = field(default_factory=list)


def _normalize_ref(parts: List[str], conn_map: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], Optional[str], str]:
    """Return (conn_key, schema_or_table_owner, col_or_table).

    XSQL supports three forms:
      [a] col                  -> (None, None, "col")
      [b] schema.table         -> (None, schema, "table")
      [c] conn.table           -> (conn, None, "table")   when conn is in conn_map
      [d] conn.schema.table    -> (conn, schema, "table")
      [e] table.col            -> (None, table, "col")
      [f] schema.table.col     -> (None, schema, "col")
    """
    if len(parts) == 1:
        return None, None, parts[0]
    if len(parts) == 2:
        # conn.table OR schema.table: prefer conn match when available.
        if conn_map and parts[0] in conn_map:
            return parts[0], None, parts[1]
        return None, parts[0], parts[1]
    # 3 parts: conn.schema.table OR schema.table.col OR conn.table.col
    if conn_map and parts[0] in conn_map:
        return parts[0], parts[1], parts[2]
    return None, parts[0], parts[1]


class XSQLCompiler:
    """Compile a ParsedQuery into per-connection SQL.

    Aims to keep *all* logic on the DB engine (SUMIF/COUNTIF/SUMIFS etc.
    compile down to CASE WHEN aggregates). When that is impossible (e.g.
    XLOOKUP needs a second connection mid-row), we mark the plan as
    `needs_python_eval=True` so the engine can hydrate and join in Python.
    """

    PYTHON_FUNCTIONS = {"XLOOKUP", "VLOOKUP", "FILTER", "IF"}

    def __init__(self, parsed: ParsedQuery, conn_map: Dict[str, Dict[str, Any]]):
        self.p = parsed
        # conn_map: {conn_key: {"engine": "postgres"|"sqlserver", "schema": default_schema}}
        self.conn_map = conn_map

    def compile(self) -> CompiledQuery:
        primary_conn, primary_schema, primary_table = _normalize_ref(self.p.from_table.parts, self.conn_map)
        primary_conn = self._resolve_conn(primary_conn)
        primary_cols: List[str] = []
        primary_aliases: Dict[str, str] = {}
        primary_select: List[str] = []

        # Detect whether any SELECT expression needs Python eval.
        needs_py = any(self._expr_needs_python(s) for s, _ in self.p.select)
        # Grouping keys must be columns.
        group_cols = [self._column_name(c) for c in self.p.group_by]
        for col in group_cols:
            if col:
                primary_select.append(col)
                primary_cols.append(col)

        # Aggregate / scalar SELECT items.
        for expr, alias in self.p.select:
            sql_frag, col_name = self._emit_select_expr(expr, alias)
            primary_select.append(sql_frag)
            primary_cols.append(col_name)
            if alias:
                primary_aliases[alias] = sql_frag

        # Push WHERE into SQL when possible.
        where_sql = ""
        if self.p.where is not None and not needs_py:
            where_sql = self._emit_where_for_primary(self.p.where, primary_conn, primary_schema, primary_table)

        sql = f"SELECT {', '.join(primary_select) if primary_select else '*'} FROM {self._q_table(primary_conn, primary_schema, primary_table)}{(' WHERE ' + where_sql) if where_sql else ''}"

        primary = ConnectionPlan(
            conn_key=primary_conn or "",
            schema=primary_schema,
            table=primary_table,
            sql=sql,
            columns=primary_cols,
            aliases=primary_aliases,
            needs_python_eval=needs_py,
        )

        # JOINs become Python-side merges (XSQL keeps them simple: each JOIN
        # becomes a fetch from the secondary conn, joined on the predicate).
        secondaries: List[ConnectionPlan] = []
        merges: List[Dict[str, str]] = []
        for j in self.p.joins:
            sec_conn, sec_schema, sec_table = _normalize_ref(j.target.parts, self.conn_map)
            sec_conn = self._resolve_conn(sec_conn)
            on_left = self._column_name(j.on.left)
            on_right = self._column_name(j.on.right)
            sec_sql = f"SELECT * FROM {self._q_table(sec_conn, sec_schema, sec_table)}"
            secondaries.append(ConnectionPlan(
                conn_key=sec_conn or "",
                schema=sec_schema,
                table=sec_table,
                sql=sec_sql,
                columns=[on_right] if on_right else [],
                aliases={},
                needs_python_eval=True,
            ))
            merges.append({
                "primary_col": on_left or "",
                "secondary_conn": sec_conn or "",
                "secondary_table": sec_table,
                "secondary_col": on_right or "",
                "side": j.side,
            })

        final_columns = [a if a else self._column_name(e) for (e, a) in self.p.select]
        final_columns += group_cols
        return CompiledQuery(
            primary=primary,
            secondaries=secondaries,
            merges=merges,
            final_select=[a if a else self._column_name(e) for (e, a) in self.p.select],
            final_columns=final_columns,
        )

    # ---- helpers ----

    def _resolve_conn(self, conn_key: Optional[str]) -> str:
        if conn_key is None:
            return ""  # primary (default) connection
        return conn_key

    def _q_table(self, conn_key: Optional[str], schema: Optional[str], table: str) -> str:
        parts = []
        if schema and "." in table:
            sch, tbl = table.split(".", 1)
            parts.append(self._q(sch)); parts.append(self._q(tbl))
        else:
            if schema:
                parts.append(self._q(schema))
            parts.append(self._q(table))
        return ".".join(parts)

    def _q(self, ident: str) -> str:
        # Identifier quoting: keep dotted identifiers intact, quote each piece.
        return ".".join('"' + p.replace('"', '""') + '"' for p in ident.split("."))

    def _column_name(self, expr: Any) -> str:
        if isinstance(expr, ColumnRef):
            return expr.parts[-1]
        if isinstance(expr, FuncCall):
            return expr.name
        return ""

    def _expr_needs_python(self, expr: Any) -> bool:
        if isinstance(expr, FuncCall):
            if expr.name in self.PYTHON_FUNCTIONS:
                return True
            return any(self._expr_needs_python(a) for a in expr.args)
        if isinstance(expr, BinOp):
            return self._expr_needs_python(expr.left) or self._expr_needs_python(expr.right)
        if isinstance(expr, UnaryOp):
            return self._expr_needs_python(expr.operand)
        return False

    def _emit_select_expr(self, expr: Any, alias: Optional[str]) -> Tuple[str, str]:
        if isinstance(expr, FuncCall):
            return self._emit_func(expr, alias)
        if isinstance(expr, ColumnRef):
            # Quote each segment so Arabic/keywords stay valid identifiers.
            col = ".".join(f'"{p}"' for p in expr.parts)
            return col, expr.parts[-1]
        if isinstance(expr, Literal):
            v = expr.value
            if v is None:
                return "NULL", (alias or "lit")
            if isinstance(v, str):
                return f"'{v.replace(chr(39), chr(39)+chr(39))}'", (alias or "lit")
            return str(v), (alias or "lit")
        if isinstance(expr, BinOp):
            l, _ = self._emit_select_expr(expr.left, None)
            r, _ = self._emit_select_expr(expr.right, None)
            return f"({l} {expr.op} {r})", (alias or "calc")
        if isinstance(expr, UnaryOp):
            inner, _ = self._emit_select_expr(expr.operand, None)
            return f"({expr.op}{inner})", (alias or "calc")
        return "NULL", (alias or "col")

    def _emit_func(self, fn: FuncCall, alias: Optional[str]) -> Tuple[str, str]:
        """Translate linear functions into SQL where possible.

        - SUM, MIN, MAX, AVG, COUNT → SQL aggregates (unchanged)
        - SUMIF(range, criteria, sum_range) → SUM(CASE WHEN criteria THEN sum_range END)
        - COUNTIF(range, criteria) → SUM(CASE WHEN criteria THEN 1 ELSE 0 END)
        - SUMIFS(sum_range, criteria_range1, criteria1, ...) → SUM(CASE WHEN ... END)
        - COUNTBLANK(range) → SUM(CASE WHEN range IS NULL OR range = '' THEN 1 ELSE 0 END)
        - COUNTA(range) → COUNT(range)  (Postgres) / approximate for SQL Server
        - IF(cond, a, b) → CASE WHEN cond THEN a ELSE b END
        - FILTER(range, criteria) → handled Python-side (returns the matched value)
        - XLOOKUP / VLOOKUP → handled Python-side (cross-connection)
        """
        name = fn.name
        col = alias or name.lower()
        if name in ("SUM", "MIN", "MAX", "AVG", "COUNT"):
            args_sql = [self._emit_select_expr(a, None)[0] for a in fn.args]
            return f"{name}({', '.join(args_sql)})", col
        if name == "SUMIF":
            # SUMIF(sum_range, criteria_expr, criteria_value)
            sum_range, crit_expr, crit_val = fn.args
            sum_sql = self._emit_select_expr(sum_range, None)[0]
            pred = self._eq_predicate(crit_expr, crit_val)
            return f"SUM(CASE WHEN {pred} THEN {sum_sql} END)", col
        if name == "COUNTIF":
            crit_expr, crit_val = fn.args[0], fn.args[1]
            pred = self._eq_predicate(crit_expr, crit_val)
            return f"SUM(CASE WHEN {pred} THEN 1 ELSE 0 END)", col
        if name == "SUMIFS":
            # SUMIFS(sum_range, c1_range, c1_val, c2_range, c2_val, ...)
            sum_range = fn.args[0]
            sum_sql = self._emit_select_expr(sum_range, None)[0]
            conds = []
            for i in range(1, len(fn.args), 2):
                rng, val = fn.args[i], fn.args[i + 1]
                rng_sql = self._emit_select_expr(rng, None)[0]
                val_sql = self._emit_select_expr(val, None)[0]
                conds.append(f"{rng_sql} = {val_sql}")
            return f"SUM(CASE WHEN {' AND '.join(conds)} THEN {sum_sql} END)", col
        if name == "COUNTBLANK":
            rng = fn.args[0]
            rng_sql = self._emit_select_expr(rng, None)[0]
            return f"SUM(CASE WHEN {rng_sql} IS NULL OR {rng_sql} = '' THEN 1 ELSE 0 END)", col
        if name == "COUNTA":
            rng = fn.args[0]
            rng_sql = self._emit_select_expr(rng, None)[0]
            return f"COUNT({rng_sql})", col
        if name == "IF":
            cond, a, b = fn.args
            cond_sql = self._emit_select_expr(cond, None)[0]
            a_sql = self._emit_select_expr(a, None)[0]
            b_sql = self._emit_select_expr(b, None)[0]
            return f"CASE WHEN {cond_sql} THEN {a_sql} ELSE {b_sql} END", col
        # FILTER / XLOOKUP / VLOOKUP handled Python-side — caller adds a marker
        # column so the engine can post-process.
        if name in ("FILTER", "XLOOKUP", "VLOOKUP"):
            marker = f"__py_{name.lower()}_{col}"
            return f"NULL AS {self._q(marker)}", marker
        return "NULL", col

    def _eq_predicate(self, range_expr: Expr, value: Expr) -> str:
        rng_sql = self._emit_select_expr(range_expr, None)[0]
        val_sql = self._emit_select_expr(value, None)[0]
        return f"{rng_sql} = {val_sql}"

    def _emit_where_for_primary(self, expr: Any, conn_key: str, schema: Optional[str], table: str) -> str:
        # Conservative: emit only comparisons against column refs we know exist
        # on the primary connection. Anything else stays on the Python side.
        if isinstance(expr, BinOp):
            if expr.op in ("AND", "OR"):
                l = self._emit_where_for_primary(expr.left, conn_key, schema, table)
                r = self._emit_where_for_primary(expr.right, conn_key, schema, table)
                if l and r:
                    return f"({l} {expr.op} {r})"
                return ""
            if isinstance(expr.left, ColumnRef) and expr.op in ("=", "!=", "<", "<=", ">", ">="):
                return self._eq_predicate(expr.left, expr.right)
        if isinstance(expr, ColumnRef):
            # bare column → treat as truthy / IS NOT NULL
            col = self._column_name(expr)
            return f"{self._q(col)} IS NOT NULL"
        return ""


# ---------------------------------------------------------------------------
# 5) Public API: parse + compile + run.
# ---------------------------------------------------------------------------


def compile_xsql(text: str, conn_map: Dict[str, Dict[str, Any]]) -> CompiledQuery:
    parsed = parse(text)
    return XSQLCompiler(parsed, conn_map).compile()


# ---------------------------------------------------------------------------
# 6) RML → XSQL translator.
# ---------------------------------------------------------------------------
# Take a parsed RMLReportCompiler (fields + columns + links + table_opts +
# general_where) and emit a single XSQL string that the existing compiler can
# plan into per-connection SQL + Python merge.
# ---------------------------------------------------------------------------


def _rml_table_short(t: str) -> str:
    """Strip schema/owner from a table reference. RML often uses
    `dbo.tblX` or just `tblX`. We want the bare table name.
    """
    s = str(t or "").strip()
    if not s:
        return ""
    if "." in s:
        s = s.split(".")[-1]
    return s.strip("[]\"'")


def _rml_field_alias_from_name(name: str) -> str:
    """Pick a safe alias from a field/column Arabic-or-Latin name.
    We transliterate to ASCII when possible, fallback to lower-snake.
    """
    import re as _re
    s = str(name or "").strip()
    if not s:
        return "col"
    out = _re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_").lower()
    return out or "col"


def rml_to_xsql(compiler, conn_keys: Optional[List[str]] = None) -> Tuple[str, Dict[str, str]]:
    """Convert a compiled RML into a single XSQL string + namespace map.

    Parameters
    ----------
    compiler : RMLReportCompiler (already-loaded .rml)
    conn_keys : optional list of namespace aliases to use for the
                tables referenced by the report. The i-th conn_key is
                mapped to the i-th <rml_connections> entry in the file
                (after the default). If None, we auto-assign ns_<i>.

    Returns
    -------
    (sql_text, namespace_map)
        sql_text is a single XSQL string the player can hand to /api/xsql/run/.
        namespace_map is {"ns_2": "2", "ns_6": "6", ...} ready for that endpoint.
    """
    # --- collect fields + their connection groups ---
    try:
        fields = list(compiler.fields() or [])
    except Exception:
        fields = []
    try:
        rml_conns = list(compiler.connections() or [])
    except Exception:
        rml_conns = []
    # Map gid (global connection id from Django) -> rml_conn.id -> namespace.
    gid_to_ns: Dict[str, str] = {}
    ns_to_gid: Dict[str, str] = {}
    for i, rc in enumerate(rml_conns):
        gid = str(getattr(rc, "connection_id", "") or "")
        if not gid:
            continue
        if conn_keys and i < len(conn_keys):
            ns = conn_keys[i]
        else:
            ns = f"ns_{gid}"
        gid_to_ns[gid] = ns
        ns_to_gid[ns] = gid
    # --- group fields by (table_source, gid) so each FROM block is a real query ---
    from collections import OrderedDict
    table_groups: "OrderedDict[Tuple[str,str], list]" = OrderedDict()
    for f in fields:
        ts = _rml_table_short(getattr(f, "table_source", "") or "")
        gid = str(getattr(f, "connection_id", "") or getattr(f, "conn_id", "") or "")
        if not ts:
            continue
        # Default gid: the first connection in the file.
        if not gid and rml_conns:
            gid = str(getattr(rml_conns[0], "connection_id", "") or "")
        if not gid:
            continue
        if gid not in gid_to_ns:
            # An unknown gid (e.g. dropped connection). Synthesize ns.
            ns = f"ns_{gid}"
            gid_to_ns[gid] = ns
            ns_to_gid[ns] = gid
        table_groups.setdefault((gid, ts), []).append(f)
    # Pick the base (=first) connection+table as FROM target.
    if not table_groups:
        return "SELECT 1 AS empty_xsql", {}
    (base_gid, base_ts), base_fields = next(iter(table_groups.items()))
    base_ns = gid_to_ns[base_gid]
    base_fields_count = len(base_fields)
    # --- SELECT list ---
    select_parts: List[str] = []
    seen_aliases: Dict[str, int] = {}
    for f in base_fields:
        fn = getattr(f, "name", "")
        if not fn:
            continue
        # column reference fully qualified by base namespace.
        alias = _rml_field_alias_from_name(fn)
        if alias in seen_aliases:
            seen_aliases[alias] += 1
            alias = f"{alias}_{seen_aliases[alias]}"
        else:
            seen_aliases[alias] = 1
        select_parts.append(f'"{fn}" AS {alias}')
    # Secondary tables → LEFT JOIN (we don't know the right join side here).
    joins_sql: List[str] = []
    for (gid, ts), flist in list(table_groups.items())[1:]:
        ns = gid_to_ns[gid]
        join_cols: List[str] = []
        for f in flist:
            fn = getattr(f, "name", "")
            if fn:
                join_cols.append(f'"{ns}"."{ts}"."{fn}"')
        sel_cols_sql = ", ".join(join_cols) if join_cols else "*"
        joins_sql.append(
            f'LEFT JOIN "{ns}"."{ts}" ON 1=1 -- pseudo-join; real link via XLOOKUP()'
        )
    # --- general_where -> WHERE (best effort) ---
    gw = ""
    try:
        gw = str(compiler.general_where() or "")
    except Exception:
        gw = ""
    # If general_where references columns not in base, drop it (caller may add XLOOKUP).
    where_sql = ""
    if gw.strip():
        # Strip [[...]] and [...]; we keep it raw in WHERE — XSQL parser understands both.
        # The most common case is `([[tbl.col]] = val)` which maps to base cols.
        where_sql = gw.strip()
        if not where_sql.upper().startswith("WHERE"):
            where_sql = "WHERE " + where_sql
        # Translate `[[tbl.col]]` → `"tbl"."col"` (no schema) for the base namespace.
        where_sql = re.sub(r"\[\[([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\]\]", r'"\1"."\2"', where_sql)
    # --- assemble XSQL ---
    parts: List[str] = ["SELECT"]
    parts.append("    " + ",\n    ".join(select_parts) if select_parts else "    *")
    parts.append(f'FROM "{base_ns}"."{base_ts}" AS t0')
    parts.extend(joins_sql)
    if where_sql:
        parts.append(where_sql)
    parts.append(f"-- {base_fields_count} fields from {base_ns}.{base_ts}")
    sql_text = "\n".join(parts)
    return sql_text, ns_to_gid


def xsql_namespaces_from_rml(compiler) -> Dict[str, str]:
    """Return the {namespace: gid} map for an RML — used as `namespaces`
    argument for /api/xsql/run/."""
    _sql, ns = rml_to_xsql(compiler, conn_keys=None)
    return ns

