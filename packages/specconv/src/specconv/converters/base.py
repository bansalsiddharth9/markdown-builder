"""The converter interface (design: Converter interface).

Adding a tool means adding one class implementing Converter and one routing rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

MEDIA_DIR = "media"  # extracted images, relative to the document folder


class ConversionError(RuntimeError):
    """A converter failed; the message says why."""


@dataclass
class ConversionResult:
    markdown: str
    tool: str  # e.g. "docling"
    tool_version: str  # e.g. "2.151.0"
    options: dict[str, Any]  # exact options used, for the manifest
    media: list[Path] = field(default_factory=list)  # extracted images, relative to out_dir
    warnings: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)  # tool-specific facts for conversion.json


class Converter(Protocol):
    name: str

    def version(self) -> str: ...

    def convert(self, src: Path, out_dir: Path) -> ConversionResult: ...
