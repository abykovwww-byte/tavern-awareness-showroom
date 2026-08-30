# Setup Flow

1. Deploy the standalone stack and committed Showroom catalog through this
   project's IaC workflow.
2. Open the standalone Showroom at its configured address.
3. In the public catalog, select a published scenario backed by WorldPack
   `awareness` (`awareness-week` in the committed catalog).
4. Start a Showroom run and fill in the learner role or position requested by
   the scenario. Do not create a party or player manually: Gateway creates the
   run-owned internal state.
5. Verify that turns 1, 3, 5, 7, 8, and 9 expose their authored site surfaces,
   while turns 2, 4, 6, and 10 remain non-site decisions.
6. Verify deterministic typed-event scoring and the final debrief after the
   tenth player response.

Canonical run state is initialized from `state-seed.json`; world context is
read from the files referenced by `manifest.json`.
