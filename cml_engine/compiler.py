"""
CML Compiler — Production Ready
Parses CML XML for Settings: <cml_metadata>, <controls>, <rules>.

File layout:
<cml>
  <cml_metadata name="companies" displayName="الشركات" category="النظام"
                icon="fa-building" description="..." table="sys_companies"
                schema="SYS" connection="ORCL_PROD" scope="system" app="settings"/>
  <controls>
    <control id="1" name="company_code" alias="رمز الشركة" inputType="text"
             dataType="VARCHAR" required="true" category="أساسية" ...>
      <validation regex="..." min_length="2" max_length="20"/>
      <options><option>...</option></options>
    </control>
  </controls>
  <rules>
    <rule id="1" name="code_unique" type="unique" target="company_code"
          message="رمز الشركة يجب أن يكون فريداً"/>
    <rule id="2" name="name_required" type="required" target="company_name" message="..."/>
  </rules>
</cml>
"""
from __future__ import annotations
import pathlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class CMLMetadata:
    name: str
    display_name: str
    category: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    table: Optional[str] = None
    schema: Optional[str] = None
    connection: Optional[str] = None
    scope: str = "system"  # system | app
    app: Optional[str] = None  # app folder name when scope == app
    namespace: Optional[str] = None  # addressable name for cross-file ns.rule references
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "displayName": self.display_name,
            "displayname": self.display_name,
            "category": self.category,
            "icon": self.icon,
            "description": self.description,
            "table": self.table,
            "schema": self.schema,
            "connection": self.connection,
            "scope": self.scope,
            "app": self.app,
            "namespace": self.namespace,
        }


@dataclass
class CMLControl:
    id: str
    name: str  # must equal DB/setting key
    alias: str
    input_type: str = "text"  # text, number, date, time, datetime, select, boolean, email, phone, file, textarea, password
    data_type: Optional[str] = None
    required: bool = False
    default: Optional[str] = None
    placeholder: Optional[str] = None
    category: Optional[str] = None
    options: List[str] = field(default_factory=list)
    ref_table: Optional[str] = None
    ref_fk: Optional[str] = None
    ref_display: Optional[str] = None
    validation: Dict[str, Any] = field(default_factory=dict)
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "alias": self.alias,
            "inputType": self.input_type,
            "input_type": self.input_type,
            "dataType": self.data_type,
            "data_type": self.data_type,
            "required": self.required,
            "default": self.default,
            "placeholder": self.placeholder,
            "category": self.category,
            "options": self.options,
            "refTable": self.ref_table,
            "ref_table": self.ref_table,
            "refFk": self.ref_fk,
            "ref_fk": self.ref_fk,
            "refDisplay": self.ref_display,
            "ref_display": self.ref_display,
            "validation": self.validation,
            "validationRules": self.validation,
        }


@dataclass
class CMLRule:
    """Business rule: type in default|required|readonly|hidden|unique|min|max|min_length|max_length|regex|equals"""
    id: str
    name: str
    type: str = "required"
    target: str = ""
    message: Optional[str] = None
    value: Optional[str] = None
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "target": self.target,
            "message": self.message,
            "value": self.value,
        }


class CMLCompiler:
    """Parses CML XML and extracts metadata + controls + rules."""

    def __init__(self, path: str | pathlib.Path | None = None, *, xml_text: str | None = None):
        self.path = pathlib.Path(path) if path else None
        self._xml_text: str | None = xml_text
        self._root: Optional[ET.Element] = None
        self._metadata: Optional[CMLMetadata] = None
        self._controls: Optional[List[CMLControl]] = None
        self._rules: Optional[List[CMLRule]] = None
        if path or xml_text:
            self._parse()

    @classmethod
    def from_string(cls, xml_text: str) -> "CMLCompiler":
        return cls(xml_text=xml_text)

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> "CMLCompiler":
        return cls(path=path)

    def _load_xml(self) -> str:
        if self._xml_text is not None:
            return self._xml_text
        if self.path and self.path.exists():
            return self.path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"CML file not found: {self.path}")

    def _parse(self) -> None:
        raw = self._load_xml().lstrip("\ufeff")
        try:
            self._root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise ValueError(f"Invalid CML XML at line {e.position[0]}: {e}") from e

    def _find(self, tag: str) -> Optional[ET.Element]:
        if self._root is None:
            return None
        t = tag.lower()
        for el in self._root.iter():
            if el.tag.lower() == t:
                return el
        return None

    def cml_metadata(self) -> Dict[str, Any]:
        if self._metadata:
            return self._metadata.to_dict()
        el = self._find("cml_metadata")
        if el is None:
            el = self._find("rpt_metadata") or self._find("fml_metadata")
        if el is None:
            raise ValueError("Missing <cml_metadata> tag in CML (required: name, displayName)")

        def get(*names: str, default: Optional[str] = None) -> Optional[str]:
            for k, v in el.attrib.items():
                if k.lower() in [n.lower() for n in names]:
                    return v.strip() if isinstance(v, str) else v
            return default

        name = get("name")
        if not name:
            raise ValueError("<cml_metadata> missing required attribute 'name'")
        display = get("displayName", "displayname", "display_name", default=name)
        scope = (get("scope", default="system") or "system").lower()
        if scope not in ("system", "app"):
            scope = "system"
        meta = CMLMetadata(
            name=name,
            display_name=display,
            category=get("category"),
            icon=get("icon"),
            description=get("description", "desc"),
            table=get("table"),
            schema=get("schema"),
            connection=get("connection"),
            scope=scope,
            app=get("app"),
            namespace=get("namespace", "ns"),
            raw_attrs=dict(el.attrib),
        )
        self._metadata = meta
        return meta.to_dict()

    def _parse_validation(self, el: ET.Element) -> Dict[str, Any]:
        rules: Dict[str, Any] = {}
        for child in list(el):
            if child.tag.lower() == "validation":
                for k, v in child.attrib.items():
                    nk = k.lower()
                    if nk in ("regex",):
                        nk = "pattern"
                    elif nk in ("minlength", "min-length"):
                        nk = "min_length"
                    elif nk in ("maxlength", "max-length"):
                        nk = "max_length"
                    elif nk in ("min_value", "minvalue"):
                        nk = "min"
                    elif nk in ("max_value", "maxvalue"):
                        nk = "max"
                    if nk in ("min_length", "max_length") and str(v).strip().isdigit():
                        rules[nk] = int(str(v).strip())
                    else:
                        rules[nk] = v
                if child.text and child.text.strip() and "pattern" not in rules:
                    rules["pattern"] = child.text.strip()
        for attr_key in ["regex", "pattern", "min_length", "max_length", "minLength", "maxLength", "min", "max"]:
            v = None
            for k, vv in el.attrib.items():
                if k.lower() == attr_key.lower():
                    v = vv
                    break
            if v is not None:
                nk = attr_key.lower()
                if nk == "regex":
                    nk = "pattern"
                elif nk == "minlength":
                    nk = "min_length"
                elif nk == "maxlength":
                    nk = "max_length"
                if nk in ("min_length", "max_length") and str(v).strip().isdigit():
                    rules[nk] = int(str(v).strip())
                else:
                    rules[nk] = v
        return rules

    def controls(self) -> List[CMLControl]:
        if self._controls is not None:
            return self._controls
        container = self._find("controls")
        if container is None:
            cols = [e for e in self._root.iter() if e.tag.lower() == "control"] if self._root is not None else []
            if not cols:
                self._controls = []
                return self._controls
        else:
            cols = [e for e in container if e.tag.lower() == "control"]
        result: List[CMLControl] = []
        for idx, el in enumerate(cols, start=1):
            def get(*names: str, default: Optional[str] = None) -> Optional[str]:
                for k, v in el.attrib.items():
                    if k.lower() in [n.lower() for n in names]:
                        return v.strip() if isinstance(v, str) else v
                return default

            cid = get("id", default=str(idx))
            name = get("name", default=cid)
            alias = get("alias", "label", default=name)
            input_type = (get("inputType", "input_type", "type", default="text") or "text").lower()
            data_type = get("dataType", "datatype", "data_type")
            required = str(get("required", default="false")).lower() in ("true", "1", "yes")
            default_val = get("default", "defaultValue")
            placeholder = get("placeholder")
            category = get("category", "group", "section")
            ref_table = get("refTable", "reftable", "ref_table")
            ref_fk = get("refFk", "reffk", "ref_fk")
            ref_display = get("refDisplay", "refdisplay", "ref_display")
            opts: List[str] = []
            for child in list(el):
                if child.tag.lower() == "options":
                    for opt in child:
                        if opt.tag.lower() == "option":
                            opts.append(opt.text or opt.get("value") or "")
                elif child.tag.lower() == "option":
                    opts.append(child.text or child.get("value") or "")
            if not opts and get("options"):
                opts = [o.strip() for o in str(get("options")).split(",") if o.strip()]
            result.append(CMLControl(
                id=str(cid), name=str(name), alias=str(alias),
                input_type=input_type,
                data_type=str(data_type) if data_type else None,
                required=required, default=default_val, placeholder=placeholder,
                category=category,
                options=opts,
                ref_table=str(ref_table) if ref_table else None,
                ref_fk=str(ref_fk) if ref_fk else None,
                ref_display=str(ref_display) if ref_display else None,
                validation=self._parse_validation(el),
                raw_attrs=dict(el.attrib),
            ))
        self._controls = result
        return result

    def rules(self) -> List[CMLRule]:
        if self._rules is not None:
            return self._rules
        container = self._find("rules")
        if container is None:
            cols = [e for e in self._root.iter() if e.tag.lower() == "rule"] if self._root is not None else []
            # exclude rules nested elsewhere? rules only live under <rules>
            filtered = []
            for el in cols:
                inside_controls = False
                if self._root is not None:
                    for anc in self._root.iter():
                        if anc.tag.lower() == "controls" and el in list(anc):
                            inside_controls = True
                            break
                if not inside_controls:
                    filtered.append(el)
            cols = filtered
            if not cols:
                self._rules = []
                return self._rules
        else:
            cols = [e for e in container if e.tag.lower() == "rule"]
        result: List[CMLRule] = []
        for idx, el in enumerate(cols, start=1):
            def get(*names: str, default: Optional[str] = None) -> Optional[str]:
                for k, v in el.attrib.items():
                    if k.lower() in [n.lower() for n in names]:
                        return v.strip() if isinstance(v, str) else v
                return default

            rid = get("id", default=str(idx))
            name = get("name", default=rid)
            rtype = (get("type", default="required") or "required").lower()
            target = get("target", "field", "column", default="")
            message = get("message", "msg")
            value = get("value")
            if value is None and el.text and el.text.strip():
                value = el.text.strip()
            result.append(CMLRule(
                id=str(rid), name=str(name), type=rtype,
                target=str(target), message=message,
                value=str(value) if value is not None else None,
                raw_attrs=dict(el.attrib),
            ))
        self._rules = result
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": self.cml_metadata(),
            "controls": [c.to_dict() for c in self.controls()],
            "rules": [r.to_dict() for r in self.rules()],
        }

    def __repr__(self) -> str:
        try:
            m = self.cml_metadata()
            return f"<CMLCompiler name={m.get('name')} controls={len(self.controls())} rules={len(self.rules())}>"
        except Exception:
            return "<CMLCompiler unparsed>"
