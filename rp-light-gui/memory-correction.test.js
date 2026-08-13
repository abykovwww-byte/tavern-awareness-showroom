"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const functionSource = source.match(/function partyMessagePayload[\s\S]*?\n}/)?.[0];
assert.ok(functionSource, "partyMessagePayload must remain testable");

const context = {};
vm.runInNewContext(functionSource, context);
const plain = (value) => JSON.parse(JSON.stringify(value));

assert.deepEqual(
  plain(context.partyMessagePayload("Иду к воротам", "req-1")),
  { content: "Иду к воротам", idempotency_key: "req-1" },
);

const correction = {
  field: "canon",
  fact_id: "fact:old-gate",
  action: "replace",
  replacement_text: "Ворота открыты.",
};
assert.deepEqual(
  plain(context.partyMessagePayload("Продолжаю путь", "req-2", [correction])),
  {
    content: "Продолжаю путь",
    idempotency_key: "req-2",
    story_memory_corrections: [correction],
  },
);

assert.match(source, /data-story-memory-action="replace"/);
assert.match(source, /data-story-memory-action="retract"/);
assert.match(source, /rp_contract_revision \|\| 0\) >= 2/);
assert.match(source, /rp_contract_revision \|\| 0\) < 2/);

console.log("light gui story memory correction: ok");
