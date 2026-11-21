"""`specweb serve` (web server) and `specweb worker` (queue consumer).

Both read the same specweb.yaml (deviation WB8). They run as two separate
processes that share the data folder.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(name="specweb", help="Web UI for specconv and specval.", no_args_is_help=True,
                  add_completion=False)  # fmt: skip

ConfigOpt = Annotated[
    Path | None, typer.Option("--config", help="specweb.yaml (default: $SPECWEB_CONFIG).")
]


def _load(config: Path | None):
    """Load the settings, or exit with code 2 and a message on a config error.

    Also sets $SPECWEB_DATA and creates the data folder.
    """
    # Imported only when a command runs, not when the CLI module loads.
    from specweb.settings import ConfigError, load_config

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    # The queue module reads its location from the environment when imported.
    # So this must run before specweb.tasks is imported (create_app imports it).
    os.environ["SPECWEB_DATA"] = str(cfg.data_dir)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


@app.command()
def serve(
    config: ConfigOpt = None,
    host: Annotated[str, typer.Option(help="Address to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
    forwarded_allow_ips: Annotated[
        str,
        typer.Option(
            help="Proxies whose X-Forwarded-For is trusted for the client address "
            "(per-client limits); '*' inside Docker, where only the proxy can connect."
        ),
    ] = "127.0.0.1",
) -> None:
    """Run the web server (put a reverse proxy with HTTPS in front of it)."""
    import uvicorn

    cfg = _load(config)
    # Import after _load: the app imports specweb.tasks, which reads $SPECWEB_DATA.
    from specweb.app import create_app

    uvicorn.run(
        create_app(cfg),
        host=host,
        port=port,
        # Take the client address from X-Forwarded-For, but only from the
        # trusted proxies; otherwise anyone could fake it (deviation SEC6).
        proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips,
    )


@app.command()
def worker(config: ConfigOpt = None) -> None:
    """Run the queue consumer that converts and validates jobs.

    First ends jobs that a previous worker stop left half done (see
    worker.recover_interrupted_jobs). Run one worker command per data folder.
    """
    cfg = _load(config)
    from huey.consumer import Consumer

    # Import after _load: the queue file location comes from $SPECWEB_DATA.
    from specweb import worker as jobs_worker
    from specweb.tasks import huey

    # Jobs that were running when the worker last stopped are lost from the
    # queue; mark them as errors before taking new tasks (deviation HP6).
    jobs_worker.recover_interrupted_jobs(cfg)

    # worker_concurrency jobs at once, each in its own consumer process (default 1,
    # because conversion needs several GB of memory; design: Jobs and workers).
    Consumer(huey, workers=cfg.worker_concurrency, worker_type="process").run()


def main() -> None:
    """Entry point of the `specweb` command."""
    app()
