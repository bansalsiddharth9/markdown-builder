from __future__ import annotations

from pathlib import Path

import pytest
from specval.config import (
    AcceptanceQuestion,
    ConfigError,
    SpecvalConfig,
    load_acceptance,
    load_config,
)


def test_repo_config_matches_design_defaults(repo_root: Path) -> None:
    assert load_config(repo_root / "config/specval.yaml") == SpecvalConfig()
    assert load_acceptance(repo_root / "config/acceptance.yaml") == []


def test_defaults_when_no_default_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.text_coverage.fail_below == 0.98 and cfg.text_coverage.warn_below == 0.995
    assert cfg.sample_pages.count == 3 and cfg.sample_pages.seed == 42


def test_token_families_parsed(write) -> None:
    cfg = load_config(write("v.yaml", "token_families:\n  smpte: ['\\d{2}:\\d{2}']\n  one: 'x+'\n"))
    assert cfg.token_families == {"smpte": (r"\d{2}:\d{2}",), "one": ("x+",)}


@pytest.mark.parametrize("text, message", [
    ("text_coverage: {fail_below: 98}\n", "between"),
    ("text_coverage: {fail_below: 0.99, warn_below: 0.98}\n", "warn_below must be >= fail_below"),
    ("text_coverage: {fuzzy: 90}\n", "unknown keys"),
    ("sample_pages: {count: -1}\n", "at least 0"),
    ("pdf_table_count_tolerance: 1.5\n", "expected an integer"),
    ("token_families: {bad: '(unclosed'}\n", "invalid regex"),
    ("always_review_table_patterns: code\n", "list of non-empty strings"),
    ("checks: {}\n", "unknown keys"),
])
def test_invalid_config_rejected(write, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(write("v.yaml", text))


def test_acceptance_questions(write) -> None:
    path = write("a.yaml", """
questions:
  - doc: cta-608-e
    question: Maximum rows in roll-up mode?
    expect: "4 rows"
  - doc: cta-608-e
    question: Which byte starts roll-up?
    expect: "0x25"
""")
    assert load_acceptance(path) == [
        AcceptanceQuestion("cta-608-e", "Maximum rows in roll-up mode?", "4 rows"),
        AcceptanceQuestion("cta-608-e", "Which byte starts roll-up?", "0x25"),
    ]


@pytest.mark.parametrize("item, message", [
    # unquoted, YAML would read these as the numbers 37 and 1.1
    ("- {doc: a, question: q, expect: 0x25}\n", "must be a quoted string"),
    ("- {doc: a, question: q, expect: 1.10}\n", "must be a quoted string"),
    ("- {doc: a, question: q}\n", "'expect' is required"),
    ("- {doc: a, question: q, expect: ''}\n", "non-empty string"),
    ("- {doc: a, question: q, expect: x, page: 3}\n", "unknown keys"),
])
def test_invalid_acceptance_rejected(write, item: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_acceptance(write("a.yaml", "questions:\n" + item))
