const assert = require("node:assert/strict");
const { formatMessageTime, normalizeMessageDate } = require("./message-time.js");

assert.equal(normalizeMessageDate(1_720_000_000).toISOString(), "2024-07-03T09:46:40.000Z");
assert.equal(normalizeMessageDate(1_720_000_000_000).toISOString(), "2024-07-03T09:46:40.000Z");
assert.equal(normalizeMessageDate("not-a-date"), null);

const formatted = formatMessageTime("2024-07-03T09:46:40Z", "ru-RU");
assert.match(formatted.text, /^\d{2}:\d{2}$/);
assert.equal(formatted.iso, "2024-07-03T09:46:40.000Z");
assert.ok(formatted.title.length > formatted.text.length);

console.log("showcase gui message time: ok");
