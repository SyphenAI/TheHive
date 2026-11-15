from __future__ import annotations

import httpx
import pytest

from app.services import context as ctx
from app.services import scholar
from app.services.context import UnsafeURL, _check, fetch_url, fit_context


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://[::1]/",
        "ftp://example.com/",
        "http://example.com:8080/",
        "http://user:pw@example.com/",
        "http://metadata.internal/",
    ],
)
def test_ssrf_guard_blocks(url):
    with pytest.raises(UnsafeURL):
        _check(url)


def test_fetch_pins_ip_and_extracts_html(monkeypatch):
    # Pretend example.com resolves to a public IP, and serve a fake page from a mock transport.
    monkeypatch.setattr(ctx, "_check", lambda u: ("example.com", "93.184.216.34"))
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["host_header"] = req.headers.get("host")
        seen["url_host"] = req.url.host
        seen["sni"] = req.extensions.get("sni_hostname")
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><head><title>Hi &amp; bye</title><script>x()</script></head><body><h1>Head</h1><p>Body text.</p></body></html>",
        )

    real_client = httpx.Client

    def client_factory(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real_client(**kw)

    monkeypatch.setattr(ctx.httpx, "Client", client_factory)
    r = fetch_url("https://example.com/page")
    assert seen == {"host_header": "example.com", "url_host": "93.184.216.34", "sni": "example.com"}
    assert r["title"] == "Hi & bye" and "Head" in r["text"] and "x()" not in r["text"]


def test_fetch_rejects_binary_and_oversize(monkeypatch):
    monkeypatch.setattr(ctx, "_check", lambda u: ("example.com", "93.184.216.34"))
    real_client = httpx.Client

    def mk(resp):
        def client_factory(**kw):
            kw["transport"] = httpx.MockTransport(lambda req: resp)
            return real_client(**kw)

        return client_factory

    monkeypatch.setattr(
        ctx.httpx, "Client", mk(httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"))
    )
    with pytest.raises(UnsafeURL, match="content type"):
        fetch_url("https://example.com/a.pdf")
    monkeypatch.setattr(
        ctx.httpx,
        "Client",
        mk(httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x" * (ctx.MAX_BYTES + 10))),
    )
    with pytest.raises(UnsafeURL, match="larger"):
        fetch_url("https://example.com/big.txt")


def test_fit_context_truncates_middle():
    text = "A" * 50_000 + "MIDDLE" + "Z" * 50_000
    out, truncated = fit_context(text, model_context_tokens=8000, reserve_tokens=3000)
    assert (
        truncated
        and "context truncated" in out
        and out.startswith("AAAA")
        and out.endswith("ZZZZ")
        and "MIDDLE" not in out
    )
    small, t2 = fit_context("short", model_context_tokens=8000, reserve_tokens=3000)
    assert not t2 and small == "short"


def test_scholar_merge_and_block(monkeypatch):
    a = {
        "source": "crossref",
        "title": "Prompt Injection Attacks on LLM Agents",
        "authors": ["A. One", "B. Two"],
        "year": "2024",
        "venue": "S&P",
        "abstract": "We study prompt injection.",
        "doi": "10.1/x",
        "url": "https://doi.org/10.1/x",
        "cited_by_count": 40,
    }
    b = dict(a, source="openalex", abstract="We study prompt injection in detail.", cited_by_count=55)
    c = {
        "source": "arxiv",
        "title": "Unrelated Paper About Cooking",
        "authors": ["C"],
        "year": "2010",
        "venue": "arXiv",
        "abstract": "",
        "doi": "",
        "url": "https://arxiv.org/abs/1",
        "cited_by_count": 0,
    }
    monkeypatch.setattr(
        scholar, "SOURCES", {"crossref": lambda *x: [a], "openalex": lambda *x: [b], "arxiv": lambda *x: [c]}
    )
    items = scholar.search("prompt injection LLM agents", limit=5)
    assert [i["title"] for i in items][0].startswith("Prompt Injection")
    assert items[0]["cited_by_count"] == 55 and set(items[0]["sources"]) == {"crossref", "openalex"}
    assert len(items) == 2
    block = scholar.evidence_block(items, "prompt injection")
    assert block.startswith("<evidence") and "[1] A. One, B. Two (2024)" in block and "doi:10.1/x" in block
