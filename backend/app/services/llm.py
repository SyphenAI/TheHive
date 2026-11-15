"""Provider transport. One function: chat(). Three wire styles: openai | anthropic | google.

The transport is deliberately dumb: it does not know about rounds, budgets, or the DB.
The orchestrator decides *whether* to call; this module decides *how*.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.services.registry import ModelInfo, ProviderInfo

DEFAULT_TIMEOUT = 120.0


@dataclass
class ChatResult:
    content: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    raw_model: str = ""


class ChatError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class Transport(Protocol):
    def chat(
        self,
        *,
        provider: ProviderInfo,
        info: ModelInfo,
        api_key: str,
        system: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        temperature: float | None,
        base_url_override: str = "",
    ) -> ChatResult: ...


def _headers(provider: ProviderInfo, api_key: str) -> dict[str, str]:
    auth = provider.auth or {}
    kind = auth.get("type", "bearer")
    headers = {"content-type": "application/json"}
    if kind == "bearer":
        headers["authorization"] = f"Bearer {api_key}"
    elif kind == "x-api-key":
        headers["x-api-key"] = api_key
    elif kind == "header":
        headers[auth.get("header", "authorization")] = api_key
    headers.update(auth.get("extra_headers") or {})
    return headers


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    retryable = resp.status_code in (408, 409, 425, 429, 500, 502, 503, 504)
    snippet = resp.text[:500]
    raise ChatError(f"HTTP {resp.status_code}: {snippet}", status=resp.status_code, retryable=retryable)


class HttpTransport:
    """Real network transport."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout

    def chat(
        self,
        *,
        provider: ProviderInfo,
        info: ModelInfo,
        api_key: str,
        system: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        temperature: float | None,
        base_url_override: str = "",
    ) -> ChatResult:
        base = (base_url_override or provider.base_url).rstrip("/")
        if not base:
            raise ChatError(f"Provider '{provider.key}' has no base_url; set one on the token.")
        started = time.perf_counter()
        if provider.style == "anthropic":
            result = self._anthropic(base, provider, info, api_key, system, messages, max_output_tokens, temperature)
        elif provider.style == "google":
            result = self._google(base, provider, info, api_key, system, messages, max_output_tokens, temperature)
        else:
            result = self._openai(base, provider, info, api_key, system, messages, max_output_tokens, temperature)
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    # ---- wire styles ----
    def _openai(self, base, provider, info, api_key, system, messages, max_out, temperature) -> ChatResult:  # noqa: ANN001
        body: dict = {
            "model": info.model,
            "messages": [{"role": "system", "content": system}, *messages] if system else messages,
        }
        body["max_completion_tokens" if info.max_completion_tokens else "max_tokens"] = max_out
        if temperature is not None and not info.no_temperature:
            body["temperature"] = temperature
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{base}{provider.chat_path}", headers=_headers(provider, api_key), json=body)
        _raise_for(resp)
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatError(f"Malformed response: {str(data)[:300]}") from exc
        usage = data.get("usage") or {}
        return ChatResult(
            content=content,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            latency_ms=0,
            raw_model=str(data.get("model") or info.model),
        )

    def _anthropic(self, base, provider, info, api_key, system, messages, max_out, temperature) -> ChatResult:  # noqa: ANN001
        body: dict = {"model": info.model, "max_tokens": max_out, "messages": messages}
        if system:
            body["system"] = system
        if temperature is not None and not info.no_temperature:
            body["temperature"] = temperature
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{base}{provider.chat_path}", headers=_headers(provider, api_key), json=body)
        _raise_for(resp)
        data = resp.json()
        parts = data.get("content") or []
        content = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        usage = data.get("usage") or {}
        return ChatResult(
            content=content,
            tokens_in=int(usage.get("input_tokens") or 0),
            tokens_out=int(usage.get("output_tokens") or 0),
            latency_ms=0,
            raw_model=str(data.get("model") or info.model),
        )

    def _google(self, base, provider, info, api_key, system, messages, max_out, temperature) -> ChatResult:  # noqa: ANN001
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
        ]
        body: dict = {"contents": contents, "generationConfig": {"maxOutputTokens": max_out}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if temperature is not None and not info.no_temperature:
            body["generationConfig"]["temperature"] = temperature
        path = provider.chat_path.format(model=info.model)
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{base}{path}", headers=_headers(provider, api_key), json=body)
        _raise_for(resp)
        data = resp.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
            content = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatError(f"Malformed response: {str(data)[:300]}") from exc
        usage = data.get("usageMetadata") or {}
        return ChatResult(
            content=content,
            tokens_in=int(usage.get("promptTokenCount") or 0),
            tokens_out=int(usage.get("candidatesTokenCount") or 0),
            latency_ms=0,
            raw_model=info.model,
        )


# ---------------- streaming ----------------
def _sse_lines(resp: httpx.Response):
    """Yield (event, data) tuples from an SSE body."""
    event, data = None, []
    for line in resp.iter_lines():
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    if data:
        yield event, "\n".join(data)


class StreamingMixin:
    """Adds chat_stream() to HttpTransport. Yields text deltas; final item is a ChatResult with usage."""

    def chat_stream(
        self, *, provider, info, api_key, system, messages, max_output_tokens, temperature, base_url_override=""
    ):  # noqa: ANN001
        base = (base_url_override or provider.base_url).rstrip("/")
        if not base:
            raise ChatError(f"Provider '{provider.key}' has no base_url; set one on the token.")
        started = time.perf_counter()
        if provider.style == "anthropic":
            gen = self._anthropic_stream(
                base, provider, info, api_key, system, messages, max_output_tokens, temperature
            )
        elif provider.style == "google":
            gen = self._google_stream(base, provider, info, api_key, system, messages, max_output_tokens, temperature)
        else:
            gen = self._openai_stream(base, provider, info, api_key, system, messages, max_output_tokens, temperature)
        for item in gen:
            if isinstance(item, ChatResult):
                item.latency_ms = int((time.perf_counter() - started) * 1000)
            yield item

    def _openai_stream(self, base, provider, info, api_key, system, messages, max_out, temperature):  # noqa: ANN001
        import json

        body: dict = {
            "model": info.model,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "system", "content": system}, *messages] if system else messages,
        }
        body["max_completion_tokens" if info.max_completion_tokens else "max_tokens"] = max_out
        if temperature is not None and not info.no_temperature:
            body["temperature"] = temperature
        parts: list[str] = []
        tin = tout = 0
        with (
            httpx.Client(timeout=self.timeout) as client,
            client.stream(
                "POST", f"{base}{provider.chat_path}", headers=_headers(provider, api_key), json=body
            ) as resp,
        ):
            if resp.status_code >= 400:
                resp.read()
                _raise_for(resp)
            for _ev, data in _sse_lines(resp):
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("usage"):
                    tin = int(obj["usage"].get("prompt_tokens") or 0)
                    tout = int(obj["usage"].get("completion_tokens") or 0)
                for ch in obj.get("choices") or []:
                    delta = (ch.get("delta") or {}).get("content")
                    if delta:
                        parts.append(delta)
                        yield delta
        yield ChatResult(content="".join(parts), tokens_in=tin, tokens_out=tout, latency_ms=0, raw_model=info.model)

    def _anthropic_stream(self, base, provider, info, api_key, system, messages, max_out, temperature):  # noqa: ANN001
        import json

        body: dict = {"model": info.model, "max_tokens": max_out, "messages": messages, "stream": True}
        if system:
            body["system"] = system
        if temperature is not None and not info.no_temperature:
            body["temperature"] = temperature
        parts: list[str] = []
        tin = tout = 0
        with (
            httpx.Client(timeout=self.timeout) as client,
            client.stream(
                "POST", f"{base}{provider.chat_path}", headers=_headers(provider, api_key), json=body
            ) as resp,
        ):
            if resp.status_code >= 400:
                resp.read()
                _raise_for(resp)
            for ev, data in _sse_lines(resp):
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                t = obj.get("type") or ev
                if t == "message_start":
                    tin = int(((obj.get("message") or {}).get("usage") or {}).get("input_tokens") or 0)
                elif t == "content_block_delta":
                    d = obj.get("delta") or {}
                    if d.get("type") == "text_delta" and d.get("text"):
                        parts.append(d["text"])
                        yield d["text"]
                elif t == "message_delta":
                    tout = int((obj.get("usage") or {}).get("output_tokens") or tout)
                elif t == "error":
                    raise ChatError(str(obj.get("error"))[:300])
        yield ChatResult(content="".join(parts), tokens_in=tin, tokens_out=tout, latency_ms=0, raw_model=info.model)

    def _google_stream(self, base, provider, info, api_key, system, messages, max_out, temperature):  # noqa: ANN001
        import json

        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
        ]
        body: dict = {"contents": contents, "generationConfig": {"maxOutputTokens": max_out}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if temperature is not None and not info.no_temperature:
            body["generationConfig"]["temperature"] = temperature
        path = (
            provider.chat_path.format(model=info.model).replace(":generateContent", ":streamGenerateContent")
            + "?alt=sse"
        )
        parts: list[str] = []
        tin = tout = 0
        with (
            httpx.Client(timeout=self.timeout) as client,
            client.stream("POST", f"{base}{path}", headers=_headers(provider, api_key), json=body) as resp,
        ):
            if resp.status_code >= 400:
                resp.read()
                _raise_for(resp)
            for _ev, data in _sse_lines(resp):
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                um = obj.get("usageMetadata") or {}
                if um:
                    tin = int(um.get("promptTokenCount") or tin)
                    tout = int(um.get("candidatesTokenCount") or tout)
                for cand in obj.get("candidates") or []:
                    for p in (cand.get("content") or {}).get("parts") or []:
                        if p.get("text"):
                            parts.append(p["text"])
                            yield p["text"]
        yield ChatResult(content="".join(parts), tokens_in=tin, tokens_out=tout, latency_ms=0, raw_model=info.model)


for _name in ("chat_stream", "_openai_stream", "_anthropic_stream", "_google_stream"):
    setattr(HttpTransport, _name, getattr(StreamingMixin, _name))


def list_models(provider: ProviderInfo, api_key: str, base_url_override: str = "") -> list[str]:
    """Best-effort model discovery. Empty list on any failure."""
    base = (base_url_override or provider.base_url).rstrip("/")
    if not base or not provider.models_path:
        return []
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{base}{provider.models_path}", headers=_headers(provider, api_key))
        if resp.status_code >= 400:
            return []
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    items = data.get("data") or data.get("models") or []
    out: list[str] = []
    for item in items:
        mid = item.get("id") or item.get("name") or ""
        mid = str(mid).removeprefix("models/")
        if mid:
            out.append(mid)
    return sorted(out)


def test_connection(provider: ProviderInfo, api_key: str, base_url_override: str = "") -> dict:
    started = time.perf_counter()
    models = list_models(provider, api_key, base_url_override)
    latency = int((time.perf_counter() - started) * 1000)
    if models:
        return {
            "ok": True,
            "latency_ms": latency,
            "message": f"{len(models)} model(s) visible",
            "models_seen": models[:50],
        }
    return {
        "ok": False,
        "latency_ms": latency,
        "message": "Model list unavailable; check key, base URL, or network.",
        "models_seen": [],
    }
