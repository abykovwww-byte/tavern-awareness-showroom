# Decision 032: History-first RP prompt and sectioned story memory

**Дата:** 2026-08-24

## Status

**Decision status: Accepted.** Пользователь принял S1 нового RP-ядра: бюджет
prompt переносится с неизменяемых JSON-проекций на дословную историю и пять
независимо покрываемых секций долговременной памяти. Их штатное обновление
выполняется одним общим model call, а не пятью отдельными пересказами.

**Delivery status:** `каркас` для строк
[`registry/032.yml`](registry/032.yml). Реализация S1 и узкая activation для
`merchant-sviatoslav` слиты и применены на сервере: новая stamp-party получила
effective revision `8`. Она не выполняла narrator или service model call.
Живые gates 25/60 ходов по решению пользователя отложены до полной реализации
RP-ядра, поэтому readiness не повышается. S2–S4 в это решение не входят.

## Context

На длинных RP-партиях revision `7` передавала narrator большие повторяющиеся
проекции canonical seed: state summary, relevant characters, absolute rules в
двух формах, scene contract, episodic memory и archive retrieval. При этом
дословная история после быстрого story-memory coverage сокращалась до нескольких
ходов. Неизменяемая структура занимала prompt, а свежая проза, которая реально
объясняет местоимения, действия и последствия, исчезала.

`scene_state` и private narrator bundle пытались компенсировать отсутствие
транскрипта, но создавали второе, менее свежее мнение о сцене. Revision `8`
возвращает основную ответственность дословной истории и оставляет structured
артефактом только relationship store. Canonical seed остаётся в базе и публичные
API не удаляются, но его общая сериализация больше не является narrator input.

## Scope

Решение применяется только к `scenario_type=rp` с
`rp_contract_revision >= 8`:

- opening scene и обычные narrative turns;
- narrator prompt, hard-overflow policy и prompt diagnostics;
- RP story-memory scheduling, provider routing и persistence;
- relationship pre-prompt scope.

Revisions `0..7` сохраняют прежнее поведение. Existing parties автоматически не
мигрируют. Training runtime, scoring, artifacts и debrief не меняются. Первая
реализация решения не активировала observed revision и не меняла WorldPack
declared revision; отдельная узкая активация зафиксирована ниже.

## Decision

### Playable history units

История считается не строками SQLite, а игровыми единицами:

- `opening_scene` — один assistant message; только точная техническая реплика
  `[AUTO_START] Старт партии` подавляется;
- `narrative` — целая пара `user + assistant`;
- `turn_kind = null` или пустое значение — legacy `narrative`;
- `world_command`, `gm_correction` и любые будущие non-game kinds исключаются.

RAW narrator window содержит якорные последние 50–57 eligible units и одновременно все
units новее безопасного покрытия story memory. Начало recent-window квантуется
по восемь units: `desired_start = max(0, N - 50)`,
`quantized_start = floor(desired_start / 8) * 8`, после чего фактическое начало
равно более ранней из quantized boundary и первой uncovered unit. Поэтому
обычное окно имеет 50–57 units, отставшая или частичная память расширяет его
назад и coverage gap не возникает.
Настройка `RP_RAW_HISTORY_WINDOW_TURNS` имеет default 50 и hard minimum 20;
увеличение окна не меняет eligibility или правило uncovered tail.

### Revision-8 narrator prompt

Порядок narrator-visible слоёв:

1. короткие русские правила narrator;
2. `WORLD_SYSTEM_PROMPT`, полный блок не более 5 000 символов;
3. `WORLD_ABSOLUTE_RULES`, один нумерованный prose-list без storage IDs/source,
   не более 3 000 символов;
4. RAW history с якорем по восемь units;
5. `RP_STORY_MEMORY`, если snapshot уже существует, не более 24 000 символов;
6. `PARTY_LORE_CARDS`, только целые cards, не более 4 000 символов;
7. только содержательный `AUTHORITATIVE_OUTCOME` — target, активное ограничение
   или обязательное последствие; generic no-check envelope отсутствует;
8. `RELATIONSHIP_PRESSURE` и due resolution;
9. `WORLD_AUTHORS_NOTE`, не более 1 500 символов и последний system block;
10. current player action последним message.

Revision `8` не читает и не рендерит `Relevant state summary`,
`RELEVANT_CHARACTERS`, `PROMPT_AUTHORITY_HIERARCHY`, scene state/boundary/
reanchor/transition allowance, legacy `LONG_TERM_PARTY_MEMORY`,
`UNCOMPACTED_ARCHIVE_FALLBACK` или `RETRIEVED_ARCHIVE_SCENES`. Narrator возвращает
plain text, а не `rp-gateway.rp-narrator-bundle.v1`.

При hard input overflow Gateway действует только так:

1. удаляет `PARTY_LORE_CARDS` целым block;
2. удаляет с головы только целые safely covered RAW units, но оставляет минимум
   20 units и никогда не открывает coverage gap;
3. если required set всё ещё не помещается, завершает запрос до provider.

Required blocks, отдельные messages и отдельные turns не обрезаются.

Блоки 1–3 и первые 50 units фактического RAW window образуют неизменяемую
якорную основу provider prefix. Story memory, lore cards, correction overlay,
relationship pressure, world events, author note и current action находятся
после неё. В блоках 1–3 запрещены turn/state/revision IDs, timestamps и любые
счётчики. Новые cache controls, headers или provider settings не добавляются:
DeepSeek/OpenAI используют совпадение prefix, существующий явный
`cache_control` остаётся только для Anthropic.

Для rev8 turn metadata рядом с `prompt_assembly` сохраняются
`cached_prompt_tokens`, `prompt_tokens` и `stable_prompt_prefix_hash`. Hash
считается по реально повторяемой основе — блокам 1–3 и первым 50 RAW units, а
не по растущему хвосту 51–57; поэтому он меняется при сдвиге якоря, но не при
добавлении unit внутри одного восьмиходового bucket.

### Relationship scope

Для revision `8` один deterministic scan равен current player input плюс три
предыдущих eligible RAW units целиком. По нему выбираются relationship
characters по whole aliases и optional `Outcome.target`. `scene_state`, seed
location и authored active threads больше не являются сигналами присутствия.
Canonical human-readable character name остаётся единственным label в prompt.

### Five story-memory sections, one normal call

До появления 51-й eligible unit story memory отсутствует и service job не
создаётся. После этого одна normal job выполняет ровно один oldest-first batch
не более восьми units. Один основной OpenRouter request возвращает все пять
именованных секций:

- situation + canon;
- active/resolved threads;
- characters;
- inventory/assets + rules/abilities;
- chronology + unresolved hooks.

У каждой секции собственные `coverage` и `fresh | stale | failed`. Safe coverage
равно минимуму пяти coverages. Partial snapshot сразу доступен narrator с явной
пометкой stale/failed; RAW автоматически растягивается к минимальному coverage.

Gateway валидирует ответ строго структурно и отдельно по каждой секции:

- секция присутствует в общем JSON и является объектом;
- поля и типы соответствуют section schema;
- каждый факт имеет корректные `fact_id`, `text`, `status`, `authority` и
  `source_turn_ids`, а уже известный факт с тем же текстом сохраняет `fact_id`;
- `finish_reason != length`.

Смысловую полноту Gateway не оценивает. Поля-массивы могут быть пустыми, а
`current_situation` — `null`; такой ответ валиден, продвигает coverage и не
повторяется ради непустоты. `finish_reason=length` означает отказ всего общего
ответа, даже если его JSON-фрагмент можно распарсить.

Если одна секция общего ответа не прошла эту проверку, Gateway выполняет один
дополнительный request только для неё; четыре валидные секции не повторяются.
Normal cadence — четыре newly observed eligible units. Durable retry того же
job повторяет только оставшиеся failed/stale sections, даже если за время
ожидания появились новые turns. Exact already-applied user correction
распознаётся идемпотентно.
Новая rev-8 запись использует authority `user`; revisions `0..7` сохраняют
`user_correction`, а sectioned reader нормализует это legacy-значение. Поле
absorption в S1 не вводится.

Основной общий request и каждый точечный request содержат только complete turns
и не более 20 000 символов serialized model messages. Общий request имеет
`max_tokens=4000`, точечный — `max_tokens=800`. Если восемь units не помещаются,
batch уменьшается с хвоста; одна слишком большая complete unit не режется.

### Persistence and routing

Существующая `rp_story_memory_snapshots` получает nullable `base_snapshot_id` и
`update_id`. Уникальность по `(campaign_id, to_turn_id)` снимается, чтобы partial
retry или будущая targeted correction могли сохранить новую revision при том же
coverage. Legacy caller без `update_id` сохраняет прежнюю дедупликацию; explicit
same-coverage write требует idempotent `update_id` и newest effective base.

Каждая heavy section role по умолчанию закреплена explicit-настройками
`RP_STORY_MEMORY_PROVIDER=openrouter` и
`RP_STORY_MEMORY_MODEL=deepseek/deepseek-v4-pro`. Явная замена модели остаётся
одной config-строкой, но route не наследует `LLM_PROVIDER` или
`SERVICE_MODEL_CHOICE`, не пробует локальную модель и не имеет NVIDIA fallback.
`service_call_log` хранит nullable `section_key` и `update_id`: основной вызов
пишет `section_key=all`, точечный — exact key секции. Общий
`SERVICE_JOB_MAX_ATTEMPTS=5` сохраняется для relationship extraction и legacy
jobs; только rev-8 story-memory job имеет максимум две durable attempts.

Для revision `8` episodic `memory` job не создаётся, а pending legacy job
завершается terminal no-op. Retention и полный `turns.prompt_json` не меняются.

## Supersession

Для revision `8+` это решение заменяет narrator/memory части Decisions
[016](016-rp-living-story-memory.md), [028](028-rp-uncovered-tail-and-overflow.md),
[029](029-scene-scoped-relationship-pressure.md),
[030](030-rp-prompt-authority-and-deduplication.md) и
[031](031-rp-scene-state-and-atomic-continuity.md). Их contracts и live evidence
остаются действующими для revision `7` и старше автоматически не переписываются.

## Consequences

- Prompt становится меньше по повторяющейся структуре и существенно больше по
  дословной истории.
- Memory lag увеличивает narrator input, но не создаёт слепой участок.
- Штатное обновление требует одного cloud call; стоимость и latency растут
  только на число структурно невалидных секций.
- Частичный cloud failure заметен как stale section и service trace, а не как
  молчаливый fallback.
- Scene bundle и canonical scene projection остаются только compatibility-кодом
  revision `7`; удалять их физически нельзя, пока существуют такие parties.
- Пустая валидная секция не провоцирует повтор и не подталкивает модель к
  выдумыванию фактов ради заполнения массива.

## Activation slice 2026-08-25

Source activation задаёт `RP_CONTRACT_OBSERVED_REVISION=8`, но declared revision
поднимается до `8` только у `merchant-sviatoslav`. Поэтому новая ordinary
RP-party получает `min(declared, observed)`: «Купец» становится rev8-canary,
остальные WorldPacks остаются на своих `6/7`, а persisted revision существующих
parties/branches не меняется.

Prompt «Купца» приведён к фактической rev8 boundary: он больше не ссылается на
удалённый `<AUTHORITATIVE_WORLD_STATE>` и не просит narrator записывать поля
state/memory. Ansible apply, container-env и stamp-party подтвердили effective
revision `8`, но stamp содержал только opening boundary без model calls. До
отложенных живых gates 25/60 registry остаётся на уровне `каркас`.

## Verification gates

Локальный gate проверяет eligibility, legacy null turns, квантованное окно
50–57 + uncovered, стабильный восьмиходовый hash, порядок stable-prefix / volatile-tail,
cache counters, safe coverage, целые overflow units, отсутствие legacy blocks,
plain narrator response, one-call five-section cadence, structural section retry,
empty-section acceptance, `finish_reason=length`, same-coverage persistence и
explicit provider/model logging.

Живой gate остаётся отдельным действием после полной реализации RP-ядра:

1. 25-turn rev-8 party: 24 предыдущих игровых units дословно, целевой состав
   blocks, service part не более 13 500 символов, no fallback и корректная
   местоименная связность;
2. 60-turn party: отсутствие episodic memory jobs/rows, один `section_key=all`
   call на штатный update, safe coverage без gaps, partial structural failure
   сохраняет четыре sections и вызывает только один exact section retry;
3. `service_call_log`: calls имеют exact OpenRouter/model, input не более
   20 000 символов, output limits 4000/800 и ни одной NVIDIA row;
4. на той же 60-turn party начало RAW и `stable_prompt_prefix_hash` меняются
   только на границе восьмиходового якоря; не менее пяти ходов из каждого окна
   восьми имеют `cached_prompt_tokens / prompt_tokens >= 0.70`, а средняя доля
   по партии выше 8.6%.

До этих gates registry не повышается выше `каркас`.

## Не входит

- S2 authored lore-card import и unified card scan — отдельный
  [Decision 037](037-rp-authored-lore-cards-and-confirmed-drafts.md);
- S3 GM channel, correction overlay и absorption — отдельный
  [Decision 038](038-rp-gm-corrections-and-player-overlay.md);
- S4 world clock/events;
- автоматическая миграция существующих parties/branches или массовое поднятие
  остальных WorldPacks до revision `8`;
- retention, prompt truncation или удаление raw/audit/state history.
- смена narrator model profile ради более выгодного cache-read тарифа.
