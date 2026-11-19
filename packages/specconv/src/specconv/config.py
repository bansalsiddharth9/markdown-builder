"""Load and validate ``specconv.yaml`` and ``sources.yaml``.

Loading is strict: unknown keys, wrong types and invalid values raise
ConfigError, which the CLI turns into exit code 2. A typo in a config file
must never be silently ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/specconv.yaml")

RENDER_MODES = ("static", "browser")
CLEAN_MODES = ("none", "main-content")
SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ConfigError(ValueError):
    """A configuration file is missing, malformed or has invalid values."""


@dataclass(frozen=True)
class PdfConfig:
    min_chars_per_page: int = 50
    min_text_page_ratio: float = 0.7
    # Pinned Docling models (specconv download-models). Relative paths are
    # resolved against the config file's folder; built-in default: ./models.
    models_path: Path = Path("models")


@dataclass(frozen=True)
class HtmlConfig:
    strip_tags: tuple[str, ...] = ("script", "style", "noscript")


@dataclass(frozen=True)
class FetchConfig:
    timeout_s: float = 30.0
    retries: int = 3
    user_agent: str = "specconv/1.0"
    # Where Playwright's Chromium is installed. Relative paths are resolved
    # against the config file's folder. None = Playwright's default location.
    browsers_path: Path | None = None


@dataclass(frozen=True)
class SpecconvConfig:
    pdf: PdfConfig = field(default_factory=PdfConfig)
    html: HtmlConfig = field(default_factory=HtmlConfig)
    fetch: FetchConfig = field(default_factory=FetchConfig)
    # Local HTML file (path relative to the input folder, or file name) -> the
    # document's own URL, used to rewrite full-URL links to its own sections.
    self_urls: dict[str, str] = field(default_factory=dict)

    def self_url_for(self, rel_path: str) -> str | None:
        """The configured own URL for a local file, by relative path, then file name."""
        return self.self_urls.get(rel_path) or self.self_urls.get(Path(rel_path).name)


@dataclass(frozen=True)
class Source:
    """One web page from sources.yaml."""

    id: str
    url: str
    render: str = "static"  # static | browser
    clean: str = "none"  # none | main-content
    drop_selectors: tuple[str, ...] = ()


# ---------------------------------------------------------------- helpers


def _read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"{path}: file not found") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}; allowed: {sorted(allowed)}")


def _int(value: Any, where: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: expected an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{where}: must be at least {minimum}, got {value}")
    return value


def _float(value: Any, where: str, lo: float | None = None, hi: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: expected a number, got {value!r}")
    value = float(value)
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ConfigError(f"{where}: must be between {lo} and {hi}, got {value}")
    return value


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _str_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{where}: expected a list of non-empty strings, got {value!r}")
    return tuple(value)


# ---------------------------------------------------------- specconv.yaml


def load_config(path: Path | None = None) -> SpecconvConfig:
    """Load specconv.yaml.

    With path=None the default file is used if it exists, otherwise built-in
    defaults. An explicitly given path must exist.
    """
    if path is None:
        if not DEFAULT_CONFIG_PATH.exists():
            return SpecconvConfig()
        path = DEFAULT_CONFIG_PATH
    data = _mapping(_read_yaml(path), str(path))
    _reject_unknown(data, {"pdf", "html", "fetch", "self_urls"}, str(path))
    base = path.resolve().parent

    pdf_d = _mapping(data.get("pdf"), f"{path}: pdf")
    _reject_unknown(
        pdf_d, {"min_chars_per_page", "min_text_page_ratio", "models_path"}, f"{path}: pdf"
    )
    pdf = PdfConfig(
        min_chars_per_page=_int(
            pdf_d.get("min_chars_per_page", PdfConfig.min_chars_per_page),
            f"{path}: pdf.min_chars_per_page",
            minimum=1,
        ),
        min_text_page_ratio=_float(
            pdf_d.get("min_text_page_ratio", PdfConfig.min_text_page_ratio),
            f"{path}: pdf.min_text_page_ratio",
            lo=0.0,
            hi=1.0,
        ),
        models_path=(base / _str(pdf_d["models_path"], f"{path}: pdf.models_path")).resolve()
        if "models_path" in pdf_d
        else PdfConfig.models_path,
    )

    html_d = _mapping(data.get("html"), f"{path}: html")
    _reject_unknown(html_d, {"strip_tags"}, f"{path}: html")
    html = HtmlConfig(
        strip_tags=_str_list(html_d["strip_tags"], f"{path}: html.strip_tags")
        if "strip_tags" in html_d
        else HtmlConfig.strip_tags,
    )

    fetch_d = _mapping(data.get("fetch"), f"{path}: fetch")
    _reject_unknown(
        fetch_d, {"timeout_s", "retries", "user_agent", "browsers_path"}, f"{path}: fetch"
    )
    browsers_path = None
    if fetch_d.get("browsers_path") is not None:
        browsers_path = base / _str(fetch_d["browsers_path"], f"{path}: fetch.browsers_path")
    fetch = FetchConfig(
        timeout_s=_float(
            fetch_d.get("timeout_s", FetchConfig.timeout_s), f"{path}: fetch.timeout_s", lo=1.0
        ),
        retries=_int(
            fetch_d.get("retries", FetchConfig.retries), f"{path}: fetch.retries", minimum=0
        ),
        user_agent=_str(
            fetch_d.get("user_agent", FetchConfig.user_agent), f"{path}: fetch.user_agent"
        ),
        browsers_path=browsers_path.resolve() if browsers_path else None,
    )
    self_urls: dict[str, str] = {}
    for name, url in _mapping(data.get("self_urls"), f"{path}: self_urls").items():
        where = f"{path}: self_urls.{name}"
        url = _str(url, where)
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"{where}: must start with http:// or https://, got {url!r}")
        self_urls[_str(name, where)] = url
    return SpecconvConfig(pdf=pdf, html=html, fetch=fetch, self_urls=self_urls)


# ------------------------------------------------------------ sources.yaml


def load_sources(path: Path) -> list[Source]:
    """Load sources.yaml. Ids must be unique and safe to use as folder names."""
    data = _mapping(_read_yaml(path), str(path))
    _reject_unknown(data, {"sources"}, str(path))
    items = data.get("sources")
    if items is None:
        return []
    if not isinstance(items, list):
        raise ConfigError(f"{path}: 'sources' must be a list")

    sources: list[Source] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        where = f"{path}: sources[{i}]"
        item = _mapping(item, where)
        _reject_unknown(item, {"id", "url", "render", "clean", "drop_selectors"}, where)
        if "id" not in item:
            raise ConfigError(f"{where}: 'id' is required")
        sid = _str(item["id"], f"{where}.id")
        if not SOURCE_ID_RE.match(sid):
            raise ConfigError(
                f"{where}.id: {sid!r} may only contain letters, digits, '.', '_' and '-' "
                "and must start with a letter or digit"
            )
        if sid in seen:
            raise ConfigError(f"{where}.id: duplicate id {sid!r}")
        seen.add(sid)

        if "url" not in item:
            raise ConfigError(f"{where}: 'url' is required")
        url = _str(item["url"], f"{where}.url")
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"{where}.url: must start with http:// or https://, got {url!r}")

        render = item.get("render", "static")
        if render not in RENDER_MODES:
            raise ConfigError(
                f"{where}.render: must be one of {list(RENDER_MODES)}, got {render!r}"
            )
        clean = item.get("clean", "none")
        if clean not in CLEAN_MODES:
            raise ConfigError(f"{where}.clean: must be one of {list(CLEAN_MODES)}, got {clean!r}")

        drop: tuple[str, ...] = ()
        if "drop_selectors" in item:
            drop = _str_list(item["drop_selectors"], f"{where}.drop_selectors")
            if clean != "main-content":
                raise ConfigError(f"{where}.drop_selectors: only allowed with clean: main-content")

        sources.append(Source(id=sid, url=url, render=render, clean=clean, drop_selectors=drop))
    return sources
