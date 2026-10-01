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
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
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


# ---------------------------------------------------------------------------
# Lightweight Markdown -> ReportLab flowables (no extra dependency)
# Supports: paragraphs, **bold**, *italic*, pipe tables, blank-line breaks.
# ---------------------------------------------------------------------------

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITAL_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")


def _inline_md(text: str) -> str:
    """Escape HTML then apply a tiny Markdown inline subset to ReportLab XML."""
    s = escape(str(text))
    s = _BOLD_RE.sub(r"<b>\1</b>", s)
    s = _ITAL_RE.sub(r"<i>\1</i>", s)
    return s


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def _is_sep_row(line: str) -> bool:
    s = line.strip().strip("|").replace(":", "").replace("-", "").replace("|", "").replace(" ", "")
    return _is_table_row(line) and s == ""


def _parse_row(line: str) -> list[str]:
    parts = [c.strip() for c in line.strip().strip("|").split("|")]
    return parts


def md_to_flowables(text, para_style, cell_style=None, cellhead_style=None,
                    caption_style=None, table_font="Helvetica", table_size=8.3,
                    subhead_style=None):
    """
    Convert a Markdown string into a list of (kind, flowable) pairs.
      kind "col"  -> flows in the two-column body
      kind "full" -> spans the page (tables, standalone **subheadings**)

    Standalone bold lines (**Title**) are treated as subheadings and kept
    full-width so they stay visually attached to a following table.
    """
    if text is None:
        return []
    raw = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    out = []
    i = 0
    para_buf = []

    def flush_para():
        nonlocal para_buf
        if not para_buf:
            return
        body = " ".join(l.strip() for l in para_buf if l.strip())
        para_buf = []
        if body:
            out.append(("col", Paragraph(_inline_md(body), para_style)))

    # Standalone **Title** or markdown ## Title lines are subheadings
    subhead_re = re.compile(r"^\s*(?:##\s+(.+)|\*\*(.+?)\*\*)\s*$")

    while i < len(lines):
        line = lines[i]

        # --- standalone subheading ---
        m = subhead_re.match(line)
        if m and not _is_table_row(line):
            flush_para()
            title = (m.group(1) or m.group(2) or "").strip()
            style = subhead_style or para_style
            head_fl = Paragraph(_inline_md(title), style)
            i += 1
            # Skip blank lines after the subheading
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            # If a markdown table follows immediately, keep heading + table together
            if i < len(lines) and _is_table_row(lines[i]):
                tlines = []
                while i < len(lines) and (_is_table_row(lines[i]) or lines[i].strip() == ""):
                    if lines[i].strip() == "":
                        if i + 1 < len(lines) and _is_table_row(lines[i + 1]):
                            i += 1
                            continue
                        break
                    tlines.append(lines[i])
                    i += 1
                rows = [_parse_row(r) for r in tlines if not _is_sep_row(r)]
                if rows:
                    header = rows[0]
                    body_rows = rows[1:]
                    ncols = len(header)
                    def pad(r, n=ncols):
                        r = list(r) + [""] * max(0, n - len(r))
                        return r[:n]
                    header = pad(header)
                    body_rows = [pad(r) for r in body_rows]
                    ch = cellhead_style or para_style
                    cs = cell_style or para_style
                    data = [[Paragraph(_inline_md(c), ch) for c in header]]
                    data += [[Paragraph(_inline_md(c), cs) for c in r] for r in body_rows]
                    col_keys = [f"c{n}" for n in range(ncols)]
                    fake_rows = [{col_keys[j]: body_rows[ri][j] for j in range(ncols)}
                                 for ri in range(len(body_rows))]
                    widths = compute_col_widths(col_keys, fake_rows, font=table_font, size=table_size)
                    from reportlab.lib import colors as _colors
                    t = Table(data, repeatRows=1, hAlign="LEFT", colWidths=widths)
                    t.setStyle(TableStyle([
                        ("LINEABOVE", (0, 0), (-1, 0), 1.0, _colors.HexColor("#3a3a3a")),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.5, _colors.HexColor("#3a3a3a")),
                        ("LINEBELOW", (0, 1), (-1, -2), 0.3, _colors.HexColor("#c9c5bc")),
                        ("LINEBELOW", (0, -1), (-1, -1), 1.0, _colors.HexColor("#3a3a3a")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]))
                    out.append(("full", KeepTogether([head_fl, Spacer(1, 4), t])))
                    out.append(("full", Spacer(1, 8)))
                    continue
            # No table follows: full-width subheading alone
            out.append(("full", head_fl))
            out.append(("full", Spacer(1, 4)))
            continue

        # --- bullet / numbered list ---
        bullet_m = re.match(r'^(\s*)([-*+]|\d+[.)])\s+(.*)$', line)
        if bullet_m and not _is_table_row(line):
            flush_para()
            items = []
            while i < len(lines):
                bm = re.match(r'^(\s*)([-*+]|\d+[.)])\s+(.*)$', lines[i])
                if not bm:
                    # continuation line of previous item (indented, non-empty, not a new block)
                    if items and lines[i].startswith('  ') and lines[i].strip() and not subhead_re.match(lines[i]) and not _is_table_row(lines[i]):
                        items[-1] = items[-1] + ' ' + lines[i].strip()
                        i += 1
                        continue
                    break
                marker, body = bm.group(2), bm.group(3)
                numbered = bool(re.match(r'\d+[.)]', marker))
                items.append((numbered, body))
                i += 1
            for n, (numbered, body) in enumerate(items, 1):
                bullet = f'{n}.' if numbered else '\u2022'
                list_style = ParagraphStyle(
                    'md_bullet',
                    parent=para_style,
                    leftIndent=14,
                    bulletIndent=0,
                    spaceBefore=1.5,
                    spaceAfter=2.5,
                )
                out.append(("col", Paragraph(
                    _inline_md(body),
                    list_style,
                    bulletText=bullet,
                )))
            continue

        if _is_table_row(line):
            flush_para()
            tlines = []
            while i < len(lines) and (_is_table_row(lines[i]) or lines[i].strip() == ""):
                if lines[i].strip() == "":
                    if i + 1 < len(lines) and _is_table_row(lines[i + 1]):
                        i += 1
                        continue
                    break
                tlines.append(lines[i])
                i += 1
            rows = [_parse_row(r) for r in tlines if not _is_sep_row(r)]
            if not rows:
                continue
            header = rows[0]
            body_rows = rows[1:]
            ncols = len(header)
            def pad(r, n=ncols):
                r = list(r) + [""] * max(0, n - len(r))
                return r[:n]
            header = pad(header)
            body_rows = [pad(r) for r in body_rows]
            ch = cellhead_style or para_style
            cs = cell_style or para_style
            data = [[Paragraph(_inline_md(c), ch) for c in header]]
            data += [[Paragraph(_inline_md(c), cs) for c in r] for r in body_rows]
            col_keys = [f"c{n}" for n in range(ncols)]
            fake_rows = [{col_keys[j]: body_rows[ri][j] for j in range(ncols)}
                         for ri in range(len(body_rows))]
            widths = compute_col_widths(col_keys, fake_rows, font=table_font, size=table_size)
            from reportlab.lib import colors as _colors
            t = Table(data, repeatRows=1, hAlign="LEFT", colWidths=widths)
            t.setStyle(TableStyle([
                ("LINEABOVE", (0, 0), (-1, 0), 1.0, _colors.HexColor("#3a3a3a")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, _colors.HexColor("#3a3a3a")),
                ("LINEBELOW", (0, 1), (-1, -2), 0.3, _colors.HexColor("#c9c5bc")),
                ("LINEBELOW", (0, -1), (-1, -1), 1.0, _colors.HexColor("#3a3a3a")),
                ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            out.append(("full", t))
            out.append(("full", Spacer(1, 8)))
            continue

        if line.strip() == "":
            flush_para()
            i += 1
            continue
        para_buf.append(line)
        i += 1

    return out




def _promote_pre_table_cols(pieces, max_chars=220):
    """
    Promote consecutive "col" pieces that sit immediately before a "full"
    piece (table/subhead) to full-width ONLY when that run is short.

    Long body prose stays two-column; a one-line intro above a table does not
    leave a blank second column.
    """
    if not pieces:
        return pieces

    def _chars(fl):
        # Best-effort character count from a Paragraph or nested flowable
        try:
            t = getattr(fl, 'text', None) or getattr(fl, 'plain', None)
            if t:
                return len(re.sub(r'<[^>]+>', '', str(t)))
        except Exception:
            pass
        return 80  # unknown flowable: treat as moderate

    out = []
    i = 0
    n = len(pieces)
    while i < n:
        kind, fl = pieces[i]
        if kind != "col":
            out.append(pieces[i])
            i += 1
            continue
        j = i
        cols = []
        while j < n and pieces[j][0] == "col":
            cols.append(pieces[j][1])
            j += 1
        k = j
        while k < n and pieces[k][0] == "full" and isinstance(pieces[k][1], Spacer):
            k += 1
        follows_full = k < n and pieces[k][0] == "full"
        total = sum(_chars(c) for c in cols)
        # Short run before a table/subhead -> full width; otherwise keep columns
        if follows_full and total <= max_chars:
            for c in cols:
                out.append(("full", c))
        else:
            for c in cols:
                out.append(("col", c))
        i = j
    return out



def _human_label(name: str) -> str:
    return (name or "").replace("_", " ").strip().capitalize()


def _block_order(block: dict, order_key: str, map_key: str) -> list:
    """Return ordered sub-block names from item_order/field_order or map keys."""
    order = block.get(order_key)
    if isinstance(order, list) and order:
        return order
    mapping = block.get(map_key) or {}
    return list(mapping.keys())



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
        "section": ParagraphStyle("section", fontName=f["bold"], fontSize=11, leading=14,
                                  textColor=INK, spaceBefore=10, spaceAfter=6),
        "subhead": ParagraphStyle("subhead", fontName=f["sans_bold"], fontSize=9, leading=11.5,
                                 textColor=INK, spaceBefore=8, spaceAfter=3),
    }

    # ---------------- full-width front matter (page 1) ----------------
    front = []
    schema = load_schema(article["schema"]) if article.get("schema") else {}
    # Badge-style leaf blocks (if any) become the kicker above the title
    kickers = []
    for bname in schema.get("structure") or []:
        bdef = (schema.get("blocks") or {}).get(bname) or {}
        if bdef.get("style") == "badge" and article.get(bname):
            kickers.append(str(article[bname]))
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
            try:
                idx = affs.index(a["affiliation"]) + 1
                name += f"<super>{idx}</super>"
            except ValueError:
                pass
        parts.append(name)
    front.append(Paragraph(", ".join(parts), st["authors"]))
    for i, af in enumerate(affs, 1):
        prefix = f"<super>{i}</super> " if len(affs) > 1 else ""
        front.append(Paragraph(prefix + escape(af), st["affil"]))
    front.append(HRFlowable(width="100%", thickness=0.8, color=RULE_STRONG, spaceBefore=9, spaceAfter=4))

    # ---------------- schema-driven body (structure / blocks) ----------------
    # Each emitted unit is ("col", flowables) or ("full", flowables).
    blocks = []
    table_no = 0

    def panel(flowables, full=False):
        """Boxed style: tinted panel. full=True spans the text measure."""
        width = TEXT_WIDTH if full else COLUMN_WIDTH
        t = Table([[fl] for fl in flowables], colWidths=[width])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(PANEL)),
            ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.HexColor(ACCENT)),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
        ]))
        return t

    def emit_leaf(field_def, value, name=None):
        """Render one leaf field; appends to blocks."""
        nonlocal table_no
        if not isinstance(field_def, dict):
            return
        shape = field_def.get("shape") or "text"
        style_name = field_def.get("style") or ("table" if shape == "table" else "plain")
        label = field_def.get("label")  # may be None: section questions use content as heading
        optional = field_def.get("optional")

        if style_name == "badge":
            return  # already used as kicker

        empty = value is None or value == "" or value == []
        if empty:
            if not optional and label:
                blocks.append(("col", [Paragraph(
                    f"[Missing required field: {escape(label)}]", st["error"])]))
            return

        heading = Paragraph(escape(label).upper(), st["head"]) if label else None
        text_style = st["opinion"] if style_name == "opinion" else st["text"]

        # ---- table (structured rows or dynamic wrapper) ----
        if shape == "table":
            # Wrapper form: {label, columns, rows}
            if isinstance(value, dict) and "columns" in value and "rows" in value:
                cols = value.get("columns") or []
                rows = value.get("rows") or []
                tlabel = value.get("label") or label or "Table"
            elif isinstance(value, list) and all(isinstance(r, dict) for r in value):
                cols = field_def.get("columns") or []
                if cols == "dynamic" or not cols:
                    cols = list(value[0].keys()) if value else []
                rows = value
                tlabel = label or "Table"
            else:
                blocks.append(("col", [
                    heading if heading else Spacer(1, 0),
                    Paragraph(
                        f"[cannot display &quot;{escape(str(label or name))}&quot;: "
                        f"content does not match the expected table shape]", st["error"])
                ]))
                return
            table_no += 1
            data = [[Paragraph(escape(str(c).replace("_", " ")).upper(), st["cellhead"]) for c in cols]]
            data += [[Paragraph(escape(str(r.get(c, ""))), st["cell"]) for c in cols] for r in rows]
            t = Table(data, repeatRows=1, hAlign="LEFT",
                      colWidths=compute_col_widths(cols, rows, font=f["body"], size=8.3))
            t.setStyle(TableStyle([
                ("LINEABOVE", (0, 0), (-1, 0), 1.0, colors.HexColor(RULE_STRONG)),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor(RULE_STRONG)),
                ("LINEBELOW", (0, 1), (-1, -2), 0.3, colors.HexColor(RULE)),
                ("LINEBELOW", (0, -1), (-1, -1), 1.0, colors.HexColor(RULE_STRONG)),
                ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            blocks.append(("full", [
                Paragraph(f"TABLE {table_no}.&nbsp; {escape(str(tlabel)).upper()}", st["caption"]),
                t, Spacer(1, 8)]))
            return

        # ---- list of tables (dynamic) ----
        if shape == "list" and isinstance(field_def.get("item"), dict) and field_def["item"].get("shape") == "table":
            if not isinstance(value, list):
                return
            for twrap in value:
                emit_leaf(field_def["item"], twrap, name=name)
            return

        # ---- list (bullets / numbered) ----
        if shape == "list" and isinstance(value, list):
            body = []
            numbered = style_name == "numbered"
            for n, item in enumerate(value, 1):
                body.append(Paragraph(
                    _inline_md(str(item)),
                    st["ref"] if numbered else st["bullet"],
                    bulletText=f"{n}." if numbered else "\u2022"))
            if style_name == "boxed" and heading:
                blocks.append(("col", [heading, panel(body)]))
            elif heading:
                blocks.append(("col", [heading] + body))
            else:
                blocks.append(("col", body))
            return

        # ---- boolean / date (run-in) ----
        if shape in ("boolean", "date"):
            import datetime as _dt
            if shape == "boolean":
                txt = "Yes" if value else "No"
            else:
                txt = (value.strftime("%-d %B %Y")
                       if isinstance(value, (_dt.date, _dt.datetime)) else str(value))
            lab = label or _human_label(name or "")
            blocks.append(("col", [Paragraph(
                f'<font name="{f["sans_bold"]}" size="7.8">{escape(lab).upper()}</font>'
                f"&nbsp;&nbsp; {escape(txt)}", st["runin"])]))
            return

        # ---- section heading (value IS the heading) ----
        if style_name == "section" and shape == "text":
            blocks.append(("full", [Spacer(1, 6), Paragraph(_inline_md(str(value)), st["section"]), Spacer(1, 2)]))
            return

        # ---- text (with Markdown) ----
        if shape == "text":
            pieces = md_to_flowables(
                value, text_style,
                cell_style=st["cell"], cellhead_style=st["cellhead"],
                caption_style=st["caption"],
                table_font=f["body"], table_size=8.3,
                subhead_style=st.get("subhead"),
            )
            pieces = _promote_pre_table_cols(pieces)
            if not pieces:
                return

            # Boxed fields: label + body as full-width panel so two-column
            # balancing cannot interleave them with neighbouring sections.
            if style_name == "boxed":
                col_bits = []
                for kind, fl in pieces:
                    if kind == "col":
                        col_bits.append(fl)
                    else:
                        # flush any pending body into a full-width boxed panel
                        if col_bits:
                            body = ([heading] if heading else []) + [panel(col_bits, full=True)]
                            blocks.append(("full", body + [Spacer(1, 4)]))
                            heading = None
                            col_bits = []
                        # full pieces (subheads, tables) stay full-width, no extra TABLE N.
                        # (subheading above the table is the caption)
                        if not isinstance(fl, Spacer):
                            blocks.append(("full", [fl]))
                        else:
                            blocks.append(("full", [fl]))
                if col_bits or heading:
                    body = ([heading] if heading else []) + ([panel(col_bits, full=True)] if col_bits else [])
                    blocks.append(("full", body + [Spacer(1, 4)]))
                return

            # Plain / opinion text: column body; full-width for subheads + tables
            if heading:
                blocks.append(("full", [heading, Spacer(1, 2)]))
            for kind, fl in pieces:
                if kind == "col":
                    blocks.append(("col", [fl]))
                else:
                    # Do not inject "TABLE N." — markdown **subheadings** already
                    # name the table; a bare number looks orphaned under a heading.
                    blocks.append(("full", [fl]))
            return

        blocks.append(("col", [Paragraph(
            f"[unrecognised shape: {escape(str(shape))}]", st["error"])]))

    def emit_block(block_def, value, name=None):
        """Dispatch one structure entry (leaf, repeat, or block)."""
        if not isinstance(block_def, dict):
            return
        shape = block_def.get("shape")
        if shape == "repeat":
            if not isinstance(value, list):
                return
            order = _block_order(block_def, "item_order", "item")
            item_defs = block_def.get("item") or {}
            for item in value:
                if not isinstance(item, dict):
                    continue
                for sub_name in order:
                    sub_def = item_defs.get(sub_name) or {}
                    emit_leaf(sub_def, item.get(sub_name), name=sub_name)
                # thin rule between sections
                blocks.append(("col", [Spacer(1, 4),
                                       HRFlowable(width="100%", thickness=0.4,
                                                  color=RULE, spaceBefore=2, spaceAfter=4)]))
        elif shape == "block":
            if not isinstance(value, dict):
                return
            order = _block_order(block_def, "field_order", "fields")
            field_defs = block_def.get("fields") or {}
            for sub_name in order:
                sub_def = field_defs.get(sub_name) or {}
                emit_leaf(sub_def, value.get(sub_name), name=sub_name)
        else:
            emit_leaf(block_def, value, name=name)

    structure = schema.get("structure") or []
    block_defs = schema.get("blocks") or {}
    # Backward compatibility: old flat `fields:` list schemas
    if not structure and schema.get("fields"):
        for field in schema["fields"]:
            if field.get("visibility") == "editor":
                continue
            emit_leaf(field, article.get(field.get("key")), name=field.get("key"))
    else:
        for bname in structure:
            emit_block(block_defs.get(bname) or {}, article.get(bname), name=bname)

        story = list(front) + [NextPageTemplate("later")]
    run = []

    def flush():
        if not run:
            return
        # Always two-column for body text. Short intros above tables are
        # already promoted to full-width in _promote_pre_table_cols.
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
