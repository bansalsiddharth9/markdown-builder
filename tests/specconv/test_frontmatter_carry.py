from __future__ import annotations

import pytest
from common.frontmatter import FrontMatter, FrontMatterError
from specconv.frontmatter import Previous, carry_over


def fm(**kw) -> FrontMatter:
    base = dict(id="doc", source_type="pdf", source_file="Doc.pdf")
    return FrontMatter(**{**base, **kw})


def test_carry_over_refuses_a_different_document() -> None:
    previous = Previous(fm(source_file="Other.pdf", title="Other"), "body")
    with pytest.raises(FrontMatterError, match="holds a different document"):
        carry_over(fm(), "body", previous)


def test_carry_over_refuses_different_type_or_url() -> None:
    with pytest.raises(FrontMatterError):
        carry_over(fm(), "b", Previous(fm(source_type="html"), "b"))
    web = dict(source_type="web", source_file="", source_url="https://a.org/")
    with pytest.raises(FrontMatterError):
        carry_over(fm(**web), "b", Previous(fm(**{**web, "source_url": "https://b.org/"}), "b"))


def test_carry_over_same_body_keeps_validated_without_warnings() -> None:
    validated = {"date": "2026-11-19", "validator": "specval 0.1.0", "by": "Sam"}
    result = carry_over(fm(), "body", Previous(fm(title="T", validated=validated), "body"))
    assert result.front_matter.title == "T" and result.front_matter.validated == validated
    assert result.warnings == []
