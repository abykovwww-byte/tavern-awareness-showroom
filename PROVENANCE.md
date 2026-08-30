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

The split preserves source history. The later training-only prune removed the
dedicated RP, novel, Light GUI, world-clock, relationship and GM surface while
retaining the shared Gateway turn/state primitives required by Showroom. The
public provider-flow regression exercises scenario start and one provider turn
with a mocked provider completion. It verifies route integration and retained
turn/state dependencies, but it is not a live-provider or full-course runtime
proof.

Runtime data and secrets are deliberately outside provenance:

- no SQLite file was copied;
- no party state or covers were copied;
- no users, sessions, visitors, runs, or provider keys were copied;
- no server `.env` or local override was copied.

The production stack starts with a fresh standalone database. Historical RP
Stack data is not imported into this project.
