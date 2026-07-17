# Testing STscript Checks

Run these commands on the server from `/srv/apps/rp-stack`.

## Automated Negative Tests

```bash
python3 scripts/test-check-workflow.py
```

Covered cases:

- high difficulty produces a plausible failure;
- long stylish player prose does not create a mechanical bonus by itself;
- unavailable resources block checks;
- hard constraints override critical success;
- at least five check types render `<AUTHORITATIVE_OUTCOME>`;
- repeated state patch application for the same `check_id` is rejected;
- rollback restores resource values;
- last generated outcome can be cleared before narration;
- logs do not contain API-key-looking markers.

## Manual SillyTavern Smoke Test

1. Enable Quick Replies in SillyTavern.
2. Create a preset named `RP Checks`.
3. Copy the relevant snippets from `configs/stscript/checks/` into Quick Reply
   buttons, or use `configs/stscript/quick-replies/rp-checks.quick-replies.md`
   as the button map.
4. Click `Check: Persuasion` and enter explicit target, skill, and difficulty.
5. Run the printed server command, for example:

```bash
python3 scripts/run-check.py --type persuasion --target advisor --skill 2 --difficulty 12
```

6. Paste the printed `<AUTHORITATIVE_OUTCOME>` into the `Inject Outcome` Quick
   Reply.
7. Ask GLM to narrate the next beat with `configs/prompts/outcome-narration.md`
   active.
8. If GLM contradicts the fixed outcome, regenerate once using
   `configs/prompts/outcome-repair.md`.
9. Review the generated patch:

```bash
python3 scripts/validate-state.py --patch state/proposed/check-<id>.json
python3 scripts/apply-state-patch.py --patch state/proposed/check-<id>.json
```

10. Apply only after review:

```bash
python3 scripts/apply-state-patch.py --patch state/proposed/check-<id>.json --confirm
```

## Rollback

Clear the last outcome before the next narrator response:

```bash
python3 scripts/rollback-last-check.py
python3 scripts/rollback-last-check.py --confirm
```

Rollback the last applied state patch:

```bash
python3 scripts/apply-state-patch.py --rollback latest
python3 scripts/apply-state-patch.py --rollback latest --confirm
```

After rollback, clear the `authoritative_outcome` injection in SillyTavern with
the `Clear Outcome` Quick Reply.
