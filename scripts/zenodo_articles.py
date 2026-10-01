#!/usr/bin/env python3
"""
Deposit article PDFs to Zenodo with the DOI embedded in the final PDF.

Correct order (required so the published PDF cites a real DOI):
  1. Create a Zenodo deposition with prereserve_doi → obtain the DOI
  2. Render the PDF with that DOI in the footer (doi_override)
  3. Upload that PDF to the deposition
  4. Publish → DOI becomes live
  5. Record DOI in _data/zenodo-ledger.yml (no write-back into article .md)

If the article is unchanged and already has a real ledger DOI, the PDF is
re-rendered with that DOI (so /files/<slug>.pdf always matches the ledger)
and skipped for a new deposit.

Environment
-----------
  ZENODO_TOKEN          Required (unless --dry-run). deposit:write + deposit:actions
  ZENODO_LIVE           "1" → zenodo.org; default → sandbox.zenodo.org
  ZENODO_CREATOR_NAME   Fallback creator name
  ZENODO_CREATOR_ORCID  Optional
  SITE_URL              Optional; related_identifiers link back to the article page

Usage
-----
  python scripts/zenodo_articles.py PDF_OUT_DIR
  python scripts/zenodo_articles.py PDF_OUT_DIR --dry-run
  python scripts/zenodo_articles.py PDF_OUT_DIR --force
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

import requests
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from render_pdf import build as render_pdf_build  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "_data" / "zenodo-ledger.yml"
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)

PLACEHOLDER_DOI_PREFIXES = (
    "10.5281/zenodo.000",
    "10.5281/zenodo.xxxx",
    "doi:pending",
    "pending",
)



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


def is_placeholder_doi(doi) -> bool:
    if not doi:
        return True
    d = str(doi).strip().lower()
    return any(d.startswith(p) for p in PLACEHOLDER_DOI_PREFIXES) or d in ("", "none", "null")


def is_sandbox_doi(doi) -> bool:
    """sandbox.zenodo.org test DOI (prefix 10.5072) -- not permanent."""
    return str(doi or "").strip().lower().startswith("10.5072/")


def load_front_matter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        raise ValueError(f"no front matter in {path}")
    return yaml.safe_load(m.group(1)) or {}


def content_hash(article_path: pathlib.Path) -> str:
    """Hash of article + its schema (not PDF — DOI-in-PDF must not affect the hash)."""
    h = hashlib.sha256()
    h.update(article_path.read_bytes())
    fm = load_front_matter(article_path)
    schema_name = fm.get("schema")
    if schema_name:
        schema_path = ROOT / "_data" / f"{schema_name}.yml"
        if schema_path.is_file():
            h.update(schema_path.read_bytes())
    return h.hexdigest()


def load_ledger() -> dict:
    if LEDGER_PATH.is_file():
        return yaml.safe_load(LEDGER_PATH.read_text()) or {}
    return {}


def save_ledger(ledger: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(
        yaml.dump(ledger, default_flow_style=False, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )


def api_base() -> str:
    if os.environ.get("ZENODO_LIVE", "").strip() == "1":
        return "https://zenodo.org/api"
    return "https://sandbox.zenodo.org/api"


def token() -> str:
    t = os.environ.get("ZENODO_TOKEN", "").strip()
    if not t:
        sys.exit(
            "ZENODO_TOKEN is not set. Create a token with deposit:write and "
            "deposit:actions scopes (sandbox or production)."
        )
    return t


def build_metadata(fm: dict, slug: str) -> dict:
    authors = fm.get("authors") or []
    creators = []
    for a in authors:
        name = f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
        entry: dict = {"name": name or "Unknown"}
        if a.get("affiliation"):
            entry["affiliation"] = a["affiliation"]
        if a.get("orcid"):
            entry["orcid"] = str(a["orcid"]).replace("https://orcid.org/", "")
        creators.append(entry)

    if not creators:
        name = os.environ.get("ZENODO_CREATOR_NAME", "Ch'ng, Ewe Seng")
        entry = {"name": name}
        orcid = os.environ.get("ZENODO_CREATOR_ORCID")
        if orcid:
            entry["orcid"] = orcid.replace("https://orcid.org/", "")
        creators = [entry]

    title = fm.get("title") or slug
    parts = []
    if fm.get("the_issue"):
        parts.append(str(fm["the_issue"]).strip())
    if fm.get("bottom_line"):
        parts.append("Bottom line: " + str(fm["bottom_line"]).strip())
    description = "\n\n".join(parts) or f"Article: {title}"

    keywords = fm.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]

    pub_date = str(fm.get("published_date") or datetime.now(timezone.utc).date())
    licence = (fm.get("licence") or "cc-by-4.0").lower().replace(" ", "-")

    meta = {
        "title": title,
        "upload_type": "publication",
        "publication_type": "article",
        "description": description[:20000],
        "creators": creators,
        "access_right": "open",
        "license": licence,
        "publication_date": pub_date,
        "keywords": keywords[:20] if keywords else ["microjournal", "practice update"],
    }

    site_url = os.environ.get("SITE_URL", "").rstrip("/")
    if site_url:
        meta["related_identifiers"] = [{
            "identifier": f"{site_url}/articles/{slug}/",
            "relation": "isIdenticalTo",
            "resource_type": "publication-article",
            "scheme": "url",
        }]
    return meta


def create_deposition_with_reserved_doi(access_token: str, metadata: dict) -> dict:
    """
    Create a draft deposition, attach metadata, prereserve a DOI.
    Returns {id, bucket, doi, links, ...}.
    """
    base = api_base()
    params = {"access_token": access_token}
    headers = {"Content-Type": "application/json"}

    r = requests.post(
        f"{base}/deposit/depositions",
        params=params,
        json={},
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()
    dep = r.json()
    dep_id = dep["id"]
    bucket = dep["links"]["bucket"]

    payload = {"metadata": {**metadata, "prereserve_doi": True}}
    r = requests.put(
        f"{base}/deposit/depositions/{dep_id}",
        params=params,
        json=payload,
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()
    dep = r.json()

    pre = (dep.get("metadata") or {}).get("prereserve_doi") or {}
    doi = pre.get("doi") or dep.get("doi")
    if not doi:
        raise RuntimeError(
            f"Zenodo did not return a prereserved DOI for deposition {dep_id}: {dep}"
        )

    return {
        "id": dep_id,
        "bucket": bucket,
        "doi": doi,
        "links": dep.get("links", {}),
        "raw": dep,
    }


def upload_and_publish(dep_id: int, bucket: str, pdf_path: pathlib.Path, access_token: str) -> dict:
    base = api_base()
    params = {"access_token": access_token}

    with open(pdf_path, "rb") as fp:
        r = requests.put(
            f"{bucket}/{pdf_path.name}",
            data=fp,
            params=params,
            timeout=180,
        )
    r.raise_for_status()

    r = requests.post(
        f"{base}/deposit/depositions/{dep_id}/actions/publish",
        params=params,
        timeout=60,
    )
    r.raise_for_status()
    published = r.json()
    doi = published.get("doi") or (published.get("metadata") or {}).get("doi")
    concept = published.get("conceptdoi")
    return {
        "doi": doi,
        "concept_doi": concept,
        "deposition_id": published.get("id") or dep_id,
        "links": published.get("links", {}),
        "raw": published,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reserve Zenodo DOI → embed in PDF → upload → publish"
    )
    parser.add_argument(
        "pdf_dir",
        type=pathlib.Path,
        help="Output directory for final PDFs (with real DOIs embedded)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-deposit even if content unchanged")
    parser.add_argument("--clear-sandbox", action="store_true",
                        help="Remove every sandbox test DOI from the ledger, then exit")
    args = parser.parse_args()

    if args.clear_sandbox:
        ledger = load_ledger()
        test = [slug for slug, e in ledger.items() if is_sandbox_doi((e or {}).get("doi"))]
        for slug in test:
            del ledger[slug]
            print(f"CLEAR {slug}: removed sandbox test DOI")
        if test:
            save_ledger(ledger)
        print(f"\nRemoved {len(test)} sandbox test DOI(s); real DOIs untouched.")
        return 0

    out_dir: pathlib.Path = args.pdf_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    ledger = load_ledger()
    articles = find_articles(ROOT)
    deposited, reused, failed = [], [], []

    access_token = None if args.dry_run else token()
    live = os.environ.get("ZENODO_LIVE", "").strip() == "1"
    target = "zenodo.org (LIVE)" if live else "sandbox.zenodo.org"
    print(f"Target: {target}")
    print(f"Articles: {len(articles)}  |  PDF out: {out_dir}")
    print()

    for article_path in articles:
        slug = article_path.stem
        pdf_path = out_dir / f"{slug}.pdf"
        fm = load_front_matter(article_path)
        if str(fm.get("status") or "").strip().lower() != "published":
            print(f"SKIP  {slug}: status is not 'published' -- no DOI minted")
            continue
        h = content_hash(article_path)
        entry = ledger.get(slug) or {}
        existing_doi = entry.get("doi")

        # Path A: unchanged + real DOI in ledger → re-render PDF with that DOI
        # A sandbox DOI never counts as done for a live run (and a real DOI is
        # never replaced by a sandbox test).
        same_kind = bool(existing_doi) and (is_sandbox_doi(existing_doi) == (not live))
        if not live and existing_doi and not is_placeholder_doi(existing_doi) and not is_sandbox_doi(existing_doi):
            print(f"KEEP  {slug}: already has real DOI {existing_doi} -- sandbox test skipped")
            reused.append(slug)
            continue
        if (
            not args.force
            and entry.get("content_hash") == h
            and existing_doi
            and not is_placeholder_doi(existing_doi)
            and same_kind
        ):
            try:
                render_pdf_build(str(article_path), str(pdf_path), doi_override=existing_doi)
                print(f"OK    {slug}: reused doi={existing_doi} (PDF re-embedded)")
                reused.append(slug)
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL  {slug}: re-render failed: {exc}", file=sys.stderr)
                failed.append((slug, str(exc)))
            continue

        metadata = build_metadata(fm, slug)

        if args.dry_run:
            print(f"WOULD {slug}: prereserve DOI → render PDF → upload → publish")
            print(f"       title={metadata['title'][:60]!r}")
            fake = f"10.5281/zenodo.DRYRUN-{slug[:8]}"
            render_pdf_build(str(article_path), str(pdf_path), doi_override=fake)
            deposited.append(slug)
            continue

        try:
            dep = create_deposition_with_reserved_doi(access_token, metadata)
            reserved_doi = dep["doi"]
            print(f"RESERVE {slug}: {reserved_doi} (deposition {dep['id']})")

            # Embed reserved DOI in the PDF *before* upload
            render_pdf_build(str(article_path), str(pdf_path), doi_override=reserved_doi)

            published = upload_and_publish(
                dep["id"], dep["bucket"], pdf_path, access_token
            )
            final_doi = published["doi"] or reserved_doi

            ledger[slug] = {
                "doi": final_doi,
                "concept_doi": published.get("concept_doi"),
                "deposition_id": published["deposition_id"],
                "content_hash": h,
                "deposited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "title": metadata["title"],
                "sandbox": not live,
            }
            print(f"DONE  {slug}: {final_doi} (PDF embeds this DOI)")
            deposited.append(slug)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  {slug}: {exc}", file=sys.stderr)
            failed.append((slug, str(exc)))

    if not live and deposited:
        print("\nSandbox run: test DOIs (10.5072/…) are written to the ledger and shown on the site,")
        print("labelled as test DOIs. A later live run replaces them with real DOIs;")
        print("the 'clear-test-dois' mode removes them.")
    if not args.dry_run and deposited:
        save_ledger(ledger)
        print(f"\nLedger written to {LEDGER_PATH.relative_to(ROOT)}")

    print(
        f"\nDeposited {len(deposited)}, reused {len(reused)}, failed {len(failed)} "
        f"(of {len(articles)})."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
