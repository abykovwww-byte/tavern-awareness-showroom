# Architecture

## Iteration 1

SillyTavern is the only runtime application in this iteration. It is deployed by Ansible as a Docker Compose service and stores all mutable state in host-mounted directories under `/srv/app-data/rp-stack`.

```text
LAN browser
  -> 192.168.1.88:8000
  -> Docker port bind on 192.168.1.88 only
  -> rp-stack-sillytavern
  -> NVIDIA hosted OpenAI-compatible Chat Completions API
```

The NVIDIA API key is entered in SillyTavern by the user and is not managed by Ansible.

## Iteration 2

The project now stores authoritative world state separately from chat prose.

```text
state/current.json
  -> validated by scripts/validate-state.py
  -> updated only by scripts/apply-state-patch.py --confirm
  -> previous versions copied to state/history/
  -> audit events appended to state/audit.log
  -> rendered into prompt by scripts/render-state-block.py
```

The LLM may generate a proposed patch, but it cannot write `state/current.json` directly. The user reviews or edits the proposed patch and explicitly confirms application.

Future iterations will add:

- FastAPI RP Gateway with SQLite state and rule engine.

## Iteration 3

Frequent checks are now resolved before narration.

```text
SillyTavern Quick Reply
  -> explicit check type and modifiers
  -> scripts/run-check.py
  -> state/checks.log
  -> state/last-check.json
  -> state/proposed/check-<id>.json
  -> <AUTHORITATIVE_OUTCOME>
  -> STscript /inject near the next chat turn
  -> GLM narrates the fixed outcome
```

`run-check.py` supports persuasion, intimidation, deception, stealth,
information search, resource use, feasibility, trust shifts, simple conflict,
and random events. It does not parse arbitrary player prose for bonuses.

State remains authoritative. Quick Reply variables are transient scene controls,
and World Info remains static lore plus rendered authoritative state.

## Iteration 4

The arbiter now runs as a FastAPI service between SillyTavern and NVIDIA.

```text
SillyTavern container
  -> http://rp-gateway:8088/v1/chat/completions
  -> Intent Parser
  -> Rule Engine
  -> SQLite State Store
  -> Adjudicator
  -> Narrative Request Builder
  -> NVIDIA OpenAI-compatible API
  -> Output Validator
  -> one optional repair
  -> OpenAI-compatible response
```

The gateway persists state history in
`/srv/app-data/rp-stack/gateway/rp_gateway.db` and mirrors the current state to
`/srv/apps/rp-stack/state/current.json` for the earlier helper scripts.

The gateway is not published through Nginx and is only reachable inside the
Docker network by default.

## Iteration 5

World management now has a player-facing command layer.

```text
SillyTavern Quick Reply
  -> captures natural-language instruction
  -> sends /world <instruction>
  -> RP Gateway drafts a pending StatePatch
  -> gateway returns readable preview and proposal id
  -> /world apply latest applies transactionally
  -> /world discard latest drops the pending proposal
```

The LLM is allowed to draft JSON, but it still cannot directly mutate canonical
state. The gateway validates generated operations, stores them as pending
patches, and only applies them after an explicit confirmation command.

## Iteration 6

The stack adds a LAN-only Light GUI for the intended play loop.

```text
LAN browser
  -> http://192.168.1.88:8010
  -> rp-light-gui static client
  -> /api proxy to rp-gateway
  -> party-scoped gateway API
```

The central binding is explicit and server-owned:

```text
Party = WorldPack + PlayerCharacter + ModelProfile + State + TurnHistory
```

The browser stores only the selected `party_id` preference. Each game, check,
state, history and GM request goes through `/api/parties/{party_id}/...`.
Gateway resolves that party to the selected world pack, player character,
model profile and `StateStore(state_campaign_id)`.

SillyTavern remains available on port `8000` as a legacy/debug client.

## Iteration 7

Long-running Light GUI parties now have gateway-owned long-term memory.

```text
party turn succeeds
  -> turn remains in SQLite turns
  -> gateway keeps the latest raw turns in the narrator prompt
  -> older unsummarized turns are compressed into memory_summaries
  -> next narrator prompt gets LONG_TERM_PARTY_MEMORY + current state + outcome + raw tail
```

Memory is party-scoped by `campaign_id`, which is the selected party's
`state_campaign_id`. It is context, not authority: canonical state and
`AUTHORITATIVE_OUTCOME` override memory. Each summary records the covered turn
range and `state_version`, so rollback can create a new state version without
destroying turn history or hiding what summary was generated against.
