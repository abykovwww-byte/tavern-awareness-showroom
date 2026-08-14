"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {
  MAX_COMPARE,
  annotationAttemptId,
  annotationPayload,
  annotationUrl,
  buildAlignmentRows,
  captureExplanation,
  changeLineDiff,
  compactLineDiff,
  comparisonChangedFields,
  jsonChanges,
  lineDiff,
  metadataFallback,
  missingPhaseMessage,
  normalizeDetail,
  normalizeTimestamp,
  parseEmbeddedJson,
  phasePresentation,
  traceContractLabel,
  traceDetailUrl,
  traceListUrl,
} = require("./trace.js");

assert.equal(MAX_COMPARE, 4);
assert.equal(
  traceListUrl("party/a", "branch 7", 42, 30),
  "/api/parties/party%2Fa/turn-traces?branch_id=branch+7&limit=30&before=42",
);
assert.equal(traceListUrl("party_1", null, null, 30), "/api/parties/party_1/turn-traces?limit=30");
assert.equal(
  traceDetailUrl("party_1", "request/1", "branch_2"),
  "/api/parties/party_1/turn-traces/request%2F1?branch_id=branch_2",
);
assert.equal(
  annotationUrl("party_1", "request_1", null),
  "/api/parties/party_1/turn-traces/request_1/annotations",
);

const normalized = normalizeDetail({
  schema_version: "turn-trace.v1",
  trace: { request_id: "request_1" },
});
assert.equal(normalized.trace.request_id, "request_1");
assert.deepEqual(normalized.trace.phases, []);
assert.equal(normalizeTimestamp(1_720_000_000).toISOString(), "2024-07-03T09:46:40.000Z");
assert.equal(normalizeTimestamp(1_720_000_000_000).toISOString(), "2024-07-03T09:46:40.000Z");
assert.equal(normalizeTimestamp("not-a-date"), null);
assert.equal(traceContractLabel({ scenario_type: "training", rp_contract_revision: null }), "training");
assert.equal(traceContractLabel({ scenario_type: "novel", rp_contract_revision: null }), "novel");
assert.equal(
  traceContractLabel({ scenario_type: "rp", rp_contract_version: "rp-core.v1", rp_contract_revision: 0 }),
  "rp-core.v1",
);
assert.equal(
  traceContractLabel({ scenario_type: "rp", rp_contract_version: "rp-core.v2", rp_contract_revision: 6 }),
  "RP r6",
);

const revisionZero = {
  trace: {
    request_id: "request_zero",
    capture_status: "complete",
    phases: [
      { phase_key: "intent:1", alignment_key: "intent", title: "Intent", output: { kind: "talk" } },
      { phase_key: "narrator:1", alignment_key: "narrator", title: "Narrator", output: "Scene A" },
    ],
  },
};
const candidateRevision = {
  trace: {
    request_id: "request_candidate",
    capture_status: "partial",
    phases: [
      { phase_key: "intent:1", alignment_key: "intent", title: "Intent", output: { kind: "talk" } },
      { phase_key: "rp-core:pressure:1", alignment_key: "rp-core:pressure", title: "New phase", output: { trust: -1 } },
      { phase_key: "narrator:attempt:1", alignment_key: "narrator", title: "Narrator retry", output: "Scene B" },
    ],
  },
};
const aligned = buildAlignmentRows([revisionZero, candidateRevision]);
assert.deepEqual(aligned.map((row) => row.alignmentKey), ["intent", "narrator", "rp-core:pressure"]);
assert.equal(aligned.find((row) => row.alignmentKey === "narrator").phases[1].phase_key, "narrator:attempt:1");
assert.equal(aligned.find((row) => row.alignmentKey === "rp-core:pressure").phases[0], null);

const repeated = buildAlignmentRows([
  { trace: { phases: [
    { phase_key: "retry:1", alignment_key: "narrator" },
    { phase_key: "retry:2", alignment_key: "narrator" },
  ] } },
  { trace: { phases: [{ phase_key: "retry:1", alignment_key: "narrator" }] } },
]);
assert.equal(repeated.length, 2);
assert.equal(repeated[1].occurrence, 2);
assert.equal(repeated[1].phases[1], null);

assert.deepEqual(phasePresentation({ event_type: "player_input" }), {
  actor: "Пользователь",
  description: "Пользователь отправляет действие",
});
assert.equal(phasePresentation({ event_type: "narrator_attempt" }).actor, "Нарратор");
assert.deepEqual(
  phasePresentation({ event_type: "service_model_call", alignment_key: "service:relationship_extraction" }),
  {
    actor: "Служебная LLM",
    description: "Служебная LLM извлекает изменения отношений из завершённого хода",
  },
);
assert.equal(
  phasePresentation({ event_type: "service_job", alignment_key: "service_job:relationship_extraction", lane: "background" }).actor,
  "GW",
);
assert.equal(phasePresentation({ event_type: "state_delta" }).actor, "GW");
assert.equal(phasePresentation({ event_type: "unknown", title: "Понятное старое название" }).description, "Понятное старое название");

const missingAlignment = buildAlignmentRows([
  { trace: { phases: [{ phase_key: "same-key" }] } },
  { trace: { phases: [{ phase_key: "same-key" }] } },
]);
assert.equal(missingAlignment.length, 2);
assert.notEqual(missingAlignment[0].alignmentKey, missingAlignment[1].alignmentKey);

assert.equal(missingPhaseMessage({ trace: { capture_status: "complete" } }), "Не выполнялась (захват полный).");
assert.equal(
  missingPhaseMessage({ trace: { capture_status: "partial" } }),
  "Нет захвата — неизвестно, выполнялась ли эта фаза.",
);
assert.match(captureExplanation("missing"), /Нельзя определить/);

assert.deepEqual(metadataFallback({ provider: { fallback_used: true } }), {
  path: "metadata.provider.fallback_used",
  value: true,
});
assert.equal(metadataFallback({ narrative: "fallback_used=true" }), null);
assert.equal(metadataFallback({ fallback_used: false }), null);

const diff = lineDiff("alpha\nbeta", "alpha\ngamma");
assert.ok(diff.some((line) => line.type === "delete" && line.text === "beta"));
assert.ok(diff.some((line) => line.type === "insert" && line.text === "gamma"));

assert.deepEqual(parseEmbeddedJson('```json\n{"events":[]}\n```'), { events: [] });
assert.equal(parseEmbeddedJson("обычный текст"), null);
assert.deepEqual(
  jsonChanges(
    { state: { characters: { nezhan: { loyalty: 0, label: "ровно" } }, untouched: { value: 1 } } },
    { state: { characters: { nezhan: { loyalty: 12, label: "расположение" } }, untouched: { value: 1 } } },
  ),
  [
    { path: "$.state.characters.nezhan.label", operation: "replace", before: "ровно", after: "расположение" },
    { path: "$.state.characters.nezhan.loyalty", operation: "replace", before: 0, after: 12 },
  ],
);
assert.deepEqual(
  jsonChanges(["a", "b"], ["a", "c", "d"]),
  [
    { path: "$[1]", operation: "replace", before: "b", after: "c" },
    { path: "$[2]", operation: "add", before: undefined, after: "d" },
  ],
);
assert.deepEqual(
  jsonChanges(
    { raw_response: '```json\n{"events":[{"event_id":"shared_risk","evidence":"old"}]}\n```' },
    { raw_response: '```json\n{"events":[{"event_id":"shared_risk","evidence":"new"}]}\n```' },
  ),
  [{ path: "$.raw_response.events[0].evidence", operation: "replace", before: "old", after: "new" }],
);
assert.deepEqual(
  jsonChanges('{"same":true}', { same: true }),
  [{ path: "$", operation: "replace", before: '{"same":true}', after: { same: true } }],
);
assert.deepEqual(
  jsonChanges('{"same":true}', '{ "same": true }'),
  [{ path: "$", operation: "replace", before: '{"same":true}', after: '{ "same": true }' }],
);
const addedLines = changeLineDiff({ operation: "add", before: undefined, after: { added: true } });
assert.ok(addedLines.length > 0 && addedLines.every((line) => line.type === "insert"));
assert.ok(addedLines.every((line) => !line.text.includes("поле не захвачено")));
const removedLines = changeLineDiff({ operation: "remove", before: { removed: true }, after: undefined });
assert.ok(removedLines.length > 0 && removedLines.every((line) => line.type === "delete"));
assert.ok(removedLines.every((line) => !line.text.includes("поле не захвачено")));
const longBefore = Array.from({ length: 260 }, (_, index) => `line ${index}`);
const longAfter = [...longBefore];
longAfter[130] = "changed line";
const compact = compactLineDiff(longBefore.join("\n"), longAfter.join("\n"));
assert.ok(compact.some((line) => line.type === "skip" && line.count > 100));
assert.ok(compact.some((line) => line.type === "delete" && line.text === "line 130"));
assert.ok(compact.some((line) => line.type === "insert" && line.text === "changed line"));
assert.ok(compact.length < 12);

assert.deepEqual(annotationPayload("phase:1", "  Проверить факт  ", "annotation_1"), {
  annotation_id: "annotation_1",
  phase_key: "phase:1",
  body: "Проверить факт",
});
assert.equal(Object.hasOwn(annotationPayload("phase:1", "note", "annotation_2"), "author_user_id"), false);
const pendingAnnotationId = annotationAttemptId(null);
assert.equal(annotationAttemptId(pendingAnnotationId), pendingAnnotationId);
assert.deepEqual(
  comparisonChangedFields(
    { lane: "main", status: "completed", capture_status: "complete", output: "same" },
    { lane: "background", status: "failed", capture_status: "partial", output: "same" },
  ),
  ["lane", "status", "capture_status"],
);
const comparableFields = ["lane", "status", "capture_status", "input", "output", "details", "metadata", "warnings"];
assert.deepEqual(
  comparisonChangedFields(
    { lane: "main", status: "completed", capture_status: "complete", input: { a: 1 }, output: { a: 1 }, details: { a: 1 }, metadata: { a: 1 }, warnings: [] },
    { lane: "background", status: "failed", capture_status: "partial", input: { a: 2 }, output: { a: 2 }, details: { a: 2 }, metadata: { a: 2 }, warnings: ["changed"] },
  ),
  comparableFields,
);

const directory = __dirname;
const source = fs.readFileSync(path.join(directory, "trace.js"), "utf8");
const html = fs.readFileSync(path.join(directory, "trace.html"), "utf8");
const appSource = fs.readFileSync(path.join(directory, "app.js"), "utf8");
const index = fs.readFileSync(path.join(directory, "index.html"), "utf8");
const dockerfile = fs.readFileSync(path.join(directory, "Dockerfile"), "utf8");
const nginx = fs.readFileSync(path.join(directory, "nginx.conf"), "utf8");

assert.doesNotMatch(source, /\.innerHTML\b/);
assert.match(source, /textContent/);
assert.match(source, /className: "json-node"/);
assert.match(source, /text: "Исполнитель и фаза"/);
assert.match(source, /\/api\/turn-traces\/parties/);
const adminGatePosition = source.indexOf('auth.user?.role !== "admin"');
const traceLoadPosition = source.indexOf('requestJson("/api/turn-traces/parties")');
assert.ok(adminGatePosition >= 0 && adminGatePosition < traceLoadPosition);
assert.match(index, /id="traceWorkbenchLink" class="text-button hidden"/);
assert.match(appSource, /traceWorkbenchLink\.classList\.toggle\("hidden", appState\.authEnabled && !isAdmin\(\)\)/);
assert.match(html, /<main id="traceWorkspace"[^>]*tabindex="-1">/);
assert.match(html, /aria-live="polite"/);
assert.match(html, /<ol id="traceList"/);
assert.match(html, /id="compareTableHost"/);
assert.match(dockerfile, /COPY rp-light-gui\/trace\.html/);
assert.match(dockerfile, /COPY rp-light-gui\/trace\.css/);
assert.match(dockerfile, /COPY rp-light-gui\/trace\.js/);
assert.match(nginx, /location = \/trace\.html/);

console.log("light gui turn trace workbench: ok");
