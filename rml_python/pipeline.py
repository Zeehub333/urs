"""
Odex ERP / RML Reporting System — Integrated Pipeline
Connects: RMLReportCompiler (XML) → OracleEngine (DB) → UIEngine (375 components)
Production-ready, secure, with resource management and shared CSS.
"""
from __future__ import annotations
import pathlib
from typing import Any, Dict, List, Optional

from .compiler import RMLReportCompiler, RMLMetadata, RMLColumn
from .oracle_engine import OracleEngine
from .engine import RMLReportEngine

# UIEngine is in odex.web.ui_engine (shared component engine, 375 Material classes)
import sys
sys.path.insert(0, "C:/urs2")
try:
    from odex.web.ui_engine import UIEngine
except ImportError:
    from web.ui_engine import UIEngine  # fallback

# ── Pipeline ──────────────────────────────────────────────────────────────────

class OdexPipeline:
    """
    End-to-End Execution Flow:
    1. Load RML XML via RMLReportCompiler → metadata + columns (direct/computed/fk_lookup/aggregated)
    2. Build & execute SQL via RMLReportEngine + OracleEngine (secure _q, try/finally, OFFSET/FETCH, GROUP BY)
    3. Pass rows + columns + metadata → UIEngine (build, build_table, build_chart, data_view, dashboard)
    """

    def __init__(
        self,
        rml_path: str | pathlib.Path | None = None,
        *,
        xml_text: str | None = None,
        oracle_dsn: str = "localhost:1521/XEPDB1",
        oracle_user: str = "odex",
        oracle_password: str = "odex",
        ui_root: str | pathlib.Path | None = None,
    ):
        # 1. RML Compiler
        if rml_path or xml_text:
            self.compiler = RMLReportCompiler(path=rml_path, xml_text=xml_text)
            self.metadata: Dict[str, Any] = self.compiler.rpt_metadata()
            self.columns: List[RMLColumn] = self.compiler.columns()
        else:
            self.compiler = None
            self.metadata = {}
            self.columns = []

        # 2. Oracle Engine (secure, try/finally)
        self.oracle = OracleEngine(dsn=oracle_dsn, user=oracle_user, password=oracle_password)

        # 3. RML Report Engine (Query Builder)
        if self.compiler:
            self.rml_engine = RMLReportEngine(self.compiler, self.oracle)
        else:
            self.rml_engine = None

        # 4. UI Engine (375 components, shared CSS)
        self.ui = UIEngine(root=ui_root) if ui_root else UIEngine()

    # ── RML Loading ───────────────────────────────────────────────────────

    def load_rml(self, path: str | pathlib.Path, *, xml_text: str | None = None) -> Dict[str, Any]:
        """Load/reload RML and re-init RMLReportEngine. Returns {metadata, columns}."""
        self.compiler = RMLReportCompiler(path=path, xml_text=xml_text)
        self.metadata = self.compiler.rpt_metadata()
        self.columns = self.compiler.columns()
        self.rml_engine = RMLReportEngine(self.compiler, self.oracle)
        return {"metadata": self.metadata, "columns": [c.to_dict() for c in self.columns]}

    def rpt_metadata(self) -> Dict[str, Any]:
        """Core engine function: returns <rpt_metadata> params."""
        if self.compiler:
            return self.compiler.rpt_metadata()
        return self.metadata

    # ── Execution ─────────────────────────────────────────────────────────

    def preview_sql(self, payload: Dict[str, Any]) -> str:
        """Generate Oracle SQL preview for UI (no execution)."""
        if not self.rml_engine:
            raise RuntimeError("No RML loaded — call load_rml() first")
        return self.rml_engine.preview_sql(payload)

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute report with frontend payload:
        {
            activeTable: "employees",
            filters: [{field, op, value, valFrom, valTo}],  # equals/contains/gt/lt/between
            columnFilters: {col: [values]},  # Excel-like
            sort: {column, direction} | [{...}],
            page: 1, pageSize: 50|"all",
            groupBy: "department"
        }
        Returns: {rows, total, page, pageSize, totalPages, sql, params, columns, metadata}
        Handles connection lifecycle and cursor cleanup internally.
        """
        if not self.rml_engine:
            raise RuntimeError("No RML loaded")
        return self.rml_engine.execute(payload)

    def groups(self, group_by: str, filters: List[Dict[str, Any]] | None = None, active_table: str | None = None):
        """Get grouping metrics for sidebar (GROUP BY)."""
        if not self.rml_engine:
            raise RuntimeError("No RML loaded")
        return self.rml_engine.groups(group_by, filters, active_table)

    def detail_rows(self, master_value, page: int = 1, page_size: int | str = 50) -> Dict[str, Any]:
        """Fetch <detail> sub-records for one master key value."""
        if not self.rml_engine:
            raise RuntimeError("No RML loaded")
        return self.rml_engine.detail_rows(master_value, page=page, page_size=page_size)

    def detail_search(self, q, limit_keys: int = 900) -> Dict[str, Any]:
        """Master key values whose detail rows match a search text."""
        if not self.rml_engine:
            raise RuntimeError("No RML loaded")
        return self.rml_engine.detail_search(q, limit_keys=limit_keys)

    def distinct_values(self, column_alias: str, limit: int = 100, search: str = None) -> Dict[str, Any]:
        """Distinct raw values of one display column (status-map helper + filter menu)."""
        if not self.rml_engine:
            raise RuntimeError("No RML loaded")
        return self.rml_engine.distinct_values(column_alias, limit=limit, search=search)

    def group_values(self, column_alias: str, filters=None, bucket=None, limit: int = 500, active_table: str = None) -> Dict[str, Any]:
        """Server-side group keys + counts over ALL records (sidebar)."""
        if not self.rml_engine:
            raise RuntimeError("No RML loaded")
        return self.rml_engine.group_values(column_alias, filters=filters, bucket=bucket, limit=limit, active_table=active_table)

    def inferred_joins(self) -> List[Dict[str, Any]]:
        """Effective JOINs between report tables (explicit + inferred)."""
        if not self.rml_engine:
            raise RuntimeError("No RML loaded")
        return self.rml_engine.inferred_joins()

    # ── UI Binding ────────────────────────────────────────────────────────

    def render_table(self, rows: List[Dict[str, Any]], table_component: str = "DataTable1") -> str:
        """Shortcut: UIEngine DataTable (e.g., DataTable1) — no 'render' in name."""
        # Use UIEngine's 375 defs: DataTable1(rows, cols) etc.
        fn = getattr(self.ui, table_component, None)
        if fn:
            cols = list(rows[0].keys()) if rows else []
            return fn(rows, cols)
        # Fallback to generic build
        return self.ui.build("tables", table_component, {"rows": rows, "cols": list(rows[0].keys()) if rows else []})

    def render_chart(self, rows: List[Dict[str, Any]], chart_component: str = "BarChart1", x_field: str | None = None, y_field: str | None = None) -> str:
        """Shortcut: BarChart1(data, opts) etc."""
        fn = getattr(self.ui, chart_component, None)
        # Map rows to chart data: [{x, y}, ...]
        if rows and x_field and y_field:
            data = [{"x": r.get(x_field), "y": r.get(y_field)} for r in rows]
        elif rows:
            # Auto: first col as x, second numeric as y
            cols = list(rows[0].keys()) if rows else []
            x_field = x_field or cols[0] if cols else "name"
            # Find numeric col
            y_field = y_field or next((c for c in cols if isinstance(rows[0].get(c), (int, float))), cols[1] if len(cols) > 1 else cols[0])
            data = [{"x": r.get(x_field), "y": r.get(y_field, 0)} for r in rows]
        else:
            data = []
        if fn:
            return fn(data, {"title": chart_component})
        return self.ui.build("charts", chart_component, {"data": data, "opts": {"title": chart_component}})

    def render_view(self, view_component: str = "DataView1", props: Dict[str, Any] | None = None) -> str:
        fn = getattr(self.ui, view_component, None)
        if fn:
            return fn(props or {})
        return self.ui.build("views", view_component, props or {})

    def data_view(self, title: str, rows: List[Dict[str, Any]], view: str = "DataView1", table: str = "DataTable1", chart: str = "BarChart1") -> str:
        """Composite: view + chart + table (uses UIEngine.data_view which internally uses build_*)"""
        return self.ui.data_view(title, rows, view=view, table=table, chart=chart)

    def dashboard(self, title: str, datasets: Dict[str, List[Dict[str, Any]]]) -> str:
        return self.ui.dashboard(title, datasets)

    def bind_and_build(self, payload: Dict[str, Any], view: str = "DataView1", table: str = "DataTable1", chart: str = "BarChart1") -> str:
        """
        One-call: execute via RMLReportEngine and build UI via UIEngine.
        Maps frontend filters/sort/page/group directly to Oracle OFFSET/FETCH/GROUP BY.
        """
        result = self.execute(payload)
        rows = result["rows"]
        meta = result["metadata"]
        # Choose visualization based on column types
        # If aggregated columns present → chart, else table
        has_aggregated = any(c.col_type == "aggregated" for c in self.columns)
        # Build view header with metadata
        view_html = self.render_view(view, {"title": meta.get("displayName", meta.get("name", "Report")), "description": f"Category: {meta.get('category','')} • Rows: {result['total']}", "count": result["total"]})
        if has_aggregated or payload.get("groupBy"):
            chart_html = self.render_chart(rows, chart)
            table_html = self.render_table(rows, table)
            return f"<div>{view_html}{chart_html}{table_html}<pre style='background:#1e1e2f;color:#a5ff90;padding:12px;border-radius:8px;overflow:auto;font-size:11px'>{result['sql']}</pre></div>"
        else:
            table_html = self.render_table(rows, table)
            return f"<div>{view_html}{table_html}<pre style='background:#1e1e2f;color:#a5ff90;padding:12px;border-radius:8px;overflow:auto'>{result['sql']}</pre></div>"

    # ── Shared CSS (enterprise theming) ───────────────────────────────────

    def get_shared_css(self) -> str:
        return self.ui.get_shared_css()

    def set_shared_css(self, css: str) -> bool:
        return self.ui.set_shared_css(css)

    def update_css_var(self, var: str, value: str) -> str:
        return self.ui.update_css_var(var, value)

    # ── Convenience: Full page ────────────────────────────────────────────

    def page(self, payload: Dict[str, Any], title: str | None = None) -> str:
        """Full HTML page with shared CSS + report."""
        body = self.bind_and_build(payload)
        return self.ui.generate_page(title or self.metadata.get("displayName", "Odex Report"), body)

# ── Example Usage ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Example RML (would normally be a file like reports/employees.rml)
    rml_xml = """<rml>
        <rpt_metadata name="emp_report" displayName="تقرير بيانات الموظفين المتقدم" category="HR" connection="ORCL_PROD" schema="HR_SYS"/>
        <columns>
            <column id="1" name="id" alias="المعرف" dataType="NUMBER" expr="id" col_type="direct"/>
            <column id="2" name="name" alias="اسم الموظف" expr="name" col_type="direct"/>
            <column id="3" name="department" alias="القسم" expr="department" col_type="direct"/>
            <column id="4" name="salary" alias="الراتب (ر.س)" expr="salary" dataType="NUMBER" col_type="direct"/>
            <column id="5" name="dept_display" alias="القسم (عرض)" expr="department" refTable="departments" refFk="dept_id" refDisplay="dept_name" col_type="fk_lookup"/>
            <column id="6" name="total" alias="العدد" expr="COUNT(*)" col_type="aggregated"/>
        </columns>
    </rml>"""

    pipe = OdexPipeline(xml_text=rml_xml)

    # Frontend payload (from Report Player: Odoo search tags + Excel grid + grouping sidebar)
    payload = {
        "activeTable": "employees",
        "filters": [
            {"field": "القسم", "op": "contains", "value": "تقنية"},
            {"field": "الراتب (ر.س)", "op": "gt", "value": 5000},
        ],
        "columnFilters": {"القسم": ["تقنية المعلومات", "المالية"]},
        "sort": {"column": "المعرف", "direction": "asc"},
        "page": 1,
        "pageSize": 50,
        "groupBy": None,  # or "القسم" for sidebar grouping
    }

    print("Metadata:", pipe.rpt_metadata())
    print("Columns:", [c.alias for c in pipe.columns])
    print("\nSQL Preview:\n", pipe.preview_sql(payload))

    # To execute (requires Oracle connection):
    # result = pipe.execute(payload)
    # print(f"Rows: {len(result['rows'])}/{result['total']}")
    # html = pipe.bind_and_build(payload, view="DataView1", table="DataTable1", chart="BarChart1")
    # pathlib.Path("report.html").write_text(pipe.page(payload), encoding="utf-8")

    # UI direct (375 funcs, without 'render'):
    ui = pipe.ui
    html_btn = ui.PrimaryButton1("حفظ", "alert('saved')")  # no 'render' in name
    html_field = ui.TextField1("أحمد", {"label": "اسم المستخدم"})
    html_table = ui.DataTable1([{"المعرف": 1, "اسم الموظف": "أحمد"}], cols=["المعرف", "اسم الموظف"])
    print("\nUI direct:", html_btn[:80])
