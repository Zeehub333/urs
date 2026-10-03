"""
STML views — sidebar + player payloads ("دليل النظام").

Pure functions (no Django import at module level, so the package also works
standalone). api_stml_* wrappers import JsonResponse lazily for urs wiring:

    path("api/stml/", include("odex.ad_system.stml_urls"))  # optional, user-side
"""
from __future__ import annotations
import pathlib
from typing import Any, Dict, List, Optional, Union

from .stml_compiler import compile_file
from .stml_engine import STMLEngine

STML_DIR = pathlib.Path(__file__).resolve().parent
SIDEBAR_GROUP = "دليل النظام"


def _resolve_path(name_or_path: Union[str, pathlib.Path],
                  stml_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    p = pathlib.Path(name_or_path)
    if p.is_file():
        return p
    base = pathlib.Path(stml_dir) if stml_dir else STML_DIR
    cand = base / (name_or_path if str(name_or_path).endswith(".stml") else f"{name_or_path}.stml")
    if not cand.is_file():
        raise ValueError(f"STML[unknown-file]: ملف الشجرة '{name_or_path}' غير موجود")
    return cand


def _engine_for(name_or_path: Union[str, pathlib.Path],
                stml_dir: Optional[pathlib.Path] = None) -> STMLEngine:
    return STMLEngine(compile_file(_resolve_path(name_or_path, stml_dir)))


def tree_list(stml_dir: Optional[Union[str, pathlib.Path]] = None) -> List[Dict[str, Any]]:
    """Sidebar payload: every *.stml under the System Directory group."""
    base = pathlib.Path(stml_dir) if stml_dir else STML_DIR
    out = []
    for f in sorted(base.glob("*.stml")):
        try:
            out.append(_engine_for(f).sidebar_entry(f.name))
        except ValueError:
            continue  # broken file must not kill the sidebar
    return out


def tree_detail(name_or_path: Union[str, pathlib.Path],
                stml_dir: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """Player payload: serial tree (nested) + flat list + per-model sequences."""
    eng = _engine_for(name_or_path, stml_dir)
    models = sorted({r["model_source"] for r in eng.flatten() if r["model_source"]})
    return {
        "tree": eng.to_dict(deep=True),
        "sequences": {m: eng.sequence_for(m) for m in models},
    }


def node_ops(name_or_path: Union[str, pathlib.Path], code: str,
             stml_dir: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """Operations statement (كشف العمليات) for one node."""
    return _engine_for(name_or_path, stml_dir).operations_statement(code)


def tree_visible(name_or_path: Union[str, pathlib.Path], user_no: str,
                 perm_rows: Optional[List[Any]] = None,
                 stml_dir: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """Central visibility payload: tree pruned to what the user may see."""
    return _engine_for(name_or_path, stml_dir).filter_visible(user_no, perm_rows)


def node_records(name_or_path: Union[str, pathlib.Path], code: str, records: List[Any],
                 code_field: str = "code",
                 stml_dir: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """Form records under a branch (serial-prefix match)."""
    eng = _engine_for(name_or_path, stml_dir)
    rows = eng.match_records(code, records, code_field)
    node = eng.find(code)
    return {"node": code, "label": node.label if node else "",
            "count": len(rows), "rows": rows}


def node_access(name_or_path: Union[str, pathlib.Path], code: str, user_no: str,
                perm_rows: Optional[List[Any]] = None, fmlk_id: str = "",
                rml_id: str = "", api_path: str = "",
                stml_dir: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    eng = _engine_for(name_or_path, stml_dir)
    node = eng.find(code)
    return {
        "node": code, "label": node.label if node else "",
        "locked": bool(node and node.locked),
        "allowed": eng.check_access(code, user_no, perm_rows or [],
                                    fmlk_id=fmlk_id, rml_id=rml_id, api_path=api_path),
    }


# -- Django wrappers (lazy import; urs wiring adds the urls) -------------
def api_stml_list(request):
    from django.http import JsonResponse
    return JsonResponse({"group": SIDEBAR_GROUP, "files": tree_list()}, safe=False)


def api_stml_tree(request, name: str):
    from django.http import JsonResponse
    try:
        return JsonResponse(tree_detail(name))
    except ValueError as ex:
        from django.http import JsonResponse as JR
        return JR({"error": str(ex)}, status=404)


def api_stml_ops(request, name: str, code: str):
    from django.http import JsonResponse
    try:
        return JsonResponse(node_ops(name, code))
    except ValueError as ex:
        from django.http import JsonResponse as JR
        return JR({"error": str(ex)}, status=404)


def api_stml_access(request):
    """POST JSON: {file, code, user_no, perm_rows:[...], fmlk_id?, rml_id?, api_path?}"""
    import json as _json
    from django.http import JsonResponse
    try:
        body = _json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"error": "JSON غلط"}, status=400)
    try:
        return JsonResponse(node_access(
            body.get("file", ""), body.get("code", ""), body.get("user_no", ""),
            perm_rows=body.get("perm_rows") or [], fmlk_id=body.get("fmlk_id", ""),
            rml_id=body.get("rml_id", ""), api_path=body.get("api_path", "")))
    except ValueError as ex:
        return JsonResponse({"error": str(ex)}, status=404)
