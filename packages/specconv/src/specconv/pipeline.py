"""Convert every input into its own output folder and record it in the manifest.

Output per document (design: Output layout)::

    output/<id>/<id>.md          converted document with front matter
    output/<id>/source.<ext>     exact copy of the input
    output/<id>/media/           extracted images
    output/<id>/conversion.json  tool, version, options, warnings, timings

Each document is built in a temporary folder and swapped into place only
after it succeeded, so a failed re-conversion leaves the previous output
untouched. One failing document never stops the others (fail loudly, per
document, in the manifest and the log).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.frontmatter import FrontMatter, FrontMatterError, render_document
from common.manifest import Manifest, ManifestEntry

from specconv import __version__
from specconv import manifest as manifest_io
from specconv.config import SpecconvConfig
from specconv.converters.base import ConversionError
from specconv.converters.pandoc import PandocConverter
from specconv.frontmatter import carry_over, read_previous
from specconv.htmlclean import clean_html
from specconv.postprocess import PostprocessError, postprocess
from specconv.routing import InputDoc, Route, route

log = logging.getLogger("specconv.pipeline")

POSTPROCESS_VERSION = 1  # bump when post-processing changes, so outputs are redone
FAILED_STATUSES = frozenset({"failed", "no_text_layer"})


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def options_hash(options: dict[str, Any]) -> str:
    blob = json.dumps(options, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("ascii")).hexdigest()[:16]


@dataclass
class RunResult:
    manifest: Manifest
    processed: list[ManifestEntry] = field(default_factory=list)

    @property
    def failed(self) -> list[ManifestEntry]:
        return [e for e in self.processed if e.status in FAILED_STATUSES]


class Pipeline:
    def __init__(self, cfg: SpecconvConfig, output: Path, work: Path) -> None:
        self.cfg = cfg
        self.output = output
        self.work = work
        self._pandoc: dict[str, PandocConverter] = {}

    # ----------------------------------------------------------- converters

    def pandoc(self, from_format: str) -> PandocConverter:
        if from_format not in self._pandoc:
            self._pandoc[from_format] = PandocConverter(from_format)
        return self._pandoc[from_format]

    # ------------------------------------------------------------------ run

    def run(self, docs: list[InputDoc], previous: Manifest, full_run: bool) -> RunResult:
        """Convert docs. On a full run the manifest lists exactly these docs;
        otherwise (--only) entries of other documents are kept."""
        result = RunResult(manifest=Manifest() if full_run else previous)
        for doc in docs:
            entry = self.convert_one(doc)
            result.processed.append(entry)
            result.manifest.upsert(entry)
            level = logging.ERROR if entry.status in FAILED_STATUSES else logging.INFO
            log.log(
                level, "%s: %s%s", doc.id, entry.status, f" ({entry.error})" if entry.error else ""
            )
            for warning in entry.warnings:
                log.warning("%s: %s", doc.id, warning)
        if full_run:
            for old in previous.entries:
                if result.manifest.get(old.id) is None and (self.output / old.id).exists():
                    log.warning(
                        "%s: input no longer present; its output folder %s was kept",
                        old.id,
                        self.output / old.id,
                    )
        manifest_io.save(result.manifest, self.output)
        return result

    def convert_one(self, doc: InputDoc) -> ManifestEntry:
        r = route(doc)
        base = dict(id=doc.id, source=doc.display_source, source_type=doc.source_type, route=r.name)
        if r.converter is None:
            return ManifestEntry(
                **base,
                status="unsupported",
                warnings=[
                    f"{doc.display_source}: legacy .doc files are not "
                    "supported in v1; save as .docx"
                ],
            )
        if doc.source_type == "web":
            return ManifestEntry(
                **base,
                status="failed",
                error="web sources need fetching, which is built in phase 4",
            )
        if r.converter == "docling":
            return ManifestEntry(
                **base, status="failed", error="PDF conversion is built in phase 3"
            )
        assert doc.path is not None
        start = time.monotonic()
        try:
            return self._convert_with_pandoc(doc, r, base)
        except (ConversionError, PostprocessError, FrontMatterError, OSError) as exc:
            return ManifestEntry(
                **base,
                status="failed",
                error=str(exc),
                duration_s=round(time.monotonic() - start, 3),
            )
        except Exception as exc:  # one bad document must not stop the run
            log.exception("%s: unexpected error", doc.id)
            return ManifestEntry(
                **base,
                status="failed",
                error=f"unexpected {type(exc).__name__}: {exc}",
                duration_s=round(time.monotonic() - start, 3),
            )
        finally:
            shutil.rmtree(self._tmp_dir(doc.id), ignore_errors=True)

    # ------------------------------------------------------------ internals

    def _tmp_dir(self, doc_id: str) -> Path:
        return self.output / f".{doc_id}.tmp"

    def _convert_with_pandoc(self, doc: InputDoc, r: Route, base: dict[str, Any]) -> ManifestEntry:
        assert doc.path is not None and r.from_format is not None
        start = time.monotonic()
        converter = self.pandoc(r.from_format)
        source_sha = sha256_file(doc.path)
        final_dir = self.output / doc.id
        md_name = f"{doc.id}.md"
        previous = read_previous(final_dir / md_name)

        tmp = self._tmp_dir(doc.id)
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)

        warnings: list[str] = []
        pandoc_input = doc.path
        if r.from_format == "html":
            cleaned = clean_html(doc.path.read_bytes(), self.cfg.html.strip_tags)
            pandoc_input = self.work / doc.id / "clean.html"
            pandoc_input.parent.mkdir(parents=True, exist_ok=True)
            pandoc_input.write_bytes(cleaned.html.encode("utf-8"))
            log.debug(
                "%s: removed %s; source encoding %s",
                doc.id,
                cleaned.removed or "nothing",
                cleaned.original_encoding,
            )

        result = converter.convert(pandoc_input, tmp, resource_dir=doc.path.resolve().parent)
        warnings += [f"pandoc: {w}" for w in result.warnings]
        processed = postprocess(result.markdown, tmp)
        warnings += processed.warnings

        fm = FrontMatter(
            id=doc.id,
            source_type=doc.source_type,
            source_file=doc.rel_path or "",
            source_sha256=source_sha,
            converted_with=f"{result.tool} {result.tool_version}",
            converted_at=utc_now(),
        )
        carried = carry_over(fm, processed.markdown, previous)
        warnings += carried.warnings

        (tmp / md_name).write_bytes(
            render_document(carried.front_matter, processed.markdown).encode("utf-8")
        )
        shutil.copyfile(doc.path, tmp / f"source{doc.path.suffix.lower()}")
        duration = round(time.monotonic() - start, 3)
        strip_tags = list(self.cfg.html.strip_tags) if r.from_format == "html" else None
        options = {
            "route": r.name,
            "converter": result.options,
            "strip_tags": strip_tags,
            "postprocess_version": POSTPROCESS_VERSION,
        }
        conversion = {
            "id": doc.id,
            "tool": result.tool,
            "tool_version": result.tool_version,
            "options": options,
            "media": [p.as_posix() for p in result.media],
            "warnings": warnings,
            "converted_at": fm.converted_at,
            "duration_s": {"convert": result.duration_s, "total": duration},
        }
        (tmp / "conversion.json").write_bytes(
            (json.dumps(conversion, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        _swap_into_place(tmp, final_dir)

        return ManifestEntry(
            **base,
            status="ok",
            source_sha256=source_sha,
            converter=result.tool,
            converter_version=result.tool_version,
            tool_versions={"specconv": __version__, result.tool: result.tool_version},
            options=options,
            options_hash=options_hash(options),
            warnings=warnings,
            duration_s=duration,
        )


def _swap_into_place(tmp: Path, final_dir: Path) -> None:
    old = final_dir.with_name(f".{final_dir.name}.old")
    shutil.rmtree(old, ignore_errors=True)
    if final_dir.exists():
        final_dir.rename(old)
    tmp.rename(final_dir)
    shutil.rmtree(old, ignore_errors=True)
