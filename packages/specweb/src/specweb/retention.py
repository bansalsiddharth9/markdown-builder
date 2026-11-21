"""Retention (design: Retention): jobs and their files are deleted after
retention_days by a daily task; approved documents stay in the library."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select

from specweb.db import RUNNING, Job, sessions
from specweb.jobs import delete_job_files
from specweb.settings import WebConfig

log = logging.getLogger("specweb.retention")


def purge_expired(cfg: WebConfig, now: dt.datetime | None = None) -> list[str]:
    """Delete jobs created more than retention_days ago (not while running).
    Returns the deleted job ids."""
    now = now or dt.datetime.now(dt.UTC)
    # created_at has this fixed format, so comparing strings compares times.
    cutoff = (now - dt.timedelta(days=cfg.retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    deleted = []
    with sessions(cfg.data_dir)() as session:
        jobs = session.scalars(select(Job).where(Job.created_at < cutoff)).all()
        for job in jobs:
            if job.status in RUNNING:
                continue  # the worker is still writing to it; next day
            deleted.append(job.id)
            session.delete(job)
        session.commit()
    # Files after the rows are committed, as in api.remove_job.
    for job_id in deleted:
        delete_job_files(cfg, job_id)
        log.info("deleted expired job %s", job_id)
    return deleted
