# Iteration 3 Test Report

## Scope

STscript/Quick Reply check workflow with server-side adjudication helpers.

## Local Checks

To be run before deployment:

```bash
python3 scripts/test-state-workflow.py
python3 scripts/test-check-workflow.py
python3 scripts/validate-state.py
```

## Server Checks

To be run after Ansible apply:

```bash
cd /srv/apps/rp-stack
python3 scripts/test-state-workflow.py
python3 scripts/test-check-workflow.py
python3 scripts/validate-state.py
```

## Acceptance Mapping

- At least five check types work: covered by `test-check-workflow.py`.
- Checks use state and constraints: resource, target status, and hard-constraint
  cases are covered.
- Outcome fixed before prose: `run-check.py` emits `<AUTHORITATIVE_OUTCOME>`.
- GLM describes but does not decide: covered by `outcome-narration.md`.
- Roll/modifier log: `state/checks.log`.
- Rollback: `rollback-last-check.py` and `apply-state-patch.py --rollback`.
- Negative tests: `test-check-workflow.py`.
- Separate Git commit: required by the IaC workflow.
