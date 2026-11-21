# CLAUDE.md

Guidance for Claude Code (and any developer) working in this repository.

## What this project is

It turns caption and subtitle specifications (PDF, DOCX, HTML, web pages) into a Markdown
reference library, and checks each result against its source so a person knows what to review.

| Package | Role |
| --- | --- |
| `packages/specconv` | Converter CLI. PDFs go through Docling, everything else through Pandoc. |
| `packages/specval` | Validator CLI. Nine checks (V1–V9), a review queue, approval. |
| `packages/specweb` | Web UI (FastAPI, Jinja2, HTMX, SQLite, Huey) on top of the two CLIs. |
| `packages/common` | Formats both tools share: `manifest.json` and Markdown front matter. |

- **Design:** `spec/Caption Spec to Markdown Converter — Design.md`.
- **Deviations:** every place the build differs from the design is a row in
  `spec/deviations.md` with an ID (I1, Q4, F16, WB16, HP1…). Code comments cite these IDs.
- **User guide:** `README.md`.

## How the pieces fit

```text
input/ + config/sources.yaml
   │  specconv convert   (route each file: pdf→Docling, html/docx→Pandoc; HTML is cleaned
   │                      first in htmlprep.py; post-processing in postprocess.py)
   ▼
output/<id>/<id>.md, media/, source.<ext>, conversion.json; output/manifest.json
   │  specval run        (re-reads the SOURCE independently, compares, writes reports)
   ▼
validation/<id>.md|.json, summary.csv
   │  specval approve    (writes `validated` into the front matter)
```

- **The validator must stay independent of the converter.** They never import each other
  (`tests/test_independence.py`), and they read the same source with *different* libraries.
  For HTML, the converter uses lxml and the validator uses html5lib (HP1). Otherwise a reading
  bug would hide itself.
- **specweb** has two processes: `serve` (the pages) and `worker` (takes jobs from the Huey
  queue). The worker runs `specconv` and `specval` as **subprocesses** for every job, so changes
  to those packages apply without restarting it. Each job lives in `data/jobs/<job-id>/`
  (`input/ work/ output/ validation/ logs/`). Approved documents are copied to
  `data/library/`, which is never deleted with a job.
- `RECIPE_VERSION` in `specconv/pipeline.py` is part of the "is this output stale?" check.
  Bump it whenever a change alters conversion output, so documents are converted again.

## Commands

```bash
uv sync --all-packages                                   # environment (Python 3.12, pinned)
uv run pytest                                            # all tests, about 3 minutes
uv run pytest -q tests/specweb                           # one package
uv run ruff check . && uv run ruff format --check packages tests scripts
uv run python scripts/licenses.py --check                # after adding/updating a dependency

uv run specconv convert --input input --sources config/sources.yaml
uv run specval run [--doc ID]
uv run specweb serve  --config deploy/specweb.yaml --host 0.0.0.0 --port 8000
uv run specweb worker --config deploy/specweb.yaml
```

- Expected test result: everything passes except **1 xfail**
  (`test_code_block_line_breaks_kept`, a known Docling limitation).
- Browser tests and `render: browser` need `PLAYWRIGHT_BROWSERS_PATH=.playwright`. The browsers
  are installed there, not in `~/.cache`.

## Rules for working here

1. **Ask before implementing.** Answer questions first. Do not change code, install, or
   restart anything until the user explicitly says to go ahead. Answering a clarifying
   question is not a go-ahead.
2. **Test first (TDD).** Write the failing test, watch it fail for the right reason, then write
   the code. Run the full suite before calling something done.
3. **Record deviations.** Any behaviour that differs from the design gets a row in
   `spec/deviations.md` (next free ID in the current section; say why and who decided).
4. **Commit only when asked.** Never add a `Co-Authored-By` line or any other mention of
   Claude to commit messages or PR descriptions: the user is the only author.
5. **Never commit anything from `input/`**: the specs are copyrighted (git-ignored). The same
   goes for `output/`, `validation/`, `work/`, `data/` and `models/`.
6. **Plain language.** The user prefers short, simple explanations.
7. **Comments:** every module, class and function has a docstring. Comment the *why*: tool
   workarounds, security reasons, thresholds, deviation IDs. Don't comment obvious lines.

## Pitfalls we have hit

- **`ruff format .` rewrites the Python examples inside the design document.** Format only
  `packages tests scripts`.
- **`pkill -f "specweb serve"` also kills your own shell** when that shell's command line
  contains the same words. Use a bracket pattern (`pkill -u media -f "[s]pecweb serve"`) and
  start the server in a separate command, with `setsid nohup … & disown`.
- **Templates and static files reload live** (Jinja re-reads changed templates; static files
  are served from disk). Python changes to
  `specweb` need a server restart. Restarting can drop a VS Code port forward; the server is
  still reachable at `http://<host>:8000`.
- **Restarting the worker ends running jobs.** At start it marks jobs left in
  fetching/converting/validating as `error` (HP6). Check the Jobs page first, and never run two
  workers on the same data folder.
- **Strict CSP** (`script-src 'self'; style-src 'self'`): no inline `<script>`, `style=` or
  `onclick=`. Put JS in `specweb/static/*.js` and CSS in `static/style.css`.
- **Tests check the rendered HTML.** In templates, use Jinja comments `{# #}`, not `<!-- -->`.
- **Python's `html.parser` mis-nests unclosed `<td>`.** Never use it for source HTML; use lxml
  (converter) or html5lib (validator).
- **Tests never touch the network** (a fixture blocks it). Web tests use a local HTTP server.
  The specweb tests use a fake runner instead of Docling and Pandoc.
- **`.doc` files are not supported.** Convert them to `.docx` with LibreOffice
  (`/usr/bin/soffice --headless --convert-to docx`).
