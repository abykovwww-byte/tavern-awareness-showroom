"use strict";

const assert = require("node:assert/strict");
const { parseStructuredNarrative } = require("./structured-content.js");

const sample = `Ход 1. Понедельник, 10:00-14:00.

ПИСЬМО
Канал: корпоративная почта
От: Роман Иванов <roman@example.test>
Кому: player@example.test
Дата/время: понедельник, 15:10
Тема: Срочное подтверждение
Вложения: нет
Ссылки: https://example.test/confirm
Тело:
Иван, привет.

Подтверди ознакомление до 18:00.
Подпись:
Роман Иванов
Project Manager
---
СООБЩЕНИЕ
Канал: рабочий мессенджер
Чат: личный чат
От: Максим Карелин
Кому: player@example.test
Дата/время: понедельник, 15:25
Вложения: нет
Ссылки: нет
Текст:
Иваныч, привет ещё раз!
---
Что вы сделаете?`;

const segments = parseStructuredNarrative(sample);
assert.deepEqual(segments.map((segment) => segment.type), ["text", "email", "messenger", "text"]);
assert.equal(segments[1].fields["Тема"], "Срочное подтверждение");
assert.match(segments[1].fields["Тело"], /до 18:00/);
assert.match(segments[1].fields["Подпись"], /Project Manager/);
assert.equal(segments[2].fields["Чат"], "личный чат");
assert.equal(segments[2].fields["Текст"], "Иваныч, привет ещё раз!");
assert.equal(segments[3].text, "Что вы сделаете?");

const malformed = parseStructuredNarrative("ПИСЬМО\nнеструктурированный текст");
assert.deepEqual(malformed, [{ type: "text", text: "ПИСЬМО\nнеструктурированный текст" }]);

const incomplete = parseStructuredNarrative("СООБЩЕНИЕ\nОт: Максим\nТекст: Привет");
assert.deepEqual(incomplete, [{ type: "text", text: "СООБЩЕНИЕ\nОт: Максим\nТекст: Привет" }]);

const emailWithoutSeparator = parseStructuredNarrative(`ПИСЬМО
Канал: корпоративная почта
От: Анна Петрова <petrova@ptsecurity.com>
Кому: employee@ptsecurity.com
Дата/время: текущий интервал
Тема: План на сегодня
Вложения: нет
Ссылки: нет
Тело:
До 09:45 пришли короткий план по своим задачам.
Подпись:
Отправитель указан в поле «От»
Email: petrova@ptsecurity.com

Что ты делаешь и как отвечаешь в рамках своей должности?`);
assert.deepEqual(emailWithoutSeparator.map((segment) => segment.type), ["email", "text"]);
assert.equal(emailWithoutSeparator[0].fields["Подпись"], "Отправитель указан в поле «От»\nEmail: petrova@ptsecurity.com");
assert.equal(emailWithoutSeparator[1].text, "Что ты делаешь и как отвечаешь в рамках своей должности?");

const messageWithoutSeparator = parseStructuredNarrative(`СООБЩЕНИЕ
Канал: корпоративный мессенджер
Чат: личный чат
От: Иван Козырев
Кому: employee@ptsecurity.com
Дата/время: текущий интервал
Вложения: нет
Ссылки: нет
Текст:
Окей, понял. Давай тогда так: я переговорю с клиентом.

Слушай, а что там с обновлением VPN? Ты себе уже поставил или как?

Что ты отвечаешь Ивану?`);
assert.deepEqual(messageWithoutSeparator.map((segment) => segment.type), ["messenger", "text"]);
assert.match(messageWithoutSeparator[0].fields["Текст"], /Ты себе уже поставил или как\?/);
assert.doesNotMatch(messageWithoutSeparator[0].fields["Текст"], /Что ты отвечаешь Ивану/);
assert.equal(messageWithoutSeparator[1].text, "Что ты отвечаешь Ивану?");

const questionInsideMessage = parseStructuredNarrative(`СООБЩЕНИЕ
Канал: корпоративный мессенджер
Чат: личный чат
От: Иван Козырев
Кому: employee@ptsecurity.com
Текст:
Привет.

Что ты думаешь?`);
assert.deepEqual(questionInsideMessage.map((segment) => segment.type), ["messenger"]);
assert.match(questionInsideMessage[0].fields["Текст"], /Что ты думаешь\?/);

console.log("structured content parser: ok");
