const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const html = fs.readFileSync(require.resolve("./index.html"), "utf8");

const rowSource = source.match(/function adminDatasetTurnRow[\s\S]*?\n}/)?.[0];
assert.ok(rowSource, "dataset row renderer must remain testable");

const context = {
  escapeHtml(value) {
    return String(value);
  },
};
vm.runInNewContext(rowSource, context);

const row = context.adminDatasetTurnRow({
  turn_id: 248,
  review_status: "review",
  player_message: "Я кот",
  auto_tags: ["training", "main"],
  tags: [],
});

assert.match(row, /data-dataset-open/);
assert.match(row, />Проверить<\/button>/);
assert.doesNotMatch(row, /data-dataset-status="review"/);
assert.match(html, /id="datasetTurnDialog"/);
assert.match(html, /id="datasetTurnPlayerMessage"/);
assert.match(html, /id="datasetTurnAssistantMessage"/);
assert.match(source, /datasetTurnPlayerMessage\.textContent = turn\.player_message/);
assert.match(source, /datasetTurnAssistantMessage\.textContent = turn\.assistant_response/);
assert.match(source, /escapeHtml\(createdAt\.text\)/);

console.log("light gui dataset review: ok");
