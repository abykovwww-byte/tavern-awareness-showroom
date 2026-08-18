# Decision 029: Производный pre-scene scope для relationship pressure

**Дата:** 2026-08-18

## Status

**Decision status: Accepted.** Пользователь явно поручил второй delivery slice
[Plan 028](../plans/028-rp-continuity-project-design.md).

**Delivery status:** `каркас`. Source integration и focused offline regressions
завершены локально; PR merge, deploy и live causal proof ещё не завершены и
зафиксированы в [`registry/029.yml`](registry/029.yml). Revision `7` остаётся
candidate, observed revision — `6`.

## Context

Существующий relationship layer хранит durable causes и due obligations
независимо от текущей сцены. Само наличие причины, события или срока не означает,
что персонаж находится рядом с игроком или участвует в текущем действии. Если
такой NPC автоматически попадает в narrator prompt, relationship obligation
может вернуть отсутствующего персонажа и вытеснить актуальную сцену.

Для второго slice нужен только детерминированный pre-scene фильтр поверх уже
существующих state, Outcome и WorldPack aliases. Новая авторитетная проекция
сцены, отдельное хранилище или второй relationship subsystem не требуются.

## Decision

Для `scenario_type=rp` на candidate revision `7` Gateway вычисляет bounded набор
`relationship_scene_character_ids` непосредственно перед рендерингом
relationship pressure. Набор является производным значением одного запроса, не
сохраняется. Персонаж становится eligible только по одному из трёх сигналов:

1. canonical location персонажа совпадает с `player.location`;
2. текущее действие игрока содержит однозначный whole-alias персонажа;
3. `Outcome.target` однозначно совпадает с тем же alias-набором.

Structured `active_threads` не является сигналом присутствия и не может
самостоятельно добавить NPC. Membership даёт только rank enrichment уже
eligible-кандидату.

Alias-набор состоит из stable character ID, canonical `name`/`display_name` и
явных aliases WorldPack. Свободный текст story memory, предыдущая narration,
наличие relationship cause/event, один только relationship edge и один только
active thread не являются сигналами присутствия.

### Deterministic top-N

Eligible-кандидаты получают суммируемый score из тех же authoritative inputs:

- explicit current-action alias или `Outcome.target`: `+100`;
- та же canonical location: `+30`;
- structured active thread: `+20`, но только после eligibility по одному из
  трёх сигналов выше.

Сортировка идёт по score по убыванию, затем по stable character ID по
возрастанию. После сортировки применяется существующий
`MAX_RETRIEVED_CHARACTERS = 6`. Одинаковый authoritative input всегда даёт один
и тот же набор; дополнительный LLM-вызов не выполняется.

### Relationship rendering

Существующие pressure и due-event renderers получают этот набор как allow-list.
В narrator prompt входят только causes и guidance персонажей из allow-list.
Фильтр не меняет веса, оси, clocks, relationship stores или правила extraction.

Due `favour` отсутствующего персонажа не рендерится, но остаётся durable и
`active`. Omission не является evidence исполнения и сама по себе не может
поставить `resolved`, `delivered` или `expired`. Когда персонаж снова попадает в
derived scope, due guidance снова становится eligible; закрыть обязательство
может только существующее evidence-checked правило по committed сцене того же
персонажа.

### Privacy and compatibility

Narrator-visible block сохраняет текущую sanitization: допускаются display name,
словесная полоса и качественное guidance. Character/event IDs, weights, clocks,
target/accomplice IDs и raw payload остаются внутри Gateway.

Revisions `0..6`, `novel` и `training` сохраняют текущий путь. Existing source
parties не мигрируют. Candidate проверяется только на checkpoint/autotest branch;
source raw history, canonical state и relationship rows не меняются.

## Verification boundary

Focused offline regressions должны доказать:

- каждый из трёх eligibility-сигналов выбирает правильного персонажа;
- active-thread-only, relationship cause, due event или edge без этих сигналов
  не выбирают NPC;
- active thread может изменить порядок уже eligible-кандидатов, но не расширяет
  их набор;
- score, tie-break и top-6 детерминированы;
- absent due `favour` скрыт из prompt и остаётся `active`;
- последующий alias/location/target снова делает guidance eligible, но не
  закрывает событие без matching committed evidence;
- revisions `0..6` и non-RP modes не меняются;
- renderer не раскрывает private relationship fields и не мутирует stores.

`Подключено` требует deployed isolated branch с одновременно relevant cause,
absent ordinary cause и absent due `favour`. Authored Starosta/Merchant state не
является достаточной fixture: почти все modeled NPC уже перечислены в
`active_threads`. Нужен отдельный branch/state proof, где absent NPC находится в
другой location, не назван в current action и не является `Outcome.target`;
membership в active thread допустим и должен не влиять на eligibility.

Checkpoint fork не переносит derived relationship rows, а revision-7 pre-provider
path намеренно их не seed-ит. Поэтому live proof выполняется после отдельного
warm-up turn либо явно задокументированного bootstrap. Recorded provider prompt
должен содержать только relevant pressure, а authoritative relationship rows до
и после omission должны подтвердить, что отсутствующее due obligation осталось
active. Один validator status или семантически правдоподобный ответ модели этого
не доказывает.

## Consequences

- Relationship obligation ждёт подходящего контекста и не захватывает narration.
- Durable causes не теряются из-за prompt filtering.
- Scope остаётся объяснимым, bounded и воспроизводимым без новой модели.
- Дополнительных таблиц, jobs и provider calls нет.

## Non-goals

- `scene_state`, persisted presence, schema migration или scene-state fast path;
- автоматическое перемещение NPC в сцену;
- новые relationship axes, weights, clocks или event kinds;
- изменение relationship extraction или acceptance oracle.

## Related decisions

- [Decision 020](020-rp-relationship-pressure-layer.md)
- [Decision 022](022-readiness-and-observability-policy.md)
- [Decision 024](024-simplified-rp-core.md)
- [Decision 026](026-rp-core-delivery.md)
- [Decision 028](028-rp-uncovered-tail-and-overflow.md)
