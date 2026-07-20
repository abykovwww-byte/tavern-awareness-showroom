# Setup Flow: Предел Дымных Врат

This file walks through the full local flow for review, SillyTavern GUI setup and the first game smoke test.

## 1. Review Pack

Local draft folder:

```text
roles/apps/files/rp-stack/worldpacks/smoke-gate-borderland/
```

Required review points:

- `state-seed.json` is the canonical starting state.
- `world-info/index.md` is prompt memory, not authority.
- `prompts/gm-system.md` and `prompts/authors-note.md` define narrator behavior.
- `prompts/opening-scene.md` is the first message to start play.

## 2. Install On The Server

Do this only when you are ready to replace/start the live campaign state and
make the lorebook visible in SillyTavern.

First deploy the IaC source so the pack exists on the server under
`/srv/apps/rp-stack/worldpacks/smoke-gate-borderland/`. The usual stack flow is:

```text
commit local IaC change -> push -> sudo systemctl start ansible-local-apply.service on the server
```

The Ansible apply copies the lorebook JSON server-side into:

```text
/srv/app-data/rp-stack/data/default-user/worlds/Predel_Dymnyh_Vrat.json
```

After reloading SillyTavern, the lorebook should be visible in the World Info /
Lorebook dropdown. No browser file-picker import is needed.

On the server:

```bash
cd /srv/apps/rp-stack
python3 scripts/install-worldpack.py smoke-gate-borderland
python3 scripts/install-worldpack.py smoke-gate-borderland --confirm
python3 scripts/render-state-block.py
```

The installer is server-side. It does not use the browser file picker. It:

- validates `state-seed.json`;
- backs up the current state file and gateway SQLite database when present;
- writes a new gateway state version into SQLite and `state/current.json`;
- also ensures `sillytavern/Predel_Dymnyh_Vrat.json` exists in `/srv/app-data/rp-stack/data/default-user/worlds/` if Ansible has not already copied it.

After install, reload SillyTavern. The lorebook should be available in the
World Info / Lorebook dropdown as a server-side world file.

Paste the `render-state-block.py` output into the SillyTavern Chat Lorebook
entry named `AUTHORITATIVE_WORLD_STATE`.

## 3. Configure SillyTavern GUI

Open:

```text
http://192.168.1.88:8000
```

Connection settings:

```text
API type: Chat Completion
Chat Completion Source: Custom (OpenAI-compatible)
Base URL: http://rp-gateway:8088/v1
Model: z-ai/glm-5.2
API key: NVIDIA key entered manually in the UI
```

Do not put the NVIDIA key into Git, docs, compose files or prompts.

Prompt setup:

- Put `prompts/gm-system.md` into the active system prompt/custom prompt area.
- Put `prompts/authors-note.md` into Author's Note.
- Select/enable the server-installed `Predel_Dymnyh_Vrat` lorebook in World Info / Lorebook.
- Add a Chat Lorebook entry `AUTHORITATIVE_WORLD_STATE` and keep it always active when possible.

Character/chat setup:

- Create a narrator/GM chat or character for this campaign.
- Paste `prompts/opening-scene.md` as the first scene.
- Keep NPC cards/notes from `characters/index.md` available as reference.

Quick Replies:

- Enable the `RP World` preset if it exists.
- If missing, recreate it from `configs/stscript/quick-replies/rp-world.quick-replies.md`.

## 4. First Smoke Test

Send a normal player action first:

```text
Я осматриваю письмо и серый воск, не трогая его голыми руками. Затем выглядываю в окно и пытаюсь понять, кто из стражников старший.
```

Then test a bounded check:

```text
/check information target=old-ferry-inn skill=2 difficulty=10 goal="понять, кто мог оставить письмо в комнате"
```

Then test world preview:

```text
/world Запомни: игрок заметил, что серый воск на письме похож на старый архивный воск, но это еще не доказательство.
```

If the preview looks right:

```text
/world apply latest
```

Finally:

```text
/world show
```

The expected behavior is: normal RP text stays narrative, `/check` resolves through gateway rules, `/world` previews before mutation, and canonical state changes only after apply.
