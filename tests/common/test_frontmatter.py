from __future__ import annotations

import pytest
from common.frontmatter import (
    FrontMatter,
    FrontMatterError,
    parse_document,
    render_document,
    render_front_matter,
    split_front_matter,
)

BODY = "\n# 1 Scope\n\nText with trailing space   \n\n```xml\n<tt/>\n```\n"


def sample() -> FrontMatter:
    return FrontMatter(
        id="cta-608-e",
        source_type="pdf",
        source_file="CTA-608-E.pdf",
        source_sha256="3f1c" * 16,
        converted_with="docling 2.151.0",
        converted_at="2026-11-19T10:42:00Z",
        flags=["mixed_text_layer"],
    )


def test_round_trip_keeps_body_byte_for_byte() -> None:
    text = render_document(sample(), BODY)
    fm, body = parse_document(text)
    assert fm == sample()
    assert body == BODY


def test_fields_written_in_design_order() -> None:
    keys = [line.split(":", 1)[0] for line in render_front_matter(sample()).splitlines()[1:-1]]
    assert keys == [
        "id",
        "title",
        "spec_version",
        "source_type",
        "source_file",
        "source_url",
        "source_sha256",
        "fetched_at",
        "converted_with",
        "converted_at",
        "flags",
        "manual_fixes",
        "validated",
    ]


def test_render_is_deterministic() -> None:
    assert render_front_matter(sample()) == render_front_matter(sample())


def test_hand_edited_unquoted_dates_stay_strings() -> None:
    text = (
        "---\nid: x\nsource_type: web\nconverted_at: 2026-11-19T10:42:00Z\n"
        "fetched_at: 2026-11-19 10:00:00\n"
        "validated: {date: 2026-11-20, validator: specval 0.1.0, by: Sam}\n---\nbody\n"
    )
    fm, body = parse_document(text)
    assert fm.converted_at == "2026-11-19T10:42:00Z"
    assert fm.fetched_at == "2026-11-19T10:00:00"
    assert fm.validated == {"date": "2026-11-20", "validator": "specval 0.1.0", "by": "Sam"}
    assert body == "body\n"
    # and they survive another round trip unchanged
    assert parse_document(render_document(fm, body))[0] == fm


def test_values_needing_quotes_round_trip() -> None:
    fm = sample()
    fm.title = 'Line 21: "Data" # not a comment'
    fm.spec_version = "1.10"  # must not become the number 1.1
    fm.manual_fixes = [{"reason": "override: table 3 checked by hand"}]
    assert parse_document(render_document(fm, ""))[0] == fm


def test_split_without_front_matter() -> None:
    assert split_front_matter("# Title\n") == (None, "# Title\n")


@pytest.mark.parametrize(
    "text, message",
    [
        ("# no front matter\n", "no front matter"),
        ("---\nid: x\n", "not closed"),
        ("---\n- a list\n---\n", "mapping"),
        ("---\nid: [unclosed\n---\n", "not valid YAML"),
        ("---\ntitle: x\n---\n", "missing 'id'"),
        ("---\nid: x\ncolour: red\n---\n", "unknown fields"),
        ("---\nid: x\nsource_type: rtf\n---\n", "source_type"),
        ("---\nid: x\nflags: mixed\n---\n", "flags"),
        ("---\nid: x\nvalidated: yes\n---\n", "validated"),
    ],
)
def test_invalid_front_matter_rejected(text: str, message: str) -> None:
    with pytest.raises(FrontMatterError, match=message):
        parse_document(text)
