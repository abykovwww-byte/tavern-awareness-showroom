"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const html = fs.readFileSync(require.resolve("./index.html"), "utf8");
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

assert.deepEqual(
  plain(context.partyMessagePayload(
    "Ворота открыты.",
    "req-gm",
    [],
    "gm",
    "memory:canon:fact:old-gate:replace",
  )),
  {
    content: "Ворота открыты.",
    idempotency_key: "req-gm",
    channel: "gm",
    gm_target_slot: "memory:canon:fact:old-gate:replace",
  },
);

assert.match(source, /data-story-memory-action="replace"/);
assert.match(source, /data-story-memory-action="retract"/);
assert.match(source, /contractRevision >= 2/);
assert.match(source, /rp_contract_revision \|\| 0\) < 2/);
assert.match(source, /rp_contract_revision \|\| 0\) >= 9/);
assert.match(source, /result\.status === "route_required"/);
assert.match(source, /result\.status === "gm_draft"/);
assert.match(source, /turn\.turn_kind === "gm_correction"/);
assert.match(html, /id="gmMessageSubmit"/);
assert.match(html, /id="gmRouteMasterButton"/);
assert.match(html, /id="gmRouteSceneButton"/);
assert.match(html, /id="gmConfirmButton"/);
assert.match(html, /id="gmRejectButton"/);

console.log("light gui story memory correction: ok");
