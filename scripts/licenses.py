"""Generate LICENSES.md from the installed environment (design principle 5).

    uv run python scripts/licenses.py          # rewrite LICENSES.md
    uv run python scripts/licenses.py --check  # exit 1 if stale or a license is not allowed

Python packages are read from the environment's metadata. System tools and
model weights cannot be discovered that way and are listed by hand below;
update them when a tool or model changes.
"""

from __future__ import annotations

import argparse
import re
import sys
from importlib.metadata import distributions
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LICENSES_MD = REPO_ROOT / "LICENSES.md"
OWN_PACKAGES = {"common", "specconv", "specval", "specweb", "spec-md"}

# Licenses allowed for use inside a company. Anything else fails --check
# until a person reviews it and adds it here.
ALLOWED = {
    "0BSD",
    "Apache-2.0",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSL-1.0",
    "CC0-1.0",
    "CNRI-Python",
    "ISC",
    "LLVM-exception",
    "MIT",
    "MIT-0",
    "MIT-CMU",
    "MPL-2.0",
    "PSF-2.0",
    "Zlib",
    "dependency licenses",
}

# Classifier / free-text labels -> SPDX ids
NORMALIZE = {
    "MIT License": "MIT",
    "OSI Approved; MIT License": "MIT",
    "Apache Software License": "Apache-2.0",
    "Apache 2.0 License": "Apache-2.0",
    "BSD License": "BSD",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}

SYSTEM_TOOLS = [
    # name, version, license, how it is used
    (
        "Pandoc",
        "3.14",
        "GPL-2.0-or-later",
        "run as a separate program (HTML and DOCX conversion); not linked or distributed",
    ),
    (
        "Chromium headless shell (Playwright build)",
        "159.0.8154.6",
        "BSD-3-Clause and others",
        "headless browser for render: browser fetching; installed by 'playwright install chromium'",
    ),
    (
        "FFmpeg (Playwright build)",
        "1013",
        "LGPL-2.1-or-later",
        "downloaded by Playwright alongside Chromium; not used by specconv",
    ),
    ("uv", "0.13.9", "MIT OR Apache-2.0", "environment and lock file management"),
    (
        "Caddy (Docker image caddy:2.10.2)",
        "2.10.2",
        "Apache-2.0",
        "reverse proxy with HTTPS in deploy/docker-compose.yml; separate container",
    ),
    (
        "htmx",
        "2.0.4",
        "0BSD",
        "served by specweb as static/htmx.min.js (from unpkg.com/htmx.org@2.0.4)",
    ),
    ("Python", "3.12", "PSF-2.0", "runtime"),
]

from specconv.models import PINNED_MODELS  # noqa: E402  (single source of the pins)

MODEL_WEIGHTS = [
    # repository, revision, license, use
    (m.repo_id, f"`{m.revision}`", m.license, m.use)
    for m in PINNED_MODELS
]


def _license_of(dist) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()
    classifiers = [
        c.split("::")[-1].strip()
        for c in (meta.get_all("Classifier") or [])
        if c.startswith("License ::")
    ]
    # "License :: OSI Approved" alone says nothing; keep the specific classifier.
    specific = [c for c in classifiers if c != "OSI Approved"]
    classifiers = specific or classifiers
    if classifiers:
        return " OR ".join(NORMALIZE.get(c, c) for c in classifiers)
    raw = (meta.get("License") or "").strip()
    if raw:
        return NORMALIZE.get(raw, raw.splitlines()[0][:60])
    return _license_from_file(dist)


# Opening words of the common license texts, for packages whose metadata names
# no license but which ship the license file (e.g. huey).
LICENSE_TEXTS = {
    "Permission is hereby granted, free of charge, to any person obtaining a copy": "MIT",
    "Apache License": "Apache-2.0",
    "Redistribution and use in source and binary forms, with or without": "BSD",
}


def _license_from_file(dist) -> str:
    for file in dist.files or []:
        if "LICEN" not in file.name.upper() and "COPYING" not in file.name.upper():
            continue
        try:
            text = " ".join(Path(dist.locate_file(file)).read_text(errors="replace").split())
        except OSError:
            continue
        for words, spdx in LICENSE_TEXTS.items():
            if words in text:
                return f"{spdx} (from {file.name})"
    return "UNKNOWN"


def _license_ids(expression: str) -> set[str]:
    """Split an SPDX-like expression into license ids (ignoring AND/OR/WITH)."""
    expression = re.sub(r"\s*\(from [^)]*\)", "", expression)
    parts = re.split(r"\s+(?:AND|OR|WITH)\s+|[(),;]", expression)
    return {NORMALIZE.get(p.strip(), p.strip()) for p in parts if p.strip()}


def collect() -> list[tuple[str, str, str]]:
    rows = {}
    for dist in distributions():
        name = dist.metadata["Name"]
        if name.lower() in OWN_PACKAGES:
            continue
        lic = NORMALIZE.get(_license_of(dist), _license_of(dist))
        rows[name.lower()] = (name, dist.version, lic)
    return [rows[k] for k in sorted(rows)]


def disallowed(packages: list[tuple[str, str, str]]) -> list[str]:
    return [f"{n} {v}: {lic}" for n, v, lic in packages if not _license_ids(lic) <= ALLOWED]


def render(packages: list[tuple[str, str, str]]) -> str:
    out = [
        "# Licenses",
        "",
        "Every tool, library and model weight used by this project, with its license "
        "(design principle 5: open source only, usable inside a company).",
        "",
        "Generated by `uv run python scripts/licenses.py`; `tests/test_licenses.py` fails "
        "when this file is out of date or a package's license is not on the allowed list "
        "in that script. The system tool and model tables are maintained by hand in the script.",
        "",
        "## System tools",
        "",
        "| Tool | Version | License | Use |",
        "| --- | --- | --- | --- |",
        *[f"| {n} | {v} | {lic} | {use} |" for n, v, lic, use in SYSTEM_TOOLS],
        "",
        "Pandoc is GPL: it is only executed as a separate program and not distributed with "
        "this code, which GPL permits for internal use. If the Docker image is ever shipped "
        "outside the company, Pandoc's GPL terms apply to that image.",
        "",
        "## Model weights",
        "",
        "With the settings in the design (no OCR, accurate table mode, no enrichments) "
        "Docling uses only these two models.",
        "",
        "| Repository | Revision | License | Use |",
        "| --- | --- | --- | --- |",
        *[f"| `{r}` | {rev} | {lic} | {use} |" for r, rev, lic, use in MODEL_WEIGHTS],
        "",
        "Both are pinned to the commits above (`specconv/models.py`), downloaded once with "
        "`specconv download-models` into `models/`, and loaded from there with Hugging Face "
        "downloads switched off.",
        "",
        "Docling also installs RapidOCR (Apache-2.0) as a dependency; OCR is disabled, so "
        "its models are never downloaded.",
        "",
        "## Python packages",
        "",
        "All packages installed in the project environment (direct and indirect), "
        "excluding this project's own packages.",
        "",
        "| Package | Version | License |",
        "| --- | --- | --- |",
        *[f"| {n} | {v} | {lic} |" for n, v, lic in packages],
        "",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if LICENSES.md is stale or a license is not allowed",
    )
    args = parser.parse_args()
    packages = collect()
    bad = disallowed(packages)
    text = render(packages)
    if args.check:
        stale = not LICENSES_MD.exists() or LICENSES_MD.read_text(encoding="utf-8") != text
        if stale:
            print(
                "LICENSES.md is out of date; run: uv run python scripts/licenses.py",
                file=sys.stderr,
            )
        for line in bad:
            print(f"license not on the allowed list: {line}", file=sys.stderr)
        return 1 if stale or bad else 0
    LICENSES_MD.write_text(text, encoding="utf-8")
    for line in bad:
        print(f"WARNING: license not on the allowed list: {line}", file=sys.stderr)
    print(f"wrote {LICENSES_MD} ({len(packages)} packages)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
