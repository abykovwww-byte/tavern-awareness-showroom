# Plan 028: последовательная поставка RP continuity revision 7

**Дата:** 2026-08-17

**Статус:** PR1 local candidate. Этот план задаёт порядок поставки, но не
активирует revision `7`, не мигрирует существующие партии и не подтверждает
deploy/live readiness. В PR1 принят только
[Decision 028](../decisions/028-rp-uncovered-tail-and-overflow.md).

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
отдельные решение, PR, apply и live-store proof. Последующий slice не входит в
delivery cycle, пока предыдущий не доказан на изолированной live-партии.

| Порядок | Срез | Граница результата |
|---:|---|---|
| 1 | DC1: полный uncovered tail и hard-overflow recovery | Нет coverage gap; невместимый required prompt не достигает narrator |
| 2 | Scene-scoped relationship pressure | Отсутствующий NPC не попадает в сцену только из-за relationship obligation |
| 3 | Prompt identities, de-duplication и authority hierarchy | Optional дубликаты имеют явные identities и порядок authority |
| 4 | Scene projection, continuity gate и atomic commit | Сцена валидируется и сохраняется вместе с ходом как одна транзакция |

PR1 реализует только первую строку. Названия следующих строк — roadmap, а не
принятые ADR или реализованные runtime-контракты.

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

После merge и apply live proof выполняется только на изолированной Merchant
checkpoint/autotest branch. Нужно сопоставить effective snapshot, eligible turn
IDs, recorded `prompt_json`, narrator attempt, state version и неизменные hashes
source party. Только этот store-to-prompt proof позволяет поднять Decision 028 до
`подключено`.

## Последующие gates

Каждый следующий slice получает собственный ADR и registry только перед своей
реализацией. Для каждого отдельно обязательны focused tests, полный CI, merge,
pull-based apply, container/HTTP verification и изолированный live-store proof.
Observed revision повышается с `6` до `7` отдельным rollout change только после
всех четырёх proofs.

## Сознательно отложено

- автоматическая миграция или ремонт существующих партий «Купец»/«Староста»;
- scene-state backfill и semantic contradiction judge;
- prompt block identity hierarchy и scene-scoped relationship filter;
- provider-specific tokenizer и semantic compression protected tail;
- event sourcing, новые таблицы или maintenance UI;
- заявления `наблюдается`/`держится` до live evidence и endurance run.

## Источники

- [Decision 028](../decisions/028-rp-uncovered-tail-and-overflow.md)
- [Decision 026](../decisions/026-rp-core-delivery.md)
- [Decision 024](../decisions/024-simplified-rp-core.md)
- [Decision 022](../decisions/022-readiness-and-observability-policy.md)
- [Decision 016](../decisions/016-rp-living-story-memory.md)
- [Decision 009](../decisions/009-long-context-memory-policy.md)
