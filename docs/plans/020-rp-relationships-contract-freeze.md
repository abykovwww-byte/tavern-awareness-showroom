# Decision 020 contract freeze

This file freezes the first-slice implementation contracts for Decision 020.
It is an execution artifact, not a replacement for the ADR.

## Storage DDL

The shared turn ledger adds a party-local clock alongside its global row ID:

```sql
ALTER TABLE turns ADD COLUMN party_turn INTEGER;
```

Existing rows are backfilled by the correlated count of rows in the same
`campaign_id` with `id <= current id`. New rows store the exact committed
`state.meta.turn`. `turns.id` remains the identity used by idempotency and
rollback; `party_turn` is the only relationship clock.

```sql
CREATE TABLE IF NOT EXISTS relationship_causes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    axis TEXT NOT NULL,
    event_id TEXT NOT NULL,
    weight INTEGER NOT NULL,
    turn_id INTEGER NOT NULL,
    party_turn INTEGER NOT NULL,
    expires_turn INTEGER,
    evidence TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(campaign_id, character_id, axis, event_id, turn_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
);

CREATE TABLE IF NOT EXISTS character_badges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    badge_kind TEXT NOT NULL,
    badge_id TEXT NOT NULL,
    party_turn INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(campaign_id, character_id, badge_kind, badge_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
);

CREATE TABLE IF NOT EXISTS narrative_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    axis TEXT NOT NULL,
    event_id TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_turn INTEGER NOT NULL,
    due_turn INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    resolution TEXT,
    resolved_turn INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
);

CREATE TABLE IF NOT EXISTS character_axis_state (
    campaign_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    axis TEXT NOT NULL,
    band TEXT NOT NULL,
    band_since_turn INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(campaign_id, character_id, axis),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
);

CREATE INDEX IF NOT EXISTS idx_relationship_causes_lookup
    ON relationship_causes(campaign_id, character_id, axis, party_turn);
CREATE INDEX IF NOT EXISTS idx_narrative_events_active
    ON narrative_events(campaign_id, status, due_turn);
```

## Service signatures

```python
class RelationshipStore:
    def add_cause(self, *, character_id: str, axis: str, event_id: str,
                  weight: int, turn_id: int, party_turn: int, expires_turn: int | None,
                  evidence: str, source: str) -> bool: ...
    def value(self, character_id: str, axis: str, at_party_turn: int) -> int: ...
    def set_badge(self, *, character_id: str, badge_kind: str, badge_id: str,
                  party_turn: int, payload: dict | None = None) -> bool: ...
    def active_events(self, at_party_turn: int) -> list[dict]: ...
    def pressure_rows(self, at_party_turn: int) -> list[dict]: ...

class RelationshipMechanics:
    def apply_events(self, *, turn_id: int, party_turn: int, events: list[dict]) -> list[dict]: ...
    def advance_turn(self, party_turn: int) -> list[dict]: ...
    def pressure_block(self, party_turn: int, character_names: dict[str, str]) -> str | None: ...

class RelationshipExtractionService:
    async def process_turn(self, turn_id: int, authorization: str | None = None) -> dict: ...
    def parse_response(self, payload: object, *, aliases: dict[str, list[str]], turn_text: str) -> dict: ...
```

The three services receive the party-scoped `StateStore` and the validated
WorldPack relationship model in their constructors. Mechanics receives no
model client. `process_turn` is idempotent through the frozen cause uniqueness
constraint and the existing `relationship_extraction` service job keyed by the
recorded turn request ID.

`turn_id` is always the global `turns.id` used for idempotency and rollback.
`party_turn` is the committed party-local `state.meta.turn` and is the only
clock used for expiry, boundary progression, event deadlines, badges and prompt
pressure. Every recorded turn stores both values in `turns`; extraction resolves
`party_turn` from that row and treats a missing value as an audited technical
failure, not as a model-response rejection.

## Extraction response

```json
{"events": [{"character_mention": "Иван", "event_id": "insult_public", "evidence": "..."}]}
```

The complete response is rejected without retry when it contains any numeric
value, more than five events, a malformed shape, an unknown or ambiguous alias,
non-verbatim evidence, an unknown event identifier, or an event without
non-empty evidence. Audit rejection codes are exactly:
`malformed_response`, `missing_evidence`, `evidence_not_verbatim`,
`mention_missing`, `mention_not_in_evidence`, `unresolved_mention`,
`ambiguous_mention`, `unknown_event_id`, `numeric_field_present`,
`too_many_events`, `character_id_present`.

## WorldPack manifest and model

The manifest entry is:

```json
"relationships": {
  "schema_version": "rp-relationships.v2",
  "model": "relationships/model.json"
}
```

The `model.json` shape, identifiers and initial values are the JSON object in
Decision 020 section B.4. Unknown keys are rejected by preflight. The first
slice permits only axis `loyalty`, badge kinds `wound` and `role`, and boundary
events `crack`, `ultimatum`, `plot`, `strike`, and `favour`.

## Prompt boundary

`RELATIONSHIP_PRESSURE` is computed only for `scenario_type == "rp"` and is
inserted after state summary and `AUTHORITATIVE_OUTCOME`, before the current
player message. It may contain character display names, Russian band labels,
qualitative pressure, and the generic plot-tell instruction. It must not
contain axis values, weights, numeric clock residue, character/event IDs,
accomplice or target IDs, strike form, or raw `payload_json`.
