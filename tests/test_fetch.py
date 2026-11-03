import argparse
import threading
from datetime import timedelta

import pytest

from markfind.cli import parse_duration
from markfind.extract import Page
from markfind.fetch import HostLimiter, fetch_many, host_of, interleave_hosts


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_host_limiter_spaces_requests_per_host():
    clock = FakeClock()
    limiter = HostLimiter(delay=2.0, clock=clock, sleep=clock.sleep)
    page = Page("", "")

    limiter.run("a.example", lambda: page)
    limiter.run("b.example", lambda: page)  # different host: no wait
    assert clock.sleeps == []

    clock.now += 0.5
    limiter.run("a.example", lambda: page)
    assert clock.sleeps == [1.5]

    clock.now += 5
    limiter.run("a.example", lambda: page)  # enough time passed
    assert clock.sleeps == [1.5]


def test_host_limiter_records_time_even_on_failure():
    clock = FakeClock()
    limiter = HostLimiter(delay=1.0, clock=clock, sleep=clock.sleep)

    def boom():
        raise OSError("down")

    with pytest.raises(OSError):
        limiter.run("a.example", boom)
    limiter.run("a.example", lambda: Page("", ""))
    assert clock.sleeps == [1.0]


def test_interleave_hosts():
    items = [
        (1, "https://a.example/1"),
        (2, "https://a.example/2"),
        (3, "https://a.example/3"),
        (4, "https://B.example/1"),
        (5, "https://c.example/1"),
        (6, "https://b.example/2"),
    ]
    assert [id_ for id_, _ in interleave_hosts(items)] == [1, 4, 5, 2, 6, 3]
    assert host_of("https://User@Example.COM:8080/x") == "example.com"


def test_fetch_many_one_request_per_host_at_a_time():
    active: dict[str, int] = {}
    peak: dict[str, int] = {}
    lock = threading.Lock()
    release = threading.Event()

    def fetch(url, timeout):
        host = host_of(url)
        with lock:
            active[host] = active.get(host, 0) + 1
            peak[host] = max(peak.get(host, 0), active[host])
        release.wait(0.05)
        with lock:
            active[host] -= 1
        return Page("", url)

    items = [(n, f"https://{h}.example/{n}") for n, h in enumerate("aaabbb")]
    results = list(fetch_many(items, fetch=fetch, workers=6, limiter=HostLimiter(delay=0)))

    assert sorted(id_ for id_, _, _ in results) == list(range(6))
    assert peak == {"a.example": 1, "b.example": 1}


def test_parse_duration():
    assert parse_duration("90d") == timedelta(days=90)
    assert parse_duration("2W") == timedelta(weeks=2)
    assert parse_duration("12h") == timedelta(hours=12)
    with pytest.raises(argparse.ArgumentTypeError):
        parse_duration("soon")
