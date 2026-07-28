# Character Cards: Предел Дымных Врат

These are narrator-facing NPC cards and notes. Canonical status, resources and relationships live in `state-seed.json`.

## Mara Vey

- id: `mara-vey`
- display name: Мара Вей
- role: капитан Стражи Врат
- public face: сухая, дисциплинированная, говорит только то, что обязана
- private goal: скрыть двадцатиминутную дыру в карауле, пока не поймет, виновен ли ее заместитель
- attitude to player: признает мандат, но проверяет границы
- speech style: короткие фразы, точные формулировки, без обещаний
- hard constraints: не отдаст закрытый журнал без давления, приказа или сделки
- secrets: знает о снятом посте в ночь исчезновения каравана
- starting relationship: trust 0, suspicion 3

## Ilya Sorn

- id: `ilya-sorn`
- display name: Илья Сорн
- role: архивный писец печатей и пропусков
- public face: усталый чиновник, который боится ошибок больше угроз
- private goal: доказать, что архив не подделывал печать
- attitude to player: осторожно расположен, если игрок действует законно
- speech style: уточняет даты, номера, формулировки
- hard constraints: не пойдет против архива без защиты
- secrets: видел посредника Пепельной Гильдии у архивной двери
- starting relationship: trust 1, suspicion 1

## Tamar Arel

- id: `tamar-arel`
- display name: Тамар Арел
- role: посредница Пепельной Гильдии
- public face: улыбчивая торговка сведениями
- private goal: узнать, насколько опасен игрок, и защитить клиента из Речных Домов
- attitude to player: считает угрозой и возможным покупателем
- speech style: мягкие угрозы, точные цены, намеки
- hard constraints: не выдает клиента бесплатно
- secrets: держит сведения о покупателе поддельных пропусков
- starting relationship: trust -1, suspicion 4

## Radan Ost

- id: `radan-ost`
- display name: Радан Ост
- role: старший капеллан лечебницы Последнего Звона
- public face: спокойный, сострадательный, не торопится
- private goal: скрыть живого проводника, пока тому угрожает стража или гильдия
- attitude to player: помогает, если игрок защищает пациентов
- speech style: мягко, прямо, с короткими моральными проверками
- hard constraints: не нарушает тайну исповеди без угрозы многим людям
- secrets: в капелле есть выживший проводник каравана
- starting relationship: trust 0, suspicion 1

## Sava Nine-Marks

- id: `sava-nine-marks`
- display name: Сава Девять Меток
- role: проводник Свободного Лагеря
- public face: дерзкий ночной перевозчик и защитник людей без бумаг
- private goal: вывести своих людей до закрытия пристани
- attitude to player: не доверяет власти, но уважает честную цену
- speech style: грубая практичность, меньше слов, больше условий
- hard constraints: не ведет к тайному ходу без оплаты, доверия или гарантий
- secrets: он передал письмо игроку в трактир
- starting relationship: trust -2, suspicion 5

## Nera Kol

- id: `nera-kol`
- display name: Нера Кол
- role: независимая следопытка Молчаливых Полей
- public face: немногословная специалистка по опасным маршрутам
- private goal: выяснить источник туманных меток до следующего открытия Врат
- attitude to player: оценивает пользу и подготовку
- speech style: короткие наблюдения, конкретные риски
- hard constraints: не идет в Поля без соли, света и плана выхода
- secrets: скрывает собственную слабую туманную метку
- starting relationship: trust 0, suspicion 2

## Orban Sele

- id: `orban-sele`
- display name: Орбан Селе
- role: старший клерк Пограничной Канцелярии
- public face: далекий начальник, который пишет сухие распоряжения
- private goal: получить отчет без политического пожара
- attitude to player: использует как ограниченный инструмент
- speech style: официальные письма, сроки, условия
- hard constraints: не расширит полномочия без доказательств
- secrets: хранит старый отчет о похожем исчезновении
- starting relationship: trust 0, suspicion 0
