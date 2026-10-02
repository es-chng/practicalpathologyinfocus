# Scholar Schema

**A schema-driven publishing system for short academic writing**  
Microjournals • journal-club records • teaching reviews • diagnostic formats • practice updates

One text file per article. On every commit the system validates the file against its article schema, then publishes it as a web page, a typeset PDF, and an entry in its issue on GitHub Pages. An optional second workflow gives each article its own DOI through Zenodo.

Live demonstration: [Practical Pathology in Focus](https://es-chng.github.io/practicalpathologyinfocus/)

---

## Why this exists

Most academic publishing tools are either full journal platforms (heavy, multi-user, expensive to run) or simple static blogs (too unstructured for scholarly work). Scholar Schema sits in between:

- **Schema is the single source of truth.** Article structure, validation rules, the browser form, the web templates and the PDF layout all read the same YAML schema files. Add a new article type by writing one new schema.
- **Client-side authoring form.** The form at `/author/` is generated from the schemas. It enforces required fields and character limits, can load existing articles for editing, and never sends data anywhere.
- **Schema locking for permanence.** When the first article that uses a schema is published, the build records a fingerprint. Later changes to that schema are rejected so published articles keep the structure they were published under. New versions are explicit (`schema-name-v2`).
- **Unified web + PDF.** The same schema-driven HTML is used for the website and for WeasyPrint. Page counts are measured from the real PDF and injected back into the site.
- **Zero ongoing server cost.** Pure static site + GitHub Actions + optional Zenodo. Everything lives in Git.

Designed for single authors and small teams who want structured, citable, permanent short-form scholarly output without running a full journal platform.

---

## Quick start (new publication)

1. Create a new GitHub repository and upload the contents of this package (or fork it).
2. **Settings → Pages → Source:** GitHub Actions.
3. **Settings → Actions → General → Workflow permissions:** Read and write permissions.
4. Edit `_config.yml`:
   ```yaml
   title: "Your Microjournal Title"
   tagline: "A short description"
   description: >-
     One or two sentences about the publication.
   url: "https://USERNAME.github.io"
   baseurl: "/REPOSITORY"   # "" if the repository is USERNAME.github.io
   ```
5. Rewrite `about.md`.
6. (Optional) Delete or replace the example articles in `_articles/` and the issue in `_issues/`.
7. Commit to `main`. The site rebuilds in a few minutes.

Every subsequent commit to `main` validates, builds the site, generates new/changed PDFs, and publishes to GitHub Pages.

---

## Core concepts

| Term | Meaning |
| --- | --- |
| **Base schema** | Fields every article has (title, authors, volume, issue, licence, status, …) |
| **Article schema** | Type-specific fields and their order (`_data/schema-NAME.yml`) |
| **Block** | One field in an article schema (question, sections, references, …) |
| **Shape** | The data type of a block: `text`, `list`, `table`, `repeat`, `block`, `image`, `boolean`, `date` |
| **Style** | How a block is rendered: `plain`, `boxed`, `badge`, `section`, `opinion`, `numbered`, `wide` … |
| **Teaser** | Exactly one block per schema marked `teaser: true` — used in listings |

### Shapes and styles

**Shapes**

| Shape | Value | Notes |
| --- | --- | --- |
| `text` | String | Supports limited Markdown (paragraphs, **bold**, *italic*, lists, pipe tables, `##` subheadings) |
| `list` | List | May declare an `item` shape |
| `table` | Rows of named columns | `columns: [a, b]` or `columns: dynamic` |
| `boolean` | `true` / `false` | |
| `date` | `YYYY-MM-DD` | |
| `repeat` | List of groups | Sub-blocks under `item`, order in `item_order` |
| `block` | One group | Sub-blocks under `fields`, order in `field_order` |
| `image` | One figure | `file`, `caption`, optional `alt` and `credit` |

**Styles:** `plain` (default), `boxed`, `opinion`, `badge`, `numbered`, `section`, `table`, `wide` (full-width figures in the PDF).

Other attributes: `label`, `optional: true`, `max_chars`, `teaser: true`.

### Example article schema (abbreviated)

```yaml
version: 2
article_type: educational_review

structure:
  - article_type
  - question
  - background
  - sections
  - bottom_line
  - references

blocks:
  article_type:
    shape: text
    style: badge
    optional: true

  question:
    shape: text
    style: boxed
    label: "Core Question"

  background:
    shape: text
    label: "Background"

  sections:
    shape: repeat
    item_order: [heading, question, content, figures, tables, bottom_line]
    item:
      heading:  { shape: text, style: section }
      question: { shape: text, style: boxed, label: "Question", optional: true, max_chars: 250 }
      content:  { shape: text, optional: true }
      figures:
        shape: list
        optional: true
        item: { shape: image }
      tables:
        shape: list
        optional: true
        item: { shape: table, columns: dynamic }
      bottom_line: { shape: text, style: boxed, label: "Bottom line", optional: true, max_chars: 450 }

  bottom_line:
    shape: text
    style: boxed
    label: "Summary / Bottom Line"
    teaser: true
    max_chars: 450

  references:
    shape: list
    style: numbered
    label: "References"
    optional: true
```

A new article type needs only a new file in `_data/`. The form, validation, templates and PDF renderer pick it up automatically.

---

## Writing an article

Open the form on the published site at `/author/` (or the demonstration site).  
Choose an article type → fill the form → download the `.md` file → add it to `_articles/` on GitHub.

The form is built from the schemas, so it always matches them. It marks required sections, counts characters against limits, and can open an existing article file for editing. Everything runs in the browser.

You can also write the YAML front matter by hand; the structure is documented in the schema files themselves and in the longer notes that previously accompanied this package.

**Images:** place files in `assets/images/` (ideally one folder per article) and list them in the article. The build fails if a file is missing, is not a web image format, or has no caption. Figures are numbered automatically. In the PDF they appear two-up by default (`style: wide` for full width).

---

## Included article types (examples)

| Schema | Intended use | Teaser block |
| --- | --- | --- |
| `schema-educational-review-v2` | Topic reviewed in sections (with optional figures per section) | Summary / Bottom Line |
| `schema-educational-review` | Version 1 (kept for already-published articles) | Summary / Bottom Line |
| `schema-diagnostic-pitfall` | One mimic versus one true diagnosis | Bottom Line |
| `schema-case-report` | One instructive case (patient consent required) | Bottom Line |
| `schema-journal-club` | Appraisal of one published paper | Bottom Line for Practice |
| `schema-diagnostic-challenge` | Self-test case with answer | Teaching Point |
| `schema-practice-update` | Changed guideline or classification | Bottom Line |

These are examples. Copy and adapt them, or design new ones with the live schema designer on the `/author/` page.

---

## Workflow

One GitHub Actions workflow (“Publish”) does everything:

```
commit to main (or monthly scheduled run)
  1. Validate articles, issues and schemas; lock newly published schemas
  2. Build the Jekyll site
  3. Print new or changed articles to PDF, and each issue as one PDF
     (contents page + all its published articles); inject page counts
  4. Publish to GitHub Pages
```

Optional Zenodo path (sandbox → live) reserves a DOI, typesets the PDF with that DOI, deposits the record, and records the DOI so the next build shows it on the web page and in the PDF.

**Schema locks.** When the first article using a schema is published, its SHA-256 fingerprint is written to `_data/schema-locks.yml`. Any later change to a locked schema stops the build. To evolve an article type, copy the schema to a new versioned file.

---

## Adapting for your own publication

1. Change the values in `_config.yml` (title, tagline, description, premise text, url/baseurl).
2. Rewrite `about.md`.
3. Keep, delete or replace the example articles and issue.
4. Keep or replace the example schemas in `_data/`. Design new ones with the form’s schema preview.
5. Adjust colours and type scale in `assets/css/style.css` if desired (`--text-scale`, `--heading-scale`, the colour variables).
6. (Optional) Set up Zenodo tokens as repository secrets for DOIs.

The system is deliberately small. Most customisation is done by editing schemas and the two CSS files.

---

## Local preview (optional)

Requires Python 3.12+ and Ruby 3.x. On Linux, WeasyPrint needs Pango, HarfBuzz, GDK-PixBuf and a serif font (the GitHub Actions workflow installs these).

```bash
pip install -r requirements.txt
bundle install
python scripts/validate.py
bundle exec jekyll build --baseurl ""
python scripts/build_pdfs.py _site/files --baseurl "" --cache .pdf-cache
python -m http.server 4000 -d _site   # open http://localhost:4000
```

Serve the finished `_site` folder rather than using `jekyll serve` (which would rebuild and drop the PDFs/page counts).

---

## File map

| Path | Purpose |
| --- | --- |
| `_config.yml` | Publication name, homepage text, web address |
| `about.md` | About page |
| `_articles/` | Articles (one Markdown file each, front-matter only) |
| `_issues/` | Issues |
| `author/` + `assets/js/author.js` | Browser form generated from the schemas |
| `_data/schema-*.yml` | Article schemas |
| `_data/schema-locks.yml`, `zenodo-ledger.yml` | Written by the workflow — do not edit by hand |
| `_layouts/`, `_includes/` | Templates |
| `assets/css/style.css` | Web visual design |
| `assets/css/pdf.css` | Print layout for WeasyPrint |
| `scripts/` | Validation, PDF generation, Zenodo deposit |
| `.github/workflows/publish.yml` | The single workflow |

---

## Licence

The code is MIT-licensed (see `LICENSE`).  
Each article carries the licence stated in its own front matter (commonly CC BY 4.0).

---

## Citation

If you use or adapt Scholar Schema, please cite it:

```
Ch'ng, E. S. (2026). Scholar Schema: a structured academic publishing system (Version 1.0.0) [Computer software]. https://github.com/es-chng/scholar-schema
```

See `CITATION.cff` for machine-readable details.

---

## Design notes

- Everything public by design (including drafts). Never put private notes in article files.
- The form and the CI use the same validation rules.
- PDFs are cached; a PDF is rebuilt only when its inputs change.
- Page counts come from the actual PDF, not from an estimate.
- The visual design is intentionally restrained (serif, near-monochrome, tight measure) so the focus stays on the content.

Contributions, forks and new article-type schemas are welcome. See `CONTRIBUTING.md`.
