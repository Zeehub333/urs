"""
UI Engine — dynamic web components (375 JS) for odex
Categories: views, charts, buttons, fields, search, tables, grids
No static registry — discovers *.js at runtime, renders dynamically.
"""
import json
import pathlib
from typing import Any, Dict, List, Optional

COMPONENT_ROOT = pathlib.Path(__file__).parent / "components"
SHARED_CSS = pathlib.Path(__file__).parent / "web" / "static" / "shared.css"
# fallback shared location (system-wide)
if not SHARED_CSS.exists():
    SHARED_CSS = pathlib.Path("C:/urs2/shared.css")
CATEGORIES = ["views", "charts", "buttons", "fields", "search", "tables", "grids"]

class UIEngine:
    def __init__(self, root: pathlib.Path = COMPONENT_ROOT):
        self.root = pathlib.Path(root)
        self._cache: Dict[str, Dict[str, Any]] = {}

    # ── discovery (dynamic) ──
    def discover(self, refresh: bool = False) -> Dict[str, List[str]]:
        if self._cache and not refresh:
            return {k: list(v.keys()) for k, v in self._cache.items()}
        self._cache.clear()
        for cat in CATEGORIES:
            d = self.root / cat
            if not d.exists():
                continue
            self._cache[cat] = {}
            for p in d.glob("*.js"):
                self._cache[cat][p.stem] = {"path": str(p), "category": cat, "name": p.stem, "code": p.read_text(encoding="utf-8", errors="ignore")[:2000]}
        return {k: list(v.keys()) for k, v in self._cache.items()}

    def list_components(self, category: str = None, limit: int = 375, offset: int = 0, search: str = None) -> List[Dict[str, Any]]:
        self.discover()
        items = []
        cats = [category] if category else CATEGORIES
        for cat in cats:
            for name, meta in self._cache.get(cat, {}).items():
                if search and search.lower() not in name.lower():
                    continue
                items.append({"category": cat, "name": name, "path": meta["path"]})
        return items[offset: offset + limit]

    def count(self, category: str = None, search: str = None) -> int:
        return len(self.list_components(category, limit=10000, search=search))

    def get_code(self, category: str, name: str) -> Optional[str]:
        self.discover()
        meta = self._cache.get(category, {}).get(name)
        if not meta:
            return None
        return pathlib.Path(meta["path"]).read_text(encoding="utf-8")

    def exists(self, category: str, name: str) -> bool:
        self.discover()
        return name in self._cache.get(category, {})

    # ── generic render ──
    def build(self, category: str, name: str, props: Dict[str, Any] = None) -> str:
        import uuid
        props = props or {}
        code = self.get_code(category, name)
        if code is None:
            return f"<!-- missing {category}/{name} -->"
        uid = f"odex-{category}-{name}-{uuid.uuid4().hex[:8]}"
        # module scripts have document.currentScript == null, so use id
        return f"<div id=\"{uid}\" data-component=\"{category}/{name}\" data-props='{json.dumps(props)}'></div>\n<script type=\"module\">import {{ {name} }} from '/static/components/{category}/{name}.js'; document.getElementById('{uid}').innerHTML = new {name}({json.dumps(props)}).render();</script>"

    # ── category shortcuts (dynamic) ──
    def build_view(self, name: str, props: Dict[str, Any] = None) -> str: return self.build("views", name, props)
    def build_chart(self, name: str, data: List[Any], opts: Dict[str, Any] = None) -> str: return self.build("charts", name, {"data": data, "opts": opts or {}})
    def build_button(self, name: str, label: str, on_click: str = "") -> str: return self.build("buttons", name, {"label": label, "onClick": on_click})
    def build_field(self, name: str, value: Any = "", cfg: Dict[str, Any] = None) -> str: return self.build("fields", name, {"value": value, "cfg": cfg or {}})
    def build_search(self, name: str, query: str = "") -> str: return self.build("search", name, {"query": query})
    def build_table(self, name: str, rows: List[Dict[str, Any]], cols: List[str] = None) -> str:
        cols = cols or (list(rows[0].keys()) if rows else [])
        return self.build("tables", name, {"rows": rows, "cols": cols})
    def build_grid(self, name: str, items: List[Any], cols: int = 3) -> str: return self.build("grids", name, {"items": items, "cols": cols})

    # ── composite helpers ──
    def data_view(self, title: str, rows: List[Dict[str, Any]], view: str = "DataView1", table: str = "DataTable1", chart: str = "BarChart1") -> str:
        view_html = self.build_view(view, {"title": title, "count": len(rows)})
        table_html = self.build_table(table, rows)
        chart_html = self.build_chart(chart, rows)
        return f"<section class='data-view'><h2>{title}</h2>{view_html}{chart_html}{table_html}</section>"

    def dashboard(self, title: str, datasets: Dict[str, List[Dict[str, Any]]]) -> str:
        parts = [f"<h1>{title}</h1>"]
        for name, rows in datasets.items():
            parts.append(self.data_view(name, rows))
        return "\n".join(parts)

    def search_bar(self, query: str = "", results: List[Dict[str, Any]] = None, table: str = "DataTable1") -> str:
        bar = self.build_search("SearchBar1", query)
        tbl = self.build_table(table, results or [])
        return f"<div class='search-wrap'>{bar}{tbl}</div>"

    def form(self, fields: List[Dict[str, Any]], submit_label: str = "Submit", button: str = "PrimaryButton1") -> str:
        html_fields = "".join(self.build_field(f.get("type", "TextField1"), f.get("value", ""), f) for f in fields)
        btn = self.build_button(button, submit_label, "submitForm()")
        return f"<form>{html_fields}{btn}</form>"

    # ── backend binding (dynamic table/columns) ──
    def bind_engine(self, engine, table: str, filters: Dict[str, Any] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch rows from any dynamic engine (oracle/sqlserver/postgres/zk) and return for UI."""
        try:
            engine.connect()
            rows = engine.filter(table, filters or {}, limit=limit) if filters else engine.list_all(table, limit=limit)
            return rows
        finally:
            try:
                engine.disconnect()
            except Exception:
                pass

    def bind_and_build(self, engine, table: str, view: str = "DataView1", component: str = "DataTable1", filters: Dict[str, Any] = None) -> str:
        rows = self.bind_engine(engine, table, filters)
        return self.data_view(f"{table} ({len(rows)})", rows, view=view, table=component)

    # ── utils ──
    def search_components(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self.list_components(search=query, limit=limit)

    def component_stats(self) -> Dict[str, Any]:
        disc = self.discover()
        return {"total": sum(len(v) for v in disc.values()), "by_category": {k: len(v) for k, v in disc.items()}, "categories": CATEGORIES}

    def export_manifest(self) -> Dict[str, Any]:
        return {"root": str(self.root), "stats": self.component_stats(), "components": self.list_components(limit=10000)}

    def generate_page(self, title: str, body_html: str) -> str:
        return f"""<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><link rel=\"stylesheet\" href=\"/static/shared.css\"></head><body>{body_html}<script>function search(v){{console.log('search',v)}}</script></body></html>"""

    # ── shared CSS (editable from UI) ──
    def get_shared_css(self) -> str:
        p = SHARED_CSS
        if not p.exists():
            # fallback to demo.css
            alt = pathlib.Path(__file__).parent / "static" / "demo.css"
            p = alt if alt.exists() else p
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def set_shared_css(self, css: str) -> bool:
        p = SHARED_CSS
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(css, encoding="utf-8")
        # also mirror to other shared location
        try:
            pathlib.Path("C:/urs2/shared.css").write_text(css, encoding="utf-8")
        except Exception:
            pass
        try:
            pathlib.Path("C:/urs2/odex/shared.css").write_text(css, encoding="utf-8")
        except Exception:
            pass
        return True

    def update_css_var(self, var: str, value: str) -> str:
        css = self.get_shared_css()
        # simple var handling: replace :root { --var: ... } or append
        import re
        if re.search(rf"{re.escape(var)}\s*:", css):
            css = re.sub(rf"({re.escape(var)}\s*:\s*)[^;]+", rf"\g<1>{value}", css)
        else:
            css = css.replace(":root{", f":root{{{var}:{value};")
            if ":root{" not in css:
                css = f":root{{{var}:{value};}}\n" + css
        self.set_shared_css(css)
        return css


# ── AUTO-GENERATED COMPONENT DEFS (375) ──
# each def name == JS component name, params == JS props
    def ConfirmButton1(self, label: str = '', on_click: str = '') -> str:
        """buttons/ConfirmButton1 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ConfirmButton1', {'label': label, 'onClick': on_click})

    def ConfirmButton2(self, label: str = '', on_click: str = '') -> str:
        """buttons/ConfirmButton2 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ConfirmButton2', {'label': label, 'onClick': on_click})

    def ConfirmButton3(self, label: str = '', on_click: str = '') -> str:
        """buttons/ConfirmButton3 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ConfirmButton3', {'label': label, 'onClick': on_click})

    def ConfirmButton4(self, label: str = '', on_click: str = '') -> str:
        """buttons/ConfirmButton4 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ConfirmButton4', {'label': label, 'onClick': on_click})

    def ConfirmButton5(self, label: str = '', on_click: str = '') -> str:
        """buttons/ConfirmButton5 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ConfirmButton5', {'label': label, 'onClick': on_click})

    def ConfirmButton6(self, label: str = '', on_click: str = '') -> str:
        """buttons/ConfirmButton6 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ConfirmButton6', {'label': label, 'onClick': on_click})

    def DangerButton1(self, label: str = '', on_click: str = '') -> str:
        """buttons/DangerButton1 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DangerButton1', {'label': label, 'onClick': on_click})

    def DangerButton2(self, label: str = '', on_click: str = '') -> str:
        """buttons/DangerButton2 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DangerButton2', {'label': label, 'onClick': on_click})

    def DangerButton3(self, label: str = '', on_click: str = '') -> str:
        """buttons/DangerButton3 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DangerButton3', {'label': label, 'onClick': on_click})

    def DangerButton4(self, label: str = '', on_click: str = '') -> str:
        """buttons/DangerButton4 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DangerButton4', {'label': label, 'onClick': on_click})

    def DangerButton5(self, label: str = '', on_click: str = '') -> str:
        """buttons/DangerButton5 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DangerButton5', {'label': label, 'onClick': on_click})

    def DangerButton6(self, label: str = '', on_click: str = '') -> str:
        """buttons/DangerButton6 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DangerButton6', {'label': label, 'onClick': on_click})

    def DropdownButton1(self, label: str = '', on_click: str = '') -> str:
        """buttons/DropdownButton1 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DropdownButton1', {'label': label, 'onClick': on_click})

    def DropdownButton2(self, label: str = '', on_click: str = '') -> str:
        """buttons/DropdownButton2 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DropdownButton2', {'label': label, 'onClick': on_click})

    def DropdownButton3(self, label: str = '', on_click: str = '') -> str:
        """buttons/DropdownButton3 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DropdownButton3', {'label': label, 'onClick': on_click})

    def DropdownButton4(self, label: str = '', on_click: str = '') -> str:
        """buttons/DropdownButton4 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DropdownButton4', {'label': label, 'onClick': on_click})

    def DropdownButton5(self, label: str = '', on_click: str = '') -> str:
        """buttons/DropdownButton5 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DropdownButton5', {'label': label, 'onClick': on_click})

    def DropdownButton6(self, label: str = '', on_click: str = '') -> str:
        """buttons/DropdownButton6 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'DropdownButton6', {'label': label, 'onClick': on_click})

    def FloatingButton1(self, label: str = '', on_click: str = '') -> str:
        """buttons/FloatingButton1 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'FloatingButton1', {'label': label, 'onClick': on_click})

    def FloatingButton2(self, label: str = '', on_click: str = '') -> str:
        """buttons/FloatingButton2 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'FloatingButton2', {'label': label, 'onClick': on_click})

    def FloatingButton3(self, label: str = '', on_click: str = '') -> str:
        """buttons/FloatingButton3 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'FloatingButton3', {'label': label, 'onClick': on_click})

    def FloatingButton4(self, label: str = '', on_click: str = '') -> str:
        """buttons/FloatingButton4 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'FloatingButton4', {'label': label, 'onClick': on_click})

    def FloatingButton5(self, label: str = '', on_click: str = '') -> str:
        """buttons/FloatingButton5 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'FloatingButton5', {'label': label, 'onClick': on_click})

    def FloatingButton6(self, label: str = '', on_click: str = '') -> str:
        """buttons/FloatingButton6 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'FloatingButton6', {'label': label, 'onClick': on_click})

    def IconButton1(self, label: str = '', on_click: str = '') -> str:
        """buttons/IconButton1 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'IconButton1', {'label': label, 'onClick': on_click})

    def IconButton2(self, label: str = '', on_click: str = '') -> str:
        """buttons/IconButton2 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'IconButton2', {'label': label, 'onClick': on_click})

    def IconButton3(self, label: str = '', on_click: str = '') -> str:
        """buttons/IconButton3 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'IconButton3', {'label': label, 'onClick': on_click})

    def IconButton4(self, label: str = '', on_click: str = '') -> str:
        """buttons/IconButton4 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'IconButton4', {'label': label, 'onClick': on_click})

    def IconButton5(self, label: str = '', on_click: str = '') -> str:
        """buttons/IconButton5 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'IconButton5', {'label': label, 'onClick': on_click})

    def IconButton6(self, label: str = '', on_click: str = '') -> str:
        """buttons/IconButton6 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'IconButton6', {'label': label, 'onClick': on_click})

    def LoadingButton1(self, label: str = '', on_click: str = '') -> str:
        """buttons/LoadingButton1 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'LoadingButton1', {'label': label, 'onClick': on_click})

    def LoadingButton2(self, label: str = '', on_click: str = '') -> str:
        """buttons/LoadingButton2 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'LoadingButton2', {'label': label, 'onClick': on_click})

    def LoadingButton3(self, label: str = '', on_click: str = '') -> str:
        """buttons/LoadingButton3 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'LoadingButton3', {'label': label, 'onClick': on_click})

    def LoadingButton4(self, label: str = '', on_click: str = '') -> str:
        """buttons/LoadingButton4 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'LoadingButton4', {'label': label, 'onClick': on_click})

    def LoadingButton5(self, label: str = '', on_click: str = '') -> str:
        """buttons/LoadingButton5 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'LoadingButton5', {'label': label, 'onClick': on_click})

    def LoadingButton6(self, label: str = '', on_click: str = '') -> str:
        """buttons/LoadingButton6 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'LoadingButton6', {'label': label, 'onClick': on_click})

    def PrimaryButton1(self, label: str = '', on_click: str = '') -> str:
        """buttons/PrimaryButton1 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'PrimaryButton1', {'label': label, 'onClick': on_click})

    def PrimaryButton2(self, label: str = '', on_click: str = '') -> str:
        """buttons/PrimaryButton2 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'PrimaryButton2', {'label': label, 'onClick': on_click})

    def PrimaryButton3(self, label: str = '', on_click: str = '') -> str:
        """buttons/PrimaryButton3 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'PrimaryButton3', {'label': label, 'onClick': on_click})

    def PrimaryButton4(self, label: str = '', on_click: str = '') -> str:
        """buttons/PrimaryButton4 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'PrimaryButton4', {'label': label, 'onClick': on_click})

    def PrimaryButton5(self, label: str = '', on_click: str = '') -> str:
        """buttons/PrimaryButton5 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'PrimaryButton5', {'label': label, 'onClick': on_click})

    def PrimaryButton6(self, label: str = '', on_click: str = '') -> str:
        """buttons/PrimaryButton6 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'PrimaryButton6', {'label': label, 'onClick': on_click})

    def SecondaryButton1(self, label: str = '', on_click: str = '') -> str:
        """buttons/SecondaryButton1 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SecondaryButton1', {'label': label, 'onClick': on_click})

    def SecondaryButton2(self, label: str = '', on_click: str = '') -> str:
        """buttons/SecondaryButton2 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SecondaryButton2', {'label': label, 'onClick': on_click})

    def SecondaryButton3(self, label: str = '', on_click: str = '') -> str:
        """buttons/SecondaryButton3 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SecondaryButton3', {'label': label, 'onClick': on_click})

    def SecondaryButton4(self, label: str = '', on_click: str = '') -> str:
        """buttons/SecondaryButton4 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SecondaryButton4', {'label': label, 'onClick': on_click})

    def SecondaryButton5(self, label: str = '', on_click: str = '') -> str:
        """buttons/SecondaryButton5 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SecondaryButton5', {'label': label, 'onClick': on_click})

    def SecondaryButton6(self, label: str = '', on_click: str = '') -> str:
        """buttons/SecondaryButton6 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SecondaryButton6', {'label': label, 'onClick': on_click})

    def SplitButton1(self, label: str = '', on_click: str = '') -> str:
        """buttons/SplitButton1 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SplitButton1', {'label': label, 'onClick': on_click})

    def SplitButton2(self, label: str = '', on_click: str = '') -> str:
        """buttons/SplitButton2 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SplitButton2', {'label': label, 'onClick': on_click})

    def SplitButton3(self, label: str = '', on_click: str = '') -> str:
        """buttons/SplitButton3 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SplitButton3', {'label': label, 'onClick': on_click})

    def SplitButton4(self, label: str = '', on_click: str = '') -> str:
        """buttons/SplitButton4 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SplitButton4', {'label': label, 'onClick': on_click})

    def SplitButton5(self, label: str = '', on_click: str = '') -> str:
        """buttons/SplitButton5 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SplitButton5', {'label': label, 'onClick': on_click})

    def SplitButton6(self, label: str = '', on_click: str = '') -> str:
        """buttons/SplitButton6 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'SplitButton6', {'label': label, 'onClick': on_click})

    def ToggleButton1(self, label: str = '', on_click: str = '') -> str:
        """buttons/ToggleButton1 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ToggleButton1', {'label': label, 'onClick': on_click})

    def ToggleButton2(self, label: str = '', on_click: str = '') -> str:
        """buttons/ToggleButton2 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ToggleButton2', {'label': label, 'onClick': on_click})

    def ToggleButton3(self, label: str = '', on_click: str = '') -> str:
        """buttons/ToggleButton3 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ToggleButton3', {'label': label, 'onClick': on_click})

    def ToggleButton4(self, label: str = '', on_click: str = '') -> str:
        """buttons/ToggleButton4 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ToggleButton4', {'label': label, 'onClick': on_click})

    def ToggleButton5(self, label: str = '', on_click: str = '') -> str:
        """buttons/ToggleButton5 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ToggleButton5', {'label': label, 'onClick': on_click})

    def ToggleButton6(self, label: str = '', on_click: str = '') -> str:
        """buttons/ToggleButton6 — props: label: str = '', on_click: str = ''"""
        return self.build('buttons', 'ToggleButton6', {'label': label, 'onClick': on_click})

    def AreaChart1(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/AreaChart1 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'AreaChart1', {'data': data or [], 'opts': opts or {}})

    def AreaChart2(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/AreaChart2 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'AreaChart2', {'data': data or [], 'opts': opts or {}})

    def AreaChart3(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/AreaChart3 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'AreaChart3', {'data': data or [], 'opts': opts or {}})

    def AreaChart4(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/AreaChart4 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'AreaChart4', {'data': data or [], 'opts': opts or {}})

    def AreaChart5(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/AreaChart5 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'AreaChart5', {'data': data or [], 'opts': opts or {}})

    def AreaChart6(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/AreaChart6 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'AreaChart6', {'data': data or [], 'opts': opts or {}})

    def AreaChart7(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/AreaChart7 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'AreaChart7', {'data': data or [], 'opts': opts or {}})

    def BarChart1(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/BarChart1 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'BarChart1', {'data': data or [], 'opts': opts or {}})

    def BarChart2(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/BarChart2 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'BarChart2', {'data': data or [], 'opts': opts or {}})

    def BarChart3(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/BarChart3 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'BarChart3', {'data': data or [], 'opts': opts or {}})

    def BarChart4(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/BarChart4 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'BarChart4', {'data': data or [], 'opts': opts or {}})

    def BarChart5(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/BarChart5 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'BarChart5', {'data': data or [], 'opts': opts or {}})

    def BarChart6(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/BarChart6 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'BarChart6', {'data': data or [], 'opts': opts or {}})

    def BarChart7(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/BarChart7 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'BarChart7', {'data': data or [], 'opts': opts or {}})

    def DonutChart1(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/DonutChart1 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'DonutChart1', {'data': data or [], 'opts': opts or {}})

    def DonutChart2(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/DonutChart2 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'DonutChart2', {'data': data or [], 'opts': opts or {}})

    def DonutChart3(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/DonutChart3 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'DonutChart3', {'data': data or [], 'opts': opts or {}})

    def DonutChart4(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/DonutChart4 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'DonutChart4', {'data': data or [], 'opts': opts or {}})

    def DonutChart5(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/DonutChart5 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'DonutChart5', {'data': data or [], 'opts': opts or {}})

    def DonutChart6(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/DonutChart6 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'DonutChart6', {'data': data or [], 'opts': opts or {}})

    def DonutChart7(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/DonutChart7 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'DonutChart7', {'data': data or [], 'opts': opts or {}})

    def FunnelChart1(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/FunnelChart1 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'FunnelChart1', {'data': data or [], 'opts': opts or {}})

    def FunnelChart2(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/FunnelChart2 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'FunnelChart2', {'data': data or [], 'opts': opts or {}})

    def FunnelChart3(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/FunnelChart3 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'FunnelChart3', {'data': data or [], 'opts': opts or {}})

    def FunnelChart4(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/FunnelChart4 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'FunnelChart4', {'data': data or [], 'opts': opts or {}})

    def FunnelChart5(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/FunnelChart5 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'FunnelChart5', {'data': data or [], 'opts': opts or {}})

    def FunnelChart6(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/FunnelChart6 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'FunnelChart6', {'data': data or [], 'opts': opts or {}})

    def FunnelChart7(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/FunnelChart7 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'FunnelChart7', {'data': data or [], 'opts': opts or {}})

    def GaugeChart1(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/GaugeChart1 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'GaugeChart1', {'data': data or [], 'opts': opts or {}})

    def GaugeChart2(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/GaugeChart2 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'GaugeChart2', {'data': data or [], 'opts': opts or {}})

    def GaugeChart3(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/GaugeChart3 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'GaugeChart3', {'data': data or [], 'opts': opts or {}})

    def GaugeChart4(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/GaugeChart4 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'GaugeChart4', {'data': data or [], 'opts': opts or {}})

    def GaugeChart5(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/GaugeChart5 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'GaugeChart5', {'data': data or [], 'opts': opts or {}})

    def GaugeChart6(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/GaugeChart6 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'GaugeChart6', {'data': data or [], 'opts': opts or {}})

    def GaugeChart7(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/GaugeChart7 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'GaugeChart7', {'data': data or [], 'opts': opts or {}})

    def HeatmapChart1(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/HeatmapChart1 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'HeatmapChart1', {'data': data or [], 'opts': opts or {}})

    def HeatmapChart2(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/HeatmapChart2 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'HeatmapChart2', {'data': data or [], 'opts': opts or {}})

    def HeatmapChart3(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/HeatmapChart3 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'HeatmapChart3', {'data': data or [], 'opts': opts or {}})

    def HeatmapChart4(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/HeatmapChart4 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'HeatmapChart4', {'data': data or [], 'opts': opts or {}})

    def HeatmapChart5(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/HeatmapChart5 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'HeatmapChart5', {'data': data or [], 'opts': opts or {}})

    def HeatmapChart6(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/HeatmapChart6 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'HeatmapChart6', {'data': data or [], 'opts': opts or {}})

    def HeatmapChart7(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/HeatmapChart7 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'HeatmapChart7', {'data': data or [], 'opts': opts or {}})

    def LineChart1(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/LineChart1 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'LineChart1', {'data': data or [], 'opts': opts or {}})

    def LineChart2(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/LineChart2 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'LineChart2', {'data': data or [], 'opts': opts or {}})

    def LineChart3(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/LineChart3 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'LineChart3', {'data': data or [], 'opts': opts or {}})

    def LineChart4(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/LineChart4 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'LineChart4', {'data': data or [], 'opts': opts or {}})

    def LineChart5(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/LineChart5 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'LineChart5', {'data': data or [], 'opts': opts or {}})

    def LineChart6(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/LineChart6 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'LineChart6', {'data': data or [], 'opts': opts or {}})

    def LineChart7(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/LineChart7 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'LineChart7', {'data': data or [], 'opts': opts or {}})

    def PieChart1(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/PieChart1 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'PieChart1', {'data': data or [], 'opts': opts or {}})

    def PieChart2(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/PieChart2 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'PieChart2', {'data': data or [], 'opts': opts or {}})

    def PieChart3(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/PieChart3 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'PieChart3', {'data': data or [], 'opts': opts or {}})

    def PieChart4(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/PieChart4 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'PieChart4', {'data': data or [], 'opts': opts or {}})

    def PieChart5(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/PieChart5 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'PieChart5', {'data': data or [], 'opts': opts or {}})

    def PieChart6(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/PieChart6 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'PieChart6', {'data': data or [], 'opts': opts or {}})

    def PieChart7(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/PieChart7 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'PieChart7', {'data': data or [], 'opts': opts or {}})

    def RadarChart1(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/RadarChart1 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'RadarChart1', {'data': data or [], 'opts': opts or {}})

    def RadarChart2(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/RadarChart2 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'RadarChart2', {'data': data or [], 'opts': opts or {}})

    def RadarChart3(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/RadarChart3 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'RadarChart3', {'data': data or [], 'opts': opts or {}})

    def RadarChart4(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/RadarChart4 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'RadarChart4', {'data': data or [], 'opts': opts or {}})

    def RadarChart5(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/RadarChart5 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'RadarChart5', {'data': data or [], 'opts': opts or {}})

    def RadarChart6(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/RadarChart6 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'RadarChart6', {'data': data or [], 'opts': opts or {}})

    def RadarChart7(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/RadarChart7 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'RadarChart7', {'data': data or [], 'opts': opts or {}})

    def ScatterChart1(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/ScatterChart1 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'ScatterChart1', {'data': data or [], 'opts': opts or {}})

    def ScatterChart2(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/ScatterChart2 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'ScatterChart2', {'data': data or [], 'opts': opts or {}})

    def ScatterChart3(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/ScatterChart3 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'ScatterChart3', {'data': data or [], 'opts': opts or {}})

    def ScatterChart4(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/ScatterChart4 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'ScatterChart4', {'data': data or [], 'opts': opts or {}})

    def ScatterChart5(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/ScatterChart5 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'ScatterChart5', {'data': data or [], 'opts': opts or {}})

    def ScatterChart6(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/ScatterChart6 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'ScatterChart6', {'data': data or [], 'opts': opts or {}})

    def ScatterChart7(self, data: List[Any] = None, opts: Dict[str, Any] = None) -> str:
        """charts/ScatterChart7 — props: data: List[Any] = None, opts: Dict[str, Any] = None"""
        return self.build('charts', 'ScatterChart7', {'data': data or [], 'opts': opts or {}})

    def CheckboxField1(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/CheckboxField1 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'CheckboxField1', {'value': value, 'cfg': cfg or {}})

    def CheckboxField2(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/CheckboxField2 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'CheckboxField2', {'value': value, 'cfg': cfg or {}})

    def CheckboxField3(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/CheckboxField3 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'CheckboxField3', {'value': value, 'cfg': cfg or {}})

    def CheckboxField4(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/CheckboxField4 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'CheckboxField4', {'value': value, 'cfg': cfg or {}})

    def CheckboxField5(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/CheckboxField5 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'CheckboxField5', {'value': value, 'cfg': cfg or {}})

    def CheckboxField6(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/CheckboxField6 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'CheckboxField6', {'value': value, 'cfg': cfg or {}})

    def DateField1(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/DateField1 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'DateField1', {'value': value, 'cfg': cfg or {}})

    def DateField2(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/DateField2 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'DateField2', {'value': value, 'cfg': cfg or {}})

    def DateField3(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/DateField3 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'DateField3', {'value': value, 'cfg': cfg or {}})

    def DateField4(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/DateField4 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'DateField4', {'value': value, 'cfg': cfg or {}})

    def DateField5(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/DateField5 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'DateField5', {'value': value, 'cfg': cfg or {}})

    def DateField6(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/DateField6 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'DateField6', {'value': value, 'cfg': cfg or {}})

    def FileField1(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/FileField1 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'FileField1', {'value': value, 'cfg': cfg or {}})

    def FileField2(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/FileField2 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'FileField2', {'value': value, 'cfg': cfg or {}})

    def FileField3(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/FileField3 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'FileField3', {'value': value, 'cfg': cfg or {}})

    def FileField4(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/FileField4 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'FileField4', {'value': value, 'cfg': cfg or {}})

    def FileField5(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/FileField5 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'FileField5', {'value': value, 'cfg': cfg or {}})

    def FileField6(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/FileField6 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'FileField6', {'value': value, 'cfg': cfg or {}})

    def MultiSelect1(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/MultiSelect1 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'MultiSelect1', {'value': value, 'cfg': cfg or {}})

    def MultiSelect2(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/MultiSelect2 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'MultiSelect2', {'value': value, 'cfg': cfg or {}})

    def MultiSelect3(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/MultiSelect3 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'MultiSelect3', {'value': value, 'cfg': cfg or {}})

    def MultiSelect4(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/MultiSelect4 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'MultiSelect4', {'value': value, 'cfg': cfg or {}})

    def MultiSelect5(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/MultiSelect5 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'MultiSelect5', {'value': value, 'cfg': cfg or {}})

    def MultiSelect6(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/MultiSelect6 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'MultiSelect6', {'value': value, 'cfg': cfg or {}})

    def NumberField1(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/NumberField1 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'NumberField1', {'value': value, 'cfg': cfg or {}})

    def NumberField2(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/NumberField2 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'NumberField2', {'value': value, 'cfg': cfg or {}})

    def NumberField3(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/NumberField3 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'NumberField3', {'value': value, 'cfg': cfg or {}})

    def NumberField4(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/NumberField4 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'NumberField4', {'value': value, 'cfg': cfg or {}})

    def NumberField5(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/NumberField5 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'NumberField5', {'value': value, 'cfg': cfg or {}})

    def NumberField6(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/NumberField6 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'NumberField6', {'value': value, 'cfg': cfg or {}})

    def PasswordField1(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/PasswordField1 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'PasswordField1', {'value': value, 'cfg': cfg or {}})

    def PasswordField2(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/PasswordField2 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'PasswordField2', {'value': value, 'cfg': cfg or {}})

    def PasswordField3(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/PasswordField3 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'PasswordField3', {'value': value, 'cfg': cfg or {}})

    def PasswordField4(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/PasswordField4 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'PasswordField4', {'value': value, 'cfg': cfg or {}})

    def PasswordField5(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/PasswordField5 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'PasswordField5', {'value': value, 'cfg': cfg or {}})

    def PasswordField6(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/PasswordField6 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'PasswordField6', {'value': value, 'cfg': cfg or {}})

    def RadioField1(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RadioField1 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RadioField1', {'value': value, 'cfg': cfg or {}})

    def RadioField2(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RadioField2 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RadioField2', {'value': value, 'cfg': cfg or {}})

    def RadioField3(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RadioField3 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RadioField3', {'value': value, 'cfg': cfg or {}})

    def RadioField4(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RadioField4 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RadioField4', {'value': value, 'cfg': cfg or {}})

    def RadioField5(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RadioField5 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RadioField5', {'value': value, 'cfg': cfg or {}})

    def RadioField6(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RadioField6 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RadioField6', {'value': value, 'cfg': cfg or {}})

    def RichTextField1(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RichTextField1 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RichTextField1', {'value': value, 'cfg': cfg or {}})

    def RichTextField2(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RichTextField2 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RichTextField2', {'value': value, 'cfg': cfg or {}})

    def RichTextField3(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RichTextField3 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RichTextField3', {'value': value, 'cfg': cfg or {}})

    def RichTextField4(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RichTextField4 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RichTextField4', {'value': value, 'cfg': cfg or {}})

    def RichTextField5(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RichTextField5 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RichTextField5', {'value': value, 'cfg': cfg or {}})

    def RichTextField6(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/RichTextField6 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'RichTextField6', {'value': value, 'cfg': cfg or {}})

    def SelectField1(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/SelectField1 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'SelectField1', {'value': value, 'cfg': cfg or {}})

    def SelectField2(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/SelectField2 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'SelectField2', {'value': value, 'cfg': cfg or {}})

    def SelectField3(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/SelectField3 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'SelectField3', {'value': value, 'cfg': cfg or {}})

    def SelectField4(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/SelectField4 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'SelectField4', {'value': value, 'cfg': cfg or {}})

    def SelectField5(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/SelectField5 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'SelectField5', {'value': value, 'cfg': cfg or {}})

    def SelectField6(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/SelectField6 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'SelectField6', {'value': value, 'cfg': cfg or {}})

    def TextField1(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/TextField1 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'TextField1', {'value': value, 'cfg': cfg or {}})

    def TextField2(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/TextField2 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'TextField2', {'value': value, 'cfg': cfg or {}})

    def TextField3(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/TextField3 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'TextField3', {'value': value, 'cfg': cfg or {}})

    def TextField4(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/TextField4 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'TextField4', {'value': value, 'cfg': cfg or {}})

    def TextField5(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/TextField5 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'TextField5', {'value': value, 'cfg': cfg or {}})

    def TextField6(self, value: Any = '', cfg: Dict[str, Any] = None) -> str:
        """fields/TextField6 — props: value: Any = '', cfg: Dict[str, Any] = None"""
        return self.build('fields', 'TextField6', {'value': value, 'cfg': cfg or {}})

    def DataGrid1(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/DataGrid1 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'DataGrid1', {'items': items or [], 'cols': cols})

    def DataGrid2(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/DataGrid2 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'DataGrid2', {'items': items or [], 'cols': cols})

    def DataGrid3(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/DataGrid3 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'DataGrid3', {'items': items or [], 'cols': cols})

    def DataGrid4(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/DataGrid4 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'DataGrid4', {'items': items or [], 'cols': cols})

    def DataGrid5(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/DataGrid5 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'DataGrid5', {'items': items or [], 'cols': cols})

    def DataGrid6(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/DataGrid6 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'DataGrid6', {'items': items or [], 'cols': cols})

    def DataGrid7(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/DataGrid7 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'DataGrid7', {'items': items or [], 'cols': cols})

    def DataGrid8(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/DataGrid8 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'DataGrid8', {'items': items or [], 'cols': cols})

    def EditableGrid1(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/EditableGrid1 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'EditableGrid1', {'items': items or [], 'cols': cols})

    def EditableGrid2(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/EditableGrid2 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'EditableGrid2', {'items': items or [], 'cols': cols})

    def EditableGrid3(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/EditableGrid3 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'EditableGrid3', {'items': items or [], 'cols': cols})

    def EditableGrid4(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/EditableGrid4 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'EditableGrid4', {'items': items or [], 'cols': cols})

    def EditableGrid5(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/EditableGrid5 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'EditableGrid5', {'items': items or [], 'cols': cols})

    def EditableGrid6(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/EditableGrid6 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'EditableGrid6', {'items': items or [], 'cols': cols})

    def EditableGrid7(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/EditableGrid7 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'EditableGrid7', {'items': items or [], 'cols': cols})

    def EditableGrid8(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/EditableGrid8 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'EditableGrid8', {'items': items or [], 'cols': cols})

    def KanbanGrid1(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/KanbanGrid1 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'KanbanGrid1', {'items': items or [], 'cols': cols})

    def KanbanGrid2(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/KanbanGrid2 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'KanbanGrid2', {'items': items or [], 'cols': cols})

    def KanbanGrid3(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/KanbanGrid3 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'KanbanGrid3', {'items': items or [], 'cols': cols})

    def KanbanGrid4(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/KanbanGrid4 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'KanbanGrid4', {'items': items or [], 'cols': cols})

    def KanbanGrid5(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/KanbanGrid5 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'KanbanGrid5', {'items': items or [], 'cols': cols})

    def KanbanGrid6(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/KanbanGrid6 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'KanbanGrid6', {'items': items or [], 'cols': cols})

    def KanbanGrid7(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/KanbanGrid7 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'KanbanGrid7', {'items': items or [], 'cols': cols})

    def KanbanGrid8(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/KanbanGrid8 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'KanbanGrid8', {'items': items or [], 'cols': cols})

    def TreeGrid1(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/TreeGrid1 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'TreeGrid1', {'items': items or [], 'cols': cols})

    def TreeGrid2(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/TreeGrid2 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'TreeGrid2', {'items': items or [], 'cols': cols})

    def TreeGrid3(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/TreeGrid3 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'TreeGrid3', {'items': items or [], 'cols': cols})

    def TreeGrid4(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/TreeGrid4 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'TreeGrid4', {'items': items or [], 'cols': cols})

    def TreeGrid5(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/TreeGrid5 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'TreeGrid5', {'items': items or [], 'cols': cols})

    def TreeGrid6(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/TreeGrid6 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'TreeGrid6', {'items': items or [], 'cols': cols})

    def TreeGrid7(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/TreeGrid7 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'TreeGrid7', {'items': items or [], 'cols': cols})

    def TreeGrid8(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/TreeGrid8 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'TreeGrid8', {'items': items or [], 'cols': cols})

    def VirtualGrid1(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/VirtualGrid1 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'VirtualGrid1', {'items': items or [], 'cols': cols})

    def VirtualGrid2(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/VirtualGrid2 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'VirtualGrid2', {'items': items or [], 'cols': cols})

    def VirtualGrid3(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/VirtualGrid3 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'VirtualGrid3', {'items': items or [], 'cols': cols})

    def VirtualGrid4(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/VirtualGrid4 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'VirtualGrid4', {'items': items or [], 'cols': cols})

    def VirtualGrid5(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/VirtualGrid5 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'VirtualGrid5', {'items': items or [], 'cols': cols})

    def VirtualGrid6(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/VirtualGrid6 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'VirtualGrid6', {'items': items or [], 'cols': cols})

    def VirtualGrid7(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/VirtualGrid7 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'VirtualGrid7', {'items': items or [], 'cols': cols})

    def VirtualGrid8(self, items: List[Any] = None, cols: int = 3) -> str:
        """grids/VirtualGrid8 — props: items: List[Any] = None, cols: int = 3"""
        return self.build('grids', 'VirtualGrid8', {'items': items or [], 'cols': cols})

    def AdvancedSearch1(self, query: str = '') -> str:
        """search/AdvancedSearch1 — props: query: str = ''"""
        return self.build('search', 'AdvancedSearch1', {'query': query})

    def AdvancedSearch2(self, query: str = '') -> str:
        """search/AdvancedSearch2 — props: query: str = ''"""
        return self.build('search', 'AdvancedSearch2', {'query': query})

    def AdvancedSearch3(self, query: str = '') -> str:
        """search/AdvancedSearch3 — props: query: str = ''"""
        return self.build('search', 'AdvancedSearch3', {'query': query})

    def AdvancedSearch4(self, query: str = '') -> str:
        """search/AdvancedSearch4 — props: query: str = ''"""
        return self.build('search', 'AdvancedSearch4', {'query': query})

    def AdvancedSearch5(self, query: str = '') -> str:
        """search/AdvancedSearch5 — props: query: str = ''"""
        return self.build('search', 'AdvancedSearch5', {'query': query})

    def AdvancedSearch6(self, query: str = '') -> str:
        """search/AdvancedSearch6 — props: query: str = ''"""
        return self.build('search', 'AdvancedSearch6', {'query': query})

    def AdvancedSearch7(self, query: str = '') -> str:
        """search/AdvancedSearch7 — props: query: str = ''"""
        return self.build('search', 'AdvancedSearch7', {'query': query})

    def FilterSearch1(self, query: str = '') -> str:
        """search/FilterSearch1 — props: query: str = ''"""
        return self.build('search', 'FilterSearch1', {'query': query})

    def FilterSearch2(self, query: str = '') -> str:
        """search/FilterSearch2 — props: query: str = ''"""
        return self.build('search', 'FilterSearch2', {'query': query})

    def FilterSearch3(self, query: str = '') -> str:
        """search/FilterSearch3 — props: query: str = ''"""
        return self.build('search', 'FilterSearch3', {'query': query})

    def FilterSearch4(self, query: str = '') -> str:
        """search/FilterSearch4 — props: query: str = ''"""
        return self.build('search', 'FilterSearch4', {'query': query})

    def FilterSearch5(self, query: str = '') -> str:
        """search/FilterSearch5 — props: query: str = ''"""
        return self.build('search', 'FilterSearch5', {'query': query})

    def FilterSearch6(self, query: str = '') -> str:
        """search/FilterSearch6 — props: query: str = ''"""
        return self.build('search', 'FilterSearch6', {'query': query})

    def FilterSearch7(self, query: str = '') -> str:
        """search/FilterSearch7 — props: query: str = ''"""
        return self.build('search', 'FilterSearch7', {'query': query})

    def GlobalSearch1(self, query: str = '') -> str:
        """search/GlobalSearch1 — props: query: str = ''"""
        return self.build('search', 'GlobalSearch1', {'query': query})

    def GlobalSearch2(self, query: str = '') -> str:
        """search/GlobalSearch2 — props: query: str = ''"""
        return self.build('search', 'GlobalSearch2', {'query': query})

    def GlobalSearch3(self, query: str = '') -> str:
        """search/GlobalSearch3 — props: query: str = ''"""
        return self.build('search', 'GlobalSearch3', {'query': query})

    def GlobalSearch4(self, query: str = '') -> str:
        """search/GlobalSearch4 — props: query: str = ''"""
        return self.build('search', 'GlobalSearch4', {'query': query})

    def GlobalSearch5(self, query: str = '') -> str:
        """search/GlobalSearch5 — props: query: str = ''"""
        return self.build('search', 'GlobalSearch5', {'query': query})

    def GlobalSearch6(self, query: str = '') -> str:
        """search/GlobalSearch6 — props: query: str = ''"""
        return self.build('search', 'GlobalSearch6', {'query': query})

    def GlobalSearch7(self, query: str = '') -> str:
        """search/GlobalSearch7 — props: query: str = ''"""
        return self.build('search', 'GlobalSearch7', {'query': query})

    def LiveSearch1(self, query: str = '') -> str:
        """search/LiveSearch1 — props: query: str = ''"""
        return self.build('search', 'LiveSearch1', {'query': query})

    def LiveSearch2(self, query: str = '') -> str:
        """search/LiveSearch2 — props: query: str = ''"""
        return self.build('search', 'LiveSearch2', {'query': query})

    def LiveSearch3(self, query: str = '') -> str:
        """search/LiveSearch3 — props: query: str = ''"""
        return self.build('search', 'LiveSearch3', {'query': query})

    def LiveSearch4(self, query: str = '') -> str:
        """search/LiveSearch4 — props: query: str = ''"""
        return self.build('search', 'LiveSearch4', {'query': query})

    def LiveSearch5(self, query: str = '') -> str:
        """search/LiveSearch5 — props: query: str = ''"""
        return self.build('search', 'LiveSearch5', {'query': query})

    def LiveSearch6(self, query: str = '') -> str:
        """search/LiveSearch6 — props: query: str = ''"""
        return self.build('search', 'LiveSearch6', {'query': query})

    def LiveSearch7(self, query: str = '') -> str:
        """search/LiveSearch7 — props: query: str = ''"""
        return self.build('search', 'LiveSearch7', {'query': query})

    def SearchBar1(self, query: str = '') -> str:
        """search/SearchBar1 — props: query: str = ''"""
        return self.build('search', 'SearchBar1', {'query': query})

    def SearchBar2(self, query: str = '') -> str:
        """search/SearchBar2 — props: query: str = ''"""
        return self.build('search', 'SearchBar2', {'query': query})

    def SearchBar3(self, query: str = '') -> str:
        """search/SearchBar3 — props: query: str = ''"""
        return self.build('search', 'SearchBar3', {'query': query})

    def SearchBar4(self, query: str = '') -> str:
        """search/SearchBar4 — props: query: str = ''"""
        return self.build('search', 'SearchBar4', {'query': query})

    def SearchBar5(self, query: str = '') -> str:
        """search/SearchBar5 — props: query: str = ''"""
        return self.build('search', 'SearchBar5', {'query': query})

    def SearchBar6(self, query: str = '') -> str:
        """search/SearchBar6 — props: query: str = ''"""
        return self.build('search', 'SearchBar6', {'query': query})

    def SearchBar7(self, query: str = '') -> str:
        """search/SearchBar7 — props: query: str = ''"""
        return self.build('search', 'SearchBar7', {'query': query})

    def DataTable1(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/DataTable1 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'DataTable1', {'rows': rows or [], 'cols': cols or []})

    def DataTable2(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/DataTable2 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'DataTable2', {'rows': rows or [], 'cols': cols or []})

    def DataTable3(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/DataTable3 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'DataTable3', {'rows': rows or [], 'cols': cols or []})

    def DataTable4(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/DataTable4 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'DataTable4', {'rows': rows or [], 'cols': cols or []})

    def DataTable5(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/DataTable5 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'DataTable5', {'rows': rows or [], 'cols': cols or []})

    def DataTable6(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/DataTable6 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'DataTable6', {'rows': rows or [], 'cols': cols or []})

    def DataTable7(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/DataTable7 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'DataTable7', {'rows': rows or [], 'cols': cols or []})

    def DataTable8(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/DataTable8 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'DataTable8', {'rows': rows or [], 'cols': cols or []})

    def EditableTable1(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/EditableTable1 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'EditableTable1', {'rows': rows or [], 'cols': cols or []})

    def EditableTable2(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/EditableTable2 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'EditableTable2', {'rows': rows or [], 'cols': cols or []})

    def EditableTable3(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/EditableTable3 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'EditableTable3', {'rows': rows or [], 'cols': cols or []})

    def EditableTable4(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/EditableTable4 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'EditableTable4', {'rows': rows or [], 'cols': cols or []})

    def EditableTable5(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/EditableTable5 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'EditableTable5', {'rows': rows or [], 'cols': cols or []})

    def EditableTable6(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/EditableTable6 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'EditableTable6', {'rows': rows or [], 'cols': cols or []})

    def EditableTable7(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/EditableTable7 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'EditableTable7', {'rows': rows or [], 'cols': cols or []})

    def EditableTable8(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/EditableTable8 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'EditableTable8', {'rows': rows or [], 'cols': cols or []})

    def PivotTable1(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/PivotTable1 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'PivotTable1', {'rows': rows or [], 'cols': cols or []})

    def PivotTable2(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/PivotTable2 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'PivotTable2', {'rows': rows or [], 'cols': cols or []})

    def PivotTable3(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/PivotTable3 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'PivotTable3', {'rows': rows or [], 'cols': cols or []})

    def PivotTable4(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/PivotTable4 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'PivotTable4', {'rows': rows or [], 'cols': cols or []})

    def PivotTable5(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/PivotTable5 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'PivotTable5', {'rows': rows or [], 'cols': cols or []})

    def PivotTable6(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/PivotTable6 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'PivotTable6', {'rows': rows or [], 'cols': cols or []})

    def PivotTable7(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/PivotTable7 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'PivotTable7', {'rows': rows or [], 'cols': cols or []})

    def PivotTable8(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/PivotTable8 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'PivotTable8', {'rows': rows or [], 'cols': cols or []})

    def SortableTable1(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/SortableTable1 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'SortableTable1', {'rows': rows or [], 'cols': cols or []})

    def SortableTable2(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/SortableTable2 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'SortableTable2', {'rows': rows or [], 'cols': cols or []})

    def SortableTable3(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/SortableTable3 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'SortableTable3', {'rows': rows or [], 'cols': cols or []})

    def SortableTable4(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/SortableTable4 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'SortableTable4', {'rows': rows or [], 'cols': cols or []})

    def SortableTable5(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/SortableTable5 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'SortableTable5', {'rows': rows or [], 'cols': cols or []})

    def SortableTable6(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/SortableTable6 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'SortableTable6', {'rows': rows or [], 'cols': cols or []})

    def SortableTable7(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/SortableTable7 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'SortableTable7', {'rows': rows or [], 'cols': cols or []})

    def SortableTable8(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/SortableTable8 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'SortableTable8', {'rows': rows or [], 'cols': cols or []})

    def VirtualTable1(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/VirtualTable1 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'VirtualTable1', {'rows': rows or [], 'cols': cols or []})

    def VirtualTable2(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/VirtualTable2 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'VirtualTable2', {'rows': rows or [], 'cols': cols or []})

    def VirtualTable3(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/VirtualTable3 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'VirtualTable3', {'rows': rows or [], 'cols': cols or []})

    def VirtualTable4(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/VirtualTable4 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'VirtualTable4', {'rows': rows or [], 'cols': cols or []})

    def VirtualTable5(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/VirtualTable5 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'VirtualTable5', {'rows': rows or [], 'cols': cols or []})

    def VirtualTable6(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/VirtualTable6 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'VirtualTable6', {'rows': rows or [], 'cols': cols or []})

    def VirtualTable7(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/VirtualTable7 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'VirtualTable7', {'rows': rows or [], 'cols': cols or []})

    def VirtualTable8(self, rows: List[Dict[str, Any]] = None, cols: List[str] = None) -> str:
        """tables/VirtualTable8 — props: rows: List[Dict[str, Any]] = None, cols: List[str] = None"""
        return self.build('tables', 'VirtualTable8', {'rows': rows or [], 'cols': cols or []})

    def CalendarView1(self, props: Dict[str, Any] = None) -> str:
        """views/CalendarView1 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CalendarView1', props or {})

    def CalendarView2(self, props: Dict[str, Any] = None) -> str:
        """views/CalendarView2 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CalendarView2', props or {})

    def CalendarView3(self, props: Dict[str, Any] = None) -> str:
        """views/CalendarView3 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CalendarView3', props or {})

    def CalendarView4(self, props: Dict[str, Any] = None) -> str:
        """views/CalendarView4 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CalendarView4', props or {})

    def CalendarView5(self, props: Dict[str, Any] = None) -> str:
        """views/CalendarView5 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CalendarView5', props or {})

    def CalendarView6(self, props: Dict[str, Any] = None) -> str:
        """views/CalendarView6 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CalendarView6', props or {})

    def CalendarView7(self, props: Dict[str, Any] = None) -> str:
        """views/CalendarView7 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CalendarView7', props or {})

    def CardView1(self, props: Dict[str, Any] = None) -> str:
        """views/CardView1 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CardView1', props or {})

    def CardView2(self, props: Dict[str, Any] = None) -> str:
        """views/CardView2 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CardView2', props or {})

    def CardView3(self, props: Dict[str, Any] = None) -> str:
        """views/CardView3 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CardView3', props or {})

    def CardView4(self, props: Dict[str, Any] = None) -> str:
        """views/CardView4 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CardView4', props or {})

    def CardView5(self, props: Dict[str, Any] = None) -> str:
        """views/CardView5 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CardView5', props or {})

    def CardView6(self, props: Dict[str, Any] = None) -> str:
        """views/CardView6 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CardView6', props or {})

    def CardView7(self, props: Dict[str, Any] = None) -> str:
        """views/CardView7 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'CardView7', props or {})

    def DataView1(self, props: Dict[str, Any] = None) -> str:
        """views/DataView1 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DataView1', props or {})

    def DataView2(self, props: Dict[str, Any] = None) -> str:
        """views/DataView2 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DataView2', props or {})

    def DataView3(self, props: Dict[str, Any] = None) -> str:
        """views/DataView3 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DataView3', props or {})

    def DataView4(self, props: Dict[str, Any] = None) -> str:
        """views/DataView4 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DataView4', props or {})

    def DataView5(self, props: Dict[str, Any] = None) -> str:
        """views/DataView5 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DataView5', props or {})

    def DataView6(self, props: Dict[str, Any] = None) -> str:
        """views/DataView6 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DataView6', props or {})

    def DataView7(self, props: Dict[str, Any] = None) -> str:
        """views/DataView7 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DataView7', props or {})

    def DetailView1(self, props: Dict[str, Any] = None) -> str:
        """views/DetailView1 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DetailView1', props or {})

    def DetailView2(self, props: Dict[str, Any] = None) -> str:
        """views/DetailView2 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DetailView2', props or {})

    def DetailView3(self, props: Dict[str, Any] = None) -> str:
        """views/DetailView3 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DetailView3', props or {})

    def DetailView4(self, props: Dict[str, Any] = None) -> str:
        """views/DetailView4 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DetailView4', props or {})

    def DetailView5(self, props: Dict[str, Any] = None) -> str:
        """views/DetailView5 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DetailView5', props or {})

    def DetailView6(self, props: Dict[str, Any] = None) -> str:
        """views/DetailView6 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DetailView6', props or {})

    def DetailView7(self, props: Dict[str, Any] = None) -> str:
        """views/DetailView7 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'DetailView7', props or {})

    def GalleryView1(self, props: Dict[str, Any] = None) -> str:
        """views/GalleryView1 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'GalleryView1', props or {})

    def GalleryView2(self, props: Dict[str, Any] = None) -> str:
        """views/GalleryView2 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'GalleryView2', props or {})

    def GalleryView3(self, props: Dict[str, Any] = None) -> str:
        """views/GalleryView3 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'GalleryView3', props or {})

    def GalleryView4(self, props: Dict[str, Any] = None) -> str:
        """views/GalleryView4 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'GalleryView4', props or {})

    def GalleryView5(self, props: Dict[str, Any] = None) -> str:
        """views/GalleryView5 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'GalleryView5', props or {})

    def GalleryView6(self, props: Dict[str, Any] = None) -> str:
        """views/GalleryView6 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'GalleryView6', props or {})

    def GalleryView7(self, props: Dict[str, Any] = None) -> str:
        """views/GalleryView7 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'GalleryView7', props or {})

    def KanbanView1(self, props: Dict[str, Any] = None) -> str:
        """views/KanbanView1 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'KanbanView1', props or {})

    def KanbanView2(self, props: Dict[str, Any] = None) -> str:
        """views/KanbanView2 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'KanbanView2', props or {})

    def KanbanView3(self, props: Dict[str, Any] = None) -> str:
        """views/KanbanView3 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'KanbanView3', props or {})

    def KanbanView4(self, props: Dict[str, Any] = None) -> str:
        """views/KanbanView4 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'KanbanView4', props or {})

    def KanbanView5(self, props: Dict[str, Any] = None) -> str:
        """views/KanbanView5 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'KanbanView5', props or {})

    def KanbanView6(self, props: Dict[str, Any] = None) -> str:
        """views/KanbanView6 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'KanbanView6', props or {})

    def KanbanView7(self, props: Dict[str, Any] = None) -> str:
        """views/KanbanView7 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'KanbanView7', props or {})

    def ListView1(self, props: Dict[str, Any] = None) -> str:
        """views/ListView1 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'ListView1', props or {})

    def ListView2(self, props: Dict[str, Any] = None) -> str:
        """views/ListView2 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'ListView2', props or {})

    def ListView3(self, props: Dict[str, Any] = None) -> str:
        """views/ListView3 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'ListView3', props or {})

    def ListView4(self, props: Dict[str, Any] = None) -> str:
        """views/ListView4 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'ListView4', props or {})

    def ListView5(self, props: Dict[str, Any] = None) -> str:
        """views/ListView5 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'ListView5', props or {})

    def ListView6(self, props: Dict[str, Any] = None) -> str:
        """views/ListView6 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'ListView6', props or {})

    def ListView7(self, props: Dict[str, Any] = None) -> str:
        """views/ListView7 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'ListView7', props or {})

    def MapView1(self, props: Dict[str, Any] = None) -> str:
        """views/MapView1 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'MapView1', props or {})

    def MapView2(self, props: Dict[str, Any] = None) -> str:
        """views/MapView2 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'MapView2', props or {})

    def MapView3(self, props: Dict[str, Any] = None) -> str:
        """views/MapView3 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'MapView3', props or {})

    def MapView4(self, props: Dict[str, Any] = None) -> str:
        """views/MapView4 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'MapView4', props or {})

    def MapView5(self, props: Dict[str, Any] = None) -> str:
        """views/MapView5 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'MapView5', props or {})

    def MapView6(self, props: Dict[str, Any] = None) -> str:
        """views/MapView6 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'MapView6', props or {})

    def MapView7(self, props: Dict[str, Any] = None) -> str:
        """views/MapView7 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'MapView7', props or {})

    def SplitView1(self, props: Dict[str, Any] = None) -> str:
        """views/SplitView1 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'SplitView1', props or {})

    def SplitView2(self, props: Dict[str, Any] = None) -> str:
        """views/SplitView2 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'SplitView2', props or {})

    def SplitView3(self, props: Dict[str, Any] = None) -> str:
        """views/SplitView3 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'SplitView3', props or {})

    def SplitView4(self, props: Dict[str, Any] = None) -> str:
        """views/SplitView4 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'SplitView4', props or {})

    def SplitView5(self, props: Dict[str, Any] = None) -> str:
        """views/SplitView5 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'SplitView5', props or {})

    def SplitView6(self, props: Dict[str, Any] = None) -> str:
        """views/SplitView6 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'SplitView6', props or {})

    def SplitView7(self, props: Dict[str, Any] = None) -> str:
        """views/SplitView7 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'SplitView7', props or {})

    def TimelineView1(self, props: Dict[str, Any] = None) -> str:
        """views/TimelineView1 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'TimelineView1', props or {})

    def TimelineView2(self, props: Dict[str, Any] = None) -> str:
        """views/TimelineView2 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'TimelineView2', props or {})

    def TimelineView3(self, props: Dict[str, Any] = None) -> str:
        """views/TimelineView3 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'TimelineView3', props or {})

    def TimelineView4(self, props: Dict[str, Any] = None) -> str:
        """views/TimelineView4 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'TimelineView4', props or {})

    def TimelineView5(self, props: Dict[str, Any] = None) -> str:
        """views/TimelineView5 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'TimelineView5', props or {})

    def TimelineView6(self, props: Dict[str, Any] = None) -> str:
        """views/TimelineView6 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'TimelineView6', props or {})

    def TimelineView7(self, props: Dict[str, Any] = None) -> str:
        """views/TimelineView7 — props: props: Dict[str, Any] = None"""
        return self.build('views', 'TimelineView7', props or {})

# ── END AUTO-GENERATED ──

# singleton helper
_default: Optional[UIEngine] = None
def get_ui_engine(root: pathlib.Path = COMPONENT_ROOT) -> UIEngine:
    global _default
    if _default is None or _default.root != pathlib.Path(root):
        _default = UIEngine(root)
    return _default
