# Decision 028: Полный uncovered tail и восстановление при переполнении RP prompt

**Дата:** 2026-08-17

## Status

**Decision status: Accepted.** Пользователь поручил последовательную реализацию
[Plan 028](../plans/028-rp-continuity-project-design.md); это решение принимает
только первый delivery slice.

**Delivery status:** `подключено` для всех строк
[`registry/028.yml`](registry/028.yml). Полный uncovered tail и revision stamp
подтверждены на deployed изолированной ветке, а paired live-store canary закрыл
fit-after-refresh и hard-overflow fail-before-narrator paths. На момент этого
evidence run effective observed revision была `6`. Последующий activation merge
`a4076b0938f2b152f77e675e8545156ce783a8f3` применён, а ordinary-party stamp
proof подтвердил effective observed revision `7` без миграции прежних parties.
Ни deterministic canary, ни activation stamp не доказывают уровень
`наблюдается`.

## Context

Effective RP story memory может отставать от committed turns. Если percentage
compaction удаляет часть более нового raw tail, narrator получает старый snapshot
и неполную текущую сцену. Сохранение одной последней пары в revision `6` не
гарантирует полный хвост после story-memory boundary.

Raw transcript остаётся durable source history. Мягкая 50% character-цель не
может создавать coverage gap; жёсткой границей остаётся provider input budget.

## Decision

Решение применяется только к `scenario_type=rp` с effective revision `7`.
Revisions `0..6`, `novel` и `training` не меняются.

### Полный uncovered tail

Gateway выбирает newest valid effective RP story-memory snapshot:

```text
coverage = effective_rp_story_memory.to_turn_id or 0
raw_tail = turns_for_memory(after_turn_id=coverage), ordered ascending
```

Каждая non-excluded пара `player_message`/`narrative_response` из `raw_tail`
копируется дословно и защищается целиком. Episodic chapters не сдвигают boundary;
archive retrieval выбирает только ходы `<= coverage`.

Непосредственно перед narrator call Gateway повторно читает effective snapshot.
При изменении, исчезновении или rollback snapshot весь prompt пересобирается из
authoritative stores и снова сверяется. После трёх нестабильных циклов запрос
fail-closed завершается до provider; prompt с coverage gap не отправляется.

### Hard-overflow recovery

При hard-token overflow optional blocks удаляются только целиком: retrieved
archive scenes, episodic memory, lore cards, затем non-mandatory relevant
characters. Percentage-only target не удаляет protected revision-7 history.

Если required set не помещается, Gateway выполняет один bounded synchronous
force-refresh через существующий
`RPStoryMemoryUpdater.catch_up(force=True, fail_open=False)`:

1. используется stack-managed service model и существующий batch budget;
2. после каждого условно committed snapshot coverage перечитывается, а prompt
   полностью пересобирается;
3. цикл останавливается при fit, пустом plan, ошибке, deadline или ceiling 64
   batches;
4. первый narrator call разрешён только после fit; refresh сам narrator не
   вызывает, а существующая validation/repair policy после ответа сохраняется;
5. иначе sanitized `PromptBudgetExceeded` возникает до narrator и до player
   turn/state/relationship mutation.

Успешно продвинутый story snapshot остаётся допустимым maintenance side effect
при конечном overflow. Player turn и canonical state не меняются.
Revision-7 pre-provider relationship rendering также не материализует отсутствующий
derived seed; штатный post-commit advance создаёт его только после успешного хода.

### Revision stamp

Поддерживаемый публичный диапазон — `0..7`. После pull-based apply 23 августа
2026 года effective observed revision равна `7`. Обычная новая RP-партия
сохраняет точное
`min(WorldPack declared revision, RP_CONTRACT_OBSERVED_REVISION)`, runtime читает
persisted party revision, existing parties не повышаются автоматически. Явная
checkpoint/autotest branch может запросить candidate revision в пределах
WorldPack declaration. Non-RP всегда использует `0`.

Live stamp proof создал через обычный Gateway API, не запуская opening/narrator,
`party_b286ed285388` («Староста», declared `7`) и `party_7928b20be697`
(declared `6`), а follow-up `party_517a98233313` проверил `novel`. API, SQLite и
runtime settings сохранили соответственно `7`, `6` и `0`; у всех трёх parties
отсутствуют turns, turn requests и service/provider calls.
Полные rows/revision maps прежних `63` parties и `18` branches, а также их
state-tree hashes совпали до/после; novel proof повторил equality для уже `65`
parties.

### Diagnostics

Prompt Inspector и context diagnostics сообщают effective/prompt coverage,
pending turns/tokens, configured threshold, hard-budget status, operator status
`normal|lagging|overflow` и последний bounded force-refresh result с batches и
coverage before/after. Inspector дополнительно перечисляет included raw IDs. При
hard overflow Inspector возвращает sanitized status с пустыми `messages/blocks`
без world/player prompt text; обычный preview сохраняет существующий интерфейс.
Диагностика read-only и не становится runtime authority.

## Verification boundary

Offline tests доказывают exact uncovered tail, forced refresh, bounded stop,
snapshot advance/rollback races, zero narrator call и отсутствие player mutation
при конечном overflow, sanitized diagnostics и stamp matrix
`6/7 -> 6`, `7/7 -> 7`, `7/6 -> 6`, non-RP -> `0`.

Tail/stamp proof требует deployed canary на изолированной Merchant
checkpoint/autotest branch. Recorded prompt должен содержать все и только полные
пары после нового coverage; source-party raw/state hashes обязаны совпасть.
Запрос обязан явно передать `rp_contract_revision: 7`, а созданная branch —
вернуть и сохранить revision `7`; наследование source revision `6` не является
evidence этого решения. Hard-overflow proof отдельно требует deployed
revision-7 turn path с force-refresh, точными narrator/turn/state boundaries и
неизменностью существующих партий.

Deployed canary `autotest_e3e62b5ea73d` на branch
`branch_e1664fcbbe07` выполнил эту positive проверку. Source party осталась на
revision `6`, explicit branch сохранила revision `7`; snapshot `70` покрывал
turn IDs `1435..1450`, а записанный provider prompt содержал ровно одну полную
verbatim-пару uncovered tail с turn ID `1451`, без covered-пар и с текущим
действием последним. Source raw/state hashes не изменились. Narrator был вызван
один раз, transport и validator завершились успешно, без fallback и repair.

Paired isolated live-store proof затем проверил обе force-refresh развилки на
deployed Gateway-коде. Party `party_39f2d3cd6307` продвинула coverage
`1634 -> 1636`, сократила pending turns `2 -> 0`, после пересборки выполнила один
mock narrator call и committed один turn. Party `party_4a07c4ad0613` продвинула
coverage `1638 -> 1640`, но после пересборки осталась в hard overflow
(`estimated_tokens=26917`, `budget_tokens=4000`): narrator не вызывался, request
получил status `failed`, новые turn/state version и relationship projections не
появились. Допустимым side effect остался только maintenance story-memory
snapshot. External provider calls были равны нулю, SQLite `quick_check` прошёл,
а hashes всех существовавших до canary партий и проекций совпали с baseline.

Merchant narration по-прежнему не подтверждает устойчивость ролей, а paired
proof использовал deterministic provider boundary. Поэтому все строки DC1 имеют
уровень только `подключено`: исправленная semantic continuity, `наблюдается`,
`держится` не заявляются. Последующий activation stamp отдельно подтвердил
ordinary availability revision `7`, но не расширил evidence механизма.

## Consequences

- 50% остаётся мягкой long-party целью.
- Редкий overflow может синхронно вызвать существующую service model.
- Невместимый required prompt приводит к явному отказу, а не тихой потере tail.
- Observed revision `7` применяется только к новым ordinary parties и не
  мигрирует существующие партии.

## Non-goals

- новый summarizer, service, job type, tokenizer или vector store;
- semantic compression protected tail;
- scene projection, prompt hierarchy или relationship redesign;
- изменение acceptance corpus;
- автоматическая миграция legacy party.

## Related decisions

- [Decision 009](009-long-context-memory-policy.md)
- [Decision 016](016-rp-living-story-memory.md)
- [Decision 022](022-readiness-and-observability-policy.md)
- [Decision 024](024-simplified-rp-core.md)
- [Decision 026](026-rp-core-delivery.md)
