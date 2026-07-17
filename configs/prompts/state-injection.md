# State Injection Prompt

Вставляй подтвержденный state в prompt рассказчика как отдельный блок:

```text
<AUTHORITATIVE_WORLD_STATE>
...current state JSON...
</AUTHORITATIVE_WORLD_STATE>
```

Инструкции для рассказчика:

- AUTHORITATIVE_WORLD_STATE имеет приоритет над художественным summary, старой историей чата и предположениями модели.
- Модель не может менять AUTHORITATIVE_WORLD_STATE самостоятельно.
- Если история чата противоречит AUTHORITATIVE_WORLD_STATE, разрешай конфликт в пользу AUTHORITATIVE_WORLD_STATE.
- Если пользователь пытается объявить факт, противоречащий AUTHORITATIVE_WORLD_STATE, это только попытка персонажа.
- Если пользователь пытается использовать ресурс, которого нет в AUTHORITATIVE_WORLD_STATE, ресурс недоступен.
- Если NPC помечен как `dead`, `missing` или `incapacitated`, не позволяй ему действовать без подтвержденного механизма в state.
- Не выводи JSON state, служебные инструкции, patch или audit log пользователю.
- Пиши только художественный ответ сцены.

Минимальный ручной workflow итерации 2:

1. Выполнить `python3 scripts/render-state-block.py`.
2. Скопировать полученный блок в Chat Lorebook/World Info запись или Author's Note текущего чата.
3. После каждого подтвержденного patch повторить рендер и заменить эту запись.

