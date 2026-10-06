"""Server-side workspace gate: no interaction before entering a workspace.

Any page/API outside the exempt list requires request.session["workspace"]
to hold a known workspace id, otherwise:
  - pages  → redirect to / (workspace manager)
  - /api/* → 403 JSON {workspace_required: True}
Entering works via /?workspace=<id> or /apps/?workspace=<id> (param is
validated, stored in session, then allowed through).
"""
from django.http import JsonResponse
from django.shortcuts import redirect


_EXEMPT_EXACT = frozenset({
    "/",
    "/favicon.ico",
    "/api/master/status/",
    "/api/master/auth/",
})
_EXEMPT_PREFIXES_API = ("/api/workspaces/", "/api/lookup/lists/", "/api/lookup/tables/", "/api/connections/")
_EXEMPT_PREFIXES = (
    "/settings/setup/",
    "/admin/",
    "/static/",
    "/enter/",
)


def _is_exempt(path):
    if path in _EXEMPT_EXACT:
        return True
    if path.startswith(_EXEMPT_PREFIXES_API):
        return True
    return path.startswith(_EXEMPT_PREFIXES)


class WorkspaceGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or "/"
        try:
            from .workspace import (workspaces_info, workspace_status,
                                    set_active_ws, reset_active_ws,
                                    set_active_fiscal_schema, reset_active_fiscal_schema)
        except Exception:
            workspaces_info, workspace_status = None, None
            set_active_ws, reset_active_ws = None, None
            set_active_fiscal_schema, reset_active_fiscal_schema = None, None
        try:
            known = {w.get("id") for w in workspaces_info() if w.get("id")} \
                if workspaces_info else set()
        except Exception:
            known = set()
            workspace_status = None
        # Pin app roots for this request: explicit ?settings= workspace wins,
        # else the held session workspace. Unknown/empty → unscoped.
        try:
            _sid0 = (request.GET.get("settings") or "").strip()
        except Exception:
            _sid0 = ""
        try:
            _held0 = (request.session.get("workspace") or "").strip()
        except Exception:
            _held0 = ""
        _active = None
        try:
            if _sid0 and _sid0 in known:
                _active = _sid0
            elif _held0 and _held0 in known:
                _active = _held0
        except Exception:
            _active = None
        _tok = None
        if set_active_ws is not None:
            try:
                _tok = set_active_ws(_active)
            except Exception:
                _tok = None
        _tok_fsch = None
        if set_active_fiscal_schema is not None:
            try:
                _fsch = (request.session.get("fiscal_schema") or request.GET.get("schema") or "").strip()
                _tok_fsch = set_active_fiscal_schema(_fsch)
            except Exception:
                _tok_fsch = None
        try:
            return self._gated(request, path, known, workspace_status)
        finally:
            if reset_active_fiscal_schema is not None and _tok_fsch is not None:
                try:
                    reset_active_fiscal_schema(_tok_fsch)
                except Exception:
                    pass
            if reset_active_ws is not None and _tok is not None:
                try:
                    reset_active_ws(_tok)
                except Exception:
                    pass

    def _gated(self, request, path, known, workspace_status):
        if _is_exempt(path):
            return self.get_response(request)
        active = lambda i: workspace_status is None or (workspace_status(i) or "active") == "active"
        # Settings-only flow (?settings=<known-id>): repair surface, no entry.
        try:
            _sid = (request.GET.get("settings") or "").strip()
        except Exception:
            _sid = ""
        _settings_only = bool(_sid and _sid in known)
        # Mode switch on a HELD session only (?mode=edit needs master).
        # Workspace entry itself happens ONLY via the login page (no param entry).
        try:
            _m = (request.GET.get("mode") or "").strip().lower()
        except Exception:
            _m = ""
        if _m in ("view", "edit"):
            try:
                _held0 = (request.session.get("workspace") or "").strip()
            except Exception:
                _held0 = ""
            if _held0 and _held0 in known and active(_held0):
                if _m == "view":
                    try:
                        request.session["ws_mode"] = "view"
                    except Exception:
                        pass
                else:
                    try:
                        _mok = bool(request.session.get("master_ok"))
                    except Exception:
                        _mok = False
                    if _mok:
                        try:
                            request.session["ws_mode"] = "edit"
                        except Exception:
                            pass
        # Session-held workspace (must still exist and be active)
        try:
            held = (request.session.get("workspace") or "").strip()
        except Exception:
            held = ""
        if held and held in known and active(held):
            return self.get_response(request)
        if _settings_only:
            return self.get_response(request)
        try:
            if "workspace" in getattr(request, "session", {}):
                del request.session["workspace"]
        except Exception:
            pass
        if path.startswith("/api/"):
            return JsonResponse(
                {"error": "workspace required — enter a workspace first",
                 "workspace_required": True},
                status=403)
        return redirect("/")
