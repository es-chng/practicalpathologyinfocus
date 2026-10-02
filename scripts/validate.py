#!/usr/bin/env python3
"""Check every article, issue and schema before anything is built.

  - schemas: valid shapes and styles, sub-block order lists, one teaser block
  - images: each file exists in assets/images/, in a web format, with a caption
  - articles: the base fields, then every block against its article schema
  - numbering: no two articles share volume/issue/order
  - issues: every published article belongs to an existing issue
  - locks: a schema used by a published article never changes
    (its SHA-256 is recorded in _data/schema-locks.yml on first publication)

Usage: python scripts/validate.py         exit status 1 if anything is wrong
"""
import datetime as dt
import hashlib
import sys

import yaml

from journal import DATA, DOI, ROOT, articles, front_matter, is_published, issues, schema_files

LOCKS = DATA / "schema-locks.yml"
SHAPES = {"text", "list", "table", "boolean", "date", "repeat", "block", "image"}
STYLES = {"plain", "boxed", "opinion", "badge", "numbered", "section", "table", "wide"}
IMAGES = ROOT / "assets" / "images"
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
STATUSES = {"published", "draft"}
# base fields every article has; a schema block may not reuse these names
RESERVED = {"title", "authors", "corresponding_email", "volume", "issue", "order", "published_date",
            "licence", "schema", "status", "keywords", "doi"}


def is_date(v):
    return isinstance(v, dt.date)


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


# --------------------------------------------------------------- schemas ---

def check_block(where, b):
    if not isinstance(b, dict):
        return [f"{where}: must be a mapping"]
    errs = []
    shape, style = b.get("shape"), b.get("style")
    if shape not in SHAPES:
        errs.append(f"{where}: unknown shape {shape!r}")
    if style and style not in STYLES:
        errs.append(f"{where}: unknown style {style!r}")
    for kind, order_key in (("item", "item_order"), ("fields", "field_order")):
        if shape != ("repeat" if kind == "item" else "block"):
            continue
        subs = b.get(kind)
        if not isinstance(subs, dict) or not subs:
            errs.append(f"{where}: a {shape} block needs a non-empty '{kind}'")
            continue
        # the web templates walk this list, so it must name every sub-block
        if sorted(b.get(order_key) or []) != sorted(subs):
            errs.append(f"{where}: '{order_key}' must list exactly: {', '.join(subs)}")
        for n, sub in subs.items():
            errs += check_block(f"{where}.{n}", sub)
    if shape == "table" and not (b.get("columns") == "dynamic"
                                 or (isinstance(b.get("columns"), list) and b["columns"])):
        errs.append(f"{where}: a table needs 'columns' (a list, or 'dynamic')")
    if shape == "list" and b.get("item") is not None:
        errs += check_block(f"{where}[item]", b["item"])
    return errs


def check_schema(name, s):
    structure, blocks = s.get("structure"), s.get("blocks")
    if not isinstance(structure, list) or not structure or not isinstance(blocks, dict):
        return [f"{name}: needs a 'structure' list and a 'blocks' mapping"]
    errs = [f"{name}: '{n}' is in structure but not in blocks" for n in structure if n not in blocks]
    errs += [f"{name}: '{n}' is a base field name; choose another name for this block"
             for n in blocks if n in RESERVED]
    errs += [f"{name}: '{n}' is defined in blocks but not listed in structure, so it never appears"
             for n in blocks if n not in structure]
    for n, b in blocks.items():
        errs += check_block(f"{name}.{n}", b)
    teasers = [n for n in structure if isinstance(blocks.get(n), dict) and blocks[n].get("teaser")]
    if len(teasers) != 1:
        errs.append(f"{name}: exactly one block needs 'teaser: true' (found {len(teasers)})")
    return errs


# --------------------------------------------------------------- values ----

def check_value(where, b, v):
    if v is None or v == "" or v == [] or v == {}:
        return [] if b.get("optional") else [f"'{where}' is required but missing or empty"]
    shape = b.get("shape")
    if shape == "text":
        if not isinstance(v, str):
            return [f"'{where}' must be text"]
        if b.get("max_chars") and len(v) > b["max_chars"]:
            return [f"'{where}' is {len(v)} characters, over its limit of {b['max_chars']}"]
    elif shape == "list":
        if not isinstance(v, list):
            return [f"'{where}' must be a list"]
        if b.get("item"):
            return [e for i, x in enumerate(v) for e in check_value(f"{where}[{i}]", b["item"], x)]
    elif shape == "table":
        if b.get("columns") == "dynamic":
            if not (isinstance(v, dict) and isinstance(v.get("columns"), list) and v["columns"]
                    and isinstance(v.get("rows"), list)):
                return [f"'{where}' must have 'label', a non-empty 'columns' list and 'rows'"]
            cols, rows = v["columns"], v["rows"]
        else:
            cols, rows = b["columns"], v
            if not isinstance(rows, list):
                return [f"'{where}' must be a list of rows"]
        return [f"'{where}' row {j + 1} is missing column(s): {', '.join(map(str, miss))}"
                for j, r in enumerate(rows)
                for miss in [[c for c in cols if not isinstance(r, dict) or c not in r]] if miss]
    elif shape == "boolean" and not isinstance(v, bool):
        return [f"'{where}' must be true or false"]
    elif shape == "date" and not is_date(v):
        return [f"'{where}' must be a date written without quotes, like 2026-09-28"]
    elif shape == "repeat":
        if not isinstance(v, list):
            return [f"'{where}' must be a list"]
        errs = []
        for i, x in enumerate(v, 1):
            if not isinstance(x, dict):
                errs.append(f"'{where}[{i}]' must be a mapping")
                continue
            for n, sub in b["item"].items():
                errs += check_value(f"{where}[{i}].{n}", sub, x.get(n))
        return errs
    elif shape == "image":
        if not isinstance(v, dict) or not isinstance(v.get("file"), str):
            return [f"'{where}' needs 'file' (a path inside assets/images/) and 'caption'"]
        errs = []
        path = (IMAGES / v["file"]).resolve()
        if not path.is_relative_to(IMAGES.resolve()):
            errs.append(f"'{where}' file {v['file']!r} must stay inside assets/images/")
        elif path.suffix.lower() not in IMAGE_TYPES:
            errs.append(f"'{where}' file {v['file']!r} must be one of: {', '.join(sorted(IMAGE_TYPES))}")
        elif not path.is_file():
            errs.append(f"'{where}' file {v['file']!r} is not in assets/images/")
        if not isinstance(v.get("caption"), str) or not v["caption"].strip():
            errs.append(f"'{where}' needs a 'caption'")
        return errs
    elif shape == "block":
        if not isinstance(v, dict):
            return [f"'{where}' must be a mapping"]
        return [e for n, sub in b["fields"].items() for e in check_value(f"{where}.{n}", sub, v.get(n))]
    return []


def check_base(fm, schemas):
    errs = []
    if not isinstance(fm.get("title"), str) or not fm["title"].strip():
        errs.append("'title' is required")
    authors = fm.get("authors")
    if not isinstance(authors, list) or not authors or not all(
            isinstance(a, dict) and a.get("given") and a.get("family") for a in authors):
        errs.append("'authors' must list at least one author, each with 'given' and 'family'")
    for k in ("volume", "issue", "order"):
        if not is_int(fm.get(k)):
            errs.append(f"'{k}' must be a whole number (1, 2, ...)")
    if not is_date(fm.get("published_date")):
        errs.append("'published_date' must be a date written without quotes, like 2026-09-28")
    if not fm.get("licence"):
        errs.append("'licence' is required")
    if fm.get("status") not in STATUSES:
        errs.append(f"'status' must be 'published' or 'draft', not {fm.get('status')!r}")
    if fm.get("schema") not in schemas:
        errs.append(f"'schema' must name a file in _data/ (found {fm.get('schema')!r})")
    if fm.get("doi") is not None and not DOI.match(str(fm["doi"])):
        errs.append(f"'doi' {fm['doi']!r} is not a DOI like 10.5281/zenodo.123")
    return errs


# --------------------------------------------------------------- locks -----

def check_locks(published, files):
    """published: {schema name: [article file names]}. Returns errors."""
    locks = (yaml.safe_load(LOCKS.read_text(encoding="utf-8")) or {}) if LOCKS.is_file() else {}
    errs, added = [], False
    for name, users in sorted(published.items()):
        digest = hashlib.sha256(files[name].read_bytes()).hexdigest()
        if name not in locks:
            locks[name] = {"content_hash": digest,
                           "locked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "locked_by": sorted(users)}
            added = True
            print(f"LOCK   {name}: now used by a published article, so it can no longer change")
        elif locks[name].get("content_hash") != digest:
            errs.append(f"{name}: changed after publication. Restore it, or copy it to a new "
                        f"schema file for new articles (used by {', '.join(sorted(users))})")
    if added:
        LOCKS.write_text("# Written by scripts/validate.py when a schema is first published. "
                         "Do not edit.\n" + yaml.safe_dump(locks, sort_keys=True), encoding="utf-8")
    return errs


# --------------------------------------------------------------- main ------

def main():
    files = schema_files()
    schemas = {n: yaml.safe_load(p.read_text(encoding="utf-8")) or {} for n, p in files.items()}
    report = []  # (file, message)

    for n, s in schemas.items():
        report += [(f"_data/{n}.yml", e) for e in check_schema(n, s)]
    valid_schemas = {n: s for n, s in schemas.items() if not check_schema(n, s)}

    known_issues = set()
    for p in issues():
        fm = front_matter(p)
        if not (is_int(fm.get("volume")) and is_int(fm.get("issue")) and is_date(fm.get("published_date"))):
            report.append((p.name, "needs whole-number 'volume' and 'issue' and an unquoted 'published_date'"))
        known_issues.add((fm.get("volume"), fm.get("issue")))

    numbers, published = {}, {}
    for p in articles():
        try:
            fm = front_matter(p)
        except Exception as e:  # noqa: BLE001 -- report and carry on
            report.append((p.name, f"cannot read front matter: {e}"))
            continue
        errs = check_base(fm, schemas)
        s = valid_schemas.get(fm.get("schema"))
        if s:
            for n in s["structure"]:
                errs += check_value(n, s["blocks"][n], fm.get(n))
        key = (fm.get("volume"), fm.get("issue"), fm.get("order"))
        if key in numbers:
            errs.append(f"volume {key[0]}, issue {key[1]}, order {key[2]} is already used by {numbers[key]}")
        numbers.setdefault(key, p.name)
        if is_published(fm):
            if key[:2] not in known_issues:
                errs.append(f"is published in volume {key[0]}, issue {key[1]}, "
                            f"but _issues/ has no such issue")
            if fm.get("schema") in files:
                published.setdefault(fm["schema"], []).append(p.name)
        report += [(p.name, e) for e in errs]

    report += [("_data/schema-locks.yml", e) for e in check_locks(published, files)]

    for where, msg in report:
        print(f"ERROR  {where}: {msg}")
    print(f"Checked {len(articles())} article(s), {len(issues())} issue(s), "
          f"{len(schemas)} schema(s): {len(report)} error(s).")
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
