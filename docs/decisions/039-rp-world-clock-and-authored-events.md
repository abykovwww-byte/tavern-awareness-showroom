# Decision 039: world clock and authored global events

**Дата:** 2026-08-25

## Status

**Decision status: Accepted.** RP revision `10` добавляет необязательные
WorldPack-часы. Модель оценивает только прошедшее в записанном ходе время, а
Gateway детерминированно применяет заранее написанные события и их последствия.

**Delivery status:** `каркас` для строк
[`registry/039.yml`](registry/039.yml). Revision 10 уже применён и первый
60-turn production endurance подтвердил ordinary clock path. Новая проекция
часов в opening требует повторной post-apply проверки; existing parties
автоматически не мигрируют.

## Context

Исторические события нужны narrator, но свободный LLM-планировщик снова сделал
бы модель источником канона и добавил бы в каждый prompt растущий JSON мира.
Одновременно одной реальной даты недостаточно: игрок может отменить поход,
закрыть долг или изменить иной authored deadline до того, как он наступит.

Поэтому часы отделяют два полномочия: bounded local model оценивает только
`elapsed`, а автор WorldPack заранее задаёт условия, отмену и два допустимых
типа последствий.

## Decision

### Revision and WorldPack boundary

- Path включён только для `scenario_type=rp`, `rp_contract_revision >= 10` и
  WorldPack с declared `files.world_clock`.
- Файл использует закрытую схему `rp-gateway.world-clock.v1`: начальная дата,
  ISO-8601 `max_step`, typed markers и authored events.
- Условие события — ровно `date_gte`, `after_event` или `after_confirmed`.
- У каждого события непустой `superseded_by`: игрок может отменить или заменить
  его подтверждённым authored marker до срабатывания.
- Marker создаётся только typed `state_equals` predicate по разрешённому пути
  canonical state либо отдельным owner-scoped explicit confirmation. Свободный
  текст модели не подтверждает marker.
- Последствия v1 ограничены durable `world_fact` и включением/выключением
  существующей authored Lore Card по stable key. Перемещение персонажа,
  произвольный state patch и новые типы действий отклоняются при загрузке.

### Canonical state and atomicity

`state.world_clock` хранит UTC date, max step, последний обработанный party turn,
confirmed markers, retained fired IDs/statuses, durable world facts, pending
announcements и последний `elapsed/reason`.

В одной SQLite-транзакции clock update:

1. ограничивает elapsed authored `max_step`;
2. проверяет typed markers и события до устойчивой точки, включая
   `after_event` chains;
3. фиксирует fired/superseded status и durable facts;
4. меняет только referenced authored Lore Cards;
5. создаёт новую state version и audit row.

`meta.turn` и сцена при этом не сдвигаются. Одна пара `(campaign_id,
party_turn)` применяется не более одного раза. Fired IDs не удаляются.

Часы не являются presence registry. Если authored событие отправило NPC в
поход, durable fact сообщает narrator, что NPC физически отсутствует, но NPC
Lore Card по-прежнему может подняться по имени.

### Post-commit service job

После успешного normal narrative commit Gateway в той же transaction создаёт
один `world_clock` job для фактического `party_turn`. Opening scene,
noncanonical safe fallback, `world_command`, `gm_correction` и будущие non-game
kinds job не создают. Если legacy queue уже содержит job для excluded
noncanonical turn, worker завершает его без model call и без clock tick.

Jobs применяются строго по `party_turn`. Следующий игровой запрос никогда не
ждёт часы: если main turn уже владеет состоянием, clock job откладывается без
расхода попытки. Поэтому событие обычно попадёт в prompt хода `N+1`, но при
занятом Gateway может появиться позже.

Service request получает только player+narrator text последнего записанного
хода. Полный serialized prompt не длиннее 4 000 символов, output — не более 50
tokens, strict JSON содержит только `{"elapsed":"PT2H"}`. Route всегда exact
local Gemma и не наследует global service selector, `LLM_PROVIDER`, party
narrator/BYOK, fallback или NVIDIA.

После исчерпания retries Gateway идемпотентно применяет
`elapsed=PT0S, reason=service_unavailable`. Пропущенное время потом не
догоняется; gameplay turn остаётся committed, а ошибки видны в
`service_call_log` с `provider=local`.

### Narrator and player projection

Перед narrator Gateway строит один `СОБЫТИЯ МИРА` block не длиннее 800
символов. Он расположен после relationship pressure и до author note/current
action, содержит новые ещё не объявленные события, durable facts когда они
помещаются и ближайший authored horizon.

Opening scene получает ту же проекцию до первого narrator call, но не создаёт
elapsed job: до первого действия игрока нечего оценивать. Его успешный atomic
commit сохраняет `metadata_json.world_clock_events` и снимает фактически
показанные pending IDs так же, как ordinary turn; repair opening получает тот же
block.

Pending event считается объявленным только внутри успешного turn commit,
который реально получил его в prompt. Поэтому provider/validation failure не
теряет событие. Неканоничный safe fallback не снимает pending ID и не записывает
`metadata_json.world_clock_events`, поэтому UI не прикрепляет событие к ответу,
который его не отыграл. Он также не создаёт elapsed job и потому не сдвигает
игровую дату. Успешный turn хранит ту же безопасную проекцию; Light GUI
показывает под соответствующим narrator response «В мире произошло» и
«Ближайший горизонт».

### First authored canary

`merchant-sviatoslav` является единственным authored-clock canary среди
revision-10 WorldPacks. Он задаёт не менее четырёх событий, включая выступление
Вятичского похода. Для
Велимира consequence фиксирует отсутствие из Подола durable world fact, а не
новую модель присутствия. Каждое событие имеет отдельный cancellation marker.

## Consequences

- Narrator получает даты и последствия как короткий событийный сигнал, а не
  растущий сериализованный мир.
- LLM не создаёт события и не изменяет канон; его единственный вклад — bounded
  оценка elapsed.
- WorldPack author обязан заранее описать отмену и конечные последствия, поэтому
  необратимый календарный сценарий не проходит validation.
- Async failure не тормозит игру и не создаёт поздний скачок времени.

## Non-goals

- свободное LLM-планирование событий, семантическое распознавание отмены или
  произвольные state patches;
- presence registry, маршруты NPC, simulation tick или catch-up elapsed;
- новые consequence types, новый provider, новая dependency или retention
  policy;
- автоматическая миграция existing parties/WorldPacks;
- автоматическая миграция existing parties, production DB rewrite, live local
  outage и отдельный semantic oracle времени.

## Verification gates

Локальные gates проверяют закрытую схему, max-step cap, chain execution,
supersession, atomic state/fact/Lore Card mutation, per-turn idempotency и job
order; bounded strict local request, пять ошибок и terminal `PT0S` без NVIDIA;
prompt order/800-char bound, commit-only announcement, history metadata и Light
GUI labels; а также четыре Merchant events с Вятичским походом.

Следующая post-apply партия должна повторно доказать цепочку `opening date ->
turn N commit -> local elapsed -> atomic authored event -> first available
narrator prompt -> one GUI announcement`, включая cancellation и forced local
outage. До этого все строки registry остаются на уровне `каркас`.

## Related decisions

- [Decision 020](020-rp-relationship-pressure-layer.md)
- [Decision 032](032-rp-history-first-prompt-and-sectioned-memory.md)
- [Decision 037](037-rp-authored-lore-cards-and-confirmed-drafts.md)
- [Decision 038](038-rp-gm-corrections-and-player-overlay.md)
