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
        "deepseek-ai/deepseek-v4-pro,deepseek-ai/deepseek-v4-flash",
    )
    nvidia_disabled_models: tuple[str, ...] = env_list("NVIDIA_DISABLED_MODELS", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_prompts: bool = env_bool("LOG_PROMPTS", False)
    max_repair_attempts: int = env_int("MAX_REPAIR_ATTEMPTS", 1)
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))
    model_attempt_timeout_seconds: float = float(os.getenv("MODEL_ATTEMPT_TIMEOUT_SECONDS", "45"))

    @property
    def sqlite_path(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// DATABASE_URL values are supported in this MVP")
        return self.database_url[len(prefix) :]


def get_settings() -> Settings:
    return Settings()
