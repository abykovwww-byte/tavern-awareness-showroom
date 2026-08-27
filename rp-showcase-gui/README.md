# Awareness Showroom GUI

Public, registration-free storefront for training scenarios served by the
standalone Awareness Gateway.

- A scenario references either `awareness` or `awareness-one-day` and adds its
  public title, description, model, cover and leaderboard presentation.
- Scenario type is fixed to `training`; world source is fixed to a preset
  WorldPack. RP and prompt-world controls are absent.
- Anonymous participants receive the dedicated HttpOnly visitor cookie from
  Awareness Gateway; raw internal party IDs are not exposed.
- Gateway remains authoritative for results, scoring, artifacts, workspace
  events and training progression.
- Administration uses the standalone Gateway admin session and credentials.

The client is built by this repository's `compose.yml` and proxies `/api/*` to
the `awareness-gateway` service. It does not call or join RP Stack.

Run the browser checks through `scripts/ci.ps1`; a source check is not a live
browser or provider proof.
