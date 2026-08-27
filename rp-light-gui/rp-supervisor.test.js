"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const functionSource = source.match(/function rpSupervisorStatusHtml[\s\S]*?\n}/)?.[0];
assert.ok(functionSource, "rpSupervisorStatusHtml must remain testable");

const context = {
  appState: {
    supervisor: {
      enabled: true,
      mode: "observe",
      story_turn_count: 7,
      first_retrospective_story_turn: 56,
      next_retrospective_story_turn: 56,
      last_evaluation: null,
      service_model: { title: "OpenRouter service model" },
    },
  },
  escapeHtml: (value) => String(value),
};
vm.runInNewContext(functionSource, context);

const observed = context.rpSupervisorStatusHtml();
assert.match(observed, /Надзор · наблюдение/);
assert.match(observed, /7\/56 канонических ходов/);
assert.match(observed, /ничего не добавляет в prompt нарратора/);
assert.match(observed, /OpenRouter service model/);

context.appState.supervisor.mode = "enforce";
context.appState.supervisor.story_turn_count = 64;
context.appState.supervisor.next_retrospective_story_turn = 72;
context.appState.supervisor.last_evaluation = { status: "checked", status_reason: null };
context.appState.supervisor.active_advisory_count = 2;
const enforced = context.rpSupervisorStatusHtml();
assert.match(enforced, /режим рекомендаций/);
assert.match(enforced, /следующая оценка на 72/);
assert.match(enforced, /проверено/);
assert.match(enforced, /Активных рекомендаций: 2/);

assert.match(source, /\/api\/parties\/\$\{partyId\}\/supervisor/);
assert.doesNotMatch(source, /supervisor.*raw_(?:prompt|response)/i);

console.log("light gui RP supervisor status: ok");
