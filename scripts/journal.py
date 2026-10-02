"""Shared helpers: where things live and how they are read.

Every script imports from here, so a rule (which articles are published,
where a DOI comes from) is written once and cannot drift between scripts.
"""
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "_data"
LEDGER = DATA / "zenodo-ledger.yml"
FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)
DOI = re.compile(r"^10\.\d{4,9}/\S+$")


def front_matter(path):
    m = FRONT_MATTER.match(pathlib.Path(path).read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{path}: no front matter block")
    return yaml.safe_load(m.group(1)) or {}


def articles():
    """Every article file, sorted. Same rule as Jekyll: *.md in _articles/."""
    return sorted((ROOT / "_articles").glob("*.md"))


def issues():
    return sorted((ROOT / "_issues").glob("*.md"))


def schema_files():
    return {p.stem: p for p in sorted(DATA.glob("schema-*.yml")) if p.stem != "schema-locks"}


def schema(name):
    return yaml.safe_load((DATA / f"{name}.yml").read_text(encoding="utf-8")) or {}


def config():
    return yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8")) or {}


def is_published(fm):
    """The one rule, used by the site, the PDFs and Zenodo alike."""
    return fm.get("status") == "published"


def teaser_key(sch):
    return next((n for n in sch.get("structure", []) if sch["blocks"].get(n, {}).get("teaser")), None)


def ledger():
    return (yaml.safe_load(LEDGER.read_text(encoding="utf-8")) or {}) if LEDGER.is_file() else {}


def save_ledger(data):
    LEDGER.write_text("# slug -> DOI, written by scripts/zenodo.py. Do not edit by hand.\n"
                      + yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8")


def doi_for(path, fm=None):
    """The article's DOI: from the Zenodo ledger, else a `doi:` in its front matter."""
    path = pathlib.Path(path)
    doi = (ledger().get(path.stem) or {}).get("doi") or (fm or front_matter(path)).get("doi")
    return str(doi) if doi and DOI.match(str(doi)) else None


def is_test_doi(doi):
    """sandbox.zenodo.org DOIs (prefix 10.5072) are tests, not permanent."""
    return str(doi or "").startswith("10.5072/")
