# Decision 024: Упрощённое RP-ядро без скрытых проверок

**Дата:** 2026-08-11

## Status

**Decision status: Accepted.**

Порядок поставки, кумулятивные `rp_contract_revision`, миграционные границы и
проверка S1–S6 заменены [Decision 026](026-rp-core-delivery.md). Продуктовые
инварианты этого решения сохраняются; общий P0 и обязательный acceptance-контур
больше не являются предусловием реализации независимого вертикального слайса.

Решение применяется только к `scenario_type=rp` и включается версионированным
WorldPack-контрактом `rp-core.v2`. Существующие партии остаются на
`rp-core.v1`, пока не пройдут отдельную явную миграцию. `training` и `novel` не
меняются.

## Context

Партия «Староста» показала четыре связанных разрыва: свободный текст превращался
в `feasibility` со скрытым D20; абсолютное правило силы не удерживалось после
ответа модели; full-replacement story memory сохраняла ложную проекцию после
коррекции; данные персонажей и `/timeline` могли существовать без причинного
влияния на следующий prompt и сцену.

Raw turns, state versions и prompt traces при этом сохранялись. Их удаление или
переписывание не входит в решение.

### Reality Pass

Сохраняются raw turns, state versions, `prompt_json`, WorldPack constraints,
существующие story-memory/relationship/state stores и явный World Instructor
apply. Выводятся из нового RP-контура free text → `feasibility`, случайный D20,
механический `POST /checks` и принятие любого непустого ответа без проверки
абсолютных правил. Расширяются существующие memory, relationship и state
механизмы; новая универсальная character/state-подсистема не вводится.

## Implementation precondition: P0

До заявления evidence-ступеней Decision 022 обязательны:

1. полный локальный и container test path, включая acceptance-модули;
2. provider-canary, связанный с фактически выполненным запуском;
3. независимо зафиксированный и хешированный пользователем acceptance-корпус,
   разметка и пороги;
4. красный R0 baseline партии «Староста» с source revision, snapshot identity и
   доказательствами четырёх исходных разрывов;
5. source-backed карта `сохраняем | выводим | расширяем | вводим впервые`.

До выполнения P0 реализация и локальные тесты могут быть поставлены, но registry
не поднимается до `подключено`, `наблюдается` или `держится`.

## Versioned activation

RP WorldPack объявляет `rp_contract.schema_version`. Новые активные RP-паки
используют `rp-core.v2`; старая партия без явной миграции продолжает работать на
`rp-core.v1`. Gateway сохраняет выбранную версию в party store.

## Decision

RP-ядро подчиняется шести инвариантам.

### 1. Raw history неизменяема, prompt — выбираем

Все исходные сообщения и ответы сохраняются без удаления, переписывания или
замены summary. Raw history служит аудитом и источником пересборки проекций, но
prompt выбирает только актуальные недавние ходы, active facts, релевантные
эпизоды и персонажей в пределах существующей context policy.

### 2. В RP нет скрытых проверок

Для `rp-core.v2` свободный текст и compatibility `POST /checks` превращаются в
нейтральный `narrative_continuation`: без `feasibility`, случайного броска,
difficulty, score, success или failure. Envelope не создаёт запись `checks` и
не меняет мир сам по себе. Сложность следует из WorldPack, state, информации,
ресурсов, целей NPC, отношений и прежних последствий.

### 3. Абсолютные правила исполняются после ответа

WorldPack может типизировать `world_constraints` как `kind: absolute`, указать
стабильный `id`, `source` и машинно проверяемые `forbidden_claims`. Gateway
добавляет их отдельным `WORLD_ABSOLUTE_RULES` блоком и валидирует ответ модели до
применения state patch и записи turn. Нарушение запускает существующий repair;
повторное нарушение приводит к контролируемому отказу, а не к канону.

### 4. Story memory — восстанавливаемая проекция

`rp-gateway.rp-story-memory.v2` хранит каждую запись с `authority`,
`source_turn_ids` и состоянием `active | superseded | retracted`. Snapshot остаётся
append-only и аудируемым, но в narrator prompt попадают только active entries.
WorldPack, canonical state и явная пользовательская коррекция выше narrator и
model inference.

### 5. Персонажный loop использует существующую проекцию

Decision 020/021 и runtime closure Decision 022 остаются механизмом
`seed/scene → attributed cause → durable projection → RELATIONSHIP_PRESSURE →
next prompt`. ADR-024 не вводит вторую character subsystem. Значимое активное
поле считается подключённым только при наличии writer, store, prompt/runtime
consumer, correction rule и causal probe.

### 6. У активного состояния есть consumer — иначе оно выводится из контура

Активное поле или projection требует writer, авторитетный store, prompt/runtime
consumer, правила correction/migration и причинную пробу. Данные без consumer
не выдаются за механику и выводятся в legacy-контур отдельной миграцией.
`/timeline` остаётся audit trail и само по себе не доказывает изменения мира или
персонажа.

## Migration

Автоматической миграции старых RP-партий нет. Для отдельной партии нужны:

1. checkpoint raw history и projections;
2. сохранённый hash raw turns;
3. кандидат story-memory v2 с provenance/status;
4. приоритет WorldPack и canonical state над legacy snapshot;
5. пользовательская проверка correction/character corpus;
6. атомарное переключение party contract либо сохранение v1.

Исторические checks остаются для аудита, но не используются после переключения.

## Acceptance boundary

ADR не задаёт размер prompt, срок проявления character cause, число повторов и
model profiles, количество repair-попыток, endurance horizon или semantic
пороги. Они принадлежат отдельно одобренному и хешированному acceptance-манифесту
Decision 022; код механики и его оракул не принимаются одним изменением.

Красный baseline R0 должен воспроизводимо показать скрытую проверку, разомкнутую
цепочку доверия Любавы, активное ложное ограничение силы после коррекции и
отсутствие потребляемого эффекта за `/timeline`.

Реализация считается `подключённой` после исполнения нового пути реальным
RP-ходом, `наблюдаемой` после прохождения зафиксированных случаев до следующего
prompt и сцены и `держащейся` только после production endurance. Общий средний
score не может скрыть провал одного инварианта; hash raw history при миграции не
меняется.

## Consequences

- Light GUI больше не показывает механическую RP-форму.
- Активные RP WorldPacks удаляют `/check`/D20-инструкции и объявляют v2.
- Публичный `POST /checks` сохраняется как совместимый нейтральный вход.
- `training` deterministic scoring и `novel` prose contract не меняются.
- Story-memory v1 snapshots остаются читаемыми и нормализуются как
  `legacy_projection`; их автоматическая активация в v2-партии не выполняется.

## Supersession and dependencies

- Decision 022 остаётся обязательным evidence-gate; Decision 027 предоставляет
  диагностику, но не является продуктовой зависимостью.
- Decision 016 заменён в части авторитетности story memory.
- Decisions 020 и 021 сохраняются и расширяются замкнутым character loop.
- Decision 010 сохраняет разделение режимов; его RP-resolution superseded для
  `rp-core.v2`.

## Non-goals

- удаление raw turns или исторических checks;
- универсальный physics/event-sourcing engine;
- новый character dashboard;
- изменение `training`/`novel`;
- автоматическая миграция существующих партий;
- численные acceptance-пороги и production endurance horizon.
