#!/usr/bin/env python3
"""
Builds a scholarly two-column PDF for one article: a full-width title block
(base schema), then the article schema's fields in order, set in two balanced
columns, with table fields spanning the full width as numbered tables. Page 1
carries a citation footnote; every page has a running head and "Page X of N".
Field names are never hardcoded: shapes and styles come from the article schema.

Usage: python scripts/render_pdf.py _articles/FILE.md out.pdf
"""
import os
import pathlib
import re
import sys

import yaml
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

ROOT = pathlib.Path(__file__).resolve().parent.parent
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)

# Directories searched (in order) for the DejaVu TrueType faces. The renderer
# never assumes a font is present: if a face is missing it falls back to
# ReportLab's built-in standard fonts (see fonts()).
_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu/",
    "/usr/share/fonts/truetype/",
    "/usr/share/fonts/",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
]


def _find_font(filenames):
    """Return the path of the first of `filenames` found under any searched
    font directory (searched recursively), else None."""
    for base in _FONT_DIRS:
        if not os.path.isdir(base):
            continue
        for name in filenames:
            p = os.path.join(base, name)
            if os.path.isfile(p):
                return p
        for root, _dirs, files in os.walk(base):
            for name in filenames:
                if name in files:
                    return os.path.join(root, name)
    return None


def fonts():
    """Register and return the font names used throughout the document, as a
    dict with keys body, bold, italic, sans, sans_bold.

    Preference order, each family used as a complete set so faces never mix:
      1. Liberation Serif / Liberation Sans -- Times- and Arial-metric
         faces with wide Unicode coverage (Greek, >=, +/-, micro), the
         classic look of printed journals (package: fonts-liberation);
      2. DejaVu Serif / DejaVu Sans (package: fonts-dejavu);
      3. ReportLab's built-in Times and Helvetica, which need no files.
    The PDF therefore always renders; at worst the typeface differs.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    families = [
        ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf", "LiberationSerif-Italic.ttf"),
        ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf"),
    ]
    sans_families = [
        ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
        ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ]
    out = {"body": "Times-Roman", "bold": "Times-Bold", "italic": "Times-Italic",
           "sans": "Helvetica", "sans_bold": "Helvetica-Bold"}
    for faces in families:
        paths = [_find_font([f]) for f in faces]
        if all(paths):
            for alias, path in zip(("Body", "Body-Bold", "Body-Italic"), paths):
                pdfmetrics.registerFont(TTFont(alias, path))
            # lets <b> and <i> inside a paragraph switch to the right faces
            pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold",
                                          italic="Body-Italic", boldItalic="Body-Bold")
            out.update(body="Body", bold="Body-Bold", italic="Body-Italic")
            break
    for faces in sans_families:
        paths = [_find_font([f]) for f in faces]
        if all(paths):
            for alias, path in zip(("Sans", "Sans-Bold"), paths):
                pdfmetrics.registerFont(TTFont(alias, path))
            pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold",
                                          italic="Sans", boldItalic="Sans-Bold")
            out.update(sans="Sans", sans_bold="Sans-Bold")
            break
    return out


def hyphenation_lang():
    """'en_GB' when the pyphen package is installed (better justified text in
    narrow columns), else None -- the PDF still renders without it."""
    try:
        import pyphen  # noqa: F401
        return "en_GB"
    except ImportError:
        return None


def site_config():
    """Publication name for the running head and citation, from _config.yml."""
    try:
        return yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def load_article(path):
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(FM_RE.match(text).group(1))


def load_schema(name):
    return yaml.safe_load((ROOT / "_data" / f"{name}.yml").read_text())


# Page geometry (A4). Body text is set in two columns; tables span both.
MARGIN_X = 1.8 * cm
MARGIN_TOP = 2.3 * cm      # includes the running head
MARGIN_BOTTOM = 1.9 * cm   # includes the page footer
COLUMN_GAP = 0.7 * cm
TEXT_WIDTH = 21.0 * cm - 2 * MARGIN_X
COLUMN_WIDTH = (TEXT_WIDTH - COLUMN_GAP) / 2
TABLE_AVAILABLE_WIDTH = TEXT_WIDTH
CELL_PADDING = 10  # table cells: 5 pt left + 5 pt right


def compute_col_widths(cols, rows=None, font="Helvetica", size=9, available=TABLE_AVAILABLE_WIDTH):
    """
    Column widths sized by content, the way a web browser lays out a table:

      - each column's minimum is its longest single word (it can never be
        narrower without breaking a word);
      - each column's preferred width is its longest cell written on one line;
      - if everything fits, columns get their preferred widths, scaled to fill
        the line; otherwise every column gets its minimum and the remaining
        space is shared by how much more each column wants (square-root
        weighted, so long columns get more room without starving short ones).

    So a short "Source" column stays compact and a long "Finding" column gets
    most of the room, whatever columns a schema declares.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    if not cols:
        return []
    rows = rows or []

    def cells(c):
        return [c.capitalize()] + [str(r.get(c, "")) for r in rows]

    mins, prefs = [], []
    for c in cols:
        texts = cells(c)
        longest_word = max((stringWidth(w, font, size) for t in texts for w in t.split()), default=0)
        longest_cell = max((stringWidth(t, font, size) for t in texts), default=0)
        mins.append(longest_word + CELL_PADDING)
        prefs.append(max(longest_cell, longest_word) + CELL_PADDING)

    if sum(prefs) <= available:
        scale = available / sum(prefs)
        return [p * scale for p in prefs]
    if sum(mins) >= available:
        scale = available / sum(mins)  # extreme case: very long words everywhere
        return [m * scale for m in mins]
    # Share the spare room by the square root of each column's extra need, so a
    # very long column gets the most room without squeezing a moderate one
    # (such as a citation column) into a sliver.
    spare = available - sum(mins)
    wants = [max(p - m, 0) ** 0.5 for p, m in zip(prefs, mins)]
    total_want = sum(wants) or 1
    return [m + spare * w / total_want for m, w in zip(mins, wants)]


def is_placeholder_doi(doi) -> bool:
    """True for empty/placeholder DOIs (XXXXXXX, 000…, pending)."""
    d = str(doi or "").strip().lower()
    if d in ("", "none", "null"):
        return True
    return any(d.startswith(x) for x in ("10.5281/zenodo.000", "10.5281/zenodo.xxx", "doi:pending", "pending"))


def _initials(given):
    return "".join(w[0].upper() for w in re.split(r"[\s\-]+", str(given or "")) if w)


def citation_text(article, journal, doi):
    """Vancouver-style citation, e.g.
    Ch'ng ES. Title. Journal. 2026;1(1):3. doi:10.5281/zenodo.123"""
    names = [f"{a['family']} {_initials(a.get('given'))}".strip() for a in article["authors"]]
    if len(names) > 6:
        names = names[:3] + ["et al"]
    pub = article["published_date"]
    year = pub.year if hasattr(pub, "year") else str(pub)[:4]
    title = str(article["title"]).rstrip(".?!")
    end = "?" if str(article["title"]).rstrip().endswith("?") else "."
    cite = (f"{', '.join(names)}. {title}{end} <i>{escape(journal)}</i>. "
            f"{year};{article['volume']}({article['issue']}):{article['order']}.")
    if doi and str(doi).startswith("10.5072/"):
        cite += f" doi:{escape(str(doi))} (test DOI from sandbox.zenodo.org, not permanent)"
    elif doi:
        cite += f" doi:{escape(str(doi))}"
    return cite


def _render(article_path, out_path, doi_override=None, page_count=None):
    """
    Render one article PDF once and return its number of pages.

    Layout (scholarly two-column):
      page 1   running head with the journal name; article type, title,
               authors and affiliations across the full width; a footnote
               block with the citation, dates, correspondence and licence;
      body     the article schema's fields in order, set in two balanced
               columns; table fields span the full width as numbered tables;
      pages    running head and "Page X of N" footer on every page.

    page_count: N in "Page X of N"; see build().
    doi_override: if set, used instead of article['doi']. Used by the Zenodo
                  deposit flow so the final PDF embeds the reserved DOI.
    """
    import datetime as _dt
    from reportlab.lib import colors
    from reportlab.platypus import BaseDocTemplate, Frame, NextPageTemplate, PageTemplate
    from reportlab.platypus.flowables import BalancedColumns

    article = load_article(pathlib.Path(article_path))
    f = fonts()
    hy = hyphenation_lang()
    cfg = site_config()
    journal = str(cfg.get("title") or "")
    display_doi = doi_override if doi_override else article.get("doi")
    if is_placeholder_doi(display_doi):
        display_doi = None

    INK, MUTED, ACCENT = "#1a1a1a", "#5c5a56", "#5c3d2e"
    RULE, RULE_STRONG, PANEL = "#c9c5bc", "#3a3a3a", "#f4f2ed"
    J = dict(alignment=TA_JUSTIFY)
    if hy:
        J["hyphenationLang"] = hy
    st = {
        "kicker": ParagraphStyle("kicker", fontName=f["sans_bold"], fontSize=7.5, leading=10,
                                 textColor=ACCENT, spaceAfter=5),
        "title": ParagraphStyle("title", fontName=f["bold"], fontSize=17, leading=20.5,
                                textColor=INK, spaceAfter=9, **dict(J, hyphenationLang=None)),
        "authors": ParagraphStyle("authors", fontName=f["body"], fontSize=10.5, leading=13.5,
                                  textColor=INK, spaceAfter=3),
        "affil": ParagraphStyle("affil", fontName=f["italic"], fontSize=8, leading=10.5,
                                textColor=MUTED),
        "head": ParagraphStyle("head", fontName=f["sans_bold"], fontSize=7.8, leading=10,
                               textColor=INK, spaceBefore=9, spaceAfter=3),
        "text": ParagraphStyle("text", fontName=f["body"], fontSize=9.5, leading=12.6,
                               textColor=INK, spaceAfter=3, **J),
        "opinion": ParagraphStyle("opinion", fontName=f["italic"], fontSize=9.5, leading=12.6,
                                  textColor=INK, spaceAfter=3, **J),
        "bullet": ParagraphStyle("bullet", fontName=f["body"], fontSize=9.5, leading=12.6,
                                 leftIndent=10, bulletIndent=0, bulletFontName=f["body"],
                                 textColor=INK, spaceAfter=2.5, **J),
        "ref": ParagraphStyle("ref", fontName=f["body"], fontSize=8, leading=10.2,
                              leftIndent=13, bulletIndent=0, bulletFontName=f["body"],
                              bulletFontSize=8, textColor=INK, spaceAfter=2.5, **J),
        "caption": ParagraphStyle("caption", fontName=f["sans_bold"], fontSize=7.8, leading=10,
                                  textColor=INK, spaceBefore=6, spaceAfter=4),
        "cell": ParagraphStyle("cell", fontName=f["body"], fontSize=8.3, leading=10.6,
                               textColor=INK, **J),
        "cellhead": ParagraphStyle("cellhead", fontName=f["sans_bold"], fontSize=7.2, leading=9,
                                   textColor=INK),
        "foot": ParagraphStyle("foot", fontName=f["body"], fontSize=7.3, leading=9.2,
                               textColor=MUTED, **dict(J, hyphenationLang=None)),
        "runin": ParagraphStyle("runin", fontName=f["body"], fontSize=9.5, leading=12.6,
                                textColor=INK, spaceBefore=6, spaceAfter=2),
        "error": ParagraphStyle("error", fontName=f["italic"], fontSize=9, leading=12,
                                textColor=MUTED),
    }

    # ---------------- full-width front matter (page 1) ----------------
    front = []
    schema = load_schema(article["schema"]) if article.get("schema") else {"fields": []}
    fields = [fl for fl in schema.get("fields", []) if fl.get("visibility") != "editor"]
    kickers = [str(article.get(fl["key"])) for fl in fields
               if fl.get("style") == "badge" and article.get(fl["key"])]
    if kickers:
        front.append(Paragraph(escape(" · ".join(kickers)).upper(), st["kicker"]))
    front.append(Paragraph(escape(article["title"]), st["title"]))
    affs = []
    for a in article["authors"]:
        af = a.get("affiliation")
        if af and af not in affs:
            affs.append(af)
    parts = []
    for a in article["authors"]:
        name = escape(f"{a['given']} {a['family']}")
        if len(affs) > 1 and a.get("affiliation"):
            name += f"<super>{affs.index(a['affiliation']) + 1}</super>"
        parts.append(name)
    front.append(Paragraph(", ".join(parts), st["authors"]))
    for i, af in enumerate(affs, 1):
        prefix = f"<super>{i}</super> " if len(affs) > 1 else ""
        front.append(Paragraph(prefix + escape(af), st["affil"]))
    front.append(HRFlowable(width="100%", thickness=0.8, color=RULE_STRONG, spaceBefore=9, spaceAfter=4))

    # ---------------- schema-driven body ----------------
    # Each field becomes one block: ("col", flowables) set in the columns, or
    # ("full", flowables) spanning the page. Consecutive column blocks are
    # poured into one two-column BalancedColumns section.
    blocks = []
    table_no = 0

    def panel(flowables):
        """Boxed style: a lightly tinted panel within the column."""
        t = Table([[fl] for fl in flowables], colWidths=[COLUMN_WIDTH])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(PANEL)),
            ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.HexColor(ACCENT)),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
        ]))
        return t

    for field in fields:
        key, label, shape = field["key"], field["label"], field["shape"]
        style_name = field.get("style") or ("table" if shape == "table" else "plain")
        if style_name == "badge":
            continue  # shown as the kicker above the title
        value = article.get(key)
        if value is None or value == "" or value == []:
            if not field.get("optional"):
                blocks.append(("col", [Paragraph(f"[Missing required field: {escape(label)}]", st["error"])]))
            continue
        heading = Paragraph(escape(label).upper(), st["head"])
        text_style = st["opinion"] if style_name == "opinion" else st["text"]

        if shape == "table":
            ok = isinstance(value, list) and all(isinstance(r, dict) for r in value)
            if not ok:
                blocks.append(("col", [heading, Paragraph(
                    f"[cannot display &quot;{escape(label)}&quot;: content does not match "
                    f"the expected table shape]", st["error"])]))
                continue
            table_no += 1
            cols = field.get("columns", [])
            data = [[Paragraph(escape(c.replace("_", " ")).upper(), st["cellhead"]) for c in cols]] + [
                [Paragraph(escape(str(r.get(c, ""))), st["cell"]) for c in cols] for r in value]
            t = Table(data, repeatRows=1, hAlign="LEFT",
                      colWidths=compute_col_widths(cols, value, font=f["body"], size=8.3))
            t.setStyle(TableStyle([  # booktabs: rules above, below the header, and at the end
                ("LINEABOVE", (0, 0), (-1, 0), 1.0, colors.HexColor(RULE_STRONG)),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor(RULE_STRONG)),
                ("LINEBELOW", (0, 1), (-1, -2), 0.3, colors.HexColor(RULE)),
                ("LINEBELOW", (0, -1), (-1, -1), 1.0, colors.HexColor(RULE_STRONG)),
                ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            blocks.append(("full", [
                Paragraph(f"TABLE {table_no}.&nbsp; {escape(label).upper()}", st["caption"]),
                t, Spacer(1, 8)]))
            continue

        body = []
        if shape == "text":
            body = [Paragraph(escape(str(value)), text_style)]
        elif shape == "list" and isinstance(value, list):
            numbered = style_name == "numbered"
            for n, item in enumerate(value, 1):
                body.append(Paragraph(escape(str(item)), st["ref"] if numbered else st["bullet"],
                                      bulletText=f"{n}." if numbered else "\u2022"))
        elif shape in ("boolean", "date"):
            # short values run in after their label on one line:
            # "CONFLICTS OF INTEREST DECLARED  No"
            if shape == "boolean":
                txt = "Yes" if value else "No"
            else:
                txt = value.strftime("%-d %B %Y") if isinstance(value, (_dt.date, _dt.datetime)) else str(value)
            blocks.append(("col", [Paragraph(
                f'<font name="{f["sans_bold"]}" size="7.8">{escape(label).upper()}</font>'
                f"&nbsp;&nbsp; {escape(txt)}", st["runin"])]))
            continue
        else:
            body = [Paragraph(f"[unrecognised shape: {escape(shape)}]", st["error"])]

        if style_name == "boxed":
            blocks.append(("col", [heading, panel(body)]))
        else:
            blocks.append(("col", [heading] + body))

    story = list(front) + [NextPageTemplate("later")]
    run = []

    def flush():
        if run:
            story.append(BalancedColumns(list(run), nCols=2, innerPadding=COLUMN_GAP,
                                         leftPadding=0, rightPadding=0, topPadding=0,
                                         bottomPadding=0, spaceAfter=4))
            run.clear()

    for kind, fl in blocks:
        if kind == "col":
            run.extend(fl)
        else:
            flush()
            story.extend(fl)
    flush()

    # ---------------- page furniture ----------------
    pub = article["published_date"]
    pub_text = f"{pub.day} {pub.strftime('%B %Y')}" if hasattr(pub, "strftime") else str(pub)
    foot_bits = [f"<b>Cite as:</b> {citation_text(article, journal, display_doi)}",
                 f"Published {pub_text}. Licence: {escape(str(article.get('licence', '')))}."]
    if article.get("corresponding_email"):
        foot_bits.append(f"Correspondence: {escape(str(article['corresponding_email']))}")
    first_foot = Paragraph("<br/>".join(foot_bits), st["foot"])
    _, foot_h = first_foot.wrap(TEXT_WIDTH, 1000)

    W, H = A4
    head_y = H - MARGIN_TOP + 0.75 * cm
    issue_label = f"Volume {article['volume']}, Issue {article['issue']} · Article {article['order']}"
    short_title = str(article["title"])
    if len(short_title) > 80:
        short_title = short_title[:77].rsplit(" ", 1)[0] + "…"

    def furniture(canvas, doc, first):
        canvas.saveState()
        canvas.setFont(f["sans_bold"] if first else f["sans"], 7.5)
        canvas.setFillColor(colors.HexColor(ACCENT if first else MUTED))
        canvas.drawString(MARGIN_X, head_y, journal if first else short_title)
        canvas.setFont(f["sans"], 7.5)
        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.drawRightString(W - MARGIN_X, head_y, issue_label if first else journal)
        canvas.setStrokeColor(colors.HexColor(RULE))
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN_X, head_y - 5, W - MARGIN_X, head_y - 5)
        if first:
            y = MARGIN_BOTTOM + 0.2 * cm
            canvas.line(MARGIN_X, y + foot_h + 5, MARGIN_X + 4 * cm, y + foot_h + 5)
            first_foot.drawOn(canvas, MARGIN_X, y)
        canvas.setFont(f["sans"], 7.5)
        total = f" of {page_count}" if page_count else ""
        canvas.drawRightString(W - MARGIN_X, MARGIN_BOTTOM - 0.9 * cm, f"Page {doc.page}{total}")
        canvas.restoreState()

    frame_h = H - MARGIN_TOP - MARGIN_BOTTOM
    first_frame = Frame(MARGIN_X, MARGIN_BOTTOM + foot_h + 0.8 * cm, TEXT_WIDTH,
                        frame_h - foot_h - 0.8 * cm, id="first",
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    later_frame = Frame(MARGIN_X, MARGIN_BOTTOM, TEXT_WIDTH, frame_h, id="later",
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc = BaseDocTemplate(out_path, pagesize=A4, title=str(article["title"]),
                          author=", ".join(f"{a['given']} {a['family']}" for a in article["authors"]),
                          subject=journal)
    doc.addPageTemplates([
        PageTemplate(id="first", frames=[first_frame], onPage=lambda c, d: furniture(c, d, True)),
        PageTemplate(id="later", frames=[later_frame], onPage=lambda c, d: furniture(c, d, False)),
    ])
    doc.build(story)
    return doc.page


def pages_text(n) -> str:
    if not n:
        return ""
    return "1 page" if n == 1 else f"{n} pages"


def build(article_path, out_path, doi_override=None):
    """
    Build one article PDF and return its page count.

    Every page shows "Page X of N": a trial render finds N, the real render
    prints it, and if that ever changes the length it renders once more.

    doi_override: if set, used in the footer instead of article['doi'].
                  Used by the Zenodo deposit flow so the final PDF embeds
                  the real reserved DOI before upload.
    """
    import io
    n = _render(article_path, io.BytesIO(), doi_override, page_count=99)
    for _ in range(3):
        m = _render(article_path, out_path, doi_override, page_count=n)
        if m == n:
            return n
        n = m
    return n


def count_pdf_pages(pdf_path) -> int:
    """Number of pages in an existing PDF (used for PDFs reused from the cache)."""
    import re
    data = pathlib.Path(pdf_path).read_bytes()
    return len(re.findall(rb"/Type\s*/Page(?![a-z])", data))


if __name__ == "__main__":
    # Optional third arg: DOI override for embedding a reserved Zenodo DOI
    override = sys.argv[3] if len(sys.argv) > 3 else None
    n = build(sys.argv[1], sys.argv[2], doi_override=override)
    print(f"wrote {sys.argv[2]} ({pages_text(n)})" + (f" (doi={override})" if override else ""))
