#!/usr/bin/env python3
"""Give each published article a DOI on Zenodo, with the DOI printed in its PDF.

For each published article that is new or changed since its last deposit:
reserve a DOI -> typeset the PDF with it -> upload -> publish -> record the
DOI in _data/zenodo-ledger.yml (saved after every deposit). Unchanged
articles are never deposited twice, and a test run never replaces a real DOI.

  python scripts/zenodo.py                sandbox.zenodo.org (test DOIs, 10.5072/...)
  python scripts/zenodo.py --live         zenodo.org (permanent DOIs)
  python scripts/zenodo.py --clear-tests  remove test DOIs from the ledger

Needs ZENODO_TOKEN (scopes deposit:write and deposit:actions) from the
matching server.
"""
import argparse
import datetime as dt
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile

import requests

from journal import (DATA, ROOT, articles, config, front_matter, is_published, is_test_doi, ledger, save_ledger,
                     schema, teaser_key)
from render_pdf import build


def content_hash(path, fm):
    """The article and its schema; the DOI printed in the PDF does not count."""
    return hashlib.sha256(path.read_bytes() + (DATA / f"{fm['schema']}.yml").read_bytes()).hexdigest()


def metadata(path, fm):
    cfg = config()
    site = f"{cfg.get('url', '')}{cfg.get('baseurl', '')}".rstrip("/")
    meta = {
        "upload_type": "publication",
        "publication_type": "article",
        "title": fm["title"],
        "creators": [{"name": f"{a['family']}, {a['given']}",
                      **({"affiliation": a["affiliation"]} if a.get("affiliation") else {}),
                      **({"orcid": str(a["orcid"]).rsplit("/", 1)[-1]} if a.get("orcid") else {})}
                     for a in fm["authors"]],
        "description": str(fm.get(teaser_key(schema(fm["schema"]))) or fm["title"]),
        "publication_date": str(fm["published_date"]),
        "access_right": "open",
        "license": str(fm["licence"]).lower().replace(" ", "-"),  # "CC BY 4.0" -> "cc-by-4.0"
        "journal_title": cfg.get("title", ""),
        "journal_volume": str(fm["volume"]),
        "journal_issue": str(fm["issue"]),
        "prereserve_doi": True,
    }
    if fm.get("keywords"):
        meta["keywords"] = [str(k) for k in fm["keywords"]]
    if site:
        meta["related_identifiers"] = [{"identifier": f"{site}/articles/{path.stem}/", "relation": "isIdenticalTo",
                                        "resource_type": "publication-article", "scheme": "url"}]
    return meta


def deposit(api, session, path, fm, book):
    """Reserve a DOI, render its Jekyll HTML with WeasyPrint, upload and publish."""
    r = session.post(f"{api}/deposit/depositions", json={}, timeout=60)
    r.raise_for_status()
    record = r.json()
    r = session.put(f"{api}/deposit/depositions/{record['id']}", json={"metadata": metadata(path, fm)}, timeout=60)
    r.raise_for_status()
    doi = r.json()["metadata"]["prereserve_doi"]["doi"]
    slug = path.stem
    previous = book.get(slug)
    baseurl = os.environ.get("JEKYLL_BASEURL", config().get("baseurl", ""))
    book[slug] = {"doi": doi, "record": record["id"]}
    save_ledger(book)  # Jekyll reads the provisional DOI for the deposited PDF.
    try:
        env = {**os.environ, "JEKYLL_ENV": "production"}
        subprocess.run(["bundle", "exec", "jekyll", "build", "--baseurl", baseurl],
                       cwd=ROOT, env=env, check=True)
        with tempfile.TemporaryDirectory() as tmp:
            pdf = pathlib.Path(tmp) / f"{slug}.pdf"
            page = ROOT / "_site" / "articles" / slug / "index.html"
            build(page, pdf, site_dir=ROOT / "_site", baseurl=baseurl)
            with pdf.open("rb") as fp:
                session.put(f"{record['links']['bucket']}/{slug}.pdf", data=fp, timeout=300).raise_for_status()
        r = session.post(f"{api}/deposit/depositions/{record['id']}/actions/publish", timeout=60)
        r.raise_for_status()
    except Exception:
        if previous is None:
            book.pop(slug, None)
        else:
            book[slug] = previous
        save_ledger(book)
        raise
    return {"doi": r.json().get("doi") or doi, "record": record["id"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="deposit to zenodo.org (permanent)")
    ap.add_argument("--force", action="store_true", help="deposit even unchanged articles again")
    ap.add_argument("--clear-tests", action="store_true", help="remove test DOIs from the ledger")
    args = ap.parse_args()
    book = ledger()

    if args.clear_tests:
        tests = [s for s, e in book.items() if is_test_doi(e.get("doi"))]
        for s in tests:
            del book[s]
        save_ledger(book)
        print(f"Removed {len(tests)} test DOI(s): {', '.join(tests) or 'none'}")
        return 0

    token = os.environ.get("ZENODO_TOKEN", "").strip()
    if not token:
        sys.exit("ZENODO_TOKEN is not set (a token with deposit:write and deposit:actions).")
    api = "https://zenodo.org/api" if args.live else "https://sandbox.zenodo.org/api"
    session = requests.Session()
    session.params = {"access_token": token}
    print(f"Target: {api}")

    failed = 0
    for path in articles():
        fm, slug = front_matter(path), path.stem
        if not is_published(fm):
            continue
        entry, h = book.get(slug) or {}, content_hash(path, fm)
        if entry.get("doi") and not is_test_doi(entry["doi"]) and not args.live:
            print(f"KEEP   {slug}: has a real DOI ({entry['doi']}); a test run never replaces it")
            continue
        if entry.get("hash") == h and is_test_doi(entry.get("doi")) != args.live and not args.force:
            print(f"SAME   {slug}: unchanged since {entry['doi']}")
            continue
        try:
            book[slug] = {**deposit(api, session, path, fm, book), "hash": h,
                          "deposited": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            save_ledger(book)  # saved at once, so a later failure cannot lose this DOI
            print(f"DOI    {slug}: {book[slug]['doi']}")
        except Exception as e:  # noqa: BLE001 -- report, carry on with the next article
            failed += 1
            print(f"FAIL   {slug}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
