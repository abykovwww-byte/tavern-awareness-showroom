# Decision 040: RP supervisor and bounded rule reassertion

**Дата:** 2026-08-26

## Status

**Decision status: Accepted.** RP Gateway получает опциональный надзорный слой,
который ретроспективно оценивает поведение нарратора на цельном окне партии.
Первая активация `day-watch-moscow-v2` использует только `observe`; она ничего не
добавляет в prompt нарратора.

**Delivery status:** `каркас` по требованиям
[`registry/040.yml`](registry/040.yml). Механизм, контракт WorldPack, API и UI
реализованы и прошли локальные проверки, но ещё не Ansible-applied и не прошли
первую реальную 56-ходовую ретроспективу. Decision 041 уже поставлен и больше
не блокирует это решение.

## Context

Поведенческое правило может оставаться в начале prompt, но постепенно проиграть
десяткам свежих примеров собственного вывода модели. Отдельный поздний ход при
этом выглядит связным, поэтому per-turn validator не видит дрейф. Story memory
тоже не подходит: она хранит факты и нити, а не оценивает форму ведения на
длинной дуге.

Надзор нужен не для проверки длины или «книжности» прозы. Он также не определяет
локацию действия: нарратор справляется с местом сам, а ось `scene_mobility`
измеряет только развитие сценовых ситуаций по уже записанному тексту.

## Decision

### Явное включение WorldPack

Надзор не появляется у существующей партии автоматически. RP WorldPack явно
объявляет безопасный файл внутри pack:

```json
"files": {"rp_supervisor": "rp-supervisor.json"}
```

Файл имеет закрытую схему `rp-gateway.rp-supervisor.v1` и один из режимов:

- `observe` — оценивать и сохранять типизированные результаты, но никогда не
  создавать prompt-блок;
- `enforce` — дополнительно применять объявленные WorldPack коридоры и
  авторские тексты рекомендаций.

Механизм не требует новой ревизии `rp-core.v2`. Первая поставка добавляет
`day-watch-moscow-v2/rp-supervisor.json` в `observe`, без численных коридоров.

### Окно и каденс

- Вход — ровно последние `50` канонических playable RP units из сохранённых
  player+narrator turns.
- `opening_scene` с текстом считается unit; `world_command`, `gm_correction`,
  rollback-excluded и noncanonical fallback не считаются.
- Story memory, state summary и иные производные слои на вход не подаются.
- Запуск происходит на кратных восьми количествах eligible units, когда уже
  накоплено не меньше 50: впервые на `56`, затем на `64`, `72` и так далее.
- Окно заморожено `source request/turn ID`, его границы и SHA-256 сохраняются.
- Усечение запрещено. Если полный prompt плюс резерв ответа не помещаются в
  объявленный context активной служебной модели, Gateway не вызывает провайдера
  и сохраняет `unchecked/context_capacity`.

Надзор использует существующий глобальный service-model route и не выбирает,
не меняет и не ретраит отдельный provider/model. Независимый model selector не
входит в это решение.

### Шесть осей

Один structured-output вызов оценивает ровно шесть WorldPack-authored rubric:

1. `world_resistance` — обоснованное сопротивление мира без автоматической
   уступчивости или тотального блокирования;
2. `turn_return_variety` — естественное разнообразие возврата инициативы без
   одной повторяющейся формулы и без искусственного меню;
3. `consequence_pressure` — устойчивые последствия без их исчезновения или
   перегрузки каждого эпизода новой ценой;
4. `conflict_continuity` — развитие и развязка конфликтов без регулярной замены
   «кем-то сильнее» и без вечной неподвижности;
5. `world_agency` — самостоятельные цели мира при сохранении реального влияния
   героя;
6. `scene_mobility` — причинное развитие сценовых ситуаций без застывания или
   бессвязных скачков; это не authoritative location tracking.

Модель возвращает только `rule_id`, `score`, `confidence`,
`evidence_turn_ids` и `status=ok|unknown`. Evidence IDs обязаны принадлежать
замороженному окну. Gateway сам вычисляет `direction` и severity по коридору в
режиме `enforce`; модель не получает власть над политикой.

Для `turn_return_variety` действует узкий regex sentinel по повторяющимся
замыканиям последних 16 units. Он не является отдельным классификатором. Если
его оценка расходится с model score больше чем на `0.35`, подавляется только эта
ось и выставляется диагностический флаг; остальные пять результатов остаются
независимыми.

### Ограниченное переутверждение

В `observe` любой результат помечается `observe_mode`, а advisory всегда пуст.

В `enforce` WorldPack обязан задать для каждой оси `corridor.min/max`,
`advisory_below` и `advisory_above`. Gateway рассматривает только `status=ok`,
confidence не ниже общего порога и непустое evidence. Из отклонений выбираются
не более двух самых сильных. Один и тот же direction может быть переутверждён
не более `K=3` последовательных ретроспектив; четвёртая подавляется с флагом
оператору.

Тексты advisory — заранее проверенная политика WorldPack. Они описывают только
манеру ведения и не называют персонажа, нить, событие, цель или место. Gateway
формирует один блок до 800 символов:

```text
RP_SUPERVISOR_ADVISORY
<низкоприоритетное пояснение>
- <не более двух авторских рекомендаций>
```

Он стоит после relationship pressure и world events, но до author note и
текущего действия, включая repair prompt. Блок ниже канона, абсолютных правил,
подтверждённых исправлений, story memory и фактов мира. Он не пишет state, не
регенерирует показанный ход и не конкурирует с Decision 039.

### Хранение, privacy и lifecycle

Отдельная таблица хранит только типизированную оценку, advisories, флаги,
границы/hash окна, contract hash, provider/model, статус, token/context counts,
latency и сроки. Полный supervisor prompt и сырой provider response не попадают
в `service_call_log`: вызов использует `ServiceModelClient(..., trace=False)`.

TTL равен ровно 30 дням. Просроченные строки не читаются и удаляются при
следующей записи/явной очистке. Rollback инвалидирует оценку, если откат затронул
любой turn её окна. Party deletion удаляет строки раньше turns; ветка не
наследует оценки родительской партии.

Ошибки structured output, transport и contract evaluation fail-open для игры:
ход уже сохранён и не откатывается, а ретроспектива получает отдельный
`error/<type>`. `checked`, `unchecked` и `error` нельзя смешивать с «замечаний
нет».

### Пользовательский статус

Owner-scoped `GET /api/parties/{party_id}/supervisor` отдаёт mode, eligible
turn count, первую/следующую ретроспективу, выбранный глобальный service model и
последнюю типизированную оценку. Light GUI показывает это в существующем экране
памяти. Для `observe` он прямо сообщает, что надзор ничего не добавляет в prompt
нарратора. Нового selector, панели настройки или location field нет.

## Живое доказательство

Зелёные тесты доказывают каркас, но не продуктовый эффект. Для перехода выше
`каркас` нужна реальная party chain:

```text
50-turn frozen window and hash
→ due retrospective at eligible turn 56 or later
→ typed checked result
→ (для enforce) authored advisory selected
→ exact advisory present in next stored prompt_json
→ next retrospective returns inside corridor or reaches bounded K
→ subsequent prompt removes the advisory
```

Первый baseline `day-watch-moscow-v2` остаётся `observe`, поэтому его честное
доказательство заканчивается на typed `checked` result и отсутствии advisory во
всех последующих prompts. Коридоры и переход к `enforce` принимаются отдельным
явным изменением после просмотра baseline; они не выводятся автоматически.

## Non-goals

- Не определять и не хранить локацию действия.
- Не ограничивать длину или литературность хода.
- Не вести канон, события мира, отношения или story memory.
- Не добавлять provider/model selector и не менять существующую маршрутизацию.
- Не добавлять зависимости, новый сервис, отдельный scheduler или RP revision.
- Не активировать enforcement и не угадывать коридоры в первой поставке.

## Related decisions

- [Decision 016](016-rp-living-story-memory.md)
- [Decision 022](022-readiness-and-observability-policy.md)
- [Decision 032](032-rp-history-first-prompt-and-sectioned-memory.md)
- [Decision 039](039-rp-world-clock-and-authored-events.md)
- [Decision 041](041-rp-narrative-presets-and-opening-seeds.md)
