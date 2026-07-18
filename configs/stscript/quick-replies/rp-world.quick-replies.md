# RP World Quick Replies

The IaC role creates a `RP World` Quick Reply preset under SillyTavern data when
the file does not already exist:

```text
/srv/app-data/rp-stack/data/default-user/QuickReplies/RP World.json
```

If you want to recreate it manually, use the labels and script bodies below. The
buttons send ordinary chat messages that the RP Gateway intercepts before the
narrator model.

## Setup

```stscript
/qr-presetadd slots=8 inject=false RP World
```

## Buttons

Label: `Мир`

```stscript
/input rows=6 wide=on large=on okButton="Preview" Что изменить или запомнить в мире? |
/setvar key=rp_world_instruction |
/send /world {{getvar::rp_world_instruction}} |
/trigger
```

Label: `Применить мир`

```stscript
/send /world apply latest |
/trigger
```

Label: `Отменить preview`

```stscript
/send /world discard latest |
/trigger
```

Label: `Показать мир`

```stscript
/send /world show |
/trigger
```

Label: `Откат мира`

```stscript
/send /world rollback |
/trigger
```

## Player Flow

1. Click `Мир`.
2. Write a natural instruction, for example:

```text
Запомни: стражник Varn теперь подозревает игрока, но боится докладывать капитану.
```

3. The gateway replies with a preview and a proposal id.
4. Click `Применить мир` to apply the latest proposal, or `Отменить preview`
   to discard it.

The canonical state is changed only after `apply`. The lorebook can still be
used as prompt memory, but the RP Gateway remains the source of truth.
