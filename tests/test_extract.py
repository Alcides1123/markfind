from markfind.extract import MIN_MAIN_WORDS, extract_text

LONG = " ".join(f"word{n}" for n in range(MIN_MAIN_WORDS))


def test_extract_skips_scripts_and_keeps_words_apart():
    page = extract_text(
        """<html><head><title> My  Page </title>
        <style>body { color: red }</style>
        <script>var secret = "nope";</script></head>
        <body><h1>Hello</h1><p>caf&eacute; <b>bold</b>text</p>
        <table><tr><td>one</td><td>two</td></tr></table>
        <noscript>enable js</noscript></body></html>"""
    )
    assert page.title == "My Page"
    assert page.text == "Hello café bold text one two"


def test_extract_handles_fragments():
    assert extract_text("plain words").text == "plain words"
    assert extract_text("").text == ""


def test_boilerplate_elements_are_dropped():
    page = extract_text(
        """<body>
        <header><a href="/">Site name</a></header>
        <nav><a>Home</a><a>About</a></nav>
        <p>Real content.</p>
        <aside>Popular posts</aside>
        <form><label>Email</label><button>Subscribe</button></form>
        <footer>Copyright</footer>
        </body>"""
    )
    assert page.text == "Real content."


def test_boilerplate_by_class_id_role_and_hidden():
    page = extract_text(
        """<body>
        <div class="site-sidebar"><div><p>nested sidebar text</p></div></div>
        <div id="cookie_banner">We use cookies</div>
        <ul role="navigation"><li>Menu item</li></ul>
        <div aria-hidden="true">decorative</div>
        <div hidden>invisible</div>
        <div class="header-ad">Buy now</div>
        <p class="lead">Kept paragraph</p>
        <div class="additional">also kept</div>
        </body>"""
    )
    assert page.text == "Kept paragraph also kept"


def test_void_and_self_closing_boilerplate_do_not_swallow_text():
    page = extract_text(
        '<p>before</p><input class="nav-search"><img class="ad" src="x">'
        '<div class="ad"/><p>after</p>'
    )
    assert page.text == "before after"


def test_main_content_preferred_when_substantial():
    page = extract_text(
        f"""<body>
        <div class="intro">Unmarked site intro</div>
        <article>
          <header><h1>Article title</h1></header>
          <p>{LONG}</p>
          <div class="share">Share on social</div>
        </article>
        </body>"""
    )
    assert page.text == f"Article title {LONG}"


def test_short_main_content_falls_back_to_whole_page():
    page = extract_text(
        """<body><main><p>Teaser only</p></main>
        <div><p>More body text outside main</p></div></body>"""
    )
    assert page.text == "Teaser only More body text outside main"


def test_unclosed_boilerplate_drops_rest_of_page_only_when_unclosed():
    # Browsers would auto-close <p> etc., but a missing </nav> really does
    # leave the rest of the page inside the nav; markfind treats it the same.
    assert extract_text("<p>kept</p><nav>menu<p>lost").text == "kept"


def test_page_wide_flag_classes_do_not_drop_the_page():
    # Regression: Wikipedia's <html> class lists feature flags such as
    # "vector-feature-main-menu-pinned-disabled"; "menu" made the whole page
    # look like boilerplate and Wikipedia articles were indexed as empty.
    page = extract_text(
        f"""<html class="client-js vector-feature-main-menu-pinned-disabled">
        <body class="skin-vector sticky-header-enabled has-sidebar">
        <div class="vector-main-menu">Main menu Home Contents</div>
        <main id="content" class="mw-body"><h1>Okapi BM25</h1><p>{LONG}</p></main>
        </body></html>"""
    )
    assert page.text == f"Okapi BM25 {LONG}"


def test_state_flag_classes_are_not_boilerplate():
    page = extract_text(
        '<div class="layout has-sidebar is-menu-open"><p>Kept text</p></div>'
        '<div class="sidebar">Dropped</div>'
    )
    assert page.text == "Kept text"


def test_falls_back_to_all_text_when_filtering_removes_nearly_everything():
    # A misjudged wrapper ("content-menu-wrapper") contains the whole page.
    page = extract_text(
        f"""<body><div class="content-menu-wrapper"><h1>Title</h1><p>{LONG}</p>
        <script>ignored()</script></div><p>tail</p></body>"""
    )
    assert page.text == f"Title {LONG} tail"
