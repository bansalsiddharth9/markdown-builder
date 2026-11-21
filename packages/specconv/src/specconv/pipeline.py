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

Incremental runs (design: Incremental runs): a document is skipped and
marked skipped_unchanged when its source SHA-256, converter version and
options hash match the previous manifest entry and its output exists
(deviation Q9). --force converts everything again.

Web sources (design: Web sources) are converted from the fetch cache in
work/fetched/; a source that is not cached yet is fetched first. Their
source.html is the fetched page, and a page with under 500 characters of
text after cleanup is flagged suspect_empty.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from importlib.metadata import version as dist_version
from pathlib import Path
from typing import Any

import yaml
from common.frontmatter import FrontMatter, FrontMatterError, render_document
from common.manifest import Manifest, ManifestEntry

from specconv import __version__
from specconv import manifest as manifest_io
from specconv.config import SpecconvConfig
from specconv.converters.base import MEDIA_DIR, ConversionError, ConversionResult
from specconv.converters.docling import DoclingConverter
from specconv.converters.pandoc import PandocConverter
from specconv.fetch import FetchError, FetchOutcome, fetch_source
from specconv.frontmatter import carry_over, read_previous
from specconv.htmlprep import OwnUrl, PreparedHtml, prepare_html
from specconv.postprocess import PostprocessError, postprocess
from specconv.routing import InputDoc, Route, route
from specconv.split import split_sections
from specconv.textlayer import FLAG_MIXED, PdfReadError, TextLayer, check_text_layer

log = logging.getLogger("specconv.pipeline")

# Bump when HTML preparation, filters or post-processing change. It is part of the
# options hash, so every document is converted again on the next run (deviation Q9).
RECIPE_VERSION = 9
FAILED_STATUSES = frozenset({"failed", "no_text_layer"})
# Only documents that converted fine can be skipped; failed ones are always retried.
SKIPPABLE_STATUSES = frozenset({"ok", "skipped_unchanged"})
SUSPECT_EMPTY_CHARS = 500  # design: page that renders empty
FLAG_SUSPECT_EMPTY = "suspect_empty"


def utc_now() -> str:
    """Current UTC time as ISO 8601 with a Z, to the second."""
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, read in 1 MiB blocks so large PDFs need little memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def options_hash(options: dict[str, Any]) -> str:
    """Short, stable hash of the options; keys are sorted so dict order does not matter."""
    blob = json.dumps(options, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("ascii")).hexdigest()[:16]


@dataclass
class RunResult:
    """The manifest after a run and the entries of the documents handled in it."""

    manifest: Manifest
    processed: list[ManifestEntry] = field(default_factory=list)

    @property
    def failed(self) -> list[ManifestEntry]:
        """Entries that make the CLI exit with 1 (failed, no_text_layer; deviation P11)."""
        return [e for e in self.processed if e.status in FAILED_STATUSES]


@dataclass
class Prepared:
    """What a route produced before the shared finishing steps."""

    result: ConversionResult
    flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # extra conversion.json sections


class Pipeline:
    """Converts documents one by one; converters are created once and reused."""

    def __init__(
        self,
        cfg: SpecconvConfig,
        output: Path,
        work: Path,
        previous: Manifest | None = None,
        force: bool = False,
        split: int | None = None,
    ) -> None:
        self.cfg = cfg
        self.output = output
        self.work = work
        self.previous = previous or Manifest()
        self.force = force
        self.split = split  # --split LEVEL: also write sections/ (phase 7)
        self._pandoc: dict[str, PandocConverter] = {}
        self._docling: DoclingConverter | None = None
        self._fetched: dict[str, FetchOutcome] = {}  # web source id -> fetch used

    # ----------------------------------------------------------- converters

    def pandoc(self, from_format: str) -> PandocConverter:
        """The Pandoc converter for "html" or "docx", created on first use."""
        if from_format not in self._pandoc:
            self._pandoc[from_format] = PandocConverter(from_format)
        return self._pandoc[from_format]

    def docling(self) -> DoclingConverter:
        """Created on first use and reused for the whole run (model loading is slow)."""
        if self._docling is None:
            self._docling = DoclingConverter(self.cfg.pdf.models_path)
        return self._docling

    def _options(self, doc: InputDoc, r: Route) -> tuple[str, dict[str, Any]]:
        """Converter version and the options recorded in the manifest, known
        before converting so unchanged documents can be skipped."""
        if r.converter == "pandoc":
            assert r.from_format is not None
            conv = self.pandoc(r.from_format)
            version, converter_options = conv.version(), conv.options()
            strip_tags = list(self.cfg.html.strip_tags) if r.from_format == "html" else None
        else:
            version = dist_version("docling")
            converter_options = DoclingConverter.options()
            strip_tags = None
        options = {
            "route": r.name,
            "converter": converter_options,
            "strip_tags": strip_tags,
            "recipe_version": RECIPE_VERSION,
        }
        if self.split is not None:
            options["split"] = self.split
        if r.from_format == "html" and doc.rel_path is not None:
            # A changed own URL changes which links are rewritten.
            options["self_url"] = self.cfg.self_url_for(doc.rel_path)
        if doc.source is not None:
            # convert_one fetches web sources before calling this.
            fetched = self._fetched[doc.id].record
            options["source"] = {
                "url": doc.source.url,
                "render": doc.source.render,
                "clean": doc.source.clean,
                "drop_selectors": list(doc.source.drop_selectors),
            }
            # The final URL decides absolute links and self-links, the encoding how
            # the bytes are read; the downloaded images go into media/.
            options["fetch"] = {
                "final_url": fetched.final_url,
                "encoding": fetched.encoding,
                "images": options_hash({"ok": fetched.images, "failed": fetched.images_failed}),
            }
        if r.converter == "docling":
            options["pdf"] = {
                "min_chars_per_page": self.cfg.pdf.min_chars_per_page,
                "min_text_page_ratio": self.cfg.pdf.min_text_page_ratio,
            }
        return version, options

    # ------------------------------------------------------------------ run

    def run(self, docs: list[InputDoc], full_run: bool) -> RunResult:
        """Convert docs. On a full run the manifest lists exactly these docs;
        otherwise (--only) entries of other documents are kept (deviation P10).

        The manifest is saved at the end, also when documents failed.
        """
        result = RunResult(manifest=Manifest() if full_run else self.previous)
        for doc in docs:
            entry = self.convert_one(doc)
            result.processed.append(entry)
            result.manifest.upsert(entry)
            level = logging.ERROR if entry.status in FAILED_STATUSES else logging.INFO
            log.log(
                level, "%s: %s%s", doc.id, entry.status, f" ({entry.error})" if entry.error else ""
            )
            # A skipped document's warnings were already logged when it was converted.
            if entry.status != "skipped_unchanged":
                for warning in entry.warnings:
                    log.warning("%s: %s", doc.id, warning)
        if full_run:
            for old in self.previous.entries:
                if result.manifest.get(old.id) is None and (self.output / old.id).exists():
                    log.warning(
                        "%s: input no longer present; its output folder %s was kept",
                        old.id,
                        self.output / old.id,
                    )
        manifest_io.save(result.manifest, self.output)
        return result

    def convert_one(self, doc: InputDoc) -> ManifestEntry:
        """Convert one document and return its manifest entry. Never raises.

        Every error becomes a "failed" entry so the other documents still run. A web
        source is fetched first (or taken from the cache); from then on it is
        handled like a local HTML file.
        """
        r = route(doc)
        base: dict[str, Any] = dict(
            id=doc.id, source=doc.display_source, source_type=doc.source_type, route=r.name
        )
        if r.converter is None:
            return ManifestEntry(
                **base,
                status="unsupported",
                warnings=[
                    f"{doc.display_source}: legacy .doc files are not "
                    "supported in v1; save as .docx"
                ],
            )
        start = time.monotonic()
        try:
            if doc.source is not None:
                outcome = fetch_source(doc.source, self.cfg.fetch, self.work)
                self._fetched[doc.id] = outcome
                # From here on the cached page is the document's file.
                doc = dataclasses.replace(doc, path=outcome.html_path)
            assert doc.path is not None
            source_sha = sha256_file(doc.path)
            version, options = self._options(doc, r)
            skipped = self._skip_if_unchanged(doc, source_sha, version, options)
            if skipped is not None:
                return skipped
            if r.converter == "docling":
                return self._convert_pdf(doc, r, base, source_sha, version, options)
            return self._convert(doc, base, source_sha, options, self._run_pandoc(doc, r))
        except (
            ConversionError,
            PostprocessError,
            FrontMatterError,
            PdfReadError,
            FetchError,
            OSError,
        ) as exc:
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
            # Leftovers of a failed build; after success the folder was already moved.
            shutil.rmtree(self._tmp_dir(doc.id), ignore_errors=True)

    # --------------------------------------------------------- incremental

    def _skip_if_unchanged(
        self, doc: InputDoc, source_sha: str, version: str, options: dict[str, Any]
    ) -> ManifestEntry | None:
        """The previous entry marked skipped_unchanged, or None if it must be converted.

        Also converts again when the source path changed or the output file is gone.
        """
        if self.force:
            return None
        prev = self.previous.get(doc.id)
        if (
            prev is None
            or prev.status not in SKIPPABLE_STATUSES
            or prev.source != doc.display_source
            or prev.source_sha256 != source_sha
            or prev.converter_version != version
            or prev.options_hash != options_hash(options)
            or not (self.output / doc.id / f"{doc.id}.md").is_file()
        ):
            return None
        return dataclasses.replace(prev, status="skipped_unchanged", duration_s=0.0)

    # -------------------------------------------------------------- routes

    def _tmp_dir(self, doc_id: str) -> Path:
        """Hidden build folder next to the final one (same file system, so rename works)."""
        return self.output / f".{doc_id}.tmp"

    def _new_tmp(self, doc_id: str) -> Path:
        """Empty build folder; leftovers of an interrupted run are removed first."""
        tmp = self._tmp_dir(doc_id)
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        return tmp

    def _run_pandoc(self, doc: InputDoc, r: Route) -> Prepared:
        """Prepare HTML if needed, run Pandoc, and collect flags and warnings.

        Pandoc reads the prepared copy in work/<id>/prepared.html, never the
        original; the original is still what source.<ext> keeps.
        """
        assert doc.path is not None and r.from_format is not None
        tmp = self._new_tmp(doc.id)
        prepared: PreparedHtml | None = None
        pandoc_input = doc.path
        if r.from_format == "html":
            prepared = self._prepare_html(doc, tmp)
            pandoc_input = self.work / doc.id / "prepared.html"
            pandoc_input.parent.mkdir(parents=True, exist_ok=True)
            pandoc_input.write_bytes(prepared.html.encode("utf-8"))
        result = self.pandoc(r.from_format).convert(pandoc_input, tmp)
        flags = list(prepared.flags) if prepared else []
        warnings = list(prepared.warnings) if prepared else []
        extra: dict[str, Any] = {"html": prepared.summary() if prepared else None}
        if doc.source is not None and prepared is not None:
            record = self._fetched[doc.id].record
            extra["fetch"] = dataclasses.asdict(record)
            for failure in record.images_failed:
                warnings.append(
                    f"image {failure['url']}: not downloaded when the page was fetched "
                    f"({failure['error']}); web link kept"
                )
            if prepared.text_chars < SUSPECT_EMPTY_CHARS:
                flags.append(FLAG_SUSPECT_EMPTY)
                hint = (
                    "; if the page is built by JavaScript, set render: browser in sources.yaml"
                    if doc.source.render == "static"
                    else ""
                )
                warnings.append(
                    f"only {prepared.text_chars} characters of text after cleanup "
                    f"(under {SUSPECT_EMPTY_CHARS}){hint}"
                )
        return Prepared(result=result, flags=flags, warnings=warnings, extra=extra)

    def _convert_pdf(
        self,
        doc: InputDoc,
        r: Route,
        base: dict[str, Any],
        source_sha: str,
        version: str,
        options: dict[str, Any],
    ) -> ManifestEntry:
        """Check the text layer, then convert with Docling.

        A PDF below min_text_page_ratio gets status no_text_layer and no output folder
        is written (deviation Q10). Damaged PDFs raise PdfReadError (a failed entry).
        """
        assert doc.path is not None
        start = time.monotonic()
        layer: TextLayer = check_text_layer(doc.path, self.cfg.pdf)
        flags = [FLAG_MIXED] if layer.mixed else []
        warnings = []
        if layer.mixed:
            warnings.append(
                f"text layer on {layer.text_pages} of {layer.pages} pages; pages without "
                f"text: {_page_list(layer.pages_without_text)}"
            )
        if layer.ratio < self.cfg.pdf.min_text_page_ratio:
            return ManifestEntry(
                **{**base, "route": "none:no_text_layer"},
                status="no_text_layer",
                source_sha256=source_sha,
                tool_versions={"specconv": __version__},
                flags=flags,
                warnings=warnings,
                error=(
                    f"text layer on {layer.text_pages} of {layer.pages} pages "
                    f"({layer.ratio:.0%}, need {self.cfg.pdf.min_text_page_ratio:.0%}); "
                    "not converted (image-only or damaged PDF; OCR is out of scope)"
                ),
                duration_s=round(time.monotonic() - start, 3),
            )
        tmp = self._new_tmp(doc.id)
        result = self.docling().convert(
            doc.path, tmp, json_path=self.work / f"{doc.id}.docling.json"
        )
        dropped = result.details.get("dropped_as_page_furniture") or []
        if dropped:
            warnings.append(
                f"{len(dropped)} text item(s) left out as page headers or footers; "
                "listed in conversion.json under docling.dropped_as_page_furniture"
            )
        prepared = Prepared(
            result=result,
            flags=flags,
            warnings=warnings,
            extra={"text_layer": layer.summary(), "docling": result.details},
        )
        return self._convert(doc, base, source_sha, options, prepared)

    # ------------------------------------------------------------ finishing

    def _convert(
        self,
        doc: InputDoc,
        base: dict[str, Any],
        source_sha: str,
        options: dict[str, Any],
        prepared: Prepared,
    ) -> ManifestEntry:
        """Shared steps: post-process, front matter, files, swap, manifest entry.

        Everything is written into the build folder first; the previous output is
        replaced only at the end (deviation P9).
        """
        assert doc.path is not None
        start = time.monotonic()
        result = prepared.result
        tmp = self._tmp_dir(doc.id)
        final_dir = self.output / doc.id
        md_name = f"{doc.id}.md"
        # Read now: raises before any work if the old front matter is unreadable (P7).
        previous = read_previous(final_dir / md_name)

        warnings = list(prepared.warnings)
        warnings += [f"{result.tool}: {w}" for w in result.warnings]
        processed = postprocess(result.markdown, tmp)
        warnings += processed.warnings

        fetched = self._fetched.get(doc.id) if doc.source is not None else None
        fm = FrontMatter(
            id=doc.id,
            source_type=doc.source_type,
            source_file=doc.rel_path or "",
            source_url=doc.source.url if doc.source is not None else None,
            fetched_at=fetched.record.fetched_at if fetched is not None else None,
            source_sha256=source_sha,
            converted_with=f"{result.tool} {result.tool_version}",
            converted_at=utc_now(),
            flags=list(prepared.flags),
        )
        carried = carry_over(fm, processed.markdown, previous)
        warnings += carried.warnings

        (tmp / md_name).write_bytes(
            render_document(carried.front_matter, processed.markdown).encode("utf-8")
        )
        shutil.copyfile(doc.path, tmp / f"source{doc.path.suffix.lower()}")
        if self.split is not None:
            prepared.extra["split"] = _write_sections(
                tmp, doc.id, md_name, processed.markdown, self.split
            )
        conversion = {
            "id": doc.id,
            "tool": result.tool,
            "tool_version": result.tool_version,
            "options": options,
            "media": [p.as_posix() for p in result.media],
            "warnings": warnings,
            **prepared.extra,
            "converted_at": fm.converted_at,
            "duration_s": {
                "convert": result.duration_s,
                "finish": round(time.monotonic() - start, 3),
            },
        }
        (tmp / "conversion.json").write_bytes(
            (json.dumps(conversion, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        _swap_into_place(tmp, final_dir)

        tool_versions = {"specconv": __version__, result.tool: result.tool_version}
        if fetched is not None:
            fetcher = "playwright" if fetched.record.render == "browser" else "httpx"
            tool_versions[fetcher] = dist_version(fetcher)
        return ManifestEntry(
            **base,
            status="ok",
            source_sha256=source_sha,
            converter=result.tool,
            converter_version=result.tool_version,
            tool_versions=tool_versions,
            options=options,
            options_hash=options_hash(options),
            flags=list(prepared.flags),
            warnings=warnings,
            duration_s=round(result.duration_s + time.monotonic() - start, 3),
        )

    def _prepare_html(self, doc: InputDoc, tmp: Path) -> PreparedHtml:
        """Clean the HTML, rewrite self-links and copy local images into tmp/media.

        Web sources pass their URLs, encoding, clean mode and downloaded images;
        local files pass their configured self_url, if any.
        """
        assert doc.path is not None
        known: list[OwnUrl] = []
        web: dict[str, Any] = {}
        if doc.source is not None:
            record = self._fetched[doc.id].record
            known = [OwnUrl(doc.source.url, "sources.yaml"), OwnUrl(record.final_url, "final URL")]
            web = dict(
                page_url=record.final_url,
                encoding=record.encoding,
                clean=doc.source.clean,
                drop_selectors=doc.source.drop_selectors,
                downloaded_images=record.downloaded_images(self.work),
            )
        elif doc.rel_path is not None:
            configured = self.cfg.self_url_for(doc.rel_path)
            if configured:
                known.append(OwnUrl(configured, "self_urls"))
        prepared = prepare_html(
            doc.path.read_bytes(),
            strip_tags=self.cfg.html.strip_tags,
            media_dir=tmp / MEDIA_DIR,
            # A fetched page has no local image folder: its images are web links.
            html_path=doc.path if doc.source is None else None,
            known_urls=known,
            **web,
        )
        log.debug(
            "%s: own URLs %s; %d self-links rewritten; removed %s; images %d copied, "
            "%d remote, %d missing",
            doc.id,
            [u.url for u in prepared.own_urls] or "unknown",
            prepared.self_links_rewritten,
            prepared.removed or "nothing",
            len(prepared.images_copied),
            prepared.images_remote,
            len(prepared.images_missing),
        )
        return prepared


def _page_list(pages: list[int], limit: int = 20) -> str:
    """Page numbers as "1, 2, 3", cut after limit with a count of the rest."""
    shown = ", ".join(map(str, pages[:limit]))
    return shown + (f" … ({len(pages) - limit} more)" if len(pages) > limit else "")


def _with_meta(meta: dict[str, Any], body: str) -> bytes:
    """A section file: YAML front matter, then the body, as UTF-8."""
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    return f"---\n{head}---\n{body}".encode()


def _write_sections(tmp: Path, doc_id: str, md_name: str, markdown: str, level: int):
    """sections/<file>.md per section and sections/index.md (design: --split).

    Front matter fields are listed in deviation SP3. Returns the summary for
    conversion.json.
    """
    result = split_sections(markdown, level)
    folder = tmp / "sections"
    folder.mkdir()
    parent = f"../{md_name}"
    for s in result.sections:
        meta = {
            "parent": doc_id,
            "parent_file": parent,
            "section": s.number,
            "title": s.title,
            "level": s.level,
            "order": s.order,
        }
        (folder / s.filename).write_bytes(_with_meta(meta, s.body))
    meta = {"parent": doc_id, "parent_file": parent, "split_level": level,
            "sections": len(result.sections)}  # fmt: skip
    (folder / "index.md").write_bytes(_with_meta(meta, result.index_body(doc_id)))
    return {"level": level, "sections": [s.filename for s in result.sections]}


def _swap_into_place(tmp: Path, final_dir: Path) -> None:
    """Replace final_dir with tmp, keeping the old folder until the new one is in place.

    Two renames, not one atomic step: if the process dies between them, the old
    output is left in .<id>.old until the next successful swap of that document.
    """
    old = final_dir.with_name(f".{final_dir.name}.old")
    shutil.rmtree(old, ignore_errors=True)
    if final_dir.exists():
        final_dir.rename(old)
    tmp.rename(final_dir)
    shutil.rmtree(old, ignore_errors=True)
