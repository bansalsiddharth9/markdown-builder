"""HTML preparation: cleanup, own URL, self-links, images (design: Inputs and
fetching; decisions I1, I2, S1, S2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from specconv.htmlprep import FLAG_SELF_URL_UNKNOWN, OwnUrl, prepare_html

STRIP = ("script", "style", "noscript")
OWN = "https://www.w3.org/TR/2099/REC-x-20990101/"


def prep(html: str, tmp_path: Path, known=(), html_name: str = "doc.html"):
    page = tmp_path / html_name
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(html, encoding="utf-8")
    return prepare_html(
        page.read_bytes(),
        strip_tags=STRIP,
        media_dir=tmp_path / "out" / "media",
        html_path=page,
        known_urls=known,
    )


# ----------------------------------------------------------------- own URL


def test_own_url_from_saved_from_comment(tmp_path: Path) -> None:
    result = prep(f"<!-- saved from url=(0040){OWN} --><p>x</p>", tmp_path)
    assert [(u.url, u.origin) for u in result.own_urls] == [(OWN, "saved-from comment")]


def test_own_url_order_and_duplicates(tmp_path: Path) -> None:
    html = (
        f'<!-- saved from url=(0040){OWN} --><link rel="canonical" href="{OWN}index.html">'
        '<link rel="canonical" href="https://other.org/">'
    )
    result = prep(html, tmp_path, known=[OwnUrl("http://config.org/x", "self_urls")])
    assert [u.origin for u in result.own_urls] == ["self_urls", "saved-from comment"]


def test_own_url_from_canonical_link(tmp_path: Path) -> None:
    result = prep(f'<link rel="canonical" href="{OWN}"><p>x</p>', tmp_path)
    assert result.own_urls[0].origin == "canonical link"


# -------------------------------------------------------------- self-links


@pytest.mark.parametrize(
    "href, expected",
    [
        (f"{OWN}#intro", "#intro"),
        (OWN.replace("https:", "http:") + "#intro", "#intro"),
        (f"{OWN}index.html#intro", "#intro"),
        ("https://WWW.W3.ORG/TR/2099/REC-x-20990101/#intro", "#intro"),
        ("index.html#intro", "#intro"),  # relative, resolved against the own URL
        ("#intro", "#intro"),
        (OWN, OWN),  # S1: no fragment, unchanged
        ("https://www.w3.org/TR/ttml2/#timing", "https://www.w3.org/TR/ttml2/#timing"),
        (f"{OWN}?q=1#intro", f"{OWN}?q=1#intro"),  # query differs: another address
    ],
)
def test_self_links(tmp_path: Path, href: str, expected: str) -> None:
    result = prep(f'<!-- saved from url=(0040){OWN} --><a href="{href}">x</a>', tmp_path)
    assert f'href="{expected}"' in result.html
    assert result.self_links_rewritten == (1 if expected == "#intro" and href != "#intro" else 0)
    assert result.flags == []


def test_self_url_unknown_flag_only_with_possible_self_links(tmp_path: Path) -> None:
    flagged = prep('<a href="https://a.org/spec#x">x</a>', tmp_path)
    assert flagged.flags == [FLAG_SELF_URL_UNKNOWN] and flagged.warnings
    plain = prep('<a href="#x">x</a> <a href="https://a.org/">y</a>', tmp_path)
    assert plain.flags == [] and plain.warnings == []


# ------------------------------------------------------------------ images


def test_local_image_copied_web_kept_missing_kept(tmp_path: Path) -> None:
    files = tmp_path / "Doc_files"
    files.mkdir()
    (files / "fig 1.png").write_bytes(b"png")
    result = prep(
        '<img src="Doc_files/fig%201.png"><img src="https://w3.org/l.png">'
        '<img src="Doc_files/gone.png"><img src="data:image/png;base64,AA==">',
        tmp_path,
    )
    assert (tmp_path / "out" / "media" / "fig-1.png").read_bytes() == b"png"
    assert 'src="media/fig-1.png"' in result.html
    assert 'src="https://w3.org/l.png"' in result.html
    assert 'src="Doc_files/gone.png"' in result.html
    assert result.images_copied == ["media/fig-1.png"]
    assert result.images_remote == 1
    assert result.images_missing == ["Doc_files/gone.png"]
    assert any("file not found" in w for w in result.warnings)


def test_image_outside_html_folder_not_copied(tmp_path: Path) -> None:
    (tmp_path / "secret.png").write_bytes(b"secret")
    result = prep(
        '<img src="../secret.png"><img src="file:///etc/hostname">',
        tmp_path,
        html_name="sub/doc.html",
    )
    assert not (tmp_path / "sub" / "out").exists() and not (tmp_path / "out" / "media").exists()
    assert len(result.images_missing) == 2
    assert all("outside the HTML file's folder" in w for w in result.warnings)


def test_same_image_copied_once_and_name_clashes_get_suffix(tmp_path: Path) -> None:
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "fig.png").write_bytes(folder.encode())
    result = prep('<img src="a/fig.png"><img src="b/fig.png"><img src="a/fig.png">', tmp_path)
    assert result.images_copied == ["media/fig.png", "media/fig-2.png"]
    assert result.html.count('src="media/fig.png"') == 2


# ----------------------------------------------------------------- cleanup


def test_strip_tags_and_encoding(tmp_path: Path) -> None:
    raw = (
        '<html><head><meta charset="iso-8859-1"><script>x</script></head>'
        "<body><p>Café</p><noscript>n</noscript><pre>  a  \n\n b\t</pre></body></html>"
    )
    result = prepare_html(raw.encode("iso-8859-1"), strip_tags=STRIP, media_dir=tmp_path / "m")
    assert "Café" in result.html and "<script" not in result.html and "<noscript" not in result.html
    assert "<pre><code>  a  \n\n b\t</code></pre>" in result.html
    assert result.removed == {"script": 1, "noscript": 1}
    assert result.original_encoding == "iso-8859-1"


# ---------------------------------------------- ids Pandoc's reader drops


@pytest.mark.parametrize("tag", ["nav", "article", "footer", "details"])
def test_targeted_id_on_id_losing_element_moves_to_anchor(tmp_path: Path, tag: str) -> None:
    html = f'<{tag} id="toc"><p>Contents</p></{tag}><a href="#toc">top</a>'
    result = prep(html, tmp_path)
    assert f'<{tag}><a id="toc"></a><p>Contents</p></{tag}>' in result.html
    assert result.ids_moved_to_anchors == 1


def test_untargeted_ids_and_other_elements_untouched(tmp_path: Path) -> None:
    html = '<nav id="side"><p>x</p></nav><section id="s"><p>y</p></section><a href="#s">s</a>'
    result = prep(html, tmp_path)
    assert '<nav id="side">' in result.html and '<section id="s">' in result.html
    assert result.ids_moved_to_anchors == 0


# ------------------------------------------------ one-cell tables and <pre>

EXAMPLE = (
    '<p><a href="#ex1">ex</a></p>'
    '<table class="example" id="ex1"><caption>Example – <b>One</b></caption>'
    '<tbody><tr><td><div class="exampleInner"><pre>&lt;tt&gt;\n  x\n&lt;/tt&gt;</pre></div>'
    "</td></tr></tbody></table>"
)


def test_single_cell_table_unwrapped_caption_then_contents(tmp_path: Path) -> None:
    result = prep(EXAMPLE, tmp_path)
    assert "<table" not in result.html and "<caption" not in result.html
    assert (
        '<p><a id="ex1"></a>Example – <b>One</b></p><div class="exampleInner">'
        "<pre><code>&lt;tt&gt;\n  x\n&lt;/tt&gt;</code></pre></div>"
    ) in result.html
    assert result.tables_unwrapped == 1 and result.pre_given_code == 1


def test_single_cell_table_ids_kept_and_no_caption(tmp_path: Path) -> None:
    html = '<table id="t"><tr id="r"><td id="c">Note <i>here</i></td></tr></table>'
    result = prep(html, tmp_path)
    assert '<p><a id="t"></a><a id="r"></a><a id="c"></a></p>Note <i>here</i>' in result.html
    plain = prep("<table><tr><td>Just text</td></tr></table>", tmp_path / "b")
    assert plain.html == "Just text"


def test_tables_with_more_cells_kept_and_nested_box_unwrapped(tmp_path: Path) -> None:
    html = (
        "<table><tr><th>A</th></tr><tr><td>1</td></tr></table>"
        "<table><tr><td>a</td><td><table><tr><td>inner</td></tr></table></td></tr></table>"
    )
    result = prep(html, tmp_path)
    assert result.html.count("<table>") == 2
    assert "<td>a</td><td>inner</td>" in result.html
    assert result.tables_unwrapped == 1


def test_pre_with_code_left_alone(tmp_path: Path) -> None:
    html = '<pre>\n<code class="language-xml">&lt;a/&gt;</code>\n</pre>'
    result = prep(html, tmp_path)
    assert html in result.html and result.pre_given_code == 0
