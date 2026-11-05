"""The web interface (markfind serve), driven in a real browser."""

import re

from conftest import BM25, EVIL, FTS, GONE, PYDOCS, build_db, serve
from playwright.sync_api import expect

from markfind.importers import Bookmark


def result_urls(page):
    return page.locator("#results .result .url").all_inner_texts()


def search(page, text):
    page.locator("#q").fill(text)
    expect(page.locator("#search-status")).to_contain_text("match")


def test_search_as_you_type(page):
    page.goto(page.base)
    expect(page.locator("#stats")).to_contain_text("5 bookmarks")
    expect(page.locator("#q")).to_be_focused()

    page.locator("#q").press_sequentially("bm25", delay=20)
    expect(page.locator("#search-status")).to_have_text("2 matches")
    assert set(result_urls(page)) == {FTS, BM25}
    expect(page.locator("#results mark").first).to_have_text("bm25")
    assert "?q=bm25" in page.url  # the search is kept in the address

    page.locator("#q").fill("nothingmatchesthis")
    expect(page.locator("#search-status")).to_have_text("No matches.")


def test_search_from_the_address_and_folder_filter(page):
    page.goto(page.base + "?q=sqlite")
    expect(page.locator("#search-status")).to_have_text("2 matches")
    page.locator("#folder").select_option("Dev")
    expect(page.locator("#search-status")).to_have_text("2 matches")
    page.locator("#folder").select_option("Dev/Databases")
    expect(page.locator("#search-status")).to_have_text("1 match")
    assert result_urls(page) == [FTS]
    assert "folder=Dev%2FDatabases" in page.url


def test_tags_notes_and_tag_filter(page):
    page.goto(page.base + "?q=sqlite")
    expect(page.locator("#search-status")).to_have_text("2 matches")
    fts = page.locator(".result", has=page.locator(f'.url:text-is("{FTS}")'))
    expect(fts.locator(".tag")).to_have_text(["sqlite", "search"])
    expect(fts.locator(".note")).to_have_text("read the ranking section first")

    # The tag menu lists every tag with its count.
    expect(page.locator("#tag")).to_be_visible()
    assert "search (2)" in page.locator("#tag option").all_inner_texts()

    # Clicking a tag filters by it; with an empty box, it lists the tag.
    fts.locator(".tag", has_text="search").click()
    expect(page.locator("#tag")).to_have_value("search")
    assert "tag=search" in page.url
    page.locator("#q").fill("")
    expect(page.locator("#search-status")).to_have_text("2 bookmarks tagged search")
    assert set(result_urls(page)) == {FTS, BM25}


def test_tag_from_the_address_ignores_case(page):
    page.goto(page.base + "?tag=SQLITE")
    expect(page.locator("#tag")).to_have_value("sqlite")
    expect(page.locator("#search-status")).to_have_text("2 bookmarks tagged sqlite")
    assert set(result_urls(page)) == {FTS, PYDOCS}


def test_tag_menu_hidden_without_tags(browser, tmp_path):
    path = tmp_path / "plain.sqlite"
    build_db(path, [Bookmark(FTS, "SQLite FTS5 Extension", "Dev")])
    server = serve(path)
    try:
        page = browser.new_page()
        page.goto(server.url + "?q=sqlite")
        expect(page.locator("#search-status")).to_have_text("1 match")
        expect(page.locator("#tag")).to_be_hidden()
        expect(page.locator(".result .tag")).to_have_count(0)
        page.close()
    finally:
        server.shutdown()
        server.server_close()


def test_keyboard_navigation_and_similar_view(page, context):
    page.goto(page.base + "?q=sqlite")
    expect(page.locator("#search-status")).to_have_text("2 matches")
    first_url = result_urls(page)[0]

    page.keyboard.press("ArrowDown")
    expect(page.locator(".result.selected")).to_have_count(1)
    expect(page.locator(".result").first).to_have_class(re.compile("selected"))
    expect(page.locator("#q")).to_be_focused()  # typing keeps working

    # Enter opens the highlighted result in a new tab.
    with context.expect_page() as new_tab:
        page.keyboard.press("Enter")
    assert new_tab.value.url == first_url
    new_tab.value.close()

    # Shift+Enter shows bookmarks similar to it; Esc goes back.
    page.keyboard.press("Shift+Enter")
    expect(page.locator("#similar-header")).to_be_visible()
    expect(page.locator("#similar-title")).to_contain_text("Similar to:")
    expect(page.locator("#similar-keywords")).to_contain_text("Based on:")
    assert "similar=" in page.url
    page.keyboard.press("Escape")
    expect(page.locator("#similar-header")).to_be_hidden()
    expect(page.locator("#search-status")).to_have_text("2 matches")

    # A second Esc clears the search; / jumps back to the box.
    page.keyboard.press("Escape")
    expect(page.locator("#q")).to_have_value("")
    page.locator("#tab-broken").focus()
    page.keyboard.press("/")
    expect(page.locator("#q")).to_be_focused()


def test_similar_button_and_back(page):
    page.goto(page.base + "?q=bm25")
    expect(page.locator("#search-status")).to_have_text("2 matches")
    page.locator(".result").first.locator("button.similar").click()
    expect(page.locator("#similar-header")).to_be_visible()
    expect(page.locator("#search-status")).to_contain_text("similar bookmark")
    page.locator("#back").click()
    expect(page.locator("#similar-header")).to_be_hidden()
    expect(page.locator("#search-status")).to_have_text("2 matches")


def test_dead_links_and_saved_copy(page, context):
    page.goto(page.base)
    page.locator("#tab-broken").click()
    expect(page.locator("#view-broken")).to_be_visible()
    expect(page.locator("#broken-status")).to_have_text("1 bookmark")
    expect(page.locator("#broken h2")).to_have_text("gone (1)")
    item = page.locator("#broken li")
    expect(item.locator(".error")).to_have_text("HTTP 404 Not Found")

    with context.expect_page() as new_tab:
        item.locator("a.saved").click()
    copy = new_tab.value
    copy.wait_for_load_state()
    assert copy.url.startswith(page.base + "archive?url=")
    expect(copy.locator(".banner")).to_contain_text(f"Saved copy of {GONE}")
    expect(copy.locator("h2")).to_have_text("Saved")
    # The copy is sandboxed: no scripts run, and it can't reach the API.
    assert copy.evaluate(
        "() => { try { return localStorage.length; } catch { return 'blocked'; } }"
    ) == ("blocked")
    copy.close()


def test_page_text_and_titles_are_never_run_as_html(page):
    page.goto(page.base + "?q=hostile")
    expect(page.locator("#search-status")).to_have_text("1 match")
    title = page.locator(".result .title")
    expect(title).to_have_text('<img src=x onerror="window.pwned=1">Evil title')
    expect(page.locator(".result img")).to_have_count(0)
    expect(page.locator(".result .snippet")).to_contain_text("<script>")
    assert page.evaluate("() => window.pwned") is None
    assert page.dialogs == []
    assert result_urls(page) == [EVIL]


def test_bad_raw_query_shows_an_error(page):
    page.goto(page.base)
    page.locator("#raw").check()
    page.locator("#q").fill('"unbalanced')
    expect(page.locator("#search-status")).to_have_class(re.compile("error"))
    expect(page.locator("#search-status")).to_contain_text("invalid query")
