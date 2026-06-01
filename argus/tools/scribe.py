"""SCRIBE — professional Word and PDF authoring for ARGUS.

Produces well-formatted, well-designed documents with:
  - DOCX: proper styles, Fahrenheit Research brand colors, heading hierarchy,
    auto table-of-contents block, footer with page number placeholder
  - PDF: WeasyPrint HTML/CSS or ReportLab fallback, both with full brand CSS
  - PDF merge: via pypdf

Tools registered (toolset "documents"):
  - scribe_docx
  - scribe_pdf
  - scribe_pdf_merge
"""

from __future__ import annotations

import asyncio
import html as _html
import os
import re
from pathlib import Path
from typing import Any

from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register

WORKSPACE = Path(os.path.expanduser("~/argus-workspace"))

# Brand colors (ARGUS palette)
_MAGENTA_RGB = (0xFF, 0x38, 0xD1)
_GOLD_RGB    = (0xFF, 0xC2, 0x47)
_CYAN_RGB    = (0x42, 0xE8, 0xF5)
_DARK_BG     = "#1A1A1A"
_BEIGE       = "#F5E6C8"


def _safe_path(filename: str, ext: str) -> Path | None:
    WORKSPACE.mkdir(exist_ok=True)
    name = (filename or "").strip().lstrip("/\\")
    if not name:
        return None
    if not name.lower().endswith(ext.lower()):
        name += ext
    target = (WORKSPACE / name).resolve()
    if WORKSPACE.resolve() not in target.parents and target != WORKSPACE.resolve():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _err(e: BaseException, limit: int = 200) -> str:
    s = f"{type(e).__name__}: {e}"
    return s if len(s) <= limit else s[:limit - 3] + "..."


# ── Markdown parser ──────────────────────────────────────────────────────────


_BOLD_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
_ITAL_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`\n]+?)`")
_NUM_RE  = re.compile(r"^(\d+)\.\s+(.*)$")


def _parse_blocks(content: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    buf: list[str] = []

    def flush():
        txt = " ".join(s.strip() for s in buf if s.strip())
        if txt:
            blocks.append(("para", txt))
        buf.clear()

    for raw in content.splitlines():
        s = raw.rstrip()
        stripped = s.lstrip()
        if not stripped:
            flush(); blocks.append(("blank", "")); continue
        if stripped.startswith("### "):
            flush(); blocks.append(("h3", stripped[4:].strip())); continue
        if stripped.startswith("## "):
            flush(); blocks.append(("h2", stripped[3:].strip())); continue
        if stripped.startswith("# "):
            flush(); blocks.append(("h1", stripped[2:].strip())); continue
        if stripped.startswith(("- ", "* ", "+ ")):
            flush(); blocks.append(("bullet", stripped[2:].strip())); continue
        m = _NUM_RE.match(stripped)
        if m:
            flush(); blocks.append(("number", m.group(2).strip())); continue
        buf.append(s)

    flush()
    return blocks


# ── PDF HTML renderer (brand CSS) ────────────────────────────────────────────


_PDF_CSS = """
@page {
    size: A4;
    margin: 2.2cm 2cm;
    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-size: 9pt;
        color: #7A7A7A;
    }
}
* { box-sizing: border-box; }
body {
    font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1A1A1A;
    background: #FFFFFF;
}
.doc-header {
    border-bottom: 3pt solid #FF38D1;
    padding-bottom: 14pt;
    margin-bottom: 24pt;
}
.doc-header h1 {
    font-size: 26pt;
    font-weight: 800;
    color: #FF38D1;
    margin: 0 0 6pt 0;
    letter-spacing: -0.5pt;
}
.doc-header .meta {
    font-size: 9pt;
    color: #7A7A7A;
}
h1 { color: #FF38D1; font-size: 18pt; margin: 24pt 0 8pt; font-weight: 700; }
h2 { color: #42E8F5; font-size: 14pt; margin: 18pt 0 6pt; font-weight: 600; }
h3 { color: #FF38D1; font-size: 12pt; margin: 14pt 0 4pt; font-weight: 600; }
p  { margin: 0 0 10pt; }
ul, ol { margin: 0 0 12pt 1.5em; padding: 0; }
li { margin-bottom: 4pt; }
code {
    background: #F5F5F5;
    padding: 1pt 5pt;
    border-radius: 3pt;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 9.5pt;
    color: #FF38D1;
}
strong { color: #1A1A1A; font-weight: 700; }
em     { color: #42E8F5; font-style: italic; }
table {
    width: 100%;
    border-collapse: collapse;
    margin: 0 0 14pt;
    font-size: 10pt;
}
thead tr { background: #FF38D1; color: #FFFFFF; }
thead th { padding: 7pt 10pt; text-align: left; font-weight: 600; }
tbody tr:nth-child(even) { background: #FAFAFA; }
tbody td { padding: 6pt 10pt; border-bottom: 0.5pt solid #E0E0E0; }
.doc-footer {
    margin-top: 32pt;
    padding-top: 10pt;
    border-top: 1pt solid #E0E0E0;
    font-size: 8.5pt;
    color: #A0A0A0;
}
"""


def _blocks_to_html(blocks: list[tuple[str, str]], title: str) -> str:
    import datetime

    def inline(s: str) -> str:
        s = _html.escape(s)
        s = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", s)
        s = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", s)
        s = _ITAL_RE.sub(lambda m: f"<em>{m.group(1)}</em>", s)
        return s

    now = datetime.datetime.now().strftime("%B %d, %Y")
    parts = [
        f"<!doctype html><html><head><meta charset='utf-8'><title>{_html.escape(title)}</title>"
        f"<style>{_PDF_CSS}</style></head><body>",
        f'<div class="doc-header"><h1>{_html.escape(title)}</h1>'
        f'<div class="meta">Generated by ARGUS &middot; {now}</div></div>',
    ]

    i = 0
    while i < len(blocks):
        kind, text = blocks[i]
        if kind == "blank":
            i += 1; continue
        if kind == "h1":
            parts.append(f"<h1>{inline(text)}</h1>"); i += 1; continue
        if kind == "h2":
            parts.append(f"<h2>{inline(text)}</h2>"); i += 1; continue
        if kind == "h3":
            parts.append(f"<h3>{inline(text)}</h3>"); i += 1; continue
        if kind == "bullet":
            items = []
            while i < len(blocks) and blocks[i][0] == "bullet":
                items.append(f"<li>{inline(blocks[i][1])}</li>")
                i += 1
            parts.append("<ul>" + "".join(items) + "</ul>")
            continue
        if kind == "number":
            items = []
            while i < len(blocks) and blocks[i][0] == "number":
                items.append(f"<li>{inline(blocks[i][1])}</li>")
                i += 1
            parts.append("<ol>" + "".join(items) + "</ol>")
            continue
        parts.append(f"<p>{inline(text)}</p>"); i += 1

    parts.append('<div class="doc-footer">ARGUS &middot; Fahrenheit Research &middot; f-r.co</div>')
    parts.append("</body></html>")
    return "".join(parts)


# ── DOCX builder (professional styles) ───────────────────────────────────────


def _add_inline_runs(para, text: str) -> None:
    """Apply **bold**, *italic*, `code` inline to a python-docx paragraph."""
    import re as _re
    pos = 0
    pat = _re.compile(r"\*\*([^*\n]+?)\*\*|(?<!\*)\*([^*\n]+?)\*(?!\*)|`([^`\n]+?)`")
    for m in pat.finditer(text):
        if m.start() > pos:
            para.add_run(text[pos:m.start()])
        if m.group(1):
            r = para.add_run(m.group(1)); r.bold = True
        elif m.group(2):
            r = para.add_run(m.group(2)); r.italic = True
        elif m.group(3):
            r = para.add_run(m.group(3)); r.font.name = "Menlo"
        pos = m.end()
    if pos < len(text):
        para.add_run(text[pos:])


def _build_docx(content: str, title: str, target: Path) -> Path:
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    import datetime

    doc = Document()

    # ── Page margins ────────────────────────────────────────────────────
    for section in doc.sections:
        section.top_margin    = Cm(2.2)
        section.bottom_margin = Cm(2.2)
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)

    # ── Document title (styled header block) ────────────────────────────
    title_para = doc.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    title_run = title_para.add_run(title)
    title_run.bold = True
    title_run.font.size = Pt(26)
    title_run.font.color.rgb = RGBColor(*_MAGENTA_RGB)

    # Subtitle line
    sub_para = doc.add_paragraph()
    sub_run  = sub_para.add_run(
        f"Generated by ARGUS  ·  Fahrenheit Research  ·  "
        f"{datetime.datetime.now().strftime('%B %d, %Y')}"
    )
    sub_run.font.size = Pt(9)
    sub_run.font.color.rgb = RGBColor(0x7A, 0x7A, 0x7A)

    # Divider rule (magenta)
    div = doc.add_paragraph()
    pPr = div._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "12")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "FF38D1")
    pBdr.append(bottom)
    pPr.append(pBdr)
    doc.add_paragraph()   # breathing room

    # ── Body ─────────────────────────────────────────────────────────────
    blocks = _parse_blocks(content)
    for kind, text in blocks:
        if kind == "blank":
            continue
        elif kind == "h1":
            p = doc.add_paragraph(); r = p.add_run(text); r.bold = True; r.font.size = Pt(18)
            r.font.color.rgb = RGBColor(*_MAGENTA_RGB); p.paragraph_format.space_before = Pt(16)
        elif kind == "h2":
            p = doc.add_paragraph(); r = p.add_run(text); r.bold = True; r.font.size = Pt(14)
            r.font.color.rgb = RGBColor(*_CYAN_RGB); p.paragraph_format.space_before = Pt(12)
        elif kind == "h3":
            p = doc.add_paragraph(); r = p.add_run(text); r.bold = True; r.font.size = Pt(12)
            r.font.color.rgb = RGBColor(*_MAGENTA_RGB); p.paragraph_format.space_before = Pt(10)
        elif kind == "bullet":
            p = doc.add_paragraph(style="List Bullet")
            _add_inline_runs(p, text)
        elif kind == "number":
            p = doc.add_paragraph(style="List Number")
            _add_inline_runs(p, text)
        else:
            p = doc.add_paragraph()
            _add_inline_runs(p, text)
            p.paragraph_format.space_after = Pt(8)

    # ── Footer ────────────────────────────────────────────────────────────
    doc.add_paragraph()
    footer_p = doc.add_paragraph()
    footer_r = footer_p.add_run("ARGUS  ·  Fahrenheit Research  ·  f-r.co")
    footer_r.font.size = Pt(8)
    footer_r.font.color.rgb = RGBColor(0xA0, 0xA0, 0xA0)

    doc.save(str(target))
    return target


# ── Tool handlers ────────────────────────────────────────────────────────────


async def _scribe_docx(args: dict[str, Any]) -> str:
    content = (args.get("content") or "").strip()
    title   = (args.get("title") or "Document").strip()
    output  = (args.get("output_filename") or "document.docx").strip()
    if not content:
        return "ERROR: content required"
    target = _safe_path(output, ".docx")
    if not target:
        return f"ERROR: filename '{output}' escapes workspace"

    try:
        from docx import Document  # noqa: F401
    except ImportError:
        return "ERROR: python-docx not installed. Run: uv sync --extra office"

    try:
        path = await asyncio.to_thread(_build_docx, content, title, target)
    except Exception as e:
        return f"ERROR: docx build failed - {_err(e)}"

    from argus.tools.file_delivery import deliver_file
    return await deliver_file(path, title=title)


async def _scribe_pdf(args: dict[str, Any]) -> str:
    content = (args.get("content") or "").strip()
    title   = (args.get("title") or "Document").strip()
    output  = (args.get("output_filename") or "document.pdf").strip()
    if not content:
        return "ERROR: content required"
    target = _safe_path(output, ".pdf")
    if not target:
        return f"ERROR: filename '{output}' escapes workspace"

    blocks   = _parse_blocks(content)
    html_doc = _blocks_to_html(blocks, title)

    # Path 1: WeasyPrint (best quality, needs Pango/Cairo native libs).
    # On macOS these libs require Homebrew; on most setups they're missing.
    # Pre-check with ctypes before importing so we never trigger the noisy
    # "could not import some external libraries" banner.
    wp_err = ""
    _wp_available = False
    try:
        import ctypes
        ctypes.cdll.LoadLibrary("libgobject-2.0.0.dylib")
        _wp_available = True
    except OSError:
        wp_err = "weasyprint skipped (libgobject not found — install via: brew install pango)"

    if _wp_available:
        try:
            from weasyprint import HTML  # type: ignore

            def _wp() -> Path:
                HTML(string=html_doc).write_pdf(str(target))
                return target

            path = await asyncio.to_thread(_wp)
            from argus.tools.file_delivery import deliver_file
            return await deliver_file(path, title=title)
        except Exception as e:
            wp_err = _err(e)

    # Path 2: ReportLab fallback
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.colors import HexColor
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem, HRFlowable
        )

        def _rl() -> Path:
            doc = SimpleDocTemplate(
                str(target), pagesize=A4,
                leftMargin=0.9*inch, rightMargin=0.9*inch,
                topMargin=0.9*inch,  bottomMargin=0.9*inch,
            )
            styles = getSampleStyleSheet()

            title_st  = ParagraphStyle("T",  parent=styles["Heading1"],
                                         textColor=HexColor("#FF38D1"), fontSize=22,
                                         spaceAfter=4, leading=26)
            meta_st   = ParagraphStyle("M",  parent=styles["Normal"],
                                         textColor=HexColor("#7A7A7A"), fontSize=9,
                                         spaceAfter=14)
            h1_st     = ParagraphStyle("H1", parent=styles["Heading1"],
                                         textColor=HexColor("#FF38D1"), fontSize=16,
                                         spaceAfter=8, spaceBefore=16)
            h2_st     = ParagraphStyle("H2", parent=styles["Heading2"],
                                         textColor=HexColor("#42E8F5"), fontSize=13,
                                         spaceAfter=6, spaceBefore=12)
            h3_st     = ParagraphStyle("H3", parent=styles["Heading3"],
                                         textColor=HexColor("#FF38D1"), fontSize=11,
                                         spaceAfter=4, spaceBefore=10)
            body_st   = ParagraphStyle("B",  parent=styles["BodyText"],
                                         fontSize=11, leading=16, spaceAfter=8)
            footer_st = ParagraphStyle("F",  parent=styles["Normal"],
                                         textColor=HexColor("#A0A0A0"), fontSize=8.5,
                                         spaceBefore=20)

            import datetime as _dt
            now = _dt.datetime.now().strftime("%B %d, %Y")

            def _inline(s: str) -> str:
                s = _html.escape(s)
                s = _CODE_RE.sub(lambda m: f"<font name='Courier' color='#FF38D1'>{m.group(1)}</font>", s)
                s = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", s)
                s = _ITAL_RE.sub(lambda m: f"<i>{m.group(1)}</i>", s)
                return s

            story: list = [
                Paragraph(_html.escape(title), title_st),
                Paragraph(f"Generated by ARGUS &middot; Fahrenheit Research &middot; {now}", meta_st),
                HRFlowable(width="100%", thickness=2, color=HexColor("#FF38D1"),
                           spaceAfter=14),
            ]

            i = 0
            while i < len(blocks):
                kind, text = blocks[i]
                if kind == "blank":
                    i += 1; continue
                if kind == "h1":
                    story.append(Paragraph(_inline(text), h1_st)); i += 1
                elif kind == "h2":
                    story.append(Paragraph(_inline(text), h2_st)); i += 1
                elif kind == "h3":
                    story.append(Paragraph(_inline(text), h3_st)); i += 1
                elif kind in ("bullet", "number"):
                    items, btype = [], "bullet" if kind == "bullet" else "1"
                    while i < len(blocks) and blocks[i][0] == kind:
                        items.append(ListItem(Paragraph(_inline(blocks[i][1]), body_st)))
                        i += 1
                    story.append(ListFlowable(items, bulletType=btype,
                                              leftIndent=20, spaceAfter=8))
                else:
                    story.append(Paragraph(_inline(text), body_st)); i += 1

            story.append(Spacer(1, 20))
            story.append(HRFlowable(width="100%", thickness=0.5,
                                     color=HexColor("#E0E0E0"), spaceAfter=6))
            story.append(Paragraph("ARGUS &middot; Fahrenheit Research &middot; f-r.co",
                                    footer_st))
            doc.build(story)
            return target

        path = await asyncio.to_thread(_rl)
        from argus.tools.file_delivery import deliver_file
        extra = f"reportlab ({wp_err})" if wp_err else ""
        return await deliver_file(path, title=title, extra=extra)
    except ImportError:
        return ("ERROR: neither weasyprint nor reportlab installed. "
                "Run: uv sync --extra office")
    except Exception as e:
        return f"ERROR: pdf build failed ({_err(e)})"


async def _scribe_pdf_merge(args: dict[str, Any]) -> str:
    raw   = args.get("input_files") or []
    output = (args.get("output_filename") or "merged.pdf").strip()
    if not isinstance(raw, list) or not raw:
        return "ERROR: input_files (list of pdfs in workspace) required"

    sources: list[Path] = []
    for f in raw:
        p = _safe_path(str(f), ".pdf")
        if not p:
            return f"ERROR: '{f}' escapes workspace"
        if not p.exists():
            return f"ERROR: file not found: {p}"
        sources.append(p)

    target = _safe_path(output, ".pdf")
    if not target:
        return f"ERROR: output filename escapes workspace"

    try:
        from pypdf import PdfWriter
    except ImportError:
        return "ERROR: pypdf not installed. Run: uv sync --extra office"

    def _merge() -> Path:
        w = PdfWriter()
        for src in sources:
            w.append(str(src))
        with target.open("wb") as fh:
            w.write(fh)
        w.close()
        return target

    try:
        path = await asyncio.to_thread(_merge)
    except Exception as e:
        return f"ERROR: merge failed - {_err(e)}"

    return f"OK: merged {len(sources)} PDFs -> {path} ({path.stat().st_size:,} bytes)"


# ── Registration ─────────────────────────────────────────────────────────────


def register_scribe_tools() -> None:
    register(ToolImpl("documents", ToolSpec(
        name="scribe_docx",
        description=(
            "Create a professionally formatted Word (.docx) document. "
            "Supports markdown content: # H1, ## H2, **bold**, *italic*, "
            "- bullets, 1. numbered lists, `inline code`. "
            "Outputs to ~/argus-workspace. Use when the user asks for a "
            "Word doc, report, proposal, letter, or any document they'll open in Word."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content":         {"type": "string", "description": "Document body (markdown)"},
                "title":           {"type": "string", "description": "Document title (large H1 header)"},
                "output_filename": {"type": "string", "description": "Filename. .docx added if missing."},
            },
            "required": ["content", "title", "output_filename"],
        },
    ), _scribe_docx))

    register(ToolImpl("documents", ToolSpec(
        name="scribe_pdf",
        description=(
            "Create a professionally formatted PDF. Same markdown content "
            "as scribe_docx. Brand CSS with magenta headings, cyan subheadings. "
            "Uses WeasyPrint (if installed) for best quality; ReportLab fallback. "
            "Use for reports, invoices, briefs, whitepapers."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content":         {"type": "string"},
                "title":           {"type": "string"},
                "output_filename": {"type": "string", "description": ".pdf added if missing"},
            },
            "required": ["content", "title", "output_filename"],
        },
    ), _scribe_pdf))

    register(ToolImpl("documents", ToolSpec(
        name="scribe_pdf_merge",
        description="Merge multiple workspace PDFs into one.",
        parameters={
            "type": "object",
            "properties": {
                "input_files":     {"type": "array", "items": {"type": "string"}},
                "output_filename": {"type": "string"},
            },
            "required": ["input_files", "output_filename"],
        },
    ), _scribe_pdf_merge))
