"""Command line interface (design: CLI).

Exit codes: 0 all ok; 1 at least one document failed; 2 bad configuration
or missing system dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from specconv import __version__, deps
from specconv.config import ConfigError, Source, SpecconvConfig, load_config, load_sources
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

ConfigOpt = Annotated[Path | None, typer.Option(
    "--config", help="specconv.yaml (default: config/specconv.yaml if present, else built-in defaults).")]
SourcesOpt = Annotated[Path | None, typer.Option("--sources", help="sources.yaml listing web pages.")]
WorkOpt = Annotated[Path, typer.Option("--work", help="Folder for fetched pages and debug files.")]
OnlyOpt = Annotated[str | None, typer.Option("--only", help="Process only the document with this id.")]
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


def _check_dependencies(cfg: SpecconvConfig, need_pandoc: bool, need_docling: bool,
                        need_chromium: bool) -> None:
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
    refetch: Annotated[bool, typer.Option("--refetch", help="Download again even if cached.")] = False,
    only: OnlyOpt = None,
    verbose: VerboseOpt = False,
) -> None:
    """Download the web pages listed in sources.yaml into the fetch cache."""
    cfg, srcs = _load(config, sources)
    _only_known(only, srcs, sources)
    selected = [s for s in srcs if only is None or s.id == only]
    _check_dependencies(cfg, need_pandoc=False, need_docling=False,
                        need_chromium=any(s.render == "browser" for s in selected))
    with run_logging(work / f"fetch-{run_timestamp()}.log", verbose) as log:
        log.info("specconv %s fetch: %d source(s), refetch=%s", __version__, len(selected), refetch)
        log.debug("tool versions: %s", deps.tool_versions())
        log.error("fetch is not implemented yet (build phase 4)")
    raise typer.Exit(EXIT_FAILED)


@app.command()
def convert(
    input: Annotated[Path | None, typer.Option("--input", help="Folder of PDF, HTML and DOCX files.")] = None,
    sources: SourcesOpt = None,
    output: Annotated[Path, typer.Option("--output", help="Output folder.")] = Path("output"),
    config: ConfigOpt = None,
    work: WorkOpt = Path("work"),
    split: Annotated[int | None, typer.Option(
        "--split", min=1, max=6, help="Also write one file per heading at this level.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Re-convert unchanged documents too.")] = False,
    only: OnlyOpt = None,
    verbose: VerboseOpt = False,
) -> None:
    """Convert an input folder and/or the web pages in sources.yaml to Markdown."""
    if input is None and sources is None:
        raise _fail_config("give --input, --sources or both")
    if input is not None and not input.is_dir():
        raise _fail_config(f"--input {input}: not a folder")
    cfg, srcs = _load(config, sources)
    _only_known(only, srcs, None)  # --only may also name a local file's id
    _check_dependencies(cfg, need_pandoc=True, need_docling=True,
                        need_chromium=any(s.render == "browser" for s in srcs))
    with run_logging(output / f"run-{run_timestamp()}.log", verbose) as log:
        log.info("specconv %s convert: input=%s sources=%d output=%s split=%s force=%s",
                 __version__, input, len(srcs), output, split, force)
        log.info("tool versions: %s", deps.tool_versions())
        log.error("convert is not implemented yet (build phases 2-3)")
    raise typer.Exit(EXIT_FAILED)


@app.command("check-deps")
def check_deps(config: ConfigOpt = None) -> None:
    """Check that Pandoc, Docling and Chromium are installed, and print their versions."""
    cfg, _ = _load(config, None)
    _check_dependencies(cfg, need_pandoc=True, need_docling=True, need_chromium=True)
    for name, ver in deps.tool_versions().items():
        typer.echo(f"{name} {ver}")


def main() -> None:
    app()
