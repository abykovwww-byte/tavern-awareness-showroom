# Setup flow

1. Deploy through the RP Stack IaC workflow.
2. Open Light GUI at `http://192.168.1.88:8010`.
3. Create a party with world `awareness-one-day` and explicitly select scenario type `training`.
4. Create a player character whose description includes profession, ordinary responsibilities and authority boundaries.
5. Start the party and verify that turn 1 contains one email.
6. Complete ten player responses and verify that the separate next response is the debrief.

Canonical state is initialized per party from `state-seed.json`.
