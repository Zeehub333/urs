"""AD-System — دليل النظام الديناميكي + سجل الأنشطة.

Pure logic (no Django imports at module level so designers/tests can use it
anywhere). Django-dependent helpers receive explicit callables/rows.

Tree kinds:
  forms   — *.fmlk per workspace app (inputs, serials, details)
  reports — *.rml per workspace app (columns, rules+variables, charts)
  rules   — flattened business rules from reports {rule_file, rule_name}
  src:<key> — custom table sources from <ws>/ad_sources.json
              {key, ar, conn, table, schema, seq_col, name_col}
"""
from __future__ import annotations

import json
import pathlib
import re
import time
from typing import Any, Dict, List, Optional

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _ok_ident(v: str) -> bool:
    return bool(v) and bool(_IDENT.fullmatch(str(v).strip()))


# ── workspace app roots ──────────────────────────────────────────────
def ws_apps(ws_dir: pathlib.Path) -> List[pathlib.Path]:
    """App dirs holding models: <ws>/apps/* (+ legacy <ws>/* direct)."""
    try:
        base = pathlib.Path(ws_dir)
        apps = base / "apps"
        if apps.is_dir():
            return sorted([p for p in apps.iterdir() if p.is_dir()])
        return [base]
    except Exception:
        return []


def _display_of(meta: Dict[str, Any], fallback: str) -> str:
    for k in ("displayName", "display_name", "alias", "ar", "name"):
        try:
            v = str(meta.get(k) or "").strip()
        except Exception:
            v = ""
        if v:
            return v
    return fallback


# ── tree scan (files only, no DB) ────────────────────────────────────
def scan_forms(ws_dir: pathlib.Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from fmlk_engine.compiler import FMLKFormCompiler
    except Exception:
        return out
    for app in ws_apps(ws_dir):
        try:
            files = sorted(app.glob("*.fmlk")) + sorted(app.glob("*.fml"))
        except Exception:
            continue
        for p in files:
            try:
                comp = FMLKFormCompiler(path=p)
                meta = comp.fml_metadata() or {}
                fields = []
                serials = []
                for f in (comp.fields() or []):
                    try:
                        d = f.to_dict() if hasattr(f, "to_dict") else dict(f)
                    except Exception:
                        continue
                    nm = str(d.get("name") or "").strip()
                    if not nm:
                        continue
                    fields.append({
                        "name": nm,
                        "alias": str(d.get("alias") or nm),
                        "input_type": str(d.get("inputType") or d.get("input_type") or "text"),
                        "data_type": str(d.get("dataType") or d.get("data_type") or "VARCHAR"),
                    })
                    try:
                        if d.get("serial"):
                            serials.append(nm)
                    except Exception:
                        pass
                details = []
                try:
                    for dd in (comp.details() or []):
                        d = dd.to_dict() if hasattr(dd, "to_dict") else dict(dd)
                        details.append({
                            "table": str(d.get("table") or ""),
                            "alias": str(d.get("alias") or d.get("table") or ""),
                            "columns": [str(c.get("name") or "") for c in (d.get("columns") or [])
                                        if isinstance(c, dict) and str(c.get("name") or "").strip()],
                        })
                except Exception:
                    pass
                actions = []
                try:
                    for a in (comp.actions() or []):
                        aname = str(getattr(a, "name", "") or "").strip()
                        alabel = str(getattr(a, "label", "") or aname).strip()
                        if aname:
                            actions.append({"name": aname, "label": alabel})
                except Exception:
                    actions = []
                out.append({
                    "kind": "form",
                    "file": p.name,
                    "app": app.name,
                    "name_ar": _display_of(meta, p.stem),
                    "table": str(meta.get("table") or ""),
                    "conn": str(meta.get("connection") or ""),
                    "schema": str(meta.get("schema") or ""),
                    "inputs": fields,
                    "serials": serials,
                    "details": details,
                    "actions": actions,
                    "custom_actions": actions,
                })
            except Exception:
                continue
    return out


def scan_reports(ws_dir: pathlib.Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from rml_python.compiler import RMLReportCompiler
    except Exception:
        return out
    for app in ws_apps(ws_dir):
        try:
            files = sorted(app.glob("*.rml"))
        except Exception:
            continue
        for p in files:
            try:
                if p.name.startswith(("__draft_", "__preview_", "__fetchq_")):
                    continue
                comp = RMLReportCompiler(path=p)
                meta = comp.rpt_metadata() or {}
                cols = []
                for c in (comp.columns() or []):
                    try:
                        d = c.to_dict() if hasattr(c, "to_dict") else dict(c)
                    except Exception:
                        continue
                    nm = str(d.get("name") or d.get("alias") or "").strip()
                    if nm:
                        cols.append({"name": nm, "alias": str(d.get("alias") or nm)})
                rules = []
                try:
                    for r in (comp.rules() or []):
                        d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
                        rn = str(d.get("name") or "").strip()
                        if not rn:
                            continue
                        vv = d.get("variables") or []
                        rules.append({
                            "name": rn,
                            "variables": [str(v.get("name") or "") for v in vv
                                          if isinstance(v, dict) and str(v.get("name") or "").strip()],
                        })
                except Exception:
                    pass
                try:
                    ncharts = len(comp.charts() or [])
                except Exception:
                    ncharts = 0
                out.append({
                    "kind": "report",
                    "file": p.name,
                    "app": app.name,
                    "name_ar": _display_of(meta, p.stem),
                    "columns": cols,
                    "rules": rules,
                    "charts": ncharts,
                })
            except Exception:
                continue
    return out


def flatten_rules(reports: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in reports or []:
        for ru in (r.get("rules") or []):
            out.append({"kind": "rule", "rule_file": r.get("file", ""),
                        "app": r.get("app", ""), "rule_name": ru.get("name", ""),
                        "variables": list(ru.get("variables") or [])})
    return out


def collect_sequences(forms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for f in forms or []:
        for s in (f.get("serials") or []):
            out.append({"kind": "sequence", "owner": "%s/%s" % (f.get("app", ""), f.get("file", "")),
                        "table": f.get("table", ""), "name": s})
    return out


# ── custom sources (<ws>/ad_sources.json) ───────────────────────────
def sources_path(ws_dir: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(ws_dir) / "ad_sources.json"


def read_sources(ws_dir: pathlib.Path) -> List[Dict[str, Any]]:
    try:
        p = sources_path(ws_dir)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
        items = data.get("sources") if isinstance(data, dict) else data
        return [s for s in (items or []) if isinstance(s, dict)]
    except Exception:
        return []


def write_sources(ws_dir: pathlib.Path, sources: List[Dict[str, Any]]) -> None:
    p = sources_path(ws_dir)
    p.write_text(json.dumps({"sources": sources}, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_source(s: Dict[str, Any]) -> str:
    """'' when valid, else Arabic error."""
    try:
        key = str(s.get("key") or "").strip()
        if not _ok_ident(key):
            return "مفتاح المصدر لاتيني فقط"
        if not str(s.get("table") or "").strip():
            return "حدد الجدول"
        for k, label in (("seq_col", "عمود التسلسل"), ("name_col", "عمود الاسم")):
            v = str(s.get(k) or "").strip()
            if not _ok_ident(v):
                return "%s غير صالح" % label
        return ""
    except Exception:
        return "مصدر غير صالح"


def builtin_sources() -> List[Dict[str, Any]]:
    return [
        {"key": "forms", "ar": "النماذج", "kind": "builtin"},
        {"key": "reports", "ar": "التقارير", "kind": "builtin"},
        {"key": "rules", "ar": "القواعد", "kind": "builtin"},
    ]


def all_sources(ws_dir: pathlib.Path) -> List[Dict[str, Any]]:
    return builtin_sources() + [s for s in read_sources(ws_dir) if str(s.get("key") or "").strip()]


def tree(ws_dir: pathlib.Path, with_counts: Optional[callable] = None) -> Dict[str, Any]:
    """Full directory: forms/reports/rules/sequences/sources (+counts on demand)."""
    forms = scan_forms(ws_dir)
    reports = scan_reports(ws_dir)
    rules = flatten_rules(reports)
    out: Dict[str, Any] = {
        "forms": forms,
        "reports": reports,
        "rules": rules,
        "sequences": collect_sequences(forms),
        "sources": all_sources(ws_dir),
        "counts": {"forms": len(forms), "reports": len(reports),
                   "rules": len(rules), "sources": len(all_sources(ws_dir))},
    }
    if with_counts is not None:
        try:
            out["record_counts"] = with_counts(forms) or {}
        except Exception:
            out["record_counts"] = {}
    return out


# ── autocomplete shaping (DB-free rows for builtin kinds) ───────────
def search_rows(kind: str, tree_data: Dict[str, Any], q: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    """Rows shaped like grid records for pick-fill (keys match perm detail cols)."""
    qn = str(q or "").strip().lower()
    try:
        limit = max(1, min(int(limit or 20), 200))
    except (TypeError, ValueError):
        limit = 20
    rows: List[Dict[str, Any]] = []
    if kind == "forms":
        for f in (tree_data.get("forms") or []):
            nm = str(f.get("name_ar") or "")
            if qn and qn not in nm.lower() and qn not in str(f.get("file") or "").lower():
                continue
            rows.append({"form_file": f.get("file", ""), "form_name": nm,
                         "ref_key": f.get("file", ""), "ref_name": nm,
                         "app": f.get("app", ""), "table": f.get("table", ""),
                         "custom_actions": f.get("custom_actions", [])})
    elif kind == "reports":
        for r in (tree_data.get("reports") or []):
            nm = str(r.get("name_ar") or "")
            if qn and qn not in nm.lower() and qn not in str(r.get("file") or "").lower():
                continue
            rows.append({"report_file": r.get("file", ""), "report_name": nm,
                         "ref_key": r.get("file", ""), "ref_name": nm,
                         "app": r.get("app", "")})
    elif kind == "rules":
        for ru in (tree_data.get("rules") or []):
            nm = str(ru.get("rule_name") or "")
            if qn and qn not in nm.lower():
                continue
            rows.append({"rule_file": ru.get("rule_file", ""), "rule_name": nm,
                         "ref_key": "%s:%s" % (ru.get("rule_file", ""), nm), "ref_name": nm,
                         "app": ru.get("app", "")})
    elif kind == "sources":
        for s in (tree_data.get("sources") or []):
            key = str(s.get("key") or "")
            nm = str(s.get("ar") or key)
            if qn and qn not in nm.lower() and qn not in key.lower():
                continue
            rows.append({"source": key, "ref_key": key, "ref_name": nm,
                         "table": str(s.get("table") or "")})
    return rows[:limit]


def form_inputs_lookup(tree_data: Dict[str, Any], form_file: str) -> List[Dict[str, Any]]:
    for f in (tree_data.get("forms") or []):
        if str(f.get("file") or "") == str(form_file or ""):
            return list(f.get("inputs") or [])
    return []


def report_columns_lookup(tree_data: Dict[str, Any], report_file: str) -> List[Dict[str, Any]]:
    for r in (tree_data.get("reports") or []):
        if str(r.get("file") or "") == str(report_file or ""):
            return list(r.get("columns") or [])
    return []


def rule_variables_lookup(tree_data: Dict[str, Any], rule_file: str, rule_name: str) -> List[str]:
    for r in (tree_data.get("reports") or []):
        if str(r.get("file") or "") != str(rule_file or ""):
            continue
        for ru in (r.get("rules") or []):
            if str(ru.get("name") or "") == str(rule_name or ""):
                return list(ru.get("variables") or [])
    return []


# ── activity log storage (raw SQL, sqlite + postgres, no migrations) ─
LOG_TABLE = "ad_activity_log"

_LOG_DDL = {
    "sqlite": (
        'CREATE TABLE IF NOT EXISTS "ad_activity_log" ('
        '"id" INTEGER PRIMARY KEY AUTOINCREMENT, '
        '"ts" TIMESTAMP DEFAULT CURRENT_TIMESTAMP, '
        '"ws_id" VARCHAR(120) DEFAULT \'\', '
        '"username" VARCHAR(200) DEFAULT \'\', '
        '"device" VARCHAR(300) DEFAULT \'\', '
        '"action" VARCHAR(40) DEFAULT \'\', '
        '"entity" VARCHAR(120) DEFAULT \'\', '
        '"entity_id" VARCHAR(200) DEFAULT \'\', '
        '"ref_table" VARCHAR(200) DEFAULT \'\', '
        '"details" TEXT DEFAULT \'\')'
    ),
    "postgres": (
        'CREATE TABLE IF NOT EXISTS "ad_activity_log" ('
        '"id" SERIAL PRIMARY KEY, '
        '"ts" TIMESTAMP DEFAULT CURRENT_TIMESTAMP, '
        '"ws_id" VARCHAR(120) DEFAULT \'\', '
        '"username" VARCHAR(200) DEFAULT \'\', '
        '"device" VARCHAR(300) DEFAULT \'\', '
        '"action" VARCHAR(40) DEFAULT \'\', '
        '"entity" VARCHAR(120) DEFAULT \'\', '
        '"entity_id" VARCHAR(200) DEFAULT \'\', '
        '"ref_table" VARCHAR(200) DEFAULT \'\', '
        '"details" TEXT DEFAULT \'\')'
    ),
}


def ensure_log_table() -> bool:
    """CREATE TABLE IF NOT EXISTS on the Django default DB. Never raises."""
    try:
        from django.db import connection as _dj
        vendor = str(getattr(_dj, "vendor", "") or "").lower()
        ddl = _LOG_DDL.get("sqlite" if "sqlite" in vendor else "postgres")
        with _dj.cursor() as cur:
            cur.execute(ddl)
        return True
    except Exception:
        return False


def log(ws_id: str = "", username: str = "", action: str = "", entity: str = "",
        entity_id: str = "", ref_table: str = "", device: str = "",
        details: str = "") -> bool:
    """Append one activity row. Best-effort — never raises."""
    try:
        from django.db import connection as _dj
        vendor = str(getattr(_dj, "vendor", "") or "").lower()
        ph = "?" if "sqlite" in vendor else "%s"
        sql = ('INSERT INTO "ad_activity_log" ("ws_id","username","action","entity",'
               '"entity_id","ref_table","device","details") VALUES '
               "(%s,%s,%s,%s,%s,%s,%s,%s)" % (ph, ph, ph, ph, ph, ph, ph, ph))
        with _dj.cursor() as cur:
            cur.execute(sql, [str(ws_id or "")[:120], str(username or "")[:200],
                              str(action or "")[:40], str(entity or "")[:120],
                              str(entity_id or "")[:200], str(ref_table or "")[:200],
                              str(device or "")[:300], str(details or "")[:2000]])
        return True
    except Exception:
        try:
            if ensure_log_table():
                return log(ws_id, username, action, entity, entity_id,
                           ref_table, device, details)
        except Exception:
            pass
        return False


def query_log(ws_id: str = "", username: str = "", action: str = "",
              entity: str = "", day_from: str = "", day_to: str = "",
              page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    """Filtered log rows (newest first). Never raises."""
    try:
        from django.db import connection as _dj
        vendor = str(getattr(_dj, "vendor", "") or "").lower()
        ph = "?" if "sqlite" in vendor else "%s"
        ensure_log_table()
        where, params = [], []
        if ws_id:
            where.append('"ws_id" = %s' % ph); params.append(ws_id)
        if username:
            where.append('"username" = %s' % ph); params.append(username)
        if action:
            where.append('"action" = %s' % ph); params.append(action)
        if entity:
            where.append('"entity" = %s' % ph); params.append(entity)
        if day_from:
            where.append('date("ts") >= date(%s)' % ph if "sqlite" in vendor
                         else '"ts"::date >= %s::date')
            params.append(day_from)
        if day_to:
            where.append('date("ts") <= date(%s)' % ph if "sqlite" in vendor
                         else '"ts"::date <= %s::date')
            params.append(day_to)
        try:
            page = max(1, int(page or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = max(1, min(int(page_size or 50), 500))
        except (TypeError, ValueError):
            page_size = 50
        w = (" WHERE " + " AND ".join(where)) if where else ""
        with _dj.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM "ad_activity_log"%s' % w, params)
            total = (cur.fetchone() or [0])[0] or 0
            cur.execute('SELECT "id","ts","ws_id","username","device","action","entity",'
                        '"entity_id","ref_table","details" FROM "ad_activity_log"%s '
                        'ORDER BY "id" DESC LIMIT %s OFFSET %s' % (w, ph, ph),
                        params + [page_size, (page - 1) * page_size])
            cols = [d[0] for d in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in (cur.fetchall() or [])]
            for r in rows:
                try:
                    r["ts"] = str(r.get("ts") or "")
                except Exception:
                    pass
        return {"rows": rows, "total": total, "page": page, "pageSize": page_size}
    except Exception:
        return {"rows": [], "total": 0, "page": 1, "pageSize": 50}


def record_info(ref_table: str = "", entity_id: str = "", ws_id: str = "") -> Dict[str, Any]:
    """Creation/modification info of one record for the player info panel."""
    out: Dict[str, Any] = {"created": None, "modified": None, "count": 0}
    try:
        ensure_log_table()
        from django.db import connection as _dj
        vendor = str(getattr(_dj, "vendor", "") or "").lower()
        ph = "?" if "sqlite" in vendor else "%s"
        # match qualified or bare table alike (log may store either form)
        tables = []
        _rt = str(ref_table or "").strip()
        if _rt:
            tables.append(_rt)
            if "." in _rt and _rt.split(".")[-1] not in tables:
                tables.append(_rt.split(".")[-1])
        where, params = [], []
        if tables:
            where.append("(" + " OR ".join(['"ref_table" = %s' % ph] * len(tables)) + ")")
            params.extend(tables)
        if entity_id not in (None, ""):
            where.append('"entity_id" = %s' % ph); params.append(str(entity_id))
        if ws_id:
            where.append('"ws_id" = %s' % ph); params.append(ws_id)
        if not where:
            return out
        w = " WHERE " + " AND ".join(where)
        with _dj.cursor() as cur:
            cur.execute('SELECT "action","username","device","ts" FROM "ad_activity_log"%s '
                        'AND "action" IN (%s,%s) ORDER BY "id" ASC' % (w, ph, ph),
                        params + ["insert", "create"])
            first = cur.fetchone()
            cur.execute('SELECT COUNT(*) FROM "ad_activity_log"%s' % w, params)
            out["count"] = (cur.fetchone() or [0])[0] or 0
            cur.execute('SELECT "action","username","device","ts" FROM "ad_activity_log"%s '
                        'AND ("action"=%s OR "action"=%s) ORDER BY "id" DESC LIMIT 1' % (w, ph, ph),
                        params + ["update", "modify"])
            lastm = cur.fetchone()
        if first:
            out["created"] = {"action": first[0], "username": str(first[1] or ""),
                               "device": str(first[2] or ""), "at": str(first[3] or "")}
        if lastm:
            out["modified"] = {"action": lastm[0], "username": str(lastm[1] or ""),
                                "device": str(lastm[2] or ""), "at": str(lastm[3] or "")}
        return out
    except Exception:
        return out


def now_iso() -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
