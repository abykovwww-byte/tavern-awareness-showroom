# Setup Flow

1. Deploy the committed world pack through the RP Stack IaC workflow.
2. Open Light GUI at `http://192.168.1.88:8010`.
3. Create a party with world `awareness` and scenario type `training`.
4. Create or select a player character and model profile.
5. Verify that turns 1, 3, 5, 7, 8, and 9 expose their authored site surfaces,
   while turns 2, 4, 6, and 10 remain non-site decisions.
6. Verify deterministic typed-event scoring and the final debrief after the
   tenth player response.

Canonical party state is initialized from `state-seed.json`; world context is
read from the files referenced by `manifest.json`.
