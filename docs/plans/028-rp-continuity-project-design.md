# Plan 028: последовательная поставка RP continuity revision 7

**Дата:** 2026-08-17

**Статус:** PR1 и canary plumbing merged и применены. Positive Merchant canary
поднял требования полного uncovered tail и revision stamp до `подключено`, но
hard-overflow negative proof остаётся `каркас`, а semantic continuity не
подтверждена. Пользователь отдельно принял PR2-контракт
[Decision 029](../decisions/029-scene-scoped-relationship-pressure.md). PR2 merge
применён, а isolated canary поднял обе его registry-строки до `подключено`.
Третий document-first контракт принят в
[Decision 030](../decisions/030-rp-prompt-authority-and-deduplication.md). Его
offline gates выполнены, а applied isolated canary поднял его первую registry-row
до `подключено`. Hard-budget eviction и cross-surface diagnostics parity
остаются `каркас`. Optional branch-aware context/preview wiring merged в PR59 и
applied, но exact live parity ещё не доказана. Narrow excluded-turn/emitter-ID
hardening дал focused `17 passed`; merge, apply и live proof этого follow-up ещё
нет. Semantic continuity и уровень `наблюдается` не доказаны. Четвёртый document-first
контракт принят в
[Decision 031](../decisions/031-rp-scene-state-and-atomic-continuity.md); все его
registry-строки остаются `каркас`. Локальная source implementation, focused
tests, полный Gateway (`547 passed`) и `scripts/ci.ps1` выполнены; merge, apply и
live proof отсутствуют.
Этот план не активирует revision `7`, не мигрирует существующие партии и не
повышает observed revision выше `6`.

## Цель

Revision `7` должна устранить разрыв между effective RP story memory и более
новой сырой историей, а затем последовательно закрыть остальные причины потери
continuity. Исходный сбой «Купца» показал, что transport success и успешный
`OutputValidator` сами по себе не доказывают сохранение ролей, места и текущей
сцены.

Проверяемая цепочка остаётся такой:

```text
player input
  -> effective story-memory coverage
  -> полный uncovered raw tail
  -> recorded provider prompt
  -> validated response
  -> committed turn
  -> следующий prompt
```

## Порядок поставки

Проект разделён на четыре самостоятельных delivery slice. Каждый получает
отдельные решение, PR, apply и live-store proof. Незакрытый gate предыдущего
slice не считается закрытым из-за начала следующего и продолжает блокировать
observed rollout. Пользователь явно открыл PR2 при оставшемся DC1 hard-overflow
negative gate; это не меняет readiness DC1.

| Порядок | Срез | Граница результата |
|---:|---|---|
| 1 | DC1: полный uncovered tail и hard-overflow recovery | Нет coverage gap; невместимый required prompt не достигает narrator |
| 2 | [Derived pre-scene relationship scope](../decisions/029-scene-scoped-relationship-pressure.md) | Отсутствующий NPC не попадает в сцену только из-за relationship obligation |
| 3 | [Prompt authority, structural deduplication и diagnostics](../decisions/030-rp-prompt-authority-and-deduplication.md) | Один authoritative representation на continuity tier; assembly объяснима без prompt content |
| 4 | [Scene projection, continuity gate и atomic commit](../decisions/031-rp-scene-state-and-atomic-continuity.md) | Сцена валидируется и сохраняется вместе с ходом как одна транзакция |

PR1 поставил первую строку, а deployed PR2 подключил вторую по Decision 029.
Третья строка принята document-first по Decision 030 и подключена только в части
prompt hierarchy/structural deduplication; её hard-budget и diagnostics-parity
gates остаются `каркас`. Четвёртая строка принята document-first по Decision 031,
её local source/offline gates выполнены, но все registry-строки остаются
`каркас`; runtime delivery и live proof отсутствуют. Ни один новый slice не
меняет readiness предыдущих.

## Общие инварианты

- Gateway остаётся authority для revision, state, memory coverage, prompt и
  commit.
- Candidate `7` действует только для `scenario_type=rp`; revisions `0..6`,
  `novel` и `training` сохраняют прежнее поведение.
- Observed revision остаётся `6`, пока все срезы не получили отдельные live
  proofs и rollout-решение.
- Existing party сохраняет persisted revision; auto-migration отсутствует.
- Raw turns не удаляются и не переписываются ради prompt budget.
- Новые сервисы, telemetry, vector store, tokenizer dependency и второй LLM
  judge не добавляются.
- Acceptance corpus остаётся независимым read-only oracle и не меняется вместе
  с механизмом.

## PR1 / DC1

### Revision stamp

Код принимает candidate revision `0..7`, но обычная новая RP-партия получает:

```text
min(WorldPack declared revision, RP_CONTRACT_OBSERVED_REVISION)
```

Это значение сохраняется в party row и затем читается runtime-настройками именно
из партии. WorldPack может объявить candidate `7`, пока inventory продолжает
явно задавать observed `6`. Только явно созданная checkpoint/autotest branch
может запросить candidate revision выше source party. Non-RP получает `0`.

### Coverage и raw tail

Для revision `7`:

```text
coverage = effective_rp_story_memory.to_turn_id or 0
raw_tail = turns_for_memory(after_turn_id=coverage), ordered ascending
```

В prompt дословно входят все non-excluded пары из `raw_tail`. Episodic memory
coverage не сдвигает эту границу, а retrieval ограничивается ходами
`<= coverage`. Перед narrator call Gateway повторно читает effective snapshot и
при любом изменении snapshot ID/coverage полностью пересобирает request и снова
сверяет snapshot. Три последовательных нестабильных цикла завершаются fail-closed
до provider.

Мягкая character-цель revision `6` не имеет права удалять uncovered pair в
revision `7`. Реальный provider input budget остаётся жёсткой границей.

### Hard overflow

При превышении hard budget Gateway сначала целиком удаляет optional blocks в
порядке: retrieved archive scenes, episodic memory, lore cards, non-mandatory
relevant-character detail. Protected raw tail, system/world rules, story-memory
coverage, authoritative state/outcome и current action не режутся.

Если protected set всё ещё не помещается, Gateway запускает один bounded
synchronous `RPStoryMemoryUpdater.catch_up(force=True, fail_open=False)` через
существующую stack-managed service model. После каждого committed snapshot prompt
полностью пересобирается. Первый narrator call разрешён только после успешной
пересборки; сам refresh narrator не вызывает, а существующая validation/repair
policy после ответа не меняется. Иначе sanitized `PromptBudgetExceeded`
возникает до narrator и до мутации player turn/state/relationship projections.

Story-memory snapshot, успешно созданный maintenance-путём, может сохраниться
даже при конечном overflow; player turn и canonical state при отказе не меняются.
Пред-provider relationship rendering для revision `7` не создаёт отсутствующий
derived seed; он материализуется штатным post-commit advance только после
успешного хода.

### Диагностика

Prompt Inspector и context diagnostics показывают effective/prompt coverage;
Inspector дополнительно показывает полный список included raw turn IDs. Оба
пути сообщают:

- pending turns/tokens и configured pending threshold;
- `normal` / `lagging` / `overflow` operator status;
- hard input budget и sanitized overflow result;
- force-refresh attempt, request ID, batches, terminal result и coverage
  before/after.

При hard overflow ответ Inspector дополнительно sanitized: `messages/blocks`
пусты, world/player prompt text не возвращается. Обычный preview сохраняет
существующий интерфейс. Диагностика остаётся read-only и не становится runtime
trigger или authority.

## Gates PR1

Offline regressions обязаны доказать:

- exact tail после effective story coverage без percentage trimming;
- forced refresh ниже cadence и bounded multi-batch stop-when-fit;
- повторную сборку при advance/rollback snapshot;
- zero narrator calls и отсутствие player mutation при конечном overflow;
- sanitized preview/context без world/player prompt text;
- stamp matrix `6/7 -> 6`, `7/7 -> 7`, `7/6 -> 6`, non-RP -> `0`.

Deployed canary `autotest_e3e62b5ea73d` на branch
`branch_e1664fcbbe07` явно запросил и сохранил revision `7`, пока source party
осталась на revision `6`. Effective snapshot `70` покрывал turn IDs
`1435..1450`; eligible tail `[1451]` вошёл в recorded `prompt_json` ровно одной
полной verbatim-парой, covered-пары не просочились, текущее действие осталось
последним. Source raw/state hashes не изменились. Narrator выполнил один вызов с
успешными transport/validator status, без fallback и repair.

Этот result поднимает tail и revision-stamp requirements до `подключено`, но не
весь registry. Canary fit не вошёл в hard-overflow, поэтому live-доказательство
zero narrator calls и отсутствия turn/state/relationship mutation при конечном
overflow остаётся `каркас`. Narrative сместил действие в другую локацию и не
подтвердил устойчивость ролей: это не отменяет точный prompt-presence proof, но
не доказывает исправленную continuity и не позволяет заявлять `наблюдается`.

## PR2 / DC2

Decision 029 ограничивает relationship pressure производным pre-scene набором.
На candidate revision `7` персонаж может войти в него только по совпадению с
`player.location`, explicit whole-alias в текущем действии или `Outcome.target`.
Уже eligible-кандидат получает score `100` за action-or-target, `30` за location
и дополнительно `20` за membership в structured `active_threads`. Active thread
сам по себе не расширяет набор. Кандидаты сортируются по убыванию score и stable
ID и ограничиваются существующим top-6.

Причина или due event сами по себе не являются сигналом присутствия. Absent due
`favour` скрывается из prompt, но остаётся durable и `active`; omission не
закрывает и не истекает событие. Guidance возвращается только после нового
derived relevance, а delivery по-прежнему требует matching committed evidence.
Новая state-проекция сцены, таблица или LLM-классификатор не добавляются.

### Gates PR2

Offline regressions должны доказать каждый из трёх eligibility-сигналов,
исключение active-thread-only/relationship-only NPC, rank enrichment без
расширения набора, deterministic top-6, durable omission и совместимость
revisions `0..6`/non-RP. `Подключено` требует deployed isolated branch с
отдельной absent-NPC fixture: другая location, без current-action alias и
`Outcome.target`, даже если NPC остаётся в active thread. Поскольку checkpoint
fork не переносит derived relationship rows, proof turn выполняется после
warm-up либо явно задокументированного bootstrap. Recorded provider prompt должен
одновременно содержать relevant pressure и скрывать absent ordinary/due
pressure, а authoritative relationship rows — подтверждать, что omitted due
`favour` осталось active. Обычный source state «Купца»/«Старосты», где почти все
modeled NPC перечислены в `active_threads`, сам по себе этого не доказывает.

Deployed merge `fb13eecd56351d885e3309f6464a7d3a2e2b04e3` прошёл apply
2026-08-18 11:36:50 MSK и container gate `429 passed, 1 skipped`.
`autotest_53d37c3afef0` создал revision-7 branch `branch_9b616d225e4e` от
revision-6 source fixture и завершил `2/2` ходов без fallback или terminal error.
Warm-up с одним validation repair только материализовал `favour` event Бажены
`29`; proof turn выполнил один narrator call без repair.

На proof turn игрок и Милена были в `red-clay-ravine`, а Бажена оставалась в
`olshanitsa-village` и active thread без action alias или `Outcome.target`.
Recorded prompt содержал один relationship system-block с Миленой; Бажена,
Радогост, due header и private numeric/JSON fields отсутствовали. После omission
event `29` оставался active и unresolved при
`due_turn=10 <= party_turn=13`; source state и six-table structural hash не
изменились. Outputs не назвали отсутствующих NPC, но это узкое наблюдение не
доказывает полную semantic continuity или `наблюдается`.

## PR3 / DC3

Decision 030 задаёт для normal party-chat и admin-autotest narrator turns
deterministic authority hierarchy потенциально перекрывающихся
continuity-слоёв revision `7`:

```text
AUTHORITATIVE_OUTCOME / current action
  > newest complete uncovered raw tail
  > effective RP_STORY_MEMORY
  > archive sources
```

Narrator получает mandatory system block `PROMPT_AUTHORITY_HIERARCHY` со stable
`block_id=prompt_authority`, той же hierarchy и safety line
`The current action is intent, not an automatic fact.` Machine
`authority_order` от этого не меняется. Когда одновременно существуют non-empty
legacy `long_term_memory` candidate и effective `RP_STORY_MEMORY`, legacy block
подавляется структурно с omission reason
`structural_deduplication`. Сравнение текста, embeddings и отдельный LLM judge
не используются; suppression не удаляет raw turns, chapters, snapshots или
archive rows.

После обычного relevance selection optional blocks могут быть удалены ради
budget только целиком и только при фактическом hard provider token overflow.
Soft percentage/character target этого не делает. Каждый budget-omitted block
получает reason `hard_input_budget`; required-set overflow по-прежнему проходит
bounded refresh/fail-before-provider DC1.

Одна content-free projection `prompt_assembly` фиксирует exact constants
`schema_version=rp-gateway.prompt-assembly.v1`, `rp_contract_revision=7` и
`authority_order=[authoritative_outcome_current_action, uncovered_raw_tail,
rp_story_memory, archive]`, а также
`story_memory_covered_through_turn_id`, ordered included block IDs, uncovered raw
turn IDs и omitted block identities/reasons. Для recorded turn объект должен
совпадать в `metadata_json`, `gateway_assembly` trace, Prompt Inspector
`source=last` и recorded context. Current dry-run использует ту же schema для
собственной assembly, но не обязан быть byte-equal предыдущему recorded turn.
Diagnostic не содержит prompt/response text, names, state values или secrets и
не отправляется provider. Новая таблица, колонка, provider field или
дополнительный call не добавляются.

Recorded projection и `prompt_json` описывают initial full narrator assembly и
transport retries с теми же messages. Compact validation-repair не заменяет эти
surfaces и виден отдельно только в private admin Turn Trace. Exclusion хода из
narrative memory не скрывает content-free projection из JSON/API diagnostics.
Light GUI/shared UI/Showcase отдельный renderer или branch selector для неё не
получают.

Для штатного isolated-branch proof follow-up добавляет optional query
`branch_id` к read-only `GET /api/parties/{party_id}/context` и
`POST /api/parties/{party_id}/prompt/preview`. Без параметра public contract и
response source party не меняются; с параметром Gateway использует isolated
branch store, source-party runtime settings и persisted branch revision и
возвращает `branch_id`.
Raw `state_campaign_id` наружу не передаётся, provider/state/turn mutation не
возникает. Wiring, четыре focused test, полный Gateway `449 passed` и repository
CI merged в PR59 и applied, но без exact isolated-branch live parity
diagnostics-row остаётся `каркас`.

### Gates PR3

Offline regressions обязаны доказать mandatory authority block и exact order,
structural suppression только при двух присутствующих memory candidates, legacy
fallback без effective snapshot, отсутствие percentage-only eviction,
whole-block hard-token eviction и recorded parity content-free
`prompt_assembly`. Current dry-run обязан возвращать ту же schema для своей
assembly. Excluded latest turn обязан сохранять projection во всех recorded
diagnostic surfaces, не возвращаясь в narrative memory; outputs каждого
registered prompt-block emitter обязаны классифицироваться в свой stable ID.
Legacy revisions и non-RP modes не меняются.

Offline gates выполнены: focused DC3 — `15 passed`, combined revision-7 —
`104 passed`, полный Gateway — `445 passed`, `scripts/ci.ps1` — success. Applied
canary `autotest_2eb4d5e1a53f` на revision-7 branch
`branch_ccf0d535a98c` подтвердил exact structural deduplication. Primary attempt
получил `403`, последующий transport model-fallback `openrouter/auto` — `200`;
оба получили exact same prompt. Validation repair и Gateway safe-fallback text
отсутствовали. Source сохранил revision `0`, canonical
state SHA `dc076bcc31535f4b38a5ffbc9a14373b136a15a520f86c59b372377cd1d01164` и
combined source projections SHA
`2e86389f74ff6f7c05490cc0f65bb1c18b224b3e533b12800d108fb01d6dfe73`; все
individual table hashes совпали с baseline. Поэтому hierarchy/deduplication row
имеет уровень `подключено`.

Canary не входил в actual hard provider token overflow, поэтому hard-budget row
остаётся `каркас`. Branch diagnostics wiring merged и applied, но точное
равенство recorded metadata, trace, Prompt Inspector `source=last` и context на
isolated branch не проверено; diagnostics row также остаётся `каркас`. Валидный narration
сам по себе не доказывает semantic continuity или `наблюдается`.
Opening-scene persistence/parity не входит в DC3 и остаётся pending gate
четвёртого opening/atomic-commit slice; observed revision `7` до этого gate не
активируется и сейчас остаётся `6`.

## PR4 / DC4

Decision 031 задаёт для normal и opening revision-7 turns минимальный private
narrator bundle:

```text
schema_version + narrative_text
scene_claims { location_id, present_character_ids[] }
scene_delta[]
```

Gateway проверяет known IDs, literal bounded evidence, previous scene и current
player action. `scene_delta` разрешает только typed movement/presence changes.
Явное non-negated first-person движение в named destination позволяет narrator
выбрать existing known location ID для player; такой allowance не применяется к
NPC, `Outcome.target`, third-person mention, correction или negation.

Accepted anchored delta формирует canonical `scene_state`. Allowed operation с
unmatched evidence сразу, без repair/provider call, durable dropped с actual
value/evidence, а scene projection получает stale/as-of marker. Repair остаётся
только для hard schema/unknown/forbidden/unauthorized/scene-claim violations.
Unauthorized transition после repair завершается без canonical commit, даже если
operation отсутствует или dropped.

Pre-bundle transport exhaustion может записать atomic noncanonical fallback
turn. Его metadata содержит `story_memory_canonical=false`; fallback narrator
prose исключён из raw/story-memory/chapter/retrieval/relationship canon, а player
input и unresolved stale/as-of marker явно входят в следующий prompt. После
получения invalid bundle допустима одна repair-попытка, но не safe fallback.

Normal и opening accepted turns сохраняют state version, scene state,
turn/private adjudication metadata и request completion одной SQLite transaction.
`current.json` остаётся best-effort mirror после commit. Explicit scene-affecting
world commands не пишут `scene_state` напрямую, а atomically помечают projection
stale; rollback восстанавливает historical projection либо stale bootstrap.

Stable affiliations ограничены authored canonical loyalty/faction и optional
finite WorldPack-owned map. Узкий sentence guard ловит только explicit конфликт
known character+affiliation aliases и отправляет его в hard repair; неизвестная
free prose остаётся вне semantic judge, а mechanic relationship roles не
становятся narrative affiliation.

### Gates PR4

Локальная source implementation и обязательные offline gates выполнены, но все
четыре строки registry 031 остаются `каркас`. Реализованы
strict-schema/authorization/anchoring regressions, раздельные tests
для immediate no-repair authorized-unanchored drop, unauthorized hidden-by-drop
failure и finite affiliation conflict, opening
parity, noncanonical fallback exclusion, world-command stale policy и полный
failure-injection набор на каждой atomic write boundary. Отдельно проверяется,
что post-commit failure `current.json` не откатывает SQLite.

Полный local CI выполнен. Далее нужны merge, pull-based apply и isolated
revision-7 live proofs
normal/opening, hard no-commit и pre-bundle fallback. Валидный provider response
или green CI не поднимает readiness и не доказывает semantic continuity.

## Последующие gates

Для незавершённых gates каждого slice отдельно обязательны focused tests, полный
CI, merge, pull-based apply, container/HTTP verification и изолированный
live-store proof. Observed revision повышается с `6` до `7` отдельным rollout
change только после всех четырёх proofs и explicit activation decision.

## Сознательно отложено

- автоматическая миграция или ремонт существующих партий «Купец»/«Староста»;
- автоматический scene-state backfill и semantic contradiction judge;
- provider-specific tokenizer и semantic compression protected tail;
- event sourcing, новые таблицы или maintenance UI;
- заявления `наблюдается`/`держится` до later-party causal evidence и endurance
  run.

## Источники

- [Decision 028](../decisions/028-rp-uncovered-tail-and-overflow.md)
- [Decision 029](../decisions/029-scene-scoped-relationship-pressure.md)
- [Decision 030](../decisions/030-rp-prompt-authority-and-deduplication.md)
- [Decision 031](../decisions/031-rp-scene-state-and-atomic-continuity.md)
- [Decision 026](../decisions/026-rp-core-delivery.md)
- [Decision 024](../decisions/024-simplified-rp-core.md)
- [Decision 022](../decisions/022-readiness-and-observability-policy.md)
- [Decision 016](../decisions/016-rp-living-story-memory.md)
- [Decision 009](../decisions/009-long-context-memory-policy.md)
