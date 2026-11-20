"""HTML preparation: cleanup, own URL, self-links, images (design: Inputs and
fetching; decisions I1, I2, S1, S2)."""

from __future__ import annotations

import re
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
    assert "<body>Just text</body>" in plain.html  # lxml adds the page wrapper


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


# ------------------------------------------- web sources: links, cleanup


def web(html: str | bytes, tmp_path: Path, **kw):
    data = html.encode("utf-8") if isinstance(html, str) else html
    return prepare_html(data, strip_tags=STRIP, media_dir=tmp_path / "m", **kw)


def test_relative_links_made_absolute_hash_links_unchanged(tmp_path: Path) -> None:
    html = (
        '<a href="#intro">a</a><a href="../other/">b</a><a href="mailto:x@y.org">c</a>'
        '<img src="img/fig.png"><img src="//cdn.example.org/i.png">'
        '<a href="https://elsewhere.org/x">d</a>'
    )
    result = web(html, tmp_path, page_url="https://w3.org/TR/spec/")
    assert '<a href="#intro">' in result.html
    assert '<a href="https://w3.org/TR/other/">' in result.html
    assert '<a href="mailto:x@y.org">' in result.html
    assert 'src="https://w3.org/TR/spec/img/fig.png"' in result.html
    assert 'src="https://cdn.example.org/i.png"' in result.html
    assert result.links_made_absolute == 3
    assert result.images_remote == 2 and not result.images_copied


def test_base_href_used_for_absolute_links(tmp_path: Path) -> None:
    html = '<head><base href="/assets/"></head><img src="a.png">'
    result = web(html, tmp_path, page_url="https://ex.org/docs/page")
    assert 'src="https://ex.org/assets/a.png"' in result.html


def test_relative_links_to_own_page_become_hash_links(tmp_path: Path) -> None:
    html = '<a href="./#intro">a</a><a href="index.html#b">b</a><a href="?x=1#c">c</a>'
    final = "https://w3.org/TR/2018/REC-x/"
    result = web(
        html,
        tmp_path,
        page_url=final,
        known_urls=[OwnUrl("https://w3.org/TR/x/", "sources.yaml"), OwnUrl(final, "final URL")],
    )
    assert '<a href="#intro">' in result.html and '<a href="#b">' in result.html
    assert 'href="https://w3.org/TR/2018/REC-x/?x=1#c"' in result.html  # other query: other page
    assert result.self_links_rewritten == 2


def test_encoding_override_beats_meta(tmp_path: Path) -> None:
    html = '<meta charset="iso-8859-1"><p>Café</p>'.encode()  # UTF-8 bytes, wrong meta
    assert "Café" in web(html, tmp_path, encoding="utf-8").html
    assert "Café" not in web(html, tmp_path / "b").html  # the meta alone misleads


MAIN_PAGE = (
    "<html><head><title>T</title></head><body>"
    "<header>Site banner</header><nav>Menu</nav>"
    "<main><header><h1>Spec title</h1></header><nav>Page toc</nav>"
    "<section><header>Section intro</header><p>Body text</p><aside>Side</aside></section>"
    '<div class="ad">Buy</div><footer>Main footer</footer></main>'
    "<aside>Related</aside><footer>Site footer</footer></body></html>"
)


def test_main_content_keeps_main_only(tmp_path: Path) -> None:
    result = web(MAIN_PAGE, tmp_path, clean="main-content", drop_selectors=[".ad"])
    for kept in ("Spec title", "Section intro", "Body text", "Main footer", "<title>T</title>"):
        assert kept in result.html, kept
    for gone in ("Site banner", "Menu", "Page toc", "Side", "Buy", "Related", "Site footer"):
        assert gone not in result.html, gone
    assert result.main_content == {"kept": "main", "removed": {"nav": 1, "aside": 1, ".ad": 1}}


def test_main_content_articles_when_no_main(tmp_path: Path) -> None:
    html = (
        "<body><header>Banner</header><article><header><h1>A</h1></header><p>a</p>"
        "<article><p>nested</p></article></article><p>between</p><article><p>b</p></article>"
        "<footer>Foot</footer></body>"
    )
    result = web(html, tmp_path, clean="main-content")
    assert "<h1>A</h1>" in result.html and "nested" in result.html and "<p>b</p>" in result.html
    assert "between" not in result.html and "Banner" not in result.html
    assert result.main_content["kept"] == "article"


def test_main_content_without_main_or_article(tmp_path: Path) -> None:
    html = "<body><header>Banner</header><nav>Menu</nav><p>Body</p><footer>Foot</footer></body>"
    result = web(html, tmp_path, clean="main-content")
    assert "Body" in result.html
    assert "Banner" not in result.html and "Menu" not in result.html and "Foot" not in result.html
    assert result.main_content == {
        "kept": "body",
        "removed": {"nav": 1, "header": 1, "footer": 1},
    }


def test_clean_none_keeps_everything(tmp_path: Path) -> None:
    result = web(MAIN_PAGE, tmp_path)
    assert "Site banner" in result.html and "Related" in result.html
    assert result.main_content is None


def test_text_chars_counted_after_cleanup(tmp_path: Path) -> None:
    result = web("<body><nav>Menu text</nav><p>a b c</p></body>", tmp_path, clean="main-content")
    assert result.text_chars == 3


def test_main_renamed_so_pandoc_keeps_the_rest(tmp_path: Path) -> None:
    result = web('<p>Before</p><main id="m" class="c"><p>In</p></main><p>After</p>', tmp_path)
    assert '<p>Before</p><div class="c" id="m"><p>In</p></div><p>After</p>' in result.html
    assert result.main_renamed == 1


def test_downloaded_images_copied_into_media(tmp_path: Path) -> None:
    fig = tmp_path / "cache" / "fig.png"
    fig.parent.mkdir()
    fig.write_bytes(b"png")
    html = '<img src="img/fig.png"><img src="img/fig.png#v"><img src="other.png">'
    result = web(
        html,
        tmp_path,
        page_url="https://ex.org/spec/",
        downloaded_images={"https://ex.org/spec/img/fig.png": fig},
    )
    assert result.html.count('src="m/fig.png"') == 2
    assert 'src="https://ex.org/spec/other.png"' in result.html
    assert result.images_copied == ["m/fig.png"] and (tmp_path / "m/fig.png").read_bytes() == b"png"
    assert result.images_downloaded == 2 and result.images_remote == 1


def test_template_removed_with_default_strip_tags(tmp_path: Path) -> None:
    """<template> content is never displayed (deviations Q19), e.g. a browser
    extension's button saved with the page."""
    from specconv.config import HtmlConfig

    html = "<p>Text</p><template><div>AI Assistant</div><svg></svg></template>"
    result = prepare_html(
        html.encode(), strip_tags=HtmlConfig().strip_tags, media_dir=tmp_path / "m"
    )
    assert "AI Assistant" not in result.html and result.removed == {"template": 1}


# ------------------------------------------------------- old-style HTML

# HTML lets a new cell close the previous one; older pages rely on it
# (input/SCC, "Closed Captions and the Scenarist Closed Caption Format").
UNCLOSED = (
    "<ol><li>Here is a table:\n<p><table border=1>"
    "<tr><th align=right>Row:<th>1<th>2</tr>\n"
    "<tr><td align=right>High Byte:<td>91<td>92</tr>\n"
    "</table></p>\n<li>next</ol>"
)


def test_unclosed_cells_read_by_browser_rules(tmp_path: Path) -> None:
    result = prep(UNCLOSED, tmp_path)
    assert "<td>91</td><td>92</td>" in result.html  # not nested in each other
    assert "<th>1</th><th>2</th>" in result.html


def test_unclosed_cells_become_a_markdown_table(tmp_path: Path, monkeypatch) -> None:
    from specconv import cli
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    Path("input").mkdir()
    Path("input/old.htm").write_text(UNCLOSED, encoding="utf-8")
    result = CliRunner().invoke(
        cli.app, ["convert", "--input", "input", "--output", "output", "--work", "work"]
    )
    assert result.exit_code == 0, result.output
    markdown = Path("output/old/old.md").read_text(encoding="utf-8")
    assert "| High Byte: | 91 | 92 |" in re.sub(" +", " ", markdown)
