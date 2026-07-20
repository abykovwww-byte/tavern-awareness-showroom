# Setup Flow: Эллиноид

## Light GUI

After deployment under `/srv/apps/rp-stack/worldpacks/ellinoid/`, refresh `http://192.168.1.88:8010` or use the GUI refresh button. Create a new party, select world pack "Эллиноид", then use either "Роль из мира" or a custom player character prompt.

Recommended player character prompt:

```text
Я играю 18-летнюю дворянку из знатного, но обедневшего рода Соколовских. Имя, внешность и темперамент я задаю сама. Семья ждет выгодного брака; для героини важны любовь, честь, долг и возможность самой выбрать судьбу.
```

## SillyTavern Compatibility

The SillyTavern lorebook artifact is `sillytavern/ellinoid.json`. It becomes visible in SillyTavern only after the JSON is installed into the server-side runtime worlds folder, normally `/srv/app-data/rp-stack/data/default-user/worlds/`, or imported from the actual browser host as a fallback.

Draft files in this folder do not automatically appear in the SillyTavern GUI until deployed or imported.
