const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const start = source.indexOf("const MODEL_PRICE_REFERENCE_INPUT_TOKENS");
const end = source.indexOf("function selectedRadioValue", start);
assert.ok(start >= 0 && end > start, "model pricing helpers must remain testable");

const context = {
  normalizeProvider: (value) => String(value || "").trim().toLowerCase(),
};
vm.runInNewContext(source.slice(start, end), context);

const deepSeek = {
  provider: "openrouter",
  pricing_prompt: "0.00000063168",
  pricing_completion: "0.00000126336",
  pricing_input_cache_read: "0.000000053298",
};

assert.ok(Math.abs(context.modelReferenceTurnCost(deepSeek, 0) - 0.060830784) < 1e-12);
assert.ok(Math.abs(context.modelReferenceTurnCost(deepSeek, 0.8) - 0.016873752) < 1e-12);
assert.equal(context.modelCostTier(deepSeek), "$");
assert.equal(context.modelCostTierLabel(deepSeek), "$ · ≈ $0.017/ход (80% cached input)");
assert.match(context.modelPricingLabel(deepSeek), /\$0\.0533\/M cached input/);
assert.match(context.modelPricingLabel(deepSeek), /cold \$0\.061 · warm 80% \$0\.017/);

const withoutCacheDiscount = {
  provider: "openrouter",
  pricing_prompt: deepSeek.pricing_prompt,
  pricing_completion: deepSeek.pricing_completion,
};
assert.equal(
  context.modelReferenceTurnCost(withoutCacheDiscount, 0.8),
  context.modelReferenceTurnCost(withoutCacheDiscount, 0),
);
assert.equal(context.modelCostTier(withoutCacheDiscount), "$$$");
assert.match(context.modelCostTierLabel(withoutCacheDiscount), /без скидки кэша/);
assert.match(context.modelPricingLabel(withoutCacheDiscount), /скидка cached input не заявлена/);

assert.equal(context.modelCostTier({ ...deepSeek, is_free: true }), "");
assert.equal(context.modelCostTierLabel({ ...deepSeek, is_free: true }), "FREE");

console.log("model pricing tests passed");
