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
    database_url: str = os.getenv("DATABASE_URL", "sqlite:////data/rp_gateway.db")
    world_state_path: str = os.getenv("WORLD_STATE_PATH", "/state/current.json")
    party_state_root: str = os.getenv("PARTY_STATE_ROOT", "/state/parties")
    state_schema_path: str = os.getenv("STATE_SCHEMA_PATH", "/state/schema.json")
    worldpacks_path: str = os.getenv("WORLD_PACKS_PATH", "/worldpacks")
    nvidia_api_base: str = os.getenv("NVIDIA_API_BASE", "https://integrate.api.nvidia.com/v1")
    nvidia_api_key: str = os.getenv("NVIDIA_API_KEY", "")
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
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "900"))
    model_attempt_timeout_seconds: float = float(os.getenv("MODEL_ATTEMPT_TIMEOUT_SECONDS", "45"))
    post_turn_helpers_inline: bool = env_bool("POST_TURN_HELPERS_INLINE", False)
    party_raw_turn_limit: int = env_int("PARTY_RAW_TURN_LIMIT", 96)
    narrative_history_message_limit: int = env_int("NARRATIVE_HISTORY_MESSAGE_LIMIT", 0)
    memory_auto_min_unsummarized_turns: int = env_int("MEMORY_AUTO_MIN_UNSUMMARIZED_TURNS", 48)
    memory_max_batch_turns: int = env_int("MEMORY_MAX_BATCH_TURNS", 96)
    journal_auto_min_unsummarized_turns: int = env_int("JOURNAL_AUTO_MIN_UNSUMMARIZED_TURNS", 24)
    journal_max_batch_turns: int = env_int("JOURNAL_MAX_BATCH_TURNS", 48)
    auth_enabled: bool = env_bool("GATEWAY_AUTH_ENABLED", True)
    auth_session_cookie_name: str = os.getenv("GATEWAY_SESSION_COOKIE_NAME", "rp_gateway_session")
    auth_session_ttl_seconds: int = env_int("GATEWAY_SESSION_TTL_SECONDS", 60 * 60 * 24 * 14)
    auth_cookie_secure: bool = env_bool("GATEWAY_COOKIE_SECURE", False)
    bootstrap_admin_username: str = os.getenv("GATEWAY_BOOTSTRAP_ADMIN_USERNAME", "admin")
    bootstrap_admin_password: str = os.getenv("GATEWAY_BOOTSTRAP_ADMIN_PASSWORD", "")

    @property
    def sqlite_path(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// DATABASE_URL values are supported in this MVP")
        return self.database_url[len(prefix) :]

    @property
    def effective_narrative_history_message_limit(self) -> int:
        if self.narrative_history_message_limit > 0:
            return self.narrative_history_message_limit
        return max((self.party_raw_turn_limit * 2) + 1, 1)


def get_settings() -> Settings:
    return Settings()
