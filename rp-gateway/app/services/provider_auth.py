"""Provider authentication policy for outbound Gateway requests."""

from __future__ import annotations

from typing import Any

from app.services.nvidia_catalog import normalize_provider


def outbound_headers(settings: Any, inbound_authorization: str | None) -> dict[str, str]:
    """Return OpenAI-compatible request headers without forwarding browser auth to local LLM."""
    provider = normalize_provider(str(settings.llm_provider))
    headers = {"Content-Type": "application/json"}
    if provider == "local":
        return headers
    authorization = f"Bearer {settings.nvidia_api_key}" if settings.nvidia_api_key else inbound_authorization
    if not authorization:
        raise PermissionError(f"API key is required for provider {settings.llm_provider}")
    headers["Authorization"] = authorization
    return headers
