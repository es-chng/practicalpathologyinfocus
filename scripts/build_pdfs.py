#!/usr/bin/env python3
"""Print every article's built page to PDF, then fill page counts into the site.

Run after the site is built. A PDF is reused only when its article, schema,
built page, stylesheets or scripts are unchanged.

Usage: python scripts/build_pdfs.py OUT_DIR --site-dir _site [--cache CACHE_DIR]
"""
import argparse
import hashlib
import json
import pathlib
import re
import shutil
import sys

from journal import DATA, ROOT, articles, config, front_matter
from render_pdf import build


PDF_CSS = ROOT / "assets" / "css" / "pdf.css"
WEB_CSS = ROOT / "assets" / "css" / "style.css"


def fingerprint(article_path, html_path):
    fm = front_matter(article_path)
    schema_path = DATA / f"{fm.get('schema')}.yml"
    h = hashlib.sha256()
    inputs = [article_path, schema_path, ROOT / "_config.yml", html_path,
              PDF_CSS, WEB_CSS, *sorted((ROOT / "scripts").glob("*.py"))]
    for path in inputs:
        h.update(str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path).encode())
        data = path.read_bytes() if path.is_file() else b""
        if path == html_path:
            # the stylesheet links carry a build timestamp (?v=...) that changes
            # every build without changing the page; ignore it, or no PDF is reused
            data = re.sub(rb"\?v=\d+", b"", data)
        h.update(data)
    # images shown in the article: replacing a file under the same name must reprint the PDF
    for name in sorted(set(re.findall(r'<img\b[^>]*\bsrc="[^"]*/assets/images/([^"?]+)"',
                                      html_path.read_text(encoding="utf-8")))):
        image = ROOT / "assets" / "images" / name
        h.update(name.encode() + (image.read_bytes() if image.is_file() else b""))
    h.update(str(config().get("baseurl", "")).encode())
    return h.hexdigest()


MARKER = re.compile(r"<!--pdf-pages:([\w-]+)\|([^|]*)\|(.*?)-->")


def fill_page_counts(site_dir, pages):
    """Write page counts into the built site, so it is built only once.

    Templates leave <!--pdf-pages:SLUG|BEFORE|AFTER--> where a count goes;
    it becomes BEFORE + "12 pages" + AFTER, or nothing if there is no PDF.
    """
    def count(m):
        n = pages.get(m.group(1))
        return f"{m.group(2)}{n} page{'s' * (n != 1)}{m.group(3)}" if n else ""

    for page in site_dir.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        filled = MARKER.sub(count, text)
        if filled != text:
            page.write_text(filled, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=pathlib.Path)
    ap.add_argument("--site-dir", type=pathlib.Path, default=ROOT / "_site",
                    help="Jekyll output directory (must already be built)")
    ap.add_argument("--cache", type=pathlib.Path)
    ap.add_argument("--baseurl", default=None, help="Jekyll baseurl used when building the site")
    args = ap.parse_args()
    site_dir = args.site_dir.resolve()
    baseurl = config().get("baseurl", "") if args.baseurl is None else args.baseurl
    cache = args.cache or args.out
    for directory in (args.out, cache):
        directory.mkdir(parents=True, exist_ok=True)
    manifest_path = cache / "manifest.json"
    old = json.loads(manifest_path.read_text()) if args.cache and manifest_path.is_file() else {}

    manifest, built, failed = {}, [], []
    for article_path in articles():
        slug = article_path.stem
        html_path = site_dir / "articles" / slug / "index.html"
        pdf_path = cache / f"{slug}.pdf"
        if not html_path.is_file():
            failed.append(slug)
            print(f"FAIL   {slug}: Jekyll HTML not found at {html_path}", file=sys.stderr)
            continue

        digest = fingerprint(article_path, html_path)
        if old.get(slug, {}).get("hash") == digest and pdf_path.is_file():
            manifest[slug] = old[slug]
        else:
            try:
                pages = build(html_path, pdf_path, site_dir=site_dir, baseurl=baseurl)
                manifest[slug] = {"hash": digest, "pages": pages}
                built.append(slug)
            except Exception as error:  # report all failed articles, then fail the workflow
                failed.append(slug)
                print(f"FAIL   {slug}: {error}", file=sys.stderr)
                continue

        if cache != args.out:
            shutil.copy2(pdf_path, args.out / pdf_path.name)

    for slug in set(old) - set(manifest):
        (cache / f"{slug}.pdf").unlink(missing_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    fill_page_counts(site_dir, {slug: entry["pages"] for slug, entry in manifest.items()})
    print(f"PDFs: built {len(built)}, reused {len(manifest) - len(built)}, failed {len(failed)}"
          + (f" (built: {', '.join(built)})" if built else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
