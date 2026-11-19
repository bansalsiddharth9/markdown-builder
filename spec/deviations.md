# Deviations from the design

Every place where the implementation differs from or adds to
[Caption Spec to Markdown Converter — Design](Caption%20Spec%20to%20Markdown%20Converter%20—%20Design.md),
and every choice the design left open. Updated at the end of each build phase.

Status: **open** = waiting for a decision; **accepted** = confirmed by the project owner;
**implemented** = done, not yet reviewed.

## Environment and dependencies

| # | Design says | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| E1 | Python 3.11+ | Python 3.12 (`.python-version`), `requires-python >=3.12,<3.13` | 3.12 was already on the host; one fixed version keeps the lock file reproducible. | implemented |
| E2 | Pandoc 3.x on PATH | Pandoc 3.14 binary copied into `.venv/bin`; the dependency check also looks there when the environment is not activated | Keeps every tool inside the project folder. | implemented |
| E3 | Chromium via `playwright install chromium` | Installed into `<repo>/.playwright`; new config key `fetch.browsers_path` points Playwright at it (an existing `PLAYWRIGHT_BROWSERS_PATH` still wins) | Keeps every tool inside the project folder. | implemented |
| E4 | — | PyTorch (pulled in by Docling) comes from the CPU-only index (`torch 2.15.0+cpu`) | The host GPU (GTX 1080) is not supported by current CUDA builds; CPU runs are more reproducible (design goal: deterministic output); avoids several GB of CUDA downloads. | implemented |
| E5 | — | Lock file resolved for Linux x86_64 only (`tool.uv.environments`) | Current PyTorch has no macOS Intel builds, which made the lock unsolvable; the tools only run on Linux (this host and the Docker image). | implemented |
| E6 | Pin all versions | `typer` pinned to 0.27.3 instead of the newest | Docling 2.151.0 requires `typer <0.28`, and the workspace shares one lock file. | implemented |
| E7 | — | `scripts/licenses.py` generates `LICENSES.md`; `tests/test_licenses.py` fails if the file is stale or any installed package has a license outside an allowed list | Enforces design principle 5 automatically whenever dependencies change. | implemented |
| E8 | — | Both Docling models pinned to commits (layout `8f39ad3c…`, TableFormer `fc0f2d45…` = tag v2.3.0) in `specconv/models.py`; downloaded once into `models/` (git-ignored, ~500 MB) with the new `specconv download-models`; new config key `pdf.models_path`; Docling runs with `HF_HUB_OFFLINE=1`. `convert` exits 2 with a hint when a PDF needs converting and the models are missing. | Docling otherwise downloads its layout model from the moving revision `main`; reproducible output needs fixed weights, and conversion should never use the network. | implemented |
| E9 | — | `ruff` (MIT) added as a pinned dev tool; code formatted with it; lint rules fixed explicitly in `pyproject.toml` (`E, F, W, I, B, UP`, line length 100) | Consistent formatting across phases; an explicit rule set does not change when ruff's defaults do. | implemented |
| E10 | — | `soupsieve==4.1.2` (MIT) is now a direct, pinned dependency of `specconv` | It was already installed with BeautifulSoup; `drop_selectors` are now checked with it when `sources.yaml` is loaded (F14). | implemented |

## Converter (phase 2)

| # | Design says | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| P1 | "Keep heading `id` attributes where Pandoc emits them" | Pandoc's `gfm` writer emits none, so in-page links broke. **Resolved in the design** (Lua filter `anchors.lua`, anchors on the heading line). Not implemented yet: added at the start of phase 3, together with self-link rewriting. | Design updated after phase 2. | accepted |
| P2 | Protected regions: fenced code, pipe tables, HTML `<table>` | Also indented code blocks and raw HTML `<pre>` blocks. The scanner is deliberately conservative: unclosed fences and HTML blocks are protected to the end of the document. | Pandoc writes DOCX code ("Source Code" style) as indented code, not fenced, so trailing-whitespace cleanup would otherwise alter code. Over-protecting only skips cosmetic cleanup; it never changes content. | implemented |
| P3 | Raw HTML blocks | A `<table>`/`<pre>` region starts only when the tag begins a line | Spec prose mentions elements inline (e.g. "the \<table\> element"); those must not start a region. | implemented |
| P4 | `pandoc SRC … --extract-media=OUT/media -o OUT/raw.md` | Pandoc runs inside the document folder with `--extract-media=media` and `--resource-path=<source folder>`; Markdown is read from stdout (no `raw.md`) | Links come out relative (`media/…`) without rewriting; relative image paths in HTML resolve from the source's folder. | implemented |
| P5 | HTML cleanup (`strip_tags`) is described for fetched pages | Also applied to local HTML files. The cleaned copy goes to `work/<id>/clean.html`; parsing uses Python's `html.parser`, which does not restructure the document; output is always UTF-8. `source.<ext>` stays the untouched original. | Local HTML has the same script/style/noscript noise (e.g. `<noscript>` text leaked into Markdown); Pandoc requires UTF-8 input. | implemented |
| P6 | `<id>` = slug of the file name, duplicates get a numeric suffix | Same, plus ids are **stable across runs**: a file keeps the id its path had in the previous manifest, and a new file never takes an id already in the manifest or an existing output folder name | Without this, adding or removing a file shifted ids, so a document was written into another document's folder and inherited its hand-edited title, version and validation. Found by a test. | implemented |
| P7 | Carry over `title`, `spec_version`, `manual_fixes`, `validated` | Refused (document fails with a clear message) when the existing file belongs to a different source or id, or its front matter cannot be read | Never give one document's hand-edited fields to another, never silently drop them. | implemented |
| P8 | — | Warning when the body changed and the previous file lists `manual_fixes` | Re-conversion replaces hand edits to the body; the person should know. | implemented |
| P9 | One output folder per document | Built in a temporary folder and swapped into place only on success | A failed re-conversion keeps the previous good output; stale media from an earlier conversion are removed. | implemented |
| P10 | Manifest lists every input | Full run: exactly the current inputs. `--only`: other entries kept. Output folders of removed inputs are kept on disk, with a warning. | Never delete converted files automatically. | implemented |
| P11 | Statuses `ok, failed, no_text_layer, unsupported, skipped_unchanged`; exit 1 if a document failed | Exit 1 for `failed` and `no_text_layer`; `unsupported` (`.doc`) is a warning only. Manifest `source_type` gains `doc`. | A `.doc` file is a known, reported limitation rather than a conversion failure. | open |
| P12 | Ignore other extensions and log them as skipped | Hidden files and folders (`.gitkeep`, `.DS_Store`) are ignored silently | They are never documents. | implemented |
| P13 | Per-document timeout (15 minutes, described for the web worker) | Pandoc is run with a 900 s timeout in the CLI too | A hung converter must not block a run. | implemented |
| P14 | — | Until their phases land, web sources fail with "built in phase 4" (PDFs: done in phase 3) | Fail loudly rather than skip. | temporary |

### Heading ids (P1): resolved

The design chose option C (Lua filter writing `<a id>` anchors on the heading line). The comparison of options A to C that led to it is in the git history of this file.

## Images, ids and self-links (decided after the design update, 2026-11-19)

| # | Design says | Decision | Why | Status |
| --- | --- | --- | --- | --- |
| I1 | Images are copied into `media/`; fetched pages get absolute image URLs | **Conversion never uses the network.** Images on disk (including a `_files` folder) are copied into `media/`; images that exist only on the web stay as web links. Downloading web images, if wanted, belongs to the fetch step (phase 4) and its protected HTTP client. | Pandoc otherwise downloads every web image while converting (seen in a test): results would depend on the network, and in the web app an uploaded HTML file could make the server request any address, bypassing the URL protection. | accepted |
| I2 | Report any image that cannot be found; V7 then fails | The image link is kept, still pointing at the missing file, and a warning is recorded | Pandoc replaces a missing image with its description text, which would hide the problem from V7. | accepted |
| I3 | The Lua filter keeps every id that a link in the document targets | Heading and section ids are **always** kept, targeted or not; other element ids (definitions, figures, examples, table rows and cells) only when a link in the document targets them | Other documents link into a spec's sections (e.g. IMSC into TTML2), and those links are not in the document itself. | accepted |
| S1 | Rewrite links to the document's own URL into `#fragment` links | A link to the document's own URL **without** a fragment is left unchanged | There is no fragment to keep; the link is harmless. | accepted |
| S2 | Flag `self_url_unknown` when no own URL is found | Only set when the document has full-URL links with a fragment that could be self-links | Otherwise every hand-written local HTML file would carry a meaningless flag. | accepted |

## Converter (phase 3)

| # | Design says | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| Q1 | Lua filter keeps ids of headings and of the `<section>` containing them, on the heading line | Done. A plain `<section>` wrapper (only class `section`) is then removed; wrappers with other classes (e.g. `informative`, `note`) are kept without their id. | Pandoc's GFM writer otherwise writes every section as a `<div id class="section">` wrapper; once the id is on the heading the plain wrapper carries nothing. Other classes can carry meaning. | implemented |
| Q2 | — | DOCX: only heading ids that a link in the document targets are kept (`-M anchor-headings=targeted`); HTML keeps all (I3) | Pandoc's DOCX reader invents heading ids even with `-auto_identifiers`; those are not source ids and nothing links to them. | implemented |
| Q3 | — | Table ids are always removed from the table itself (anchor before it if targeted); a code block keeps its id attribute | Pandoc writes a pipe table's id literally in its caption (`Codes {#tbl-codes}`); removing a code block's last attribute makes Pandoc switch from a fenced to an indented block. | implemented |
| Q4 | Pandoc with `--extract-media` | HTML: Pandoc runs with `--sandbox` and no `--extract-media`; `specconv` copies local images itself before conversion. DOCX: `--sandbox` with `--extract-media` (images inside the file). | Implements I1/I2: Pandoc would download web images and replace missing ones with their description. | implemented |
| Q5 | Images in the `_files` folder are copied into `media/` | Only images inside the HTML file's own folder (and its subfolders) are copied; other local paths (`../x.png`, `file:///etc/…`) keep their link and get a warning. Names are made safe (`fig 1.png` → `fig-1.png`), clashes get a suffix, a file used twice is copied once. | Security for the web app: an uploaded page must not pull arbitrary server files into a downloadable `media/` folder. | implemented |
| Q6 | Keep the `_files` folder next to the HTML file in `input/` | Files inside `<name>_files/` next to `<name>.html`/`.htm` are not scanned as inputs | Browsers also save frames and scripts as `.html`/`.js` there; they are parts of the page, not documents. | implemented |
| Q7 | Docling markdown export | Tables with merged cells use Docling's HTML export **including** its `<caption>`; the caption is not repeated as a paragraph. Pictures are rendered at `images_scale=2.0` (144 dpi). | Docling's HTML export adds the caption even with `add_caption=False`; Pandoc's HTML tables also carry the caption inside, so PDF and HTML outputs match. Docling's default 72 dpi is too coarse for spec diagrams. | implemented |
| Q8 | — | Text Docling classifies as page header/footer ("furniture") is listed in `conversion.json` (`docling.dropped_as_page_furniture`), with a warning | Docling drops it from the Markdown by design, and a real code line at the bottom of a page can be misclassified (seen in the fixture: the `WEBVTT` line). Content is unchanged; the list lets a reviewer (and later V3/V6) see what was dropped. | implemented |
| Q9 | Incremental: skip when source SHA-256 and converter version match | Also requires the same options hash (now including the HTML file's configured `self_url` and a `recipe_version` that is bumped when preparation, filters or post-processing change), the same source path, an existing output file, and a previous status of `ok`/`skipped_unchanged` (failed documents are retried). A skipped entry keeps the previous entry's details. | A changed option or recipe must produce new output; found by a test (changing `self_urls` was not picked up). | implemented |
| Q10 | `no_text_layer`: not converted | No output folder is written; an existing output folder from an earlier conversion is left in place. Damaged or password-protected PDFs fail with a clear error. | Never delete converted files automatically. | implemented |
| Q11 | Fixtures: HTML printed to PDF with `page.pdf()` | The fixture HTML now has table borders, a real-size image and W3C-style shaded code blocks; mixed-text-layer PDFs are built by merging pages | Without borders Docling found no tables; without a visible size it found no picture; real specs look like this. | implemented |
| Q12 | — | When a link targets an id on `<nav>`, `<article>`, `<footer>` or `<details>`, HTML preparation moves the id to an empty `<a id>` at the start of the element | Pandoc's HTML reader drops ids on these four elements (checked with Pandoc 3.14); TTML2's `#toc` link pointed nowhere. Content unchanged. | implemented |
| Q13 | — | Docling's own log warnings (e.g. "5 of 152 pdf cells … were dropped from the table") are collected into the document's warnings in the manifest and `conversion.json` | Design principle 3 (fail loudly): on CEA-608-E they were only printed to the console. | implemented |
| Q14 | — | HTML preparation replaces every table with exactly one cell by its caption (as a paragraph) followed by the cell's contents. Ids on the table, its rows or the cell move to empty `<a id>` anchors. | W3C specs draw a box around each example with a one-cell table; inside a table cell an example cannot be a code block. Decided by the user (option B, 2026-11-19). The validator must apply the same rule when counting tables (V1). | implemented |
| Q15 | — | A `<pre>` without a `<code>` inside gets one before conversion | Pandoc 3.14's HTML reader writes a bare `<pre>` as plain text with `<br />` line breaks, not as a code block, wherever it is. TTML2 (272) and SAMI (16) use bare `<pre>`. | implemented |
| Q16 | — | The anchors filter writes code blocks that have no language, class or id as fenced blocks itself (the fence is longer than any backtick run in the code) | Pandoc's GFM writer writes such blocks as indented code. DOCX "Source Code" paragraphs are now fenced too. | implemented |
| Q17 | — | The anchors filter clears a table's classes and styles too, not only its id | Pandoc's GFM writer printed them after the caption as text: `Table 5-1 – Namespaces {.common}` (25 tables in TTML2). Found by V4 in phase 5. | implemented |
| Q18 | — | Text Docling labels as a formula but cannot decode (its formula model is not used) is written as its original PDF text in a code block, instead of Docling's `<!-- formula-not-decoded -->`; counted in `conversion.json` as `docling.formulas_as_text` | **Content loss found by V4:** on CEA-608-E page 41 the URL syntax `<url>[attr1:val1]…[checksum]` and its example were replaced by the placeholder. A code block keeps `<` and `[` exact. | implemented |
| Q19 | `clean: none` removes only `<script>`, `<style>`, `<noscript>` | `<template>` is removed too (default `html.strip_tags`) | Browsers never display `<template>` content. The saved TTML2 page contained an Adobe Acrobat extension's button ("AI Assistant") inside one, which Pandoc converted into 5 icons and text. Found by V1 (image count). | implemented |
| Q20 | — | Tables Docling labels as a document index (table of contents, list of tables, list of figures) are written as a bullet list, one entry per row, text exactly as in the PDF (dot leaders and page numbers kept, Markdown characters escaped); counted in `conversion.json` as `docling.document_index_as_list` | Decided by the user (2026-11-19): the contents pages of CEA-608-E (PDF pages 6–13) came out as 8 one-column tables. Real tables are unaffected. | implemented |
| Q21 | — | Pandoc runs with `--preserve-tabs` | Pandoc's default turns tabs in code into spaces, so 3 TTML2 examples were not byte-exact. Found by V6 in phase 6. | implemented |
| Q22 | — | A second Lua filter, `spacing.lua`, moves whitespace at the edges of inline code and emphasis outside the markup | CommonMark drops one space at each end of a code span that starts and ends with a space (TTML2 `<length-1>top<length-2>`) and does not read `*S' *` (a non-breaking space before the closing `*`) as emphasis, so readers saw lost spaces and stray asterisks. The visible text is unchanged. Found by V5 and V3. | implemented |


### Code examples inside table cells (TTML2): resolved (Q14, Q15, Q16)

W3C specs such as TTML2 wrap every example in a one-cell table: `<table class="example"><caption>…</caption><tr><td><pre>…</pre></td></tr></table>`. Before the fix the examples came out as escaped text joined with `<br />` inside an HTML table. Two causes: inside a table cell an example cannot be a Markdown code block, and Pandoc 3.14 reads a bare `<pre>` (no `<code>`) as plain text even outside tables. The user chose option B (unwrap one-cell tables); Q15 and Q16 were needed to make the examples real fenced code blocks.

On TTML2 this unwraps 329 tables: 120 examples and 138 syntax boxes (all with a `<pre>`), 63 image boxes, 7 formula boxes and 1 text box. The 9 tables with more than one cell are unchanged.

## Fetching (phase 4)

| # | Design says | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| F1 | A cached fetch is reused unless `--refetch` | The cache is also not used when the source's `url` or `render` changed in `sources.yaml`, the saved HTML no longer matches the SHA-256 in its sidecar, or the sidecar is unreadable | A stale cache would silently convert the wrong page. | implemented |
| F2 | Non-200 status is a failure | Also a failure: a content type other than HTML (e.g. a URL that serves a PDF), with the hint to put the file in the input folder | Web sources are always converted as HTML. | implemented |
| F3 | 3 retries | Retried: network errors, timeouts, HTTP 408, 425, 429 and 5xx, waiting 1, 2, 4 s between attempts. Other 4xx (e.g. 404) fail at once. Same rules for `render: browser`. | A missing page will not appear on a second try; a busy server may. | implemented |
| F4 | — | A failed `--refetch` keeps the earlier cached copy; the log says so, and the command exits 1 | Never lose a good page because the site was down once. | implemented |
| F5 | — | The page's encoding is taken from the HTTP `Content-Type` charset when present, and is UTF-8 for `render: browser` (saved from `page.content()`); it overrides a `<meta charset>` in the page | HTML's own rule (the header wins); a browser-rendered page keeps its original `<meta>`, which no longer matches the saved bytes. | implemented |
| F6 | Sidecar: URL, final URL, HTTP status, fetch time, render mode | Also: content type, encoding, SHA-256, size, user agent, specconv version, duration | Needed for F1 and F5, and for the run report. | implemented |
| F7 | `clean: main-content` removes `<nav>`, `<header>`, `<footer>`, `<aside>`; keeps only `<main>` or `<article>` | Keeps the first `<main>`; without one, all top-level `<article>` elements. `<nav>` and `<aside>` are removed everywhere; `<header>` and `<footer>` only outside `<main>`, `<article>` and `<section>`. Then `drop_selectors`. What was kept and removed is in `conversion.json`. | Inside an article or section, `<header>` usually holds its title (HTML's own meaning); removing it would drop headings. A page can have several articles. | implemented |
| F8 | Relative links and image URLs become absolute | Attributes changed: `href` on `a`, `area`, `link`; `src` on `img`, `source`, `track`, `video`, `audio`, `iframe`, `embed`; `data` on `object`. A `<base href>` is honoured. Links to the page itself (e.g. `./#intro`, `index.html#b`) then become `#…` by the self-link rule. | Complete list instead of "links and images"; HTML's own base rule. | implemented |
| F9 | Under 500 characters of text after cleanup: flag `suspect_empty`, suggest `render: browser` | Counts non-whitespace characters, as for PDFs; the page is still converted, with the flag and a warning; the `render: browser` hint only for static pages | Same counting as the PDF text-layer check; the flag asks a person to look, it does not decide. | implemented |
| F10 | — | Every `<main>` becomes a `<div>` with the same attributes before conversion, for all HTML (local and fetched) | **Content loss found:** when a page has a `<main>`, Pandoc 3.14's HTML reader silently drops everything outside it, even with `clean: none`. None of the current real specs has a `<main>`, but many web pages do. | implemented |
| F11 | Fetched page: `source.<ext>` is the fetched HTML | `source.html` is the cached page byte for byte; front matter has `source_url`, `fetched_at` and an empty `source_file`; the manifest records `httpx` or `playwright` in `tool_versions` | `source_file` names a file in the input folder, which a web page does not have. | implemented |
| F12 | `convert` fetches sources not yet cached | Chromium is only required for `render: browser` sources that are not cached yet (both `fetch` and `convert`) | Converting from the cache needs no browser. | implemented |
| F13 | — | A web source's options in the manifest include its `url`, `render`, `clean`, `drop_selectors`, final URL and encoding, so changing `clean` re-converts from the cache without fetching again | Same rule as other options (D1). | implemented |
| F14 | — | `drop_selectors` are checked when `sources.yaml` is loaded; an invalid CSS selector is a config error (exit 2) | Fail before any work starts (D3). | implemented |
| F15 | No network access in tests | An autouse test fixture blocks every connection that is not to the local machine; fetch tests use a local HTTP server, including the Chromium tests | Enforced instead of only promised. | implemented |
| F16 | Relative image URLs become absolute (images stay on the web) | `fetch` downloads every `<img src>` image of the page, from any site, into `work/fetched/<id>_files/` and lists each one (URL, file, SHA-256, size, content type) in the sidecar; `convert` copies them into `media/` like a saved page's `_files` folder, still without network. An image that cannot be downloaded (HTTP error, an HTML page instead of an image, over 50 MB) does not fail the fetch: it keeps its web link and the document gets a warning. Same image URL: one download; same file name from two URLs: `x.png`, `x-2.png`. The cache is stale if a downloaded image is missing or changed; a changed image re-converts the document. | Decided by the user (option B, 2026-11-19): the library must keep its figures when the site moves or the reader is offline. | implemented |

### Live check

`specconv fetch` + `convert` on the TTML2 entry in `config/sources.yaml` (fetched from w3.org on 2026-11-19): HTTP 200, 1.6 MB; own URLs from `sources.yaml` and the canonical link; the live page already uses `#…` links (0 self-links to rewrite); all 5,308 in-page links resolve; 329 one-cell tables unwrapped, all 272 examples fenced code blocks; 86 relative links made absolute; all 83 images downloaded by `fetch` and copied into `media/` (F16), every image link points to a local file; no warnings.

## Real specs (phase 3 run on `input/`)

| Spec | Result |
| --- | --- |
| TTML2 (saved page, 1.9 MB) | Pandoc. All 6,786 self-links rewritten (the number the design predicted); all 6,764 in-page links resolve; 83 images copied from the `_files` folder; all 272 examples are fenced code blocks (Q14 to Q16). |
| SAMI 1.0 (`.htm`) | Pandoc. Own URL from the canonical link; all 29 in-page links resolve; 9 images exist only on the web and stay web links (I1); its 16 examples are fenced code blocks (Q15). |
| CEA-608-E (143-page PDF) | Docling, 4 minutes on this host's CPU, peak memory 5.6 GB (relevant for worker sizing in the web app). 78 tables, 12 with merged cells written as HTML; 416 page headers/footers left out (licence watermark, running title, page numbers); Docling reports 7 table cells it dropped in two tables (now in the warnings); one footnote row was split across cells. |

### Known Docling limitations (seen on the fixture; the validator must expect them)

These are not fixed in the converter (design principle 1: never "fix" content). They are recorded here because V3, V4 and V6 must handle them:

- **Line breaks inside code are lost**: a multi-line XML example comes out as one line (text complete, order kept). A strict `xfail` test records this; it will start failing if a future Docling keeps line breaks.
- **Code at a page break can be dropped** as a page footer (see Q8), and a neighbouring paragraph can be pulled into a code block depending on layout.
- **Characters are normalized by Docling's PDF parser**: the en dash `–` becomes `-` (pypdf and pdfplumber read `–` from the same file).
- **Heading levels are flattened**: every heading comes out as `##`.
- **HTML special characters in text are escaped** (`-->` becomes `--&gt;`): correct Markdown, but V3/V4 must decode entities before comparing.

## Command line

| # | Design says | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| C1 | `specconv fetch`, `specconv convert` | Added `specconv check-deps`: runs all dependency checks and prints tool versions | Quick way to verify an installation. | implemented |
| C2 | `specconv` options as listed | Added `--config` (default `config/specconv.yaml` if present, else built-in defaults), `--work` (default `work/`) and `--verbose` | The design names `work/` and config files but no options for them; `specval` already had `--config`. | implemented |
| C3 | `specval run --output --doc --config` | Added `--acceptance` (default `config/acceptance.yaml` if present), `--validation` (default `validation/`) and `--verbose` | Same reason as C2. | implemented |
| C4 | `specval approve ID --output [--override]` | Added `--by NAME` | The web UI records who approved; the CLI should be able to as well. | implemented |
| C5 | `convert --input input/ --sources sources.yaml` | Both are optional, but at least one is required | Allows converting only files or only web pages. | implemented |
| C6 | A log file per run | `convert` → `output/run-<timestamp>.log`; `fetch` → `work/fetch-<timestamp>.log`; `specval run` → `validation/run-<timestamp>.log` | The design only names the `convert` log location. | implemented |
| C8 | — | New command `specconv download-models [--force]` | Fetches the pinned Docling models (E8); needed once per machine and in the Docker image. | implemented |
| C7 | — | Commands not built yet exit with code 1 and say "not implemented yet (phase N)" | Design principle 3: fail loudly, never pretend to succeed. Removed as each phase lands (`convert`: phase 2; `fetch`: phase 4; `specval run`: phase 5; `specval approve`: phase 6; `--split`: phase 7). | implemented |

## Data formats

| # | Design says | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| D1 | Manifest entry fields: id, source, route, converter and version, status, flags, warnings, duration | Also: `source_type`, `source_sha256`, `tool_versions` (every tool involved), `options`, `options_hash`, `error`; file-level `schema_version`, `generator`, `generated_at` | `source_sha256` is needed for incremental runs; `options_hash` makes a changed option (e.g. `--split`) trigger re-conversion, not only a changed source or converter version; `tool_versions` follows "record tool versions in every manifest entry". | implemented |
| D2 | `validated` set to "today's date and the validator version" | `validated: {date, validator, by}` mapping | Records who approved (see C4); one structured value instead of a free string. | implemented |
| D3 | — | Config and manifest loading is strict: unknown keys, wrong types and out-of-range values are errors (exit code 2) | A typo in a config file must not be silently ignored. | implemented |
| D4 | `acceptance.yaml` `expect` values | `expect` must be a quoted string; unquoted numbers are rejected with a message | YAML reads unquoted `0x25` as 37 and `1.10` as 1.1, which would silently check for the wrong text. | implemented |
| D5 | `sources.yaml` `id` names the output folder | Ids may only contain letters, digits, `.`, `_`, `-` and must start with a letter or digit; `drop_selectors` is only allowed with `clean: main-content` | Ids become folder names, so `../x` must be impossible; a selector list that is never applied is a config mistake. | implemented |

## Validator (phase 5)

Rules V-a to V-g were open; each was implemented with its proposed default (design: "use the default and list the question in the phase summary"). V-h to V-r were decided while building and running phase 5.

| # | Topic | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| V-a | V4 hex pattern `[0-9A-Fa-f]{2,4}h` matched words like "each"; 4-digit hex matches years | Whole words only, with at least one digit; the 4-digit family only counts table cells | As proposed. | implemented |
| V-b | "shall not" also contains "shall" | Longest match wins: "shall not", "should not", "must not" are one token each | As proposed. | implemented |
| V-c | "may" also matches the month | Kept as designed | Both sides are counted the same way, so a month only matters if it is lost. | implemented |
| V-d | PDF running headers, footers and page numbers inflate source counts | Left out of the PDF text: lines that repeat unchanged on more than half the pages, and lines whose number follows the page number ("Page 12") on more than half the pages | The proposed "digits ignored" rule also removed real lines such as "Table 3 text"; tying the number to the page avoids that. CEA-608-E: 550 lines left out. | implemented |
| V-e | V2 for PDFs (no source headings) | Only checks that the Markdown numbering is in order | As proposed. | implemented |
| V-f | DOCX code blocks | A paragraph style whose name contains "code" or "source", or a paragraph whose runs are all in a monospace font (Courier, Consolas, Menlo, Monaco, Source Code, Lucida Console); consecutive ones form one block | As proposed. **Note:** Pandoc only treats the exact style `Source Code` as code, so a DOCX using e.g. a "Code Block" style will fail V1 (its code is converted as normal paragraphs). That is a real loss of code formatting; see the open question below. | implemented |
| V-g | One-cell tables and bare `<pre>` (Q14, Q15) | V1 does not count tables with exactly one cell (source and Markdown); every `<pre>` is a code block | As proposed; matches what the converter does. | implemented |
| V-h | "4-digit byte pairs inside tables" for PDFs | Not compared for PDFs (shown as not applicable in the details) | The PDF text has no reliable table structure, so the family compared phone numbers, years and statistics (CEA-608-E: 8 false differences). V5 (phase 6) compares PDF table cells with pdfplumber instead. | implemented |
| V-i | V4 for PDFs: pypdf runs a table row's cells together ("25 % %") | Markdown table rows are joined the same way before V4 compares PDF documents | Otherwise every "number + unit" split across two cells is a false difference. | implemented |
| V-j | V1 images | `<svg>` drawings (outermost only) count as images, as `<img>` does | Pandoc converts an inline `<svg>` into an image (a `data:` URI). | implemented |
| V-k | V7 valid `#…` targets | Every `id` in raw HTML in the Markdown is a valid target (not only `<a>`, `<span>` and table elements), plus GitHub-style heading slugs | A browser jumps to any element with that id. | implemented |
| V-l | V7 media | A local image or `media/` link to a missing file fails; an image that is still a web link warns ("not stored locally") | Phase 4 downloads images, so a web image is either from a local HTML file or a failed download. | implemented |
| V-m | V7 front matter | Also fails when the front matter `id` is not the folder's id, or `source_sha256` does not match `source.<ext>` | Catches a Markdown file paired with the wrong source. | implemented |
| V-n | V7 lint | pymarkdownlnt with style rules off that the converters' output breaks by design (raw HTML, line length, several top-level headings, Docling's flat `##`, and MD051 because it does not know `<a id>` anchors). Files over 10,000 lines are not linted, and the report says so | The linter's time grows about with the square of the length: 1,757 lines 5 s, 4,350 lines 27 s; TTML2 (24,519 lines) would take about 15 minutes. Lint only warns. | implemented |
| V-o | Exit code of `specval run` | 1 if any document fails, or its conversion failed (`failed`, `no_text_layer`); `unsupported` documents are reported but do not fail the run | Same rule as `specconv convert`. | implemented |
| V-p | Documents not converted | Reported with overall status `not_converted`, the conversion error, and no checks | There is nothing to compare. | implemented |
| V-q | Checks of phase 6 | Shown as `not_run` in reports and `summary.csv` until phase 6 | Never pretend a check passed. | implemented |
| V-r | `specval run --doc ID` | Rewrites that document's reports and its `summary.csv` row; other rows are kept | Otherwise one document's re-check would erase the others from the summary. | implemented |

### Open question: DOCX code styles

Pandoc only recognizes the paragraph style named exactly `Source Code` as code. DOCX specs that use another code style (e.g. "Code Block", "HTML Preformatted") or just a monospace font come out as normal paragraphs, which V1 reports as missing code blocks. The converter could map those styles to code before Pandoc runs (for example with a Lua filter on the `custom-style` attribute). None of the current real specs is a DOCX, so this has not been seen yet.

### Real specs (phase 5)

| Spec | Result | Findings |
| --- | --- | --- |
| TTML2 | pass (V1, V2, V4, V7) | Before the fixes above it failed: 5 extra images (Q19) and caption attribute text (Q17). |
| SAMI | warn | 9 images that exist only on the web; one heading level jump (1 to 3) that is in the source. |
| CEA-608-E | fail | Table count: Markdown 70 (after Q20), pdfplumber 72; checked by the user, no table missing (FS-1). Real things to review: a "2. Text and XDS" heading inside Annex E that is probably a list item; one table where Docling merged two rows ("63 64 c d c d"); pypdf reads `attr 1:val1` with a space where Docling has none. Before Q18 it also reported the lost URL syntax on page 41. |

## Validator (phase 6)

| # | Topic | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| V-s | V3 normalization | Besides NFKC and whitespace: dash and quote variants unified, runs of 3+ leader dots treated as one "...", PDF words hyphenated across lines joined | Docling turns "–" into "-" (exact characters are V4's job); pypdf and Docling read a different number of leader dots in contents lines. | implemented |
| V-t | V3 units for PDFs | Each printed line is split into sentences on its own | PDF text has no paragraphs: a heading without a full stop glued onto the next sentence, and Docling can put blocks in another order (seen on the fixture's WebVTT example). | implemented |
| V-u | V3 listing | Missing sentences are listed (up to 50) as fail or warn findings when coverage is below the thresholds, otherwise as "info" (listed in the report, status unchanged) | The design lists every missing sentence but sets the status by coverage. | implemented |
| V-v | V5 HTML/DOCX | Tables compared as grids with spans expanded (a merged cell's text in every position it covers); rows aligned so a missing or extra row is named once; an empty first Markdown row is ignored when the Markdown has one row more (Pandoc's header row for tables without one); a cell's paragraphs are joined by a space | Locations: table number, row, column, and the Markdown line of the cell. | implemented |
| V-w | V5 PDF | pdfplumber tables with only empty cells are ignored (FS-1); disagreements with a paired pdfplumber table are warnings; cells of unpaired Docling tables not found in the PDF text are failures; cells compared without spaces and with V3's normalization | Two approximate extractors: a disagreement narrows what to check but proves nothing; a cell that is nowhere in the PDF text is a real problem. | implemented |
| V-x | V6 XML | A block is XML when tagged xml/ttml or when it starts with "<" and has a closing tag, "/>", a declaration or a comment; fragments are parsed inside a wrapper element that declares the prefixes they use; a parse error the paired source block also has is "info" | Spec examples are fragments; `<url>[attr:val]` (CEA-608-E) is not XML; a faithful copy of a non-well-formed example is not a conversion error (TTML2's syntax boxes). | implemented |
| V-y | V6 WebVTT | Cue settings after the timing must be `name:value` tokens | Otherwise cue text joined onto the timing line (Docling's lost line breaks) would pass. | implemented |
| V-z | V8 | Tokens of hex digits with at least one digit and one of O, o, l, I, S (and an x only as the 0x prefix, an h only at the end); a token the source has as often is not reported | A faithful copy is not a confusion. | implemented |
| V-aa | Review queue samples | PDF: pages; HTML and DOCX: sections from the top two heading levels; seed is `<seed>:<document id>` | Repeatable per document. | implemented |
| V-ab | `specval approve` | Refuses when there is no report, the document was not converted, the Markdown changed since the report (SHA-256 recorded in the report), or the report has fail findings without `--override`; an override is recorded in `manual_fixes` as `{type: approval override, reason, date, by, fail_findings}` | An approval must cover exactly the file that was checked. | implemented |

### Known Docling limitations found in phase 6 (CEA-608-E and the fixture)

- A code block can be split in two and the parts put in another order (fixture WebVTT example).
- Some rows of the contents pages are dropped (e.g. "7.5 Other Real-Time Display Methods"); the sections themselves are converted.
- Text inside diagrams is part of the image, so V3 lists it as missing (e.g. waveform labels on page 27).
- Footnote markers can move to another cell (`MPA4` vs `MPA`).

### Real specs (phase 6)

| Spec | Result |
| --- | --- |
| TTML2 | pass on all nine checks (after Q21 and Q22); 5,959 sentences, 129 tables and 272 code blocks compared. |
| SAMI | warn: only V7 (web images, one heading level jump in the source). |
| CEA-608-E | fail, with genuine findings: V1 table count (FS-1), V2 stray "2." heading, V3 98.9% (contents rows and diagram text, see above), V4 two tokens, V5 13 cells not found and some extractor disagreements. |

## Split (phase 7)

| # | Design says | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| SP1 | One file per heading at the level | A section starts at every heading of that level **or higher** (a `#` heading also ends a `##` section); deeper headings stay inside | Otherwise the text under a higher heading would belong to the previous section's file. | implemented |
| SP2 | File names like `07-3-2-pop-on-captions.md` | Numbered headings: the number with its first part zero-padded, then a slug of the title; unnumbered: `sNN-<slug>.md` by position; repeats get `-2`; names limited to 60 slug characters | Matches the design's example; position keeps unnumbered files in order. | implemented |
| SP3 | Section front matter names the parent document and section number | `parent`, `parent_file` (`../<id>.md`), `section` (number or null), `title`, `level`, `order`; `index.md` has `parent`, `parent_file`, `split_level`, `sections` | Plain YAML; the shared front-matter schema is for whole documents. | implemented |
| SP4 | Links to anchors in other sections are rewritten | Also relative paths (`media/…`) get `../`; links inside tables are rewritten (the cell text is unchanged), links inside fenced code and `<pre>` never; an unknown `#id` is left as it is | Section files sit one folder down; V7 reports unknown targets on the full document. | implemented |
| SP5 | `index.md` | The text before the first section, then a nested list of links to the sections | One entry point per document. | implemented |
| SP6 | — | `--split` is part of the options (and the manifest's options hash) only when given; converting again without it removes `sections/` | Existing outputs are not re-converted just because the option exists. | implemented |

Real spec: TTML2 split at level 2 gives 42 section files and `index.md`; all 6,177 local links in them resolve (found and fixed while testing: Pandoc indents nested list content by 4 spaces, which must not be taken for code).

## Real-spec run and README (phase 8)

The design asks for one PDF with code tables, one W3C page and one DOCX. `input/` has no DOCX spec, so phase 8 used the three specs given (FS-3):

| Spec | Converter | Validation | Time |
| --- | --- | --- | --- |
| CEA-608-E (PDF, 143 pages) | Docling | fail: genuine findings to review (FS-1 table count, a stray heading, contents rows and diagram text Docling dropped, extractor disagreements) | convert about 4 min, peak memory 5.6 GB; validate 52 s |
| TTML2 (saved W3C page, 1.9 MB) | Pandoc | pass on all nine checks | convert 20 s; validate 27 s |
| SAMI (HTML) | Pandoc | warn (web images, a heading level jump that is in the source) | convert 1 s; validate 5 s |

Thresholds: the design defaults were kept. On these specs every fail and warn left after the fixes found during phases 5 and 6 is a real difference a person should look at, and the faithful conversions pass, so there was nothing to tune.

Host sizing (design open question): one PDF worker needs about 6 GB of memory; this host converted 143 pages in about 4 minutes on CPU.

`README.md` covers setup (uv, Pandoc 3.14 in `.venv/bin`, Chromium in `.playwright/`, pinned models), daily use (convert, validate, review queue, approve, split), configuration, outputs, PDF limitations and development commands.

## Web application core (phase 9)

| # | Design says | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| WB1 | Workers load the Docling models once and keep them warm; every step runs in a subprocess with a timeout | Every step runs as `python -m specconv …` / `python -m specval …` in a subprocess (new `__main__` modules in both packages), so models are loaded once per job, not kept warm | The two design points conflict; robustness wins: a stuck or crashing converter is killed by its timeout and cannot take the worker down. Model loading adds a few seconds per PDF job. | implemented |
| WB2 | Step timeout 15 minutes per document | Each step's timeout is 15 minutes times the number of documents (URLs for fetch) | One `convert` run handles all documents of a job. | implemented |
| WB3 | Queue in `specweb.sqlite` | Huey's queue is in its own file, `data/queue.sqlite`; jobs and documents in `data/specweb.sqlite` (WAL mode, shared by web and worker) | Two libraries writing one SQLite file would contend for its lock. | implemented |
| WB4 | Store uploads under generated names, never the user's file name | Stored name = a slug of the last path part of the uploaded name (`CTA 608-E.pdf` -> `cta-608-e.pdf`), unique per job; the original name is kept only in the database for display | Document ids come from file names; a slug is safe (no paths, ASCII only) and keeps ids readable. Content-type checks, size and count limits follow in phase 11. | implemented |
| WB5 | `/api/documents/{doc_id}` (open question W-a) | Document ids are unique per job only, so document routes will be `/api/documents/{job_id}/{doc_id}` (phase 10) | W-a's proposed default. | decided |
| WB6 | Upload form: choose static or browser per URL | One URL per line in a text box; add `browser` after a URL for a JavaScript-built page | No JavaScript needed for a per-row control. The API takes `urls` as a JSON list of `{url, render}`. | implemented |
| WB7 | — | A failed fetch does not stop the job: `convert` records it as a failed document, the others are converted | Same rule as the command line: one bad document never stops the rest. | implemented |
| WB8 | — | New command `specweb serve [--host --port --config]` and `specweb worker [--config]`; settings from `specweb.yaml` (strict) and `SPECWEB_DATA` / `SPECWEB_CONFIG` | Entry points for the Docker services in phase 11. | implemented |
| WB9 | — | `scripts/licenses.py` reads a package's bundled LICENSE file when its metadata names no license (huey: MIT); htmx 2.0.4 (0BSD) is vendored as `static/htmx.min.js` and listed in LICENSES.md | huey 3.5.4 declares no license in its metadata. | implemented |
| WB10 | Approve is enabled once every fail item is ticked, or with an override reason, matching `specval approve` | `specval approve` needs an override when the report has fail findings, so ticking every fail item in the UI records the override reason "all N fail item(s) reviewed and ticked in the web UI" | The approval stays attributable and visible in `manual_fixes`, exactly as on the command line. The button is enabled by `static/review.js`; the server applies specval's rules either way. | implemented |
| WB11 | — | Approval compares the SHA-256 of the Markdown **body** (new report field `markdown_body_sha256`), not of the whole file | The UI edits `title` and `spec_version` in the front matter; that must not invalidate a validation. Changes to the text still do. Added to `specval` first, with a test. | implemented |
| WB12 | Approved documents are copied into `data/library/<doc_id>/` | `data/library/<doc_id>/<version>/` with the Markdown, media, sections, source, validation report and `meta.json` (job, approver, date, title, version, override); the Library page shows the latest version of each | Re-approving an id adds a version instead of overwriting (W-a's proposed default). | implemented |
| WB13 | Download "a zip with media" | The zip holds `<doc_id>/<doc_id>.md`, `media/`, `sections/` (if split) and the validation report (`validation.json`, `validation.md`); the source is a separate download | Sources can be large and are available on their own. | implemented |
| WB14 | DOCX sources shown as Pandoc HTML (open question W-b) | `pandoc --sandbox -f docx -t html`, sanitized with nh3, display only; plus a download link | W-b's proposed default: rendering a preview is not conversion logic. | decided |
| WB15 | Review queue items link to the Markdown line and source page | Markdown lines link to the Raw tab (`#L<n>`, each line has an anchor); PDF pages link to the source with `#page=<n>` (opened by the browser's PDF viewer); other locations are shown as text | HTML and DOCX locations are element paths, not pages. | implemented |

## Future scope (to be checked later)

| # | Topic | Detail | Possible approach |
| --- | --- | --- | --- |
| FS-1 | V1 table count for PDFs is misleading | pdfplumber, the second PDF reader, counts diagrams with drawn boxes as tables and cannot see tables without ruled lines. CEA-608-E: it finds 4 "tables" in Figures 2, 7 and 8 (PDF pages 26, 101, 104; all cells empty) and misses the two abbreviation lists (pages 18–19), so V1 reports 72 against the Markdown's 70 and fails although no table is missing (checked by the user, 2026-11-19). V1 also gives only the totals, not where they differ. | Report the pages where the counts differ; ignore pdfplumber tables whose cells are all empty or that lie on a Docling picture; reconsider in phase 6 together with V5, which pairs tables by page. |
| FS-2 | V6 lists TTML2's syntax boxes (`count = xsd:integer`) as "XML not well-formed (the source has the same problem)" | They start with `<` and contain closing tags, so they are parsed; the findings are info only and do not change the status, but they make the report long. | Recognize the W3C "syntax" box (class `syntax` on the source table) and skip XML parsing for it. |
| FS-3 | No real DOCX spec has been converted yet | Phase 8 asks for one; `input/` has none. The DOCX path is tested with the synthetic fixture only. | Convert and validate a real DOCX spec when one is available; check the DOCX code-style question (phase 5 open question) at the same time. |
| FS-4 | Editing a title after approval does not update the library copy | The library holds what was approved; later `title` / `spec_version` edits change the job's Markdown only. | Re-approve to store a new version, or let metadata edits also update the latest library version's `meta.json`. |

## Web application still open (affect phases 9 to 11)

| # | Topic | Proposed default | Status |
| --- | --- | --- | --- |
| W-a | Document ids are unique per job only; they clash in `/api/documents/{doc_id}` and `data/library/<doc_id>/` | API uses `<job_id>/<slug>`; re-approving a slug stores a new version in the library | decided (WB5) |
| W-b | DOCX preview needs Pandoc inside `specweb` | Treated as display only, not conversion logic | decided (WB14) |
