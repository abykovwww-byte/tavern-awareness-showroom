# Decision 006: Light GUI Party Flow

Date: 2026-07-20

## Status

Accepted. MVP implemented for the LAN-only `rp-light-gui` client and
party-scoped gateway API.

## Context

The original backend-compatible client was sufficient as an RP surface, but its UI was
too heavy for the intended play loop. The project needs a small LAN-only player
GUI that treats Codex-generated world packs, player characters, model selection,
chat, and GM controls as one coherent game session.

The current `rp-gateway` does not know when the user switches worlds in
that client. It starts with a single `CAMPAIGN_ID`, creates one `StateStore`,
and resolves all state requests against that fixed campaign. That is acceptable
for a single campaign, but it cannot support multiple worlds, multiple parties,
or switching between saved games.

The next architecture must make the game binding explicit and server-owned:

```text
Party = WorldPack + PlayerCharacter + ModelProfile + State + TurnHistory
```

The light GUI selects a party. The gateway resolves every game request through
that party. Global client-side lorebook selection must not be the source of
truth for this flow.

## Decision

Add a first-class party/session layer to `rp-gateway`, then build `rp-light-gui`
as a thin web client over that API.

The light GUI flow is:

1. New party.
2. Select a world from Codex-created world packs.
3. Select an existing player character, or create one through an LLM-assisted
   character creation dialogue.
4. Select an LLM/model profile.
5. Start the game chat.
6. During play, use normal chat plus a compact GM mode for state changes,
   checks, rollback, and service commands.

The gateway, not the browser, owns the active binding:

```text
browser route /parties/{party_id}
  -> rp-light-gui sends party_id on every request
  -> rp-gateway loads Party(party_id)
  -> gateway resolves world pack, player character, model profile, state store
  -> gateway adjudicates, calls the selected model, writes state/history
```

## Data Model

Initial SQLite tables should be small and migration-friendly.

```text
worldpacks(
  id text primary key,
  title text not null,
  slug text not null unique,
  status text not null,
  manifest_path text not null,
  state_seed_path text not null,
  lorebook_path text,
  manifest_json text not null,
  created_at text not null,
  updated_at text not null
)

player_characters(
  id text primary key,
  worldpack_id text not null references worldpacks(id),
  name text not null,
  description text not null,
  status text not null,
  starting_state_patch_json text,
  profile_json text not null,
  created_at text not null,
  updated_at text not null
)

model_profiles(
  id text primary key,
  title text not null,
  provider text not null,
  base_url text not null,
  model text not null,
  params_json text not null,
  api_key_source text not null,
  created_at text not null,
  updated_at text not null
)

parties(
  id text primary key,
  title text not null,
  worldpack_id text not null references worldpacks(id),
  player_character_id text not null references player_characters(id),
  model_profile_id text not null references model_profiles(id),
  state_campaign_id text not null unique,
  status text not null,
  created_at text not null,
  updated_at text not null
)

turns(
  id text primary key,
  party_id text not null references parties(id),
  state_version integer,
  role text not null,
  content text not null,
  metadata_json text not null,
  created_at text not null
)
```

The existing `state_versions.campaign_id` can map to `parties.state_campaign_id`
for compatibility. In the MVP, `state_campaign_id` may simply equal `party_id`.

## Gateway API

The existing single-campaign endpoints may stay temporarily for compatibility,
but the light GUI should use party-scoped endpoints only.

```text
GET  /api/worldpacks
GET  /api/worldpacks/{worldpack_id}
GET  /api/worldpacks/{worldpack_id}/player-templates

GET  /api/player-characters?worldpack_id=...
POST /api/player-characters/draft
POST /api/player-characters

GET  /api/model-profiles

GET  /api/parties
POST /api/parties
GET  /api/parties/{party_id}
POST /api/parties/{party_id}/activate

GET  /api/parties/{party_id}/state
GET  /api/parties/{party_id}/history
POST /api/parties/{party_id}/messages
POST /api/parties/{party_id}/checks
POST /api/parties/{party_id}/world/instruct
POST /api/parties/{party_id}/world/apply
POST /api/parties/{party_id}/world/discard
POST /api/parties/{party_id}/rollback
```

`POST /api/parties/{party_id}/messages` is the primary game endpoint. It should
return both the assistant message and the state metadata needed by the GUI.

```json
{
  "message": {
    "role": "assistant",
    "content": "..."
  },
  "party_id": "...",
  "state_version": 12,
  "outcome": {
    "type": "feasibility",
    "status": "success"
  },
  "pending_proposal": null
}
```

OpenAI-compatible `/v1/chat/completions` can remain for external integrations, but it is
not the preferred API for the light GUI.

## Light GUI Screens

### New Party

- Show available world packs generated by Codex.
- Show world status: draft, installed, playable, archived.
- Let the user inspect a short premise before choosing.

### Character

- Show existing characters for the selected world.
- Offer "create character" as a chat-like assistant flow.
- Save the resulting player profile server-side.

### Model

- Show safe server-defined model profiles.
- Do not expose raw API keys to the browser.
- Store provider/model/settings in `model_profiles`.

### Game

- Main chat.
- Active party/world/player/model summary.
- Service buttons: state, history, rollback, checks, GM mode.
- GM mode panel for `/world`-style instructions, proposal preview, apply,
  discard, and rollback.

## Implementation Plan

### Phase 1: Gateway party core

- [x] Add database migrations/bootstrap for `worldpacks`, `player_characters`,
      `model_profiles`, `parties`, and `turns`.
- [x] Add repository/service layer for party lookup and creation.
- [x] Scan installed `worldpacks/*/manifest.json` into `worldpacks`.
- [x] Seed at least one model profile from environment/config.
- [x] Create a party from selected world pack, character, and model profile.
- [x] Initialize party state from the selected world pack `state-seed.json`.
- [x] Make `StateStore` usable per request by `state_campaign_id`.

### Phase 2: Party-scoped gameplay API

- [x] Add party-scoped state/history endpoints.
- [x] Add `POST /api/parties/{party_id}/messages`.
- [x] Route adjudication and narrative calls through the selected party.
- [x] Persist turn history.
- [x] Add party-scoped checks.
- [x] Add party-scoped world instruction/apply/discard endpoints.
- [x] Add party-scoped rollback.
- [x] Keep current `/v1/chat/completions` working as a compatibility endpoint during the
      transition.

### Phase 3: Character creation

- [x] Define player character JSON schema.
- [x] Add deterministic character draft endpoint.
- [x] Convert approved character drafts into saved `player_characters`.
- [ ] Apply optional starting state patch when creating a party.

### Phase 4: Light GUI MVP

- [x] Add `rp-light-gui` app to the Compose stack.
- [x] Add Ansible variables/templates for the new service.
- [x] Build screens: party list, new party wizard, game chat, GM mode.
- [x] Store active party in the browser route/local preference only; gateway
      remains authoritative.
- [x] Add LAN-only access, matching the current trusted bind-host model.

### Phase 5: Verification

- [ ] Unit test party creation and state initialization.
- [ ] Unit test world pack scanning.
- [ ] Unit test party-scoped state isolation.
- [ ] API test full flow: create party, send message, state changes only for
      that party.
- [ ] Browser test light GUI happy path.
- [x] Keep Light GUI as the supported browser client.

## Open Questions

- The supported browser path is now `rp-light-gui` only.
- Should model profiles use only server `.env` keys, or also allow per-party
  user-entered keys stored outside Git?
- Should Codex world creation automatically register the world pack in gateway
  storage, or should the gateway rescan world packs on startup and on demand?
- Should party export/import be added in the MVP, or after the first playable
  light GUI?

## Notes For The Next Thread

The critical architectural change is not the React UI. It is making
`party_id` the required game context. Once gateway requests are party-scoped,
world switching, character switching, model selection, history, rollback, and GM
mode all become ordinary operations against the selected party.
