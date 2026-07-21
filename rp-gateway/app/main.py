"""FastAPI entrypoint for RP Gateway."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import Settings, get_settings
from app.core.json_patch import PatchError
from app.models.schemas import (
    ChatCompletionRequest,
    ChatMessage,
    HealthResponse,
    Outcome,
    PatchEnvelope,
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
    WorldPromptCreate,
    WorldApplyRequest,
    WorldInstructionRequest,
)
from app.services.adjudicator import Adjudicator
from app.services.character_view import party_character_sheets
from app.services.context_estimator import estimate_party_context
from app.services.journal import JournalBuilder
from app.services.memory import MemorySummarizer
from app.services.narrative import NarrativeClient, response_text
from app.services.party_store import PartyStore
from app.services.prompt_tools import PromptInspector
from app.services.state_store import StateStore


AUTO_START_HISTORY_MESSAGE = "[AUTO_START] Старт партии"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    store = StateStore(settings.sqlite_path, settings.campaign_id, settings.world_state_path)
    adjudicator = Adjudicator(settings, store)
    party_store = PartyStore(settings)

    app = FastAPI(title="RP Gateway", version="0.5.0")
    app.state.settings = settings
    app.state.store = store
    app.state.adjudicator = adjudicator
    app.state.party_store = party_store

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        try:
            store.get_state()
            database = "ok"
        except Exception:  # noqa: BLE001
            database = "error"
        status = "ok" if database == "ok" else "error"
        return HealthResponse(status=status, campaign_id=settings.campaign_id, database=database)

    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        return {"campaign_id": settings.campaign_id, "state": store.get_state()}

    @app.get("/api/state/history")
    def get_history(limit: int = 50) -> dict[str, Any]:
        return {"campaign_id": settings.campaign_id, "history": store.history(limit=limit)}

    @app.get("/api/worldpacks")
    def list_worldpacks() -> dict[str, Any]:
        return {"worldpacks": [pack.model_dump(mode="json") for pack in party_store.list_worldpacks()]}

    @app.post("/api/worldpacks/prompt")
    def create_prompt_worldpack(request: WorldPromptCreate) -> dict[str, Any]:
        try:
            pack = party_store.create_prompt_worldpack(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"worldpack": pack.model_dump(mode="json")}

    @app.get("/api/worldpacks/{worldpack_id}")
    def get_worldpack(worldpack_id: str) -> dict[str, Any]:
        try:
            pack = party_store.get_worldpack(worldpack_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"worldpack": pack.model_dump(mode="json")}

    @app.get("/api/worldpacks/{worldpack_id}/player-templates")
    def player_templates(worldpack_id: str) -> dict[str, Any]:
        try:
            templates = party_store.player_templates(worldpack_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"worldpack_id": worldpack_id, "templates": [template.model_dump(mode="json") for template in templates]}

    @app.get("/api/player-characters")
    def list_player_characters(worldpack_id: str | None = None) -> dict[str, Any]:
        characters = party_store.list_player_characters(worldpack_id=worldpack_id)
        return {"player_characters": [character.model_dump(mode="json") for character in characters]}

    @app.post("/api/player-characters/draft")
    def draft_player_character(request: PlayerCharacterDraftRequest) -> dict[str, Any]:
        try:
            pack = party_store.get_worldpack(request.worldpack_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        description = request.concept.strip() or str(pack.manifest.get("player_role") or "Player character")
        return {
            "draft": {
                "worldpack_id": request.worldpack_id,
                "name": request.name,
                "description": description,
                "profile": {
                    "source": "light-gui-draft",
                    "worldpack_id": request.worldpack_id,
                    "world_title": pack.title,
                    "concept": description,
                },
            }
        }

    @app.post("/api/player-characters")
    def create_player_character(request: PlayerCharacterCreate) -> dict[str, Any]:
        try:
            character = party_store.create_player_character(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"player_character": character.model_dump(mode="json")}

    @app.get("/api/model-profiles")
    def list_model_profiles() -> dict[str, Any]:
        profiles = party_store.list_model_profiles()
        return {"model_profiles": [profile.model_dump(mode="json") for profile in profiles]}

    @app.get("/api/parties")
    def list_parties() -> dict[str, Any]:
        return {"parties": [party.model_dump(mode="json") for party in party_store.list_parties()]}

    @app.post("/api/parties")
    def create_party(request: PartyCreate) -> dict[str, Any]:
        try:
            party = party_store.create_party(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party": party.model_dump(mode="json")}

    @app.get("/api/parties/{party_id}")
    def get_party(party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party": party.model_dump(mode="json")}

    @app.post("/api/parties/{party_id}/activate")
    def activate_party(party_id: str) -> dict[str, Any]:
        try:
            party = party_store.activate_party(party_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party": party.model_dump(mode="json")}

    @app.delete("/api/parties/{party_id}")
    def delete_party(party_id: str) -> dict[str, Any]:
        try:
            party_store.delete_party(party_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True, "party_id": party_id}

    @app.patch("/api/parties/{party_id}/model")
    def update_party_model(party_id: str, request: PartyModelUpdate) -> dict[str, Any]:
        try:
            party = party_store.update_party_model(party_id, request.model_profile_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party": party.model_dump(mode="json")}

    @app.get("/api/parties/{party_id}/state")
    def get_party_state(party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state = party_store.store_for_party(party_id).get_state()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party_id": party.id, "state_campaign_id": party.state_campaign_id, "state": party_state}

    @app.get("/api/parties/{party_id}/history")
    def get_party_history(party_id: str, limit: int = 50) -> dict[str, Any]:
        try:
            party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "turns": party_state_store.turn_history(limit=limit),
            "state_versions": party_state_store.history(limit=limit),
        }

    @app.get("/api/parties/{party_id}/memory")
    def get_party_memory(party_id: str, limit: int = 5) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
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
        party_id: str,
        request: PartyMemorySummarizeRequest = PartyMemorySummarizeRequest(),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
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
    def delete_party_memory_latest(party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
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
    def get_party_context(party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
            model_profile = party.model_profile or party_store.get_model_profile(party.model_profile_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "context": estimate_party_context(party_state_store, party_settings, model_profile),
        }

    @app.get("/api/parties/{party_id}/characters")
    def get_party_characters(party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "party_id": party_id,
            "state_campaign_id": party.state_campaign_id,
            "characters": party_character_sheets(party_state_store.get_state()),
        }

    @app.get("/api/parties/{party_id}/journal")
    def get_party_journal(party_id: str, limit: int = 8) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
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
        party_id: str,
        request: PartyJournalSummarizeRequest = PartyJournalSummarizeRequest(),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
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
    def delete_party_journal_latest(party_id: str) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
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
    def preview_party_prompt(party_id: str, request: PartyPromptPreviewRequest) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            preview = PromptInspector(party_settings, party_state_store).preview(request.content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, "preview": preview}

    @app.post("/api/parties/{party_id}/start")
    async def start_party(
        party_id: str,
        request: PartyStartRequest = PartyStartRequest(),
        authorization: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
            model_profile = party.model_profile or party_store.get_model_profile(party.model_profile_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        idempotency_key = request.idempotency_key or f"party-start:{party_id}"
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

        request_id = x_request_id or f"req_{uuid.uuid4().hex}"
        try:
            state = party_state_store.get_state()
            prompt = party_start_prompt(party_store, party)
            chat_request = ChatCompletionRequest(
                model=model_profile.model,
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stream=False,
            )
            raw = await NarrativeClient(party_settings).complete(
                chat_request,
                state,
                party_start_outcome(party_id),
                authorization,
                memory_summary=party_state_store.latest_memory_summary(),
                request_id=request_id,
            )
            response = Adjudicator(party_settings, party_state_store).normalize_response(raw, model_profile.model)
            text = response_text(response)
            state_version = party_state_store.current_version() or int(state.get("meta", {}).get("state_version") or 1)
            turn_id = party_state_store.record_turn(
                idempotency_key,
                request_id,
                AUTO_START_HISTORY_MESSAGE,
                text,
                response,
                state_version,
            )
            party_state_store.audit(
                "party_start_complete",
                {"request_id": request_id, "turn_id": turn_id, "model": model_profile.model},
                request_id,
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
        party_id: str,
        request: PartyMessageRequest,
        authorization: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
            model_profile = party.model_profile or party_store.get_model_profile(party.model_profile_id)
            chat_request = party_chat_request(party_state_store, model_profile.model, request)
            response = await Adjudicator(party_settings, party_state_store).handle_chat(
                chat_request,
                authorization,
                request.idempotency_key,
                x_request_id,
            )
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
        party_id: str,
        request: PartyCheckRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        command = check_command(request)
        return await party_message(
            party_id,
            PartyMessageRequest(content=command),
            authorization=authorization,
            x_request_id=None,
        )

    @app.post("/api/parties/{party_id}/world/instruct")
    async def party_world_instruct(
        party_id: str,
        request: WorldInstructionRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            party = party_store.get_party(party_id)
            party_state_store = party_store.store_for_party(party_id)
            party_settings = settings_for_party(settings, party)
            world = Adjudicator(party_settings, party_state_store).world
            draft = await world.draft_instruction(request.instruction, authorization)
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
    def party_world_proposals(party_id: str, limit: int = 10) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"party_id": party_id, "proposals": party_state_store.pending_patches(limit=limit)}

    @app.post("/api/parties/{party_id}/world/apply")
    def party_world_apply(party_id: str, request: WorldApplyRequest) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id)
            patch = party_state_store.get_pending_patch(request.proposal_id)
            if not request.confirm:
                return {"party_id": party_id, "applied": False, "would_apply": patch.model_dump(mode="json")}
            state = party_state_store.apply_pending_patch(request.proposal_id, reason="party_world_instruction_apply")
        except (PatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, "applied": True, "state": state}

    @app.post("/api/parties/{party_id}/world/discard")
    def party_world_discard(party_id: str, request: WorldApplyRequest) -> dict[str, Any]:
        try:
            party_state_store = party_store.store_for_party(party_id)
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
            party_state_store = party_store.store_for_party(party_id)
            state = party_state_store.rollback(int(target_version) if target_version is not None else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"party_id": party_id, "rolled_back": True, "state": state}

    @app.post("/api/state/patch/preview")
    def preview_patch(envelope: PatchEnvelope) -> dict[str, Any]:
        try:
            candidate = store.preview_patch(envelope.patch)
        except PatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"candidate": candidate, "would_apply": envelope.patch.model_dump(mode="json")}

    @app.post("/api/state/patch/apply")
    def apply_patch(envelope: PatchEnvelope) -> dict[str, Any]:
        try:
            if not envelope.confirm:
                return preview_patch(envelope)
            state = store.apply_state_patch(envelope.patch, reason="api_patch_apply")
        except (PatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"applied": True, "state": state}

    @app.get("/api/world/proposals")
    def world_proposals(limit: int = 10) -> dict[str, Any]:
        return {"campaign_id": settings.campaign_id, "proposals": store.pending_patches(limit=limit)}

    @app.post("/api/world/instruct")
    async def world_instruct(
        request: WorldInstructionRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            draft = await adjudicator.world.draft_instruction(request.instruction, authorization)
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
    def world_apply(request: WorldApplyRequest) -> dict[str, Any]:
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
            response = await adjudicator.handle_chat(request, authorization, idempotency_key, request_id)
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


def party_chat_request(store: StateStore, model: str, request: PartyMessageRequest) -> ChatCompletionRequest:
    messages: list[ChatMessage] = []
    for turn in store.turn_history(limit=8):
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
