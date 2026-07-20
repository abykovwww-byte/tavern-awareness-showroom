"""NVIDIA model catalog helpers for the Light GUI."""

from __future__ import annotations

import re
from typing import Any

import httpx


DEFAULT_CATALOG_URL = "https://build.nvidia.com/models?q=llm"


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
    "classify",
    "guard",
    "safety",
    "whisper",
    "tts",
    "speech",
    "image",
    "diffusion",
    "calibration",
    "jailbreak",
    "topic-control",
    "paligemma",
    "lipsync",
}


def profile_id_for_model(model_id: str) -> str:
    clean = re.sub(r"[^\w\-]+", "-", model_id.strip().lower(), flags=re.UNICODE).strip("-")
    return clean or "nvidia-model"


def static_model_profiles(settings: Any) -> list[dict[str, Any]]:
    profiles = [profile_payload(settings, item, rank=index + 10, source="static_build_nvidia_fallback") for index, item in enumerate(STATIC_NVIDIA_MODELS)]
    known = {profile["model"] for profile in profiles}
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
            ),
        )
    return profiles


def profile_payload(settings: Any, item: dict[str, Any], rank: int, source: str) -> dict[str, Any]:
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
        "catalog_url": item.get("catalog_url", f"https://build.nvidia.com/{model_id}"),
    }
    return {
        "id": profile_id_for_model(model_id),
        "title": f"{title} (NVIDIA)",
        "provider": "nvidia-openai-compatible",
        "base_url": settings.nvidia_api_base,
        "model": model_id,
        "params": params,
        "api_key_source": "server_env_or_authorization_header",
    }


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
    for index, model_id in enumerate(sorted(set(models))):
        profiles.append(
            profile_payload(
                settings,
                {
                    "model": model_id,
                    "title": display_title_from_model(model_id),
                    "publisher": publisher_from_model(model_id),
                    "description": "Модель возвращена NVIDIA OpenAI-compatible /v1/models для текущего ключа.",
                    "rp_fit": "Endpoint сообщает, что модель доступна для ключа; RP-качество нужно проверить на короткой сцене.",
                    "context_window": "уточняется endpoint",
                    "tags": ["live api"],
                    "availability": "NVIDIA /v1/models",
                    "catalog_url": f"https://build.nvidia.com/{model_id}",
                },
                rank=500 + index,
                source="nvidia_api_live",
            )
        )
    return profiles


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
    return "/" in lower and not any(term in lower for term in SKIP_MODEL_TERMS)


def display_title_from_model(model_id: str) -> str:
    _, _, model = model_id.partition("/")
    return model.replace("-", " ").replace("_", " ").title()
