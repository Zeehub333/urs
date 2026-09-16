"""مستند الطباعة PDF — نفس بيانات التقرير (RTL عربي).

- الخط: Arial من خطوط ويندوز (عربي كامل) مع fallback.
- النصوص تُشكّل عبر arabic_reshaper + python-bidi (وإلا ظهرت مفككة/معكوسة).
- الأعمدة معكوسة الترتيب (الأول يميناً) + ترويسة مكررة كل صفحة + ترقيم صفحات.
"""
import os
from io import BytesIO
from typing import Any, Dict, List

_FONT = "Ar"
_FONT_BOLD = "Ar-Bold"
_FONTS_READY = False


def _ensure_fonts() -> None:
    global _FONTS_READY
    if _FONTS_READY:
        return
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        regular = os.path.join(windir, "Fonts", "arial.ttf")
        bold = os.path.join(windir, "Fonts", "arialbd.ttf")
        if os.path.exists(regular):
            pdfmetrics.registerFont(TTFont(_FONT, regular))
        if os.path.exists(bold):
            pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold))
        else:
            try:
                pdfmetrics.registerFont(TTFont(_FONT_BOLD, regular))
            except Exception:
                pass
    except Exception:
        pass
    _FONTS_READY = True


def _ar(text: Any) -> str:
    """تشكيل النص العربي + ترتيب ثنائي الاتجاه (آمن للأرقام واللاتينية)."""
    s = "" if text is None else str(text)
    if s == "":
        return ""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(s))
    except Exception:
        return s


def _font_name(bold: bool = False) -> str:
    _ensure_fonts()
    try:
        from reportlab.pdfbase import pdfmetrics
        name = _FONT_BOLD if bold else _FONT
        pdfmetrics.getFont(name)
        return name
    except Exception:
        return "Helvetica-Bold" if bold else "Helvetica"


def build_pdf(columns: List[Dict[str, Any]], rows: List[Dict[str, Any]],
              title: str = "", meta: Dict[str, Any] = None) -> bytes:
    """columns: [{alias|name}] rows: [{alias: value}] → PDF bytes (أفقي A4)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame,
                                    Paragraph, Spacer, Table, TableStyle)

    meta = meta or {}
    headers = [str(c.get("alias") or c.get("name") or "") for c in (columns or [])]
    ncols = max(len(headers), 1)
    # RTL: الأول يميناً → عكس ترتيب الأعمدة
    headers_r = list(reversed(headers))

    body_font = _font_name(False)
    head_font = _font_name(True)
    fs = 10 if ncols <= 5 else (9 if ncols <= 8 else 7)
    cell_style = ParagraphStyle("cell", fontName=body_font, fontSize=fs,
                                leading=fs + 3, alignment=1, spaceAfter=1, spaceBefore=1)
    head_style = ParagraphStyle("head", fontName=head_font, fontSize=fs,
                                leading=fs + 3, alignment=1, textColor=colors.white)

    data = [[Paragraph(_ar(h), head_style) for h in headers_r]]
    for r in (rows or []):
        line = []
        for c in (columns or []):
            key = c.get("alias") or c.get("name") or ""
            v = r.get(key, r.get(c.get("name") or "", ""))
            if isinstance(v, dict):
                v = ""
            line.append(Paragraph(_ar("" if v is None else v), cell_style))
        data.append(list(reversed(line)))

    page_w, page_h = landscape(A4)
    avail = page_w - 2 * cm
    col_w = avail / ncols

    title_txt = _ar(title or "تقرير")
    info_parts = []
    if meta.get("date"):
        info_parts.append(_ar(str(meta["date"])))
    if meta.get("row_count") is not None:
        info_parts.append(_ar(f"عدد السجلات: {meta['row_count']}"))
    info_txt = "   |   ".join(info_parts)

    story = []
    st_title = ParagraphStyle("title", fontName=_font_name(True), fontSize=15,
                              leading=20, alignment=1)
    st_info = ParagraphStyle("info", fontName=body_font, fontSize=9,
                             leading=12, alignment=1, textColor=colors.grey)
    story.append(Paragraph(title_txt, st_title))
    if info_txt:
        story.append(Paragraph(info_txt, st_info))
    story.append(Spacer(1, 0.3 * cm))
    tbl = Table(data, colWidths=[col_w] * ncols, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), head_font),
        ("FONTSIZE", (0, 0), (-1, -1), fs),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    story.append(tbl)

    buf = BytesIO()

    def _footer(canvas, doc):
        try:
            canvas.saveState()
            canvas.setFont(body_font, 8)
            canvas.setFillColor(colors.grey)
            canvas.drawCentredString(page_w / 2, 1.2 * cm, f"{doc.page}")
            canvas.restoreState()
        except Exception:
            pass

    doc = BaseDocTemplate(buf, pagesize=landscape(A4),
                          leftMargin=cm, rightMargin=cm,
                          topMargin=cm, bottomMargin=1.5 * cm,
                          title=str(title or "تقرير"))
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(cm, 1.5 * cm, avail, page_h - 2.5 * cm)],
                                       onPage=_footer)])
    doc.build(story)
    return buf.getvalue()
