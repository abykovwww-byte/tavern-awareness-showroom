"""FastAPI entrypoint for RP Gateway."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import Settings, get_settings
from app.core.json_patch import PatchError
from app.models.schemas import ChatCompletionRequest, HealthResponse, PatchEnvelope
from app.services.adjudicator import Adjudicator
from app.services.state_store import StateStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = StateStore(settings.sqlite_path, settings.campaign_id, settings.world_state_path)
    adjudicator = Adjudicator(settings, store)

    app = FastAPI(title="RP Gateway", version="0.4.0")
    app.state.settings = settings
    app.state.store = store
    app.state.adjudicator = adjudicator

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
