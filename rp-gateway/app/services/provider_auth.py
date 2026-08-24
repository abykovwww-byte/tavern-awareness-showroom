"""Provider authentication policy for outbound Gateway requests."""

from __future__ import annotations

from app.services.provider_catalog import normalize_provider


def outbound_headers(
    provider: str,
    api_key: str | None,
    inbound_authorization: str | None,
) -> dict[str, str]:
    """Return OpenAI-compatible request headers without forwarding browser auth to local LLM."""
    provider = normalize_provider(provider)
    if provider not in {"local", "gemini", "openrouter"}:
        raise ValueError(f"provider is retired or unsupported: {provider}")
    headers = {"Content-Type": "application/json"}
    if provider == "local":
        return headers
    authorization = f"Bearer {api_key}" if api_key else inbound_authorization
    if not authorization:
        raise PermissionError(f"API key is required for provider {provider}")
    headers["Authorization"] = authorization
    return headers
