"""Narrative LLM client and OpenAI-compatible response helpers."""

from __future__ import annotations

import asyncio
import copy
import logging
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, Outcome, StructuredNarrativeBundle
from app.services.context_budget import estimate_tokens
from app.services.provider_catalog import normalize_provider
from app.services.provider_auth import outbound_headers
from app.services.trace_redaction import redact_trace_value


logger = logging.getLogger(__name__)

FORBIDDEN_STRUCTURED_MARKUP_RE = re.compile(
    r"[<>]|(?:javascript|data|vbscript):|`|\*\*|__|(?m:^\s*(?:#{1,6}\s|[-*+]\s|>\s))",
    re.IGNORECASE,
)
VISIBLE_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)


def _canonical_provider_line_breaks(value: str) -> str:
    return value.replace("\r\n", "\n").translate(
        {
            ord("\r"): "\n",
            ord("\u0085"): "\n",
            ord("\u2028"): "\n",
            ord("\u2029"): "\n",
        }
    )


class PromptBudgetExceeded(RuntimeError):
    """The required training prompt cannot fit the provider input window."""

    def __init__(self, *, estimated_tokens: int, token_budget: int):
        self.estimated_tokens = estimated_tokens
        self.token_budget = token_budget
        super().__init__("Required training context exceeds the provider input budget")


























def training_turn_prompt_block(contract: dict[str, Any]) -> str:
    output_rules = [
        "Return only the requested provider response: no analysis, preamble, commentary, or Markdown fences.",
        "For an active turn, follow the supplied JSON schema: put only optional scene-setting prose in narrative_text and put each visible field value in the exact visible_surfaces object. Gateway renders markers, labels, the authored header, and the final question.",
        "The current ACTIVE_TRAINING_TURN_CONTRACT has priority over every earlier turn and message.",
        "Every must_include item is mandatory. Preserve complete authored sender names; never shorten, replace, or generalize them.",
    ]
    format_rules: list[str] = []
    surfaces = contract.get("surfaces") if contract.get("kind") == "turn" else None
    if isinstance(surfaces, list) and surfaces:
        marker_counts: dict[str, int] = {}
        for surface in surfaces:
            if not isinstance(surface, dict):
                continue
            marker = "ПИСЬМО" if surface.get("type") == "email" else "СООБЩЕНИЕ"
            marker_counts[marker] = marker_counts.get(marker, 0) + max(
                int(surface.get("count", 1) or 1), 1
            )
        format_rules.extend(
            [
                "STRUCTURED_VISIBLE_SURFACES_FORMAT",
                "Do not put ПИСЬМО/СООБЩЕНИЕ marker lines, field labels, the authored header, or the final question in narrative_text; Gateway adds them after decoding.",
                "Fill every required field value in visible_surfaces. Do not repeat the field label inside its value. Write sender identities as 'Имя — login@domain' or 'Имя — handle', never inside angle brackets.",
                "Do not use Markdown, HTML, angle brackets, or Markdown-formatted links in narrative_text or any field value.",
                "Required surface marker counts (JSON instruction data, not visible output): "
                + json.dumps(marker_counts, ensure_ascii=False, separators=(",", ":"))
                + ". The response schema provides one exact visible_surfaces key for every required block.",
            ]
        )
        for surface in surfaces:
            if not isinstance(surface, dict):
                continue
            marker = "ПИСЬМО" if surface.get("type") == "email" else "СООБЩЕНИЕ"
            count = max(int(surface.get("count", 1) or 1), 1)
            fields = [str(field) for field in surface.get("required_fields", [])]
            format_rules.append(
                f"Fill exactly {count} {marker} surface value object(s) using the exact visible_surfaces keys assigned by the response schema."
            )
            if fields:
                format_rules.append(
                    f"Every {marker} value object must contain exactly these JSON keys; put only field values inside them: {' | '.join(fields)}"
                )
            links_policy = str(surface.get("links", "none"))
            effective_links = surface.get("effective_links")
            if links_policy == "none" or (
                links_policy == "artifact"
                and isinstance(effective_links, dict)
                and not effective_links.get("enabled")
            ):
                format_rules.append(
                    f"Set the 'Ссылки:' JSON value to exact 'нет' in every {marker} object and put no URL in other values."
                )
            elif links_policy == "artifact" and isinstance(effective_links, dict):
                display_url = str(effective_links.get("display_url") or "").strip()
                if display_url:
                    format_rules.append(
                        f"Set the first {marker} object's 'Ссылки:' JSON value to exact {display_url}; set it to 'нет' in every other {marker} object and never repeat the URL in other values."
                    )
    return "\n".join(
        [
            "ACTIVE_TRAINING_TURN_CONTRACT",
            "This machine-readable WorldPack contract is authoritative for the current visible turn only.",
            "Generate fresh natural wording with the LLM, but do not change its turn, sender, channel, required facts, attachment, URL policy, or player-role boundary.",
            "Do not infer a different event from prior history and never expose hidden assessment rules.",
            *output_rules,
            *format_rules,
            json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
        ]
    )


def training_artifact_prompt_block(
    contract: dict[str, Any],
    training_turn_contract: dict[str, Any] | None = None,
) -> str:
    site = contract.get("site") if "site" in contract or "workspace" in contract else contract
    workspace = contract.get("workspace") if "site" in contract or "workspace" in contract else None
    lines = ["TRAINING_INTERACTION_CONTRACT"]
    structured_turn = bool(training_turn_contract and training_turn_contract.get("kind") == "turn")
    if structured_turn:
        lines.extend(
            [
                "Return exactly one JSON object with schema_version rp-gateway.narrative-bundle.v3, narrative_text, visible_surfaces, artifacts, and workspace_files.",
                "Use narrative_text only for optional scene-setting prose. Fill the exact visible_surfaces keys and fields required by the response schema; Gateway renders the visible blocks.",
                "For workspace_files emit exactly supplied file_key and blueprint_id values and fill only declared string slots.",
            ]
        )
    elif workspace:
        lines.extend(
            [
                "Return exactly one JSON object with schema_version rp-gateway.narrative-bundle.v2, narrative_text, artifacts, and workspace_files.",
                "For workspace_files emit exactly supplied file_key and blueprint_id values and fill only declared string slots.",
            ]
        )
    else:
        lines.append("Return exactly one JSON object with schema_version rp-gateway.narrative-bundle.v1, narrative_text, and artifacts.")
    if site:
        lines.append("Emit exactly the supplied artifact_key and blueprint_id and fill only the declared string slots.")
        if structured_turn:
            lines.append(
                "The fixed display_url is Gateway-owned. Use only the exact value allowed by the response schema and never copy it into narrative_text, body text, or artifact slots."
            )
        else:
            lines.append("Put the exact fixed display_url only in the visible narrative_text field line 'Ссылки:'.")
        lines.append("Do not emit display_url or any other fixed URL field inside an artifact object.")
    if not structured_turn:
        lines.extend(
            [
                "Put the complete visible surface body inside narrative_text; Gateway applies the exact authored header and final question.",
                "After JSON decoding, narrative_text must obey VISIBLE_PLAIN_TEXT_FORMAT: preserve the required line breaks and literal undecorated ПИСЬМО/СООБЩЕНИЕ marker lines.",
            ]
        )
    lines.extend(
        [
            "Inside narrative_text and generated slots do not use Markdown, HTML, angle brackets, or Markdown-formatted links. Write sender identities after an em dash.",
            "Do not put any text before or after the JSON object. Do not wrap it in a Markdown code fence.",
            "Never emit HTML, CSS, JavaScript, remote assets, credentials, paths, MIME types, file classification, answer keys, scoring, correctness, or remediation.",
            json.dumps({"site": site, "workspace": workspace}, ensure_ascii=False, separators=(",", ":")),
        ]
    )
    return "\n".join(lines)


def _expanded_training_surfaces(
    contract: dict[str, Any] | None,
) -> list[tuple[str, dict[str, Any], int]]:
    if not contract or contract.get("kind") != "turn":
        return []
    expanded: list[tuple[str, dict[str, Any], int]] = []
    for surface_index, surface in enumerate(contract.get("surfaces") or [], start=1):
        if not isinstance(surface, dict):
            continue
        count = max(int(surface.get("count", 1) or 1), 1)
        for instance_index in range(1, count + 1):
            expanded.append((f"surface_{surface_index}_{instance_index}", surface, instance_index))
    return expanded


def _surface_link_value(surface: dict[str, Any], instance_index: int) -> str:
    effective = surface.get("effective_links")
    if (
        surface.get("links") == "artifact"
        and isinstance(effective, dict)
        and effective.get("enabled")
        and instance_index == 1
    ):
        return str(effective.get("display_url") or "").strip()
    return "нет"


def training_interaction_response_format(
    contract: dict[str, Any],
    training_turn_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    combined = "site" in contract or "workspace" in contract
    site = contract.get("site") if combined else contract
    workspace = contract.get("workspace") if combined else None
    structured_surfaces = _expanded_training_surfaces(training_turn_contract)

    def slots_schema(slots: dict[str, Any]) -> dict[str, Any]:
        properties = {
            str(slot_id): {
                "type": "string",
                "minLength": 1 if slot_contract.get("required", True) else 0,
                "maxLength": int(slot_contract["max_length"]),
            }
            for slot_id, slot_contract in slots.items()
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        }

    def content_schema(item: dict[str, Any], key_name: str) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [key_name, "blueprint_id", "slots"],
            "properties": {
                key_name: {"type": "string", "enum": [str(item[key_name])]},
                "blueprint_id": {"type": "string", "enum": [str(item["blueprint_id"])]},
                "slots": slots_schema(item.get("slots") or {}),
            },
        }

    artifact_count = 1 if site else 0
    artifact_item = (
        content_schema(site, "artifact_key")
        if site
        else {
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {},
        }
    )
    properties: dict[str, Any] = {
        "schema_version": {
            "type": "string",
            "enum": [
                "rp-gateway.narrative-bundle.v3"
                if structured_surfaces
                else (
                    "rp-gateway.narrative-bundle.v2"
                    if workspace
                    else "rp-gateway.narrative-bundle.v1"
                )
            ],
        },
        "narrative_text": {
            "type": "string",
            "description": (
                "Optional scene-setting prose only when visible_surfaces is present; no URLs, markup, "
                "surface markers, field labels, authored header, or final question. Otherwise the complete visible text."
            ),
            "minLength": 0 if structured_surfaces else 1,
            "maxLength": 30000,
        },
        "artifacts": {
            "type": "array",
            "minItems": artifact_count,
            "maxItems": artifact_count,
            "items": artifact_item,
        },
    }
    required = ["schema_version", "narrative_text", "artifacts"]
    if structured_surfaces:
        visible_properties: dict[str, Any] = {}
        for surface_key, surface, instance_index in structured_surfaces:
            field_properties: dict[str, Any] = {}
            field_names = [str(field).strip() for field in surface.get("required_fields") or []]
            for field_name in field_names:
                value_schema: dict[str, Any] = {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 6000,
                }
                if field_name == "Ссылки:":
                    value_schema["enum"] = [_surface_link_value(surface, instance_index)]
                field_properties[field_name] = value_schema
            visible_properties[surface_key] = {
                "type": "object",
                "additionalProperties": False,
                "required": field_names,
                "properties": field_properties,
            }
        properties["visible_surfaces"] = {
            "type": "object",
            "additionalProperties": False,
            "required": list(visible_properties),
            "properties": visible_properties,
        }
        required.append("visible_surfaces")

    if workspace or structured_surfaces:
        files = list((workspace or {}).get("files") or [])
        file_schemas = [content_schema(item, "file_key") for item in files]
        properties["workspace_files"] = {
            "type": "array",
            "minItems": len(files),
            "maxItems": len(files),
            "items": (
                file_schemas[0]
                if len(file_schemas) == 1
                else (
                    {"anyOf": file_schemas}
                    if file_schemas
                    else {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [],
                        "properties": {},
                    }
                )
            ),
        }
        required.append("workspace_files")
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "training_narrative_bundle",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": properties,
            },
        },
    }


@dataclass(frozen=True)
class StructuredNarrativeMaterialization:
    response: dict[str, Any]
    text: str
    valid: bool
    violations: list[str]


def materialize_structured_training_response(
    response: dict[str, Any],
    training_turn_contract: dict[str, Any] | None,
    interaction_contract: dict[str, Any] | None,
) -> StructuredNarrativeMaterialization:
    """Render a provider-only v3 bundle while preserving the legacy public response."""

    raw_text = response_text(response)
    expected = _expanded_training_surfaces(training_turn_contract)
    if not expected:
        return StructuredNarrativeMaterialization(response, raw_text, True, [])
    try:
        decoded = json.loads(json_object_content(raw_text))
    except (TypeError, ValueError, json.JSONDecodeError):
        return StructuredNarrativeMaterialization(
            response,
            raw_text,
            False,
            ["active training turn requires a structured narrative bundle v3"],
        )
    if not isinstance(decoded, dict) or decoded.get("schema_version") != "rp-gateway.narrative-bundle.v3":
        return StructuredNarrativeMaterialization(
            response,
            raw_text,
            False,
            ["active training turn requires schema_version rp-gateway.narrative-bundle.v3"],
        )
    try:
        bundle = StructuredNarrativeBundle.model_validate(decoded)
    except ValidationError as exc:
        return StructuredNarrativeMaterialization(
            response,
            raw_text,
            False,
            [f"invalid structured narrative bundle: {str(exc)[:500]}"],
        )

    violations: list[str] = []
    expected_keys = [item[0] for item in expected]
    if set(bundle.visible_surfaces) != set(expected_keys):
        violations.append("visible_surfaces do not exactly match the active training turn")
    rendered_blocks: list[str] = []
    multiline_fields = {"Тело:", "Текст:", "Подпись:"}
    marker_pattern = re.compile(r"(?m)^(ПИСЬМО|СООБЩЕНИЕ)\s*$")
    lead = _canonical_provider_line_breaks(bundle.narrative_text).strip()
    known_fields = {
        str(field).strip()
        for _, surface, _ in expected
        for field in surface.get("required_fields") or []
    }
    field_line_pattern = re.compile(
        r"(?m)^\s*(?:" + "|".join(re.escape(field) for field in sorted(known_fields)) + r")",
        re.IGNORECASE,
    )
    if marker_pattern.search(lead):
        violations.append("narrative_text must not contain visible surface markers")
    if VISIBLE_URL_RE.search(lead):
        violations.append("narrative_text must not contain a URL")
    if FORBIDDEN_STRUCTURED_MARKUP_RE.search(lead):
        violations.append("narrative_text must not contain markup")
    if field_line_pattern.search(lead):
        violations.append("narrative_text must not contain visible field labels")
    for boundary_key in ("header", "question"):
        boundary = str((training_turn_contract or {}).get(boundary_key) or "").strip()
        if boundary and boundary in lead:
            violations.append(f"narrative_text must not contain the authored {boundary_key}")
    for surface_key, surface, instance_index in expected:
        supplied_fields = bundle.visible_surfaces.get(surface_key)
        if supplied_fields is None:
            continue
        expected_fields = [str(field).strip() for field in surface.get("required_fields") or []]
        if set(supplied_fields) != set(expected_fields):
            violations.append(f"{surface_key} fields do not exactly match the active training turn")
            continue
        lines = ["ПИСЬМО" if surface.get("type") == "email" else "СООБЩЕНИЕ"]
        for field_name in expected_fields:
            value = _canonical_provider_line_breaks(supplied_fields[field_name]).strip()
            if not value:
                violations.append(f"{surface_key} has an empty field: {field_name}")
                continue
            if field_name not in multiline_fields and ("\n" in value or "\r" in value):
                violations.append(f"{surface_key} has a multiline single-line field: {field_name}")
            if marker_pattern.search(value):
                violations.append(f"{surface_key} contains an injected visible surface marker")
            if FORBIDDEN_STRUCTURED_MARKUP_RE.search(value):
                violations.append(f"{surface_key} contains forbidden markup")
            if field_line_pattern.search(value):
                violations.append(f"{surface_key} contains an injected visible field label")
            if field_name == "Ссылки:" and value != _surface_link_value(surface, instance_index):
                violations.append(f"{surface_key} has a non-authoritative link value")
            lines.append(field_name if field_name in multiline_fields else f"{field_name} {value}")
            if field_name in multiline_fields:
                lines.append(value)
        rendered_blocks.append("\n".join(lines))

    combined = "site" in (interaction_contract or {}) or "workspace" in (interaction_contract or {})
    site = (interaction_contract or {}).get("site") if combined else interaction_contract
    workspace = (interaction_contract or {}).get("workspace") if combined else None
    if not site and bundle.artifacts:
        violations.append("structured narrative bundle contains undeclared artifacts")
    if not workspace and bundle.workspace_files:
        violations.append("structured narrative bundle contains undeclared workspace_files")
    if violations:
        return StructuredNarrativeMaterialization(response, raw_text, False, violations)

    parts = [lead, *rendered_blocks]
    rendered_text = "\n\n".join(part for part in parts if part)
    if interaction_contract:
        legacy_bundle: dict[str, Any] = {
            "schema_version": (
                "rp-gateway.narrative-bundle.v2"
                if workspace
                else "rp-gateway.narrative-bundle.v1"
            ),
            "narrative_text": rendered_text,
            "artifacts": [item.model_dump(mode="json") for item in bundle.artifacts],
        }
        if workspace:
            legacy_bundle["workspace_files"] = [
                item.model_dump(mode="json") for item in bundle.workspace_files
            ]
        bridged = with_text(response, json.dumps(legacy_bundle, ensure_ascii=False))
        return StructuredNarrativeMaterialization(bridged, rendered_text, True, [])
    rendered = with_text(response, rendered_text)
    return StructuredNarrativeMaterialization(rendered, rendered_text, True, [])


class ProviderRateLimitError(RuntimeError):
    def __init__(
        self,
        provider: str,
        model: str,
        retry_after_seconds: float | None,
        error_type: str | None,
        provider_code: str | None,
        response_message: str | None,
    ):
        self.details = {
            "provider": provider,
            "model": model,
            "status": 429,
            "retry_after_seconds": retry_after_seconds,
            "error_type": error_type,
            "provider_code": provider_code,
            "response_message": response_message,
        }
        retry_hint = f" Retry after {retry_after_seconds:g}s." if retry_after_seconds else ""
        super().__init__(f"{provider} API returned 429 rate limit for {model}.{retry_hint}")

    def public_detail(self) -> dict[str, Any]:
        return {
            "code": "provider_rate_limited",
            "message": "The selected model is temporarily rate limited.",
            "provider": self.details["provider"],
            "model": self.details["model"],
            "retry_after_seconds": self.details["retry_after_seconds"],
            "error_type": self.details["error_type"],
        }


class NarrativeClient:
    def __init__(
        self,
        settings: Settings,
        trace_recorder: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.settings = settings
        self.trace_recorder = trace_recorder

    async def complete(
        self,
        request: ChatCompletionRequest,
        state: dict[str, Any],
        outcome: Outcome,
        inbound_authorization: str | None,
        repair_instruction: str | None = None,
        failed_response_text: str | None = None,
        memory_summary: dict[str, Any] | list[dict[str, Any]] | None = None,
        request_id: str | None = None,
        artifact_contract: dict[str, Any] | None = None,
        training_turn_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.settings.scenario_type != "training":
            raise ValueError("training gateway accepts only scenario_type=training")
        headers = outbound_headers(
            self.settings.llm_provider,
            self.settings.llm_api_key,
            inbound_authorization,
        )
        payload = request.model_dump(exclude_none=True)
        if repair_instruction:
            payload["messages"] = self.repair_messages(
                state,
                outcome,
                repair_instruction,
                failed_response_text or "",
                artifact_contract=artifact_contract,
                training_turn_contract=training_turn_contract,
            )
        else:
            payload["messages"] = self.narrative_messages(
                request,
                state,
                outcome,
                repair_instruction=None,
                memory_summary=memory_summary,
                artifact_contract=artifact_contract,
                training_turn_contract=training_turn_contract,
            )
        if training_turn_contract and training_turn_contract.get("kind") == "turn":
            payload["response_format"] = training_interaction_response_format(
                artifact_contract or {},
                training_turn_contract,
            )
        elif artifact_contract:
            payload["response_format"] = training_interaction_response_format(artifact_contract)
        response_format_enabled = "response_format" in payload
        self.apply_prompt_cache_policy(payload)
        payload["stream"] = False
        narrator_settings_model = (request._narrator_settings_model or "").strip().lower()

        if self.settings.llm_api_base.startswith("mock://"):
            attempt_payload = copy.deepcopy(payload)
            attempt_payload["model"] = self.settings.narrative_model
            uses_narrator_settings = narrator_settings_model == self.settings.narrative_model.strip().lower()
            if narrator_settings_model and not uses_narrator_settings:
                for key in ("reasoning", "temperature", "top_p", "max_tokens"):
                    attempt_payload.pop(key, None)
            self.apply_model_policy(
                attempt_payload,
                self.settings.narrative_model,
                require_parameters=uses_narrator_settings or response_format_enabled,
            )
            started = time.perf_counter()
            try:
                data = self.mock_completion(
                    outcome,
                    repair_instruction,
                    artifact_contract,
                    state=state,
                    training_turn_contract=training_turn_contract,
                )
            except Exception as exc:
                self.record_trace_attempt(
                    request_id=request_id,
                    payload=attempt_payload,
                    model=self.settings.narrative_model,
                    attempt_index=1,
                    repair_instruction=repair_instruction,
                    status="failed",
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                    error=exc,
                )
                raise
            self.record_trace_attempt(
                request_id=request_id,
                payload=attempt_payload,
                model=self.settings.narrative_model,
                attempt_index=1,
                repair_instruction=repair_instruction,
                status="completed",
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                raw_response=json.dumps(data, ensure_ascii=False),
                usage=data.get("usage") if isinstance(data, dict) else None,
                http_status=200,
            )
            return data

        timeout = httpx.Timeout(self.settings.model_attempt_timeout_seconds, connect=15.0)
        attempts = self.model_attempts(self.settings.narrative_model)
        last_timeout: httpx.TimeoutException | None = None
        last_status: httpx.HTTPStatusError | None = None
        last_request_error: httpx.RequestError | None = None
        rate_limit_retries = 0
        trace_attempt_index = 0
        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, model in enumerate(attempts):
                attempt_payload = copy.deepcopy(payload)
                attempt_payload["model"] = model
                uses_narrator_settings = narrator_settings_model == model.strip().lower()
                if narrator_settings_model and not uses_narrator_settings:
                    for key in ("reasoning", "temperature", "top_p", "max_tokens"):
                        attempt_payload.pop(key, None)
                self.apply_model_policy(
                    attempt_payload,
                    model,
                    require_parameters=uses_narrator_settings or response_format_enabled,
                )
                empty_response_retry_used = False
                while True:
                    trace_attempt_index += 1
                    started = time.perf_counter()
                    logger.info(
                        "llm_attempt_start request_id=%s check_id=%s model=%s attempt=%s/%s timeout_seconds=%s repair=%s",
                        request_id,
                        outcome.check_id,
                        model,
                        index + 1,
                        len(attempts),
                        self.settings.model_attempt_timeout_seconds,
                        bool(repair_instruction),
                    )
                    try:
                        async with asyncio.timeout(self.settings.model_attempt_timeout_seconds):
                            response = await client.post(
                                f"{self.settings.llm_api_base.rstrip('/')}/chat/completions",
                                json=attempt_payload,
                                headers=headers,
                            )
                    except (httpx.TimeoutException, TimeoutError) as exc:
                        timeout_error = exc
                        if not isinstance(exc, httpx.TimeoutException):
                            timeout_error = httpx.TimeoutException(
                                "Narrative provider exceeded the wall-clock deadline",
                                request=httpx.Request(
                                    "POST",
                                    f"{self.settings.llm_api_base.rstrip('/')}/chat/completions",
                                ),
                            )
                        last_timeout = timeout_error
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                        logger.warning(
                            "llm_attempt_timeout request_id=%s check_id=%s model=%s attempt=%s/%s elapsed_ms=%s fallback=%s",
                            request_id,
                            outcome.check_id,
                            model,
                            index + 1,
                            len(attempts),
                            elapsed_ms,
                            index < len(attempts) - 1,
                        )
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            error=timeout_error,
                        )
                        if index < len(attempts) - 1:
                            break
                        raise timeout_error from exc
                    except httpx.RequestError as exc:
                        last_request_error = exc
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                        logger.warning(
                            "llm_attempt_network_error request_id=%s check_id=%s model=%s "
                            "attempt=%s/%s elapsed_ms=%s error_type=%s fallback=%s",
                            request_id,
                            outcome.check_id,
                            model,
                            index + 1,
                            len(attempts),
                            elapsed_ms,
                            type(exc).__name__,
                            index < len(attempts) - 1,
                        )
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            error=exc,
                        )
                        if index < len(attempts) - 1:
                            break
                        raise
                    if response.status_code == 429:
                        error = provider_rate_limit_error(response, self.settings.llm_provider, model)
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                        retry_delay = error.details["retry_after_seconds"] or self.settings.rate_limit_retry_default_wait_seconds
                        can_retry = (
                            rate_limit_retries < self.settings.rate_limit_retry_attempts
                            and 0 < retry_delay <= self.settings.rate_limit_retry_max_wait_seconds
                        )
                        logger.warning(
                            "llm_attempt_rate_limited request_id=%s check_id=%s model=%s elapsed_ms=%s retry_after_seconds=%s error_type=%s provider_code=%s retry=%s fallback=%s",
                            request_id,
                            outcome.check_id,
                            model,
                            elapsed_ms,
                            error.details["retry_after_seconds"],
                            error.details["error_type"],
                            error.details["provider_code"],
                            can_retry,
                            index < len(attempts) - 1,
                        )
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            raw_response=(response.text if self.trace_recorder is not None and request_id else None),
                            error=error,
                            http_status=response.status_code,
                        )
                        if can_retry:
                            rate_limit_retries += 1
                            await asyncio.sleep(retry_delay)
                            continue
                        if index < len(attempts) - 1:
                            break
                        raise error
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        last_status = exc
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                        logger.warning(
                            "llm_attempt_http_error request_id=%s check_id=%s model=%s status=%s elapsed_ms=%s fallback=%s",
                            request_id,
                            outcome.check_id,
                            model,
                            response.status_code,
                            elapsed_ms,
                            index < len(attempts) - 1,
                        )
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            raw_response=(response.text if self.trace_recorder is not None and request_id else None),
                            error=exc,
                            http_status=response.status_code,
                        )
                        if index < len(attempts) - 1 and response.status_code in {
                            400,
                            403,
                            404,
                            408,
                            410,
                            500,
                            502,
                            503,
                            504,
                        }:
                            break
                        raise
                    try:
                        data = response.json()
                        if not isinstance(data, dict):
                            raise RuntimeError("Narrative provider response must be a JSON object")
                    except Exception as exc:
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            raw_response=(response.text if self.trace_recorder is not None and request_id else None),
                            error=exc,
                            http_status=response.status_code,
                        )
                        raise
                    data.setdefault("model", model)
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    if not response_text(data).strip():
                        error = RuntimeError("Narrative provider returned an empty final response")
                        retry_same_model = not empty_response_retry_used
                        logger.warning(
                            "llm_attempt_empty_response request_id=%s check_id=%s model=%s "
                            "elapsed_ms=%s retry_same_model=%s fallback_after_retry=%s",
                            request_id,
                            outcome.check_id,
                            model,
                            elapsed_ms,
                            retry_same_model,
                            index < len(attempts) - 1,
                        )
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            raw_response=(response.text if self.trace_recorder is not None and request_id else None),
                            error=error,
                            http_status=response.status_code,
                        )
                        if retry_same_model:
                            empty_response_retry_used = True
                            continue
                        if index < len(attempts) - 1:
                            break
                        # Preserve the existing terminal empty-response audit and
                        # no-commit handling in the caller after the bounded retry.
                        return data
                    logger.info(
                        "llm_attempt_success request_id=%s check_id=%s model=%s status=%s elapsed_ms=%s fallback_used=%s",
                        request_id,
                        outcome.check_id,
                        model,
                        response.status_code,
                        elapsed_ms,
                        index > 0 or model != self.settings.narrative_model,
                    )
                    self.record_trace_attempt(
                        request_id=request_id,
                        payload=attempt_payload,
                        model=model,
                        attempt_index=trace_attempt_index,
                        repair_instruction=repair_instruction,
                        status="completed",
                        elapsed_ms=elapsed_ms,
                        raw_response=(response.text if self.trace_recorder is not None and request_id else None),
                        usage=data.get("usage") if isinstance(data, dict) else None,
                        http_status=response.status_code,
                    )
                    return data
        if last_status:
            raise last_status
        if last_timeout:
            raise last_timeout
        if last_request_error:
            raise last_request_error
        raise RuntimeError(f"No model attempts configured for provider {self.settings.llm_provider}")

    def record_trace_attempt(
        self,
        *,
        request_id: str | None,
        payload: dict[str, Any],
        model: str,
        attempt_index: int,
        repair_instruction: str | None,
        status: str,
        elapsed_ms: float,
        raw_response: str | None = None,
        usage: Any = None,
        error: Exception | None = None,
        http_status: int | None = None,
    ) -> None:
        if self.trace_recorder is None or not request_id:
            return
        event = {
            "request_id": request_id,
            "status": status,
            "provider": self.settings.llm_provider,
            "model": model,
            "attempt_index": attempt_index,
            "repair": repair_instruction is not None,
            "repair_instruction": repair_instruction,
            "latency_ms": elapsed_ms,
            "http_status": http_status,
            "usage": usage,
            "input": {"payload": payload},
            "output": {"raw_response": raw_response} if raw_response is not None else None,
            "error": (
                {"type": type(error).__name__, "message": str(error)[:1000]}
                if error is not None
                else None
            ),
        }
        safe_event = redact_trace_value(event, self.trace_secrets())
        try:
            self.trace_recorder(safe_event)
        except Exception as exc:  # noqa: BLE001 - diagnostics must never break a turn
            logger.warning(
                "turn_trace_capture_failed request_id=%s error=%s",
                request_id,
                f"{type(exc).__name__}: {exc}",
            )

    def trace_secrets(self) -> tuple[str | None, ...]:
        return (
            self.settings.llm_api_key,
            self.settings.service_openrouter_api_key,
        )

    def model_attempts(self, primary_model: str) -> list[str]:
        disabled = set(self.settings.llm_disabled_models)
        candidates = [primary_model, *self.settings.llm_fallback_models]
        attempts: list[str] = []
        for model in candidates:
            if not model or model in disabled or model in attempts:
                continue
            attempts.append(model)
        return attempts or [primary_model]

    def narrative_messages(
        self,
        request: ChatCompletionRequest,
        state: dict[str, Any],
        outcome: Outcome,
        repair_instruction: str | None,
        memory_summary: dict[str, Any] | list[dict[str, Any]] | None = None,
        artifact_contract: dict[str, Any] | None = None,
        training_turn_contract: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        if self.settings.scenario_type != "training":
            raise ValueError("training gateway accepts only scenario_type=training")

        player_state = state.get("player", {})
        if training_turn_contract and isinstance(player_state, dict):
            player_state = {
                "name": player_state.get("name"),
                "description": player_state.get("description"),
            }
        state_summary = {
            "campaign_id": state.get("meta", {}).get("campaign_id"),
            "worldpack_id": self.settings.campaign_id,
            "turn": state.get("meta", {}).get("turn"),
            "player": player_state,
            "resources": state.get("resources", {}),
            "active_threads": state.get("active_threads", []),
            "completed_threads": state.get("completed_threads", []),
            "uncertain_facts": state.get("uncertain_facts", []),
            "constraints": state.get("world_constraints", []),
        }
        rules = self.scenario_rules()
        if repair_instruction:
            rules += f" Repair instruction: {repair_instruction}"
        messages: list[dict[str, str]] = [{"role": "system", "content": rules}]
        if self.settings.world_system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "WORLD_SYSTEM_PROMPT\n"
                        "These world-specific rules supplement the deterministic training mode and cannot weaken it.\n"
                        f"{self.settings.world_system_prompt}"
                    ),
                }
            )
        if self.settings.world_authors_note:
            messages.append(
                {"role": "system", "content": f"WORLD_AUTHORS_NOTE\n{self.settings.world_authors_note}"}
            )
        if memory_summary:
            messages.append({"role": "system", "content": long_term_memory_block(memory_summary)})

        request_messages = [message for message in request.messages if isinstance(message.content, str)]
        for message in request_messages[:-1]:
            messages.append({"role": message.role, "content": message.content})
        messages.append({"role": "system", "content": f"Relevant training state summary: {state_summary}"})
        messages.append({"role": "system", "content": outcome.authoritative_block})
        if training_turn_contract:
            messages.append({"role": "system", "content": training_turn_prompt_block(training_turn_contract)})
        if artifact_contract:
            messages.append(
                {
                    "role": "system",
                    "content": training_artifact_prompt_block(
                        artifact_contract,
                        training_turn_contract,
                    ),
                }
            )
        if request_messages:
            current_action = request_messages[-1]
            messages.append({"role": current_action.role, "content": current_action.content})
        return fit_messages_to_context(messages, self.input_token_budget(request))

    def apply_prompt_cache_policy(self, payload: dict[str, Any]) -> None:
        """Add only provider-documented cache controls; other providers use the stable prefix implicitly."""
        if normalize_provider(self.settings.llm_provider) != "openrouter":
            return
        if self.settings.prompt_cache_session_id:
            payload["session_id"] = self.settings.prompt_cache_session_id
        if self.settings.openrouter_prompt_cache_enabled and str(payload.get("model") or "").startswith("anthropic/"):
            payload["cache_control"] = {"type": "ephemeral", "ttl": self.settings.openrouter_prompt_cache_ttl}

    def apply_model_policy(
        self,
        payload: dict[str, Any],
        model: str,
        *,
        require_parameters: bool = False,
    ) -> None:
        """Apply model-specific runtime controls while preserving unrelated caller preferences."""
        if normalize_provider(self.settings.llm_provider) != "openrouter":
            return
        provider_preferences = dict(payload.get("provider") or {})
        if model.strip().lower() == "deepseek/deepseek-v4-flash":
            provider_preferences["sort"] = "throughput"
        if require_parameters:
            provider_preferences["require_parameters"] = True
        if provider_preferences:
            payload["provider"] = provider_preferences

    def repair_messages(
        self,
        state: dict[str, Any],
        outcome: Outcome,
        repair_instruction: str,
        failed_response_text: str,
        artifact_contract: dict[str, Any] | None = None,
        training_turn_contract: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Build a compact correction request for one deterministic training turn."""
        player_resources = state.get("player", {}).get("resources", {})
        repair_context = {
            "campaign_id": state.get("meta", {}).get("campaign_id"),
            "turn": state.get("meta", {}).get("turn"),
            "current_turn_window": player_resources.get("current-turn-window"),
            "authoritative_outcome": outcome.authoritative_block,
            "repair_instruction": repair_instruction,
            "failed_response": failed_response_text,
        }
        repair_rules = (
            " Correct only the supplied failed response. Do not continue the scenario, redo the turn, "
            "or introduce new facts. Return only the corrected narration or required narrative bundle."
        )
        if training_turn_contract:
            repair_rules += (
                " Return a complete corrected response, not a patch. Fix the named defects while preserving every "
                "visible fact and requirement the failed response already satisfied. Before returning it, recheck "
                "every surface in ACTIVE_TRAINING_TURN_CONTRACT, including marker counts, required_fields, "
                "must_include, profile_adaptation_instruction, effective_links, and attachments."
            )
        messages = [{"role": "system", "content": self.scenario_rules() + repair_rules}]
        if training_turn_contract:
            messages.append({"role": "system", "content": training_turn_prompt_block(training_turn_contract)})
        if artifact_contract:
            messages.append(
                {
                    "role": "system",
                    "content": training_artifact_prompt_block(
                        artifact_contract,
                        training_turn_contract,
                    ),
                }
            )
        messages.append(
            {
                "role": "user",
                "content": json.dumps(repair_context, ensure_ascii=False, separators=(",", ":")),
            }
        )
        return messages

    def input_token_budget(self, request: ChatCompletionRequest) -> int:
        reserve = max(
            self.settings.party_context_completion_reserve_tokens,
            int(request.max_tokens or 0),
        )
        return max(self.settings.effective_party_context_limit_tokens - reserve, 1)

    def scenario_rules(self) -> str:
        if self.settings.scenario_type != "training":
            raise ValueError("training gateway accepts only scenario_type=training")
        return (
            "Reply in the player's language. Output only final in-world narration and dialogue. "
            "Preserve player agency: never choose actions, beliefs, emotions, or conclusions for the player character. "
            "Treat current state as authoritative, do not invent missing resources, and never expose service JSON, "
            "analysis, recommendations, diagnostics, critique, outcome tags, or Gateway wording. "
            "You are the runtime narrator for a deterministic training scenario. There are no random rolls or "
            "skill checks. Follow the authored scenario structure, schedule, presentation templates, and completion "
            "conditions exactly. Resolve only actions explicitly stated by the player and advance exactly one "
            "scenario turn. Do not coach, hint, assess, explain best practice, reveal hidden scoring, or announce "
            "whether an item is safe or suspicious unless the authored scenario explicitly schedules a final debrief. "
            "If player.resources.current-turn-window is present, preserve that exact scheduled turn and never remain "
            "in the previous time window. When a structured response schema is supplied, Gateway adds the authored "
            "player-facing header; otherwise begin with that exact header."
        )

    def mock_completion(
        self,
        outcome: Outcome,
        repair_instruction: str | None,
        artifact_contract: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        training_turn_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mode = self.settings.llm_api_base.removeprefix("mock://")
        if mode == "timeout":
            raise httpx.TimeoutException("mock timeout")
        if mode == "http-503":
            request = httpx.Request("POST", "https://mock.provider.local/chat/completions")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("mock provider unavailable", request=request, response=response)
        if mode == "rate-limit":
            raise ProviderRateLimitError(
                provider=self.settings.llm_provider,
                model=self.settings.narrative_model,
                retry_after_seconds=3,
                error_type="rate_limit_exceeded",
                provider_code="mock_rate_limited",
                response_message="mock rate limit",
            )
        if mode == "violate" and not repair_instruction:
            content = "Despite the failure, the king secretly grants equivalent military authority."
        elif mode == "meta-leak" and not repair_instruction:
            content = "— Анализ: игроку нужен таймскип.\nРекомендация: перейти к событию.\n\nТы идешь к мосту."
        elif mode == "meta-leak":
            content = "Телефон гаснет в кармане. Дорога к мосту сжимается до нескольких шагов, и впереди уже слышны голоса."
        elif mode == "repair-fail":
            content = "Despite the failure, the king still transfers command authority."
        else:
            content = "The scene shifts around the attempt, leaving the next opening clear without taking control from the player."
        site_contract = (
            artifact_contract.get("site")
            if artifact_contract and ("site" in artifact_contract or "workspace" in artifact_contract)
            else artifact_contract
        )
        workspace_contract = (
            artifact_contract.get("workspace")
            if artifact_contract and ("site" in artifact_contract or "workspace" in artifact_contract)
            else None
        )
        if artifact_contract or (training_turn_contract and training_turn_contract.get("kind") == "turn"):
            slot_values = {
                slot_id: ("Продолжить" if slot_id.endswith("label") else "Учебная страница")
                for slot_id in (site_contract or {}).get("slots", {})
            }
            workspace_files = []
            for file_contract in (workspace_contract or {}).get("files", []):
                workspace_files.append(
                    {
                        "file_key": file_contract["file_key"],
                        "blueprint_id": file_contract["blueprint_id"],
                        "slots": {slot_id: "Учебный документ" for slot_id in file_contract.get("slots", {})},
                    }
                )
            narrative_text = content
            artifacts = []
            if site_contract:
                artifacts = [
                    {
                        "artifact_key": site_contract["artifact_key"],
                        "blueprint_id": site_contract["blueprint_id"],
                        "slots": slot_values,
                    }
                ]
            if training_turn_contract and training_turn_contract.get("kind") == "turn":
                visible_surfaces: dict[str, dict[str, str]] = {}
                player = (state or {}).get("player") if isinstance((state or {}).get("player"), dict) else {}
                recipient = str((player or {}).get("name") or "Коллега")
                for surface_key, surface, instance_index in _expanded_training_surfaces(
                    training_turn_contract
                ):
                    values = {
                        "Канал:": (
                            "корпоративная почта"
                            if surface.get("type") == "email"
                            else "рабочий мессенджер"
                        ),
                        "Чат:": "рабочий чат",
                        "От:": "Учебный отправитель — sender@example.test",
                        "Кому:": recipient,
                        "Дата/время:": "текущий рабочий блок",
                        "Тема:": "Учебный рабочий запрос",
                        "Вложения:": "нет",
                        "Ссылки:": _surface_link_value(surface, instance_index),
                        "Тело:": content,
                        "Текст:": content,
                        "Подпись:": "Учебный отправитель",
                    }
                    visible_surfaces[surface_key] = {
                        str(field).strip(): values.get(str(field).strip(), "Учебное значение")
                        for field in surface.get("required_fields") or []
                    }
                content = json.dumps(
                    {
                        "schema_version": "rp-gateway.narrative-bundle.v3",
                        "narrative_text": "",
                        "visible_surfaces": visible_surfaces,
                        "artifacts": artifacts,
                        "workspace_files": workspace_files,
                    },
                    ensure_ascii=False,
                )
            else:
                if site_contract:
                    narrative_text = f"{content}\n\nСсылка: {site_contract['display_url']}"
                content = json.dumps(
                    {
                        "schema_version": (
                            "rp-gateway.narrative-bundle.v2"
                            if workspace_contract
                            else "rp-gateway.narrative-bundle.v1"
                        ),
                        "narrative_text": narrative_text,
                        "artifacts": artifacts,
                        **({"workspace_files": workspace_files} if workspace_contract else {}),
                    },
                    ensure_ascii=False,
                )
        return {
            "id": f"mock-{outcome.check_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.settings.narrative_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }


def response_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return str(content) if content is not None else ""


def json_object_content(value: str) -> str:
    """Extract one provider-wrapped JSON object without accepting mixed JSON payloads."""
    text = value.strip()
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if len(fenced) == 1:
        return fenced[0].strip()
    return text


def provider_rate_limit_error(response: httpx.Response, provider: str, model: str) -> ProviderRateLimitError:
    payload: dict[str, Any] = {}
    try:
        decoded = response.json()
        if isinstance(decoded, dict):
            payload = decoded
    except (ValueError, json.JSONDecodeError):
        pass
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    metadata = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
    message = str(error.get("message") or payload.get("message") or "").strip()[:500] or None
    return ProviderRateLimitError(
        provider=provider,
        model=model,
        retry_after_seconds=parse_retry_after(response.headers.get("Retry-After")),
        error_type=str(metadata.get("error_type") or error.get("error_type") or "").strip() or None,
        provider_code=str(metadata.get("provider_code") or "").strip() or None,
        response_message=message,
    )


def parse_retry_after(value: str | None) -> float | None:
    try:
        seconds = float(value or "")
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def fit_messages_to_context(
    messages: list[dict[str, str]],
    token_budget: int,
    *,
    max_prompt_chars: int | None = None,
    protect_history: bool = False,
    fail_on_token_overflow: bool = False,
    diagnostics: dict[str, Any] | None = None,
    history_removable_units: int | None = None,
    raw_history_turn_ids: list[int] | None = None,
) -> list[dict[str, str]]:
    """Keep the latest action and mandatory instructions inside the real provider input budget."""
    fitted = [dict(message) for message in messages]
    remaining_history_removals = (
        max(int(history_removable_units), 0)
        if history_removable_units is not None
        else None
    )
    fitted_raw_turn_ids = [int(turn_id) for turn_id in (raw_history_turn_ids or [])]
    while fitted:
        prompt_text = "\n".join(message["content"] for message in fitted)
        over_token_budget = estimate_tokens(prompt_text) > token_budget
        over_prompt_chars = max_prompt_chars is not None and len(prompt_text) > max_prompt_chars
        if not over_token_budget and not over_prompt_chars:
            break
        if protect_history:
            # Revision 7 keeps the legacy percentage target best-effort and
            # evicts whole optional blocks only for the provider's hard budget.
            if not over_token_budget:
                break
            optional_prefixes = (
                ("PARTY_LORE_CARDS",)
                if remaining_history_removals is not None
                else (
                    "RETRIEVED_ARCHIVE_SCENES",
                    "LONG_TERM_PARTY_MEMORY",
                    "PARTY_LORE_CARDS",
                    "RELEVANT_CHARACTERS",
                )
            )
            trim_index = next(
                (
                    index
                    for prefix in optional_prefixes
                    for index, message in enumerate(fitted)
                    if message.get("role") == "system"
                    and message.get("content", "").startswith(prefix)
                ),
                None,
            )
            if trim_index is not None:
                removed = fitted.pop(trim_index)
                record_prompt_omission(
                    diagnostics,
                    block_id=prompt_block_id(removed, trim_index),
                    reason="hard_input_budget",
                )
                continue
            if remaining_history_removals is not None and remaining_history_removals > 0:
                history_indices = [
                    index
                    for index, message in enumerate(fitted[:-1])
                    if message.get("role") != "system"
                ]
                oldest_history = history_indices[0] if history_indices else None
                if oldest_history is None:
                    remaining_history_removals = 0
                elif fitted[oldest_history].get("role") == "assistant":
                    fitted.pop(oldest_history)
                    remaining_history_removals -= 1
                    if fitted_raw_turn_ids:
                        fitted_raw_turn_ids.pop(0)
                    continue
                elif (
                    fitted[oldest_history].get("role") == "user"
                    and oldest_history + 1 < len(fitted) - 1
                    and fitted[oldest_history + 1].get("role") == "assistant"
                ):
                    fitted.pop(oldest_history + 1)
                    fitted.pop(oldest_history)
                    remaining_history_removals -= 1
                    if fitted_raw_turn_ids:
                        fitted_raw_turn_ids.pop(0)
                    continue
            if fail_on_token_overflow:
                raise PromptBudgetExceeded(
                    estimated_tokens=estimate_tokens(prompt_text),
                    token_budget=token_budget,
                )
            break
        history_indices = [
            index
            for index, message in enumerate(fitted[:-1])
            if message.get("role") != "system"
        ]
        if (
            over_prompt_chars
            and not over_token_budget
            and [fitted[index].get("role") for index in history_indices] == ["user", "assistant"]
        ):
            break
        oldest_history = history_indices[0] if history_indices else None
        if oldest_history is not None:
            if (
                over_prompt_chars
                and fitted[oldest_history].get("role") == "user"
                and oldest_history + 1 < len(fitted) - 1
                and fitted[oldest_history + 1].get("role") == "assistant"
            ):
                fitted.pop(oldest_history + 1)
            fitted.pop(oldest_history)
            continue
        if over_prompt_chars and not over_token_budget:
            break
        trim_prefixes = (
            "RETRIEVED_ARCHIVE_SCENES",
            "UNCOMPACTED_ARCHIVE_FALLBACK",
            "LONG_TERM_PARTY_MEMORY",
        )
        trim_index = next(
            (
                index
                for prefix in trim_prefixes
                for index, message in enumerate(fitted)
                if message.get("content", "").startswith(prefix)
            ),
            None,
        )
        if trim_index is None:
            trim_index = next((index for index, message in enumerate(fitted[:-1]) if message.get("role") == "system"), None)
        if trim_index is None:
            trim_index = len(fitted) - 1
        content = fitted[trim_index].get("content", "")
        excess_chars = max((estimate_tokens(prompt_text) - token_budget) * 3, 1)
        retained = max(len(content) - excess_chars, 0)
        if retained == 0:
            fitted.pop(trim_index)
        else:
            fitted[trim_index]["content"] = content[:retained]
    if diagnostics is not None and raw_history_turn_ids is not None:
        diagnostics["raw_history_turn_ids"] = fitted_raw_turn_ids
    return fitted


def with_text(response: dict[str, Any], text: str) -> dict[str, Any]:
    updated = dict(response)
    choices = list(updated.get("choices", [])) or [{"index": 0, "message": {"role": "assistant", "content": ""}}]
    first = dict(choices[0])
    message = dict(first.get("message", {}))
    message["role"] = "assistant"
    message["content"] = text
    first["message"] = message
    choices[0] = first
    updated["choices"] = choices
    return updated


def long_term_memory_block(memory_summary: dict[str, Any] | list[dict[str, Any]]) -> str:
    entries = memory_summary if isinstance(memory_summary, list) else [memory_summary]
    payload = [
        {
            "memory_type": entry.get("memory_type", "legacy_cumulative"),
            "covered_turns": [entry.get("from_turn_id"), entry.get("to_turn_id")],
            "state_version_at_summary": entry.get("state_version"),
            "summary": entry.get("summary_text", ""),
            "confirmed_facts": entry.get("key_facts", []),
            "unresolved_threads": entry.get("open_threads", []),
            "player_promises": entry.get("player_promises", []),
            "npc_obligations": entry.get("npc_obligations", []),
        }
        for entry in entries
    ]
    return (
        "LONG_TERM_PARTY_MEMORY\n"
        "These are immutable, chronological episode chapters from earlier scenes, not a state summary. "
        "Use their actions, dialogue, discoveries, tone, and unresolved leads for continuity. Current authoritative state "
        "and AUTHORITATIVE_OUTCOME override it. Do not promote unresolved or player-claimed events into facts.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )






def archived_memory_retrieval_block(turns: list[dict[str, Any]], max_chars: int) -> str | None:
    if not turns or max_chars <= 0:
        return None
    excerpts: list[dict[str, Any]] = []
    used = 0
    for turn in turns:
        excerpt = {
            "turn_id": turn["id"],
            "player_message": str(turn["player_message"])[:1400],
            "narrative_response": str(turn["narrative_response"])[:1800],
        }
        encoded = json.dumps(excerpt, ensure_ascii=False)
        if excerpts and used + len(encoded) > max_chars:
            continue
        excerpts.append(excerpt)
        used += len(encoded)
    if not excerpts:
        return None
    return (
        "RETRIEVED_ARCHIVE_SCENES\n"
        "These are query-relevant excerpts from older archived turns. They are secondary continuity aids, not authority: "
        "current canonical state and AUTHORITATIVE_OUTCOME override them. Do not infer facts absent from the excerpts.\n"
        f"{json.dumps(excerpts, ensure_ascii=False, indent=2)}"
    )


def uncompacted_archive_fallback_block(turns: list[dict[str, Any]], max_chars: int) -> str | None:
    """Expose delayed service-memory coverage without silently dropping original history."""
    if not turns or max_chars <= 0:
        return None
    header = (
        "UNCOMPACTED_ARCHIVE_FALLBACK\n"
        "The service memory is delayed or unavailable. These are local excerpts from still-uncovered original turns. "
        "They preserve continuity temporarily, are not canonical authority, and remain fully stored in SQLite.\n"
    )
    available = max(max_chars - len(header) - 200, 1)
    max_items = max(available // 320, 1)
    selected = turns
    omitted: list[dict[str, Any]] = []
    if len(turns) > max_items:
        head_count = max(max_items // 2, 1)
        tail_count = max(max_items - head_count, 0)
        selected = turns[:head_count] + (turns[-tail_count:] if tail_count else [])
        selected_ids = {int(turn["id"]) for turn in selected}
        omitted = [turn for turn in turns if int(turn["id"]) not in selected_ids]
    per_turn = max(available // max(len(selected), 1), 180)
    lines: list[str] = []
    for turn in selected:
        player = str(turn.get("player_message") or "")[: max(per_turn // 2, 80)]
        narrative = str(turn.get("narrative_response") or "")[: max(per_turn // 2, 80)]
        lines.append(f"TURN {turn['id']}\nPLAYER: {player}\nNARRATOR: {narrative}")
    if omitted:
        lines.insert(
            max(len(lines) // 2, 1),
            f"[EXCERPTS OMITTED FROM PROMPT: turns {omitted[0]['id']}-{omitted[-1]['id']}; originals remain in archive]",
        )
    return (header + "\n\n".join(lines))[:max_chars]
