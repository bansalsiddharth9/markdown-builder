from __future__ import annotations

from pathlib import Path

import pytest
from specconv.config import (
    ConfigError,
    FetchConfig,
    Source,
    SpecconvConfig,
    load_config,
    load_sources,
)


def test_repo_config_files_load(repo_root: Path) -> None:
    cfg = load_config(repo_root / "config/specconv.yaml")
    assert cfg.pdf.min_chars_per_page == 50
    assert cfg.pdf.min_text_page_ratio == 0.7
    assert cfg.html.strip_tags == ("script", "style", "noscript")
    assert cfg.fetch == FetchConfig(timeout_s=30.0, retries=3, user_agent="specconv/1.0",
                                    browsers_path=repo_root / ".playwright")
    assert [s.id for s in load_sources(repo_root / "config/sources.yaml")] == ["ttml2"]


def test_defaults_when_no_default_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_config() == SpecconvConfig()


def test_explicit_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_partial_file_keeps_other_defaults(write) -> None:
    cfg = load_config(write("c.yaml", "pdf:\n  min_text_page_ratio: 0.5\n"))
    assert cfg.pdf.min_text_page_ratio == 0.5
    assert cfg.pdf.min_chars_per_page == 50
    assert cfg.fetch.browsers_path is None


def test_browsers_path_relative_to_config_file(write, tmp_path: Path) -> None:
    cfg = load_config(write("sub/c.yaml", "fetch:\n  browsers_path: ../browsers\n"))
    assert cfg.fetch.browsers_path == (tmp_path / "browsers").resolve()


@pytest.mark.parametrize("text, message", [
    ("pdff: {}\n", "unknown keys"),
    ("pdf:\n  min_chars: 5\n", "unknown keys"),
    ("pdf:\n  min_chars_per_page: fifty\n", "expected an integer"),
    ("pdf:\n  min_chars_per_page: true\n", "expected an integer"),
    ("pdf:\n  min_chars_per_page: 0\n", "at least 1"),
    ("pdf:\n  min_text_page_ratio: 70\n", "between"),
    ("html:\n  strip_tags: script\n", "list of non-empty strings"),
    ("fetch:\n  retries: -1\n", "at least 0"),
    ("fetch:\n  user_agent: ''\n", "non-empty string"),
    ("- a list\n", "expected a mapping"),
    ("pdf: [\n", "invalid YAML"),
])
def test_invalid_config_rejected(write, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(write("c.yaml", text))


def test_sources_parsed_with_defaults(write) -> None:
    path = write("s.yaml", """
sources:
  - id: ttml2
    url: https://www.w3.org/TR/ttml2/
  - id: vendor-caption-faq
    url: https://example.com/captions/faq
    render: browser
    clean: main-content
    drop_selectors: [".cookie-banner", "#sidebar"]
""")
    assert load_sources(path) == [
        Source(id="ttml2", url="https://www.w3.org/TR/ttml2/"),
        Source(id="vendor-caption-faq", url="https://example.com/captions/faq", render="browser",
               clean="main-content", drop_selectors=(".cookie-banner", "#sidebar")),
    ]


def test_empty_sources_file(write) -> None:
    assert load_sources(write("s.yaml", "")) == []
    assert load_sources(write("s2.yaml", "sources:\n")) == []


@pytest.mark.parametrize("item, message", [
    ("- url: https://a.org/\n", "'id' is required"),
    ("- id: a\n", "'url' is required"),
    ("- {id: a, url: https://a.org/}\n- {id: a, url: https://b.org/}\n", "duplicate id"),
    ("- {id: ../evil, url: https://a.org/}\n", "may only contain"),
    ("- {id: .hidden, url: https://a.org/}\n", "may only contain"),
    ("- {id: a, url: ftp://a.org/}\n", "http:// or https://"),
    ("- {id: a, url: https://a.org/, render: chrome}\n", "render"),
    ("- {id: a, url: https://a.org/, clean: all}\n", "clean"),
    ("- {id: a, url: https://a.org/, drop_selectors: [nav]}\n", "only allowed with clean: main-content"),
    ("- {id: a, url: https://a.org/, wait: 5}\n", "unknown keys"),
])
def test_invalid_sources_rejected(write, item: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_sources(write("s.yaml", "sources:\n" + item))
