# Decision 037: Authored Lore Cards and confirmed party drafts

**Дата:** 2026-08-25

## Status

**Decision status: Accepted.** Для RP revision `8` Lore Cards становятся
коротким авторским контекстом, который включается только по явному упоминанию,
а не второй копией всего canonical state. Игрок может отдельно запросить
черновик карточки из завершённого хода и сохранить его только после проверки.

**Delivery status:** `каркас` для строк
[`registry/037.yml`](registry/037.yml). Source implementation и локальные
детерминированные проверки не являются server apply или живой проверкой
нарратива. Длинная партия и связанный live gate по решению пользователя
отложены до полной реализации RP-ядра.

## Context

Revision `8` уже возвращает основную часть prompt-бюджета дословной истории.
Но существующие Lore Cards создавались только внутри партии, искались также по
собственному скрытому content и не объясняли игроку, какая карточка реально
попала в конкретный ответ. Это делало скрытый факт собственным триггером и
оставляло WorldPack без компактного author-reviewed слоя для NPC, мест и улик.

Runtime-генерация всей библиотеки мира при старте вернула бы исходную проблему:
лишний model call, непроверенный лор и меняющийся результат для одинакового
WorldPack. Поэтому нарезка мира остаётся author-time операцией.

## Decision

### Authored WorldPack cards

- WorldPack может объявить `manifest.files.lore_cards = "lore-cards"` и хранить
  один или несколько `lore-cards/*.json` со schema
  `rp-gateway.worldpack-lore-cards.v1`.
- Каждая карточка имеет стабильный ASCII `key`, `title`, непустые точные
  `keywords`, `content`, `always_on` и `enabled`. Пустой список keywords и
  повторный key запрещены.
- NPC card использует `key=npc:<character-id>`, canonical name в `title`, все
  русские формы из relationship aliases в `keywords`, а в content — цель,
  жёсткие границы и факты, которые нельзя раскрывать без причины. Для NPC
  `always_on=false`.
- Нарезку может подготовить developer script через exact OpenRouter model, но
  результат является только candidate-файлом: человек сверяет его с authored
  source и коммитит JSON. Runtime не вызывает модель для импорта и не пишет в
  source WorldPack.
- При создании новой RP rev8 party Gateway валидирует и копирует authored cards
  в её существующее party-scoped Lore Card storage. Existing parties и revisions
  `0..7` не мигрируют.

Первый пакет `merchant-sviatoslav` содержит 16 карточек, включая отдельную
карточку для каждого из десяти NPC relationship model.

### One recent scan

Для rev8 один deterministic scan используется и для Lore Cards, и для
relationship presence:

1. current player input;
2. три предыдущих complete eligible RAW units;
3. optional `Outcome.target`.

Opening входит как assistant-only unit, обычный narrative — как целая
`user + assistant` пара; non-game kinds исключаются по Decision 032. Seed
location, active threads и `scene_state` не добавляют присутствие.

Lore Card выбирается только по whole match её `title` или `keywords` либо по
явному `always_on`. `content` не участвует в поиске: скрытый факт не может
поднять сам себя. В prompt входят только целые карточки; сериализованный
`PARTY_LORE_CARDS` не превышает 4 000 символов и при общем hard overflow
удаляется целиком первым.

### Player-confirmed draft

- Под каждым сохранённым narrator response новой rev8 RP party Light GUI
  показывает явную кнопку создания draft из этого завершённого хода.
- `POST /api/parties/{party_id}/lore-cards/draft` принимает только реальные
  complete eligible turn IDs. До восьми выбранных units сериализуются целиком;
  exact messages не могут превышать 8 000 символов.
- Выполняется один request с role `lore_card_draft`, provider `openrouter`, model
  `deepseek/deepseek-v4-pro`, `max_tokens=400` и strict JSON schema. Local,
  NVIDIA, narrator BYOK, retry на другую модель и provider fallback запрещены.
- Draft заполняет видимую форму, но не создаёт Lore Card. Только существующий
  create endpoint после явного submit сохраняет отредактированный игроком текст
  и source turn IDs.

### Observable raised cards

После финального prompt budget Gateway записывает точный упорядоченный список
`prompt_assembly.lore_card_ids` в metadata хода. History API добавляет только
читаемые title для этих ID, сохраняя порядок metadata. Light GUI показывает
эти карточки под соответствующим narrator response. Карточка, отброшенная
budget-ом, в metadata и UI не появляется.

Каждый draft attempt проходит через общий `ServiceModelClient` и остаётся в
`service_call_log` с exact role/provider/model, redacted prompt и непустым raw
response либо видимой ошибкой. Provider success не означает автоматического
создания карточки.

## Consequences

- WorldPack несёт только компактные author-reviewed карточки, а не новый runtime
  JSON мира в каждом prompt.
- NPC может сохранять скрытую мотивацию, не активируя её скрытым текстом.
- Игрок понимает причинную связь «эта карточка была поднята для этого ответа» и
  контролирует переход model draft в долговременную заметку.
- Импорт не увеличивает число model calls при создании партии.

## Non-goals

- автоматическая смысловая проверка или автоматический commit generated cards;
- фоновое создание карточек из каждого хода или free-language classifier;
- новый database table, migration существующих parties или история версий cards;
- изменение canonical state, relationship extraction, training runtime,
  retention или narrator provider policy;
- длинный live-run до завершения остальных срезов RP-ядра.

## Verification gates

Локально проверяются authored import без service call, schema/duplicate/alias
guards, whole title/keyword scan, отсутствие self-activation по content,
current-plus-three scope, 4 000-character whole-card block, exact turn metadata,
draft route/model/budgets/logging и отсутствие card до confirm. Repository gate
также проверяет минимум 15 карточек и полное NPC alias coverage у «Купца».

После полной реализации отдельный live gate должен подтвердить на длинной новой
party causal chain `recent mention -> selected IDs -> exact prompt -> committed
metadata -> chips under narrator response`, отсутствие model call на import и
отсутствие NVIDIA/local/fallback rows у draft. До этого registry не повышается
выше `каркас`.

## Related decisions

- [Decision 020](020-rp-relationship-pressure-layer.md)
- [Decision 032](032-rp-history-first-prompt-and-sectioned-memory.md)
- [Decision 036](036-retire-novel-and-nvidia.md)
