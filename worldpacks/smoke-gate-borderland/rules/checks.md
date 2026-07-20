# Checks: Предел Дымных Врат

Use gateway checks when the outcome is uncertain, costly, resisted by an NPC, or would change state.

## Suggested Check Types

- `persuasion`: убедить NPC дать доступ, время, защиту или показания.
- `deception`: скрыть цель, выдать легенду, поймать собеседника на противоречии.
- `intimidation`: давить отчетом, законом, оглаской или угрозой последствий.
- `information`: искать улику в архиве, сравнивать печати, читать реестры.
- `resource`: тратить монеты, воск, настойку, соль или иной ограниченный ресурс.
- `stealth`: попасть в место без внимания, проследить за посредником.
- `trust`: проверить, готов ли NPC рискнуть ради игрока.
- `feasibility`: оценить сложный план до попытки.
- `conflict`: физическое столкновение, задержание, побег.

## Example Chat Commands

```text
/check information target=ledger-archive skill=2 difficulty=11 goal="сравнить две печати каравана"
/check persuasion target=mara-vey skill=1 difficulty=13 goal="получить доступ к закрытому журналу Стражи"
/check deception target=tamar-arel skill=2 difficulty=12 goal="выдать себя за покупателя сведений о пропусках"
/check resource resource=coin amount=2 difficulty=8 goal="заплатить Саве за безопасную встречу"
/check feasibility difficulty=10 goal="пойти в Молчаливые Поля без проводника, но с солью и фонарем"
```

## Difficulty Guide

- 8: routine if prepared, still not automatic.
- 10: normal pressure with visible stakes.
- 12: resisted by an NPC or constrained by law.
- 14: risky, opposed, or likely to create consequences.
- 16: possible only with strong leverage, rare resource or excellent setup.

## State Rules

- A failed check must remain failed in narration.
- A partial success must name its price or limitation.
- Access to `gate-journal-access`, the hidden survivor, the river route, and black salt should be confirmed in state before treated as available.
- Repeated attempts need a changed situation, new leverage, new resource, or a consequence.
