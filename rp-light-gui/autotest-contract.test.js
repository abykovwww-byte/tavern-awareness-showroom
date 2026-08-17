const assert = require("node:assert/strict");
const fs = require("node:fs");

const appSource = fs.readFileSync(require.resolve("./app.js"), "utf8");
const htmlSource = fs.readFileSync(require.resolve("./index.html"), "utf8");

assert.match(
  htmlSource,
  /id="adminAutotestRevisionInput"[^>]*type="number"[^>]*min="0"[^>]*max="7"/,
  "autotest form must expose the bounded candidate revision",
);
assert.match(
  appSource,
  /const candidateRevision = revisionText === "" \? undefined : Number\(revisionText\);/,
  "an empty candidate revision must preserve source-party behavior",
);
assert.match(
  appSource,
  /rp_contract_revision: candidateRevision/,
  "autotest payload must pass the requested candidate revision",
);

console.log("light gui autotest contract: ok");
