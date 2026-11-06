"""Verifying HTTPS through the operating system, when truststore is installed."""

import ssl
import sys
import types
import urllib.request

import pytest

from markfind import fetch


@pytest.fixture(autouse=True)
def fresh_context(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    real = fetch._ssl_context  # a test may replace it before this fixture finishes
    real.cache_clear()
    yield
    real.cache_clear()


@pytest.fixture
def fake_truststore(monkeypatch):
    module = types.ModuleType("truststore")
    module.SSLContext = lambda protocol: ("os-context", protocol)
    monkeypatch.setitem(sys.modules, "truststore", module)


def test_without_truststore_python_decides(monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)  # makes the import fail
    assert fetch._ssl_context() is None


def test_with_truststore_the_system_decides(fake_truststore):
    assert fetch._ssl_context() == ("os-context", ssl.PROTOCOL_TLS_CLIENT)


@pytest.mark.parametrize("variable", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_chosen_certificates_win(fake_truststore, monkeypatch, variable):
    monkeypatch.setenv(variable, "/some/certs")
    assert fetch._ssl_context() is None


def test_real_truststore_makes_a_verifying_context():
    truststore = pytest.importorskip("truststore")
    context = fetch._ssl_context()
    assert isinstance(context, truststore.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


class FakeResponse:
    headers = {}

    def __enter__(self):
        raise ConnectionError("enough")

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize(
    ("context", "expected"),
    [("a context", {"timeout": 3, "context": "a context"}), (None, {"timeout": 3})],
)
def test_fetch_page_uses_it(monkeypatch, context, expected):
    seen = {}

    def urlopen(request, **options):
        seen.update(options)
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(fetch, "_ssl_context", lambda: context)
    with pytest.raises(ConnectionError):
        fetch.fetch_page("https://a.example/", timeout=3)
    assert seen == expected
