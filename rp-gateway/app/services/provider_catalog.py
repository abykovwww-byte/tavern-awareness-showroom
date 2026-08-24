"""Model catalog helpers for active OpenAI-compatible LLM providers."""

from __future__ import annotations

import re
from typing import Any

import httpx


PROVIDER_TITLES = {
    "gemini": "Gemini",
    "openrouter": "OpenRouter",
    "local": "Local Vulkan",
}

NARRATOR_MAX_TOKEN_OPTIONS = [1024, 2048, 4096, 8192, 16384]
NARRATOR_CONTROL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "openai/gpt-5.6-luna": {
        "reasoning_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "default_reasoning_effort": "medium",
        "temperature": False,
        "top_p": False,
        "max_tokens": NARRATOR_MAX_TOKEN_OPTIONS,
    },
    "openai/gpt-5.6-luna-pro": {
        "reasoning_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "default_reasoning_effort": "medium",
        "temperature": False,
        "top_p": False,
        "max_tokens": NARRATOR_MAX_TOKEN_OPTIONS,
    },
    "deepseek/deepseek-v4-flash": {
        "reasoning_efforts": ["none", "high", "xhigh"],
        "default_reasoning_effort": "high",
        "temperature": True,
        "top_p": True,
        "max_tokens": NARRATOR_MAX_TOKEN_OPTIONS,
    },
}


def narrator_control_capabilities(provider: str, model_id: str) -> dict[str, Any]:
    if normalize_provider(provider) != "openrouter":
        return {}
    capabilities = NARRATOR_CONTROL_CAPABILITIES.get(model_id.strip().lower())
    if not capabilities:
        return {}
    return {
        **capabilities,
        "reasoning_efforts": list(capabilities["reasoning_efforts"]),
        "max_tokens": list(capabilities["max_tokens"]),
    }


def validate_narrator_settings(provider: str, model_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    if not settings:
        return {}
    capabilities = narrator_control_capabilities(provider, model_id)
    if not capabilities:
        raise ValueError("manual narrator settings are not supported by this model")
    effort = settings.get("reasoning_effort")
    if effort is not None and effort not in capabilities["reasoning_efforts"]:
        raise ValueError(f"reasoning effort {effort!r} is not supported by {model_id}")
    if settings.get("temperature") is not None and not capabilities["temperature"]:
        raise ValueError(f"temperature is not supported by {model_id}")
    if settings.get("top_p") is not None and not capabilities["top_p"]:
        raise ValueError(f"top_p is not supported by {model_id}")
    max_tokens = settings.get("max_tokens")
    if max_tokens is not None and max_tokens not in capabilities["max_tokens"]:
        raise ValueError(f"max_tokens {max_tokens!r} is not supported by {model_id}")
    return dict(settings)

STATIC_GEMINI_MODELS: list[dict[str, Any]] = [
    {
        "model": "gemini-3.6-flash",
        "title": "Gemini 3.6 Flash",
        "publisher": "Google",
        "description": "Google Gemini fast reasoning model exposed through the OpenAI-compatible API.",
        "rp_fit": "Fast option for interactive scenes and utility actions.",
        "context_window": "1,048,576 tokens",
        "context_tokens": 1_048_576,
        "tags": ["reasoning", "fast", "live catalog"],
        "availability": "Gemini API",
    },
    {
        "model": "gemini-3.5-flash",
        "title": "Gemini 3.5 Flash",
        "publisher": "Google",
        "description": "Google Gemini model exposed through the OpenAI-compatible API.",
        "rp_fit": "Balanced option for narration and frequent utility calls.",
        "context_window": "1,048,576 tokens",
        "context_tokens": 1_048_576,
        "tags": ["fast", "live catalog"],
        "availability": "Gemini API",
    },
]

STATIC_OPENROUTER_MODELS: list[dict[str, Any]] = [
    {
        "model": "openrouter/auto",
        "title": "Auto Router",
        "publisher": "OpenRouter",
        "description": "OpenRouter automatically selects a compatible text model.",
        "rp_fit": "Useful as a broad availability fallback; a specific model is more predictable for a campaign.",
        "context_window": "131,072 tokens (minimum routed budget)",
        "context_tokens": 131_072,
        "tags": ["router", "automatic"],
        "availability": "OpenRouter API",
    },
    {
        "model": "openrouter/free",
        "title": "Free Models Router",
        "publisher": "OpenRouter",
        "description": "OpenRouter selects a currently available free text model.",
        "rp_fit": "Zero-cost testing option; model identity and narrative style can change between requests.",
        "context_window": "131,072 tokens (minimum routed budget)",
        "context_tokens": 131_072,
        "tags": ["router", "automatic", "free"],
        "availability": "OpenRouter free router",
        "is_free": True,
        "pricing_prompt": "0",
        "pricing_completion": "0",
    },
]


SKIP_MODEL_TERMS = {
    "embed",
    "embedding",
    "rerank",
    "ranking",
    "classify",
    "guard",
    "safety",
    "whisper",
    "tts",
    "speech",
    "audio",
    "asr",
    "ocr",
    "clip",
    "image",
    "diffusion",
    "calibration",
    "jailbreak",
    "topic-control",
    "paligemma",
    "lipsync",
    "translate",
    "riva",
    "vila",
    "neva",
    "nvclip",
    "palmyra-fin",
    "palmyra-med",
}

LOW_CAPACITY_MODEL_TERMS = {
    "gpt-oss-20b",
    "flash-lite",
    "gpt-3.5",
    "gpt-4-turbo-preview",
    "llama-2",
}
MIN_RP_MODEL_BILLIONS = 30.0
MIN_RP_CONTEXT_TOKENS = 131072
SPECIALIZED_RP_TERMS = {
    "roleplay",
    "roleplaying",
    "storytelling",
    "creative writing",
    "interactive fiction",
    "character chat",
    "narrative-rich",
    "narrative structure",
}
OPENROUTER_RP_PREFERENCE = (
    "aion-3.0",
    "euryale",
    "cydonia",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "mimo-v2.5",
    "minimax-m3",
    "glm-5.2",
    "claude-opus",
    "claude-sonnet",
    "openrouter/free",
)

# Presentation metadata is deliberately kept separate from the live catalogue:
# availability, context, and pricing continue to come from OpenRouter /models.
OPENROUTER_FEATURED_MODELS: dict[str, dict[str, Any]] = {
    "z-ai/glm-5.2": {
        "rank": 1,
        "title": "GLM 5.2",
        "rp_fit": "Главный выбор для длинной кампании: хорошо держит канон, причинность, NPC-мотивы и многоходовые планы.",
        "tags": ["длинная кампания", "канон", "сложный GM"],
    },
    "deepseek/deepseek-v4-pro": {
        "rank": 2,
        "title": "DeepSeek V4 Pro",
        "rp_fit": "Для расследований, фракций и правил мира: сильнее всего там, где важны последствия и логика сцены.",
        "tags": ["причинность", "расследование", "правила"],
    },
    "deepseek/deepseek-v4-flash": {
        "rank": 3,
        "title": "DeepSeek V4 Flash",
        "rp_fit": "Быстрый и экономный рабочий GM для обычных ходов; выбирай, когда темп важнее литературной полировки.",
        "tags": ["быстро", "экономно", "длинный контекст"],
    },
    "qwen/qwen3.5-397b-a17b": {
        "rank": 4,
        "title": "Qwen3.5 397B A17B",
        "rp_fit": "Насыщенный GM для больших сцен и нескольких NPC: хороший баланс диалогов, следования инструкциям и масштаба.",
        "tags": ["богатая сцена", "NPC", "баланс"],
    },
    "aion-labs/aion-3.0": {
        "rank": 5,
        "title": "Aion 3.0",
        "rp_fit": "Специализированный multi-model рассказчик для ролевой игры и сторителлинга; дорогой, но уместен для ключевых сцен.",
        "tags": ["RP-специализация", "сторителлинг", "премиум"],
    },
    "sao10k/l3.3-euryale-70b": {
        "rank": 6,
        "title": "Euryale 70B",
        "rp_fit": "Творческая RP-модель для живых диалогов, характерных голосов и атмосферной прозы.",
        "tags": ["RP-специализация", "персонажи", "проза"],
    },
    "thedrummer/cydonia-24b-v4.1": {
        "rank": 7,
        "title": "Cydonia 24B V4.1",
        "rp_fit": "Недорогой креативный вариант с хорошим следованием prompt и памятью деталей сцены.",
        "tags": ["RP-специализация", "креатив", "экономно"],
    },
    "minimax/minimax-m3": {
        "rank": 8,
        "title": "MiniMax M3",
        "rp_fit": "Длинный контекст и сильная работа с текстом и изображениями; подходит для кампаний с картами и референсами.",
        "tags": ["мультимодальность", "длинная кампания", "референсы"],
    },
    "anthropic/claude-sonnet-4.6": {
        "rank": 9,
        "title": "Claude Sonnet 4.6",
        "rp_fit": "Премиальный универсальный GM для аккуратной прозы, сложных инструкций и важных поворотных сцен.",
        "tags": ["премиум", "инструкции", "поворотная сцена"],
    },
    "moonshotai/kimi-k3": {
        "rank": 10,
        "title": "Kimi K3",
        "rp_fit": "Сильная long-context альтернатива для масштабной кампании; полезна, когда нужен широкий контекст и сложное рассуждение.",
        "tags": ["длинный контекст", "сложный сюжет", "альтернатива"],
    },
}

def profile_id_for_model(model_id: str) -> str:
    clean = re.sub(r"[^\w\-]+", "-", model_id.strip().lower(), flags=re.UNICODE).strip("-")
    return clean or "model"


def normalize_provider(provider: str) -> str:
    value = provider.strip().lower()
    if value in {"nvidia", "nvidia-openai-compatible"}:
        return "nvidia"
    return value


def profile_id_for_provider_model(provider: str, model_id: str) -> str:
    clean_provider = normalize_provider(provider)
    return f"{clean_provider}-{profile_id_for_model(model_id)}"


def provider_base_url(settings: Any, provider: str) -> str:
    clean_provider = normalize_provider(provider)
    if clean_provider == "local":
        return str(settings.local_llm_base_url)
    if clean_provider == "gemini":
        return str(settings.gemini_api_base)
    if clean_provider == "openrouter":
        return str(settings.openrouter_api_base)
    raise ValueError(f"provider is retired or unsupported: {provider}")


def provider_api_key(settings: Any, provider: str) -> str:
    clean_provider = normalize_provider(provider)
    if clean_provider == "local":
        return ""
    if clean_provider == "gemini":
        return str(settings.gemini_api_key)
    if clean_provider == "openrouter":
        return str(settings.openrouter_api_key)
    raise ValueError(f"provider is retired or unsupported: {provider}")


def enrich_openrouter_profile_params(model_id: str, params: dict[str, Any]) -> dict[str, Any]:
    featured = OPENROUTER_FEATURED_MODELS.get(model_id)
    if not featured:
        return params
    enriched = dict(params)
    enriched["featured_rank"] = featured["rank"]
    enriched["title_override"] = featured["title"]
    enriched["rp_fit"] = featured["rp_fit"]
    tags = ["Избранное", *featured["tags"], *list(enriched.get("tags") or [])]
    enriched["tags"] = list(dict.fromkeys(str(tag) for tag in tags if tag))
    return enriched


def static_model_profiles(settings: Any) -> list[dict[str, Any]]:
    profiles = configured_provider_profiles(
        settings,
        provider="gemini",
        configured_models=settings.gemini_models,
        static_items=STATIC_GEMINI_MODELS,
        rank_start=1000,
    )
    profiles.extend(
        configured_provider_profiles(
            settings,
            provider="openrouter",
            configured_models=settings.openrouter_models,
            static_items=STATIC_OPENROUTER_MODELS,
            rank_start=2900,
        )
    )
    if getattr(settings, "local_llm_enabled", False):
        profiles.insert(
            0,
            profile_payload(
                settings,
                {
                    "model": settings.local_llm_model_alias,
                    "title": "Gemma 4 26B A4B QAT Q4",
                    "publisher": "Google",
                    "description": "Локальная Gemma 4 на Radeon 780M через Vulkan; модель доступна только Gateway внутри Docker.",
                    "rp_fit": "Локальный одиночный RP-рассказчик: 32k рабочий контекст, без неявного cloud fallback.",
                    "context_window": f"{settings.local_llm_context_tokens:,} tokens (working budget)",
                    "context_tokens": settings.local_llm_context_tokens,
                    "tags": ["local", "Vulkan", "Radeon 780M", "no cloud fallback"],
                    "temperature": 0.85,
                    "max_tokens": 1200,
                    "availability": "Local runner / Vulkan",
                    "catalog_url": "",
                },
                rank=-10,
                source="local_vulkan",
                provider="local",
            ),
        )
    return profiles


def configured_provider_profiles(
    settings: Any,
    provider: str,
    configured_models: tuple[str, ...],
    static_items: list[dict[str, Any]],
    rank_start: int,
) -> list[dict[str, Any]]:
    known = {str(item["model"]): item for item in static_items}
    profiles: list[dict[str, Any]] = []
    for index, model_id in enumerate(configured_models):
        item = dict(known.get(model_id, {}))
        item.setdefault("model", model_id)
        item.setdefault("title", display_title_from_model(model_id))
        item.setdefault("publisher", PROVIDER_TITLES[provider])
        item.setdefault("description", f"Server-configured {PROVIDER_TITLES[provider]} model.")
        item.setdefault("rp_fit", "Explicit server model; validate style on a short scene before a long campaign.")
        item.setdefault("tags", ["server configured"])
        item.setdefault("availability", f"{PROVIDER_TITLES[provider]} API")
        profiles.append(
            profile_payload(
                settings,
                item,
                rank=rank_start + index,
                source=f"{provider}_server_config",
                provider=provider,
            )
        )
    return profiles


def profile_payload(
    settings: Any,
    item: dict[str, Any],
    rank: int,
    source: str,
    provider: str,
) -> dict[str, Any]:
    provider = normalize_provider(provider)
    model_id = str(item["model"])
    title = str(item.get("title") or model_id)
    description = str(item.get("description") or "OpenAI-compatible text model.")
    rp_fit = str(item.get("rp_fit") or "Validate prose style on a short scene before a long campaign.")
    params = {
        "temperature": item.get("temperature", 0.8),
        "max_tokens": item.get("max_tokens", 4096),
        "rank": rank,
        "description": description,
        "rp_fit": rp_fit,
        "context_window": item.get("context_window", ""),
        "context_tokens": item.get("context_tokens"),
        "tags": item.get("tags", []),
        "source": source,
        "publisher": item.get("publisher", publisher_from_model(model_id)),
        "availability": item.get("availability", ""),
        "catalog_url": item.get("catalog_url", provider_catalog_url(provider, model_id)),
        "is_free": bool(item.get("is_free", False)),
        "pricing_prompt": str(item.get("pricing_prompt", "")),
        "pricing_completion": str(item.get("pricing_completion", "")),
        "pricing_input_cache_read": str(item.get("pricing_input_cache_read", "")),
        "pricing_input_cache_write": str(item.get("pricing_input_cache_write", "")),
        "pricing_input_cache_write_1h": str(item.get("pricing_input_cache_write_1h", "")),
        "rp_specialized": bool(item.get("rp_specialized", False)),
    }
    return {
        "id": profile_id_for_provider_model(provider, model_id),
        "title": f"{title} ({PROVIDER_TITLES.get(provider, provider.title())})",
        "provider": provider,
        "base_url": provider_base_url(settings, provider),
        "model": model_id,
        "params": params,
        "api_key_source": "none" if provider == "local" else "server_env_or_managed_key",
    }


def provider_catalog_url(provider: str, model_id: str) -> str:
    if provider == "gemini":
        return "https://ai.google.dev/gemini-api/docs/models"
    if provider == "openrouter":
        return f"https://openrouter.ai/{model_id}"
    return ""


def publisher_from_model(model_id: str) -> str:
    publisher, _, _ = model_id.partition("/")
    return publisher.replace("-", " ").replace("_", " ").title() or "Unknown"


def fetch_provider_api_profiles(settings: Any, provider: str) -> list[dict[str, Any]]:
    provider = normalize_provider(provider)
    if provider not in {"gemini", "openrouter"}:
        return []
    api_key = provider_api_key(settings, provider)
    if provider == "gemini" and not api_key:
        return []

    base_url = provider_base_url(settings, provider).rstrip("/")
    headers = {"User-Agent": "rp-gateway/0.7"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = httpx.Timeout(8.0, connect=3.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(f"{base_url}/models", headers=headers)
    response.raise_for_status()

    profiles: list[dict[str, Any]] = []
    for index, raw in enumerate(response.json().get("data", [])):
        model_id = str(raw.get("id") or "").strip()
        if not provider_model_is_suitable(provider, model_id, raw):
            continue
        context_length = raw.get("context_length")
        context_label = f"{int(context_length):,} tokens" if isinstance(context_length, (int, float)) else "provider catalog"
        supported_raw = raw.get("supported_parameters") or []
        supported = [str(value) for value in supported_raw[:6]] if isinstance(supported_raw, list) else []
        description = str(raw.get("description") or f"Model returned by the {PROVIDER_TITLES[provider]} catalog.")
        rp_specialized = has_specialized_rp_signal(model_id, description)
        pricing = raw.get("pricing") or {}
        prompt_price = str(pricing.get("prompt") or "")
        completion_price = str(pricing.get("completion") or "")
        is_free = model_id.endswith(":free") or model_id == "openrouter/free" or prices_are_free(prompt_price, completion_price)
        item = {
            "model": model_id,
            "title": str(raw.get("name") or display_title_from_model(model_id)),
            "publisher": publisher_from_model(model_id) if "/" in model_id else PROVIDER_TITLES[provider],
            "description": description,
            "rp_fit": (
                f"RP-specialized: {description[:420]}{'...' if len(description) > 420 else ''}"
                if rp_specialized
                else "Long-context text model available from the selected provider; validate prose style before a long campaign."
            ),
            "context_window": context_label,
            "context_tokens": int(context_length) if isinstance(context_length, (int, float)) else None,
            "tags": ["live api", *( ["RP specialized"] if rp_specialized else []), *( ["FREE"] if is_free else []), *supported],
            "availability": f"{PROVIDER_TITLES[provider]} /models",
            "catalog_url": provider_catalog_url(provider, model_id),
            "is_free": is_free,
            "pricing_prompt": prompt_price,
            "pricing_completion": completion_price,
            "pricing_input_cache_read": str(pricing.get("input_cache_read") or ""),
            "pricing_input_cache_write": str(pricing.get("input_cache_write") or ""),
            "pricing_input_cache_write_1h": str(pricing.get("input_cache_write_1h") or ""),
            "rp_specialized": rp_specialized,
        }
        profiles.append(
            profile_payload(
                settings,
                item,
                rank=(1100 + index if provider == "gemini" else openrouter_rp_rank(model_id, description, index)),
                source=f"{provider}_api_live",
                provider=provider,
            )
        )
    return profiles


def provider_model_is_suitable(provider: str, model_id: str, raw: dict[str, Any]) -> bool:
    if not model_id:
        return False
    if provider == "openrouter" and model_id.lower().endswith(":batch"):
        return False
    if provider == "gemini":
        return (
            is_quality_rp_model(model_id)
            and model_id.startswith("gemini-")
            and not any(term in model_id for term in {"image", "audio", "embedding", "tts"})
        )
    if "/" not in model_id:
        return False
    architecture = raw.get("architecture") or {}
    output_modalities = architecture.get("output_modalities") or []
    if output_modalities and "text" not in output_modalities:
        return False
    context_length = raw.get("context_length")
    description = str(raw.get("description") or "")
    rp_specialized = has_specialized_rp_signal(model_id, description)
    if not rp_specialized and not is_quality_rp_model(model_id):
        return False
    return isinstance(context_length, (int, float)) and context_length >= MIN_RP_CONTEXT_TOKENS


def has_specialized_rp_signal(model_id: str, description: str) -> bool:
    haystack = f"{model_id} {description}".lower()
    return any(term in haystack for term in SPECIALIZED_RP_TERMS)


def prices_are_free(prompt_price: str, completion_price: str) -> bool:
    try:
        return float(prompt_price) == 0 and float(completion_price) == 0
    except (TypeError, ValueError):
        return False


def openrouter_rp_rank(model_id: str, description: str, catalog_index: int) -> int:
    haystack = f"{model_id} {description}".lower()
    for index, term in enumerate(OPENROUTER_RP_PREFERENCE):
        if term in haystack:
            return 2000 + index
    if has_specialized_rp_signal(model_id, description):
        return 2050 + catalog_index
    return 3000 + catalog_index


def is_quality_rp_model(model_id: str) -> bool:
    lower = model_id.lower()
    if any(term in lower for term in SKIP_MODEL_TERMS | LOW_CAPACITY_MODEL_TERMS):
        return False
    if re.search(r"(?:^|[-_/:])(mini|nano|small|lite)(?:[-_/:]|$)", lower):
        return False
    sizes = [float(value) for value in re.findall(r"(?:^|[-_/])(\d+(?:\.\d+)?)b(?:[-_/]|$)", lower)]
    return not sizes or max(sizes) >= MIN_RP_MODEL_BILLIONS


def display_title_from_model(model_id: str) -> str:
    model = model_id.rpartition("/")[2]
    return model.replace("-", " ").replace("_", " ").title()
