"""
DML Engine — render a compiled DML document to printable preview HTML.

``render_preview_html(comp_or_path, data)`` returns a full standalone HTML
document sized to the DML paper setting, with ``[var]`` / ``%%field%%``
placeholders substituted from ``data`` and optional ``table`` rows rendered
through the section ``<tableview>`` styles.
"""
from __future__ import annotations
import html as _html
import pathlib
from typing import Dict, Any, List, Optional

from .compiler import DMLCompiler

PAPER_SIZES = {
    "A4": ("210mm", "297mm"),
    "A5": ("148mm", "210mm"),
    "Letter": ("216mm", "279mm"),
    "Legal": ("216mm", "356mm"),
    "A3": ("297mm", "420mm"),
}

# ── System (global) variables — resolved automatically at render/print time ──
SYSTEM_VARS = [
    {"name": "current_user", "label": "اسم المستخدم الحالي", "type": "text"},
    {"name": "company_name", "label": "اسم الشركة", "type": "text"},
    {"name": "company_name_en", "label": "اسم الشركة (إنجليزي)", "type": "text"},
    {"name": "company_code", "label": "رمز الشركة", "type": "text"},
    {"name": "tax_number", "label": "الرقم الضريبي", "type": "text"},
    {"name": "branch_name", "label": "اسم الفرع", "type": "text"},
    {"name": "fiscal_year", "label": "السنة المالية", "type": "text"},
    {"name": "today", "label": "تاريخ اليوم", "type": "date"},
    {"name": "today_ar", "label": "تاريخ اليوم (عربي)", "type": "text"},
    {"name": "now_time", "label": "الساعة الحالية", "type": "text"},
    {"name": "now_datetime", "label": "التاريخ والوقت الحالي", "type": "text"},
    {"name": "year", "label": "السنة", "type": "text"},
    {"name": "month", "label": "الشهر (رقم)", "type": "text"},
    {"name": "month_name", "label": "اسم الشهر", "type": "text"},
    {"name": "day", "label": "اليوم (رقم)", "type": "text"},
    {"name": "weekday", "label": "يوم الأسبوع", "type": "text"},
    {"name": "doc_title", "label": "عنوان المستند", "type": "text"},
    {"name": "report_title", "label": "عنوان التقرير", "type": "text"},
    {"name": "app_name", "label": "اسم التطبيق", "type": "text"},
    {"name": "row_count", "label": "عدد السجلات", "type": "text"},
]

AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
             "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
AR_WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


def get_system_vars() -> Dict[str, Any]:
    """Date/time defaults for system variables (request-aware values merged by caller)."""
    import datetime
    now = datetime.datetime.now()
    return {
        "current_user": "", "company_name": "", "company_name_en": "",
        "company_code": "", "tax_number": "", "branch_name": "",
        "fiscal_year": "", "doc_title": "", "report_title": "",
        "app_name": "", "row_count": "",
        "today": now.strftime("%Y-%m-%d"),
        "today_ar": "%d %s %d" % (now.day, AR_MONTHS[now.month - 1], now.year),
        "now_time": now.strftime("%H:%M"),
        "now_datetime": now.strftime("%Y-%m-%d %H:%M"),
        "year": str(now.year), "month": "%02d" % now.month,
        "month_name": AR_MONTHS[now.month - 1],
        "day": "%02d" % now.day, "weekday": AR_WEEKDAYS[now.weekday()],
    }


def _esc(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


# ── TableView variants (type) — base CSS merged under custom th/tr/td ──
TABLE_VARIANTS = {
    "grid": {
        "label": "شبكي",
        "table": "width:100%;border-collapse:collapse;",
        "th": "background:#f1f5f9;border:1px solid #94a3b8;padding:4px 8px;font-weight:bold;",
        "tr": "", "td": "border:1px solid #94a3b8;padding:4px 8px;", "even": "",
    },
    "striped": {
        "label": "مخطط",
        "table": "width:100%;border-collapse:collapse;",
        "th": "background:#1e293b;color:#ffffff;padding:6px 8px;text-align:right;",
        "tr": "", "td": "padding:6px 8px;border-bottom:1px solid #e2e8f0;",
        "even": "background:#f8fafc;",
    },
    "plain": {
        "label": "عادي",
        "table": "width:100%;border-collapse:collapse;",
        "th": "padding:6px 8px;border-bottom:2px solid #0f172a;text-align:right;",
        "tr": "", "td": "padding:6px 8px;border-bottom:1px solid #e2e8f0;", "even": "",
    },
    "compact": {
        "label": "مضغوط",
        "table": "width:100%;border-collapse:collapse;",
        "th": "background:#f1f5f9;border:1px solid #e2e8f0;padding:2px 6px;font-size:11px;",
        "tr": "", "td": "border:1px solid #e2e8f0;padding:2px 6px;font-size:11px;", "even": "",
    },
}


def _merge_css(*parts: str) -> str:
    return "; ".join(p.strip().strip(";") for p in parts if p and p.strip())


def _num_val(v: Any):
    try:
        s = str(v).replace(",", "").strip()
        if not s:
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else ("%g" % round(x, 2))


def _hex_tint(hex_color: str, alpha: float = 0.16) -> str:
    try:
        hc = str(hex_color or "").strip()
        if len(hc) == 7 and hc.startswith("#"):
            r, g, b = int(hc[1:3], 16), int(hc[3:5], 16), int(hc[5:7], 16)
            return "rgba(%d,%d,%d,%s)" % (r, g, b, alpha)
    except Exception:
        pass
    return ""


def _status_cell(col: Dict[str, Any], raw_v: Any, td_css: str):
    """Status-mapped cell: returns (html_or_None, cell_css). None html = plain render."""
    try:
        if not (col.get("is_status") or col.get("isStatus")):
            return None, td_css
        sm = col.get("status_map") or col.get("statusMap") or []
        vs = "" if raw_v is None else str(raw_v).strip()
        hit = next((s for s in sm if isinstance(s, dict) and str(s.get("value", "") or "") == vs), None)
        if hit is None:
            return None, td_css
        label = str(hit.get("label", "") or vs)
        color = str(hit.get("color", "") or "")
        tint = _hex_tint(color) if color else ""
        css = _merge_css(td_css, ("background:%s;" % tint) if tint else "")
        fg = color if (len(color) == 7 and color.startswith("#")) else "#334155"
        try:
            _r, _g, _b = int(fg[1:3], 16), int(fg[3:5], 16), int(fg[5:7], 16)
            _tx = "#1e293b" if (0.299*_r + 0.587*_g + 0.114*_b) / 255 > 0.6 else "#fff"
        except Exception:
            _tx = "#fff"
        badge = ('<span style="display:inline-block;padding:1px 10px;border-radius:9999px;'
                 'font-weight:bold;font-size:11px;background:%s;color:%s;">%s</span>'
                 % (_esc(fg), _esc(_tx), _esc(label)))
        return badge, css
    except Exception:
        return None, td_css


def _table_html(columns: List[Dict[str, Any]], rows: List[Dict[str, Any]],
                tv: Optional[Dict[str, Any]] = None,
                groups: Optional[List[Dict[str, Any]]] = None) -> str:
    tv = tv or {}
    variant = str(tv.get("variant") or "grid").strip().lower()
    layout = str(tv.get("layout") or "rows").strip().lower()
    preset = TABLE_VARIANTS.get(variant, TABLE_VARIANTS["grid"])
    tbl_css = _merge_css(preset["table"], tv.get("style"))
    th_css = _merge_css(preset["th"], tv.get("th"))
    tr_css = _merge_css(preset["tr"], tv.get("tr"))
    td_css = _merge_css(preset["td"], tv.get("td"))
    even_css = preset.get("even", "")
    cols = columns or []
    rows = rows or []
    # Stronger header definition; group row stands out one tone darker
    _grp_th_css = _merge_css(th_css, "background:#e2e8f0;font-weight:bold;border:1px solid #94a3b8")
    h = ['<table class="dml-tableview" style="%s">' % _esc(tbl_css)]
    if layout == "grid2":
        # Horizontal 2-column label/value grid (single voucher record)
        h.append("<tbody>")
        for r in rows:
            cells = []
            for c in cols:
                key = c.get("alias") or c.get("name") or ""
                v = r.get(key, r.get((c.get("name") or ""), ""))
                cells.append('<th style="%s">%s</th><td style="%s">%s</td>'
                             % (_esc(th_css), _esc(key), _esc(td_css), _esc(v)))
            for i in range(0, len(cells), 2):
                h.append('<tr style="%s">%s</tr>' % (_esc(tr_css), "".join(cells[i:i + 2])))
        h.append("</tbody></table>")
        return "".join(h)
    h = ['<table class="dml-tableview" style="%s">' % _esc(tbl_css)]
    h.append("<thead>")
    # Group spanning rows (RML column groups, multi-level) — same logic as report player
    _grps = [g for g in (groups or []) if isinstance(g, dict) and (g.get("name")) and (g.get("columns"))]
    def _glvl(_g):
        try:
            return max(1, min(int(_g.get("level", 1) or 1), 5))
        except Exception:
            return 1
    if _grps:
        try:
            _grps = sorted(_grps, key=lambda g: (int(g.get("order", 0) or 0), str(g.get("id", ""))))
        except Exception:
            pass
        _aliases = [c.get("alias") or c.get("name") or "" for c in cols]
        _used = sorted({_glvl(_g) for _g in _grps
                        if any(a in _aliases for a in (_g.get("columns") or []))})
        _depth = len(_used) or 1
    else:
        _used, _depth = [], 1
    if _grps:

        # chain per column: group at each used level (or None)
        _chains = []
        for _c in cols:
            _ck = _c.get("alias") or _c.get("name") or ""
            _ch = []
            for _L in _used:
                _hit = None
                for _g in _grps:
                    if _glvl(_g) == _L and _ck in (_g.get("columns") or []):
                        _hit = _g
                        break
                _ch.append(_hit)
            _chains.append(_ch)
        _pending = [0] * len(cols)
        _done = [False] * len(cols)
        for _L in range(1, _depth + 1):
            h.append("<tr>")
            _i = 0
            while _i < len(cols):
                if _pending[_i] > 0:
                    _pending[_i] -= 1
                    _i += 1
                    continue
                _g = _chains[_i][_L - 1]
                if _g is None:
                    _rs, _below = 1, False
                    for _k in range(_L + 1, _depth + 1):
                        if _chains[_i][_k - 1] is not None:
                            _below = True
                            break
                        _rs += 1
                    _ck = cols[_i].get("alias") or cols[_i].get("name") or ""
                    if not _below:
                        h.append('<th rowspan="%d" style="%s">%s</th>' % (_rs + 1, _esc(_grp_th_css), _esc(_ck)))
                        _done[_i] = True
                        for _k in range(_L + 1, _depth + 1):
                            _pending[_i] += 1
                    else:
                        h.append('<th rowspan="%d" style="%s"></th>' % (_rs, _esc(_grp_th_css)))
                        for _k in range(1, _rs):
                            _pending[_i] += 1
                    _i += 1
                    continue
                _span, _j = 0, _i
                while _j < len(cols) and _pending[_j] == 0 and _chains[_j][_L - 1] is _g:
                    _span += 1
                    _j += 1
                _css = _grp_th_css if _L == 1 else th_css
                h.append('<th colspan="%d" style="%s">%s</th>' % (_span, _esc(_css), _esc(str(_g.get("name") or ""))))
                _i = _j
            h.append("</tr>")
        h.append("<tr>")
        for _idx, _c in enumerate(cols):
            if _done[_idx]:
                continue
            _ca = _c.get("alias") or _c.get("name") or ""
            h.append('<th style="%s">%s</th>' % (_esc(th_css), _esc(_ca)))
        h.append("</tr></thead><tbody>")
    if not _grps:
        h.append("<tr>")
        for c in cols:
            alias = c.get("alias") or c.get("name") or ""
            h.append('<th style="%s">%s</th>' % (_esc(th_css), _esc(alias)))
        h.append("</tr></thead><tbody>")
    for i, r in enumerate(rows):
        rstyle = _merge_css(tr_css, even_css if (i % 2 == 1 and even_css) else "")
        h.append('<tr style="%s">' % _esc(rstyle))
        for c in cols:
            key = c.get("alias") or c.get("name") or ""
            v = r.get(key, r.get((c.get("name") or ""), ""))
            _st, _cell_css = _status_cell(c, v, td_css)
            h.append('<td style="%s">%s</td>' % (_esc(_cell_css), _st if _st is not None else _esc(v)))
        h.append("</tr>")
    if tv.get("total_row") and rows:
        sums = []
        for c in cols:
            key = c.get("alias") or c.get("name") or ""
            vals = [r.get(key, r.get((c.get("name") or ""), "")) for r in rows]
            nums = [_num_val(v) for v in vals]
            if any(n is None and str(v).strip() != "" for n, v in zip(nums, vals)) or not any(n is not None for n in nums):
                sums.append("")
            else:
                sums.append(_fmt_num(sum(n for n in nums if n is not None)))
        h.append('<tr style="%s">' % _esc(_merge_css(tr_css, "font-weight:bold;")))
        for j, c in enumerate(cols):
            txt = "الإجمالي" if j == 0 else sums[j]
            h.append('<td style="%s">%s</td>' % (_esc(td_css), _esc(txt)))
        h.append("</tr>")
    if tv.get("count_row"):
        h.append('<tr style="%s"><td colspan="%d" style="%s">%s</td></tr>' % (
            _esc(tr_css), max(len(cols), 1), _esc(td_css), _esc("العدد: %d" % len(rows))))
    h.append("</tbody></table>")
    return "".join(h)


def render_preview_html(comp_or_path, data: Optional[Dict[str, Any]] = None,
                        table: Optional[Dict[str, Any]] = None,
                        detail: Optional[Dict[str, Any]] = None) -> str:
    """Build a full printable HTML document from a DML file.

    :param comp_or_path: DMLCompiler instance or path to ``*.dml``.
    :param data: mapping for ``[var]`` / ``%%field%%`` substitution.
    :param table: ``{"columns": [...], "rows": [...]}`` mirrored into each
        section ``<tableview>`` (player table snapshot).
    :param detail: ``{"columns": [...], "rows": [...]}`` used by tableviews
        with ``source="player:detail"`` (e.g. voucher detail lines).
    """
    comp = comp_or_path if isinstance(comp_or_path, DMLCompiler) else DMLCompiler(path=comp_or_path)
    data = data or {}
    table = table or {}
    detail = detail or {}
    meta = comp.metadata()
    paper = (meta.get("paper") or "A4").strip()
    orient = (meta.get("orientation") or "portrait").strip().lower()
    margin = (meta.get("margin") or "12mm").strip()
    w, h = PAPER_SIZES.get(paper, PAPER_SIZES["A4"])
    if orient == "landscape":
        w, h = h, w
    title = meta.get("title") or meta.get("name") or "Document"

    head = comp.header()
    foot = comp.footer()

    parts = [
        "<!DOCTYPE html><html dir=\"rtl\" lang=\"ar\"><head><meta charset=\"utf-8\">",
        "<title>%s</title>" % _esc(title),
        "<style>",
        "@page{size:%s %s;margin:%s}" % (_esc(w), _esc(h), _esc(margin)),
        "body{font-family:Tahoma,Arial,sans-serif;background:#525659;margin:0;padding:16px}",
        ".dml-page{background:#fff;width:%s;min-height:%s;margin:0 auto;box-shadow:0 2px 12px rgba(0,0,0,.35);display:flex;flex-direction:column;overflow:hidden}" % (_esc(w), _esc(h)),
        ".dml-header{padding:10px 14px;border-bottom:1px solid #e2e8f0;position:relative}",
        ".dml-body{flex:1;padding:10px 14px}",
        ".dml-section{margin-bottom:10px;position:relative}",
        ".dml-footer{padding:10px 14px;border-top:1px solid #e2e8f0;color:#64748b;font-size:11px;position:relative}",
        ".dml-textbox{box-sizing:border-box;margin:4px;}",
        ".dml-textbox[data-lane=\"right\"]{float:right;}",
        ".dml-textbox[data-lane=\"left\"]{float:left;}",
        ".dml-textbox[data-lane=\"center\"]{display:block;margin-left:auto;margin-right:auto;}",
        ".dml-textbox[data-w=\"third\"]{width:32%;}",
        ".dml-textbox[data-w=\"half\"]{width:48%;}",
        ".dml-textbox[data-w=\"full\"]{width:100%;}",
        ".dml-clear{clear:both;}",
        ".dml-tableview{width:100%;border-collapse:collapse;font-size:12px}",
        ".dml-tableview th,.dml-tableview td{border:1px solid #cbd5e1;padding:4px 8px}",
        "@media print{body{background:#fff;padding:0}.dml-page{box-shadow:none;margin:0;width:auto;min-height:auto}}",
        "</style></head><body>",
        "<div class=\"dml-page\">",
        "<div class=\"dml-header\" style=\"%s\">%s</div>" % (_esc(head.style), comp.render_text(head.html, data)),
        "<div class=\"dml-body\">",
    ]
    for s in comp.sections():
        parts.append("<div class=\"dml-section\" style=\"%s\">" % _esc(s.style))
        if (s.print_before or "").strip():
            parts.append("<div class=\"dml-print-extra\">%s</div>" % comp.render_text(s.print_before, data))
        parts.append(comp.render_text(s.html, data))
        if s.tableview is not None:
            _tvd = s.tableview.to_dict()
            _src = str(_tvd.get("source") or "player:main")
            _tbl = detail if _src == "player:detail" else table
            parts.append(_table_html(_tbl.get("columns", []), _tbl.get("rows", []), _tvd,
                                     _tbl.get("groups", [])))
        parts.append("<div class=\"dml-clear\"></div>")
        if (s.print_after or "").strip():
            parts.append("<div class=\"dml-print-extra\">%s</div>" % comp.render_text(s.print_after, data))
        parts.append("</div>")
    parts.append("</div>")
    parts.append("<div class=\"dml-footer\" style=\"%s\">%s</div>" % (_esc(foot.style), comp.render_text(foot.html, data)))
    parts.append("</div></body></html>")
    return "".join(parts)


def render_to_file(dml_path: str | pathlib.Path, out_path: str | pathlib.Path,
                   data: Optional[Dict[str, Any]] = None,
                   table: Optional[Dict[str, Any]] = None,
                   detail: Optional[Dict[str, Any]] = None) -> str:
    html = render_preview_html(dml_path, data=data, table=table, detail=detail)
    pathlib.Path(out_path).write_text(html, encoding="utf-8")
    return str(out_path)
