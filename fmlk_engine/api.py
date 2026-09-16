"""
fmlk_engine API — FastAPI for Forms Player
Endpoints:
  GET  /api/fmlk/metadata?fml=hr_form
  GET  /api/fmlk/tabs?fml=...
  POST /api/fmlk/preview_insert {fml, data}
  POST /api/fmlk/create {fml, data}
  POST /api/fmlk/update {fml, id, data}
  POST /api/fmlk/delete {fml, id}
  GET  /api/fmlk/records?fml=...&page=&pageSize=
  GET  /api/fmlk/record?fml=...&id=...
"""
from __future__ import annotations
import pathlib
from typing import Any, Dict
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .compiler import FMLKFormCompiler
from .engine import FMLKFormEngine

ROOT = pathlib.Path(__file__).parent
TEMPLATES = ROOT / "templates"
WEB_ROOT = pathlib.Path("C:/urs2/odex/web")
EXAMPLES = ROOT / "examples"
EXAMPLES.mkdir(exist_ok=True)

# Default HR form example (matches image, but generic — not immediately applied, just example)
_default_fml = """<fml>
  <fml_metadata name="hr_form" displayName="نموذج الموظف" category="HR" connection="ORCL_PROD" schema="HR_SYS" table="employees"/>
  <tabs>
    <tab id="basic" name="Basic information" alias="البيانات الأساسية" sort_order="1"/>
    <tab id="work" name="Work" alias="العمل" sort_order="2"/>
    <tab id="personal" name="Personal" alias="الشخصية" sort_order="3"/>
  </tabs>
  <fields>
    <field id="1" name="first_name" alias="اسم First" inputType="text" tab="basic" category="Personal information" position="left" required="true"/>
    <field id="2" name="last_name" alias="اسم" inputType="text" tab="basic" category="Personal information" position="right"/>
    <field id="3" name="civility" alias="Civility" inputType="select" tab="basic" category="Personal information" options="السيد,السيدة"/>
    <field id="4" name="email" alias="البريد الإلكتروني" inputType="email" tab="basic" category="Contact" position="full"/>
    <field id="5" name="work_mobile" alias="Work mobile phone" inputType="phone" tab="basic" category="Work phones" position="left"/>
    <field id="6" name="work_fixed" alias="Work fixed phone" inputType="phone" tab="basic" category="Work phones" position="right"/>
    <field id="7" name="birth_date" alias="تاريخ Birth" inputType="date" tab="basic" category="Personal information"/>
    <field id="8" name="nationality" alias="Citizenship" inputType="select" tab="basic" category="Personal information" refTable="countries" refFk="id" refDisplay="name"/>
  </fields>
</fml>"""
_default_path = EXAMPLES / "hr_form.fmlk"
if not _default_path.exists():
    _default_path.write_text(_default_fml, encoding="utf-8")

def _load_compiler(fml_name: str) -> FMLKFormCompiler:
    for cand in [EXAMPLES / f"{fml_name}.fmlk", EXAMPLES / fml_name, WEB_ROOT / f"{fml_name}.fmlk", pathlib.Path(fml_name)]:
        if cand.exists():
            return FMLKFormCompiler(path=cand)
    return FMLKFormCompiler(path=_default_path)

def _get_engine(fml_name: str) -> FMLKFormEngine:
    comp = _load_compiler(fml_name)
    # Use OracleEngine from rml_python (secure)
    try:
        from rml_python.oracle_engine import OracleEngine
        db = OracleEngine(dsn="localhost:1521/XEPDB1", user="postgres", password="postgres")
    except:
        from rml_python.oracle_engine import OracleEngine as OE
        db = OE(dsn="localhost:1521/XEPDB1", user="postgres", password="postgres")
    return FMLKFormEngine(comp, db)

app = FastAPI(title="Odex FMLK Form Engine", version="1.0.0")

class PreviewRequest(BaseModel):
    fml: str = "hr_form"
    data: Dict[str, Any]

class CreateRequest(BaseModel):
    fml: str = "hr_form"
    data: Dict[str, Any]

class UpdateRequest(BaseModel):
    fml: str = "hr_form"
    id: Any
    data: Dict[str, Any]

class DeleteRequest(BaseModel):
    fml: str = "hr_form"
    id: Any

@app.get("/api/fmlk/metadata")
def get_metadata(fml: str = "hr_form"):
    try:
        comp = _load_compiler(fml)
        return {"metadata": comp.fml_metadata(), "tabs": [t.to_dict() for t in comp.tabs()], "fields": [f.to_dict() for f in comp.fields()]}
    except Exception as e:
        raise HTTPException(400, str(e))

# ── Foreign Keys: dynamic lookup endpoints ──────────────────────────────────
@app.get("/api/fmlk/lookup")
def lookup(fml: str = "hr_form", field: str = "", table: str = "", limit: int = 50, search: str | None = None):
    """Fetch reference data dynamically for fields with ref_table/ref_fk (e.g., nationality -> countries)."""
    if not field and not table:
        raise HTTPException(400, "field or table required (e.g., ?field=nationality)")
    try:
        eng = _get_engine(fml)
        # if field provided → use engine's FK logic
        if field:
            try:
                return eng.get_field_lookup(field_name=field, limit=limit, search=search)
            except ValueError as ve:
                # fallback: if field has no FK but table param given, try generic table lookup
                if table:
                    raise ve
                raise HTTPException(404, str(ve))
        # generic table lookup (without field metadata)
        from rml_python.oracle_engine import _q as q
        # default fk/display
        fk = "id"
        disp = "name"
        # try to infer from field if possible
        tbl = table
        sql = f"SELECT {q(fk)} AS value, {q(disp)} AS label FROM {q(tbl)} FETCH NEXT :lim ROWS ONLY"
        params = {"lim": limit}
        if search:
            sql = f"SELECT {q(fk)} AS value, {q(disp)} AS label FROM {q(tbl)} WHERE LOWER({q(disp)}) LIKE :search FETCH NEXT :lim ROWS ONLY"
            params["search"] = f"%{search.lower()}%"
        try:
            if not eng.db.conn:
                eng.db.connect()
            cur = eng.db._exec(sql, params)
            try:
                cols = [d[0].lower() for d in cur.description] if cur.description else ["value", "label"]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
            finally:
                try: cur.close()
                except: pass
            return {"field": field or table, "refTable": tbl, "options": rows, "source": "db", "sql": sql}
        except Exception as db_e:
            # mock fallback
            mock = [{"value": 1, "label": "الإدارة العامة"}, {"value": 2, "label": "الفرع الرئيسي"}]
            return {"field": field or table, "refTable": tbl, "options": mock, "source": "mock", "error": str(db_e), "sql": sql}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/fmlk/lookups")
def list_lookups(fml: str = "hr_form"):
    """List all fields that have Foreign Key references with their endpoints."""
    try:
        eng = _get_engine(fml)
        return {"fml": fml, "lookups": eng.list_lookups()}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/fmlk/preview_insert")
def preview_insert(req: PreviewRequest):
    try:
        eng = _get_engine(req.fml)
        sql = eng.preview_insert_sql(req.data)
        return {"sql": sql, "metadata": eng.compiler.fml_metadata()}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/fmlk/create")
def create(req: CreateRequest):
    try:
        eng = _get_engine(req.fml)
        try:
            res = eng.create_record(req.data)
            return res
        except Exception as db_e:
            # Mock fallback for demo without Oracle
            sql = eng.preview_insert_sql(req.data)
            return {"ok": True, "mock": True, "rowcount": 1, "sql": sql + f"\n-- Mock (DB: {db_e})", "params": req.data}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/fmlk/update")
def update(req: UpdateRequest):
    try:
        eng = _get_engine(req.fml)
        # Assume id field is 'id'
        pk = {"id": req.id}
        try:
            res = eng.update_record(pk, req.data)
            return res
        except Exception as db_e:
            sql = eng.preview_update_sql(pk, req.data)
            return {"ok": True, "mock": True, "rowcount": 1, "sql": sql + f"\n-- Mock ({db_e})"}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/fmlk/delete")
def delete(req: DeleteRequest):
    try:
        eng = _get_engine(req.fml)
        pk = {"id": req.id}
        try:
            res = eng.delete_record(pk)
            return res
        except Exception as db_e:
            sql = eng.preview_delete_sql(pk)
            return {"ok": True, "mock": True, "sql": sql + f"\n-- Mock ({db_e})"}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/fmlk/records")
def list_records(fml: str = "hr_form", page: int = 1, pageSize: int = 50):
    try:
        eng = _get_engine(fml)
        try:
            res = eng.list_records(page=page, page_size=pageSize)
            return res
        except Exception as db_e:
            # Mock
            mock_rows = [{"id": i, "first_name": f"موظف {i}", "email": f"user{i}@test.com"} for i in range(1, 6)]
            return {"rows": mock_rows, "total": len(mock_rows), "page": page, "pageSize": pageSize, "mock": True, "error": str(db_e)}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/fmlk/record")
def get_record(fml: str = "hr_form", id: str = "1"):
    try:
        eng = _get_engine(fml)
        try:
            row = eng.get_record({"id": id})
            return {"row": row}
        except Exception as db_e:
            return {"row": {"id": id, "first_name": "أحمد", "email": "ahmed@test.com"}, "mock": True}
    except Exception as e:
        raise HTTPException(400, str(e))

# Static and player
try:
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static-fmlk")
except: pass

@app.get("/", response_class=HTMLResponse)
def root():
    for p in [TEMPLATES / "forms_player.html", WEB_ROOT / "forms_player.html"]:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return "<h1>FMLK Forms Player</h1><a href='/forms_player.html'>Forms Player</a>"

@app.get("/forms_player.html", response_class=HTMLResponse)
def forms_player():
    for p in [TEMPLATES / "forms_player.html", WEB_ROOT / "forms_player.html"]:
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise HTTPException(404, "forms_player.html not found")
