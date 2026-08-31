"""Pydantic request/response models."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_serializer

from app.core.config import RP_CONTRACT_MAX_REVISION



WorldChoiceId = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"),
]


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

ActiveScenarioType = Literal["training"]
StoredScenarioType = Literal["rp", "novel", "training"]
ShowroomScenarioStatus = Literal["draft", "published", "archived"]
ShowroomWorldSource = Literal["preset"]
ShowroomLeaderboardMetric = Literal["state_path", "turn_count"]
TrainingArtifactEventType = Literal["link_opened", "form_submitted", "site_closed", "reported"]


class ChatMessage(BaseModel):
    role: str
    content: str | list[Any] | None = ""
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

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








class WorldPackPresetSummary(BaseModel):
    id: WorldChoiceId
    title: str


class WorldPackOpeningSummary(BaseModel):
    id: WorldChoiceId
    title: str
    player_role: str = Field(max_length=4000)


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
    presets: list[WorldPackPresetSummary] | None = None
    presets_default: WorldChoiceId | None = None
    openings: list[WorldPackOpeningSummary] | None = None
    openings_default: WorldChoiceId | None = None

    @model_serializer(mode="wrap")
    def omit_legacy_choice_fields(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.presets is None:
            data.pop("presets", None)
            data.pop("presets_default", None)
        if self.openings is None:
            data.pop("openings", None)
            data.pop("openings_default", None)
        return data


class WorldPackVisibilityUpdate(BaseModel):
    visibility: Literal["public", "private"]




class PlayerTemplate(BaseModel):
    id: str
    name: str
    description: str
    profile: dict[str, Any] = Field(default_factory=dict)




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
    opening_id: WorldChoiceId | None = None
    created_at: str
    updated_at: str

    @model_serializer(mode="wrap")
    def omit_legacy_opening_id(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.opening_id is None:
            data.pop("opening_id", None)
        return data


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
    scenario_type: ActiveScenarioType
    worldpack_id: str
    player_character_id: str
    model_profile_id: str






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




















class PartyStartRequest(BaseModel):
    idempotency_key: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None




class PartySummary(BaseModel):
    id: str
    owner_user_id: str | None = None
    title: str
    scenario_type: StoredScenarioType
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
    preset_id: WorldChoiceId | None = None
    opening_id: WorldChoiceId | None = None
    worldpack_materialization_hashes: dict[str, str] | None = None
    worldpack_materialization: dict[str, Any] | None = Field(default=None, exclude=True, repr=False)
    created_at: str
    updated_at: str
    worldpack: WorldPackSummary | None = None
    player_character: PlayerCharacterSummary | None = None
    model_profile: ModelProfileSummary | None = None

    @model_serializer(mode="wrap")
    def omit_legacy_materialization_fields(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.preset_id is None:
            data.pop("preset_id", None)
        if self.opening_id is None:
            data.pop("opening_id", None)
        if self.worldpack_materialization_hashes is None:
            data.pop("worldpack_materialization_hashes", None)
        return data










class PartyMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    idempotency_key: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None




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


class StructuredNarrativeBundle(BaseModel):
    """Provider-only bundle; Gateway renders its visible surface fields."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rp-gateway.narrative-bundle.v3"]
    narrative_text: str = Field(max_length=30000)
    visible_surfaces: dict[str, dict[str, str]] = Field(min_length=1, max_length=20)
    artifacts: list[NarrativeArtifactContent] = Field(max_length=4)
    workspace_files: list[NarrativeWorkspaceFileContent] = Field(max_length=8)


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
    scenario_type: ActiveScenarioType
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
    scenario_type: ActiveScenarioType | None = None
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






class ValidationResult(BaseModel):
    valid: bool
    violations: list[str] = Field(default_factory=list)
    repair_instruction: str = ""


class HealthResponse(BaseModel):
    status: str
    campaign_id: str
    database: str






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






class ServiceModelUpdate(BaseModel):
    choice_id: str = Field(min_length=1, max_length=120)
