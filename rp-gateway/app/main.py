"""FastAPI entrypoint for RP Gateway."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.core.config import Settings, get_settings
from app.core.json_patch import PatchError
from app.models.schemas import (
    AutoTestCreate,
    ChatCompletionRequest,
    ChatMessage,
    HealthResponse,
    LoginRequest,
    Outcome,
    PatchEnvelope,
    PatchOperation,
    PartyCharacterStateEditRequest,
    PartyBranchCreate,
    PartyCheckRequest,
    PartyCreate,
    PartyDatasetUpdate,
    PartyLoreCardCreate,
    PartyLoreCardUpdate,
    PartyMemorySummarizeRequest,
    PartyMessageRequest,
    PartyModelUpdate,
    PartyPromptPreviewRequest,
    PartyStartRequest,
    PartyTurnDatasetUpdate,
    TurnFeedbackUpdate,
    TrainingArtifactEventRequest,
    PartyCheckpointCreate,
    PlayerCharacterCreate,
    PlayerCharacterDraftRequest,
    ProviderApiKeyCreate,
    ProviderApiKeyUpdate,
    ServiceModelUpdate,
    ShowroomRunCreate,
    ShowroomScenarioCreate,
    ShowroomScenarioUpdate,
    UserCreate,
    UserDeleteRequest,
    UserPasswordUpdate,
    UserStatusUpdate,
    WorldPromptCreate,
    WorldApplyRequest,
    WorldInstructionRequest,
    WorldPackVisibilityUpdate,
    StatePatch,
)
from app.services.adjudicator import Adjudicator, RequestAlreadyRunning
from app.services.auth_store import AuthStore, AuthUser
from app.services.autotest import AutoPlayerClient
from app.services.character_view import party_character_sheets
from app.services.context_budget import estimate_tokens, model_context_limit_tokens, split_turns_by_token_budget
from app.services.context_estimator import estimate_party_context
from app.services.memory import MemorySummarizer
from app.services.narrative import (
    ProviderRateLimitError,
    NarrativeClient,
    archived_memory_retrieval_block,
    party_lore_cards_block,
    response_text,
    uncompacted_archive_fallback_block,
)
from app.services.nvidia_catalog import normalize_provider, provider_api_key, provider_base_url
from app.services.provider_auth import outbound_headers
from app.services.service_models import (
    SERVICE_MODEL_SETTING_KEY,
    service_model_choice,
    service_model_choices,
    service_model_settings,
)
from app.services.party_store import PartyStore
from app.services.prompt_tools import PromptInspector
from app.services.rule_engine import awareness_turn_window, awareness_turns_remaining, is_awareness_campaign
from app.services.showroom import ShowroomStore
from app.services.state_store import StateStore
from app.services.training_artifacts import TrainingArtifactService
from app.services.validator import OutputValidator, safe_fallback
from app.services.world_instructor import WorldInstructor


AUTO_START_HISTORY_MESSAGE = "[AUTO_START] Старт партии"
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    store = StateStore(settings.sqlite_path, settings.campaign_id, settings.world_state_path)
    auth_store = AuthStore(settings)
    party_store = PartyStore(settings, default_owner_user_id=auth_store.default_owner_user_id())
    showroom_store = ShowroomStore(settings, party_store)

    app = FastAPI(title="RP Gateway", version="0.5.0")
    app.state.settings = settings
    app.state.store = store
    app.state.auth_store = auth_store
    app.state.adjudicator = Adjudicator(settings, store)
    app.state.party_store = party_store
    app.state.showroom_store = showroom_store
    app.state.autotest_tasks = {}

    def settings_with_global_service_model(base: Settings) -> Settings:
        choice_id = auth_store.get_global_setting(SERVICE_MODEL_SETTING_KEY, base.service_model_choice)
        return replace(base, service_model_choice=choice_id)

    def settings_with_provider_key(base: Settings, party: Any | None = None) -> Settings:
        if party is None:
            return settings_with_global_service_model(base)
        updates: dict[str, Any] = {}
        key_fields = {
            "nvidia": "nvidia_api_key",
            "gemini": "gemini_api_key",
            "openrouter": "openrouter_api_key",
        }
        for provider, field_name in key_fields.items():
            secret = auth_store.default_provider_secret(
                provider_base_url(base, provider),
                provider=provider,
                owner_user_id=party.owner_user_id,
                party_id=party.id,
            )
            if secret:
                updates[field_name] = secret
        hydrated = replace(base, **updates) if updates else base
        selected_key = provider_api_key(hydrated, hydrated.llm_provider)
        if selected_key != hydrated.nvidia_api_key:
            hydrated = replace(hydrated, nvidia_api_key=selected_key)
        return settings_with_global_service_model(hydrated)

    def runtime_settings_for_party(party: Any) -> Settings:
        return settings_with_provider_key(settings_for_party(settings, party), party)

    app.state.adjudicator = Adjudicator(settings_with_global_service_model(settings), store)

    def runtime_settings_for_profile(profile: Any, cache_session_id: str, party: Any | None = None) -> Settings:
        return settings_with_provider_key(settings_for_model_profile(settings, profile, cache_session_id), party)

    def runtime_settings_for_branch(party: Any, branch_id: str) -> Settings:
        return replace(
            runtime_settings_for_party(party),
            prompt_cache_session_id=f"rp-party:{party.id}:branch:{branch_id}",
        )

    async def run_autotest(run_id: str) -> None:
        try:
            while True:
                run = party_store.get_autotest_run(run_id)
                if run["status"] in {"completed", "failed", "stopped"}:
                    return
                if run["stop_requested"] or run["status"] == "stopping":
                    party_store.update_autotest_run(run_id, status="stopped", current_phase="stopped")
                    return
                completed_turns = int(run["completed_turns"])
                requested_turns = int(run["requested_turns"])
                if completed_turns >= requested_turns:
                    party_store.update_autotest_run(run_id, status="completed", current_phase="done")
                    return

                if run.get("branch_id"):
                    party = party_store.get_party(run["source_party_id"], owner_user_id=run["owner_user_id"])
                    party_state_store = party_store.store_for_branch(
                        party.id,
                        run["branch_id"],
                        owner_user_id=run["owner_user_id"],
                    )
                    party_settings = runtime_settings_for_branch(party, run["branch_id"])
                else:
                    # Backward compatibility for runs created before checkpoint branches existed.
                    party = party_store.get_party(run["test_party_id"], owner_user_id=run["owner_user_id"])
                    party_state_store = party_store.store_for_party(party.id, owner_user_id=run["owner_user_id"])
                    party_settings = runtime_settings_for_party(party)
                player_profile = party_store.get_model_profile(run["player_model_profile_id"])
                player_settings = runtime_settings_for_profile(player_profile, f"rp-autotest-player:{run_id}", party)
                turn_number = completed_turns + 1
                request_id = f"autotest_{run_id}_{turn_number}"

                party_store.update_autotest_run(run_id, current_phase="player", error=None)
                action = await AutoPlayerClient(player_settings, player_profile).next_action(
                    player_prompt=run["player_prompt"],
                    player_character=party.player_character,
                    scenario_type=party.scenario_type,
                    history=party_state_store.turn_history(limit=32),
                    request_id=f"{request_id}_player",
                )
                run = party_store.get_autotest_run(run_id)
                if run["stop_requested"]:
                    party_store.update_autotest_run(
                        run_id,
                        status="stopped",
                        current_phase="stopped",
                        last_player_action=action,
                    )
                    return

                party_store.update_autotest_run(
                    run_id,
                    current_phase="narrator",
                    last_player_action=action,
                )
                narrator_profile = party.model_profile or party_store.get_model_profile(party.model_profile_id)
                chat_request = party_chat_request(
                    party_state_store,
                    narrator_profile.model,
                    PartyMessageRequest(
                        content=action,
                        idempotency_key=f"autotest:{run_id}:turn:{turn_number}",
                    ),
                    party_settings,
                )
                narrator_response = await Adjudicator(party_settings, party_state_store).handle_chat(
                    chat_request,
                    authorization=None,
                    idempotency_key=f"autotest:{run_id}:turn:{turn_number}",
                    request_id=f"{request_id}_narrator",
                    allow_gateway_fallback=True,
                )
                fallback_turns = int(run.get("fallback_turns") or 0)
                choices = narrator_response.get("choices") or []
                if choices and choices[0].get("finish_reason") == "provider_fallback":
                    fallback_turns += 1
                party_store.update_autotest_run(
                    run_id,
                    completed_turns=turn_number,
                    fallback_turns=fallback_turns,
                    current_phase="player" if turn_number < requested_turns else "done",
                    status="running" if turn_number < requested_turns else "completed",
                    last_player_action=action,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("autotest_run_failed run_id=%s", run_id)
            party_store.update_autotest_run(
                run_id,
                status="failed",
                current_phase="failed",
                error=f"{type(exc).__name__}: {exc}"[:500],
            )

    def schedule_autotest(run_id: str) -> None:
        existing = app.state.autotest_tasks.get(run_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(run_autotest(run_id))
        app.state.autotest_tasks[run_id] = task

        def forget_task(_task: asyncio.Task[Any]) -> None:
            app.state.autotest_tasks.pop(run_id, None)

        task.add_done_callback(forget_task)

    @app.on_event("startup")
    async def resume_service_jobs() -> None:
        for party in party_store.list_parties():
            party_state_store = party_store.store_for_party(party.id)
            recovered = party_state_store.recover_interrupted_work()
            if any(recovered.values()):
                logger.warning("recovered_interrupted_work party_id=%s %s", party.id, recovered)
            if any(job["status"] in {"pending", "running"} for job in party_state_store.service_jobs(limit=20)):
                Adjudicator(runtime_settings_for_party(party), party_state_store).schedule_service_jobs()
        for branch in party_store.list_all_party_branches():
            branch_store = party_store.store_for_branch(branch["party_id"], branch["id"])
            recovered = branch_store.recover_interrupted_work()
            if any(recovered.values()):
                logger.warning("recovered_interrupted_branch_work branch_id=%s %s", branch["id"], recovered)
        for run in party_store.resumable_autotest_runs():
            schedule_autotest(run["id"])

    def current_user(request: Request) -> AuthUser | None:
        if not settings.auth_enabled:
            return None
        user = getattr(request.state, "user", None)
        if not isinstance(user, AuthUser):
            raise HTTPException(status_code=401, detail="authentication required")
        return user

    def owner_user_id(request: Request) -> str | None:
        if getattr(request.state, "showroom_party_access", False):
            return None
        user = current_user(request)
        return user.id if user else None

    def require_admin(request: Request) -> AuthUser | None:
        user = current_user(request)
        if settings.auth_enabled and (not user or not user.is_admin):
            raise HTTPException(status_code=403, detail="admin role required")
        return user

    def can_view_private_worldpacks(request: Request) -> bool:
        user = current_user(request)
        return not settings.auth_enabled or bool(user and user.is_admin)

    def accessible_worldpack(request: Request, worldpack_id: str) -> Any:
        return party_store.get_worldpack(
            worldpack_id,
            owner_user_id=owner_user_id(request),
            include_private=can_view_private_worldpacks(request),
        )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if not settings.auth_enabled or not request.url.path.startswith("/api/"):
            return await call_next(request)
        if request.url.path.startswith("/api/auth/"):
            return await call_next(request)
        if request.url.path == "/api/showroom" or request.url.path.startswith("/api/showroom/"):
            return await call_next(request)
        token = request.cookies.get(settings.auth_session_cookie_name)
        user = auth_store.user_for_session(token)
        if user is None:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        request.state.user = user
        return await call_next(request)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        try:
            store.get_state()
            database = "ok"
        except Exception:  # noqa: BLE001
            database = "error"
        status = "ok" if database == "ok" else "error"
        return HealthResponse(status=status, campaign_id=settings.campaign_id, database=database)

    @app.get("/api/auth/me")
    def auth_me(request: Request) -> dict[str, Any]:
        user = auth_store.user_for_session(request.cookies.get(settings.auth_session_cookie_name)) if settings.auth_enabled else None
        return {
            "auth_enabled": settings.auth_enabled,
            "authenticated": user is not None or not settings.auth_enabled,
            "user": user.public_dict() if user else None,
        }

    @app.post("/api/auth/login")
    def auth_login(request: LoginRequest, response: Response) -> dict[str, Any]:
        if not settings.auth_enabled:
            return {"auth_enabled": False, "authenticated": True, "user": None}
        try:
            user = auth_store.authenticate(request.username, request.password)
        except ValueError:
            user = None
        if user is None:
            raise HTTPException(status_code=401, detail="invalid username or password")
        token = auth_store.create_session(user.id)
        response.set_cookie(
            settings.auth_session_cookie_name,
            token,
            max_age=settings.auth_session_ttl_seconds,
            httponly=True,
            secure=settings.auth_cookie_secure,
            samesite="lax",
        )
        return {"auth_enabled": True, "authenticated": True, "user": user.public_dict()}

    @app.post("/api/auth/logout")
    def auth_logout(request: Request, response: Response) -> dict[str, Any]:
        auth_store.delete_session(request.cookies.get(settings.auth_session_cookie_name))
        response.delete_cookie(settings.auth_session_cookie_name)
        return {"logged_out": True}

    @app.get("/api/admin/users")
    def admin_list_users(request: Request) -> dict[str, Any]:
        require_admin(request)
        users = auth_store.list_users()
        return {
            "users": [
                {
                    **user.public_dict(),
                    "party_count": len(party_store.list_parties(owner_user_id=user.id)),
                    "character_count": len(party_store.list_player_characters(owner_user_id=user.id)),
                }
                for user in users
            ]
        }

    @app.post("/api/admin/users")
    def admin_create_user(request: Request, payload: UserCreate) -> dict[str, Any]:
        require_admin(request)
        try:
            user = auth_store.create_user(payload.username, payload.password, payload.role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"user": user.public_dict()}

    @app.patch("/api/admin/users/{user_id}/password")
    def admin_set_user_password(request: Request, user_id: str, payload: UserPasswordUpdate) -> dict[str, Any]:
        require_admin(request)
        try:
            user = auth_store.set_password(user_id, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"user": user.public_dict()}

    @app.patch("/api/admin/users/{user_id}/status")
    def admin_set_user_status(request: Request, user_id: str, payload: UserStatusUpdate) -> dict[str, Any]:
        require_admin(request)
        try:
            user = auth_store.set_user_status(user_id, payload.status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"user": user.public_dict()}

    @app.delete("/api/admin/users/{user_id}")
    def admin_delete_user(request: Request, user_id: str, payload: UserDeleteRequest = UserDeleteRequest()) -> dict[str, Any]:
        admin = require_admin(request)
        if admin and admin.id == user_id:
            raise HTTPException(status_code=400, detail="cannot delete the current admin session user")
        try:
            if payload.delete_data:
                party_store.delete_user_data(user_id)
            elif party_store.list_parties(owner_user_id=user_id) or party_store.list_player_characters(owner_user_id=user_id):
                raise ValueError("user still owns parties or characters")
            auth_store.delete_user(user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": True, "user_id": user_id, "deleted_data": payload.delete_data}

    @app.get("/api/admin/global-settings/service-model")
    def admin_get_service_model(request: Request) -> dict[str, Any]:
        require_admin(request)
        runtime = settings_with_global_service_model(settings)
        return {
            "term": "Служебная модель",
            "scope": "Весь RP Stack: все текущие и будущие партии всех пользователей",
            "uses": ["Долговременная память", "Изменение мира", "Генерация персонажей"],
            "choice_id": runtime.service_model_choice,
            "selected": service_model_choice(runtime),
            "choices": service_model_choices(runtime),
        }

    @app.patch("/api/admin/global-settings/service-model")
    def admin_set_service_model(request: Request, payload: ServiceModelUpdate) -> dict[str, Any]:
        require_admin(request)
        choices = service_model_choices(settings)
        selected = next((choice for choice in choices if choice["id"] == payload.choice_id), None)
        if selected is None:
            raise HTTPException(status_code=400, detail="unknown service model choice")
        if not selected["available"]:
            detail = "local service model is disabled" if selected["provider"] == "local" else "server OpenRouter API key is not configured"
            raise HTTPException(status_code=400, detail=detail)
        auth_store.set_global_setting(SERVICE_MODEL_SETTING_KEY, payload.choice_id)
        runtime = settings_with_global_service_model(settings)
        return {
            "term": "Служебная модель",
            "scope": "Весь RP Stack: все текущие и будущие партии всех пользователей",
            "uses": ["Долговременная память", "Изменение мира", "Генерация персонажей"],
            "choice_id": runtime.service_model_choice,
            "selected": service_model_choice(runtime),
            "choices": service_model_choices(runtime),
        }

    @app.patch("/api/admin/datasets/parties/{party_id}")
    def admin_update_party_dataset(
        request: Request,
        party_id: str,
        payload: PartyDatasetUpdate,
    ) -> dict[str, Any]:
        admin = require_admin(request)
        try:
            party = party_store.update_party_dataset(
                party_id,
                review_status=payload.review_status,
                tags=payload.tags,
                owner_user_id=admin.id if admin else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party": party.model_dump(mode="json")}

    @app.get("/api/admin/datasets/parties/{party_id}/turns")
    def admin_list_dataset_turns(
        request: Request,
        party_id: str,
        branch_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        admin = require_admin(request)
        try:
            turns = party_store.list_dataset_turns(
                party_id,
                branch_id=branch_id,
                owner_user_id=admin.id if admin else None,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party_id": party_id, "branch_id": branch_id, "turns": turns}

    @app.put("/api/admin/datasets/parties/{party_id}/turns/{turn_id}")
    def admin_label_dataset_turn(
        request: Request,
        party_id: str,
        turn_id: int,
        payload: PartyTurnDatasetUpdate,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        admin = require_admin(request)
        try:
            label = party_store.set_turn_dataset_label(
                party_id,
                turn_id,
                branch_id=branch_id,
                review_status=payload.review_status,
                tags=payload.tags,
                notes=payload.notes,
                owner_user_id=admin.id if admin else None,
                updated_by_user_id=admin.id if admin else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party_id": party_id, "branch_id": branch_id, "label": label}

    @app.get("/api/admin/datasets/export.jsonl")
    def admin_export_dataset(
        request: Request,
        scenario_type: str | None = None,
        include_branches: bool = True,
    ) -> StreamingResponse:
        admin = require_admin(request)
        if scenario_type and scenario_type not in {"rp", "novel", "training"}:
            raise HTTPException(status_code=400, detail="scenario_type must be rp, novel, or training")
        export = party_store.export_dataset_records(
            owner_user_id=admin.id if admin else None,
            scenario_type=scenario_type,
            include_branches=include_branches,
        )
        body = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in export["records"])
        return StreamingResponse(
            iter([body]),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": 'attachment; filename="rp-gateway-sft-v1.jsonl"',
                "X-Dataset-Approved-Turns": str(export["approved_turns"]),
                "X-Dataset-Skipped-Missing-Prompt": str(export["skipped_missing_prompt"]),
            },
        )

    @app.get("/api/state")
    def get_state(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {"campaign_id": settings.campaign_id, "state": store.get_state()}

    @app.get("/api/state/history")
    def get_history(request: Request, limit: int = 50) -> dict[str, Any]:
        require_admin(request)
        return {"campaign_id": settings.campaign_id, "history": store.history(limit=limit)}

    @app.get("/api/worldpacks")
    def list_worldpacks(request: Request) -> dict[str, Any]:
        packs = party_store.list_worldpacks(
            owner_user_id=owner_user_id(request),
            include_private=can_view_private_worldpacks(request),
        )
        return {"worldpacks": [pack.model_dump(mode="json") for pack in packs]}

    @app.patch("/api/admin/worldpacks/{worldpack_id}/visibility")
    def admin_set_worldpack_visibility(
        request: Request,
        worldpack_id: str,
        payload: WorldPackVisibilityUpdate,
    ) -> dict[str, Any]:
        require_admin(request)
        try:
            pack = party_store.set_worldpack_visibility(worldpack_id, payload.visibility)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"worldpack": pack.model_dump(mode="json")}

    @app.post("/api/worldpacks/prompt")
    def create_prompt_worldpack(request: Request, payload: WorldPromptCreate) -> dict[str, Any]:
        try:
            pack = party_store.create_prompt_worldpack(payload, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"worldpack": pack.model_dump(mode="json")}

    @app.get("/api/worldpacks/{worldpack_id}")
    def get_worldpack(request: Request, worldpack_id: str) -> dict[str, Any]:
        try:
            pack = accessible_worldpack(request, worldpack_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"worldpack": pack.model_dump(mode="json")}

    @app.get("/api/worldpacks/{worldpack_id}/player-templates")
    def player_templates(request: Request, worldpack_id: str) -> dict[str, Any]:
        try:
            templates = party_store.player_templates(
                worldpack_id,
                owner_user_id=owner_user_id(request),
                include_private=can_view_private_worldpacks(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"worldpack_id": worldpack_id, "templates": [template.model_dump(mode="json") for template in templates]}

    @app.get("/api/player-characters")
    def list_player_characters(request: Request, worldpack_id: str | None = None) -> dict[str, Any]:
        characters = party_store.list_player_characters(worldpack_id=worldpack_id, owner_user_id=owner_user_id(request))
        return {"player_characters": [character.model_dump(mode="json") for character in characters]}

    @app.post("/api/player-characters/draft")
    def draft_player_character(request: Request, payload: PlayerCharacterDraftRequest) -> dict[str, Any]:
        try:
            pack = accessible_worldpack(request, payload.worldpack_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        description = payload.concept.strip() or str(pack.manifest.get("player_role") or "Player character")
        return {
            "draft": {
                "worldpack_id": payload.worldpack_id,
                "name": payload.name,
                "description": description,
                "profile": {
                    "source": "light-gui-draft",
                    "worldpack_id": payload.worldpack_id,
                    "world_title": pack.title,
                    "concept": description,
                },
            }
        }

    @app.post("/api/player-characters")
    def create_player_character(request: Request, payload: PlayerCharacterCreate) -> dict[str, Any]:
        try:
            accessible_worldpack(request, payload.worldpack_id)
            character = party_store.create_player_character(payload, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"player_character": character.model_dump(mode="json")}

    @app.get("/api/model-profiles")
    def list_model_profiles() -> dict[str, Any]:
        party_store.settings = settings_with_provider_key(settings)
        profiles = party_store.list_model_profiles()
        return {"model_profiles": [profile.model_dump(mode="json") for profile in profiles]}

    @app.get("/api/parties")
    def list_parties(request: Request) -> dict[str, Any]:
        return {"parties": [party.model_dump(mode="json") for party in party_store.list_parties(owner_user_id=owner_user_id(request))]}

    @app.post("/api/parties")
    def create_party(request: Request, payload: PartyCreate) -> dict[str, Any]:
        try:
            accessible_worldpack(request, payload.worldpack_id)
            party = party_store.create_party(payload, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party": party.model_dump(mode="json")}

    @app.get("/api/parties/{party_id}")
    def get_party(request: Request, party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party": party.model_dump(mode="json")}

    @app.get("/api/parties/{party_id}/byok")
    def list_party_byok(request: Request, party_id: str) -> dict[str, Any]:
        owner_id = owner_user_id(request)
        try:
            party_store.get_party(party_id, owner_user_id=owner_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "api_keys": [key.public_dict() for key in auth_store.list_provider_api_keys(owner_id, party_id)],
        }

    @app.post("/api/parties/{party_id}/byok")
    def create_party_byok(request: Request, party_id: str, payload: ProviderApiKeyCreate) -> dict[str, Any]:
        owner_id = owner_user_id(request)
        try:
            party_store.get_party(party_id, owner_user_id=owner_id)
            key = auth_store.create_provider_api_key(
                label=payload.label,
                secret_value=payload.api_key,
                provider=payload.provider,
                base_url=payload.base_url,
                is_default=payload.is_default,
                owner_user_id=owner_id,
                party_id=party_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, "api_key": key.public_dict()}

    @app.patch("/api/parties/{party_id}/byok/{key_id}")
    def update_party_byok(
        request: Request,
        party_id: str,
        key_id: str,
        payload: ProviderApiKeyUpdate,
    ) -> dict[str, Any]:
        owner_id = owner_user_id(request)
        try:
            party_store.get_party(party_id, owner_user_id=owner_id)
            key = auth_store.update_provider_api_key(
                key_id,
                label=payload.label,
                secret_value=payload.api_key,
                is_default=payload.is_default,
                owner_user_id=owner_id,
                party_id=party_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, "api_key": key.public_dict()}

    @app.delete("/api/parties/{party_id}/byok/{key_id}")
    def delete_party_byok(request: Request, party_id: str, key_id: str) -> dict[str, Any]:
        owner_id = owner_user_id(request)
        try:
            party_store.get_party(party_id, owner_user_id=owner_id)
            auth_store.delete_provider_api_key(key_id, owner_id, party_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": True, "party_id": party_id, "api_key_id": key_id}

    @app.post("/api/parties/{party_id}/activate")
    def activate_party(request: Request, party_id: str) -> dict[str, Any]:
        try:
            party = party_store.activate_party(party_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party": party.model_dump(mode="json")}

    @app.delete("/api/parties/{party_id}")
    def delete_party(request: Request, party_id: str) -> dict[str, Any]:
        owner_id = owner_user_id(request)
        try:
            party_store.get_party(party_id, owner_user_id=owner_id)
            auth_store.delete_party_provider_api_keys(owner_id, party_id)
            party_store.delete_party(party_id, owner_user_id=owner_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True, "party_id": party_id}

    @app.patch("/api/parties/{party_id}/model")
    def update_party_model(request: Request, party_id: str, payload: PartyModelUpdate) -> dict[str, Any]:
        try:
            party = party_store.update_party_model(party_id, payload.model_profile_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party": party.model_dump(mode="json")}

    @app.get("/api/parties/{party_id}/state")
    def get_party_state(request: Request, party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(request))
            party_state = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request)).get_state()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party_id": party.id, "state_campaign_id": party.state_campaign_id, "state": party_state}

    @app.get("/api/parties/{party_id}/history")
    def get_party_history(request: Request, party_id: str, limit: int = 50) -> dict[str, Any]:
        try:
            party_store.get_party(party_id, owner_user_id=owner_user_id(request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "turns": party_state_store.turn_history(limit=limit),
            "state_versions": party_state_store.history(limit=limit),
        }

    @app.post("/api/parties/{party_id}/artifact-events")
    def record_party_artifact_event(
        http_request: Request,
        party_id: str,
        payload: TrainingArtifactEventRequest,
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            service = TrainingArtifactService(party.worldpack, party_state_store)
            if not service.enabled or party.scenario_type != "training":
                raise ValueError("interactive training artifacts are not enabled for this party")
            result = service.record_event(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, **result.model_dump(mode="json")}

    @app.put("/api/parties/{party_id}/turns/{turn_id}/feedback")
    def update_party_turn_feedback(
        request: Request,
        party_id: str,
        turn_id: int,
        payload: TurnFeedbackUpdate,
    ) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            feedback = party_state_store.set_turn_feedback(
                turn_id,
                rating=payload.rating or ("positive" if payload.liked else "none"),
                source_ui="light-gui",
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party_id": party_id, "feedback": feedback}

    @app.get("/api/parties/{party_id}/requests/{request_id}")
    def get_party_request(request: Request, party_id: str, request_id: str) -> dict[str, Any]:
        try:
            party_store.get_party(party_id, owner_user_id=owner_user_id(request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        turn = party_state_store.get_turn_by_request_id(request_id)
        status = party_state_store.get_turn_request(request_id)
        if turn:
            return {
                "party_id": party_id,
                "request_id": request_id,
                "status": "completed",
                "turn": turn,
                "request": status,
            }
        if status:
            return {
                "party_id": party_id,
                "request_id": request_id,
                "status": status.get("status"),
                "error": status.get("error"),
                "turn": None,
                "request": status,
            }
        return {
            "party_id": party_id,
            "request_id": request_id,
            "status": "unknown",
            "turn": None,
            "request": None,
        }

    @app.get("/api/parties/{party_id}/memory")
    def get_party_memory(request: Request, party_id: str, limit: int = 5) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            party_settings = runtime_settings_for_party(party)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        summarizer = MemorySummarizer(party_settings, party_state_store)
        chapters = party_state_store.memory_chapters(limit=limit)
        legacy_summaries = party_state_store.memory_summaries(limit=limit)
        return {
            "party_id": party_id,
            "memory": party_state_store.latest_memory_coverage(),
            "summaries": chapters or legacy_summaries,
            "legacy_summaries": legacy_summaries,
            "chapters": chapters,
            "stats": summarizer.stats(),
        }

    @app.get("/api/parties/{party_id}/service-jobs")
    def get_party_service_jobs(request: Request, party_id: str, limit: int = 20) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party_id": party_id, "jobs": party_state_store.service_jobs(limit=min(max(limit, 1), 100))}

    @app.get("/api/parties/{party_id}/lore-cards")
    def get_party_lore_cards(request: Request, party_id: str, include_archived: bool = False) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party_id": party_id, "cards": party_state_store.lore_cards(include_archived=include_archived)}

    @app.post("/api/parties/{party_id}/lore-cards")
    def create_party_lore_card(request: Request, party_id: str, card: PartyLoreCardCreate) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            created = party_state_store.create_lore_card(**card.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        party_state_store.audit("lore_card_created", {"card_id": created["id"], "title": created["title"]})
        return {"party_id": party_id, "card": created}

    @app.patch("/api/parties/{party_id}/lore-cards/{card_id}")
    def update_party_lore_card(
        request: Request,
        party_id: str,
        card_id: int,
        card: PartyLoreCardUpdate,
    ) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            updated = party_state_store.update_lore_card(card_id, card.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        party_state_store.audit("lore_card_updated", {"card_id": updated["id"], "archived": updated["archived"]})
        return {"party_id": party_id, "card": updated}

    @app.get("/api/parties/{party_id}/checkpoints")
    def get_party_checkpoints(
        request: Request,
        party_id: str,
        limit: int = 50,
        include_state: bool = False,
    ) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "checkpoints": party_state_store.memory_checkpoints(
                limit=min(max(limit, 1), 100),
                include_state=include_state,
            ),
        }

    @app.post("/api/parties/{party_id}/checkpoints")
    def create_party_checkpoint(request: Request, party_id: str, checkpoint: PartyCheckpointCreate) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            created = party_state_store.create_memory_checkpoint(checkpoint.label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        party_state_store.audit(
            "memory_checkpoint_created",
            {"checkpoint_id": created["id"], "through_turn_id": created["through_turn_id"]},
        )
        return {"party_id": party_id, "checkpoint": created}

    @app.get("/api/parties/{party_id}/branches")
    def get_party_branches(request: Request, party_id: str, limit: int = 100) -> dict[str, Any]:
        try:
            branches = party_store.list_party_branches(
                party_id,
                owner_user_id=owner_user_id(request),
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party_id": party_id, "branches": branches}

    @app.post("/api/parties/{party_id}/branches")
    def create_party_branch(request: Request, party_id: str, payload: PartyBranchCreate) -> dict[str, Any]:
        try:
            branch = party_store.create_party_branch(
                party_id=party_id,
                checkpoint_id=payload.checkpoint_id,
                label=payload.label,
                owner_user_id=owner_user_id(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, "branch": branch}

    @app.get("/api/parties/{party_id}/branches/{branch_id}")
    def get_party_branch(request: Request, party_id: str, branch_id: str, limit: int = 200) -> dict[str, Any]:
        owner_id = owner_user_id(request)
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_id)
            branch = party_store.get_party_branch(party_id, branch_id, owner_user_id=owner_id)
            branch_store = party_store.store_for_branch(party_id, branch_id, owner_user_id=owner_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        branch_state = branch_store.get_state()
        return {
            "party": party.model_dump(mode="json"),
            "branch": branch,
            "state": branch_state,
            "turns": branch_store.turn_history(limit=min(max(limit, 1), 500)),
            "state_versions": branch_store.history(limit=min(max(limit, 1), 500)),
            "characters": party_character_sheets(branch_state),
        }

    @app.post("/api/parties/{party_id}/memory/summarize")
    async def summarize_party_memory(
        http_request: Request,
        party_id: str,
        request: PartyMemorySummarizeRequest = PartyMemorySummarizeRequest(),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            party_settings = runtime_settings_for_party(party)
            result = await MemorySummarizer(party_settings, party_state_store).summarize(
                authorization,
                force=request.force,
                fail_open=False,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise HTTPException(status_code=502, detail=f"Narrative provider HTTP {status}") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, **result}

    @app.delete("/api/parties/{party_id}/memory/latest")
    def delete_party_memory_latest(request: Request, party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            party_settings = runtime_settings_for_party(party)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        deleted = party_state_store.delete_latest_memory_coverage()
        return {
            "party_id": party_id,
            "deleted": deleted is not None,
            "deleted_memory": deleted,
            "memory": party_state_store.latest_memory_coverage(),
            "stats": MemorySummarizer(party_settings, party_state_store).stats(),
        }

    @app.get("/api/parties/{party_id}/context")
    def get_party_context(request: Request, party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            party_settings = runtime_settings_for_party(party)
            model_profile = party.model_profile or party_store.get_model_profile(party.model_profile_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "context": estimate_party_context(party_state_store, party_settings, model_profile),
        }

    @app.get("/api/parties/{party_id}/characters")
    def get_party_characters(request: Request, party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "state_campaign_id": party.state_campaign_id,
            "characters": party_character_sheets(party_state_store.get_state()),
        }

    @app.post("/api/parties/{party_id}/characters/edit")
    def party_character_edit(http_request: Request, party_id: str, request: PartyCharacterStateEditRequest) -> dict[str, Any]:
        try:
            party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            patch = character_state_patch(party_state_store.get_state(), request)
            party_state_store.create_patch_proposal(patch)
            candidate = party_state_store.preview_patch(patch)
            if request.confirm:
                state = party_state_store.apply_pending_patch(patch.check_id or "latest", reason="party_character_edit_confirm")
                return {"party_id": party_id, "applied": True, "proposal_id": patch.check_id, "state": state}
        except (PatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "applied": False,
            "proposal_id": patch.check_id,
            "proposal": patch.model_dump(mode="json"),
            "candidate": candidate,
        }

    @app.post("/api/parties/{party_id}/characters/generate")
    async def party_character_generate(
        http_request: Request,
        party_id: str,
        request: PartyCharacterStateEditRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        request_id = f"character_generate_{uuid.uuid4().hex[:12]}"
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            party_settings = runtime_settings_for_party(party)
            generated = await generate_character_edit(party_settings, party_state_store, request, authorization, request_id)
            patch = character_state_patch(party_state_store.get_state(), generated)
            state = party_state_store.apply_state_patch(patch, reason=f"party_character_generate:{request_id}")
            character_id = stable_character_id(generated.character_id or generated.name or "")
            party_state_store.audit(
                "party_character_generate",
                {
                    "request_id": request_id,
                    "character_id": character_id,
                    "model": service_model_settings(party_settings).intent_model,
                    "service_model": True,
                },
                request_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise HTTPException(status_code=502, detail=f"Narrative provider HTTP {status}") from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=502, detail="Narrative provider timed out") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (PatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "applied": True,
            "request_id": request_id,
            "character_id": character_id,
            "generated": generated.model_dump(mode="json"),
            "patch": patch.model_dump(mode="json"),
            "state": state,
        }

    @app.post("/api/parties/{party_id}/prompt/preview")
    def preview_party_prompt(http_request: Request, party_id: str, request: PartyPromptPreviewRequest) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            party_settings = runtime_settings_for_party(party)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            preview = PromptInspector(party_settings, party_state_store).preview(request.content, source=request.source)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, "preview": preview}

    @app.post("/api/parties/{party_id}/start")
    async def start_party(
        http_request: Request,
        party_id: str,
        request: PartyStartRequest = PartyStartRequest(),
        authorization: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            party_settings = runtime_settings_for_party(party)
            model_profile = party.model_profile or party_store.get_model_profile(party.model_profile_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        idempotency_key = request.idempotency_key or f"party-start:{party_id}"
        request_id = x_request_id or f"req_{uuid.uuid4().hex}"
        existing = party_state_store.get_turn_by_idempotency(idempotency_key)
        if existing:
            message = existing.get("choices", [{}])[0].get("message", {"role": "assistant", "content": ""})
            return {
                "party_id": party_id,
                "started": False,
                "already_started": True,
                "reason": "idempotency_key_exists",
                "state_version": party_state_store.current_version(),
                "message": message,
                "raw": existing,
            }

        existing_turns = party_state_store.turn_history(limit=1)
        if existing_turns:
            return {
                "party_id": party_id,
                "started": False,
                "already_started": True,
                "reason": "history_exists",
                "state_version": party_state_store.current_version(),
                "latest_turn": existing_turns[-1],
            }

        request_status = party_state_store.begin_turn_request(idempotency_key, request_id)
        if not request_status.get("acquired"):
            if request_status.get("status") == "completed" and request_status.get("response"):
                response = request_status["response"]
                message = response.get("choices", [{}])[0].get("message", {"role": "assistant", "content": ""})
                return {
                    "party_id": party_id,
                    "started": False,
                    "already_started": True,
                    "reason": "request_completed",
                    "state_version": party_state_store.current_version(),
                    "message": message,
                    "raw": response,
                }
            if request_status.get("status") == "running":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "status": "running",
                        "request_id": request_status.get("request_id") or request_id,
                        "idempotency_key": idempotency_key,
                        "message": "request is already running",
                    },
                )

        try:
            state = party_state_store.get_state()
            start_patch = party_start_state_patch(state, party_id, party.worldpack_id, party.scenario_type)
            narrative_state = party_start_narrative_state(state, start_patch)
            prompt = party_start_prompt(party_store, party)
            chat_request = ChatCompletionRequest(
                model=model_profile.model,
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stream=False,
            )
            start_outcome = party_start_outcome(party_id, party.scenario_type)
            memory_summary = party_state_store.memory_for_prompt(party_settings.party_memory_prompt_max_chars)
            narrative = NarrativeClient(party_settings)
            artifact_service = TrainingArtifactService(party.worldpack, party_state_store)
            artifact_contract = artifact_service.contract_for_state(narrative_state)
            prompt_messages = narrative.narrative_messages(
                chat_request,
                narrative_state,
                start_outcome,
                repair_instruction=None,
                memory_summary=memory_summary,
                artifact_contract=artifact_contract,
            )
            repaired = False
            fallback_reason: str | None = None
            raw = await narrative.complete(
                chat_request,
                narrative_state,
                start_outcome,
                authorization,
                memory_summary=memory_summary,
                request_id=request_id,
                artifact_contract=artifact_contract,
            )
            adjudicator = Adjudicator(party_settings, party_state_store)
            response = adjudicator.normalize_response(raw, model_profile.model)
            text = response_text(response)
            artifact_result = artifact_service.materialize_response(response, artifact_contract)
            if artifact_result.valid:
                response = artifact_result.response
                text = artifact_result.text
            validator = OutputValidator()
            validation = validator.validate(
                text,
                start_outcome,
                narrative_state,
                campaign_id=party.worldpack_id,
                scenario_type=party.scenario_type,
            )
            if (not validation.valid or not artifact_result.valid) and party_settings.max_repair_attempts > 0:
                repaired = True
                repair_instruction = validation.repair_instruction
                if not artifact_result.valid:
                    repair_instruction = " ".join(
                        [
                            repair_instruction,
                            "Return a valid narrative bundle: " + "; ".join(artifact_result.violations),
                        ]
                    ).strip()
                raw = await narrative.complete(
                    chat_request,
                    narrative_state,
                    start_outcome,
                    authorization,
                    repair_instruction,
                    memory_summary=memory_summary,
                    request_id=request_id,
                    artifact_contract=artifact_contract,
                )
                response = adjudicator.normalize_response(raw, model_profile.model)
                text = response_text(response)
                artifact_result = artifact_service.materialize_response(response, artifact_contract)
                if artifact_result.valid:
                    response = artifact_result.response
                    text = artifact_result.text
                validation = validator.validate(
                    text,
                    start_outcome,
                    narrative_state,
                    campaign_id=party.worldpack_id,
                    scenario_type=party.scenario_type,
                )
            if not validation.valid or not artifact_result.valid:
                fallback_reason = "validation_failed"
                party_state_store.audit(
                    "party_start_validation_failed",
                    {
                        "request_id": request_id,
                        "model": model_profile.model,
                        "violations": [*validation.violations, *artifact_result.violations],
                    },
                    request_id,
                )
                allow_safe_fallback = getattr(http_request.state, "showroom_party_access", False) or (
                    party.scenario_type == "training"
                    and is_awareness_campaign(narrative_state, party.worldpack_id)
                )
                if not allow_safe_fallback:
                    raise RuntimeError("LLM response failed narrative validation")
                text = safe_fallback(
                    start_outcome,
                    narrative_state,
                    "",
                    party.worldpack_id,
                    party.scenario_type,
                )
                response = adjudicator.provider_fallback_response(
                    start_outcome,
                    text,
                    "validation_failed",
                    request_id,
                )
                artifact_result = artifact_service.fallback_materialization(response, text, artifact_contract)
                response = artifact_result.response
                text = artifact_result.text
            if start_patch:
                state = party_state_store.apply_state_patch(start_patch, reason=f"party_start:{request_id}")
            state_version = party_state_store.current_version() or int(state.get("meta", {}).get("state_version") or 1)
            turn_id = party_state_store.record_turn(
                idempotency_key,
                request_id,
                AUTO_START_HISTORY_MESSAGE,
                text,
                response,
                state_version,
                prompt_messages,
                {
                    "schema_version": "rp-gateway.turn.v1",
                    "turn_kind": "opening_scene",
                    "scenario_type": party.scenario_type,
                    "worldpack_id": party.worldpack_id,
                    "state_campaign_id": party_state_store.campaign_id,
                    "narrative_provider": party_settings.llm_provider,
                    "narrative_model": model_profile.model,
                    "generated_by": "human",
                    "validator_valid": validation.valid,
                    "repaired": repaired,
                    "fallback": fallback_reason is not None,
                    "fallback_reason": fallback_reason,
                    "llm_calls": 2 if repaired else 1,
                    "outcome": start_outcome.model_dump(mode="json"),
                },
                artifacts=artifact_result.persistence_records,
            )
            party_state_store.complete_turn_request(idempotency_key, response)
            party_state_store.audit(
                "party_start_complete",
                {"request_id": request_id, "turn_id": turn_id, "model": model_profile.model},
                request_id,
            )
        except PermissionError as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise HTTPException(status_code=502, detail=f"Narrative provider HTTP {status}") from exc
        except ProviderRateLimitError as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            party_state_store.audit("party_start_rate_limited", {"request_id": request_id, **exc.details}, request_id)
            raise HTTPException(status_code=429, detail=exc.public_detail()) from exc
        except RuntimeError as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "party_id": party_id,
            "started": True,
            "already_started": False,
            "state_version": state_version,
            "message": response.get("choices", [{}])[0].get("message", {"role": "assistant", "content": ""}),
            "raw": response,
        }

    @app.post("/api/parties/{party_id}/messages")
    async def party_message(
        http_request: Request,
        party_id: str,
        request: PartyMessageRequest,
        authorization: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            party_settings = runtime_settings_for_party(party)
            model_profile = party.model_profile or party_store.get_model_profile(party.model_profile_id)
            chat_request = party_chat_request(
                party_state_store,
                model_profile.model,
                request,
                party_settings,
            )
            artifact_service = TrainingArtifactService(party.worldpack, party_state_store)
            response = await Adjudicator(
                party_settings,
                party_state_store,
                training_artifacts=artifact_service,
            ).handle_chat(
                chat_request,
                authorization,
                request.idempotency_key,
                x_request_id,
                allow_gateway_fallback=(
                    party.scenario_type == "training"
                    and is_awareness_campaign(party_state_store.get_state(), party.worldpack_id)
                ),
            )
        except RequestAlreadyRunning as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "status": "running",
                    "request_id": exc.request_id,
                    "idempotency_key": exc.idempotency_key,
                    "message": "request is already running",
                },
            ) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ProviderRateLimitError as exc:
            raise HTTPException(status_code=429, detail=exc.public_detail()) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        message = response.get("choices", [{}])[0].get("message", {"role": "assistant", "content": ""})
        return {
            "party_id": party_id,
            "state_version": party_state_store.current_version(),
            "message": message,
            "raw": response,
        }

    def require_showroom_visitor(request: Request) -> str:
        visitor_id = showroom_store.visitor_id(request.cookies.get(settings.showroom_visitor_cookie_name))
        if not visitor_id:
            raise HTTPException(status_code=404, detail="anonymous showroom session not found")
        return visitor_id

    @app.get("/api/showroom/scenarios")
    def public_showroom_scenarios() -> dict[str, Any]:
        return {"scenarios": showroom_store.list_scenarios(public_only=True)}

    @app.get("/api/showroom/scenarios/{scenario_id}/leaderboard")
    def public_showroom_leaderboard(scenario_id: str, limit: int = 50) -> dict[str, Any]:
        try:
            return showroom_store.leaderboard(scenario_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/showroom/scenarios/{scenario_id}/cover")
    def public_showroom_cover(scenario_id: str) -> FileResponse:
        try:
            path, mime_type = showroom_store.cover(scenario_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type=mime_type, headers={"Cache-Control": "public, max-age=3600"})

    @app.post("/api/showroom/scenarios/{scenario_id}/runs")
    def create_showroom_run(
        http_request: Request,
        response: Response,
        scenario_id: str,
        payload: ShowroomRunCreate,
    ) -> dict[str, Any]:
        visitor_id, new_token = showroom_store.ensure_visitor(
            http_request.cookies.get(settings.showroom_visitor_cookie_name)
        )
        if new_token:
            response.set_cookie(
                settings.showroom_visitor_cookie_name,
                new_token,
                max_age=settings.showroom_visitor_ttl_seconds,
                httponly=True,
                secure=settings.auth_cookie_secure,
                samesite="lax",
                path="/",
            )
        try:
            run = showroom_store.create_run(scenario_id, visitor_id, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"run": run}

    @app.get("/api/showroom/runs")
    def list_showroom_runs(request: Request) -> dict[str, Any]:
        visitor_id = showroom_store.visitor_id(request.cookies.get(settings.showroom_visitor_cookie_name))
        return {"runs": showroom_store.list_runs(visitor_id) if visitor_id else []}

    @app.get("/api/showroom/runs/{run_id}")
    def get_showroom_run(request: Request, run_id: str) -> dict[str, Any]:
        visitor_id = require_showroom_visitor(request)
        try:
            return {"run": showroom_store.get_run(run_id, visitor_id)}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/showroom/runs/{run_id}/history")
    def get_showroom_run_history(request: Request, run_id: str, limit: int = 100) -> dict[str, Any]:
        visitor_id = require_showroom_visitor(request)
        try:
            party_id = showroom_store.party_id_for_run(run_id, visitor_id)
            request.state.showroom_party_access = True
            history = get_party_history(request, party_id, limit=max(1, min(limit, 500)))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"run_id": run_id, "turns": history["turns"]}

    @app.put("/api/showroom/runs/{run_id}/turns/{turn_id}/feedback")
    def update_showroom_turn_feedback(
        request: Request,
        run_id: str,
        turn_id: int,
        payload: TurnFeedbackUpdate,
    ) -> dict[str, Any]:
        visitor_id = require_showroom_visitor(request)
        try:
            party_id = showroom_store.party_id_for_run(run_id, visitor_id)
            request.state.showroom_party_access = True
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            feedback = party_state_store.set_turn_feedback(
                turn_id,
                rating=payload.rating or ("positive" if payload.liked else "none"),
                source_ui="showroom",
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"run_id": run_id, "feedback": feedback}

    @app.post("/api/showroom/runs/{run_id}/artifact-events")
    def record_showroom_artifact_event(
        http_request: Request,
        run_id: str,
        payload: TrainingArtifactEventRequest,
    ) -> dict[str, Any]:
        visitor_id = require_showroom_visitor(http_request)
        try:
            party_id = showroom_store.party_id_for_run(run_id, visitor_id)
            http_request.state.showroom_party_access = True
            result = record_party_artifact_event(http_request, party_id, payload)
            showroom_store.touch_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result.pop("party_id", None)
        return {"run_id": run_id, **result}

    @app.post("/api/showroom/runs/{run_id}/start")
    async def start_showroom_run(
        http_request: Request,
        run_id: str,
        payload: PartyStartRequest = PartyStartRequest(),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        visitor_id = require_showroom_visitor(http_request)
        try:
            party_id = showroom_store.party_id_for_run(run_id, visitor_id)
            http_request.state.showroom_party_access = True
            result = await start_party(
                http_request,
                party_id,
                payload,
                authorization=None,
                x_request_id=x_request_id,
            )
            showroom_store.touch_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result.pop("party_id", None)
        return {"run_id": run_id, **result}

    @app.post("/api/showroom/runs/{run_id}/messages")
    async def showroom_run_message(
        http_request: Request,
        run_id: str,
        payload: PartyMessageRequest,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        visitor_id = require_showroom_visitor(http_request)
        try:
            party_id = showroom_store.party_id_for_run(run_id, visitor_id)
            http_request.state.showroom_party_access = True
            result = await party_message(
                http_request,
                party_id,
                payload,
                authorization=None,
                x_request_id=x_request_id,
            )
            showroom_store.touch_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result.pop("party_id", None)
        return {"run_id": run_id, **result}

    @app.get("/api/admin/showroom/scenarios")
    def admin_showroom_scenarios(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {"scenarios": showroom_store.list_scenarios(public_only=False)}

    @app.post("/api/admin/showroom/scenarios")
    def admin_create_showroom_scenario(
        request: Request,
        payload: ShowroomScenarioCreate,
    ) -> dict[str, Any]:
        admin = require_admin(request)
        try:
            scenario = showroom_store.create_scenario(payload, created_by=admin.id if admin else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"scenario": scenario}

    @app.patch("/api/admin/showroom/scenarios/{scenario_id}")
    def admin_update_showroom_scenario(
        request: Request,
        scenario_id: str,
        payload: ShowroomScenarioUpdate,
    ) -> dict[str, Any]:
        require_admin(request)
        try:
            scenario = showroom_store.update_scenario(
                scenario_id,
                payload.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"scenario": scenario}

    @app.put("/api/admin/showroom/scenarios/{scenario_id}/cover")
    async def admin_upload_showroom_cover(request: Request, scenario_id: str) -> dict[str, Any]:
        require_admin(request)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > settings.showroom_cover_max_bytes:
                    raise HTTPException(status_code=413, detail="cover image is too large")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid content length") from exc
        data = await request.body()
        try:
            scenario = showroom_store.save_cover(
                scenario_id,
                request.headers.get("content-type", "application/octet-stream"),
                data,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"scenario": scenario}

    @app.delete("/api/admin/showroom/scenarios/{scenario_id}/cover")
    def admin_delete_showroom_cover(request: Request, scenario_id: str) -> dict[str, Any]:
        require_admin(request)
        try:
            scenario = showroom_store.delete_cover(scenario_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"scenario": scenario}

    @app.get("/api/admin/autotests/models")
    def admin_autotest_models(request: Request) -> dict[str, Any]:
        require_admin(request)
        party_store.settings = settings_with_provider_key(settings)
        profiles = party_store.list_autotest_model_profiles()
        return {"model_profiles": [profile.model_dump(mode="json") for profile in profiles]}

    @app.get("/api/admin/autotests")
    def admin_list_autotests(
        request: Request,
        source_party_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        admin = require_admin(request)
        if source_party_id:
            try:
                party_store.get_party(source_party_id, owner_user_id=admin.id if admin else None)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"runs": party_store.list_autotest_runs(limit=limit, source_party_id=source_party_id)}

    @app.post("/api/admin/autotests")
    async def admin_create_autotest(
        http_request: Request,
        payload: AutoTestCreate,
    ) -> dict[str, Any]:
        admin = require_admin(http_request)
        owner_id = admin.id if admin else None
        try:
            source_party = party_store.get_party(payload.source_party_id, owner_user_id=owner_id)
            supported_profiles = {profile.id: profile for profile in party_store.list_autotest_model_profiles()}
            player_profile = supported_profiles.get(payload.player_model_profile_id)
            if player_profile is None:
                raise ValueError("auto-player model must be an available OpenRouter or Local Gemma profile")
            source_store = party_store.store_for_party(source_party.id, owner_user_id=owner_id)
            if source_store.has_running_turn_request():
                raise ValueError("wait for the current party turn to finish before creating an auto-test branch")
            label = f"Автотест · {time.strftime('%Y-%m-%d %H:%M:%S')} · {payload.turn_count} ходов"
            checkpoint = source_store.create_memory_checkpoint(label)
            branch = party_store.create_party_branch(
                party_id=source_party.id,
                checkpoint_id=int(checkpoint["id"]),
                label=label,
                branch_type="autotest",
                owner_user_id=owner_id,
            )
            source_store.audit(
                "autotest_branch_created",
                {"branch_id": branch["id"], "checkpoint_id": checkpoint["id"], "requested_turns": payload.turn_count},
            )
            run = party_store.create_autotest_run(
                owner_user_id=owner_id,
                source_party_id=source_party.id,
                branch_id=branch["id"],
                checkpoint_id=int(checkpoint["id"]),
                player_model_profile_id=player_profile.id,
                player_prompt=payload.player_prompt,
                requested_turns=payload.turn_count,
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        schedule_autotest(run["id"])
        checkpoint_summary = {key: value for key, value in checkpoint.items() if key != "state"}
        return {"run": run, "branch": branch, "checkpoint": checkpoint_summary}

    @app.post("/api/admin/autotests/{run_id}/stop")
    def admin_stop_autotest(request: Request, run_id: str) -> dict[str, Any]:
        require_admin(request)
        try:
            run = party_store.request_autotest_stop(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"run": run}

    @app.post("/api/parties/{party_id}/checks")
    async def party_check(
        http_request: Request,
        party_id: str,
        request: PartyCheckRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if party.scenario_type != "rp":
            raise HTTPException(status_code=400, detail="Manual skill checks are available only for RP parties")
        command = check_command(request)
        return await party_message(
            http_request,
            party_id,
            PartyMessageRequest(content=command),
            authorization=authorization,
            x_request_id=None,
        )

    @app.post("/api/parties/{party_id}/world/instruct")
    async def party_world_instruct(
        http_request: Request,
        party_id: str,
        request: WorldInstructionRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            party_settings = runtime_settings_for_party(party)
            world = Adjudicator(party_settings, party_state_store).world
            draft = await world.draft_instruction(request.instruction, authorization, use_llm=request.use_llm)
            party_state_store.create_patch_proposal(draft.patch)
            candidate = party_state_store.preview_patch(draft.patch)
            if request.confirm:
                state = party_state_store.apply_pending_patch(draft.proposal_id, reason="party_world_instruction_confirm")
                return {"party_id": party_id, "applied": True, "proposal": draft.model_dump(mode="json"), "state": state}
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (PatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "applied": False,
            "proposal": draft.model_dump(mode="json"),
            "candidate": candidate,
        }

    @app.get("/api/parties/{party_id}/world/proposals")
    def party_world_proposals(request: Request, party_id: str, limit: int = 10) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party_id": party_id, "proposals": party_state_store.pending_patches(limit=limit)}

    @app.post("/api/parties/{party_id}/world/apply")
    def party_world_apply(http_request: Request, party_id: str, request: WorldApplyRequest) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            patch = party_state_store.get_pending_patch(request.proposal_id)
            if not request.confirm:
                return {"party_id": party_id, "applied": False, "would_apply": patch.model_dump(mode="json")}
            state = party_state_store.apply_pending_patch(request.proposal_id, reason="party_world_instruction_apply")
        except (PatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, "applied": True, "state": state}

    @app.post("/api/parties/{party_id}/world/discard")
    def party_world_discard(http_request: Request, party_id: str, request: WorldApplyRequest) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            proposal_id = party_state_store.discard_pending_patch(request.proposal_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, "discarded": True, "proposal_id": proposal_id}

    @app.post("/api/parties/{party_id}/rollback")
    async def party_rollback(party_id: str, request: Request) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if request.headers.get("content-length") not in {None, "0"}:
            body = await request.json()
        target_version = body.get("target_version")
        try:
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            state = party_state_store.rollback(int(target_version) if target_version is not None else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, "rolled_back": True, "state": state}

    @app.post("/api/state/patch/preview")
    def preview_patch(request: Request, envelope: PatchEnvelope) -> dict[str, Any]:
        require_admin(request)
        try:
            candidate = store.preview_patch(envelope.patch)
        except PatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"candidate": candidate, "would_apply": envelope.patch.model_dump(mode="json")}

    @app.post("/api/state/patch/apply")
    def apply_patch(request: Request, envelope: PatchEnvelope) -> dict[str, Any]:
        require_admin(request)
        try:
            if not envelope.confirm:
                return preview_patch(request, envelope)
            state = store.apply_state_patch(envelope.patch, reason="api_patch_apply")
        except (PatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"applied": True, "state": state}

    @app.get("/api/world/proposals")
    def world_proposals(request: Request, limit: int = 10) -> dict[str, Any]:
        require_admin(request)
        return {"campaign_id": settings.campaign_id, "proposals": store.pending_patches(limit=limit)}

    @app.post("/api/world/instruct")
    async def world_instruct(
        http_request: Request,
        request: WorldInstructionRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(http_request)
        try:
            draft = await Adjudicator(settings_with_provider_key(settings), store).world.draft_instruction(
                request.instruction,
                authorization,
                use_llm=request.use_llm,
            )
            store.create_patch_proposal(draft.patch)
            candidate = store.preview_patch(draft.patch)
            if request.confirm:
                state = store.apply_pending_patch(draft.proposal_id, reason="world_instruction_api_confirm")
                return {"applied": True, "proposal": draft.model_dump(mode="json"), "state": state}
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (PatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "applied": False,
            "proposal": draft.model_dump(mode="json"),
            "candidate": candidate,
            "apply_command": f"/world apply {draft.proposal_id}",
            "discard_command": f"/world discard {draft.proposal_id}",
        }

    @app.post("/api/world/apply")
    def world_apply(http_request: Request, request: WorldApplyRequest) -> dict[str, Any]:
        require_admin(http_request)
        try:
            patch = store.get_pending_patch(request.proposal_id)
            if not request.confirm:
                return {"applied": False, "would_apply": patch.model_dump(mode="json")}
            state = store.apply_pending_patch(request.proposal_id, reason="world_instruction_api_apply")
        except (PatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"applied": True, "state": state}

    @app.post("/api/turn/rollback")
    async def rollback(request: Request) -> dict[str, Any]:
        require_admin(request)
        body: dict[str, Any] = {}
        if request.headers.get("content-length") not in {None, "0"}:
            body = await request.json()
        target_version = body.get("target_version")
        try:
            state = store.rollback(int(target_version) if target_version is not None else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"rolled_back": True, "state": state}

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: ChatCompletionRequest,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        request_id = x_request_id or f"req_{uuid.uuid4().hex}"
        try:
            response = await Adjudicator(settings_with_provider_key(settings), store).handle_chat(request, authorization, idempotency_key, request_id)
        except RequestAlreadyRunning as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "status": "running",
                    "request_id": exc.request_id,
                    "idempotency_key": exc.idempotency_key,
                    "message": "request is already running",
                },
            ) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if request.stream:
            return StreamingResponse(stream_openai_response(response), media_type="text/event-stream")
        return JSONResponse(response)

    return app


def settings_for_party(settings: Settings, party: Any) -> Settings:
    model_profile = party.model_profile
    party_cache_id = (
        getattr(party, "id", "")
        or getattr(party, "state_campaign_id", "")
        or party.worldpack_id
    )
    prompt_values = {
        "scenario_type": getattr(party, "scenario_type", "rp"),
        "campaign_id": party.worldpack_id,
        "world_system_prompt": worldpack_prompt_text(party, "gm_system"),
        "world_authors_note": worldpack_prompt_text(party, "authors_note"),
        "prompt_cache_session_id": f"rp-party:{party_cache_id}",
    }
    if model_profile is None:
        return replace(settings, **prompt_values)
    configured = settings_for_model_profile(settings, model_profile, f"rp-party:{party_cache_id}")
    return replace(configured, **prompt_values)


def settings_for_model_profile(settings: Settings, model_profile: Any, cache_session_id: str) -> Settings:
    provider = normalize_provider(model_profile.provider)
    return replace(
        settings,
        llm_provider=provider,
        nvidia_api_base=model_profile.base_url,
        narrative_model=model_profile.model,
        intent_model=model_profile.model,
        validator_model=model_profile.model,
        nvidia_fallback_models=settings.nvidia_fallback_models if provider == "nvidia" else (),
        nvidia_disabled_models=settings.nvidia_disabled_models if provider == "nvidia" else (),
        model_attempt_timeout_seconds=(
            settings.local_llm_timeout_seconds if provider == "local" else settings.model_attempt_timeout_seconds
        ),
        prompt_cache_session_id=cache_session_id,
        party_context_limit_tokens=min(
            model_context_limit_tokens(model_profile) or settings.party_context_max_tokens,
            settings.party_context_max_tokens,
        ),
    )


def worldpack_prompt_text(party: Any, file_key: str) -> str:
    world = getattr(party, "worldpack", None)
    if world is None or not isinstance(world.manifest, dict):
        return ""
    files = world.manifest.get("files")
    relative_path = files.get(file_key) if isinstance(files, dict) else None
    if not isinstance(relative_path, str) or not relative_path.strip():
        return ""
    root = Path(world.manifest_path).resolve().parent
    target = (root / relative_path).resolve()
    if target != root and root not in target.parents:
        return ""
    try:
        return target.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def party_start_prompt(party_store: PartyStore, party: Any) -> str:
    world = party.worldpack or party_store.get_worldpack(party.worldpack_id)
    character = party.player_character or party_store.get_player_character(party.player_character_id)
    manifest = world.manifest if isinstance(world.manifest, dict) else {}
    opening_scene = party_store.opening_scene_text(world)
    premise = str(manifest.get("premise") or world.premise or manifest.get("prompt") or "").strip()
    player_role = str(manifest.get("player_role") or "").strip()
    opening_block = opening_scene or (
        "No dedicated opening-scene file is available. Synthesize the first scene from the current state, "
        "world premise, and player character. End with a concrete player-facing choice."
    )
    mode_instruction = {
        "novel": (
            "Write the opening passage of a collaborative novel in Russian. Do not use dice, checks, skills, "
            "game-system labels, or a menu of actions. Establish character voice, relationships, atmosphere, and an "
            "immediate dramatic opening while leaving the player character's decisions to the player."
        ),
        "training": (
            "Write the first turn of a deterministic training scenario in Russian. Follow the world opening template, "
            "schedule, and formatting literally. Do not reveal lessons, hints, safety judgments, scoring, or hidden "
            "scenario structure. Do not choose an action for the player."
        ),
        "rp": (
            "Write the first GM message for a roleplaying party in Russian. Establish a playable situation without "
            "rolling a check or resolving a player choice, and end with a concrete opening for player action."
        ),
    }.get(party.scenario_type, "Write the opening scene in Russian while preserving player agency.")
    return "\n\n".join(
        [
            "START_PARTY_OPENING_SCENE",
            "This is an internal Light GUI auto-start request, not a player action.",
            f"Selected scenario type: {party.scenario_type}",
            mode_instruction,
            "Use second person where appropriate and preserve player agency.",
            "Do not expose service instructions, JSON, model policy, or the AUTO_START marker.",
            f"World title: {world.title}",
            f"World premise: {premise or 'use the current authoritative state'}",
            f"Player character: {character.name}",
            f"Player role: {character.description or player_role or 'active player character'}",
            f"Opening scene source:\n{opening_block}",
        ]
    )


def party_start_outcome(party_id: str, scenario_type: str = "rp") -> Outcome:
    result = {
        "novel": "narrative_continuation",
        "training": "deterministic_resolution",
    }.get(scenario_type, "success")
    return Outcome(
        check_id=f"party_start:{party_id}",
        action_type="feasibility",
        actor="system",
        target="opening_scene",
        result=result,
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        blocked_reasons=[],
        consequences=["Initial scene is introduced; no player decision has been resolved yet."],
        forbidden_reinterpretations=[
            "Do not treat the start request as a player action.",
            "Do not change state or grant resources through the opening narration.",
        ],
        authoritative_block=(
            "AUTHORITATIVE_OUTCOME: This is the start of a new party. Present the opening scene only. "
            "No mechanical check was rolled, and no player action has been resolved."
        ),
    )


def party_start_state_patch(
    state: dict[str, Any],
    party_id: str,
    worldpack_id: str | None = None,
    scenario_type: str = "rp",
) -> StatePatch | None:
    if scenario_type != "training":
        return None
    if not is_awareness_campaign(state, worldpack_id):
        return None
    turn = 1
    window = awareness_turn_window(turn, state, worldpack_id)
    if not window:
        return None
    resources = state.get("player", {}).get("resources", {})
    operations = [
        resource_value_patch(
            resources,
            "current-turn-window",
            window,
            "Marks Awareness opening scene as the first scheduled message window.",
            turn,
        ),
        resource_value_patch(
            resources,
            "turns-remaining",
            awareness_turns_remaining(turn),
            "Tracks remaining Awareness message turns after opening.",
            turn,
        ),
        PatchOperation(
            op="add",
            path="/timeline/-",
            value={
                "turn": turn,
                "event": f"Ход 1 Awareness открыт: {window}.",
                "confirmed": True,
                "participants": ["player"],
            },
            reason="Records the first Awareness turn opened by party start.",
            turn=turn,
        ),
    ]
    return StatePatch(turn=turn, check_id=f"party_start_state:{party_id}", source="party-start", patch=operations)


def party_start_narrative_state(state: dict[str, Any], patch: StatePatch | None) -> dict[str, Any]:
    if not patch:
        return state
    cloned = copy.deepcopy(state)
    meta = cloned.setdefault("meta", {})
    meta["turn"] = max(int(meta.get("turn", 0) or 0), patch.turn)
    resources = cloned.setdefault("player", {}).setdefault("resources", {})
    for operation in patch.patch:
        prefix = "/player/resources/"
        if operation.path.startswith(prefix):
            resources[operation.path.removeprefix(prefix)] = operation.value
    return cloned


def resource_value_patch(resources: Any, resource_id: str, value: Any, reason: str, turn: int) -> PatchOperation:
    op = "replace" if isinstance(resources, dict) and resource_id in resources else "add"
    return PatchOperation(
        op=op,
        path=f"/player/resources/{resource_id}",
        value=value,
        reason=reason,
        turn=turn,
    )


def party_chat_request(
    store: StateStore,
    model: str,
    request: PartyMessageRequest,
    settings: Settings,
) -> ChatCompletionRequest:
    memory = store.latest_memory_coverage()
    covered_through = int(memory["to_turn_id"]) if memory else 0
    turns = store.turns_for_memory(after_turn_id=covered_through)
    current_message_tokens = estimate_tokens(request.content)
    history_budget = max(settings.effective_party_history_token_budget - current_message_tokens, 0)
    overflow_turns, raw_turns = split_turns_by_token_budget(turns, history_budget)
    messages: list[ChatMessage] = []
    lore_block = party_lore_cards_block(
        store.lore_cards_for_prompt(
            request.content,
            limit=settings.party_lore_card_prompt_limit,
            max_chars=settings.party_lore_card_prompt_max_chars,
        )
    )
    if lore_block:
        messages.append(ChatMessage(role="system", content=lore_block))
    fallback_block = uncompacted_archive_fallback_block(
        overflow_turns,
        settings.party_memory_fallback_max_chars,
    )
    if fallback_block:
        messages.append(ChatMessage(role="system", content=fallback_block))
    for turn in raw_turns:
        messages.append(ChatMessage(role="user", content=turn["player_message"]))
        messages.append(ChatMessage(role="assistant", content=turn["narrative_response"]))
    if settings.party_memory_retrieval_enabled:
        retrieved = store.search_archived_turns(
            request.content,
            through_turn_id=covered_through,
            limit=settings.party_memory_retrieval_limit,
        )
        retrieval_block = archived_memory_retrieval_block(retrieved, settings.party_memory_retrieval_max_chars)
        if retrieval_block:
            messages.append(ChatMessage(role="system", content=retrieval_block))
    messages.append(ChatMessage(role="user", content=request.content))
    return ChatCompletionRequest(
        model=model,
        messages=messages,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        stream=False,
    )


async def generate_character_edit(
    settings: Settings,
    store: StateStore,
    request: PartyCharacterStateEditRequest,
    authorization: str | None,
    request_id: str,
) -> PartyCharacterStateEditRequest:
    state = store.get_state()
    runtime = service_model_settings(settings)
    if runtime.nvidia_api_base.startswith("mock://"):
        return mock_generated_character_edit(settings, state, request)

    headers = outbound_headers(runtime, None)

    world = WorldInstructor(settings, store)
    payload: dict[str, Any] = {
        "model": runtime.intent_model,
        "temperature": 0.35,
        "max_tokens": 1200,
        "stream": False,
        "messages": [
            {"role": "system", "content": character_generation_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "input_fields": request.model_dump(mode="json", exclude_none=True),
                        "state_excerpt": character_generation_state_excerpt(state, request),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    timeout = httpx.Timeout(runtime.model_attempt_timeout_seconds, connect=15.0)
    attempts = world.model_attempts(runtime.intent_model, runtime)
    last_timeout: httpx.TimeoutException | None = None
    last_status: httpx.HTTPStatusError | None = None
    last_parse_error: ValueError | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for index, model in enumerate(attempts):
            payload["model"] = model
            started = time.perf_counter()
            logger.info(
                "character_llm_attempt_start request_id=%s model=%s attempt=%s/%s timeout_seconds=%s",
                request_id,
                model,
                index + 1,
                len(attempts),
                runtime.model_attempt_timeout_seconds,
            )
            try:
                response = await client.post(
                    f"{runtime.nvidia_api_base.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                last_timeout = exc
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                logger.warning(
                    "character_llm_attempt_timeout request_id=%s model=%s attempt=%s/%s elapsed_ms=%s fallback=%s",
                    request_id,
                    model,
                    index + 1,
                    len(attempts),
                    elapsed_ms,
                    index < len(attempts) - 1,
                )
                if index < len(attempts) - 1:
                    continue
                raise
            if response.status_code == 429:
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                logger.warning(
                    "character_llm_attempt_rate_limited request_id=%s model=%s elapsed_ms=%s",
                    request_id,
                    model,
                    elapsed_ms,
                )
                raise RuntimeError(f"{runtime.llm_provider} API returned 429 rate limit")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_status = exc
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                logger.warning(
                    "character_llm_attempt_http_error request_id=%s model=%s status=%s elapsed_ms=%s fallback=%s",
                    request_id,
                    model,
                    response.status_code,
                    elapsed_ms,
                    index < len(attempts) - 1,
                )
                if index < len(attempts) - 1 and response.status_code in {400, 404, 408, 500, 502, 503, 504}:
                    continue
                raise
            try:
                data = world.extract_json(response_text(response.json()))
            except ValueError as exc:
                last_parse_error = exc
                logger.warning(
                    "character_llm_attempt_parse_error request_id=%s model=%s attempt=%s/%s fallback=%s error=%s",
                    request_id,
                    model,
                    index + 1,
                    len(attempts),
                    index < len(attempts) - 1,
                    exc,
                )
                if index < len(attempts) - 1:
                    continue
                raise RuntimeError("LLM did not return character JSON") from exc
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "character_llm_attempt_success request_id=%s model=%s elapsed_ms=%s fallback_used=%s",
                request_id,
                model,
                elapsed_ms,
                index > 0 or model != runtime.intent_model,
            )
            return coerce_generated_character_edit(data, request, state)
    if last_status:
        raise last_status
    if last_timeout:
        raise last_timeout
    if last_parse_error:
        raise RuntimeError("LLM did not return character JSON") from last_parse_error
    raise RuntimeError(f"No model attempts configured for provider {runtime.llm_provider}")


def character_generation_prompt() -> str:
    return (
        "Generate exactly one structured tabletop-RP character edit for RP Gateway. "
        "Return only a JSON object, no markdown and no explanations. "
        "Allowed keys: target, character_id, name, status, location, current_goal, attitude_to_player, "
        "loyalty, trust, fear, knowledge, obligations, hard_constraints, secrets. "
        "knowledge, obligations, hard_constraints, and secrets may be arrays of short strings. "
        "trust must be an integer from -10 to 10; fear must be an integer from 0 to 10. "
        "Preserve every non-empty user-provided field exactly; only fill missing fields. "
        "Use the user's language for generated prose. Do not invent resolved plot outcomes, only character traits, "
        "social posture, knowledge, duties, and constraints that fit the current state."
    )


def character_generation_state_excerpt(state: dict[str, Any], request: PartyCharacterStateEditRequest) -> dict[str, Any]:
    character_id = stable_character_id(request.character_id or request.name or "")
    characters = state.get("characters", {}) if isinstance(state.get("characters"), dict) else {}
    return {
        "turn": state.get("meta", {}).get("turn"),
        "player": state.get("player", {}),
        "target_character_id": character_id,
        "existing_character": characters.get(character_id),
        "nearby_characters": [
            {"id": key, "name": value.get("name") if isinstance(value, dict) else key}
            for key, value in list(characters.items())[:20]
        ],
        "world_constraints": state.get("world_constraints", [])[:20],
        "timeline_tail": state.get("timeline", [])[-8:],
    }


def coerce_generated_character_edit(
    data: dict[str, Any],
    source: PartyCharacterStateEditRequest,
    state: dict[str, Any],
) -> PartyCharacterStateEditRequest:
    payload = data.get("character") if isinstance(data.get("character"), dict) else data
    normalized: dict[str, Any] = {
        "target": source.target,
        "character_id": normalize_optional_string(payload.get("character_id")),
        "name": normalize_optional_string(payload.get("name")),
        "status": normalize_optional_string(payload.get("status")),
        "location": normalize_optional_string(payload.get("location")),
        "current_goal": normalize_optional_string(payload.get("current_goal")),
        "attitude_to_player": normalize_optional_string(payload.get("attitude_to_player")),
        "loyalty": normalize_optional_string(payload.get("loyalty")),
        "trust": clamp_optional_int(payload.get("trust"), -10, 10),
        "fear": clamp_optional_int(payload.get("fear"), 0, 10),
        "knowledge": normalize_line_field(payload.get("knowledge")),
        "obligations": normalize_line_field(payload.get("obligations")),
        "hard_constraints": normalize_line_field(payload.get("hard_constraints")),
        "secrets": normalize_line_field(payload.get("secrets")),
        "confirm": True,
    }
    for field in [
        "character_id",
        "name",
        "status",
        "location",
        "current_goal",
        "attitude_to_player",
        "loyalty",
        "trust",
        "fear",
        "knowledge",
        "obligations",
        "hard_constraints",
        "secrets",
    ]:
        value = getattr(source, field)
        if value is not None and (not isinstance(value, str) or value.strip()):
            normalized[field] = value
    if source.target == "player":
        normalized["character_id"] = None
    elif not normalized.get("character_id") and normalized.get("name"):
        normalized["character_id"] = stable_character_id(str(normalized["name"]))
    if not normalized.get("location") and source.target == "player":
        normalized["location"] = normalize_optional_string(state.get("player", {}).get("location"))
    return PartyCharacterStateEditRequest.model_validate(normalized)


def mock_generated_character_edit(
    settings: Settings,
    state: dict[str, Any],
    source: PartyCharacterStateEditRequest,
) -> PartyCharacterStateEditRequest:
    mode = settings.nvidia_api_base.removeprefix("mock://")
    if mode == "timeout":
        raise httpx.TimeoutException("mock timeout")
    if mode == "http-503":
        request = httpx.Request("POST", "https://mock.nvidia.local/chat/completions")
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("mock provider unavailable", request=request, response=response)
    if mode == "rate-limit":
        raise RuntimeError(f"{settings.llm_provider} API returned 429 rate limit")
    name = source.name or source.character_id or ("Игрок" if source.target == "player" else "NPC")
    default_location = "unknown" if source.target == "npc" else state.get("player", {}).get("location") or "unknown"
    generated = {
        "target": source.target,
        "character_id": source.character_id or (stable_character_id(name) if source.target == "npc" else None),
        "name": name,
        "status": source.status or ("active" if source.target == "player" else "alive"),
        "location": source.location or default_location,
        "current_goal": source.current_goal or f"держать свою роль в сцене как {name}",
        "attitude_to_player": source.attitude_to_player or ("самоконтроль" if source.target == "player" else "нейтральное любопытство"),
        "loyalty": source.loyalty or "локальная рутина",
        "trust": source.trust if source.trust is not None else 0,
        "fear": source.fear if source.fear is not None else 0,
        "knowledge": source.knowledge or f"{name} замечает детали текущей сцены.",
        "obligations": source.obligations or f"{name} не нарушает явные ограничения мира.",
        "hard_constraints": source.hard_constraints or "",
        "secrets": source.secrets or "",
        "confirm": True,
    }
    return PartyCharacterStateEditRequest.model_validate(generated)


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_line_field(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        lines = [str(item).strip(" -•\t") for item in value if str(item).strip(" -•\t")]
        return "\n".join(lines) or None
    return normalize_optional_string(value)


def clamp_optional_int(value: Any, lower: int, upper: int) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(lower, min(upper, number))


def character_state_patch(state: dict[str, Any], request: PartyCharacterStateEditRequest) -> StatePatch:
    turn = int(state.get("meta", {}).get("turn", 0)) + 1
    proposal_id = f"character-{uuid.uuid4().hex[:12]}"
    operations: list[PatchOperation] = []
    if request.target == "player":
        for field, value in [
            ("name", request.name),
            ("status", request.status),
            ("location", request.location),
            ("description", request.current_goal),
        ]:
            add_value_patch(operations, state, f"/player/{field}", value, turn, f"Updates player {field} from Light GUI character editor.")
        if request.knowledge is not None:
            add_value_patch(
                operations,
                state,
                "/player/known_world_facts",
                split_editor_lines(request.knowledge),
                turn,
                "Updates player known facts from Light GUI character editor.",
            )
        if request.obligations is not None:
            add_value_patch(
                operations,
                state,
                "/player/constraints",
                split_editor_lines(request.obligations),
                turn,
                "Updates player constraints from Light GUI character editor.",
            )
        participant = "player"
    else:
        character_id = stable_character_id(request.character_id or request.name or "")
        if not character_id:
            raise ValueError("character_id or name is required for NPC edits")
        characters = state.get("characters", {}) if isinstance(state.get("characters"), dict) else {}
        participant = character_id
        base_path = f"/characters/{pointer_escape(character_id)}"
        if character_id not in characters:
            operations.append(
                PatchOperation(
                    op="add",
                    path=base_path,
                    value={
                        "name": request.name or character_id,
                        "status": request.status or "alive",
                        "location": request.location or "unknown",
                        "attitude_to_player": request.attitude_to_player or "",
                        "trust": request.trust if request.trust is not None else 0,
                        "fear": request.fear if request.fear is not None else 0,
                        "loyalty": request.loyalty or "unknown",
                        "current_goal": request.current_goal or "",
                        "knowledge": split_editor_lines(request.knowledge),
                        "secrets": split_editor_lines(request.secrets),
                        "obligations": split_editor_lines(request.obligations),
                        "hard_constraints": split_editor_lines(request.hard_constraints),
                        "last_confirmed_update": turn,
                    },
                    reason="Creates NPC from Light GUI character editor.",
                    turn=turn,
                )
            )
        else:
            for field, value in [
                ("name", request.name),
                ("status", request.status),
                ("location", request.location),
                ("current_goal", request.current_goal),
                ("attitude_to_player", request.attitude_to_player),
                ("loyalty", request.loyalty),
                ("trust", request.trust),
                ("fear", request.fear),
            ]:
                add_value_patch(operations, state, f"{base_path}/{field}", value, turn, f"Updates NPC {field} from Light GUI character editor.")
            for field, value in [
                ("knowledge", split_editor_lines(request.knowledge)),
                ("obligations", split_editor_lines(request.obligations)),
                ("hard_constraints", split_editor_lines(request.hard_constraints)),
                ("secrets", split_editor_lines(request.secrets)),
            ]:
                if value:
                    add_value_patch(operations, state, f"{base_path}/{field}", value, turn, f"Updates NPC {field} from Light GUI character editor.")
            add_value_patch(operations, state, f"{base_path}/last_confirmed_update", turn, turn, "Marks NPC update turn.")
    if not operations:
        raise ValueError("no character fields to update")
    operations.append(
        PatchOperation(
            op="add",
            path="/timeline/-",
            value={"turn": turn, "event": f"Character editor updated {participant}.", "confirmed": True, "participants": [participant]},
            reason="Records Light GUI character edit.",
            turn=turn,
        )
    )
    return StatePatch(turn=turn, check_id=proposal_id, source="character-editor", patch=operations)


def add_value_patch(
    operations: list[PatchOperation],
    state: dict[str, Any],
    path: str,
    value: Any,
    turn: int,
    reason: str,
) -> None:
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    operations.append(
        PatchOperation(
            op="replace" if path_exists(state, path) else "add",
            path=path,
            value=value.strip() if isinstance(value, str) else value,
            reason=reason,
            turn=turn,
        )
    )


def path_exists(document: Any, path: str) -> bool:
    current = document
    for part in [part.replace("~1", "/").replace("~0", "~") for part in path.strip("/").split("/")]:
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return False
    return True


def split_editor_lines(value: str | None) -> list[str]:
    if value is None:
        return []
    return [line.strip(" -•\t") for line in value.splitlines() if line.strip(" -•\t")]


def stable_character_id(value: str) -> str:
    clean = re.sub(r"[^\w\-]+", "-", value.strip().lower(), flags=re.UNICODE).strip("-")
    return clean[:80]


def pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def check_command(request: PartyCheckRequest) -> str:
    parts = [f"/check {request.check_type}", f"skill={request.skill}", f"difficulty={request.difficulty}"]
    if request.target:
        parts.append(f"target={quote_token(request.target)}")
    if request.goal:
        parts.append(f"goal={quote_token(request.goal)}")
    return " ".join(parts)


def quote_token(value: str) -> str:
    if re_safe_token(value):
        return value
    return json.dumps(value, ensure_ascii=False)


def re_safe_token(value: str) -> bool:
    return bool(value) and all(char.isalnum() or char in {"_", "-", "."} for char in value)


async def stream_openai_response(response: dict[str, Any]):
    content = str(response.get("choices", [{}])[0].get("message", {}).get("content", ""))
    chunk = {
        "id": response.get("id"),
        "object": "chat.completion.chunk",
        "created": response.get("created"),
        "model": response.get("model"),
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    for part in split_stream(content):
        chunk["choices"] = [{"index": 0, "delta": {"content": part}, "finish_reason": None}]
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def split_stream(content: str, size: int = 80) -> list[str]:
    if not content:
        return [""]
    return [content[index : index + size] for index in range(0, len(content), size)]
