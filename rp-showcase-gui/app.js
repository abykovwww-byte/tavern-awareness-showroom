const appState = {
  scenarios: [],
  runs: [],
  selectedScenario: null,
  currentRun: null,
  adminUser: null,
  adminScenarios: [],
  worldpacks: [],
  modelProfiles: [],
  editingScenario: null,
  coverFile: null,
};

const views = {
  storefront: document.querySelector("#storefrontView"),
  start: document.querySelector("#startView"),
  leaderboard: document.querySelector("#leaderboardView"),
  chat: document.querySelector("#chatView"),
  adminLogin: document.querySelector("#adminLoginView"),
  admin: document.querySelector("#adminView"),
};

const els = {
  homeButton: document.querySelector("#homeButton"),
  adminButton: document.querySelector("#adminButton"),
  scenarioGrid: document.querySelector("#scenarioGrid"),
  scenarioCount: document.querySelector("#scenarioCount"),
  scenarioEmpty: document.querySelector("#scenarioEmpty"),
  resumeSection: document.querySelector("#resumeSection"),
  resumeList: document.querySelector("#resumeList"),
  selectedCover: document.querySelector("#selectedCover"),
  selectedMode: document.querySelector("#selectedMode"),
  selectedTitle: document.querySelector("#selectedTitle"),
  selectedDescription: document.querySelector("#selectedDescription"),
  selectedWorld: document.querySelector("#selectedWorld"),
  selectedLeaderboardButton: document.querySelector("#selectedLeaderboardButton"),
  startForm: document.querySelector("#startForm"),
  characterNameInput: document.querySelector("#characterNameInput"),
  characterPromptInput: document.querySelector("#characterPromptInput"),
  leaderboardOptInInput: document.querySelector("#leaderboardOptInInput"),
  leaderboardTitle: document.querySelector("#leaderboardTitle"),
  leaderboardScoreLabel: document.querySelector("#leaderboardScoreLabel"),
  leaderboardBody: document.querySelector("#leaderboardBody"),
  leaderboardEmpty: document.querySelector("#leaderboardEmpty"),
  leaderboardStartButton: document.querySelector("#leaderboardStartButton"),
  chatMode: document.querySelector("#chatMode"),
  chatTitle: document.querySelector("#chatTitle"),
  chatCharacter: document.querySelector("#chatCharacter"),
  chatLeaderboardButton: document.querySelector("#chatLeaderboardButton"),
  chatThread: document.querySelector("#chatThread"),
  messageForm: document.querySelector("#messageForm"),
  messageInput: document.querySelector("#messageInput"),
  adminLoginForm: document.querySelector("#adminLoginForm"),
  adminUsernameInput: document.querySelector("#adminUsernameInput"),
  adminPasswordInput: document.querySelector("#adminPasswordInput"),
  adminLogoutButton: document.querySelector("#adminLogoutButton"),
  newScenarioButton: document.querySelector("#newScenarioButton"),
  adminScenarioList: document.querySelector("#adminScenarioList"),
  scenarioForm: document.querySelector("#scenarioForm"),
  scenarioFormTitle: document.querySelector("#scenarioFormTitle"),
  scenarioStatusPill: document.querySelector("#scenarioStatusPill"),
  scenarioTitleInput: document.querySelector("#scenarioTitleInput"),
  scenarioDescriptionInput: document.querySelector("#scenarioDescriptionInput"),
  scenarioTypeSelect: document.querySelector("#scenarioTypeSelect"),
  scenarioStatusSelect: document.querySelector("#scenarioStatusSelect"),
  providerSelect: document.querySelector("#providerSelect"),
  modelSelect: document.querySelector("#modelSelect"),
  presetWorldField: document.querySelector("#presetWorldField"),
  promptWorldField: document.querySelector("#promptWorldField"),
  worldpackSelect: document.querySelector("#worldpackSelect"),
  worldPromptInput: document.querySelector("#worldPromptInput"),
  leaderboardEnabledInput: document.querySelector("#leaderboardEnabledInput"),
  leaderboardLabelInput: document.querySelector("#leaderboardLabelInput"),
  leaderboardMetricSelect: document.querySelector("#leaderboardMetricSelect"),
  leaderboardPathInput: document.querySelector("#leaderboardPathInput"),
  coverInput: document.querySelector("#coverInput"),
  coverPreview: document.querySelector("#coverPreview"),
  deleteCoverButton: document.querySelector("#deleteCoverButton"),
  toast: document.querySelector("#toast"),
  busy: document.querySelector("#busy"),
  busyText: document.querySelector("#busyText"),
};

const scenarioTypeLabels = { rp: "RP", novel: "Совместный роман", training: "Обучение" };
const statusLabels = { draft: "Черновик", published: "Опубликован", archived: "Архив" };

function bindEvents() {
  els.homeButton.addEventListener("click", () => showView("storefront"));
  els.adminButton.addEventListener("click", openAdmin);
  document.querySelectorAll("[data-open-view]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.openView));
  });
  els.selectedLeaderboardButton.addEventListener("click", () => openLeaderboard(appState.selectedScenario));
  els.leaderboardStartButton.addEventListener("click", () => openStart(appState.selectedScenario));
  els.chatLeaderboardButton.addEventListener("click", () => openLeaderboard(appState.currentRun?.scenario));
  els.startForm.addEventListener("submit", createRun);
  els.messageForm.addEventListener("submit", sendMessage);
  els.adminLoginForm.addEventListener("submit", loginAdmin);
  els.adminLogoutButton.addEventListener("click", logoutAdmin);
  els.newScenarioButton.addEventListener("click", newScenario);
  els.scenarioForm.addEventListener("submit", saveScenario);
  els.providerSelect.addEventListener("change", () => renderModelOptions());
  els.scenarioStatusSelect.addEventListener("change", renderStatusPill);
  els.leaderboardMetricSelect.addEventListener("change", renderLeaderboardMetricFields);
  document.querySelectorAll('input[name="worldSource"]').forEach((radio) => {
    radio.addEventListener("change", renderWorldSource);
  });
  els.coverInput.addEventListener("change", previewCover);
  els.deleteCoverButton.addEventListener("click", deleteCover);
}

async function boot() {
  bindEvents();
  setBusy(true, "Загружаю витрину...");
  try {
    const [scenarios, runs] = await Promise.all([
      apiGet("/api/showroom/scenarios"),
      apiGet("/api/showroom/runs"),
    ]);
    appState.scenarios = scenarios.scenarios || [];
    appState.runs = runs.runs || [];
    renderStorefront();
    if (location.hash === "#admin") await openAdmin();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function showView(name) {
  Object.entries(views).forEach(([key, view]) => view.classList.toggle("active", key === name));
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (name !== "admin" && name !== "adminLogin" && location.hash === "#admin") {
    history.replaceState(null, "", location.pathname);
  }
}

function renderStorefront() {
  els.scenarioGrid.replaceChildren();
  els.scenarioCount.textContent = `${appState.scenarios.length} ${plural(appState.scenarios.length, "сценарий", "сценария", "сценариев")}`;
  els.scenarioEmpty.classList.toggle("hidden", appState.scenarios.length > 0);
  for (const scenario of appState.scenarios) {
    els.scenarioGrid.append(scenarioCard(scenario));
  }
  renderRuns();
}

function scenarioCard(scenario) {
  const article = document.createElement("article");
  article.className = "scenario-card";

  const cover = document.createElement("div");
  cover.className = "cover";
  if (scenario.cover_url) {
    const image = document.createElement("img");
    image.src = scenario.cover_url;
    image.alt = "";
    cover.append(image);
  }
  const coverCopy = document.createElement("div");
  coverCopy.className = "cover-copy";
  const mode = document.createElement("strong");
  mode.textContent = scenarioTypeLabels[scenario.scenario_type] || scenario.scenario_type;
  const world = document.createElement("p");
  world.textContent = scenario.world?.title || "Мир не указан";
  coverCopy.append(mode, world);
  cover.append(coverCopy);

  const body = document.createElement("div");
  body.className = "scenario-card-body";
  const heading = document.createElement("h3");
  heading.textContent = scenario.title;
  const description = document.createElement("p");
  description.textContent = scenario.description || "Автор сценария пока не добавил описание.";
  const actions = document.createElement("div");
  actions.className = "scenario-card-actions";
  const startButton = button("Начать", "button button-primary");
  startButton.addEventListener("click", () => openStart(scenario));
  const leadersButton = button("Лидеры", "button button-quiet");
  leadersButton.disabled = !scenario.leaderboard_enabled;
  leadersButton.addEventListener("click", () => openLeaderboard(scenario));
  actions.append(startButton, leadersButton);
  body.append(heading, description, actions);
  article.append(cover, body);
  return article;
}

function renderRuns() {
  els.resumeList.replaceChildren();
  els.resumeSection.classList.toggle("hidden", appState.runs.length === 0);
  for (const run of appState.runs) {
    const row = document.createElement("div");
    row.className = "resume-item";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${run.scenario.title} · ${run.display_name}`;
    const detail = document.createElement("p");
    detail.textContent = `Последняя активность: ${formatDate(run.updated_at)}`;
    copy.append(title, detail);
    const continueButton = button("Продолжить", "button");
    continueButton.addEventListener("click", () => openRun(run));
    row.append(copy, continueButton);
    els.resumeList.append(row);
  }
}

function openStart(scenario) {
  if (!scenario) return;
  appState.selectedScenario = scenario;
  els.selectedMode.textContent = scenarioTypeLabels[scenario.scenario_type] || scenario.scenario_type;
  els.selectedTitle.textContent = scenario.title;
  els.selectedDescription.textContent = scenario.description || "";
  els.selectedWorld.textContent = scenario.world?.title || "Внутренний prompt-мир";
  els.selectedCover.style.backgroundImage = scenario.cover_url ? `url("${scenario.cover_url}")` : "";
  els.selectedLeaderboardButton.disabled = !scenario.leaderboard_enabled;
  els.leaderboardOptInInput.checked = Boolean(scenario.leaderboard_enabled);
  showView("start");
  els.characterNameInput.focus();
}

async function openLeaderboard(scenario) {
  if (!scenario?.id || !scenario.leaderboard_enabled) return;
  appState.selectedScenario = appState.scenarios.find((item) => item.id === scenario.id) || scenario;
  setBusy(true, "Загружаю таблицу лидеров...");
  try {
    const result = await apiGet(`/api/showroom/scenarios/${encodeURIComponent(scenario.id)}/leaderboard`);
    els.leaderboardTitle.textContent = result.scenario_title || scenario.title;
    els.leaderboardScoreLabel.textContent = result.label || scenario.leaderboard_label || "Очки";
    els.leaderboardBody.replaceChildren();
    for (const entry of result.entries || []) {
      const row = document.createElement("tr");
      const rankCell = document.createElement("td");
      const rank = document.createElement("span");
      rank.className = "rank";
      rank.textContent = entry.rank;
      rankCell.append(rank);
      const nameCell = document.createElement("td");
      nameCell.textContent = entry.display_name;
      const scoreCell = document.createElement("td");
      scoreCell.className = "number";
      scoreCell.textContent = formatScore(entry.score);
      const updatedCell = document.createElement("td");
      updatedCell.textContent = formatDate(entry.updated_at);
      row.append(rankCell, nameCell, scoreCell, updatedCell);
      els.leaderboardBody.append(row);
    }
    els.leaderboardEmpty.classList.toggle("hidden", Boolean((result.entries || []).length));
    showView("leaderboard");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function createRun(event) {
  event.preventDefault();
  if (!appState.selectedScenario) return;
  setBusy(true, "Создаю героя и стартовую сцену...");
  try {
    const created = await apiPost(`/api/showroom/scenarios/${encodeURIComponent(appState.selectedScenario.id)}/runs`, {
      character_name: els.characterNameInput.value.trim(),
      character_prompt: els.characterPromptInput.value.trim(),
      leaderboard_opt_in: els.leaderboardOptInInput.checked,
      client_request_id: makeRequestId("showroom-create"),
    });
    appState.currentRun = created.run;
    const started = await apiPost(
      `/api/showroom/runs/${encodeURIComponent(created.run.id)}/start`,
      { idempotency_key: `showroom-start:${created.run.id}` },
      { "X-Request-ID": makeRequestId("showroom-start") },
    );
    await refreshRuns();
    await openRun(appState.runs.find((run) => run.id === created.run.id) || created.run, started.message?.content);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function refreshRuns() {
  const result = await apiGet("/api/showroom/runs");
  appState.runs = result.runs || [];
  renderRuns();
}

async function openRun(run, fallbackOpening = "") {
  if (!run) return;
  setBusy(true, "Открываю историю...");
  try {
    const details = await apiGet(`/api/showroom/runs/${encodeURIComponent(run.id)}`);
    const history = await apiGet(`/api/showroom/runs/${encodeURIComponent(run.id)}/history?limit=300`);
    appState.currentRun = details.run;
    const scenario = appState.scenarios.find((item) => item.id === details.run.scenario.id) || details.run.scenario;
    appState.selectedScenario = scenario;
    els.chatMode.textContent = scenarioTypeLabels[scenario.scenario_type] || scenario.scenario_type;
    els.chatTitle.textContent = scenario.title;
    els.chatCharacter.textContent = details.run.display_name;
    els.chatLeaderboardButton.disabled = !details.run.scenario.leaderboard_enabled;
    renderHistory(history.turns || [], fallbackOpening);
    showView("chat");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function renderHistory(turns, fallbackOpening = "") {
  els.chatThread.replaceChildren();
  for (const turn of turns) {
    if (turn.player_message && !String(turn.player_message).startsWith("[AUTO_START]")) {
      appendMessage("player", appState.currentRun.display_name, turn.player_message);
    }
    if (turn.narrative_response) appendMessage("gm", "Рассказчик", turn.narrative_response);
  }
  if (!turns.length && fallbackOpening) appendMessage("gm", "Рассказчик", fallbackOpening);
}

function appendMessage(kind, author, content, pending = false) {
  const article = document.createElement("article");
  article.className = `message message-${kind}${pending ? " message-pending" : ""}`;
  const heading = document.createElement("strong");
  heading.textContent = author;
  const text = document.createElement("p");
  text.textContent = content;
  article.append(heading, text);
  els.chatThread.append(article);
  article.scrollIntoView({ behavior: "smooth", block: "end" });
  return article;
}

async function sendMessage(event) {
  event.preventDefault();
  const content = els.messageInput.value.trim();
  if (!content || !appState.currentRun) return;
  els.messageInput.value = "";
  appendMessage("player", appState.currentRun.display_name, content);
  const pending = appendMessage("gm", "Рассказчик", "Ответ формируется...", true);
  try {
    const result = await apiPost(
      `/api/showroom/runs/${encodeURIComponent(appState.currentRun.id)}/messages`,
      { content, idempotency_key: makeRequestId("showroom-message") },
      { "X-Request-ID": makeRequestId("showroom-request") },
    );
    pending.remove();
    appendMessage("gm", "Рассказчик", result.message?.content || "Ответ получен без текста.");
    await refreshRuns();
  } catch (error) {
    pending.remove();
    appendMessage("gm", "Система", `Не удалось получить ответ: ${error.message}`);
  }
}

async function openAdmin() {
  setBusy(true, "Проверяю доступ администратора...");
  try {
    const auth = await apiGet("/api/auth/me");
    appState.adminUser = auth.user;
    if (!auth.authenticated || !auth.user) {
      showView("adminLogin");
      location.hash = "admin";
      return;
    }
    if (auth.user.role !== "admin") throw new Error("Требуется роль администратора Gateway.");
    await loadAdminData();
    showView("admin");
    location.hash = "admin";
  } catch (error) {
    showToast(error.message, true);
    showView("adminLogin");
  } finally {
    setBusy(false);
  }
}

async function loginAdmin(event) {
  event.preventDefault();
  setBusy(true, "Вхожу...");
  try {
    const result = await apiPost("/api/auth/login", {
      username: els.adminUsernameInput.value.trim(),
      password: els.adminPasswordInput.value,
    });
    if (result.user?.role !== "admin") throw new Error("У пользователя нет роли администратора.");
    els.adminPasswordInput.value = "";
    await openAdmin();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function logoutAdmin() {
  try {
    await apiPost("/api/auth/logout", {});
  } finally {
    appState.adminUser = null;
    showView("storefront");
  }
}

async function loadAdminData() {
  const [scenarios, worlds, models] = await Promise.all([
    apiGet("/api/admin/showroom/scenarios"),
    apiGet("/api/worldpacks"),
    apiGet("/api/model-profiles"),
  ]);
  appState.adminScenarios = scenarios.scenarios || [];
  appState.worldpacks = worlds.worldpacks || [];
  appState.modelProfiles = models.model_profiles || [];
  renderProviderOptions();
  renderWorldpackOptions();
  renderAdminList();
  if (appState.editingScenario) {
    const fresh = appState.adminScenarios.find((item) => item.id === appState.editingScenario.id);
    fresh ? editScenario(fresh) : newScenario();
  } else if (appState.adminScenarios.length) {
    editScenario(appState.adminScenarios[0]);
  } else {
    newScenario();
  }
}

function renderAdminList() {
  els.adminScenarioList.replaceChildren();
  if (!appState.adminScenarios.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Сценариев пока нет.";
    els.adminScenarioList.append(empty);
    return;
  }
  for (const scenario of appState.adminScenarios) {
    const item = button("", "admin-list-item");
    item.classList.toggle("active", appState.editingScenario?.id === scenario.id);
    const title = document.createElement("strong");
    title.textContent = scenario.title;
    const detail = document.createElement("small");
    detail.textContent = `${scenarioTypeLabels[scenario.scenario_type]} · ${statusLabels[scenario.status]} · ${scenario.world.title}`;
    item.append(title, detail);
    item.addEventListener("click", () => editScenario(scenario));
    els.adminScenarioList.append(item);
  }
}

function newScenario() {
  appState.editingScenario = null;
  appState.coverFile = null;
  els.scenarioForm.reset();
  els.scenarioFormTitle.textContent = "Новый сценарий";
  els.scenarioTitleInput.value = "";
  els.scenarioDescriptionInput.value = "";
  els.scenarioTypeSelect.value = "rp";
  els.scenarioStatusSelect.value = "draft";
  els.leaderboardEnabledInput.checked = true;
  els.leaderboardMetricSelect.value = "state_path";
  els.leaderboardPathInput.value = "meta.turn";
  els.leaderboardLabelInput.value = "Очки";
  const preset = document.querySelector('input[name="worldSource"][value="preset"]');
  preset.checked = true;
  renderProviderOptions();
  renderModelOptions();
  renderWorldSource();
  renderLeaderboardMetricFields();
  renderStatusPill();
  els.coverPreview.textContent = "Обложка не выбрана";
  els.coverPreview.style.backgroundImage = "";
  els.deleteCoverButton.classList.add("hidden");
  renderAdminList();
  els.scenarioTitleInput.focus();
}

function editScenario(scenario) {
  appState.editingScenario = scenario;
  appState.coverFile = null;
  els.scenarioFormTitle.textContent = scenario.title;
  els.scenarioTitleInput.value = scenario.title;
  els.scenarioDescriptionInput.value = scenario.description || "";
  els.scenarioTypeSelect.value = scenario.scenario_type;
  els.scenarioStatusSelect.value = scenario.status;
  els.leaderboardEnabledInput.checked = scenario.leaderboard_enabled;
  els.leaderboardMetricSelect.value = scenario.leaderboard_metric;
  els.leaderboardPathInput.value = scenario.leaderboard_state_path || "meta.turn";
  els.leaderboardLabelInput.value = scenario.leaderboard_label || "Очки";
  const worldSource = document.querySelector(`input[name="worldSource"][value="${scenario.world_source}"]`);
  if (worldSource) worldSource.checked = true;
  els.worldpackSelect.value = scenario.worldpack_id;
  els.worldPromptInput.value = scenario.world_prompt || "";
  els.providerSelect.value = scenario.model_profile?.provider || "";
  renderModelOptions(scenario.model_profile_id);
  renderWorldSource();
  renderLeaderboardMetricFields();
  renderStatusPill();
  els.coverInput.value = "";
  els.coverPreview.textContent = scenario.cover_url ? "Текущая обложка" : "Обложка не выбрана";
  els.coverPreview.style.backgroundImage = scenario.cover_url ? `url("${scenario.cover_url}")` : "";
  els.deleteCoverButton.classList.toggle("hidden", !scenario.cover_url);
  renderAdminList();
}

function renderProviderOptions() {
  const selected = els.providerSelect.value;
  const providers = [...new Set(appState.modelProfiles.map((profile) => profile.provider))].sort();
  els.providerSelect.replaceChildren();
  for (const provider of providers) {
    const option = document.createElement("option");
    option.value = provider;
    option.textContent = providerLabel(provider);
    els.providerSelect.append(option);
  }
  if (providers.includes(selected)) els.providerSelect.value = selected;
  renderModelOptions();
}

function renderModelOptions(preferredId = "") {
  const provider = els.providerSelect.value;
  const selected = preferredId || els.modelSelect.value;
  const profiles = appState.modelProfiles.filter((profile) => profile.provider === provider);
  els.modelSelect.replaceChildren();
  for (const profile of profiles) {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.title;
    els.modelSelect.append(option);
  }
  if (profiles.some((profile) => profile.id === selected)) els.modelSelect.value = selected;
}

function renderWorldpackOptions() {
  els.worldpackSelect.replaceChildren();
  for (const pack of appState.worldpacks) {
    const option = document.createElement("option");
    option.value = pack.id;
    option.textContent = pack.title;
    els.worldpackSelect.append(option);
  }
}

function renderWorldSource() {
  const source = selectedWorldSource();
  els.presetWorldField.classList.toggle("hidden", source !== "preset");
  els.promptWorldField.classList.toggle("hidden", source !== "prompt");
  els.worldpackSelect.required = source === "preset";
  els.worldPromptInput.required = source === "prompt";
}

function renderStatusPill() {
  els.scenarioStatusPill.textContent = statusLabels[els.scenarioStatusSelect.value] || els.scenarioStatusSelect.value;
}

function renderLeaderboardMetricFields() {
  const statePath = els.leaderboardMetricSelect.value === "state_path";
  els.leaderboardPathInput.disabled = !statePath;
}

function previewCover() {
  appState.coverFile = els.coverInput.files?.[0] || null;
  if (!appState.coverFile) return;
  els.coverPreview.textContent = "";
  els.coverPreview.style.backgroundImage = `url("${URL.createObjectURL(appState.coverFile)}")`;
}

async function saveScenario(event) {
  event.preventDefault();
  const source = selectedWorldSource();
  const payload = {
    title: els.scenarioTitleInput.value.trim(),
    description: els.scenarioDescriptionInput.value.trim(),
    status: els.scenarioStatusSelect.value,
    scenario_type: els.scenarioTypeSelect.value,
    model_profile_id: els.modelSelect.value,
    world_source: source,
    worldpack_id: source === "preset" ? els.worldpackSelect.value : null,
    world_prompt: source === "prompt" ? els.worldPromptInput.value.trim() : null,
    leaderboard_enabled: els.leaderboardEnabledInput.checked,
    leaderboard_metric: els.leaderboardMetricSelect.value,
    leaderboard_state_path: els.leaderboardPathInput.value.trim() || "meta.turn",
    leaderboard_label: els.leaderboardLabelInput.value.trim() || "Очки",
    sort_order: appState.editingScenario?.sort_order ?? 100,
  };
  setBusy(true, "Сохраняю сценарий...");
  try {
    const response = appState.editingScenario
      ? await apiPatch(`/api/admin/showroom/scenarios/${encodeURIComponent(appState.editingScenario.id)}`, payload)
      : await apiPost("/api/admin/showroom/scenarios", payload);
    if (appState.coverFile) {
      await apiRaw(
        `/api/admin/showroom/scenarios/${encodeURIComponent(response.scenario.id)}/cover`,
        "PUT",
        appState.coverFile,
        { "Content-Type": appState.coverFile.type || "application/octet-stream" },
      );
    }
    appState.editingScenario = response.scenario;
    await loadAdminData();
    const publicData = await apiGet("/api/showroom/scenarios");
    appState.scenarios = publicData.scenarios || [];
    renderStorefront();
    showToast("Сценарий сохранён.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function deleteCover() {
  if (!appState.editingScenario) return;
  setBusy(true, "Удаляю обложку...");
  try {
    await apiDelete(`/api/admin/showroom/scenarios/${encodeURIComponent(appState.editingScenario.id)}/cover`);
    await loadAdminData();
    showToast("Обложка удалена.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function selectedWorldSource() {
  return document.querySelector('input[name="worldSource"]:checked')?.value || "preset";
}

function button(text, className) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = className;
  element.textContent = text;
  return element;
}

function providerLabel(provider) {
  return { openrouter: "OpenRouter", nvidia: "NVIDIA", gemini: "Gemini", local: "Local LLM" }[provider] || provider;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

function formatScore(value) {
  return typeof value === "number" ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(value) : value;
}

function plural(value, one, few, many) {
  const mod10 = value % 10;
  const mod100 = value % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function makeRequestId(prefix) {
  return `${prefix}:${crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

function setBusy(active, text = "Загрузка...") {
  els.busy.classList.toggle("hidden", !active);
  els.busyText.textContent = text;
}

let toastTimer = null;
function showToast(message, error = false) {
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.classList.toggle("error", error);
  els.toast.classList.remove("hidden");
  toastTimer = setTimeout(() => els.toast.classList.add("hidden"), 5000);
}

async function apiGet(path) {
  return apiRequest(path, { method: "GET" });
}

async function apiPost(path, body, headers = {}) {
  return apiRequest(path, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });
}

async function apiPatch(path, body) {
  return apiRequest(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

async function apiDelete(path) {
  return apiRequest(path, { method: "DELETE" });
}

async function apiRaw(path, method, body, headers = {}) {
  return apiRequest(path, { method, headers, body });
}

async function apiRequest(path, options) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail : payload;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || `HTTP ${response.status}`));
  }
  return payload;
}

boot();
