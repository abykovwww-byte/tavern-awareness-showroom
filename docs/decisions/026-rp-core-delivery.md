# Decision 026: Поставка упрощённого RP-ядра вертикальными слайсами

**Дата:** 2026-08-12

## Status

**Decision status: Accepted.** Решение пользователя.

**Delivery status:** candidate revision `6` `подключена` на `abykovserv` и
прошла live-canary на развёрнутом Gateway. PR #35 доставил кумулятивные
S1–S6, PR #36 открыл candidate revision в admin autotest, PR #38 исправил
обнаруженный live-разрыв S6 и применён в merge revision `6e1de87`.
Контейнерный набор после последнего apply: `333 passed, 1 skipped`.

Candidate-run `autotest_07cbe4b1df74` на checkpoint-ветке
`branch_c58108e112ec` завершил 1/1 ход через OpenRouter
`openai/gpt-5.6-luna-pro`: `transport_status=ok`, `fallback=false`, validator valid,
новой строки `checks` нет. Effective prompt составил 129654 символа
при 260384 символах полного raw transcript — 49,79%. Source party осталась
на 168 ходах, а её raw-префикс в branch совпал по SHA-256. Этот rollout
поднимает `RP_CONTRACT_OBSERVED_REVISION` до `6` для новых обычных RP-партий;
существующие party автоматически не мигрируют. Статус `держится` не
заявляется: общий 50-turn endurance ещё не выполнен.

Decision 024 сохраняет продуктовые инварианты RP-ядра. Это решение заменяет его
единый P0-контур на шесть независимо проверяемых вертикальных слайсов.

## Context

Партия «Староста» показала не дефект модели, а разрывы runtime:

- свободный RP-текст мог превращаться в `feasibility` и скрытый D20;
- full-replacement story memory могла воскресить исправленный факт;
- абсолютное правило WorldPack не проверялось после ответа narrator;
- relationship cause могла сохраниться, но не дойти до реального provider prompt
  и наблюдаемого поведения;
- часть state существовала без доказанного потребителя;
- длинная raw history хранилась правильно, но effective prompt мог быть избыточен.

Зелёный CI, HTTP 200 или наличие строки в SQLite не заменяют доказательство цепочки
`input -> authority -> projection -> provider prompt -> later scene`.

## Decision

### Границы

1. Изменяется только `scenario_type=rp`.
2. `training` сохраняет WorldPack-owned deterministic runtime без D20; scoring,
   evidence, progression и debrief не меняются.
3. `novel` сохраняет немеханический контракт.
4. Gateway остаётся authority для версии RP-контракта, state, memory, prompt,
   validation и commit хода.
5. Публичные интерфейсы сохраняются; `rp_contract_revision` добавляется совместимо.
6. Raw turns, audit и прежние check-записи не удаляются и не переписываются.
7. Новые общие платформы, registry state и event sourcing не вводятся.

### Кумулятивная ревизия

Gateway хранит `rp_contract_revision` в party и branch metadata:

- `0` — legacy-поведение до этого решения;
- `1..6` — наивысший активный слайс, ревизии кумулятивны;
- WorldPack объявляет максимальную поддержанную ревизию в
  `rp_contract.revision`;
- обычная новая партия получает не больше
  `RP_CONTRACT_OBSERVED_REVISION`, который по умолчанию равен `0`;
- checkpoint-ветка или autotest может явно запросить candidate-ревизию, не меняя
  исходную партию;
- существующие партии не мигрируют автоматически.

Поднятие `RP_CONTRACT_OBSERVED_REVISION` разрешено только после deploy и успешного
live-canary соответствующей ревизии. Явная миграция существующей source party и
перенос candidate-проекций требуют отдельного пользовательского действия; этот ADR
не разрешает менять живую исходную партию автоматически.

### S1 — RP без скрытой проверки

- Любой обычный RP-ввод и compatibility `/check` дают нейтральный
  `narrative_continuation` с нулевым outcome envelope.
- `random.SystemRandom`, D20, DC, skill score и success/failure не участвуют и не
  создают строку `checks`.
- `training` остаётся deterministic и также не использует случайный D20.

### S2 — Correction-aware living memory

- Каждая запись v2 получает стабильный `fact_id`, authority, source turn и статус
  `active | superseded | retracted`.
- Snapshot остаётся append-only; merge выполняется Gateway, а не доверяется полной
  замене сервисной моделью.
- Weak inference не может отозвать факт, вернуть tombstone в active или создать
  новый tombstone без известного факта.
- Service model не назначает authority и provenance: Gateway принудительно делает
  каждую новую или изменённую запись `inference` и разрешает только IDs ходов
  фактически обработанного пакета. Неизменённая запись сохраняет прежнюю authority.
- Более сильная и более новая user/state/WorldPack authority может изменить статус.
- В существующем party message API для RP revision 2+ доступна типизированная
  коррекция активной list-записи: `retract` или `replace` по её `field` и
  `fact_id`. Клиент не передаёт authority, status или provenance: Gateway
  проверяет цель до provider call, сохраняет correction в metadata того же
  committed turn и использует фактический turn ID как источник.
- Коррекция форсирует ближайшую сборку snapshot и сразу накладывается на
  effective narrator prompt. Поэтому задержка или retry фоновой service job не
  возвращает ошибочный факт в текущую или следующую сцену; повторная коррекция
  уже неактивной цели отклоняется до state/turn mutation.
- Bounded memory освобождает место за счёт weak-записей и не отбрасывает
  tombstone или replacement с `user_correction`; если безопасного места нет,
  preflight отклоняет `replace`, а не превращает его в неявный `retract`.
- `current_situation` остаётся rolling single-object projection и не входит в
  list-entry correction API: для него нельзя сохранить одновременно tombstone
  и replacement без отдельного контракта идентичности.
- В effective prompt попадают только active v2-записи; legacy `legacy_projection`
  не активируется автоматически при повышении revision и не передаётся service
  model как материал для перефразирования.
- Rollback сохраняет append-only audit, но инвалидирует snapshots, покрывающие
  исключённые ходы; UI, updater и prompt читают newest valid projection. Запись
  результата фоновой job атомарно проверяет contributing turns и base snapshot,
  поэтому job, пережившая rollback, не может воскресить отменённую ветку.

### S3 — Absolute rule enforcement до commit

- Typed `world_constraints` с `kind=absolute` и `forbidden_claims` входят в prompt.
- Первый ответ narrator валидируется, допускается один repair, затем выполняется
  повторная validation.
- При повторном нарушении нет state version, turn, artifact или успешного ответа,
  включая RP candidate/autotest branch. Gateway fallback остаётся только у
  deterministic training runtime.
- Тот же контракт действует для opening scene.

### S4 — Замкнутый персонажный цикл

Цепочка должна быть полной:

```text
seed или подтверждённая сцена
-> stable character_id + evidence
-> durable relationship projection
-> qualitative pressure в фактическом provider prompt
-> наблюдаемая услуга/конфликт/последствие
-> deterministic resolution или продолжение причины
```

Для актуального source WorldPack `starosta` positive seed Бажены открывает `favour`
по WorldPack clock. На due turn Gateway передаёт narrator обязательство показать
конкретную добровольную помощь, но не считает часы доказательством исполнения.
Событие получает `resolved/delivered` только после evidence-checked extraction
положительной причины того же персонажа из записанной сцены, причём authored
event WorldPack должен явно разрешать это через `resolves: ["favour"]`; иначе
событие остаётся active. Delivery хранит source turn ID. Rollback этого хода
атомарно возвращает `favour` в active, а запоздавшая extraction job не может
закрыть его по уже исключённому ходу.
Внутренние ID, численные веса и сроки не раскрываются. Старый live-checkpoint с Любавой
проверяется по тому же инварианту после deploy, если пользователь создаёт его копию.

### S5 — Consumer-or-retire

Retained RP paths имеют реальный prompt/runtime consumer: player facts, relevant
characters, relationships, factions, locations, resources, active/completed threads,
uncertain facts и absolute constraints. `/timeline` остаётся audit-only и не
объявляется активной механикой.

### S6 — Неизменяемая raw history и выборочный prompt

- Raw turns полностью сохраняются и не мутируют при сборке prompt.
- Effective prompt использует bounded recent turns, active story memory, chapters,
  lore, relevant characters, dynamic state и выборочный archive retrieval.
- Offline long-party fixture должен дать prompt не больше 50% полного raw transcript,
  когда процентная граница достижима без удаления минимального continuity tail,
  сохранить текущее действие и тот же hash raw turns.
- Для revision 6 Gateway перед provider call удаляет старейшие полные
  `user/assistant`-пары, пока длинный effective prompt не уложится в этот порог,
  но останавливает percentage-only trimming перед последней полной парой.
  Обязательные system-блоки, последняя полная пара и текущее действие не режутся
  только ради процента. Если этот минимальный continuity tail уже превышает 50%,
  effective prompt может быть больше процентной границы; реальный provider input
  token budget остаётся обязательным и при необходимости сокращает контекст дальше.

## Проверка и ступени готовности

Словарь Decision 022 сохраняется:

- `каркас` — локальный код и focused tests;
- `подключено` — путь реально выполнен на развёрнутом runtime;
- `наблюдается` — исходный дефект исчез и доказана вся причинная цепочка;
- `держится` — после S6 пройден общий 50-turn endurance.

Каждый слайс требует offline regression, deploy через pull-based IaC и live-canary.
Для S2/S4 canary выполняется на новой партии или checkpoint-ветке, не меняющей source
party. После S6 один изолированный live-run должен пройти 50 committed RP-ходов и
включать S1–S6, сохранив raw-history hash.

## Supersession

- Decision 024 сохраняет продуктовые инварианты, но его общий P0,
  acceptance-manifest и обязательный registry не блокируют отдельный слайс.
- Decision 022 сохраняет словарь готовности, причинные доказательства и раздельные
  delivery-состояния; proxy-метрики не становятся доказательством фичи.
- Decision 016 заменяется S2 только в части authority и merge RP story memory.
- Decisions 020/021 остаются основой relationship projection и расширяются S4.

## Non-goals

- observability UI;
- универсальный physics/semantic engine;
- event-sourcing или общая feature-flag платформа;
- удаление исторических raw turns/checks/projections;
- переработка `training` или `novel`;
- автоматическая миграция существующих source parties;
- соседний рефакторинг.
