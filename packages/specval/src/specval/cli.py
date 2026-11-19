"""Command line interface (design: CLI).

Exit codes: 0 all ok; 1 at least one document failed; 2 bad configuration
or missing system dependency.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Annotated

import typer
from common.manifest import Manifest, ManifestError, read_manifest

from specval import __version__, deps, runner
from specval.approve import ApproveError
from specval.approve import approve as approve_doc
from specval.config import ConfigError, load_acceptance, load_config
from specval.report import write_json, write_markdown, write_summary
from specval.runlog import run_logging, run_timestamp

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2

DEFAULT_ACCEPTANCE_PATH = Path("config/acceptance.yaml")

app = typer.Typer(
    name="specval",
    help="Validate specconv output against its sources and record approvals.",
    no_args_is_help=True,
    add_completion=False,
)

OutputOpt = Annotated[Path, typer.Option("--output", help="specconv output folder.")]
ValidationOpt = Annotated[Path, typer.Option("--validation", help="Folder for reports.")]


def _fail_config(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(EXIT_CONFIG)


def _check_dependencies() -> None:
    problems = deps.check_all()
    if problems:
        typer.echo("error: missing dependencies:", err=True)
        for problem in problems:
            typer.echo(f"  {problem}", err=True)
        raise typer.Exit(EXIT_CONFIG)


def _read_manifest(output: Path) -> Manifest:
    path = output / "manifest.json"
    try:
        return read_manifest(path)
    except FileNotFoundError as exc:
        raise _fail_config(f"{path}: not found; run 'specconv convert' first") from exc
    except ManifestError as exc:
        raise _fail_config(str(exc)) from exc


@app.command()
def run(
    output: OutputOpt = Path("output"),
    doc: Annotated[
        str | None, typer.Option("--doc", help="Validate only this document id.")
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="specval.yaml (default: config/specval.yaml if present, else built-in defaults).",
        ),
    ] = None,
    acceptance: Annotated[
        Path | None,
        typer.Option(
            "--acceptance", help="acceptance.yaml (default: config/acceptance.yaml if present)."
        ),
    ] = None,
    validation: ValidationOpt = Path("validation"),
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Also show debug messages.")
    ] = False,
) -> None:
    """Check every converted document and write reports to the validation folder."""
    try:
        cfg = load_config(config)
        if acceptance is None and DEFAULT_ACCEPTANCE_PATH.exists():
            acceptance = DEFAULT_ACCEPTANCE_PATH
        questions = load_acceptance(acceptance) if acceptance is not None else []
    except ConfigError as exc:
        raise _fail_config(str(exc)) from exc
    _check_dependencies()
    manifest = _read_manifest(output)
    if doc is not None and manifest.get(doc) is None:
        raise _fail_config(f"--doc {doc!r}: not in {output / 'manifest.json'}")

    entries = [e for e in manifest.entries if doc is None or e.id == doc]
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    reports = []
    with run_logging(validation / f"run-{run_timestamp()}.log", verbose) as log:
        log.info(
            "specval %s run: output=%s documents=%d acceptance questions=%d",
            __version__,
            output,
            len(entries),
            len(questions),
        )
        log.debug("config: %s", cfg)
        log.info("tool versions: %s", deps.tool_versions())
        known = {e.id for e in manifest.entries}
        for q in questions:
            if q.doc not in known:
                log.warning("acceptance question %r names unknown document %r", q.question, q.doc)
        for entry in entries:
            report = runner.validate_document(entry, output, cfg, now, questions=questions)
            reports.append(report)
            write_json(report, validation)
            write_markdown(report, validation)
            statuses = " ".join(
                f"{c.id}={c.status}" for c in report.checks if c.status != "not_run"
            )
            level = logging.ERROR if report.is_failure else logging.INFO
            log.log(
                level,
                "%s: %s%s (%.1f s)",
                entry.id,
                report.overall,
                f" [{statuses}]" if statuses else "; " + "; ".join(report.notes),
                report.duration_s,
            )
        summary = write_summary(
            reports, validation, keep_others={e.id for e in manifest.entries} if doc else None
        )
        counts: dict[str, int] = {}
        for report in reports:
            counts[report.overall] = counts.get(report.overall, 0) + 1
        log.info(
            "done: %s; reports in %s, summary %s",
            ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no documents",
            validation,
            summary,
        )
    raise typer.Exit(EXIT_FAILED if any(r.is_failure for r in reports) else EXIT_OK)


@app.command()
def approve(
    doc_id: Annotated[str, typer.Argument(metavar="ID", help="Document id to approve.")],
    output: OutputOpt = Path("output"),
    override: Annotated[
        str | None,
        typer.Option(
            "--override",
            help="Approve despite fail findings; the reason is recorded in manual_fixes.",
        ),
    ] = None,
    by: Annotated[str | None, typer.Option("--by", help="Name of the person approving.")] = None,
    validation: ValidationOpt = Path("validation"),
) -> None:
    """Mark a reviewed document as validated in its front matter."""
    if override is not None and not override.strip():
        raise _fail_config("--override needs a non-empty reason")
    manifest = _read_manifest(output)
    if manifest.get(doc_id) is None:
        raise _fail_config(f"{doc_id!r}: not in {output / 'manifest.json'}")
    try:
        message = approve_doc(doc_id, output, validation, override=override, by=by)
    except ApproveError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_FAILED) from exc
    typer.echo(message)


def main() -> None:
    app()
