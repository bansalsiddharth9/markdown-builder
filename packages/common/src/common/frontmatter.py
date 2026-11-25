"""Read and write the YAML front matter of converted Markdown files.

Format (design: Front matter)::

    ---
    id: cta-608-e
    title: ""
    ...
    validated: null
    ---
    <markdown body>

Only parsing, validation and rendering live here. Which values to write
(e.g. carry-over on re-conversion) is decided by specconv and specval.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, fields
from typing import Any

import yaml

DELIMITER = "---"
# "doc": legacy Word, converted through LibreOffice since deviation DOC1.
SOURCE_TYPES = frozenset({"pdf", "html", "docx", "web", "doc"})


class FrontMatterError(ValueError):
    """Front matter is missing, malformed or has invalid values."""


@dataclass
class FrontMatter:
    """Front matter fields, in the order they are written."""

    id: str
    title: str = ""  # filled by hand
    spec_version: str = ""  # filled by hand
    source_type: str = "pdf"  # pdf | html | docx | web
    source_file: str = ""  # input file name; empty for web sources (deviation F11)
    source_url: str | None = None  # set for web sources
    source_sha256: str = ""  # of source.<ext>; V7 checks it (deviation V-m)
    fetched_at: str | None = None  # set for web sources, ISO 8601 UTC
    converted_with: str = ""  # e.g. "docling 2.151.0"
    converted_at: str = ""  # ISO 8601 UTC
    flags: list[str] = field(default_factory=list)  # e.g. mixed_text_layer, suspect_empty
    manual_fixes: list[Any] = field(default_factory=list)  # edited by hand
    # Set by `specval approve`: {date, validator, by}; null until reviewed (deviation D2).
    validated: dict[str, Any] | None = None

    def validate(self) -> None:
        """Raise FrontMatterError if any value has the wrong type or is not allowed."""
        problems: list[str] = []
        if not isinstance(self.id, str) or not self.id:
            problems.append("id must be a non-empty string")
        for name in (
            "title",
            "spec_version",
            "source_file",
            "source_sha256",
            "converted_with",
            "converted_at",
        ):
            if not isinstance(getattr(self, name), str):
                problems.append(f"{name} must be a string")
        if self.source_type not in SOURCE_TYPES:
            problems.append(f"source_type must be one of {sorted(SOURCE_TYPES)}")
        for name in ("source_url", "fetched_at"):
            if getattr(self, name) is not None and not isinstance(getattr(self, name), str):
                problems.append(f"{name} must be a string or null")
        if not isinstance(self.flags, list) or not all(isinstance(f, str) for f in self.flags):
            problems.append("flags must be a list of strings")
        if not isinstance(self.manual_fixes, list):
            problems.append("manual_fixes must be a list")
        if self.validated is not None and not isinstance(self.validated, dict):
            problems.append("validated must be a mapping or null")
        if problems:
            raise FrontMatterError("invalid front matter: " + "; ".join(problems))

    def to_dict(self) -> dict[str, Any]:
        """All fields in declaration order, which is the order they are written."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrontMatter:
        """Build and validate front matter from parsed YAML.

        Unknown fields are an error, not ignored, so a typo in a hand edit is
        caught. Missing fields other than id get their defaults.
        """
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise FrontMatterError(f"front matter has unknown fields: {sorted(unknown)}")
        if "id" not in data:
            raise FrontMatterError("front matter is missing 'id'")
        fm = cls(**{k: _normalize(v) for k, v in data.items()})
        fm.validate()
        return fm


def _normalize(value: Any) -> Any:
    """YAML turns unquoted dates into date objects; keep them as ISO strings.

    Works through nested dicts and lists (e.g. ``validated.date``).
    """
    # datetime first: it is a subclass of date.
    if isinstance(value, dt.datetime):
        # UTC is written with "Z", as the converter writes its own timestamps.
        if value.tzinfo is not None and value.utcoffset() == dt.timedelta(0):
            return value.replace(tzinfo=None).isoformat() + "Z"
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def split_front_matter(text: str) -> tuple[str | None, str]:
    """Return (raw YAML or None, body). Body is everything after the closing line.

    The file must start with a '---' line; otherwise there is no front matter
    and the whole text is the body. An opening line without a closing one
    raises FrontMatterError.
    """
    # Split on "\n" only and strip "\r" when comparing, so files with Windows
    # line endings work and the body is returned byte for byte.
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != DELIMITER:
        return None, text
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r") == DELIMITER:
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    raise FrontMatterError("front matter is not closed with '---'")


def parse_document(text: str) -> tuple[FrontMatter, str]:
    """Parse a converted Markdown file into its front matter and body.

    Raises FrontMatterError if there is no front matter or it is invalid.
    """
    raw, body = split_front_matter(text)
    if raw is None:
        raise FrontMatterError("document has no front matter")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"front matter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise FrontMatterError("front matter must be a YAML mapping")
    return FrontMatter.from_dict(data), body


def render_front_matter(fm: FrontMatter) -> str:
    """Render front matter, including both '---' lines and a trailing newline.

    Validates first, so invalid front matter is never written.
    """
    fm.validate()
    body = yaml.safe_dump(
        fm.to_dict(),
        sort_keys=False,
        allow_unicode=True,
        # Lists and mappings of plain values inline ([a, b], {k: v}); nested ones as blocks.
        default_flow_style=None,
        width=10_000,  # never wrap long values
    )
    return f"{DELIMITER}\n{body}{DELIMITER}\n"


def render_document(fm: FrontMatter, body: str) -> str:
    """Front matter followed by the body, unchanged."""
    return render_front_matter(fm) + body
