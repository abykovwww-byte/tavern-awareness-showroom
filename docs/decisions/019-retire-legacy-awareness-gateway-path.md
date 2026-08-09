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
пользователем в тот же день и внесены в решение. Вторая редакция, в тот же день:
попытка исполнения остановилась на том, что курс `awareness` не выражается
контрактом `rp-training-program.v2`; решением пользователя вводится v3 с
`surfaces[]`, и классификация изменения поднята до версионированного изменения
схемы. Implementation is delegated to Codex; commit, deployment and live
verification are separate delivery states.

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

Курс `awareness` не выражается контрактом `rp-training-program.v2`, и это
обнаружилось при переносе. В v2 ход несёт ровно одну поверхность — письмо или
сообщение, — а блок второго типа считается грубым нарушением. В `awareness` за
один рабочий блок игроку приходят оба канала сразу, и первый ход по legacy-правилу
обязан содержать не менее двух писем и не менее одного сообщения. Это не
особенность фолбэк-текста: правило действует и для обычного ответа модели,
поэтому «переписать тексты» сохранило бы контракт ценой самого курса.

Многоканальный ход — общий примитив, а не предметное правило Awareness: любой
курс может ставить игрока перед несколькими каналами в одном временном окне.
По границе Decision 017 такой примитив есть версионированное изменение схемы, а
не ветка в Gateway. Поэтому вводится `rp-training-program.v3`, где ход несёт
список `surfaces[]`, а авторский текст отказа поднимается на уровень хода, к
которому относится. Версии v1 и v2 остаются поддержаны без правок в паках:
`awareness-one-day` продолжает работать на v2, и хеш его контракта не меняется.
Одна живая версия схемы дешевле двух, но дешевле неё только версия, в которой
курс не искажён.

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
8. **Старые версии схемы не ломаются.** Паки на `rp-training-program.v1` и `.v2`
   принимаются без правок, а `contract_hash` пака `awareness-one-day` после
   введения v3 остаётся байт-в-байт прежним.

## Validation and evidence

Инвариант 1 проверяется поиском по `rp-gateway/app/`: совпадений нет.
Инвариант 2 — обзором предикатов и негативным тестом, который падает при
внесении сравнения номера хода с литералом. Инварианты 3 и 6 — тестом полного
прохождения курса `awareness` через WorldPack-owned runtime и тестом RP-партии
длиной более двенадцати ходов, где проверяется `metadata_json`, а не видимый
текст. Инвариант 4 — агрегирующим запросом к `turns` до и после изменения.
Инвариант 5 — тестом, где ход, перекрытый откатом, отсутствует в следующем
снапшоте реестра. Инвариант 7 — существующими
`test_training_runtime.py` и `test_training_artifacts.py`. Инвариант 8 — прогоном
существующих тестов `awareness-one-day` без правок в самом паке и сравнением его
`contract_hash` до и после введения v3.

Preflight: `scripts/validate-repository.py` и, поскольку меняется runtime JSON,
`scripts/validate-training-runtime.py`. Полный локальный гейт —
`scripts/ci.ps1`.

Продакшн-доказательство: доля RP-ходов с `metadata_json.fallback` до и после,
и отсутствие `llm_safe_fallback` в `audit_events` для RP-партий после
изменения. На момент написания решения это не измерено — read-only проба
контейнера в сессии аудита была недоступна, поэтому размер ущерба в решении не
заявлен намеренно.

## Consequences

Появляется вторая живая версия program-схемы. Пока существуют паки на v2,
`training_runtime.py` и `scripts/validate-training-runtime.py` держат обе ветки
разбора, и каждое следующее изменение контракта надо думать дважды. Плата
принята: альтернатива — переписать `awareness` под один канал за ход, то есть
изменить курс ради формата. Взамен многоканальный ход перестаёт быть чужой
конструкцией и становится доступен любому будущему паку.

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

**Written:** 2026-08-09, ревизия 2 после остановки первой попытки исполнения ·
**Runner:** Codex `gpt-5.6-sol`,
`model_reasoning_effort = "medium"` · **Branch:** `codex/retire-legacy-awareness`
(изолированный worktree под `codex-worktrees/`) · **Base:** `main` at `8bb5979`

## B.1 Blast radius

Classification: **versioned schema change.** Первая редакция ADR ставила
«common to every training scenario»; условие «если курс не выражается в v2 — это
становится версионированным изменением» сработало, и классификация повышена
пользователем 2026-08-09.

Что проверено по исходникам, а не по описанию:

- Авторские fallback-тексты контрактом `rp-training-fallbacks.v1` не
  выражаются: он несёт только `schema_version` и `note`, грузится как
  `required=False`, проверяется лишь по версии
  (`training_runtime.py:743`, `:766`). Тексты живут в `program`
  (`:516-528`, `:829-838`). Решение: тексты остаются в `program.json`.
- v2 допускает ровно одну поверхность на ход, блок второго типа — hard
  violation (`training_runtime.py:400-408`). Legacy `awareness` требует в первом
  ходе ≥2 писем и ≥1 сообщения (`validator.py:237-241`), а его фолбэки смешивают
  оба типа. Мигрированный `awareness-one-day` — один канал на ход во всех
  десяти ходах, то есть v2 писалась под другую модель курса.

**Измеренный ripple set** (grep по репозиторию, не предположение). Ключ
`surface` читает единственный модуль приложения:

- `rp-gateway/app/services/training_runtime.py` — разбор, промпт-контракт,
  валидация нарратива, `fallback_text`, карта `RUNTIME_PROGRAM_SCHEMAS`;
- `roles/apps/files/rp-stack/scripts/validate-training-runtime.py` — та же карта
  версий и дублирующая проверка формы;
- `rp-gateway/tests/test_training_runtime.py`;
- `codex-skills/training-world-pack-builder/SKILL.md` и
  `references/training-contract.md` плюс сгенерированные копии в
  `~/.codex/skills/` (`scripts/sync-codex-skills.ps1 -Mode Check`);
- `docs/wiki/04-worldpacks-and-modes.md`.

**В ripple не входят** — проверено отдельно, чтобы не раздувать объём:
`rp-light-gui/`, `rp-showcase-gui/`, `ui-shared/` не читают `surface` и
турн-контракт вовсе; `scripts/validate-repository.py` про training-схемы ничего
не знает; отдельного «генератора WorldPack» в репозитории нет — есть только
skill-документация. Если найдёшь консьюмера вне этого списка — остановись и
доложи, список неверен.

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
| S1 | Ввести `rp-training-program.v3` с `surfaces[]`, сохранив v1/v2 | `rp-gateway/app/services/training_runtime.py`, `scripts/validate-training-runtime.py`, `codex-skills/training-world-pack-builder/**` | `worldpacks/**` | Wave 0 |
| L1 | Мигрировать `awareness` на v3 | `worldpacks/awareness/**` | `worldpacks/awareness-one-day/training/*.json`, `scripts/validate-training-runtime.py` | S1 |
| L2 | Удалить legacy Awareness из Gateway | `rp-gateway/app/services/validator.py`, `rp-gateway/app/services/rule_engine.py`, `rp-gateway/app/services/adjudicator.py`, `rp-gateway/app/main.py`, `rp-gateway/app/services/party_store.py` | `worldpacks/**` | S1 |
| L3 | Исключение перекрытых откатом ходов из памяти | `rp-gateway/app/services/state_store.py`, `rp-gateway/app/services/rp_story_memory.py` | — | — |
| L4 | Тесты под контракты S1 и L1-L3 | `rp-gateway/tests/**` | — | контракты в §B.4 |

S1 владеет `training_runtime.py` целиком, поэтому не может идти рядом с L1 и L2:
L1 пишет паки под контракт, которого до S1 нет, а L2 читает `worldpacks/**`,
чтобы убедиться, что удаляемое поведение уже выражено данными. L3 не касается
training-контракта вообще и от S1 не зависит.

**Wave 0 — contract freeze (sequential, no agents).** Зафиксировать в ветке
форму `rp-training-program.v3` из §B.4; форму `program.json` и
`assessment.json` для `awareness`, выведенную из `awareness-one-day` и из
`scripts/validate-training-runtime.py`; `fallbacks.json` копируется как
метаданные `rp-training-fallbacks.v1` и исполняемых текстов не получает; DDL и
сигнатуры из §B.4; перечень удаляемых символов из §B.5. Ничего ниже не
стартует, пока это не записано.

Отдельно и первым: закоммитить уже существующие локальные правки L3 в
`state_store.py` собственным коммитом, до старта S1 — они не зависят ни от
схемы, ни от миграции, и не должны смешаться с ней в общем diff.

**Wave 1 — S1, схема v3 (sequential, single agent).** Один агент, потому что
владеет файлом, который читают обе следующие дорожки. Паки не трогает вовсе.
Выход: `awareness-one-day` проходит без единой правки в паке, его
`contract_hash` не изменился, и на пустом каркасе v3 валидатор принимает
многоканальный ход.

**Wave 2 — L1, L2, L4 in parallel.** Каждому агенту: его строка таблицы,
замороженные контракты, инварианты из Part A, запрет трогать что-либо вне
колонки `Owns`. Каждый агент отчитывается: изменённые файлы, focused-команда
теста, какой контракт пришлось согнуть.

**Wave 3 — integration (sequential, single agent).** Слить дорожки; прогнать
`scripts/validate-training-runtime.py` и `scripts/validate-repository.py`;
обновить `docs/wiki/03-turn-lifecycle.md` и `docs/wiki/04-worldpacks-and-modes.md`;
обновить Status этого ADR; прогнать `scripts/sync-codex-skills.ps1 -Mode Check`,
поскольку S1 меняет `codex-skills/`. Закрыть активные legacy-партии `awareness`
по §B.6 — до деплоя, чтобы ни одна партия не пережила удаление legacy-пути.

**Wave 4 — снятие RP-валидатора (sequential, single agent, только после
зелёной Wave 3).** Инвариант 3 запрещает делать это параллельно с L2: пока
legacy-путь не удалён и не доказан тестом, снятие валидации скрывает следующую
протечку.

Do not parallelise: S1; anything that renumbers, rehashes or bumps a version;
anything editing a file two lanes read; the deploy; Wave 4.

## B.4 Frozen contracts

### `rp-training-program.v3` (S1)

Ход несёт список поверхностей вместо одной, а авторский текст отказа
поднимается на уровень хода — он один на весь ход и покрывает все его блоки:

```json
{
  "turn": 1,
  "window": "...", "header": "...", "instruction": "...",
  "question": "...", "require_question": true,
  "variation_budget": ["..."],
  "fallback": "<авторский текст со всеми блоками этого хода>",
  "surfaces": [
    {"type": "email", "count": 2, "links": "none",
     "must_include": ["..."], "required_patterns": ["..."],
     "forbidden_patterns": ["..."], "profile_adaptation": false},
    {"type": "messenger", "count": 1, "links": "artifact"}
  ]
}
```

Правила, которые S1 обязан реализовать явно:

- `surfaces` — непустой список; типы внутри одного хода не повторяются;
  `count` у каждой поверхности — целое ≥ 1.
- Валидация нарратива: для каждой поверхности число блоков её маркера равно её
  `count`; маркер, не заявленный ни одной поверхностью хода, запрещён. Это
  обобщение текущего правила `other_marker` (`training_runtime.py:400-408`), а
  не его отмена.
- `require_question` переезжает с поверхности на ход: вопрос принадлежит ходу,
  а не каналу.
- `links` остаётся политикой поверхности. Автоматическая починка «Ссылки: нет»
  (`training_runtime.py:315-320`) применяется только когда все поверхности хода
  объявили одну и ту же политику; при смешанной политике нарушение остаётся
  hard и repair не предлагается — переписывать чужой блок вслепую хуже, чем
  вернуть ошибку.
- Карты `RUNTIME_PROGRAM_SCHEMAS` в `training_runtime.py` и в
  `scripts/validate-training-runtime.py` получают запись
  `rp-training-runtime.v3` → `rp-training-program.v3`. Обе карты правятся в
  одном коммите: разошедшиеся карты — это пак, который проходит preflight и
  падает в рантайме.
- `rp-training-assessment.v1` и `rp-training-fallbacks.v1` не меняются.
- Промпт-контракт хода всегда отдаёт `surfaces` списком, в том числе для паков
  v1/v2, где список одноэлементный; его версия поднимается до
  `rp-gateway.training-turn-contract.v2`. Консьюмеров вне `training_runtime.py`
  у него нет — проверено; если появится, это остановка и доклад.
- `contract_hash` считается по файлам пака, поэтому введение v3 не меняет хеш
  ни одного существующего пака. Это инвариант 8 и он проверяется, а не
  предполагается.

Обратная совместимость: ветка разбора v1/v2 остаётся живой, `surface` в
единственном числе продолжает приниматься, и ни один пак не редактируется ради
v3, кроме мигрируемого `awareness`.

### Хранилище (L3), SQLite

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

Транспортный статус RP (Wave 4), ключ в `turns.metadata_json`:

```json
{"transport_status": "ok" | "provider_error" | "provider_timeout" | "invalid_response"}
```

Пишется для всех `scenario_type`, включая Training, чтобы агрегат был
сопоставим. Поля `validator_valid`, `repaired`, `fallback`, `fallback_reason`
для Training сохраняются без изменений. Для RP они после Wave 4 остаются в
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

1. **Wave 0.** Закоммитить существующие правки L3 отдельным коммитом. Затем
   вывести и записать в ветку форму v3 из §B.4 и форму runtime-файлов для
   `awareness`. Проверяемый исход: `git status` чист по `state_store.py`, в
   ветке лежит файл с формой, и `validate-training-runtime.py` принимает пустой
   каркас v3.
2. **S1.** Ввести `rp-training-program.v3` по §B.4 в `training_runtime.py` и
   `scripts/validate-training-runtime.py` одним коммитом, обновить
   `codex-skills/training-world-pack-builder/`. Паки не трогать. Проверяемый
   исход: `test_training_runtime.py` зелёный без правок в
   `worldpacks/awareness-one-day/`, `contract_hash` этого пака совпадает с
   зафиксированным до S1, а каркас v3 с двумя поверхностями в одном ходе
   принимается обоими валидаторами.
3. **L1.** Перенести расписание курса из прозы `manifest.json.assumptions` в
   `program.json` на v3: 10 ходов, нечётные 10:00-14:00, чётные 15:00-18:00,
   финальный разбор после ответа на ход 10. Туда же — авторские тексты отказа,
   в `turns[].fallback` и `debrief.fallback`, перенесённые из course-specific
   фолбэков Gateway дословно, вместе с их многоканальной структурой; ход 1
   объявляет `surfaces` с двумя письмами и одним сообщением. Детекторы и веса —
   в `assessment.json`; `fallbacks.json` создаётся как метаданные версии.
   Добавить блок `training_runtime` в `manifest.json`. Проверяемый исход:
   `validate-training-runtime.py` проходит на `awareness`, ни один
   fallback-текст не остался только в удаляемом коде Gateway, и ни один текст
   не переписан ради формата — расхождения с legacy показать построчно.
4. **L2.** Удалить из `rp-gateway/app/`: `AWARENESS_TURN_WINDOWS`,
   `AWARENESS_DEBRIEF_WINDOW`, `AWARENESS_ONE_DAY_ID`,
   `AWARENESS_ONE_DAY_TURN_WINDOWS`, `AWARENESS_ONE_DAY_SECURITY_TURNS`,
   `AWARENESS_ONE_DAY_SITE_TURNS`, `AWARENESS_HINT_RE`,
   `AWARENESS_EARLY_DEBRIEF_RE`, `AWARENESS_SCORE_RE`, `AWARENESS_META_RE`,
   `AWARENESS_INTERNAL_PROCESS_RE`, `AWARENESS_MENTAL_STATE_RE`,
   `AWARENESS_PLAYER_ACTION_PATTERNS`, все функции `awareness_*` в
   `validator.py` и `rule_engine.py`, их вызовы в `adjudicator.py`, `main.py` и
   `party_store.py`, включая временный guard из рабочего дерева. Проверяемый
   исход: `grep -ri awareness rp-gateway/app/` пуст, импорты не сломаны.
5. **L3.** Реализовать контракт хранилища из §B.4 — часть уже сделана и
   закоммичена шагом 1, доделать остаток. Проверяемый исход: ход, перекрытый
   откатом, не возвращается из `turns_for_memory`.
6. **L4.** Тесты: полный курс `awareness` через WorldPack-owned runtime с тем же
   расписанием и той же оценкой; ход с двумя поверхностями принимается, а лишний
   блок незаявленного типа отвергается; паки v2 продолжают проходить; RP-партия
   длиной 12+ ходов с проверкой `metadata_json`; негативный тест на инвариант 2;
   тест исключения из памяти. Усилить существующий
   `test_rp_party_after_turn_10_keeps_narrator_response` проверкой метаданных.
7. **Wave 3.** Закрыть активные legacy-партии `awareness` — те, у которых нет
   записи в `training_runtime_snapshots`. Сначала read-only запрос: сколько их,
   на каких ходах, когда был последний ход; числа показать пользователю до
   закрытия. Закрытие идёт штатным переводом партии в завершённое состояние
   через Gateway, не правкой строк в SQLite вручную. Проверяемый исход: после
   деплоя ни одна партия `awareness` без снапшота контракта не остаётся
   активной, и приложены числа до/после.
8. **Wave 4.** Записывать `transport_status` для всех режимов. Проверяемый
   исход: агрегирующий запрос к `turns` возвращает распределение по новому полю.
9. **Wave 4.** Убрать из RP-пути `OutputValidator`, repair-вызов и
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
  `awareness` закрываются (шаг 7). Принудительная миграция отклонена — она
  меняла бы шкалу уже отыгранных ходов; доигрывание на замороженной версии
  отклонено — оно оставляет две ветви курса в рантайме. Если запрос покажет
  неожиданно много активных партий, сообщить числа и остановиться до закрытия.
- **Decided by the user 2026-08-09:** `validator_valid`, `repaired`, `fallback`
  остаются в RP-метаданных ещё один релиз как deprecated. Не удалять в этом
  изменении.
- **Decided by the user 2026-08-09:** авторские fallback-тексты `awareness`
  живут в `program.json`; `fallbacks.json` остаётся метаданными.
- **Decided by the user 2026-08-09, после остановки первой попытки:** многоканальный ход
  вводится как `rp-training-program.v3` с `surfaces[]`. Вариант «подогнать курс
  под один канал за ход в v2» отклонён — он менял бы сам курс, а не его запись.
  Инвариант «Training не деградирует» здесь главнее стоимости схемы. Тексты
  фолбэков не переписывать под формат: если какой-то из них всё же не ложится в
  v3, это остановка и доклад, а не молчаливая правка текста.
- If a step turns out to contradict an accepted ADR, stop and report — do not
  reverse a decision silently. В частности: шаг 9 отменяет часть поведения,
  описанного в Decision 018, и уточняет его порядок; 018 обновляется в том же
  изменении.

---

# Part C — Verification Codex must perform

A step is not done because it was written. Report which delivery state each
claim rests on: *local edit · tested · committed · pushed · Ansible-applied ·
container-tested · HTTP-verified · browser-verified.*

## C.1 Static and focused

0. Совместимость v2 (S1), до всего остального: `test_training_runtime.py` и
   `test_awareness_one_day.py` зелёные без единой правки в
   `worldpacks/awareness-one-day/`, и `contract_hash` этого пака совпадает
   до и после S1 — привести оба значения. Если хеш изменился, схема тронула
   данные, и это остановка.
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

Для правила `surfaces[]`: подать в валидатор ответ, где есть блок типа, не
заявленного ни одной поверхностью хода, и показать hard violation; затем ответ,
где число блоков заявленного типа не равно `count`, — тоже hard. Правило,
которое принимает всё, что угодно, схемы не стоит. Отдельно показать, что ход
`awareness` с двумя письмами и одним сообщением проходит.

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

1. Blast-radius classification (agreed or disputed) — включая подтверждение,
   что консьюмеров `surface` вне списка §B.1 не нашлось.
2. Per-lane: files changed, tests run, contracts bent.
3. Guard-fires evidence from C.2.
4. Delivery state reached, named exactly.
5. Production numbers from C.4, before and after, включая счёт закрытых
   legacy-партий.
6. What was not done, and why. Три вопроса из §B.6 закрыты пользователем —
   если реализация вынудила отступить от любого из них, это остановка и доклад,
   а не запись в отчёт постфактум.
