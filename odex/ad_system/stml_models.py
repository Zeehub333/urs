"""
STML models — System Tree Markup Language (definition-only, no DB tables).

Grammar:
<tree name label maxLevels serial separator>
  <child name label level digits model_source fmlk dist_fmlks gpm_model/>   (level template)
  <node label code level? model_source fmlk dist_fmlks gpm_model>            (instance, nestable)
    <node .../>...
  </node>
</tree>

Binding contract:
- model_source: opaque "app.Model" (or table) reference the node sequences data for.
- fmlk: form bound to the node; "*all" = every form -> gpm_model REQUIRED.
- dist_fmlks: operation forms linked to the node (purchase invoices, returns, ...)
  feeding the future operations statement (quantities + amounts). JSON array or
  comma-separated.
- gpm_model: permission-source model used only with fmlk="*all" (ad_users case).
  Accepted row shape: user_no, fmlk_id | rml_id, api_path, is_allowed.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as _xesc_fn


def _xesc(v: Any) -> str:
    return _xesc_fn(str(v or ""), {'"': "&quot;"})


@dataclass
class STMLLevel:
    """<child> level template: code-segment width + default bindings."""
    name: str
    label: str
    level: int
    digits: int = 3
    model_source: str = ""
    fmlk: str = ""
    dist_fmlks: List[str] = field(default_factory=list)
    gpm_model: str = ""
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "level": self.level,
            "digits": self.digits, "model_source": self.model_source,
            "fmlk": self.fmlk, "dist_fmlks": list(self.dist_fmlks),
            "gpm_model": self.gpm_model,
        }

    def to_xml(self) -> str:
        attrs = (f'name="{_xesc(self.name)}" label="{_xesc(self.label)}" '
                 f'level="{int(self.level)}" digits="{int(self.digits)}"')
        if self.model_source:
            attrs += f' model_source="{_xesc(self.model_source)}"'
        if self.fmlk:
            attrs += f' fmlk="{_xesc(self.fmlk)}"'
        if self.dist_fmlks:
            attrs += f' dist_fmlks="{_xesc(",".join(self.dist_fmlks))}"'
        if self.gpm_model:
            attrs += f' gpm_model="{_xesc(self.gpm_model)}"'
        return f"  <child {attrs}/>"


@dataclass
class STMLNode:
    """<node> instance. code = bare segment; full_code/level filled by the engine.

    Branch props (persisted to stml, read+written by designer/settings):
    digits = own segment width (None = inherit level template), auto =
    children auto-numbered (False = manual codes required).
    """
    label: str
    code: str = ""
    full_code: str = ""
    level: int = 0
    name: str = ""
    digits: Optional[int] = None
    auto: bool = True
    model_source: str = ""
    fmlk: str = ""
    dist_fmlks: List[str] = field(default_factory=list)
    gpm_model: str = ""
    children: List["STMLNode"] = field(default_factory=list)
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    @property
    def locked(self) -> bool:
        """True when the node opens every form (permission check required)."""
        return self.fmlk == "*all"

    @property
    def has_ops(self) -> bool:
        """True when operation forms are linked (operations statement enabled)."""
        return bool(self.dist_fmlks)

    def to_dict(self, deep: bool = True) -> Dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "code": self.code,
            "full_code": self.full_code, "level": self.level,
            "digits": self.digits, "auto": self.auto,
            "model_source": self.model_source, "fmlk": self.fmlk,
            "dist_fmlks": list(self.dist_fmlks), "gpm_model": self.gpm_model,
            "locked": self.locked, "has_ops": self.has_ops,
            "children": [c.to_dict(deep=True) for c in self.children] if deep else len(self.children),
        }

    def to_xml(self, indent: str = "  ") -> str:
        attrs = [f'label="{_xesc(self.label)}"']
        if self.name:
            attrs.append(f'name="{_xesc(self.name)}"')
        if self.code:
            attrs.append(f'code="{_xesc(self.code)}"')
        if self.digits:
            attrs.append(f'digits="{int(self.digits)}"')
        attrs.append(f'auto="{1 if self.auto else 0}"')
        if self.model_source:
            attrs.append(f'model_source="{_xesc(self.model_source)}"')
        if self.fmlk:
            attrs.append(f'fmlk="{_xesc(self.fmlk)}"')
        if self.dist_fmlks:
            attrs.append(f'dist_fmlks="{_xesc(",".join(self.dist_fmlks))}"')
        if self.gpm_model:
            attrs.append(f'gpm_model="{_xesc(self.gpm_model)}"')
        head = indent + "<node " + " ".join(attrs)
        if not self.children:
            return head + "/>"
        lines = [head + ">"]
        for c in self.children:
            lines.append(c.to_xml(indent + "  "))
        lines.append(indent + "</node>")
        return "\n".join(lines)


@dataclass
class STMLTree:
    name: str
    label: str
    max_levels: int = 9
    serial: bool = True
    separator: str = ""
    levels: List[STMLLevel] = field(default_factory=list)
    nodes: List[STMLNode] = field(default_factory=list)
    raw_attrs: Dict[str, str] = field(default_factory=dict)

    def level_def(self, level: int) -> Optional[STMLLevel]:
        for lv in self.levels:
            if lv.level == level:
                return lv
        return None

    def digits_for(self, level: int) -> int:
        lv = self.level_def(level)
        return lv.digits if lv else 3

    def to_dict(self, deep: bool = True) -> Dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "maxLevels": self.max_levels,
            "serial": self.serial, "separator": self.separator,
            "levels": [lv.to_dict() for lv in self.levels],
            "nodes": [n.to_dict(deep=deep) for n in self.nodes],
        }

    def to_xml(self) -> str:
        head = (f'<tree name="{_xesc(self.name)}" label="{_xesc(self.label)}" '
                f'maxLevels="{int(self.max_levels)}" serial="{1 if self.serial else 0}" '
                f'separator="{_xesc(self.separator)}">')
        lines = [head]
        lines.extend(lv.to_xml() for lv in self.levels)
        lines.extend(n.to_xml("  ") for n in self.nodes)
        lines.append("</tree>")
        return "\n".join(lines)


@dataclass
class GPMPermRow:
    """One permission row from the gpm_model source.

    Accepted shape: user_no (رقم المستخدم), fmlk_id or rml_id,
    api_path, is_allowed. Closed by default: no matching row = denied.
    """
    user_no: str
    fmlk_id: str = ""
    rml_id: str = ""
    api_path: str = ""
    is_allowed: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GPMPermRow":
        raw = d.get("is_allowed", False)
        allowed = raw in (True, 1, "1", "true", "True", "yes", "y") if isinstance(raw, str) else bool(raw)
        return cls(
            user_no=str(d.get("user_no", "") or ""),
            fmlk_id=str(d.get("fmlk_id", "") or ""),
            rml_id=str(d.get("rml_id", "") or ""),
            api_path=str(d.get("api_path", "") or ""),
            is_allowed=allowed,
        )
