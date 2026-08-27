# Source provenance

This repository was created as a history-preserving split of the RP Stack
application subtree.

| Field | Value |
|---|---|
| Source repository | `ubuntu_ansible_palybooks` |
| Source subtree | `roles/apps/files/rp-stack` |
| Source base revision | `e16a4027fb091a26f362a0acdd51c5f906f53399` |
| Subtree split revision | `f6074084cbb15f4fc7c8cf6a56f5ed80201eb1d1` |
| Bootstrap branch | `codex/bootstrap-training-project` |

The split preserves source history; it does not prove that every inherited RP
file belongs in the final training-only product. Removal and process-level mode
isolation are reviewed follow-up changes. Conversely, files must not be removed
only because their names look RP-specific: Showroom currently calls the shared
Gateway turn pipeline, so dependency removal requires tests.

Runtime data and secrets are deliberately outside provenance:

- no SQLite file was copied;
- no party state or covers were copied;
- no users, sessions, visitors, runs, or provider keys were copied;
- no server `.env` or local override was copied.

The recommended cutover starts with a fresh standalone database and retains the
old RP Stack database/state as a separate legacy recovery set.
