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
