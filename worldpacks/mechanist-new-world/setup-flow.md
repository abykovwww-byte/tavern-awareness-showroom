# Setup Flow: Механист Нового Мира

This world pack is source-managed through the abykovserv IaC route.

## Light GUI

After deployment on `192.168.1.88`, refresh `http://192.168.1.88:8010` or use the GUI refresh button. Create a new party, select world pack "Механист Нового Мира", then use "Роль из мира" or a custom player character prompt.

Recommended player character prompt:

```text
Я играю 30-летнего мужчину-попаданца из Москвы XXI века, рост 187 см, вес 110 кг. Имя, прошлую профессию и характер я задаю сам. У персонажа есть абсолютный потенциал создавать и оживлять механизмы, но сила раскрывается постепенно через опыт, материалы, мастерские и риск.
```

## SillyTavern Compatibility

The lorebook artifact is `sillytavern/mechanist-new-world.json`. It becomes visible in SillyTavern only after Ansible copies it into `/srv/app-data/rp-stack/data/default-user/worlds/` on `192.168.1.88`.

Do not install this pack by writing Windows-local `/srv` paths.
