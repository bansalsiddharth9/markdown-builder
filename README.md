# Caption spec converter

Two command-line tools that turn caption specifications into a Markdown reference library and check the result:

- **`specconv`** converts PDF, HTML, DOCX, DOC and web pages to Markdown (Docling for PDFs, Pandoc for the rest; a `.doc` is first saved as `.docx` by LibreOffice).
- **`specval`** checks each converted file against its source on its own terms and writes a report saying what passed and what a person must check.

A web UI, **`specweb`**, puts both behind a browser page for the internal network (see [Web app](#web-app-specweb)).

The design is in [`spec/Caption Spec to Markdown Converter — Design.md`](spec/Caption%20Spec%20to%20Markdown%20Converter%20—%20Design.md); every place the build differs from it is listed in [`spec/deviations.md`](spec/deviations.md).

## Setup (once per machine)

Linux x86_64, about 3 GB of disk for the environment and models, and 6 GB of free memory for large PDFs.

1. Install [uv](https://docs.astral.sh/uv/), then create the environment (Python 3.12, all versions pinned in `uv.lock`):

   ```bash
   uv sync --all-packages
   ```

2. Install Pandoc 3.14 into the environment:

   ```bash
   curl -L -o /tmp/pandoc.tar.gz \
     https://github.com/jgm/pandoc/releases/download/3.14/pandoc-3.14-linux-amd64.tar.gz
   tar -xzf /tmp/pandoc.tar.gz -C /tmp && cp /tmp/pandoc-3.14/bin/pandoc .venv/bin/
   ```

3. Install Chromium (only needed for web pages with `render: browser`) into `.playwright/`:

   ```bash
   PLAYWRIGHT_BROWSERS_PATH=.playwright uv run playwright install chromium
   ```

4. Download the pinned Docling models into `models/` (about 500 MB, needs the network once):

   ```bash
   uv run specconv download-models
   ```

5. For legacy Word `.doc` files only: install LibreOffice (saves them as `.docx`) and catdoc (the validator reads their text with it):

   ```bash
   sudo apt install libreoffice-writer catdoc
   ```

6. Check everything:

   ```bash
   uv run specconv check-deps
   ```

Conversion never uses the network after this; only `specconv fetch` does.

## Daily use

Put files in `input/` (PDF, HTML, HTM, DOCX, DOC; a page saved with "Save page as, complete" goes in with its `_files` folder) and list web pages in `config/sources.yaml`. Sub-folders are fine and are mirrored: `input/SCC/My Spec.pdf` becomes `output/SCC/my-spec/my-spec.md` and `validation/SCC/my-spec.md`, and its id is `SCC/my-spec`. Then:

```bash
uv run specconv convert --input input --sources config/sources.yaml   # converts into output/
uv run specval run                                                    # checks into validation/
```

Open `validation/<id>.md` for each document. It has a results table for the nine checks and a **review queue**, in the order to work through it:

1. failures, each with the Markdown line and the source page or section;
2. tables that are always reviewed (captions or headers with "code", "command", "control", "character set");
3. warnings;
4. a fixed random sample of pages or sections to compare by eye.

When a document is fine:

```bash
uv run specval approve <id> --by "Your Name"
uv run specval approve <id> --by "Your Name" --override "reason"   # despite fail findings
```

Approval writes `validated` into the document's front matter. It is refused if the Markdown changed since the last `specval run`.

### Commands

| Command | What it does |
| --- | --- |
| `specconv convert --input DIR [--sources FILE] [--split N] [--force] [--only ID]` | Convert; unchanged documents are skipped, `--force` converts everything again; `--split 2` also writes one file per section into `output/<id>/sections/` |
| `specconv fetch --sources FILE [--refetch] [--only ID]` | Download web pages and their images into `work/fetched/` (convert does this for pages not fetched yet) |
| `specconv check-deps` / `download-models` | Check the installation / get the Docling models |
| `specval run [--doc ID] [--acceptance FILE]` | Validate all documents, or one |
| `specval approve ID [--override REASON] [--by NAME]` | Mark a reviewed document as validated |

Exit codes: 0 all ok, 1 at least one document failed, 2 bad configuration or missing dependency. Every run writes a log (`output/run-*.log`, `work/fetch-*.log`, `validation/run-*.log`).

### Output

```text
output/
  manifest.json            one entry per document: route, versions, status, warnings
  <id>/<name>.md           the Markdown, with front matter (title, spec_version: fill in by hand)
  <id>/source.<ext>        exact copy of the input
  <id>/media/              images (and unmapped-table-p<page>-<n>.png: tables whose symbols the PDF cannot name)
  <id>/conversion.json     tool, options, warnings, what was dropped (e.g. PDF page headers)
  <id>/libreoffice.docx    only for a .doc: the copy LibreOffice made, which Pandoc read
  <id>/sections/           only with --split
validation/
  summary.csv              one row per document, status per check
  <id>.md, <id>.json       the report
```

The id mirrors the input folder: `input/SCC/My Spec.pdf` has the id `SCC/my-spec`, so its files are in `output/SCC/my-spec/` (with `my-spec.md`) and its report is `validation/SCC/my-spec.md`. A file directly in `input/`, and every web page from `sources.yaml`, has a plain id such as `ttml2`. Output made before this layout is moved into its new folder on the next run, with its front matter kept.

Edit `title`, `spec_version` and `manual_fixes` in the front matter freely: they are kept when a document is converted again (`validated` is reset if the text changed).

## Configuration

- `config/specconv.yaml`: PDF text-layer thresholds, HTML tags to strip, fetch timeout and retries, `self_urls` for saved pages without a "saved from" comment.
- `config/sources.yaml`: web pages (`id`, `url`, `render: static|browser`, `clean: none|main-content`).
- `config/specval.yaml`: coverage thresholds, PDF table tolerance, always-reviewed table words, sample size and seed, extra token patterns.
- `config/acceptance.yaml`: questions with an expected snippet per document (check Validation9); quote every `expect` value.

Unknown keys are errors, so a typo never goes unnoticed.

## Web app (`specweb`)

A browser UI on top of the two tools: upload files or paste URLs, follow the job, review each document side by side with its source, tick the review queue, approve, and download. Approved documents are kept in a library. There are no accounts: everyone who can reach the port can use every feature, so run it on the internal network only.

Run it locally (two processes, same settings file; jobs and the library go into `data/`):

```bash
uv run specweb serve  --config deploy/specweb.yaml --port 8000
uv run specweb worker --config deploy/specweb.yaml
```

Then open http://localhost:8000. `deploy/specweb.yaml` is the only settings file, for local runs and Docker alike; the Docker image keeps its data on the `/data` volume (`SPECWEB_DATA`).

Deploy with Docker Compose (web, worker and a Caddy proxy with HTTPS, sharing one data volume). First tell Caddy the addresses users will type: copy the template and fill in this server's name and IP (`hostname`, `hostname -I`):

```bash
cp deploy/.env.example deploy/.env
# edit deploy/.env, e.g.
#   SPECWEB_HOST=wks0019, 192.168.4.161
#   SPECWEB_DEFAULT_SNI=192.168.4.161
docker compose -f deploy/docker-compose.yml up -d --build
```

Caddy answers only the names and IPs in `SPECWEB_HOST`; anything else is refused. `SPECWEB_DEFAULT_SNI` makes `https://<IP>` work (browsers send no site name for an IP). `deploy/.env` belongs to one server and is git-ignored; without it, the site opens only as `https://localhost` on the server itself. Caddy makes its own certificate, so browsers warn until Caddy's root certificate is installed on them (or the Caddyfile uses the company's certificate). Changes to `deploy/.env` or the Caddyfile need only `docker compose -f deploy/docker-compose.yml up -d --force-recreate proxy`, no rebuild.

The image contains Pandoc, LibreOffice Writer and catdoc (for `.doc`), Chromium and the pinned Docling models. Give the worker about 8 GB of memory per PDF conversion; `worker_concurrency` in `deploy/specweb.yaml` sets how many run at once.

Protections (settings in `specweb.yaml`): at most 20 files of 100 MB per job, file contents must match their extension; URLs that point at private, loopback or link-local addresses are refused, also after redirects and for page images (`fetch_allowlist` permits named internal hosts); at most 5 waiting jobs per client and 50 in total; new jobs are refused when free disk is low; jobs are deleted after 30 days (approved documents stay in the library); rendered Markdown is sanitized and HTML sources are shown sandboxed without scripts.

## What to expect from PDFs

PDFs are converted with Docling, which is good but not perfect. Known limitations (details in `spec/deviations.md`): headings all come out at the same level, a code example that runs over a page break is split into two code blocks, and a code line at a page break can be taken for a page footer (everything dropped as header or footer is listed in `conversion.json`). The validator is built to catch these, so a PDF report usually has items to review. PDFs without a text layer are reported as `no_text_layer` and not converted (no OCR).

Page headers and footers are left out on both sides: the validator recognises lines that repeat on most pages (also only the odd or only the even ones) or as the first or last line of several pages, and a line the converter dropped as a header lists as "confirm it is one" instead of a missing sentence.

Docling joins the lines of a code example; the converter puts the line breaks and indentation back from the PDF. Text inside a drawing (labels, an XML example drawn as a figure) is written under its image as "Text in the figure", one line per text, without the drawing's layout.

Some PDFs draw symbols with a font that has no Unicode map (EBU Tech 3264's code-page tables): the PDF holds each symbol's picture, not which character it is, so the text shows `�` or wrong letters. Such a table gets a note and a picture of it from the PDF directly underneath; use the picture as the reference. The validator lists those pages as "compare by eye" warnings instead of counting their lines as missing text.

## Development

```bash
uv run pytest                      # all tests (about 2.5 minutes; the PDF tests run Docling)
uv run ruff check . && uv run ruff format --check packages tests scripts
uv run python scripts/licenses.py --check   # LICENSES.md is up to date and every license is allowed
```

Tests never use the internet (a fixture blocks it); web tests use a local HTTP server. The converter and validator never import each other (`tests/test_independence.py`); shared code lives in `packages/common`. Never commit specs from `input/`: they are copyrighted and git-ignored.
