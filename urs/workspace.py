"""Workspace layer — apps live in <workspace>/apps/* with fallback to legacy roots.

Folder layout (created by setup):
    workspace_1/
        workspace.conf      # PRIMARY_CONNECTION + USERS_TABLE (+ display meta)
        settings.py         # WORKSPACE = {name, brand, company, domain, logo,
                            #   country, currency, brand_colors, fiscal_year}
        apps/
            settings/ ...   # moved from odex/system/*

Resolution order everywhere: workspace apps → odex/system → system.
Nothing here imports urs.views/models (safe for config/settings.py).
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from django.conf import settings as _dj
    BASE_DIR = Path(_dj.BASE_DIR)
except Exception:
    import pathlib as _pl
    BASE_DIR = _pl.Path(__file__).resolve().parent.parent

WORKSPACE_GLOB = "workspace_*"
APPS_DIRNAME = "apps"

DEFAULT_WORKSPACE = {
    "name": "workspace_1",
    "brand": "Odex",
    "company": "",
    "domain": "",
    "logo": "",
    "country": "",
    "currency": "",
    "brand_colors": {"primary": "#4f46e5", "accent": "#10b981"},
    "fiscal_year": "",
}


def workspace_dirs():
    """Workspace roots: BASE_DIR/workspace_* containing workspace.conf or apps/ (sorted)."""
    out = []
    try:
        for p in sorted(BASE_DIR.glob(WORKSPACE_GLOB)):
            try:
                if p.is_dir() and ((p / "workspace.conf").is_file() or (p / APPS_DIRNAME).is_dir()):
                    out.append(p)
            except Exception:
                continue
    except Exception:
        pass
    return out


def system_roots():
    """App-container roots in priority order: workspace apps/*, then legacy."""
    roots = []
    for ws in workspace_dirs():
        d = ws / APPS_DIRNAME
        try:
            if d.is_dir():
                roots.append(d)
        except Exception:
            continue
    roots.append(BASE_DIR / "odex" / "system")
    roots.append(BASE_DIR / "system")
    return roots


def app_roots(app_name):
    """Candidate dirs for one app across all roots (priority order)."""
    app = str(app_name or "").strip()
    if not app:
        return []
    return [r / app for r in system_roots()]


def first_app_dir(app_name):
    """First existing app dir or None."""
    for cand in app_roots(app_name):
        try:
            if cand.is_dir():
                return cand
        except Exception:
            continue
    return None


def ensure_app_dir(app_name):
    """Dir for NEW apps: first workspace apps/* (created) else legacy odex/system."""
    app = str(app_name or "").strip()
    wss = workspace_dirs()
    base = (wss[0] / APPS_DIRNAME) if wss else (BASE_DIR / "odex" / "system")
    d = base / app
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_modals_dirs():
    """Existing */settings/modals dirs across roots (for template lookup)."""
    out = []
    for r in system_roots():
        try:
            d = r / "settings" / "modals"
            if d.is_dir():
                out.append(d)
        except Exception:
            continue
    return out


def settings_app_candidates():
    """Candidate settings-app dirs (display_names.json, modals live here)."""
    out = []
    for r in system_roots():
        out.append(r / "settings")
    return out


def names_file(read_only=True):
    """display_names.json path: first existing for read; workspace settings app for write."""
    cands = [d / "display_names.json" for d in settings_app_candidates()]
    for p in cands:
        try:
            if p.is_file():
                return p
        except Exception:
            continue
    if read_only and cands:
        return cands[0]
    if cands:
        try:
            cands[0].parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return cands[0]
    return BASE_DIR / "odex" / "system" / "settings" / "display_names.json"


def documents_dir(app_name, create=True):
    """<app>/documents across roots (auto-create in first workspace app)."""
    app = str(app_name or "").strip()
    for cand in app_roots(app):
        try:
            d = cand / "documents"
            if d.is_dir():
                return d
        except Exception:
            continue
    cands = app_roots(app)
    d = (cands[0] / "documents") if cands else (BASE_DIR / "odex" / "system" / app / "documents")
    if create:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return d


def modals_dir():
    """First existing settings/modals dir (designer fragments)."""
    for r in system_roots():
        try:
            d = r / "settings" / "modals"
            if d.is_dir():
                return d
        except Exception:
            continue
    return BASE_DIR / "odex" / "system" / "settings" / "modals"


def pending_icons_dir(create=True):
    """Transient icon staging: workspace settings app first, else legacy."""
    cands = []
    for r in system_roots():
        cands.append(r / "settings" / ".pending_icons")
    cands.append(BASE_DIR / "odex" / "system" / ".pending_icons")
    for d in cands:
        try:
            if d.is_dir():
                return d
        except Exception:
            continue
    d = cands[0]
    if create:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return d


def read_conf(path):
    """Parse KEY=VALUE conf (# comments, no spaces required around =)."""
    out = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


def load_workspace_settings(ws_path):
    """Load <ws>/settings.py WORKSPACE dict (safe defaults on any error)."""
    data = dict(DEFAULT_WORKSPACE)
    name = Path(ws_path).name
    data["name"] = name
    try:
        import re as _re_hex
        _bc = data.get("brand_colors") or {}

        def _hex(v, fb):
            v = str(v or "").strip()
            return v if _re_hex.fullmatch(r"#[0-9a-fA-F]{6}", v or "") else fb
        data["brand_colors"] = {"primary": _hex(_bc.get("primary"), "#4f46e5"),
                                "accent": _hex(_bc.get("accent"), "#10b981")}
    except Exception:
        pass
    try:
        import importlib.util as _ilu
        sp = Path(ws_path) / "settings.py"
        if sp.is_file():
            spec = _ilu.spec_from_file_location("workspace_settings_%s" % name, str(sp))
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            w = getattr(mod, "WORKSPACE", None)
            if isinstance(w, dict):
                for k, v in w.items():
                    if k == "brand_colors" and isinstance(v, dict):
                        data["brand_colors"] = {**data["brand_colors"], **v}
                    elif v not in (None, ""):
                        data[k] = v
    except Exception:
        pass
    return data


def workspace_info(ws_path):
    """One workspace summary for manager/API (never raises)."""
    ws = Path(ws_path)
    conf = read_conf(ws / "workspace.conf")
    cfg = load_workspace_settings(ws)
    apps, forms, reports = 0, 0, 0
    try:
        ad = ws / APPS_DIRNAME
        if ad.is_dir():
            for sub in sorted(ad.iterdir()):
                try:
                    if not sub.is_dir() or sub.name.startswith("."):
                        continue
                    has_meta = (sub / "metadata.json").is_file()
                    nf = len(list(sub.glob("*.fmlk"))) + len(list(sub.glob("*.fml")))
                    nr = len(list(sub.glob("*.rml")))
                    if has_meta or nf or nr:
                        apps += 1
                        forms += nf
                        reports += nr
                except Exception:
                    continue
    except Exception:
        pass
    return {
        "id": ws.name,
        "name": conf.get("WORKSPACE_NAME") or cfg.get("name") or ws.name,
        "brand": conf.get("BRAND") or cfg.get("brand") or "Odex",
        "company": conf.get("COMPANY") or cfg.get("company") or "",
        "domain": conf.get("DOMAIN") or cfg.get("domain") or "",
        "logo": cfg.get("logo") or "",
        "country": cfg.get("country") or "",
        "currency": cfg.get("currency") or "",
        "brand_colors": cfg.get("brand_colors") or DEFAULT_WORKSPACE["brand_colors"],
        "fiscal_year": cfg.get("fiscal_year") or "",
        "status": (conf.get("STATUS") or "active").strip().lower() or "active",
        "primary_connection": conf.get("PRIMARY_CONNECTION") or "",
        "schema": conf.get("SCHEMA") or "",
        "users_table": conf.get("USERS_TABLE") or "",
        "users_user_column": conf.get("USERS_USER_COLUMN") or "",
        "users_password_column": conf.get("USERS_PASSWORD_COLUMN") or "",
        "counts": {"apps": apps, "forms": forms, "reports": reports},
    }


def workspaces_info():
    """All workspaces for manager/API (stable order)."""
    return [workspace_info(w) for w in workspace_dirs()]


def workspace_status(ws_id):
    """STATUS from workspace.conf (active default). Cheap single-file read."""
    try:
        ws_id = str(ws_id or "").strip()
        if not ws_id:
            return ""
        for ws in workspace_dirs():
            if ws.name == ws_id:
                st = (read_conf(ws / "workspace.conf").get("STATUS") or "active").strip().lower()
                return st or "active"
    except Exception:
        pass
    return ""


def primary_workspace():
    """First workspace or None."""
    wss = workspace_dirs()
    return wss[0] if wss else None


def primary_connection_name():
    """PRIMARY_CONNECTION from first workspace.conf ('' when unset)."""
    try:
        ws = primary_workspace()
        if ws is None:
            return ""
        return read_conf(ws / "workspace.conf").get("PRIMARY_CONNECTION", "").strip()
    except Exception:
        return ""


def primary_users_table():
    """USERS_TABLE from first workspace.conf ('' when unset)."""
    try:
        ws = primary_workspace()
        if ws is None:
            return ""
        return read_conf(ws / "workspace.conf").get("USERS_TABLE", "").strip()
    except Exception:
        return ""
