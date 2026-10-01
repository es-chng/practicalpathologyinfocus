#!/usr/bin/env python3
"""
Builds a PDF only for an article that is new or has changed since the last run --
reusing everything else from a cache directory that persists between CI runs.

"Changed" means either the article's own file changed, OR the schema it declares
changed (a schema edit can alter every article that uses it, so those must all be
considered changed too, even though their own files are untouched).

A small manifest.json inside CACHE_DIR records, per article, the combined hash of
its own file plus its schema's file, as of the last successful build. If a file
declares a schema this script cannot find, the schema itself has an unresolved
name that would already have been caught by validate_schema.py earlier in the
pipeline -- this script assumes validation has already passed.

Usage: python scripts/render_changed_pdfs.py CACHE_DIR OUTPUT_DIR
    CACHE_DIR persists between runs (e.g. restored via actions/cache).
    OUTPUT_DIR is where the deploy step picks PDFs up from; every current
    article's PDF is copied here, whether freshly built or reused.
"""
import hashlib
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from render_pdf import build, count_pdf_pages, is_placeholder_doi  # noqa: E402
import yaml  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST_NAME = "manifest.json"



# Same rule as Jekyll: every Markdown file anywhere under _articles/, in any
# letter case (.md, .MD, .markdown, ...). Anything else there is reported, so a
# misnamed article is never silently skipped.
MARKDOWN_EXTS = {".md", ".markdown", ".mkdown", ".mkdn", ".mkd"}


def find_articles(root):
    folder = root / "_articles"
    found, ignored = [], []
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        (found if p.suffix.lower() in MARKDOWN_EXTS else ignored).append(p)
    for p in ignored:
        print(f"WARNING  {p.relative_to(root)} is in _articles/ but is not a Markdown (.md) "
              f"file, so it is not treated as an article")
    return found


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


PAGES_DATA = ROOT / "_data" / "pdf-pages.yml"


def ledger_doi(article_path):
    """The real DOI recorded for this article by zenodo_articles.py, if any."""
    ledger_path = ROOT / "_data" / "zenodo-ledger.yml"
    if not ledger_path.is_file():
        return None
    ledger = yaml.safe_load(ledger_path.read_text()) or {}
    doi = (ledger.get(article_path.stem) or {}).get("doi")
    return None if is_placeholder_doi(doi) else doi


def combined_hash(article_path):
    """The article's own bytes, plus its schema's bytes if it declares one,
    plus its ledger DOI -- so the PDF is rebuilt once a real DOI is minted --
    plus the PDF renderer's own code, so every PDF is rebuilt when the layout
    (e.g. justification, spacing) in render_pdf.py changes."""
    h = hashlib.sha256()
    h.update((ROOT / "scripts" / "render_pdf.py").read_bytes())
    h.update(str(ledger_doi(article_path) or "").encode())
    h.update(article_path.read_bytes())
    text = article_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("schema:"):
            schema_name = line.split(":", 1)[1].strip().strip('"').strip("'")
            schema_path = ROOT / "_data" / f"{schema_name}.yml"
            if schema_path.is_file():
                h.update(schema_path.read_bytes())
            break
    return h.hexdigest()


def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: python scripts/render_changed_pdfs.py CACHE_DIR OUTPUT_DIR")
    cache_dir = pathlib.Path(sys.argv[1])
    out_dir = pathlib.Path(sys.argv[2])
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = cache_dir / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}

    current_articles = find_articles(ROOT)
    current_names = {p.name for p in current_articles}

    built, reused, failed = [], [], []
    page_counts = {}

    for path in current_articles:
        h = combined_hash(path)
        pdf_name = f"{path.stem}.pdf"
        cached_pdf = cache_dir / pdf_name
        unchanged = manifest.get(path.name) == h and cached_pdf.is_file()

        if unchanged:
            reused.append(path.name)
        else:
            try:
                build(str(path), str(cached_pdf), doi_override=ledger_doi(path))
                manifest[path.name] = h
                built.append(path.name)
            except Exception as exc:  # noqa: BLE001 -- one bad article should not stop the rest
                failed.append((path.name, str(exc)))
                print(f"FAIL  {path.name}: {exc}", file=sys.stderr)
                continue

        shutil.copy(cached_pdf, out_dir / pdf_name)
        page_counts[path.stem] = count_pdf_pages(cached_pdf)

    # Forget articles that no longer exist, so the cache does not grow forever
    # and a deleted article's stale PDF is never left lying around.
    removed = [name for name in manifest if name not in current_names]
    for name in removed:
        del manifest[name]
        stale_pdf = cache_dir / f"{pathlib.Path(name).stem}.pdf"
        stale_pdf.unlink(missing_ok=True)

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    # Page counts for the website ("4 pages"), read by the article and issue
    # pages. Written before Jekyll builds the site; not committed.
    PAGES_DATA.write_text(
        "# Written by scripts/render_changed_pdfs.py on every build. Do not edit.\n"
        + yaml.safe_dump(page_counts, sort_keys=True)
    )

    print(f"Built {len(built)}, reused {len(reused)}, removed {len(removed)}, failed {len(failed)} "
          f"(of {len(current_articles)} article(s) total).")
    if built:
        print("Built:  " + ", ".join(built))
    if reused:
        print("Reused: " + ", ".join(reused))
    if removed:
        print("Removed (article no longer exists): " + ", ".join(removed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
