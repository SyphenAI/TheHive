"""Provider registry: loads registry/providers.json. No model IDs are hardcoded in Python."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    model: str
    price_in: float  # USD per 1M input tokens
    price_out: float  # USD per 1M output tokens
    context: int = 128_000
    no_temperature: bool = False
    max_completion_tokens: bool = False
    known: bool = True


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    display: str
    style: str  # openai | anthropic | google
    base_url: str
    chat_path: str
    models_path: str
    auth: dict[str, Any]
    default_price: dict[str, float]
    models: dict[str, dict[str, Any]] = field(default_factory=dict)

    def model_info(self, model: str) -> ModelInfo:
        meta = self.models.get(model)
        if meta is None:
            return ModelInfo(
                provider=self.key,
                model=model,
                price_in=float(self.default_price.get("in", 0.0)),
                price_out=float(self.default_price.get("out", 0.0)),
                known=False,
            )
        return ModelInfo(
            provider=self.key,
            model=model,
            price_in=float(meta.get("in", self.default_price.get("in", 0.0))),
            price_out=float(meta.get("out", self.default_price.get("out", 0.0))),
            context=int(meta.get("context", 128_000)),
            no_temperature=bool(meta.get("no_temperature", False)),
            max_completion_tokens=bool(meta.get("max_completion_tokens", False)),
        )


class Registry:
    def __init__(self, raw: dict[str, Any]):
        self.version = int(raw.get("$schema_version", 1))
        self.providers: dict[str, ProviderInfo] = {}
        for key, p in (raw.get("providers") or {}).items():
            self.providers[key] = ProviderInfo(
                key=key,
                display=p.get("display", key),
                style=p.get("style", "openai"),
                base_url=p.get("base_url", ""),
                chat_path=p.get("chat_path", "/chat/completions"),
                models_path=p.get("models_path", "/models"),
                auth=p.get("auth") or {"type": "bearer"},
                default_price=p.get("default_price") or {"in": 0.0, "out": 0.0},
                models=p.get("models") or {},
            )

    def provider(self, key: str) -> ProviderInfo:
        try:
            return self.providers[key]
        except KeyError as exc:
            raise ValueError(f"Unknown provider '{key}'") from exc

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "providers": {
                k: {
                    "display": p.display,
                    "style": p.style,
                    "base_url": p.base_url,
                    "models": {
                        m: {"in": v.get("in"), "out": v.get("out"), "context": v.get("context")}
                        for m, v in p.models.items()
                    },
                }
                for k, p in self.providers.items()
            },
        }


def _candidate_paths() -> list[Path]:
    cfg = get_settings()
    here = Path(__file__).resolve()
    return [
        cfg.registry_path,
        here.parents[3] / "registry" / "providers.json",  # repo layout: backend/app/services -> repo root
    ]


@lru_cache
def load_registry() -> Registry:
    for path in _candidate_paths():
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                return Registry(json.load(fh))
    raise FileNotFoundError("providers.json not found; set REGISTRY_PATH")


def estimate_cost(info: ModelInfo, tokens_in: int, tokens_out: int) -> float:
    return (tokens_in / 1_000_000) * info.price_in + (tokens_out / 1_000_000) * info.price_out


def rough_token_count(text: str) -> int:
    """Cheap pre-flight estimate (~4 chars/token). Real counts come back from the provider."""
    return max(1, len(text) // 4)
