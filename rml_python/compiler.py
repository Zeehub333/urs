"""
RML Compiler & Metadata Parser — Production Ready
Parses XML RML files conforming to Oracle RML format.
Extracts <rpt_metadata> and <column> definitions with robust error handling.
"""
from __future__ import annotations
import pathlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

# ── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class RMLMetadata:
    """Official report metadata from <rpt_metadata>"""
    name: str
    display_name: str
    category: Optional[str] = None
    connection: Optional[str] = None
    schema: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    namespace: Optional[str] = None  # addressable name for cross-file ns.rule references
    report_type: str = "master"  # master | detail | doc
    doc_layout: Optional[str] = None  # doc only: card (default, one record) | table (كشف واحد)
    distinct: bool = False  # صفوف مميزة فقط (SELECT DISTINCT) — يُضبط من المصمم
    group_levels: int = 1  # عدد مستويات المجموعات (رئيسية/فرعية) — يُضبط من المصمم
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "displayName": self.display_name,
            "displayname": self.display_name,  # alias for case-insensitive consumers
            "category": self.category,
            "connection": self.connection,
            "schema": self.schema,
            "icon": self.icon,
            "description": self.description,
            "namespace": self.namespace,
            "type": self.report_type,
            "reportType": self.report_type,
            "doc_layout": self.doc_layout,
            "docLayout": self.doc_layout,
            "distinct": self.distinct,
            "group_levels": self.group_levels,
            "groupLevels": self.group_levels,
            "raw_attrs": dict(self.raw_attrs or {}),
        }


@dataclass
class RMLDetail:
    """Master-detail configuration: sub-records of another table.

    <detail table="ITEMS" master="BILL_NO" detail="BILL_NO"><column .../>...
    master: key column in the master (FROM) table.
    detail: key column in the detail table (the link reference).
    """

    table: str
    master: str
    detail: str
    columns: List["RMLColumn"] = field(default_factory=list)
    rel_type: str = "one_to_many"  # one_to_one | one_to_many (detail is sub-level by default)
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table": self.table,
            "master": self.master,
            "detail": self.detail,
            "masterKey": self.master,
            "detailKey": self.detail,
            "rel_type": self.rel_type,
            "relType": self.rel_type,
            "columns": [c.to_dict() for c in self.columns],
        }

@dataclass
class RMLField:
    """Base field definition — must map 1:1 to a DB column.
    <field name type conn_id table_source>
    name MUST equal the DB column name; used for direct display or computation.
    """
    id: str
    name: str  # DB column name (required, must match DB)
    data_type: Optional[str] = None  # type / dataType
    connection_id: Optional[str] = None  # conn_id / connection_id / connection
    table_source: Optional[str] = None  # table_source / tableSource / table
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.data_type,
            "dataType": self.data_type,
            "data_type": self.data_type,
            "connection_id": self.connection_id,
            "connectionId": self.connection_id,
            "conn_id": self.connection_id,
            "table_source": self.table_source,
            "tableSource": self.table_source,
        }


@dataclass
class RMLColumn:
    """Display/computed column — calculated and shown in UI.
    <column alias name expr where_clause icon>
    alias: display label; name: programmatic column name; expr: SQL expression
    (may reference <field> names); where_clause: per-column filter;
    icon: FontAwesome icon for UI.
    Columns can now reference multiple tables via ref_table/ref_fk/ref_display lists,
    or a single table for backward compatibility.
    """
    id: str
    name: str
    alias: str
    data_type: Optional[str] = None
    expr: str = ""
    # Support multiple tables: list of {table, fk, display} for cross-table references
    ref_tables: List[Dict[str, Optional[str]]] = field(default_factory=list)
    # Backward compatibility: single ref (when ref_tables is empty)
    ref_table: Optional[str] = None
    ref_fk: Optional[str] = None
    ref_display: Optional[str] = None
    col_type: str = "direct"  # direct | computed | fk_lookup | aggregated
    join_type: str = "one_to_one"  # one_to_one | one_to_many (fk_lookup cardinality)
    # Runtime-derived lookup (no stored props needed): set by the engine from
    # the expression's display ref + the scope↔ref link (see _derive_lookup)
    ref_scope_key: Optional[str] = None  # scope-side FK column (may differ from ref_fk)
    ref_scope_table: Optional[str] = None  # scope table the key belongs to
    connection_id: Optional[str] = None  # link column to specific connection (compat)
    where_clause: Optional[str] = None  # per-column filter (where_clause / where)
    icon: Optional[str] = None  # UI icon
    is_amount: bool = False  # amount column (is_amount) -> formatted with thousands separators
    currency_field: Optional[str] = None  # optional currency source column (currency_field)
    is_status: bool = False  # status column -> value mapped to label+color in player
    status_map: List[Dict[str, str]] = field(default_factory=list)  # [{value,label,color}]
    is_distinct: bool = False  # منع التكرار: DISTINCT ON (هذا العمود) — يُضبط من المصمم
    raw_attrs: Dict[str, str] = field(default_factory=dict)
    # Display formatting (set from the designer type modal, honored by the player)
    max_chars: Optional[int] = None  # text: max chars (truncate + tooltip)
    wrap: bool = False  # text: wrap lines (else nowrap ellipsis)
    decimals: Optional[int] = None  # number: decimal places
    date_format: Optional[str] = None  # date/datetime/time: display pattern

    def to_dict(self) -> Dict[str, Any]:
        base = {
            "id": self.id,
            "name": self.name,
            "alias": self.alias,
            "dataType": self.data_type,
            "data_type": self.data_type,
            "expr": self.expr,
            "join_type": self.join_type,
            "joinType": self.join_type,
            "col_type": self.col_type,
            "connection_id": self.connection_id,
            "connectionId": self.connection_id,
            "where_clause": self.where_clause,
            "whereClause": self.where_clause,
            "icon": self.icon,
            "is_amount": self.is_amount,
            "isAmount": self.is_amount,
            "currency_field": self.currency_field,
            "currencyField": self.currency_field,
            "is_status": self.is_status,
            "isStatus": self.is_status,
            "status_map": [dict(s) for s in self.status_map],
            "statusMap": [dict(s) for s in self.status_map],
            "is_distinct": self.is_distinct,
            "isDistinct": self.is_distinct,
            "max_chars": self.max_chars,
            "maxChars": self.max_chars,
            "wrap": self.wrap,
            "decimals": self.decimals,
            "date_format": self.date_format,
            "dateFormat": self.date_format,
        }
        # Add ref tables (new) and backward-compat single refs
        if self.ref_tables:
            base["refTables"] = self.ref_tables
        else:
            if self.ref_table:
                base["refTable"] = self.ref_table
            if self.ref_fk:
                base["refFk"] = self.ref_fk
            if self.ref_display:
                base["refDisplay"] = self.ref_display
        if self.ref_scope_key:
            base["refScopeKey"] = self.ref_scope_key
        if self.ref_scope_table:
            base["refScopeTable"] = self.ref_scope_table
        return base


@dataclass
class RMLGroup:
    """Column group for grouped table headers (column spanning).

    <groups><group id name order><column alias/>...</group></groups>
    Members reference display columns by alias; order controls left-to-right
    group position in the player header.
    """

    id: str
    name: str
    order: int = 0
    columns: List[str] = field(default_factory=list)  # member column aliases
    level: int = 1  # 1 = رئيسية، 2+ = فرعية (صفوف رأس متداخلة)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "order": self.order,
            "columns": list(self.columns),
            "level": self.level,
        }


@dataclass
class RMLConnection:
    """Connection reference inside RML — links to connections table"""

    id: str  # auto-numbered within report (1,2,3...)
    connection_id: str  # FK to connections table (e.g., 5)
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "connection_id": self.connection_id, "connectionId": self.connection_id}


@dataclass
class RMLChart:
    """Chart definition inside RML — multiple charts per report"""

    id: str
    type: str = "bar"  # bar, line, pie, doughnut, area
    x: Optional[str] = None  # field to compare on X (labels for pie)
    y: Optional[str] = None  # field to compare on Y (values)
    y2: Optional[str] = None  # optional second value column (compare mode)
    title: Optional[str] = None
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "x": self.x,
            "y": self.y,
            "y2": self.y2,
            "title": self.title,
        }


@dataclass
class RMLRuleVariable:
    """متغير قاعدة أعمال: <variable name display type options>
    type ∈ {text, number, date, time, expression, boolean, choice}."""
    name: str
    display: str = ""
    type: str = "text"
    options: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "display": self.display or self.name,
                "type": self.type or "text", "options": list(self.options or [])}


@dataclass
class RMLRulePolicy:
    """سياسة تحت قاعدة: <policy id name priority is_default>
    <match><on field><v/></on></match><values><v name>value</v></values></policy>.
    match: {field: [values]} — فارغ = تنطبق على كل السجلات.
    priority: الأصغر = أعلى أولوية = يُقيّم أولاً ويفوز عند التداخل (افتراضي 100).
    is_default: سياسة افتراضية واحدة لكل قاعدة — قيمها هي ELSE عند عدم مطابقة
    أي سياسة (Fallback). سياسة بلا match تُعامل كافتراضية للتوافق الخلفي."""
    id: str
    name: str = ""
    match: Dict[str, List[str]] = field(default_factory=dict)
    values: Dict[str, str] = field(default_factory=dict)
    priority: int = 100
    is_default: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name or self.id,
                "match": {k: list(v) for k, v in (self.match or {}).items()},
                "values": dict(self.values or {}),
                "priority": self.priority,
                "is_default": bool(self.is_default)}


@dataclass
class RMLRule:
    """Computed business rule inside RML.
    <rule id name display icon description expr connection_id type message value>
      <sources><source field/></sources>
      <variables><variable .../></variables>
      <policies><policy .../></policies>
    Addressable cross-file as <namespace>.<name> (e.g. hrRules.rule1):
    the rule expression is resolved and inlined at compile time.
    Per-record values via $name.var$ in column expressions (policy match).
    """
    id: str
    name: str
    expr: str = ""
    connection_id: Optional[str] = None
    type: Optional[str] = None
    message: Optional[str] = None
    value: Optional[str] = None
    display: str = ""
    icon: str = ""
    description: str = ""
    sources: List[str] = field(default_factory=list)
    variables: List[RMLRuleVariable] = field(default_factory=list)
    policies: List[RMLRulePolicy] = field(default_factory=list)
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "expr": self.expr,
            "connection_id": self.connection_id,
            "type": self.type,
            "message": self.message,
            "value": self.value,
            "display": self.display or self.name,
            "icon": self.icon or "",
            "description": self.description or "",
            "sources": list(self.sources or []),
            "variables": [v.to_dict() for v in (self.variables or [])],
            "policies": [p.to_dict() for p in (self.policies or [])],
        }

# ── Compiler ─────────────────────────────────────────────────────────────────

class RMLReportCompiler:
    """
    Parses RML XML and extracts metadata + columns.
    Usage:
        compiler = RMLReportCompiler("report.rml")
        meta = compiler.rpt_metadata()  # -> dict
        cols = compiler.columns()       # -> List[RMLColumn]
    Also supports: RMLReportCompiler.from_string(xml_text)
    """

    def __init__(self, path: str | pathlib.Path | None = None, *, xml_text: str | None = None):
        self.path = pathlib.Path(path) if path else None
        self._xml_text: str | None = xml_text
        self._root: Optional[ET.Element] = None
        self._metadata: Optional[RMLMetadata] = None
        self._columns: Optional[List[RMLColumn]] = None
        self._fields: Optional[List[RMLField]] = None
        self._connections: Optional[List[RMLConnection]] = None
        self._charts: Optional[List[RMLChart]] = None
        self._rules: Optional[List[RMLRule]] = None
        self._groups: Optional[List[RMLGroup]] = None
        self._detail: Optional[RMLDetail] = None
        self._doc_template: Optional[str] = None
        self._general_where: Optional[str] = None
        if path or xml_text:
            self._parse()

    @classmethod
    def from_string(cls, xml_text: str) -> "RMLReportCompiler":
        """Create compiler from XML string (convenience)."""
        return cls(xml_text=xml_text)

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> "RMLReportCompiler":
        return cls(path=path)

    # ── Internal parsing ──────────────────────────────────────────────────

    def _load_xml(self) -> str:
        if self._xml_text is not None:
            return self._xml_text
        if self.path and self.path.exists():
            return self.path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"RML file not found: {self.path}")

    def _parse(self) -> None:
        """Parse XML with strict error handling; populates _root."""
        raw = self._load_xml()
        # Strip BOM and handle encoding
        raw = raw.lstrip("\ufeff")
        try:
            # Use ET; for large files, consider lxml for XSD validation
            self._root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise ValueError(f"Invalid RML XML at line {e.position[0]}: {e}") from e

        # Normalize tag names to lower for case-insensitive search
        # Keep original for error messages, but search lower

    def _find_metadata_element(self) -> Optional[ET.Element]:
        if self._root is None:
            return None
        # Search case-insensitive: rpt_metadata, RPT_METADATA, etc.
        for el in self._root.iter():
            if el.tag.lower() == "rpt_metadata":
                return el
        # Also check direct child
        for el in list(self._root):
            if el.tag.lower() == "rpt_metadata":
                return el
        return None

    def _find_columns_container(self) -> Optional[ET.Element]:
        if self._root is None:
            return None
        for el in self._root.iter():
            if el.tag.lower() == "columns":
                return el
        return None

    # ── Public API ────────────────────────────────────────────────────────

    def rpt_metadata(self) -> Dict[str, Any]:
        """
        Core engine function: returns report metadata as dict.
        Supports: name, displayName/displayname, category, connection, schema
        """
        if self._metadata:
            return self._metadata.to_dict()

        el = self._find_metadata_element()
        if el is None:
            raise ValueError("Missing <rpt_metadata> tag in RML (required: name, displayName)")

        # Helper to fetch attr case-insensitive
        def get_attr(*names: str, default: Optional[str] = None) -> Optional[str]:
            # names are lower variants to try
            for k, v in el.attrib.items():
                if k.lower() in [n.lower() for n in names]:
                    return v.strip() if isinstance(v, str) else v
            return default

        name = get_attr("name")
        if not name:
            raise ValueError("<rpt_metadata> missing required attribute 'name'")

        display = get_attr("displayName", "displayname", "display_name", default=name)
        category = get_attr("category")
        connection = get_attr("connection")
        schema = get_attr("schema")
        icon = get_attr("icon")
        description = get_attr("description", "desc")
        namespace = get_attr("namespace", "ns")
        report_type = (get_attr("type", "report_type", "reportType", default="master") or "master").strip().lower()
        if report_type not in ("master", "detail", "doc"):
            report_type = "master"
        doc_layout = (get_attr("doc_layout", "docLayout", "layout", default="") or "").strip().lower() or None
        _dv = (get_attr("distinct", default="") or "").strip().lower()
        distinct = _dv in ("1", "true", "yes", "y")
        try:
            group_levels = max(1, min(int(str(get_attr("group_levels", "groupLevels", "levels", default="1") or 1)), 5))
        except (TypeError, ValueError):
            group_levels = 1

        meta = RMLMetadata(
            name=name,
            display_name=display,
            category=category,
            connection=connection,
            schema=schema,
            icon=icon,
            description=description,
            namespace=namespace,
            report_type=report_type,
            doc_layout=doc_layout,
            distinct=distinct,
            group_levels=group_levels,
            raw_attrs=dict(el.attrib),
        )
        self._metadata = meta
        return meta.to_dict()

    @staticmethod
    def _parse_column_el(el, idx: int) -> "RMLColumn":
        """Parse one <column> element (shared by columns + detail columns)."""
        def get(*names: str, default: Optional[str] = None) -> Optional[str]:
            for k, v in el.attrib.items():
                if k.lower() in [n.lower() for n in names]:
                    return v.strip() if isinstance(v, str) else v
            return default

        col_id = get("id", default=str(idx))
        name = get("name", default=col_id)
        alias = get("alias", default=name)
        data_type = get("dataType", "datatype", "data_type", "type")
        expr = get("expr", "expression", default=name)
        # Parse multiple ref tables for cross-table references
        # Support attributes: refTable, reftable, ref_table (single) and
        # refTables (JSON list or comma-separated), or legacy single refs
        ref_tables_raw = get("refTables", "ref_tables", default="")
        ref_tables: List[Dict[str, Optional[str]]] = []
        if ref_tables_raw:
            # Try JSON array first: [{"table":"T1","fk":"C1","display":"D1"},...]
            try:
                import json
                parsed = json.loads(ref_tables_raw)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict):
                            tt = item.get("table") or item.get("from_table") or ""
                            ff = item.get("fk") or item.get("from_fk") or item.get("ref_fk") or ""
                            dd = item.get("display") or item.get("ref_display") or item.get("from_display") or ""
                            ref_tables.append({"table": str(tt).strip() or None, "fk": str(ff).strip() or None, "display": str(dd).strip() or None})
                        else:
                            # fallback: comma-separated "table:fk:display"
                            parts = str(item).split(":")
                            if len(parts) >= 3:
                                ref_tables.append({"table": parts[0].strip() or None, "fk": parts[1].strip() or None, "display": parts[2].strip() or None})
                            elif len(parts) >= 2:
                                ref_tables.append({"table": parts[0].strip() or None, "fk": parts[1].strip() or None, "display": None})
                    if ref_tables:
                        ref_table = ref_tables[0]["table"]
                        ref_fk = ref_tables[0]["fk"]
                        ref_display = ref_tables[0]["display"]
                    else:
                        ref_table = get("refTable", "reftable", "ref_table")
                        ref_fk = get("refFk", "reffk", "ref_fk")
                        ref_display = get("refDisplay", "refdisplay", "ref_display")
                else:
                    ref_table = get("refTable", "reftable", "ref_table")
                    ref_fk = get("refFk", "reffk", "ref_fk")
                    ref_display = get("refDisplay", "refdisplay", "ref_display")
            except Exception:
                ref_table = get("refTable", "reftable", "ref_table")
                ref_fk = get("refFk", "reffk", "ref_fk")
                ref_display = get("refDisplay", "refdisplay", "ref_display")
        else:
            ref_table = get("refTable", "reftable", "ref_table")
            ref_fk = get("refFk", "reffk", "ref_fk")
            ref_display = get("refDisplay", "refdisplay", "ref_display")
        
        # Build ref_tables list from single ref for backward compat (only if no JSON was parsed)
        if not ref_tables and ref_table and ref_fk and ref_display:
            ref_tables = [{"table": ref_table, "fk": ref_fk, "display": ref_display}]
        
        jt_raw = (get("join_type", "joinType", "join-type", "ref_type", "refType", "rel_type", "relType", "cardinality", "rel", default="") or "")
        jt = str(jt_raw).strip().lower().replace("-", "_").replace(" ", "_")
        join_type = "one_to_many" if jt in ("one_to_many", "one_many", "1_n", "1m", "many", "o2m") else "one_to_one"
        connection_id = get("connection_id", "connectionId", "connection")
        where_clause = get("where_clause", "whereClause", "where")
        icon = get("icon")
        is_amount = str(get("is_amount", "isAmount", "is-amount", default="") or "").strip().lower() in ("1", "true", "yes", "y")
        currency_field = get("currency_field", "currencyField", "currency_column", "currencyColumn", "currency")
        is_status = str(get("is_status", "isStatus", "is-status", default="") or "").strip().lower() in ("1", "true", "yes", "y")
        is_distinct = str(get("distinct", "is_distinct", "isDistinct", "is-distinct", default="") or "").strip().lower() in ("1", "true", "yes", "y")
        # Status map children: <status value="0" label="نشط" color="#16a34a"/>
        status_map: List[Dict[str, str]] = []
        try:
            for ch in list(el):
                if ch.tag is None or str(ch.tag).lower() != "status":
                    continue
                _sv = _sl = _sc = ""
                for k, v in (ch.attrib or {}).items():
                    kl = str(k).lower()
                    if kl in ("value", "val", "code"):
                        _sv = str(v)
                    elif kl in ("label", "name", "title", "text"):
                        _sl = str(v)
                    elif kl in ("color", "colour", "bg"):
                        _sc = str(v)
                if _sv == "" and ch.text and str(ch.text).strip():
                    _sv = str(ch.text).strip()
                if _sv != "" or _sl != "":
                    status_map.append({"value": _sv, "label": _sl or _sv, "color": _sc or ""})
        except Exception:
            pass
        if status_map and not is_status:
            is_status = True
        # Display formatting attrs (designer type modal)
        def _to_int(_v):
            try:
                _s = str(_v or "").strip()
                return int(_s) if _s != "" else None
            except Exception:
                return None
        max_chars = _to_int(get("max_chars", "maxChars", "max-chars", "maxlength", "max_length"))
        wrap = str(get("wrap", "text_wrap", "textWrap", default="") or "").strip().lower() in ("1", "true", "yes", "y", "normal", "wrap")
        decimals = _to_int(get("decimals", "decimal_places", "decimalPlaces", "dec", "scale"))
        if decimals is not None:
            decimals = max(0, min(decimals, 6))
        date_format = get("date_format", "dateFormat", "date-format", "format")
        date_format = str(date_format).strip() if date_format else None
        # Column classification: explicit type attr takes precedence
        explicit_type = (get("col_type", "colType", "column_type", default="") or "").lower()
        if explicit_type in ("direct", "computed", "fk_lookup", "fk-lookup", "lookup", "aggregated", "aggregate"):
            # Normalize
            if explicit_type in ("fk-lookup", "lookup"):
                explicit_type = "fk_lookup"
            elif explicit_type in ("aggregate",):
                explicit_type = "aggregated"
            col_type = explicit_type
        else:
            # Infer
            if ref_table and ref_fk and ref_display:
                col_type = "fk_lookup"
            elif expr and any(kw in expr.upper() for kw in ("COUNT(", "SUM(", "AVG(", "MAX(", "MIN(", "GROUP")):
                col_type = "aggregated"
            elif any(op in expr for op in ("+", "-", "*", "/", "||", "CASE", "DECODE", "NVL")):
                col_type = "computed"
            else:
                col_type = "direct"

        return RMLColumn(
            id=str(col_id),
            name=str(name),
            alias=str(alias),
            data_type=str(data_type) if data_type else None,
            expr=str(expr),
            ref_table=str(ref_table) if ref_table else None,
            ref_fk=str(ref_fk) if ref_fk else None,
            ref_display=str(ref_display) if ref_display else None,
            ref_tables=ref_tables,
            col_type=col_type,
            join_type=join_type,
            connection_id=str(connection_id) if connection_id else None,
            where_clause=str(where_clause) if where_clause else None,
            icon=str(icon) if icon else None,
            is_amount=is_amount,
            currency_field=str(currency_field) if currency_field else None,
            is_status=is_status,
            status_map=status_map,
            is_distinct=is_distinct,
            max_chars=max_chars,
            wrap=wrap,
            decimals=decimals,
            date_format=date_format,
            raw_attrs=dict(el.attrib),
        )

    def detail(self) -> Optional[RMLDetail]:
        """Extract <detail table master detail> + its <column> children."""
        if self._detail is not None:
            return self._detail
        el = None
        if self._root is not None:
            for e in self._root.iter():
                if e.tag.lower() == "detail":
                    el = e
                    break
        if el is None:
            return None

        def get(*names: str, default: Optional[str] = None) -> Optional[str]:
            for k, v in el.attrib.items():
                if k.lower() in [n.lower() for n in names]:
                    return v.strip() if isinstance(v, str) else v
            return default

        table = get("table", "table_source", "tableSource")
        master = get("master", "master_key", "masterKey", "link_master", "linkMaster", "master_col", "masterCol")
        detail_key = get("detail", "detail_key", "detailKey", "link_detail", "linkDetail", "detail_col", "detailCol")
        if not table or not master or not detail_key:
            return None
        rel_raw = (get("rel_type", "relType", "rel", "cardinality", default="") or "").strip().lower().replace("-", "_").replace(" ", "_")
        # detail lives on a sub-level -> default one_to_many
        rel_type = "one_to_one" if rel_raw in ("one_to_one", "one_one", "1_1", "1to1", "one") else "one_to_many"
        cols = [self._parse_column_el(c, i) for i, c in
                enumerate([c for c in list(el) if c.tag.lower() == "column"], start=1)]
        self._detail = RMLDetail(table=str(table), master=str(master), detail=str(detail_key),
                                 columns=cols, rel_type=rel_type, raw_attrs=dict(el.attrib))
        return self._detail

    def doc_template(self) -> str:
        """Extract <doc_template> print layout text ({{column_alias}} vars)."""
        if self._doc_template is not None:
            return self._doc_template
        self._doc_template = ""
        if self._root is not None:
            for e in self._root.iter():
                if e.tag.lower() in ("doc_template", "doctemplate", "document_template"):
                    self._doc_template = (e.text or "").strip()
                    break
        return self._doc_template

    def general_where(self) -> str:
        """Extract report-level <general_where> filter (ANDed into master WHERE)."""
        if getattr(self, "_general_where", None) is not None:
            return self._general_where
        self._general_where = ""
        if self._root is not None:
            for e in self._root.iter():
                if e.tag.lower() in ("general_where", "generalwhere", "report_where", "global_where"):
                    self._general_where = (e.text or "").strip()
                    break
        return self._general_where

    @staticmethod
    def _norm_rel(v: str, default: str = "") -> str:
        """Normalize one_one/one_many variants -> one_to_one / one_to_many."""
        s = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
        if s in ("one_to_one", "one_one", "1_1", "1to1", "one", "1_1_both"):
            return "one_to_one"
        if s in ("one_to_many", "one_many", "1_n", "1m", "many", "o2m", "one_to_many_both"):
            return "one_to_many"
        return default

    def _detail_table_norm(self) -> str:
        try:
            d = self.detail()
            if d and d.table:
                s = str(d.table).strip().replace('"', "")
                return (s.split(".")[-1] if "." in s else s).upper()
        except Exception:
            pass
        return ""

    def table_opts(self) -> List[Dict[str, str]]:
        """Extract <table_opts><table name conn is_default is_sub/> (designer table roles).

        Unknown to older readers — safely ignored (this parser only reads known tags).
        """
        result: List[Dict[str, str]] = []
        if self._root is None:
            return result

        def get(el, *names: str, default: str = "") -> str:
            for k, v in (el.attrib or {}).items():
                if k.lower() in [n.lower() for n in names]:
                    return v.strip() if isinstance(v, str) else v
            return default

        def flag(v: str) -> bool:
            return str(v or "").strip().lower() in ("1", "true", "yes", "y")

        cont = None
        for el in self._root.iter():
            if el.tag.lower() == "table_opts":
                cont = el
                break
        if cont is None:
            return result
        for el in list(cont):
            if el.tag.lower() != "table":
                continue
            name = get(el, "name", "table")
            if not name:
                continue
            result.append({
                "name": name,
                "conn": get(el, "conn", "conn_id", "connection_id"),
                "is_default": flag(get(el, "is_default", "isDefault", "default")),
                "is_sub": flag(get(el, "is_sub", "isSub", "sub")),
            })
        return result

    def links(self) -> List[Dict[str, str]]:
        """Extract <links><link from_table from_col to_table to_col rel_type> (data diagram).

        rel_type: one_to_one | one_to_many. Explicit value wins; otherwise auto:
        - same level (both tables only in main columns, or neither is the detail table) -> one_to_one bidirectional
        - different levels (one side is the <detail> table) -> one_to_many
        """
        result: List[Dict[str, str]] = []
        if self._root is None:
            return result
        dt_norm = self._detail_table_norm()

        def _norm(t: str) -> str:
            s = str(t or "").strip().replace('"', "")
            return (s.split(".")[-1] if "." in s else s).upper()

        for el in self._root.iter():
            if el.tag.lower() != "link":
                continue

            def get(*names: str, default: str = "") -> str:
                for k, v in el.attrib.items():
                    if k.lower() in [n.lower() for n in names]:
                        return v.strip() if isinstance(v, str) else v
                return default

            ft = get("from_table", "fromTable", "from")
            fc = get("from_col", "fromCol", "from_column", "fromColumn")
            tt = get("to_table", "toTable", "to")
            tc = get("to_col", "toCol", "to_column", "toColumn")
            fconn = get("from_conn", "fromConn", "from_connection", "fromConnection")
            tconn = get("to_conn", "toConn", "to_connection", "toConnection")
            rel_raw = get("rel_type", "relType", "rel", "cardinality", "join_type", "joinType")
            rel = self._norm_rel(rel_raw, default="")
            if ft and tt:
                if not rel and dt_norm:
                    fn, tn = _norm(ft), _norm(tt)
                    rel = "one_to_many" if (fn == dt_norm or tn == dt_norm) else "one_to_one"
                result.append({"from_table": ft, "from_col": fc, "to_table": tt, "to_col": tc,
                               "from_conn": fconn, "to_conn": tconn,
                               "rel_type": rel or "one_to_one", "relType": rel or "one_to_one"})
        return result

    def columns(self) -> List[RMLColumn]:
        """Extract dynamic report columns from <column> tags."""
        if self._columns is not None:
            return self._columns

        container = self._find_columns_container()
        if container is None:
            # Fallback: search all <column> directly under root (skip <detail> children)
            cols = []
            if self._root is not None:
                parent = {c: p for p in self._root.iter() for c in list(p)}
                for e in self._root.iter():
                    if e.tag.lower() != "column":
                        continue
                    p = parent.get(e)
                    if p is not None and p.tag.lower() == "detail":
                        continue
                    cols.append(e)
            if not cols:
                self._columns = []
                return self._columns
        else:
            cols = [e for e in container if e.tag.lower() == "column"]

        result: List[RMLColumn] = []
        for idx, el in enumerate(cols, start=1):
            result.append(self._parse_column_el(el, idx))

        self._columns = result
        return result

    def _find_fields_container(self) -> Optional[ET.Element]:
        if self._root is None:
            return None
        for el in self._root.iter():
            if el.tag.lower() == "fields":
                return el
        return None

    def fields(self) -> List[RMLField]:
        """Extract base field definitions from <fields><field>.
        Each field MUST have name == DB column name.
        <field name type conn_id table_source>
        """
        if self._fields is not None:
            return self._fields
        container = self._find_fields_container()
        if container is None:
            # Fallback: <field> directly under root (but not inside columns)
            cols = [e for e in self._root.iter() if e.tag.lower() == "field"] if self._root is not None else []
            # Exclude any <field> nested inside <columns>
            filtered = []
            for el in cols:
                inside_columns = False
                if self._root is not None:
                    for anc in self._root.iter():
                        if anc.tag.lower() == "columns" and el in list(anc):
                            inside_columns = True
                            break
                if not inside_columns:
                    filtered.append(el)
            cols = filtered
            if not cols:
                self._fields = []
                return self._fields
        else:
            cols = [e for e in container if e.tag.lower() == "field"]
        result: List[RMLField] = []
        for idx, el in enumerate(cols, start=1):
            def get(*names: str, default: Optional[str] = None) -> Optional[str]:
                for k, v in el.attrib.items():
                    if k.lower() in [n.lower() for n in names]:
                        return v.strip() if isinstance(v, str) else v
                return default
            fid = get("id", default=str(idx))
            name = get("name", default=fid)
            # name must equal DB column name — enforce non-empty, sanitize lightly
            name = str(name).strip()
            data_type = get("type", "dataType", "datatype", "data_type")
            connection_id = get("conn_id", "connection_id", "connectionId", "connection")
            table_source = get("table_source", "tableSource", "table")
            result.append(RMLField(
                id=str(fid),
                name=name,
                data_type=str(data_type) if data_type else None,
                connection_id=str(connection_id) if connection_id else None,
                table_source=str(table_source) if table_source else None,
                raw_attrs=dict(el.attrib),
            ))
        self._fields = result
        return result

    def connections(self) -> List[RMLConnection]:
        """Extract <rml_connections><connection> — auto-numbered ids referencing connections table"""
        if self._connections is not None:
            return self._connections
        result: List[RMLConnection] = []
        # find rml_connections container
        container = None
        if self._root is not None:
            for el in self._root.iter():
                if el.tag.lower() == "rml_connections":
                    container = el
                    break
        cols = []
        if container is not None:
            cols = [e for e in container if e.tag.lower() == "connection"]
        else:
            # fallback: search direct <connection> under root (without wrapper) but not inside columns
            if self._root is not None:
                for el in self._root.iter():
                    if el.tag.lower() == "connection" and el not in [c for col in (self._columns or []) for c in []]:
                        # ensure not inside columns
                        parent_is_columns = False
                        for anc in self._root.iter():
                            if el in list(anc):
                                if anc.tag.lower() == "columns":
                                    parent_is_columns = True
                                    break
                        if not parent_is_columns:
                            cols.append(el)
                # deduplicate
                cols = list({id(c): c for c in cols}.values()) if cols else []
                # If still found via direct search, but our container was None, we already have
                # Actually simpler: if container is None, also check for <rml_connections> lowercase
                pass
        for idx, el in enumerate(cols, start=1):
            def get(*names, default=None):
                for k, v in el.attrib.items():
                    if k.lower() in [n.lower() for n in names]:
                        return v.strip() if isinstance(v, str) else v
                return default
            cid = get("id", default=str(idx))
            conn_id = get("connection_id", "connectionId", "connection", default=cid)
            result.append(RMLConnection(id=str(cid), connection_id=str(conn_id), raw_attrs=dict(el.attrib)))
        self._connections = result
        return result

    def charts(self) -> List[RMLChart]:
        """Extract <rml_chart><chart> — multiple charts per report"""
        if self._charts is not None:
            return self._charts
        result: List[RMLChart] = []
        container = None
        if self._root is not None:
            for el in self._root.iter():
                if el.tag.lower() in ("rml_chart", "rml_charts", "charts"):
                    # avoid picking <columns> which also could be named charts? but we check for chart children
                    # ensure it contains <chart> children
                    if any(child.tag.lower() == "chart" for child in list(el)):
                        container = el
                        break
        cols = []
        if container is not None:
            cols = [e for e in container if e.tag.lower() == "chart"]
        else:
            # fallback: search all <chart> directly
            if self._root is not None:
                cols = [e for e in self._root.iter() if e.tag.lower() == "chart"]
        for idx, el in enumerate(cols, start=1):
            def get(*names, default=None):
                for k, v in el.attrib.items():
                    if k.lower() in [n.lower() for n in names]:
                        return v.strip() if isinstance(v, str) else v
                return default
            cid = get("id", default=str(idx))
            ctype = get("type", default="bar")
            x = get("x", "x_field", "xField")
            y = get("y", "y_field", "yField")
            y2 = get("y2", "y_field2", "yField2", "second", "series2")
            title = get("title", "name", default=f"مخطط {cid}")
            result.append(RMLChart(id=str(cid), type=str(ctype).lower(), x=str(x) if x else None, y=str(y) if y else None, y2=str(y2) if y2 else None, title=str(title) if title else None, raw_attrs=dict(el.attrib)))
        self._charts = result
        return result

    def rules(self) -> List[RMLRule]:
        """Extract computed business rules from <rules><rule>."""
        if self._rules is not None:
            return self._rules
        container = None
        if self._root is not None:
            for el in self._root.iter():
                if el.tag.lower() == "rules":
                    container = el
                    break
        cols = []
        if container is not None:
            cols = [e for e in container if e.tag.lower() == "rule"]
        elif self._root is not None:
            # fallback: <rule> directly under root (not inside columns/fields)
            for el in self._root.iter():
                if el.tag.lower() != "rule":
                    continue
                inside = False
                for anc in self._root.iter():
                    if anc.tag.lower() in ("columns", "fields") and el in list(anc):
                        inside = True
                        break
                if not inside:
                    cols.append(el)
            cols = list({id(c): c for c in cols}.values()) if cols else []
        result: List[RMLRule] = []
        for idx, el in enumerate(cols, start=1):
            def get(*names: str, default: Optional[str] = None) -> Optional[str]:
                for k, v in el.attrib.items():
                    if k.lower() in [n.lower() for n in names]:
                        return v.strip() if isinstance(v, str) else v
                return default

            rid = get("id", default=str(idx))
            name = get("name", default=rid)
            expr = get("expr", "expression", default="")
            connection_id = get("connection_id", "connectionId", "connection", "conn_id")
            rtype = get("type")
            message = get("message", "msg")
            value = get("value")
            if value is None and el.text and el.text.strip():
                value = el.text.strip()
            display = get("display", "displayName", "title") or ""
            icon = get("icon") or ""
            description = get("description", "desc") or ""
            # <sources><source field="emp_no"/></sources>
            sources: List[str] = []
            for ch in list(el):
                if ch.tag.lower() == "sources":
                    for s in list(ch):
                        if s.tag.lower() == "source":
                            for k, v in s.attrib.items():
                                if k.lower() in ("field", "name") and (v or "").strip():
                                    sources.append(v.strip())
            # <variables><variable name display type options><opt/></variables>
            variables: List[RMLRuleVariable] = []
            for ch in list(el):
                if ch.tag.lower() == "variables":
                    for v in list(ch):
                        if v.tag.lower() != "variable":
                            continue
                        vget = {k.lower(): (vv.strip() if isinstance(vv, str) else vv)
                                for k, vv in v.attrib.items()}
                        vname = (vget.get("name") or "").strip()
                        if not vname:
                            continue
                        vtype = (vget.get("type") or "text").strip().lower()
                        if vtype not in ("text", "number", "date", "time",
                                         "expression", "boolean", "choice"):
                            vtype = "text"
                        opts: List[str] = []
                        if (vget.get("options") or "").strip():
                            opts = [o.strip() for o in str(vget.get("options")).split(",")
                                    if o.strip()]
                        for o in list(v):
                            if o.tag.lower() == "opt" and (o.text or "").strip():
                                opts.append(o.text.strip())
                        variables.append(RMLRuleVariable(
                            name=vname,
                            display=(vget.get("display") or vname),
                            type=vtype, options=opts))
            # <policies><policy id name><match><on field><v/></on></match>
            #   <values><v name>value</v></values></policy></policies>
            policies: List[RMLRulePolicy] = []
            for ch in list(el):
                if ch.tag.lower() == "policies":
                    for p in list(ch):
                        if p.tag.lower() != "policy":
                            continue
                        pget = {k.lower(): (vv.strip() if isinstance(vv, str) else vv)
                                for k, vv in p.attrib.items()}
                        pid = (pget.get("id") or pget.get("name") or f"p{len(policies)+1}")
                        try:
                            _prio = int(str(pget.get("priority", "100")).strip() or 100)
                        except (TypeError, ValueError):
                            _prio = 100
                        _is_def = str(pget.get("is_default", pget.get("default", ""))).strip().lower() in (
                            "1", "true", "t", "yes", "y", "صح", "نعم")
                        match: Dict[str, List[str]] = {}
                        values: Dict[str, str] = {}
                        for pc in list(p):
                            if pc.tag.lower() == "match":
                                for on in list(pc):
                                    if on.tag.lower() != "on":
                                        continue
                                    of = ""
                                    for k, vv in on.attrib.items():
                                        if k.lower() in ("field", "name"):
                                            of = (vv or "").strip()
                                    if not of:
                                        continue
                                    vals = [(vv.text or "").strip() for vv in list(on)
                                            if vv.tag.lower() == "v" and (vv.text or "").strip()]
                                    if vals:
                                        match[of] = vals
                            elif pc.tag.lower() == "values":
                                for vv in list(pc):
                                    if vv.tag.lower() != "v":
                                        continue
                                    vn = ""
                                    for k, vvv in vv.attrib.items():
                                        if k.lower() == "name":
                                            vn = (vvv or "").strip()
                                    if vn:
                                        values[vn] = (vv.text or "").strip()
                        policies.append(RMLRulePolicy(id=str(pid),
                            name=str(pget.get("name") or pid),
                            match=match, values=values,
                            priority=_prio, is_default=_is_def))
            result.append(RMLRule(
                id=str(rid), name=str(name), expr=str(expr or ""),
                connection_id=str(connection_id) if connection_id else None,
                type=str(rtype) if rtype else None,
                message=str(message) if message else None,
                value=str(value) if value is not None else None,
                display=str(display or ""), icon=str(icon or ""),
                description=str(description or ""),
                sources=sources, variables=variables, policies=policies,
                raw_attrs=dict(el.attrib),
            ))
        self._rules = result
        return result

    def to_dict(self) -> Dict[str, Any]:
        """Full RML as dict for API."""
        return {
            "metadata": self.rpt_metadata(),
            "connections": [c.to_dict() for c in self.connections()],
            "charts": [c.to_dict() for c in self.charts()],
            "fields": [f.to_dict() for f in self.fields()],
            "columns": [c.to_dict() for c in self.columns()],
            "rules": [r.to_dict() for r in self.rules()],
            "groups": [g.to_dict() for g in self.groups()],
        }

    def groups(self) -> List[RMLGroup]:
        """Extract <groups><group name order><column alias/>...] — header spanning."""
        if self._groups is not None:
            return self._groups
        result: List[RMLGroup] = []
        container = None
        if self._root is not None:
            for el in self._root.iter():
                if el.tag.lower() == "groups":
                    container = el
                    break
        grps = []
        if container is not None:
            grps = [e for e in container if e.tag.lower() == "group"]
        for idx, el in enumerate(grps, start=1):
            def get(*names: str, default: Optional[str] = None) -> Optional[str]:
                for k, v in el.attrib.items():
                    if k.lower() in [n.lower() for n in names]:
                        return v.strip() if isinstance(v, str) else v
                return default
            gid = get("id", default=str(idx))
            name = get("name", "title", default=f"مجموعة {gid}")
            try:
                order = int(str(get("order", "position", default=str(idx)) or idx))
            except (TypeError, ValueError):
                order = idx
            try:
                level = max(1, min(int(str(get("level", default="1")) or 1), 5))
            except (TypeError, ValueError):
                level = 1
            members: List[str] = []
            for ch in list(el):
                if ch.tag.lower() not in ("column", "col", "member"):
                    continue
                alias = None
                for k, v in ch.attrib.items():
                    if k.lower() in ("alias", "name", "column", "col", "field"):
                        alias = v.strip() if isinstance(v, str) else v
                        break
                if alias is None and ch.text and ch.text.strip():
                    alias = ch.text.strip()
                if alias and alias not in members:
                    members.append(alias)
            result.append(RMLGroup(id=str(gid), name=str(name), order=order, columns=members, level=level))
        result.sort(key=lambda g: (g.order, g.id))
        self._groups = result
        return result

    def __repr__(self) -> str:
        try:
            meta = self.rpt_metadata()
            return f"<RMLReportCompiler name={meta.get('name')} cols={len(self.columns())}>"
        except Exception:
            return "<RMLReportCompiler unparsed>"
