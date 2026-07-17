# 003: STscript Check Adjudication

## Status

Accepted for iteration 3.

## Context

Iteration 2 made world state durable but still required a fully manual state
patch loop. Iteration 3 needs frequent checks with explicit commands or Quick
Replies, fixed outcomes before prose, roll/modifier logs, rollback, and negative
tests.

References checked:

- https://docs.sillytavern.app/usage/st-script/
- https://docs.sillytavern.app/usage/st-script/#quick-replies-script-library-and-auto-execution
- https://docs.sillytavern.app/usage/st-script/#prompt-injections

The SillyTavern STscript documentation supports local variables, Quick Replies
as script buttons, `/messages`, and `/inject` prompt injections. It does not
provide a safe native way for a browser-side script to read and validate
`/srv/apps/rp-stack/state/current.json` directly. For that reason, iteration 3
keeps the rule calculation in a server helper and uses STscript as the explicit
UI workflow and prompt-injection layer.

## Decision

Add a bounded adjudication helper:

```text
scripts/run-check.py
  -> reads state/current.json
  -> validates hard constraints and resources
  -> computes final_score = skill + preparation + leverage + relation + roll - difficulty
  -> writes state/checks.log
  -> writes state/last-check.json
  -> writes state/proposed/check-<id>.json
  -> prints <AUTHORITATIVE_OUTCOME>
```

Supported check types:

- `persuasion`
- `intimidation`
- `deception`
- `stealth`
- `information`
- `resource`
- `feasibility`
- `trust`
- `conflict`
- `random_event`

Allowed result labels:

- `critical_failure`
- `failure`
- `failure_with_progress`
- `partial_success`
- `success`
- `critical_success`

Quick Replies and STscript snippets live under:

```text
configs/stscript/checks/
configs/stscript/quick-replies/
configs/stscript/variables.md
```

Narration prompts live under:

```text
configs/prompts/outcome-narration.md
configs/prompts/outcome-repair.md
```

## Guardrails

- Dead, missing, or incapacitated NPC targets block normal targeted checks.
- Unavailable or insufficient resources block resource checks.
- Numeric relationship deltas are clamped to documented ranges.
- A critical success cannot override hard constraints.
- A repeated check id returns the previous result instead of generating a new
  patch.
- Applying a state patch with the same `check_id` twice is rejected by
  `apply-state-patch.py`.
- Full player prose is not written to logs; only a short SHA-256 digest is kept
  when `--action-detail` is supplied.
- Roll, modifiers, difficulty, final score, and blocked reasons are logged.
- State changes still require `apply-state-patch.py --confirm`.

## Consequences

- Outcome is fixed before GLM narration.
- GLM describes the result; it does not decide it.
- The user can rollback either the last injected outcome or the last applied
  state patch.
- This is still not a fully integrated API loop. Iteration 4 will move the same
  rules behind a FastAPI OpenAI-compatible gateway.
