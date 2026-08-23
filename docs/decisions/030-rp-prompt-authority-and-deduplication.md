# Decision 030: Authority, structural deduplication и диагностика RP prompt

**Дата:** 2026-08-18

## Status

**Decision status: Accepted.** Пользователь явно поручил третий delivery slice
[Plan 028](../plans/028-rp-continuity-project-design.md). Решение принимает
контракт document-first. Focused DC3 дал `15 passed`, combined revision-7 suite
— `104 passed`, полный Gateway — `445 passed`, а `scripts/ci.ps1` завершился
успешно. Основной source contract, branch-aware read-only diagnostics и narrow
excluded-turn/emitter-ID hardening merged и applied; deployed container suite
завершилась результатом `548 passed, 1 skipped`. Три isolated live proofs
подключили structural deduplication, whole-block hard-budget eviction и
cross-surface diagnostics parity.

**Delivery status:** все строки [`registry/030.yml`](registry/030.yml) имеют
уровень `подключено`. На момент DC3 live proof effective observed revision была
`6`; последующий activation merge применён и ordinary-party stamp proof
подтвердил effective observed revision `7`. Этот rollout не повышает DC3 выше
`подключено` и не является заявлением об исправленной semantic continuity.

## Context

DC1 защищает полный raw tail новее effective RP story-memory coverage, а DC2
ограничивает relationship pressure текущей derived scene relevance. При этом
одна и та же часть истории всё ещё может одновременно попасть в prompt через
`RP_STORY_MEMORY`, legacy `LONG_TERM_PARTY_MEMORY`, raw tail и archive retrieval.
Такие структурные дубликаты расходуют hard input budget и дают narrator несколько
конкурирующих представлений одной chronology.

Существующие preview, context diagnostics, recorded turn metadata и Turn Trace
также не имеют одного content-free описания фактически собранных блоков. Для
третьего slice нужен единый детерминированный контракт assembly, а не semantic
сравнение текстов и не новый источник authority.

## Decision

Решение применяется только к normal party-chat и admin-autotest narrator turns
с `scenario_type=rp` на effective revision `7`. Opening-scene path не входит в
DC3. Revisions `0..6`, `novel` и `training` сохраняют прежнее поведение.

### Authority hierarchy

Canonical state и абсолютные WorldPack rules сохраняют общий приоритет над
текстовыми слоями. Внутри потенциально перекрывающихся continuity-слоёв Gateway
использует следующий строгий порядок:

```text
AUTHORITATIVE_OUTCOME / current action
  > newest complete uncovered raw tail
  > effective RP_STORY_MEMORY
  > archive sources
```

`current action` остаётся последним сообщением narrator request. Uncovered tail
содержит все complete non-excluded пары новее effective story-memory coverage по
Decision 028. `RP_STORY_MEMORY` представляет покрытую историю, а episodic memory,
retrieved archive scenes и uncompacted archive fallback относятся к нижнему
archive tier. Более низкий tier не может вытеснить или переопределить более
высокий только из-за порядка сборки prompt. Приоритет current action означает
приоритет текущего намерения игрока, а не автоматическое превращение заявленного
действия или результата в canonical fact; adjudicated outcome и state сохраняют
свои существующие границы authority.

Narrator prompt revision `7` содержит ровно один mandatory system block с
prefix `PROMPT_AUTHORITY_HIERARCHY` и stable `block_id=prompt_authority`. Block
передаёт ту же hierarchy без state или memory content и не может быть удалён как
optional. Его присутствие даёт recorded provider prompt typed oracle порядка
authority, а не требует выводить этот порядок из prose других блоков.

```text
PROMPT_AUTHORITY_HIERARCHY
authoritative_outcome_current_action > uncovered_raw_tail > rp_story_memory > archive
The current action is intent, not an automatic fact.
```

Safety line не позволяет narrator трактовать действие игрока как уже
совершившийся canonical факт; machine `authority_order` при этом не меняется.

### Structural deduplication

Если в revision-7 assembly одновременно есть non-empty legacy
`long_term_memory` candidate и effective `RP_STORY_MEMORY`, legacy block не
добавляется в provider prompt. В canonical assembly diagnostic он фиксируется
как omitted с причиной
`structural_deduplication`. Это правило определяется наличием authority block, а
не сравнением фраз, embeddings или решением LLM.

Suppression не удаляет raw turns, memory chapters, story snapshots или archive
rows и не меняет их eligibility для других revisions/modes. Если effective
`RP_STORY_MEMORY` отсутствует, существующий fallback path
`long_term_memory` остаётся допустимым. Archive retrieval продолжает соблюдать
DC1 boundary `turn_id <= story-memory coverage` и не заменяет uncovered raw tail.

### Hard-budget eviction

После обычного deterministic selection и structural deduplication budget-driven
eviction optional block разрешён только при фактическом превышении hard provider
input token budget. Soft percentage/character target не удаляет selected
optional blocks на revision `7`.

При hard overflow optional blocks удаляются только целиком и в существующем
deterministic порядке Decision 028. Частичное обрезание текста блока не
допускается. Каждый удалённый block получает omission reason
`hard_input_budget`. Protected outcome/current action, uncovered raw tail и
остальные required blocks не скрываются этим механизмом; если required set не
помещается, действует bounded refresh и fail-before-provider контракт DC1.

Обычное отсутствие блока из-за пустого source или existing relevance rule не
является budget eviction. DC3 не меняет правила выбора lore, characters или
relationship scope.

### Canonical `prompt_assembly`

Gateway формирует один content-free объект `prompt_assembly` из той же
фактической сборки, которая отправляется narrator:

```text
schema_version = "rp-gateway.prompt-assembly.v1"
rp_contract_revision = 7
authority_order = ["authoritative_outcome_current_action",
                   "uncovered_raw_tail",
                   "rp_story_memory",
                   "archive"]
story_memory_covered_through_turn_id
included_block_ids
raw_tail_turn_ids
omitted_blocks[{block_id, reason}]
```

`schema_version`, `rp_contract_revision` и `authority_order` имеют ровно
указанные значения. `story_memory_covered_through_turn_id` фиксирует exact
integer coverage boundary или `0` при отсутствии effective snapshot;
`raw_tail_turn_ids` содержат ascending IDs новее этой границы;
`included_block_ids` идут в порядке фактического prompt. `omitted_blocks`
содержит stable identity и причину `structural_deduplication` или
`hard_input_budget`. Объект не содержит
prompt/response text, character names, state values, secrets или provider
payload.

Один и тот же объект под ключом `prompt_assembly` используется для normal
party-chat/admin-autotest turns в JSON/API ответе Prompt Inspector, context
diagnostics, `gateway_assembly` trace detail и `metadata_json` committed turn.
Для сохранённого хода значения в turn metadata, Prompt Inspector с
`source=last` и recorded context обязаны совпадать даже после
`excluded_from_memory=1`: memory eligibility не удаляет content-free audit
metadata. Current dry-run preview вычисляет ту же schema для собственной
текущей assembly; он не обязан быть byte-equal diagnostic предыдущего recorded
turn. Existing access control и sanitization сохраняются: diagnostic не
раскрывает hidden relationship content и не становится runtime authority.

`prompt_assembly` и committed `prompt_json` описывают initial full narrator
assembly и transport retries с теми же messages. Compact validation-repair
является отдельной provider attempt и не заменяет эти две recorded surfaces;
его exact input остаётся доступен отдельно в private admin Turn Trace. Поэтому
`prompt_assembly` нельзя трактовать как описание последней repair attempt.

Проекция возвращается только JSON-полями `preview.prompt_assembly` и
`context.prompt_assembly`. Отдельного renderer или branch selector для неё в
Light GUI, shared UI и Showcase в этом slice нет.

Новая таблица, колонка, provider request field или дополнительный provider call
не добавляются. Diagnostic хранится в существующих JSON metadata/trace payload;
сам narrator его не получает. Existing exact `prompt_json` и trace input остаются
источниками аудита фактического provider request.

### Branch-aware read-only diagnostics

Для штатной проверки isolated candidate branch Gateway принимает необязательный
query-параметр `branch_id` на двух существующих read-only endpoint:

```text
GET  /api/parties/{party_id}/context?branch_id={branch_id}
POST /api/parties/{party_id}/prompt/preview?branch_id={branch_id}
     {"content":"...", "source":"current|last"}
```

Тело preview не меняется. Без `branch_id` сохраняются прежние source-party path
и response shape. С параметром Gateway разрешает ветку только внутри той же
party и owner scope, выбирает её isolated state store, source-party runtime
settings и persisted branch revision, а в ответе возвращает тот же `branch_id`. Неизвестная, чужая
или относящаяся к другой партии ветка получает `404`; raw `state_campaign_id`
публичным входом не становится.

Оба endpoint остаются read-only: не создают provider call, turn, snapshot или
ветку и не меняют source/branch state. Реализация, четыре focused regression,
полный Gateway `449 passed` и repository CI merged в PR59 и applied. Narrow
excluded-turn/emitter-ID hardening также merged и applied. Isolated live-store
party `party_ad201794ce31` затем доказала cross-surface parity для исключённого
latest turn: metadata, `gateway_assembly` trace, Prompt Inspector `source=last`
и recorded context вернули один `prompt_assembly` с SHA-256
`ddd7998d28273a07fc33bf597a1e7fc8af66d906546b5f545edd3a647ec2a335`.

## Verification boundary

Focused offline regressions должны доказать:

- строгую hierarchy outcome/current action, uncovered raw tail, story memory и
  archive при перекрывающихся inputs;
- ровно один mandatory `PROMPT_AUTHORITY_HIERARCHY` block с
  `block_id=prompt_authority` и exact hierarchy;
- omission non-empty `long_term_memory` candidate с причиной
  `structural_deduplication` только при наличии effective `RP_STORY_MEMORY`;
- сохранение legacy fallback при отсутствии effective story snapshot;
- отсутствие optional eviction при одной лишь percentage/character цели;
- whole-block eviction и причину `hard_input_budget` только при реальном hard
  token overflow;
- parity одного content-free `prompt_assembly` между committed turn metadata,
  Prompt Inspector `source=last`, recorded context и `gateway_assembly` trace;
- сохранение этой parity после `excluded_from_memory=1` без возврата хода в
  story-memory/archive eligibility;
- соответствие каждого registered stable block ID output реального emitter, а
  не тестовой копии prefix table;
- ту же exact schema для current dry-run preview без требования byte equality с
  предыдущим recorded turn;
- отсутствие prompt text, response text, character names, state values и
  secrets в diagnostic;
- совместимость revisions `0..6` и non-RP modes без новой таблицы, provider
  field или provider call.

Эта offline boundary выполнена: focused DC3 regressions — `15 passed`, combined
revision-7 набор — `104 passed`, полный Gateway — `445 passed`; repository gate
`scripts/ci.ps1` также завершился успешно.

Isolated live canary `autotest_2eb4d5e1a53f` на revision-7 branch
`branch_ccf0d535a98c` подтвердил exact structural deduplication первой строки
registry. Primary provider attempt завершился `403`, последующий
`openrouter/auto` — `200`; оба получили exact same prompt. Validation repair и
Gateway safe-fallback text не использовались; второй provider attempt был
transport model fallback после `403`. Source party сохранила persisted revision `0`: canonical state
SHA `dc076bcc31535f4b38a5ffbc9a14373b136a15a520f86c59b372377cd1d01164`,
combined source projections SHA
`2e86389f74ff6f7c05490cc0f65bb1c18b224b3e533b12800d108fb01d6dfe73`, а
каждый individual table hash совпал с baseline. Это подключает первую строку и
не является semantic-output proof.

Отдельная revision-7 party `party_1bc1a1204dde` проверила фактический hard input
budget. Full assembly оценивалась в `15360` tokens при budget `15359`, required
set — в `12669`; единственным optional candidate был `relevant_characters`.
Gateway omitted ровно весь block с exact record
`{block_id: relevant_characters, reason: hard_input_budget}`, и его message
целиком отсутствовал в provider prompt. Metadata и trace совпали, один mock
narrator call завершил request и добавил ровно один turn/state version. External
provider calls были равны нулю, hashes существующих protected stores совпали с
baseline. Это подключает вторую строку без percentage-only или partial eviction.

Excluded latest fallback turn party `party_ad201794ce31` подключил третью строку:
metadata, `gateway_assembly` trace, Prompt Inspector `source=last` и recorded
context вернули byte-equivalent content-free projection с SHA-256
`ddd7998d28273a07fc33bf597a1e7fc8af66d906546b5f545edd3a647ec2a335`, хотя
turn имел `excluded_from_memory=1`. Проекция по-прежнему описывает initial full
narrator assembly; compact validation-repair остаётся отдельной attempt в
private admin Turn Trace и не переписывает recorded `prompt_assembly`.

Opening persistence остаётся границей Decision 031 и проверена его отдельным
live-store proof. Все deterministic canaries подтверждают только исполнение в
реальном deployed turn path: semantic continuity, `наблюдается` и `держится` не
доказаны. Во время этих canaries effective observed revision оставалась `6`;
отдельный source target `7` не меняет границу самого evidence.

## Consequences

- Один continuity fact не получает несколько конкурирующих structural owners.
- Optional budget reduction становится объяснимой и воспроизводимой.
- Preview, context и recorded execution можно сравнивать без копирования текста
  prompt в новый diagnostic.
- Observed revision `7` применяется только к новым ordinary parties и не
  мигрирует существующие партии.

## Non-goals

- semantic text-equivalence judge, embeddings или content hashing oracle;
- новая memory model, tokenizer dependency, table, column или provider field;
- отдельный `prompt_assembly` для compact validation-repair или замена initial
  committed `prompt_json` repair payload;
- renderer `prompt_assembly` или branch selector в Light GUI/shared UI/Showcase;
- изменение lore/relevant-character/relationship relevance rules;
- opening-scene assembly persistence/parity; этот gate принадлежал четвёртому
  slice и теперь закрыт Decision 031, но сам по себе не был основанием для
  activation observed revision `7`;
- `scene_state`, persisted presence или scene-state fast path;
- structured narrator response bundle, continuity validator, fallback policy или
  atomic scene-state/turn commit;
- автоматическая миграция existing parties.

## Related decisions

- [Decision 009](009-long-context-memory-policy.md)
- [Decision 016](016-rp-living-story-memory.md)
- [Decision 022](022-readiness-and-observability-policy.md)
- [Decision 024](024-simplified-rp-core.md)
- [Decision 026](026-rp-core-delivery.md)
- [Decision 028](028-rp-uncovered-tail-and-overflow.md)
- [Decision 029](029-scene-scoped-relationship-pressure.md)
