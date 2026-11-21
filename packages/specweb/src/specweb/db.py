"""SQLite tables (design: Storage): jobs and documents.

The file is data/specweb.sqlite. The web server and the worker are separate
processes that both write to it, so it runs in WAL mode (deviation WB3).
The Huey queue has its own file (data/queue.sqlite).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import ForeignKey, String, Text, create_engine, event
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

# Job states while the worker is busy with a job. "queued" is not in it: a
# queued job can still be deleted, a running one cannot.
RUNNING = ("fetching", "converting", "validating")


class Base(DeclarativeBase):
    """Base class of the tables."""

    pass


class Job(Base):
    """One submission: a set of files and/or URLs converted and validated together.

    Its files live in data/jobs/<id>/ (see jobs.py).
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # 12 hex chars, random
    created_at: Mapped[str] = mapped_column(String(32))  # UTC, "YYYY-MM-DDTHH:MM:SSZ"
    submitted_by: Mapped[str | None] = mapped_column(String(200))  # free-text name, optional
    client_ip: Mapped[str | None] = mapped_column(String(64))  # for the per-client job limit
    status: Mapped[str] = mapped_column(String(16), default="queued")  # see JOB_STATUSES
    options_json: Mapped[str] = mapped_column(Text, default="{}")  # e.g. {"split": 2}
    error: Mapped[str | None] = mapped_column(Text)  # why the job failed, if it did
    documents: Mapped[list[Document]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Document.id"
    )

    @property
    def options(self) -> dict[str, Any]:
        """options_json as a dict."""
        return json.loads(self.options_json or "{}")

    def to_dict(self, with_documents: bool = True) -> dict[str, Any]:
        """The job as the API returns it, with the overall validation result.

        client_ip is left out on purpose: every job is visible to everyone.
        """
        data: dict[str, Any] = {
            "id": self.id,
            "created_at": self.created_at,
            "submitted_by": self.submitted_by,
            "status": self.status,
            "options": self.options,
            "error": self.error,
            "overall": overall_of(self),
        }
        if with_documents:
            data["documents"] = [d.to_dict() for d in self.documents]
        return data


class Document(Base):
    """One input of a job (an uploaded file or a URL) and its results."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    source_name: Mapped[str] = mapped_column(String(500))  # file name as uploaded, or URL
    # For a file: its safe name in the job's input/. For a URL: its id in sources.yaml.
    stored_name: Mapped[str | None] = mapped_column(String(200))  # in the job's input/
    url: Mapped[str | None] = mapped_column(Text)  # None for uploaded files
    render: Mapped[str | None] = mapped_column(String(16))  # URLs: "static" or "browser"
    source_type: Mapped[str] = mapped_column(String(16))  # pdf, html, docx or web
    doc_id: Mapped[str | None] = mapped_column(String(200))  # specconv's id, once converted
    # Conversion status from specconv's manifest (ok, failed, ...) or a job state.
    status: Mapped[str] = mapped_column(String(32), default="queued")
    converter: Mapped[str | None] = mapped_column(String(32))  # which converter specconv used
    validation_status: Mapped[str | None] = mapped_column(String(32))  # specval's "overall"
    error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[str | None] = mapped_column(String(32))
    override_reason: Mapped[str | None] = mapped_column(Text)  # set when approved despite fails
    job: Mapped[Job] = relationship(back_populates="documents")

    def to_dict(self) -> dict[str, Any]:
        """The document as the API returns it (stored_name is internal)."""
        return {
            "id": self.id,
            "job_id": self.job_id,
            "source_name": self.source_name,
            "url": self.url,
            "render": self.render,
            "source_type": self.source_type,
            "doc_id": self.doc_id,
            "status": self.status,
            "converter": self.converter,
            "validation_status": self.validation_status,
            "error": self.error,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "override_reason": self.override_reason,
        }


def overall_of(job: Job) -> str | None:
    """Worst validation result of the job's documents (fail > warn > pass).

    not_converted counts as worse than warn. None if nothing was validated yet.
    """
    statuses = [d.validation_status for d in job.documents if d.validation_status]
    for status in ("fail", "not_converted", "warn", "pass"):
        if status in statuses:
            return status
    return None


@lru_cache(maxsize=8)
def session_factory(db_path: str) -> sessionmaker[Session]:
    """A session maker for the SQLite file at db_path; creates missing tables.

    Cached: one engine per file and process. (Tests use several data folders,
    hence more than one entry.)
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # Wait up to 30 s for the other process's write lock instead of failing.
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
        """Set WAL mode and foreign keys on each new connection."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")  # web and worker processes share the file
        # SQLite ignores ON DELETE CASCADE unless this is on.
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    # Objects stay readable after the session closes (routes use them after `with`).
    return sessionmaker(engine, expire_on_commit=False)


def sessions(data_dir: Path) -> sessionmaker[Session]:
    """The session maker for data_dir/specweb.sqlite.

    The path is resolved first, so "data" and "./data" share one engine.
    """
    return session_factory(str((data_dir / "specweb.sqlite").resolve()))
