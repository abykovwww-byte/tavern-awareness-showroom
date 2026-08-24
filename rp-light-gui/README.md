# RP Light GUI

Small LAN-only browser client for `rp-gateway`.

It is intentionally shipped as static HTML/CSS/JS served by nginx. The stack can
build it without npm registry access, and all provider calls still go through
`rp-gateway`.

Runtime URL after deploy:

```text
http://192.168.1.88:8010
```

## Request trace workbench

Light GUI also serves an authenticated operator page at `/trace.html`. It loads
the user's parties and checkpoint branches itself, so a copied link can point to
an exact request without relying on in-memory chat state:

```text
/trace.html?party_id=<party_id>&branch_id=<branch_id>&request_id=<request_id>
```

`branch_id` and `request_id` are optional. The page lists request-scoped traces,
shows only phases actually returned by Gateway, separates main and background
lanes, compares up to four requests by `alignment_key`, and posts phase comments
without accepting a browser-supplied author. Partial or missing capture is shown
as unknown rather than as proof that a phase did not run.

The trace workbench uses these owner/admin-scoped routes:

```text
/api/turn-traces/parties
/api/turn-traces/parties/{party_id}/branches
/api/parties/{party_id}/turn-traces
/api/parties/{party_id}/turn-traces/{request_id}
/api/parties/{party_id}/turn-traces/{request_id}/annotations
```

It is shipped as plain HTML/CSS/JS with no package-manager dependency. Run its
focused checks with `node --check trace.js` and `node trace.test.js`.

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

- the UI first selects `Local`, `Gemini`, or `OpenRouter`, then shows only
  models from that provider;
- gateway seeds a curated static catalog and refreshes each provider through
  its OpenAI-compatible `/models` endpoint;
- OpenRouter models are filtered for text output, useful context, and RP quality;
  specialized storytelling models are ranked first and free models are marked;
- provider keys can come from server environment variables or the admin key
  store, and are never sent to the browser;
- if live refresh fails, the static catalog remains available.

Party creation supports installed worldpacks or prompt-generated worlds. Prompt
worlds are saved by gateway as generated worldpacks under the party state volume,
then referenced by the normal party registry.

Party creation also requires an explicit scenario type: `rp` or `training`.
The type is persisted on the party and controls Gateway mechanics;
worldpacks can advertise supported types but cannot select one automatically.
