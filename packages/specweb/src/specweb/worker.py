"""Processing one job (design: Jobs and workers).

The worker runs the same entry points as the command line, `python -m
specconv` and `python -m specval`, each in a subprocess with a timeout, so a
stuck or crashing converter cannot take the worker down and there is one code
path for CLI and UI. Job states: queued -> fetching -> converting ->
validating -> done or error; each document has its own status.

Models are loaded once per job, not kept warm (deviation WB1). Each step's
timeout is step_timeout_s times the number of documents (deviation WB2).
The worker writes job and document status to the shared database; the web
server only reads it (and HTMX polling shows the changes).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from specweb.db import Document, Job, sessions
from specweb.jobs import job_folder
from specweb.settings import WebConfig

log = logging.getLogger("specweb.worker")

# Which package's CLI runs each subcommand.
TOOL_OF = {"fetch": "specconv", "convert": "specconv", "run": "specval"}


class StepTimeout(RuntimeError):
    """A step did not finish within its timeout (the subprocess was killed)."""


def run_step_command(cmd: list[str], timeout: float, log_path: Path) -> int:
    """Run cmd with output to log_path; return its exit code; raise StepTimeout.

    stdout and stderr go to the same log file (logs/<step>.log in the job).
    On timeout, subprocess.run kills the process before StepTimeout is raised.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as fh:
        try:
            return subprocess.run(
                cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout
            ).returncode
        except subprocess.TimeoutExpired as exc:
            raise StepTimeout(
                # cmd[2] is the tool name in [python, "-m", tool, ...]
                f"{cmd[2] if len(cmd) > 2 else cmd[0]} did not finish within {timeout:g} s"
            ) from exc


def run_step(args: list[str], timeout: float, log_path: Path) -> int:
    """args start with the subcommand: fetch / convert (specconv), run (specval).

    Runs `python -m <tool> <args>` with this Python, so the worker uses the
    same installed packages as the command line.
    """
    return run_step_command([sys.executable, "-m", TOOL_OF[args[0]], *args], timeout, log_path)


def _set(cfg: WebConfig, job_id: str, **changes) -> None:
    """Set job columns (status=..., error=...) in one short transaction.

    doc_status, if given, is also set on every document that is still in a
    job state (queued ... validating); documents with a result keep it.
    Does nothing if the job was deleted.
    """
    with sessions(cfg.data_dir)() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        doc_status = changes.pop("doc_status", None)
        for key, value in changes.items():
            setattr(job, key, value)
        if doc_status is not None:
            for doc in job.documents:
                if doc.status in ("queued", "fetching", "converting", "validating"):
                    doc.status = doc_status
        session.commit()


def process_job(cfg: WebConfig, job_id: str) -> None:
    """Fetch (if there are URLs), convert and validate one job; record results.

    Exit codes 0 and 1 of convert and validate are normal (1 means some
    documents failed or have findings). Any other code, a timeout or an
    OSError puts the job in "error" with the message.
    """
    with sessions(cfg.data_dir)() as session:
        job = session.get(Job, job_id)
        if job is None:
            log.info("job %s no longer exists", job_id)
            return
        # Read what we need now; the session closes before the long steps.
        options = job.options
        n_docs = max(1, len(job.documents))  # timeout multiplier (WB2); never 0
        n_urls = sum(1 for d in job.documents if d.url)
        has_files = any(d.url is None for d in job.documents)
    folder = job_folder(cfg, job_id)
    sources = folder / "sources.yaml"
    work, output, validation, logs = (folder / n for n in ("work", "output", "validation", "logs"))
    # Command-line options for specconv and specval, from specweb.yaml.
    conv_cfg = ["--config", str(cfg.specconv_config)] if cfg.specconv_config else []
    # Fetching for anyone on the network: never reach private addresses (design: Security).
    guard = ["--block-private-addresses"]
    for host in cfg.fetch_allowlist:
        guard += ["--allow-host", host]
    val_cfg = ["--config", str(cfg.specval_config)] if cfg.specval_config else []
    if cfg.acceptance:
        val_cfg += ["--acceptance", str(cfg.acceptance)]
    try:
        if n_urls:
            _set(cfg, job_id, status="fetching", doc_status="fetching")
            # Fetch failures are recorded per document by convert; carry on (WB7).
            run_step(["fetch", "--sources", str(sources), "--work", str(work), *conv_cfg, *guard],
                     cfg.step_timeout_s * n_urls, logs / "fetch.log")  # fmt: skip
        _set(cfg, job_id, status="converting", doc_status="converting")
        args = ["convert", "--output", str(output), "--work", str(work), *conv_cfg, *guard]
        if has_files:
            args += ["--input", str(folder / "input")]
        if n_urls:
            args += ["--sources", str(sources)]
        if options.get("split") is not None:
            args += ["--split", str(options["split"])]
        code = run_step(args, cfg.step_timeout_s * n_docs, logs / "convert.log")
        if code not in (0, 1):  # 1: some documents failed; still a result
            raise RuntimeError(f"convert exited with code {code} (see logs/convert.log)")
        _record_conversion(cfg, job_id, output)
        _set(cfg, job_id, status="validating")
        code = run_step(
            ["run", "--output", str(output), "--validation", str(validation), *val_cfg],
            cfg.step_timeout_s * n_docs,
            logs / "validate.log",
        )
        if code not in (0, 1):  # 1: some documents have fail findings
            raise RuntimeError(f"validate exited with code {code} (see logs/validate.log)")
        _record_validation(cfg, job_id, validation)
        _set(cfg, job_id, status="done")
    except (StepTimeout, RuntimeError, OSError) as exc:
        log.error("job %s: %s", job_id, exc)
        _set(cfg, job_id, status="error", error=str(exc), doc_status="error")


def _record_conversion(cfg: WebConfig, job_id: str, output: Path) -> None:
    """Copy each document's result (doc_id, status, converter, error) from
    specconv's output/manifest.json into the database.

    Manifest entries are matched by source: the URL for web documents, the
    stored file name for uploads. A document missing from the manifest is
    marked as an error.
    """
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    by_source = {e["source"]: e for e in manifest.get("documents", [])}
    with sessions(cfg.data_dir)() as session:
        job = session.get(Job, job_id)
        # A running job cannot be deleted (api.remove_job), so it still exists.
        assert job is not None
        for doc in job.documents:
            entry = by_source.get(doc.url if doc.url else doc.stored_name)
            if entry is None:
                doc.status, doc.error = "error", "not in the converter's manifest"
                continue
            doc.doc_id = entry["id"]
            doc.status = entry["status"]
            doc.converter = entry.get("converter")
            doc.error = entry.get("error")
        session.commit()


def _record_validation(cfg: WebConfig, job_id: str, validation: Path) -> None:
    """Set each converted document's validation_status from specval's report
    (validation/<doc_id>.json, field "overall"). Documents without a report
    keep None."""
    with sessions(cfg.data_dir)() as session:
        job = session.get(Job, job_id)
        assert job is not None
        for doc in job.documents:
            report = validation / f"{doc.doc_id}.json" if doc.doc_id else None
            if report is not None and report.is_file():
                doc.validation_status = json.loads(report.read_text(encoding="utf-8"))["overall"]
        session.commit()


def documents_of(cfg: WebConfig, job_id: str) -> list[Document]:
    """The job's documents, or [] if the job does not exist."""
    with sessions(cfg.data_dir)() as session:
        job = session.get(Job, job_id)
        return list(job.documents) if job else []
