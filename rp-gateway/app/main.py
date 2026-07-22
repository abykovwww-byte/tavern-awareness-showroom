"""FastAPI entrypoint for RP Gateway."""

from __future__ import annotations

import copy
import json
import logging
import re
import time
import uuid
from dataclasses import replace
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import Settings, get_settings
from app.core.json_patch import PatchError
from app.models.schemas import (
    ChatCompletionRequest,
    ChatMessage,
    HealthResponse,
    LoginRequest,
    Outcome,
    PatchEnvelope,
    PatchOperation,
    PartyCharacterStateEditRequest,
    PartyCheckRequest,
    PartyCreate,
    PartyJournalSummarizeRequest,
    PartyMemorySummarizeRequest,
    PartyMessageRequest,
    PartyModelUpdate,
    PartyPromptPreviewRequest,
    PartyStartRequest,
    PlayerCharacterCreate,
    PlayerCharacterDraftRequest,
    ProviderApiKeyCreate,
    ProviderApiKeyUpdate,
    UserCreate,
    UserDeleteRequest,
    UserPasswordUpdate,
    UserStatusUpdate,
    WorldPromptCreate,
    WorldApplyRequest,
    WorldInstructionRequest,
    StatePatch,
)
from app.services.adjudicator import Adjudicator, RequestAlreadyRunning
from app.services.auth_store import AuthStore, AuthUser
from app.services.character_view import party_character_sheets
from app.services.context_estimator import estimate_party_context
from app.services.journal import JournalBuilder
from app.services.memory import MemorySummarizer
from app.services.narrative import NarrativeClient, response_text
from app.services.party_store import PartyStore
from app.services.prompt_tools import PromptInspector
from app.services.rule_engine import awareness_turn_window, awareness_turns_remaining
from app.services.state_store import StateStore
from app.services.validator import OutputValidator
from app.services.world_instructor import WorldInstructor


AUTO_START_HISTORY_MESSAGE = "[AUTO_START] Старт партии"
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    store = StateStore(settings.sqlite_path, settings.campaign_id, settings.world_state_path)
    auth_store = AuthStore(settings)
    party_store = PartyStore(settings, default_owner_user_id=auth_store.default_owner_user_id())

    app = FastAPI(title="RP Gateway", version="0.5.0")
    app.state.settings = settings
    app.state.store = store
    app.state.auth_store = auth_store
    app.state.adjudicator = Adjudicator(settings, store)
    app.state.party_store = party_store

    def settings_with_provider_key(base: Settings) -> Settings:
        secret = auth_store.default_provider_secret(base.nvidia_api_base)
        if secret:
            return replace(base, nvidia_api_key=secret)
        return base

    def runtime_settings_for_party(party: Any) -> Settings:
        return settings_with_provider_key(settings_for_party(settings, party))

    def current_user(request: Request) -> AuthUser | None:
        if not settings.auth_enabled:
            return None
        user = getattr(request.state, "user", None)
        if not isinstance(user, AuthUser):
            raise HTTPException(status_code=401, detail="authentication required")
        return user

    def owner_user_id(request: Request) -> str | None:
        user = current_user(request)
        return user.id if user else None

    def require_admin(request: Request) -> AuthUser | None:
        user = current_user(request)
        if settings.auth_enabled and (not user or not user.is_admin):
            raise HTTPException(status_code=403, detail="admin role required")
        return user

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if not settings.auth_enabled or not request.url.path.startswith("/api/"):
            return await call_next(request)
        if request.url.path.startswith("/api/auth/"):
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

    @app.get("/api/admin/api-keys")
    def admin_list_api_keys(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {"api_keys": [key.public_dict() for key in auth_store.list_provider_api_keys()]}

    @app.post("/api/admin/api-keys")
    def admin_create_api_key(request: Request, payload: ProviderApiKeyCreate) -> dict[str, Any]:
        require_admin(request)
        try:
            key = auth_store.create_provider_api_key(
                label=payload.label,
                secret_value=payload.api_key,
                provider=payload.provider,
                base_url=payload.base_url,
                is_default=payload.is_default,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"api_key": key.public_dict()}

    @app.patch("/api/admin/api-keys/{key_id}")
    def admin_update_api_key(request: Request, key_id: str, payload: ProviderApiKeyUpdate) -> dict[str, Any]:
        require_admin(request)
        try:
            key = auth_store.update_provider_api_key(
                key_id,
                label=payload.label,
                secret_value=payload.api_key,
                is_default=payload.is_default,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"api_key": key.public_dict()}

    @app.delete("/api/admin/api-keys/{key_id}")
    def admin_delete_api_key(request: Request, key_id: str) -> dict[str, Any]:
        require_admin(request)
        try:
            auth_store.delete_provider_api_key(key_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": True, "api_key_id": key_id}

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
        return {"worldpacks": [pack.model_dump(mode="json") for pack in party_store.list_worldpacks(owner_user_id=owner_user_id(request))]}

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
            pack = party_store.get_worldpack(worldpack_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"worldpack": pack.model_dump(mode="json")}

    @app.get("/api/worldpacks/{worldpack_id}/player-templates")
    def player_templates(request: Request, worldpack_id: str) -> dict[str, Any]:
        try:
            templates = party_store.player_templates(worldpack_id, owner_user_id=owner_user_id(request))
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
            pack = party_store.get_worldpack(payload.worldpack_id, owner_user_id=owner_user_id(request))
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

    @app.post("/api/parties/{party_id}/activate")
    def activate_party(request: Request, party_id: str) -> dict[str, Any]:
        try:
            party = party_store.activate_party(party_id, owner_user_id=owner_user_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party": party.model_dump(mode="json")}

    @app.delete("/api/parties/{party_id}")
    def delete_party(request: Request, party_id: str) -> dict[str, Any]:
        try:
            party_store.delete_party(party_id, owner_user_id=owner_user_id(request))
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
        return {
            "party_id": party_id,
            "memory": party_state_store.latest_memory_summary(),
            "summaries": party_state_store.memory_summaries(limit=limit),
            "stats": summarizer.stats(),
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
        deleted = party_state_store.delete_latest_memory_summary()
        return {
            "party_id": party_id,
            "deleted": deleted is not None,
            "deleted_memory": deleted,
            "memory": party_state_store.latest_memory_summary(),
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
                {"request_id": request_id, "character_id": character_id, "model": party_settings.intent_model},
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

    @app.get("/api/parties/{party_id}/journal")
    def get_party_journal(request: Request, party_id: str, limit: int = 8) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            party_settings = runtime_settings_for_party(party)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        journal = JournalBuilder(party_settings, party_state_store)
        return {
            "party_id": party_id,
            "journal": party_state_store.latest_journal_entry(),
            "entries": party_state_store.journal_entries(limit=limit),
            "stats": journal.stats(),
        }

    @app.post("/api/parties/{party_id}/journal/summarize")
    async def summarize_party_journal(
        http_request: Request,
        party_id: str,
        request: PartyJournalSummarizeRequest = PartyJournalSummarizeRequest(),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            party_settings = runtime_settings_for_party(party)
            result = await JournalBuilder(party_settings, party_state_store).summarize(
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

    @app.delete("/api/parties/{party_id}/journal/latest")
    def delete_party_journal_latest(request: Request, party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(request))
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(request))
            party_settings = runtime_settings_for_party(party)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        deleted = party_state_store.delete_latest_journal_entry()
        journal = JournalBuilder(party_settings, party_state_store)
        return {
            "party_id": party_id,
            "deleted": deleted is not None,
            "deleted_journal": deleted,
            "journal": party_state_store.latest_journal_entry(),
            "entries": party_state_store.journal_entries(limit=8),
            "stats": journal.stats(),
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
            start_patch = party_start_state_patch(state, party_id)
            narrative_state = party_start_narrative_state(state, start_patch)
            prompt = party_start_prompt(party_store, party)
            chat_request = ChatCompletionRequest(
                model=model_profile.model,
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stream=False,
            )
            start_outcome = party_start_outcome(party_id)
            memory_summary = party_state_store.latest_memory_summary()
            narrative = NarrativeClient(party_settings)
            prompt_messages = narrative.narrative_messages(
                chat_request,
                narrative_state,
                start_outcome,
                repair_instruction=None,
                memory_summary=memory_summary,
            )
            raw = await narrative.complete(
                chat_request,
                narrative_state,
                start_outcome,
                authorization,
                memory_summary=memory_summary,
                request_id=request_id,
            )
            adjudicator = Adjudicator(party_settings, party_state_store)
            response = adjudicator.normalize_response(raw, model_profile.model)
            text = response_text(response)
            if start_patch:
                validator = OutputValidator()
                validation = validator.validate(text, start_outcome, narrative_state)
                if not validation.valid and party_settings.max_repair_attempts > 0:
                    raw = await narrative.complete(
                        chat_request,
                        narrative_state,
                        start_outcome,
                        authorization,
                        validation.repair_instruction,
                        memory_summary=memory_summary,
                        request_id=request_id,
                    )
                    response = adjudicator.normalize_response(raw, model_profile.model)
                    text = response_text(response)
                    validation = validator.validate(text, start_outcome, narrative_state)
                if not validation.valid:
                    party_state_store.audit(
                        "party_start_validation_failed",
                        {"request_id": request_id, "model": model_profile.model, "violations": validation.violations},
                        request_id,
                    )
                    raise RuntimeError("LLM response failed narrative validation")
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
                party_settings.party_raw_turn_limit,
            )
            response = await Adjudicator(party_settings, party_state_store).handle_chat(
                chat_request,
                authorization,
                request.idempotency_key,
                x_request_id,
                allow_gateway_fallback=False,
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

    @app.post("/api/parties/{party_id}/checks")
    async def party_check(
        http_request: Request,
        party_id: str,
        request: PartyCheckRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
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
    if model_profile is None:
        return replace(settings, campaign_id=party.state_campaign_id)
    return replace(
        settings,
        campaign_id=party.state_campaign_id,
        nvidia_api_base=model_profile.base_url,
        narrative_model=model_profile.model,
        intent_model=model_profile.model,
        validator_model=model_profile.model,
    )


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
    return "\n\n".join(
        [
            "START_PARTY_OPENING_SCENE",
            "This is an internal Light GUI auto-start request, not a player action.",
            "Write the first GM/narrator message for a new roleplay party in Russian.",
            "Use second person, preserve player agency, do not resolve any player choice, and end by asking what the player does.",
            "Do not expose service instructions, JSON, model policy, or the AUTO_START marker.",
            f"World title: {world.title}",
            f"World premise: {premise or 'use the current authoritative state'}",
            f"Player character: {character.name}",
            f"Player role: {character.description or player_role or 'active player character'}",
            f"Opening scene source:\n{opening_block}",
        ]
    )


def party_start_outcome(party_id: str) -> Outcome:
    return Outcome(
        check_id=f"party_start:{party_id}",
        action_type="feasibility",
        actor="system",
        target="opening_scene",
        result="success",
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


def party_start_state_patch(state: dict[str, Any], party_id: str) -> StatePatch | None:
    if state.get("meta", {}).get("campaign_id") != "awareness":
        return None
    turn = 1
    window = awareness_turn_window(turn)
    if not window:
        return None
    resources = state.get("player", {}).get("resources", {})
    operations = [
        resource_value_patch(
            resources,
            "current-turn-window",
            window,
            "Marks Awareness opening scene as the first scheduled half-day.",
            turn,
        ),
        resource_value_patch(
            resources,
            "turns-remaining",
            awareness_turns_remaining(turn),
            "Tracks remaining Awareness half-day turns after opening.",
            turn,
        ),
        PatchOperation(
            op="add",
            path="/timeline/-",
            value={
                "turn": turn,
                "event": "Ход 1 Awareness открыт: понедельник, 10:00-14:00.",
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
    raw_turn_limit: int,
) -> ChatCompletionRequest:
    messages: list[ChatMessage] = []
    for turn in store.turn_history(limit=max(raw_turn_limit, 0)):
        messages.append(ChatMessage(role="user", content=turn["player_message"]))
        messages.append(ChatMessage(role="assistant", content=turn["narrative_response"]))
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
    if settings.nvidia_api_base.startswith("mock://"):
        return mock_generated_character_edit(settings, state, request)

    outbound_authorization = authorization
    if settings.nvidia_api_key:
        outbound_authorization = f"Bearer {settings.nvidia_api_key}"
    if not outbound_authorization:
        raise PermissionError("NVIDIA API key is required in Authorization header or NVIDIA_API_KEY env")

    world = WorldInstructor(settings, store)
    payload: dict[str, Any] = {
        "model": settings.intent_model,
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
    timeout = httpx.Timeout(settings.model_attempt_timeout_seconds, connect=15.0)
    attempts = world.model_attempts(settings.intent_model)
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
                settings.model_attempt_timeout_seconds,
            )
            try:
                response = await client.post(
                    f"{settings.nvidia_api_base.rstrip('/')}/chat/completions",
                    json=payload,
                    headers={"Authorization": outbound_authorization, "Content-Type": "application/json"},
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
                raise RuntimeError("NVIDIA API returned 429 rate limit")
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
                index > 0 or model != settings.intent_model,
            )
            return coerce_generated_character_edit(data, request, state)
    if last_status:
        raise last_status
    if last_timeout:
        raise last_timeout
    if last_parse_error:
        raise RuntimeError("LLM did not return character JSON") from last_parse_error
    raise RuntimeError("No NVIDIA model attempts configured")


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
    if not normalized.get("location"):
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
        raise RuntimeError("NVIDIA API returned 429 rate limit")
    name = source.name or source.character_id or ("Игрок" if source.target == "player" else "NPC")
    generated = {
        "target": source.target,
        "character_id": source.character_id or (stable_character_id(name) if source.target == "npc" else None),
        "name": name,
        "status": source.status or ("active" if source.target == "player" else "alive"),
        "location": source.location or state.get("player", {}).get("location") or "unknown",
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
                        "location": request.location or state.get("player", {}).get("location", "unknown"),
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
