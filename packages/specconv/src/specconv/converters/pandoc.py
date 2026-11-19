"""PandocConverter for HTML and DOCX (design: Per-tool settings).

    pandoc SRC --sandbox -f html-auto_identifiers -t gfm --wrap=none
           --metadata=anchor-headings:all --lua-filter anchors.lua
    pandoc SRC --sandbox -f docx-auto_identifiers --track-changes=accept -t gfm
           --wrap=none --metadata=anchor-headings:targeted --lua-filter anchors.lua
           --extract-media=media

- --sandbox: Pandoc reads only the files on its command line and never uses
  the network (decision I1). HTML images were already copied into media/ by
  specconv.htmlprep, so HTML needs no --extract-media; a missing or web image
  keeps its link (decision I2) instead of being replaced by its description.
- -auto_identifiers: HTML headings only carry ids that exist in the source.
  Pandoc's DOCX reader invents heading ids regardless, so for DOCX the
  filter keeps only heading ids that a link targets (anchor-headings).
- anchors.lua writes the ids that in-page links need as empty <a id> anchors.

Pandoc runs inside the output folder, so DOCX images land in OUT/media and
links are already relative ("media/…"). Markdown is read from stdout;
stderr lines become warnings.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from specconv.converters.base import MEDIA_DIR, ConversionError, ConversionResult
from specconv.deps import find_pandoc, pandoc_version

PANDOC_TIMEOUT_S = 900  # design: 15 minutes per document
ANCHORS_FILTER = Path(__file__).resolve().parent.parent / "filters" / "anchors.lua"


class PandocConverter:
    """Convert HTML or DOCX with Pandoc to GitHub-flavoured Markdown."""

    FORMATS = ("html", "docx")

    def __init__(self, from_format: str, pandoc: Path | None = None) -> None:
        if from_format not in self.FORMATS:
            raise ValueError(f"unsupported Pandoc input format {from_format!r}")
        found = pandoc or find_pandoc()
        if found is None:
            raise ConversionError("pandoc not found")
        if not ANCHORS_FILTER.is_file():
            raise ConversionError(f"Lua filter missing: {ANCHORS_FILTER}")
        self.pandoc = found
        self.from_format = from_format
        self.name = "pandoc"
        self._version: str | None = None
        # HTML heading ids come from the source and other documents link to them;
        # Pandoc invents DOCX heading ids, so only those a link targets are kept.
        self.anchor_headings = "all" if from_format == "html" else "targeted"

    def version(self) -> str:
        if self._version is None:
            self._version = pandoc_version(self.pandoc)
        return self._version

    def options(self) -> dict[str, object]:
        opts: dict[str, object] = {
            "from": f"{self.from_format}-auto_identifiers",
            "to": "gfm",
            "wrap": "none",
            "sandbox": True,
            "lua_filter": ANCHORS_FILTER.name,
            "anchor_headings": self.anchor_headings,
        }
        if self.from_format == "docx":
            opts["track_changes"] = "accept"
            opts["extract_media"] = MEDIA_DIR
        return opts

    def command(self, src: Path) -> list[str]:
        cmd = [
            str(self.pandoc),
            str(src.resolve()),
            "--sandbox",
            "-f",
            f"{self.from_format}-auto_identifiers",
        ]
        if self.from_format == "docx":
            cmd.append("--track-changes=accept")
        cmd += [
            "-t",
            "gfm",
            "--wrap=none",
            f"--metadata=anchor-headings:{self.anchor_headings}",
            f"--lua-filter={ANCHORS_FILTER}",
        ]
        if self.from_format == "docx":
            cmd.append(f"--extract-media={MEDIA_DIR}")
        return cmd

    def convert(self, src: Path, out_dir: Path) -> ConversionResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                self.command(src),
                cwd=out_dir,
                capture_output=True,
                timeout=PANDOC_TIMEOUT_S,
                check=False,  # the return code is checked below
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(f"pandoc timed out after {PANDOC_TIMEOUT_S} s") from exc
        stderr = proc.stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise ConversionError(f"pandoc exited with code {proc.returncode}: {stderr.strip()}")
        try:
            markdown = proc.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConversionError(f"pandoc output is not valid UTF-8: {exc}") from exc
        media_root = out_dir / MEDIA_DIR
        media = (
            sorted(p.relative_to(out_dir) for p in media_root.rglob("*") if p.is_file())
            if media_root.exists()
            else []
        )
        return ConversionResult(
            markdown=markdown,
            tool=self.name,
            tool_version=self.version(),
            options=self.options(),
            media=media,
            warnings=[line.strip() for line in stderr.splitlines() if line.strip()],
            duration_s=round(time.monotonic() - start, 3),
        )
