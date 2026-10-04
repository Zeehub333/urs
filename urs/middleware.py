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
_EXEMPT_PREFIXES_API = ("/api/workspaces/",)
_EXEMPT_PREFIXES = (
    "/settings/setup/",
    "/admin/",
    "/static/",
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
        if _is_exempt(path):
            return self.get_response(request)
        try:
            from .workspace import workspaces_info, workspace_status
            known = {w.get("id") for w in workspaces_info() if w.get("id")}
        except Exception:
            known = set()
            workspace_status = None
        active = lambda i: workspace_status is None or (workspace_status(i) or "active") == "active"
        # Settings-only flow (?settings=<known-id>): repair surface, no entry.
        try:
            _sid = (request.GET.get("settings") or "").strip()
        except Exception:
            _sid = ""
        _settings_only = bool(_sid and _sid in known)
        # Entry with explicit workspace choice (?workspace=<id>[&mode=..][&settings=..])
        try:
            asked = (request.GET.get("workspace") or "").strip()
        except Exception:
            asked = ""
        if asked and asked in known and active(asked):
            try:
                request.session["workspace"] = asked
            except Exception:
                pass
            try:
                _m = (request.GET.get("mode") or "").strip().lower()
                if _m == "view":
                    request.session["ws_mode"] = "view"
                elif _m == "edit":
                    # edit mode needs master password (pre-authed session)
                    try:
                        _mok = bool(request.session.get("master_ok"))
                    except Exception:
                        _mok = False
                    if _mok:
                        request.session["ws_mode"] = "edit"
            except Exception:
                pass
            return self.get_response(request)
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
