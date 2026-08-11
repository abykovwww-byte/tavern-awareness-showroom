const appState = {
  authEnabled: true,
  currentUser: null,
  worldpacks: [],
  modelProfiles: [],
  parties: [],
  activeParty: null,
  activeBranch: null,
  partyState: null,
  contextEstimate: null,
  memory: null,
  loreCards: [],
  checkpoints: [],
  branches: [],
  serviceJobs: [],
  characters: null,
  promptPreview: null,
  history: null,
  chatArchiveExpanded: false,
  proposals: [],
  busy: false,
  busyText: "",
  pendingMessages: {},
  adminUsers: [],
  adminWorldpacks: [],
  byokKeys: [],
  serviceModelSettings: null,
  adminAutotestProfiles: [],
  adminAutotestRuns: [],
  adminDatasetTurns: [],
  adminDatasetReviewTurnId: null,
  worldMarkdownImport: null,
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
  memoryPanelHint: document.querySelector("#memoryPanelHint"),
  memorySummarizeButton: document.querySelector("#memorySummarizeButton"),
  memoryClearButton: document.querySelector("#memoryClearButton"),
  loreCardForm: document.querySelector("#loreCardForm"),
  loreCardTitle: document.querySelector("#loreCardTitle"),
  loreCardContent: document.querySelector("#loreCardContent"),
  loreCardKeywords: document.querySelector("#loreCardKeywords"),
  loreCardAlwaysOn: document.querySelector("#loreCardAlwaysOn"),
  loreCardList: document.querySelector("#loreCardList"),
  checkpointForm: document.querySelector("#checkpointForm"),
  checkpointLabel: document.querySelector("#checkpointLabel"),
  checkpointList: document.querySelector("#checkpointList"),
  characterSheets: document.querySelector("#characterSheets"),
  characterEditTarget: document.querySelector("#characterEditTarget"),
  characterEditId: document.querySelector("#characterEditId"),
  characterEditName: document.querySelector("#characterEditName"),
  characterEditStatus: document.querySelector("#characterEditStatus"),
  characterEditLocation: document.querySelector("#characterEditLocation"),
  characterEditGoal: document.querySelector("#characterEditGoal"),
  characterEditAdvancedFields: document.querySelector("#characterEditAdvancedFields"),
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
  proposalList: document.querySelector("#proposalList"),
  toast: document.querySelector("#toast"),
  partyDialog: document.querySelector("#partyDialog"),
  partyForm: document.querySelector("#partyForm"),
  partyTitleInput: document.querySelector("#partyTitleInput"),
  worldSelect: document.querySelector("#worldSelect"),
  worldReadyFields: document.querySelector("#worldReadyFields"),
  worldPromptFields: document.querySelector("#worldPromptFields"),
  worldPromptTextFields: document.querySelector("#worldPromptTextFields"),
  worldMarkdownFields: document.querySelector("#worldMarkdownFields"),
  worldPromptTitleInput: document.querySelector("#worldPromptTitleInput"),
  worldPromptInput: document.querySelector("#worldPromptInput"),
  worldMarkdownInput: document.querySelector("#worldMarkdownInput"),
  worldMarkdownStatus: document.querySelector("#worldMarkdownStatus"),
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
  autotestBlock: document.querySelector("#autotestBlock"),
  adminWorldpacksList: document.querySelector("#adminWorldpacksList"),
  adminUsersList: document.querySelector("#adminUsersList"),
  adminUserForm: document.querySelector("#adminUserForm"),
  adminUsernameInput: document.querySelector("#adminUsernameInput"),
  adminPasswordInput: document.querySelector("#adminPasswordInput"),
  adminRoleSelect: document.querySelector("#adminRoleSelect"),
  byokKeysList: document.querySelector("#byokKeysList"),
  byokKeyForm: document.querySelector("#byokKeyForm"),
  byokKeyProviderSelect: document.querySelector("#byokKeyProviderSelect"),
  byokKeyLabelInput: document.querySelector("#byokKeyLabelInput"),
  byokKeyInput: document.querySelector("#byokKeyInput"),
  serviceModelForm: document.querySelector("#serviceModelForm"),
  serviceModelSelect: document.querySelector("#serviceModelSelect"),
  serviceModelMeta: document.querySelector("#serviceModelMeta"),
  adminAutotestForm: document.querySelector("#adminAutotestForm"),
  adminAutotestPromptInput: document.querySelector("#adminAutotestPromptInput"),
  adminAutotestProviderSelect: document.querySelector("#adminAutotestProviderSelect"),
  adminAutotestModelSelect: document.querySelector("#adminAutotestModelSelect"),
  adminAutotestTurnsInput: document.querySelector("#adminAutotestTurnsInput"),
  adminAutotestStartButton: document.querySelector("#adminAutotestStartButton"),
  adminAutotestRunsList: document.querySelector("#adminAutotestRunsList"),
  adminDatasetPartyForm: document.querySelector("#adminDatasetPartyForm"),
  adminDatasetPartyStatus: document.querySelector("#adminDatasetPartyStatus"),
  adminDatasetPartyTags: document.querySelector("#adminDatasetPartyTags"),
  adminDatasetExportButton: document.querySelector("#adminDatasetExportButton"),
  adminDatasetTurnsList: document.querySelector("#adminDatasetTurnsList"),
  datasetTurnDialog: document.querySelector("#datasetTurnDialog"),
  datasetTurnDialogTitle: document.querySelector("#datasetTurnDialogTitle"),
  datasetTurnDialogMeta: document.querySelector("#datasetTurnDialogMeta"),
  datasetTurnPlayerMessage: document.querySelector("#datasetTurnPlayerMessage"),
  datasetTurnAssistantMessage: document.querySelector("#datasetTurnAssistantMessage"),
  datasetTurnInteractionEvidence: document.querySelector("#datasetTurnInteractionEvidence"),
  datasetTurnAutoTags: document.querySelector("#datasetTurnAutoTags"),
  datasetTurnTags: document.querySelector("#datasetTurnTags"),
  datasetTurnNotes: document.querySelector("#datasetTurnNotes"),
  closeDatasetTurnDialog: document.querySelector("#closeDatasetTurnDialog"),
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
  "Модель": "Модель ведущего для реплик партии. Память, изменение мира и генерация персонажей используют отдельную глобальную служебную модель.",
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
const WORLD_PROMPT_MAX_CHARS = 6000;
const WORLD_MARKDOWN_MAX_CHARS = 200000;
const WORLD_MARKDOWN_MAX_BYTES = 1024 * 1024;
const WORLD_MARKDOWN_PREVIEW_CHARS = 1200;
const WORLD_MARKDOWN_HELP = "Выбери произвольный UTF-8 `.md` до 1 МиБ и 200 000 символов. Markdown используется как текстовый world system prompt и не исполняется.";
const pendingRecoveryTasks = {};
let partyReloadGeneration = 0;
const autotestPollingTasks = {};

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
  els.loreCardForm.addEventListener("submit", createLoreCard);
  els.loreCardList.addEventListener("click", handleLoreCardAction);
  els.checkpointForm.addEventListener("submit", createCheckpoint);
  els.checkpointList.addEventListener("click", handleBranchAction);
  els.characterEditTarget.addEventListener("change", fillCharacterEditorFromSelection);
  els.characterManualDraftButton.addEventListener("click", previewCharacterManualDraft);
  els.characterLlmDraftButton.addEventListener("click", previewCharacterLlmDraft);
  els.promptPreviewButton.addEventListener("click", previewPrompt);
  els.changePartyModelButton.addEventListener("click", changePartyModel);
  els.deletePartyButton.addEventListener("click", deleteActiveParty);
  els.worldSelect.addEventListener("change", () => {
    syncAutoPartyTitle();
    renderWorldPreview();
    syncReadyCharacterDescription();
  });
  els.worldPromptTitleInput.addEventListener("input", renderWorldPreview);
  els.worldPromptInput.addEventListener("input", renderWorldPreview);
  els.worldMarkdownInput.addEventListener("change", loadWorldMarkdownFile);
  els.modelProviderSelect.addEventListener("change", renderDialogModelOptions);
  els.modelSelect.addEventListener("change", renderModelPreview);
  els.partyModelProviderSelect.addEventListener("change", renderPartyModelOptions);
  els.messageForm.addEventListener("submit", sendMessage);
  els.partyForm.addEventListener("submit", createParty);
  els.checkForm.addEventListener("submit", runCheck);
  els.adminUserForm.addEventListener("submit", createAdminUser);
  els.byokKeyForm.addEventListener("submit", createByokKey);
  els.serviceModelForm.addEventListener("submit", saveServiceModel);
  els.serviceModelSelect.addEventListener("change", renderServiceModelMeta);
  els.adminUsersList.addEventListener("click", handleAdminUserAction);
  els.adminWorldpacksList.addEventListener("change", handleAdminWorldpackVisibility);
  els.byokKeysList.addEventListener("click", handleByokKeyAction);
  els.adminAutotestForm.addEventListener("submit", createAdminAutotest);
  els.adminAutotestProviderSelect.addEventListener("change", renderAdminAutotestModelOptions);
  els.adminAutotestRunsList.addEventListener("click", handleAdminAutotestAction);
  els.adminDatasetPartyForm.addEventListener("submit", saveAdminDatasetParty);
  els.adminDatasetExportButton.addEventListener("click", downloadAdminDataset);
  els.adminDatasetTurnsList.addEventListener("click", handleAdminDatasetTurnAction);
  els.closeDatasetTurnDialog.addEventListener("click", closeAdminDatasetTurnDialog);
  els.datasetTurnDialog.addEventListener("click", handleAdminDatasetDialogAction);
  els.datasetTurnDialog.addEventListener("close", () => { appState.adminDatasetReviewTurnId = null; });
  els.chatLog.addEventListener("click", handleTurnFeedbackClick);
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
      appState.byokKeys = [];
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
  appState.activeBranch = null;
  appState.partyState = null;
  appState.contextEstimate = null;
  appState.memory = null;
  appState.loreCards = [];
  appState.checkpoints = [];
  appState.branches = [];
  appState.serviceJobs = [];
  appState.characters = null;
  appState.promptPreview = null;
  appState.history = null;
  appState.chatArchiveExpanded = false;
  appState.proposals = [];
  appState.pendingMessages = {};
  appState.adminUsers = [];
  appState.byokKeys = [];
  appState.serviceModelSettings = null;
  appState.adminAutotestProfiles = [];
  appState.adminAutotestRuns = [];
  appState.adminDatasetTurns = [];
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
  if (els.autotestBlock) {
    els.autotestBlock.classList.toggle("hidden", !isAdmin());
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
  if (appState.activeParty?.id !== party.id) {
    appState.loreCards = [];
    appState.checkpoints = [];
    appState.branches = [];
    appState.serviceJobs = [];
    appState.byokKeys = [];
  }
  appState.activeParty = party;
  appState.activeBranch = null;
  localStorage.setItem(ACTIVE_PARTY_STORAGE_KEY, party.id);
  await reloadActiveParty();
  if (isAdmin()) await reloadAdminAutotestRuns(party.id);
}

async function reloadActiveParty() {
  if (!appState.activeParty) {
    renderAll();
    return;
  }
  const partyId = appState.activeParty.id;
  const reloadGeneration = ++partyReloadGeneration;
  const optionalPartyData = loadOptionalPartyData(partyId, reloadGeneration).catch((error) => {
    console.warn("Optional party data was not refreshed", error);
  });
  const [party, partyState, history, proposals, context, memory, characters] = await Promise.all([
    apiGet(`/api/parties/${partyId}`),
    apiGet(`/api/parties/${partyId}/state`),
    apiGet(`/api/parties/${partyId}/history`),
    apiGet(`/api/parties/${partyId}/world/proposals`),
    apiGet(`/api/parties/${partyId}/context`),
    apiGet(`/api/parties/${partyId}/memory`),
    apiGet(`/api/parties/${partyId}/characters`),
  ]);
  if (appState.activeParty?.id !== partyId || reloadGeneration !== partyReloadGeneration) return;
  appState.activeParty = party.party;
  appState.partyState = partyState.state;
  appState.history = history;
  appState.proposals = proposals.proposals || [];
  appState.contextEstimate = context.context || null;
  appState.memory = memory;
  appState.characters = characters;
  appState.promptPreview = null;
  appState.chatArchiveExpanded = false;
  reconcilePendingFromHistory(partyId, history);
  ensurePendingRecovery(partyId);
  if (isAdmin()) await reloadAdminDatasetTurns(partyId);
  renderAll();
  void optionalPartyData;
}

async function openPartyBranch(partyId, branchId) {
  if (appState.activeParty?.id !== partyId) {
    await selectParty(partyId);
  }
  const payload = await apiGet(`/api/parties/${partyId}/branches/${branchId}`);
  if (appState.activeParty?.id !== partyId) return;
  appState.activeBranch = payload.branch;
  appState.partyState = payload.state;
  appState.history = {
    party_id: partyId,
    branch_id: branchId,
    turns: payload.turns || [],
    state_versions: payload.state_versions || [],
  };
  appState.characters = { characters: payload.characters || {} };
  appState.contextEstimate = null;
  appState.memory = null;
  appState.byokKeys = [];
  appState.proposals = [];
  appState.chatArchiveExpanded = false;
  if (isAdmin()) await reloadAdminDatasetTurns(partyId, branchId);
  renderAll();
}

async function reloadActiveBranch() {
  const branch = appState.activeBranch;
  const party = appState.activeParty;
  if (!party || !branch) return;
  await openPartyBranch(party.id, branch.id);
}

async function loadOptionalPartyData(partyId, reloadGeneration) {
  const [loreCards, checkpoints, branches, serviceJobs, byok] = await Promise.allSettled([
    apiGet(`/api/parties/${partyId}/lore-cards`),
    apiGet(`/api/parties/${partyId}/checkpoints`),
    apiGet(`/api/parties/${partyId}/branches`),
    apiGet(`/api/parties/${partyId}/service-jobs`),
    apiGet(`/api/parties/${partyId}/byok`),
  ]);
  if (appState.activeParty?.id !== partyId || reloadGeneration !== partyReloadGeneration) return;
  if (loreCards.status === "fulfilled") appState.loreCards = loreCards.value.cards || [];
  if (checkpoints.status === "fulfilled") appState.checkpoints = checkpoints.value.checkpoints || [];
  if (branches.status === "fulfilled") appState.branches = branches.value.branches || [];
  if (serviceJobs.status === "fulfilled") appState.serviceJobs = serviceJobs.value.jobs || [];
  if (byok.status === "fulfilled") appState.byokKeys = byok.value.api_keys || [];
  renderMemoryTools();
  renderByok();
  renderBranchReadOnlyControls();
}

function renderAll() {
  renderPartyList();
  renderHeader();
  renderMeta();
  renderState();
  renderContext();
  renderMemory();
  renderMemoryTools();
  renderCharacters();
  renderPromptPreview();
  renderChat();
  renderProposals();
  renderMessageControls();
  renderAdminPanel();
  renderByok();
  renderBranchReadOnlyControls();
}

function renderBranchReadOnlyControls() {
  if (!appState.activeBranch) return;
  const containers = [els.loreCardForm, els.checkpointForm, els.checkForm];
  containers.forEach((container) => {
    container?.querySelectorAll("input, textarea, select, button").forEach((node) => { node.disabled = true; });
  });
  [
    els.memorySummarizeButton,
    els.memoryClearButton,
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
  ].forEach((node) => { if (node) node.disabled = true; });
  els.loreCardList?.querySelectorAll("[data-lore-action]").forEach((node) => { node.disabled = true; });
  els.proposalList?.querySelectorAll("button").forEach((node) => { node.disabled = true; });
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
  const branch = appState.activeBranch;
  els.activePartyTitle.textContent = party
    ? branch ? `${party.title} · ветка «${branch.label}»` : party.title
    : "Нет активной партии";
  els.activeWorld.textContent = branch
    ? `${party?.worldpack?.title || party?.worldpack_id || "Мир"} · checkpoint #${branch.source_checkpoint_id}`
    : party?.worldpack?.title || "Мир не выбран";
}

function renderMeta() {
  const party = appState.activeParty;
  const branch = appState.activeBranch;
  els.deletePartyButton.disabled = !party || Boolean(branch);
  els.changePartyModelButton.disabled = !party || Boolean(branch);
  if (els.checkPanel) {
    els.checkPanel.classList.toggle("hidden", !party || Boolean(branch) || party.scenario_type !== "rp");
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
    ["State", branch?.state_campaign_id || party.state_campaign_id],
  ];
  if (branch) rows.push(["Ветка", branch.label], ["Checkpoint", `#${branch.source_checkpoint_id}`]);
  els.partyMeta.innerHTML = rows
    .map(([key, value]) => `<dt title="${escapeHtml(metaHints[key] || "")}">${escapeHtml(key)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd>`)
    .join("");
}

function renderPartyModelSelect() {
  if (!els.partyModelSelect || !els.partyModelProviderSelect) return;
  const currentProvider = normalizeProvider(appState.activeParty?.model_profile?.provider);
  renderProviderOptions(els.partyModelProviderSelect, currentProvider);
  els.partyModelProviderSelect.disabled = !appState.activeParty || Boolean(appState.activeBranch) || !availableProviders().length;
  renderPartyModelOptions();
}

function renderPartyModelOptions() {
  const provider = normalizeProvider(els.partyModelProviderSelect?.value);
  const profiles = profilesForProvider(provider);
  els.partyModelSelect.innerHTML = modelOptionsHtml(profiles, provider);
  els.partyModelSelect.disabled = !appState.activeParty || Boolean(appState.activeBranch) || !profiles.length;
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
    .map(([key, value]) => `${escapeHtml(key)}: подозрение ${escapeHtml(value.suspicion ?? "-")}`);
  els.stateSummary.innerHTML = [
    stateItem("Версия", `v${meta.state_version ?? "-"} · ход ${meta.turn ?? "-"}`, "Номер сохраненного state и текущий ход партии."),
    stateItem("Локация", playerLocation, "Где сейчас находится персонаж."),
    stateItem("Ресурсы", resources, "Подтвержденные ресурсы игрока; их нельзя выдумывать в ходе."),
    stateItem("Отношения", relRows.length ? relRows.join("<br>") : "нет записей", "Подозрение NPC и фракций к игроку."),
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
  const characterTokens = estimate.relevant_characters_tokens ? formatTokens(estimate.relevant_characters_tokens) : "0";
  const historyTokens = estimate.direct_history_tokens ? formatTokens(estimate.direct_history_tokens) : "0";
  const memoryTokens = estimate.memory_summary_tokens ? formatTokens(estimate.memory_summary_tokens) : "0";
  const memoryCoverage = Array.isArray(estimate.memory_covered_turns) ? ` · память ${estimate.memory_covered_turns.join("-")}` : "";
  const storyMemoryTokens = estimate.rp_story_memory_tokens ? formatTokens(estimate.rp_story_memory_tokens) : "0";
  const storyMemoryCoverage = Array.isArray(estimate.rp_story_memory_covered_turns)
    ? ` · story ${estimate.rp_story_memory_covered_turns.join("-")}`
    : "";
  const storyMemoryBreakdown = appState.activeParty?.scenario_type === "rp"
    ? ` · RP story ~${storyMemoryTokens}${storyMemoryCoverage}`
    : "";
  const cache = estimate.prompt_cache || {};
  const cacheValue = cache.available
    ? (Number(cache.cached_tokens || 0) > 0
      ? `hit: ${formatTokens(cache.cached_tokens)}${Number(cache.cache_write_tokens || 0) ? ` · запись: ${formatTokens(cache.cache_write_tokens)}` : ""}`
      : (Number(cache.cache_write_tokens || 0) ? `запись: ${formatTokens(cache.cache_write_tokens)}` : "провайдер не вернул cache hit"))
    : "провайдер не передал метрики";
  els.contextSummary.innerHTML = `
    <div class="context-meter ${escapeHtml(estimate.severity || "unknown")}" title="Оценка приблизительная: tokenizer NVIDIA недоступен, считаем по размеру prompt.">
      <div class="context-meter-head">
        <strong>~${formatTokens(estimate.estimated_total_tokens)} токенов</strong>
        <span>${escapeHtml(source)} · ${escapeHtml(percentLabel)}</span>
      </div>
      <progress class="context-bar" max="100" value="${fill}" aria-label="Использование контекста: ${fill}%"></progress>
    </div>
    ${stateItem("Запрос", escapeHtml(estimate.last_request_id || "-"), "X-Request-ID последнего сохраненного turn.")}
    ${stateItem("Лимит модели", `${escapeHtml(limitLabel)} · ${escapeHtml(estimate.context_window || "уточняется")}`, "Контекстное окно активной модели из model profile.")}
    ${stateItem("История", `${escapeHtml(historyText)}${omitted ? `<br><span class="warning-text">вне прямого окна ~${omitted} ходов</span>` : ""}`, historyHint)}
    ${stateItem("Разбивка", `state ~${escapeHtml(stateTokens)} · главы ~${escapeHtml(memoryTokens)}${escapeHtml(storyMemoryBreakdown)} · история ~${escapeHtml(historyTokens)}${escapeHtml(memoryCoverage)} · ответ до ${escapeHtml(formatTokens(estimate.completion_reserved_tokens || 0))}`, "Оценка входного prompt плюс зарезервированный max_tokens ответа.")}
    ${stateItem("Prompt cache", escapeHtml(cacheValue), "Фактическая метрика последнего ответа: hit — токены, прочитанные из кэша; запись — создание нового кэш-префикса. Для NVIDIA и локальной модели метрика может отсутствовать.")}
    ${stateItem("NPC", `~${escapeHtml(characterTokens)}`, "Выбранные карточки персонажей в фактическом prompt.")}
    ${notes.length ? `<div class="context-notes">${notes.map((note) => `<div>${escapeHtml(note)}</div>`).join("")}</div>` : ""}
  `;
}

function renderMemory() {
  if (!els.memorySummary) return;
  const payload = appState.memory || {};
  const memory = payload.memory || null;
  const stats = payload.stats || {};
  const isRp = appState.activeParty?.scenario_type === "rp";
  const storyMemory = isRp ? payload.story_memory || null : null;
  const storyStats = isRp ? payload.story_memory_stats || {} : {};
  if (els.memoryPanelHint) {
    els.memoryPanelHint.textContent = isRp
      ? "RP story memory хранит живой реестр канона, персонажей, проектов и сюжетных линий; эпизодические главы сохраняют подробности старых сцен. Оба слоя не меняют canonical state."
      : "Долгая память для модели: сжатые старые ходы, подтвержденные факты, открытые нити и изменения отношений. State напрямую не меняет.";
  }
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
  const storyHtml = isRp ? rpStoryMemoryHtml(storyMemory, storyStats) : "";
  if (!memory) {
    const oldTurns = stats.eligible_old_turns ?? 0;
    const overflowTokens = formatTokens(stats.unsummarized_old_tokens || 0);
    const budget = formatTokens(stats.history_token_budget || 0);
    els.memorySummary.innerHTML = [
      storyHtml,
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
    ${storyHtml}
    ${stateItem("Покрытие", `ходы ${escapeHtml(covered)} · state ${escapeHtml(stateVersion)}`, "Диапазон turns, сжатых в последнюю сводку.")}
    <div class="state-item memory-text"><strong>Сводка</strong>${summary || "пусто"}</div>
    ${memoryList("Факты", memory.key_facts)}
    ${memoryList("Нити", memory.open_threads)}
    ${memoryList("Отношения", memory.relationship_changes)}
    ${stateItem("Модель", escapeHtml(memory.model || "unknown"), "Модель, которая сгенерировала сводку.")}
  `;
}

function rpStoryMemoryHtml(snapshot, stats) {
  if (!snapshot) {
    const pending = stats.pending_turns || 0;
    const cadence = stats.update_every_turns || 4;
    return stateItem(
      "RP story memory",
      pending ? `ожидает пакет: ${escapeHtml(pending)}/${escapeHtml(cadence)} ходов` : "пока не создана",
      "Живой структурированный реестр обновляется служебной LLM только для RP-партий. До обновления новые сцены остаются в raw history.",
    );
  }
  const data = snapshot.memory || {};
  const covered = `${snapshot.from_turn_id ?? "-"}-${snapshot.to_turn_id ?? "-"}`;
  return `
    ${stateItem("RP story memory", `revision ${escapeHtml(snapshot.revision || 1)} · ходы ${escapeHtml(covered)}`, "Кумулятивный RP-only snapshot; canonical state имеет больший приоритет.")}
    ${data.current_situation ? `<div class="state-item memory-text"><strong>Текущая ситуация</strong>${escapeHtml(clipText(data.current_situation, 900))}</div>` : ""}
    ${memoryList("Канон", data.canon)}
    ${memoryList("Персонажи", data.characters)}
    ${memoryList("Активные линии", data.active_threads)}
    ${memoryList("Нераскрытые зацепки", data.unresolved_hooks)}
  `;
}

function renderMemoryTools() {
  if (!els.loreCardList || !els.checkpointList) return;
  const activeJobs = (appState.serviceJobs || []).filter((job) => ["pending", "running", "failed"].includes(job.status));
  const jobHtml = activeJobs.length
    ? `<div class="state-item"><strong>Служебная LLM</strong>${activeJobs
        .map((job) => `${escapeHtml(job.job_type)}: ${escapeHtml(job.status)} · попытка ${escapeHtml(job.attempts)}/${escapeHtml(job.max_attempts)}${job.last_error ? ` · ${escapeHtml(job.last_error)}` : ""}`)
        .join("<br>")}</div>`
    : `<div class="state-item"><strong>Служебная LLM</strong>очередь пуста</div>`;
  const cards = appState.loreCards || [];
  els.loreCardList.innerHTML = jobHtml + (cards.length
    ? cards.map((card) => `<div class="state-item lore-card">
        <strong>${escapeHtml(card.title)}</strong>
        <div>${escapeHtml(clipText(card.content, 500))}</div>
        <div class="mini-metrics"><span>${card.always_on ? "always-on" : escapeHtml((card.keywords || []).join(", ") || "без триггеров")}</span><span>${card.enabled ? "включена" : "выключена"}</span></div>
        <div class="inline-actions">
          <button class="text-button" type="button" data-lore-action="toggle" data-card-id="${card.id}" data-enabled="${card.enabled}">${card.enabled ? "Выключить" : "Включить"}</button>
          <button class="text-button danger" type="button" data-lore-action="archive" data-card-id="${card.id}">Архивировать</button>
        </div>
      </div>`).join("")
    : `<div class="state-item">Lore Cards пока нет.</div>`);
  const checkpoints = appState.checkpoints || [];
  const checkpointHtml = checkpoints.length
    ? checkpoints.map((checkpoint) => `<div class="state-item">
        <strong>${escapeHtml(checkpoint.label)}</strong>
        <div class="mini-metrics"><span>ход ${escapeHtml(checkpoint.through_turn_id ?? "-")}</span><span>state v${escapeHtml(checkpoint.state_version)}</span><span>memory до ${escapeHtml(checkpoint.memory_coverage_turn_id ?? "-")}</span></div>
      </div>`).join("")
    : `<div class="state-item">Checkpoints пока нет.</div>`;
  const branches = appState.branches || [];
  const branchHtml = branches.length
    ? branches.map((branch) => `<div class="state-item">
        <strong>Ветка · ${escapeHtml(branch.label)}</strong>
        <div class="mini-metrics"><span>checkpoint #${escapeHtml(branch.source_checkpoint_id)}</span><span>${escapeHtml(branch.branch_type)}</span><span>${escapeHtml(branch.status)}</span></div>
        <div class="inline-actions"><button class="text-button" type="button" data-branch-id="${escapeHtml(branch.id)}">Открыть ветку</button></div>
      </div>`).join("")
    : `<div class="state-item">Веток пока нет.</div>`;
  els.checkpointList.innerHTML = `${checkpointHtml}${branchHtml}`;
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
  const compactRpEditor = isCompactRpCharacterEditor(appState.activeParty?.scenario_type);
  els.characterEditAdvancedFields?.classList.toggle("hidden", compactRpEditor);
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

function isCompactRpCharacterEditor(scenarioType) {
  return scenarioType === "rp";
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
    promptInspectionHtml(preview.inspection),
    ...blocks.map((block, index) => promptBlock(block, index)),
  ].join("");
}

function promptInspectionHtml(inspection) {
  if (!inspection || typeof inspection !== "object") return "";
  const chapters = inspection.chapters || {};
  const included = Array.isArray(chapters.included) ? chapters.included : [];
  const excluded = Array.isArray(chapters.excluded) ? chapters.excluded : [];
  const raw = inspection.raw || {};
  const retrieval = Array.isArray(inspection.retrieval) ? inspection.retrieval : [];
  const fallback = inspection.fallback || {};
  const storyMemory = inspection.story_memory || null;
  const chapterText = included.length
    ? included.map(memoryInspectionEntry).join("<br>")
    : "нет глав, вошедших в этот dry-run";
  const excludedText = excluded.length
    ? excluded.map(memoryInspectionEntry).join("<br>")
    : "нет";
  const rawIncluded = turnRangeLabel(raw.included_turn_ids);
  const rawExcluded = turnRangeLabel(raw.excluded_turn_ids);
  const retrievalText = retrieval.length
    ? retrieval.map((item) => `ход ${item.turn_id} · score ${item.score} · ${escapeHtml(item.match_mode || "lexical")} · exact ${escapeHtml(item.lexical_score ?? 0)} · fuzzy ${escapeHtml(item.fuzzy_score ?? 0)} · ${escapeHtml((item.matched_terms || []).join(", "))}`).join("<br>")
    : "нет совпадений в архиве";
  const serviceJobs = Array.isArray(fallback.service_jobs) ? fallback.service_jobs : [];
  const fallbackText = fallback.active
    ? `активен для ${escapeHtml(turnRangeLabel(fallback.turn_ids))}; ${serviceJobs
        .map((job) => `${escapeHtml(job.job_type)}: ${escapeHtml(job.status)} (${escapeHtml(job.attempts)}/${escapeHtml(job.max_attempts)})`)
        .join(" · ") || "service job pending"}`
    : "не активен";
  const storyMemoryText = storyMemory?.enabled
    ? (storyMemory.revision
      ? `revision ${escapeHtml(storyMemory.revision)} · ${escapeHtml(coveredTurnRangeLabel(storyMemory.covered_turn_ids))} · резерв ~${escapeHtml(formatTokens(storyMemory.reserved_tokens || 0))}`
      : `ещё не создана · резерв ~${escapeHtml(formatTokens(storyMemory.reserved_tokens || 0))}`)
    : "";
  return `<div class="state-item prompt-inspection">
    <strong>Почему это в prompt</strong>
    ${storyMemoryText ? `<div><span class="muted-label">RP story memory:</span><br>${storyMemoryText}</div>` : ""}
    <div><span class="muted-label">Главы:</span><br>${chapterText}</div>
    <div><span class="muted-label">Raw:</span> ${escapeHtml(rawIncluded)}${rawExcluded ? `<br><span class="warning-text">вне raw-бюджета: ${escapeHtml(rawExcluded)}</span>` : ""}</div>
    <div><span class="muted-label">Archive retrieval:</span><br>${retrievalText}</div>
    <div><span class="muted-label">Service memory fallback:</span><br>${fallbackText}</div>
    ${excluded.length ? `<details class="prompt-inspection-excluded"><summary>Не вошедшие главы (${excluded.length})</summary>${excludedText}</details>` : ""}
  </div>`;
}

function memoryInspectionEntry(entry) {
  const kind = entry.memory_type === "chapter" ? "глава" : "legacy memory";
  const range = `${entry.from_turn_id ?? "-"}–${entry.to_turn_id ?? "-"}`;
  return `${escapeHtml(kind)} ${escapeHtml(range)} · ~${escapeHtml(formatTokens(entry.estimated_tokens || 0))}`;
}

function turnRangeLabel(turnIds) {
  if (!Array.isArray(turnIds) || !turnIds.length) return "нет";
  const first = turnIds[0];
  const last = turnIds[turnIds.length - 1];
  return first === last ? `ход ${first}` : `ходы ${first}–${last} (${turnIds.length})`;
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
    const autoStart = isAutoStartTurn(turn);
    if (autoStart) {
      messages.push(messageHtml("system", "Старт", "Партия началась автоматически.", turn.created_at));
    } else {
      messages.push(messageHtml("user", "Игрок", turn.player_message, turn.created_at));
    }
    messages.push(messageHtml(
      "assistant",
      "GM",
      turn.narrative_response,
      turn.created_at,
      autoStart ? null : {
        turnId: turn.id,
        rating: turn.player_rating || (turn.player_liked ? "positive" : "none"),
      },
      turn.artifacts || [],
      turn.id,
    ));
  }
  if (pending && !turns.some((turn) => turn.request_id === pending.requestId)) {
    if (!pending.autoStart) {
      messages.push(messageHtml("user", "Игрок", pending.text, pending.createdAt));
    }
    messages.push(pendingMessageHtml(pending.requestId, pending.status, pending.createdAt));
  }
  els.chatLog.innerHTML = messages.join("");
  mountTrainingArtifacts(visibleTurns);
  els.chatLog.scrollTop = scrollMode === "top" ? 0 : els.chatLog.scrollHeight;
}

function coveredTurnRangeLabel(turnIds) {
  if (!Array.isArray(turnIds) || turnIds.length < 2) return "покрытие неизвестно";
  const [first, last] = turnIds;
  return first === last ? `ход ${first}` : `ходы ${first}–${last}`;
}

function mountTrainingArtifacts(turns) {
  const renderer = globalThis.TrainingArtifacts;
  if (!renderer || appState.activeBranch || !appState.activeParty) return;
  for (const turn of turns) {
    if (!Array.isArray(turn.artifacts) || !turn.artifacts.length) continue;
    const host = els.chatLog.querySelector(`[data-training-artifact-turn="${Number(turn.id)}"]`);
    if (!host) continue;
    let artifactsToMount = turn.artifacts;
    const message = host.closest(".message");
    const messengerLinks = message?.querySelectorAll(".messenger-card .artifact-chip-link") || [];
    for (const linkChip of messengerLinks) {
      const matchingArtifact = renderer.artifactForDisplayUrl?.(turn.artifacts, linkChip.textContent);
      if (!matchingArtifact) continue;
      linkChip.replaceWith(host);
      artifactsToMount = [matchingArtifact];
      break;
    }
    renderer.mount(host, artifactsToMount, (payload) => apiPost(
      `/api/parties/${encodeURIComponent(appState.activeParty.id)}/artifact-events`,
      payload,
    ));
  }
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

async function handleTurnFeedbackClick(event) {
  const button = event.target.closest("[data-turn-feedback]");
  if (!button || !appState.activeParty) return;
  const turnId = Number(button.dataset.turnFeedback);
  if (!Number.isInteger(turnId) || turnId <= 0) return;
  const selectedRating = button.dataset.rating;
  const rating = button.getAttribute("aria-pressed") === "true" ? "none" : selectedRating;
  const controls = button.closest(".message-feedback");
  const buttons = [...controls.querySelectorAll("[data-turn-feedback]")];
  buttons.forEach((item) => { item.disabled = true; });
  try {
    const result = await apiPut(
      `/api/parties/${encodeURIComponent(appState.activeParty.id)}/turns/${turnId}/feedback`,
      { rating },
    );
    const saved = normalizeTurnFeedbackRating(result.feedback);
    const turn = (appState.history?.turns || []).find((item) => Number(item.id) === turnId);
    if (turn) {
      turn.player_rating = saved;
      turn.player_liked = saved === "positive";
      turn.player_disliked = saved === "negative";
    }
    updateTurnFeedbackControls(controls, saved);
    showToast({
      positive: "Связка отмечена как удачная.",
      negative: "Связка отмечена как неудачная.",
      none: "Оценка убрана.",
    }[saved]);
  } catch (error) {
    showToast(error.message);
  } finally {
    buttons.forEach((item) => { item.disabled = false; });
  }
}

function normalizeTurnFeedbackRating(feedback) {
  if (["positive", "negative", "none"].includes(feedback?.rating)) return feedback.rating;
  if (feedback?.disliked) return "negative";
  return feedback?.liked ? "positive" : "none";
}

function turnFeedbackIconHtml(rating) {
  const transform = rating === "negative" ? ' transform="rotate(180 12 12)"' : "";
  return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><g${transform}><path d="M7 10v12"></path><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"></path></g></svg>`;
}

function turnFeedbackControlsHtml(feedback) {
  const currentRating = normalizeTurnFeedbackRating(feedback);
  const buttons = [
    ["positive", "Хорошая связка реплик", "Убрать положительную оценку"],
    ["negative", "Неудачная связка реплик", "Убрать отрицательную оценку"],
  ].map(([rating, label, activeLabel]) => {
    const active = currentRating === rating;
    const accessibleLabel = active ? activeLabel : label;
    return `<button class="turn-feedback-button turn-feedback-${rating}" type="button" data-turn-feedback="${escapeHtml(feedback.turnId)}" data-rating="${rating}" aria-label="${accessibleLabel}" aria-pressed="${active}" title="${accessibleLabel}">${turnFeedbackIconHtml(rating)}</button>`;
  }).join("");
  return `<div class="message-feedback" role="group" aria-label="Оценка связки реплик">${buttons}</div>`;
}

function updateTurnFeedbackControls(controls, rating) {
  controls.querySelectorAll("[data-turn-feedback]").forEach((button) => {
    const active = button.dataset.rating === rating;
    const positive = button.dataset.rating === "positive";
    const label = active
      ? `Убрать ${positive ? "положительную" : "отрицательную"} оценку`
      : `${positive ? "Хорошая" : "Неудачная"} связка реплик`;
    button.setAttribute("aria-pressed", String(active));
    button.setAttribute("aria-label", label);
    button.title = label;
  });
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
  const [users, worldpacks, serviceModel, autotestModels] = await Promise.all([
    apiGet("/api/admin/users"),
    apiGet("/api/worldpacks"),
    apiGet("/api/admin/global-settings/service-model"),
    apiGet("/api/admin/autotests/models"),
  ]);
  appState.adminUsers = users.users || [];
  appState.adminWorldpacks = (worldpacks.worldpacks || []).filter((pack) => !pack.owner_user_id);
  appState.serviceModelSettings = serviceModel;
  appState.adminAutotestProfiles = autotestModels.model_profiles || [];
  await Promise.all([
    reloadAdminAutotestRuns(appState.activeParty?.id),
    reloadAdminDatasetTurns(appState.activeParty?.id, appState.activeBranch?.id),
  ]);
  renderAdminPanel();
  renderMessageControls();
}

function adminAutotestsUrl(partyId) {
  return `/api/admin/autotests?source_party_id=${encodeURIComponent(partyId)}`;
}

async function reloadAdminDatasetTurns(partyId, branchId = null) {
  if (!isAdmin() || !partyId) {
    appState.adminDatasetTurns = [];
    renderAdminDataset();
    return;
  }
  const query = branchId ? `?branch_id=${encodeURIComponent(branchId)}` : "";
  const response = await apiGet(`/api/admin/datasets/parties/${encodeURIComponent(partyId)}/turns${query}`);
  if (appState.activeParty?.id !== partyId || (appState.activeBranch?.id || null) !== (branchId || null)) return;
  appState.adminDatasetTurns = response.turns || [];
  renderAdminDataset();
}

async function reloadAdminAutotestRuns(partyId) {
  if (!isAdmin() || !partyId) {
    appState.adminAutotestRuns = [];
    renderAdminAutotestRuns();
    return;
  }
  const response = await apiGet(adminAutotestsUrl(partyId));
  if (appState.activeParty?.id !== partyId) return;
  appState.adminAutotestRuns = (response.runs || []).filter((run) => run.source_party_id === partyId);
  renderAdminAutotestRuns();
  appState.adminAutotestRuns
    .filter((run) => ["running", "stopping"].includes(run.status))
    .forEach((run) => pollAdminAutotest(run.id));
}

function renderAdminPanel() {
  if (!els.adminPanel) return;
  els.adminPanel.classList.toggle("hidden", !isAdmin());
  if (!isAdmin()) {
    els.adminWorldpacksList.innerHTML = "";
    els.adminUsersList.innerHTML = "";
    els.adminAutotestRunsList.innerHTML = "";
    els.adminDatasetTurnsList.innerHTML = "";
    return;
  }
  renderServiceModel();
  renderAdminDataset();
  renderAdminAutotestOptions();
  renderAdminAutotestRuns();
  els.adminWorldpacksList.innerHTML = appState.adminWorldpacks.length
    ? appState.adminWorldpacks.map((pack) => adminWorldpackRow(pack)).join("")
    : `<div class="admin-empty">Миров-пресетов нет.</div>`;
  els.adminUsersList.innerHTML = appState.adminUsers.length
    ? appState.adminUsers.map((user) => adminUserRow(user)).join("")
    : `<div class="admin-empty">Пользователей нет.</div>`;
}

function adminWorldpackRow(pack) {
  return `<label class="admin-row admin-worldpack-row">
    <div>
      <strong>${escapeHtml(pack.title)}</strong>
      <span>${escapeHtml(pack.id)}</span>
    </div>
    <select data-worldpack-visibility data-worldpack-id="${escapeHtml(pack.id)}" aria-label="Видимость мира ${escapeHtml(pack.title)}">
      <option value="public" ${pack.visibility !== "private" ? "selected" : ""}>Публичный</option>
      <option value="private" ${pack.visibility === "private" ? "selected" : ""}>Приватный</option>
    </select>
  </label>`;
}

async function handleAdminWorldpackVisibility(event) {
  const select = event.target.closest("[data-worldpack-visibility]");
  if (!select) return;
  const worldpackId = select.dataset.worldpackId;
  const previous = appState.adminWorldpacks.find((pack) => pack.id === worldpackId)?.visibility || "public";
  select.disabled = true;
  try {
    const response = await apiPatch(`/api/admin/worldpacks/${encodeURIComponent(worldpackId)}/visibility`, {
      visibility: select.value,
    });
    appState.adminWorldpacks = appState.adminWorldpacks.map((pack) =>
      pack.id === worldpackId ? response.worldpack : pack,
    );
    appState.worldpacks = appState.worldpacks.map((pack) =>
      pack.id === worldpackId ? response.worldpack : pack,
    );
    renderAdminPanel();
    renderWorldOptions();
    showToast(response.worldpack.visibility === "private" ? "Мир теперь приватный." : "Мир теперь публичный.");
  } catch (error) {
    select.value = previous;
    select.disabled = false;
    showToast(error.message);
  }
}

function renderAdminDataset() {
  if (!els.adminDatasetPartyForm) return;
  const party = appState.activeParty;
  const disabled = !party;
  els.adminDatasetPartyStatus.disabled = disabled;
  els.adminDatasetPartyTags.disabled = disabled;
  els.adminDatasetPartyForm.querySelector("button[type='submit']").disabled = disabled;
  els.adminDatasetExportButton.disabled = !isAdmin();
  if (!party) {
    els.adminDatasetPartyStatus.value = "review";
    els.adminDatasetPartyTags.value = "";
    els.adminDatasetTurnsList.innerHTML = `<div class="admin-empty">Выберите партию для разметки.</div>`;
    return;
  }
  els.adminDatasetPartyStatus.value = party.dataset_review_status || "review";
  els.adminDatasetPartyTags.value = (party.dataset_tags || []).join(", ");
  const visibleTurns = [...appState.adminDatasetTurns].reverse().slice(0, 100);
  els.adminDatasetTurnsList.innerHTML = visibleTurns.length
    ? visibleTurns.map(adminDatasetTurnRow).join("")
    : `<div class="admin-empty">В выбранной линии пока нет записанных ходов.</div>`;
}

function adminDatasetTurnRow(turn) {
  const statusLabels = { review: "на проверке", approved: "одобрено", excluded: "исключено" };
  const tags = [...(turn.auto_tags || []), ...(turn.tags || [])].join(", ");
  const preview = String(turn.player_message || "").replace(/\s+/g, " ").slice(0, 180);
  return `<div class="admin-row">
    <div>
      <strong>#${escapeHtml(turn.turn_id)} · ${escapeHtml(statusLabels[turn.review_status] || turn.review_status)}</strong>
      <span>${escapeHtml(preview || "без сообщения игрока")}</span>
      <span>${escapeHtml(tags)}</span>
    </div>
    <div class="row-actions">
      <button class="text-button dataset-review-open" type="button" data-dataset-open data-turn-id="${escapeHtml(turn.turn_id)}">Проверить</button>
      <button class="text-button" type="button" data-dataset-status="approved" data-turn-id="${escapeHtml(turn.turn_id)}">Одобрить</button>
      <button class="text-button danger-text" type="button" data-dataset-status="excluded" data-turn-id="${escapeHtml(turn.turn_id)}">Исключить</button>
    </div>
  </div>`;
}

function datasetTagsFromInput(value) {
  return String(value || "").split(",").map((tag) => tag.trim()).filter(Boolean);
}

async function saveAdminDatasetParty(event) {
  event.preventDefault();
  const party = appState.activeParty;
  if (!party) return;
  try {
    const response = await apiPatch(`/api/admin/datasets/parties/${encodeURIComponent(party.id)}`, {
      review_status: els.adminDatasetPartyStatus.value,
      tags: datasetTagsFromInput(els.adminDatasetPartyTags.value),
    });
    appState.activeParty = response.party;
    appState.parties = appState.parties.map((item) => item.id === response.party.id ? response.party : item);
    renderAdminDataset();
    renderPartyList();
    showToast("Разметка партии сохранена.");
  } catch (error) {
    showToast(error.message);
  }
}

async function handleAdminDatasetTurnAction(event) {
  const openButton = event.target.closest("[data-dataset-open]");
  if (openButton) {
    const turn = appState.adminDatasetTurns.find((item) => String(item.turn_id) === openButton.dataset.turnId);
    if (turn) openAdminDatasetTurnDialog(turn);
    return;
  }
  const button = event.target.closest("[data-dataset-status]");
  const party = appState.activeParty;
  if (!button || !party) return;
  const branchQuery = appState.activeBranch?.id
    ? `?branch_id=${encodeURIComponent(appState.activeBranch.id)}`
    : "";
  const turn = appState.adminDatasetTurns.find((item) => String(item.turn_id) === button.dataset.turnId);
  try {
    await apiPut(
      `/api/admin/datasets/parties/${encodeURIComponent(party.id)}/turns/${encodeURIComponent(button.dataset.turnId)}${branchQuery}`,
      {
        review_status: button.dataset.datasetStatus,
        tags: turn?.tags || [],
        notes: turn?.notes || "",
      },
    );
    await reloadAdminDatasetTurns(party.id, appState.activeBranch?.id);
    showToast("Статус хода сохранён.");
  } catch (error) {
    showToast(error.message);
  }
}

function openAdminDatasetTurnDialog(turn) {
  appState.adminDatasetReviewTurnId = Number(turn.turn_id);
  fillAdminDatasetTurnDialog(turn);
  if (!els.datasetTurnDialog.open) els.datasetTurnDialog.showModal();
}

function closeAdminDatasetTurnDialog() {
  if (els.datasetTurnDialog.open) els.datasetTurnDialog.close();
}

function fillAdminDatasetTurnDialog(turn) {
  const statusLabels = { review: "На проверке", approved: "Одобрено", excluded: "Исключено" };
  const ratingLabels = { positive: "Игроку понравилось", negative: "Игроку не понравилось", none: "Без оценки игрока" };
  const feedback = turn.player_feedback || {};
  const createdAt = globalThis.MessageTime?.formatMessageTime(turn.created_at) || "";
  const branchLabel = turn.branch_id ? "Ветка" : "Основная линия";
  els.datasetTurnDialogTitle.textContent = `Ход #${turn.turn_id}`;
  els.datasetTurnDialogMeta.innerHTML = [
    `<span class="dataset-status dataset-status-${escapeHtml(turn.review_status || "review")}">${escapeHtml(statusLabels[turn.review_status] || turn.review_status)}</span>`,
    `<span>${escapeHtml(branchLabel)}</span>`,
    createdAt ? `<span title="${escapeHtml(createdAt.title)}">${escapeHtml(createdAt.text)}</span>` : "",
    `<span>${escapeHtml(ratingLabels[feedback.rating] || ratingLabels.none)}</span>`,
  ].filter(Boolean).join("");
  els.datasetTurnPlayerMessage.textContent = turn.player_message || "Реплика игрока отсутствует.";
  els.datasetTurnAssistantMessage.textContent = turn.assistant_response || "Ответ LLM отсутствует.";
  const eventLabels = {
    link_opened: "Перешёл по ссылке",
    form_submitted: "Отправил форму",
    credentials_submitted: "Ввёл и отправил учётные данные",
    reported: "Сообщил о подозрительном сообщении",
  };
  const evidenceItems = Array.isArray(turn.interaction_evidence) ? turn.interaction_evidence : [];
  els.datasetTurnInteractionEvidence.textContent = evidenceItems.length
    ? evidenceItems.map((item) => {
      const result = item.decision_result === "pass"
        ? "успех"
        : item.decision_result === "fail" ? "ошибка" : "без оценки";
      const label = eventLabels[item.event_type] || item.event_type || "Действие";
      return `${label} — ${result}${item.evidence ? ` (${item.evidence})` : ""}`;
    }).join("\n")
    : "За этот ход действий в учебном сайте не зафиксировано.";
  els.datasetTurnAutoTags.innerHTML = (turn.auto_tags || []).length
    ? turn.auto_tags.map((tag) => `<span class="dataset-tag">${escapeHtml(tag)}</span>`).join("")
    : `<span class="dataset-tag dataset-tag-muted">Нет</span>`;
  els.datasetTurnTags.value = (turn.tags || []).join(", ");
  els.datasetTurnNotes.value = turn.notes || "";
}

async function handleAdminDatasetDialogAction(event) {
  const button = event.target.closest("[data-dataset-dialog-status]");
  const party = appState.activeParty;
  const turnId = appState.adminDatasetReviewTurnId;
  if (!button || !party || !turnId) return;
  const buttons = [...els.datasetTurnDialog.querySelectorAll("[data-dataset-dialog-status]")];
  buttons.forEach((item) => { item.disabled = true; });
  const branchQuery = appState.activeBranch?.id
    ? `?branch_id=${encodeURIComponent(appState.activeBranch.id)}`
    : "";
  try {
    await apiPut(
      `/api/admin/datasets/parties/${encodeURIComponent(party.id)}/turns/${encodeURIComponent(turnId)}${branchQuery}`,
      {
        review_status: button.dataset.datasetDialogStatus,
        tags: datasetTagsFromInput(els.datasetTurnTags.value),
        notes: els.datasetTurnNotes.value.trim(),
      },
    );
    await reloadAdminDatasetTurns(party.id, appState.activeBranch?.id);
    const updatedTurn = appState.adminDatasetTurns.find((item) => Number(item.turn_id) === turnId);
    if (updatedTurn) fillAdminDatasetTurnDialog(updatedTurn);
    showToast("Разметка связки сохранена.");
  } catch (error) {
    showToast(error.message);
  } finally {
    buttons.forEach((item) => { item.disabled = false; });
  }
}

function downloadAdminDataset() {
  window.location.assign("/api/admin/datasets/export.jsonl");
}

function renderAdminAutotestOptions() {
  const previousProvider = normalizeProvider(els.adminAutotestProviderSelect.value);
  const providers = ["local", "openrouter"].filter((provider) =>
    appState.adminAutotestProfiles.some((profile) => normalizeProvider(profile.provider) === provider),
  );
  els.adminAutotestProviderSelect.innerHTML = providers
    .map((provider) => `<option value="${escapeHtml(provider)}">${escapeHtml(providerLabel(provider))}</option>`)
    .join("");
  if (providers.includes(previousProvider)) els.adminAutotestProviderSelect.value = previousProvider;
  renderAdminAutotestModelOptions();
  const ready = Boolean(appState.activeParty && !appState.activeBranch && providers.length && els.adminAutotestModelSelect.value);
  els.adminAutotestStartButton.disabled = !ready;
  els.adminAutotestStartButton.title = appState.activeParty
    ? appState.activeBranch
      ? "Вернитесь в основную линию партии перед запуском"
      : "Создать checkpoint и отдельную ветку текущей партии"
    : "Сначала выберите партию";
}

function renderAdminAutotestModelOptions() {
  const provider = normalizeProvider(els.adminAutotestProviderSelect.value);
  const previous = els.adminAutotestModelSelect.value;
  const profiles = appState.adminAutotestProfiles.filter((profile) => normalizeProvider(profile.provider) === provider);
  els.adminAutotestModelSelect.innerHTML = modelOptionsHtml(profiles, provider);
  if (profiles.some((profile) => profile.id === previous)) els.adminAutotestModelSelect.value = previous;
  els.adminAutotestModelSelect.disabled = !profiles.length;
  els.adminAutotestStartButton.disabled = !appState.activeParty || Boolean(appState.activeBranch) || !profiles.length;
}

function renderAdminAutotestRuns() {
  const partyId = appState.activeParty?.id;
  const visibleRuns = appState.adminAutotestRuns.filter((run) => run.source_party_id === partyId);
  els.adminAutotestRunsList.innerHTML = visibleRuns.length
    ? visibleRuns.map((run) => adminAutotestRow(run)).join("")
    : `<div class="admin-empty">Для выбранной партии автотестов пока нет.</div>`;
}

function adminAutotestRow(run) {
  const profile = appState.adminAutotestProfiles.find((item) => item.id === run.player_model_profile_id);
  const statusLabels = {
    running: "выполняется",
    stopping: "останавливается",
    stopped: "остановлен",
    completed: "завершён",
    failed: "ошибка",
  };
  const active = ["running", "stopping"].includes(run.status);
  const details = [
    `${run.completed_turns}/${run.requested_turns} ходов`,
    run.fallback_turns ? `safe fallback: ${run.fallback_turns}` : "",
    profile ? `${providerLabel(profile.provider)} · ${profile.title}` : run.player_model_profile_id,
    run.current_phase === "player" ? "ход игрока" : run.current_phase === "narrator" ? "ответ ведущего" : "",
    run.error || "",
  ].filter(Boolean).join(" · ");
  return `<div class="admin-row autotest-row">
    <div>
      <strong>${escapeHtml(statusLabels[run.status] || run.status)}</strong>
      <span>${escapeHtml(details)}</span>
    </div>
    <div class="row-actions">
      <button class="text-button" type="button" data-autotest-action="open" data-run-id="${escapeHtml(run.id)}">Открыть</button>
      ${active ? `<button class="text-button danger-text" type="button" data-autotest-action="stop" data-run-id="${escapeHtml(run.id)}">Стоп</button>` : ""}
    </div>
  </div>`;
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

function renderByok() {
  if (!els.byokKeysList || !els.byokKeyForm) return;
  const party = appState.activeParty;
  els.byokKeyForm.querySelectorAll("input, select, button").forEach((node) => {
    node.disabled = !party || Boolean(appState.activeBranch);
  });
  if (!party) {
    els.byokKeysList.innerHTML = `<div class="admin-empty">Сначала выберите партию.</div>`;
    return;
  }
  els.byokKeysList.innerHTML = appState.byokKeys.length
    ? appState.byokKeys.map((key) => byokKeyRow(key)).join("")
    : `<div class="admin-empty">У этой партии нет BYOK-ключей.</div>`;
}

function byokKeyRow(key) {
  return `<div class="admin-row">
    <div>
      <strong>${escapeHtml(key.label)}</strong>
      <span>${escapeHtml(providerLabel(key.provider))} · ${escapeHtml(key.is_default ? "default" : "backup")} · ...${escapeHtml(key.secret_hint || "----")}</span>
    </div>
    <div class="row-actions">
      <button class="text-button" type="button" data-byok-action="default" data-key-id="${escapeHtml(key.id)}" ${key.is_default ? "disabled" : ""}>Default</button>
      <button class="text-button danger-text" type="button" data-byok-action="delete" data-key-id="${escapeHtml(key.id)}">Удалить</button>
    </div>
  </div>`;
}

function renderServiceModel() {
  const payload = appState.serviceModelSettings;
  if (!isAdmin() || !payload) return;
  els.serviceModelSelect.innerHTML = (payload.choices || []).map((choice) => {
    const price = choice.provider === "local"
      ? "бесплатно"
      : `$${formatPrice(choice.input_price)}/$${formatPrice(choice.output_price)} за 1M`;
    const unavailable = choice.available ? "" : " · недоступна";
    return `<option value="${escapeHtml(choice.id)}" ${choice.id === payload.choice_id ? "selected" : ""} ${choice.available ? "" : "disabled"}>${escapeHtml(choice.title)} · ${escapeHtml(price + unavailable)}</option>`;
  }).join("");
  renderServiceModelMeta();
}

function renderServiceModelMeta() {
  const payload = appState.serviceModelSettings;
  if (!payload || !els.serviceModelMeta) return;
  const choice = (payload.choices || []).find((item) => item.id === els.serviceModelSelect.value) || payload.selected;
  if (!choice) return;
  const context = Number(choice.context_tokens || 0).toLocaleString("ru-RU");
  const price = choice.provider === "local"
    ? "Локально, без оплаты API"
    : `OpenRouter: $${formatPrice(choice.input_price)} вход / $${formatPrice(choice.output_price)} выход за 1M токенов`;
  els.serviceModelMeta.textContent = `${choice.model} · контекст ${context} · ${price}`;
}

function formatPrice(value) {
  return Number(value || 0).toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
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
  els.worldMarkdownInput.value = "";
  els.worldMarkdownStatus.textContent = WORLD_MARKDOWN_HELP;
  appState.worldMarkdownImport = null;
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
  const scenarioType = selectedRadioValue("scenarioType", "");
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
  const worldSource = selectedRadioValue("worldSource");
  const worldPrompt = worldSource === "prompt";
  const worldMarkdown = worldSource === "markdown_file";
  const generatedWorld = worldPrompt || worldMarkdown;
  const characterPrompt = selectedRadioValue("characterSource") === "prompt";
  els.worldReadyFields.classList.toggle("hidden", generatedWorld);
  els.worldPromptFields.classList.toggle("hidden", !generatedWorld);
  els.worldPromptTextFields.classList.toggle("hidden", !worldPrompt);
  els.worldMarkdownFields.classList.toggle("hidden", !worldMarkdown);
  els.worldSelect.toggleAttribute("required", !generatedWorld);
  els.worldPromptInput.toggleAttribute("required", worldPrompt);
  els.worldMarkdownInput.toggleAttribute("required", worldMarkdown);
  els.characterDescriptionLabel.textContent = characterPrompt ? "Prompt персонажа" : "Описание готового персонажа";
  els.characterDescriptionHint.textContent = characterPrompt
    ? "Опиши роль, характер, ограничения и стартовые ресурсы. Gateway сохранит это в profile персонажа."
    : generatedWorld
      ? "Для созданного мира используется стандартная роль игрока; можно заменить ее своим описанием."
      : "Берется роль игрока из worldpack; можно слегка поправить перед стартом.";
  if (!characterPrompt) {
    syncReadyCharacterDescription();
  }
  renderWorldPreview();
  renderModelPreview();
}

function renderWorldPreview() {
  const worldSource = selectedRadioValue("worldSource");
  if (worldSource === "prompt") {
    const title = els.worldPromptTitleInput.value.trim() || "Свой мир";
    const prompt = els.worldPromptInput.value.trim() || "Опиши мир, стартовую ситуацию, тон и ограничения.";
    els.worldPreview.innerHTML = `<strong>${escapeHtml(title)}</strong><br>${escapeHtml(prompt)}`;
    syncGeneratedWorldPartyTitle(title);
    return;
  }
  if (worldSource === "markdown_file") {
    const imported = appState.worldMarkdownImport;
    const title = els.worldPromptTitleInput.value.trim() || imported?.title || "Мир из Markdown";
    if (!imported) {
      els.worldPreview.innerHTML = `<strong>${escapeHtml(title)}</strong><br>Выбери файл .md, чтобы проверить размер и содержимое.`;
      syncGeneratedWorldPartyTitle(title);
      return;
    }
    const preview = imported.content.slice(0, WORLD_MARKDOWN_PREVIEW_CHARS);
    const suffix = imported.content.length > preview.length ? "\n…" : "";
    els.worldPreview.innerHTML = `<strong>${escapeHtml(title)}</strong><br><span>${escapeHtml(imported.name)} · ${imported.content.length.toLocaleString("ru-RU")} символов · ${formatFileSize(imported.size)}</span><div class="world-markdown-preview">${escapeHtml(preview + suffix)}</div>`;
    syncGeneratedWorldPartyTitle(title);
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

function syncGeneratedWorldPartyTitle(title) {
  const previousAuto = els.partyTitleInput.dataset.autoValue || "";
  if (els.partyTitleInput.value && els.partyTitleInput.value !== previousAuto && els.partyTitleInput.value !== "Новая партия") return;
  const nextAuto = `${title}: партия`;
  els.partyTitleInput.value = nextAuto;
  els.partyTitleInput.dataset.autoValue = nextAuto;
}

async function loadWorldMarkdownFile() {
  appState.worldMarkdownImport = null;
  const file = els.worldMarkdownInput.files?.[0];
  if (!file) {
    els.worldMarkdownStatus.textContent = WORLD_MARKDOWN_HELP;
    renderWorldPreview();
    return;
  }
  try {
    const imported = await readWorldMarkdownFile(file);
    appState.worldMarkdownImport = imported;
    els.worldMarkdownStatus.textContent = `${imported.name} · ${imported.content.length.toLocaleString("ru-RU")} символов · ${formatFileSize(imported.size)}`;
    if (!els.worldPromptTitleInput.value.trim()) {
      els.worldPromptTitleInput.value = imported.title;
    }
  } catch (error) {
    els.worldMarkdownInput.value = "";
    els.worldMarkdownStatus.textContent = error.message;
    showToast(error.message);
  }
  renderWorldPreview();
}

async function readWorldMarkdownFile(file) {
  if (!/\.md$/i.test(file.name || "")) {
    throw new Error("Можно загрузить только файл с расширением .md.");
  }
  if (file.size > WORLD_MARKDOWN_MAX_BYTES) {
    throw new Error("Markdown-файл больше 1 МиБ.");
  }
  const content = (await file.text()).replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").trim();
  if (!content) {
    throw new Error("Markdown-файл пуст.");
  }
  if (content.includes("\u0000")) {
    throw new Error("Markdown-файл должен быть обычным текстом без NUL-байтов.");
  }
  if (content.length > WORLD_MARKDOWN_MAX_CHARS) {
    throw new Error(`Markdown-файл содержит больше ${WORLD_MARKDOWN_MAX_CHARS.toLocaleString("ru-RU")} символов.`);
  }
  const title = file.name.replace(/\.md$/i, "").replace(/[_-]+/g, " ").trim().slice(0, 160) || "Мир из Markdown";
  return { name: file.name, size: file.size, content, title };
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} КиБ`;
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
  const scenarioType = selectedRadioValue("scenarioType", "");
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
  const worldSource = selectedRadioValue("worldSource");
  if (worldSource === "ready") {
    if (!els.worldSelect.value) throw new Error("Выбери готовый мир.");
    const pack = selectedWorldpack();
    if (!pack) throw new Error("Готовый мир не найден.");
    return pack;
  }
  const title = els.worldPromptTitleInput.value.trim() || els.partyTitleInput.value.trim() || "Свой мир";
  let payload;
  if (worldSource === "prompt") {
    const prompt = els.worldPromptInput.value.trim();
    if (!prompt) throw new Error("Заполни prompt мира.");
    if (prompt.length > WORLD_PROMPT_MAX_CHARS) throw new Error(`Ручной prompt не должен превышать ${WORLD_PROMPT_MAX_CHARS} символов.`);
    payload = { title, prompt, source: "text" };
  } else if (worldSource === "markdown_file") {
    const file = els.worldMarkdownInput.files?.[0];
    if (!file) throw new Error("Выбери Markdown-файл мира.");
    const imported = appState.worldMarkdownImport || (await readWorldMarkdownFile(file));
    appState.worldMarkdownImport = imported;
    payload = { title, prompt: imported.content, source: "markdown_file", source_filename: imported.name };
  } else {
    throw new Error("Выбери источник мира.");
  }
  const result = await apiPost("/api/worldpacks/prompt", payload);
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
  try {
    await globalThis.TrainingArtifacts?.flush();
  } catch (error) {
    showToast(error.message);
    return;
  }
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
  const payload = {
    target: isPlayer ? "player" : "npc",
    character_id: isPlayer ? null : (selectedNpc || els.characterEditId.value.trim()),
    name: els.characterEditName.value.trim() || null,
    status: els.characterEditStatus.value.trim() || null,
    location: els.characterEditLocation.value.trim() || null,
    current_goal: els.characterEditGoal.value.trim() || null,
    confirm: false,
  };
  if (isCompactRpCharacterEditor(appState.activeParty?.scenario_type)) {
    return payload;
  }
  const trust = els.characterEditTrust.value === "" ? null : Number(els.characterEditTrust.value);
  const fear = els.characterEditFear.value === "" ? null : Number(els.characterEditFear.value);
  return {
    ...payload,
    attitude_to_player: els.characterEditAttitude.value.trim() || null,
    loyalty: els.characterEditLoyalty.value.trim() || null,
    trust,
    fear,
    knowledge: els.characterEditKnowledge.value.trim() ? els.characterEditKnowledge.value : null,
    obligations: els.characterEditObligations.value.trim() ? els.characterEditObligations.value : null,
    hard_constraints: els.characterEditHardConstraints.value.trim() ? els.characterEditHardConstraints.value : null,
    secrets: els.characterEditSecrets.value.trim() ? els.characterEditSecrets.value : null,
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
  ].some((value) => value !== null && value !== undefined && value !== "");
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
    payload.trust !== null && payload.trust !== undefined ? `Доверие к игроку: ${payload.trust}` : "",
    payload.fear !== null && payload.fear !== undefined ? `Страх: ${payload.fear}` : "",
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

async function createByokKey(event) {
  event.preventDefault();
  const partyId = appState.activeParty?.id;
  if (!partyId || appState.activeBranch) return;
  try {
    setBusy(true, "Сохраняю BYOK для выбранной партии...");
    await apiPost(`/api/parties/${encodeURIComponent(partyId)}/byok`, {
      label: els.byokKeyLabelInput.value.trim(),
      api_key: els.byokKeyInput.value,
      provider: els.byokKeyProviderSelect.value,
      is_default: true,
    });
    els.byokKeyLabelInput.value = "";
    els.byokKeyInput.value = "";
    const keys = await apiGet(`/api/parties/${encodeURIComponent(partyId)}/byok`);
    if (appState.activeParty?.id !== partyId) return;
    appState.byokKeys = keys.api_keys || [];
    const models = await apiGet("/api/model-profiles");
    appState.modelProfiles = models.model_profiles || [];
    renderMeta();
    renderByok();
    showToast("BYOK сохранён только для выбранной партии.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function handleByokKeyAction(event) {
  const button = event.target.closest("[data-byok-action]");
  const partyId = appState.activeParty?.id;
  if (!button || !partyId || appState.activeBranch) return;
  const keyId = button.dataset.keyId;
  const action = button.dataset.byokAction;
  try {
    setBusy(true, "Обновляю BYOK выбранной партии...");
    if (action === "default") {
      await apiPatch(`/api/parties/${encodeURIComponent(partyId)}/byok/${encodeURIComponent(keyId)}`, { is_default: true });
      showToast("Default BYOK обновлён для этой партии.");
    } else if (action === "delete") {
      const ok = window.confirm("Удалить BYOK из выбранной партии?");
      if (!ok) return;
      await apiDelete(`/api/parties/${encodeURIComponent(partyId)}/byok/${encodeURIComponent(keyId)}`);
      showToast("BYOK удалён из этой партии.");
    }
    const keys = await apiGet(`/api/parties/${encodeURIComponent(partyId)}/byok`);
    if (appState.activeParty?.id === partyId) appState.byokKeys = keys.api_keys || [];
    renderByok();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function createAdminAutotest(event) {
  event.preventDefault();
  if (!isAdmin()) return;
  if (!appState.activeParty) {
    showToast("Сначала выберите партию.");
    return;
  }
  if (appState.activeBranch) {
    showToast("Сначала вернитесь в основную линию партии.");
    return;
  }
  const turnCount = Number(els.adminAutotestTurnsInput.value);
  if (!Number.isInteger(turnCount) || turnCount < 1 || turnCount > 30) {
    showToast("Количество ходов должно быть от 1 до 30.");
    return;
  }
  try {
    setBusy(true, "Сохраняю checkpoint и создаю ветку автотеста...");
    const result = await apiPost("/api/admin/autotests", {
      source_party_id: appState.activeParty.id,
      player_prompt: els.adminAutotestPromptInput.value.trim(),
      turn_count: turnCount,
      player_model_profile_id: els.adminAutotestModelSelect.value,
    });
    appState.adminAutotestRuns = [
      result.run,
      ...appState.adminAutotestRuns.filter((run) => run.id !== result.run.id),
    ];
    if (result.branch && !appState.branches.some((branch) => branch.id === result.branch.id)) {
      appState.branches.unshift(result.branch);
      renderMemoryTools();
    }
    renderAdminAutotestRuns();
    pollAdminAutotest(result.run.id);
    showToast("Автотест запущен в отдельной ветке от checkpoint. Основная линия не изменяется.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function handleAdminAutotestAction(event) {
  const button = event.target.closest("[data-autotest-action]");
  if (!button || !isAdmin()) return;
  const run = appState.adminAutotestRuns.find((item) => item.id === button.dataset.runId);
  if (!run) return;
  try {
    if (button.dataset.autotestAction === "stop") {
      const result = await apiPost(`/api/admin/autotests/${run.id}/stop`, {});
      replaceAdminAutotestRun(result.run);
      renderAdminAutotestRuns();
      pollAdminAutotest(run.id);
      showToast("Автотест остановится на безопасной границе текущего LLM-запроса.");
      return;
    }
    if (button.dataset.autotestAction === "open") {
      if (run.branch_id) {
        await openPartyBranch(run.source_party_id, run.branch_id);
        showToast("Открыта ветка автотеста внутри исходной партии.");
        return;
      }
      if (!appState.parties.some((party) => party.id === run.test_party_id)) {
        const response = await apiGet(`/api/parties/${run.test_party_id}`);
        appState.parties.unshift(response.party);
        renderPartyList();
      }
      await selectParty(run.test_party_id);
      showToast("Открыт legacy-прогон, созданный до поддержки веток.");
    }
  } catch (error) {
    showToast(error.message);
  }
}

function replaceAdminAutotestRun(run) {
  appState.adminAutotestRuns = [
    run,
    ...appState.adminAutotestRuns.filter((item) => item.id !== run.id),
  ].sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)));
}

function pollAdminAutotest(runId) {
  if (autotestPollingTasks[runId]) return autotestPollingTasks[runId];
  const sourcePartyId = appState.adminAutotestRuns.find((run) => run.id === runId)?.source_party_id;
  if (!sourcePartyId) return null;
  const task = (async () => {
    try {
      for (let attempt = 0; attempt < 3600 && isAdmin(); attempt += 1) {
        await delay(3000);
        if (appState.activeParty?.id !== sourcePartyId) return;
        const response = await apiGet(adminAutotestsUrl(sourcePartyId));
        if (appState.activeParty?.id !== sourcePartyId) return;
        appState.adminAutotestRuns = (response.runs || []).filter((run) => run.source_party_id === sourcePartyId);
        renderAdminAutotestRuns();
        renderMessageControls();
        const run = appState.adminAutotestRuns.find((item) => item.id === runId);
        if (run?.branch_id && appState.activeBranch?.id === run.branch_id) {
          await reloadActiveBranch();
        }
        if (!run || !["running", "stopping"].includes(run.status)) {
          if (run && appState.activeParty?.id === run.source_party_id) {
            const branches = await apiGet(`/api/parties/${run.source_party_id}/branches`);
            appState.branches = branches.branches || [];
            renderMemoryTools();
            renderBranchReadOnlyControls();
          }
          return;
        }
      }
    } catch (error) {
      showToast(`Статус автотеста не обновлён: ${error.message}`);
    } finally {
      delete autotestPollingTasks[runId];
    }
  })();
  autotestPollingTasks[runId] = task;
  return task;
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

async function createLoreCard(event) {
  event.preventDefault();
  if (!appState.activeParty) return;
  const keywords = els.loreCardKeywords.value.split(",").map((item) => item.trim()).filter(Boolean);
  try {
    setBusy(true, "Сохраняю Lore Card...");
    await apiPost(`/api/parties/${appState.activeParty.id}/lore-cards`, {
      title: els.loreCardTitle.value.trim(),
      content: els.loreCardContent.value.trim(),
      keywords,
      always_on: els.loreCardAlwaysOn.checked,
      enabled: true,
      source_turn_ids: [],
    });
    els.loreCardForm.reset();
    await reloadActiveParty();
    openPanelFor(els.loreCardList);
    showToast("Lore Card добавлена. Она не меняет canonical state.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function handleLoreCardAction(event) {
  const button = event.target.closest("[data-lore-action]");
  if (!button || !appState.activeParty) return;
  const cardId = button.dataset.cardId;
  const action = button.dataset.loreAction;
  if (action === "archive" && !window.confirm("Архивировать Lore Card? Исходная история и state не изменятся.")) return;
  try {
    setBusy(true, action === "archive" ? "Архивирую Lore Card..." : "Обновляю Lore Card...");
    const payload = action === "archive" ? { archived: true } : { enabled: button.dataset.enabled !== "true" };
    await apiPatch(`/api/parties/${appState.activeParty.id}/lore-cards/${cardId}`, payload);
    await reloadActiveParty();
    openPanelFor(els.loreCardList);
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function createCheckpoint(event) {
  event.preventDefault();
  if (!appState.activeParty) return;
  try {
    setBusy(true, "Создаю checkpoint...");
    await apiPost(`/api/parties/${appState.activeParty.id}/checkpoints`, { label: els.checkpointLabel.value.trim() });
    els.checkpointForm.reset();
    await reloadActiveParty();
    openPanelFor(els.checkpointList);
    showToast("Checkpoint создан без изменения истории.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function handleBranchAction(event) {
  const button = event.target.closest("[data-branch-id]");
  if (!button || !appState.activeParty) return;
  try {
    await openPartyBranch(appState.activeParty.id, button.dataset.branchId);
    showToast("Открыта ветка партии. Основная линия не изменяется.");
  } catch (error) {
    showToast(error.message);
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
    const source = content ? "current" : "last";
    setBusy(true, source === "current" ? "Проверяю prompt следующего хода..." : "Открываю prompt предыдущего запроса...");
    const result = await apiPost(`/api/parties/${appState.activeParty.id}/prompt/preview`, { content, source });
    appState.promptPreview = result.preview;
    renderPromptPreview();
    openPanelFor(els.promptPreview);
    showToast(source === "current" ? "Собран dry-run следующего хода." : "Prompt предыдущего хода открыт.");
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
  const timestamp = pendingMessageForParty(appState.activeParty?.id)?.createdAt || Date.now();
  els.chatLog.insertAdjacentHTML("beforeend", messageHtml("user", "Игрок", text, timestamp));
  els.chatLog.insertAdjacentHTML("beforeend", pendingMessageHtml(requestId, "GM формирует ответ...", timestamp));
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

function appendPendingStartMessage(requestId, status) {
  if (!appState.history) appState.history = { turns: [] };
  const timestamp = pendingMessageForParty(appState.activeParty?.id)?.createdAt || Date.now();
  els.chatLog.insertAdjacentHTML("beforeend", pendingMessageHtml(requestId, status, timestamp));
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
        replacePendingMessage(partyId, requestId, turn.narrative_response);
        clearPendingMessage(partyId);
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
  const activeAutotest = appState.adminAutotestRuns.find(
    (run) => run.test_party_id === appState.activeParty?.id && ["running", "stopping"].includes(run.status),
  );
  const branchReadOnly = Boolean(appState.activeBranch);
  const locked = Boolean(pendingMessage || activeAutotest || branchReadOnly);
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
    const status = pendingMessage?.status
      || (branchReadOnly
        ? "Открыта изолированная ветка. Вернитесь в основную линию, выбрав партию слева."
        : "Автотест управляет этой партией; ручной ввод временно отключён.");
    els.messageStatus.innerHTML = `${pendingMessage ? `<span class="spinner" aria-hidden="true"></span>` : ""}<span>${escapeHtml(status)}</span>`;
  } else {
    els.messageStatus.classList.add("hidden");
    els.messageStatus.innerHTML = "";
  }
}

function pendingMessageHtml(requestId, status, timestamp = Date.now()) {
  return `<article class="message assistant pending" data-pending-id="${escapeHtml(requestId)}">
    <div class="role">GM</div>
    <div class="pending-line"><span class="spinner" aria-hidden="true"></span><span class="pending-text">${escapeHtml(status)}</span></div>
    ${messageTimeHtml(timestamp)}
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

async function saveServiceModel(event) {
  event.preventDefault();
  if (!isAdmin()) return;
  try {
    setBusy(true, "Применяю служебную модель ко всему RP Stack...");
    appState.serviceModelSettings = await apiPatch("/api/admin/global-settings/service-model", {
      choice_id: els.serviceModelSelect.value,
    });
    renderServiceModel();
    showToast("Служебная модель применена ко всем текущим и будущим партиям.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function apiPut(path, body) {
  return api(path, {
    method: "PUT",
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
    const error = new Error(apiErrorMessage(data.detail, response.status));
    error.status = response.status;
    error.detail = data.detail;
    error.response = data;
    throw error;
  }
  return data;
}

function apiErrorMessage(detail, status) {
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        const location = Array.isArray(item?.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
        const message = typeof item?.msg === "string" ? item.msg.replace(/^Value error,\s*/i, "") : "";
        return [location, message].filter(Boolean).join(": ");
      })
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  if (detail && typeof detail === "object" && detail.code === "provider_rate_limited") {
    const retryAfter = Number(detail.retry_after_seconds);
    const retryHint = Number.isFinite(retryAfter) && retryAfter > 0
      ? ` \u041f\u043e\u0432\u0442\u043e\u0440\u0438 \u0447\u0435\u0440\u0435\u0437 ${Math.ceil(retryAfter)} \u0441.`
      : " \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439 \u0447\u0443\u0442\u044c \u043f\u043e\u0437\u0436\u0435 \u0438\u043b\u0438 \u0432\u044b\u0431\u0435\u0440\u0438 \u0434\u0440\u0443\u0433\u0443\u044e \u043c\u043e\u0434\u0435\u043b\u044c.";
    return `\u041c\u043e\u0434\u0435\u043b\u044c ${detail.model || ""} \u0432\u0440\u0435\u043c\u0435\u043d\u043d\u043e \u043e\u0433\u0440\u0430\u043d\u0438\u0447\u0438\u043b\u0430 \u0437\u0430\u043f\u0440\u043e\u0441\u044b.${retryHint}`;
  }
  return typeof detail === "string" ? detail : `HTTP ${status}`;
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
  for (const [family, familyProfiles] of openRouterFamilyGroups(remaining)) {
    groups.push(`<optgroup label="${escapeHtml(family)}">${familyProfiles.map(modelOptionHtml).join("")}</optgroup>`);
  }
  return groups.join("");
}

function openRouterFamilyGroups(profiles) {
  const families = new Map();
  for (const profile of profiles) {
    const family = openRouterFamilyLabel(profile);
    const group = families.get(family) || [];
    group.push(profile);
    families.set(family, group);
  }
  return [...families.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([family, familyProfiles]) => [
      family,
      familyProfiles.sort((left, right) => String(left.title || "").localeCompare(String(right.title || ""))),
    ]);
}

function openRouterFamilyLabel(profile) {
  const model = String(profile?.model || "").toLowerCase();
  const publisher = String(profile?.params?.publisher || "").trim();
  if (model.startsWith("anthropic/")) return "Anthropic / Claude";
  if (model.startsWith("google/")) return "Google / Gemini \u0438 Gemma";
  if (model.startsWith("deepseek/")) return "DeepSeek";
  if (model.startsWith("qwen/")) return "Qwen";
  if (model.startsWith("meta-llama/") || model.startsWith("meta/")) return "Meta / Llama";
  if (model.startsWith("mistralai/")) return "Mistral";
  if (model.startsWith("openai/")) return "OpenAI";
  if (model.startsWith("sao10k/")) return "Sao10K / Euryale";
  if (model.startsWith("thedrummer/")) return "TheDrummer";
  return publisher || model.split("/")[0] || "\u041f\u0440\u043e\u0447\u0438\u0435 OpenRouter";
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
  const referenceCost = normalizeProvider(profile?.provider || "") === "openrouter" && !profile?.is_free
    ? modelReferenceTurnCost(profile, MODEL_PRICE_WARM_CACHE_RATIO)
    : null;
  const costEstimate = referenceCost === null
    ? ""
    : `≈${formatTurnCost(referenceCost)}${perMillionPriceValue(profile?.pricing_input_cache_read) === null ? "" : " warm"}`;
  const markers = [
    featuredOpenRouterRank(profile) ? "TOP" : "",
    profile.is_free ? "FREE" : modelCostTier(profile),
    costEstimate,
    profile.rp_specialized ? "RP" : "",
  ].filter(Boolean);
  const prefix = markers.length ? `[${markers.join(" · ")}] ` : "";
  return `<option value="${escapeHtml(profile.id)}">${escapeHtml(prefix + profile.title)}</option>`;
}

const MODEL_PRICE_REFERENCE_INPUT_TOKENS = 95_000;
const MODEL_PRICE_REFERENCE_OUTPUT_TOKENS = 650;
const MODEL_PRICE_WARM_CACHE_RATIO = 0.8;
const MODEL_PRICE_TIER_LIMITS = [0.02, 0.05, 0.1, 0.25];

function modelCostTierLabel(profile) {
  if (profile.is_free) return "FREE";
  const tier = modelCostTier(profile);
  if (!tier) return "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d";
  const warmCost = modelReferenceTurnCost(profile, MODEL_PRICE_WARM_CACHE_RATIO);
  const hasCacheDiscount = perMillionPriceValue(profile?.pricing_input_cache_read) !== null;
  const cacheNote = hasCacheDiscount ? "80% cached input" : "\u0431\u0435\u0437 \u0441\u043a\u0438\u0434\u043a\u0438 \u043a\u044d\u0448\u0430";
  return `${tier} \u00b7 \u2248 ${formatTurnCost(warmCost)}/\u0445\u043e\u0434 (${cacheNote})`;
}

function modelCostTier(profile) {
  if (normalizeProvider(profile?.provider || "") !== "openrouter" || profile?.is_free) return "";
  const cost = modelReferenceTurnCost(profile, MODEL_PRICE_WARM_CACHE_RATIO);
  if (cost === null) return "";
  const tierIndex = MODEL_PRICE_TIER_LIMITS.findIndex((limit) => cost <= limit);
  const tier = tierIndex < 0 ? 5 : tierIndex + 1;
  return "$".repeat(tier);
}

function modelReferenceTurnCost(profile, cacheRatio = 0) {
  const prompt = perMillionPriceValue(profile?.pricing_prompt);
  const completion = perMillionPriceValue(profile?.pricing_completion);
  if (prompt === null || completion === null) return null;
  const cacheRead = perMillionPriceValue(profile?.pricing_input_cache_read);
  const ratio = cacheRead === null ? 0 : Math.min(1, Math.max(0, Number(cacheRatio) || 0));
  const inputCost = MODEL_PRICE_REFERENCE_INPUT_TOKENS * (prompt * (1 - ratio) + (cacheRead || 0) * ratio);
  const outputCost = MODEL_PRICE_REFERENCE_OUTPUT_TOKENS * completion;
  return (inputCost + outputCost) / 1_000_000;
}

function modelPricingLabel(profile) {
  if (profile.is_free) return "FREE";
  const prompt = perMillionPrice(profile.pricing_prompt);
  const completion = perMillionPrice(profile.pricing_completion);
  if (!prompt && !completion) return "не указана каталогом";
  const tokenPrices = `$${prompt || "?"}/M input · $${completion || "?"}/M output`;
  if (normalizeProvider(profile?.provider || "") !== "openrouter") return tokenPrices;
  const cacheRead = perMillionPrice(profile.pricing_input_cache_read);
  const coldCost = modelReferenceTurnCost(profile, 0);
  const warmCost = modelReferenceTurnCost(profile, MODEL_PRICE_WARM_CACHE_RATIO);
  const cachePrice = cacheRead ? ` · $${cacheRead}/M cached input` : "";
  const warmLabel = cacheRead
    ? `warm 80% ${formatTurnCost(warmCost)}`
    : "скидка cached input не заявлена";
  return `${tokenPrices}${cachePrice} · ход 95k/650: cold ${formatTurnCost(coldCost)} · ${warmLabel}`;
}

function formatTurnCost(value) {
  if (value === null || !Number.isFinite(value)) return "?";
  const digits = value < 0.01 ? 4 : value < 1 ? 3 : 2;
  return `$${value.toFixed(digits)}`;
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

function selectedRadioValue(name, fallback = "ready") {
  return document.querySelector(`input[name='${name}']:checked`)?.value || fallback;
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

function artifactInitials(value) {
  const name = String(value || "").replace(/<[^>]+>/g, " ").trim();
  const parts = name.split(/\s+/).filter(Boolean);
  return (parts.slice(0, 2).map((part) => part[0]).join("") || "?").toLocaleUpperCase("ru-RU");
}

function artifactChipsHtml(fields) {
  const items = [
    ["Вложения", fields["Вложения"]],
    ["Ссылки", fields["Ссылки"]],
  ].filter(([, value]) => value && String(value).trim().toLocaleLowerCase("ru-RU") !== "нет");
  if (!items.length) return "";
  return `<div class="artifact-chips">${items
    .map(([label, value]) => `<span class="artifact-chip artifact-chip-${label === "Ссылки" ? "link" : "attachment"}">${escapeHtml(`${label}: ${value}`)}</span>`)
    .join("")}</div>`;
}

function emailArtifactHtml(fields) {
  const subject = fields["Тема"] || "Без темы";
  const sender = fields["От"] || "Неизвестный отправитель";
  const recipient = fields["Кому"] || "не указано";
  const folder = fields["Канал"] ? `Входящие · ${fields["Канал"]}` : "Входящие";
  const signature = fields["Подпись"]
    ? `<div class="email-signature">${escapeHtml(fields["Подпись"])}</div>`
    : "";
  return `<section class="artifact-card email-card" aria-label="${escapeHtml(`Письмо: ${subject}`)}">
    <div class="email-chrome">
      <div class="artifact-appbar email-appbar"><span class="artifact-app-icon">O</span><strong>Outlook</strong><span>${escapeHtml(folder)}</span></div>
      <h3 class="email-subject">${escapeHtml(subject)}</h3>
      <div class="artifact-sender-row">
        <span class="artifact-avatar email-avatar">${escapeHtml(artifactInitials(sender))}</span>
        <div class="artifact-sender-copy"><strong>${escapeHtml(sender)}</strong><span>Кому: ${escapeHtml(recipient)}</span></div>
        <time>${escapeHtml(fields["Дата/время"] || "")}</time>
      </div>
      ${artifactChipsHtml(fields)}
    </div>
    <div class="email-body"><p>${escapeHtml(fields["Тело"] || "")}</p>${signature}</div>
  </section>`;
}

function messengerArtifactHtml(fields) {
  const chat = fields["Чат"] || "Личный чат";
  const sender = fields["От"] || "Неизвестный отправитель";
  const recipient = fields["Кому"]
    ? `<small class="messenger-recipient">Получатель: ${escapeHtml(fields["Кому"])}</small>`
    : "";
  return `<section class="artifact-card messenger-card" aria-label="${escapeHtml(`Сообщение: ${chat}`)}">
    <div class="messenger-header">
      <span class="telegram-mark">➤</span>
      <div><strong>${escapeHtml(chat)}</strong><span>${escapeHtml(fields["Канал"] || "мессенджер")}</span></div>
    </div>
    <div class="messenger-conversation">
      <span class="artifact-avatar telegram-avatar">${escapeHtml(artifactInitials(sender))}</span>
      <div class="telegram-stack">
        <strong class="telegram-sender">${escapeHtml(sender)}</strong>
        <div class="telegram-bubble"><p>${escapeHtml(fields["Текст"] || "")}</p><span class="telegram-meta">${escapeHtml(fields["Дата/время"] || "")}</span></div>
      </div>
    </div>
    ${artifactChipsHtml(fields)}
    ${recipient}
  </section>`;
}

function narrativeContentHtml(content) {
  const parser = globalThis.StructuredContent?.parseStructuredNarrative;
  const segments = parser ? parser(content) : [{ type: "text", text: String(content || "") }];
  const hasArtifacts = segments.some((segment) => segment.type === "email" || segment.type === "messenger");
  if (!hasArtifacts) return { html: escapeHtml(content || ""), hasArtifacts: false };
  const html = segments
    .map((segment) => {
      if (segment.type === "email") return emailArtifactHtml(segment.fields);
      if (segment.type === "messenger") return messengerArtifactHtml(segment.fields);
      return `<div class="narrative-copy">${escapeHtml(segment.text || "")}</div>`;
    })
    .join("");
  return { html, hasArtifacts: true };
}

function messageTimeHtml(timestamp) {
  const formatted = globalThis.MessageTime?.formatMessageTime(timestamp);
  if (!formatted) return "";
  return `<time class="message-time" datetime="${escapeHtml(formatted.iso)}" title="${escapeHtml(formatted.title)}">${escapeHtml(formatted.text)}</time>`;
}

function messageHtml(kind, role, content, timestamp = Date.now(), feedback = null, artifacts = [], turnId = null) {
  const rendered = kind === "assistant"
    ? narrativeContentHtml(content)
    : { html: escapeHtml(content || ""), hasArtifacts: false };
  const feedbackHtml = feedback?.turnId
    ? turnFeedbackControlsHtml(feedback)
    : "";
  const artifactHost = kind === "assistant" && turnId && Array.isArray(artifacts) && artifacts.length
    ? `<div class="training-artifact-host" data-training-artifact-turn="${escapeHtml(Number(turnId))}"></div>`
    : "";
  const html = `<article class="message ${kind}${rendered.hasArtifacts ? " message-rich" : ""}">
    <div class="role">${escapeHtml(role)}</div>
    <div class="message-content">${rendered.html}</div>
    ${artifactHost}
    ${messageTimeHtml(timestamp)}
    ${feedbackHtml}
  </article>`;
  return html;
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
