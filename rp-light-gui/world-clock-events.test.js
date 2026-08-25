"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const functionSource = source.match(/function worldClockEventsForTurn[\s\S]*?\n}/)?.[0];
assert.ok(functionSource, "worldClockEventsForTurn must remain testable");

const context = {};
vm.runInNewContext(functionSource, context);
const projection = context.worldClockEventsForTurn({
  metadata: {
    world_clock_events: {
      schema_version: "rp-gateway.world-clock-events.v1",
      date: "0964-04-23T06:00:00Z",
      occurred: [
        { id: "merchant.vyatichi-campaign-departs", text: "Дружина выступила.", date: "0964-04-23T06:00:00Z" },
      ],
      horizon: [
        { id: "merchant.khazar-campaign-prepares", text: "Готовится следующий поход.", date: "0965-04-01T08:00:00Z" },
      ],
    },
  },
});

assert.deepEqual(JSON.parse(JSON.stringify(projection)), {
  date: "0964-04-23T06:00:00Z",
  occurred: [
    { id: "merchant.vyatichi-campaign-departs", text: "Дружина выступила.", date: "0964-04-23T06:00:00Z" },
  ],
  horizon: [
    { id: "merchant.khazar-campaign-prepares", text: "Готовится следующий поход.", date: "0965-04-01T08:00:00Z" },
  ],
});
assert.equal(context.worldClockEventsForTurn({ metadata: {} }), null);
assert.match(source, /В мире произошло:/);
assert.match(source, /Ближайший горизонт:/);
assert.match(source, /worldClockEventsForTurn\(turn\)/);

console.log("light gui world-clock event projection: ok");
