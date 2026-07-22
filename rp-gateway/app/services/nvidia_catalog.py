"""Model catalog helpers for the OpenAI-compatible LLM providers."""

from __future__ import annotations

import re
from typing import Any

import httpx


DEFAULT_CATALOG_URL = "https://build.nvidia.com/models?q=llm"
PROVIDER_TITLES = {
    "nvidia": "NVIDIA",
    "gemini": "Gemini",
    "openrouter": "OpenRouter",
}

STATIC_GEMINI_MODELS: list[dict[str, Any]] = [
    {
        "model": "gemini-3.6-flash",
        "title": "Gemini 3.6 Flash",
        "publisher": "Google",
        "description": "Google Gemini fast reasoning model exposed through the OpenAI-compatible API.",
        "rp_fit": "Fast option for interactive scenes and utility actions.",
        "tags": ["reasoning", "fast", "live catalog"],
        "availability": "Gemini API",
    },
    {
        "model": "gemini-3.5-flash",
        "title": "Gemini 3.5 Flash",
        "publisher": "Google",
        "description": "Google Gemini model exposed through the OpenAI-compatible API.",
        "rp_fit": "Balanced option for narration and frequent utility calls.",
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
        "tags": ["router", "automatic"],
        "availability": "OpenRouter API",
    },
    {
        "model": "openrouter/free",
        "title": "Free Models Router",
        "publisher": "OpenRouter",
        "description": "OpenRouter selects a currently available free text model.",
        "rp_fit": "Zero-cost testing option; model identity and narrative style can change between requests.",
        "tags": ["router", "automatic", "free"],
        "availability": "OpenRouter free router",
        "is_free": True,
        "pricing_prompt": "0",
        "pricing_completion": "0",
    },
]


STATIC_NVIDIA_MODELS: list[dict[str, Any]] = [
    {
        "model": "z-ai/glm-5.2",
        "title": "GLM-5.2",
        "publisher": "Z.ai",
        "description": "Флагманская long-context модель для agentic workflows, coding и долгого reasoning.",
        "rp_fit": "Лучший дефолт для длинных кампаний: держит сложные интриги, правила мира, NPC-мотивы и многоходовые планы.",
        "context_window": "до 1M токенов по model card",
        "tags": ["длинный контекст", "reasoning", "агентность", "сложный сюжет"],
        "temperature": 0.8,
        "max_tokens": 16384,
        "availability": "Free Endpoint / Partner / Download",
        "catalog_url": "https://build.nvidia.com/z-ai/glm-5.2",
    },
    {
        "model": "deepseek-ai/deepseek-v4-pro",
        "title": "DeepSeek V4 Pro",
        "publisher": "DeepSeek AI",
        "description": "MoE модель с упором на 1M-context, coding, agents и reasoning.",
        "rp_fit": "Хороша для логически плотных партий: расследования, правила фракций, причинно-следственные цепочки. Проза может быть суше, зато решения стабильные.",
        "context_window": "до 1M токенов по каталогу",
        "tags": ["reasoning", "длинный контекст", "структура", "расследования"],
        "temperature": 0.75,
        "max_tokens": 16384,
        "availability": "Free Endpoint / Partner / Download",
        "catalog_url": "https://build.nvidia.com/deepseek-ai/deepseek-v4-pro",
    },
    {
        "model": "deepseek-ai/deepseek-v4-flash",
        "title": "DeepSeek V4 Flash",
        "publisher": "DeepSeek AI",
        "description": "Быстрая версия DeepSeek V4 для agents и coding.",
        "rp_fit": "Подходит для быстрых ходов, проверок и коротких сцен, когда важнее темп, чем литературная отделка.",
        "context_window": "до 1M токенов по каталогу",
        "tags": ["быстро", "reasoning", "проверки", "низкая задержка"],
        "temperature": 0.75,
        "max_tokens": 8192,
        "availability": "Free Endpoint / Download",
        "catalog_url": "https://build.nvidia.com/deepseek-ai/deepseek-v4-flash",
    },
    {
        "model": "qwen/qwen3.5-122b-a10b",
        "title": "Qwen3.5 122B A10B",
        "publisher": "Qwen",
        "description": "122B MoE LLM для coding, reasoning, multimodal chat и tool calling.",
        "rp_fit": "Сильный баланс для RP: хорошо держит инструкции, JSON/state дисциплину и живые диалоги без чрезмерной тяжеловесности.",
        "context_window": "уточняется каталогом NVIDIA",
        "tags": ["диалоги", "tool calling", "state", "баланс"],
        "temperature": 0.7,
        "max_tokens": 12000,
        "availability": "Free Endpoint / Partner / Download",
        "catalog_url": "https://build.nvidia.com/qwen/qwen3.5-122b-a10b",
    },
    {
        "model": "qwen/qwen3.5-397b-a17b",
        "title": "Qwen3.5 397B A17B",
        "publisher": "Qwen",
        "description": "Крупная MoE/VLM модель для reasoning, chat, RAG и agentic workflows.",
        "rp_fit": "Для насыщенного GM-режима: большие сцены, несколько NPC, заметки мира и сложные переходы между сюжетными линиями.",
        "context_window": "уточняется каталогом NVIDIA",
        "tags": ["богатая сцена", "reasoning", "мультимодальность", "сложный GM"],
        "temperature": 0.75,
        "max_tokens": 16384,
        "availability": "Free Endpoint / Partner / Download",
        "catalog_url": "https://build.nvidia.com/qwen/qwen3.5-397b-a17b",
    },
    {
        "model": "qwen/qwen3-next-80b-a3b-instruct",
        "title": "Qwen3 Next 80B A3B Instruct",
        "publisher": "Qwen",
        "description": "Sparse MoE instruct model с ultra-long context и устойчивой генерацией.",
        "rp_fit": "Хороший вариант для долгих текстовых кампаний, где нужен длинный контекст и умеренная стоимость хода.",
        "context_window": "ultra-long context по каталогу",
        "tags": ["длинный контекст", "instruct", "экономный MoE", "кампания"],
        "temperature": 0.75,
        "max_tokens": 12000,
        "availability": "Free Endpoint / Download",
        "catalog_url": "https://build.nvidia.com/qwen/qwen3-next-80b-a3b-instruct",
    },
    {
        "model": "mistralai/mistral-medium-3.5-128b",
        "title": "Mistral Medium 3.5 128B",
        "publisher": "Mistral AI",
        "description": "Высокопроизводительная модель для text generation, coding и agentic use cases.",
        "rp_fit": "Сильная литературная подача и нормальная дисциплина инструкций: хорошо для атмосферного GM с проверками.",
        "context_window": "уточняется каталогом NVIDIA",
        "tags": ["проза", "reasoning", "атмосфера", "инструкции"],
        "temperature": 0.8,
        "max_tokens": 12000,
        "availability": "Free Endpoint / Download",
        "catalog_url": "https://build.nvidia.com/mistralai/mistral-medium-3.5-128b",
    },
    {
        "model": "mistralai/mistral-small-4-119b-2603",
        "title": "Mistral Small 4 119B",
        "publisher": "Mistral AI",
        "description": "Hybrid MoE instruct/reasoning/coding model с 256k context и multimodal input.",
        "rp_fit": "Добрый компромисс для регулярной игры: быстрый темп, нормальный контекст, уверенное следование правилам.",
        "context_window": "256k по каталогу",
        "tags": ["баланс", "256k", "темп", "инструкции"],
        "temperature": 0.75,
        "max_tokens": 10000,
        "availability": "Free Endpoint / Download",
        "catalog_url": "https://build.nvidia.com/mistralai/mistral-small-4-119b-2603",
    },
    {
        "model": "mistralai/ministral-14b-instruct-2512",
        "title": "Ministral 14B Instruct",
        "publisher": "Mistral AI",
        "description": "Компактная general-purpose VLM для chat и instruction use cases.",
        "rp_fit": "Для быстрых тестов, легких сцен и коротких партий. Меньше глубины, зато отзывчивый MVP-режим.",
        "context_window": "262k по каталогу",
        "tags": ["быстро", "легкий GM", "тесты", "262k"],
        "temperature": 0.85,
        "max_tokens": 4096,
        "availability": "Free Endpoint / Download",
        "catalog_url": "https://build.nvidia.com/mistralai/ministral-14b-instruct-2512",
    },
    {
        "model": "mistralai/mistral-nemotron",
        "title": "Mistral Nemotron",
        "publisher": "Mistral AI",
        "description": "Agentic model для coding, instruction following и function calling.",
        "rp_fit": "Практичный GM для structured flow: команды, /check, world edits и аккуратное следование системным ограничениям.",
        "context_window": "уточняется каталогом NVIDIA",
        "tags": ["function calling", "инструкции", "state", "GM tools"],
        "temperature": 0.7,
        "max_tokens": 4096,
        "availability": "Free Endpoint",
        "catalog_url": "https://build.nvidia.com/mistralai/mistral-nemotron",
    },
    {
        "model": "mistralai/mixtral-8x7b-instruct-v0.1",
        "title": "Mixtral 8x7B Instruct",
        "publisher": "Mistral AI",
        "description": "MoE instruct model для следования инструкциям и creative text.",
        "rp_fit": "Старый рабочий вариант для творческого текста и простых сцен, но хуже держит сложный state, чем свежие модели.",
        "context_window": "уточняется каталогом NVIDIA",
        "tags": ["креатив", "легаси", "простые сцены"],
        "temperature": 0.9,
        "max_tokens": 4096,
        "availability": "Free Endpoint / Download",
        "catalog_url": "https://build.nvidia.com/mistralai/mixtral-8x7b-instruct-v0.1",
    },
    {
        "model": "meta/llama-3.3-70b-instruct",
        "title": "Llama 3.3 70B Instruct",
        "publisher": "Meta",
        "description": "Advanced LLM для reasoning, math, general knowledge и function calling.",
        "rp_fit": "Надежный baseline для диалогов, общих сцен и NPC. Хорошо звучит, но не такой длинноконтекстный, как GLM/DeepSeek.",
        "context_window": "уточняется каталогом NVIDIA",
        "tags": ["диалог", "baseline", "NPC", "reasoning"],
        "temperature": 0.8,
        "max_tokens": 4096,
        "availability": "Free Endpoint / Partner / Download",
        "catalog_url": "https://build.nvidia.com/meta/llama-3_3-70b-instruct",
    },
    {
        "model": "meta/llama-3.1-70b-instruct",
        "title": "Llama 3.1 70B Instruct",
        "publisher": "Meta",
        "description": "Модель для сложных conversation tasks, reasoning и text generation.",
        "rp_fit": "Стабильный диалоговый GM для классического fantasy/sci-fi RP, если не нужен самый свежий long-context профиль.",
        "context_window": "уточняется каталогом NVIDIA",
        "tags": ["диалог", "стабильность", "общий GM"],
        "temperature": 0.8,
        "max_tokens": 4096,
        "availability": "Free Endpoint / Partner / Download",
        "catalog_url": "https://build.nvidia.com/meta/llama-3_1-70b-instruct",
    },
    {
        "model": "meta/llama-3.2-3b-instruct",
        "title": "Llama 3.2 3B Instruct",
        "publisher": "Meta",
        "description": "Маленькая instruct model для language understanding, reasoning и text generation.",
        "rp_fit": "Только для smoke-тестов и очень легких сцен: быстро, но будет терять нюансы мира и долгой истории.",
        "context_window": "уточняется каталогом NVIDIA",
        "tags": ["smoke test", "быстро", "маленькая"],
        "temperature": 0.8,
        "max_tokens": 2048,
        "availability": "Free Endpoint / Partner / Download",
        "catalog_url": "https://build.nvidia.com/meta/llama-3.2-3b-instruct",
    },
    {
        "model": "openai/gpt-oss-120b",
        "title": "GPT-OSS 120B",
        "publisher": "OpenAI",
        "description": "Open-weight MoE reasoning model для high reasoning, instruction following и tool use.",
        "rp_fit": "Сильный выбор для правил, проверок и причинности. Для художественной атмосферы лучше поднять temperature и давать явный стиль.",
        "context_window": "128k по model card",
        "tags": ["reasoning", "rules", "tool use", "128k"],
        "temperature": 0.85,
        "max_tokens": 8192,
        "availability": "Free Endpoint / Partner / Download",
        "catalog_url": "https://build.nvidia.com/openai/gpt-oss-120b",
    },
    {
        "model": "openai/gpt-oss-20b",
        "title": "GPT-OSS 20B",
        "publisher": "OpenAI",
        "description": "Smaller MoE reasoning model для lower latency и local/specialized use cases.",
        "rp_fit": "Для быстрых технических проверок, черновых сцен и экономных ходов. В сложной драме лучше выбрать 120B/GLM/Qwen.",
        "context_window": "128k family context по model card",
        "tags": ["быстро", "reasoning", "черновик", "экономно"],
        "temperature": 0.85,
        "max_tokens": 4096,
        "availability": "Free Endpoint / Download",
        "catalog_url": "https://build.nvidia.com/openai/gpt-oss-20b",
    },
    {
        "model": "nvidia/nvidia-nemotron-nano-9b-v2",
        "title": "NVIDIA Nemotron Nano 9B v2",
        "publisher": "NVIDIA",
        "description": "High-efficiency hybrid Transformer-Mamba LLM для reasoning и agentic tasks.",
        "rp_fit": "Легкий быстрый судья для /check и world-management. Для красивой прозы лучше крупнее.",
        "context_window": "уточняется каталогом NVIDIA",
        "tags": ["быстро", "checks", "agentic", "маленькая"],
        "temperature": 0.7,
        "max_tokens": 4096,
        "availability": "Free Endpoint / Partner / Download",
        "catalog_url": "https://build.nvidia.com/nvidia/nvidia-nemotron-nano-9b-v2",
    },
    {
        "model": "nvidia/nemotron-mini-4b-instruct",
        "title": "NVIDIA Nemotron Mini 4B Instruct",
        "publisher": "NVIDIA",
        "description": "Optimized SLM для on-device inference, roleplay, RAG и function calling.",
        "rp_fit": "Самый легкий RP-профиль: годится для быстрых локальных/дешевых сцен, но не для глубокой кампании.",
        "context_window": "уточняется каталогом NVIDIA",
        "tags": ["roleplay", "RAG", "function calling", "легкая"],
        "temperature": 0.85,
        "max_tokens": 2048,
        "availability": "Free Endpoint",
        "catalog_url": "https://build.nvidia.com/nvidia/nemotron-mini-4b-instruct",
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
MIN_RP_CONTEXT_TOKENS = 65536
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
    "nemotron-3-ultra",
    "nemotron-3-super",
    "openrouter/free",
)

CHAT_MODEL_TERMS = {
    "baichuan",
    "chat",
    "command",
    "deepseek",
    "dbrx",
    "gemma",
    "glm",
    "gpt-oss",
    "granite",
    "inkling",
    "instruct",
    "laguna",
    "llama",
    "mistral",
    "mixtral",
    "nemotron",
    "palmyra-creative",
    "phi",
    "qwen",
    "sarvam",
    "seed",
    "solar",
    "step",
    "yi",
    "zamba",
}


def profile_id_for_model(model_id: str) -> str:
    clean = re.sub(r"[^\w\-]+", "-", model_id.strip().lower(), flags=re.UNICODE).strip("-")
    return clean or "nvidia-model"


def normalize_provider(provider: str) -> str:
    value = provider.strip().lower()
    if value in {"nvidia", "nvidia-openai-compatible"}:
        return "nvidia"
    return value


def profile_id_for_provider_model(provider: str, model_id: str) -> str:
    clean_provider = normalize_provider(provider)
    model_slug = profile_id_for_model(model_id)
    return model_slug if clean_provider == "nvidia" else f"{clean_provider}-{model_slug}"


def provider_base_url(settings: Any, provider: str) -> str:
    clean_provider = normalize_provider(provider)
    if clean_provider == "gemini":
        return str(settings.gemini_api_base)
    if clean_provider == "openrouter":
        return str(settings.openrouter_api_base)
    return str(settings.nvidia_api_base)


def provider_api_key(settings: Any, provider: str) -> str:
    clean_provider = normalize_provider(provider)
    if clean_provider == "gemini":
        return str(settings.gemini_api_key)
    if clean_provider == "openrouter":
        return str(settings.openrouter_api_key)
    return str(settings.nvidia_api_key)


def static_model_profiles(settings: Any) -> list[dict[str, Any]]:
    curated_nvidia = [item for item in STATIC_NVIDIA_MODELS if is_rp_candidate(str(item["model"]))]
    profiles = [
        profile_payload(settings, item, rank=index + 10, source="static_build_nvidia_fallback", provider="nvidia")
        for index, item in enumerate(curated_nvidia)
    ]
    disabled = set(getattr(settings, "nvidia_disabled_models", ()))
    for profile in profiles:
        if profile["model"] == getattr(settings, "narrative_model", "") and profile["model"] not in disabled:
            profile["params"]["rank"] = 0
        if profile["model"] in disabled:
            profile["params"]["rank"] = max(int(profile["params"].get("rank", 9999)), 900)
            profile["params"]["availability"] = f"{profile['params'].get('availability', '').strip()} / disabled on this server".strip(" /")
    known = {profile["model"] for profile in profiles if profile["provider"] == "nvidia"}
    if settings.narrative_model not in known:
        profiles.insert(
            0,
            profile_payload(
                settings,
                {
                    "model": settings.narrative_model,
                    "title": settings.narrative_model,
                    "publisher": "NVIDIA",
                    "description": "Модель задана через NARRATIVE_MODEL/NVIDIA_MODEL в окружении gateway.",
                    "rp_fit": "Серверный override. Используй, если этот alias точно доступен в твоем NVIDIA endpoint.",
                    "context_window": "зависит от выбранной модели",
                    "tags": ["server override"],
                    "temperature": 0.8,
                    "max_tokens": 4096,
                    "availability": "env override",
                    "catalog_url": DEFAULT_CATALOG_URL,
                },
                rank=0,
                source="server_env",
                provider="nvidia",
            ),
        )

    profiles.extend(
        configured_provider_profiles(
            settings,
            provider="gemini",
            configured_models=settings.gemini_models,
            static_items=STATIC_GEMINI_MODELS,
            rank_start=1000,
        )
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
    provider: str = "nvidia",
) -> dict[str, Any]:
    provider = normalize_provider(provider)
    model_id = str(item["model"])
    title = str(item.get("title") or model_id)
    description = str(item.get("description") or "Модель из каталога NVIDIA NIM.")
    rp_fit = str(item.get("rp_fit") or "Доступна в NVIDIA catalog; RP-поведение нужно проверить на короткой сцене.")
    params = {
        "temperature": item.get("temperature", 0.8),
        "max_tokens": item.get("max_tokens", 4096),
        "rank": rank,
        "description": description,
        "rp_fit": rp_fit,
        "context_window": item.get("context_window", ""),
        "tags": item.get("tags", []),
        "source": source,
        "publisher": item.get("publisher", publisher_from_model(model_id)),
        "availability": item.get("availability", ""),
        "catalog_url": item.get("catalog_url", provider_catalog_url(provider, model_id)),
        "is_free": bool(item.get("is_free", False)),
        "pricing_prompt": str(item.get("pricing_prompt", "")),
        "pricing_completion": str(item.get("pricing_completion", "")),
        "rp_specialized": bool(item.get("rp_specialized", False)),
    }
    return {
        "id": profile_id_for_provider_model(provider, model_id),
        "title": f"{title} ({PROVIDER_TITLES.get(provider, provider.title())})",
        "provider": provider,
        "base_url": provider_base_url(settings, provider),
        "model": model_id,
        "params": params,
        "api_key_source": "server_env_or_managed_key",
    }


def provider_catalog_url(provider: str, model_id: str) -> str:
    if provider == "gemini":
        return "https://ai.google.dev/gemini-api/docs/models"
    if provider == "openrouter":
        return f"https://openrouter.ai/{model_id}"
    return f"https://build.nvidia.com/{model_id}"


def publisher_from_model(model_id: str) -> str:
    publisher, _, _ = model_id.partition("/")
    return publisher.replace("-", " ").replace("_", " ").title() or "NVIDIA"


def fetch_build_nvidia_profiles(settings: Any) -> list[dict[str, Any]]:
    url = getattr(settings, "nvidia_model_catalog_url", DEFAULT_CATALOG_URL) or DEFAULT_CATALOG_URL
    timeout = httpx.Timeout(4.0, connect=2.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": "rp-gateway/0.6"})
    response.raise_for_status()
    models = parse_build_catalog(response.text)
    profiles: list[dict[str, Any]] = []
    known_static = {item["model"]: item for item in STATIC_NVIDIA_MODELS}
    for index, model_id in enumerate(models):
        item = dict(known_static.get(model_id, {}))
        item.setdefault("model", model_id)
        item.setdefault("title", display_title_from_model(model_id))
        item.setdefault("publisher", publisher_from_model(model_id))
        item.setdefault("description", "Модель найдена в live-каталоге build.nvidia.com.")
        item.setdefault("rp_fit", "Новый или неописанный профиль: начни с короткой сцены и проверь стиль, память и следование /check.")
        item.setdefault("context_window", "уточняется каталогом NVIDIA")
        item.setdefault("tags", ["live catalog"])
        item.setdefault("availability", "build.nvidia.com catalog")
        item.setdefault("catalog_url", f"https://build.nvidia.com/{model_id}")
        profiles.append(profile_payload(settings, item, rank=200 + index, source="build_nvidia_live"))
    return profiles


def fetch_integrate_api_profiles(settings: Any) -> list[dict[str, Any]]:
    if not getattr(settings, "nvidia_api_key", ""):
        return []
    base_url = settings.nvidia_api_base.rstrip("/")
    timeout = httpx.Timeout(4.0, connect=2.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {settings.nvidia_api_key}", "User-Agent": "rp-gateway/0.6"},
        )
    response.raise_for_status()
    data = response.json()
    models = []
    for item in data.get("data", []):
        model_id = str(item.get("id") or "").strip()
        if model_id and is_rp_candidate(model_id):
            models.append(model_id)
    profiles: list[dict[str, Any]] = []
    known_static = {item["model"]: item for item in STATIC_NVIDIA_MODELS}
    for index, model_id in enumerate(sorted(set(models))):
        item = dict(known_static.get(model_id, {}))
        item.setdefault("model", model_id)
        item.setdefault("title", display_title_from_model(model_id))
        item.setdefault("publisher", publisher_from_model(model_id))
        item.setdefault("description", "Модель возвращена NVIDIA OpenAI-compatible /v1/models для текущего ключа.")
        item.setdefault("rp_fit", "Endpoint сообщает, что модель доступна для ключа; RP-качество нужно проверить на короткой сцене.")
        item.setdefault("context_window", "уточняется endpoint")
        item.setdefault("tags", ["live api"])
        item["availability"] = "NVIDIA /v1/models"
        item.setdefault("catalog_url", f"https://build.nvidia.com/{model_id}")
        profiles.append(
            profile_payload(
                settings,
                item,
                rank=500 + index,
                source="nvidia_api_live",
            )
        )
    return profiles


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
            "tags": ["live api", *( ["RP specialized"] if rp_specialized else []), *( ["FREE"] if is_free else []), *supported],
            "availability": f"{PROVIDER_TITLES[provider]} /models",
            "catalog_url": provider_catalog_url(provider, model_id),
            "is_free": is_free,
            "pricing_prompt": prompt_price,
            "pricing_completion": completion_price,
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
    minimum_context = 32768 if rp_specialized else MIN_RP_CONTEXT_TOKENS
    return not isinstance(context_length, (int, float)) or context_length >= minimum_context


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


def parse_build_catalog(html: str) -> list[str]:
    models: list[str] = []
    for href in re.findall(r'href=["\'](/[^"\']+)["\']', html):
        model_id = model_id_from_href(href)
        if model_id and is_rp_candidate(model_id):
            models.append(model_id)
    return sorted(set(models), key=models.index)


def model_id_from_href(href: str) -> str | None:
    path = href.split("?", 1)[0].strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    if parts[0] in {"models", "skills", "blueprints", "docs"}:
        return None
    publisher, model_slug = parts[0], parts[1]
    if publisher in {"meta"}:
        model_slug = model_slug.replace("_", ".")
    model_id = f"{publisher}/{model_slug}"
    return model_id if "/" in model_id else None


def is_rp_candidate(model_id: str) -> bool:
    lower = model_id.lower()
    return "/" in lower and is_quality_rp_model(lower) and any(term in lower for term in CHAT_MODEL_TERMS)


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
