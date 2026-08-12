"""Environment configuration for RP Gateway."""

from __future__ import annotations

import os
from dataclasses import dataclass


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


def env_list(name: str, default: str = "") -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "production")
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = env_int("APP_PORT", 8088)
    campaign_id: str = os.getenv("CAMPAIGN_ID", "default")
    scenario_type: str = os.getenv("SCENARIO_TYPE", "rp")
    rp_contract_version: str = os.getenv("RP_CONTRACT_VERSION", "rp-core.v1")
    rp_contract_revision: int = env_int("RP_CONTRACT_REVISION", 0)
    rp_contract_observed_revision: int = env_int("RP_CONTRACT_OBSERVED_REVISION", 0)
    world_system_prompt: str = ""
    world_authors_note: str = ""
    database_url: str = os.getenv("DATABASE_URL", "sqlite:////data/rp_gateway.db")
    world_state_path: str = os.getenv("WORLD_STATE_PATH", "/state/current.json")
    party_state_root: str = os.getenv("PARTY_STATE_ROOT", "/state/parties")
    state_schema_path: str = os.getenv("STATE_SCHEMA_PATH", "/state/schema.json")
    worldpacks_path: str = os.getenv("WORLD_PACKS_PATH", "/worldpacks")
    nvidia_api_base: str = os.getenv("NVIDIA_API_BASE", "https://integrate.api.nvidia.com/v1")
    service_nvidia_api_base: str = os.getenv("SERVICE_NVIDIA_API_BASE", os.getenv("NVIDIA_API_BASE", "https://integrate.api.nvidia.com/v1"))
    nvidia_api_key: str = os.getenv("NVIDIA_API_KEY", "")
    service_nvidia_api_key: str = os.getenv("SERVICE_NVIDIA_API_KEY", os.getenv("NVIDIA_API_KEY", ""))
    llm_provider: str = os.getenv("LLM_PROVIDER", "nvidia")
    gemini_api_base: str = os.getenv(
        "GEMINI_API_BASE",
        "https://generativelanguage.googleapis.com/v1beta/openai",
    )
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_models: tuple[str, ...] = env_list(
        "GEMINI_MODELS",
        "gemini-3.6-flash,gemini-3.5-flash",
    )
    gemini_model_catalog_live: bool = env_bool("GEMINI_MODEL_CATALOG_LIVE", True)
    openrouter_api_base: str = os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    # Kept separate so party-scoped BYOK can never become the credential of
    # the stack-wide service model.
    service_openrouter_api_key: str = os.getenv("SERVICE_OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_models: tuple[str, ...] = env_list("OPENROUTER_MODELS", "openrouter/auto,openrouter/free")
    openrouter_fallback_models: tuple[str, ...] = env_list("OPENROUTER_FALLBACK_MODELS", "openrouter/auto")
    openrouter_model_catalog_live: bool = env_bool("OPENROUTER_MODEL_CATALOG_LIVE", True)
    local_llm_enabled: bool = env_bool("LOCAL_LLM_ENABLED", False)
    local_llm_base_url: str = os.getenv("LOCAL_LLM_BASE_URL", "http://rp-local-llm:8080/v1")
    local_llm_model_alias: str = os.getenv("LOCAL_LLM_MODEL_ALIAS", "gemma-4-26b-a4b-it-rp-q4")
    local_llm_context_tokens: int = env_int("LOCAL_LLM_CONTEXT_TOKENS", 32_768)
    local_llm_timeout_seconds: float = float(os.getenv("LOCAL_LLM_TIMEOUT_SECONDS", "240"))
    # "Service model" (служебная модель) is the global LLM used by background
    # memory, world-edit drafting, and character generation. It never narrates turns.
    service_model_choice: str = os.getenv("SERVICE_MODEL_CHOICE", "local-gemma")
    service_fallback_model: str = os.getenv("SERVICE_FALLBACK_MODEL", os.getenv("NARRATIVE_MODEL", "z-ai/glm-5.2"))
    provider_model_catalog_ttl_seconds: int = env_int("PROVIDER_MODEL_CATALOG_TTL_SECONDS", 86400)
    nvidia_model_catalog_live: bool = env_bool("NVIDIA_MODEL_CATALOG_LIVE", True)
    nvidia_model_catalog_url: str = os.getenv("NVIDIA_MODEL_CATALOG_URL", "https://build.nvidia.com/models?q=llm")
    nvidia_model_catalog_ttl_seconds: int = env_int("NVIDIA_MODEL_CATALOG_TTL_SECONDS", 86400)
    narrative_model: str = os.getenv("NARRATIVE_MODEL", os.getenv("NVIDIA_MODEL", "z-ai/glm-5.2"))
    intent_model: str = os.getenv("INTENT_MODEL", os.getenv("NVIDIA_MODEL", "z-ai/glm-5.2"))
    validator_model: str = os.getenv("VALIDATOR_MODEL", os.getenv("NVIDIA_MODEL", "z-ai/glm-5.2"))
    nvidia_fallback_models: tuple[str, ...] = env_list(
        "NVIDIA_FALLBACK_MODELS",
        "deepseek-ai/deepseek-v4-pro,deepseek-ai/deepseek-v4-flash,qwen/qwen3.5-397b-a17b",
    )
    nvidia_disabled_models: tuple[str, ...] = env_list("NVIDIA_DISABLED_MODELS", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_prompts: bool = env_bool("LOG_PROMPTS", False)
    max_repair_attempts: int = env_int("MAX_REPAIR_ATTEMPTS", 1)
    training_repair_attempts: int = env_int("TRAINING_REPAIR_ATTEMPTS", 1)
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "900"))
    model_attempt_timeout_seconds: float = float(os.getenv("MODEL_ATTEMPT_TIMEOUT_SECONDS", "150"))
    party_start_model_attempt_timeout_seconds: float = float(
        os.getenv("PARTY_START_MODEL_ATTEMPT_TIMEOUT_SECONDS", "300")
    )
    rate_limit_retry_attempts: int = env_int("RATE_LIMIT_RETRY_ATTEMPTS", 1)
    rate_limit_retry_default_wait_seconds: float = float(os.getenv("RATE_LIMIT_RETRY_DEFAULT_WAIT_SECONDS", "1"))
    rate_limit_retry_max_wait_seconds: float = float(os.getenv("RATE_LIMIT_RETRY_MAX_WAIT_SECONDS", "10"))
    post_turn_helpers_inline: bool = env_bool("POST_TURN_HELPERS_INLINE", False)
    party_context_max_tokens: int = env_int("PARTY_CONTEXT_MAX_TOKENS", 131_072)
    party_context_limit_tokens: int = env_int("PARTY_CONTEXT_LIMIT_TOKENS", 0)
    party_context_completion_reserve_tokens: int = env_int("PARTY_CONTEXT_COMPLETION_RESERVE_TOKENS", 16_384)
    party_context_system_reserve_tokens: int = env_int("PARTY_CONTEXT_SYSTEM_RESERVE_TOKENS", 32_768)
    party_context_min_history_tokens: int = env_int("PARTY_CONTEXT_MIN_HISTORY_TOKENS", 8_192)
    memory_summary_batch_tokens: int = env_int("MEMORY_SUMMARY_BATCH_TOKENS", 10_000)
    party_memory_chapter_max_tokens: int = env_int("PARTY_MEMORY_CHAPTER_MAX_TOKENS", 6_000)
    party_memory_chapter_max_chars: int = env_int("PARTY_MEMORY_CHAPTER_MAX_CHARS", 24_000)
    party_memory_prompt_max_chars: int = env_int("PARTY_MEMORY_PROMPT_MAX_CHARS", 60_000)
    party_memory_retrieval_enabled: bool = env_bool("PARTY_MEMORY_RETRIEVAL_ENABLED", True)
    party_memory_retrieval_limit: int = env_int("PARTY_MEMORY_RETRIEVAL_LIMIT", 3)
    party_memory_retrieval_max_chars: int = env_int("PARTY_MEMORY_RETRIEVAL_MAX_CHARS", 9_000)
    party_memory_fallback_max_chars: int = env_int("PARTY_MEMORY_FALLBACK_MAX_CHARS", 24_000)
    rp_story_memory_update_turns: int = env_int("RP_STORY_MEMORY_UPDATE_TURNS", 4)
    rp_story_memory_batch_tokens: int = env_int("RP_STORY_MEMORY_BATCH_TOKENS", 6_000)
    rp_story_memory_max_tokens: int = env_int("RP_STORY_MEMORY_MAX_TOKENS", 6_000)
    rp_story_memory_max_chars: int = env_int("RP_STORY_MEMORY_MAX_CHARS", 24_000)
    rp_story_memory_prompt_max_chars: int = env_int("RP_STORY_MEMORY_PROMPT_MAX_CHARS", 24_000)
    rp_story_memory_reserve_tokens: int = env_int("RP_STORY_MEMORY_RESERVE_TOKENS", 10_000)
    party_lore_card_prompt_limit: int = env_int("PARTY_LORE_CARD_PROMPT_LIMIT", 8)
    party_lore_card_prompt_max_chars: int = env_int("PARTY_LORE_CARD_PROMPT_MAX_CHARS", 12_000)
    service_job_max_attempts: int = env_int("SERVICE_JOB_MAX_ATTEMPTS", 5)
    service_job_retry_base_seconds: int = env_int("SERVICE_JOB_RETRY_BASE_SECONDS", 5)
    service_job_retry_max_seconds: int = env_int("SERVICE_JOB_RETRY_MAX_SECONDS", 300)
    openrouter_prompt_cache_enabled: bool = env_bool("OPENROUTER_PROMPT_CACHE_ENABLED", True)
    openrouter_prompt_cache_ttl: str = os.getenv("OPENROUTER_PROMPT_CACHE_TTL", "5m")
    prompt_cache_session_id: str = os.getenv("PROMPT_CACHE_SESSION_ID", "")
    auth_enabled: bool = env_bool("GATEWAY_AUTH_ENABLED", True)
    auth_session_cookie_name: str = os.getenv("GATEWAY_SESSION_COOKIE_NAME", "rp_gateway_session")
    auth_session_ttl_seconds: int = env_int("GATEWAY_SESSION_TTL_SECONDS", 60 * 60 * 24 * 14)
    auth_cookie_secure: bool = env_bool("GATEWAY_COOKIE_SECURE", False)
    bootstrap_admin_username: str = os.getenv("GATEWAY_BOOTSTRAP_ADMIN_USERNAME", "admin")
    bootstrap_admin_password: str = os.getenv("GATEWAY_BOOTSTRAP_ADMIN_PASSWORD", "")
    showroom_visitor_cookie_name: str = os.getenv("SHOWROOM_VISITOR_COOKIE_NAME", "rp_showroom_visitor")
    showroom_visitor_ttl_seconds: int = env_int("SHOWROOM_VISITOR_TTL_SECONDS", 60 * 60 * 24 * 30)
    showroom_cover_dir: str = os.getenv("SHOWROOM_COVER_DIR", "/data/showroom-covers")
    showroom_cover_max_bytes: int = env_int("SHOWROOM_COVER_MAX_BYTES", 5 * 1024 * 1024)

    @property
    def sqlite_path(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// DATABASE_URL values are supported in this MVP")
        return self.database_url[len(prefix) :]

    @property
    def effective_party_context_limit_tokens(self) -> int:
        configured = self.party_context_limit_tokens or self.party_context_max_tokens
        return max(configured, self.party_context_min_history_tokens)

    @property
    def effective_party_history_token_budget(self) -> int:
        story_memory_reserve = self.rp_story_memory_reserve_tokens if self.scenario_type == "rp" else 0
        available = (
            self.effective_party_context_limit_tokens
            - self.party_context_completion_reserve_tokens
            - self.party_context_system_reserve_tokens
            - story_memory_reserve
        )
        return max(available, self.party_context_min_history_tokens)


def get_settings() -> Settings:
    return Settings()
