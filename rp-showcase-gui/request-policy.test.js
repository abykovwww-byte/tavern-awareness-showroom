import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./app.js", import.meta.url), "utf8");

test("dynamic Showroom API reads bypass browser caches", () => {
  assert.match(source, /fetch\(path, \{ credentials: "same-origin", cache: "no-store", \.\.\.options \}\)/);
});
