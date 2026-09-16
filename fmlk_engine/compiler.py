"""
FMLK Compiler — Production Ready
Parses FMLK XML for Forms: <fml_metadata>, <tabs>, <fields>, positioning, input methods.
"""
from __future__ import annotations
import pathlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import re

try:
    from .field_types import normalize_input_type, CONFIG_ATTRS
except ImportError:  # fallback when used as standalone script
    from field_types import normalize_input_type, CONFIG_ATTRS

# ── visibleIf evaluator (mirrors forms_player fmlkVisibleCheck) ─────────────
# Grammar: expr := or; or := and ("||" and)*; and := term ("&&" term)*;
# term := name ("==" | "!=") value, value may be "quoted" | 'quoted' | bare.
_VISIBLE_TERM = re.compile(r"""^([A-Za-z0-9_]+)\s*(==|!=)\s*(?:"([^"]*)"|'([^']*)'|(.+))$""")

def _visible_val(v: Any) -> str:
    """Stringify a submitted value the way the frontend compares it (JS String())."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        try:
            import json as _json
            return _json.dumps(v, ensure_ascii=False)
        except Exception:
            return str(v)
    return str(v)


def evaluate_visible(cond: Any, data: Optional[Dict[str, Any]] = None) -> bool:
    """True if a visibleIf condition holds for the given submitted values.

    - Empty/missing condition → visible.
    - Unparseable term → treated as visible (same fail-open as the frontend).
    """
    c = str(cond or "").strip()
    if not c:
        return True
    data = data or {}

    def one(expr: str) -> bool:
        m = _VISIBLE_TERM.match(expr.strip())
        if not m:
            return True
        fname, op, dq, sq, bare = m.groups()
        want = dq if dq is not None else (sq if sq is not None else (bare or "").strip())
        got = _visible_val(data.get(fname)).strip()
        eq = got == want
        return eq if op == "==" else not eq

    def all_of(expr: str) -> bool:
        return all(one(t) for t in str(expr).split("&&") if t.strip())

    try:
        return any(all_of(o) for o in c.split("||") if o.strip())
    except Exception:
        return True

@dataclass
class FMLKMetadata:
    name: str
    display_name: str
    category: Optional[str] = None
    connection: Optional[str] = None
    schema: Optional[str] = None
    table: Optional[str] = None  # main table for form (programmatic)
    model_type: str = "form"  # form | rule — نوع الموديل
    description: Optional[str] = None
    icon: Optional[str] = None
    raw_attrs: Dict[str, str] = field(default_factory=dict)
    def to_dict(self):
        return {
            "name": self.name,
            "displayName": self.display_name,
            "displayname": self.display_name,
            "category": self.category,
            "connection": self.connection,
            "schema": self.schema,
            "table": self.table,
            "table_ar": self.display_name,  # اسم الجدول بالعربي
            "table_en": self.table or self.name,  # اسم الجدول البرمجي
            "model_type": self.model_type,
            "modelType": self.model_type,
            "description": self.description,
            "icon": self.icon,
        }

@dataclass
class FMLKField:
    id: str
    name: str
    alias: str  # label
    data_type: Optional[str] = None  # VARCHAR, INTEGER, NUMERIC, TEXT, DATE, TIMESTAMP, BOOLEAN...
    input_type: str = "text"  # text, number, date, select, file, phone, email, etc.
    required: bool = False
    nullable: bool = True  # قيد NULL في DB
    editable: bool = True  # قابل للتحرير في الواجهة (عكس readonly)
    primary_key: bool = False  # المفتاح الرئيسي
    default: Optional[str] = None  # قيمة افتراضية ثابتة
    formula: Optional[str] = None  # صيغة حسابية SQL مع مراجع [field] — تُحسب عند الإدخال
    placeholder: Optional[str] = None
    visible_if: Optional[str] = None  # شرط الإظهار: field==value && field2!=v2 || ...
    tab: Optional[str] = None
    category: Optional[str] = None  # group within tab (e.g., Work phones, Personal information)
    position: Optional[str] = None  # e.g., left, right, full
    col_span: int = 1
    row_span: int = 1
    ref_table: Optional[str] = None
    ref_fk: Optional[str] = None
    ref_display: Optional[str] = None
    icon: Optional[str] = None  # أيقونة المدخل (fa-*) تُعرض بجانب الاسم في المشغل
    destination: Optional[str] = None  # الوجهة: main (الجدول الأساسي) أو اسم جدول متفرع
    options: List[str] = field(default_factory=list)  # for select (literal values only)
    options_source: List[Dict[str, str]] = field(default_factory=list)  # [{table, schema, column}] from [table.column] refs
    validation: Dict[str, Any] = field(default_factory=dict)  # Validation Engine: regex/pattern, min_length, max_length, min, max
    config: Dict[str, Any] = field(default_factory=dict)  # relational widget config (parent_field, sub_fields, ...)
    raw_attrs: Dict[str, str] = field(default_factory=dict)
    def is_visible(self, data: Optional[Dict[str, Any]] = None) -> bool:
        """الحقل ظاهر لقيم البيانات المعطاة؟ (visibleIf غير محقق = مخفي)."""
        return evaluate_visible(self.visible_if, data)
    def effective_required(self, data: Optional[Dict[str, Any]] = None) -> bool:
        """مطلوب فعلاً؟ — خاصية required تسقط حال الإخفاء (visibleIf غير محقق)."""
        return bool(self.required) and self.is_visible(data)
    def to_dict(self):
        base = {"id": self.id, "name": self.name, "alias": self.alias, "dataType": self.data_type, "inputType": self.input_type, "required": self.required, "nullable": self.nullable, "editable": self.editable, "readonly": (not self.editable), "primary_key": self.primary_key, "primaryKey": self.primary_key, "default": self.default, "defaultValue": self.default, "formula": self.formula, "calc_expr": self.formula, "placeholder": self.placeholder, "visibleIf": self.visible_if, "visible_if": self.visible_if, "tab": self.tab, "category": self.category, "position": self.position, "colSpan": self.col_span, "rowSpan": self.row_span, "refTable": self.ref_table, "refFk": self.ref_fk, "refDisplay": self.ref_display, "icon": self.icon, "destination": self.destination, "options": self.options, "options_source": self.options_source, "optionsSource": self.options_source, "validation": self.validation, "validationRules": self.validation, "config": self.config}
        # Foreign Keys: expose dynamic endpoint for frontend to fetch reference data
        if self.ref_table:
            base["refEndpoint"] = f"/api/fmlk/lookup?field={self.name}&table={self.ref_table}"
            base["lookupEndpoint"] = base["refEndpoint"]
        # Frontend validation pass-through (mirrors validation dict for direct use)
        if self.validation:
            # flatten for convenience: pattern -> regex normalization
            if "regex" in self.validation and "pattern" not in self.validation:
                base["pattern"] = self.validation["regex"]
            if "pattern" in self.validation:
                base["pattern"] = self.validation["pattern"]
        return base

@dataclass
class FMLKTab:
    id: str
    name: str
    alias: str
    sort_order: int = 0
    visible_if: Optional[str] = None  # شرط إظهار التبويب — نفس صيغة الحقول
    raw_attrs: Dict[str, str] = field(default_factory=dict)
    def to_dict(self): return {"id": self.id, "name": self.name, "alias": self.alias, "sortOrder": self.sort_order, "visibleIf": self.visible_if, "visible_if": self.visible_if}

@dataclass
class FMLKDetail:
    """جدول متفرع one-to-many يُعرض كشبكة تفاصيل أسفل النموذج."""
    table: str
    alias: str = ""
    master: str = ""  # عمود الجدول الأساسي (PK)
    detail: str = ""  # عمود الربط في الجدول المتفرع (FK)
    rel_type: str = "one_to_many"
    columns: List[Dict[str, Any]] = field(default_factory=list)  # [{name, alias, data_type, input_type}]
    raw_attrs: Dict[str, str] = field(default_factory=dict)
    def to_dict(self):
        return {"table": self.table, "alias": self.alias or self.table, "master": self.master,
                "detail": self.detail, "rel_type": self.rel_type, "columns": self.columns}

@dataclass
class FMLKAction:
    """زر مخصص في عمود الإجراءات — يُنفذ على السجل المحدد عبر endpoint ويعرض render."""
    name: str
    label: str
    endpoint: str = ""
    icon: str = "fa-bolt"
    badge_color: str = "#4f46e5"
    render: str = ""  # ملف مودال منفصل (نسبي لمجلد التطبيق) يعرض نتيجة التنفيذ
    level: str = "record"  # record: زر لكل سجل | view: زر عام في الشريط العلوي | form: زر للفورم بأكمله
    replace: str = "new"  # للفورم: زر جديد أم بدل زر موجود (add/edit/delete/save)
    raw_attrs: Dict[str, str] = field(default_factory=dict)
    def to_dict(self):
        return {"name": self.name, "label": self.label, "endpoint": self.endpoint,
                "icon": self.icon, "badge_color": self.badge_color, "badgeColor": self.badge_color,
                "render": self.render, "level": self.level, "replace": self.replace}

class FMLKFormCompiler:
    """
    Parses FMLK XML:
    <fml>
      <fml_metadata name="hr_form" displayName="HR Form" table="employees" .../>
      <tabs><tab id="basic" name="Basic information" alias="البيانات الأساسية"/>...</tabs>
      <fields>
        <field id="1" name="first_name" alias="اسم First" inputType="text" tab="basic" category="Personal information" position="left" required="true"/>
      </fields>
    </rml>
    Provides fml_metadata() and fields() + tabs()
    """
    def __init__(self, path: str | pathlib.Path | None = None, *, xml_text: str | None = None):
        self.path = pathlib.Path(path) if path else None
        self._xml_text = xml_text
        self._root: Optional[ET.Element] = None
        self._metadata: Optional[FMLKMetadata] = None
        self._fields: Optional[List[FMLKField]] = None
        self._tabs: Optional[List[FMLKTab]] = None
        self._actions: Optional[List[FMLKAction]] = None
        self._details: Optional[List[FMLKDetail]] = None
        if path or xml_text:
            self._parse()

    @classmethod
    def from_string(cls, xml_text: str): return cls(xml_text=xml_text)
    @classmethod
    def from_file(cls, path): return cls(path=path)

    def _load_xml(self) -> str:
        if self._xml_text is not None: return self._xml_text
        if self.path and self.path.exists(): return self.path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"FMLK file not found: {self.path}")

    def _parse(self):
        raw = self._load_xml().lstrip("\ufeff")
        try:
            self._root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise ValueError(f"Invalid FMLK XML at line {e.position[0]}: {e}") from e

    def _find(self, tag: str) -> Optional[ET.Element]:
        if self._root is None: return None
        t = tag.lower()
        for el in self._root.iter():
            if el.tag.lower() == t: return el
        return None

    def fml_metadata(self) -> Dict[str, Any]:
        if self._metadata: return self._metadata.to_dict()
        el = self._find("fml_metadata")
        if el is None:
            el = self._find("rpt_metadata")  # fallback
        if el is None:
            raise ValueError("Missing <fml_metadata> in FMLK")
        def get(*names, default=None):
            for k,v in el.attrib.items():
                if k.lower() in [n.lower() for n in names]: return v.strip()
            return default
        name = get("name")
        if not name: raise ValueError("<fml_metadata> missing 'name'")
        display = get("displayName","displayname","display_name", default=name)
        mt = (get("model_type", "modelType", "model-type", default="form") or "form").strip().lower()
        if mt not in ("form", "rule"):
            mt = "form"
        meta = FMLKMetadata(name=name, display_name=display, category=get("category"), connection=get("connection"), schema=get("schema"), table=get("table"), model_type=mt, description=get("description","desc"), icon=get("icon"), raw_attrs=dict(el.attrib))
        self._metadata = meta
        return meta.to_dict()

    def tabs(self) -> List[FMLKTab]:
        if self._tabs is not None: return self._tabs
        tabs_el = self._find("tabs")
        result: List[FMLKTab] = []
        if tabs_el is not None:
            for idx, el in enumerate([e for e in tabs_el if e.tag.lower()=="tab"], start=1):
                def get(*names, default=None):
                    for k,v in el.attrib.items():
                        if k.lower() in [n.lower() for n in names]: return v
                    return default
                tid = get("id", default=str(idx))
                name = get("name", default=tid)
                alias = get("alias", default=name)
                order = int(get("sort_order", "sortOrder", default=str(idx)) or idx)
                tvis = get("visibleIf", "visible_if", "visible-if", "showIf", "show_if", "show-if")
                result.append(FMLKTab(id=str(tid), name=str(name), alias=str(alias), sort_order=order, visible_if=(str(tvis).strip() if tvis else None), raw_attrs=dict(el.attrib)))
        # If no <tabs>, infer from fields' tab values
        if not result:
            fields = self.fields()
            tab_names = sorted({f.tab for f in fields if f.tab})
            for idx, tn in enumerate(tab_names, start=1):
                result.append(FMLKTab(id=tn, name=tn, alias=tn, sort_order=idx))
        self._tabs = sorted(result, key=lambda x: x.sort_order)
        return self._tabs

    def details(self) -> List[FMLKDetail]:
        """الجداول المتفرعة one-to-many من <details><detail table master detail>."""
        if self._details is not None: return self._details
        result: List[FMLKDetail] = []
        det_el = self._find("details")
        if det_el is not None:
            for el in [e for e in det_el if e.tag.lower() == "detail"]:
                def get(*names, default=None):
                    for k, v in el.attrib.items():
                        if k.lower() in [n.lower() for n in names]: return v
                    return default
                tbl = (get("table") or "").strip()
                if not tbl:
                    continue
                cols: List[Dict[str, Any]] = []
                for cc in list(el):
                    if cc.tag.lower() not in ("columns", "column", "field"):
                        continue
                    _items = [c for c in list(cc)] if cc.tag.lower() == "columns" else [cc]
                    for c in _items:
                        if c.tag.lower() not in ("column", "field"):
                            continue
                        def cg(*names, default=None):
                            for k, v in c.attrib.items():
                                if k.lower() in [n.lower() for n in names]: return v
                            return default
                        _nm = (cg("name") or "").strip()
                        if not _nm:
                            continue
                        cols.append({"name": _nm, "alias": (cg("alias", "label") or _nm).strip(),
                                     "data_type": (cg("dataType", "datatype", "data_type") or "VARCHAR").strip().upper(),
                                     "input_type": normalize_input_type(cg("inputType", "input_type", "type", default="text"))})
                result.append(FMLKDetail(
                    table=tbl, alias=(get("alias", "label") or tbl).strip(),
                    master=(get("master", "master_col", "masterCol") or "").strip(),
                    detail=(get("detail", "detail_col", "detailCol", "fk") or "").strip(),
                    rel_type=(get("rel_type", "relType", "rel-type") or "one_to_many").strip().lower(),
                    columns=cols, raw_attrs=dict(el.attrib)))
        self._details = result
        return result

    def fields(self) -> List[FMLKField]:
        if self._fields is not None: return self._fields
        # Find fields container
        container = self._find("fields")
        if container is None:
            container = self._find("columns")  # fallback
        cols = [e for e in (container if container is not None else self._root or []) if hasattr(e, 'tag') and e.tag.lower() in ("field","column")]
        # If still none, search iter
        if not cols and self._root is not None:
            cols = [e for e in self._root.iter() if e.tag.lower() in ("field","column")]
        result: List[FMLKField] = []
        for idx, el in enumerate(cols, start=1):
            def get(*names, default=None):
                for k,v in el.attrib.items():
                    if k.lower() in [n.lower() for n in names]: return v
                return default
            fid = get("id", default=str(idx))
            name = get("name", default=fid)
            alias = get("alias", "label", default=name)
            data_type = get("dataType","datatype","data_type")
            input_type = normalize_input_type(get("inputType","input_type","type", default="text"))
            required = str(get("required", default="false")).lower() in ("true","1","yes")
            nullable = str(get("nullable", default="true" if not required else "false")).lower() in ("true","1","yes")
            editable = not (str(get("editable", default="true")).lower() in ("false","0","no"))
            if str(get("readonly", default="false")).lower() in ("true","1","yes"):
                editable = False
            primary_key = str(get("primary_key","primaryKey","pk", default="false")).lower() in ("true","1","yes")
            default_val = get("default","defaultValue")
            formula = get("formula","calc_expr","calcExpr","calc-expression")
            placeholder = get("placeholder")
            visible_if = get("visibleIf","visible_if","visible-if","showIf","show_if","show-if")
            tab = get("tab","tabId","tab_id")
            category = get("category","group","section")
            position = get("position","pos","layout")
            col_span = int(get("colSpan","col_span","colspan", default="1") or 1)
            row_span = int(get("rowSpan","row_span","rowspan", default="1") or 1)
            ref_table = get("refTable","reftable","ref_table")
            ref_fk = get("refFk","reffk","ref_fk")
            ref_display = get("refDisplay","refdisplay","ref_display")
            icon = get("icon","iconCls","icon_class")
            destination = get("destination","dest","dest_table","target")
            # ── Validation Engine: parse <validation> child or field attributes ──
            validation_rules: Dict[str, Any] = {}
            # 1) child <validation> element (e.g., <field><validation regex="..." min_length="2" max_length="50"/></field>)
            for child in list(el):
                if child.tag.lower() == "validation":
                    for k, v in child.attrib.items():
                        nk = k.lower()
                        if nk in ("regex",): nk = "pattern"
                        elif nk in ("minlength", "min_length", "min-length"): nk = "min_length"
                        elif nk in ("maxlength", "max_length", "max-length"): nk = "max_length"
                        elif nk in ("min_value", "minvalue"): nk = "min"
                        elif nk in ("max_value", "maxvalue"): nk = "max"
                        # coerce integers
                        if nk in ("min_length", "max_length") and str(v).strip().isdigit():
                            validation_rules[nk] = int(str(v).strip())
                        elif nk in ("min", "max") and str(v).strip().replace(".","",1).replace("-","",1).isdigit():
                            try:
                                validation_rules[nk] = float(v) if "." in str(v) else int(v)
                            except: validation_rules[nk] = v
                        else:
                            validation_rules[nk] = v
                    if child.text and child.text.strip():
                        # text content as regex if not already set
                        if "pattern" not in validation_rules:
                            validation_rules["pattern"] = child.text.strip()
            # 2) validation attributes directly on <field>
            for attr_key in ["regex", "pattern", "min_length", "max_length", "minLength", "maxLength", "min", "max", "min_value", "max_value"]:
                v = get(attr_key)
                if v is not None:
                    nk = attr_key.lower()
                    if nk == "regex": nk = "pattern"
                    elif nk in ("minlength",): nk = "min_length"
                    elif nk in ("maxlength",): nk = "max_length"
                    elif nk in ("min_value", "minvalue"): nk = "min"
                    elif nk in ("max_value", "maxvalue"): nk = "max"
                    if nk in ("min_length", "max_length") and str(v).strip().isdigit():
                        validation_rules[nk] = int(str(v).strip())
                    elif nk in ("min", "max") and str(v).strip().replace(".","",1).replace("-","",1).isdigit():
                        try:
                            validation_rules[nk] = float(v) if "." in str(v) else int(v)
                        except: validation_rules[nk] = v
                    else:
                        validation_rules[nk] = v
            # legacy generic validation="..." attribute
            if not validation_rules and get("validation"):
                gv = get("validation")
                # assume regex if looks like pattern, else keep as pattern
                validation_rules["pattern"] = gv
            # options for select — literal strings, or {value,label} when
            # <option value="x">label</option> carries a distinct label
            opts = []
            # Check <options><option> children
            for child in list(el):
                if child.tag.lower() == "options":
                    for opt in child:
                        if opt.tag.lower() == "option":
                            _t = (opt.text or "").strip()
                            _v = (opt.get("value") or "").strip()
                            _c = (opt.get("color") or "").strip()
                            if _c and (_t or _v):
                                opts.append({"value": _v or _t, "label": _t or _v, "color": _c})
                            elif _t and _v and _t != _v:
                                opts.append({"value": _v, "label": _t})
                            else:
                                opts.append(_t or _v)
                elif child.tag.lower() == "option":
                    _t = (child.text or "").strip()
                    _v = (child.get("value") or "").strip()
                    _c = (child.get("color") or "").strip()
                    if _c and (_t or _v):
                        opts.append({"value": _v or _t, "label": _t or _v, "color": _c})
                    elif _t and _v and _t != _v:
                        opts.append({"value": _v, "label": _t})
                    else:
                        opts.append(_t or _v)
            # Also check attribute options="a,b,c" (may include [table.column] refs)
            if not opts and get("options"):
                opts = [o.strip() for o in get("options").split(",") if o.strip()]
            # Split [schema.table.column] / [table.column] refs from literal options
            # (dict options {value,label} always stay literal)
            opts_literals: List[str] = []
            opts_source: List[Dict[str, str]] = []
            for _o in opts:
                if isinstance(_o, dict):
                    opts_literals.append(_o)
                    continue
                _m = re.fullmatch(r"\[([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){1,2})\]", _o.strip())
                if _m:
                    _parts = _m.group(1).split(".")
                    if len(_parts) == 2:
                        _tbl, _col = _parts
                        _sch = ""
                    else:
                        _sch, _tbl, _col = _parts
                    opts_source.append({"table": _tbl, "schema": _sch, "column": _col, "ref": _o.strip()})
                else:
                    opts_literals.append(_o)
            opts = opts_literals
            # <formula> child element (calc expression with [field] refs)
            for child in list(el):
                if child.tag.lower() == "formula" and child.text and child.text.strip():
                    formula = child.text.strip()
            # Relational widget config (extra attributes per input type)
            config: Dict[str, Any] = {}
            for cfg_key in CONFIG_ATTRS.get(input_type, []):
                v = get(cfg_key, cfg_key.lower(), cfg_key.upper())
                if v is not None and str(v).strip() != "":
                    config[cfg_key] = v
            if formula and "calc_expr" not in config:
                config["calc_expr"] = formula
            result.append(FMLKField(id=str(fid), name=str(name), alias=str(alias), data_type=str(data_type) if data_type else None, input_type=input_type, required=required, nullable=nullable, editable=editable, primary_key=primary_key, default=default_val, formula=formula, placeholder=placeholder, visible_if=(str(visible_if).strip() if visible_if else None), tab=tab, category=category, position=position, col_span=col_span, row_span=row_span, ref_table=str(ref_table) if ref_table else None, ref_fk=str(ref_fk) if ref_fk else None, ref_display=str(ref_display) if ref_display else None, icon=(str(icon).strip() if icon else None), destination=(str(destination).strip() if destination else None), options=opts, options_source=opts_source, validation=validation_rules, config=config, raw_attrs=dict(el.attrib)))
        self._fields = result
        return result

    def actions(self) -> List[FMLKAction]:
        """أزرار مخصصة من <custom_actions><action name label endpoint icon badge_color/>."""
        if self._actions is not None:
            return self._actions
        result: List[FMLKAction] = []
        container = self._find("custom_actions")
        els = [e for e in container if e.tag.lower() == "action"] if container is not None else []
        if not els and self._root is not None:
            # fallback: <action> مباشرة تحت الجذر
            els = [e for e in self._root if e.tag.lower() == "action"]
        for idx, el in enumerate(els, start=1):
            def get(*names, default=None):
                for k, v in el.attrib.items():
                    if k.lower() in [n.lower() for n in names]:
                        return v
                return default
            name = get("name", default=f"action{idx}")
            ep = (get("endpoint", default="") or "").strip()
            # أمان: مسار نسبي فقط
            if ep and not ep.startswith("/"):
                ep = ""
            rn = (get("render", default="") or "").strip()
            # أمان: ملف html نسبي داخل مجلد التطبيق فقط
            if rn and (not rn.endswith(".html") or ".." in rn or rn.startswith("/") or ":" in rn):
                rn = ""
            lv = (get("level", default="record") or "record").strip().lower()
            if lv not in ("record", "view", "form"):
                lv = "record"
            rp = (get("replace", default="new") or "new").strip().lower()
            if rp not in ("new", "add", "edit", "delete", "save"):
                rp = "new"
            result.append(FMLKAction(
                name=str(name), label=str(get("label", "lable", default=name)),
                endpoint=ep, icon=str(get("icon", default="fa-bolt")),
                badge_color=str(get("badge_color", "badgeColor", "badge-color", default="#4f46e5")),
                render=rn, level=lv, replace=rp,
                raw_attrs=dict(el.attrib)))
        self._actions = result
        return result

    def to_dict(self):
        return {"metadata": self.fml_metadata(), "tabs": [t.to_dict() for t in self.tabs()], "fields": [f.to_dict() for f in self.fields()], "details": [d.to_dict() for d in self.details()], "actions": [a.to_dict() for a in self.actions()]}

    def __repr__(self):
        try:
            m=self.fml_metadata()
            return f"<FMLKFormCompiler name={m.get('name')} fields={len(self.fields())} tabs={len(self.tabs())}>"
        except: return "<FMLKFormCompiler unparsed>"
