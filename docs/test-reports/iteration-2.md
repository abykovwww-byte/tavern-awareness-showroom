# Iteration 2 Test Report

## Result

Status: PASS locally, pending server apply verification.

## Command

```bash
python scripts/test-state-workflow.py
```

## Covered Scenarios

- Dead NPC cannot be made alive by player declaration.
- Unavailable resource cannot be used as if owned.
- Proposed patch without reason is rejected.
- Invalid JSON is rejected.
- Patch preview without `--confirm` does not modify state.
- User-corrected patch applies with `--confirm`.
- Applied state survives reload from disk.
- Rollback creates a new state version from history.
- State block renders with `<AUTHORITATIVE_WORLD_STATE>`.

## Notes

Runtime server validation must be repeated after Ansible apply.

