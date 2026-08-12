"use strict";

const MAX_COMPARE = 4;
const TRACE_PAGE_SIZE = 30;
const ACTIVE_PARTY_STORAGE_KEY = "rp-light-gui-active-party";

const STATUS_LABELS = {
  completed: "Завершён",
  failed: "Ошибка",
  running: "Выполняется",
  skipped: "Пропущен",
};

const CAPTURE_LABELS = {
  complete: "Полный захват",
  partial: "Частичный захват",
  missing: "Захват отсутствует",
};

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object || {}, key);
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (!isObject(value)) return value;
  return Object.keys(value)
    .sort()
    .reduce((result, key) => {
      result[key] = stableValue(value[key]);
      return result;
    }, {});
}

function formatData(value) {
  if (typeof value === "string") return value;
  if (value === undefined) return "[поле не захвачено]";
  return JSON.stringify(stableValue(value), null, 2);
}

function normalizeTimestamp(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  const date = Number.isFinite(numeric)
    ? new Date(Math.abs(numeric) < 1_000_000_000_000 ? numeric * 1000 : numeric)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function statusLabel(status) {
  const key = String(status || "").toLowerCase();
  return STATUS_LABELS[key] || (key ? `Статус: ${key}` : "Статус не указан");
}

function captureLabel(captureStatus) {
  const key = String(captureStatus || "").toLowerCase();
  return CAPTURE_LABELS[key] || (key ? `Захват: ${key}` : "Статус захвата не указан");
}

function captureExplanation(captureStatus) {
  const key = String(captureStatus || "").toLowerCase();
  if (key === "complete") {
    return "Захват полный. Если фазы нет в трассе, она не выполнялась.";
  }
  if (key === "partial") {
    return "Захват частичный. По отсутствующим данным нельзя определить, выполнялась ли фаза.";
  }
  if (key === "missing") {
    return "Захват отсутствует. Нельзя определить, какие фазы выполнялись.";
  }
  return "Gateway не указал полноту захвата. Отсутствие фазы нельзя считать доказательством, что она не выполнялась.";
}

function missingPhaseMessage(detail) {
  const captureStatus = String(detail?.trace?.capture_status || "").toLowerCase();
  if (captureStatus === "complete") return "Не выполнялась (захват полный).";
  if (captureStatus === "partial" || captureStatus === "missing") {
    return "Нет захвата — неизвестно, выполнялась ли эта фаза.";
  }
  return "Статус захвата не указан — выполнение фазы неизвестно.";
}

function appendQuery(url, params) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  });
  const suffix = search.toString();
  return suffix ? `${url}?${suffix}` : url;
}

function traceListUrl(partyId, branchId = null, before = null, limit = TRACE_PAGE_SIZE) {
  return appendQuery(`/api/parties/${encodeURIComponent(partyId)}/turn-traces`, {
    branch_id: branchId,
    limit,
    before,
  });
}

function traceDetailUrl(partyId, requestId, branchId = null) {
  return appendQuery(
    `/api/parties/${encodeURIComponent(partyId)}/turn-traces/${encodeURIComponent(requestId)}`,
    { branch_id: branchId },
  );
}

function annotationUrl(partyId, requestId, branchId = null) {
  return appendQuery(
    `/api/parties/${encodeURIComponent(partyId)}/turn-traces/${encodeURIComponent(requestId)}/annotations`,
    { branch_id: branchId },
  );
}

function annotationPayload(phaseKey, body, annotationId) {
  return {
    annotation_id: annotationId,
    phase_key: phaseKey,
    body: String(body || "").trim(),
  };
}

function annotationAttemptId(currentId) {
  if (currentId) return currentId;
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? `annotation_${crypto.randomUUID()}`
    : `annotation_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function normalizeDetail(payload) {
  if (!isObject(payload)) return { trace: { phases: [] } };
  const trace = isObject(payload.trace) ? payload.trace : payload;
  return {
    schema_version: payload.schema_version || null,
    party: payload.party || null,
    branch: payload.branch || null,
    state_campaign_id: payload.state_campaign_id || null,
    trace: { ...trace, phases: asArray(trace.phases) },
  };
}

function buildAlignmentRows(details) {
  const rows = new Map();
  details.forEach((rawDetail, traceIndex) => {
    const detail = normalizeDetail(rawDetail);
    const occurrences = new Map();
    detail.trace.phases.forEach((phase, phaseIndex) => {
      const alignmentKey = phase.alignment_key
        ? String(phase.alignment_key)
        : `unaligned:${traceIndex}:${phaseIndex}:${String(phase.phase_key || "phase-without-key")}`;
      const occurrence = (occurrences.get(alignmentKey) || 0) + 1;
      occurrences.set(alignmentKey, occurrence);
      const rowKey = `${alignmentKey}\u0000${occurrence}`;
      if (!rows.has(rowKey)) {
        rows.set(rowKey, {
          rowKey,
          alignmentKey,
          occurrence,
          title: phase.title || alignmentKey,
          phases: Array(details.length).fill(null),
        });
      }
      rows.get(rowKey).phases[traceIndex] = phase;
    });
  });
  return Array.from(rows.values());
}

function metadataFallback(metadata) {
  if (!isObject(metadata)) return null;
  const queue = [{ path: "metadata", value: metadata }];
  const fallbackKey = /(^|_)(fallback|fallback_used|used_fallback|fallback_reason|fallback_provider)($|_)/i;
  while (queue.length) {
    const current = queue.shift();
    for (const [key, value] of Object.entries(current.value)) {
      const path = `${current.path}.${key}`;
      if (fallbackKey.test(key) && value !== false && value !== null && value !== undefined && value !== "") {
        return { path, value };
      }
      if (isObject(value)) queue.push({ path, value });
    }
  }
  return null;
}

function comparablePhase(phase) {
  if (!phase) return null;
  return {
    lane: phase.lane,
    status: phase.status,
    capture_status: phase.capture_status,
    input: phase.input,
    output: phase.output,
    details: phase.details,
    metadata: phase.metadata,
    warnings: phase.warnings,
  };
}

function comparisonChangedFields(baselinePhase, phase) {
  const fields = ["lane", "status", "capture_status", "input", "output", "details", "metadata", "warnings"];
  return fields.filter((key) => formatData(baselinePhase?.[key]) !== formatData(phase?.[key]));
}

function lineDiff(beforeValue, afterValue, maxLines = 180) {
  const before = formatData(beforeValue).split("\n");
  const after = formatData(afterValue).split("\n");
  if (before.join("\n") === after.join("\n")) {
    return before.map((text) => ({ type: "equal", text }));
  }

  if (before.length > maxLines || after.length > maxLines || before.length * after.length > maxLines * maxLines) {
    let prefix = 0;
    while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
    let suffix = 0;
    while (
      suffix < before.length - prefix &&
      suffix < after.length - prefix &&
      before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
    ) suffix += 1;
    return [
      ...before.slice(0, prefix).map((text) => ({ type: "equal", text })),
      ...before.slice(prefix, before.length - suffix).map((text) => ({ type: "delete", text })),
      ...after.slice(prefix, after.length - suffix).map((text) => ({ type: "insert", text })),
      ...before.slice(before.length - suffix).map((text) => ({ type: "equal", text })),
    ];
  }

  const table = Array.from({ length: before.length + 1 }, () => new Uint16Array(after.length + 1));
  for (let left = before.length - 1; left >= 0; left -= 1) {
    for (let right = after.length - 1; right >= 0; right -= 1) {
      table[left][right] = before[left] === after[right]
        ? table[left + 1][right + 1] + 1
        : Math.max(table[left + 1][right], table[left][right + 1]);
    }
  }

  const result = [];
  let left = 0;
  let right = 0;
  while (left < before.length && right < after.length) {
    if (before[left] === after[right]) {
      result.push({ type: "equal", text: before[left] });
      left += 1;
      right += 1;
    } else if (table[left + 1][right] >= table[left][right + 1]) {
      result.push({ type: "delete", text: before[left] });
      left += 1;
    } else {
      result.push({ type: "insert", text: after[right] });
      right += 1;
    }
  }
  while (left < before.length) result.push({ type: "delete", text: before[left++] });
  while (right < after.length) result.push({ type: "insert", text: after[right++] });
  return result;
}

const exported = {
  MAX_COMPARE,
  annotationPayload,
  annotationAttemptId,
  annotationUrl,
  buildAlignmentRows,
  captureExplanation,
  comparablePhase,
  comparisonChangedFields,
  formatData,
  lineDiff,
  metadataFallback,
  missingPhaseMessage,
  normalizeTimestamp,
  normalizeDetail,
  traceDetailUrl,
  traceListUrl,
};

if (typeof module !== "undefined" && module.exports) module.exports = exported;

if (typeof document !== "undefined") {
  const state = {
    parties: [],
    branches: [],
    partyId: null,
    branchId: null,
    scope: null,
    traces: [],
    nextBefore: null,
    activeRequestId: null,
    details: new Map(),
    compareIds: new Set(),
    view: "single",
    loadGeneration: 0,
    autoRefreshTimer: null,
  };

  const dom = {};

  function element(tagName, options = {}, children = []) {
    const node = document.createElement(tagName);
    if (options.className) node.className = options.className;
    if (options.text !== undefined) node.textContent = String(options.text);
    if (options.type) node.type = options.type;
    if (options.value !== undefined) node.value = String(options.value);
    if (options.title) node.title = options.title;
    Object.entries(options.attrs || {}).forEach(([name, value]) => {
      if (value !== null && value !== undefined) node.setAttribute(name, String(value));
    });
    asArray(children).forEach((child) => {
      if (child !== null && child !== undefined) node.append(child);
    });
    return node;
  }

  function badge(text, tone = "") {
    return element("span", { className: `badge ${tone}`.trim(), text });
  }

  function setPageStatus(message = "", tone = "") {
    dom.pageStatus.textContent = message;
    dom.pageStatus.className = `page-status ${tone}`.trim();
  }

  function formatDate(value) {
    const date = normalizeTimestamp(value);
    if (!date) return value ? String(value) : "—";
    return new Intl.DateTimeFormat("ru-RU", {
      dateStyle: "short",
      timeStyle: "medium",
    }).format(date);
  }

  function makeDefinitionList(node, rows) {
    node.replaceChildren();
    rows.forEach(([term, value]) => {
      node.append(element("dt", { text: term }), element("dd", { text: value ?? "—" }));
    });
  }

  async function requestJson(path, options = {}) {
    const response = await fetch(path, options);
    const text = await response.text();
    let payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = { detail: text };
      }
    }
    if (!response.ok) {
      if (response.status === 401) showAuthenticationRequired();
      const detail = Array.isArray(payload.detail)
        ? payload.detail.map((item) => item?.msg || String(item)).join("; ")
        : payload.detail;
      const error = new Error(typeof detail === "string" ? detail : `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function readAuth() {
    const response = await fetch("/api/auth/me");
    if (response.status === 404) return { auth_enabled: false, authenticated: true, user: null };
    const text = await response.text();
    const payload = text ? JSON.parse(text) : {};
    if (!response.ok) return { auth_enabled: true, authenticated: false, user: null };
    return payload;
  }

  function cacheDom() {
    [
      "accountLabel", "authNotice", "traceWorkspace", "refreshButton", "partySelect", "branchSelect",
      "scopeMeta", "pageStatus", "requestCount", "traceSearch", "traceStatusFilter", "traceCaptureFilter",
      "traceList", "loadMoreButton", "singleViewButton", "compareViewButton", "clearCompareButton",
      "compareSelectionStatus", "emptyStage", "detailView", "compareView", "traceRequestId", "traceTitle",
      "traceBadges", "traceMeta", "captureNotice", "traceWarnings", "phaseLanes", "compareTableHost",
    ].forEach((id) => { dom[id] = document.getElementById(id); });
  }

  function bindEvents() {
    dom.refreshButton.addEventListener("click", refreshCurrentScope);
    dom.partySelect.addEventListener("change", () => changeParty(dom.partySelect.value));
    dom.branchSelect.addEventListener("change", () => changeBranch(dom.branchSelect.value || null));
    dom.traceSearch.addEventListener("input", renderTraceList);
    dom.traceStatusFilter.addEventListener("change", renderTraceList);
    dom.traceCaptureFilter.addEventListener("change", renderTraceList);
    dom.loadMoreButton.addEventListener("click", () => loadTraceList({ append: true }));
    dom.singleViewButton.addEventListener("click", () => setView("single"));
    dom.compareViewButton.addEventListener("click", () => setView("compare"));
    dom.clearCompareButton.addEventListener("click", clearCompare);
  }

  function showAuthenticationRequired() {
    if (!dom.traceWorkspace || !dom.authNotice) return;
    dom.traceWorkspace.classList.add("hidden");
    dom.authNotice.classList.remove("hidden");
  }

  function renderPartyOptions() {
    const fragment = document.createDocumentFragment();
    if (!state.parties.length) {
      const option = element("option", { text: "Нет доступных партий", value: "" });
      option.disabled = true;
      option.selected = true;
      fragment.append(option);
    } else {
      state.parties.forEach((party) => {
        const option = element("option", { text: party.title || party.id, value: party.id });
        option.selected = party.id === state.partyId;
        fragment.append(option);
      });
    }
    dom.partySelect.replaceChildren(fragment);
    dom.partySelect.disabled = !state.parties.length;
  }

  function renderBranchOptions() {
    const fragment = document.createDocumentFragment();
    const main = element("option", { text: "Основная линия", value: "" });
    main.selected = !state.branchId;
    fragment.append(main);
    state.branches.forEach((branch) => {
      const option = element("option", { text: branch.label || branch.id, value: branch.id });
      option.selected = branch.id === state.branchId;
      fragment.append(option);
    });
    dom.branchSelect.replaceChildren(fragment);
    dom.branchSelect.disabled = !state.partyId;
  }

  function renderScope() {
    const selectedParty = state.scope?.party || state.parties.find((party) => party.id === state.partyId) || null;
    const selectedBranch = state.scope?.branch || state.branches.find((branch) => branch.id === state.branchId) || null;
    makeDefinitionList(dom.scopeMeta, [
      ["Сценарий", selectedParty?.scenario_type],
      ["Контракт", selectedParty?.rp_contract_version],
      ["Ревизия RP", selectedBranch?.rp_contract_revision ?? selectedParty?.rp_contract_revision],
      ["State", state.scope?.state_campaign_id || selectedBranch?.state_campaign_id || selectedParty?.state_campaign_id],
    ]);
  }

  function resetTraceScope() {
    state.scope = null;
    state.traces = [];
    state.nextBefore = null;
    state.activeRequestId = null;
    state.details.clear();
    state.compareIds.clear();
    state.view = "single";
    renderTraceList();
    renderStage();
    renderCompareControls();
  }

  async function loadBranches(partyId) {
    const payload = await requestJson(`/api/turn-traces/parties/${encodeURIComponent(partyId)}/branches`);
    if (state.partyId !== partyId) return;
    state.branches = asArray(payload.branches);
  }

  async function changeParty(partyId, preferredBranchId = null, preferredRequestId = null) {
    if (!partyId) return;
    state.partyId = partyId;
    state.branchId = null;
    state.branches = [];
    resetTraceScope();
    renderPartyOptions();
    renderBranchOptions();
    renderScope();
    localStorage.setItem(ACTIVE_PARTY_STORAGE_KEY, partyId);
    setPageStatus("Загружаю ветки и трассы…");
    try {
      await loadBranches(partyId);
      const preferredBranchMissing = Boolean(
        preferredBranchId && !state.branches.some((branch) => branch.id === preferredBranchId),
      );
      if (preferredBranchId && !preferredBranchMissing) {
        state.branchId = preferredBranchId;
      }
      renderBranchOptions();
      await loadTraceList({ append: false });
      if (preferredBranchMissing) {
        setPageStatus("Ветка из ссылки недоступна. Показана основная линия; запрос из другой области не открыт.", "error");
        return;
      }
      if (preferredRequestId) await openTrace(preferredRequestId);
    } catch (error) {
      setPageStatus(`Не удалось загрузить область: ${error.message}`, "error");
    }
  }

  async function changeBranch(branchId) {
    state.branchId = branchId || null;
    resetTraceScope();
    renderBranchOptions();
    renderScope();
    setPageStatus("Загружаю трассы выбранной ветки…");
    try {
      await loadTraceList({ append: false });
    } catch (error) {
      setPageStatus(`Не удалось загрузить трассы: ${error.message}`, "error");
    }
  }

  async function loadTraceList({ append = false, quiet = false } = {}) {
    if (!state.partyId) return;
    const generation = ++state.loadGeneration;
    const before = append ? state.nextBefore : null;
    if (!quiet) setPageStatus(append ? "Загружаю более ранние запросы…" : "Обновляю список запросов…");
    dom.loadMoreButton.disabled = true;
    try {
      const payload = await requestJson(traceListUrl(state.partyId, state.branchId, before));
      if (generation !== state.loadGeneration) return;
      const incoming = asArray(payload.traces);
      if (append) {
        const known = new Set(state.traces.map((trace) => trace.request_id));
        state.traces.push(...incoming.filter((trace) => !known.has(trace.request_id)));
      } else {
        state.traces = incoming;
      }
      state.nextBefore = payload.next_before ?? null;
      state.scope = {
        schema_version: payload.schema_version || null,
        party: payload.party || null,
        branch: payload.branch || null,
        state_campaign_id: payload.state_campaign_id || null,
      };
      renderScope();
      renderTraceList();
      renderCompareControls();
      updateLocation();
      scheduleAutoRefresh();
      if (!quiet) setPageStatus(`Загружено запросов: ${state.traces.length}`, "success");
    } catch (error) {
      if (generation === state.loadGeneration) {
        setPageStatus(`Обновление не удалось. Уже загруженные данные сохранены: ${error.message}`, "error");
      }
      throw error;
    } finally {
      dom.loadMoreButton.disabled = false;
    }
  }

  function filteredTraces() {
    const query = dom.traceSearch.value.trim().toLowerCase();
    const status = dom.traceStatusFilter.value;
    const capture = dom.traceCaptureFilter.value;
    return state.traces.filter((trace) => {
      if (status && trace.status !== status) return false;
      if (capture && trace.capture_status !== capture) return false;
      if (!query) return true;
      return [trace.request_id, trace.turn_id, trace.party_turn, trace.preview, trace.status]
        .some((value) => String(value ?? "").toLowerCase().includes(query));
    });
  }

  function traceHeading(trace) {
    if (trace.party_turn !== null && trace.party_turn !== undefined) return `Ход ${trace.party_turn}`;
    if (trace.turn_id !== null && trace.turn_id !== undefined) return `Turn ${trace.turn_id}`;
    return "Запрос без зафиксированного хода";
  }

  function renderTraceList() {
    if (!dom.traceList) return;
    const traces = filteredTraces();
    const fragment = document.createDocumentFragment();
    traces.forEach((trace) => {
      const item = element("li", {
        className: `trace-list-item ${trace.request_id === state.activeRequestId ? "selected" : ""}`.trim(),
      });
      const open = element("button", {
        className: "trace-open",
        type: "button",
        attrs: { "aria-label": `Открыть трассу ${trace.request_id}` },
      });
      open.append(
        element("strong", { text: traceHeading(trace) }),
        element("small", { text: trace.request_id || "request_id не указан" }),
        element("small", {
          text: [
            statusLabel(trace.status),
            captureLabel(trace.capture_status),
            `фаз: ${trace.phase_count ?? "—"}`,
            trace.rp_contract_revision !== undefined ? `RP r${trace.rp_contract_revision}` : null,
          ].filter(Boolean).join(" · "),
        }),
      );
      if (trace.preview) open.append(element("span", { className: "trace-preview", text: trace.preview }));
      open.addEventListener("click", () => openTrace(trace.request_id));

      const compareLabel = element("label", {
        className: "compare-check",
        title: "Добавить запрос к сравнению",
      });
      const checkbox = element("input", {
        type: "checkbox",
        attrs: { "aria-label": `Сравнить запрос ${trace.request_id}` },
      });
      checkbox.checked = state.compareIds.has(trace.request_id);
      checkbox.disabled = !checkbox.checked && state.compareIds.size >= MAX_COMPARE;
      checkbox.addEventListener("change", () => toggleCompare(trace.request_id, checkbox.checked));
      compareLabel.append(checkbox);
      item.append(open, compareLabel);
      fragment.append(item);
    });
    if (!traces.length) {
      fragment.append(element("li", {
        className: "empty-phases",
        text: state.traces.length ? "Нет запросов по выбранным фильтрам." : "В этой области трасс пока нет.",
      }));
    }
    dom.traceList.replaceChildren(fragment);
    dom.requestCount.textContent = String(traces.length);
    dom.loadMoreButton.classList.toggle("hidden", state.nextBefore === null || state.nextBefore === undefined);
  }

  function toggleCompare(requestId, selected) {
    if (selected && state.compareIds.size < MAX_COMPARE) state.compareIds.add(requestId);
    if (!selected) state.compareIds.delete(requestId);
    renderTraceList();
    renderCompareControls();
    if (state.view === "compare") {
      if (state.compareIds.size >= 2) renderComparison();
      else setView("single");
    }
  }

  function clearCompare() {
    state.compareIds.clear();
    setView("single");
    renderTraceList();
    renderCompareControls();
  }

  function renderCompareControls() {
    const count = state.compareIds.size;
    dom.compareViewButton.disabled = count < 2;
    dom.clearCompareButton.classList.toggle("hidden", count === 0);
    dom.compareSelectionStatus.textContent = count
      ? `Для сравнения выбрано ${count} из ${MAX_COMPARE}.`
      : "Сравнение не выбрано.";
  }

  async function fetchTrace(requestId) {
    if (state.details.has(requestId)) return state.details.get(requestId);
    const partyId = state.partyId;
    const branchId = state.branchId;
    const payload = await requestJson(traceDetailUrl(partyId, requestId, branchId));
    if (state.partyId !== partyId || state.branchId !== branchId) {
      throw new Error("Область трассы изменилась во время загрузки");
    }
    const detail = normalizeDetail(payload);
    state.details.set(requestId, detail);
    return detail;
  }

  async function openTrace(requestId) {
    if (!requestId) return;
    state.activeRequestId = requestId;
    state.view = "single";
    renderTraceList();
    renderStage();
    if (!state.details.has(requestId)) renderStageLoading(requestId);
    updateLocation(requestId);
    setPageStatus(`Загружаю ${requestId}…`);
    try {
      const detail = await fetchTrace(requestId);
      if (state.activeRequestId !== requestId) return;
      renderDetail(detail);
      setPageStatus("Трасса загружена", "success");
    } catch (error) {
      if (state.activeRequestId === requestId) {
        setPageStatus(`Не удалось загрузить трассу: ${error.message}`, "error");
        renderStageError(error.message);
      }
    }
  }

  function setView(view) {
    if (view === "compare" && state.compareIds.size < 2) return;
    state.view = view;
    renderStage();
    if (view === "compare") renderComparison();
  }

  function renderStage() {
    const comparing = state.view === "compare";
    dom.singleViewButton.classList.toggle("active", !comparing);
    dom.singleViewButton.setAttribute("aria-pressed", String(!comparing));
    dom.compareViewButton.classList.toggle("active", comparing);
    dom.compareViewButton.setAttribute("aria-pressed", String(comparing));
    dom.compareView.classList.toggle("hidden", !comparing);
    dom.detailView.classList.toggle("hidden", comparing || !state.activeRequestId);
    dom.emptyStage.classList.toggle("hidden", comparing || Boolean(state.activeRequestId));
    if (!comparing && state.activeRequestId && state.details.has(state.activeRequestId)) {
      renderDetail(state.details.get(state.activeRequestId));
    }
  }

  function renderStageError(message) {
    dom.detailView.classList.remove("hidden");
    dom.emptyStage.classList.add("hidden");
    dom.traceRequestId.textContent = state.activeRequestId || "";
    dom.traceTitle.textContent = "Трасса недоступна";
    dom.traceBadges.replaceChildren(badge("Ошибка загрузки", "failed"));
    dom.traceMeta.replaceChildren();
    dom.captureNotice.className = "capture-notice missing";
    dom.captureNotice.textContent = message;
    dom.traceWarnings.replaceChildren();
    dom.phaseLanes.replaceChildren();
  }

  function renderStageLoading(requestId) {
    dom.detailView.classList.remove("hidden");
    dom.emptyStage.classList.add("hidden");
    dom.traceRequestId.textContent = requestId;
    dom.traceTitle.textContent = "Загрузка трассы";
    dom.traceBadges.replaceChildren(badge("Загрузка", "running"));
    dom.traceMeta.replaceChildren();
    dom.captureNotice.className = "capture-notice";
    dom.captureNotice.textContent = "Данные запроса ещё не получены.";
    dom.traceWarnings.replaceChildren();
    dom.phaseLanes.replaceChildren();
  }

  function makeDataSection(label, phase, key) {
    const details = element("details", { className: "data-section" });
    details.append(element("summary", { text: label }));
    if (!hasOwn(phase, key)) {
      details.append(element("div", { className: "data-empty", text: "Поле не захвачено." }));
    } else {
      details.append(element("pre", { text: formatData(phase[key]) }));
    }
    return details;
  }

  function phaseCaptureCopy(phase) {
    const status = String(phase.capture_status || "").toLowerCase();
    if (status === "complete") return "Данные этой фазы захвачены полностью.";
    if (status === "partial") return "Данные этой фазы захвачены частично; отсутствие поля не означает, что значения не было.";
    if (status === "missing") return "Данные этой фазы не захвачены.";
    return "Полнота захвата этой фазы не указана.";
  }

  function makeAnnotationNode(annotation) {
    const item = element("li", { className: "annotation" });
    const meta = element("div", { className: "annotation-meta" });
    meta.append(
      element("span", { text: annotation.author_user_id || "Автор не указан" }),
      element("time", { text: formatDate(annotation.created_at), attrs: { datetime: annotation.created_at || "" } }),
    );
    item.append(meta, element("p", { text: annotation.body || "" }));
    return item;
  }

  function makeAnnotationSection(detail, phase) {
    const section = element("section", { className: "annotation-section" });
    const annotations = asArray(phase.annotations);
    const heading = element("div", { className: "annotation-heading" });
    heading.append(
      element("h4", { text: "Комментарии" }),
      element("span", { className: "annotation-count", text: String(annotations.length) }),
    );
    section.append(heading);
    if (annotations.length) {
      const list = element("ol", { className: "annotation-list" });
      annotations.forEach((annotation) => list.append(makeAnnotationNode(annotation)));
      section.append(list);
    } else {
      section.append(element("p", { className: "phase-status-copy", text: "Комментариев пока нет." }));
    }

    if (phase.phase_key) {
      const form = element("form", { className: "annotation-form" });
      const label = element("label");
      label.append(
        element("span", { text: "Новый комментарий" }),
      );
      const textarea = element("textarea", {
        attrs: {
          maxlength: "4000",
          required: "required",
          placeholder: "Наблюдение по этой фазе",
          "aria-label": `Комментарий к фазе ${phase.phase_key}`,
        },
      });
      label.append(textarea);
      const submit = element("button", { className: "button secondary", type: "submit", text: "Добавить комментарий" });
      const status = element("span", { className: "phase-status-copy", attrs: { role: "status", "aria-live": "polite" } });
      form.append(label, status, submit);
      let pendingAnnotationId = null;
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const body = textarea.value.trim();
        if (!body) return;
        submit.disabled = true;
        textarea.disabled = true;
        status.textContent = "Сохраняю…";
        try {
          pendingAnnotationId = annotationAttemptId(pendingAnnotationId);
          const response = await requestJson(
            annotationUrl(state.partyId, detail.trace.request_id, state.branchId),
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(annotationPayload(phase.phase_key, body, pendingAnnotationId)),
            },
          );
          if (response.annotation && !annotations.some((item) => item.id === response.annotation.id)) {
            annotations.push(response.annotation);
            phase.annotations = annotations;
          }
          textarea.value = "";
          pendingAnnotationId = null;
          state.details.set(detail.trace.request_id, detail);
          renderDetail(detail);
          setPageStatus(response.duplicate ? "Комментарий уже был сохранён" : "Комментарий добавлен", "success");
        } catch (error) {
          status.textContent = `Не удалось сохранить: ${error.message}`;
        } finally {
          submit.disabled = false;
          textarea.disabled = false;
        }
      });
      section.append(form);
    }
    return section;
  }

  function makePhaseCard(detail, phase, index) {
    const status = String(phase.status || "").toLowerCase();
    const card = element("article", { className: `phase-card ${status}`.trim() });
    const header = element("header", { className: "phase-heading" });
    const main = element("div", { className: "phase-heading-main" });
    const names = element("div");
    names.append(
      element("h4", { className: "phase-title", text: phase.title || phase.phase_key || "Фаза без названия" }),
      element("p", { className: "phase-key", text: phase.phase_key || "phase_key не указан" }),
    );
    main.append(element("span", { className: "phase-index", text: index + 1 }), names);
    const badges = element("div", { className: "badge-row" });
    badges.append(badge(statusLabel(phase.status), status), badge(captureLabel(phase.capture_status), phase.capture_status));
    if (phase.alignment_key) badges.append(badge(`align: ${phase.alignment_key}`, "info"));
    const fallback = metadataFallback(phase.metadata);
    if (fallback) badges.append(badge("Fallback по metadata", "settling"));
    header.append(main, badges);
    card.append(header, element("p", { className: "phase-status-copy", text: phaseCaptureCopy(phase) }));
    if (fallback) {
      card.append(element("p", {
        className: "fallback-note",
        text: `Fallback зафиксирован только в ${fallback.path}: ${formatData(fallback.value)}`,
      }));
    }
    card.append(
      makeDataSection("Вход", phase, "input"),
      makeDataSection("Выход", phase, "output"),
      makeDataSection("Детали Gateway", phase, "details"),
      makeDataSection("Метаданные", phase, "metadata"),
    );
    if (asArray(phase.warnings).length) {
      const warnings = element("details", { className: "data-section" });
      warnings.append(element("summary", { text: `Предупреждения (${phase.warnings.length})` }));
      warnings.append(element("pre", { text: formatData(phase.warnings) }));
      card.append(warnings);
    }
    card.append(makeAnnotationSection(detail, phase));
    return card;
  }

  function laneLabel(lane) {
    if (lane === "main") return "Основной поток";
    if (lane === "background") return "Фоновые задачи";
    if (lane === "unspecified") return "Канал не указан";
    return `Канал: ${lane}`;
  }

  function renderLanes(detail) {
    const phases = asArray(detail.trace.phases);
    dom.phaseLanes.replaceChildren();
    if (!phases.length) {
      const complete = detail.trace.capture_status === "complete";
      dom.phaseLanes.append(element("div", {
        className: "empty-phases",
        text: complete
          ? "Фазы не выполнялись (захват полный)."
          : "Фазы не показаны. Из-за неполного или отсутствующего захвата неизвестно, выполнялись ли они.",
      }));
      return;
    }

    const lanes = new Map();
    phases.forEach((phase, index) => {
      const lane = String(phase.lane || "unspecified");
      if (!lanes.has(lane)) lanes.set(lane, []);
      lanes.get(lane).push({ phase, index });
    });
    const orderedLanes = Array.from(lanes.keys()).sort((left, right) => {
      const rank = (lane) => lane === "main" ? 0 : lane === "background" ? 1 : 2;
      return rank(left) - rank(right);
    });
    orderedLanes.forEach((laneName) => {
      const lane = element("section", { className: `lane ${laneName}`.trim() });
      lane.append(element("h4", { className: "lane-title", text: laneLabel(laneName) }));
      const list = element("ol", { className: "phase-list" });
      lanes.get(laneName).forEach(({ phase, index }) => {
        const item = element("li");
        item.append(makePhaseCard(detail, phase, index));
        list.append(item);
      });
      lane.append(list);
      dom.phaseLanes.append(lane);
    });
  }

  function renderTraceWarnings(trace) {
    dom.traceWarnings.replaceChildren();
    const warnings = asArray(trace.warnings);
    const omissions = asArray(trace.omissions);
    if (!warnings.length && !omissions.length) {
      dom.traceWarnings.classList.add("hidden");
      return;
    }
    dom.traceWarnings.classList.remove("hidden");
    if (warnings.length) {
      dom.traceWarnings.append(element("strong", { text: "Предупреждения" }));
      const list = element("ul");
      warnings.forEach((warning) => list.append(element("li", { text: formatData(warning) })));
      dom.traceWarnings.append(list);
    }
    if (omissions.length) {
      dom.traceWarnings.append(element("strong", { text: "Что не было захвачено" }));
      const list = element("ul");
      omissions.forEach((omission) => list.append(element("li", { text: formatData(omission) })));
      dom.traceWarnings.append(list);
    }
  }

  function renderDetail(rawDetail) {
    const detail = normalizeDetail(rawDetail);
    const trace = detail.trace;
    dom.emptyStage.classList.add("hidden");
    dom.compareView.classList.add("hidden");
    dom.detailView.classList.remove("hidden");
    dom.traceRequestId.textContent = trace.request_id || "request_id не указан";
    dom.traceTitle.textContent = traceHeading(trace);
    dom.traceBadges.replaceChildren(
      badge(statusLabel(trace.status), trace.status),
      badge(captureLabel(trace.capture_status), trace.capture_status),
    );
    if (trace.settling) dom.traceBadges.append(badge("Фоновые задачи завершаются", "settling"));
    const metaRows = [
      ["request_id", trace.request_id],
      ["turn_id", trace.turn_id],
      ["Номер хода", trace.party_turn],
      ["Создан", formatDate(trace.created_at)],
      [trace.completed_at !== undefined ? "Завершён" : "Обновлён", formatDate(trace.completed_at ?? trace.updated_at)],
      ["Фаз", trace.phases.length],
      ["Ветка", detail.branch?.label || (state.branchId ? state.branchId : "Основная линия")],
      ["State", detail.state_campaign_id],
    ];
    if (trace.rp_contract_revision !== undefined) {
      metaRows.splice(3, 0, ["Ревизия RP запроса", trace.rp_contract_revision]);
    }
    makeDefinitionList(dom.traceMeta, metaRows);
    dom.captureNotice.className = `capture-notice ${trace.capture_status || "missing"}`;
    dom.captureNotice.textContent = captureExplanation(trace.capture_status);
    renderTraceWarnings(trace);
    renderLanes(detail);
  }

  function makeDiffBlock(before, after) {
    const block = element("div", { className: "diff-block" });
    lineDiff(before, after).forEach((part) => {
      const prefix = part.type === "insert" ? "+" : part.type === "delete" ? "−" : " ";
      const line = element("span", { className: `diff-line ${part.type}`.trim() });
      line.append(element("span", { text: prefix }));
      const content = element(part.type === "insert" ? "ins" : part.type === "delete" ? "del" : "span", {
        text: part.text || " ",
      });
      line.append(content);
      block.append(line);
    });
    return block;
  }

  function makeComparisonCell(detail, phase, baselinePhase, isBaseline) {
    const cell = element("div", { className: "comparison-cell" });
    if (!phase) {
      cell.append(element("span", { className: "missing-label", text: missingPhaseMessage(detail) }));
      return cell;
    }
    cell.append(
      element("strong", { text: phase.title || phase.phase_key || "Фаза" }),
      element("span", { className: "phase-key", text: phase.phase_key || "phase_key не указан" }),
    );
    const badges = element("div", { className: "badge-row" });
    badges.append(badge(statusLabel(phase.status), phase.status), badge(captureLabel(phase.capture_status), phase.capture_status));
    cell.append(badges);

    const phaseData = element("details", { className: "diff-details" });
    phaseData.append(element("summary", { text: "Данные фазы" }));
    phaseData.append(element("pre", { text: formatData(comparablePhase(phase)) }));
    cell.append(phaseData);

    if (isBaseline) {
      cell.append(element("span", { className: "same-label", text: "База выравнивания" }));
      return cell;
    }
    if (!baselinePhase) {
      cell.append(element("span", { className: "change-label", text: "В базовом запросе фаза отсутствует" }));
      return cell;
    }

    const changed = comparisonChangedFields(baselinePhase, phase);
    cell.append(element("span", {
      className: changed.length ? "change-label" : "same-label",
      text: changed.length ? `Различаются поля: ${changed.join(", ")}` : "Совпадает с базой",
    }));
    changed.forEach((key) => {
      const details = element("details", { className: "diff-details" });
      details.append(element("summary", { text: `Diff: ${key}` }), makeDiffBlock(baselinePhase[key], phase[key]));
      cell.append(details);
    });
    return cell;
  }

  async function renderComparison() {
    if (state.compareIds.size < 2) return;
    const ids = Array.from(state.compareIds);
    setPageStatus("Загружаю выбранные трассы для сравнения…");
    dom.compareTableHost.replaceChildren(element("p", { className: "empty-phases", text: "Загрузка сравнения…" }));
    try {
      const details = await Promise.all(ids.map(fetchTrace));
      if (state.view !== "compare") return;
      const rows = buildAlignmentRows(details);
      const table = element("table", { className: "compare-table" });
      table.append(element("caption", { className: "visually-hidden", text: "Сравнение фаз выбранных трасс" }));
      const head = element("thead");
      const headRow = element("tr");
      headRow.append(element("th", { text: "alignment_key", attrs: { scope: "col" } }));
      details.forEach((detail) => {
        const header = element("th", { attrs: { scope: "col" } });
        header.append(
          element("span", { className: "compare-request", text: detail.trace.request_id }),
          element("small", {
            text: detail.trace.rp_contract_revision !== undefined
              ? `${traceHeading(detail.trace)} · RP r${detail.trace.rp_contract_revision}`
              : traceHeading(detail.trace),
          }),
        );
        headRow.append(header);
      });
      head.append(headRow);
      const body = element("tbody");
      rows.forEach((row) => {
        const tableRow = element("tr");
        const rowHeader = element("th", { attrs: { scope: "row" } });
        rowHeader.append(
          element("span", { text: row.title }),
          element("span", {
            className: "alignment-label",
            text: row.occurrence > 1 ? `${row.alignmentKey} · #${row.occurrence}` : row.alignmentKey,
          }),
        );
        tableRow.append(rowHeader);
        const baselineIndex = row.phases.findIndex(Boolean);
        const baselinePhase = baselineIndex >= 0 ? row.phases[baselineIndex] : null;
        row.phases.forEach((phase, index) => {
          const cell = element("td");
          cell.append(makeComparisonCell(details[index], phase, baselinePhase, index === baselineIndex));
          tableRow.append(cell);
        });
        body.append(tableRow);
      });
      if (!rows.length) {
        const row = element("tr");
        const cell = element("td", {
          text: "В выбранных трассах нет захваченных фаз.",
          attrs: { colspan: String(details.length + 1) },
        });
        row.append(cell);
        body.append(row);
      }
      table.append(head, body);
      dom.compareTableHost.replaceChildren(table);
      setPageStatus("Сравнение построено", "success");
    } catch (error) {
      dom.compareTableHost.replaceChildren(element("p", {
        className: "empty-phases",
        text: `Не удалось построить сравнение: ${error.message}`,
      }));
      setPageStatus(`Сравнение недоступно: ${error.message}`, "error");
    }
  }

  async function refreshCurrentScope() {
    if (!state.partyId) return;
    dom.refreshButton.disabled = true;
    const currentRequest = state.activeRequestId;
    const previousView = state.view;
    try {
      await loadTraceList({ append: false });
      if (currentRequest) {
        state.details.delete(currentRequest);
        await openTrace(currentRequest);
      }
      if (previousView === "compare" && state.compareIds.size >= 2) {
        Array.from(state.compareIds).forEach((requestId) => state.details.delete(requestId));
        state.view = "compare";
        renderStage();
        await renderComparison();
      }
    } catch {
      // loadTraceList reports the precise error and preserves already loaded data.
    } finally {
      dom.refreshButton.disabled = false;
    }
  }

  function scheduleAutoRefresh() {
    if (state.autoRefreshTimer) window.clearTimeout(state.autoRefreshTimer);
    const pending = state.traces.some((trace) => trace.settling || trace.status === "running");
    if (!pending) return;
    state.autoRefreshTimer = window.setTimeout(() => {
      const activeRequestId = state.activeRequestId;
      loadTraceList({ append: false, quiet: true })
        .then(async () => {
          if (activeRequestId && state.view === "single" && state.activeRequestId === activeRequestId) {
            state.details.delete(activeRequestId);
            await openTrace(activeRequestId);
          }
        })
        .catch(() => {});
    }, 5000);
  }

  function updateLocation(requestId = state.activeRequestId) {
    if (!state.partyId) return;
    const params = new URLSearchParams();
    params.set("party_id", state.partyId);
    if (state.branchId) params.set("branch_id", state.branchId);
    if (requestId) params.set("request_id", requestId);
    window.history.replaceState(null, "", `/trace.html?${params.toString()}`);
  }

  async function boot() {
    cacheDom();
    bindEvents();
    try {
      const auth = await readAuth();
      if (auth.auth_enabled !== false && !auth.authenticated) {
        dom.traceWorkspace.classList.add("hidden");
        dom.authNotice.classList.remove("hidden");
        return;
      }
      if (auth.user) dom.accountLabel.textContent = `${auth.user.username} · ${auth.user.role}`;
      const payload = await requestJson("/api/turn-traces/parties");
      state.parties = asArray(payload.parties);
      const params = new URLSearchParams(window.location.search);
      const requestedPartyId = params.get("party_id");
      const savedPartyId = localStorage.getItem(ACTIVE_PARTY_STORAGE_KEY);
      const requestedParty = state.parties.find((party) => party.id === requestedPartyId) || null;
      const initialParty = requestedParty
        || state.parties.find((party) => party.id === savedPartyId)
        || state.parties[0]
        || null;
      renderPartyOptions();
      renderBranchOptions();
      if (!initialParty) {
        renderScope();
        setPageStatus("Доступных партий нет. Создайте партию в Light GUI.");
        return;
      }
      const requestedScopeUnavailable = Boolean(requestedPartyId && !requestedParty);
      await changeParty(
        initialParty.id,
        requestedScopeUnavailable ? null : params.get("branch_id"),
        requestedScopeUnavailable ? null : params.get("request_id"),
      );
      if (requestedScopeUnavailable) {
        setPageStatus("Партия из ссылки недоступна. Открыта доступная вам партия; запрос из исходной области не загружался.", "error");
      }
    } catch (error) {
      setPageStatus(`Не удалось открыть экран трасс: ${error.message}`, "error");
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
}
