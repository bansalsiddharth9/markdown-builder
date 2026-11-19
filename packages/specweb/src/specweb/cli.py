"""`specweb serve` (web server) and `specweb worker` (queue consumer)."""

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
    from specweb.settings import ConfigError, load_config

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    # The queue module reads its location from the environment when imported.
    os.environ["SPECWEB_DATA"] = str(cfg.data_dir)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


@app.command()
def serve(
    config: ConfigOpt = None,
    host: Annotated[str, typer.Option(help="Address to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
) -> None:
    """Run the web server (put a reverse proxy with HTTPS in front of it)."""
    import uvicorn

    cfg = _load(config)
    from specweb.app import create_app

    uvicorn.run(create_app(cfg), host=host, port=port, proxy_headers=True)


@app.command()
def worker(config: ConfigOpt = None) -> None:
    """Run the queue consumer that converts and validates jobs."""
    cfg = _load(config)
    from huey.consumer import Consumer

    from specweb.tasks import huey

    Consumer(huey, workers=cfg.worker_concurrency, worker_type="process").run()


def main() -> None:
    app()
