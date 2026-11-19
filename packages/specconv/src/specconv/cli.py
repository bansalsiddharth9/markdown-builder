"""Command line interface (design: CLI).

Exit codes: 0 all ok; 1 at least one document failed; 2 bad configuration
or missing system dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from common.manifest import ManifestError

from specconv import __version__, deps, routing
from specconv import manifest as manifest_io
from specconv.config import ConfigError, Source, SpecconvConfig, load_config, load_sources
from specconv.pipeline import Pipeline
from specconv.runlog import run_logging, run_timestamp

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2

app = typer.Typer(
    name="specconv",
    help="Convert caption specs (PDF, HTML, DOCX, web pages) to Markdown.",
    no_args_is_help=True,
    add_completion=False,
)

ConfigOpt = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help="specconv.yaml (default: config/specconv.yaml if present, else built-in defaults).",
    ),
]
SourcesOpt = Annotated[
    Path | None, typer.Option("--sources", help="sources.yaml listing web pages.")
]
WorkOpt = Annotated[Path, typer.Option("--work", help="Folder for fetched pages and debug files.")]
OnlyOpt = Annotated[
    str | None, typer.Option("--only", help="Process only the document with this id.")
]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Also show debug messages.")]


def _fail_config(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(EXIT_CONFIG)


def _load(config: Path | None, sources: Path | None) -> tuple[SpecconvConfig, list[Source]]:
    try:
        cfg = load_config(config)
        srcs = load_sources(sources) if sources is not None else []
    except ConfigError as exc:
        raise _fail_config(str(exc)) from exc
    return cfg, srcs


def _check_dependencies(
    cfg: SpecconvConfig, need_pandoc: bool, need_docling: bool, need_chromium: bool
) -> None:
    """Report every missing dependency, then exit 2 if there was any."""
    deps.configure_browsers_path(cfg.fetch.browsers_path)
    problems = deps.check_python()
    if need_pandoc:
        problems += deps.check_pandoc()
    if need_docling:
        problems += deps.check_python_package("docling")
    if need_chromium:
        problems += deps.check_chromium()
    if problems:
        typer.echo("error: missing system dependencies:", err=True)
        for problem in problems:
            typer.echo(f"  {problem}", err=True)
        raise typer.Exit(EXIT_CONFIG)


def _only_known(only: str | None, srcs: list[Source], sources_path: Path | None) -> None:
    if only is not None and sources_path is not None and only not in {s.id for s in srcs}:
        raise _fail_config(f"--only {only!r}: no source with that id in {sources_path}")


@app.command()
def fetch(
    sources: Annotated[Path, typer.Option("--sources", help="sources.yaml listing web pages.")],
    config: ConfigOpt = None,
    work: WorkOpt = Path("work"),
    refetch: Annotated[
        bool, typer.Option("--refetch", help="Download again even if cached.")
    ] = False,
    only: OnlyOpt = None,
    verbose: VerboseOpt = False,
) -> None:
    """Download the web pages listed in sources.yaml into the fetch cache."""
    cfg, srcs = _load(config, sources)
    _only_known(only, srcs, sources)
    selected = [s for s in srcs if only is None or s.id == only]
    _check_dependencies(
        cfg,
        need_pandoc=False,
        need_docling=False,
        need_chromium=any(s.render == "browser" for s in selected),
    )
    with run_logging(work / f"fetch-{run_timestamp()}.log", verbose) as log:
        log.info("specconv %s fetch: %d source(s), refetch=%s", __version__, len(selected), refetch)
        log.debug("tool versions: %s", deps.tool_versions())
        log.error("fetch is not implemented yet (build phase 4)")
    raise typer.Exit(EXIT_FAILED)


@app.command()
def convert(
    input: Annotated[
        Path | None, typer.Option("--input", help="Folder of PDF, HTML and DOCX files.")
    ] = None,
    sources: SourcesOpt = None,
    output: Annotated[Path, typer.Option("--output", help="Output folder.")] = Path("output"),
    config: ConfigOpt = None,
    work: WorkOpt = Path("work"),
    split: Annotated[
        int | None,
        typer.Option(
            "--split", min=1, max=6, help="Also write one file per heading at this level."
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Re-convert unchanged documents too.")
    ] = False,
    only: OnlyOpt = None,
    verbose: VerboseOpt = False,
) -> None:
    """Convert an input folder and/or the web pages in sources.yaml to Markdown."""
    if input is None and sources is None:
        raise _fail_config("give --input, --sources or both")
    if input is not None and not input.is_dir():
        raise _fail_config(f"--input {input}: not a folder")
    if split is not None:
        raise _fail_config("--split is not implemented yet (build phase 7)")
    cfg, srcs = _load(config, sources)
    try:
        previous = manifest_io.load_previous(output)
    except ManifestError as exc:
        raise _fail_config(f"{manifest_io.manifest_path(output)}: {exc}") from exc
    existing_folders = (
        {p.name for p in output.iterdir() if p.is_dir() and not p.name.startswith(".")}
        if output.is_dir()
        else set()
    )
    scan = routing.scan(
        input,
        srcs,
        previous_ids={e.source: e.id for e in previous.entries if e.source_type != "web"},
        reserved=existing_folders,
    )
    docs = scan.docs
    if only is not None:
        docs = [d for d in docs if d.id == only]
        if not docs:
            raise _fail_config(
                f"--only {only!r}: no input or source has that id "
                f"(ids: {', '.join(d.id for d in scan.docs) or 'none'})"
            )
    _check_dependencies(
        cfg,
        need_pandoc=True,
        need_docling=True,
        need_chromium=any(d.source is not None and d.source.render == "browser" for d in docs),
    )

    with run_logging(output / f"run-{run_timestamp()}.log", verbose) as log:
        log.info(
            "specconv %s convert: input=%s sources=%d output=%s force=%s only=%s",
            __version__,
            input,
            len(srcs),
            output,
            force,
            only,
        )
        log.info("tool versions: %s", deps.tool_versions())
        for rel in scan.skipped:
            log.info("skipped (not a PDF, HTML or DOCX file): %s", rel)
        result = Pipeline(cfg, output, work).run(docs, previous, full_run=only is None)
        counts: dict[str, int] = {}
        for entry in result.processed:
            counts[entry.status] = counts.get(entry.status, 0) + 1
        log.info(
            "done: %s; manifest: %s",
            ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no documents",
            manifest_io.manifest_path(output),
        )
        if result.failed:
            log.error(
                "%d document(s) failed: %s",
                len(result.failed),
                ", ".join(e.id for e in result.failed),
            )
    raise typer.Exit(EXIT_FAILED if result.failed else EXIT_OK)


@app.command("check-deps")
def check_deps(config: ConfigOpt = None) -> None:
    """Check that Pandoc, Docling and Chromium are installed, and print their versions."""
    cfg, _ = _load(config, None)
    _check_dependencies(cfg, need_pandoc=True, need_docling=True, need_chromium=True)
    for name, ver in deps.tool_versions().items():
        typer.echo(f"{name} {ver}")


def main() -> None:
    app()
