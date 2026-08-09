# Decision 019: Убрать legacy Awareness из Gateway и упорядочить снятие RP-валидатора

<!--
Part A is the permanent decision record. It matches Decisions 006-018 in tone:
prose, no agent instructions, no task lists. It must still read correctly years
later, after Part B is obsolete.
-->

## Status

Accepted. Decided by the user on 2026-08-09; три вопроса, оставленные открытыми в
первой редакции — поверхность fallback-текстов, судьба активных legacy-партий и
срок жизни RP-полей `validator_valid`/`repaired`/`fallback` — закрыты
пользователем в тот же день и внесены в решение. Implementation is delegated to
Codex; commit, deployment and live verification are separate delivery states.

## Context

Владелец продукта зафиксировал два требования.

Первое: post-generation проверка художественного текста ухудшает то, ради чего
существует RP, и приоритет — как играется человеку. У игрока есть собственный
канал починки — ГМ-мод и `POST /api/parties/{party_id}/rollback`, — поэтому ход,
который можно переиграть, стоит дешевле, чем задержка и шаблон на каждом ходу.
Для Training обратное: там детерминизм есть предмет поставки.

Второе: ограничение «10 ходов» должно было уехать в состояние миров `awareness`
и `awareness-one-day`, а не остаться свойством Gateway. Это ровно инвариант 1
Decision 017 — Gateway не содержит campaign-ID ветки, расписания, регекса
предметной области, веса баллов, ключа ответов и course-specific фолбэка.

Аудит рабочего дерева 2026-08-09 показал, что механизм инцидента шире, чем
описано в Decision 018. `awareness_final_summary`
(`rp-gateway/app/services/validator.py`) состоит целиком из сравнения
`meta.turn > 10` — без проверки кампании и без проверки worldpack. Условие в
`adjudicator.py` до временной правки было `awareness_final_summary(state) and
not (training_runtime and training_runtime.enabled)`; для любой RP-партии
`training_runtime` равен `None`, поэтому вторая половина всегда истинна и всё
условие сводилось к `turn > 10`. Следствие: у каждой RP-партии в любом мире,
начиная с одиннадцатого хода, ответ нарратора подменялся шаблоном
`safe_fallback`, после чего шаблон подавался валидатору и признавался валидным.
Это не пересечение режимов и не совпадение формы состояния, а безусловное
поведение.

Это место вызова было единственным незащищённым: остальные обращения к
`awareness_*` в `validator.py` и `main.py` проходят через `is_awareness_campaign`,
а `party_start_state_patch` защищён и по кампании, и по `scenario_type`. Рядом
живёт близнец того же дефекта — `awareness_turns_remaining(turn) =
max(10 - turn, 0)` в `rule_engine.py`, где десятка также не привязана к кампании.

Причина, по которой этот код ещё жив, — незавершённая миграция, а не нарушение
решения. Раздел Compatibility в Decision 017 явно оставил legacy-путь как
deprecated. WorldPack `awareness-one-day` мигрирован; `awareness` — нет: в его
манифесте есть `training_artifacts` и `training_workspace`, но нет
`training_runtime`, а расписание курса (10 ходов, нечётные 10:00-14:00, чётные
15:00-18:00, отдельный финальный разбор после хода 10) описано прозой в
`assumptions`. Один немигрированный пак удерживает в Gateway 140 вхождений
`awareness` в пяти файлах: `rule_engine.py` 54, `validator.py` 69, `main.py` 12,
`adjudicator.py` 4, `party_store.py` 1 — расписания `AWARENESS_TURN_WINDOWS`,
`AWARENESS_ONE_DAY_SECURITY_TURNS`, `AWARENESS_ONE_DAY_SITE_TURNS`, шесть
регексов предметной области, четыре course-specific фолбэка и две функции
начисления баллов.

Существующий дизайн не может принять первое требование как есть, потому что
порядок обратный делает следующую протечку невидимой: пока legacy-путь жив,
нарративная валидация в RP остаётся единственным, что его ловит.

Отдельно аудит показал, что канал починки игрока покрывает мир, но не память.
`rollback` в `state_store.py` добавляет новую версию состояния вперёд и не
удаляет ходы, поэтому ход, который игрок отыграл заново, остаётся в `turns` и
попадает в реестр Decision 016 через `turns_for_memory`.

## Decision

Awareness перестаёт быть предметом знания Gateway. WorldPack `awareness`
мигрирует на WorldPack-owned training runtime по Decision 017, по образцу
`awareness-one-day`: длина курса, расписание окон, границы полудневных срезов,
привязка финального разбора и авторские тексты отказа объявляются в
`program.json` — тексты живут в `turns[].surface.fallback` и `debrief.fallback`,
рядом с поверхностью, которую они замещают; детекторы и веса — в
`assessment.json`. `fallbacks.json` остаётся метаданными контракта
(`rp-training-fallbacks.v1`: версия и пояснение) и исполняемых текстов не
содержит — иначе один и тот же текст имел бы два источника истины. После миграции
legacy-путь Awareness из Gateway удаляется, а не чинится guard'ом: уходят
константы расписаний, регексы предметной области, course-specific фолбэки,
функции начисления баллов и предикаты `awareness_final_summary` и
`awareness_turns_remaining`.

Ни один предикат Gateway не выводит поведение из номера хода. Номер хода — это
данные состояния, а не правило; решение о том, что ход десятый и последний,
принимает контракт рантайма курса. Это обобщение инварианта 1 Decision 017 на
предикаты, которые не упоминают кампанию и поэтому проходили мимо ревью.
Граница авторитета не меняется: Gateway остаётся исполнителем состояния,
скоринга и провайдерной политики, предметное правило есть изменение WorldPack, а
новый универсальный примитив — версионированное изменение схемы.

RP теряет нарративную валидацию — но только после того, как предыдущие два
абзаца выполнены и доказаны. В RP после успешного ответа провайдера не
выполняется семантическая проверка, repair и подстановка шаблона; остаётся
транспортная граница — непустой ответ, разбор формата, ошибка провайдера
возвращается как ошибка, а не как успешный игровой ход. До удаления полей
`validator_valid`, `repaired` и `fallback` RP-ход начинает записывать
транспортный статус, пригодный для агрегации, иначе доказать эффект решения
будет нечем. Сами поля объявляются deprecated, но живут ещё один релиз: они —
единственная существующая ручка, по которой на проде отличается фолбэк от
успешного хода, и без них окно наблюдения после снятия валидации диагностируется
вслепую. Их удаление — отдельное изменение после того, как транспортный статус
подтверждён живыми числами.

Ходы, перекрытые откатом, помечаются исключёнными из памяти и не попадают в
реестр Decision 016. Это не проверка и не гейт: ничего не блокируется, игрок
ничего не замечает — реестр перестаёт помнить то, что человек уже отменил.

## Invariants

1. **Нейтральность по предметной области.** В `rp-gateway/app/` нет символов
   `AWARENESS_*` и функций `awareness_*`; нет расписания, регекса, веса баллов,
   ключа ответов и фолбэка курса Awareness.
2. **Никаких правил из номера хода.** Ни один предикат Gateway не принимает
   решение о поведении, сравнивая `meta.turn` с числовым литералом.
3. **Порядок.** Нарративная валидация в RP снимается только после того, как
   инварианты 1 и 2 выполнены и доказаны тестом.
4. **Измеримость.** Каждый RP-ход несёт транспортный статус, по которому
   строится агрегат, и он записывается до удаления старых полей.
5. **Память не помнит отменённое.** Ход, перекрытый откатом, не попадает в
   `rp_story_memory_snapshots`.
6. **Training не деградирует.** Курс `awareness` через WorldPack-owned runtime
   сохраняет то же расписание, ту же оценку и те же авторские тексты отказа, что
   на legacy-пути.
7. **Инварианты Decision 017 сохраняются** — неизменность снапшота контракта,
   минимизация промпта, бюджет вызовов, честные метаданные Training.

## Validation and evidence

Инвариант 1 проверяется поиском по `rp-gateway/app/`: совпадений нет.
Инвариант 2 — обзором предикатов и негативным тестом, который падает при
внесении сравнения номера хода с литералом. Инварианты 3 и 6 — тестом полного
прохождения курса `awareness` через WorldPack-owned runtime и тестом RP-партии
длиной более двенадцати ходов, где проверяется `metadata_json`, а не видимый
текст. Инвариант 4 — агрегирующим запросом к `turns` до и после изменения.
Инвариант 5 — тестом, где ход, перекрытый откатом, отсутствует в следующем
снапшоте реестра. Инвариант 7 — существующими
`test_training_runtime.py` и `test_training_artifacts.py`.

Preflight: `scripts/validate-repository.py` и, поскольку меняется runtime JSON,
`scripts/validate-training-runtime.py`. Полный локальный гейт —
`scripts/ci.ps1`.

Продакшн-доказательство: доля RP-ходов с `metadata_json.fallback` до и после,
и отсутствие `llm_safe_fallback` в `audit_events` для RP-партий после
изменения. На момент написания решения это не измерено — read-only проба
контейнера в сессии аудита была недоступна, поэтому размер ущерба в решении не
заявлен намеренно.

## Consequences

Смена длины или расписания курса становится правкой WorldPack, а не релизом
приложения. Класс ошибки «training-правило сработало в RP» исчезает вместе с
кодом, а не прикрывается условием. Gateway уменьшается на 140 вхождений
кампанийной логики в пяти файлах, и разделение по Decision 018 становится
дешевле: делить будет заметно меньше кода.

Становится труднее следующее. Активные партии `awareness` на legacy-пути
закрываются в момент миграции: снапшот контракта по Decision 017 фиксируется при
первом использовании, у старых партий его нет, а принудительный перенос менял бы
шкалу оценки уже отыгранных ходов молча. Цена принята сознательно — незакрытые
сессии придётся начать заново, зато в рантайме не остаётся двух ветвей курса, и
продакшн-разбивка по `training_runtime_contract_hash` не смешивает поколения.
RP-нарратор сможет вернуть
слабый, противоречивый или содержащий служебный текст ответ — этот риск принят в
пользу непрерывности игры и отсутствия ложных шаблонных успехов, и починка
такого хода перекладывается на игрока через ГМ-мод. Диагностика RP теряет
семантические сигналы и опирается на транспортный статус.

Decision 018 остаётся в силе по своим причинам — своя fallback-политика, свои
тесты, UX-контракт RP, — но перестаёт опираться на инцидент десятого хода как на
обоснование: разделение процессов его бы не предотвратило, поскольку
`validator.py` и `rule_engine.py` переехали бы в RP-runtime вместе с предикатом,
в котором `scenario_type` не упоминается. Порядок работ поэтому обратный тому,
что подразумевало 018: сначала миграция и удаление, потом разделение.

## Related decisions

- [Decision 010: Party Scenario Types](010-party-scenario-types.md)
- [Decision 016: RP-only living story memory](016-rp-living-story-memory.md)
- [Decision 017: WorldPack-owned Training Runtime](017-worldpack-owned-training-runtime.md)
- [Decision 018: Разделить Training и RP Gateway](018-separate-training-and-rp-gateways.md)

---

# Part B — Execution brief for Codex

<!--
Non-normative. Written for the implementer, dated, and superseded once the
change is merged and verified. Do not treat anything below as design history.
-->

**Written:** 2026-08-09 · **Runner:** Codex `gpt-5.6-sol`,
`model_reasoning_effort = "medium"` · **Branch:** `codex/retire-legacy-awareness`
(изолированный worktree под `codex-worktrees/`) · **Base:** `main` at `8bb5979`

## B.1 Blast radius

Claude's classification: **common to every training scenario**.

Версионированного изменения схемы **нет**: `rp-training-runtime.v2` и
`rp-training-program.v2` уже существуют, `awareness` переезжает на них как
данные. Единственное изменение хранилища — добавление колонки в `turns`, это
миграция таблицы, а не смена версии контракта.

Проверено 2026-08-09 по исходникам, а не по описанию: расписание, progression,
детекторы и веса выражаются существующими v2; авторские fallback-тексты
контрактом `rp-training-fallbacks.v1` **не** выражаются — он несёт только
`schema_version` и `note`, грузится как `required=False` и проверяется лишь по
версии (`training_runtime.py:743`, `:766`), а исполняемые тексты валидатор
требует и рантайм читает из `program.turns[].surface.fallback` и
`program.debrief.fallback` (`:516-528`, `:829-838`). Поэтому первая редакция
этого ADR, требовавшая класть тексты в `fallbacks.json`, потребовала бы новой
версии контракта. Решение изменено, а не классификация: тексты остаются в
`program.json`, `fallbacks.json` остаётся метаданными. Schema bump не нужен.

Если по ходу работы окажется, что расписание `awareness` не выражается в v2 без
нового примитива детектора или новой поверхности — это **становится**
версионированным изменением схемы, и тогда ripple set: runtime/program schema,
`scripts/validate-repository.py`, `scripts/validate-training-runtime.py`,
генератор WorldPack, источники `codex-skills/` и сгенерированные копии в
`~/.codex/skills/`, а дорожки B.3 становятся последовательными.
**Report the classification back.** Если не согласен — остановись и скажи до
того, как писать код.

## B.2 Ground rules

- Do not hot-edit `/srv/apps/rp-stack`. The repository is the authority.
- Do not start local RP Stack application servers. Windows checks are static
  evidence only.
- Never read, print or commit `/etc/ansible/local-overrides.yml`, `.env`
  values, keys, cookies or tokens. The repository is public.
- Keep unrelated working-tree changes intact.
- Update `docs/wiki/` and this ADR in the same change if behaviour changes.

Дополнительно к этой задаче, сверено 2026-08-09: та же правка существует в двух
местах. В основном дереве `ubuntu_ansible_palybooks` лежат незакоммиченные
изменения `adjudicator.py` и `tests/test_gateway.py`; в worktree
`codex-worktrees/rp-turn10-fix` то же самое уже закоммичено как `127b698`
«fix: keep RP narration after turn ten» поверх `main` (дерево чистое). Это
временный guard `scenario_type == "training"` и базовый тест
`test_rp_party_after_turn_10_keeps_narrator_response`. Не делать эту работу
заново: взять существующее, усилить тест проверкой `metadata_json` вместо
видимого текста, а сам guard удалить в L2 — после удаления legacy-пути он
защищает от кода, которого больше нет. Решить с пользователем, вливается
`127b698` в ветку ADR или переписывается в ней; двух копий не оставлять.

## B.3 Parallel plan

| Lane | Agent task | Owns (writes) | Reads only | Depends on |
|------|-----------|---------------|------------|------------|
| L1 | Мигрировать `awareness` на WorldPack-owned runtime | `worldpacks/awareness/**` | `worldpacks/awareness-one-day/training/*.json`, `scripts/validate-training-runtime.py` | Wave 0 |
| L2 | Удалить legacy Awareness из Gateway | `rp-gateway/app/services/validator.py`, `rp-gateway/app/services/rule_engine.py`, `rp-gateway/app/services/adjudicator.py`, `rp-gateway/app/main.py`, `rp-gateway/app/services/party_store.py` | `worldpacks/**` | Wave 0 |
| L3 | Исключение перекрытых откатом ходов из памяти | `rp-gateway/app/services/state_store.py`, `rp-gateway/app/services/rp_story_memory.py` | — | Wave 0 |
| L4 | Тесты под контракты L1-L3 | `rp-gateway/tests/**` | — | контракты в §B.4 |

**Wave 0 — contract freeze (sequential, no agents).** Зафиксировать в ветке:
точную форму `program.json` и `assessment.json` для `awareness`, выведенную из
`awareness-one-day` и из `scripts/validate-training-runtime.py`, включая
`turns[].surface.fallback` и `debrief.fallback`; `fallbacks.json` копируется как
метаданные `rp-training-fallbacks.v1` и исполняемых текстов не получает; DDL и
сигнатуры из §B.4; перечень удаляемых символов из §B.5 шаг 3. Ничего ниже не
стартует, пока это не записано.

**Wave 1 — lanes in parallel.** Каждому агенту: его строка таблицы, замороженные
контракты, инварианты из Part A, запрет трогать что-либо вне колонки `Owns`.
Каждый агент отчитывается: изменённые файлы, focused-команда теста, какой
контракт пришлось согнуть.

**Wave 2 — integration (sequential, single agent).** Слить дорожки; прогнать
`scripts/validate-training-runtime.py` и `scripts/validate-repository.py`;
обновить `docs/wiki/03-turn-lifecycle.md` и `docs/wiki/04-worldpacks-and-modes.md`;
обновить Status этого ADR; при изменениях в `codex-skills/` прогнать
`scripts/sync-codex-skills.ps1 -Mode Check`. Закрыть активные legacy-партии
`awareness` по §B.6 — до деплоя, чтобы ни одна партия не пережила удаление
legacy-пути.

**Wave 3 — снятие RP-валидатора (sequential, single agent, только после
зелёной Wave 2).** Инвариант 3 запрещает делать это параллельно с L2: пока
legacy-путь не удалён и не доказан тестом, снятие валидации скрывает следующую
протечку. Шаги 7 и 8 из §B.5.

Do not parallelise: anything that renumbers, rehashes or bumps a version;
anything editing a file two lanes read; the deploy; Wave 3.

## B.4 Frozen contracts

Хранилище (L3), SQLite:

```sql
ALTER TABLE turns ADD COLUMN excluded_from_memory INTEGER NOT NULL DEFAULT 0;
```

`turns_for_memory` (`state_store.py`, сейчас строки 1285-1306) добавляет в
`WHERE`:

```sql
AND excluded_from_memory = 0
```

`rollback` (`state_store.py`, сейчас строки 1999+) после вставки восстановленной
версии помечает перекрытые ходы:

```sql
UPDATE turns SET excluded_from_memory = 1
WHERE campaign_id = ? AND state_version > ?
```

где второй параметр — `target_version`. `rollback` остаётся append-only: ходы
не удаляются.

Транспортный статус RP (Wave 3), ключ в `turns.metadata_json`:

```json
{"transport_status": "ok" | "provider_error" | "provider_timeout" | "invalid_response"}
```

Пишется для всех `scenario_type`, включая Training, чтобы агрегат был
сопоставим. Поля `validator_valid`, `repaired`, `fallback`, `fallback_reason`
для Training сохраняются без изменений. Для RP они после Wave 3 остаются в
`metadata_json` ещё один релиз как deprecated — `fallback` фиксирует `false`,
`validator_valid` и `repaired` перестают меняться, — и служат опорой для
сравнения «до/после» из §C.4. Их удаление в объём этого изменения не входит.

Формы `program.json` / `assessment.json` для `awareness` намеренно не выписаны
здесь: их канонический источник — `worldpacks/awareness-one-day/training/*.json`
и `scripts/validate-training-runtime.py`. Wave 0 фиксирует выведенную из них
форму в ветке до старта L1. `fallbacks.json` формы не имеет сверх
`{"schema_version": "rp-training-fallbacks.v1", "note": ...}` — рантайм грузит
его как необязательный и проверяет только версию
(`rp-gateway/app/services/training_runtime.py:743`, `:766`), поэтому любой
исполняемый текст, положенный туда, будет молча проигнорирован.

## B.5 Steps

1. **Wave 0.** Вывести и записать в ветку форму runtime-файлов для `awareness`
   из `awareness-one-day` и preflight-валидатора. Проверяемый исход: в ветке
   лежит файл с формой, и `validate-training-runtime.py` принимает пустой
   каркас.
2. **L1.** Перенести расписание курса из прозы `manifest.json.assumptions` в
   `program.json`: 10 ходов, нечётные 10:00-14:00, чётные 15:00-18:00,
   финальный разбор после ответа на ход 10. Туда же — авторские тексты отказа,
   в `turns[].surface.fallback` и `debrief.fallback`, перенесённые из
   course-specific фолбэков Gateway дословно. Детекторы и веса — в
   `assessment.json`; `fallbacks.json` создаётся как метаданные версии.
   Добавить блок `training_runtime` в `manifest.json`. Проверяемый исход:
   `validate-training-runtime.py` проходит на `awareness`, и ни один
   fallback-текст не остался только в удаляемом коде Gateway.
3. **L2.** Удалить из `rp-gateway/app/`: `AWARENESS_TURN_WINDOWS`,
   `AWARENESS_DEBRIEF_WINDOW`, `AWARENESS_ONE_DAY_ID`,
   `AWARENESS_ONE_DAY_TURN_WINDOWS`, `AWARENESS_ONE_DAY_SECURITY_TURNS`,
   `AWARENESS_ONE_DAY_SITE_TURNS`, `AWARENESS_HINT_RE`,
   `AWARENESS_EARLY_DEBRIEF_RE`, `AWARENESS_SCORE_RE`, `AWARENESS_META_RE`,
   `AWARENESS_INTERNAL_PROCESS_RE`, `AWARENESS_MENTAL_STATE_RE`,
   `AWARENESS_PLAYER_ACTION_PATTERNS`, все функции `awareness_*` в
   `validator.py` и `rule_engine.py`, их вызовы в `adjudicator.py`, `main.py` и
   `party_store.py`, включая временный guard из рабочего дерева. Проверяемый
   исход: `grep -ri awareness rp-gateway/app/` пуст, импорты не сломаны.
4. **L3.** Реализовать контракт хранилища из §B.4. Проверяемый исход: ход,
   перекрытый откатом, не возвращается из `turns_for_memory`.
5. **L4.** Тесты: полный курс `awareness` через WorldPack-owned runtime с тем же
   расписанием и той же оценкой; RP-партия длиной 12+ ходов с проверкой
   `metadata_json`; негативный тест на инвариант 2; тест исключения из памяти.
   Усилить существующий `test_rp_party_after_turn_10_keeps_narrator_response`
   проверкой метаданных.
6. **Wave 2.** Закрыть активные legacy-партии `awareness` — те, у которых нет
   записи в `training_runtime_snapshots`. Сначала read-only запрос: сколько их,
   на каких ходах, когда был последний ход; числа показать пользователю до
   закрытия. Закрытие идёт штатным переводом партии в завершённое состояние
   через Gateway, не правкой строк в SQLite вручную. Проверяемый исход: после
   деплоя ни одна партия `awareness` без снапшота контракта не остаётся
   активной, и приложены числа до/после.
7. **Wave 3.** Записывать `transport_status` для всех режимов. Проверяемый
   исход: агрегирующий запрос к `turns` возвращает распределение по новому полю.
8. **Wave 3.** Убрать из RP-пути `OutputValidator`, repair-вызов и
   `safe_fallback`; оставить непустой ответ, разбор формата и явную ошибку
   провайдера. `validator_valid`, `repaired`, `fallback` при этом **не**
   удалять — по §B.4 они живут ещё релиз. Проверяемый исход: RP-ход делает не
   более одного narrator completion, ошибка провайдера возвращается ошибкой, а
   не ходом, и поле `fallback` у новых RP-ходов равно `false`.

## B.6 Boundaries

- Branch: `codex/retire-legacy-awareness`. Do not push to `main`.
- Do not touch: `rp-light-gui/`, `rp-showcase-gui/`, `ui-shared/`,
  `worldpacks/awareness-one-day/`, `plugins/rp-stack-devkit/`.
- **Decided by the user 2026-08-09, не пересматривать:** активные legacy-партии
  `awareness` закрываются (шаг 6). Принудительная миграция отклонена — она
  меняла бы шкалу уже отыгранных ходов; доигрывание на замороженной версии
  отклонено — оно оставляет две ветви курса в рантайме. Если запрос покажет
  неожиданно много активных партий, сообщить числа и остановиться до закрытия.
- **Decided by the user 2026-08-09:** `validator_valid`, `repaired`, `fallback`
  остаются в RP-метаданных ещё один релиз как deprecated. Не удалять в этом
  изменении.
- **Decided by the user 2026-08-09:** авторские fallback-тексты `awareness`
  живут в `program.json`; `fallbacks.json` остаётся метаданными. Schema bump не
  делать. Если реализация упрётся в невыразимость — остановиться и доложить,
  см. §B.1.
- If a step turns out to contradict an accepted ADR, stop and report — do not
  reverse a decision silently. В частности: шаг 7 отменяет часть поведения,
  описанного в Decision 018, и уточняет его порядок; 018 обновляется в том же
  изменении.

---

# Part C — Verification Codex must perform

A step is not done because it was written. Report which delivery state each
claim rests on: *local edit · tested · committed · pushed · Ansible-applied ·
container-tested · HTTP-verified · browser-verified.*

## C.1 Static and focused

1. Focused-тесты по дорожкам, названные явно, с использованной командой:
   `test_training_runtime.py` и новый тест курса `awareness` (L1+L4),
   `test_gateway.py` для длинной RP-партии и негативного теста инварианта 2
   (L2+L4), тест исключения из памяти (L3+L4).
2. `powershell.exe -File scripts/ci.ps1` — полный локальный гейт до push.
3. `python scripts/validate-repository.py` и, поскольку runtime JSON изменился,
   `scripts/validate-training-runtime.py`.

Использовать связанные runtime, не `python` из PATH:
`%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`

Дополнительно к тестам — статическая проверка инварианта 1:
`grep -ri awareness rp-gateway/app/` должен быть пуст. Приложить вывод.

## C.2 Prove the guards actually fire

Для негативного теста на инвариант 2: внести в `rp-gateway/app/` предикат,
сравнивающий `meta.turn` с литералом, показать падение с ожидаемым сообщением,
откатить инъекцию, показать зелёный. Приложить оба вывода. Guard, который ни
разу не падал, доказательством не является.

Для теста исключения из памяти: показать, что без изменения `turns_for_memory`
перекрытый ход попадает в батч, а после — нет.

Отдельно указать для каждого позитивного теста, что он реально утверждает. Тест
курса `awareness` не должен сводиться к сравнению authored fallback с самим
собой: он обязан проверять расписание и оценку на ответах, отличных от
фолбэка. Тест длинной RP-партии обязан проверять `metadata_json`, а не видимый
текст.

## C.3 Deploy

`commit -> push -> ansible-local-apply -> runtime verification`.

`sudo` требует интерактивного пароля, поэтому Codex останавливается на
**pushed** и просит пользователя выполнить:

```bash
sudo systemctl start ansible-local-apply.service
```

Затем продолжить: подтвердить, что сервис завершился, контейнер Gateway
пересобран, и партия обоих типов отвечает по HTTP.

## C.4 Production evidence

Read-only, против SQLite внутри контейнера Gateway
(`file:/data/rp_gateway.db?mode=ro`). Пробу подавать на stdin — вложенные
кавычки не переживают PowerShell → SSH → Python.

Сообщить фактические числа, не «работает»:

- `turns.metadata_json`: `fallback`, `fallback_reason`, `validator_valid`,
  `repaired`, `narrative_model`, `training_runtime_contract_hash`, новый
  `transport_status`
- `audit_events.event_type`: `turn_complete`, `llm_validation_failed`,
  `llm_safe_fallback`, `llm_timeout`

Конкретно для этого изменения: доля RP-ходов с `fallback` до и после, с
разбивкой по `fallback_reason`; отсутствие `llm_safe_fallback` для RP-партий
после изменения; доля Training-ходов с `fallback`, сгруппированная по
`training_runtime_contract_hash`, чтобы миграция `awareness` не оказалась
усреднена с legacy-прогонами.

Отдельно по шагу 6: число активных партий `awareness` без записи в
`training_runtime_snapshots` до закрытия — с номером текущего хода и датой
последнего хода у каждой — и то же число после. Оно должно быть нулём. Партии
`awareness` со снапшотом нового контракта в этот счёт не входят.

Текст фолбэка читается как успешный ход. Судить по `metadata_json`, никогда по
видимому сообщению. Не печатать строки, содержащие секреты, и не печатать
нарративный текст.

## C.5 Report back

1. Blast-radius classification (agreed or disputed).
2. Per-lane: files changed, tests run, contracts bent.
3. Guard-fires evidence from C.2.
4. Delivery state reached, named exactly.
5. Production numbers from C.4, before and after, включая счёт закрытых
   legacy-партий.
6. What was not done, and why. Три вопроса из §B.6 закрыты пользователем —
   если реализация вынудила отступить от любого из них, это остановка и доклад,
   а не запись в отчёт постфактум.
