"""PandocConverter for HTML and DOCX (design: Per-tool settings).

    pandoc SRC -f html -t gfm --wrap=none --extract-media=media
    pandoc SRC -f docx --track-changes=accept -t gfm --wrap=none --extract-media=media

Pandoc runs with the output folder as its working directory, so extracted
media land in OUT/media and links are already relative ("media/...").
--resource-path is the source's folder, so local images referenced by HTML
resolve. Markdown is read from stdout; stderr lines become warnings.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from specconv.converters.base import MEDIA_DIR, ConversionError, ConversionResult
from specconv.deps import find_pandoc, pandoc_version

PANDOC_TIMEOUT_S = 900  # design: 15 minutes per document


class PandocConverter:
    """Convert HTML or DOCX with Pandoc to GitHub-flavoured Markdown."""

    FORMATS = ("html", "docx")

    def __init__(self, from_format: str, pandoc: Path | None = None) -> None:
        if from_format not in self.FORMATS:
            raise ValueError(f"unsupported Pandoc input format {from_format!r}")
        found = pandoc or find_pandoc()
        if found is None:
            raise ConversionError("pandoc not found")
        self.pandoc = found
        self.from_format = from_format
        self.name = "pandoc"
        self._version: str | None = None

    def version(self) -> str:
        if self._version is None:
            self._version = pandoc_version(self.pandoc)
        return self._version

    def options(self) -> dict[str, object]:
        opts: dict[str, object] = {
            "from": self.from_format,
            "to": "gfm",
            "wrap": "none",
            "extract_media": MEDIA_DIR,
        }
        if self.from_format == "docx":
            opts["track_changes"] = "accept"
        return opts

    def command(self, src: Path, resource_dir: Path | None = None) -> list[str]:
        cmd = [str(self.pandoc), str(src.resolve()), "-f", self.from_format]
        if self.from_format == "docx":
            cmd.append("--track-changes=accept")
        cmd += [
            "-t",
            "gfm",
            "--wrap=none",
            f"--extract-media={MEDIA_DIR}",
            f"--resource-path={(resource_dir or src.parent).resolve()}",
        ]
        return cmd

    def convert(
        self, src: Path, out_dir: Path, resource_dir: Path | None = None
    ) -> ConversionResult:
        """resource_dir: where relative image paths are looked up; defaults to
        src's folder. Set it when src is a cleaned copy of the original."""
        out_dir.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                self.command(src, resource_dir),
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
