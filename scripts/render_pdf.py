#!/usr/bin/env python3
"""Print one built article page (Jekyll HTML) to PDF with WeasyPrint.

The print design is assets/css/pdf.css. Before printing, two changes are
made to a copy of the page (the website itself is untouched):
  - stylesheet and image links are pointed at the files in _site/;
  - each table gets column widths sized by its content, and Markdown tables
    are wrapped in <div class="field-markdown-table"> so they can span both
    columns (WeasyPrint does not apply column-span to a table itself).
Both work on HTML this site generates itself, where tables never nest.

A whole issue is printed the same way, as one document (build_issue).

Usage: python scripts/render_pdf.py _site/articles/NAME/index.html out.pdf
"""
import html
import pathlib
import re
import sys

from weasyprint import HTML

from journal import config

TABLE = re.compile(r"(<table\b[^>]*>)(.*?</table>)", re.S | re.I)
ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
CELL = re.compile(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")


def column_widths(rows, available=150, pad=2):
    """Column widths in percent, sized by content, measured in characters.

    Each column gets at least its longest word. If every column fits on one
    line (about `available` characters across the page at table size),
    widths follow the text; otherwise the spare room is shared by the square
    root of each column's extra need, so one long column cannot starve the rest.
    """
    n = max(len(r) for r in rows)
    cols = [[r[i] if i < len(r) else "" for r in rows] for i in range(n)]
    mins = [max((len(w) for c in col for w in c.split()), default=1) + pad for col in cols]
    prefs = [max(max((len(c) for c in col), default=1) + pad, m) for col, m in zip(cols, mins)]
    if sum(prefs) <= available:
        widths = prefs
    elif sum(mins) >= available:
        widths = mins
    else:
        wants = [(p - m) ** 0.5 for p, m in zip(prefs, mins)]
        spare = available - sum(mins)
        widths = [m + spare * w / (sum(wants) or 1) for m, w in zip(mins, wants)]
    return [100 * w / sum(widths) for w in widths]


def with_column_widths(match):
    rows = [[" ".join(html.unescape(TAG.sub(" ", c)).split()) for c in CELL.findall(r)]
            for r in ROW.findall(match.group(2))]
    rows = [r for r in rows if r]
    if not rows:
        return match.group(0)
    cols = "".join(f'<col style="width:{w:.1f}%">' for w in column_widths(rows))
    table = f"{match.group(1)}<colgroup>{cols}</colgroup>{match.group(2)}"
    is_markdown = "field-table" not in match.group(1)  # schema tables carry that class
    return f'<div class="field-markdown-table">{table}</div>' if is_markdown else table


def _print(source, out, site_dir, baseurl):
    """Print HTML source to a PDF; return its number of pages."""
    # stylesheets and images: "/baseurl/assets/x?v=123" -> "assets/x", read from site_dir
    source = re.sub(r'(<(?:link|img)\b[^>]*\b(?:href|src)=")' + re.escape(baseurl) + r'/([^"?]*)(?:\?[^"]*)?"',
                    r'\1\2"', source)
    source = TABLE.sub(with_column_widths, source)
    # images are downsampled to 300 dpi at their printed size: print quality, small
    # files. These options must be given to render(), where images are loaded.
    document = HTML(string=source, base_url=pathlib.Path(site_dir).resolve().as_uri() + "/").render(
        optimize_images=True, dpi=300, jpeg_quality=85)
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    document.write_pdf(str(out))
    return len(document.pages)


def _baseurl(baseurl):
    return (config().get("baseurl", "") if baseurl is None else baseurl).rstrip("/")


def build(page_html, out, site_dir=None, baseurl=None):
    """Print one built article page; return its number of pages."""
    page_html = pathlib.Path(page_html).resolve()
    site_dir = site_dir or page_html.parents[2]
    return _print(page_html.read_text(encoding="utf-8"), out, site_dir, _baseurl(baseurl))


ARTICLE = re.compile(r'<article class="base-frame">.*?</article>', re.S)


def build_issue(issue_html, article_pages, out, site_dir, baseurl=None):
    """Print a whole issue as one document; return its number of pages.

    The issue page becomes the contents; each article page's <article> follows
    on a new page, so page numbers run through the issue and the contents can
    give each article's first page. article_pages: {slug: built page}, in order.
    """
    baseurl = _baseurl(baseurl)
    source = pathlib.Path(issue_html).read_text(encoding="utf-8")
    parts = []
    for slug, page in article_pages.items():
        article = ARTICLE.search(pathlib.Path(page).read_text(encoding="utf-8")).group(0)
        parts.append(f'<section class="issue-article" id="article-{slug}">{article}</section>')
        # contents links go to the article inside this PDF, not to its web page
        source = source.replace(f'href="{baseurl}/articles/{slug}/"', f'href="#article-{slug}"')
    source = source.replace("</body>", '<div class="issue-articles">' + "".join(parts) + "</div></body>", 1)
    return _print(source, out, site_dir, baseurl)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    pages = build(sys.argv[1], sys.argv[2])
    print(f"wrote {sys.argv[2]} ({pages} page{'s' * (pages != 1)})")
