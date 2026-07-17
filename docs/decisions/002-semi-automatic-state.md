# 002: Semi-Automatic World State

## Status

Accepted for iteration 2.

## Context

The campaign needs durable world state separate from SillyTavern chat prose. State changes must be inspectable, rejectable, and correctable by the user before becoming authoritative.

References checked:

- https://docs.sillytavern.app/usage/core-concepts/worldinfo/
- https://docs.sillytavern.app/usage/core-concepts/authors-note/
- https://docs.sillytavern.app/usage/st-script/
- https://www.rfc-editor.org/info/rfc6902/
- https://json-schema.org/

## Decision

Use JSON state files and JSON Patch-style proposed changes.

Runtime files:

```text
state/schema.json
state/current.json
state/history/
state/proposed/
state/audit.log
```

Git-managed files:

- formal schema;
- example campaign state;
- state updater prompt;
- state injection prompt;
- validation/apply/render/test scripts;
- documentation.

Mutable runtime files `state/current.json` and `state/audit.log` are seeded by Ansible with `force: false` so future IaC applies do not overwrite a live campaign.

## Workflow

1. The user asks the LLM to produce a proposed patch using `configs/prompts/state-updater.md`.
2. The proposed JSON is saved under `state/proposed/`.
3. `validate-state.py` validates the current state and proposed patch.
4. `apply-state-patch.py` previews by default.
5. `apply-state-patch.py --confirm` creates a backup, applies the patch, bumps state version and turn, and writes audit log.
6. `render-state-block.py` prints `<AUTHORITATIVE_WORLD_STATE>...</AUTHORITATIVE_WORLD_STATE>` for SillyTavern prompt injection.

## Consequences

- State is durable and independent from chat text.
- The LLM cannot silently mutate campaign facts.
- The workflow is manual but auditable.
- Full automation is deferred to STscript in iteration 3 and FastAPI in iteration 4.

