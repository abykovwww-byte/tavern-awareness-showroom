# Decision 012: Public showroom scenarios

Date: 2026-07-28
Status: accepted and implemented

## Context

The showroom must let an unregistered visitor choose an admin-authored scenario,
describe a character, and continue in a minimal chat. A storefront scenario is
not a WorldPack: several storefront scenarios may reuse one world while having
different public names, modes, models, covers, and leaderboard rules.

## Decision

The Gateway owns a separate `ShowroomScenario` aggregate:

`PublicTitle + PublicDescription + ScenarioType + ModelProfile + WorldPackReference + OptionalCover + LeaderboardPolicy`.

An admin explicitly selects `rp`, `novel`, or `training`, an existing provider/model
profile, and either an installed WorldPack or a free-form world prompt. A prompt
source creates a private internal WorldPack; the public scenario remains the stable
storefront entity.

Visitors do not register. The Gateway issues a random HttpOnly cookie and maps:

`AnonymousVisitor -> ShowroomRun -> Party -> Character + State + TurnHistory`.

The public API exposes only run IDs. The underlying party ID and technical showroom
owner are not returned. Every run is checked against the visitor cookie before the
Gateway delegates to the existing party start, history, and message handlers.

Each scenario has its own leaderboard. Its score is either a numeric canonical-state
path or the committed turn count. For Awareness training the intended path is
`player.resources.awareness-score`. Participation is opt-in per run.

## Deployment

The existing Light GUI remains on port 8010. The separate `rp-showcase-gui` is served
on port 8011 and proxies both public and admin API calls to the same Gateway. Covers
are stored under the Gateway `/data` volume. All durable changes are deployed only
through the repository and `ansible-local-apply.service` on abykovserv.
