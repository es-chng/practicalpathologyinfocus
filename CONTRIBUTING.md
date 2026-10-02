# Contributing to Scholar Schema

Thank you for your interest.

## Ways to contribute

- **New article schemas** — design a new type and open a pull request, or share it as an example.
- **Bug reports** — open an issue with the steps to reproduce and the relevant log from the GitHub Actions run.
- **Documentation** — improvements to the README or clearer examples are always welcome.
- **Forks** — you are free to fork the system for your own microjournal. A link back is appreciated but not required.

## Design principles to keep

1. Schema remains the single source of truth.
2. The browser form and the CI validation stay in sync.
3. Published schemas stay locked; evolution happens through versioned schema files.
4. The system stays small and static (no new server-side services).

## Development notes

- Run `python scripts/validate.py` before committing schema or article changes.
- The authoring form is pure client-side JavaScript (`assets/js/author.js`).
- PDF generation uses WeasyPrint; keep `assets/css/pdf.css` and the renderer scripts in step.

Please open an issue first for larger changes so we can discuss direction.
