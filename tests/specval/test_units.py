"""Unit tests for the validator's parts: numbering, token families, Markdown
and source reading."""

from __future__ import annotations

from pathlib import Path

import pytest
from specval.markdown import github_slug, parse_markdown
from specval.numbering import section_number, sequence_problems
from specval.reference import from_docx, from_html, furniture_lines
from specval.text import CODE, TABLE, TEXT
from specval.tokens import DEFAULT_FAMILIES, families

FAMILY = {f.name: f for f in DEFAULT_FAMILIES}


def md(body: str):
    full = "---\nid: x\n---\n" + body
    return parse_markdown(full, body)


# ------------------------------------------------------------ numbering


@pytest.mark.parametrize(
    "text, number",
    [
        ("7.3.2 Pop-on", ("7", "3", "2")),
        ("7.3.2. Pop-on", ("7", "3", "2")),
        ("1 Scope", ("1",)),
        ("A.1 Infoset", ("A", "1")),
        ("Annex B Examples", ("B",)),
        ("Appendix c Notes", ("C",)),
        ("A Simple Example", None),
        ("2020 overview", None),
        ("Introduction", None),
        ("10.5 GHz band", ("10", "5")),
    ],
)
def test_section_number(text: str, number) -> None:
    assert section_number(text) == number


def kinds(*texts: str) -> list[tuple[str, str]]:
    numbers = [section_number(t) for t in texts]
    return [(p.kind, ".".join(p.number)) for p in sequence_problems(numbers)]


def test_sequence_rules() -> None:
    assert kinds("1 a", "1.1 a", "1.1.1 a", "1.2 a", "2 a", "3 a") == []
    assert kinds("5 a", "5.1.1 a", "5.2 a") == [("skipped", "5.1.1")]
    assert kinds("1 a", "2 a", "2 a", "3 a") == [("duplicated", "2")]
    assert kinds("1 a", "3 a", "2 a") == [("skipped", "3")]
    assert kinds("2 a", "1 a") == [("out of order", "1")]


def test_annexes_without_numbered_heading() -> None:
    assert kinds("13.2 a", "A.1 a", "A.2 a", "C.1 a", "E.1 a") == []
    assert kinds("13 a", "Annex A x", "A.1 a", "Annex B y", "B.1 b") == []
    assert kinds("C.1 a", "B.1 a") == [("out of order", "B.1")]


def test_one_stray_number_reported_once() -> None:
    assert kinds("E.10.1 a", "2. b", "E.10.2 c", "E.11 d") == [("out of order", "2")]


# --------------------------------------------------------------- tokens


def find(name: str, text: str) -> list[str]:
    return FAMILY[name].find(text)


def test_hex_family() -> None:
    assert find("hex", "0x14, 0X1f and 2Ch and 14h") == ["0x14", "0X1f", "2Ch", "14h"]
    # deviations V-a: whole words with a digit; "each", "Bach" and "beach" are words
    assert find("hex", "each Bach beach fh 0x") == []


def test_byte_pairs_only_in_tables() -> None:
    family = FAMILY["hex byte pairs in tables"]
    assert family.scope == TABLE
    assert family.find("9420 142F ABCD 2020 12345 1.234") == ["9420", "142F", "2020"]


def test_timecodes() -> None:
    text = "01:00:00;02 01:00:00:02 00:00:01.000 03.500 01:02.345"
    assert find("timecodes", text) == ["01:00:00;02", "01:00:00:02", "00:00:01.000", "01:02.345"]


def test_units() -> None:
    text = "4 rows, 32 columns, 29.97 fps, 10%, 5 ms, 2 s, 15c, 3 sections, 1.5px"
    assert find("numbers with units", text) == [
        "4 rows", "32 columns", "29.97 fps", "10%", "5 ms", "2 s", "15c", "1.5px",
    ]  # fmt: skip


def test_keywords_longest_wins() -> None:
    # deviations V-b: "shall not" is one token
    text = "It SHALL NOT do X; it shall\nnot do Y; it shall do Z; may be optional. Mayday."
    assert find("normative keywords", text) == [
        "shall not", "shall not", "shall", "may", "optional",
    ]  # fmt: skip


def test_xml_names() -> None:
    text = "tts:origin and ttp:frameRate in <tt> and </p>, not https://x.org or <http://a.org>"
    assert find("XML names", text) == ["tts:origin", "ttp:frameRate", "<tt", "</p"]


def test_custom_families_added_and_replacing() -> None:
    merged = {f.name: f for f in families({"hex": (r"\bH'[0-9A-F]+",), "codes": (r"\bRU\d",)})}
    assert merged["hex"].find("0x14 H'2C") == ["H'2C"]
    assert merged["codes"].find("RU2 RU3") == ["RU2", "RU3"]


# ------------------------------------------------------------- Markdown


def test_markdown_lines_count_front_matter() -> None:
    doc = md("# Title\n\nText\n\n## 1 Scope\n")
    assert [(h.text, h.line) for h in doc.headings] == [("Title", 4), ("1 Scope", 8)]


def test_markdown_anchors_stripped_from_headings_and_collected() -> None:
    doc = md('## <a id="sec-1"></a>1 Scope\n\n<span id="x"></span>Text [a](#sec-1)\n')
    assert doc.headings[0].text == "1 Scope"
    assert set(doc.ids) == {"sec-1", "x"}
    assert [link.href for link in doc.links] == ["#sec-1"]
    assert doc.heading_slugs() == {"1-scope"}


def test_markdown_tables_code_images() -> None:
    body = (
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        '<table id="t"><tr><td>x</td><td><pre>code</pre></td></tr></table>\n\n'
        "<table><tr><td>one-cell box</td></tr></table>\n\n"
        "```xml\n<tt/>\n```\n\n    indented\n\n"
        '![alt](media/a.png) <img src="media/b.png"> ![](data:image/svg+xml;base64,AA==)\n'
    )
    doc = md(body)
    assert [(t.kind, t.rows) for t in doc.tables] == [
        ("pipe", (("A", "B"), ("1", "2"))),
        ("html", (("x", "code"),)),
    ]
    assert [c.text for c in doc.code_blocks] == ["code", "<tt/>\n", "indented\n"]
    assert [i.src for i in doc.images] == [
        "media/a.png",
        "media/b.png",
        "data:image/svg+xml;base64,AA==",
    ]
    ctx = {s.text.strip(): s.ctx for s in doc.segments}
    assert ctx["1"] == TABLE and ctx["code"] == CODE and ctx["one-cell box"] == TEXT
    assert "alt" not in ctx  # image alt text is not visible text


def test_markdown_javascript_and_autolinks() -> None:
    doc = md('[T](javascript:void(0) "T") and <https://a.org/x>\n')
    assert [link.href for link in doc.links] == ["javascript:void(0)", "https://a.org/x"]
    assert doc.segments[0].text == "T and https://a.org/x"


def test_github_slug() -> None:
    assert github_slug("5.1 Roll-up captions!") == "51-roll-up-captions"


# ------------------------------------------------------------ HTML source


def test_html_reference_counts_and_context() -> None:
    html = (
        "<html><head><title>T</title><style>x</style></head><body>"
        "<h1>A</h1><p>It <b>shall</b> not fail.</p>"
        "<table><tr><th>B1</th><th>B2</th></tr><tr><td>9420</td><td>RCL</td></tr></table>"
        "<table class='example'><tr><td><pre>&lt;tt/&gt;</pre></td></tr></table>"
        "<img src='a.png'><svg><svg></svg></svg><template><img src='t.png'></template>"
        "<script>var x;</script></body></html>"
    )
    ref = from_html(html.encode(), strip_tags=("script", "style", "noscript", "template"))
    assert [h.text for h in ref.headings] == ["A"]
    assert len(ref.tables) == 1 and ref.tables[0].rows == (("B1", "B2"), ("9420", "RCL"))
    assert [c.text for c in ref.code_blocks] == ["<tt/>"]
    assert len(ref.images) == 2  # <img> and the outer <svg>
    by_text = {s.text: s.ctx for s in ref.segments}
    assert by_text["It shall not fail."] == TEXT
    assert by_text["9420"] == TABLE and by_text["<tt/>"] == CODE
    assert not any("var x" in s.text or "T" == s.text for s in ref.segments)


def test_html_reference_main_content_rules() -> None:
    html = (
        "<body><header>Banner</header><nav>Menu</nav><main><header><h1>Title</h1></header>"
        "<section><p>Body</p><aside>Side</aside></section><div class='ad'>Ad</div></main>"
        "<footer>Foot</footer></body>"
    )
    ref = from_html(html.encode(), clean="main-content", drop_selectors=[".ad"])
    texts = [s.text for s in ref.segments]
    assert texts == ["Title", "Body"]


def test_html_encoding_override() -> None:
    data = '<meta charset="iso-8859-1"><p>Café</p>'.encode()
    assert from_html(data, encoding="utf-8").segments[0].text == "Café"


# ------------------------------------------------------------ DOCX source


def test_docx_reference(tmp_path: Path) -> None:
    import docx
    from docx.enum.style import WD_STYLE_TYPE

    document = docx.Document()
    document.add_heading("1 Scope", level=1)
    document.add_paragraph("Body text that shall be kept.")
    document.styles.add_style("Code Block", WD_STYLE_TYPE.PARAGRAPH)
    document.add_paragraph("<tt>", style="Code Block")
    document.add_paragraph("</tt>", style="Code Block")
    mono = document.add_paragraph()
    mono.add_run("plain monospace line").font.name = "Consolas"
    document.add_paragraph("Between")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Merged"
    table.cell(1, 0).text, table.cell(1, 1).text = "14", "2C"
    path = tmp_path / "d.docx"
    document.save(str(path))

    ref = from_docx(path)
    assert [(h.level, h.text) for h in ref.headings] == [(1, "1 Scope")]
    # deviations V-f: a code-named style or an all-monospace paragraph is code
    assert [c.text for c in ref.code_blocks] == ["<tt>\n</tt>\n", "plain monospace line"] or [
        c.text for c in ref.code_blocks
    ] == ["<tt>\n</tt>\nplain monospace line"]
    assert ref.tables[0].rows == (("Merged",), ("14", "2C"))
    assert {s.text: s.ctx for s in ref.segments}["14"] == TABLE


# ------------------------------------------------------------- PDF source


def test_running_headers_and_page_numbers_found() -> None:
    pages = [["CEA-608-E", f"Page {n + 3}", f"Table {n * 7} text", "Byte 1"] for n in range(5)]
    pages[0].append("Only once")
    found = furniture_lines(pages)
    removed = {pages[p][i] for p, i in found}
    # page numbers follow the page; "Byte 1" repeats unchanged; "Table n" does neither
    assert removed == {"CEA-608-E", "Byte 1"} | {f"Page {n + 3}" for n in range(5)}
    assert furniture_lines(pages[:2]) == set()  # too few pages to tell


# ----------------------------------------- reading HTML by browser rules


def test_html_reference_uses_browser_rules() -> None:
    # A browser shows text misplaced inside a table *before* the table
    # (HTML "foster parenting"); lxml leaves it inside. The reference must
    # read the page like a browser, and differently from the converter.
    html = "<p>Intro text here.</p><table><tr><td>Cell text</td></tr>Stray words here.</table>"
    texts = [s.text for s in from_html(html.encode()).segments]
    assert texts.index("Stray words here.") < texts.index("Cell text")


def test_html_reference_unclosed_cells() -> None:
    html = "<table><tr><th>Row:<th>1<th>2</tr><tr><td>High Byte:<td>91<td>92</tr></table>"
    (table,) = from_html(html.encode()).tables
    assert table.rows == (("Row:", "1", "2"), ("High Byte:", "91", "92"))


def test_html_reference_undeclared_utf8_page() -> None:
    # No charset anywhere (input/SAMI); html5lib alone would fall back to
    # windows-1252 and read "Café" as "CafÃ©". Valid UTF-8 is read as UTF-8,
    # like current browsers do.
    data = "<p>Café au lait, naïve.</p>".encode()
    assert from_html(data).segments[0].text == "Café\u00a0au lait, naïve."
    latin1 = "<p>Café au lait.</p>".encode("iso-8859-1")  # not valid UTF-8
    assert from_html(latin1).segments[0].text == "Café au lait."
