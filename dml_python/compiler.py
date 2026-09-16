"""
DML Compiler & Metadata Parser.

Parses ``*.dml`` document-template files::

    <dml paper="A4" title="..." name="..." source="invoice_report.rml">
      <variables>
        <var id="1" name="customer" type="text"/>
      </variables>
      <header style="...">innerHTML with %%field%% refs</header>
      <sections>
        <section id="1" name="..." style="...">
          innerHTML with [var] refs
          <tableview source="player:main" style="..." th="..." tr="..." td="..."/>
        </section>
      </sections>
      <footer style="...">innerHTML</footer>
    </dml>
"""
from __future__ import annotations
import pathlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class DMLVariable:
    id: str
    name: str
    type: str = "text"  # text | number | date | image | boolean

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "type": self.type}


@dataclass
class DMLBlock:
    html: str = ""
    style: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"html": self.html, "style": self.style}


@dataclass
class DMLTableView:
    source: str = "player:main"  # player:main | player:detail | static
    style: str = ""
    th: str = ""
    tr: str = ""
    td: str = ""
    variant: str = "grid"  # grid | striped | plain | compact
    layout: str = "rows"  # rows | grid2 (2-col label/value grid, single record)
    total_row: bool = False
    count_row: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "style": self.style,
                "th": self.th, "tr": self.tr, "td": self.td,
                "variant": self.variant, "total_row": self.total_row,
                "count_row": self.count_row, "layout": self.layout}


@dataclass
class DMLSection:
    id: str
    name: str = ""
    html: str = ""
    style: str = ""
    tableview: Optional[DMLTableView] = None
    print_before: str = ""
    print_after: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "html": self.html,
                "style": self.style,
                "tableview": (self.tableview.to_dict() if self.tableview else None),
                "print_before": self.print_before, "print_after": self.print_after}


class DMLCompiler:
    """Parse a .dml file into metadata / variables / header / sections / footer."""

    def __init__(self, path: str | pathlib.Path | None = None, *, xml_text: str | None = None):
        self.path = pathlib.Path(path) if path else None
        self._xml_text: str | None = xml_text
        self._root: Optional[ET.Element] = None
        self._variables: Optional[List[DMLVariable]] = None
        self._sections: Optional[List[DMLSection]] = None
        if path or xml_text:
            self._parse()

    @classmethod
    def from_string(cls, xml_text: str) -> "DMLCompiler":
        return cls(xml_text=xml_text)

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> "DMLCompiler":
        return cls(path=path)

    # ── parsing ──────────────────────────────────────────────────────

    def _load_xml(self) -> str:
        if self._xml_text is not None:
            return self._xml_text
        if self.path and self.path.exists():
            return self.path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"DML file not found: {self.path}")

    def _parse(self) -> None:
        raw = self._load_xml().lstrip("\ufeff")
        try:
            self._root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise ValueError(f"Invalid DML XML at line {e.position[0]}: {e}") from e

    def _find(self, *names: str) -> Optional[ET.Element]:
        if self._root is None:
            return None
        wanted = {n.lower() for n in names}
        for el in self._root.iter():
            if el.tag.lower() in wanted:
                return el
        return None

    @staticmethod
    def _inner_html(el: ET.Element) -> str:
        """Serialize mixed-content children of an element back to HTML."""
        parts = []
        if el.text and el.text.strip():
            parts.append(el.text)
        for child in list(el):
            if child.tag.lower() == "tableview":
                continue  # structural child, not content
            parts.append(ET.tostring(child, encoding="unicode"))
            if child.tail and child.tail.strip():
                parts.append(child.tail)
        return "".join(parts).strip()

    # ── accessors ────────────────────────────────────────────────────

    def metadata(self) -> Dict[str, Any]:
        if self._root is None:
            return {"paper": "A4", "title": "", "name": "", "source": "",
                    "orientation": "portrait", "margin": ""}
        a = self._root.attrib
        low = {k.lower(): v for k, v in a.items()}
        return {
            "paper": low.get("paper", "A4"),
            "title": low.get("title", ""),
            "name": low.get("name", ""),
            "source": low.get("source", ""),
            "orientation": low.get("orientation", "portrait"),
            "margin": low.get("margin", ""),
            "doctype": low.get("doctype", "kashf"),
            "key_column": low.get("key_column", low.get("keycolumn", "")),
        }

    def variables(self) -> List[DMLVariable]:
        if self._variables is not None:
            return self._variables
        out: List[DMLVariable] = []
        cont = self._find("variables", "vars")
        if cont is not None:
            for i, el in enumerate([c for c in list(cont) if c.tag.lower() == "var"], start=1):
                a = {k.lower(): (v.strip() if isinstance(v, str) else v)
                     for k, v in el.attrib.items()}
                out.append(DMLVariable(
                    id=str(a.get("id", i)),
                    name=str(a.get("name", f"var{i}")),
                    type=str(a.get("type", "text")),
                ))
        self._variables = out
        return out

    def header(self) -> DMLBlock:
        el = self._find("header")
        if el is None:
            return DMLBlock()
        return DMLBlock(html=self._inner_html(el),
                        style=str(el.attrib.get("style", "") or ""))

    def footer(self) -> DMLBlock:
        el = self._find("footer")
        if el is None:
            return DMLBlock()
        return DMLBlock(html=self._inner_html(el),
                        style=str(el.attrib.get("style", "") or ""))

    def sections(self) -> List[DMLSection]:
        if self._sections is not None:
            return self._sections
        out: List[DMLSection] = []
        cont = self._find("sections")
        scope = list(cont) if cont is not None else []
        for i, el in enumerate([c for c in scope if c.tag.lower() == "section"], start=1):
            tv: Optional[DMLTableView] = None
            for child in list(el):
                if child.tag.lower() == "tableview":
                    ta = {k.lower(): v for k, v in child.attrib.items()}
                    def _flag(*names: str) -> bool:
                        return any(str(ta.get(n, "") or "").strip().lower() in ("1", "true", "yes") for n in names)
                    tv = DMLTableView(
                        source=str(ta.get("source", "player:main")),
                        style=str(ta.get("style", "") or ""),
                        th=str(ta.get("th", "") or ""),
                        tr=str(ta.get("tr", "") or ""),
                        td=str(ta.get("td", "") or ""),
                        variant=str(ta.get("variant", "grid") or "grid").strip().lower(),
                        layout=str(ta.get("layout", "rows") or "rows").strip().lower(),
                        total_row=_flag("total_row", "totalrow", "total"),
                        count_row=_flag("count_row", "countrow", "count"),
                    )
            out.append(DMLSection(
                id=str(el.attrib.get("id", i)),
                name=str(el.attrib.get("name", "") or ""),
                html=self._inner_html(el),
                style=str(el.attrib.get("style", "") or ""),
                tableview=tv,
                print_before=str(el.attrib.get("print_before", "") or ""),
                print_after=str(el.attrib.get("print_after", "") or ""),
            ))
        self._sections = out
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": self.metadata(),
            "variables": [v.to_dict() for v in self.variables()],
            "header": self.header().to_dict(),
            "sections": [s.to_dict() for s in self.sections()],
            "footer": self.footer().to_dict(),
        }

    # ── substitution ─────────────────────────────────────────────────

    VAR_RE = re.compile(r"\[([A-Za-z0-9_\u0600-\u06FF :\.]+?)\]")
    FIELD_RE = re.compile(r"%%(.+?)%%")

    def render_text(self, text: str, data: Optional[Dict[str, Any]] = None) -> str:
        """Substitute ``[var]`` and ``%%field%%`` placeholders from data dict."""
        data = data or {}

        def _v(m: re.Match) -> str:
            key = m.group(1).strip()
            v = data.get(key, data.get(key.lower(), ""))
            return "" if v is None else str(v)

        def _f(m: re.Match) -> str:
            key = m.group(1).strip()
            v = data.get(key, data.get(key.lower(), ""))
            return "" if v is None else str(v)

        text = self.VAR_RE.sub(_v, text or "")
        return self.FIELD_RE.sub(_f, text)
