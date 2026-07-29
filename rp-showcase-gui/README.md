# RP Showroom GUI

Public, registration-free scenario storefront for `rp-gateway`.

- A showroom scenario is not a world pack. It references a world pack and adds
  its own public title, description, scenario type, model, cover, and leaderboard.
- Anonymous participants receive an HttpOnly visitor cookie from Gateway.
- Gameplay uses showroom-scoped wrapper endpoints; raw party IDs are not exposed.
- Training WorldPacks can show a five-person corporate directory beside the
  session chat. Dynamic job titles are resolved from the employee position once
  at run creation and returned as an immutable run snapshot.
- Scenario administration reuses the existing Gateway admin role.
- Training responses render valid `ПИСЬМО` blocks as Outlook-style email cards
  and valid `СООБЩЕНИЕ` blocks as Telegram-style chat bubbles. Rendering uses
  text nodes only; shown links and attachments are intentionally not clickable.
- Runtime target is `abykovserv`; this client is deployed through the RP Stack
  Compose template and Ansible.

Parser smoke test: `node structured-content.test.js`.
