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
