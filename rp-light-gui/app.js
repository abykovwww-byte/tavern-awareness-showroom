const appState = {
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
  pendingMessage: null,
};

const els = {
  partyList: document.querySelector("#partyList"),
  activeWorld: document.querySelector("#activeWorld"),
  activePartyTitle: document.querySelector("#activePartyTitle"),
  gatewayDot: document.querySelector("#gatewayDot"),
  gatewayStatus: document.querySelector("#gatewayStatus"),
  toolsButton: document.querySelector("#toolsButton"),
  closeInspectorButton: document.querySelector("#closeInspectorButton"),
  drawerBackdrop: document.querySelector("#drawerBackdrop"),
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
  modelSelect: document.querySelector("#modelSelect"),
  modelPreview: document.querySelector("#modelPreview"),
  worldPreview: document.querySelector("#worldPreview"),
  worldInstruction: document.querySelector("#worldInstruction"),
  checkForm: document.querySelector("#checkForm"),
  partyModelSelect: document.querySelector("#partyModelSelect"),
  changePartyModelButton: document.querySelector("#changePartyModelButton"),
  deletePartyButton: document.querySelector("#deletePartyButton"),
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
  "Мир": "Worldpack или prompt-мир, из которого взят стартовый state.",
  "Персонаж": "Активный игроковый персонаж этой партии.",
  "Модель": "NVIDIA model profile, выбранный для нарратива, проверок и world edits.",
  "ID партии": "Стабильный party_id; он связывает историю, state и выбранные профили.",
  "State": "campaign_id изолированного состояния партии.",
};

const CHAT_VISIBLE_TURNS = 10;

bindEvents();
setupCollapsiblePanels();
boot();

function bindEvents() {
  document.querySelector("#refreshButton").addEventListener("click", () => boot());
  document.querySelector("#stateRefreshButton").addEventListener("click", () => reloadActiveParty());
  document.querySelector("#newPartyButton").addEventListener("click", openPartyDialog);
  els.toolsButton.addEventListener("click", openInspector);
  els.closeInspectorButton.addEventListener("click", closeInspector);
  els.drawerBackdrop.addEventListener("click", closeInspector);
  document.querySelector("#closePartyDialog").addEventListener("click", closePartyDialog);
  document.querySelector("#cancelPartyButton").addEventListener("click", closePartyDialog);
  document.querySelector("#worldPreviewButton").addEventListener("click", previewWorldInstruction);
  document.querySelector("#worldApplyButton").addEventListener("click", applyWorldProposal);
  document.querySelector("#worldDiscardButton").addEventListener("click", discardWorldProposal);
  document.querySelector("#rollbackButton").addEventListener("click", rollbackParty);
  els.memorySummarizeButton.addEventListener("click", summarizeMemory);
  els.memoryClearButton.addEventListener("click", clearLatestMemory);
  els.promptPreviewButton.addEventListener("click", previewPrompt);
  els.journalSummarizeButton.addEventListener("click", summarizeJournal);
  els.journalClearButton.addEventListener("click", clearLatestJournal);
  els.changePartyModelButton.addEventListener("click", changePartyModel);
  els.deletePartyButton.addEventListener("click", deleteActiveParty);
  els.worldSelect.addEventListener("change", () => {
    renderWorldPreview();
    syncReadyCharacterDescription();
  });
  els.worldPromptTitleInput.addEventListener("input", renderWorldPreview);
  els.worldPromptInput.addEventListener("input", renderWorldPreview);
  els.modelSelect.addEventListener("change", renderModelPreview);
  els.messageForm.addEventListener("submit", sendMessage);
  els.partyForm.addEventListener("submit", createParty);
  els.checkForm.addEventListener("submit", runCheck);
  els.chatLog.addEventListener("click", (event) => {
    const button = event.target.closest("[data-chat-archive]");
    if (!button) return;
    setChatArchiveExpanded(button.dataset.chatArchive === "open");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeInspector();
  });
  document.querySelectorAll("input[name='worldSource']").forEach((input) => input.addEventListener("change", renderCreationModes));
  document.querySelectorAll("input[name='characterSource']").forEach((input) => input.addEventListener("change", renderCreationModes));
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

async function boot() {
  try {
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
    const savedPartyId = localStorage.getItem("rp-light-gui-active-party");
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
  } catch (error) {
    setGatewayStatus("недоступен", false);
    showToast(error.message);
    renderAll();
  }
}

async function selectParty(partyId) {
  const party = appState.parties.find((item) => item.id === partyId) || (await apiGet(`/api/parties/${partyId}`)).party;
  appState.activeParty = party;
  localStorage.setItem("rp-light-gui-active-party", party.id);
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
      return `<button class="party-card${active}" data-party-id="${escapeHtml(party.id)}" title="Открыть партию ${escapeHtml(party.title)}">
        <strong>${escapeHtml(party.title)}</strong>
        <span>${escapeHtml(world)}</span>
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
  renderPartyModelSelect();
  if (!party) {
    els.partyMeta.innerHTML = `<dt title="Статус выбранной партии">Статус</dt><dd>партия не выбрана</dd>`;
    return;
  }
  const rows = [
    ["Мир", party.worldpack?.title || party.worldpack_id],
    ["Персонаж", party.player_character?.name || party.player_character_id],
    ["Модель", party.model_profile?.model || party.model_profile_id],
    ["ID партии", party.id],
    ["State", party.state_campaign_id],
  ];
  els.partyMeta.innerHTML = rows
    .map(([key, value]) => `<dt title="${escapeHtml(metaHints[key] || "")}">${escapeHtml(key)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd>`)
    .join("");
}

function renderPartyModelSelect() {
  if (!els.partyModelSelect) return;
  els.partyModelSelect.innerHTML = appState.modelProfiles
    .map((profile) => `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.title)}</option>`)
    .join("");
  els.partyModelSelect.disabled = !appState.activeParty || !appState.modelProfiles.length;
  if (appState.activeParty?.model_profile_id) {
    els.partyModelSelect.value = appState.activeParty.model_profile_id;
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
  const resources = compactJson(player.resources || {});
  const threads = Array.isArray(state.active_threads) ? state.active_threads.slice(0, 4) : [];
  const relationships = state.relationships || {};
  const relRows = Object.entries(relationships)
    .slice(0, 5)
    .map(([key, value]) => `${escapeHtml(key)}: доверие ${escapeHtml(value.trust ?? "-")}, подозрение ${escapeHtml(value.suspicion ?? "-")}`);
  els.stateSummary.innerHTML = [
    stateItem("Версия", `v${meta.state_version ?? "-"} · ход ${meta.turn ?? "-"}`, "Номер сохраненного state и текущий ход партии."),
    stateItem("Локация", player.location || "unknown", "Где сейчас находится персонаж."),
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
  const percent = typeof estimate.usage_ratio === "number" ? estimate.usage_ratio * 100 : null;
  const fill = percent === null ? 0 : Math.max(2, Math.min(100, percent));
  const percentLabel = percent === null ? "лимит неизвестен" : `${formatPercent(percent)} лимита`;
  const limitLabel = estimate.context_limit_tokens ? formatTokens(estimate.context_limit_tokens) : "неизвестно";
  const notes = Array.isArray(estimate.notes) ? estimate.notes : [];
  const historyText = [
    `в prompt ${estimate.direct_history_messages ?? 0} сообщений`,
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
        <span>${escapeHtml(percentLabel)}</span>
      </div>
      <div class="context-bar"><span style="width: ${fill}%"></span></div>
    </div>
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
    const waiting = stats.next_auto_summary_turns_remaining ?? 0;
    els.memorySummary.innerHTML = [
      stateItem("Сводка", "еще не собрана", "Summary появится, когда накопятся старые ходы за пределами raw окна."),
      stateItem(
        "Покрытие",
        `старых ходов ${escapeHtml(oldTurns)} · до auto ${escapeHtml(waiting)}`,
        "Gateway хранит последние raw ходы напрямую, а более старые сжимает в long-term memory.",
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

function characterCard(character) {
  const relation = relationshipText(character.relationship);
  const lastSeen = character.last_seen ? `ход ${character.last_seen.turn ?? "-"} · ${character.last_seen.event || ""}` : "нет отметки";
  const metrics = [
    character.status ? `статус: ${character.status}` : "",
    character.location ? `место: ${character.location}` : "",
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
    els.promptPreview.innerHTML = `<div class="state-item">Нажми «Показать prompt», чтобы собрать dry-run для текущего текста в поле хода.</div>`;
    return;
  }
  const blocks = Array.isArray(preview.blocks) ? preview.blocks : [];
  const total = formatTokens(preview.estimated_prompt_tokens || 0);
  const dryRun = preview.dry_run ? "dry-run, state не меняется" : "preview";
  els.promptPreview.innerHTML = [
    stateItem("Оценка", `~${escapeHtml(total)} токенов · ${escapeHtml(dryRun)}`, "Приблизительная оценка prompt по блокам."),
    ...blocks.map((block, index) => promptBlock(block, index)),
  ].join("");
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
    els.chatLog.innerHTML = `<div class="empty-chat">Создай или выбери партию.</div>`;
    return;
  }
  if (!turns.length && !pending) {
    els.chatLog.innerHTML = `<div class="empty-chat">Партия готова. Первый ход начнет историю.</div>`;
    return;
  }
  const hiddenTurnCount = Math.max(0, turns.length - CHAT_VISIBLE_TURNS);
  const visibleTurns = hiddenTurnCount && !appState.chatArchiveExpanded ? turns.slice(-CHAT_VISIBLE_TURNS) : turns;
  const messages = [];
  if (hiddenTurnCount) {
    messages.push(chatArchiveHtml(hiddenTurnCount));
  }
  for (const turn of visibleTurns) {
    messages.push(messageHtml("user", "Игрок", turn.player_message));
    messages.push(messageHtml("assistant", "GM", turn.narrative_response));
  }
  if (pending && !turns.some((turn) => turn.request_id === pending.requestId)) {
    messages.push(messageHtml("user", "Игрок", pending.text));
    messages.push(pendingMessageHtml(pending.requestId, pending.status));
  }
  els.chatLog.innerHTML = messages.join("");
  els.chatLog.scrollTop = scrollMode === "top" ? 0 : els.chatLog.scrollHeight;
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
  els.worldSelect.innerHTML = appState.worldpacks
    .map((pack) => `<option value="${escapeHtml(pack.id)}">${escapeHtml(pack.title)} · ${escapeHtml(pack.status)}</option>`)
    .join("");
  els.modelSelect.innerHTML = appState.modelProfiles
    .map((profile) => `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.title)}</option>`)
    .join("");
  const pack = selectedWorldpack();
  els.partyTitleInput.value = pack ? `${pack.title}: партия` : "Новая партия";
  els.worldPromptTitleInput.value = "";
  els.worldPromptInput.value = "";
  els.characterNameInput.value = "Игрок";
  els.characterDescriptionInput.value = pack?.manifest?.player_role || "";
  renderWorldPreview();
  renderModelPreview();
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
      <dt>Alias</dt><dd>${escapeHtml(profile.model)}</dd>
      <dt>Контекст</dt><dd>${escapeHtml(profile.context_window || "уточняется")}</dd>
      <dt>Источник</dt><dd>${escapeHtml(sourceLabel(profile.source))}</dd>
      <dt>Доступность</dt><dd>${escapeHtml(profile.availability || "зависит от ключа NVIDIA")}</dd>
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
  const characterPrompt = selectedRadioValue("characterSource") === "prompt";
  try {
    setBusy(true);
    if (!modelProfileId) throw new Error("Нет доступной модели для партии.");
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
      worldpack_id: worldpack.id,
      player_character_id: character.player_character.id,
      model_profile_id: modelProfileId,
    });
    closePartyDialog();
    await boot();
    await selectParty(party.party.id);
    showToast("Партия создана.");
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

async function sendMessage(event) {
  event.preventDefault();
  if (appState.pendingMessage) {
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
    setBusy(true);
    setPendingStatus("GM формирует ответ...");
    const result = await apiPost(
      `/api/parties/${partyId}/messages`,
      { content: text, idempotency_key: requestId },
      { "X-Request-ID": requestId },
    );
    const content = result.message?.content || "";
    if (content) {
      replacePendingMessage(requestId, content);
    }
    setPendingStatus("Ответ получен. Обновляю историю...");
    try {
      await reloadActiveParty();
    } catch (syncError) {
      showToast(`Ответ получен, но история не обновилась: ${syncError.message}`);
    }
  } catch (error) {
    setPendingStatus("Запрос оборвался. Проверяю историю...");
    const recovered = await waitForRecoveredMessage(partyId, requestId).catch(() => null);
    if (recovered?.narrative_response) {
      showToast("Ответ подтянут из истории.");
    } else {
      replacePendingMessage(requestId, `Ответ не получен: ${error.message}`, true);
      showToast(error.message);
    }
  } finally {
    clearPendingMessage();
    setBusy(false);
  }
}

async function previewWorldInstruction() {
  const text = els.worldInstruction.value.trim();
  if (!text || !appState.activeParty) return;
  try {
    setBusy(true);
    await apiPost(`/api/parties/${appState.activeParty.id}/world/instruct`, { instruction: text });
    els.worldInstruction.value = "";
    await reloadActiveParty();
    openPanelFor(els.proposalList);
    showToast("Черновик изменений создан.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function applyWorldProposal() {
  if (!appState.activeParty) return;
  try {
    setBusy(true);
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
    setBusy(true);
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
    setBusy(true);
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
    setBusy(true);
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
    setBusy(true);
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
    setBusy(true);
    const result = await apiPost(`/api/parties/${appState.activeParty.id}/prompt/preview`, { content });
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
    setBusy(true);
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
    setBusy(true);
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
    setBusy(true);
    await apiDelete(`/api/parties/${party.id}`);
    localStorage.removeItem("rp-light-gui-active-party");
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
    setBusy(true);
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
    setBusy(true);
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

function makeClientRequestId() {
  const random = Math.random().toString(36).slice(2, 10);
  return `ui_${Date.now().toString(36)}_${random}`;
}

function startPendingMessage(partyId, requestId, text) {
  appState.pendingMessage = {
    partyId,
    requestId,
    text,
    status: "GM формирует ответ...",
  };
  renderMessageControls();
}

function activePendingMessage() {
  if (!appState.pendingMessage || appState.pendingMessage.partyId !== appState.activeParty?.id) {
    return null;
  }
  return appState.pendingMessage;
}

function setPendingStatus(status) {
  if (!appState.pendingMessage) return;
  appState.pendingMessage.status = status;
  const pending = els.chatLog.querySelector(`[data-pending-id="${appState.pendingMessage.requestId}"] .pending-text`);
  if (pending) pending.textContent = status;
  renderMessageControls();
}

function clearPendingMessage() {
  appState.pendingMessage = null;
  renderMessageControls();
}

function replacePendingMessage(requestId, content, isError = false) {
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
  const attempts = 24;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) await delay(5000);
    setPendingStatus(`Проверяю историю партии... ${attempt + 1}/${attempts}`);
    const history = await apiGet(`/api/parties/${partyId}/history`);
    const turn = (history.turns || []).find((item) => item.request_id === requestId);
    if (turn?.narrative_response) {
      appState.history = history;
      renderAll();
      await reloadActiveParty().catch(() => {});
      return turn;
    }
  }
  return null;
}

function renderMessageControls() {
  const locked = Boolean(appState.pendingMessage);
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
    els.messageStatus.innerHTML = `<span class="spinner" aria-hidden="true"></span><span>${escapeHtml(appState.pendingMessage.status)}</span>`;
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

async function apiDelete(path) {
  return api(path, { method: "DELETE" });
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
    throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
  }
  return data;
}

function selectedWorldpack() {
  return appState.worldpacks.find((pack) => pack.id === els.worldSelect.value) || appState.worldpacks[0] || null;
}

function selectedModelProfile() {
  return appState.modelProfiles.find((profile) => profile.id === els.modelSelect.value) || appState.modelProfiles[0] || null;
}

function selectedRadioValue(name) {
  return document.querySelector(`input[name='${name}']:checked`)?.value || "ready";
}

function sourceLabel(source) {
  const labels = {
    static_build_nvidia_fallback: "статичный fallback build.nvidia.com",
    build_nvidia_live: "live build.nvidia.com",
    nvidia_api_live: "live NVIDIA /v1/models",
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

function setBusy(value) {
  appState.busy = value;
  document.body.classList.toggle("busy", value);
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
