# STscript Variables

The state file remains the only long-term source of truth.

## Authoritative Stores

- `state/current.json`: durable campaign state.
- `state/proposed/*.json`: proposed changes awaiting review.
- `state/checks.log`: roll, modifier, result, and proposal audit trail.
- `state/last-check.json`: last generated outcome for prompt injection.

## STscript Local Variables

- `rp_check_type`: selected check type, such as `persuasion` or `stealth`.
- `rp_check_target`: current target id, if any.
- `rp_check_skill`: explicit skill modifier.
- `rp_check_difficulty`: explicit difficulty.
- `rp_check_preparation`: explicit preparation modifier.
- `rp_check_leverage`: explicit leverage modifier.
- `rp_check_resource`: resource id for resource checks.
- `rp_last_outcome`: the latest `<AUTHORITATIVE_OUTCOME>` block.
- `rp_last_check_id`: idempotency key for the latest check.
- `rp_debug`: `on` shows operational details to the user; any other value keeps
  them hidden.

## World Info

World Info / Lorebook entries should contain static lore and the rendered
`<AUTHORITATIVE_WORLD_STATE>` block. They must not contain a competing copy of
relationship scores, resources, or check results.

## Ownership Rule

If values disagree, trust them in this order:

1. `state/current.json`
2. `state/last-check.json` and the injected `<AUTHORITATIVE_OUTCOME>`
3. STscript variables for the current scene only
4. World Info static lore
5. Chat prose
