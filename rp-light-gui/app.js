const appState = {
  authEnabled: true,
  currentUser: null,
  worldpacks: [],
  modelProfiles: [],
  parties: [],
  activeParty: null,
  partyState: null,
  contextEstimate: null,
  memory: null,
  characters: null,
  journal: null,
  promptPreview: null,
  history: null,
  chatArchiveExpanded: false,
  proposals: [],
  busy: false,
  busyText: "",
  pendingMessages: {},
  adminUsers: [],
  adminApiKeys: [],
};

const els = {
  loginScreen: document.querySelector("#loginScreen"),
  loginForm: document.querySelector("#loginForm"),
  loginUsername: document.querySelector("#loginUsername"),
  loginPassword: document.querySelector("#loginPassword"),
  accountStrip: document.querySelector("#accountStrip"),
  currentUserLabel: document.querySelector("#currentUserLabel"),
  logoutButton: document.querySelector("#logoutButton"),
  partyList: document.querySelector("#partyList"),
  activeWorld: document.querySelector("#activeWorld"),
  activePartyTitle: document.querySelector("#activePartyTitle"),
  gatewayDot: document.querySelector("#gatewayDot"),
  gatewayStatus: document.querySelector("#gatewayStatus"),
  toolsButton: document.querySelector("#toolsButton"),
  closeInspectorButton: document.querySelector("#closeInspectorButton"),
  drawerBackdrop: document.querySelector("#drawerBackdrop"),
  historyControls: document.querySelector("#historyControls"),
  chatLog: document.querySelector("#chatLog"),
  messageStatus: document.querySelector("#messageStatus"),
  messageForm: document.querySelector("#messageForm"),
  messageInput: document.querySelector("#messageInput"),
  messageSubmit: document.querySelector("#messageSubmit"),
  partyMeta: document.querySelector("#partyMeta"),
  stateSummary: document.querySelector("#stateSummary"),
  contextSummary: document.querySelector("#contextSummary"),
  memorySummary: document.querySelector("#memorySummary"),
  memorySummarizeButton: document.querySelector("#memorySummarizeButton"),
  memoryClearButton: document.querySelector("#memoryClearButton"),
  characterSheets: document.querySelector("#characterSheets"),
  characterEditTarget: document.querySelector("#characterEditTarget"),
  characterEditId: document.querySelector("#characterEditId"),
  characterEditName: document.querySelector("#characterEditName"),
  characterEditStatus: document.querySelector("#characterEditStatus"),
  characterEditLocation: document.querySelector("#characterEditLocation"),
  characterEditGoal: document.querySelector("#characterEditGoal"),
  characterEditAttitude: document.querySelector("#characterEditAttitude"),
  characterEditLoyalty: document.querySelector("#characterEditLoyalty"),
  characterEditTrust: document.querySelector("#characterEditTrust"),
  characterEditFear: document.querySelector("#characterEditFear"),
  characterEditKnowledge: document.querySelector("#characterEditKnowledge"),
  characterEditObligations: document.querySelector("#characterEditObligations"),
  characterEditHardConstraints: document.querySelector("#characterEditHardConstraints"),
  characterEditSecrets: document.querySelector("#characterEditSecrets"),
  characterManualDraftButton: document.querySelector("#characterManualDraftButton"),
  characterLlmDraftButton: document.querySelector("#characterLlmDraftButton"),
  promptPreviewButton: document.querySelector("#promptPreviewButton"),
  promptPreview: document.querySelector("#promptPreview"),
  journalSummary: document.querySelector("#journalSummary"),
  journalSummarizeButton: document.querySelector("#journalSummarizeButton"),
  journalClearButton: document.querySelector("#journalClearButton"),
  proposalList: document.querySelector("#proposalList"),
  toast: document.querySelector("#toast"),
  partyDialog: document.querySelector("#partyDialog"),
  partyForm: document.querySelector("#partyForm"),
  partyTitleInput: document.querySelector("#partyTitleInput"),
  worldSelect: document.querySelector("#worldSelect"),
  worldReadyFields: document.querySelector("#worldReadyFields"),
  worldPromptFields: document.querySelector("#worldPromptFields"),
  worldPromptTitleInput: document.querySelector("#worldPromptTitleInput"),
  worldPromptInput: document.querySelector("#worldPromptInput"),
  characterNameInput: document.querySelector("#characterNameInput"),
  characterDescriptionInput: document.querySelector("#characterDescriptionInput"),
  characterDescriptionLabel: document.querySelector("#characterDescriptionLabel"),
  characterDescriptionHint: document.querySelector("#characterDescriptionHint"),
  modelProviderSelect: document.querySelector("#modelProviderSelect"),
  modelSelect: document.querySelector("#modelSelect"),
  modelPreview: document.querySelector("#modelPreview"),
  worldPreview: document.querySelector("#worldPreview"),
  worldInstruction: document.querySelector("#worldInstruction"),
  worldPreviewLlmButton: document.querySelector("#worldPreviewLlmButton"),
  checkForm: document.querySelector("#checkForm"),
  checkPanel: document.querySelector("#checkPanel"),
  partyModelProviderSelect: document.querySelector("#partyModelProviderSelect"),
  partyModelSelect: document.querySelector("#partyModelSelect"),
  changePartyModelButton: document.querySelector("#changePartyModelButton"),
  deletePartyButton: document.querySelector("#deletePartyButton"),
  operationStatus: document.querySelector("#operationStatus"),
  adminPanel: document.querySelector("#adminPanel"),
  adminUsersList: document.querySelector("#adminUsersList"),
  adminUserForm: document.querySelector("#adminUserForm"),
  adminUsernameInput: document.querySelector("#adminUsernameInput"),
  adminPasswordInput: document.querySelector("#adminPasswordInput"),
  adminRoleSelect: document.querySelector("#adminRoleSelect"),
  adminApiKeysList: document.querySelector("#adminApiKeysList"),
  adminApiKeyForm: document.querySelector("#adminApiKeyForm"),
  adminApiKeyProviderSelect: document.querySelector("#adminApiKeyProviderSelect"),
  adminApiKeyLabelInput: document.querySelector("#adminApiKeyLabelInput"),
  adminApiKeyInput: document.querySelector("#adminApiKeyInput"),
};

const checkLabels = {
  persuasion: "Убеждение",
  intimidation: "Запугивание",
  deception: "Обман",
  stealth: "Скрытность",
  information: "Поиск сведений",
  resource: "Ресурс",
  feasibility: "Реалистичность",
};

const metaHints = {
  "Сценарий": "Режим исполнения Gateway, выбранный при создании партии.",
  "Мир": "Worldpack или prompt-мир, из которого взят стартовый state.",
  "Персонаж": "Активный игроковый персонаж этой партии.",
  "Провайдер": "API-провайдер выбранной модели.",
  "Модель": "Модель, выбранная для нарратива, проверок и world edits.",
  "ID партии": "Стабильный party_id; он связывает историю, state и выбранные профили.",
  "State": "campaign_id изолированного состояния партии.",
};

const scenarioTypeLabels = {
  rp: "RP · D20",
  novel: "Роман",
  training: "Обучение",
};

const providerLabels = {
  local: "Локальная Vulkan",
  nvidia: "NVIDIA",
  gemini: "Gemini",
  openrouter: "OpenRouter",
};
const providerOrder = ["local", "nvidia", "gemini", "openrouter"];

const CHAT_VISIBLE_TURNS = 4;
const AUTO_START_HISTORY_MESSAGE = "[AUTO_START] Старт партии";
const ACTIVE_PARTY_STORAGE_KEY = "rp-light-gui-active-party";
const PENDING_STORAGE_KEY = "rp-light-gui-pending-messages";
const PENDING_MAX_AGE_MS = 60 * 60 * 1000;
const PENDING_RECOVERY_ATTEMPTS = 180;
const PENDING_RECOVERY_INTERVAL_MS = 5000;
const pendingRecoveryTasks = {};

bindEvents();
setupCollapsiblePanels();
boot();

function bindEvents() {
  els.loginForm.addEventListener("submit", login);
  els.logoutButton.addEventListener("click", logout);
  document.querySelector("#refreshButton").addEventListener("click", () => boot());
  document.querySelector("#stateRefreshButton").addEventListener("click", () => reloadActiveParty());
  document.querySelector("#newPartyButton").addEventListener("click", openPartyDialog);
  els.toolsButton.addEventListener("click", openInspector);
  els.closeInspectorButton.addEventListener("click", closeInspector);
  els.drawerBackdrop.addEventListener("click", closeInspector);
  document.querySelector("#closePartyDialog").addEventListener("click", closePartyDialog);
  document.querySelector("#cancelPartyButton").addEventListener("click", closePartyDialog);
  document.querySelector("#worldPreviewButton").addEventListener("click", previewWorldInstruction);
  els.worldPreviewLlmButton.addEventListener("click", () => previewWorldInstruction({ useLlm: true }));
  document.querySelector("#worldApplyButton").addEventListener("click", applyWorldProposal);
  document.querySelector("#worldDiscardButton").addEventListener("click", discardWorldProposal);
  document.querySelector("#rollbackButton").addEventListener("click", rollbackParty);
  els.memorySummarizeButton.addEventListener("click", summarizeMemory);
  els.memoryClearButton.addEventListener("click", clearLatestMemory);
  els.characterEditTarget.addEventListener("change", fillCharacterEditorFromSelection);
  els.characterManualDraftButton.addEventListener("click", previewCharacterManualDraft);
  els.characterLlmDraftButton.addEventListener("click", previewCharacterLlmDraft);
  els.promptPreviewButton.addEventListener("click", previewPrompt);
  els.journalSummarizeButton.addEventListener("click", summarizeJournal);
  els.journalClearButton.addEventListener("click", clearLatestJournal);
  els.changePartyModelButton.addEventListener("click", changePartyModel);
  els.deletePartyButton.addEventListener("click", deleteActiveParty);
  els.worldSelect.addEventListener("change", () => {
    syncAutoPartyTitle();
    renderWorldPreview();
    syncReadyCharacterDescription();
  });
  els.worldPromptTitleInput.addEventListener("input", renderWorldPreview);
  els.worldPromptInput.addEventListener("input", renderWorldPreview);
  els.modelProviderSelect.addEventListener("change", renderDialogModelOptions);
  els.modelSelect.addEventListener("change", renderModelPreview);
  els.partyModelProviderSelect.addEventListener("change", renderPartyModelOptions);
  els.messageForm.addEventListener("submit", sendMessage);
  els.partyForm.addEventListener("submit", createParty);
  els.checkForm.addEventListener("submit", runCheck);
  els.adminUserForm.addEventListener("submit", createAdminUser);
  els.adminApiKeyForm.addEventListener("submit", createAdminApiKey);
  els.adminUsersList.addEventListener("click", handleAdminUserAction);
  els.adminApiKeysList.addEventListener("click", handleAdminApiKeyAction);
  [els.chatLog, els.historyControls].filter(Boolean).forEach((node) => node.addEventListener("click", handleChatArchiveClick));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeInspector();
  });
  document.querySelectorAll("input[name='worldSource']").forEach((input) => input.addEventListener("change", renderCreationModes));
  document.querySelectorAll("input[name='characterSource']").forEach((input) => input.addEventListener("change", renderCreationModes));
  document.querySelectorAll("input[name='scenarioType']").forEach((input) =>
    input.addEventListener("change", () => {
      renderWorldOptions();
      syncAutoPartyTitle();
      syncReadyCharacterDescription();
      renderWorldPreview();
    }),
  );
}

function openInspector() {
  document.body.classList.add("inspector-open");
}

function closeInspector() {
  document.body.classList.remove("inspector-open");
}

function setupCollapsiblePanels() {
  document.querySelectorAll(".inspector .summary-panel").forEach((panel, index) => {
    if (panel.dataset.collapsibleReady) return;
    panel.dataset.collapsibleReady = "true";
    const existingHead = panel.querySelector(":scope > .panel-head");
    const label = existingHead?.querySelector(".section-label") || panel.querySelector(":scope > .section-label");
    const title = label?.textContent.trim() || `Панель ${index + 1}`;
    const head = document.createElement("div");
    head.className = "collapsible-head";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "collapsible-toggle";
    toggle.setAttribute("aria-expanded", "false");
    toggle.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg><span></span>`;
    toggle.querySelector("span").textContent = title;
    toggle.addEventListener("click", () => setPanelOpen(panel, !panel.classList.contains("panel-open")));
    head.appendChild(toggle);

    const actions = document.createElement("div");
    actions.className = "collapsible-actions";
    if (existingHead) {
      Array.from(existingHead.children).forEach((child) => {
        if (!child.classList.contains("section-label")) actions.appendChild(child);
      });
      existingHead.remove();
    }
    if (label?.parentElement === panel) label.remove();
    if (actions.children.length) head.appendChild(actions);

    const body = document.createElement("div");
    body.className = "collapsible-body";
    Array.from(panel.childNodes).forEach((child) => body.appendChild(child));
    panel.appendChild(head);
    panel.appendChild(body);
  });
}

function setPanelOpen(panel, open) {
  panel.classList.toggle("panel-open", open);
  const toggle = panel.querySelector(":scope > .collapsible-head .collapsible-toggle");
  if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
}

function openPanelFor(element) {
  const panel = element?.closest?.(".summary-panel");
  if (panel) setPanelOpen(panel, true);
}

function setChatArchiveExpanded(expanded) {
  appState.chatArchiveExpanded = expanded;
  renderChat({ scrollMode: expanded ? "top" : "bottom" });
}

function handleChatArchiveClick(event) {
  const button = event.target.closest("[data-chat-archive]");
  if (!button) return;
  setChatArchiveExpanded(button.dataset.chatArchive === "open");
}

async function boot() {
  try {
    const auth = await readAuthState();
    appState.authEnabled = auth.auth_enabled !== false;
    appState.currentUser = auth.user || null;
    renderAuth();
    if (appState.authEnabled && !auth.authenticated) {
      clearWorkspaceState();
      renderAll();
      showLoginScreen();
      return;
    }
    hideLoginScreen();
    setGatewayStatus("синхронизация", false);
    const [health, worldpacks, models, parties] = await Promise.all([
      apiGet("/health"),
      apiGet("/api/worldpacks"),
      apiGet("/api/model-profiles"),
      apiGet("/api/parties"),
    ]);
    appState.worldpacks = worldpacks.worldpacks || [];
    appState.modelProfiles = models.model_profiles || [];
    appState.parties = parties.parties || [];
    setGatewayStatus(health.status || "ok", health.status === "ok");
    if (els.partyDialog.open && (!els.modelSelect.options.length || !els.worldSelect.options.length)) {
      renderDialogOptions();
      renderCreationModes();
    }
    renderPartyList();
    restorePendingMessages();
    prunePendingMessages(appState.parties.map((party) => party.id));
    const savedPartyId = localStorage.getItem(ACTIVE_PARTY_STORAGE_KEY);
    const active = appState.parties.find((party) => party.id === savedPartyId) || appState.parties[0] || null;
    if (active) {
      await selectParty(active.id);
    } else {
      appState.activeParty = null;
      appState.partyState = null;
      appState.contextEstimate = null;
      appState.memory = null;
      appState.characters = null;
      appState.journal = null;
      appState.promptPreview = null;
      appState.history = null;
      appState.chatArchiveExpanded = false;
      appState.proposals = [];
      renderAll();
    }
    if (isAdmin()) {
      await reloadAdminData().catch((error) => showToast(error.message));
    }
  } catch (error) {
    setGatewayStatus("недоступен", false);
    showToast(error.message);
    renderAll();
  }
}

async function readAuthState() {
  const response = await fetch("/api/auth/me");
  if (response.status === 404) {
    return { auth_enabled: false, authenticated: true, user: null };
  }
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    return { auth_enabled: true, authenticated: false, user: null };
  }
  return data;
}

async function login(event) {
  event.preventDefault();
  try {
    setBusy(true);
    const result = await apiPost("/api/auth/login", {
      username: els.loginUsername.value.trim(),
      password: els.loginPassword.value,
    });
    appState.currentUser = result.user || null;
    els.loginPassword.value = "";
    hideLoginScreen();
    await boot();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function logout() {
  try {
    setBusy(true);
    await apiPost("/api/auth/logout", {});
  } catch {
    // Expired sessions are already logged out from the user's perspective.
  } finally {
    appState.currentUser = null;
    clearWorkspaceState();
    renderAuth();
    renderAll();
    showLoginScreen();
    setBusy(false);
  }
}

function clearWorkspaceState() {
  appState.worldpacks = [];
  appState.modelProfiles = [];
  appState.parties = [];
  appState.activeParty = null;
  appState.partyState = null;
  appState.contextEstimate = null;
  appState.memory = null;
  appState.characters = null;
  appState.journal = null;
  appState.promptPreview = null;
  appState.history = null;
  appState.chatArchiveExpanded = false;
  appState.proposals = [];
  appState.pendingMessages = {};
  appState.adminUsers = [];
  appState.adminApiKeys = [];
}

function renderAuth() {
  const user = appState.currentUser;
  els.accountStrip.classList.toggle("hidden", !user);
  if (user) {
    els.currentUserLabel.textContent = `${user.username} · ${user.role}`;
  }
  if (els.adminPanel) {
    els.adminPanel.classList.toggle("hidden", !isAdmin());
  }
}

function showLoginScreen() {
  document.body.classList.add("auth-locked");
  els.loginScreen.classList.remove("hidden");
  window.setTimeout(() => els.loginUsername.focus(), 0);
}

function hideLoginScreen() {
  document.body.classList.remove("auth-locked");
  els.loginScreen.classList.add("hidden");
}

function isAdmin() {
  return appState.currentUser?.role === "admin";
}

async function selectParty(partyId) {
  const party = appState.parties.find((item) => item.id === partyId) || (await apiGet(`/api/parties/${partyId}`)).party;
  appState.activeParty = party;
  localStorage.setItem(ACTIVE_PARTY_STORAGE_KEY, party.id);
  await reloadActiveParty();
}

async function reloadActiveParty() {
  if (!appState.activeParty) {
    renderAll();
    return;
  }
  const partyId = appState.activeParty.id;
  const [party, partyState, history, proposals, context, memory, characters, journal] = await Promise.all([
    apiGet(`/api/parties/${partyId}`),
    apiGet(`/api/parties/${partyId}/state`),
    apiGet(`/api/parties/${partyId}/history`),
    apiGet(`/api/parties/${partyId}/world/proposals`),
    apiGet(`/api/parties/${partyId}/context`),
    apiGet(`/api/parties/${partyId}/memory`),
    apiGet(`/api/parties/${partyId}/characters`),
    apiGet(`/api/parties/${partyId}/journal`),
  ]);
  appState.activeParty = party.party;
  appState.partyState = partyState.state;
  appState.history = history;
  appState.proposals = proposals.proposals || [];
  appState.contextEstimate = context.context || null;
  appState.memory = memory;
  appState.characters = characters;
  appState.journal = journal;
  appState.promptPreview = null;
  appState.chatArchiveExpanded = false;
  reconcilePendingFromHistory(partyId, history);
  ensurePendingRecovery(partyId);
  renderAll();
}

function renderAll() {
  renderPartyList();
  renderHeader();
  renderMeta();
  renderState();
  renderContext();
  renderMemory();
  renderCharacters();
  renderPromptPreview();
  renderJournal();
  renderChat();
  renderProposals();
  renderMessageControls();
  renderAdminPanel();
}

function renderPartyList() {
  if (!appState.parties.length) {
    els.partyList.innerHTML = `<div class="empty-chat">Партий пока нет.</div>`;
    return;
  }
  els.partyList.innerHTML = appState.parties
    .map((party) => {
      const active = appState.activeParty?.id === party.id ? " active" : "";
      const world = party.worldpack?.title || party.worldpack_id;
      const scenario = scenarioTypeLabels[party.scenario_type] || party.scenario_type;
      return `<button class="party-card${active}" data-party-id="${escapeHtml(party.id)}" title="Открыть партию ${escapeHtml(party.title)}">
        <strong>${escapeHtml(party.title)}</strong>
        <span>${escapeHtml(world)} · ${escapeHtml(scenario)}</span>
      </button>`;
    })
    .join("");
  els.partyList.querySelectorAll("[data-party-id]").forEach((button) => {
    button.addEventListener("click", () => selectParty(button.dataset.partyId));
  });
}

function renderHeader() {
  const party = appState.activeParty;
  els.activePartyTitle.textContent = party ? party.title : "Нет активной партии";
  els.activeWorld.textContent = party?.worldpack?.title || "Мир не выбран";
}

function renderMeta() {
  const party = appState.activeParty;
  els.deletePartyButton.disabled = !party;
  els.changePartyModelButton.disabled = !party;
  if (els.checkPanel) {
    els.checkPanel.classList.toggle("hidden", !party || party.scenario_type !== "rp");
  }
  renderPartyModelSelect();
  if (!party) {
    els.partyMeta.innerHTML = `<dt title="Статус выбранной партии">Статус</dt><dd>партия не выбрана</dd>`;
    return;
  }
  const rows = [
    ["Сценарий", scenarioTypeLabels[party.scenario_type] || party.scenario_type],
    ["Мир", party.worldpack?.title || party.worldpack_id],
    ["Персонаж", party.player_character?.name || party.player_character_id],
    ["Провайдер", providerLabel(party.model_profile?.provider)],
    ["Модель", party.model_profile?.model || party.model_profile_id],
    ["ID партии", party.id],
    ["State", party.state_campaign_id],
  ];
  els.partyMeta.innerHTML = rows
    .map(([key, value]) => `<dt title="${escapeHtml(metaHints[key] || "")}">${escapeHtml(key)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd>`)
    .join("");
}

function renderPartyModelSelect() {
  if (!els.partyModelSelect || !els.partyModelProviderSelect) return;
  const currentProvider = normalizeProvider(appState.activeParty?.model_profile?.provider);
  renderProviderOptions(els.partyModelProviderSelect, currentProvider);
  els.partyModelProviderSelect.disabled = !appState.activeParty || !availableProviders().length;
  renderPartyModelOptions();
}

function renderPartyModelOptions() {
  const provider = normalizeProvider(els.partyModelProviderSelect?.value);
  const profiles = profilesForProvider(provider);
  els.partyModelSelect.innerHTML = modelOptionsHtml(profiles, provider);
  els.partyModelSelect.disabled = !appState.activeParty || !profiles.length;
  const current = appState.activeParty?.model_profile_id;
  if (profiles.some((profile) => profile.id === current)) {
    els.partyModelSelect.value = current;
  }
}

function renderState() {
  const state = appState.partyState;
  if (!state) {
    els.stateSummary.innerHTML = `<div class="state-item">State еще не загружен.</div>`;
    return;
  }
  const meta = state.meta || {};
  const player = state.player || {};
  const playerLocation = locationLabel(player.location);
  const resources = compactJson(player.resources || {});
  const threads = Array.isArray(state.active_threads) ? state.active_threads.slice(0, 4) : [];
  const relationships = state.relationships || {};
  const relRows = Object.entries(relationships)
    .slice(0, 5)
    .map(([key, value]) => `${escapeHtml(key)}: доверие ${escapeHtml(value.trust ?? "-")}, подозрение ${escapeHtml(value.suspicion ?? "-")}`);
  els.stateSummary.innerHTML = [
    stateItem("Версия", `v${meta.state_version ?? "-"} · ход ${meta.turn ?? "-"}`, "Номер сохраненного state и текущий ход партии."),
    stateItem("Локация", playerLocation, "Где сейчас находится персонаж."),
    stateItem("Ресурсы", resources, "Подтвержденные ресурсы игрока; их нельзя выдумывать в ходе."),
    stateItem("Отношения", relRows.length ? relRows.join("<br>") : "нет записей", "Доверие/подозрение NPC и фракций к игроку."),
    stateItem(
      "Нити",
      threads.length ? threads.map((thread) => escapeHtml(thread.description || thread.id)).join("<br>") : "нет активных",
      "Активные сюжетные линии, которые GM должен помнить.",
    ),
  ].join("");
}

function renderContext() {
  if (!els.contextSummary) return;
  const estimate = appState.contextEstimate;
  if (!appState.activeParty) {
    els.contextSummary.innerHTML = `<div class="state-item">Партия не выбрана.</div>`;
    return;
  }
  if (!estimate) {
    els.contextSummary.innerHTML = `<div class="state-item">Оценка контекста еще не загружена.</div>`;
    return;
  }
  const source = promptSourceLabel(estimate.prompt_source);
  if (estimate.prompt_source === "missing_recorded_prompt" || estimate.prompt_source === "invalid_recorded_prompt") {
    const notes = Array.isArray(estimate.notes) ? estimate.notes : [];
    els.contextSummary.innerHTML = [
      stateItem("Prompt", source, "Фактический prompt появится после нового хода на обновленном Gateway."),
      stateItem("История", `всего ходов ${escapeHtml(estimate.history_turns_total ?? 0)}`, "Старые ходы сохранены, но их prompt_json еще не был записан или не читается."),
      notes.length ? `<div class="context-notes">${notes.map((note) => `<div>${escapeHtml(note)}</div>`).join("")}</div>` : "",
    ].join("");
    return;
  }
  const percent = typeof estimate.usage_ratio === "number" ? estimate.usage_ratio * 100 : null;
  const fill = percent === null ? 0 : Math.max(2, Math.min(100, percent));
  const percentLabel = percent === null ? "лимит неизвестен" : `${formatPercent(percent)} лимита`;
  const limitLabel = estimate.context_limit_tokens ? formatTokens(estimate.context_limit_tokens) : "неизвестно";
  const notes = Array.isArray(estimate.notes) ? estimate.notes : [];
  const historyText = [
    `в предыдущем prompt ${estimate.direct_history_messages ?? 0} сообщений`,
    `примерно ${estimate.direct_history_turns_estimate ?? 0} ходов`,
    `всего ${estimate.history_turns_total ?? 0}`,
  ].join(" · ");
  const omitted = Number(estimate.omitted_history_turns_estimate || 0);
  const historyHint = omitted
    ? `Еще ${omitted} старых ходов не попадут в прямой prompt; они остаются в storage/state.`
    : "Все сохраненные ходы сейчас помещаются в прямое окно prompt.";
  const stateTokens = estimate.state_summary_tokens ? formatTokens(estimate.state_summary_tokens) : "0";
  const historyTokens = estimate.direct_history_tokens ? formatTokens(estimate.direct_history_tokens) : "0";
  const memoryTokens = estimate.memory_summary_tokens ? formatTokens(estimate.memory_summary_tokens) : "0";
  const memoryCoverage = Array.isArray(estimate.memory_covered_turns) ? ` · память ${estimate.memory_covered_turns.join("-")}` : "";
  els.contextSummary.innerHTML = `
    <div class="context-meter ${escapeHtml(estimate.severity || "unknown")}" title="Оценка приблизительная: tokenizer NVIDIA недоступен, считаем по размеру prompt.">
      <div class="context-meter-head">
        <strong>~${formatTokens(estimate.estimated_total_tokens)} токенов</strong>
        <span>${escapeHtml(source)} · ${escapeHtml(percentLabel)}</span>
      </div>
      <div class="context-bar"><span style="width: ${fill}%"></span></div>
    </div>
    ${stateItem("Запрос", escapeHtml(estimate.last_request_id || "-"), "X-Request-ID последнего сохраненного turn.")}
    ${stateItem("Лимит модели", `${escapeHtml(limitLabel)} · ${escapeHtml(estimate.context_window || "уточняется")}`, "Контекстное окно активной модели из model profile.")}
    ${stateItem("История", `${escapeHtml(historyText)}${omitted ? `<br><span class="warning-text">вне прямого окна ~${omitted} ходов</span>` : ""}`, historyHint)}
    ${stateItem("Разбивка", `state ~${escapeHtml(stateTokens)} · память ~${escapeHtml(memoryTokens)} · история ~${escapeHtml(historyTokens)}${escapeHtml(memoryCoverage)} · ответ до ${escapeHtml(formatTokens(estimate.completion_reserved_tokens || 0))}`, "Оценка входного prompt плюс зарезервированный max_tokens ответа.")}
    ${notes.length ? `<div class="context-notes">${notes.map((note) => `<div>${escapeHtml(note)}</div>`).join("")}</div>` : ""}
  `;
}

function renderMemory() {
  if (!els.memorySummary) return;
  const payload = appState.memory || {};
  const memory = payload.memory || null;
  const stats = payload.stats || {};
  if (els.memorySummarizeButton) {
    els.memorySummarizeButton.disabled = !appState.activeParty;
  }
  if (els.memoryClearButton) {
    els.memoryClearButton.disabled = !appState.activeParty || !memory;
  }
  if (!appState.activeParty) {
    els.memorySummary.innerHTML = `<div class="state-item">Партия не выбрана.</div>`;
    return;
  }
  if (!memory) {
    const oldTurns = stats.eligible_old_turns ?? 0;
    const overflowTokens = formatTokens(stats.unsummarized_old_tokens || 0);
    const budget = formatTokens(stats.history_token_budget || 0);
    els.memorySummary.innerHTML = [
      stateItem("Сводка", oldTurns ? "готовится в фоне" : "пока не нужна", "Gateway начинает сводку, только когда реальная история перестает помещаться в токеновый бюджет."),
      stateItem(
        "Покрытие",
        `вне raw ${escapeHtml(oldTurns)} ходов · ~${escapeHtml(overflowTokens)} · raw бюджет ~${escapeHtml(budget)}`,
        "Пока фоновая сводка не готова, выпавшие несводные ходы временно остаются в prompt, чтобы не терять контекст.",
      ),
    ].join("");
    return;
  }
  const covered = `${memory.from_turn_id ?? "-"}-${memory.to_turn_id ?? "-"}`;
  const stateVersion = memory.state_version ? `v${memory.state_version}` : "v-";
  const summary = escapeHtml(clipText(memory.summary_text || "", 900));
  els.memorySummary.innerHTML = `
    ${stateItem("Покрытие", `ходы ${escapeHtml(covered)} · state ${escapeHtml(stateVersion)}`, "Диапазон turns, сжатых в последнюю сводку.")}
    <div class="state-item memory-text"><strong>Сводка</strong>${summary || "пусто"}</div>
    ${memoryList("Факты", memory.key_facts)}
    ${memoryList("Нити", memory.open_threads)}
    ${memoryList("Отношения", memory.relationship_changes)}
    ${stateItem("Модель", escapeHtml(memory.model || "unknown"), "Модель, которая сгенерировала сводку.")}
  `;
}

function renderCharacters() {
  if (!els.characterSheets) return;
  renderCharacterEditor();
  if (!appState.activeParty) {
    els.characterSheets.innerHTML = `<div class="state-item">Партия не выбрана.</div>`;
    return;
  }
  const payload = appState.characters?.characters || {};
  const player = payload.player || {};
  const characters = Array.isArray(payload.characters) ? payload.characters : [];
  const cards = [
    characterCard({
      name: player.name || "Игрок",
      id: player.id || "player",
      status: player.status,
      location: player.location,
      location_label: player.location_label || locationLabel(player.location),
      current_goal: player.description,
      knowledge: player.known_world_facts,
      obligations: player.constraints,
      last_seen: null,
    }),
    ...characters.slice(0, 8).map((character) => characterCard(character)),
  ];
  if (!characters.length) {
    cards.push(`<div class="state-item">NPC в state пока нет.</div>`);
  }
  els.characterSheets.innerHTML = cards.join("");
}

function renderCharacterEditor() {
  if (!els.characterEditTarget) return;
  const editable = editableCharacters();
  const previous = els.characterEditTarget.value || "__new__";
  const values = new Set(["__new__", ...editable.map((character) => character.value)]);
  const options = [
    `<option value="__new__">Новый NPC</option>`,
    ...editable.map((character) => `<option value="${escapeHtml(character.value)}">${escapeHtml(character.label)}</option>`),
  ];
  els.characterEditTarget.innerHTML = options.join("");
  els.characterEditTarget.value = values.has(previous) ? previous : "__new__";
  const disabled = !appState.activeParty;
  [
    els.characterEditTarget,
    els.characterEditId,
    els.characterEditName,
    els.characterEditStatus,
    els.characterEditLocation,
    els.characterEditGoal,
    els.characterEditAttitude,
    els.characterEditLoyalty,
    els.characterEditTrust,
    els.characterEditFear,
    els.characterEditKnowledge,
    els.characterEditObligations,
    els.characterEditHardConstraints,
    els.characterEditSecrets,
    els.characterManualDraftButton,
    els.characterLlmDraftButton,
  ].forEach((node) => {
    if (node) node.disabled = disabled;
  });
  fillCharacterEditorFromSelection();
}

function editableCharacters() {
  const payload = appState.characters?.characters || {};
  const player = payload.player || {};
  const npcs = Array.isArray(payload.characters) ? payload.characters : [];
  return [
    { value: "player", label: `Игрок: ${player.name || "Игрок"}`, data: { ...player, target: "player" } },
    ...npcs.map((character) => ({
      value: `npc:${character.id}`,
      label: `${character.name || character.id}`,
      data: { ...character, target: "npc" },
    })),
  ];
}

function fillCharacterEditorFromSelection() {
  if (!els.characterEditTarget) return;
  const value = els.characterEditTarget.value;
  const selected = editableCharacters().find((character) => character.value === value)?.data || null;
  const isNew = value === "__new__";
  els.characterEditId.disabled = !appState.activeParty || !isNew;
  els.characterEditId.value = isNew ? "" : selected?.id || "";
  els.characterEditName.value = selected?.name || "";
  els.characterEditStatus.value = selected?.status || (isNew ? "alive" : "");
  els.characterEditLocation.value = isNew ? "" : selected?.location || "";
  els.characterEditGoal.value = selected?.current_goal || selected?.description || "";
  els.characterEditAttitude.value = selected?.attitude_to_player || "";
  els.characterEditLoyalty.value = selected?.loyalty || "";
  els.characterEditTrust.value = selected?.trust ?? "";
  els.characterEditFear.value = selected?.fear ?? "";
  els.characterEditKnowledge.value = listEditorText(selected?.knowledge || selected?.known_world_facts);
  els.characterEditObligations.value = listEditorText(selected?.obligations || selected?.constraints);
  els.characterEditHardConstraints.value = listEditorText(selected?.hard_constraints);
  els.characterEditSecrets.value = listEditorText(selected?.secrets);
}

function listEditorText(value) {
  return Array.isArray(value)
    ? value
        .map((item) => (typeof item === "string" ? item : item?.text || JSON.stringify(item)))
        .filter(Boolean)
        .join("\n")
    : "";
}

function locationLabel(location) {
  const locationText = String(location || "unknown");
  const locations = appState.partyState?.locations || {};
  const details = locations[locationText];
  if (details && typeof details === "object") {
    const direct = details.name || details.title || details.label;
    if (direct) return String(direct);
    const description = String(details.description || "").trim();
    if (description) return description.split(":")[0].split(".")[0].split(";")[0].slice(0, 90);
  }
  if (locationText === "unknown") return "unknown";
  return locationText.replace(/[-_]+/g, " ");
}

function characterCard(character) {
  const relation = relationshipText(character.relationship);
  const lastSeen = character.last_seen ? `ход ${character.last_seen.turn ?? "-"} · ${character.last_seen.event || ""}` : "нет отметки";
  const metrics = [
    character.status ? `статус: ${character.status}` : "",
    character.location ? `место: ${character.location_label || locationLabel(character.location)}` : "",
    relation,
  ].filter(Boolean);
  const details = [
    character.current_goal ? `Цель: ${character.current_goal}` : "",
    listLine("Знание", character.knowledge),
    listLine("Обязательства", character.obligations),
    listLine("Нити", character.threads?.map((thread) => thread.description || thread.id)),
    `Последнее появление: ${lastSeen}`,
  ].filter(Boolean);
  return `<div class="character-card">
    <strong>${escapeHtml(character.name || character.id || "NPC")}</strong>
    <div class="mini-metrics">${metrics.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
    <div>${details.map((item) => escapeHtml(clipText(item, 220))).join("<br>")}</div>
  </div>`;
}

function renderPromptPreview() {
  if (!els.promptPreview) return;
  if (els.promptPreviewButton) {
    els.promptPreviewButton.disabled = !appState.activeParty;
  }
  if (!appState.activeParty) {
    els.promptPreview.innerHTML = `<div class="state-item">Партия не выбрана.</div>`;
    return;
  }
  const preview = appState.promptPreview;
  if (!preview) {
    els.promptPreview.innerHTML = `<div class="state-item">Нажми «Показать prompt», чтобы открыть полный prompt предыдущего запроса.</div>`;
    return;
  }
  const blocks = Array.isArray(preview.blocks) ? preview.blocks : [];
  const total = formatTokens(preview.estimated_prompt_tokens || 0);
  const source = promptSourceLabel(preview.source);
  els.promptPreview.innerHTML = [
    stateItem("Prompt", `~${escapeHtml(total)} токенов · ${escapeHtml(source)}`, "Полный prompt предыдущего запроса, если Gateway уже записал prompt_json."),
    ...blocks.map((block, index) => promptBlock(block, index)),
  ].join("");
}

function promptSourceLabel(source) {
  const labels = {
    recorded_last_turn: "последний записанный запрос",
    invalid_recorded_prompt: "prompt_json не читается",
    missing_recorded_prompt: "нет записанного prompt",
    reconstructed_last_turn: "реконструкция старого запроса",
    no_previous_turn: "ходов еще нет",
    current_dry_run: "dry-run текущего текста",
  };
  return labels[source] || source || "prompt";
}

function promptBlock(block, index) {
  const title = block.title || block.id || `block ${index + 1}`;
  const tokens = formatTokens(block.estimated_tokens || 0);
  const open = index < 2 ? " open" : "";
  return `<details class="prompt-block"${open}>
    <summary>${escapeHtml(title)}<span>~${escapeHtml(tokens)} токенов</span></summary>
    <pre>${escapeHtml(block.content || "")}</pre>
  </details>`;
}

function renderJournal() {
  if (!els.journalSummary) return;
  const payload = appState.journal || {};
  const journal = payload.journal || null;
  const stats = payload.stats || {};
  if (els.journalSummarizeButton) {
    els.journalSummarizeButton.disabled = !appState.activeParty;
  }
  if (els.journalClearButton) {
    els.journalClearButton.disabled = !appState.activeParty || !journal;
  }
  if (!appState.activeParty) {
    els.journalSummary.innerHTML = `<div class="state-item">Партия не выбрана.</div>`;
    return;
  }
  if (!journal) {
    const total = stats.total_turns ?? 0;
    const waiting = stats.next_auto_entry_turns_remaining ?? 0;
    els.journalSummary.innerHTML = [
      stateItem("Recap", "еще не собран", "Журнал нужен человеку, не модели."),
      stateItem("Покрытие", `ходов ${escapeHtml(total)} · до auto ${escapeHtml(waiting)}`, "Auto-journal собирается пачками новых ходов."),
    ].join("");
    return;
  }
  const covered = `${journal.from_turn_id ?? "-"}-${journal.to_turn_id ?? "-"}`;
  els.journalSummary.innerHTML = `<div class="journal-entry">
    <strong>${escapeHtml(journal.title || "Журнал партии")}</strong>
    <div class="mini-metrics">
      <span>ходы ${escapeHtml(covered)}</span>
      <span>state v${escapeHtml(journal.state_version ?? "-")}</span>
    </div>
    <div>${escapeHtml(clipText(journal.recap_text || "", 1000))}</div>
    ${memoryList("Важные изменения", journal.important_changes)}
  </div>`;
}

function renderChat({ scrollMode = "bottom" } = {}) {
  const turns = appState.history?.turns || [];
  const pending = activePendingMessage();
  if (!appState.activeParty) {
    renderHistoryControls(0, 0);
    els.chatLog.innerHTML = `<div class="empty-chat">Создай или выбери партию.</div>`;
    return;
  }
  if (!turns.length && !pending) {
    renderHistoryControls(0, 0);
    els.chatLog.innerHTML = `<div class="empty-chat">Партия готова. Первый ход начнет историю.</div>`;
    return;
  }
  const hiddenTurnCount = Math.max(0, turns.length - CHAT_VISIBLE_TURNS);
  const visibleTurns = hiddenTurnCount && !appState.chatArchiveExpanded ? turns.slice(-CHAT_VISIBLE_TURNS) : turns;
  renderHistoryControls(hiddenTurnCount, turns.length);
  const messages = [];
  if (hiddenTurnCount) {
    messages.push(chatArchiveHtml(hiddenTurnCount));
  }
  for (const turn of visibleTurns) {
    if (isAutoStartTurn(turn)) {
      messages.push(messageHtml("system", "Старт", "Партия началась автоматически."));
    } else {
      messages.push(messageHtml("user", "Игрок", turn.player_message));
    }
    messages.push(messageHtml("assistant", "GM", turn.narrative_response));
  }
  if (pending && !turns.some((turn) => turn.request_id === pending.requestId)) {
    if (!pending.autoStart) {
      messages.push(messageHtml("user", "Игрок", pending.text));
    }
    messages.push(pendingMessageHtml(pending.requestId, pending.status));
  }
  els.chatLog.innerHTML = messages.join("");
  els.chatLog.scrollTop = scrollMode === "top" ? 0 : els.chatLog.scrollHeight;
}

function renderHistoryControls(hiddenTurnCount, totalTurns) {
  if (!els.historyControls) return;
  if (!hiddenTurnCount) {
    els.historyControls.classList.add("hidden");
    els.historyControls.innerHTML = "";
    return;
  }
  const expanded = appState.chatArchiveExpanded;
  els.historyControls.classList.remove("hidden");
  els.historyControls.innerHTML = `<div>
    <strong>${expanded ? "Вся история раскрыта" : `Показаны последние ${CHAT_VISIBLE_TURNS} хода`}</strong>
    <span>${expanded ? `Всего ходов: ${totalTurns}.` : `Скрыто ранних ходов: ${hiddenTurnCount} из ${totalTurns}.`}</span>
  </div>
  <button class="text-button" type="button" data-chat-archive="${expanded ? "close" : "open"}">
    ${expanded ? "Свернуть начало" : "Показать начало"}
  </button>`;
}

function chatArchiveHtml(hiddenTurnCount) {
  const expanded = appState.chatArchiveExpanded;
  return `<div class="history-archive">
    <div>
      <strong>${expanded ? "Начало диалога раскрыто" : "Начало диалога свернуто"}</strong>
      <span>${expanded ? `Можно вернуть компактный вид. Ранних ходов: ${hiddenTurnCount}.` : `Скрыто ранних ходов: ${hiddenTurnCount}.`}</span>
    </div>
    <button class="text-button" type="button" data-chat-archive="${expanded ? "close" : "open"}">
      ${expanded ? "Свернуть начало" : "Показать начало"}
    </button>
  </div>`;
}

function isAutoStartTurn(turn) {
  return String(turn?.player_message || "").startsWith(AUTO_START_HISTORY_MESSAGE);
}

function renderProposals() {
  if (!appState.proposals.length) {
    els.proposalList.innerHTML = `<div class="proposal">Нет черновиков изменений.</div>`;
    return;
  }
  els.proposalList.innerHTML = appState.proposals
    .map(
      (proposal) => `<div class="proposal">
        <strong>${escapeHtml(proposal.proposal_id)}</strong><br>
        ход ${proposal.turn ?? "-"} · операций ${proposal.operations ?? 0}
      </div>`,
    )
    .join("");
}

async function reloadAdminData() {
  if (!isAdmin()) return;
  const [users, apiKeys] = await Promise.all([apiGet("/api/admin/users"), apiGet("/api/admin/api-keys")]);
  appState.adminUsers = users.users || [];
  appState.adminApiKeys = apiKeys.api_keys || [];
  renderAdminPanel();
}

function renderAdminPanel() {
  if (!els.adminPanel) return;
  els.adminPanel.classList.toggle("hidden", !isAdmin());
  if (!isAdmin()) {
    els.adminUsersList.innerHTML = "";
    els.adminApiKeysList.innerHTML = "";
    return;
  }
  els.adminUsersList.innerHTML = appState.adminUsers.length
    ? appState.adminUsers.map((user) => adminUserRow(user)).join("")
    : `<div class="admin-empty">Пользователей нет.</div>`;
  els.adminApiKeysList.innerHTML = appState.adminApiKeys.length
    ? appState.adminApiKeys.map((key) => adminApiKeyRow(key)).join("")
    : `<div class="admin-empty">Ключей нет.</div>`;
}

function adminUserRow(user) {
  const disabled = user.status !== "active";
  const isCurrent = user.id === appState.currentUser?.id;
  return `<div class="admin-row">
    <div>
      <strong>${escapeHtml(user.username)}</strong>
      <span>${escapeHtml(user.role)} · ${escapeHtml(user.status)} · партий ${escapeHtml(user.party_count ?? 0)}</span>
    </div>
    <div class="row-actions">
      <button class="text-button" type="button" data-admin-user-action="password" data-user-id="${escapeHtml(user.id)}">Пароль</button>
      <button class="text-button" type="button" data-admin-user-action="${disabled ? "enable" : "disable"}" data-user-id="${escapeHtml(user.id)}">${disabled ? "Вкл" : "Выкл"}</button>
      <button class="text-button danger-text" type="button" data-admin-user-action="delete" data-user-id="${escapeHtml(user.id)}" ${isCurrent ? "disabled" : ""}>Удалить</button>
    </div>
  </div>`;
}

function adminApiKeyRow(key) {
  return `<div class="admin-row">
    <div>
      <strong>${escapeHtml(key.label)}</strong>
      <span>${escapeHtml(providerLabel(key.provider))} · ${escapeHtml(key.is_default ? "default" : "backup")} · ...${escapeHtml(key.secret_hint || "----")}</span>
    </div>
    <div class="row-actions">
      <button class="text-button" type="button" data-admin-key-action="default" data-key-id="${escapeHtml(key.id)}" ${key.is_default ? "disabled" : ""}>Default</button>
      <button class="text-button danger-text" type="button" data-admin-key-action="delete" data-key-id="${escapeHtml(key.id)}">Удалить</button>
    </div>
  </div>`;
}

function openPartyDialog() {
  renderDialogOptions();
  renderCreationModes();
  if (typeof els.partyDialog.showModal === "function") {
    els.partyDialog.showModal();
  } else {
    els.partyDialog.setAttribute("open", "open");
  }
}

function closePartyDialog() {
  els.partyDialog.close();
}

function renderDialogOptions() {
  document.querySelectorAll("input[name='scenarioType']").forEach((input) => {
    input.checked = false;
  });
  renderWorldOptions();
  renderProviderOptions(els.modelProviderSelect, "nvidia");
  renderDialogModelOptions();
  const pack = selectedWorldpack();
  els.partyTitleInput.value = pack ? `${pack.title}: партия` : "Новая партия";
  els.partyTitleInput.dataset.autoValue = els.partyTitleInput.value;
  els.worldPromptTitleInput.value = "";
  els.worldPromptInput.value = "";
  els.characterNameInput.value = "Игрок";
  els.characterDescriptionInput.value = pack?.manifest?.player_role || "";
  renderWorldPreview();
  renderModelPreview();
}

function renderDialogModelOptions() {
  const provider = normalizeProvider(els.modelProviderSelect?.value);
  const profiles = profilesForProvider(provider);
  const previous = els.modelSelect.value;
  els.modelSelect.innerHTML = modelOptionsHtml(profiles, provider);
  els.modelSelect.disabled = !profiles.length;
  if (profiles.some((profile) => profile.id === previous)) {
    els.modelSelect.value = previous;
  }
  renderModelPreview();
}

function renderWorldOptions() {
  const scenarioType = selectedRadioValue("scenarioType");
  const previous = els.worldSelect.value;
  const available = appState.worldpacks.filter((pack) => worldSupportsScenario(pack, scenarioType));
  els.worldSelect.innerHTML = available
    .map((pack) => `<option value="${escapeHtml(pack.id)}">${escapeHtml(pack.title)} · ${escapeHtml(pack.status)}</option>`)
    .join("");
  if (available.some((pack) => pack.id === previous)) {
    els.worldSelect.value = previous;
  }
}

function worldSupportsScenario(pack, scenarioType) {
  if (!scenarioType) return true;
  const supported = pack?.manifest?.scenario_types?.supported;
  return !Array.isArray(supported) || !supported.length || supported.includes(scenarioType);
}

function syncAutoPartyTitle() {
  const previousAuto = els.partyTitleInput.dataset.autoValue || "";
  if (els.partyTitleInput.value && els.partyTitleInput.value !== previousAuto) return;
  const pack = selectedWorldpack();
  const nextAuto = pack ? `${pack.title}: партия` : "Новая партия";
  els.partyTitleInput.value = nextAuto;
  els.partyTitleInput.dataset.autoValue = nextAuto;
}

function renderCreationModes() {
  const worldPrompt = selectedRadioValue("worldSource") === "prompt";
  const characterPrompt = selectedRadioValue("characterSource") === "prompt";
  els.worldReadyFields.classList.toggle("hidden", worldPrompt);
  els.worldPromptFields.classList.toggle("hidden", !worldPrompt);
  els.worldSelect.toggleAttribute("required", !worldPrompt);
  els.worldPromptInput.toggleAttribute("required", worldPrompt);
  els.characterDescriptionLabel.textContent = characterPrompt ? "Prompt персонажа" : "Описание готового персонажа";
  els.characterDescriptionHint.textContent = characterPrompt
    ? "Опиши роль, характер, ограничения и стартовые ресурсы. Gateway сохранит это в profile персонажа."
    : worldPrompt
      ? "Для prompt-мира используется стандартная роль игрока; можно заменить ее своим описанием."
      : "Берется роль игрока из worldpack; можно слегка поправить перед стартом.";
  if (!characterPrompt) {
    syncReadyCharacterDescription();
  }
  renderWorldPreview();
  renderModelPreview();
}

function renderWorldPreview() {
  if (selectedRadioValue("worldSource") === "prompt") {
    const title = els.worldPromptTitleInput.value.trim() || "Свой мир";
    const prompt = els.worldPromptInput.value.trim() || "Опиши мир, стартовую ситуацию, тон и ограничения.";
    els.worldPreview.innerHTML = `<strong>${escapeHtml(title)}</strong><br>${escapeHtml(prompt)}`;
    if (!els.partyTitleInput.value || els.partyTitleInput.value === "Новая партия") {
      els.partyTitleInput.value = `${title}: партия`;
    }
    return;
  }

  const pack = selectedWorldpack();
  if (!pack) {
    els.worldPreview.textContent = "Нет доступных worldpacks.";
    return;
  }
  els.worldPreview.innerHTML = `<strong>${escapeHtml(pack.title)}</strong><br>${escapeHtml(pack.premise || pack.slug)}`;
  if (!els.partyTitleInput.value) {
    els.partyTitleInput.value = `${pack.title}: партия`;
  }
}

function renderModelPreview() {
  const profile = selectedModelProfile();
  if (!profile) {
    els.modelPreview.textContent = "Нет доступных моделей.";
    return;
  }
  const tags = Array.isArray(profile.tags) && profile.tags.length ? profile.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("") : "";
  els.modelPreview.innerHTML = `<strong>${escapeHtml(profile.title)}</strong>
    <p>${escapeHtml(profile.rp_fit || profile.description || "Описание пока не задано.")}</p>
    <dl>
      <dt>Провайдер</dt><dd>${escapeHtml(providerLabel(profile.provider))}</dd>
      <dt>Alias</dt><dd>${escapeHtml(profile.model)}</dd>
      <dt>Контекст</dt><dd>${escapeHtml(profile.context_window || "уточняется")}</dd>
      <dt>Уровень цены</dt><dd>${escapeHtml(modelCostTierLabel(profile))}</dd>
      <dt>Цена</dt><dd>${escapeHtml(modelPricingLabel(profile))}</dd>
      <dt>Источник</dt><dd>${escapeHtml(sourceLabel(profile.source))}</dd>
      <dt>Доступность</dt><dd>${escapeHtml(profile.availability || "зависит от ключа провайдера")}</dd>
    </dl>
    <div class="tag-row">${tags}</div>`;
}

function syncReadyCharacterDescription() {
  if (selectedRadioValue("characterSource") === "prompt") return;
  const pack = selectedWorldpack();
  els.characterDescriptionInput.value = pack?.manifest?.player_role || "";
}

async function createParty(event) {
  event.preventDefault();
  const modelProfileId = els.modelSelect.value;
  const scenarioType = selectedRadioValue("scenarioType");
  const characterPrompt = selectedRadioValue("characterSource") === "prompt";
  try {
    setBusy(true, "Создаю партию и стартового персонажа...");
    if (!modelProfileId) throw new Error("Нет доступной модели для партии.");
    if (!scenarioType) throw new Error("Выбери тип сценария.");
    const worldpack = await resolveWorldpack();
    const concept = characterPrompt
      ? els.characterDescriptionInput.value.trim()
      : els.characterDescriptionInput.value.trim() || worldpack?.manifest?.player_role || "Игроковый персонаж.";
    const draft = await apiPost("/api/player-characters/draft", {
      worldpack_id: worldpack.id,
      name: els.characterNameInput.value.trim(),
      concept,
    });
    draft.draft.profile = {
      ...(draft.draft.profile || {}),
      character_source: characterPrompt ? "prompt" : "worldpack_template",
    };
    const character = await apiPost("/api/player-characters", draft.draft);
    const party = await apiPost("/api/parties", {
      title: els.partyTitleInput.value.trim(),
      scenario_type: scenarioType,
      worldpack_id: worldpack.id,
      player_character_id: character.player_character.id,
      model_profile_id: modelProfileId,
    });
    closePartyDialog();
    await boot();
    await selectParty(party.party.id);
    showToast("Партия создана. GM готовит стартовую сцену...");
    await autoStartParty(party.party.id);
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function resolveWorldpack() {
  if (selectedRadioValue("worldSource") !== "prompt") {
    if (!els.worldSelect.value) throw new Error("Выбери готовый мир.");
    const pack = selectedWorldpack();
    if (!pack) throw new Error("Готовый мир не найден.");
    return pack;
  }
  const prompt = els.worldPromptInput.value.trim();
  if (!prompt) throw new Error("Заполни prompt мира.");
  const title = els.worldPromptTitleInput.value.trim() || els.partyTitleInput.value.trim() || "Свой мир";
  const result = await apiPost("/api/worldpacks/prompt", { title, prompt });
  appState.worldpacks.push(result.worldpack);
  return result.worldpack;
}

async function autoStartParty(partyId) {
  const requestId = `party_start_${partyId}`;
  startPendingMessage(partyId, requestId, "Старт партии", { autoStart: true });
  appendPendingStartMessage(requestId, "GM готовит стартовую сцену...");
  try {
    setPendingStatus("GM готовит стартовую сцену...", partyId);
    const result = await apiPost(
      `/api/parties/${partyId}/start`,
      { idempotency_key: requestId },
      { "X-Request-ID": requestId },
    );
    const content = result.message?.content || result.latest_turn?.narrative_response || "";
    if (content) {
      replacePendingMessage(partyId, requestId, content);
    }
    setPendingStatus("Старт получен. Обновляю историю...", partyId);
    try {
      await reloadPartyIfActive(partyId);
    } catch (syncError) {
      showToast(`Старт получен, но история не обновилась: ${syncError.message}`);
    }
    showToast(result.started ? "Стартовая сцена готова." : "Партия уже начата.");
  } catch (error) {
    setPendingStatus("Стартовый запрос оборвался. Проверяю историю...", partyId);
    let recoveryError = null;
    const recovered = await waitForRecoveredMessage(partyId, requestId).catch((recoverError) => {
      recoveryError = recoverError;
      return null;
    });
    if (recovered?.narrative_response) {
      showToast("Старт подтянут из истории.");
    } else {
      const message = recoveryError?.message || error.message;
      replacePendingMessage(partyId, requestId, `Стартовая сцена не получена: ${message}`, true);
      showToast(message);
    }
  } finally {
    clearPendingMessage(partyId);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (activePendingMessage()) {
    showToast("Дождись ответа GM по предыдущему ходу.");
    return;
  }
  const text = els.messageInput.value.trim();
  if (!text || !appState.activeParty) return;
  const partyId = appState.activeParty.id;
  const requestId = makeClientRequestId();
  els.messageInput.value = "";
  startPendingMessage(partyId, requestId, text);
  appendPendingMessage(text, requestId);
  try {
    setPendingStatus("GM формирует ответ...", partyId);
    const result = await apiPost(
      `/api/parties/${partyId}/messages`,
      { content: text, idempotency_key: requestId },
      { "X-Request-ID": requestId },
    );
    const content = result.message?.content || "";
    if (content) {
      replacePendingMessage(partyId, requestId, content);
    }
    setPendingStatus("Ответ получен. Обновляю историю...", partyId);
    try {
      await reloadPartyIfActive(partyId);
    } catch (syncError) {
      showToast(`Ответ получен, но история не обновилась: ${syncError.message}`);
    }
  } catch (error) {
    setPendingStatus("Запрос оборвался. Проверяю историю...", partyId);
    let recoveryError = null;
    const recovered = await waitForRecoveredMessage(partyId, requestId).catch((recoverError) => {
      recoveryError = recoverError;
      return null;
    });
    if (recovered?.narrative_response) {
      showToast("Ответ подтянут из истории.");
    } else {
      const message = recoveryError?.message || error.message;
      replacePendingMessage(partyId, requestId, `Ответ не получен: ${message}`, true);
      showToast(message);
    }
  } finally {
    clearPendingMessage(partyId);
  }
}

async function previewWorldInstruction(options = {}) {
  const text = els.worldInstruction.value.trim();
  if (!text || !appState.activeParty) return;
  try {
    setBusy(true, options.useLlm ? "LLM готовит черновик правки мира..." : "Готовлю быстрый черновик правки мира...");
    await apiPost(`/api/parties/${appState.activeParty.id}/world/instruct`, { instruction: text, use_llm: Boolean(options.useLlm) });
    els.worldInstruction.value = "";
    await reloadActiveParty();
    openPanelFor(els.proposalList);
    showToast(options.useLlm ? "LLM-черновик изменений создан." : "Быстрый черновик изменений создан.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function previewCharacterManualDraft() {
  if (!appState.activeParty) return;
  const payload = characterEditorPayload();
  const error = validateCharacterPayload(payload);
  if (error) {
    showToast(error);
    return;
  }
  try {
    setBusy(true, "Сохраняю персонажа в state...");
    await apiPost(`/api/parties/${appState.activeParty.id}/characters/edit`, { ...payload, confirm: true });
    await reloadActiveParty();
    openPanelFor(els.characterSheets);
    showToast("Персонаж сохранен.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function previewCharacterLlmDraft() {
  if (!appState.activeParty) return;
  const payload = characterEditorPayload();
  const error = validateCharacterPayload(payload);
  if (error) {
    showToast(error);
    return;
  }
  const instruction = characterEditorInstruction(payload);
  if (!instruction) return;
  try {
    setBusy(true, "LLM генерирует и сохраняет персонажа...");
    await apiPost(`/api/parties/${appState.activeParty.id}/characters/generate`, payload);
    await reloadActiveParty();
    openPanelFor(els.characterSheets);
    showToast("Персонаж сгенерирован и добавлен.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

function characterEditorPayload() {
  const selection = els.characterEditTarget.value;
  const isPlayer = selection === "player";
  const selectedNpc = selection.startsWith("npc:") ? selection.slice(4) : "";
  const trust = els.characterEditTrust.value === "" ? null : Number(els.characterEditTrust.value);
  const fear = els.characterEditFear.value === "" ? null : Number(els.characterEditFear.value);
  return {
    target: isPlayer ? "player" : "npc",
    character_id: isPlayer ? null : (selectedNpc || els.characterEditId.value.trim()),
    name: els.characterEditName.value.trim() || null,
    status: els.characterEditStatus.value.trim() || null,
    location: els.characterEditLocation.value.trim() || null,
    current_goal: els.characterEditGoal.value.trim() || null,
    attitude_to_player: els.characterEditAttitude.value.trim() || null,
    loyalty: els.characterEditLoyalty.value.trim() || null,
    trust,
    fear,
    knowledge: els.characterEditKnowledge.value.trim() ? els.characterEditKnowledge.value : null,
    obligations: els.characterEditObligations.value.trim() ? els.characterEditObligations.value : null,
    hard_constraints: els.characterEditHardConstraints.value.trim() ? els.characterEditHardConstraints.value : null,
    secrets: els.characterEditSecrets.value.trim() ? els.characterEditSecrets.value : null,
    confirm: false,
  };
}

function validateCharacterPayload(payload) {
  if (payload.target === "npc" && !payload.character_id && !payload.name) {
    return "Заполни имя нового NPC. ID создастся автоматически.";
  }
  const hasContent = [
    payload.name,
    payload.status,
    payload.location,
    payload.current_goal,
    payload.attitude_to_player,
    payload.loyalty,
    payload.trust,
    payload.fear,
    payload.knowledge,
    payload.obligations,
    payload.hard_constraints,
    payload.secrets,
  ].some((value) => value !== null && value !== "");
  return hasContent ? "" : "Заполни хотя бы одно поле персонажа.";
}

function characterEditorInstruction(payload = characterEditorPayload()) {
  const label = payload.target === "player" ? "игрока" : `NPC ${payload.character_id || payload.name || ""}`.trim();
  const lines = [
    `Создай или обнови персонажа ${label}.`,
    payload.name ? `Имя: ${payload.name}` : "",
    payload.status ? `Статус: ${payload.status}` : "",
    payload.location ? `Локация: ${payload.location}` : "",
    payload.current_goal ? `Цель/роль: ${payload.current_goal}` : "",
    payload.attitude_to_player ? `Отношение к игроку: ${payload.attitude_to_player}` : "",
    payload.loyalty ? `Лояльность/фракция: ${payload.loyalty}` : "",
    payload.trust !== null ? `Доверие к игроку: ${payload.trust}` : "",
    payload.fear !== null ? `Страх: ${payload.fear}` : "",
    payload.knowledge ? `Знания:\n${payload.knowledge}` : "",
    payload.obligations ? `Обязательства/ограничения:\n${payload.obligations}` : "",
    payload.hard_constraints ? `Жесткие ограничения:\n${payload.hard_constraints}` : "",
    payload.secrets ? `Секреты:\n${payload.secrets}` : "",
  ].filter(Boolean);
  return lines.join("\n");
}

async function createAdminUser(event) {
  event.preventDefault();
  if (!isAdmin()) return;
  try {
    setBusy(true, "Создаю пользователя Gateway...");
    await apiPost("/api/admin/users", {
      username: els.adminUsernameInput.value.trim(),
      password: els.adminPasswordInput.value,
      role: els.adminRoleSelect.value,
    });
    els.adminUsernameInput.value = "";
    els.adminPasswordInput.value = "";
    els.adminRoleSelect.value = "user";
    await reloadAdminData();
    showToast("Пользователь создан.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function handleAdminUserAction(event) {
  const button = event.target.closest("[data-admin-user-action]");
  if (!button || !isAdmin()) return;
  const userId = button.dataset.userId;
  const action = button.dataset.adminUserAction;
  try {
    setBusy(true, "Обновляю пользователя Gateway...");
    if (action === "password") {
      const password = window.prompt("Новый пароль пользователя");
      if (!password) return;
      await apiPatch(`/api/admin/users/${userId}/password`, { password });
      showToast("Пароль обновлен.");
    } else if (action === "disable" || action === "enable") {
      await apiPatch(`/api/admin/users/${userId}/status`, { status: action === "enable" ? "active" : "disabled" });
      showToast("Статус обновлен.");
    } else if (action === "delete") {
      const ok = window.confirm("Удалить пользователя вместе с его партиями, персонажами и isolated state?");
      if (!ok) return;
      await apiDelete(`/api/admin/users/${userId}`, { delete_data: true });
      showToast("Пользователь удален.");
    }
    await reloadAdminData();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function createAdminApiKey(event) {
  event.preventDefault();
  if (!isAdmin()) return;
  try {
    setBusy(true, "Сохраняю API ключ провайдера...");
    await apiPost("/api/admin/api-keys", {
      label: els.adminApiKeyLabelInput.value.trim(),
      api_key: els.adminApiKeyInput.value,
      provider: els.adminApiKeyProviderSelect.value,
      is_default: true,
    });
    els.adminApiKeyLabelInput.value = "";
    els.adminApiKeyInput.value = "";
    await reloadAdminData();
    const models = await apiGet("/api/model-profiles");
    appState.modelProfiles = models.model_profiles || [];
    renderMeta();
    showToast("API ключ сохранен.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function handleAdminApiKeyAction(event) {
  const button = event.target.closest("[data-admin-key-action]");
  if (!button || !isAdmin()) return;
  const keyId = button.dataset.keyId;
  const action = button.dataset.adminKeyAction;
  try {
    setBusy(true, "Обновляю API ключ провайдера...");
    if (action === "default") {
      await apiPatch(`/api/admin/api-keys/${keyId}`, { is_default: true });
      showToast("Default API ключ обновлен.");
    } else if (action === "delete") {
      const ok = window.confirm("Удалить API ключ из Gateway?");
      if (!ok) return;
      await apiDelete(`/api/admin/api-keys/${keyId}`);
      showToast("API ключ удален.");
    }
    await reloadAdminData();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function applyWorldProposal() {
  if (!appState.activeParty) return;
  try {
    setBusy(true, "Применяю черновик к state...");
    await apiPost(`/api/parties/${appState.activeParty.id}/world/apply`, { proposal_id: "latest", confirm: true });
    await reloadActiveParty();
    openPanelFor(els.proposalList);
    showToast("Черновик применен.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function discardWorldProposal() {
  if (!appState.activeParty) return;
  try {
    setBusy(true, "Отменяю последний черновик...");
    await apiPost(`/api/parties/${appState.activeParty.id}/world/discard`, { proposal_id: "latest", confirm: true });
    await reloadActiveParty();
    openPanelFor(els.proposalList);
    showToast("Черновик отменен.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function rollbackParty() {
  if (!appState.activeParty) return;
  const ok = window.confirm("Откатить последний примененный state этой партии?");
  if (!ok) return;
  try {
    setBusy(true, "Откатываю последний примененный state...");
    await apiPost(`/api/parties/${appState.activeParty.id}/rollback`, {});
    await reloadActiveParty();
    showToast("Откат выполнен.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function summarizeMemory() {
  if (!appState.activeParty) return;
  try {
    setBusy(true, "LLM собирает long-term memory...");
    const result = await apiPost(`/api/parties/${appState.activeParty.id}/memory/summarize`, { force: true });
    appState.memory = result;
    renderMemory();
    await reloadActiveParty();
    openPanelFor(els.memorySummary);
    showToast(result.generated ? "Память обновлена." : memoryReason(result.reason));
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function clearLatestMemory() {
  if (!appState.activeParty) return;
  const ok = window.confirm("Удалить последнюю сводку памяти этой партии?");
  if (!ok) return;
  try {
    setBusy(true, "Удаляю последнюю сводку памяти...");
    const result = await apiDelete(`/api/parties/${appState.activeParty.id}/memory/latest`);
    appState.memory = result;
    renderMemory();
    await reloadActiveParty();
    openPanelFor(els.memorySummary);
    showToast(result.deleted ? "Последняя сводка памяти удалена." : "Сводок памяти пока нет.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function previewPrompt() {
  if (!appState.activeParty) return;
  const content = els.messageInput.value.trim();
  try {
    setBusy(true, "Открываю prompt предыдущего запроса...");
    const result = await apiPost(`/api/parties/${appState.activeParty.id}/prompt/preview`, { content, source: "last" });
    appState.promptPreview = result.preview;
    renderPromptPreview();
    openPanelFor(els.promptPreview);
    showToast("Prompt preview собран.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function summarizeJournal() {
  if (!appState.activeParty) return;
  try {
    setBusy(true, "LLM собирает журнал партии...");
    const result = await apiPost(`/api/parties/${appState.activeParty.id}/journal/summarize`, { force: true });
    appState.journal = result;
    renderJournal();
    await reloadActiveParty();
    openPanelFor(els.journalSummary);
    showToast(result.generated ? "Журнал обновлен." : journalReason(result.reason));
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function clearLatestJournal() {
  if (!appState.activeParty) return;
  const ok = window.confirm("Удалить последнюю запись журнала этой партии?");
  if (!ok) return;
  try {
    setBusy(true, "Удаляю последнюю запись журнала...");
    const result = await apiDelete(`/api/parties/${appState.activeParty.id}/journal/latest`);
    appState.journal = result;
    renderJournal();
    await reloadActiveParty();
    openPanelFor(els.journalSummary);
    showToast(result.deleted ? "Последняя запись журнала удалена." : "Журнала пока нет.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function deleteActiveParty() {
  const party = appState.activeParty;
  if (!party) return;
  const ok = window.confirm(`Удалить партию "${party.title}" и ее историю ходов?`);
  if (!ok) return;
  try {
    setBusy(true, "Удаляю партию и связанную историю...");
    await apiDelete(`/api/parties/${party.id}`);
    localStorage.removeItem(ACTIVE_PARTY_STORAGE_KEY);
    await boot();
    showToast("Партия удалена.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function changePartyModel() {
  const party = appState.activeParty;
  const modelProfileId = els.partyModelSelect.value;
  if (!party || !modelProfileId || modelProfileId === party.model_profile_id) return;
  const profile = appState.modelProfiles.find((item) => item.id === modelProfileId);
  try {
    setBusy(true, "Меняю модель партии...");
    await apiPatch(`/api/parties/${party.id}/model`, { model_profile_id: modelProfileId });
    await boot();
    await selectParty(party.id);
    showToast(`Модель партии: ${profile?.title || modelProfileId}`);
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function runCheck(event) {
  event.preventDefault();
  if (!appState.activeParty) return;
  try {
    setBusy(true, "Провожу проверку и обновляю state...");
    await apiPost(`/api/parties/${appState.activeParty.id}/checks`, {
      check_type: document.querySelector("#checkType").value,
      target: document.querySelector("#checkTarget").value.trim() || null,
      skill: Number(document.querySelector("#checkSkill").value || 0),
      difficulty: Number(document.querySelector("#checkDifficulty").value || 10),
      goal: document.querySelector("#checkGoal").value.trim(),
    });
    await reloadActiveParty();
    openPanelFor(els.stateSummary);
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

function appendPendingMessage(text, requestId) {
  if (!appState.history) appState.history = { turns: [] };
  els.chatLog.insertAdjacentHTML("beforeend", messageHtml("user", "Игрок", text));
  els.chatLog.insertAdjacentHTML("beforeend", pendingMessageHtml(requestId, "GM формирует ответ..."));
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

function appendPendingStartMessage(requestId, status) {
  if (!appState.history) appState.history = { turns: [] };
  els.chatLog.insertAdjacentHTML("beforeend", pendingMessageHtml(requestId, status));
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

function makeClientRequestId() {
  const random = Math.random().toString(36).slice(2, 10);
  return `ui_${Date.now().toString(36)}_${random}`;
}

async function reloadPartyIfActive(partyId) {
  if (appState.activeParty?.id === partyId) {
    await reloadActiveParty();
  }
}

function startPendingMessage(partyId, requestId, text, options = {}) {
  appState.pendingMessages[partyId] = {
    partyId,
    requestId,
    text,
    status: "GM формирует ответ...",
    autoStart: Boolean(options.autoStart),
    createdAt: Date.now(),
  };
  savePendingMessages();
  renderMessageControls();
}

function activePendingMessage() {
  return pendingMessageForParty(appState.activeParty?.id);
}

function pendingMessageForParty(partyId) {
  return partyId ? appState.pendingMessages[partyId] || null : null;
}

function setPendingStatus(status, partyId = appState.activeParty?.id) {
  const pendingMessage = pendingMessageForParty(partyId);
  if (!pendingMessage) return;
  pendingMessage.status = status;
  savePendingMessages();
  if (appState.activeParty?.id === partyId) {
    const pending = els.chatLog.querySelector(`[data-pending-id="${pendingMessage.requestId}"] .pending-text`);
    if (pending) pending.textContent = status;
  }
  renderMessageControls();
}

function clearPendingMessage(partyId = appState.activeParty?.id) {
  if (partyId) {
    delete appState.pendingMessages[partyId];
  }
  delete pendingRecoveryTasks[partyId];
  savePendingMessages();
  renderMessageControls();
}

function replacePendingMessage(partyId, requestId, content, isError = false) {
  if (appState.activeParty?.id !== partyId) {
    return;
  }
  const placeholder = els.chatLog.querySelector(`[data-pending-id="${requestId}"]`);
  const html = messageHtml(isError ? "assistant error" : "assistant", isError ? "Система" : "GM", content);
  if (placeholder) {
    placeholder.outerHTML = html;
  } else {
    els.chatLog.insertAdjacentHTML("beforeend", html);
  }
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

async function waitForRecoveredMessage(partyId, requestId) {
  const attempts = PENDING_RECOVERY_ATTEMPTS;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) await delay(PENDING_RECOVERY_INTERVAL_MS);
    setPendingStatus(`Проверяю уже отправленный ход... ${attempt + 1}/${attempts}`, partyId);
    const turn = await recoverTurn(partyId, requestId);
    if (turn?.narrative_response) {
      if (appState.activeParty?.id === partyId) {
        await reloadActiveParty().catch(() => {});
      }
      return turn;
    }
  }
  return null;
}

function restorePendingMessages() {
  let stored = {};
  try {
    stored = JSON.parse(localStorage.getItem(PENDING_STORAGE_KEY) || "{}");
  } catch {
    stored = {};
  }
  const now = Date.now();
  appState.pendingMessages = {};
  Object.entries(stored || {}).forEach(([partyId, pending]) => {
    if (!pending || typeof pending !== "object") return;
    if (!pending.requestId || !pending.text) return;
    const createdAt = Number(pending.createdAt || 0);
    if (createdAt && now - createdAt > PENDING_MAX_AGE_MS) return;
    appState.pendingMessages[partyId] = {
      partyId,
      requestId: String(pending.requestId),
      text: String(pending.text),
      status: String(pending.status || "Восстанавливаю ожидание ответа..."),
      autoStart: Boolean(pending.autoStart),
      createdAt: createdAt || now,
    };
  });
  savePendingMessages();
}

function savePendingMessages() {
  const pending = {};
  Object.entries(appState.pendingMessages).forEach(([partyId, value]) => {
    if (value?.requestId) pending[partyId] = value;
  });
  if (Object.keys(pending).length) {
    localStorage.setItem(PENDING_STORAGE_KEY, JSON.stringify(pending));
  } else {
    localStorage.removeItem(PENDING_STORAGE_KEY);
  }
}

function prunePendingMessages(validPartyIds) {
  const valid = new Set(validPartyIds);
  let changed = false;
  Object.keys(appState.pendingMessages).forEach((partyId) => {
    if (!valid.has(partyId)) {
      delete appState.pendingMessages[partyId];
      changed = true;
    }
  });
  if (changed) savePendingMessages();
}

function reconcilePendingFromHistory(partyId, history) {
  const pending = pendingMessageForParty(partyId);
  if (!pending) return;
  const turn = (history?.turns || []).find((item) => item.request_id === pending.requestId);
  if (turn?.narrative_response) {
    clearPendingMessage(partyId);
  }
}

function ensurePendingRecovery(partyId) {
  const pending = pendingMessageForParty(partyId);
  if (!pending || pendingRecoveryTasks[partyId]) return;
  pendingRecoveryTasks[partyId] = true;
  waitForRecoveredMessage(partyId, pending.requestId)
    .then((turn) => {
      if (turn?.narrative_response) {
        clearPendingMessage(partyId);
        if (appState.activeParty?.id === partyId) {
          showToast("Ответ восстановлен из истории.");
        }
      } else if (pendingMessageForParty(partyId)) {
        replacePendingMessage(
          partyId,
          pending.requestId,
          "Ответ не найден после проверки. Запрос больше не блокирует ввод; можно повторить ход.",
          true,
        );
        clearPendingMessage(partyId);
      }
    })
    .catch((error) => {
      if (pendingMessageForParty(partyId)) {
        replacePendingMessage(partyId, pending.requestId, `Ответ не получен: ${error.message}`, true);
        clearPendingMessage(partyId);
        if (appState.activeParty?.id === partyId) {
          showToast(error.message);
        }
      }
    })
    .finally(() => {
      delete pendingRecoveryTasks[partyId];
    });
}

async function recoverTurn(partyId, requestId) {
  try {
    const status = await apiGet(`/api/parties/${partyId}/requests/${requestId}`);
    if (status.turn?.narrative_response) return status.turn;
    if (status.status === "failed") {
      throw new Error(status.error || "Запрос завершился ошибкой.");
    }
  } catch (error) {
    if (error.status !== 404 && !String(error.message || "").includes("404")) {
      throw error;
    }
  }
  const history = await apiGet(`/api/parties/${partyId}/history`);
  return (history.turns || []).find((item) => item.request_id === requestId) || null;
}

function renderMessageControls() {
  const pendingMessage = activePendingMessage();
  const locked = Boolean(pendingMessage);
  const hasParty = Boolean(appState.activeParty);
  if (els.messageInput) {
    els.messageInput.disabled = locked || !hasParty;
  }
  if (els.messageSubmit) {
    els.messageSubmit.disabled = locked || !hasParty;
  }
  if (els.messageForm) {
    els.messageForm.setAttribute("aria-busy", locked ? "true" : "false");
  }
  if (!els.messageStatus) return;
  if (locked) {
    els.messageStatus.classList.remove("hidden");
    els.messageStatus.innerHTML = `<span class="spinner" aria-hidden="true"></span><span>${escapeHtml(pendingMessage.status)}</span>`;
  } else {
    els.messageStatus.classList.add("hidden");
    els.messageStatus.innerHTML = "";
  }
}

function pendingMessageHtml(requestId, status) {
  return `<article class="message assistant pending" data-pending-id="${escapeHtml(requestId)}">
    <div class="role">GM</div>
    <div class="pending-line"><span class="spinner" aria-hidden="true"></span><span class="pending-text">${escapeHtml(status)}</span></div>
  </article>`;
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function apiGet(path) {
  return api(path);
}

async function apiPost(path, body, headers = {}) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

async function apiPatch(path, body) {
  return api(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function apiDelete(path, body = null) {
  const options = { method: "DELETE" };
  if (body) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  return api(path, options);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/login") {
      appState.currentUser = null;
      clearWorkspaceState();
      renderAuth();
      renderAll();
      showLoginScreen();
    }
    const error = new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
    error.status = response.status;
    error.detail = data.detail;
    error.response = data;
    throw error;
  }
  return data;
}

function selectedWorldpack() {
  return appState.worldpacks.find((pack) => pack.id === els.worldSelect.value) || appState.worldpacks[0] || null;
}

function selectedModelProfile() {
  return appState.modelProfiles.find((profile) => profile.id === els.modelSelect.value) || profilesForProvider(els.modelProviderSelect?.value)[0] || null;
}

function normalizeProvider(provider) {
  const value = String(provider || "").toLowerCase();
  return value === "nvidia-openai-compatible" ? "nvidia" : value;
}

function providerLabel(provider) {
  const normalized = normalizeProvider(provider);
  return providerLabels[normalized] || normalized || "не выбран";
}

function availableProviders() {
  const found = new Set(appState.modelProfiles.map((profile) => normalizeProvider(profile.provider)).filter(Boolean));
  return [...found].sort((left, right) => {
    const leftRank = providerOrder.indexOf(left);
    const rightRank = providerOrder.indexOf(right);
    return (leftRank < 0 ? 999 : leftRank) - (rightRank < 0 ? 999 : rightRank) || left.localeCompare(right);
  });
}

function profilesForProvider(provider) {
  const normalized = normalizeProvider(provider);
  return appState.modelProfiles.filter((profile) => normalizeProvider(profile.provider) === normalized);
}

function featuredOpenRouterRank(profile) {
  if (normalizeProvider(profile?.provider || "") !== "openrouter") return 0;
  const rank = Number(profile?.params?.featured_rank);
  return Number.isInteger(rank) && rank > 0 ? rank : 0;
}

function modelOptionsHtml(profiles, provider) {
  if (normalizeProvider(provider) !== "openrouter") {
    return profiles.map(modelOptionHtml).join("");
  }
  const featured = profiles
    .filter((profile) => featuredOpenRouterRank(profile))
    .sort((left, right) => featuredOpenRouterRank(left) - featuredOpenRouterRank(right));
  const remaining = profiles.filter((profile) => !featuredOpenRouterRank(profile));
  const groups = [];
  if (featured.length) {
    groups.push(`<optgroup label="\u0418\u0437\u0431\u0440\u0430\u043d\u043d\u043e\u0435 \u0434\u043b\u044f RP">${featured.map(modelOptionHtml).join("")}</optgroup>`);
  }
  if (remaining.length) {
    groups.push(`<optgroup label="\u0412\u0441\u0435 \u043e\u0441\u0442\u0430\u043b\u044c\u043d\u044b\u0435 \u043c\u043e\u0434\u0435\u043b\u0438 OpenRouter">${remaining.map(modelOptionHtml).join("")}</optgroup>`);
  }
  return groups.join("");
}

function renderProviderOptions(select, preferred) {
  if (!select) return;
  const providers = availableProviders();
  select.innerHTML = providers.map((provider) => `<option value="${escapeHtml(provider)}">${escapeHtml(providerLabel(provider))}</option>`).join("");
  const normalizedPreferred = normalizeProvider(preferred);
  if (providers.includes(normalizedPreferred)) {
    select.value = normalizedPreferred;
  }
  select.disabled = !providers.length;
}

function modelOptionHtml(profile) {
  const markers = [
    featuredOpenRouterRank(profile) ? "TOP" : "",
    profile.is_free ? "FREE" : modelCostTier(profile),
    profile.rp_specialized ? "RP" : "",
  ].filter(Boolean);
  const prefix = markers.length ? `[${markers.join(" · ")}] ` : "";
  return `<option value="${escapeHtml(profile.id)}">${escapeHtml(prefix + profile.title)}</option>`;
}

function modelCostTierLabel(profile) {
  if (profile.is_free) return "FREE";
  const tier = modelCostTier(profile);
  return tier || "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d";
}

function modelCostTier(profile) {
  if (normalizeProvider(profile?.provider || "") !== "openrouter" || profile?.is_free) return "";
  const cost = modelCostPerMillion(profile);
  if (cost === null) return "";
  const costs = profilesForProvider("openrouter")
    .filter((candidate) => !candidate.is_free)
    .map(modelCostPerMillion)
    .filter((candidate) => candidate !== null)
    .sort((left, right) => left - right);
  if (!costs.length) return "";
  const position = costs.filter((candidate) => candidate <= cost).length;
  const tier = Math.min(5, Math.max(1, Math.ceil((position / costs.length) * 5)));
  return "$".repeat(tier);
}

function modelCostPerMillion(profile) {
  const prompt = perMillionPriceValue(profile?.pricing_prompt);
  const completion = perMillionPriceValue(profile?.pricing_completion);
  if (prompt === null && completion === null) return null;
  return (prompt || 0) + (completion || 0);
}

function modelPricingLabel(profile) {
  if (profile.is_free) return "FREE";
  const prompt = perMillionPrice(profile.pricing_prompt);
  const completion = perMillionPrice(profile.pricing_completion);
  if (!prompt && !completion) return "не указана каталогом";
  return `$${prompt || "?"}/M input · $${completion || "?"}/M output`;
}

function perMillionPrice(value) {
  const price = perMillionPriceValue(value);
  return price === null ? "" : price.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

function perMillionPriceValue(value) {
  if (String(value ?? "").trim() === "") return null;
  const price = Number(value);
  if (!Number.isFinite(price) || price < 0) return null;
  return price * 1_000_000;
}

function selectedRadioValue(name) {
  return document.querySelector(`input[name='${name}']:checked`)?.value || "ready";
}

function sourceLabel(source) {
  const labels = {
    static_build_nvidia_fallback: "статичный fallback build.nvidia.com",
    build_nvidia_live: "live build.nvidia.com",
    nvidia_api_live: "live NVIDIA /v1/models",
    gemini_server_config: "настроено на сервере",
    gemini_api_live: "live Gemini /models",
    openrouter_server_config: "настроено на сервере",
    openrouter_api_live: "live OpenRouter /models",
    local_vulkan: "локальный Vulkan runner",
    server_env: "server env",
  };
  return labels[source] || source || "неизвестно";
}

function stateItem(title, body, hint) {
  return `<div class="state-item" title="${escapeHtml(hint || "")}"><strong>${escapeHtml(title)}</strong>${body}</div>`;
}

function messageHtml(kind, role, content) {
  return `<article class="message ${kind}">
    <div class="role">${escapeHtml(role)}</div>
    ${escapeHtml(content || "")}
  </article>`;
}

function compactJson(value) {
  const text = JSON.stringify(value, null, 0);
  return escapeHtml(text.length > 180 ? `${text.slice(0, 177)}...` : text);
}

function memoryList(title, items) {
  if (!Array.isArray(items) || !items.length) return "";
  const rows = items
    .slice(0, 4)
    .map((item) => `<li>${escapeHtml(memoryItemText(item))}</li>`)
    .join("");
  return `<div class="state-item memory-list"><strong>${escapeHtml(title)}</strong><ul>${rows}</ul></div>`;
}

function memoryItemText(item) {
  if (typeof item === "string") return item;
  if (!item || typeof item !== "object") return String(item ?? "");
  return item.fact || item.thread || item.change || item.promise || item.obligation || JSON.stringify(item);
}

function relationshipText(relationship) {
  if (!relationship || typeof relationship !== "object") return "";
  const bits = [];
  if (relationship.trust !== undefined) bits.push(`доверие ${relationship.trust}`);
  if (relationship.suspicion !== undefined) bits.push(`подозрение ${relationship.suspicion}`);
  if (relationship.fear !== undefined) bits.push(`страх ${relationship.fear}`);
  return bits.join(" · ");
}

function listLine(title, items) {
  if (!Array.isArray(items) || !items.length) return "";
  return `${title}: ${items.map((item) => memoryItemText(item)).slice(0, 3).join("; ")}`;
}

function memoryReason(reason) {
  const labels = {
    not_enough_old_turns: "Память появится после нескольких ходов за пределами raw окна.",
    not_enough_unsummarized_turns: "Для auto-summary пока мало новых старых ходов.",
    up_to_date: "Память уже актуальна.",
  };
  return labels[reason] || "Память не изменилась.";
}

function journalReason(reason) {
  const labels = {
    not_enough_unsummarized_turns: "Для auto-journal пока мало новых ходов.",
    up_to_date: "Журнал уже актуален.",
  };
  return labels[reason] || "Журнал не изменился.";
}

function clipText(value, limit) {
  const text = String(value ?? "");
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 3))}...` : text;
}

function setGatewayStatus(text, ok) {
  els.gatewayStatus.textContent = text;
  els.gatewayDot.classList.toggle("ok", ok);
}

function setBusy(value, text = "Запрос выполняется...") {
  appState.busy = value;
  appState.busyText = value ? text : "";
  document.body.classList.toggle("busy", value);
  if (!els.operationStatus) return;
  if (value) {
    els.operationStatus.classList.remove("hidden");
    els.operationStatus.innerHTML = `<span class="spinner" aria-hidden="true"></span><span>${escapeHtml(text)}</span>`;
  } else {
    els.operationStatus.classList.add("hidden");
    els.operationStatus.innerHTML = "";
  }
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => els.toast.classList.remove("show"), 3600);
}

function formatTokens(value) {
  const number = Number(value || 0);
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(number >= 10_000_000 ? 0 : 1)}M`;
  if (number >= 1000) return `${(number / 1000).toFixed(number >= 100_000 ? 0 : 1)}k`;
  return `${Math.round(number)}`;
}

function formatPercent(value) {
  if (value < 1) return `${value.toFixed(2)}%`;
  if (value < 10) return `${value.toFixed(1)}%`;
  return `${Math.round(value)}%`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
