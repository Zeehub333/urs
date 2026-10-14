"""Permission engine — applies permissions.fmlk records to users.

Pure decision logic (no Django/DB imports): callers fetch header + detail
rows (local PG or external read-only) and feed plain dicts in.

Record model (ad_permissions + ad_perm_* direct details):
  header: {id, perm_source(local|external), user_name, is_active}
  forms:   {form_file, form_name, can_view..can_unmigrate, custom_actions, fields_visibility JSON}
  reports: {report_file, report_name, can_view, columns_visibility JSON}
  rules:   {rule_file, rule_name, can_view..can_unmigrate, variables JSON}
  objects: {source, ref_key, ref_name, can_view..can_unmigrate, extra}

Semantics:
  - No record for the user  → allow everything (fail-open: a system without
    configured permissions behaves exactly like today).
  - Explicit 0/False on an action → deny (deny-override across rows).
  - Columns/variables: only explicit 0 hides/disables; missing = shown.
  - Header is_active falsy → deny everything for that grant.
  - perm_source == external → visibility-only: actions always allowed,
    only column/variable maps apply.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

ACTIONS = ["view", "edit", "add", "import", "export", "delete",
           "search", "migrate", "unmigrate"]

_CANON = {
    "view": "view", "show": "view", "display": "view", "عرض": "view",
    "edit": "edit", "update": "edit", "modify": "edit", "تعديل": "edit",
    "add": "add", "create": "add", "insert": "add", "اضافة": "add", "إضافة": "add",
    "import": "import", "استيراد": "import",
    "export": "export", "تصدير": "export",
    "delete": "delete", "remove": "delete", "حذف": "delete",
    "search": "search", "بحث": "search",
    "migrate": "migrate", "ترحيل": "migrate",
    "unmigrate": "unmigrate", "un_migrate": "unmigrate", "الغاء الترحيل": "unmigrate",
    "إلغاء الترحيل": "unmigrate",
}


def canon_action(a: str) -> str:
    try:
        return _CANON.get(str(a or "").strip().lower(), str(a or "").strip().lower())
    except Exception:
        return ""


def _bool(v: Any, default: bool = True) -> bool:
    """Explicit falsy (0/'0'/false/no/off/'') → False; None/missing → default."""
    if v is None:
        return default
    try:
        s = str(v).strip().lower()
    except Exception:
        return default
    if s in ("", "0", "false", "f", "no", "n", "off", "none", "null"):
        return False
    if s in ("1", "true", "t", "yes", "y", "on"):
        return True
    return default


def _action_flag(row: Dict[str, Any], action: str) -> Optional[bool]:
    """True/False when explicitly set (can_<action>), else None (absent)."""
    try:
        for k in ("can_" + action, action):
            if k in row and row[k] not in (None, ""):
                return _bool(row[k], default=True)
            for kk, vv in (row.items() if isinstance(row, dict) else []):
                if str(kk).lower() == k:
                    return _bool(vv, default=True)
    except Exception:
        pass
    return None


def _json_map(v: Any) -> Dict[str, Any]:
    import json as _js
    try:
        if v is None or (isinstance(v, str) and not v.strip()):
            return {}
        if isinstance(v, dict):
            return dict(v)
        d = _js.loads(str(v))
        return dict(d) if isinstance(d, dict) else {}
    except Exception:
        return {}


def _norm_ref(v: Any) -> str:
    try:
        return str(v or "").strip().lower()
    except Exception:
        return ""


def build_bundle(header_rows: List[Dict[str, Any]],
                 lines_by_kind: Dict[str, List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Bundle for ONE username (callers pre-filtered rows to that user).

    Empty input → None (fail-open). Else {"source", "external_only", grants:[...]}.
    Each grant: {"active", "forms": {ref: {...}}, "reports": {...},
                 "rules": {...}, "objects": {src: {ref: {...}}}}.
    """
    headers = [h for h in (header_rows or []) if isinstance(h, dict)]
    if not headers:
        return None
    grants = []
    for h in headers:
        src = str(h.get("perm_source") or h.get("source") or "local").strip().lower()
        external = src.startswith("خارج") or src in ("external", "ext", "outside")
        g: Dict[str, Any] = {
            "id": h.get("id"),
            "active": _bool(h.get("is_active", True), default=True),
            "source": "external" if external else "local",
            "forms": {}, "reports": {}, "rules": {}, "objects": {},
        }
        for ln in (lines_by_kind.get("forms") or []):
            if not isinstance(ln, dict):
                continue
            ref = _norm_ref(ln.get("form_file")) or _norm_ref(ln.get("form_name"))
            if not ref:
                continue
            e = g["forms"].setdefault(ref, {"actions": {}, "columns": {}, "custom": []})
            for a in ACTIONS:
                f = _action_flag(ln, a)
                if f is not None:
                    e["actions"][a] = f
            for k, v in _json_map(ln.get("fields_visibility") or ln.get("columns_visibility")).items():
                e["columns"][_norm_ref(k)] = _bool(v, default=True)
            try:
                ca = ln.get("custom_actions")
                if ca and str(ca).strip():
                    e["custom"].append(str(ca))
            except Exception:
                pass
            for alt in (_norm_ref(ln.get("form_file")), _norm_ref(ln.get("form_name"))):
                if alt and alt != ref:
                    g["forms"].setdefault(alt, e)
        for ln in (lines_by_kind.get("reports") or []):
            if not isinstance(ln, dict):
                continue
            ref = _norm_ref(ln.get("report_file")) or _norm_ref(ln.get("report_name"))
            if not ref:
                continue
            e = g["reports"].setdefault(ref, {"actions": {}, "columns": {}})
            f = _action_flag(ln, "view")
            if f is not None:
                e["actions"]["view"] = f
            for k, v in _json_map(ln.get("columns_visibility") or ln.get("fields_visibility")).items():
                e["columns"][_norm_ref(k)] = _bool(v, default=True)
            for alt in (_norm_ref(ln.get("report_file")), _norm_ref(ln.get("report_name"))):
                if alt and alt != ref:
                    g["reports"].setdefault(alt, e)
        for ln in (lines_by_kind.get("rules") or []):
            if not isinstance(ln, dict):
                continue
            rf, rn = _norm_ref(ln.get("rule_file")), _norm_ref(ln.get("rule_name"))
            ref = ("%s:%s" % (rf, rn)) if rf and rn else (rf or rn)
            if not ref:
                continue
            e = g["rules"].setdefault(ref, {"actions": {}, "vars": {}})
            for a in ACTIONS:
                f = _action_flag(ln, a)
                if f is not None:
                    e["actions"][a] = f
            for k, v in _json_map(ln.get("variables")).items():
                e["vars"][_norm_ref(k)] = _bool(v, default=True)
            if rf and rn:
                g["rules"].setdefault(rn, e)
        for ln in (lines_by_kind.get("objects") or []):
            if not isinstance(ln, dict):
                continue
            src = _norm_ref(ln.get("source"))
            ref = _norm_ref(ln.get("ref_key")) or _norm_ref(ln.get("ref_name"))
            if not src or not ref:
                continue
            bucket = g["objects"].setdefault(src, {})
            e = bucket.setdefault(ref, {"actions": {}, "extra": ln.get("extra")})
            for a in ACTIONS:
                f = _action_flag(ln, a)
                if f is not None:
                    e["actions"][a] = f
            for alt in (_norm_ref(ln.get("ref_key")), _norm_ref(ln.get("ref_name"))):
                if alt and alt != ref:
                    bucket.setdefault(alt, e)
        grants.append(g)
    if not grants:
        return None
    return {"grants": grants,
            "external_only": all(g["source"] == "external" for g in grants)}


def _iter_entries(bundle: Optional[Dict[str, Any]], kind: str, ref: Any) -> List[Dict[str, Any]]:
    if not bundle:
        return []
    if kind == "rules" and isinstance(ref, (list, tuple)) and len(ref) == 2:
        keys = [("%s:%s" % (ref[0], ref[1])).lower(), str(ref[1] or "").strip().lower()]
    else:
        keys = [str(ref or "").strip().lower()]
    try:
        base = str(ref or "")
        if "/" in base or "\\" in base:
            keys.append(base.replace("\\", "/").split("/")[-1].strip().lower())
    except Exception:
        pass
    out = []
    for g in (bundle.get("grants") or []):
        if not g.get("active", True):
            continue
        bucket = (g.get(kind) or {}) if kind in ("forms", "reports") else (
            g.get("rules") if kind == "rules" else {})
        if kind == "objects":
            continue
        for k in keys:
            if k and k in bucket:
                out.append(bucket[k])
                break
    return out


def _has_usable_grant(bundle: Optional[Dict[str, Any]]) -> bool:
    try:
        return any(g.get("active", True) for g in (bundle.get("grants") or []))
    except Exception:
        return False


def check(bundle: Optional[Dict[str, Any]], kind: str, ref: Any, action: str, is_superuser: bool = False) -> bool:
    """Can this user perform action on ref?
    Enterprise default: DEFAULT-DENY (الأصل في الأشياء المنع) for all users except admin/superuser.
    Actions must be explicitly granted (True/1).
    """
    if is_superuser:
        return True
    if not bundle or not _has_usable_grant(bundle):
        return False
    a = canon_action(action)
    entries = _iter_entries(bundle, kind, ref)
    if not entries:
        # Default-Deny: unlisted form/report is blocked
        return False
    granted = False
    for e in entries:
        val = e.get("actions", {}).get(a)
        if val is False or val == 0:
            return False  # explicit deny wins
        if val is True or val == 1:
            granted = True
    return granted


def check_object(bundle: Optional[Dict[str, Any]], source: str, ref: Any, action: str, is_superuser: bool = False) -> bool:
    if is_superuser:
        return True
    if not bundle or not _has_usable_grant(bundle):
        return False
    a = canon_action(action)
    keys = [str(ref or "").strip().lower()]
    granted = False
    found_any = False
    for g in (bundle.get("grants") or []):
        if not g.get("active", True):
            continue
        bucket = ((g.get("objects") or {}).get(str(source or "").strip().lower()) or {})
        for k in keys:
            e = bucket.get(k)
            if not e:
                continue
            found_any = True
            val = (e.get("actions") or {}).get(a)
            if val is False or val == 0:
                return False
            if val is True or val == 1:
                granted = True
    return granted if found_any else False


def allowed_custom_actions(bundle: Optional[Dict[str, Any]], form_ref: str, is_superuser: bool = False) -> Set[str]:
    """Set of allowed custom action names for this form."""
    if is_superuser:
        return {"*"}
    if not bundle or not _has_usable_grant(bundle):
        return set()
    out: Set[str] = set()
    import json as _js
    for e in _iter_entries(bundle, "forms", form_ref):
        for raw in (e.get("custom") or []):
            try:
                if isinstance(raw, list):
                    for item in raw:
                        if item: out.add(str(item).strip().lower())
                elif isinstance(raw, str):
                    s = raw.strip()
                    if s.startswith("[") and s.endswith("]"):
                        for item in _js.loads(s):
                            if item: out.add(str(item).strip().lower())
                    else:
                        for item in s.split(","):
                            if item.strip(): out.add(item.strip().lower())
            except Exception:
                pass
    return out


def check_custom_action(bundle: Optional[Dict[str, Any]], form_ref: str, action_name: str, is_superuser: bool = False) -> bool:
    """Can this user execute custom action on form_ref?
    Enterprise default: DEFAULT-DENY unless explicitly selected/enabled or superuser.
    """
    if is_superuser:
        return True
    allowed = allowed_custom_actions(bundle, form_ref, is_superuser=False)
    act = str(action_name or "").strip().lower()
    return act in allowed or "*" in allowed


def hidden_columns(bundle: Optional[Dict[str, Any]], kind: str, ref: Any) -> List[str]:
    """Column/input names explicitly flagged 0 for this ref (union across grants)."""
    out: List[str] = []
    if not bundle or not _has_usable_grant(bundle):
        return out
    for e in _iter_entries(bundle, kind, ref):
        for k, v in (e.get("columns") or {}).items():
            if v is False and k not in out:
                out.append(k)
    return out


def rule_vars(bundle: Optional[Dict[str, Any]], rule_file: str, rule_name: str) -> Dict[str, bool]:
    """Variable enable-map for a rule (missing vars default enabled)."""
    out: Dict[str, bool] = {}
    if not bundle or not _has_usable_grant(bundle):
        return out
    for e in _iter_entries(bundle, "rules", (rule_file, rule_name)):
        for k, v in (e.get("vars") or {}).items():
            if v is False:
                out[k] = False
            elif k not in out:
                out[k] = True
    return out


def is_external_only(bundle: Optional[Dict[str, Any]]) -> bool:
    try:
        return bool(bundle and bundle.get("external_only"))
    except Exception:
        return False
