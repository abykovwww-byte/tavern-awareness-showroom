# Decision 031: Scene state, typed continuity gate и atomic RP commit

**Дата:** 2026-08-18

## Status

**Decision status: Accepted.** Пользователь явно поручил четвёртый delivery slice
[Plan 028](../plans/028-rp-continuity-project-design.md). Решение принимает
минимальный contract document-first; локальная source implementation теперь
следует этому принятому контракту.

**Delivery status:** `подключено` для всех строк
[`registry/031.yml`](registry/031.yml). Source implementation merged и applied;
deployed container suite завершилась результатом `548 passed, 1 skipped`, а
isolated live-store proofs закрыли normal/opening, hard no-commit, anchoring/drop
и pre-bundle fallback boundaries. На момент этих proofs effective observed
revision была `6`; последующий activation merge применён и ordinary-party stamp
proof подтвердил effective observed revision `7`. Ни deterministic canary, ни
activation stamp не являются заявлением об исправленной semantic continuity или
уровне `наблюдается`.

Для revision `8+` scene projection, private narrator bundle и continuity gate
не читаются и не пишутся; их заменяет дословная history-first boundary из
[Decision 032](032-rp-history-first-prompt-and-sectioned-memory.md). Весь путь
этого ADR остаётся compatibility-контрактом только для revision `7`.

## Context

DC1 сохраняет полный uncovered raw tail, DC2 ограничивает pre-scene relationship
pressure, а DC3 задаёт prompt authority и structural deduplication. Эти срезы не
создают durable exact projection текущей сцены и не связывают narrator response,
scene transition, state version и turn одной authoritative транзакцией.

Текстовый `OutputValidator` проверяет player agency и typed absolute WorldPack
rules, но не может детерминированно доказать location и присутствие NPC. Нужен
маленький private provider contract, проверяемый по известным IDs, предыдущей
сцене, текущему действию и literal evidence. Второй LLM judge, embeddings и
свободная semantic классификация для этого не требуются.

## Scope

Контракт применяется только при explicit `rp_contract_revision=7` к:

- normal party-chat и admin-autotest narrator turns;
- opening scene через `POST /api/parties/{party_id}/start`;
- revision-7 state/turn commit, transport fallback и explicit world-command
  boundary.

Revisions `0..6` и `training` сохраняют текущий путь. Historical Novel rows
теперь архивируются по Decision 036. Existing parties
не мигрируют автоматически. Decision 028–030 и их readiness evidence не
переписываются.

## Decision

### Canonical `scene_state`

Revision `7` хранит внутри canonical state version минимальную exact projection:

```text
schema_version = "rp-gateway.scene-state.v1"
location_id
present_character_ids[]
stable_affiliations{}
as_of_state_version
as_of_party_turn
stale
stale_reason
```

`location_id` обязан быть existing known location ID. `present_character_ids`
содержит sorted unique existing character IDs; player подразумевается
присутствующим в `location_id` и в список не дублируется. Non-stale committed
projection является primary exact scope для revision-7 relevant-character и
relationship rendering. Отсутствующий due event остаётся durable и active по
Decision 029.

`stable_affiliations` формируется только Gateway из WorldPack-authored finite
данных:

- canonical character loyalty и existing faction IDs;
- при необходимости общего narrative role — optional bounded
  WorldPack-owned `rp_contract.stable_affiliations` map с known character IDs и
  declared finite values.

Narrator не создаёт и не меняет эти значения. Свободные profession, biography,
goal, belief и emotion сюда не входят. Механическая relationship-role/weight
(`mentor`, `rival`, `debtor` и подобные) не переосмысляется как narrative
affiliation. Optional map не является обязательной миграцией manifest.

Чтобы finite authored fact не оставался prompt-only, Gateway применяет узкий
deterministic narration guard. Он сканирует normalized sentences только по
WorldPack-owned whole aliases known character и known loyalty/faction/optional
stable-role values. Явное affirmative сочетание character alias с recognized
чужим affiliation alias является hard repairable conflict. Совпадение с
authored value допустимо; negation, quote/question/correction без affirmative
assertion и неизвестная free prose остаются вне gate. Relationship mechanic
roles не входят в этот lexicon. Это конечная alias-проверка, а не semantic LLM
judging.

`stale=true` сохраняет последнюю надёжную projection и exact
`as_of_state_version`/`as_of_party_turn`, но запрещает выдавать её за текущую
exact presence. Следующий prompt показывает stale/as-of marker и использует
conservative DC2 derived scope до успешной re-anchor.

### Minimal private narrator bundle

Provider возвращает private object, а public response по-прежнему содержит
только narrator text:

```text
schema_version = "rp-gateway.rp-narrator-bundle.v1"
narrative_text
scene_claims {
  location_id
  present_character_ids[]
}
scene_delta[]
```

Top-level и nested schemas используют `extra=forbid`. `scene_claims` — один
полный typed snapshot, а не граф claims: location и characters обязаны быть
existing known IDs, characters — sorted unique, не более `64` IDs.
`scene_delta` содержит не более `16` operations и использует finite allowlist:

```text
{type: "move_player",      location_id, evidence}
{type: "character_arrive", character_id, location_id, evidence}
{type: "character_depart", character_id, location_id, evidence}
```

Для каждой операции разрешены только перечисленные поля. `location_id` и
`character_id` имеют length `1..128`, `evidence` — `1..512`; arbitrary nested
values и generic JSON Patch paths запрещены. После canonical
normalization Gateway отдельно ищет его как exact substring соответствующего
player action или `narrative_text`: missing/wrong-type/over-bound evidence — hard
schema error, а well-typed, но не найденный fragment делает только эту operation
unanchored. Конкретные небольшие numeric bounds фиксируются schema и тестами, а
не provider prompt prose.

Операции над player belief, emotion, intent, goal, agency или arbitrary state
field отсутствуют в allowlist и отклоняются. Stable affiliations в bundle не
передаются.

### Player destination без обязательного alias manifest

WorldPack может объявить bounded aliases как refinement, но такой map не является
обязательным gate и не нужен для миграции существующих packs.

Для `move_player` действуют два deterministic пути:

1. exact known location ID или unambiguous declared alias сужает допустимый
   target до этого location;
2. если current player action содержит explicit non-negated **first-person**
   movement и непустую named-destination phrase, narrator может выбрать любой
   existing known `location_id`; typed operation и literal evidence обязаны
   зафиксировать этот выбор.

Второй allowance существует только для `move_player`. Он не разрешает NPC
arrival/departure, `Outcome.target`, third-person movement, простое mention,
correction или negated movement. Так игрок может назвать естественную цель и
пойти туда, но narrator не получает право перемещать других персонажей или
выдумывать unknown location.

### Deterministic continuity gate

Gateway нормализует `scene_delta`, строит ожидаемый snapshot из предыдущего
reliable `scene_state` и применяет правила:

1. `Outcome.target` может влиять на retrieval/ranking, но сам по себе никогда не
   авторизует player/NPC location или presence transition.
2. `move_player` требует player subject implicitly, explicit non-negated
   first-person movement и допустимый destination path выше. Evidence из current
   action отдельно определяет anchor. Current action остаётся intent до
   accepted bundle.
3. `character_arrive` требует known ранее отсутствующего NPC и destination,
   равный заявленному current scene; whole character alias и arrival fragment в
   narrator text отдельно определяют anchor.
4. `character_depart` требует known ранее присутствующего NPC и один existing
   destination; whole character alias и departure fragment отдельно определяют
   anchor. Player first-person movement не считается NPC arrival/departure.
5. Любое отличие `scene_claims` от previous reliable projection обязано иметь
   соответствующую well-typed authorized `scene_delta` candidate. Полное
   отсутствие такого candidate — hard scene-claim mismatch. После применения
   anchored operations рассчитанный snapshot обязан совпасть с `scene_claims`;
   отличие только из-за authorized, но unanchored candidate обрабатывается как
   soft drop+stale ниже.
6. Opening baseline строится из seed player location, known same-location
   characters и finite authored affiliations. Opening не получает player
   movement allowance только из narrator prose.

Unknown ID, forbidden field/type, ambiguous alias, recognized conflicting finite
affiliation, unauthorized transition или scene-claim mismatch без authorized
delta candidate является hard violation. Такой transition не становится
допустимым из-за отсутствующей либо dropped operation.

### Anchoring, repair и durable drop

Operation anchored, если она authorized и её normalized effect соответствует
ровно одному отличию между previous projection и `scene_claims`, а normalized
evidence найден в требуемом input/output fragment.

Hard schema/unknown-ID/forbidden/unauthorized/finite-affiliation/scene-claim
violation получает ровно одну repair-попытку с concrete violations, затем весь
parser/gate запускается заново. Если hard violation остаётся, request завершается
без canonical scene/state/turn commit.

Well-typed и authorized operation с отсутствующим либо mismatched evidence-anchor
является **soft сразу**: она не вызывает repair или дополнительный provider call.
Gateway drops только эту operation, сохраняет narration/turn, применяет только
anchored operations и маркирует committed `scene_state` stale с as-of последней
надёжной projection. `scene_claims`, зависящий только от dropped operation, не
становится reliable snapshot.

Это различие не позволяет спрятать запрещённый transition удалением operation,
но не теряет допустимый narrator text из-за безопасно отброшенной state mutation.

Private `turns.metadata_json` durable сохраняет bounded adjudication:

```text
scene_claims
applied_scene_delta[{type, character_id?, location_id, evidence}]
dropped_scene_delta[{type, character_id?, location_id, evidence, reason}]
scene_state_before
scene_state_after
story_memory_canonical
```

Applied и dropped records содержат фактические normalized value и bounded
evidence, а не только hash/reason. Та же информация попадает в existing private
audit/trace boundary. Metadata не входит в public response и не становится
вторым authority. Dropped transition не продвигается в RP story memory или
subsequent prompt как canonical fact; stale/as-of marker сохраняется до re-anchor.

### Safe fallback boundary

Provider transport routing между разрешёнными models сохраняется. Gateway safe
fallback без bundle разрешён только когда transport attempts исчерпаны **до**
получения parseable private bundle. После получения invalid bundle действует
одна repair-попытка, затем hard failure; safe fallback не маскирует schema или
continuity violation.

Pre-bundle transport fallback атомарно сохраняется как explicit noncanonical
fallback turn:

- public response и private metadata содержат finite fallback reason,
  `story_memory_canonical=false`, `scene_state_stale=true` и exact as-of
  последней reliable state/turn;
- `scene_state` остаётся на последней reliable projection и помечается stale;
- Gateway-authored fallback narrator text не входит в raw/story-memory/chapter,
  archive/retrieval или relationship canon;
- player input и noncanonical fallback marker остаются явно покрыты: следующий
  prompt показывает unresolved input, stale/as-of boundary и не выдаёт fallback
  prose за событие мира.

Для opening без prior reliable turn as-of является explicit empty/seed boundary.
Fallback не создаёт canonical opening scene; idempotent retry не должен создать
две canonical openings.

### Atomic SQLite authority и `current.json`

Успешный normal/opening bundle выполняет одну authoritative SQLite transaction,
которая вместе сохраняет:

1. canonical state version с applied scene operations и `scene_state`;
2. turn с public `narrative_text`, exact provider/prompt data и private bundle
   adjudication metadata;
3. request completion и existing authoritative audit/event consumption,
   относящиеся к этому turn.

Fallback transaction вместе сохраняет noncanonical turn, stale/as-of scene
marker и request completion. До commit background jobs и relationship post-turn
advance не видят результат. Ошибка любой authoritative write откатывает весь
набор; partial scene/state/turn/request result запрещён.

SQLite `state_versions.state_json` остаётся authority. Party/branch
`current.json` записывается best-effort только **после** successful SQLite
commit. Ошибка temp/write/replace mirror не откатывает SQLite и не создаёт
вторую version; bounded audit фиксирует failure, а следующий read/startup
восстанавливает mirror из SQLite.

### Opening parity

Revision-7 opening использует ту же bundle schema, gate, one-repair limit,
fallback boundary и atomic transaction, что normal turn. Opening
`prompt_assembly` сохраняется в metadata/trace и доступен тем же recorded
diagnostics surfaces по Decision 030. Seed baseline не меняется только из-за
opening prose; failure до authoritative commit не создаёт partial opening.

### Explicit world-command boundary

GM world proposals, `/world apply`, rollback и generic patch endpoint не являются
narrator bundle и не могут напрямую писать `scene_state` paths.

Если applied world patch меняет player/character location, character
status/loyalty, locations, factions или optional stable-affiliation source, та же
SQLite transaction маркирует scene state `stale=true` с finite reason
`world_change` и prior reliable as-of. Non-scene patch marker не меняет.
Rollback восстанавливает historical scene state из target state version; legacy
target без projection получает stale bootstrap. Следующий accepted bundle
re-anchors projection.

## Verification boundary

Focused offline regressions обязаны доказать:

- exact minimal bundle shape, strict fields/types/count/length bounds, known IDs
  и rejection arbitrary nested values;
- hard rejection unknown location/character, forbidden player
  belief/emotion/intent/goal field и unauthorized transition;
- no transition по одному `Outcome.target`;
- non-negated first-person player movement отдельно от NPC arrival/departure,
  включая natural named destination → known ID и запрет этого allowance для NPC,
  negation, correction, mention и third-person text;
- normalized literal evidence и exact computed snapshot anchoring;
- authorized unanchored evidence немедленно даёт drop без repair/provider call,
  durable value/evidence+audit+stale; unauthorized hidden-by-drop отдельно даёт
  hard repair/no-commit;
- non-stale presence fast path, stale conservative DC2 fallback и finite
  WorldPack-authored affiliations без mechanic relationship-role promotion;
- recognized conflicting authored loyalty/faction/optional stable-role sentence
  даёт hard repair, а unknown free prose не включает semantic judge;
- safe fallback только при pre-bundle transport failure, committed
  `story_memory_canonical=false`, explicit player-input marker и отсутствие
  fallback prose в всех canonical memory/retrieval/relationship consumers;
- normal/opening parity, включая recorded `prompt_assembly`;
- world-command stale policy, rollback и legacy bootstrap;
- revisions `0..6` и `training` без изменений; archived Novel history остаётся вне RP runtime.

Failure-injection tests обязательны на boundaries initial parse/gate, repaired
parse/gate, state-version write, turn/private-metadata write, audit/event write,
request completion и transaction commit. До commit ожидается либо полный
authoritative result, либо отсутствие всех его частей. Отдельные tests доказывают
атомарный fallback commit и то, что post-commit `current.json` failure оставляет
ровно один SQLite state/turn с восстанавливаемым mirror. Idempotent retry не
создаёт вторую scene/turn version.

`Подключено` требует merged/applied implementation и isolated revision-7 proofs
для normal turn и opening. Recorded bundle, scene state, applied/dropped metadata,
next prompt и SQLite hashes должны согласоваться; source party остаётся pinned и
неизменной. Отдельный negative canary нужен для hard no-commit boundary, а
pre-bundle canary — для noncanonical fallback. Валидный provider response сам по
себе не доказывает semantic continuity, `наблюдается` или `держится`.

Deployed isolated live-store party `party_16f68f4f2ba3` выполнила accepted
opening и два normal turns: итоговые authoritative counts составили `3` turns,
`4` state versions и `3` completed requests, повтор opening с тем же idempotency
key не создал новых записей. Anchored movement дал ровно одну applied operation;
следующая authorized, но unanchored operation была dropped без repair или
второго provider call, durable сохранила value/evidence и audit, а scene стала
stale с marker в следующем prompt.

Negative party `party_48fd541fdb8d` получила одинаковый unauthorized-location
violation на initial и единственной repair attempt. Request завершился `502` и
status `failed`; counts остались `0` turns, `1` initial state version и `1`
request, то есть canonical commit отсутствовал. Pre-bundle timeout party
`party_ad201794ce31` atomically сохранила один fallback turn/state с
`excluded_from_memory=1`, `story_memory_canonical=false`, stale scene и audit.
Fallback prose отсутствовал в следующем prompt, player input и unresolved marker
присутствовали, relationship canon rows остались равны нулю.

Все canaries использовали deployed Gateway code и production SQLite только в
новых isolated revision-7 parties; external provider calls были равны нулю,
SQLite `quick_check` прошёл, а hashes всех существовавших партий и protected
stores совпали с baseline. Это уровень `подключено`, не `наблюдается` или
`держится`; во время этого evidence run effective observed revision оставалась
`6`, а последующий activation не меняет доказанную границу canary.

## Privacy and retention

Private bundle evidence — bounded excerpt уже сохраняемых player/narrator texts.
Оно хранится в existing `turns.metadata_json` столько же, сколько turn, входит в
Gateway backup, доступно только existing owner/admin private paths и не
экспортируется в dataset без обычного explicit review. Evidence не копируется в
public API, Prompt Inspector или новый diagnostic log. Новая table и отдельный
TTL не вводятся; existing secret-redaction и access control остаются
обязательными.

## Consequences

- Scene continuity получает typed exact projection, но только accepted
  transaction делает её reliable.
- Bundle остаётся маленьким: один snapshot и bounded typed delta без claim graph.
- Допустимая unanchored mutation сохраняет narration, но не меняет state
  незаметно: drop, audit и stale marker обязательны.
- Transport fallback остаётся видимым пользователю и durable как
  noncanonical turn, но его narrator prose не становится фактом истории.
- Observed revision `7` применяется только к новым ordinary parties и не
  мигрирует существующие parties.

## Non-goals

- semantic contradiction judge, embeddings, vector store или второй LLM call;
- free-text profession/role/affiliation extraction;
- mechanic relationship role как narrative stable affiliation;
- arbitrary narrator JSON Patch или player belief/emotion mutation;
- mandatory location-alias manifest migration;
- новая state/event-sourcing database, service, table или filesystem authority;
- UI для ручного редактирования `scene_state`;
- автоматический repair существующих партий «Купец»/«Староста»;
- изменение readiness Decision 028–030.

## Related decisions

- [Decision 016](016-rp-living-story-memory.md)
- [Decision 022](022-readiness-and-observability-policy.md)
- [Decision 024](024-simplified-rp-core.md)
- [Decision 026](026-rp-core-delivery.md)
- [Decision 027](027-turn-trace-workbench.md)
- [Decision 028](028-rp-uncovered-tail-and-overflow.md)
- [Decision 029](029-scene-scoped-relationship-pressure.md)
- [Decision 030](030-rp-prompt-authority-and-deduplication.md)
