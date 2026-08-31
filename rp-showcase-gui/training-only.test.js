import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const html = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const source = readFileSync(new URL("./app.js", import.meta.url), "utf8");
const nginx = readFileSync(new URL("./nginx.conf", import.meta.url), "utf8");

test("Showroom admin exposes only training WorldPack scenarios", () => {
  assert.match(html, /<option value="training">Training<\/option>/);
  assert.doesNotMatch(html, /<option value="rp">/);
  assert.doesNotMatch(html, /name="worldSource"/);
  assert.doesNotMatch(html, /id="worldPromptInput"/);
});

test("Showroom scenario writes are hard-coded to the training preset contract", () => {
  assert.match(source, /scenario_type:\s*"training"/);
  assert.match(source, /world_source:\s*"preset"/);
  assert.match(source, /worldpack_id:\s*els\.worldpackSelect\.value/);
  assert.match(source, /world_prompt:\s*null/);
  assert.doesNotMatch(source, /selectedWorldSource/);
  assert.doesNotMatch(source, /worldPromptInput/);
  assert.doesNotMatch(source, /promptWorldField/);
  assert.doesNotMatch(source, /nvidia/i);
});

test("Showroom rejects the exact legacy chat completion endpoint", () => {
  const guard = nginx.match(/location\s+=\s+\/v1\/chat\/completions\s*\{([^}]*)\}/s);
  assert.ok(guard);
  assert.match(guard[1], /return\s+404\s*;/);
  assert.doesNotMatch(guard[1], /proxy_pass/);
});
