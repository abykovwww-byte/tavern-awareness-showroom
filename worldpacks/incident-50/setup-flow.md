# Setup Flow: Инцидент-50

## Light GUI

1. Выберите world pack `Инцидент-50`.
2. Создайте персонажа игрока из шаблона world pack.
3. Задайте имя, возраст, стиль работы и личный профессиональный конфликт персонажа, если хотите.
4. Выберите модель через обычный профиль Light GUI; world pack не фиксирует модель.
5. Запустите партию. Opening scene должна предложить первый фокус расследования.

## Suggested Player Character Additions

- Специализация: форензика Windows/Linux, сетевой анализ, cloud/SIEM, интервью, кризисные процессы.
- Личная слабость: перфекционизм, конфликт с эксплуатацией, страх ошибочного обвинения, усталость, прежний провал.
- Рабочий стиль: холодный аналитик, кризисный координатор, тихий следователь, жесткий защитник процедур.

## 50-Turn Use

- Ходы не обязаны быть календарными часами; один ход - одно значимое решение или последствие.
- Нарратор должен периодически напоминать о дедлайнах: 5, 15, 25, 35, 45 и 50.
- На 50-м ходу не открывать новый сюжетный узел, а закрывать основной инцидент по state.

## SillyTavern Lorebook

The lorebook is generated as `sillytavern/incident-50.json`. It is installed by IaC into:

```text
{{ rp_stack_data_dir }}/default-user/worlds/incident-50.json
```

For normal Light GUI play, do not copy `state-seed.json` into live `state/current.json`. Light GUI creates isolated party state from this seed.
