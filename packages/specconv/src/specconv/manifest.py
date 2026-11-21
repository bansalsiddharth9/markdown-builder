"""specconv's use of the shared manifest: load the previous run, save this one."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from common.manifest import Manifest, read_manifest, write_manifest

from specconv import __version__

MANIFEST_NAME = "manifest.json"


def manifest_path(output_dir: Path) -> Path:
    """Path of the manifest file in the output folder."""
    return output_dir / MANIFEST_NAME


def load_previous(output_dir: Path) -> Manifest:
    """The manifest of the previous run, or an empty one on the first run.

    A corrupt manifest raises ManifestError rather than being replaced, so
    a damaged file is noticed instead of silently forcing a full re-run.
    """
    path = manifest_path(output_dir)
    if not path.exists():
        return Manifest()
    return read_manifest(path)


def save(manifest: Manifest, output_dir: Path) -> Path:
    """Stamp the manifest with generator and time, write it, and return its path."""
    manifest.generator = f"specconv {__version__}"
    manifest.generated_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = manifest_path(output_dir)
    write_manifest(manifest, path)
    return path
