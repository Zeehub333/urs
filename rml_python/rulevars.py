"""توسيع استدعاءات قواعد الأعمال $rule.var$ إلى SQL.

التصميم (set-based داخل قاعدة البيانات — ليس Python row-by-row):
كل استدعاء يُحوَّل مرة واحدة أثناء بناء الاستعلام إلى CASE واحد،
ومحرك قاعدة البيانات يقيّمه لكل صف بلغة C. لا توجد جداول مؤقتة
ولا صلاحيات كتابة إضافية ولا تنظيف — ويعمل مع التقارير متعددة الاتصالات.

قواعد الفوز:
- السياسات تُرتّب بـ (priority الأصغر أولاً، ثم ترتيب الإنشاء) — أول
  سياسة تنطبق على السجل تفوز (سلوك حتمي يحل تداخل السياسات).
- السياسة الافتراضية (is_default أو بلا match) قيمُها هي ELSE.
- سجل بلا سياسة مطابقة ولا افتراضية → NULL (يُتجاهل الاستدعاء).

مثال: $fp_calc.dayStart$ ←
  CASE WHEN [emp_no] IN ('101','102') THEN '08:00' ELSE NULL END
ثم تُحل مراجع [الحقول] لاحقاً بآلية المحرك المعتادة.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

RULE_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\$")

# نص مقتبس يحتوي توكن واحداً فقط: '$rule.var$' → يُستبدل كاملاً (مع Quotes)
# بتوسيع التوكن (فروعه النصية مقتبسة أصلاً). أي نص مركب ('a $r.v$') يُترك حرفياً.
QUOTED_TOKEN_RE = re.compile(r"^'\s*(\$[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\$)\s*'$")

VAR_TYPES = ("text", "number", "date", "time", "expression", "boolean", "choice")

# حد ناعم: فوقه نحذر من تضخم SQL (كل سياسة = فرع WHEN واحد)
POLICY_COUNT_WARN = 100


def _norm_var_type(t: Any) -> str:
    t = str(t or "text").strip().lower()
    return t if t in VAR_TYPES else "text"


def _quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _value_sql(var_type: str, value: Any, *, rule_name: str = "", var_name: str = "") -> str:
    """تحويل قيمة متغير السياسة إلى SQL حسب نوع البيانات."""
    var_type = _norm_var_type(var_type)
    val = "" if value is None else str(value).strip()
    ctx = f" (القاعدة {rule_name} — المتغير {var_name})" if rule_name else ""
    if var_type == "expression":
        if not val:
            return "NULL"
        return val[1:] if val.startswith("=") else val  # بادئة = للتمييز تُجرد قبل الحقن
    if var_type == "number":
        if val == "":
            return "NULL"
        try:
            float(val)
        except (TypeError, ValueError):
            raise ValueError(f"قيمة رقمية غير صالحة '{val}'{ctx}")
        return val
    if var_type == "boolean":
        if val == "":
            return "NULL"
        low = val.lower()
        if low in ("1", "true", "t", "yes", "y", "صح", "نعم"):
            return "TRUE"
        if low in ("0", "false", "f", "no", "n", "غلط", "لا"):
            return "FALSE"
        raise ValueError(f"قيمة منطقية غير صالحة '{val}' (صح/غلط){ctx}")
    # text / date / time / choice → نص مقتبس
    if val == "":
        return "NULL"
    return _quote_literal(val)


def _match_sql(match: Dict[str, List[str]]) -> Optional[str]:
    """بناء شرط المطابقة: AND بين الحقول، وIN داخل الحقل الواحد.
    تُكتب الحقول بصيغة [field] ليحلها المحرك لاحقاً."""
    conds = []
    for fld, vals in (match or {}).items():
        fld = str(fld or "").strip()
        vals = [str(v) for v in (vals or []) if str(v or "").strip() != ""]
        if not fld or not vals:
            continue
        conds.append(f"[{fld}] IN ({', '.join(_quote_literal(v) for v in vals)})")
    if not conds:
        return None  # سياسة بلا شروط = تنطبق على كل السجلات
    return " AND ".join(conds)


def _policy_priority(p: Any, index: int) -> Tuple[int, int]:
    try:
        pr = int(getattr(p, "priority", 100))
    except (TypeError, ValueError):
        pr = 100
    return (pr, index)


def _is_default_policy(p: Any) -> bool:
    if bool(getattr(p, "is_default", False)):
        return True
    return _match_sql(getattr(p, "match", None) or {}) is None


def _policy_label(p: Any) -> str:
    return str(getattr(p, "name", None) or getattr(p, "id", "?") or "?")


def detect_policy_overlaps(policies: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """كشف تداخل السياسات: سياستان تتداخلان إذا كان شرطاهما يقبلان سجلاً واحداً.

    شرط AND يتداخل مع شرط AND آخر عندما: لكل حقل مشترك بينهما توجد
    قيمة مشتركة (والحقول غير المشتركة لا تمنع التداخل). السياسات
    الافتراضية (بلا شروط) تُستبعد — فهي fallback وليست تداخلاً.
    يُرجع [{a, b, fields, values}] حيث a/b اسما السياستين.
    """
    items = []
    for i, p in enumerate(policies or []):
        m = getattr(p, "match", None) or {}
        norm = {}
        for fld, vals in m.items():
            fld = str(fld or "").strip()
            vs = {str(v) for v in (vals or []) if str(v or "").strip() != ""}
            if fld and vs:
                norm[fld.lower()] = (fld, vs)
        if not norm:
            continue  # افتراضية → ليست تداخلاً
        items.append((i, _policy_label(p), norm))
    out = []
    for x in range(len(items)):
        for y in range(x + 1, len(items)):
            _, la, ma = items[x]
            _, lb, mb = items[y]
            shared = [f for f in ma if f in mb]
            if not shared:
                continue  # حقول مختلفة تماماً → لا تداخل مؤكد
            common: Dict[str, List[str]] = {}
            ok = True
            for f in shared:
                inter = ma[f][1] & mb[f][1]
                if not inter:
                    ok = False
                    break
                common[ma[f][0]] = sorted(inter)
            if ok:
                out.append({"a": la, "b": lb, "fields": sorted(common.keys()),
                            "values": common})
    return out


def expand_rule_vars(expr_text: str, rules: Optional[List[Any]]) -> str:
    """استبدال كل $rule.var$ في النص بـ CASE مبني من سياسات القاعدة."""
    if not expr_text or "$" not in expr_text or not rules:
        if not expr_text or "$" not in expr_text:
            return expr_text or ""
        raise ValueError("لا توجد قواعد معرفة — الاستدعاء $rule.var$ يتطلب قاعدة في نفس التقرير")
    by_name = {}
    for r in rules:
        n = str(getattr(r, "name", "") or "").strip().lower()
        if n:
            by_name[n] = r

    def _sub(m: re.Match) -> str:
        rule_name, var_name = m.group(1), m.group(2)
        rule = by_name.get(rule_name.lower())
        if rule is None:
            known = ", ".join(sorted({str(getattr(r, 'name', '')) for r in rules})) or "—"
            raise ValueError(
                f"قاعدة غير معروفة '${rule_name}.{var_name}$' — القواعد المعرفة: {known}")
        var = None
        for v in (getattr(rule, "variables", None) or []):
            if str(getattr(v, "name", "") or "").strip().lower() == var_name.lower():
                var = v
                break
        if var is None:
            known = ", ".join(str(getattr(v, 'name', '')) for v in
                              (getattr(rule, "variables", None) or [])) or "—"
            raise ValueError(
                f"المتغير '{var_name}' غير معرف في القاعدة '{rule_name}' — المتغيرات: {known}")
        vtype = _norm_var_type(getattr(var, "type", "text"))
        policies = list(getattr(rule, "policies", None) or [])
        if not policies:
            raise ValueError(
                f"القاعدة '{rule_name}' بلا سياسات — أنشئ سياسة من السايدبار (قواعد الأعمال)")
        if len(policies) > POLICY_COUNT_WARN:
            raise ValueError(
                f"القاعدة '{rule_name}' فيها {len(policies)} سياسة (الحد الناعم {POLICY_COUNT_WARN}) — "
                f"قسّم القاعدة أو ادمج السياسات المتشابهة لتفادي تضخم SQL")
        # الترتيب الحتمي: الأولوية الأصغر أولاً ثم ترتيب الإنشاء — الأول المطابق يفوز
        ordered = sorted(enumerate(policies), key=lambda t: _policy_priority(t[1], t[0]))
        whens = []
        seen_branches = set()  # دمج الفروع المكررة (نفس الشرط + نفس القيمة)
        default_lit = None
        for _, p in ordered:
            cond = _match_sql(getattr(p, "match", None) or {})
            vals = getattr(p, "values", None) or {}
            key = next((k for k in vals if str(k).strip().lower() == var_name.lower()), None)
            if key is None:
                continue  # السياسة لم تحدد هذا المتغير → تتخطاها
            lit = _value_sql(vtype, vals[key], rule_name=rule_name, var_name=var_name)
            if cond is None or _is_default_policy(p):
                if default_lit is None:
                    default_lit = lit  # أعلى أولوية افتراضية = ELSE
                continue  # الافتراضية لا تدخل WHEN — تظهر في ELSE فقط
            branch = (cond, lit)
            if branch in seen_branches:
                continue  # فرع مكرر — دُمج
            seen_branches.add(branch)
            whens.append(f"WHEN {cond} THEN {lit}")
        if not whens:
            if default_lit is None:
                raise ValueError(
                    f"لا توجد سياسة تحدد المتغير '{var_name}' في القاعدة '{rule_name}'")
            # بلا فروع شرطية (افتراضية فقط) → القيمة مباشرة بلا CASE
            # (CASE بلا WHEN خطأ SQL)
            return default_lit
        # ELSE أخير واحد: الافتراضية أو NULL = تجاهل الاستدعاء عند عدم المطابقة
        whens.append(f"ELSE {default_lit if default_lit is not None else 'NULL'}")
        return f"(CASE /* ${rule_name}.{var_name}$ */ " + " ".join(whens) + " END)"

    # النصوص المقتبسة تُترك حرفياً — إلا إن كان كامل النص توكن واحداً
    # ('$rule.var$') فيُستبدل بالتوسيع مع إسقاط الـ Quotes (فروعه مقتبسة أصلاً)
    parts = re.split(r"('(?:[^']|'')*')", expr_text)
    for i in range(0, len(parts), 2):
        parts[i] = RULE_VAR_RE.sub(_sub, parts[i])
    for i in range(1, len(parts), 2):
        qm = QUOTED_TOKEN_RE.match(parts[i] or "")
        if qm:
            parts[i] = RULE_VAR_RE.sub(_sub, qm.group(1))
    return "".join(parts)
