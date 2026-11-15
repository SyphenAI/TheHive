from __future__ import annotations

import httpx

from app.hive.orchestrator import Orchestrator
from app.models import HiveTurn
from app.services.llm import ChatResult, HttpTransport
from app.services.registry import load_registry
from tests.conftest import FakeTransport


class FakeStreamingTransport(FakeTransport):
    """Yields deltas, recording what the orchestrator had flushed to the DB mid-stream."""

    def __init__(self, db, **kw):
        super().__init__(**kw)
        self.db = db
        self.flushes: list[str] = []

    def chat_stream(self, **kw):
        import time

        full = super().chat(**kw)
        words = full.content.split(" ")
        for i, w in enumerate(words):
            yield (w if i == 0 else " " + w)
            time.sleep(0.15)
            # peek at what the live feed would see right now
            t = self.db.query(HiveTurn).filter(HiveTurn.state == "pending").order_by(HiveTurn.id.desc()).first()
            if t is not None:
                self.flushes.append(t.content)
        yield ChatResult(content=full.content, tokens_in=full.tokens_in, tokens_out=full.tokens_out, latency_ms=1)


def test_orchestrator_flushes_partial_content_while_streaming(db, session, seats, rng):
    t = FakeStreamingTransport(db, drift=True)
    Orchestrator(db, transport=t, rng=rng).run(session, seats, judge=None)
    assert session.status == "done"
    partial = [f for f in t.flushes if f]
    assert partial, "expected at least one mid-stream flush"
    assert any(len(f) < len(max(t.flushes, key=len)) for f in partial), "flushes should grow over time"
    final = db.query(HiveTurn).filter(HiveTurn.session_id == session.id, HiveTurn.phase == "judge").first()
    assert final.state == "done" and final.content.startswith("{")


def _mock_client(body: str, ctype: str = "text/event-stream"):
    real = httpx.Client

    def factory(**kw):
        kw["transport"] = httpx.MockTransport(
            lambda req: httpx.Response(200, headers={"content-type": ctype}, content=body.encode())
        )
        return real(**kw)

    return factory


def test_openai_stream_parser(monkeypatch):
    import app.services.llm as llm

    body = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )
    monkeypatch.setattr(llm.httpx, "Client", _mock_client(body))
    reg = load_registry()
    p = reg.provider("openai")
    items = list(
        HttpTransport().chat_stream(
            provider=p,
            info=p.model_info("gpt-4o-mini"),
            api_key="k",
            system="s",
            messages=[{"role": "user", "content": "u"}],
            max_output_tokens=10,
            temperature=0.0,
        )
    )
    assert items[:-1] == ["Hel", "lo"]
    assert items[-1].content == "Hello" and items[-1].tokens_in == 7 and items[-1].tokens_out == 2


def test_anthropic_stream_parser(monkeypatch):
    import app.services.llm as llm

    body = (
        'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":11}}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi "}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"there"}}\n\n'
        'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":3}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    monkeypatch.setattr(llm.httpx, "Client", _mock_client(body))
    reg = load_registry()
    p = reg.provider("anthropic")
    items = list(
        HttpTransport().chat_stream(
            provider=p,
            info=p.model_info("claude-haiku-4-5-20251001"),
            api_key="k",
            system="s",
            messages=[{"role": "user", "content": "u"}],
            max_output_tokens=10,
            temperature=None,
        )
    )
    assert "".join(items[:-1]) == "Hi there"
    assert items[-1].tokens_in == 11 and items[-1].tokens_out == 3


def test_google_stream_parser(monkeypatch):
    import app.services.llm as llm

    body = (
        'data: {"candidates":[{"content":{"parts":[{"text":"Yo"}]}}]}\n\n'
        'data: {"candidates":[{"content":{"parts":[{"text":"u"}]}}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":2}}\n\n'
    )
    monkeypatch.setattr(llm.httpx, "Client", _mock_client(body))
    reg = load_registry()
    p = reg.provider("google")
    items = list(
        HttpTransport().chat_stream(
            provider=p,
            info=p.model_info("gemini-2.5-flash"),
            api_key="k",
            system="s",
            messages=[{"role": "user", "content": "u"}],
            max_output_tokens=10,
            temperature=0.1,
        )
    )
    assert "".join(items[:-1]) == "You" and items[-1].tokens_in == 5 and items[-1].tokens_out == 2


def test_stream_http_error_surfaces(monkeypatch):
    import pytest

    import app.services.llm as llm

    real = httpx.Client

    def factory(**kw):
        kw["transport"] = httpx.MockTransport(lambda req: httpx.Response(401, json={"error": {"message": "bad key"}}))
        return real(**kw)

    monkeypatch.setattr(llm.httpx, "Client", factory)
    reg = load_registry()
    p = reg.provider("openai")
    with pytest.raises(llm.ChatError, match="401"):
        list(
            HttpTransport().chat_stream(
                provider=p,
                info=p.model_info("gpt-4o-mini"),
                api_key="k",
                system="",
                messages=[{"role": "user", "content": "u"}],
                max_output_tokens=10,
                temperature=0.0,
            )
        )
