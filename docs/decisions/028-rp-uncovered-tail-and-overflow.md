# Decision 028: Полный uncovered tail и восстановление при переполнении RP prompt

**Дата:** 2026-08-17

## Status

**Decision status: Accepted.** Пользователь поручил последовательную реализацию
[Plan 028](../plans/028-rp-continuity-project-design.md); это решение принимает
только первый delivery slice.

**Delivery status:** `каркас`. Контракт зарегистрирован в
[`registry/028.yml`](registry/028.yml), source и focused offline regressions
относятся только к candidate revision `7`. Merge, apply, provider-canary и
live-store proof ещё не подтверждены. Observed revision остаётся `6`.

## Context

Effective RP story memory может отставать от committed turns. Если percentage
compaction удаляет часть более нового raw tail, narrator получает старый snapshot
и неполную текущую сцену. Сохранение одной последней пары в revision `6` не
гарантирует полный хвост после story-memory boundary.

Raw transcript остаётся durable source history. Мягкая 50% character-цель не
может создавать coverage gap; жёсткой границей остаётся provider input budget.

## Decision

Решение применяется только к `scenario_type=rp` candidate revision `7`.
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

Поддерживаемый публичный диапазон становится `0..7`, но observed revision
остаётся `6`. Обычная новая RP-партия сохраняет точное
`min(WorldPack declared revision, RP_CONTRACT_OBSERVED_REVISION)`, runtime читает
persisted party revision, existing parties не повышаются автоматически. Явная
checkpoint/autotest branch может запросить candidate revision в пределах
WorldPack declaration. Non-RP всегда использует `0`.

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

`Подключено` возможно только после deployed canary на изолированной Merchant
checkpoint/autotest branch. Recorded prompt должен содержать все и только полные
пары после нового coverage; source-party raw/state hashes обязаны совпасть.

## Consequences

- 50% остаётся мягкой long-party целью.
- Редкий overflow может синхронно вызвать существующую service model.
- Невместимый required prompt приводит к явному отказу, а не тихой потере tail.
- Candidate support не поднимает observed revision и не мигрирует партии.

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
