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

- STscript adjudication helpers;
- FastAPI RP Gateway with SQLite state and rule engine.
