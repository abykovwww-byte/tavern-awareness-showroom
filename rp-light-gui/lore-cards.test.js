"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const functionSource = source.match(/function raisedLoreCardsForTurn[\s\S]*?\n}/)?.[0];
assert.ok(functionSource, "raisedLoreCardsForTurn must remain testable");

const context = {};
vm.runInNewContext(functionSource, context);
const cards = context.raisedLoreCardsForTurn({
  metadata: { prompt_assembly: { lore_card_ids: [7, 3] } },
  activated_lore_cards: [
    { id: 3, title: "Горазд" },
    { id: 7, title: "Ждан" },
    { id: 9, title: "Не была поднята" },
  ],
});

assert.deepEqual(JSON.parse(JSON.stringify(cards)), [
  { id: 7, title: "Ждан" },
  { id: 3, title: "Горазд" },
]);
assert.match(source, /data-lore-draft-turn-id/);
assert.match(source, /\/lore-cards\/draft/);
assert.match(source, /pendingLoreCardSourceTurnIds/);
assert.match(source, /Черновик готов\. В память партии он ещё не записан/);

console.log("light gui Lore Cards draft and raised-card projection: ok");
