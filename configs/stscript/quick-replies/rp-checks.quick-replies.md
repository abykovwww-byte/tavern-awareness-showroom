# RP Checks Quick Replies

Create a Quick Reply preset named `RP Checks`, then add these labels and script
bodies. The buttons intentionally do not parse arbitrary player prose. They
collect explicit check inputs, show the exact server helper command, and inject
the fixed outcome into the next prompt.

The SillyTavern docs describe Quick Replies as a built-in extension for storing
and executing STscript, including `/qr-create`, `/qr-presetadd`, `/run`, and
auto-execution options.

## Setup

```stscript
/qr-presetadd slots=16 inject=false RP Checks
```

## Check Buttons

Label: `Check: Persuasion`

```stscript
/run rp-checks.persuasion
```

Label: `Check: Intimidation`

```stscript
/run rp-checks.intimidation
```

Label: `Check: Deception`

```stscript
/run rp-checks.deception
```

Label: `Check: Stealth`

```stscript
/run rp-checks.stealth
```

Label: `Check: Information`

```stscript
/run rp-checks.information
```

Label: `Check: Resource`

```stscript
/run rp-checks.resource
```

Label: `Check: Feasibility`

```stscript
/run rp-checks.feasibility
```

Label: `Check: Trust`

```stscript
/run rp-checks.trust
```

Label: `Check: Conflict`

```stscript
/run rp-checks.conflict
```

Label: `Random Event`

```stscript
/run rp-checks.random-event
```

## State Buttons

Label: `Inject Outcome`

```stscript
/run rp-checks.inject-last-outcome
```

Label: `Clear Outcome`

```stscript
/run rp-checks.clear-last-outcome
```

Label: `Show Last Check`

```stscript
/echo {{getvar::rp_last_outcome}}
```

Label: `GM Debug`

```stscript
/setvar key=rp_debug on |
/listinjects
```

Label: `Hide GM Debug`

```stscript
/setvar key=rp_debug off
```

## Server Commands Behind The Buttons

Run these in `/srv/apps/rp-stack` after choosing a Quick Reply:

```bash
python3 scripts/run-check.py --type persuasion --target advisor --skill 2 --difficulty 12
python3 scripts/apply-state-patch.py --patch state/proposed/check-<id>.json
python3 scripts/apply-state-patch.py --patch state/proposed/check-<id>.json --confirm
python3 scripts/rollback-last-check.py --confirm
python3 scripts/apply-state-patch.py --rollback latest --confirm
```

The first generated block must be pasted through `Inject Outcome` before asking
GLM to narrate. The proposed patch remains review-first unless you explicitly
apply it with `--confirm`.
