"""Docling model weights, pinned to fixed revisions (deviation E8).

Docling would otherwise download its layout model from the moving revision
``main`` on first use. Here both models are downloaded once, at fixed commits,
into a local folder (``pdf.models_path``), and Docling is pointed at that
folder with Hugging Face downloads switched off, so a conversion never uses
the network and always uses the same weights.

Folder layout is the one Docling's own downloader uses:
``<models_path>/<owner>--<repo>/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PinnedModel:
    repo_id: str
    revision: str  # full commit id
    license: str
    use: str

    @property
    def folder(self) -> str:
        return self.repo_id.replace("/", "--")


PINNED_MODELS = (
    PinnedModel(
        repo_id="docling-project/docling-layout-heron",
        revision="8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
        license="Apache-2.0",
        use="Docling layout model (default)",
    ),
    PinnedModel(
        repo_id="docling-project/docling-models",
        revision="fc0f2d45e2218ea24bce5045f58a389aed16dc23",  # tag v2.3.0, as Docling requests
        license="CDLA-Permissive-2.0, Apache-2.0",
        use="TableFormer table structure model (accurate mode)",
    ),
)

REVISION_FILE = ".pinned-revision"  # written after a complete download


def missing_models(models_path: Path) -> list[PinnedModel]:
    """Models that are absent or were downloaded at a different revision."""
    missing = []
    for model in PINNED_MODELS:
        marker = models_path / model.folder / REVISION_FILE
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != model.revision:
            missing.append(model)
    return missing


def download_models(models_path: Path, force: bool = False) -> list[PinnedModel]:
    """Download every pinned model that is missing (or all with force). Needs network."""
    from huggingface_hub import snapshot_download

    todo = list(PINNED_MODELS) if force else missing_models(models_path)
    for model in todo:
        target = models_path / model.folder
        snapshot_download(repo_id=model.repo_id, revision=model.revision, local_dir=target)
        (target / REVISION_FILE).write_text(model.revision + "\n", encoding="utf-8")
    return todo
