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

console.log("structured content parser: ok");
