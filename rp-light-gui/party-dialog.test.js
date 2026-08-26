const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const html = fs.readFileSync(require.resolve("./index.html"), "utf8");
const plain = (value) => JSON.parse(JSON.stringify(value));

function sourceBetween(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `${startMarker} must remain testable`);
  return source.slice(start, end);
}

const selectedRadioSource = source.match(/function selectedRadioValue[\s\S]*?\n}/)?.[0];
assert.ok(selectedRadioSource, "selectedRadioValue must remain testable");

let selected = null;
const radioContext = {
  document: {
    querySelector() {
      return selected;
    },
  },
};
vm.runInNewContext(selectedRadioSource, radioContext);

assert.equal(radioContext.selectedRadioValue("scenarioType", ""), "");
assert.equal(radioContext.selectedRadioValue("worldSource"), "ready");
selected = { value: "training" };
assert.equal(radioContext.selectedRadioValue("scenarioType", ""), "training");
assert.equal((source.match(/selectedRadioValue\("scenarioType", ""\)/g) || []).length, 4);

const worldPosition = html.indexOf('id="worldSelect"');
const presetPosition = html.indexOf('id="partyPresetSelect"');
const openingPosition = html.indexOf('id="partyOpeningSelect"');
const characterPosition = html.indexOf("<legend>Персонаж</legend>");
assert.ok(worldPosition >= 0 && worldPosition < presetPosition, "preset selector must follow the world selector");
assert.ok(presetPosition < openingPosition, "opening selector must follow the preset selector");
assert.ok(openingPosition < characterPosition, "revision-11 choices must precede character creation");

function element(value = "") {
  const classes = new Set(["hidden"]);
  const attributes = new Set();
  return {
    value,
    innerHTML: "",
    disabled: false,
    dataset: {},
    classList: {
      toggle(name, force) {
        if (force) classes.add(name);
        else classes.delete(name);
      },
      contains(name) {
        return classes.has(name);
      },
    },
    toggleAttribute(name, force) {
      if (force) attributes.add(name);
      else attributes.delete(name);
    },
    hasAttribute(name) {
      return attributes.has(name);
    },
  };
}

const rev11Pack = {
  id: "rev11-world",
  title: "Revision 11",
  manifest: { player_role: "Legacy alias role" },
  presets: [
    { id: "book", title: "Книжный" },
    { id: "action", title: "Действие" },
  ],
  presets_default: "action",
  openings: [
    { id: "night-trainee", title: "Стажёр", player_role: "Стажёр Ночного Дозора" },
    { id: "independent", title: "Независимый", player_role: "Независимый Иной" },
  ],
  openings_default: "independent",
};
const legacyPack = {
  id: "legacy-world",
  title: "Legacy",
  manifest: { player_role: "Legacy player role" },
};

let currentPack = rev11Pack;
const choiceContext = {
  els: {
    partyPresetFields: element(),
    partyPresetSelect: element(),
    partyOpeningFields: element(),
    partyOpeningSelect: element(),
    characterDescriptionInput: element(),
  },
  escapeHtml: (value) => String(value),
  selectedRadioValue: (name) => choiceContext.radioValues[name] || "ready",
  selectedWorldpack: () => currentPack,
  radioValues: { worldSource: "ready", characterSource: "ready", scenarioType: "rp" },
};
vm.runInNewContext(
  [
    sourceBetween("function resolveWorldpackChoice", "function syncAutoPartyTitle"),
    sourceBetween("function syncReadyCharacterDescription", "async function createParty"),
  ].join("\n"),
  choiceContext,
);

assert.equal(
  choiceContext.resolveWorldpackChoice([{ id: "first" }, { id: "default" }], "", "default").id,
  "default",
);
assert.equal(choiceContext.resolveWorldpackChoice([{ id: "first" }], "", "missing"), null);

choiceContext.renderPartyWorldChoices();
assert.equal(choiceContext.els.partyPresetSelect.value, "action", "preset must use the declared default, not the first item");
assert.equal(choiceContext.els.partyOpeningSelect.value, "independent", "opening must use the declared default, not the first item");
assert.equal(choiceContext.els.partyPresetFields.classList.contains("hidden"), false);
assert.equal(choiceContext.els.partyOpeningFields.classList.contains("hidden"), false);
assert.equal(choiceContext.els.partyPresetSelect.hasAttribute("required"), true);
assert.equal(choiceContext.els.partyOpeningSelect.hasAttribute("required"), true);
choiceContext.syncReadyCharacterDescription();
assert.equal(choiceContext.els.characterDescriptionInput.value, "Независимый Иной");
choiceContext.els.partyOpeningSelect.value = "night-trainee";
choiceContext.syncReadyCharacterDescription();
assert.equal(choiceContext.els.characterDescriptionInput.value, "Стажёр Ночного Дозора");

choiceContext.radioValues.scenarioType = "training";
choiceContext.renderPartyWorldChoices();
assert.equal(choiceContext.els.partyPresetFields.classList.contains("hidden"), true);
assert.equal(choiceContext.els.partyOpeningFields.classList.contains("hidden"), true);
assert.deepEqual(plain(choiceContext.selectedPartyWorldChoices()), { preset: null, opening: null });
choiceContext.radioValues.scenarioType = "rp";

currentPack = legacyPack;
choiceContext.renderPartyWorldChoices();
choiceContext.syncReadyCharacterDescription();
assert.equal(choiceContext.els.partyPresetFields.classList.contains("hidden"), true);
assert.equal(choiceContext.els.partyOpeningFields.classList.contains("hidden"), true);
assert.equal(choiceContext.els.partyPresetSelect.hasAttribute("required"), false);
assert.equal(choiceContext.els.partyOpeningSelect.hasAttribute("required"), false);
assert.equal(choiceContext.els.characterDescriptionInput.value, "Legacy player role");

async function runCreateParty(pack, scenarioType = "rp") {
  const calls = [];
  const toasts = [];
  const createContext = {
    els: {
      modelSelect: element("model-1"),
      partyPresetSelect: element(""),
      partyOpeningSelect: element(""),
      characterDescriptionInput: element(""),
      characterNameInput: element("Игрок"),
      partyTitleInput: element("Тестовая партия"),
    },
    selectedWorldpack: () => pack,
    selectedRadioValue: (name, fallback = "ready") => ({ scenarioType, characterSource: "ready" }[name] || fallback),
    resolveWorldpack: async () => pack,
    setBusy() {},
    closePartyDialog() {},
    boot: async () => {},
    selectParty: async () => {},
    autoStartParty: async () => {},
    showToast(message) {
      toasts.push(message);
    },
    async apiPost(path, payload) {
      calls.push({ path, payload: plain(payload) });
      if (path === "/api/player-characters/draft") {
        return {
          draft: {
            worldpack_id: payload.worldpack_id,
            opening_id: payload.opening_id || pack.openings_default,
            name: payload.name,
            description: payload.concept,
            profile: {},
          },
        };
      }
      if (path === "/api/player-characters") return { player_character: { id: "character-1" } };
      if (path === "/api/parties") return { party: { id: "party-1" } };
      throw new Error(`unexpected POST ${path}`);
    },
  };
  vm.runInNewContext(
    [
      sourceBetween("function resolveWorldpackChoice", "function renderPartyWorldChoices"),
      sourceBetween("async function createParty", "async function resolveWorldpack"),
    ].join("\n"),
    createContext,
  );
  await createContext.createParty({ preventDefault() {} });
  return { calls, toasts };
}

(async () => {
  const rev11 = await runCreateParty(rev11Pack);
  assert.deepEqual(rev11.calls.map((call) => call.path), [
    "/api/player-characters/draft",
    "/api/player-characters",
    "/api/parties",
  ]);
  assert.deepEqual(rev11.calls[0].payload, {
    worldpack_id: "rev11-world",
    name: "Игрок",
    concept: "Независимый Иной",
    opening_id: "independent",
  });
  assert.equal(rev11.calls[1].payload.opening_id, "independent", "the returned draft must flow unchanged into character creation");
  assert.deepEqual(rev11.calls[2].payload, {
    title: "Тестовая партия",
    scenario_type: "rp",
    worldpack_id: "rev11-world",
    player_character_id: "character-1",
    model_profile_id: "model-1",
    preset_id: "action",
    opening_id: "independent",
  });
  assert.match(rev11.toasts.at(-1), /GM готовит/);

  const legacy = await runCreateParty(legacyPack);
  assert.deepEqual(legacy.calls[0].payload, {
    worldpack_id: "legacy-world",
    name: "Игрок",
    concept: "Legacy player role",
  });
  assert.deepEqual(Object.keys(legacy.calls[1].payload).sort(), [
    "description",
    "name",
    "profile",
    "worldpack_id",
  ]);
  assert.deepEqual(legacy.calls[2].payload, {
    title: "Тестовая партия",
    scenario_type: "rp",
    worldpack_id: "legacy-world",
    player_character_id: "character-1",
    model_profile_id: "model-1",
  });

  const multiScenarioTrainingPack = {
    ...rev11Pack,
    manifest: {
      ...rev11Pack.manifest,
      scenario_types: { supported: ["rp", "training"] },
    },
  };
  const training = await runCreateParty(multiScenarioTrainingPack, "training");
  assert.equal("opening_id" in training.calls[0].payload, false);
  assert.equal(training.calls[1].payload.opening_id, "independent");
  assert.equal("preset_id" in training.calls[2].payload, false);
  assert.equal("opening_id" in training.calls[2].payload, false);

  console.log("light gui party dialog: ok");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
