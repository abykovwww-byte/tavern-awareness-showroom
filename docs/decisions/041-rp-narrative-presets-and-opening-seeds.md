# Decision 041: authored narrative presets and opening seeds

**Дата:** 2026-08-26

## Status

**Decision status: Accepted.** RP contract revision `11` defines authored,
whole-prompt narrative presets and authored opening seeds selected before party
creation.

**Delivery status:** `каркас` for the requirements in
[`registry/041.yml`](registry/041.yml). The mechanism delivery raised the source
ceiling without activation. The separate source activation now adds
`day-watch-moscow-v2` and configures observed revision `11`; Ansible apply and
live-party verification remain separate and are not claimed here.

## Context

A WorldPack currently exposes one narrator system prompt, one authors note, one
opening and one state seed through `manifest.files`. A campaign can legitimately
offer a different narrative emphasis or player starting position, but accepting
client-supplied paths or composing small prompt fragments would weaken the pack
boundary and make the effective prompt hard to audit.

The existing narrator already has fixed `WORLD_SYSTEM_PROMPT` and
`WORLD_AUTHORS_NOTE` positions and budgets. Revision 11 therefore selects whole
authored variants for those positions; it does not add prompt blocks or a new
model call.

## Decision

### Revision and activation boundary

- Source accepts `rp-core.v2` revisions `0..11`; provider canaries may explicitly
  exercise candidates in the same range.
- The mechanism delivery kept ordinary runtime observed at `10`. The separate
  activation adds a conforming revision-11 pack and changes source inventory to
  `11`; live runtime changes only after Ansible apply.
- Packs at revisions `0..10` keep their existing loader, API, storage and prompt
  behavior. A revision-11 pack is rejected by ordinary creation while observed
  is lower than `11`; it is never downgraded silently.

### Closed manifest catalogs

A revision-11 manifest has both non-empty top-level catalogs and both explicit
defaults:

```json
{
  "rp_contract": {"schema_version": "rp-core.v2", "revision": 11},
  "presets": [
    {
      "id": "book",
      "title": "Книжный",
      "world_system_prompt": "presets/book/gm-system.md",
      "world_authors_note": "presets/book/authors-note.md"
    },
    {
      "id": "action",
      "title": "Действие",
      "world_system_prompt": "presets/action/gm-system.md",
      "world_authors_note": "presets/action/authors-note.md"
    },
    {
      "id": "strategic",
      "title": "Стратегический",
      "world_system_prompt": "presets/strategic/gm-system.md",
      "world_authors_note": "presets/strategic/authors-note.md"
    }
  ],
  "presets_default": "action",
  "openings": [
    {
      "id": "independent",
      "title": "Независимый старт",
      "player_role": "Независимый зарегистрированный Иной",
      "prompt": "prompts/openings/independent/opening-scene.md",
      "state_seed": "prompts/openings/independent/state-seed.json"
    },
    {
      "id": "night-trainee",
      "title": "Стажёр Ночного Дозора",
      "player_role": "Стажёр Ночного Дозора",
      "prompt": "prompts/openings/night-trainee/opening-scene.md",
      "state_seed": "prompts/openings/night-trainee/state-seed.json"
    },
    {
      "id": "day-witch",
      "title": "Младшая ведьма Дневного Дозора",
      "player_role": "Младшая ведьма Дневного Дозора",
      "prompt": "prompts/openings/day-witch/opening-scene.md",
      "state_seed": "prompts/openings/day-witch/state-seed.json"
    },
    {
      "id": "inquisition-observer",
      "title": "Наблюдатель Инквизиции",
      "player_role": "Наблюдатель Инквизиции",
      "prompt": "prompts/openings/inquisition-observer/opening-scene.md",
      "state_seed": "prompts/openings/inquisition-observer/state-seed.json"
    }
  ],
  "openings_default": "independent"
}
```

IDs are unique stable ASCII values matching
`^[a-z0-9][a-z0-9_-]{0,63}$`. Every path is a safe existing file inside its
pack. An opening seed path is exactly
`prompts/openings/<id>/state-seed.json`; the filename remains
`state-seed.json` so existing recursive state-schema validation sees it.

The four legacy root declarations remain exact default aliases:

- `files.gm_system` is `prompts/gm-system.md` and is byte-identical to the
  default preset system prompt;
- `files.authors_note` is `prompts/authors-note.md` and is byte-identical to the
  default preset authors note;
- `files.opening_scene` is `prompts/opening-scene.md` and is byte-identical to
  the default opening prompt;
- `files.state_seed` is `state-seed.json` and is byte-identical to the default
  opening seed.

Root `manifest.player_role` equals the default opening `player_role`. Default is
never inferred from array order.

Each opening `player_role`, after the same line-ending/BOM/outer-whitespace
normalization used during materialization, is at most 4000 characters. This is
the inherited `PlayerCharacterCreate.description` limit: draft output is copied
into that field, so a longer authored role would make the normal UI create path
fail validation. Revision 11 adds no separate title-length rule here.

### Prompt authority and authored content

Each preset supplies one complete `world_system_prompt` and one complete
`world_authors_note`. Gateway substitutes them at the existing prompt positions;
it does not concatenate selected and root texts. Including headings, the blocks
remain within `5000` and `1500` characters respectively:
`WORLD_SYSTEM_PROMPT\n<text>` and `WORLD_AUTHORS_NOTE\n<text>`.

Authors must keep the complete world and narrator rules in the system prompt.
The authors note must be a usable, preset-specific brief: it states the required
scene forms/elements and explicit conflict-resolution prohibitions appropriate
to that preset. Those are authoring and review requirements, not a promise of a
semantic classifier. Repository validation mechanically checks closed shapes,
paths, non-empty text, aliases and budgets only; it does not guess prose meaning
or silently shorten authored rules.

For the `day-watch-moscow-v2` activation pack, the LF-normalized
default provides the concrete authoring arithmetic:

| block | current complete block | hard limit | remaining |
|---|---:|---:|---:|
| `WORLD_SYSTEM_PROMPT` | 4821 | 5000 | 179 |
| `WORLD_AUTHORS_NOTE` | 970 | 1500 | 530 |

The system rules are not shortened silently: the 179 characters are editing
headroom, not space for a new semantic layer. Four compact scene forms plus the
conflict block belong only in the authors note, within its 530-character
headroom. If an authored variant does
not fit, pack validation fails and the user decides what to change; tooling does
not weaken or compress rules to make the gate green.

Each opening supplies a complete prompt, player role and full state seed. The
full seed removes fragment-merge semantics, but it does not change the existing
state initialization overlay order:

```text
selected full seed
→ Gateway metadata and world clock
→ PlayerCharacter, including role and known fact
→ explicit starting patch
→ final state-schema validation
```

### Selection and immutable party materialization

Clients send optional `preset_id` and `opening_id`, never paths. Omitted IDs use
the declared defaults. Unknown, duplicate or path-like IDs fail closed; there is
no fallback to the first catalog entry. Player-character draft and create
requests may receive `opening_id`, and the character summary returns the
resolved ID, so the generated description and the later party opening cannot
silently diverge.

At party creation Gateway stores an internal snapshot with selected IDs, exact
materialized system/authors/opening texts, player role, full seed, and SHA-256
of each materialized payload. Later turns read this snapshot, so a pack edit
cannot alter that party's materialized system/authors/opening texts or its
initial-seed input. Branches and autotest descendants inherit the same snapshot.

Hashes are audit checksums only. This decision adds no source-mismatch status,
telemetry, backfill or migration of existing parties. Public summaries may
expose selected IDs and audit hashes, but never the internal materialized prompt
texts or full seed.

## Consequences

- The UI renders top-level catalog summaries and sends stable IDs; it does not
  interpret or submit filesystem paths. The existing raw `manifest` field in
  `WorldPackSummary` is not removed or hidden by this delivery.
- A party has one deterministic opening state and one deterministic prompt pair
  for its lifetime.
- Prompt/history/memory ordering and provider routing are unchanged.
- Pack authors duplicate the default payload at legacy root aliases; repository
  validation catches divergence before delivery.

## Activation content: `day-watch-moscow-v2`

The separate activation delivery adds `day-watch-moscow-v2` beside the unchanged
playable `day-watch-moscow` v1 and configures inventory observed revision `11`.
This source state still requires Ansible apply and live verification.

The v2 manifest has exactly three independently selectable narrative presets:

- `book` — «Книжный»;
- `action` — «Действие», the explicit preset default;
- `strategic` — «Стратегический».

It also has exactly four independently selectable openings, each with its own
complete seed and role for a user-created character:

- `independent` — independent registered Other, the explicit opening default;
- `night-trainee` — Night Watch trainee;
- `day-witch` — junior Day Watch witch;
- `inquisition-observer` — Inquisition observer.

Any of the three presets can be paired with any of the four openings. The v2
pack carries forward the existing canon, factions, stable affiliations,
relationship model, campaign bible and all 20 authored Lore Cards. The agreed
NPC composition remains the same 11 active NPCs; this decision does not add or
remove a character. Every NPC card `content` includes two visible authored
lines, exactly `Примета: ...` and `Манера речи: ...`. Opening prompts repeat
needed appearance/speech cues because opening creation does not retrieve Lore
Cards.

The activation pack does not add `world-clock.json`. Revision 11 is cumulative,
but a new world clock is not part of this content change.

## Non-goals

This mechanism does not add a WorldPack, dynamic/model-generated variants, a
new supervisory or classification pass, prompt-fragment composition, provider
routing, dependencies, telemetry, backfill, an experiment or pilot. It does not
change the canonical builder intake question limit.

## Delivery and verification

1. Mechanism delivery: source ceiling `11`, fail-closed API/storage behavior,
   repository guards, canary range, docs and focused tests. Inventory remains
   observed `10`, and revisions `0..10` remain compatible.
2. Separate activation delivery: add and validate the revision-11 WorldPack and
   change source inventory to observed revision `11`; after merge, apply through Ansible, then prove real UI
   selection, persisted materialization, first prompt and divergent opening
   state. CI and hashes alone are not live evidence.

## Related decisions

- [Decision 032](032-rp-history-first-prompt-and-sectioned-memory.md)
- [Decision 037](037-rp-authored-lore-cards-and-confirmed-drafts.md)
- [Decision 039](039-rp-world-clock-and-authored-events.md)
