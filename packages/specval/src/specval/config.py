"""Load and validate ``specval.yaml`` and ``acceptance.yaml``.

Loading is strict: unknown keys, wrong types and invalid values raise
ConfigError, which the CLI turns into exit code 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/specval.yaml")


class ConfigError(ValueError):
    """A configuration file is missing, malformed or has invalid values."""


@dataclass(frozen=True)
class TextCoverage:
    fail_below: float = 0.98
    warn_below: float = 0.995
    min_sentence_chars: int = 20
    fuzzy_ratio: float = 95.0


@dataclass(frozen=True)
class SamplePages:
    count: int = 3
    seed: int = 42


@dataclass(frozen=True)
class SpecvalConfig:
    text_coverage: TextCoverage = field(default_factory=TextCoverage)
    pdf_table_count_tolerance: int = 1
    always_review_table_patterns: tuple[str, ...] = ("code", "character set", "command", "control")
    sample_pages: SamplePages = field(default_factory=SamplePages)
    # Extra V4 token families: name -> regexes, merged with the built-in families.
    token_families: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class AcceptanceQuestion:
    doc: str
    question: str
    expect: str


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


def _float(value: Any, where: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: expected a number, got {value!r}")
    if not lo <= value <= hi:
        raise ConfigError(f"{where}: must be between {lo} and {hi}, got {value}")
    return float(value)


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _str_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{where}: expected a list of non-empty strings, got {value!r}")
    return tuple(value)


# ------------------------------------------------------------ specval.yaml


def load_config(path: Path | None = None) -> SpecvalConfig:
    """Load specval.yaml.

    With path=None the default file is used if it exists, otherwise built-in
    defaults. An explicitly given path must exist.
    """
    if path is None:
        if not DEFAULT_CONFIG_PATH.exists():
            return SpecvalConfig()
        path = DEFAULT_CONFIG_PATH
    data = _mapping(_read_yaml(path), str(path))
    _reject_unknown(
        data,
        {
            "text_coverage",
            "pdf_table_count_tolerance",
            "always_review_table_patterns",
            "sample_pages",
            "token_families",
        },
        str(path),
    )
    d = SpecvalConfig()

    tc_d = _mapping(data.get("text_coverage"), f"{path}: text_coverage")
    _reject_unknown(
        tc_d,
        {"fail_below", "warn_below", "min_sentence_chars", "fuzzy_ratio"},
        f"{path}: text_coverage",
    )
    tc = TextCoverage(
        fail_below=_float(
            tc_d.get("fail_below", d.text_coverage.fail_below),
            f"{path}: text_coverage.fail_below",
            0.0,
            1.0,
        ),
        warn_below=_float(
            tc_d.get("warn_below", d.text_coverage.warn_below),
            f"{path}: text_coverage.warn_below",
            0.0,
            1.0,
        ),
        min_sentence_chars=_int(
            tc_d.get("min_sentence_chars", d.text_coverage.min_sentence_chars),
            f"{path}: text_coverage.min_sentence_chars",
            minimum=1,
        ),
        fuzzy_ratio=_float(
            tc_d.get("fuzzy_ratio", d.text_coverage.fuzzy_ratio),
            f"{path}: text_coverage.fuzzy_ratio",
            0.0,
            100.0,
        ),
    )
    if tc.warn_below < tc.fail_below:
        raise ConfigError(f"{path}: text_coverage.warn_below must be >= fail_below")

    sp_d = _mapping(data.get("sample_pages"), f"{path}: sample_pages")
    _reject_unknown(sp_d, {"count", "seed"}, f"{path}: sample_pages")
    sp = SamplePages(
        count=_int(
            sp_d.get("count", d.sample_pages.count), f"{path}: sample_pages.count", minimum=0
        ),
        seed=_int(sp_d.get("seed", d.sample_pages.seed), f"{path}: sample_pages.seed"),
    )

    families: dict[str, tuple[str, ...]] = {}
    for name, regexes in _mapping(data.get("token_families"), f"{path}: token_families").items():
        where = f"{path}: token_families.{name}"
        if isinstance(regexes, str):
            regexes = [regexes]
        patterns = _str_list(regexes, where)
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigError(f"{where}: invalid regex {pattern!r}: {exc}") from exc
        families[_str(name, where)] = patterns

    return SpecvalConfig(
        text_coverage=tc,
        pdf_table_count_tolerance=_int(
            data.get("pdf_table_count_tolerance", d.pdf_table_count_tolerance),
            f"{path}: pdf_table_count_tolerance",
            minimum=0,
        ),
        always_review_table_patterns=_str_list(
            data["always_review_table_patterns"], f"{path}: always_review_table_patterns"
        )
        if "always_review_table_patterns" in data
        else d.always_review_table_patterns,
        sample_pages=sp,
        token_families=families,
    )


# --------------------------------------------------------- acceptance.yaml


def load_acceptance(path: Path) -> list[AcceptanceQuestion]:
    data = _mapping(_read_yaml(path), str(path))
    _reject_unknown(data, {"questions"}, str(path))
    items = data.get("questions")
    if items is None:
        return []
    if not isinstance(items, list):
        raise ConfigError(f"{path}: 'questions' must be a list")
    questions = []
    for i, item in enumerate(items):
        where = f"{path}: questions[{i}]"
        item = _mapping(item, where)
        _reject_unknown(item, {"doc", "question", "expect"}, where)
        for key in ("doc", "question", "expect"):
            if key not in item:
                raise ConfigError(f"{where}: '{key}' is required")
        expect = item["expect"]
        if not isinstance(expect, str):
            # YAML reads unquoted 0x25 as 37 and 1.10 as 1.1; never guess the intended text.
            raise ConfigError(
                f'{where}.expect: must be a quoted string, got {expect!r} (write expect: "...")'
            )
        questions.append(
            AcceptanceQuestion(
                doc=_str(item["doc"], f"{where}.doc"),
                question=_str(item["question"], f"{where}.question"),
                expect=_str(expect, f"{where}.expect"),
            )
        )
    return questions
