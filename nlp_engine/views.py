import json
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .engine import NLPEngine

logger = logging.getLogger(__name__)


def _get_request_workspace(request) -> str:
    """استخراج مساحة العمل الحالية بدقة من السياق النشط أو الطلب أو الجلسة"""
    from urs.workspace import active_ws_id, workspaces_info

    # 1. Active workspace context (pinned by WorkspaceGateMiddleware)
    wid = active_ws_id()
    if wid and str(wid).strip():
        return str(wid).strip()

    # 2. From request query param
    ws = request.GET.get("workspace") or request.GET.get("settings")
    if ws and str(ws).strip():
        return str(ws).strip()

    # 3. From session
    if hasattr(request, "session"):
        ws = request.session.get("workspace")
        if ws and str(ws).strip():
            return str(ws).strip()

    # 4. Fallback to first existing workspace
    try:
        infos = workspaces_info()
        if infos and infos[0].get("id"):
            return infos[0].get("id")
    except Exception:
        pass
    return "workspace_1"


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_nlp_search(request):
    """
    نقطة النهاية الرئيسية للبحث الذكي واستنتاج النوايا وعرض مؤشرات الأصناف والتقارير.
    محصورة بدقة في مساحة العمل الحالية فقط.
    """
    try:
        from urs.workspace import workspaces_info
        known_workspaces = {w.get("id") for w in workspaces_info() if w.get("id")}
    except Exception:
        known_workspaces = {"workspace_1", "workspace_2"}

    try:
        if request.method == "POST":
            try:
                body = json.loads(request.body.decode("utf-8"))
                query = body.get("q") or body.get("query", "")
                ws_override = body.get("workspace")
            except Exception:
                query = request.POST.get("q", "")
                ws_override = request.POST.get("workspace")
        else:
            query = request.GET.get("q", "")
            ws_override = request.GET.get("workspace")

        query = str(query or "").strip()
        if not query:
            return JsonResponse({"ok": True, "results": [], "intent": "EMPTY", "message": "الاستعلام فارغ"})

        ws_id = ""
        if ws_override and str(ws_override).strip() in known_workspaces:
            ws_id = str(ws_override).strip()
        if not ws_id:
            ws_id = _get_request_workspace(request)

        engine = NLPEngine(workspace_id=ws_id)
        result = engine.process(query)
        # وضع الدخول: أسماء التقارير المطابقة تُحجب (تبقى الملفات للتنقل)
        try:
            from urs.views import _ws_can_design as _uv_can_design
            _uv_view = not _uv_can_design(request)
        except Exception:
            _uv_view = True
        if _uv_view and isinstance(result, dict):
            try:
                for _mr in (result.get("matched_reports") or []):
                    if isinstance(_mr, dict) and _mr.get("file"):
                        _mr["name"] = _mr.get("file")
                _kpi = result.get("kpi_data")
                if isinstance(_kpi, dict) and _kpi.get("source_file"):
                    _kpi["source_report"] = _kpi.get("source_file")
            except Exception:
                pass
        return JsonResponse(result)
    except Exception as e:
        logger.exception("NLP search failed: %s", e)
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_nlp_command(request):
    """
    توليد أوامر RML و XSQL من التعبير اللغوي
    """
    try:
        if request.method == "POST":
            body = json.loads(request.body.decode("utf-8"))
            query = body.get("q") or body.get("query", "")
        else:
            query = request.GET.get("q", "")

        ws_id = _get_request_workspace(request)
        engine = NLPEngine(workspace_id=ws_id)
        result = engine.process(query)
        return JsonResponse({
            "ok": True,
            "intent": result.get("intent"),
            "command": result.get("command"),
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
