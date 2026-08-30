# Setup flow

1. Deploy the standalone stack and committed Showroom catalog through this
   project's IaC workflow.
2. Open the standalone Showroom at its configured address.
3. In the public catalog, select a published scenario backed by WorldPack
   `awareness-one-day`.
4. Start a Showroom run and describe the learner's profession, ordinary
   responsibilities and authority boundaries in the role or position field.
   Do not create a party or player manually: Gateway creates the run-owned
   internal state.
5. Verify that turn 1 contains one email.
6. On turns 4, 6 and 9, verify that the authored simulated site opens from the
   message while the schedule remains on the same turn. The other seven turns
   must not expose a site.
7. Use only invented non-secret form text. Verify that typed events contain field IDs but no entered values and are consumed by the next player response.
8. Complete ten player responses and verify that the separate next response is the debrief.

Canonical state is initialized per Showroom run from `state-seed.json`.
