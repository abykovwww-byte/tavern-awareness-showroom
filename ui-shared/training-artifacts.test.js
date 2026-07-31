const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(__dirname + "/training-artifacts.js", "utf8");
assert.equal(source.includes("innerHTML"), false);
assert.equal(source.includes("field_values"), false);
assert.equal(source.includes("javascript:"), false);

const context = { globalThis: {}, Date, Math, Set, Error };
context.globalThis.globalThis = context.globalThis;
vm.runInNewContext(source, context);
const api = context.globalThis.TrainingArtifacts;

assert.equal(api.supportedRenderers.length, 9);
assert.equal(api.validArtifact({
  schema_version: "rp-gateway.training-artifact.v1",
  renderer: "credential-form",
  theme: "office-blue",
  artifact_id: "artifact_test",
  display_url: "https://site.example.test",
  field_ids: ["login"],
  actions: ["submit"],
}), true);
const payload = api.eventPayload({ artifact_id: "artifact_test", artifact_revision: 1 }, "form_submitted", ["login", "login"]);
assert.deepEqual([...payload.filled_field_ids], ["login"]);
assert.equal(Object.hasOwn(payload, "values"), false);
