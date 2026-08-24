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
  scenarioEditorMode: "create",
  coverFile: null,
  workspace: null,
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
  employeePositionField: document.querySelector("#employeePositionField"),
  employeePositionInput: document.querySelector("#employeePositionInput"),
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
  chatLayout: document.querySelector("#chatLayout"),
  chatThread: document.querySelector("#chatThread"),
  corporatePortal: document.querySelector("#corporatePortal"),
  chatSidebars: document.querySelector("#chatSidebars"),
  portalTitle: document.querySelector("#portalTitle"),
  portalContext: document.querySelector("#portalContext"),
  portalCharacterList: document.querySelector("#portalCharacterList"),
  trainingWorkspace: document.querySelector("#trainingWorkspace"),
  workspaceTree: document.querySelector("#workspaceTree"),
  workspacePreview: document.querySelector("#workspacePreview"),
  messageForm: document.querySelector("#messageForm"),
  messageInput: document.querySelector("#messageInput"),
  adminLoginForm: document.querySelector("#adminLoginForm"),
  adminUsernameInput: document.querySelector("#adminUsernameInput"),
  adminPasswordInput: document.querySelector("#adminPasswordInput"),
  adminLogoutButton: document.querySelector("#adminLogoutButton"),
  newScenarioButton: document.querySelector("#newScenarioButton"),
  adminScenarioList: document.querySelector("#adminScenarioList"),
  scenarioForm: document.querySelector("#scenarioForm"),
  scenarioSaveButton: document.querySelector("#scenarioSaveButton"),
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
  trainingCapabilitiesField: document.querySelector("#trainingCapabilitiesField"),
  interactiveLinksEnabledInput: document.querySelector("#interactiveLinksEnabledInput"),
  interactiveWorkspaceEnabledInput: document.querySelector("#interactiveWorkspaceEnabledInput"),
  interactiveLinksHint: document.querySelector("#interactiveLinksHint"),
  interactiveWorkspaceHint: document.querySelector("#interactiveWorkspaceHint"),
  leaderboardEnabledInput: document.querySelector("#leaderboardEnabledInput"),
  leaderboardLabelInput: document.querySelector("#leaderboardLabelInput"),
  coverInput: document.querySelector("#coverInput"),
  coverPreview: document.querySelector("#coverPreview"),
  deleteCoverButton: document.querySelector("#deleteCoverButton"),
  toast: document.querySelector("#toast"),
  busy: document.querySelector("#busy"),
  busyText: document.querySelector("#busyText"),
};

const scenarioTypeLabels = { rp: "RP", novel: "Архивный Novel", training: "Обучение" };
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
  els.chatThread.addEventListener("click", handleTurnFeedbackClick);
  els.adminLoginForm.addEventListener("submit", loginAdmin);
  els.adminLogoutButton.addEventListener("click", logoutAdmin);
  els.newScenarioButton.addEventListener("click", newScenario);
  els.scenarioForm.addEventListener("submit", saveScenario);
  els.providerSelect.addEventListener("change", () => renderModelOptions());
  els.scenarioTypeSelect.addEventListener("change", renderTrainingCapabilities);
  els.worldpackSelect.addEventListener("change", renderTrainingCapabilities);
  els.scenarioStatusSelect.addEventListener("change", renderStatusPill);
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
    title.textContent = run.scenario?.title || "Сценарий";
    const player = document.createElement("p");
    player.className = "resume-player";
    player.textContent = `Игрок: ${run.display_name || "Имя не указано"}`;
    const detail = document.createElement("p");
    detail.textContent = `Последняя активность: ${formatDate(run.updated_at)}`;
    copy.append(title, player, detail);
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
  renderCover(els.selectedCover, scenario.cover_url);
  els.selectedLeaderboardButton.disabled = !scenario.leaderboard_enabled;
  els.leaderboardOptInInput.checked = Boolean(scenario.leaderboard_enabled);
  const needsEmployeePosition = Boolean(scenario.portal?.requires_employee_position);
  els.employeePositionField.classList.toggle("hidden", !needsEmployeePosition);
  els.employeePositionInput.required = needsEmployeePosition;
  els.employeePositionInput.value = "";
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
  let pending = null;
  setBusy(true, "Создаю героя и стартовую сцену...");
  try {
    const created = await apiPost(`/api/showroom/scenarios/${encodeURIComponent(appState.selectedScenario.id)}/runs`, {
      character_name: els.characterNameInput.value.trim(),
      character_prompt: els.characterPromptInput.value.trim(),
      employee_position: els.employeePositionInput.value.trim(),
      leaderboard_opt_in: els.leaderboardOptInInput.checked,
      client_request_id: makeRequestId("showroom-create"),
    });
    appState.currentRun = created.run;
    await refreshRuns();
    const run = appState.runs.find((item) => item.id === created.run.id) || created.run;
    await openRun(run);
    setBusy(false);
    els.messageInput.disabled = true;
    pending = appendMessage("gm", "Рассказчик", "Готовлю стартовую сцену...", true);
    const started = await apiPost(
      `/api/showroom/runs/${encodeURIComponent(created.run.id)}/start`,
      { idempotency_key: `showroom-start:${created.run.id}` },
      { "X-Request-ID": makeRequestId("showroom-start") },
    );
    pending.remove();
    await openRun(run, started.message?.content);
    await refreshRuns();
  } catch (error) {
    pending?.remove();
    if (appState.currentRun && views.chat.classList.contains("active")) {
      appendMessage("gm", "Система", `Не удалось подготовить стартовую сцену: ${error.message}`);
    }
    showToast(error.message, true);
  } finally {
    els.messageInput.disabled = false;
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
    renderCorporatePortal(details.run.portal, details.run.employee_position);
    if (details.run.interactive_workspace_enabled) {
      await refreshWorkspace();
    } else {
      appState.workspace = null;
      renderWorkspace(null);
    }
    renderHistory(history.turns || [], fallbackOpening);
    showView("chat");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function renderCorporatePortal(portal, employeePosition = "") {
  const characters = Array.isArray(portal?.characters) ? portal.characters.slice(0, 5) : [];
  const visible = characters.length > 0;
  els.corporatePortal.classList.toggle("hidden", !visible);
  updateChatSidebars();
  els.portalCharacterList.replaceChildren();
  if (!visible) return;

  els.portalTitle.textContent = portal.title || "Корпоративный портал";
  els.portalContext.textContent = employeePosition
    ? `Контакты команды для должности «${employeePosition}»`
    : "Контакты участников сценария";
  for (const character of characters) {
    els.portalCharacterList.append(portalCharacterCard(character));
  }
}

function updateChatSidebars() {
  const visible = !els.corporatePortal.classList.contains("hidden") || !els.trainingWorkspace.classList.contains("hidden");
  els.chatSidebars.classList.toggle("hidden", !visible);
  els.chatLayout.classList.toggle("without-portal", !visible);
}

async function refreshWorkspace() {
  if (!appState.currentRun?.interactive_workspace_enabled) return;
  const result = await apiGet(`/api/showroom/runs/${encodeURIComponent(appState.currentRun.id)}/workspace`);
  appState.workspace = result.workspace;
  renderWorkspace(result.workspace);
}

function renderWorkspace(workspace) {
  const folders = Array.isArray(workspace?.folders) ? workspace.folders : [];
  const files = Array.isArray(workspace?.files) ? workspace.files : [];
  const visible = Boolean(appState.currentRun?.interactive_workspace_enabled);
  els.trainingWorkspace.classList.toggle("hidden", !visible);
  els.workspaceTree.replaceChildren();
  els.workspacePreview.replaceChildren();
  els.workspacePreview.classList.add("hidden");
  if (visible) {
    for (const folder of folders) {
      const section = document.createElement("section");
      section.className = "workspace-folder";
      const heading = document.createElement("h3");
      heading.textContent = `📁 ${folder.label}`;
      section.append(heading);
      for (const file of files.filter((item) => item.folder_id === folder.id)) {
        const open = button(`${workspaceFileIcon(file)} ${file.display_name}${file.extension || ""}`, "workspace-file");
        open.type = "button";
        open.addEventListener("click", () => openWorkspaceFile(file));
        section.append(open);
      }
      els.workspaceTree.append(section);
    }
    if (!files.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "В этой партии пока нет доступных файлов.";
      els.workspaceTree.append(empty);
    }
  }
  updateChatSidebars();
}

function workspaceFileIcon(file) {
  return { spreadsheet: "▦", pdf: "▤", image: "▧", document: "▥", text: "≡" }[file.media_family] || "▥";
}

async function workspaceEvent(file, eventType) {
  return apiPost(`/api/showroom/runs/${encodeURIComponent(appState.currentRun.id)}/workspace-events`, {
    event_id: makeRequestId(`workspace-${eventType}`),
    file_id: file.file_id,
    file_revision: file.file_revision,
    event_type: eventType,
  });
}

async function openWorkspaceFile(file) {
  try {
    if ((file.actions || []).includes("file_opened")) await workspaceEvent(file, "file_opened");
    els.workspacePreview.replaceChildren();
    els.workspacePreview.classList.remove("hidden");
    const title = document.createElement("h3");
    title.textContent = `${file.display_name}${file.extension || ""}`;
    const body = document.createElement("div");
    body.className = "workspace-file-body";
    for (const [label, value] of Object.entries(file.slots || {})) {
      const block = document.createElement("section");
      const heading = document.createElement("strong");
      heading.textContent = label.replaceAll("_", " ");
      const text = document.createElement("p");
      text.textContent = value;
      block.append(heading, text);
      body.append(block);
    }
    const actions = document.createElement("div");
    actions.className = "workspace-actions";
    if ((file.actions || []).includes("file_downloaded")) {
      const download = button("Скачать", "button button-quiet");
      download.type = "button";
      download.addEventListener("click", async () => {
        await workspaceEvent(file, "file_downloaded");
        if (file.resource_sha256) {
          const link = document.createElement("a");
          link.href = `/api/showroom/runs/${encodeURIComponent(appState.currentRun.id)}/workspace/files/${encodeURIComponent(file.file_id)}/content`;
          link.download = `${file.display_name}${file.extension || ""}`;
          link.click();
          return;
        }
        const blob = new Blob([Object.values(file.slots || {}).join("\n\n")], { type: "text/plain;charset=utf-8" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `${file.display_name}${file.extension || ".txt"}`;
        link.click();
        URL.revokeObjectURL(link.href);
      });
      actions.append(download);
    }
    if ((file.actions || []).includes("file_reported")) {
      const report = button("Сообщить в ИБ", "button");
      report.type = "button";
      report.addEventListener("click", async () => {
        await workspaceEvent(file, "file_reported");
        showToast("Файл передан специалистам ИБ.");
      });
      actions.append(report);
    }
    if ((file.actions || []).includes("active_content_enabled")) {
      const activate = button("Включить содержимое", "button button-danger");
      activate.type = "button";
      activate.addEventListener("click", async () => {
        await workspaceEvent(file, "active_content_enabled");
        showToast("Активное содержимое отмечено как включённое.", true);
      });
      actions.append(activate);
    }
    els.workspacePreview.append(title, body, actions);
  } catch (error) {
    showToast(error.message, true);
  }
}

function portalCharacterCard(character) {
  const article = document.createElement("article");
  article.className = "portal-character-card";

  const header = document.createElement("div");
  header.className = "portal-character-header";
  const avatar = document.createElement("span");
  avatar.className = "portal-avatar";
  avatar.textContent = initials(character.display_name);
  const identity = document.createElement("div");
  const name = document.createElement("h3");
  name.textContent = character.display_name || "Сотрудник";
  const position = document.createElement("p");
  position.textContent = character.position || "Должность не указана";
  identity.append(name, position);
  header.append(avatar, identity);

  const details = document.createElement("div");
  details.className = "portal-details";
  const fields = [
    ["city", "⌂", "Город", false],
    ["birthday", "▣", "День рождения", false],
    ["phone", "☎", "Телефон", true],
    ["messenger", "➤", "Мессенджер", true],
    ["email", "✉", "Почта", true],
  ];
  for (const [field, icon, label, copyable] of fields) {
    const value = String(character[field] || "").trim();
    if (!value) continue;
    const row = document.createElement("div");
    row.className = "portal-detail-row";
    const mark = document.createElement("span");
    mark.className = "portal-detail-icon";
    mark.textContent = icon;
    mark.title = label;
    const text = document.createElement(field === "phone" || field === "email" ? "a" : "span");
    text.className = "portal-detail-value";
    text.textContent = value;
    if (field === "phone") text.href = `tel:${value.replace(/[^+\d]/g, "")}`;
    if (field === "email") text.href = `mailto:${value}`;
    row.append(mark, text);
    if (copyable) {
      const copy = button("□", "portal-copy-button");
      copy.type = "button";
      copy.title = `Скопировать: ${label.toLocaleLowerCase("ru-RU")}`;
      copy.setAttribute("aria-label", copy.title);
      copy.addEventListener("click", () => copyPortalValue(value));
      row.append(copy);
    }
    details.append(row);
  }

  article.append(header, details);
  return article;
}

async function copyPortalValue(value) {
  try {
    await navigator.clipboard.writeText(value);
  } catch (_error) {
    const field = document.createElement("textarea");
    field.value = value;
    field.className = "clipboard-proxy";
    document.body.append(field);
    field.select();
    document.execCommand("copy");
    field.remove();
  }
  showToast("Скопировано.");
}

function renderHistory(turns, fallbackOpening = "") {
  els.chatThread.replaceChildren();
  for (const turn of turns) {
    const autoStart = String(turn.player_message || "").startsWith("[AUTO_START]");
    if (turn.player_message && !autoStart) {
      appendMessage("player", appState.currentRun.display_name, turn.player_message, false, turn.created_at);
    }
    if (turn.narrative_response) {
      appendMessage(
        "gm",
        "Рассказчик",
        turn.narrative_response,
        false,
        turn.created_at,
        autoStart ? null : {
          turnId: turn.id,
          rating: turn.player_rating || (turn.player_liked ? "positive" : "none"),
        },
        turn.artifacts || [],
        turn.id,
      );
    }
  }
  if (!turns.length && fallbackOpening) appendMessage("gm", "Рассказчик", fallbackOpening);
}

async function refreshCurrentRunHistory() {
  if (!appState.currentRun) return;
  const history = await apiGet(`/api/showroom/runs/${encodeURIComponent(appState.currentRun.id)}/history?limit=300`);
  renderHistory(history.turns || []);
}

function initials(value) {
  const name = String(value || "").replace(/<[^>]+>/g, " ").trim();
  const parts = name.split(/\s+/).filter(Boolean);
  return (parts.slice(0, 2).map((part) => part[0]).join("") || "?").toLocaleUpperCase("ru-RU");
}

function appendArtifactChips(container, fields) {
  const items = [
    ["Вложения", fields["Вложения"]],
    ["Ссылки", fields["Ссылки"]],
  ].filter(([, value]) => value && String(value).trim().toLocaleLowerCase("ru-RU") !== "нет");
  if (!items.length) return;
  const row = document.createElement("div");
  row.className = "artifact-chips";
  for (const [label, value] of items) {
    const chip = document.createElement("span");
    chip.className = `artifact-chip artifact-chip-${label === "Ссылки" ? "link" : "attachment"}`;
    chip.textContent = `${label}: ${value}`;
    row.append(chip);
  }
  container.append(row);
}

function renderEmailArtifact(segment) {
  const fields = segment.fields;
  const card = document.createElement("section");
  card.className = "artifact-card email-card";
  card.setAttribute("aria-label", `Письмо: ${fields["Тема"] || "без темы"}`);

  const appbar = document.createElement("div");
  appbar.className = "artifact-appbar email-appbar";
  const appIcon = document.createElement("span");
  appIcon.className = "artifact-app-icon";
  appIcon.textContent = "O";
  const appName = document.createElement("strong");
  appName.textContent = "Outlook";
  const folder = document.createElement("span");
  folder.textContent = fields["Канал"] ? `Входящие · ${fields["Канал"]}` : "Входящие";
  appbar.append(appIcon, appName, folder);

  const subject = document.createElement("h3");
  subject.className = "email-subject";
  subject.textContent = fields["Тема"] || "Без темы";

  const senderRow = document.createElement("div");
  senderRow.className = "artifact-sender-row";
  const avatar = document.createElement("span");
  avatar.className = "artifact-avatar email-avatar";
  avatar.textContent = initials(fields["От"]);
  const senderCopy = document.createElement("div");
  senderCopy.className = "artifact-sender-copy";
  const sender = document.createElement("strong");
  sender.textContent = fields["От"] || "Неизвестный отправитель";
  const recipient = document.createElement("span");
  recipient.textContent = `Кому: ${fields["Кому"] || "не указано"}`;
  senderCopy.append(sender, recipient);
  const date = document.createElement("time");
  date.textContent = fields["Дата/время"] || "";
  senderRow.append(avatar, senderCopy, date);

  const chrome = document.createElement("div");
  chrome.className = "email-chrome";
  chrome.append(appbar, subject, senderRow);
  appendArtifactChips(chrome, fields);

  const body = document.createElement("div");
  body.className = "email-body";
  const bodyText = document.createElement("p");
  bodyText.textContent = fields["Тело"] || "";
  body.append(bodyText);
  if (fields["Подпись"]) {
    const signature = document.createElement("div");
    signature.className = "email-signature";
    signature.textContent = fields["Подпись"];
    body.append(signature);
  }

  card.append(chrome, body);
  return card;
}

function renderMessengerArtifact(segment) {
  const fields = segment.fields;
  const card = document.createElement("section");
  card.className = "artifact-card messenger-card";
  card.setAttribute("aria-label", `Сообщение: ${fields["Чат"] || "личный чат"}`);

  const header = document.createElement("div");
  header.className = "messenger-header";
  const plane = document.createElement("span");
  plane.className = "telegram-mark";
  plane.textContent = "➤";
  const title = document.createElement("div");
  const channel = document.createElement("strong");
  channel.textContent = fields["Чат"] || "Личный чат";
  const status = document.createElement("span");
  status.textContent = fields["Канал"] || "мессенджер";
  title.append(channel, status);
  header.append(plane, title);

  const conversation = document.createElement("div");
  conversation.className = "messenger-conversation";
  const avatar = document.createElement("span");
  avatar.className = "artifact-avatar telegram-avatar";
  avatar.textContent = initials(fields["От"]);
  const stack = document.createElement("div");
  stack.className = "telegram-stack";
  const sender = document.createElement("strong");
  sender.className = "telegram-sender";
  sender.textContent = fields["От"] || "Неизвестный отправитель";
  const bubble = document.createElement("div");
  bubble.className = "telegram-bubble";
  const message = document.createElement("p");
  message.textContent = fields["Текст"] || "";
  const meta = document.createElement("span");
  meta.className = "telegram-meta";
  meta.textContent = fields["Дата/время"] || "";
  bubble.append(message, meta);
  stack.append(sender, bubble);
  conversation.append(avatar, stack);

  card.append(header, conversation);
  appendArtifactChips(card, fields);
  if (fields["Кому"]) {
    const recipient = document.createElement("small");
    recipient.className = "messenger-recipient";
    recipient.textContent = `Получатель: ${fields["Кому"]}`;
    card.append(recipient);
  }
  return card;
}

function renderNarrativeContent(container, content) {
  const parser = globalThis.StructuredContent?.parseStructuredNarrative;
  const segments = parser ? parser(content) : [{ type: "text", text: String(content || "") }];
  const hasArtifacts = segments.some((segment) => segment.type === "email" || segment.type === "messenger");
  for (const segment of segments) {
    if (segment.type === "email") container.append(renderEmailArtifact(segment));
    else if (segment.type === "messenger") container.append(renderMessengerArtifact(segment));
    else {
      const text = document.createElement("p");
      text.className = "narrative-copy";
      text.textContent = segment.text;
      container.append(text);
    }
  }
  return hasArtifacts;
}

function messageTimeElement(timestamp) {
  const formatted = globalThis.MessageTime?.formatMessageTime(timestamp);
  if (!formatted) return null;
  const time = document.createElement("time");
  time.className = "message-time";
  time.dateTime = formatted.iso;
  time.title = formatted.title;
  time.textContent = formatted.text;
  return time;
}

function appendMessage(kind, author, content, pending = false, timestamp = Date.now(), feedback = null, artifacts = [], turnId = null) {
  const article = document.createElement("article");
  article.className = `message message-${kind}${pending ? " message-pending" : ""}`;
  const heading = document.createElement("strong");
  heading.textContent = author;
  const body = document.createElement("div");
  body.className = "message-content";
  let hasArtifacts = false;
  if (kind === "gm") {
    hasArtifacts = renderNarrativeContent(body, content);
  } else {
    const text = document.createElement("p");
    text.className = "narrative-copy";
    text.textContent = content;
    body.append(text);
  }
  if (hasArtifacts) article.classList.add("message-rich");
  const time = messageTimeElement(timestamp);
  article.append(heading, body);
  if (kind === "gm" && turnId && Array.isArray(artifacts) && artifacts.length) {
    const renderer = globalThis.TrainingArtifacts;
    const host = document.createElement("div");
    host.className = "training-artifact-host";
    let artifactsToMount = artifacts;
    let mountedInline = false;
    const messengerLinks = body.querySelectorAll(".messenger-card .artifact-chip-link");
    for (const linkChip of messengerLinks) {
      const matchingArtifact = renderer?.artifactForDisplayUrl?.(artifacts, linkChip.textContent);
      if (!matchingArtifact) continue;
      linkChip.replaceWith(host);
      artifactsToMount = [matchingArtifact];
      mountedInline = true;
      break;
    }
    if (!mountedInline) article.append(host);
    renderer?.mount(host, artifactsToMount, (payload) => apiPost(
      `/api/showroom/runs/${encodeURIComponent(appState.currentRun.id)}/artifact-events`,
      payload,
    ));
  }
  if (time) article.append(time);
  if (feedback?.turnId) article.append(turnFeedbackControl(feedback));
  els.chatThread.append(article);
  article.scrollIntoView({ behavior: "smooth", block: "end" });
  return article;
}

function turnFeedbackControl(feedback) {
  const row = document.createElement("div");
  row.className = "message-feedback";
  row.setAttribute("role", "group");
  row.setAttribute("aria-label", "Оценка связки реплик");
  for (const [rating, label] of [
    ["positive", "Хорошая связка реплик"],
    ["negative", "Неудачная связка реплик"],
  ]) {
    const button = document.createElement("button");
    button.className = `turn-feedback-button turn-feedback-${rating}`;
    button.type = "button";
    button.dataset.turnFeedback = String(feedback.turnId);
    button.dataset.rating = rating;
    button.setAttribute("aria-label", label);
    button.title = label;
    button.innerHTML = turnFeedbackIconHtml(rating);
    row.append(button);
  }
  updateTurnFeedbackControls(row, normalizeTurnFeedbackRating(feedback));
  return row;
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

async function handleTurnFeedbackClick(event) {
  const button = event.target.closest("[data-turn-feedback]");
  if (!button || !appState.currentRun) return;
  const turnId = Number(button.dataset.turnFeedback);
  if (!Number.isInteger(turnId) || turnId <= 0) return;
  const selectedRating = button.dataset.rating;
  const rating = button.getAttribute("aria-pressed") === "true" ? "none" : selectedRating;
  const controls = button.closest(".message-feedback");
  const buttons = [...controls.querySelectorAll("[data-turn-feedback]")];
  buttons.forEach((item) => { item.disabled = true; });
  try {
    const result = await apiPut(
      `/api/showroom/runs/${encodeURIComponent(appState.currentRun.id)}/turns/${turnId}/feedback`,
      { rating },
    );
    const saved = normalizeTurnFeedbackRating(result.feedback);
    updateTurnFeedbackControls(controls, saved);
    showToast({
      positive: "Связка отмечена как удачная.",
      negative: "Связка отмечена как неудачная.",
      none: "Оценка убрана.",
    }[saved]);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    buttons.forEach((item) => { item.disabled = false; });
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const content = els.messageInput.value.trim();
  if (!content || !appState.currentRun) return;
  try {
    await globalThis.TrainingArtifacts?.flush();
  } catch (error) {
    showToast(error.message, true);
    return;
  }
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
    try {
      await refreshCurrentRunHistory();
      await refreshWorkspace();
    } catch (_historyError) {
      appendMessage("gm", "Рассказчик", result.message?.content || "Ответ получен без текста.");
    }
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
  if (appState.scenarioEditorMode === "edit" && appState.editingScenario) {
    const fresh = appState.adminScenarios.find((item) => item.id === appState.editingScenario.id);
    fresh ? editScenario(fresh) : newScenario();
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
    const capabilities = scenario.scenario_type === "training"
      ? ` · ссылки ${scenario.interactive_links_enabled ? "вкл" : "выкл"} · диск ${scenario.interactive_workspace_enabled ? "вкл" : "выкл"}`
      : "";
    detail.textContent = `${scenarioTypeLabels[scenario.scenario_type]} · ${statusLabels[scenario.status]} · ${scenario.world.title}${capabilities}`;
    item.append(title, detail);
    item.addEventListener("click", () => editScenario(scenario));
    els.adminScenarioList.append(item);
  }
}

function newScenario() {
  appState.scenarioEditorMode = "create";
  appState.editingScenario = null;
  appState.coverFile = null;
  els.scenarioForm.reset();
  els.scenarioFormTitle.textContent = "Новый сценарий";
  els.scenarioSaveButton.textContent = "Создать сценарий";
  els.scenarioTitleInput.value = "";
  els.scenarioDescriptionInput.value = "";
  els.scenarioTypeSelect.value = "rp";
  els.scenarioStatusSelect.value = "draft";
  els.leaderboardEnabledInput.checked = true;
  els.interactiveLinksEnabledInput.checked = false;
  els.interactiveWorkspaceEnabledInput.checked = false;
  els.leaderboardLabelInput.value = "Очки";
  const preset = document.querySelector('input[name="worldSource"][value="preset"]');
  preset.checked = true;
  renderProviderOptions();
  renderModelOptions();
  renderWorldSource();
  renderTrainingCapabilities();
  renderStatusPill();
  els.coverPreview.textContent = "Обложка не выбрана";
  renderCover(els.coverPreview, "", els.coverPreview.textContent);
  els.deleteCoverButton.classList.add("hidden");
  renderAdminList();
  els.scenarioTitleInput.focus();
}

function editScenario(scenario) {
  appState.scenarioEditorMode = "edit";
  appState.editingScenario = scenario;
  appState.coverFile = null;
  els.scenarioFormTitle.textContent = scenario.title;
  els.scenarioSaveButton.textContent = "Сохранить изменения";
  els.scenarioTitleInput.value = scenario.title;
  els.scenarioDescriptionInput.value = scenario.description || "";
  els.scenarioTypeSelect.value = scenario.scenario_type;
  els.scenarioStatusSelect.value = scenario.status;
  els.leaderboardEnabledInput.checked = scenario.leaderboard_enabled;
  els.interactiveLinksEnabledInput.checked = Boolean(scenario.interactive_links_enabled);
  els.interactiveWorkspaceEnabledInput.checked = Boolean(scenario.interactive_workspace_enabled);
  els.leaderboardLabelInput.value = scenario.leaderboard_label || "Очки";
  const worldSource = document.querySelector(`input[name="worldSource"][value="${scenario.world_source}"]`);
  if (worldSource) worldSource.checked = true;
  els.worldpackSelect.value = scenario.worldpack_id;
  els.worldPromptInput.value = scenario.world_prompt || "";
  els.providerSelect.value = scenario.model_profile?.provider || "";
  renderModelOptions(scenario.model_profile_id);
  renderWorldSource();
  renderTrainingCapabilities();
  renderStatusPill();
  els.coverInput.value = "";
  els.coverPreview.textContent = scenario.cover_url ? "Текущая обложка" : "Обложка не выбрана";
  renderCover(els.coverPreview, scenario.cover_url, els.coverPreview.textContent);
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
  for (const pack of appState.worldpacks.filter((item) => item.visibility !== "private")) {
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
  renderTrainingCapabilities();
}

function selectedWorldpack() {
  return appState.worldpacks.find((pack) => pack.id === els.worldpackSelect.value) || null;
}

function renderTrainingCapabilities() {
  const training = els.scenarioTypeSelect.value === "training";
  const preset = selectedWorldSource() === "preset";
  const pack = preset ? selectedWorldpack() : null;
  const linksSupported = Boolean(pack?.manifest?.training_artifacts?.schema_version === "rp-training-artifacts.v1");
  const workspaceSupported = Boolean(pack?.manifest?.training_workspace?.schema_version === "rp-training-workspace.v1");
  els.trainingCapabilitiesField.classList.toggle("hidden", !training);
  els.interactiveLinksEnabledInput.disabled = !training || !linksSupported;
  els.interactiveWorkspaceEnabledInput.disabled = !training || !workspaceSupported;
  if (!training) {
    els.interactiveLinksEnabledInput.checked = false;
    els.interactiveWorkspaceEnabledInput.checked = false;
  } else {
    if (!linksSupported) els.interactiveLinksEnabledInput.checked = false;
    if (!workspaceSupported) els.interactiveWorkspaceEnabledInput.checked = false;
  }
  els.interactiveLinksHint.textContent = linksSupported
    ? "WorldPack поддерживает учебные сайты. Выбор будет зафиксирован в каждом запуске."
    : "Выбранный WorldPack не содержит валидный каталог учебных сайтов.";
  els.interactiveWorkspaceHint.textContent = workspaceSupported
    ? "WorldPack поддерживает рабочую папку. Выбор будет зафиксирован в каждом запуске."
    : "Выбранный WorldPack не содержит валидный контракт рабочей папки отдела.";
}

function renderStatusPill() {
  els.scenarioStatusPill.textContent = statusLabels[els.scenarioStatusSelect.value] || els.scenarioStatusSelect.value;
}

function previewCover() {
  appState.coverFile = els.coverInput.files?.[0] || null;
  if (!appState.coverFile) return;
  els.coverPreview.textContent = "";
  renderCover(els.coverPreview, URL.createObjectURL(appState.coverFile));
}

function renderCover(container, source, fallbackText = "") {
  container.replaceChildren();
  if (source) {
    const image = document.createElement("img");
    image.className = "cover-image";
    image.src = source;
    image.alt = "";
    container.append(image);
    return;
  }
  container.textContent = fallbackText;
}

async function saveScenario(event) {
  event.preventDefault();
  const editorMode = appState.scenarioEditorMode;
  const editingScenarioId = editorMode === "edit" ? appState.editingScenario?.id : null;
  if (editorMode === "edit" && !editingScenarioId) {
    showToast("Не выбран сценарий для редактирования. Нажмите «Новый сценарий» для создания.", true);
    return;
  }
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
    interactive_links_enabled: els.scenarioTypeSelect.value === "training" && els.interactiveLinksEnabledInput.checked,
    interactive_workspace_enabled: els.scenarioTypeSelect.value === "training" && els.interactiveWorkspaceEnabledInput.checked,
    leaderboard_label: els.leaderboardLabelInput.value.trim() || "Очки",
    sort_order: editorMode === "edit" ? appState.editingScenario?.sort_order ?? 100 : 100,
  };
  setBusy(true, "Сохраняю сценарий...");
  try {
    const response = editorMode === "edit"
      ? await apiPatch(`/api/admin/showroom/scenarios/${encodeURIComponent(editingScenarioId)}`, payload)
      : await apiPost("/api/admin/showroom/scenarios", payload);
    if (appState.coverFile) {
      await apiRaw(
        `/api/admin/showroom/scenarios/${encodeURIComponent(response.scenario.id)}/cover`,
        "PUT",
        appState.coverFile,
        { "Content-Type": appState.coverFile.type || "application/octet-stream" },
      );
    }
    appState.scenarioEditorMode = "edit";
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
  return { openrouter: "OpenRouter", nvidia: "NVIDIA (архив)", gemini: "Gemini", local: "Local LLM" }[provider] || provider;
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

async function apiPut(path, body) {
  return apiRequest(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

async function apiDelete(path) {
  return apiRequest(path, { method: "DELETE" });
}

async function apiRaw(path, method, body, headers = {}) {
  return apiRequest(path, { method, headers, body });
}

async function apiRequest(path, options) {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail : payload;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || `HTTP ${response.status}`));
  }
  return payload;
}

boot();
