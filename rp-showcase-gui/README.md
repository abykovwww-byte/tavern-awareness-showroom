# RP Showroom GUI

Public, registration-free scenario storefront for `rp-gateway`.

- A showroom scenario is not a world pack. It references a world pack and adds
  its own public title, description, scenario type, model, cover, and leaderboard.
- Anonymous participants receive an HttpOnly visitor cookie from Gateway.
- Gameplay uses showroom-scoped wrapper endpoints; raw party IDs are not exposed.
- Scenario administration reuses the existing Gateway admin role.
- Runtime target is `abykovserv`; this client is deployed through the RP Stack
  Compose template and Ansible.
