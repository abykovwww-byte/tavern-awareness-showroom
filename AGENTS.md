# Tavern Awareness Showroom instructions

- Preserve the standalone boundary `browser -> Showroom -> Awareness Gateway -> its own SQLite/state/covers/WorldPacks/providers`.
- This repository targets training scenarios only. RP, novel, prompt-world, Light GUI, and calls to the RP Gateway are outside the product boundary.
- Gateway remains authoritative for authentication, state, idempotency, scoring, training events, artifacts, workspace data, and provider routing. Treat browser-issued IDs, scores, URLs, revisions, and artifact facts as untrusted.
- Keep Gateway database, state files, uploaded covers, session cookies, visitor cookies, and provider credentials separate from RP Stack resources.
- The only top-level WorldPacks after the prune wave are `awareness` and `awareness-one-day`; both must declare only the `training` scenario type.
- Keep public `/api/showroom/*` browser contracts compatible unless a versioned migration is explicitly approved.
- Never commit secrets or mutable runtime data. Use an untracked environment file and host paths outside the source checkout.
- Implement the smallest functional change. Do not add dependencies, framework layers, or adjacent refactors without a demonstrated need.
- Before editing, list the intended files. After editing, run focused checks and report source, build, deployment, activation, and live verification as separate states.
- N0/N1 Compose or CI success does not prove the inherited RP surface has been removed; that proof belongs to the prune and live-acceptance waves.

Baseline checks:

- Validate every retained state seed with `python scripts/validate-state.py --state worldpacks/<slug>/state-seed.json --schema state/schema.json`.
- Validate deterministic training contracts with `python scripts/validate-training-runtime.py --worldpacks worldpacks`.
- Run only the explicit training-focused pytest list from `.github/workflows/ci.yml` during the N2/N3 transition; do not replace it with an unconditional full inherited suite.
- Check Showroom JavaScript syntax and run its four dependency-free Node tests listed in `README.md`.
- Validate both the base Compose file and the optional local-LLM overlay before a shadow start.
- Real proof requires an isolated shadow start, browser-visible acceptance, persisted-result verification, restart continuity, and a tested rollback path.
