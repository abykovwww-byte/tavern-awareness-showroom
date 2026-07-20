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
/api/model-profiles
/api/parties
/api/parties/{party_id}/messages
/api/parties/{party_id}/world/instruct
```
