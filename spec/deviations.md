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
| E8 | — | Docling requests its layout model (`docling-project/docling-layout-heron`) at revision `main`, which is not pinned | To do in phase 3: pin both Docling models to fixed revisions and store them inside the project, so conversions stay reproducible. | open |

## Command line

| # | Design says | Implementation | Why | Status |
| --- | --- | --- | --- | --- |
| C1 | `specconv fetch`, `specconv convert` | Added `specconv check-deps`: runs all dependency checks and prints tool versions | Quick way to verify an installation. | implemented |
| C2 | `specconv` options as listed | Added `--config` (default `config/specconv.yaml` if present, else built-in defaults), `--work` (default `work/`) and `--verbose` | The design names `work/` and config files but no options for them; `specval` already had `--config`. | implemented |
| C3 | `specval run --output --doc --config` | Added `--acceptance` (default `config/acceptance.yaml` if present), `--validation` (default `validation/`) and `--verbose` | Same reason as C2. | implemented |
| C4 | `specval approve ID --output [--override]` | Added `--by NAME` | The web UI records who approved; the CLI should be able to as well. | implemented |
| C5 | `convert --input input/ --sources sources.yaml` | Both are optional, but at least one is required | Allows converting only files or only web pages. | implemented |
| C6 | A log file per run | `convert` → `output/run-<timestamp>.log`; `fetch` → `work/fetch-<timestamp>.log`; `specval run` → `validation/run-<timestamp>.log` | The design only names the `convert` log location. | implemented |
| C7 | — | Commands not built yet exit with code 1 and say "not implemented yet (phase N)" | Design principle 3: fail loudly, never pretend to succeed. Removed as each phase lands. | implemented |

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

## Web application still open (affect phases 9 to 11)

| # | Topic | Proposed default | Status |
| --- | --- | --- | --- |
| W-a | Document ids are unique per job only; they clash in `/api/documents/{doc_id}` and `data/library/<doc_id>/` | API uses `<job_id>/<slug>`; re-approving a slug stores a new version in the library | open |
| W-b | DOCX preview needs Pandoc inside `specweb` | Treated as display only, not conversion logic | open |
