const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const start = source.indexOf("function narratorControlsForProfile");
const end = source.indexOf("function selectedPartyModelProfile", start);
assert.ok(start >= 0 && end > start, "narrator setting helpers must remain testable");

const context = {};
vm.runInNewContext(source.slice(start, end), context);
const plain = (value) => JSON.parse(JSON.stringify(value));

const deepSeekProfile = {
  params: {
    narrator_controls: {
      reasoning_efforts: ["none", "high", "xhigh"],
      default_reasoning_effort: "high",
      temperature: true,
      top_p: true,
      max_tokens: [1024, 2048, 4096, 8192, 16384],
    },
  },
};
const deepSeekControls = context.narratorControlsForProfile(deepSeekProfile);
assert.deepEqual(
  plain(context.narratorSettingsPayload(deepSeekControls, {
    reasoning_effort: "xhigh",
    temperature: "0.7",
    top_p: "0.9",
    max_tokens: "4096",
  })),
  { reasoning_effort: "xhigh", temperature: 0.7, top_p: 0.9, max_tokens: 4096 },
);
assert.deepEqual(
  plain(context.narratorSettingsPayload(deepSeekControls, {
    reasoning_effort: "",
    temperature: "",
    top_p: "",
    max_tokens: "",
  })),
  {},
);
assert.throws(
  () => context.narratorSettingsPayload(deepSeekControls, { reasoning_effort: "low" }),
  /недоступна/,
);
assert.throws(
  () => context.narratorSettingsPayload(deepSeekControls, { temperature: "2.1" }),
  /от 0 до 2/,
);

const lunaControls = context.narratorControlsForProfile({
  params: {
    narrator_controls: {
      reasoning_efforts: ["none", "low", "medium", "high", "xhigh", "max"],
      default_reasoning_effort: "medium",
      temperature: false,
      top_p: false,
      max_tokens: [1024, 2048],
    },
  },
});
assert.deepEqual(
  plain(context.narratorSettingsPayload(lunaControls, {
    reasoning_effort: "max",
    temperature: "1.4",
    top_p: "0.7",
    max_tokens: "2048",
  })),
  { reasoning_effort: "max", max_tokens: 2048 },
);
assert.equal(context.narratorControlsForProfile({ params: {} }), null);

const html = fs.readFileSync(require.resolve("./index.html"), "utf8");
for (const id of [
  "narratorReasoningSelect",
  "narratorTemperatureInput",
  "narratorTopPInput",
  "narratorMaxTokensSelect",
]) {
  assert.match(html, new RegExp(`id="${id}"`));
}
assert.match(html, /Сколько усилий модель тратит/);
assert.match(html, /Обычно меняйте его или температуру/);
assert.match(source, /narrator_settings: narratorSettings/);

console.log("narrator settings tests passed");
