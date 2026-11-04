import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime

import pytest

from markfind import cli
from markfind.extract import Page
from markfind.fetch import (
    DEFAULT_RETRY_WAIT,
    HostLimiter,
    classify_error,
    fetch_many,
    retry_wait,
)
from markfind.importers import Bookmark
from markfind.store import Store

REASONS = {403: "Forbidden", 404: "Not Found", 429: "Too Many Requests", 503: "Service Unavailable"}


def http_error(code, **headers):
    msg = Message()
    for name, value in headers.items():
        msg[name.replace("_", "-")] = value
    return urllib.error.HTTPError("https://a.example/", code, REASONS[code], msg, None)


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def in_seconds(seconds):
    return format_datetime(datetime.now(timezone.utc) + timedelta(seconds=seconds), usegmt=True)


@pytest.mark.parametrize(
    "error, expected",
    [
        (http_error(429), DEFAULT_RETRY_WAIT),
        (http_error(503), DEFAULT_RETRY_WAIT),
        (http_error(429, Retry_After="30"), 30),
        (http_error(429, Retry_After="0"), 0),
        (http_error(429, Retry_After="soon"), DEFAULT_RETRY_WAIT),
        (http_error(503, Retry_After="Wed, 21 Oct 2015 07:28:00 GMT"), 0),  # in the past
        (http_error(404), None),
        (http_error(403), None),
        (http_error(429, X_Vercel_Mitigated="challenge"), None),
        (http_error(503, cf_mitigated="challenge"), None),
        (TimeoutError(), None),
    ],
)
def test_retry_wait(error, expected):
    assert retry_wait(error) == expected


def test_retry_wait_http_date():
    assert 115 <= retry_wait(http_error(429, Retry_After=in_seconds(120))) <= 120


@pytest.mark.parametrize(
    "error, kind, message",
    [
        (
            http_error(429, X_Vercel_Mitigated="challenge"),
            "blocked",
            "HTTP 429 Too Many Requests: bot-protection challenge",
        ),
        (
            http_error(403, cf_mitigated="Challenge"),
            "blocked",
            "HTTP 403 Forbidden: bot-protection challenge",
        ),
        (
            http_error(429, Retry_After="3600"),
            "server",
            "HTTP 429 Too Many Requests; the site asks to wait 3600 s",
        ),
        (http_error(503), "server", "HTTP 503 Service Unavailable"),
        (http_error(404, cf_mitigated="challenge"), "gone", "HTTP 404 Not Found"),
    ],
)
def test_classify(error, kind, message):
    assert classify_error(error) == (kind, message)


def run(fetch, **kwargs):
    clock = FakeClock()
    limiter = HostLimiter(delay=0, clock=clock, sleep=clock.sleep)
    items = [(1, "https://a.example/page")]
    ((_, _, result),) = fetch_many(items, fetch=fetch, workers=1, limiter=limiter, **kwargs)
    return result, clock.sleeps


def failing(*errors):
    """A fetch that raises these errors in turn, then succeeds."""
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return Page("", "finally")

    return fetch, calls


def test_retries_after_the_wait_the_site_asks_for():
    fetch, calls = failing(http_error(429, Retry_After="30"), http_error(503))
    result, sleeps = run(fetch)
    assert result == Page("", "finally")
    assert sleeps == [30, DEFAULT_RETRY_WAIT]
    assert len(calls) == 3


def test_gives_up_after_two_retries():
    fetch, calls = failing(*[http_error(429)] * 3)
    result, sleeps = run(fetch)
    assert isinstance(result, urllib.error.HTTPError)
    assert len(calls) == 3 and sleeps == [DEFAULT_RETRY_WAIT] * 2


@pytest.mark.parametrize(
    "error, options",
    [
        (http_error(429, Retry_After="3600"), {}),  # longer than the 60 s default
        (http_error(429, Retry_After="10"), {"max_retry_wait": 5}),
        (http_error(429, Retry_After="0"), {"max_retry_wait": 0}),  # 0 disables retries
        (http_error(429, X_Vercel_Mitigated="challenge"), {}),
        (http_error(404), {}),
    ],
)
def test_no_retry(error, options):
    fetch, calls = failing(error)
    result, sleeps = run(fetch, **options)
    assert result is error
    assert len(calls) == 1 and sleeps == []


def test_defer_holds_back_the_whole_host():
    clock = FakeClock()
    limiter = HostLimiter(delay=1, clock=clock, sleep=clock.sleep)
    limiter.run("a.example", lambda: None)
    limiter.defer("a.example", 10)
    limiter.defer("a.example", 3)  # a shorter wait doesn't cut the longer one short
    limiter.run("b.example", lambda: None)
    assert clock.sleeps == []  # other hosts aren't affected
    limiter.run("a.example", lambda: None)
    assert clock.sleeps == [10]
    limiter.run("a.example", lambda: None)
    assert clock.sleeps == [10, 1]  # then the normal delay again


def test_cli_reports_the_wait(tmp_path, monkeypatch, capsys):
    def fetch(url, timeout):
        raise http_error(429, Retry_After="7200")

    monkeypatch.setattr(cli, "fetch_page", fetch)
    db = str(tmp_path / "db.sqlite")
    with Store(db) as store:
        store.add_bookmarks([Bookmark("https://a.example/")])
    assert cli.main(["--db", db, "fetch", "--max-retry-wait", "30"]) == 0
    out = capsys.readouterr().out
    assert "failed, server: HTTP 429 Too Many Requests; the site asks to wait 7200 s" in out
