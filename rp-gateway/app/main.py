"""FastAPI entrypoint for Awareness Gateway."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.core.config import Settings, get_settings
from app.models.schemas import (
    AutoTestCreate,
    ChatCompletionRequest,
    ChatMessage,
    HealthResponse,
    LoginRequest,
    Outcome,
    PartyDatasetUpdate,
    PartyMessageRequest,
    PartyStartRequest,
    PartyTurnDatasetUpdate,
    TurnFeedbackUpdate,
    TrainingArtifactEventRequest,
    TrainingWorkspaceEventRequest,
    ServiceModelUpdate,
    ShowroomRunCreate,
    ShowroomScenarioCreate,
    ShowroomScenarioUpdate,
    UserCreate,
    UserDeleteRequest,
    UserPasswordUpdate,
    UserStatusUpdate,
    WorldPackVisibilityUpdate,
    StatePatch,
)
from app.services.adjudicator import Adjudicator, RequestAlreadyRunning
from app.services.auth_store import AuthStore, AuthUser
from app.services.autotest import AutoPlayerClient
from app.services.context_budget import estimate_tokens, model_context_limit_tokens, split_turns_by_token_budget
from app.services.narrative import (
    ProviderRateLimitError,
    archived_memory_retrieval_block,
    response_text,
    uncompacted_archive_fallback_block,
)
from app.services.provider_catalog import (
    normalize_provider,
    provider_api_key,
    provider_base_url,
    validate_narrator_settings,
)
from app.services.service_models import (
    SERVICE_MODEL_SETTING_KEY,
    service_model_choice,
    service_model_choices,
)
from app.services.party_store import PartyStore
from app.services.showroom import SHOWROOM_WORLD_OWNER, ShowroomStore
from app.services.state_store import StateStore, StateVersionConflict
from app.services.training_artifacts import TrainingArtifactService
from app.services.training_runtime import TrainingRuntimeService
from app.services.training_workspace import TrainingWorkspaceService
from app.services.validator import OutputValidator


logger = logging.getLogger(__name__)

TRAINING_START_HISTORY_MESSAGE = "[TRAINING_START] Старт учебной сессии"


def ensure_training_database_ownership(settings: Settings) -> None:
    database_path = Path(settings.sqlite_path)
    if str(database_path) == ":memory:" or not database_path.exists() or database_path.stat().st_size == 0:
        return

    violations: list[str] = []
    try:
        with sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only = ON")
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            user_tables = {table for table in tables if not table.startswith("sqlite_")}
            ownership_tables = {"parties", "showroom_scenarios", "worldpacks", "model_profiles"}
            if user_tables and user_tables.isdisjoint(ownership_tables):
                raise RuntimeError("existing database has an unrecognized ownership schema")

            def columns(table: str) -> set[str]:
                return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

            def row_count(table: str) -> int:
                return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

            if "parties" in tables and row_count("parties"):
                if "scenario_type" not in columns("parties"):
                    violations.append("parties schema")
                else:
                    count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM parties WHERE scenario_type IS NULL OR scenario_type != 'training'"
                        ).fetchone()[0]
                    )
                    if count:
                        violations.append(f"non-training parties={count}")

            if "showroom_scenarios" in tables and row_count("showroom_scenarios"):
                scenario_columns = columns("showroom_scenarios")
                if not {"scenario_type", "world_source"}.issubset(scenario_columns):
                    violations.append("showroom_scenarios schema")
                else:
                    count = int(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM showroom_scenarios
                            WHERE scenario_type IS NULL OR scenario_type != 'training'
                               OR world_source IS NULL OR world_source != 'preset'
                            """
                        ).fetchone()[0]
                    )
                    if count:
                        violations.append(f"foreign showroom scenarios={count}")

            if "worldpacks" in tables and row_count("worldpacks"):
                if "manifest_json" not in columns("worldpacks"):
                    violations.append("worldpacks schema")
                else:
                    foreign_worldpacks = 0
                    for row in connection.execute("SELECT manifest_json FROM worldpacks"):
                        try:
                            manifest = json.loads(str(row[0]))
                        except (TypeError, ValueError, json.JSONDecodeError):
                            foreign_worldpacks += 1
                            continue
                        scenario_types = manifest.get("scenario_types") if isinstance(manifest, dict) else None
                        if not (
                            isinstance(scenario_types, dict)
                            and scenario_types.get("recommended") == "training"
                            and scenario_types.get("supported") == ["training"]
                            and isinstance(manifest.get("training_runtime"), dict)
                        ):
                            foreign_worldpacks += 1
                    if foreign_worldpacks:
                        violations.append(f"foreign worldpacks={foreign_worldpacks}")

            if "model_profiles" in tables and row_count("model_profiles"):
                if "provider" not in columns("model_profiles"):
                    violations.append("model_profiles schema")
                else:
                    count = int(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM model_profiles
                            WHERE lower(provider) NOT IN ('local', 'gemini', 'openrouter')
                            """
                        ).fetchone()[0]
                    )
                    if count:
                        violations.append(f"retired model profiles={count}")
    except sqlite3.Error as exc:
        raise RuntimeError("existing Awareness database cannot be verified read-only") from exc

    if violations:
        raise RuntimeError(
            "existing database is not owned by the training-only Awareness project: "
            + ", ".join(violations)
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    if settings.scenario_type != "training":
        raise RuntimeError("training gateway requires SCENARIO_TYPE=training")
    ensure_training_database_ownership(settings)
    store = StateStore(settings.sqlite_path, settings.campaign_id, settings.world_state_path)
    auth_store = AuthStore(settings)
    party_store = PartyStore(settings, default_owner_user_id=auth_store.default_owner_user_id())
    showroom_store = ShowroomStore(settings, party_store)
    if settings.showroom_catalog_path:
        showroom_store.reconcile_catalog(settings.showroom_catalog_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        for party in party_store.list_parties():
            if party.status != "active":
                continue
            party_state_store = party_store.store_for_party(party.id)
            recovered = party_state_store.recover_interrupted_work()
            if any(recovered.values()):
                logger.warning("recovered_interrupted_work party_id=%s %s", party.id, recovered)
            if any(job["status"] in {"pending", "running"} for job in party_state_store.service_jobs(limit=20)):
                try:
                    party_runtime = runtime_settings_for_party(party)
                except ValueError as exc:
                    logger.warning("party_runtime_disabled party_id=%s error=%s", party.id, exc)
                    continue
                Adjudicator(
                    party_runtime,
                    party_state_store,
                ).schedule_service_jobs()
        for branch in party_store.list_all_party_branches():
            branch_store = party_store.store_for_branch(branch["party_id"], branch["id"])
            recovered = branch_store.recover_interrupted_work()
            if any(recovered.values()):
                logger.warning("recovered_interrupted_branch_work branch_id=%s %s", branch["id"], recovered)
        for run in party_store.resumable_autotest_runs():
            schedule_autotest(run["id"])
        yield

    app = FastAPI(title="Awareness Gateway", version="0.5.0", lifespan=lifespan)
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
        if selected_key != hydrated.llm_api_key:
            hydrated = replace(hydrated, llm_api_key=selected_key)
        return settings_with_global_service_model(hydrated)

    def runtime_settings_for_party(party: Any) -> Settings:
        return settings_with_provider_key(settings_for_party(settings, party), party)

    def ensure_party_playable(party: Any) -> None:
        if party.status == "archived":
            raise HTTPException(status_code=409, detail="archived party is terminal")
        try:
            party_store.require_active_model_profile(party.model_profile_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def training_services_for_party(
        party: Any,
        party_state_store: StateStore,
    ) -> tuple[TrainingRuntimeService, TrainingArtifactService, TrainingWorkspaceService]:
        run_capabilities = showroom_store.capabilities_for_party(party.id)
        links_enabled = run_capabilities is None or run_capabilities["interactive_links_enabled"]
        workspace_enabled = run_capabilities is None or run_capabilities["interactive_workspace_enabled"]
        return (
            TrainingRuntimeService(party.worldpack, party_state_store),
            TrainingArtifactService(party.worldpack, party_state_store, enabled=links_enabled),
            TrainingWorkspaceService(party.worldpack, party_state_store, enabled=workspace_enabled),
        )

    app.state.adjudicator = Adjudicator(settings_with_global_service_model(settings), store)

    def runtime_settings_for_profile(profile: Any, cache_session_id: str, party: Any | None = None) -> Settings:
        return settings_with_provider_key(settings_for_model_profile(settings, profile, cache_session_id), party)

    def runtime_settings_for_branch(party: Any, branch_id: str) -> Settings:
        party_store.get_party_branch(party.id, branch_id, owner_user_id=party.owner_user_id)
        return replace(
            settings_with_provider_key(settings_for_party(settings, party), party),
            prompt_cache_session_id=f"training-session:{party.id}:branch:{branch_id}",
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
                    showroom_store.require_training_party(run["source_party_id"])
                    party = party_store.get_party(
                        run["source_party_id"],
                        owner_user_id=SHOWROOM_WORLD_OWNER,
                    )
                    party_state_store = party_store.store_for_branch(
                        party.id,
                        run["branch_id"],
                        owner_user_id=SHOWROOM_WORLD_OWNER,
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
                    provider=narrator_profile.provider,
                    narrator_settings=party.narrator_settings,
                )
                runtime_service, artifact_service, workspace_service = training_services_for_party(
                    party,
                    party_state_store,
                )
                narrator_response = await Adjudicator(
                    party_settings,
                    party_state_store,
                    training_artifacts=artifact_service,
                    training_workspace=workspace_service,
                    training_runtime=runtime_service,
                ).handle_chat(
                    chat_request,
                    authorization=None,
                    idempotency_key=f"autotest:{run_id}:turn:{turn_number}",
                    request_id=f"{request_id}_narrator",
                    allow_gateway_fallback=runtime_service.enabled,
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

    def require_training_showroom_party(party_id: str) -> None:
        try:
            showroom_store.require_training_party(party_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
            "scope": "Все текущие и будущие training-прохождения",
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
            "scope": "Все текущие и будущие training-прохождения",
            "uses": ["Долговременная память", "Изменение мира", "Генерация персонажей"],
            "choice_id": runtime.service_model_choice,
            "selected": service_model_choice(runtime),
            "choices": service_model_choices(runtime),
        }

    @app.get("/api/admin/showroom/runs")
    def admin_list_showroom_runs(request: Request, limit: int = 200) -> dict[str, Any]:
        require_admin(request)
        return {"runs": showroom_store.list_admin_runs(limit=limit)}

    @app.patch("/api/admin/datasets/parties/{party_id}")
    def admin_update_party_dataset(
        request: Request,
        party_id: str,
        payload: PartyDatasetUpdate,
    ) -> dict[str, Any]:
        require_admin(request)
        require_training_showroom_party(party_id)
        try:
            party = party_store.update_party_dataset(
                party_id,
                review_status=payload.review_status,
                tags=payload.tags,
                owner_user_id=SHOWROOM_WORLD_OWNER,
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
        require_admin(request)
        require_training_showroom_party(party_id)
        try:
            turns = party_store.list_dataset_turns(
                party_id,
                branch_id=branch_id,
                owner_user_id=SHOWROOM_WORLD_OWNER,
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
        require_training_showroom_party(party_id)
        try:
            label = party_store.set_turn_dataset_label(
                party_id,
                turn_id,
                branch_id=branch_id,
                review_status=payload.review_status,
                tags=payload.tags,
                notes=payload.notes,
                owner_user_id=SHOWROOM_WORLD_OWNER,
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
        require_admin(request)
        if scenario_type not in {None, "training"}:
            raise HTTPException(status_code=400, detail="scenario_type must be training")
        export = party_store.export_dataset_records(
            owner_user_id=SHOWROOM_WORLD_OWNER,
            scenario_type="training",
            include_branches=include_branches,
            allowed_party_ids=showroom_store.training_party_ids(),
        )
        body = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in export["records"])
        return StreamingResponse(
            iter([body]),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": 'attachment; filename="awareness-showroom-training-sft-v1.jsonl"',
                "X-Dataset-Approved-Turns": str(export["approved_turns"]),
                "X-Dataset-Skipped-Missing-Prompt": str(export["skipped_missing_prompt"]),
            },
        )

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

    @app.post("/api/worldpacks/prompt", include_in_schema=False)
    def reject_prompt_worldpack() -> None:
        raise HTTPException(status_code=404, detail="not found")

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

    @app.get("/api/model-profiles")
    def list_model_profiles() -> dict[str, Any]:
        party_store.settings = settings_with_provider_key(settings)
        profiles = party_store.list_model_profiles()
        return {"model_profiles": [profile.model_dump(mode="json") for profile in profiles]}

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

    def record_party_artifact_event(
        http_request: Request,
        party_id: str,
        payload: TrainingArtifactEventRequest,
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            ensure_party_playable(party)
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            _, service, _ = training_services_for_party(party, party_state_store)
            if not service.enabled or party.scenario_type != "training":
                raise ValueError("interactive training artifacts are not enabled for this party")
            result = service.record_event(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, **result.model_dump(mode="json")}

    def get_party_workspace(http_request: Request, party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            ensure_party_playable(party)
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            _, _, service = training_services_for_party(party, party_state_store)
            if not service.enabled or party.scenario_type != "training":
                raise ValueError("interactive training workspace is not enabled for this party")
            return {"party_id": party_id, "workspace": service.snapshot(party_state_store.get_state())}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def record_party_workspace_event(
        http_request: Request,
        party_id: str,
        payload: TrainingWorkspaceEventRequest,
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            ensure_party_playable(party)
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            _, _, service = training_services_for_party(party, party_state_store)
            if not service.enabled or party.scenario_type != "training":
                raise ValueError("interactive training workspace is not enabled for this party")
            result = service.record_event(payload, party_state_store.get_state())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, **result.model_dump(mode="json")}

    def get_party_workspace_file_content(http_request: Request, party_id: str, file_id: str) -> FileResponse:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            ensure_party_playable(party)
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            _, _, service = training_services_for_party(party, party_state_store)
            if not service.enabled or party.scenario_type != "training":
                raise ValueError("interactive training workspace is not enabled for this party")
            path, mime_type, filename = service.resource_for_file(
                file_id,
                party_state_store.get_state(),
                public_only=bool(getattr(http_request.state, "showroom_party_access", False)),
            )
            return FileResponse(path, media_type=mime_type, filename=filename)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def start_party(
        http_request: Request,
        party_id: str,
        request: PartyStartRequest = PartyStartRequest(),
        authorization: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            ensure_party_playable(party)
            party_state_store = party_store.store_for_party(
                party_id,
                owner_user_id=owner_user_id(http_request),
            )
            party_settings = replace(
                runtime_settings_for_party(party),
                model_attempt_timeout_seconds=settings.party_start_model_attempt_timeout_seconds,
            )
            model_profile = party.model_profile or party_store.get_model_profile(party.model_profile_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        idempotency_key = request.idempotency_key or f"party-start:{party_id}"
        request_id = x_request_id or f"req_{uuid.uuid4().hex}"
        existing = party_state_store.get_turn_by_idempotency(idempotency_key)
        if existing:
            message = existing.get("choices", [{}])[0].get(
                "message",
                {"role": "assistant", "content": ""},
            )
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

        try:
            request_status = party_state_store.begin_turn_request(idempotency_key, request_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        request_id = str(request_status.get("request_id") or request_id)
        if not request_status.get("acquired"):
            if request_status.get("status") == "completed" and request_status.get("response"):
                response = request_status["response"]
                message = response.get("choices", [{}])[0].get(
                    "message",
                    {"role": "assistant", "content": ""},
                )
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

        adjudicator = Adjudicator(party_settings, party_state_store)
        narrative = adjudicator.narrative
        expected_party_turn = int(party_state_store.get_state().get("meta", {}).get("turn", 0)) + 1
        adjudicator.record_trace_event(
            request_id=request_id,
            phase_key="player_input",
            alignment_key="player_input",
            lane="main",
            event_type="player_input",
            status="completed",
            payload={
                "input": {
                    "content": TRAINING_START_HISTORY_MESSAGE,
                    "source": "system_auto_start",
                }
            },
            party_turn=expected_party_turn,
        )

        def trace_start_failure(exc: Exception) -> None:
            adjudicator.record_trace_event(
                request_id=request_id,
                phase_key="request_failed",
                alignment_key="request_terminal",
                lane="main",
                event_type="request_failed",
                status="failed",
                payload={"error": {"type": type(exc).__name__, "message": str(exc)[:1000]}},
                party_turn=expected_party_turn,
            )

        try:
            state = party_state_store.get_state()
            expected_state_version = int(
                state.get("meta", {}).get("state_version") or party_state_store.current_version() or 0
            )
            runtime_service, artifact_service, workspace_service = training_services_for_party(
                party,
                party_state_store,
            )
            if not runtime_service.enabled:
                raise ValueError("training runtime is not enabled for this WorldPack")
            start_patch = runtime_service.start_patch(state, party_id)
            narrative_state = party_start_narrative_state(state, start_patch)
            prompt = party_start_prompt(party_store, party)
            chat_request = ChatCompletionRequest(
                model=model_profile.model,
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stream=False,
            )
            apply_party_narrator_settings(
                chat_request,
                model_profile.provider,
                model_profile.model,
                party.narrator_settings,
            )
            start_outcome = party_start_outcome(party_id, party.scenario_type)
            memory_summary = party_state_store.memory_for_prompt(
                party_settings.party_memory_prompt_max_chars
            )
            artifact_contract = artifact_service.contract_for_state(narrative_state)
            workspace_contract = workspace_service.contract_for_state(
                narrative_state,
                party_start=True,
            )
            interaction_contract = (
                {"site": artifact_contract, "workspace": workspace_contract}
                if artifact_contract or workspace_contract
                else None
            )
            training_turn_contract = runtime_service.prompt_contract(
                narrative_state,
                interaction_contract,
            )
            prompt_messages = narrative.narrative_messages(
                chat_request,
                narrative_state,
                start_outcome,
                repair_instruction=None,
                memory_summary=memory_summary,
                artifact_contract=interaction_contract,
                training_turn_contract=training_turn_contract,
            )
            adjudicator.record_trace_event(
                request_id=request_id,
                phase_key="gateway_assembly",
                alignment_key="gateway_assembly",
                lane="main",
                event_type="gateway_assembly",
                status="completed",
                payload={
                    "capture_status": "complete",
                    "input": {"messages": prompt_messages},
                    "details": {
                        "message_count": len(prompt_messages),
                        "training_turn_contract_included": True,
                        "interaction_contract_included": bool(interaction_contract),
                        "assembly_trace": adjudicator.prompt_assembly_trace(prompt_messages, prompt),
                    },
                },
                party_turn=expected_party_turn,
            )

            repaired = False
            fallback_reason: str | None = None
            transport_status = "ok"
            try:
                raw = await narrative.complete(
                    chat_request,
                    narrative_state,
                    start_outcome,
                    authorization,
                    memory_summary=memory_summary,
                    request_id=request_id,
                    artifact_contract=interaction_contract,
                    training_turn_contract=training_turn_contract,
                )
            except (
                httpx.HTTPStatusError,
                httpx.TimeoutException,
                ProviderRateLimitError,
                httpx.RequestError,
                RuntimeError,
            ) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code if exc.response is not None else "unknown"
                    fallback_reason = f"http_{status}"
                    transport_status = "provider_error"
                elif isinstance(exc, httpx.TimeoutException):
                    fallback_reason = "timeout"
                    transport_status = "provider_timeout"
                elif isinstance(exc, ProviderRateLimitError):
                    fallback_reason = "rate_limited"
                    transport_status = "provider_error"
                elif isinstance(exc, httpx.RequestError):
                    fallback_reason = "network_error"
                    transport_status = "provider_error"
                else:
                    fallback_reason = "runtime_error"
                    transport_status = "provider_error"
                text = runtime_service.fallback_text(narrative_state, interaction_contract)
                raw = adjudicator.provider_fallback_response(
                    start_outcome,
                    text,
                    fallback_reason,
                    request_id,
                )

            response = adjudicator.normalize_response(raw, model_profile.model)
            text = response_text(response)
            if fallback_reason is None:
                artifact_result = artifact_service.materialize_response(response, artifact_contract)
                workspace_result = workspace_service.materialize_response(response, workspace_contract)
                if artifact_contract and artifact_result.valid:
                    text = artifact_result.text
                if workspace_contract and workspace_result.valid:
                    text = workspace_result.text
                text = runtime_service.normalize_narrative(
                    text,
                    narrative_state,
                    interaction_contract,
                )
                if artifact_result.valid and workspace_result.valid:
                    response = Adjudicator.merge_interaction_response(
                        response,
                        text,
                        artifact_result,
                        workspace_result,
                    )
            else:
                artifact_result = artifact_service.fallback_materialization(
                    response,
                    text,
                    artifact_contract,
                )
                text = artifact_result.text
                workspace_result = workspace_service.fallback_materialization(
                    workspace_contract,
                    text,
                )

            validator = OutputValidator()
            validation = validator.validate(
                text,
                start_outcome,
                narrative_state,
                campaign_id=party.worldpack_id,
                scenario_type="training",
                training_runtime=runtime_service,
                interaction_contract=interaction_contract,
            )
            initial_violations = [
                *validation.violations,
                *artifact_result.violations,
                *workspace_result.violations,
            ]
            adjudicator.record_trace_event(
                request_id=request_id,
                phase_key="validation:initial",
                alignment_key="validation",
                lane="main",
                event_type="validation",
                status="completed" if not initial_violations else "failed",
                payload={
                    "input": {"response": text},
                    "output": {"valid": not initial_violations, "violations": initial_violations},
                    "metadata": {"repair": False, "opening_scene": True},
                },
                party_turn=expected_party_turn,
            )
            runtime_violations = runtime_service.validate_narrative(
                text,
                narrative_state,
                interaction_contract,
            )
            runtime_violation_set = set(runtime_violations)
            repair_allowed = not runtime_service.repair_blockers(
                text,
                narrative_state,
                interaction_contract,
            ) and not any(
                violation not in runtime_violation_set for violation in validation.violations
            )
            invalid = (
                not validation.valid
                or not artifact_result.valid
                or not workspace_result.valid
            )
            if invalid and party_settings.training_repair_attempts > 0 and repair_allowed:
                repaired = True
                repair_instruction = runtime_service.repair_instruction(
                    text,
                    narrative_state,
                    interaction_contract,
                )
                if not artifact_result.valid:
                    repair_instruction = " ".join(
                        [
                            repair_instruction,
                            "Return a valid narrative bundle: " + "; ".join(artifact_result.violations),
                        ]
                    ).strip()
                if not workspace_result.valid:
                    repair_instruction = " ".join(
                        [
                            repair_instruction,
                            "Return valid workspace_files: " + "; ".join(workspace_result.violations),
                        ]
                    ).strip()
                raw = await narrative.complete(
                    chat_request,
                    narrative_state,
                    start_outcome,
                    authorization,
                    repair_instruction,
                    failed_response_text=text,
                    memory_summary=memory_summary,
                    request_id=request_id,
                    artifact_contract=interaction_contract,
                    training_turn_contract=training_turn_contract,
                )
                response = adjudicator.normalize_response(raw, model_profile.model)
                text = response_text(response)
                artifact_result = artifact_service.materialize_response(response, artifact_contract)
                workspace_result = workspace_service.materialize_response(response, workspace_contract)
                if artifact_contract and artifact_result.valid:
                    text = artifact_result.text
                if workspace_contract and workspace_result.valid:
                    text = workspace_result.text
                text = runtime_service.normalize_narrative(
                    text,
                    narrative_state,
                    interaction_contract,
                )
                if artifact_result.valid and workspace_result.valid:
                    response = Adjudicator.merge_interaction_response(
                        response,
                        text,
                        artifact_result,
                        workspace_result,
                    )
                validation = validator.validate(
                    text,
                    start_outcome,
                    narrative_state,
                    campaign_id=party.worldpack_id,
                    scenario_type="training",
                    training_runtime=runtime_service,
                    interaction_contract=interaction_contract,
                )
                repair_violations = [
                    *validation.violations,
                    *artifact_result.violations,
                    *workspace_result.violations,
                ]
                adjudicator.record_trace_event(
                    request_id=request_id,
                    phase_key="validation:repair",
                    alignment_key="validation",
                    lane="main",
                    event_type="validation",
                    status="completed" if not repair_violations else "failed",
                    payload={
                        "input": {"response": text},
                        "output": {"valid": not repair_violations, "violations": repair_violations},
                        "metadata": {"repair": True, "opening_scene": True},
                    },
                    party_turn=expected_party_turn,
                )

            if not validation.valid or not artifact_result.valid or not workspace_result.valid:
                fallback_reason = fallback_reason or "validation_failed"
                transport_status = "invalid_response"
                party_state_store.audit(
                    "party_start_validation_failed",
                    {
                        "request_id": request_id,
                        "model": model_profile.model,
                        "violations": [
                            *validation.violations,
                            *artifact_result.violations,
                            *workspace_result.violations,
                        ],
                    },
                    request_id,
                )
                text = runtime_service.fallback_text(narrative_state, interaction_contract)
                response = adjudicator.provider_fallback_response(
                    start_outcome,
                    text,
                    fallback_reason,
                    request_id,
                )
                artifact_result = artifact_service.fallback_materialization(
                    response,
                    text,
                    artifact_contract,
                )
                text = artifact_result.text
                workspace_result = workspace_service.fallback_materialization(
                    workspace_contract,
                    text,
                )

            response = Adjudicator.merge_interaction_response(
                response,
                text,
                artifact_result,
                workspace_result,
            )
            final_validation = validator.validate(
                text,
                start_outcome,
                narrative_state,
                campaign_id=party.worldpack_id,
                scenario_type="training",
                training_runtime=runtime_service,
                interaction_contract=interaction_contract,
            )
            final_violations = [
                *final_validation.violations,
                *artifact_result.violations,
                *workspace_result.violations,
            ]
            adjudicator.record_trace_event(
                request_id=request_id,
                phase_key="validation:final",
                alignment_key="validation",
                lane="main",
                event_type="validation",
                status="completed" if not final_violations else "failed",
                payload={
                    "input": {"response": text},
                    "output": {"valid": not final_violations, "violations": final_violations},
                    "metadata": {"repair": repaired, "opening_scene": True},
                },
                party_turn=expected_party_turn,
            )
            turn_metadata = {
                "schema_version": "rp-gateway.turn.v1",
                "turn_kind": "opening_scene",
                "scenario_type": "training",
                "worldpack_id": party.worldpack_id,
                "state_campaign_id": party_state_store.campaign_id,
                "narrative_provider": party_settings.llm_provider,
                "narrative_model": model_profile.model,
                "generated_by": "human",
                "validator_valid": final_validation.valid,
                "repaired": repaired,
                "fallback": fallback_reason is not None,
                "fallback_reason": fallback_reason,
                "transport_status": transport_status,
                "llm_calls": 2 if repaired else 1,
                "training_runtime_contract_hash": runtime_service.contract_hash,
                "outcome": start_outcome.model_dump(mode="json"),
                "training_capabilities": {
                    "interactive_links_enabled": artifact_service.enabled,
                    "interactive_workspace_enabled": workspace_service.enabled,
                },
            }
            state, turn_id = party_state_store.commit_turn_bundle(
                start_patch,
                reason=f"party_start:{request_id}",
                expected_state_version=expected_state_version,
                idempotency_key=idempotency_key,
                request_id=request_id,
                player_message=TRAINING_START_HISTORY_MESSAGE,
                narrative_response=text,
                response_json=response,
                prompt_messages=prompt_messages,
                metadata=turn_metadata,
                artifacts=artifact_result.persistence_records,
                workspace_files=workspace_result.persistence_records,
            )
            state_version = int(state["meta"]["state_version"])
            adjudicator.record_trace_event(
                request_id=request_id,
                phase_key="turn_commit",
                alignment_key="turn_commit",
                lane="main",
                event_type="turn_commit",
                status="completed",
                payload={
                    "output": {
                        "turn_id": turn_id,
                        "state_version": state_version,
                        "party_turn": int(state["meta"]["turn"]),
                    }
                },
                party_turn=int(state["meta"]["turn"]),
                turn_id=turn_id,
            )
            party_state_store.complete_turn_request(idempotency_key, response)
            party_state_store.audit(
                "party_start_complete",
                {
                    "request_id": request_id,
                    "turn_id": turn_id,
                    "model": model_profile.model,
                    "validator_valid": final_validation.valid,
                    "fallback_reason": fallback_reason,
                },
                request_id,
            )
        except PermissionError as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            trace_start_failure(exc)
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except httpx.TimeoutException as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            trace_start_failure(exc)
            raise HTTPException(
                status_code=504,
                detail="Narrative provider exceeded the training-start deadline",
            ) from exc
        except httpx.HTTPStatusError as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            trace_start_failure(exc)
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise HTTPException(status_code=502, detail=f"Narrative provider HTTP {status}") from exc
        except ProviderRateLimitError as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            trace_start_failure(exc)
            party_state_store.audit(
                "party_start_rate_limited",
                {"request_id": request_id, **exc.details},
                request_id,
            )
            raise HTTPException(status_code=429, detail=exc.public_detail()) from exc
        except StateVersionConflict as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            trace_start_failure(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            trace_start_failure(exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            trace_start_failure(exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            party_state_store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            trace_start_failure(exc)
            raise

        return {
            **response,
            "party_id": party_id,
            "started": True,
            "already_started": False,
            "state_version": state_version,
            "message": response.get("choices", [{}])[0].get(
                "message",
                {"role": "assistant", "content": ""},
            ),
            "raw": response,
        }

    async def party_message(
        http_request: Request,
        party_id: str,
        request: PartyMessageRequest,
        authorization: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id, owner_user_id=owner_user_id(http_request))
            ensure_party_playable(party)
            party_state_store = party_store.store_for_party(party_id, owner_user_id=owner_user_id(http_request))
            party_settings = runtime_settings_for_party(party)
            request_id = x_request_id or request.idempotency_key or f"req_{uuid.uuid4().hex}"
            model_profile = party.model_profile or party_store.get_model_profile(party.model_profile_id)
            chat_request = party_chat_request(
                party_state_store,
                model_profile.model,
                request,
                party_settings,
                provider=model_profile.provider,
                narrator_settings=party.narrator_settings,
            )
            runtime_service, artifact_service, workspace_service = training_services_for_party(party, party_state_store)
            response = await Adjudicator(
                party_settings,
                party_state_store,
                training_artifacts=artifact_service,
                training_workspace=workspace_service,
                training_runtime=runtime_service,
            ).handle_chat(
                chat_request,
                authorization,
                request.idempotency_key,
                request_id,
                allow_gateway_fallback=runtime_service.enabled,
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
        except StateVersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        message = response.get("choices", [{}])[0].get("message", {"role": "assistant", "content": ""})
        return {
            **response,
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
        try:
            showroom_store.get_scenario(scenario_id, public_only=True)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
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
            party_id = showroom_store.party_id_for_run(run_id, visitor_id, require_published=False)
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

    @app.get("/api/showroom/runs/{run_id}/workspace")
    def get_showroom_workspace(http_request: Request, run_id: str) -> dict[str, Any]:
        visitor_id = require_showroom_visitor(http_request)
        try:
            party_id = showroom_store.party_id_for_run(run_id, visitor_id)
            http_request.state.showroom_party_access = True
            result = get_party_workspace(http_request, party_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"run_id": run_id, "workspace": result["workspace"]}

    @app.post("/api/showroom/runs/{run_id}/workspace-events")
    def record_showroom_workspace_event(
        http_request: Request,
        run_id: str,
        payload: TrainingWorkspaceEventRequest,
    ) -> dict[str, Any]:
        visitor_id = require_showroom_visitor(http_request)
        try:
            party_id = showroom_store.party_id_for_run(run_id, visitor_id)
            http_request.state.showroom_party_access = True
            result = record_party_workspace_event(http_request, party_id, payload)
            showroom_store.touch_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result.pop("party_id", None)
        return {"run_id": run_id, **result}

    @app.get("/api/showroom/runs/{run_id}/workspace/files/{file_id}/content")
    def get_showroom_workspace_file_content(http_request: Request, run_id: str, file_id: str) -> FileResponse:
        visitor_id = require_showroom_visitor(http_request)
        try:
            party_id = showroom_store.party_id_for_run(run_id, visitor_id)
            http_request.state.showroom_party_access = True
            return get_party_workspace_file_content(http_request, party_id, file_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
        require_admin(request)
        if source_party_id:
            try:
                showroom_store.require_training_party(source_party_id)
                party_store.get_party(source_party_id, owner_user_id=SHOWROOM_WORLD_OWNER)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"runs": party_store.list_autotest_runs(limit=limit, source_party_id=source_party_id)}

    @app.post("/api/admin/autotests")
    async def admin_create_autotest(
        http_request: Request,
        payload: AutoTestCreate,
    ) -> dict[str, Any]:
        admin = require_admin(http_request)
        audit_owner_id = admin.id if admin else None
        try:
            showroom_store.require_training_party(payload.source_party_id)
            source_party = party_store.get_party(
                payload.source_party_id,
                owner_user_id=SHOWROOM_WORLD_OWNER,
            )
            supported_profiles = {profile.id: profile for profile in party_store.list_autotest_model_profiles()}
            player_profile = supported_profiles.get(payload.player_model_profile_id)
            if player_profile is None:
                raise ValueError("auto-player model must be an available OpenRouter or Local Gemma profile")
            source_store = party_store.store_for_party(
                source_party.id,
                owner_user_id=SHOWROOM_WORLD_OWNER,
            )
            if source_store.has_running_turn_request():
                raise ValueError("wait for the current party turn to finish before creating an auto-test branch")
            label = f"Автотест · {time.strftime('%Y-%m-%d %H:%M:%S')} · {payload.turn_count} ходов"
            checkpoint = source_store.create_memory_checkpoint(label)
            branch = party_store.create_party_branch(
                party_id=source_party.id,
                checkpoint_id=int(checkpoint["id"]),
                label=label,
                branch_type="autotest",
                owner_user_id=SHOWROOM_WORLD_OWNER,
            )
            source_store.audit(
                "autotest_branch_created",
                {"branch_id": branch["id"], "checkpoint_id": checkpoint["id"], "requested_turns": payload.turn_count},
            )
            run = party_store.create_autotest_run(
                owner_user_id=audit_owner_id,
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

    return app


def settings_for_party(
    settings: Settings,
    party: Any,
) -> Settings:
    if settings.scenario_type != "training" or getattr(party, "scenario_type", None) != "training":
        raise ValueError("training gateway accepts only scenario_type=training")
    model_profile = party.model_profile
    party_cache_id = (
        getattr(party, "id", "")
        or getattr(party, "state_campaign_id", "")
        or party.worldpack_id
    )
    prompt_values = {
        "scenario_type": "training",
        "campaign_id": party.worldpack_id,
        "world_system_prompt": worldpack_prompt_text(party, "gm_system"),
        "world_authors_note": worldpack_prompt_text(party, "authors_note"),
        "prompt_cache_session_id": f"training-session:{party_cache_id}",
    }
    if model_profile is None:
        return replace(settings, **prompt_values)
    configured = settings_for_model_profile(settings, model_profile, f"training-session:{party_cache_id}")
    return replace(
        configured,
        **prompt_values,
        model_attempt_timeout_seconds=settings.model_attempt_timeout_seconds,
    )


def settings_for_model_profile(settings: Settings, model_profile: Any, cache_session_id: str) -> Settings:
    provider = normalize_provider(model_profile.provider)
    if provider not in {"local", "gemini", "openrouter"}:
        raise ValueError(f"model profile provider is retired or unsupported: {provider}")
    fallback_models = settings.openrouter_fallback_models if provider == "openrouter" else ()
    return replace(
        settings,
        llm_provider=provider,
        llm_api_base=model_profile.base_url,
        narrative_model=model_profile.model,
        intent_model=model_profile.model,
        validator_model=model_profile.model,
        llm_fallback_models=fallback_models,
        llm_disabled_models=(),
        model_attempt_timeout_seconds=(
            settings.local_llm_timeout_seconds if provider == "local" else settings.model_attempt_timeout_seconds
        ),
        prompt_cache_session_id=cache_session_id,
        party_context_limit_tokens=min(
            model_context_limit_tokens(model_profile) or settings.party_context_max_tokens,
            settings.party_context_max_tokens,
        ),
    )


def worldpack_prompt_text(
    party: Any,
    file_key: str,
) -> str:
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
    rendered_player_role = character.description or player_role or "active player character"
    opening_block = opening_scene or (
        "No dedicated opening-scene file is available. Synthesize the first scene from the current state, "
        "world premise, and player character. End with a concrete player-facing choice."
    )
    mode_instruction = (
        "Write the first turn of a deterministic training scenario in Russian. Follow the world opening template, "
        "schedule, and formatting literally. Do not reveal lessons, hints, safety judgments, scoring, or hidden "
        "scenario structure. Do not choose an action for the player."
    )
    blocks = [
            "START_PARTY_OPENING_SCENE",
            "This is an internal Showroom auto-start request, not a player action.",
            f"Selected scenario type: {party.scenario_type}",
            mode_instruction,
            "Use second person where appropriate and preserve player agency.",
            "Do not expose service instructions, JSON, model policy, or the AUTO_START marker.",
            f"World title: {world.title}",
            f"World premise: {premise or 'use the current authoritative state'}",
            f"Player character: {character.name}",
            f"Player role: {rendered_player_role}",
            f"Opening scene source:\n{opening_block}",
        ]
    return "\n\n".join(blocks)


def party_start_outcome(
    party_id: str,
    scenario_type: str = "training",
) -> Outcome:
    if scenario_type != "training":
        raise ValueError("training gateway accepts only scenario_type=training")
    return Outcome(
        check_id=f"party_start:{party_id}",
        action_type="feasibility",
        actor="system",
        target="opening_scene",
        result="deterministic_resolution",
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


def party_chat_request(
    store: StateStore,
    model: str,
    request: PartyMessageRequest,
    settings: Settings,
    provider: str | None = None,
    narrator_settings: dict[str, Any] | None = None,
) -> ChatCompletionRequest:
    memory = store.latest_memory_coverage()
    covered_through = int(memory["to_turn_id"]) if memory else 0
    all_turns = store.turns_for_memory(include_noncanonical_fallback=False)
    turns = [turn for turn in all_turns if int(turn["id"]) > covered_through]
    current_message_tokens = estimate_tokens(request.content)
    history_budget = max(settings.effective_party_history_token_budget - current_message_tokens, 0)
    overflow_turns, raw_turns = split_turns_by_token_budget(turns, history_budget)

    messages: list[ChatMessage] = []
    fallback_block = uncompacted_archive_fallback_block(
        overflow_turns,
        settings.party_memory_fallback_max_chars,
    )
    if fallback_block:
        messages.append(ChatMessage(role="system", content=fallback_block))
    for turn in raw_turns:
        messages.extend(
            (
                ChatMessage(role="user", content=str(turn["player_message"])),
                ChatMessage(role="assistant", content=str(turn["narrative_response"])),
            )
        )
    if settings.party_memory_retrieval_enabled:
        retrieved = store.search_archived_turns(
            request.content,
            through_turn_id=covered_through,
            limit=settings.party_memory_retrieval_limit,
        )
        retrieval_block = archived_memory_retrieval_block(
            retrieved,
            settings.party_memory_retrieval_max_chars,
        )
        if retrieval_block:
            messages.append(ChatMessage(role="system", content=retrieval_block))
    messages.append(ChatMessage(role="user", content=request.content))

    chat_request = ChatCompletionRequest(
        model=model,
        messages=messages,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        stream=False,
    )
    if provider and narrator_settings:
        apply_party_narrator_settings(chat_request, provider, model, narrator_settings)
    return chat_request


def apply_party_narrator_settings(
    request: ChatCompletionRequest,
    provider: str,
    model: str,
    narrator_settings: dict[str, Any] | None,
) -> ChatCompletionRequest:
    settings = validate_narrator_settings(provider, model, narrator_settings or {})
    if not settings:
        return request
    if request.temperature is None and "temperature" in settings:
        request.temperature = float(settings["temperature"])
    if request.max_tokens is None and "max_tokens" in settings:
        request.max_tokens = int(settings["max_tokens"])
    if "top_p" in settings:
        request.top_p = float(settings["top_p"])
    effort = settings.get("reasoning_effort")
    if effort == "none":
        request.reasoning = {"enabled": False}
    elif effort:
        request.reasoning = {"effort": effort, "exclude": True}
    request._narrator_settings_model = model
    return request
