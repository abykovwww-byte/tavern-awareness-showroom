"""Global service-model catalog and runtime settings.

"Service model" ("служебная модель") is the canonical project term for the
global LLM used by long-term memory, world-state changes, and character
generation. It is deliberately independent from every party narrator model.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.core.config import Settings


SERVICE_MODEL_SETTING_KEY = "service_model_choice"

# Ten inexpensive, fast OpenRouter models selected for short structured service
# work. Prices are USD per one million input/output tokens and are kept here so
# the admin can make an informed choice without loading a remote catalog.
OPENROUTER_SERVICE_MODELS: tuple[dict[str, Any], ...] = (
    {"id": "or-qwen-3.5-flash", "model": "qwen/qwen3.5-flash-02-23", "title": "Qwen 3.5 Flash", "input_price": 0.065, "output_price": 0.26, "context_tokens": 1_000_000},
    {"id": "or-qwen-3.7-flash", "model": "qwen/qwen3.7-flash", "title": "Qwen 3.7 Flash", "input_price": 0.03, "output_price": 0.13, "context_tokens": 1_000_000},
    {"id": "or-gemini-2.5-flash-lite", "model": "google/gemini-2.5-flash-lite", "title": "Gemini 2.5 Flash Lite", "input_price": 0.10, "output_price": 0.40, "context_tokens": 1_048_576},
    {"id": "or-gpt-oss-20b", "model": "openai/gpt-oss-20b", "title": "GPT OSS 20B", "input_price": 0.03, "output_price": 0.13, "context_tokens": 131_072},
    {"id": "or-gpt-oss-120b", "model": "openai/gpt-oss-120b", "title": "GPT OSS 120B", "input_price": 0.037, "output_price": 0.17, "context_tokens": 131_072},
    {"id": "or-qwen3-30b-a3b", "model": "qwen/qwen3-30b-a3b-instruct-2507", "title": "Qwen3 30B A3B Instruct", "input_price": 0.04815, "output_price": 0.19305, "context_tokens": 262_144},
    {"id": "or-openrouter-free", "model": "openrouter/free", "title": "OpenRouter Free Router", "input_price": 0.0, "output_price": 0.0, "context_tokens": 131_072},
    {"id": "or-deepseek-v3.2-exp", "model": "deepseek/deepseek-v3.2-exp", "title": "DeepSeek V3.2 Exp", "input_price": 0.27, "output_price": 0.41, "context_tokens": 163_840},
    {"id": "or-mistral-small-3.2", "model": "mistralai/mistral-small-3.2-24b-instruct", "title": "Mistral Small 3.2 24B", "input_price": 0.10, "output_price": 0.30, "context_tokens": 256_000},
    {"id": "or-gemma-3-12b", "model": "google/gemma-3-12b-it", "title": "Gemma 3 12B", "input_price": 0.05, "output_price": 0.15, "context_tokens": 131_072},
)


def service_model_choices(settings: Settings) -> list[dict[str, Any]]:
    local = {
        "id": "local-gemma",
        "provider": "local",
        "model": settings.local_llm_model_alias,
        "title": "Локальная Gemma",
        "input_price": 0.0,
        "output_price": 0.0,
        "context_tokens": settings.local_llm_context_tokens,
        "available": settings.local_llm_enabled,
    }
    remote = [
        {**item, "provider": "openrouter", "available": bool(settings.service_openrouter_api_key)}
        for item in OPENROUTER_SERVICE_MODELS
    ]
    return [local, *remote]


def service_model_choice(settings: Settings, choice_id: str | None = None) -> dict[str, Any]:
    requested = (choice_id or settings.service_model_choice).strip()
    choices = service_model_choices(settings)
    selected = next((choice for choice in choices if choice["id"] == requested), None)
    if selected is not None:
        return selected
    return {
        "id": requested,
        "provider": "retired",
        "model": "",
        "title": "Недоступная архивная настройка",
        "input_price": 0.0,
        "output_price": 0.0,
        "context_tokens": 0,
        "available": False,
    }


def service_model_settings(settings: Settings) -> Settings:
    """Return provider settings for the globally selected service model."""
    choice = service_model_choice(settings)
    if choice["provider"] not in {"local", "openrouter"}:
        raise ValueError(f"service model choice is retired or unsupported: {choice['id']}")
    if choice["provider"] == "local":
        return replace(
            settings,
            llm_provider="local",
            llm_api_base=settings.local_llm_base_url,
            llm_api_key="",
            narrative_model=str(choice["model"]),
            intent_model=str(choice["model"]),
            validator_model=str(choice["model"]),
            llm_fallback_models=(),
            llm_disabled_models=(),
            model_attempt_timeout_seconds=settings.local_llm_timeout_seconds,
        )
    return replace(
        settings,
        llm_provider="openrouter",
        llm_api_base=settings.openrouter_api_base,
        llm_api_key=settings.service_openrouter_api_key,
        narrative_model=str(choice["model"]),
        intent_model=str(choice["model"]),
        validator_model=str(choice["model"]),
        llm_fallback_models=(),
        llm_disabled_models=(),
    )
