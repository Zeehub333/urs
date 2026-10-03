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
    "/api/workspaces/",
    "/favicon.ico",
})
_EXEMPT_PREFIXES = (
    "/settings/setup/",
    "/admin/",
    "/static/",
)


def _is_exempt(path):
    if path in _EXEMPT_EXACT:
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
            from .workspace import workspaces_info
            known = {w.get("id") for w in workspaces_info() if w.get("id")}
        except Exception:
            known = set()
        # Entry with explicit workspace choice (?workspace=<id>)
        try:
            asked = (request.GET.get("workspace") or "").strip()
        except Exception:
            asked = ""
        if asked and asked in known:
            try:
                request.session["workspace"] = asked
            except Exception:
                pass
            return self.get_response(request)
        # Session-held workspace (must still exist)
        try:
            held = (request.session.get("workspace") or "").strip()
        except Exception:
            held = ""
        if held and held in known:
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
