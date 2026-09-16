"""
Binding UI (375 components) with Data (4 engines: oracle, postgres, sqlserver, zk)
Each UI function name == JS class name, params == component props, no 'render' in name.
"""
import sys
sys.path.insert(0, "C:/urs2")
from odex.web.ui_engine import UIEngine
from odex.engines.oracle import OracleEngine
from odex.engines.postgres import PostgresEngine
from odex.engines.sqlserver import SqlServerEngine
from odex.engines.zk import ZKEngine

# ── 375 UI functions — examples (all without 'render' in name) ──
# views: DataView1(props), CardView1(props) ... 70
# charts: BarChart1(data, opts), LineChart1(data, opts) ... 70
# buttons: PrimaryButton1(label, on_click) ... 60
# fields: TextField1(value, cfg) ... 60
# search: SearchBar1(query) ... 35
# tables: DataTable1(rows, cols) ... 40
# grids: DataGrid1(items, cols) ... 40

ui = UIEngine()

# ── How to bind UI with Data ──
# 1. Fetch data via any engine's dynamic CRUD (50) + reflection (100) — no static tables
# Example: Oracle dynamic
oracle = OracleEngine(dsn="localhost:1521/XEPDB1", user="odex", password="odex")
# postgres = PostgresEngine(dsn="host=localhost dbname=odex user=postgres password=postgres")
# sqlserver = SqlServerEngine(conn_str="DRIVER={ODBC Driver 18 for SQL Server};SERVER=localhost;DATABASE=odex;UID=sa;PWD=Passw0rd;TrustServerCertificate=yes")
# zk = ZKEngine(ip="192.168.1.201")

def demo_binding():
    # Simulate data (since DB may not be connected in demo)
    sample_rows = [
        {"id": 1, "name": "Alice", "value": 100},
        {"id": 2, "name": "Bob", "value": 200},
        {"id": 3, "name": "Carol", "value": 150},
    ]
    chart_data = [{"x": "Q1", "y": 30}, {"x": "Q2", "y": 50}, {"x": "Q3", "y": 80}]

    # ── Option A: Direct prop binding (each UI function = JS class name) ──
    # No 'render' in name — call like: ui.DataView1({"title": "Sales", ...})
    html_a = ui.DataView1({"title": "DataView1 — Direct", "description": "Direct props", "count": 42})
    html_b = ui.BarChart1(chart_data, {"title": "BarChart1 — Direct"})
    html_c = ui.PrimaryButton1("حفظ", "alert('saved')")
    html_d = ui.TextField1("نص تجريبي", {"label": "اسم المستخدم", "placeholder": "أدخل..."})
    html_e = ui.SearchBar1("demo query")
    html_f = ui.DataTable1(sample_rows, ["id", "name", "value"])
    html_g = ui.DataGrid1(sample_rows, 3)

    # ── Option B: Via generic build (without 'render' in name is build) ──
    # ui.build("views", "DataView1", {"title": "Via build"})
    html_h = ui.build("views", "DataView1", {"title": "Via build()", "count": 99})

    # ── Option C: Bind via engine (dynamic) ──
    # Fetch from DB (if connected) then bind:
    # rows = ui.bind_engine(oracle, table="odex_demo", limit=50)  # uses oracle.list_all/filter dynamically
    # html_i = ui.DataTable1(rows, cols=list(rows[0].keys()) if rows else [])
    # html_j = ui.bind_and_build(oracle, table="odex_demo", view="DataView1", component="DataTable1")
    # For demo, simulate:
    rows = sample_rows  # pretend from engine
    html_i = ui.DataTable1(rows)  # col auto-detect
    html_j = ui.DataView1({"title": f"odex_demo ({len(rows)})", "description": f"From {oracle.__class__.__name__} via bind_engine"})

    # ── Combine into page ──
    body = f"""
    <h2>Binding UI (375) with Data (4 engines) — without 'render' in name</h2>
    <p>Each Python def == JS class name. Example: <code>ui.DataView1(props)</code> ↔ <code>DataView1.js → export class DataView1</code></p>
    <h3>A. Direct prop binding</h3>
    <div style="display:grid;gap:16px">
      <div>{html_a}</div>
      <div>{html_b}</div>
      <div style="display:flex;gap:8px">{html_c} {ui.SecondaryButton1("إلغاء")}</div>
      <div>{html_d}</div>
      <div>{html_e}</div>
      <div>{html_f}</div>
      <div>{html_g}</div>
    </div>
    <h3>B. Via build() (generic, no 'render')</h3>
    <div>{html_h}</div>
    <h3>C. Via engine bind (dynamic table/cols)</h3>
    <div>{html_i}</div>
    <div>{html_j}</div>
    <pre style="background:#f5f5f5;padding:12px;border-radius:8px;overflow:auto">
# Oracle dynamic
rows = UIEngine().bind_engine(OracleEngine(...), table="odex_demo", filters={{"status":"نشط"}}, limit=50)
html = UIEngine().DataTable1(rows, cols=["id","name"])
# Postgres
rows = UIEngine().bind_engine(PostgresEngine(...), table="users")
html = UIEngine().BarChart1([{{"x":r["name"],"y":r["value"]}} for r in rows], {{"title":"Users"}})
# ZK device
rows = UIEngine().bind_engine(ZKEngine(ip="192.168.1.201"), table="users")
html = UIEngine().DataGrid1(rows, cols=3)
    </pre>
    """
    page = ui.generate_page("Binding Demo — UI x Data", body)
    pathlib = __import__("pathlib")
    pathlib.Path("C:/urs2/odex/web/binding.html").write_text(page, encoding="utf-8")
    print("wrote C:/urs2/odex/web/binding.html")
    print("Direct A:", html_a[:80])
    print("Chart B:", html_b[:80])
    print("Table F:", html_f[:80])

if __name__ == "__main__":
    demo_binding()
