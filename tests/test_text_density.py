"""Pages without <main> or <article>: the content block is found by its text."""

from markfind.extract import extract_text


def prose(word, count):
    return " ".join(f"{word}{i}" for i in range(count))


def links(word, count):
    return " ".join(f'<a href="/{word}{i}">{word}{i}</a>' for i in range(count))


ARTICLE = "".join(f"<p>{prose(f'p{n}w', 40)}</p>" for n in range(5))  # 200 words


def page(body):
    return f"<html><head><title>T</title></head><body>{body}</body></html>"


def test_unlabelled_menu_and_link_lists_are_dropped():
    html = page(
        f'<div id="top">{links("menu", 12)}</div>'
        '<div class="wrap">'
        f'<div class="post"><h1>Title</h1>{ARTICLE}</div>'
        f'<div class="extra"><h3>Elsewhere</h3><ul><li>{links("blogroll", 20)}</ul>'
        f"<p>{prose('bio', 15)}</p></div>"
        "</div>"
        f'<div id="bottom">{links("archive", 10)} Copyright</div>'
    )
    text = extract_text(html).text
    assert text.startswith("Title p0w0")
    assert text.endswith("p4w39")
    for gone in ("menu0", "blogroll0", "bio0", "archive0", "Copyright"):
        assert gone not in text


def test_content_split_over_many_blocks_is_kept_together():
    paragraphs = "".join(f'<div class="para">{prose(f"p{n}w", 40)}</div>' for n in range(6))
    html = page(f'<div class="links">{links("menu", 15)}</div><div class="body">{paragraphs}</div>')
    text = extract_text(html).text
    assert "menu0" not in text
    assert all(f"p{n}w0" in text for n in range(6))


def test_prose_spread_over_blocks_is_all_kept():
    # Two columns of equal prose: neither holds 80% of it, so nothing is dropped.
    html = page(
        f'<div class="top">{links("menu", 10)}</div>'
        f'<div class="a">{prose("left", 100)}</div><div class="b">{prose("right", 100)}</div>'
    )
    text = extract_text(html).text
    assert "left0" in text and "right99" in text and "menu0" in text


def test_a_sizeable_unlabelled_block_of_prose_is_kept():
    # An aside of 30% of the prose is more than a menu: keep the whole page.
    html = page(
        f'<div class="main-col">{ARTICLE}</div><div class="col2"><p>{prose("about", 90)}</p></div>'
    )
    assert "about89" in extract_text(html).text


def test_link_only_pages_are_unchanged():
    html = page(f'<div class="a">{links("x", 40)}</div><div class="b">{links("y", 40)}</div>')
    text = extract_text(html).text
    assert "x0" in text and "y39" in text


def test_layout_tables_with_missing_end_tags():
    html = page(
        f"<table><tr><td>{links('menu', 15)}<td>{ARTICLE}</table>"
        f"<table><tr><td>{links('foot', 8)}</table>"
    )
    text = extract_text(html).text
    assert "p0w0" in text and "p4w39" in text
    assert "menu0" not in text and "foot0" not in text


def test_main_content_still_wins():
    html = page(
        f'<div id="top">{links("menu", 12)}</div>'
        f"<div><main>{ARTICLE}</main></div><div>{prose('x', 300)}</div>"
    )
    text = extract_text(html).text
    assert text.startswith("p0w0") and "x0" not in text


def test_broken_nesting_does_not_lose_text():
    html = page(f"</div><div><div>{ARTICLE}</section></td><div>{links('menu', 5)}")
    text = extract_text(html).text
    assert "p0w0" in text and "p4w39" in text


def test_small_pages_are_unchanged():
    html = page(f"<div>{links('menu', 8)}</div><div><p>{prose('w', 25)}</p></div>")
    assert "menu0" in extract_text(html).text
