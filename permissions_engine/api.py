"""
Permissions API — FastAPI for per-file RML/FML permissions
Endpoints:
  GET  /api/permissions?file=emp_report.rml   — get permissions for file
  POST /api/permissions/grant {file, principal, actions}
  POST /api/permissions/revoke {file, principal, actions}
  POST /api/permissions/check {file, principal, roles, action} -> {allowed: bool}
  GET  /api/permissions/list                 — list all files with perms
  POST /api/permissions/sync                — sync DB <-> JSON
"""
from __future__ import annotations
import pathlib
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .engine import PermissionsEngine, FilePermissions, ACE, get_permissions_engine

app = FastAPI(title="Permissions Engine", version="1.0.0")

class GrantRequest(BaseModel):
    file: str
    principal: str  # e.g., "role:hr_manager" or "user:ahmed"
    actions: List[str]
    effect: str = "allow"

class RevokeRequest(BaseModel):
    file: str
    principal: str
    actions: Optional[List[str]] = None

class CheckRequest(BaseModel):
    file: str
    principal: str  # e.g., "ahmed"
    roles: List[str] = []
    action: str
    owner: Optional[str] = None

@app.get("/api/permissions")
def get_permissions(file: str):
    try:
        eng = get_permissions_engine()
        perms = eng.load(file)
        return perms.to_dict()
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/permissions/grant")
def grant(req: GrantRequest):
    try:
        eng = get_permissions_engine()
        eng.grant(req.file, req.principal, req.actions)
        # Also sync to DB if available
        try:
            import os, sys
            sys.path.insert(0, "C:/urs2")
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
            import django
            django.setup()
            from urs.models import FilePermission
            perms = eng.load(req.file)
            FilePermission.from_file_permissions(perms)
        except Exception:
            pass
        return {"ok": True, "file": req.file, "principal": req.principal, "actions": req.actions}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/permissions/revoke")
def revoke(req: RevokeRequest):
    try:
        eng = get_permissions_engine()
        eng.revoke(req.file, req.principal, req.actions)
        try:
            import os, sys
            sys.path.insert(0, "C:/urs2")
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
            import django
            django.setup()
            from urs.models import FilePermission
            perms = eng.load(req.file)
            FilePermission.from_file_permissions(perms)
        except Exception:
            pass
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/permissions/check")
def check(req: CheckRequest):
    try:
        eng = get_permissions_engine()
        allowed = eng.check(req.file, req.principal, req.roles, req.action)
        return {"file": req.file, "principal": req.principal, "roles": req.roles, "action": req.action, "allowed": allowed}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/permissions/list")
def list_files():
    try:
        eng = get_permissions_engine()
        files = eng.list_files()
        # Also include DB files
        try:
            import os, sys
            sys.path.insert(0, "C:/urs2")
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
            import django
            django.setup()
            from urs.models import FilePermission
            db_files = list(FilePermission.objects.values_list("file_name", flat=True))
            for f in db_files:
                if f not in files:
                    files.append(f)
        except Exception:
            pass
        return {"files": sorted(files), "count": len(files)}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/permissions/sync")
def sync():
    """Sync JSON sidecars <-> DB"""
    try:
        eng = get_permissions_engine()
        # Load all files and ensure DB has them
        files = eng.list_files()
        synced = 0
        try:
            import os, sys
            sys.path.insert(0, "C:/urs2")
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
            import django
            django.setup()
            from urs.models import FilePermission
            for fname in files:
                perms = eng.load(fname)
                FilePermission.from_file_permissions(perms)
                synced += 1
            # Also load DB -> JSON for any DB-only files
            for fp in FilePermission.objects.all():
                # Ensure JSON exists
                perms = fp.to_file_permissions()
                eng.save(fp.file_name, perms)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "synced": synced, "files": files}
    except Exception as e:
        raise HTTPException(500, str(e))

# Also expose at /api/permissions/audit
@app.get("/api/permissions/audit")
def audit(file: str):
    try:
        eng = get_permissions_engine()
        return eng.audit(file)
    except Exception as e:
        raise HTTPException(400, str(e))

# ── Drivers (per-user, auto-analyzed) ───────────────────────────────────────

@app.get("/api/drivers")
def list_drivers():
    """List all user drivers (from DB)."""
    try:
        import os, sys
        sys.path.insert(0, "C:/urs2")
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        import django
        django.setup()
        from urs.models import UserDriver
        drivers = []
        for d in UserDriver.objects.select_related("user").all():
            drivers.append({
                "user": d.user.username,
                "rml_count": d.rml_count,
                "fml_count": d.fml_count,
                "generated_at": d.generated_at.isoformat() if d.generated_at else None,
                "file_path": d.file_path,
            })
        return {"drivers": drivers, "count": len(drivers)}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/drivers/{username}")
def get_driver(username: str):
    """Get driver file for a specific user (auto-analyzed RML/FML filtered by permissions)."""
    try:
        import os, sys, pathlib, json
        sys.path.insert(0, "C:/urs2")
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        import django
        django.setup()
        from urs.models import UserDriver
        try:
            d = UserDriver.objects.get(user__username=username)
            return d.driver_json
        except Exception:
            # Fallback to file
            for p in [pathlib.Path(f"C:/urs2/drivers/{username}.json"), pathlib.Path(f"C:/urs2/odex/drivers/{username}.json")]:
                if p.exists():
                    return json.loads(p.read_text(encoding="utf-8"))
            raise HTTPException(404, f"Driver for {username} not found — run POST /api/drivers/generate")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/drivers/generate")
def generate_drivers():
    """Auto-analyze all RML/FML and create/update driver for each user in DB."""
    try:
        from .engine import get_permissions_engine
        eng = get_permissions_engine()
        result = eng.auto_analyze_and_create_drivers(save_to_db=True)
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/drivers/generate/{username}")
def generate_driver_for_user(username: str):
    """Generate driver for a single user."""
    try:
        import os, sys
        sys.path.insert(0, "C:/urs2")
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        import django
        django.setup()
        from django.contrib.auth.models import User
        from .engine import get_permissions_engine
        eng = get_permissions_engine()
        try:
            user = User.objects.get(username=username)
            roles = list(user.groups.values_list("name", flat=True))
            if user.is_superuser:
                roles.append("admin")
        except Exception:
            roles = []
        driver = eng.create_driver_for_user(username, roles, save_to_db=True)
        return {"ok": True, "user": username, "roles": roles, "driver": driver}
    except Exception as e:
        raise HTTPException(400, str(e))
