/* The article form, built from the article schemas (_data/schema-*.yml).
 *
 * Each schema shape has one input widget; the form for an article type is
 * those widgets in the schema's order. "Check" applies the schema's rules
 * (required blocks, character limits); the full check still runs on GitHub.
 * The result is the article file: YAML front matter, as in _articles/.
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- helpers
  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v);
    }
    e.append(...kids.flat().filter(Boolean));
    return e;
  }
  const human = (n) => n.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  const empty = (v) => v === undefined || v === null || v === "" ||
    (Array.isArray(v) && v.length === 0) ||
    (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0);
  const ymd = (v) => (v instanceof Date ? v.toISOString().slice(0, 10) : v ? String(v) : "");
  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
  const input = (value, attrs) => { const i = el("input", Object.assign({ type: "text" }, attrs)); i.value = value ?? ""; return i; };
  const labelled = (text, control) => el("label", { class: "af-sub" }, el("span", { text }), control);

  // A list of items with Add and Remove. make(value) returns a widget.
  function repeatable(make, values, addText, start) {
    const items = [], box = el("div", { class: "af-repeat" });
    const add = (v) => {
      const w = make(v);
      const row = el("div", { class: "af-item" }, w.el, el("button", {
        type: "button", class: "af-remove", text: "Remove",
        onclick: () => { row.remove(); items.splice(items.indexOf(w), 1); },
      }));
      items.push(w);
      box.insertBefore(row, addButton);
    };
    const addButton = el("button", { type: "button", class: "af-add", text: addText, onclick: () => add() });
    box.append(addButton);
    (values && values.length ? values : Array(start || 0).fill(undefined)).forEach(add);
    return {
      el: box,
      get: () => items.map((w) => w.get()).filter((v) => !empty(v)),
      check: (where) => items.flatMap((w, i) => (w.check ? w.check(`${where} ${i + 1}`) : [])),
    };
  }

  // Table rows: one text box per column, columns() gives the current names.
  function tableRows(columns, rows) {
    const box = el("div", { class: "af-table" });
    let list;
    const row = (cols) => (v) => {
      const cells = cols.map((c) => input(v && v[c] != null ? v[c] : "", { placeholder: human(c) }));
      return {
        el: el("div", { class: "af-row", style: `--cols:${cols.length}` }, cells),
        get: () => {
          const o = {};
          cols.forEach((c, k) => { o[c] = cells[k].value.trim(); });
          return Object.values(o).some(Boolean) ? o : undefined;
        },
      };
    };
    const draw = (values) => {
      const cols = columns();
      box.replaceChildren(el("div", { class: "af-row af-head", style: `--cols:${cols.length}` },
        cols.map((c) => el("span", { text: human(c) }))));
      list = repeatable(row(cols), values, "Add row", 1);
      box.append(list.el);
    };
    draw(rows);
    return { el: box, get: () => list.get(), redraw: () => draw(list.get()) };
  }

  // ---------------------------------------------------------------- widgets
  // One per schema shape. Each returns { el, get, check? }.
  function widget(def, value) {
    switch (def.shape) {
      case "text": {
        if (def.style === "section" || def.style === "badge") {  // short, one-line text
          const i = input(value, { class: "af-wide" });
          return { el: i, get: () => i.value.trim() };
        }
        const t = el("textarea", { rows: def.max_chars && def.max_chars <= 450 ? 3 : 7 });
        t.value = value ?? "";
        const count = def.max_chars ? el("span", { class: "af-count" }) : null;
        const update = () => {
          if (!count) return;
          count.textContent = `${t.value.trim().length} / ${def.max_chars}`;
          count.classList.toggle("af-over", t.value.trim().length > def.max_chars);
        };
        t.addEventListener("input", update);
        update();
        return { el: [t, count], get: () => t.value.trim() };
      }
      case "number": {
        const i = input(value, { type: "number", min: "1", step: "1" });
        return { el: i, get: () => (i.value === "" ? undefined : parseInt(i.value, 10)) };
      }
      case "choice": {
        const s = el("select", {}, def.options.map((o) => el("option", { value: o, text: o })));
        s.value = value ?? def.options[0];
        return { el: s, get: () => s.value };
      }
      case "boolean": {
        const s = el("select", {}, ["", "Yes", "No"].map((o) => el("option", { value: o, text: o || "—" })));
        s.value = value === true ? "Yes" : value === false ? "No" : "";
        return { el: s, get: () => (s.value === "Yes" ? true : s.value === "No" ? false : undefined) };
      }
      case "date": {
        const i = input(ymd(value), { type: "date" });
        return { el: i, get: () => i.value };
      }
      case "image": {
        const v = value || {};
        const f = {
          file: input(v.file, { placeholder: "my-article/figure-1.jpg (a file in assets/images/)" }),
          caption: input(v.caption, { placeholder: "What it shows, with stain and magnification" }),
          alt: input(v.alt, { placeholder: "Short description for screen readers" }),
          credit: input(v.credit, { placeholder: "Source, if not your own" }),
        };
        return {
          el: [labelled("Image file", f.file), labelled("Caption", f.caption),
            labelled("Alt text (optional)", f.alt), labelled("Credit (optional)", f.credit)],
          get: () => {
            const o = {};
            for (const k in f) if (f[k].value.trim()) o[k] = f[k].value.trim();
            return o.file || o.caption ? o : undefined;
          },
        };
      }
      case "table": {
        if (def.columns !== "dynamic") return tableRows(() => def.columns, value);
        const v = value || {};
        const title = input(v.label, { placeholder: "Table title" });
        const cols = input((v.columns || []).join(", "), { placeholder: "Column names, separated by commas" });
        const names = () => cols.value.split(",").map((s) => s.trim()).filter(Boolean);
        const rows = tableRows(names, v.rows);
        cols.addEventListener("change", () => rows.redraw());
        return {
          el: [labelled("Table title", title), labelled("Columns", cols), rows.el],
          get: () => {
            const r = rows.get();
            return title.value.trim() || names().length || r.length
              ? { label: title.value.trim(), columns: names(), rows: r } : undefined;
          },
        };
      }
      case "list": {
        const item = def.item || { shape: "text" };
        const make = item.shape === "text"
          ? (v) => { const t = el("textarea", { rows: 2 }); t.value = v ?? ""; return { el: t, get: () => t.value.trim() }; }
          : (v) => widget(item, v);
        return repeatable(make, value, item.shape === "table" ? "Add table" : item.shape === "image" ? "Add image" : "Add item",
          def.optional ? 0 : 1);
      }
      case "repeat":
        return repeatable((v) => group(def.item, def.item_order, v), value, "Add section", def.optional ? 0 : 1);
      case "block":
        return group(def.fields, def.field_order, value);
      default:
        return { el: el("p", { text: `Unsupported shape: ${def.shape}` }), get: () => undefined };
    }
  }

  // A labelled block: the widget plus its schema rules.
  function field(name, def, value) {
    const label = def.label || human(name);
    const w = widget(def, value);
    const head = el("span", { class: "af-label" }, label, def.optional ? el("span", { class: "af-opt", text: " optional" }) : null);
    return {
      name,
      el: el("div", { class: `af-field af-${def.shape}` }, head, w.el),
      get: w.get,
      check: (where) => {
        const v = w.get(), at = where ? `${where}: ${label}` : label;
        const out = [];
        if (!def.optional && empty(v)) out.push(`${at} is required.`);
        if (def.max_chars && typeof v === "string" && v.length > def.max_chars)
          out.push(`${at} is ${v.length} characters; the limit is ${def.max_chars}.`);
        if (def.pattern && typeof v === "string" && v && !new RegExp(def.pattern).test(v))
          out.push(`${at} must be ${def.hint}.`);
        return out.concat(w.check ? w.check(at) : []);
      },
    };
  }

  // Several blocks in order: a whole article, a repeated section, or a group.
  function group(defs, order, values) {
    const fields = order.map((n) => field(n, defs[n], (values || {})[n]));
    return {
      el: el("div", { class: "af-group" }, fields.map((f) => f.el)),
      get: () => {
        const o = {};
        for (const f of fields) { const v = f.get(); if (!empty(v)) o[f.name] = v; }
        return o;
      },
      check: (where) => fields.flatMap((f) => f.check(where)),
    };
  }

  // The base fields every article has, written as a schema for the form.
  const BASE = {
    title: { shape: "text", label: "Title", max_chars: 300 },
    authors: {
      shape: "repeat", label: "Authors", item_order: ["given", "family", "affiliation", "orcid"],
      item: {
        given: { shape: "text", style: "section", label: "Given names" },
        family: { shape: "text", style: "section", label: "Family name" },
        affiliation: { shape: "text", style: "section", label: "Affiliation", optional: true },
        orcid: { shape: "text", style: "section", label: "ORCID", optional: true },
      },
    },
    corresponding_email: { shape: "text", style: "section", label: "Correspondence email", optional: true },
    volume: { shape: "number", label: "Volume" },
    issue: { shape: "number", label: "Issue" },
    order: { shape: "number", label: "Article number in the issue" },
    published_date: { shape: "date", label: "Publication date" },
    licence: { shape: "text", style: "section", label: "Licence" },
    status: { shape: "choice", label: "Status", options: ["draft", "published"] },
    keywords: { shape: "list", label: "Keywords", optional: true },
    doi: { shape: "text", style: "section", label: "DOI from elsewhere (leave empty for Zenodo)", optional: true,
      pattern: "^10\\.\\d{4,9}/\\S+$", hint: "a DOI like 10.5281/zenodo.123" },
  };
  const BASE_ORDER = Object.keys(BASE);

  // ------------------------------------------------------------- the page
  const schemas = window.SCHEMAS || {};
  const picker = document.getElementById("af-schema");
  const form = document.getElementById("af-form");
  const report = document.getElementById("af-report");
  let base, body, fileName;

  Object.keys(schemas).sort().forEach((name) => {
    const s = schemas[name];
    picker.append(el("option", { value: name, text: `${human(s.article_type || name)} (version ${s.version || 1})` }));
  });

  function render(values) {
    const v = values || {};
    const s = schemas[picker.value];
    base = group(BASE, BASE_ORDER, Object.assign({ licence: "CC BY 4.0", status: "draft" }, v));
    body = group(s.blocks, s.structure, v);
    fileName = input(v.__file || "", { placeholder: "e.g. granuloma-pitfall (becomes granuloma-pitfall.md)" });
    form.replaceChildren(
      el("fieldset", {}, el("legend", { text: "Article" }), labelled("File name", fileName), base.el),
      el("fieldset", {}, el("legend", { text: human(s.article_type || picker.value) }), body.el));
    report.replaceChildren();
  }

  // The schema's date blocks, so dates are written unquoted, as the check requires.
  function dateKeys(defs, out) {
    for (const [k, d] of Object.entries(defs || {})) {
      if (d.shape === "date") out.add(k);
      dateKeys(d.item && d.item.shape ? { _: d.item } : d.item, out);
      dateKeys(d.fields, out);
    }
    return out;
  }

  function articleText() {
    const b = base.get(), ordered = {};
    for (const k of [...BASE_ORDER.slice(0, 8), "schema", ...BASE_ORDER.slice(8)])
      if (k === "schema") ordered.schema = picker.value === PREVIEW
        ? (schemaName.value.trim().replace(/\.ya?ml$/, "") || "schema-new-type") : picker.value;
      else if (k in b) ordered[k] = b[k];
    Object.assign(ordered, body.get());
    let yaml = window.jsyaml.dump(ordered, { lineWidth: 80, noRefs: true, quotingType: '"' });
    for (const k of dateKeys(schemas[picker.value].blocks, new Set(["published_date"])))
      yaml = yaml.replace(new RegExp(`^(\\s*(?:- )?${k}: )["'](\\d{4}-\\d{2}-\\d{2})["']$`, "gm"), "$1$2");
    return `---\n${yaml}---\n`;
  }

  function check() {
    const problems = base.check("").concat(body.check(""));
    report.replaceChildren(problems.length
      ? el("div", {}, el("p", { text: `${problems.length} problem(s). The article can be saved as a draft, but the site will not build until they are fixed.` }),
        el("ul", {}, problems.map((p) => el("li", { text: p }))))
      : el("p", { text: "No problems found." }));
    return problems;
  }

  function name() {
    return (slug(fileName.value) || slug(base.get().title || "") || "new-article") + ".md";
  }

  picker.addEventListener("change", () => render());
  document.getElementById("af-check").addEventListener("click", check);
  document.getElementById("af-download").addEventListener("click", () => {
    check();
    const a = el("a", { href: URL.createObjectURL(new Blob([articleText()], { type: "text/markdown" })), download: name() });
    document.body.append(a); a.click(); a.remove();
  });
  document.getElementById("af-copy").addEventListener("click", async () => {
    check();
    await navigator.clipboard.writeText(articleText());
    report.prepend(el("p", { text: "Copied." }));
  });
  document.getElementById("af-load").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const m = (await file.text()).match(/^---\s*\n([\s\S]*?)\n---/);
    const data = m ? window.jsyaml.load(m[1]) : null;
    if (!data || !schemas[data.schema]) {
      report.replaceChildren(el("p", { text: "That file is not an article with a known schema." }));
      return;
    }
    picker.value = data.schema;
    render(Object.assign(data, { __file: file.name.replace(/\.md$/, "") }));
  });

  // -------------------------------------------------- schema preview (design)
  // The same rules as scripts/validate.py, so a schema that previews cleanly
  // also passes the check on GitHub.
  const SHAPES = ["text", "list", "table", "boolean", "date", "repeat", "block", "image"];
  const STYLES = ["plain", "boxed", "opinion", "badge", "numbered", "section", "table", "wide"];
  const RESERVED = ["title", "authors", "corresponding_email", "volume", "issue", "order",
    "published_date", "licence", "schema", "status", "keywords", "doi"];
  const isMap = (x) => x && typeof x === "object" && !Array.isArray(x);

  function blockProblems(where, b) {
    if (!isMap(b)) return [`${where}: must be a mapping`];
    const out = [];
    if (!SHAPES.includes(b.shape)) out.push(`${where}: unknown shape ${JSON.stringify(b.shape)} (use one of: ${SHAPES.join(", ")})`);
    if (b.style && !STYLES.includes(b.style)) out.push(`${where}: unknown style ${JSON.stringify(b.style)} (use one of: ${STYLES.join(", ")})`);
    for (const [kind, orderKey, shape] of [["item", "item_order", "repeat"], ["fields", "field_order", "block"]]) {
      if (b.shape !== shape) continue;
      const subs = b[kind];
      if (!isMap(subs) || !Object.keys(subs).length) { out.push(`${where}: a ${shape} block needs a non-empty '${kind}'`); continue; }
      const listed = [...(b[orderKey] || [])].sort().join(), names = Object.keys(subs);
      if (listed !== [...names].sort().join()) out.push(`${where}: '${orderKey}' must list exactly: ${names.join(", ")}`);
      for (const [n, sub] of Object.entries(subs)) out.push(...blockProblems(`${where}.${n}`, sub));
    }
    if (b.shape === "table" && !(b.columns === "dynamic" || (Array.isArray(b.columns) && b.columns.length)))
      out.push(`${where}: a table needs 'columns' (a list, or 'dynamic')`);
    if (b.shape === "list" && b.item != null) out.push(...blockProblems(`${where}[item]`, b.item));
    return out;
  }

  function schemaProblems(s) {
    if (!isMap(s) || !Array.isArray(s.structure) || !s.structure.length || !isMap(s.blocks))
      return ["needs a 'structure' list and a 'blocks' mapping"];
    const out = s.structure.filter((n) => !(n in s.blocks)).map((n) => `'${n}' is in structure but not in blocks`);
    out.push(...Object.keys(s.blocks).filter((n) => RESERVED.includes(n)).map((n) => `'${n}' is a base field name; choose another name for this block`));
    out.push(...Object.keys(s.blocks).filter((n) => !s.structure.includes(n)).map((n) => `'${n}' is defined in blocks but not listed in structure, so it never appears`));
    for (const [n, b] of Object.entries(s.blocks)) out.push(...blockProblems(n, b));
    const teasers = s.structure.filter((n) => isMap(s.blocks[n]) && s.blocks[n].teaser);
    if (teasers.length !== 1) out.push(`exactly one block needs 'teaser: true' (found ${teasers.length})`);
    return out;
  }

  const yamlBox = document.getElementById("af-yaml");
  const yamlReport = document.getElementById("af-yaml-report");
  const schemaName = document.getElementById("af-schema-name");
  const start = document.getElementById("af-start");
  const PREVIEW = "(preview)";
  Object.keys(schemas).sort().forEach((n) => start.append(el("option", { value: n, text: n })));

  function nameNote() {
    const n = schemaName.value.trim().replace(/\.ya?ml$/, "");
    if (!n) return "";
    if (!/^schema-[a-z0-9-]+$/.test(n)) return "The file name must look like schema-my-type (lowercase letters, digits and dashes).";
    if (window.LOCKED && n in window.LOCKED) return `${n} is locked: published articles use it. Save under a new name, such as ${n}-v2.`;
    if (n in schemas && n !== PREVIEW) return `This will replace the existing ${n}.yml.`;
    return "";
  }

  let timer;
  function preview() {
    let s;
    try { s = window.jsyaml.load(yamlBox.value); }
    catch (e) { yamlReport.replaceChildren(el("p", { class: "af-over", text: `YAML error: ${e.reason || e.message} (line ${(e.mark && e.mark.line + 1) || "?"})` })); return; }
    const problems = schemaProblems(s), note = nameNote();
    yamlReport.replaceChildren(...[
      problems.length ? el("ul", {}, problems.map((p) => el("li", { text: p }))) : el("p", { text: "The schema follows the rules. The form below shows it." }),
      note ? el("p", { text: note }) : null].filter(Boolean));
    if (problems.length) return;
    schemas[PREVIEW] = s;
    if (![...picker.options].some((o) => o.value === PREVIEW))
      picker.prepend(el("option", { value: PREVIEW, text: "(Schema being designed)" }));
    picker.value = PREVIEW;
    render(Object.assign(base ? base.get() : {}, body ? body.get() : {}));  // keep what is typed
  }

  yamlBox.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(preview, 300); });
  schemaName.addEventListener("input", preview);
  start.addEventListener("change", () => {
    if (!start.value) return;
    yamlBox.value = window.jsyaml.dump(schemas[start.value], { lineWidth: 100, noRefs: true });
    schemaName.value = start.value;
    preview();
  });
  document.getElementById("af-schema-download").addEventListener("click", () => {
    const n = (schemaName.value.trim().replace(/\.ya?ml$/, "") || "schema-new-type");
    const a = el("a", { href: URL.createObjectURL(new Blob([yamlBox.value], { type: "text/yaml" })), download: `${n}.yml` });
    document.body.append(a); a.click(); a.remove();
  });

  render();
})();
