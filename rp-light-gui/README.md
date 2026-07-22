# RP Light GUI

Small LAN-only browser client for `rp-gateway`.

It is intentionally shipped as static HTML/CSS/JS served by nginx. The stack can
build it without npm registry access, and all provider calls still go through
`rp-gateway`.

Runtime URL after deploy:

```text
http://192.168.1.88:8010
```

The client uses only party-scoped API routes:

```text
/api/worldpacks
/api/worldpacks/prompt
/api/model-profiles
/api/parties
/api/parties/{party_id}
/api/parties/{party_id}/messages
/api/parties/{party_id}/world/instruct
```

Model dropdown behavior:

- gateway first seeds a curated static NVIDIA NIM catalog with RP-focused
  descriptions;
- when enabled, gateway tries to refresh from `build.nvidia.com/models?q=llm`;
- when `NVIDIA_API_KEY` is set, gateway also tries the OpenAI-compatible
  `/v1/models` endpoint;
- if live refresh fails, the static catalog remains available.

Party creation supports installed worldpacks or prompt-generated worlds. Prompt
worlds are saved by gateway as generated worldpacks under the party state volume,
then referenced by the normal party registry.

Party creation also requires an explicit scenario type: `rp`, `novel`, or
`training`. The type is persisted on the party and controls Gateway mechanics;
worldpacks can advertise supported types but cannot select one automatically.
