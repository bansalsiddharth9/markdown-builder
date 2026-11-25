"""Save legacy Word .doc files as .docx with LibreOffice (deviation DOC1).

    soffice -env:UserInstallation=file://<private profile> --headless --norestore
            --convert-to docx --outdir OUT SRC.doc

Pandoc cannot read .doc, and the libraries that read it directly return plain
text only (no headings, tables or images). LibreOffice reads the format well,
so a .doc becomes a .docx first and then takes the normal DOCX route.

- LibreOffice runs as a separate program (MPL-2.0), like Pandoc.
- Each run gets its own throw-away profile folder: two LibreOffice processes
  sharing the default profile block each other, and a crashed run can leave a
  lock behind that stops every later one.
- Only real Word 97-2003 files (OLE2 signature) are accepted. LibreOffice
  opens almost anything, so a renamed text file would otherwise "convert".
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from functools import cache
from pathlib import Path

from specconv.converters.base import ConversionError

# Name of LibreOffice's .docx in the output folder; specval reads it for the
# structure checks (headings, tables), so the name is part of the output layout.
DOCX_NAME = "libreoffice.docx"
TIMEOUT_S = 300  # a large .doc takes seconds; a hung LibreOffice must not stall the run
OLE2_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")  # every Word 97-2003 file starts so


def find_soffice() -> Path | None:
    """The LibreOffice program (soffice, or libreoffice on some systems), or None."""
    found = shutil.which("soffice") or shutil.which("libreoffice")
    return Path(found) if found else None


@cache
def _version(soffice: Path) -> str:
    """'7.3.7.2' from 'LibreOffice 7.3.7.2 30(Build:2)'; cached per program."""
    out = subprocess.run(
        [str(soffice), "--version"], capture_output=True, text=True, timeout=60, check=False
    ).stdout
    match = re.search(r"(\d+(?:\.\d+)+)", out)
    return match.group(1) if match else out.strip() or "unknown"


def version() -> str:
    """LibreOffice's version; raises ConversionError when it is not installed."""
    return _version(_require())


def _require() -> Path:
    """The soffice path, or ConversionError with what to install."""
    soffice = find_soffice()
    if soffice is None:
        raise ConversionError(
            "LibreOffice (soffice) not found; it is needed for .doc files "
            "(install libreoffice-writer, or save the file as .docx)"
        )
    return soffice


def doc_to_docx(src: Path, out_dir: Path) -> Path:
    """Save src (.doc) as out_dir/DOCX_NAME and return that path.

    Raises ConversionError when the file is not a Word 97-2003 file, LibreOffice
    is missing, fails, times out or writes no file.
    """
    with src.open("rb") as fh:
        if fh.read(len(OLE2_SIGNATURE)) != OLE2_SIGNATURE:
            raise ConversionError(f"{src.name}: not a Word 97-2003 file (.doc)")
    soffice = _require()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="specconv-lo-") as tmp:
        profile, target = Path(tmp) / "profile", Path(tmp) / "out"
        cmd = [
            str(soffice),
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--norestore",
            "--convert-to",
            "docx",
            "--outdir",
            str(target),
            str(src),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(f"LibreOffice timed out after {TIMEOUT_S} s") from exc
        made = target / f"{src.stem}.docx"
        if proc.returncode != 0 or not made.is_file():
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            raise ConversionError(
                f"LibreOffice could not convert {src.name} (exit {proc.returncode})"
                + (f": {detail[-1]}" if detail else "")
            )
        dest = out_dir / DOCX_NAME
        shutil.move(made, dest)
    return dest
