# Setup Flow: Awareness

## Light GUI

1. Выберите world pack `Awareness`.
2. Создайте персонажа игрока из шаблона world pack.
3. В описании персонажа задайте профессию, отдел, обычные задачи и уровень доступа. Например: разработчик backend, системный аналитик, project manager, DevOps/SRE, QA, support, HR, finance.
4. Не выбирайте модель внутри world pack; используйте обычный профиль модели Light GUI.
5. Запустите партию. Opening scene начнет понедельник утром и предложит первое решение.

## Suggested Player Character Additions

- Профессия и отдел.
- Формат работы: офис, гибрид или удаленка.
- Уровень доступа: обычный сотрудник, руководитель малой команды, технический специалист, менеджер проекта.
- Рабочий стиль: быстрый исполнитель, осторожный аналитик, коммуникационный координатор, уставший многозадачник.
- Личная связь для внерабочих эпизодов: партнер, родственник, старый знакомый.

## 10-Turn Use

- Ход 1-2: понедельник.
- Ход 3-4: вторник.
- Ход 5-6: среда.
- Ход 7-8: четверг.
- Ход 9-10: пятница.
- Ход 10: обязательный финал, саммари и оценка.

## SillyTavern Lorebook

The lorebook is generated as `sillytavern/awareness.json`. It is installed by IaC into:

```text
{{ rp_stack_data_dir }}/default-user/worlds/awareness.json
```

For normal Light GUI play, do not copy `state-seed.json` into live `state/current.json`. Light GUI creates isolated party state from this seed.
