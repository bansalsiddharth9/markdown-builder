"""Command line interface (design: CLI).

Exit codes: 0 all ok; 1 at least one document failed; 2 bad configuration
or missing system dependency.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Annotated

import typer
from common.manifest import ManifestError

from specconv import __version__, deps, models, routing
from specconv import fetch as fetching
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
BlockOpt = Annotated[
    bool,
    typer.Option(
        "--block-private-addresses",
        help="Refuse private, loopback and link-local addresses (also after redirects).",
    ),
]
AllowHostOpt = Annotated[
    list[str] | None,
    typer.Option("--allow-host", help="Host allowed despite --block-private-addresses."),
]


def _with_guard(cfg: SpecconvConfig, block: bool, allow: list[str] | None) -> SpecconvConfig:
    """Add the command-line address-blocking options to the config (deviation F17).

    --block-private-addresses can only turn blocking on, never off; --allow-host
    adds hosts to the allow list from the config file.
    """
    if not block and not allow:
        return cfg
    fetch_cfg = dataclasses.replace(
        cfg.fetch,
        block_private_addresses=block or cfg.fetch.block_private_addresses,
        allow_hosts=tuple(cfg.fetch.allow_hosts) + tuple(allow or ()),
    )
    return dataclasses.replace(cfg, fetch=fetch_cfg)


def _fail_config(message: str) -> typer.Exit:
    """Print the error and return an exit-2 exception for the caller to raise."""
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(EXIT_CONFIG)


def _load(config: Path | None, sources: Path | None) -> tuple[SpecconvConfig, list[Source]]:
    """Load specconv.yaml and (if given) sources.yaml; a config error exits with 2."""
    try:
        cfg = load_config(config)
        srcs = load_sources(sources) if sources is not None else []
    except ConfigError as exc:
        raise _fail_config(str(exc)) from exc
    return cfg, srcs


def _check_dependencies(
    cfg: SpecconvConfig,
    need_pandoc: bool,
    need_docling: bool,
    need_chromium: bool,
    need_models: bool = False,
) -> None:
    """Report every missing dependency, then exit 2 if there was any."""
    # Before the Chromium check, so it looks in the configured folder (deviation E3).
    deps.configure_browsers_path(cfg.fetch.browsers_path)
    problems = deps.check_python()
    if need_pandoc:
        problems += deps.check_pandoc()
    if need_docling:
        problems += deps.check_python_package("docling")
    if need_chromium:
        problems += deps.check_chromium()
    if need_models:
        problems += deps.check_models(cfg.pdf.models_path)
    if problems:
        typer.echo("error: missing system dependencies:", err=True)
        for problem in problems:
            typer.echo(f"  {problem}", err=True)
        raise typer.Exit(EXIT_CONFIG)


def _only_known(only: str | None, srcs: list[Source], sources_path: Path | None) -> None:
    """Exit 2 when --only names an id that is not in sources.yaml."""
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
    block_private_addresses: BlockOpt = False,
    allow_host: AllowHostOpt = None,
) -> None:
    """Download the web pages listed in sources.yaml into the fetch cache."""
    cfg, srcs = _load(config, sources)
    cfg = _with_guard(cfg, block_private_addresses, allow_host)
    _only_known(only, srcs, sources)
    selected = [s for s in srcs if only is None or s.id == only]
    _check_dependencies(
        cfg,
        need_pandoc=False,
        need_docling=False,
        # Chromium only for browser sources that will really be downloaded (F12).
        need_chromium=any(
            s.render == "browser" and (refetch or fetching.load_cached(work, s) is None)
            for s in selected
        ),
    )
    failed: list[str] = []
    with run_logging(work / f"fetch-{run_timestamp()}.log", verbose) as log:
        log.info("specconv %s fetch: %d source(s), refetch=%s", __version__, len(selected), refetch)
        log.debug("tool versions: %s", deps.tool_versions())
        for source in selected:
            try:
                outcome = fetching.fetch_source(source, cfg.fetch, work, refetch=refetch)
            except fetching.FetchError as exc:
                failed.append(source.id)
                # A failed fetch never touches the cache, so say if an older copy is there (F4).
                kept = fetching.load_cached(work, source)
                log.error(
                    "%s: fetch failed: %s%s",
                    source.id,
                    exc,
                    f"; the cached copy from {kept.record.fetched_at} was kept" if kept else "",
                )
                continue
            record = outcome.record
            if outcome.from_cache:
                log.info(
                    "%s: cached (fetched %s; use --refetch to download again)",
                    source.id,
                    record.fetched_at,
                )
            else:
                log.info(
                    "%s: fetched %s (%s, HTTP %d, %d bytes%s) -> %s",
                    source.id,
                    record.url,
                    record.render,
                    record.status,
                    record.bytes,
                    f", final URL {record.final_url}" if record.final_url != record.url else "",
                    outcome.html_path,
                )
        log.info(
            "done: %d fetched or cached, %d failed%s",
            len(selected) - len(failed),
            len(failed),
            f" ({', '.join(failed)})" if failed else "",
        )
    raise typer.Exit(EXIT_FAILED if failed else EXIT_OK)


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
    block_private_addresses: BlockOpt = False,
    allow_host: AllowHostOpt = None,
) -> None:
    """Convert an input folder and/or the web pages in sources.yaml to Markdown.

    Unchanged documents are skipped unless --force. With --only, other documents'
    manifest entries are kept; without it the manifest lists exactly this run's inputs.
    """
    if input is None and sources is None:
        raise _fail_config("give --input, --sources or both")
    if input is not None and not input.is_dir():
        raise _fail_config(f"--input {input}: not a folder")
    cfg, srcs = _load(config, sources)
    cfg = _with_guard(cfg, block_private_addresses, allow_host)
    try:
        previous = manifest_io.load_previous(output)
    except ManifestError as exc:
        raise _fail_config(f"{manifest_io.manifest_path(output)}: {exc}") from exc
    # Existing output folders are reserved, so a new file never gets the id (and folder)
    # of an old document that is no longer in the manifest (deviation P6).
    existing_folders = (
        {p.name for p in output.iterdir() if p.is_dir() and not p.name.startswith(".")}
        if output.is_dir()
        else set()
    )
    scan = routing.scan(
        input,
        srcs,
        # For local files the manifest's "source" is the path relative to the input folder.
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
        # Chromium only for browser sources that are not in the fetch cache yet.
        need_chromium=any(
            d.source is not None
            and d.source.render == "browser"
            and fetching.load_cached(work, d.source) is None
            for d in docs
        ),
        need_models=any(d.source_type == "pdf" for d in docs),
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
        pipeline = Pipeline(cfg, output, work, previous=previous, force=force, split=split)
        result = pipeline.run(docs, full_run=only is None)
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
    """Check that Pandoc, Docling, its models and Chromium are installed; print versions."""
    cfg, _ = _load(config, None)
    _check_dependencies(
        cfg, need_pandoc=True, need_docling=True, need_chromium=True, need_models=True
    )
    for name, ver in deps.tool_versions().items():
        typer.echo(f"{name} {ver}")
    for model in models.PINNED_MODELS:
        typer.echo(f"model {model.repo_id} {model.revision[:12]}")


@app.command("download-models")
def download_models(
    config: ConfigOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Download again even if present.")] = False,
) -> None:
    """Download the pinned Docling models into pdf.models_path (needs network, once)."""
    cfg, _ = _load(config, None)
    target = cfg.pdf.models_path
    todo = models.PINNED_MODELS if force else models.missing_models(target)
    if not todo:
        typer.echo(f"all models present in {target}")
        return
    try:
        done = models.download_models(target, force=force)
    except Exception as exc:  # network or Hugging Face errors
        typer.echo(f"error: download failed: {exc}", err=True)
        raise typer.Exit(EXIT_FAILED) from exc
    for model in done:
        typer.echo(f"downloaded {model.repo_id} @ {model.revision[:12]} into {target}")


def main() -> None:
    """Run the Typer app (console script and ``python -m specconv``)."""
    app()
