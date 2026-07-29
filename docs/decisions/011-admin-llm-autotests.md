# Decision 011: Admin LLM-vs-LLM Auto-tests

## Status

Accepted for Light GUI admin accounts.

## Goal

Allow an administrator to describe how an LLM should play a character and run
up to 30 automated player turns against the normal Gateway narrator. The
auto-player provider/model is selected independently from the party narrator
and is intentionally limited to OpenRouter or the private Local Gemma profile.

## Isolation and authority

- A run creates a state checkpoint at the selected party's current head and
  forks an internal party branch from that checkpoint. It does not create a
  second Party and does not return to the world pack state seed.
- The branch has its own Gateway campaign/state identity. The checkpoint state,
  visible turn prefix, checks, memory chapters, journal entries, and active lore
  cards are copied with branch-local turn identifiers. Subsequent branch writes
  cannot mutate the main party line.
- Every generated player action enters the normal party message path. Gateway
  remains authoritative for scenario rules, checks, state, memory, validation,
  and narrator model routing.
- The auto-player sees only its public character description and the visible
  player/GM transcript. It never receives canonical state, hidden training
  scores, rubrics, answer keys, prompt-inspector blocks, or service data.
- Training parties therefore preserve the deterministic one-authored-turn rule
  and do not leak feedback before the authored debrief.

## Durability

Runs are stored in SQLite with requested/completed turn counts, safe-fallback
turn count, status, phase, selected player profile, prompt, last action, and error. A deterministic
idempotency key is used for each narrator turn. Running jobs resume after a
Gateway restart without duplicating a recorded turn.

If the narrator still fails validation after its repair attempt, the Gateway
records the validator-safe fallback as that branch turn and increments the
visible fallback counter instead of terminating the whole endurance run.

Stop requests are cooperative: the current provider request is allowed to
finish so the Gateway does not leave a half-written turn request. The run stops
before the next player/narrator boundary.

The source party remains writable while an auto-test runs because the worker
writes only to the branch. Branch views in Light GUI are read-only and appear
inside the source party's checkpoint/branch tools instead of the party list.

## API

All endpoints require an admin role when Gateway authentication is enabled:

```text
GET  /api/admin/autotests/models
GET  /api/admin/autotests?source_party_id={active_party_id}
POST /api/admin/autotests
POST /api/admin/autotests/{run_id}/stop
GET  /api/parties/{party_id}/branches
POST /api/parties/{party_id}/branches
GET  /api/parties/{party_id}/branches/{branch_id}
```

`POST /api/admin/autotests` accepts a source party, player behavior prompt,
player model profile, and `turn_count` from 1 through 30.

Light GUI lists runs only for the active source party. Switching parties clears
the visible run list and reloads it with `source_party_id`; stale polling
responses are discarded if the active party changed while the request ran.
