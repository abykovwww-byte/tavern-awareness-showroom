# Decision 038: GM corrections and player-authority overlay

**Дата:** 2026-08-25

## Status

**Decision status: Accepted.** RP revision `9` отделяет внеигровое исправление
канона от обычного действия в сцене. Исправление проходит через ограниченный
черновик, становится фактом только после явного подтверждения игроком и остаётся
видимым narrator поверх RAW/story memory, пока соответствующая секция памяти не
зафиксирует его с authority `user`.

**Delivery status:** `каркас` для строк
[`registry/038.yml`](registry/038.yml). Revision 10 активирован, и первый
60-turn production endurance выполнил реальный one-section OpenRouter call, но
обнаружил collision одинакового replacement `fact_id`: coverage продвинулась,
а replacement сохранился как `inference`, поэтому overlay правильно остался
active. Source closure устраняет collision; повторное live absorption evidence
ещё требуется. Existing parties автоматически не мигрируют и не переписываются.

## Context

Обычная реплика игрока раньше одновременно могла означать действие персонажа и
попытку исправить ошибку narrator. Legacy story-memory correction привязывалась
к следующему игровому ходу, поэтому внеигровая правка загрязняла сцену, могла
сдвинуть игровое время и до фонового обновления оставляла противоречие в prompt.

Отдельный свободный `/world` path для этого не подходит: он допускает более
широкие state additions, меняет `meta.turn` и имеет собственные draft/fallback
правила. S3 нужен более узкий контракт — только замена или отзыв уже
существующего утверждения.

## Decision

### Revision boundary

- Новый path включён только для `scenario_type=rp` и
  `rp_contract_revision >= 9`.
- Revision `9` является candidate. Изменение max revision само по себе не
  повышает observed revision и не меняет declared revision WorldPack.
- Revisions `0..8`, training, existing parties и legacy correction payload
  сохраняют прежнее поведение. Rev9 отклоняет legacy
  `story_memory_corrections[]` и требует GM channel.

### Routing before narration

`POST /api/parties/{party_id}/messages` совместимо принимает
`channel=auto|scene|gm` и optional `gm_target_slot`.

- `scene` сразу использует прежний narrator path.
- `gm` всегда обходит classifier и narrator.
- `auto` выполняет один `gm_intent` request только в local Gemma. Полный
  serialized prompt ограничен 2 000 символами, output — `100` tokens; strict
  JSON содержит только `label=scene|correction|uncertain` и `target`.
- Неуверенный, оборванный или недоступный classifier ничего не коммитит и
  возвращает явный выбор `Мастеру / В сцену`. Кнопка «Мастеру» доступна всегда
  для rev9, поэтому classifier не является единственным входом.
- Реплика персонажа без явного исправления остаётся сценой. Например,
  «Он слуга Ждана» — scene, а «Он слуга Ждана, а не летописец» — correction.

### Bounded draft and explicit decision

GM instruction не длиннее 600 символов; превышение отклоняется без усечения и
до model call. Один local `gm_patch_draft` request получает не более 4 000
символов input и 300 output tokens. Strict result выбирает ровно одну
существующую цель:

- active list fact из RP story memory — `replace` или `retract`;
- конкретное утверждение из recent eligible RAW — только `replace`;
- существующий `WORLD_ABSOLUTE_RULES` item — только `replace`.

Свободное добавление факта или правила запрещено. Gateway повторно сверяет
target, exact `before`, разрешённое действие, field/section, current state
version и capacity после возврата модели и ещё раз при confirm. Черновик не
меняет state, turn, memory или историю.

Light GUI показывает `before / after`. Только
`POST /api/parties/{party_id}/gm-corrections/decide` с `decision=confirm`
создаёт исправление; `reject` является read-only. Повтор confirm с тем же
idempotency key возвращает сохранённый результат и не создаёт второй turn.

### Atomic out-of-scene record

Confirm выполняется без narrator и одной SQLite transaction:

1. создаётся `state_versions +1` с correction reason;
2. `meta.turn`, `last_turn`, игровое время и `scene_state` не сдвигаются;
3. в existing `turns` добавляется строка `turn_kind=gm_correction` с прежним
   `party_turn` и `excluded_from_memory=1`;
4. request получает terminal `completed`, а audit фиксирует отсутствие
   narrator call.

Metadata содержит artifact `rp-gateway.player-correction.v1`: correction ID,
target kind/ID/slot/turn, section, action, exact `before/after`, player
provenance и `status=active|absorbed`. History API проецирует его как отдельную
внеигровую запись, а не пару player/narrator и не даёт ей feedback/Lore Card UI.

Absolute rule заменяется синхронно в canonical state с тем же ID/scope/kind и
новыми text, turn, source и `forbidden_claims`; такой artifact сразу terminal.
Новый absolute rule через этот path создать нельзя.

### Overlay and capacity

Active memory/RAW corrections образуют один system block
`ИСПРАВЛЕНИЯ ИГРОКА`. Он располагается после RAW, story memory и Lore Cards,
но не может менять `WORLD_ABSOLUTE_RULES` или подменять current player action.
Block не является optional overflow-кандидатом: старая противоречащая фраза в
RAW остаётся исторической репликой, но не текущим каноном.

Одновременно допускается не более 20 effective active target slots. Новый 21-й
slot отклоняется до patch model. Новая версия того же slot допустима и не
занимает дополнительное место; effective overlay использует только последнее
состояние slot. Active slots не вытесняются и не скрываются ради лимита.

### One-section absorption valve

Memory/RAW confirm создаёт request-scoped `rp_story_memory` job с двумя durable
attempts даже до обычного порога 50 ходов. Job пересобирает ровно одну
затронутую section через explicit OpenRouter
`deepseek/deepseek-v4-pro`; остальные четыре section coverage не продвигаются.
Пустой структурно валидный ответ допустим. Semantic retry и вызовы остальных
секций запрещены.

Gateway применяет existing typed replace/retract детерминированно после service
response и назначает terminal target/replacement authority `user` с GM turn как
provenance. Overlay снимается только когда одновременно выполнены оба условия:

Если service response уже содержит тот же replacement как новый `inference`,
Gateway не добавляет второй объект с одинаковым `fact_id`, а повышает найденный
объект до `authority=user` и заменяет provenance на GM turn. Это сохраняет один
факт и делает absorption idempotent независимо от того, успела ли модель сама
сформулировать исправленный текст.

1. exact user-authority fact либо tombstone сохранён в effective snapshot;
2. coverage затронутой section не меньше `target_turn_id`.

Safe coverage всего snapshot по-прежнему равен `min()` пяти section coverages.
Ошибка memory job не откатывает confirm: artifact остаётся active в overlay и
повторяется только эта section.

### Explicit service routing and failures

- `gm_intent`, `gm_patch_draft` и rev9 `relationship_extraction` используют
  только local Gemma, без наследования `LLM_PROVIDER`, party narrator/BYOK,
  NVIDIA, model fallback или provider retry.
- Rev8+ story memory, включая correction valve, остаётся на exact OpenRouter
  DeepSeek route; один normal call по-прежнему возвращает пять секций, отдельный
  request выполняется только для структурно невалидной секции.
- Relationship extraction сохраняет `SERVICE_JOB_MAX_ATTEMPTS=5`; после
  исчерпания job получает terminal `stale`, а gameplay turn остаётся committed.
- Story-memory jobs имеют максимум две попытки. Provider/role/model/result
  видны в `service_call_log`; новый вызов никогда не получает
  `provider=nvidia`.

## Consequences

- Игрок исправляет narrator без фиктивного действия персонажа и без сдвига
  сцены.
- Противоречие не ждёт следующего 50-turn batch: overlay защищает prompt сразу,
  а one-section valve даёт памяти возможность поглотить его с первого хода.
- Local small model выбирает маршрут и готовит ограниченный diff, но не получает
  полномочий записывать канон; запись остаётся за Gateway и игроком.
- История сохраняет проверяемый provenance, не увеличивая число JSON-слоёв,
  передаваемых narrator каждый ход.

## Non-goals

- свободное добавление lore, facts, rules или state patch через GM channel;
- смысловая оценка качества memory section и retry ради непустого ответа;
- автоматический confirm, silent correction или удаление исходного RAW turn;
- новая таблица, новый provider, новая dependency или миграция existing parties;
- activation revision `9`, Ansible apply, live provider canary и длинный
  semantic endurance run в этом source slice.

## Verification gates

Локальные gates проверяют bounded local requests, uncertain route без mutation,
explicit GM bypass, reject без mutation, atomic confirm/idempotency, сохранение
party turn/scene/time, excluded GM history, limit 20, overlay order, one-section
turn-12 absorption с authority `user`, safe coverage, relationship terminal
`stale`, отсутствие NVIDIA route и Light GUI diff/choice/history behavior.

После завершения RP-core отдельная новая long-party должна подтвердить цепочку
`player correction -> draft -> confirm -> active overlay -> one section call ->
user fact + coverage -> absorbed -> next narration`. До apply/live evidence все
registry rows остаются на уровне `каркас`.

## Related decisions

- [Decision 016](016-rp-living-story-memory.md)
- [Decision 032](032-rp-history-first-prompt-and-sectioned-memory.md)
- [Decision 036](036-retire-novel-and-nvidia.md)
- [Decision 037](037-rp-authored-lore-cards-and-confirmed-drafts.md)
