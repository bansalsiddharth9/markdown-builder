"""Front matter for converted documents (design: Front matter).

On re-conversion, title, spec_version, manual_fixes and validated are carried
over from the existing file, and validated is reset to null if the Markdown
body changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from common.frontmatter import FrontMatter, FrontMatterError, parse_document

CARRIED_FIELDS = ("title", "spec_version", "manual_fixes", "validated")


@dataclass
class Previous:
    """The existing converted file, if any."""

    front_matter: FrontMatter
    body: str


@dataclass
class CarryOver:
    """The front matter to write, plus warnings for the manifest."""

    front_matter: FrontMatter
    warnings: list[str] = field(default_factory=list)


def read_previous(md_path: Path) -> Previous | None:
    """Read the existing converted file.

    Raises FrontMatterError if it exists but cannot be parsed: hand-edited
    fields would otherwise be silently lost, so the caller must fail the
    document instead of overwriting it.
    """
    if not md_path.exists():
        return None
    try:
        fm, body = parse_document(md_path.read_text(encoding="utf-8"))
    except (FrontMatterError, UnicodeDecodeError) as exc:
        raise FrontMatterError(
            f"{md_path}: existing front matter cannot be read ({exc}); fix or delete the "
            "file before converting again, so hand-edited fields are not lost"
        ) from exc
    return Previous(fm, body)


def carry_over(new: FrontMatter, new_body: str, previous: Previous | None) -> CarryOver:
    """Copy hand-edited fields from the previous file into the new front matter.

    Raises FrontMatterError when the previous file belongs to another source or id
    (deviation P7), so hand-edited fields never move to the wrong document. When the
    body changed, ``validated`` is reset and a manual_fixes warning is added (P8).
    """
    if previous is None:
        return CarryOver(new)
    old = previous.front_matter
    # Web sources have no source_file, local files no source_url.
    old_source = (old.source_type, old.source_url or old.source_file)
    new_source = (new.source_type, new.source_url or new.source_file)
    if old.id != new.id or old_source != new_source:
        raise FrontMatterError(
            f"output folder for {new.id!r} holds a different document "
            f"({old.source_url or old.source_file!r}, id {old.id!r}); move or delete it, so its "
            "hand-edited fields are not given to the wrong document"
        )
    for name in CARRIED_FIELDS:
        setattr(new, name, getattr(old, name))
    warnings: list[str] = []
    if previous.body != new_body:
        if old.validated is not None:
            new.validated = None
            warnings.append("Markdown body changed: 'validated' reset to null; review again")
        if old.manual_fixes:
            warnings.append(
                "Markdown body changed and the previous file lists manual_fixes; "
                "check whether hand edits to the body must be redone"
            )
    return CarryOver(new, warnings)
