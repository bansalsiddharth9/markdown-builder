"""The Huey queue (design: Jobs and workers): SQLite storage, so no extra
service is needed on one host. The worker process runs

    huey_consumer specweb.tasks.huey -w <worker_concurrency>

(or `specweb worker`). The queue file is data/queue.sqlite.
"""

from __future__ import annotations

import os
from pathlib import Path

from huey import SqliteHuey

from specweb.settings import WebConfig

_DATA = Path(os.environ.get("SPECWEB_DATA", "data"))
_DATA.mkdir(parents=True, exist_ok=True)

huey = SqliteHuey("specweb", filename=str(_DATA / "queue.sqlite"), immediate=False)


@huey.task()
def process_job_task(job_id: str, config: dict) -> None:
    from specweb import worker

    worker.process_job(WebConfig.from_dict(config), job_id)


def enqueue(cfg: WebConfig, job_id: str) -> None:
    process_job_task(job_id, cfg.to_dict())
