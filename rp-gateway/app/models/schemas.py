"""Pydantic request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CheckType = Literal[
    "persuasion",
    "intimidation",
    "deception",
    "stealth",
    "information",
    "resource",
    "feasibility",
    "trust",
    "conflict",
    "random_event",
]

OutcomeLabel = Literal[
    "critical_failure",
    "failure",
    "failure_with_progress",
    "partial_success",
    "success",
    "critical_success",
]


class ChatMessage(BaseModel):
    role: str
    content: str | list[Any] | None = ""
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stream: bool = False


class Intent(BaseModel):
    action_type: CheckType = "feasibility"
    actor: str = "player"
    target: str | None = None
    desired_outcome: str = ""
    methods: list[str] = Field(default_factory=list)
    resources_claimed: list[str] = Field(default_factory=list)
    facts_claimed_by_player: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    skill: int = 0
    preparation: int = 0
    leverage: int = 0
    difficulty: int = 10
    resource_amount: float = 1.0


class Outcome(BaseModel):
    check_id: str
    action_type: CheckType
    actor: str
    target: str | None = None
    result: OutcomeLabel
    roll: int
    difficulty: int
    modifiers: dict[str, int]
    final_score: int
    blocked_reasons: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)
    forbidden_reinterpretations: list[str] = Field(default_factory=list)
    authoritative_block: str


class PatchOperation(BaseModel):
    op: Literal["add", "replace", "remove"]
    path: str
    value: Any | None = None
    reason: str
    turn: int


class StatePatch(BaseModel):
    turn: int
    check_id: str | None = None
    source: str = "rp-gateway"
    patch: list[PatchOperation]
    uncertain_facts: list[Any] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class PatchEnvelope(BaseModel):
    patch: StatePatch
    confirm: bool = False


class WorldInstructionRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)
    confirm: bool = False


class WorldApplyRequest(BaseModel):
    proposal_id: str = "latest"
    confirm: bool = False


class WorldPackSummary(BaseModel):
    id: str
    title: str
    slug: str
    status: str
    premise: str = ""
    manifest_path: str
    state_seed_path: str
    lorebook_path: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)


class WorldPromptCreate(BaseModel):
    title: str = Field(default="Свой мир", min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=6000)


class PlayerTemplate(BaseModel):
    id: str
    name: str
    description: str
    profile: dict[str, Any] = Field(default_factory=dict)


class PlayerCharacterDraftRequest(BaseModel):
    worldpack_id: str
    name: str = Field(default="Player Character", min_length=1, max_length=120)
    concept: str = Field(default="", max_length=4000)


class PlayerCharacterCreate(BaseModel):
    worldpack_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    starting_state_patch_json: str | None = None
    profile: dict[str, Any] = Field(default_factory=dict)


class PlayerCharacterSummary(BaseModel):
    id: str
    worldpack_id: str
    name: str
    description: str
    status: str
    starting_state_patch_json: str | None = None
    profile: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ModelProfileSummary(BaseModel):
    id: str
    title: str
    provider: str
    base_url: str
    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    api_key_source: str
    description: str = ""
    rp_fit: str = ""
    context_window: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = "static"
    availability: str = ""


class PartyCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    worldpack_id: str
    player_character_id: str
    model_profile_id: str


class PartyModelUpdate(BaseModel):
    model_profile_id: str


class PartySummary(BaseModel):
    id: str
    title: str
    worldpack_id: str
    player_character_id: str
    model_profile_id: str
    state_campaign_id: str
    status: str
    created_at: str
    updated_at: str
    worldpack: WorldPackSummary | None = None
    player_character: PlayerCharacterSummary | None = None
    model_profile: ModelProfileSummary | None = None


class PartyMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    idempotency_key: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class PartyCheckRequest(BaseModel):
    check_type: CheckType = "feasibility"
    target: str | None = Field(default=None, max_length=120)
    skill: int = 0
    difficulty: int = 10
    goal: str = Field(default="", max_length=1000)


class WorldInstructionDraft(BaseModel):
    proposal_id: str
    instruction: str
    summary: str
    changes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    patch: StatePatch


class ValidationResult(BaseModel):
    valid: bool
    violations: list[str] = Field(default_factory=list)
    repair_instruction: str = ""


class HealthResponse(BaseModel):
    status: str
    campaign_id: str
    database: str


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: dict[str, int] | None = None
