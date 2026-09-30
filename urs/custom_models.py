"""
Custom JSON table models — custom_models/<model_name>.json.

The forms designer can define tables that do not exist yet ("جدول جديد"):
the definition is stored here as JSON (source of truth), the system reads
JSON like physical tables (tables/columns endpoints merge them, so
models/sync and designers see them), and migrate creates the real PG table.

JSON shape:
{
  "name": "custody", "label": "العهد", "table": "custody",
  "schema": "public", "connection": "2",
  "fields": [{"name": "id", "label": "م", "type": "INTEGER",
              "primary_key": true, "nullable": false, ...}],
  "created_at": "...", "updated_at": "..."
}
Field keys: name, label, type, length?, nullable?=true, required?=false,
default?="", primary_key?=false, unique?=false.
"""
from __future__ import annotations
import datetime as _dt
import json as _json
import re as _re
from pathlib import Path as _P
from typing import Any, Dict, List, Optional, Tuple

try:
    from django.conf import settings as _dj_settings
    _BASE = _P(_dj_settings.BASE_DIR)
except Exception:
    import os as _os
    _BASE = _P(_os.getcwd())

try:
    from fmlk_engine.engine import FMLKFormEngine as _FMLKEng
    PG_TYPE_MAP: Dict[str, str] = dict(_FMLKEng.DATA_TYPE_MAP)
except Exception:
    PG_TYPE_MAP = {
        "VARCHAR": "VARCHAR(255)", "TEXT": "TEXT", "INTEGER": "INTEGER", "INT": "INTEGER",
        "BIGINT": "BIGINT", "NUMERIC": "NUMERIC(18,2)", "DECIMAL": "NUMERIC(18,2)",
        "FLOAT": "DOUBLE PRECISION", "BOOLEAN": "BOOLEAN", "BOOL": "BOOLEAN",
        "DATE": "DATE", "TIME": "TIME", "TIMESTAMP": "TIMESTAMP", "DATETIME": "TIMESTAMP",
        "IMAGE": "TEXT", "MEDIA": "TEXT",
    }

FIELD_TYPES: List[str] = list(PG_TYPE_MAP.keys())
NAME_RE = _re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def base_dir() -> _P:
    p = _BASE / "custom_models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(name: str) -> _P:
    return base_dir() / f"{name}.json"


def _now() -> str:
    try:
        return _dt.datetime.now().isoformat(timespec="seconds")
    except Exception:
        return ""


# ── read ────────────────────────────────────────────────────────────────
def list_models() -> List[Dict[str, Any]]:
    out = []
    try:
        files = sorted(base_dir().glob("*.json"), key=lambda p: p.name.lower())
    except Exception:
        return out
    for p in files:
        try:
            d = _json.loads(p.read_text(encoding="utf-8") or "{}")
        except Exception:
            continue
        if not isinstance(d, dict) or not d.get("name"):
            continue
        out.append({
            "name": d.get("name"), "label": d.get("label") or d.get("name"),
            "table": d.get("table") or d.get("name"),
            "schema": d.get("schema") or "public",
            "connection": str(d.get("connection") or ""),
            "fields": len(d.get("fields") or []),
            "updated_at": d.get("updated_at") or "",
        })
    return out


def get_model(name: str) -> Optional[Dict[str, Any]]:
    nm = (name or "").strip()
    if not NAME_RE.fullmatch(nm):
        return None
    p = _path(nm)
    if not p.is_file():
        return None
    try:
        d = _json.loads(p.read_text(encoding="utf-8") or "{}")
    except Exception:
        return None
    return d if isinstance(d, dict) and d.get("name") else None


def delete_model(name: str) -> bool:
    nm = (name or "").strip()
    if not NAME_RE.fullmatch(nm):
        raise ValueError("اسم الموديل غير صالح")
    p = _path(nm)
    if not p.is_file():
        return False
    p.unlink()
    return True


def _conn_matches(ref: Any, obj) -> bool:
    try:
        r = str(ref or "").strip()
        if not r:
            return False
        return r == str(getattr(obj, "id", "")) or r == str(getattr(obj, "name", "") or "")
    except Exception:
        return False


def tables_for_conn(obj) -> List[Dict[str, Any]]:
    """Custom tables bound to a connection → tables-endpoint shape + custom flag."""
    out = []
    for m in list_models():
        if not _conn_matches(m.get("connection"), obj):
            continue
        tbl = (m.get("table") or m.get("name") or "").strip()
        sch = (m.get("schema") or "public").strip() or "public"
        if not tbl:
            continue
        out.append({"name": tbl, "schema": sch, "full": f"{sch}.{tbl}",
                    "custom": True, "label": m.get("label") or tbl,
                    "model": m.get("name")})
    out.sort(key=lambda t: str(t.get("full") or "").lower())
    return out


def find_for_conn_table(obj, schema: str, table: str) -> Optional[Dict[str, Any]]:
    """Custom model backing (conn, schema.table)? None otherwise."""
    want = (table or "").strip().lower()
    wsch = (schema or "").strip().lower()
    if not want:
        return None
    for m in list_models():
        if not _conn_matches(m.get("connection"), obj):
            continue
        tbl = (m.get("table") or m.get("name") or "").strip()
        if tbl.lower() != want:
            continue
        msch = (m.get("schema") or "public").strip().lower() or "public"
        if wsch and msch != wsch:
            continue
        return get_model(m.get("name") or "")
    return None


def table_columns(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """JSON fields → columns-endpoint shape [{name, db_type, type}]."""
    cols = []
    for f in ((model or {}).get("fields") or []):
        if not isinstance(f, dict):
            continue
        nm = (f.get("name") or "").strip()
        if not nm:
            continue
        cols.append({"name": nm, "db_type": pg_type(f), "type": str(f.get("type") or "VARCHAR").upper(),
                     "label": f.get("label") or nm, "nullable": bool(f.get("nullable", True)),
                     "primary_key": bool(f.get("primary_key", False))})
    return cols


# ── validate + save ─────────────────────────────────────────────────────
def validate_model(data: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """Returns (model, error). Model is normalized + ready to store."""
    if not isinstance(data, dict):
        return None, "بيانات غير صالحة"
    name = (data.get("name") or "").strip()
    if not NAME_RE.fullmatch(name or ""):
        return None, "اسم الموديل: لاتيني + _ فقط (table_en)"
    if len(name) > 40:
        return None, "اسم الموديل طويل (40 حرفاً حداً أقصى)"
    label = (data.get("label") or "").strip()
    if not label:
        return None, "الاسم العربي (label) مطلوب"
    table = (data.get("table") or name).strip()
    if not NAME_RE.fullmatch(table or ""):
        return None, "اسم الجدول: لاتيني + _ فقط"
    schema = (data.get("schema") or "public").strip() or "public"
    if not NAME_RE.fullmatch(schema or ""):
        return None, "اسم السكيما غير صالح"
    fields = data.get("fields") or []
    if not isinstance(fields, list) or not fields:
        return None, "حقل واحد على الأقل مطلوب"
    seen, out_fields = set(), []
    for i, f in enumerate(fields, start=1):
        if not isinstance(f, dict):
            return None, f"الحقل {i}: صيغة غير صالحة"
        nm = (f.get("name") or "").strip()
        if not NAME_RE.fullmatch(nm or ""):
            return None, f"الحقل {i}: اسم برمجي غير صالح (لاتيني + _)"
        if nm.lower() in seen:
            return None, f"اسم الحقل مكرر: {nm}"
        seen.add(nm.lower())
        lb = (f.get("label") or f.get("alias") or "").strip()
        if not lb:
            return None, f"الحقل {nm}: الاسم العربي مطلوب"
        tp = str(f.get("type") or f.get("data_type") or "VARCHAR").upper()
        if tp not in PG_TYPE_MAP:
            return None, f"الحقل {nm}: نوع غير مدعوم {tp}"
        try:
            length = int(f.get("length") or 0) or 0
        except Exception:
            return None, f"الحقل {nm}: الطول غير صالح"
        if length and (length < 1 or length > 10000):
            return None, f"الحقل {nm}: الطول 1..10000"
        try:
            default = f.get("default", "")
            default = "" if default is None else str(default)
        except Exception:
            default = ""
        out_fields.append({
            "name": nm, "label": lb, "type": tp,
            "length": length,
            "nullable": bool(f.get("nullable", True)) and not bool(f.get("required", False))
                        and not bool(f.get("primary_key", False)),
            "required": bool(f.get("required", False)),
            "default": default,
            "primary_key": bool(f.get("primary_key", False)),
            "unique": bool(f.get("unique", False)),
        })
    model = {
        "name": name, "label": label, "table": table, "schema": schema,
        "connection": str(data.get("connection") or "").strip(),
        "description": str(data.get("description") or "").strip(),
        "fields": out_fields,
        "updated_at": _now(),
    }
    return model, ""


def save_model(data: Dict[str, Any], overwrite: bool = False) -> Tuple[Optional[Dict[str, Any]], str, int]:
    """Validate + write JSON. Returns (model, error, status_hint 200|201|409)."""
    model, err = validate_model(data)
    if err:
        return None, err, 400
    p = _path(model["name"])
    old = get_model(model["name"])
    if p.is_file() and not (overwrite or (data.get("overwrite"))):
        return None, "الموديل موجود مسبقاً (أرسل overwrite=true للتحديث)", 409
    if old is not None and old.get("created_at"):
        model["created_at"] = old["created_at"]
    else:
        model["created_at"] = model["updated_at"]
    created = not p.is_file()
    p.write_text(_json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    return model, "", (201 if created else 200)


# ── DDL + migrate (PG) ──────────────────────────────────────────────────
def pg_type(f: Dict[str, Any]) -> str:
    tp = str((f or {}).get("type") or "VARCHAR").upper()
    base = PG_TYPE_MAP.get(tp, "TEXT")
    if tp == "VARCHAR":
        try:
            n = int((f or {}).get("length") or 0) or 0
        except Exception:
            n = 0
        if 1 <= n <= 10000:
            return f"VARCHAR({n})"
    return base


def _q(ident: str) -> str:
    return '"' + str(ident or "").replace('"', '""') + '"'


def _col_def(f: Dict[str, Any], single_pk: bool) -> str:
    nm = (f.get("name") or "").strip()
    parts = [_q(nm), pg_type(f)]
    if f.get("primary_key"):
        parts.append("PRIMARY KEY" if single_pk else "NOT NULL")
    else:
        if f.get("required") or not f.get("nullable", True):
            parts.append("NOT NULL")
        if f.get("unique"):
            parts.append("UNIQUE")
    d = str(f.get("default") or "")
    if d != "":
        tp = str(f.get("type") or "VARCHAR").upper()
        if tp in ("INTEGER", "INT", "BIGINT", "NUMERIC", "DECIMAL", "FLOAT") and _re.fullmatch(r"-?\d+(\.\d+)?", d):
            parts.append(f"DEFAULT {d}")
        elif tp in ("BOOLEAN", "BOOL") and d.lower() in ("true", "false", "1", "0"):
            parts.append(f"DEFAULT {'TRUE' if d.lower() in ('true', '1') else 'FALSE'}")
        else:
            parts.append(f"DEFAULT '{d.replace(chr(39), chr(39) * 2)}'")
    return " ".join(parts)


def build_ddl(model: Dict[str, Any]) -> str:
    sch = (model.get("schema") or "public").strip() or "public"
    tbl = (model.get("table") or model.get("name") or "custom_model").strip()
    fields = [f for f in (model.get("fields") or []) if isinstance(f, dict) and (f.get("name") or "").strip()]
    pks = [f["name"].strip() for f in fields if f.get("primary_key")]
    defs = [_col_def(f, len(pks) == 1) for f in fields]
    pk_clause = ""
    if len(pks) > 1:
        pk_clause = f",\n  PRIMARY KEY ({', '.join(_q(c) for c in pks)})"
    if not any((f.get("name") or "") == "id" for f in fields) and not pks:
        defs.insert(0, '"id" SERIAL PRIMARY KEY')
    return (f'CREATE SCHEMA IF NOT EXISTS {_q(sch)};\n'
            f'CREATE TABLE IF NOT EXISTS {_q(sch)}.{_q(tbl)} (\n  '
            + ",\n  ".join(defs) + pk_clause + "\n);")


def _pg_params(obj) -> Dict[str, Any]:
    p = dict(host="172.16.10.101", dbname="urs", user="postgres", password="postgres", port=5432)
    try:
        p["host"] = getattr(obj, "host", "") or p["host"]
        p["dbname"] = getattr(obj, "instance", "") or p["dbname"]
        p["user"] = getattr(obj, "user", "") or p["user"]
        p["password"] = getattr(obj, "password", "") or ""
        p["port"] = int(getattr(obj, "port", 0) or 0) or p["port"]
    except Exception:
        pass
    return p


def resolve_connection(ref: Any):
    try:
        from .models import Connection
        r = str(ref or "").strip()
        if not r:
            return None
        if r.isdigit():
            return Connection.objects.filter(id=int(r)).first()
        return Connection.objects.filter(name=r).first()
    except Exception:
        return None


def junction_name(main_table: str, sub_table: str) -> str:
    """Third-table name [main]_[sub]: sanitized, ≤40 chars, deterministic."""
    def _bare(t):
        t = str(t or "").strip()
        if "." in t:
            t = t.split(".")[-1]
        t = _re.sub(r"[^A-Za-z0-9_]", "_", t).strip("_")
        return t or "tbl"
    nm = f"{_bare(main_table)}_{_bare(sub_table)}"
    if len(nm) > 40:
        # keep tail uniqueness: head + trailing hash-ish slice
        import hashlib as _hl
        h = _hl.sha1(nm.encode("utf-8")).hexdigest()[:6]
        nm = nm[:33] + "_" + h
    if not _re.match(r"[A-Za-z_]", nm):
        nm = "j_" + nm
    return nm[:40]


def ensure_junction(main_table: str, sub_table: str, schema: str, connection: Any,
                    customs: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """Build (+save) the junction custom model id/master_id/detail_id/[customs].

    customs: [{name, alias?, data_type?/type?}] designer detail columns.
    Returns (model, error). Caller migrates it.
    """
    jn = junction_name(main_table, sub_table)
    fields: List[Dict[str, Any]] = [
        {"name": "master_id", "label": "السجل الأساسي", "type": "INTEGER",
         "nullable": False, "required": True},
        {"name": "detail_id", "label": "المرجع الفرعي", "type": "INTEGER",
         "nullable": False, "required": True},
    ]
    seen = {"master_id", "detail_id"}
    for c in (customs or []):
        if not isinstance(c, dict):
            continue
        nm = (c.get("name") or "").strip()
        if not nm or not NAME_RE.fullmatch(nm):
            continue
        if nm.lower() in seen:
            return None, f"الحقل '{nm}' يتعارض مع عمود رابط محجوز"
        seen.add(nm.lower())
        tp = str(c.get("data_type") or c.get("type") or "VARCHAR").upper()
        if tp not in PG_TYPE_MAP:
            tp = "VARCHAR"
        fields.append({"name": nm, "label": (c.get("alias") or nm).strip(),
                       "type": tp, "nullable": True})
    model = {"name": jn, "label": f"رابط {main_table} ← {sub_table}",
             "table": jn, "schema": (schema or "public").strip() or "public",
             "connection": str(connection or "").strip(),
             "description": f"جدول رابط تلقائي: {main_table} × {sub_table}",
             "fields": fields}
    saved, err, _code = save_model(model, overwrite=True)
    if err:
        return None, err
    return saved, ""


def table_exists(obj, schema: str, table: str) -> bool:
    try:
        import psycopg2
        p = _pg_params(obj)
        conn = psycopg2.connect(dbname=p["dbname"], user=p["user"], password=p["password"],
                                host=p["host"], port=p["port"])
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                        (schema, table))
            ok = cur.fetchone() is not None
            cur.close()
        finally:
            conn.close()
        return ok
    except Exception:
        return False


def migrate_model(name: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """CREATE TABLE IF NOT EXISTS + ADD missing columns on the model's PG conn."""
    model = get_model(name)
    if model is None:
        return None, "الموديل غير موجود"
    obj = resolve_connection(model.get("connection"))
    if obj is None:
        return None, "اتصال الموديل غير موجود — حدد اتصالاً محلياً صالحاً"
    if str(getattr(obj, "engine", "") or "").lower() != "postgres":
        return None, f"الترحيل يدعم postgres فقط (الاتصال {obj.name}: {obj.engine})"
    sch = (model.get("schema") or "public").strip() or "public"
    tbl = (model.get("table") or model.get("name") or "").strip()
    try:
        import psycopg2
        p = _pg_params(obj)
        conn = psycopg2.connect(dbname=p["dbname"], user=p["user"], password=p["password"],
                                host=p["host"], port=p["port"])
        conn.autocommit = True
        cur = conn.cursor()
        try:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
                        (sch, tbl))
            have = {r[0].lower() for r in cur.fetchall()}
        except Exception:
            have = set()
        ddl = build_ddl(model)
        cur.execute(ddl)
        added = []
        for f in (model.get("fields") or []):
            nm = (f.get("name") or "").strip()
            if not nm or nm.lower() in have:
                continue
            try:
                cur.execute(f'ALTER TABLE {_q(sch)}.{_q(tbl)} ADD COLUMN IF NOT EXISTS {_q(nm)} {pg_type(f)}')
                added.append({"name": nm, "type": pg_type(f)})
            except Exception:
                pass
        cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                    (sch, tbl))
        cols = [{"name": r[0], "db_type": r[1]} for r in cur.fetchall()]
        conn.close()
        return {"ok": True, "model": model["name"], "schema": sch, "table": tbl,
                "ddl": ddl, "columns": cols, "added": added, "custom": True}, ""
    except Exception as e:
        return None, str(e)
