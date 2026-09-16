"""
rml_python API — FastAPI backend for Report Player
Endpoints:
  GET  /api/rml/metadata?rml=emp_report
  POST /api/rml/preview  {rml, payload}
  POST /api/rml/execute  {rml, payload}
  GET  /api/rml/groups?rml=...&groupBy=...
  GET  /api/css, POST /api/css (shared CSS)
Serves Report Player HTML at / and /report_player.html
"""
from __future__ import annotations
import pathlib
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .compiler import RMLReportCompiler
from .oracle_engine import OracleEngine
from .engine import RMLReportEngine
from .pipeline import OdexPipeline

ROOT = pathlib.Path(__file__).parent
TEMPLATES = ROOT / "templates"
WEB_ROOT = pathlib.Path("C:/urs2/odex/web")
SHARED_CSS = WEB_ROOT / "static" / "shared.css"

app = FastAPI(title="Odex RML Report Engine", version="1.0.0")

# ── RML Registry (load from examples or web) ─────────────────────────────────
# For demo, we register a default RML in memory; also load any .rml files from examples/
RML_DIR = ROOT / "examples"
RML_DIR.mkdir(exist_ok=True)

# Create default example RML if not exists
_default_rml = """<rml>
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
_default_path = RML_DIR / "emp_report.rml"
if not _default_path.exists():
    _default_path.write_text(_default_rml, encoding="utf-8")

def _load_compiler(rml_name: str) -> RMLReportCompiler:
    # Try file by name
    for cand in [RML_DIR / f"{rml_name}.rml", RML_DIR / rml_name, WEB_ROOT / f"{rml_name}.rml", pathlib.Path(rml_name)]:
        if cand.exists():
            return RMLReportCompiler(path=cand)
    # Fallback to default
    if _default_path.exists():
        return RMLReportCompiler(path=_default_path)
    # Last resort: xml_text
    return RMLReportCompiler(xml_text=_default_rml)

def _get_pipeline(rml_name: str) -> OdexPipeline:
    # For demo, use dummy Oracle credentials (will fail to connect, but preview still works)
    # In production, load from config or Vault
    return OdexPipeline(rml_path=RML_DIR / f"{rml_name}.rml" if (RML_DIR / f"{rml_name}.rml").exists() else _default_path)

# ── Models ────────────────────────────────────────────────────────────────────

class PreviewRequest(BaseModel):
    rml: str = "emp_report"
    payload: Dict[str, Any]

class ExecuteRequest(BaseModel):
    rml: str = "emp_report"
    payload: Dict[str, Any]

# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/rml/metadata")
def get_metadata(rml: str = "emp_report"):
    try:
        compiler = _load_compiler(rml)
        return {"metadata": compiler.rpt_metadata(), "columns": [c.to_dict() for c in compiler.columns()]}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/rml/preview")
def preview(req: PreviewRequest):
    try:
        pipe = _get_pipeline(req.rml)
        sql = pipe.preview_sql(req.payload)
        return {"sql": sql, "metadata": pipe.rpt_metadata(), "columns": [c.to_dict() for c in pipe.columns]}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/rml/execute")
def execute(req: ExecuteRequest):
    """
    Executes dynamic report. If Oracle not reachable, returns mock data + SQL for demo.
    Frontend Report Player expects: {rows, total, page, pageSize, totalPages, sql, columns, metadata}
    """
    try:
        pipe = _get_pipeline(req.rml)
        try:
            result = pipe.execute(req.payload)
            return result
        except Exception as db_err:
            # Fallback to mock + preview for demo without Oracle
            sql = pipe.preview_sql(req.payload)
            # Mock rows based on payload table
            mock = [
                {"المعرف": 1, "اسم الموظف": "أحمد القحطاني", "القسم": "تقنية المعلومات", "الراتب (ر.س)": 7500},
                {"المعرف": 2, "اسم الموظف": "محمد العتيبي", "القسم": "المالية", "الراتب (ر.س)": 6200},
            ]
            return {
                "rows": mock,
                "total": len(mock),
                "page": req.payload.get("page", 1),
                "pageSize": req.payload.get("pageSize", 50),
                "totalPages": 1,
                "sql": sql + f"\n-- Mock fallback (DB error: {db_err})",
                "params": {},
                "columns": [c.to_dict() for c in pipe.columns],
                "metadata": pipe.rpt_metadata(),
                "mock": True,
            }
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/rml/groups")
def groups(rml: str = "emp_report", groupBy: str = "القسم", activeTable: str = "employees"):
    try:
        pipe = _get_pipeline(rml)
        # Try real groups, fallback to mock
        try:
            data = pipe.groups(group_by=groupBy, active_table=activeTable)
            return {"groups": data}
        except Exception as e:
            return {"groups": [{"القسم": "تقنية المعلومات", "cnt": 12}, {"القسم": "المالية", "cnt": 8}], "mock": True, "error": str(e)}
    except Exception as e:
        raise HTTPException(400, str(e))

# Shared CSS (editable, system-wide)
@app.get("/api/css", response_class=PlainTextResponse)
def get_css():
    for p in [SHARED_CSS, pathlib.Path("C:/urs2/shared.css")]:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return "/* no css */"

@app.post("/api/css")
async def set_css(request: Request):
    body = await request.body()
    ctype = request.headers.get("content-type", "")
    css = ""
    if "json" in ctype:
        try:
            j = await request.json()
            css = j.get("css", "")
        except:
            css = body.decode()
    else:
        css = body.decode()
    for p in [SHARED_CSS, pathlib.Path("C:/urs2/shared.css"), pathlib.Path("C:/urs2/odex/shared.css"), WEB_ROOT / "static" / "demo.css"]:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(css, encoding="utf-8")
        except: pass
    return {"ok": True, "bytes": len(css)}

# ── Static & Report Player ────────────────────────────────────────────────────

# Serve static components for demo (375)
try:
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")
except: pass
try:
    app.mount("/components", StaticFiles(directory=str(WEB_ROOT / "components")), name="components")
except: pass

@app.get("/", response_class=HTMLResponse)
def root():
    # Serve report player
    for p in [TEMPLATES / "report_player.html", WEB_ROOT / "report_player.html"]:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return "<h1>Odex RML Engine</h1><a href='/report_player.html'>Report Player</a>"

@app.get("/report_player.html", response_class=HTMLResponse)
def report_player():
    for p in [TEMPLATES / "report_player.html", WEB_ROOT / "report_player.html"]:
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise HTTPException(404, "report_player.html not found")

# Also serve demo.html and binding.html via static
@app.get("/demo.html", response_class=HTMLResponse)
def demo():
    p = WEB_ROOT / "demo.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise HTTPException(404)

@app.get("/binding.html", response_class=HTMLResponse)
def binding():
    p = WEB_ROOT / "binding.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise HTTPException(404)
