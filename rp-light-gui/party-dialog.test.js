const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const functionSource = source.match(/function selectedRadioValue[\s\S]*?\n}/)?.[0];
assert.ok(functionSource, "selectedRadioValue must remain testable");

let selected = null;
const context = {
  document: {
    querySelector() {
      return selected;
    },
  },
};
vm.runInNewContext(functionSource, context);

assert.equal(context.selectedRadioValue("scenarioType", ""), "");
assert.equal(context.selectedRadioValue("worldSource"), "ready");
selected = { value: "training" };
assert.equal(context.selectedRadioValue("scenarioType", ""), "training");

assert.equal((source.match(/selectedRadioValue\("scenarioType", ""\)/g) || []).length, 2);

console.log("light gui party dialog: ok");
