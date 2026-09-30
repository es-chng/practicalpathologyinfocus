#!/usr/bin/env python3
"""
Schema-aware validator for the new structure-based schema format.

A schema declares:
  version: N
  structure: [block_name, ...]        # ordered list of top-level blocks
  blocks:
    block_name:
      shape: text|list|table|boolean|date|repeat|block
      ... shape-specific fields ...

This validator walks the schema, then walks each article's front matter,
and checks that every block and sub-block matches its declared shape.

Checks:
  - schema files are well-formed (version, structure, blocks)
  - every block named in structure exists in blocks
  - every repeat/block has the right sub-field container
  - exactly one top-level block has teaser: true
  - every article's front matter has all non-optional blocks
  - each block's value matches its declared shape
  - repeat blocks are lists of objects; each object is recursively validated
  - block blocks are dicts; each named sub-field is recursively validated
  - max_chars on text fields is respected
  - published articles pin their schema by content hash (immutability)

Usage:
  python scripts/validate_schema.py
  python scripts/validate_schema.py --update-locks
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys
from datetime import date, datetime, timezone

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)
LOCKS_PATH = ROOT / "_data" / "schema-locks.yml"
LEDGER_PATH = ROOT / "_data" / "zenodo-ledger.yml"

PLACEHOLDER_DOI_PREFIXES = (
    "10.5281/zenodo.000",
    "10.5281/zenodo.xxxx",
    "doi:pending",
    "pending",
)

MARKDOWN_EXTS = {".md", ".markdown", ".mkdown", ".mkdn", ".mkd"}

LEAF_SHAPES = {"text", "list", "table", "boolean", "date"}
COMPOSITE_SHAPES = {"repeat", "block"}
VALID_SHAPES = LEAF_SHAPES | COMPOSITE_SHAPES


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def find_articles(root: pathlib.Path):
    folder = root / "_articles"
    found, ignored = [], []
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        (found if p.suffix.lower() in MARKDOWN_EXTS else ignored).append(p)
    for p in ignored:
        print(f"WARNING  {p.relative_to(root)} is in _articles/ but is not a Markdown file")
    return found


def load_front_matter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        raise ValueError("no front matter block found")
    return yaml.safe_load(m.group(1)) or {}


def file_hash(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_placeholder_doi(doi) -> bool:
    if not doi:
        return True
    d = str(doi).strip().lower()
    return any(d.startswith(p) for p in PLACEHOLDER_DOI_PREFIXES) or d in ("", "none", "null")


def is_published(fm: dict, slug: str, ledger: dict) -> bool:
    status = str(fm.get("status") or "").strip().lower()
    if status in ("draft", "unpublished", "test"):
        return False
    if status in ("published", "live"):
        return True
    if fm.get("doi") and not is_placeholder_doi(fm.get("doi")):
        return True
    entry = ledger.get(slug) or {}
    if entry.get("doi") and not is_placeholder_doi(entry.get("doi")):
        return True
    return False


# ---------------------------------------------------------------------------
# Schema-level validation
# ---------------------------------------------------------------------------

def check_schema_file(schema: dict, name: str) -> list[str]:
    """Validate one schema file's internal consistency."""
    errors: list[str] = []

    if "version" not in schema:
        errors.append(f"schema '{name}' is missing a top-level 'version'")

    structure = schema.get("structure")
    if not isinstance(structure, list) or not structure:
        errors.append(f"schema '{name}' must declare a non-empty 'structure' list")
        return errors  # cannot continue meaningfully

    blocks = schema.get("blocks")
    if not isinstance(blocks, dict) or not blocks:
        errors.append(f"schema '{name}' must declare a non-empty 'blocks' mapping")
        return errors

    for block_name in structure:
        if block_name not in blocks:
            errors.append(
                f"schema '{name}': structure names block '{block_name}' "
                f"but it is not defined in blocks"
            )

    for block_name, block in blocks.items():
        errors.extend(check_block(name, block_name, block))

    teasers = [b for b in blocks.values() if b.get("teaser")]
    if len(teasers) != 1:
        errors.append(
            f"schema '{name}' must have exactly one block with teaser: true, "
            f"found {len(teasers)}"
        )

    return errors


def check_block(schema_name: str, path: str, block: dict) -> list[str]:
    """Validate one block definition, recursing into repeat/block children."""
    errors: list[str] = []
    shape = block.get("shape")
    if shape not in VALID_SHAPES:
        errors.append(
            f"schema '{schema_name}': block '{path}' has unknown shape '{shape}'"
        )
        return errors

    if shape == "repeat":
        item = block.get("item")
        if not isinstance(item, dict) or not item:
            errors.append(
                f"schema '{schema_name}': repeat block '{path}' must declare a non-empty 'item'"
            )
            return errors
        for sub_name, sub_block in item.items():
            errors.extend(check_block(schema_name, f"{path}.{sub_name}", sub_block))

    elif shape == "block":
        fields = block.get("fields")
        if not isinstance(fields, dict) or not fields:
            errors.append(
                f"schema '{schema_name}': block '{path}' must declare a non-empty 'fields'"
            )
            return errors
        for sub_name, sub_block in fields.items():
            errors.extend(check_block(schema_name, f"{path}.{sub_name}", sub_block))

    elif shape == "table":
        columns = block.get("columns")
        if columns != "dynamic" and (not isinstance(columns, list) or not columns):
            errors.append(
                f"schema '{schema_name}': table block '{path}' must declare "
                f"'columns' as a non-empty list or the string 'dynamic'"
            )

    elif shape == "list":
        item = block.get("item")
        if item is not None:
            errors.extend(check_block(schema_name, f"{path}[item]", item))

    return errors


# ---------------------------------------------------------------------------
# Article-level validation
# ---------------------------------------------------------------------------

def check_article(path: pathlib.Path, schemas: dict, ledger: dict) -> list[str]:
    try:
        fm = load_front_matter(path)
    except Exception as e:
        return [f"cannot read front matter: {e}"]

    schema_name = fm.get("schema")
    if not schema_name:
        return []  # base-only article is valid

    schema = schemas.get(schema_name)
    if not schema:
        return [f"references unknown schema '{schema_name}'"]

    errors: list[str] = []

    # Optional schema_version pin
    schema_ver = schema.get("version")
    article_ver = fm.get("schema_version")
    if article_ver is not None and schema_ver is not None and article_ver != schema_ver:
        errors.append(
            f"schema_version {article_ver!r} does not match schema '{schema_name}' "
            f"version {schema_ver!r}"
        )

    structure = schema.get("structure") or []
    blocks = schema.get("blocks") or {}

    for block_name in structure:
        block = blocks.get(block_name)
        if block is None:
            continue  # already reported at schema level
        value = fm.get(block_name)
        errors.extend(
            check_value(schema_name, block_name, block, value, path=fm)
        )

    return errors


def check_value(schema_name: str, path: str, block: dict, value, fm: dict) -> list[str]:
    """Recursively check one value against its block definition."""
    errors: list[str] = []
    shape = block.get("shape")
    optional = block.get("optional", False)

    empty = value is None or value == "" or value == []
    if empty:
        if not optional:
            label = block.get("label", path)
            errors.append(f"required block '{path}' ({label}) is missing or empty")
        return errors

    if shape == "text":
        if not isinstance(value, str):
            errors.append(f"'{path}' is declared text but value is not a string")
            return errors
        max_chars = block.get("max_chars")
        if max_chars and len(value) > max_chars:
            errors.append(
                f"'{path}' is {len(value)} characters, over its {max_chars}-character limit"
            )

    elif shape == "list":
        if not isinstance(value, list):
            errors.append(f"'{path}' is declared list but value is not a list")
            return errors
        item_def = block.get("item")
        if item_def is not None:
            for i, item in enumerate(value):
                errors.extend(check_value(schema_name, f"{path}[{i}]", item_def, item, fm))

    elif shape == "table":
        if not isinstance(value, list):
            errors.append(f"'{path}' is declared table but value is not a list")
            return errors
        # Two allowed table shapes:
        #   (a) list of dicts, each with the declared columns
        #   (b) list of {label, columns, rows} wrapper objects (dynamic columns)
        columns = block.get("columns")
        if columns == "dynamic":
            for i, t in enumerate(value):
                if not isinstance(t, dict):
                    errors.append(f"'{path}[{i}]' must be an object with label/columns/rows")
                    continue
                for key in ("label", "columns", "rows"):
                    if key not in t:
                        errors.append(f"'{path}[{i}]' is missing '{key}'")
                if isinstance(t.get("columns"), list) and isinstance(t.get("rows"), list):
                    for j, row in enumerate(t["rows"]):
                        if not isinstance(row, dict):
                            errors.append(f"'{path}[{i}].rows[{j}]' must be an object")
                            continue
                        missing = [c for c in t["columns"] if c not in row]
                        if missing:
                            errors.append(
                                f"'{path}[{i}].rows[{j}]' missing column(s): "
                                f"{', '.join(missing)}"
                            )
        else:
            for i, row in enumerate(value):
                if not isinstance(row, dict):
                    errors.append(f"'{path}[{i}]' must be an object")
                    continue
                missing = [c for c in (columns or []) if c not in row]
                if missing:
                    errors.append(
                        f"'{path}[{i}]' missing column(s): {', '.join(missing)}"
                    )

    elif shape == "boolean":
        if not isinstance(value, bool):
            errors.append(f"'{path}' is declared boolean but value is not true/false")

    elif shape == "date":
        if isinstance(value, (date, datetime)):
            pass
        elif isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value.strip()):
            pass
        else:
            errors.append(
                f"'{path}' is declared date but value is not a real date "
                f"(use unquoted YYYY-MM-DD or a quoted YYYY-MM-DD string)"
            )

    elif shape == "repeat":
        if not isinstance(value, list):
            errors.append(f"'{path}' is declared repeat but value is not a list")
            return errors
        item_def = block.get("item") or {}
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                errors.append(f"'{path}[{i}]' must be an object")
                continue
            for sub_name, sub_block in item_def.items():
                errors.extend(
                    check_value(
                        schema_name,
                        f"{path}[{i}].{sub_name}",
                        sub_block,
                        item.get(sub_name),
                        fm,
                    )
                )

    elif shape == "block":
        if not isinstance(value, dict):
            errors.append(f"'{path}' is declared block but value is not an object")
            return errors
        for sub_name, sub_block in (block.get("fields") or {}).items():
            errors.extend(
                check_value(
                    schema_name,
                    f"{path}.{sub_name}",
                    sub_block,
                    value.get(sub_name),
                    fm,
                )
            )

    return errors


# ---------------------------------------------------------------------------
# Locks / ledger
# ---------------------------------------------------------------------------

def load_locks() -> dict:
    if LOCKS_PATH.is_file():
        data = yaml.safe_load(LOCKS_PATH.read_text()) or {}
        return data if isinstance(data, dict) else {}
    return {}


def load_ledger() -> dict:
    if LEDGER_PATH.is_file():
        data = yaml.safe_load(LEDGER_PATH.read_text()) or {}
        return data if isinstance(data, dict) else {}
    return {}


def check_schema_locks(schema_paths, schemas, articles, ledger, locks, update_locks):
    errors: list[str] = []
    published_schemas: dict[str, list[str]] = {}
    for p in articles:
        try:
            fm = load_front_matter(p)
        except Exception:
            continue
        if not is_published(fm, p.stem, ledger):
            continue
        name = fm.get("schema")
        if name:
            published_schemas.setdefault(name, []).append(p.name)

    new_locks = dict(locks)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if update_locks:
        stale = [name for name in new_locks if name not in published_schemas]
        for name in stale:
            print(f"UNLOCK {name}: no published article uses it any longer")
            del new_locks[name]

    for schema_name, article_names in sorted(published_schemas.items()):
        path = schema_paths.get(schema_name)
        if not path or not path.is_file():
            errors.append(f"published articles reference missing schema file '{schema_name}'")
            continue
        current_hash = file_hash(path)
        schema = schemas.get(schema_name) or {}
        lock = locks.get(schema_name)

        if lock is None:
            new_locks[schema_name] = {
                "version": schema.get("version"),
                "content_hash": current_hash,
                "locked_at": now,
                "locked_by": sorted(article_names),
            }
            print(f"LOCK   {schema_name}: locked automatically (hash={current_hash[:12]}…)")
            continue

        locked_hash = lock.get("content_hash")
        if locked_hash and locked_hash != current_hash:
            errors.append(
                f"schema '{schema_name}' has changed since it was locked "
                f"(locked {str(locked_hash)[:12]}…, current {current_hash[:12]}…). "
                f"Create a new schema file for format changes, or restore the locked content. "
                f"Articles holding the lock: {', '.join(lock.get('locked_by') or article_names)}"
            )
        else:
            new_locks[schema_name] = {
                **lock,
                "version": schema.get("version"),
                "locked_by": sorted(article_names),
            }

    return errors, new_locks


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Validate articles against schemas")
    parser.add_argument("--update-locks", action="store_true")
    args = parser.parse_args()

    schema_paths: dict[str, pathlib.Path] = {}
    schemas: dict = {}
    for p in sorted((ROOT / "_data").glob("schema-*.yml")):
        if p.name == "schema-locks.yml":
            continue
        s = yaml.safe_load(p.read_text()) or {}
        schemas[p.stem] = s
        schema_paths[p.stem] = p

    ledger = load_ledger()
    locks = load_locks()
    articles = find_articles(ROOT)

    total_errors = 0

    for name, schema in schemas.items():
        for e in check_schema_file(schema, name):
            print(f"SCHEMA ERROR  {e}")
            total_errors += 1

    lock_errors, new_locks = check_schema_locks(
        schema_paths, schemas, articles, ledger, locks, args.update_locks
    )
    for e in lock_errors:
        print(f"LOCK ERROR  {e}")
        total_errors += 1

    if args.update_locks or new_locks != locks:
        LOCKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.dump(new_locks, default_flow_style=False, sort_keys=True, allow_unicode=True)
        LOCKS_PATH.write_text(
            "# Schema content locks — see scripts/validate_schema.py\n"
            "# Do not hand-edit hashes unless restoring a known-good state.\n"
            + body,
            encoding="utf-8",
        )
        print(f"Wrote {LOCKS_PATH.relative_to(ROOT)}")

    for p in articles:
        for e in check_article(p, schemas, ledger):
            print(f"ERROR  {p.name}: {e}")
            total_errors += 1

    print(
        f"\nChecked {len(articles)} article(s) against {len(schemas)} schema(s): "
        f"{total_errors} error(s)."
    )
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
