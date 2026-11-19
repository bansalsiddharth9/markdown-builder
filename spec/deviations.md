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
| C7 | — | Commands not built yet exit with code 1 and say "not implemented yet (phase N)" | Design principle 3: fail loudly, never pretend to succeed. Removed as each phase lands (`convert`: phase 2; `fetch`: phase 4; `--split` exits 2 until phase 7). | implemented |

## Data formats

| # | Design says | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| D1 | Manifest entry fields: id, source, route, converter and version, status, flags, warnings, duration | Also: `source_type`, `source_sha256`, `tool_versions` (every tool involved), `options`, `options_hash`, `error`; file-level `schema_version`, `generator`, `generated_at` | `source_sha256` is needed for incremental runs; `options_hash` makes a changed option (e.g. `--split`) trigger re-conversion, not only a changed source or converter version; `tool_versions` follows "record tool versions in every manifest entry". | implemented |
| D2 | `validated` set to "today's date and the validator version" | `validated: {date, validator, by}` mapping | Records who approved (see C4); one structured value instead of a free string. | implemented |
| D3 | — | Config and manifest loading is strict: unknown keys, wrong types and out-of-range values are errors (exit code 2) | A typo in a config file must not be silently ignored. | implemented |
| D4 | `acceptance.yaml` `expect` values | `expect` must be a quoted string; unquoted numbers are rejected with a message | YAML reads unquoted `0x25` as 37 and `1.10` as 1.1, which would silently check for the wrong text. | implemented |
| D5 | `sources.yaml` `id` names the output folder | Ids may only contain letters, digits, `.`, `_`, `-` and must start with a letter or digit; `drop_selectors` is only allowed with `clean: main-content` | Ids become folder names, so `../x` must be impossible; a selector list that is never applied is a config mistake. | implemented |

## Validator rules still open (affect phases 5 and 6)

| # | Topic | Proposed default | Status |
| --- | --- | --- | --- |
| V-a | V4 hex pattern `[0-9A-Fa-f]{2,4}h` also matches words like "each"; 4-digit hex matches years like "2020" | Whole words only, and require at least one digit | open |
| V-b | V4 "shall not" also contains "shall" | Longest match wins; counted once, as "shall not" | open |
| V-c | V4 "may" also matches the month | Keep as designed; report in the phase summary | open |
| V-d | PDF running headers, footers and page numbers inflate V3/V4 source counts | Remove lines that repeat on more than half the pages before comparing | open |
| V-e | V2 for PDFs (no source headings) | Check only that Markdown numbering is in sequence | open |
| V-f | DOCX code blocks: which styles count as code | Style name containing "code" or "source", or a monospace font (Courier, Consolas, Menlo, Source Code), configurable | open |
| V-g | V1/V6 for HTML after Q14/Q15: one-cell tables no longer exist in the output, and a bare `<pre>` is code | V1 skips source tables with exactly one cell; V6 counts every `<pre>` as a code block | open |

## Web application still open (affect phases 9 to 11)

| # | Topic | Proposed default | Status |
| --- | --- | --- | --- |
| W-a | Document ids are unique per job only; they clash in `/api/documents/{doc_id}` and `data/library/<doc_id>/` | API uses `<job_id>/<slug>`; re-approving a slug stores a new version in the library | open |
| W-b | DOCX preview needs Pandoc inside `specweb` | Treated as display only, not conversion logic | open |
