"""Pydantic request/response models."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from app.core.config import RP_CONTRACT_MAX_REVISION


WORLD_PROMPT_MAX_CHARS = 6_000
WORLD_MARKDOWN_MAX_CHARS = 200_000
WORLD_MARKDOWN_FILENAME_MAX_CHARS = 255


CheckType = Literal[
    "narrative",
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
    "narrative_continuation",
    "deterministic_resolution",
]

ScenarioType = Literal["rp", "novel", "training"]
ShowroomScenarioStatus = Literal["draft", "published", "archived"]
ShowroomWorldSource = Literal["preset", "prompt"]
ShowroomLeaderboardMetric = Literal["state_path", "turn_count"]
TrainingArtifactEventType = Literal["link_opened", "form_submitted", "site_closed", "reported"]


class ChatMessage(BaseModel):
    role: str
    content: str | list[Any] | None = ""
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    _raw_transcript_chars: int | None = PrivateAttr(default=None)
    _latest_player_action: str | None = PrivateAttr(default=None)
    _rp_story_memory_snapshot_id: int | None = PrivateAttr(default=None)
    _rp_story_memory_covered_through_turn_id: int | None = PrivateAttr(default=None)
    _narrator_settings_model: str | None = PrivateAttr(default=None)

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


class SceneAllowance(BaseModel):
    """Finite scene transitions authorized before narrator generation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    current_location_id: str = Field(default="unknown", min_length=1, max_length=128)
    allowed_destination_ids: list[str] = Field(default_factory=list, max_length=64)
    allowed_arrival_ids: list[str] = Field(default_factory=list, max_length=64)
    allowed_departure_ids: list[str] = Field(default_factory=list, max_length=64)
    stable_affiliations: dict[str, str] = Field(default_factory=dict, max_length=64)
    character_aliases: dict[str, list[str]] = Field(default_factory=dict, max_length=64)


class SceneClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    location_id: str = Field(min_length=1, max_length=128)
    present_character_ids: list[str] = Field(max_length=64)

    @field_validator("present_character_ids")
    @classmethod
    def validate_present_character_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 128 for item in value):
            raise ValueError("present_character_ids must contain IDs with 1..128 characters")
        if value != sorted(set(value)):
            raise ValueError("present_character_ids must be sorted and unique")
        return value


class MovePlayerSceneOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["move_player"]
    location_id: str = Field(min_length=1, max_length=128)
    evidence: str = Field(min_length=1, max_length=512)


class CharacterArriveSceneOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["character_arrive"]
    character_id: str = Field(min_length=1, max_length=128)
    location_id: str = Field(min_length=1, max_length=128)
    evidence: str = Field(min_length=1, max_length=512)


class CharacterDepartSceneOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["character_depart"]
    character_id: str = Field(min_length=1, max_length=128)
    location_id: str = Field(min_length=1, max_length=128)
    evidence: str = Field(min_length=1, max_length=512)


SceneOperation = Annotated[
    MovePlayerSceneOperation | CharacterArriveSceneOperation | CharacterDepartSceneOperation,
    Field(discriminator="type"),
]


class RPNarratorBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["rp-gateway.rp-narrator-bundle.v1"]
    narrative_text: str = Field(min_length=1, max_length=200_000)
    scene_claims: SceneClaims
    scene_delta: list[SceneOperation] = Field(max_length=16)


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
    scene_allowance: SceneAllowance | None = Field(default=None, exclude=True)


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
    use_llm: bool = True


class WorldApplyRequest(BaseModel):
    proposal_id: str = "latest"
    confirm: bool = False


class WorldPackSummary(BaseModel):
    id: str
    owner_user_id: str | None = None
    visibility: Literal["public", "private"] = "public"
    title: str
    slug: str
    status: str
    premise: str = ""
    manifest_path: str
    state_seed_path: str
    lorebook_path: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)


class WorldPackVisibilityUpdate(BaseModel):
    visibility: Literal["public", "private"]


class WorldPromptCreate(BaseModel):
    title: str = Field(default="Свой мир", min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=WORLD_MARKDOWN_MAX_CHARS)
    source: Literal["text", "markdown_file"] = "text"
    source_filename: str | None = Field(default=None, max_length=WORLD_MARKDOWN_FILENAME_MAX_CHARS)

    @model_validator(mode="after")
    def validate_world_source(self) -> "WorldPromptCreate":
        if "\x00" in self.prompt:
            raise ValueError("world prompt must be plain text without NUL bytes")
        if not self.prompt.strip():
            raise ValueError("world prompt must contain non-whitespace text")
        if self.source == "text":
            if len(self.prompt) > WORLD_PROMPT_MAX_CHARS:
                raise ValueError(f"manual world prompt must not exceed {WORLD_PROMPT_MAX_CHARS} characters")
            if self.source_filename is not None:
                raise ValueError("source_filename is only allowed for markdown_file")
            return self

        filename = (self.source_filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or not filename.lower().endswith(".md"):
            raise ValueError("markdown_file requires a .md source_filename")
        self.source_filename = filename
        return self


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
    owner_user_id: str | None = None
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
    base_url: str = Field(exclude=True, repr=False)
    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    api_key_source: str
    description: str = ""
    rp_fit: str = ""
    context_window: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = "static"
    availability: str = ""
    is_free: bool = False
    pricing_prompt: str = ""
    pricing_completion: str = ""
    pricing_input_cache_read: str = ""
    pricing_input_cache_write: str = ""
    pricing_input_cache_write_1h: str = ""
    rp_specialized: bool = False


class PartyCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    scenario_type: ScenarioType
    worldpack_id: str
    player_character_id: str
    model_profile_id: str


class NarratorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: Literal[1024, 2048, 4096, 8192, 16384] | None = None


class PartyModelUpdate(BaseModel):
    model_profile_id: str
    narrator_settings: NarratorSettings | None = None


class PartyDatasetUpdate(BaseModel):
    review_status: Literal["excluded", "review", "approved"] = "review"
    tags: list[str] = Field(default_factory=list, max_length=40)


class PartyTurnDatasetUpdate(BaseModel):
    review_status: Literal["excluded", "review", "approved"] = "review"
    tags: list[str] = Field(default_factory=list, max_length=40)
    notes: str = Field(default="", max_length=2000)


class TurnFeedbackUpdate(BaseModel):
    rating: Literal["positive", "negative", "none"] | None = None
    liked: bool | None = None


class PartyMemorySummarizeRequest(BaseModel):
    force: bool = True


class PartyPromptPreviewRequest(BaseModel):
    content: str = Field(default="", max_length=12000)
    source: Literal["current", "last"] = "last"


class PartyLoreCardCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=12000)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    always_on: bool = False
    enabled: bool = True
    source_turn_ids: list[int] = Field(default_factory=list, max_length=100)


class PartyLoreCardUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    content: str | None = Field(default=None, min_length=1, max_length=12000)
    keywords: list[str] | None = Field(default=None, max_length=40)
    always_on: bool | None = None
    enabled: bool | None = None
    archived: bool | None = None


class PartyCheckpointCreate(BaseModel):
    label: str = Field(min_length=1, max_length=160)


class PartyBranchCreate(BaseModel):
    checkpoint_id: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=160)
    rp_contract_revision: int | None = Field(default=None, ge=0, le=RP_CONTRACT_MAX_REVISION)


class PartyCharacterStateEditRequest(BaseModel):
    target: Literal["npc", "player"] = "npc"
    character_id: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    status: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=160)
    current_goal: str | None = Field(default=None, max_length=600)
    attitude_to_player: str | None = Field(default=None, max_length=300)
    loyalty: str | None = Field(default=None, max_length=200)
    trust: int | None = Field(default=None, ge=-10, le=10)
    fear: int | None = Field(default=None, ge=0, le=10)
    knowledge: str | None = Field(default=None, max_length=4000)
    obligations: str | None = Field(default=None, max_length=4000)
    hard_constraints: str | None = Field(default=None, max_length=4000)
    secrets: str | None = Field(default=None, max_length=4000)
    confirm: bool = False


class PartyStartRequest(BaseModel):
    idempotency_key: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class PartySummary(BaseModel):
    id: str
    owner_user_id: str | None = None
    title: str
    scenario_type: ScenarioType
    rp_contract_version: Literal["rp-core.v1", "rp-core.v2"] = "rp-core.v1"
    rp_contract_revision: int = Field(default=0, ge=0, le=RP_CONTRACT_MAX_REVISION)
    worldpack_id: str
    player_character_id: str
    model_profile_id: str
    state_campaign_id: str
    status: str
    dataset_review_status: Literal["excluded", "review", "approved"] = "review"
    dataset_tags: list[str] = Field(default_factory=list)
    narrator_settings: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    worldpack: WorldPackSummary | None = None
    player_character: PlayerCharacterSummary | None = None
    model_profile: ModelProfileSummary | None = None


RPStoryMemoryField = Literal[
    "canon",
    "rules_and_abilities",
    "inventory_and_assets",
    "characters",
    "active_threads",
    "resolved_threads",
    "unresolved_hooks",
    "chronology",
]


class RPStoryMemoryCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: RPStoryMemoryField
    fact_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-z0-9][a-z0-9_.:-]{7,79}$")
    action: Literal["retract", "replace"]
    replacement_text: str | None = Field(default=None, max_length=600)

    @model_validator(mode="after")
    def validate_replacement(self) -> "RPStoryMemoryCorrection":
        if self.replacement_text is not None:
            self.replacement_text = self.replacement_text.strip()
        if self.action == "replace" and not self.replacement_text:
            raise ValueError("replacement_text is required for replace")
        if self.action == "retract" and self.replacement_text is not None:
            raise ValueError("replacement_text is only allowed for replace")
        return self


class PartyMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    idempotency_key: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    story_memory_corrections: list[RPStoryMemoryCorrection] = Field(default_factory=list, max_length=10)


class TurnTraceAnnotationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annotation_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    phase_key: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("phase_key", "body")
    @classmethod
    def strip_trace_annotation_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class NarrativeArtifactContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    blueprint_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    slots: dict[str, str] = Field(default_factory=dict, max_length=40)

    @field_validator("slots")
    @classmethod
    def validate_slot_values(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not isinstance(item, str) for item in value.values()):
            raise ValueError("artifact slot values must be strings")
        return value


class NarrativeWorkspaceFileContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    blueprint_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    slots: dict[str, str] = Field(default_factory=dict, max_length=40)

    @field_validator("slots")
    @classmethod
    def validate_slot_values(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not isinstance(item, str) for item in value.values()):
            raise ValueError("workspace file slot values must be strings")
        return value


class NarrativeBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rp-gateway.narrative-bundle.v1", "rp-gateway.narrative-bundle.v2"]
    narrative_text: str = Field(min_length=1, max_length=30000)
    artifacts: list[NarrativeArtifactContent] = Field(default_factory=list, max_length=4)
    workspace_files: list[NarrativeWorkspaceFileContent] = Field(default_factory=list, max_length=8)


class TrainingArtifactSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rp-gateway.training-artifact.v1"] = "rp-gateway.training-artifact.v1"
    artifact_id: str = Field(min_length=1, max_length=160)
    artifact_key: str = Field(min_length=1, max_length=120)
    artifact_revision: int = Field(ge=1)
    surface_turn: int = Field(ge=1)
    blueprint_id: str = Field(min_length=1, max_length=120)
    renderer: str = Field(min_length=1, max_length=80)
    theme: str = Field(min_length=1, max_length=80)
    display_url: str = Field(min_length=1, max_length=300)
    field_ids: list[str] = Field(default_factory=list, max_length=20)
    field_types: dict[str, Literal["text", "password", "otp", "email"]] = Field(default_factory=dict)
    actions: list[Literal["submit", "close", "report"]] = Field(default_factory=list, max_length=8)
    slots: dict[str, str] = Field(default_factory=dict, max_length=40)


class TrainingArtifactEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    artifact_id: str = Field(min_length=1, max_length=160)
    artifact_revision: int = Field(ge=1)
    event_type: TrainingArtifactEventType
    filled_field_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("filled_field_ids")
    @classmethod
    def unique_field_ids(cls, value: list[str]) -> list[str]:
        normalized = [str(item).strip() for item in value if str(item).strip()]
        if any(len(item) > 80 for item in normalized):
            raise ValueError("artifact field id is too long")
        return list(dict.fromkeys(normalized))


class TrainingArtifactEventResponse(BaseModel):
    accepted: bool = True
    event_sequence: int = Field(ge=1)
    duplicate: bool = False


class TrainingWorkspaceEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    file_id: str = Field(min_length=1, max_length=160)
    file_revision: int = Field(ge=1)
    event_type: Literal["file_opened", "file_downloaded", "file_reported", "link_opened", "active_content_enabled"]


class TrainingWorkspaceEventResponse(BaseModel):
    accepted: bool = True
    event_sequence: int = Field(ge=1)
    duplicate: bool = False


class InteractionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_sequence: int = Field(ge=1)
    event_id: str
    artifact_id: str
    artifact_key: str
    blueprint_id: str
    event_type: str
    evidence: str = ""
    score_rule_id: str = ""
    score_once: bool = True
    score_eligible: bool = True
    decision_result: Literal["pass", "fail", "neutral"] = "neutral"


class ShowroomScenarioCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1200)
    status: ShowroomScenarioStatus = "draft"
    scenario_type: ScenarioType
    model_profile_id: str = Field(min_length=1, max_length=240)
    world_source: ShowroomWorldSource = "preset"
    worldpack_id: str | None = Field(default=None, max_length=240)
    world_prompt: str | None = Field(default=None, max_length=6000)
    leaderboard_enabled: bool = True
    leaderboard_metric: ShowroomLeaderboardMetric = "state_path"
    leaderboard_state_path: str = Field(default="meta.turn", min_length=1, max_length=240)
    leaderboard_label: str = Field(default="Очки", min_length=1, max_length=80)
    interactive_links_enabled: bool = False
    interactive_workspace_enabled: bool = False
    sort_order: int = Field(default=100, ge=0, le=10000)


class ShowroomScenarioUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1200)
    status: ShowroomScenarioStatus | None = None
    scenario_type: ScenarioType | None = None
    model_profile_id: str | None = Field(default=None, min_length=1, max_length=240)
    world_source: ShowroomWorldSource | None = None
    worldpack_id: str | None = Field(default=None, max_length=240)
    world_prompt: str | None = Field(default=None, max_length=6000)
    leaderboard_enabled: bool | None = None
    leaderboard_metric: ShowroomLeaderboardMetric | None = None
    leaderboard_state_path: str | None = Field(default=None, min_length=1, max_length=240)
    leaderboard_label: str | None = Field(default=None, min_length=1, max_length=80)
    interactive_links_enabled: bool | None = None
    interactive_workspace_enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10000)


class ShowroomRunCreate(BaseModel):
    character_name: str = Field(min_length=1, max_length=120)
    character_prompt: str = Field(min_length=1, max_length=4000)
    employee_position: str = Field(default="", max_length=160)
    leaderboard_opt_in: bool = True
    client_request_id: str | None = Field(default=None, max_length=160)


class AutoTestCreate(BaseModel):
    source_party_id: str = Field(min_length=1, max_length=120)
    player_prompt: str = Field(min_length=1, max_length=12000)
    turn_count: int = Field(ge=1, le=30)
    player_model_profile_id: str = Field(min_length=1, max_length=240)
    rp_contract_revision: int | None = Field(default=None, ge=0, le=RP_CONTRACT_MAX_REVISION)


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


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=6, max_length=200)
    role: Literal["admin", "user"] = "user"


class UserPasswordUpdate(BaseModel):
    password: str = Field(min_length=6, max_length=200)


class UserStatusUpdate(BaseModel):
    status: Literal["active", "disabled"]


class UserDeleteRequest(BaseModel):
    delete_data: bool = True


class ProviderApiKeyCreate(BaseModel):
    label: str = Field(default="Provider key", min_length=1, max_length=120)
    api_key: str = Field(min_length=1, max_length=400)
    provider: Literal["nvidia", "gemini", "openrouter"] = "nvidia"
    base_url: str | None = Field(default=None, max_length=300)
    is_default: bool = True


class ProviderApiKeyUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    api_key: str | None = Field(default=None, min_length=1, max_length=400)
    is_default: bool | None = None


class ServiceModelUpdate(BaseModel):
    choice_id: str = Field(min_length=1, max_length=120)
