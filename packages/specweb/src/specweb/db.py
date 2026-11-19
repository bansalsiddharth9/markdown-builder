"""SQLite tables (design: Storage): jobs and documents."""

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

RUNNING = ("fetching", "converting", "validating")


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(32))
    submitted_by: Mapped[str | None] = mapped_column(String(200))
    client_ip: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    options_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    documents: Mapped[list[Document]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Document.id"
    )

    @property
    def options(self) -> dict[str, Any]:
        return json.loads(self.options_json or "{}")

    def to_dict(self, with_documents: bool = True) -> dict[str, Any]:
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
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    source_name: Mapped[str] = mapped_column(String(500))  # file name as uploaded, or URL
    stored_name: Mapped[str | None] = mapped_column(String(200))  # in the job's input/
    url: Mapped[str | None] = mapped_column(Text)
    render: Mapped[str | None] = mapped_column(String(16))
    source_type: Mapped[str] = mapped_column(String(16))
    doc_id: Mapped[str | None] = mapped_column(String(200))  # specconv's id, once converted
    status: Mapped[str] = mapped_column(String(32), default="queued")
    converter: Mapped[str | None] = mapped_column(String(32))
    validation_status: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[str | None] = mapped_column(String(32))
    override_reason: Mapped[str | None] = mapped_column(Text)
    job: Mapped[Job] = relationship(back_populates="documents")

    def to_dict(self) -> dict[str, Any]:
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
    """Worst validation result of the job's documents (fail > warn > pass)."""
    statuses = [d.validation_status for d in job.documents if d.validation_status]
    for status in ("fail", "not_converted", "warn", "pass"):
        if status in statuses:
            return status
    return None


@lru_cache(maxsize=8)
def session_factory(db_path: str) -> sessionmaker[Session]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")  # web and worker processes share the file
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def sessions(data_dir: Path) -> sessionmaker[Session]:
    return session_factory(str((data_dir / "specweb.sqlite").resolve()))
