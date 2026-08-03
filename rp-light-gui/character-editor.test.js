const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const html = fs.readFileSync(require.resolve("./index.html"), "utf8");

function sourceBetween(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `${startMarker} must remain testable`);
  return source.slice(start, end);
}

const testedSource = [
  sourceBetween("function isCompactRpCharacterEditor", "function editableCharacters"),
  sourceBetween("function characterEditorPayload", "function validateCharacterPayload"),
  sourceBetween("function validateCharacterPayload", "function characterEditorInstruction"),
  sourceBetween("function characterEditorInstruction", "async function createAdminUser"),
].join("\n");

const input = (value) => ({ value });
const context = {
  appState: { activeParty: { scenario_type: "rp" } },
  els: {
    characterEditTarget: input("npc:captain"),
    characterEditId: input(""),
    characterEditName: input("Captain"),
    characterEditStatus: input("alive"),
    characterEditLocation: input("bridge"),
    characterEditGoal: input("Protect the ship"),
    characterEditAttitude: input("guarded"),
    characterEditLoyalty: input("crew"),
    characterEditTrust: input("4"),
    characterEditFear: input("1"),
    characterEditKnowledge: input("The route"),
    characterEditObligations: input("Keep watch"),
    characterEditHardConstraints: input("Never abandon crew"),
    characterEditSecrets: input("Hidden cargo"),
  },
};
vm.runInNewContext(testedSource, context);

const rpPayload = context.characterEditorPayload();
assert.deepEqual(
  Object.keys(rpPayload).sort(),
  ["character_id", "confirm", "current_goal", "location", "name", "status", "target"].sort(),
);
assert.equal(context.validateCharacterPayload(rpPayload), "");
assert.doesNotMatch(context.characterEditorInstruction(rpPayload), /undefined/);

context.appState.activeParty.scenario_type = "training";
const trainingPayload = context.characterEditorPayload();
assert.equal(trainingPayload.attitude_to_player, "guarded");
assert.equal(trainingPayload.loyalty, "crew");
assert.equal(trainingPayload.trust, 4);
assert.equal(trainingPayload.fear, 1);
assert.equal(trainingPayload.knowledge, "The route");
assert.equal(trainingPayload.obligations, "Keep watch");
assert.equal(trainingPayload.hard_constraints, "Never abandon crew");
assert.equal(trainingPayload.secrets, "Hidden cargo");

assert.match(source, /classList\.toggle\("hidden", compactRpEditor\)/);
const advancedStart = html.indexOf('id="characterEditAdvancedFields"');
const buttonsStart = html.indexOf('<div class="button-grid">', advancedStart);
assert.ok(advancedStart >= 0 && buttonsStart > advancedStart);
for (const id of [
  "characterEditAttitude",
  "characterEditLoyalty",
  "characterEditTrust",
  "characterEditFear",
  "characterEditKnowledge",
  "characterEditObligations",
  "characterEditHardConstraints",
  "characterEditSecrets",
]) {
  const fieldPosition = html.indexOf(`id="${id}"`);
  assert.ok(fieldPosition > advancedStart && fieldPosition < buttonsStart, `${id} must stay in the advanced block`);
}

console.log("light gui character editor: ok");
