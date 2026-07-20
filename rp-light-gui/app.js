const appState = {
  worldpacks: [],
  modelProfiles: [],
  parties: [],
  activeParty: null,
  partyState: null,
  contextEstimate: null,
  history: null,
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
  chatLog: document.querySelector("#chatLog"),
  messageStatus: document.querySelector("#messageStatus"),
  messageForm: document.querySelector("#messageForm"),
  messageInput: document.querySelector("#messageInput"),
  messageSubmit: document.querySelector("#messageSubmit"),
  partyMeta: document.querySelector("#partyMeta"),
  stateSummary: document.querySelector("#stateSummary"),
  contextSummary: document.querySelector("#contextSummary"),
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

bindEvents();
boot();

function bindEvents() {
  document.querySelector("#refreshButton").addEventListener("click", () => boot());
  document.querySelector("#stateRefreshButton").addEventListener("click", () => reloadActiveParty());
  document.querySelector("#newPartyButton").addEventListener("click", openPartyDialog);
  document.querySelector("#closePartyDialog").addEventListener("click", closePartyDialog);
  document.querySelector("#cancelPartyButton").addEventListener("click", closePartyDialog);
  document.querySelector("#worldPreviewButton").addEventListener("click", previewWorldInstruction);
  document.querySelector("#worldApplyButton").addEventListener("click", applyWorldProposal);
  document.querySelector("#worldDiscardButton").addEventListener("click", discardWorldProposal);
  document.querySelector("#rollbackButton").addEventListener("click", rollbackParty);
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
  document.querySelectorAll("input[name='worldSource']").forEach((input) => input.addEventListener("change", renderCreationModes));
  document.querySelectorAll("input[name='characterSource']").forEach((input) => input.addEventListener("change", renderCreationModes));
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
      appState.history = null;
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
  const [party, partyState, history, proposals, context] = await Promise.all([
    apiGet(`/api/parties/${partyId}`),
    apiGet(`/api/parties/${partyId}/state`),
    apiGet(`/api/parties/${partyId}/history`),
    apiGet(`/api/parties/${partyId}/world/proposals`),
    apiGet(`/api/parties/${partyId}/context`),
  ]);
  appState.activeParty = party.party;
  appState.partyState = partyState.state;
  appState.history = history;
  appState.proposals = proposals.proposals || [];
  appState.contextEstimate = context.context || null;
  renderAll();
}

function renderAll() {
  renderPartyList();
  renderHeader();
  renderMeta();
  renderState();
  renderContext();
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
    ${stateItem("Разбивка", `state ~${escapeHtml(stateTokens)} · история ~${escapeHtml(historyTokens)} · ответ до ${escapeHtml(formatTokens(estimate.completion_reserved_tokens || 0))}`, "Оценка входного prompt плюс зарезервированный max_tokens ответа.")}
    ${notes.length ? `<div class="context-notes">${notes.map((note) => `<div>${escapeHtml(note)}</div>`).join("")}</div>` : ""}
  `;
}

function renderChat() {
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
  const messages = [];
  for (const turn of turns) {
    messages.push(messageHtml("user", "Игрок", turn.player_message));
    messages.push(messageHtml("assistant", "GM", turn.narrative_response));
  }
  if (pending && !turns.some((turn) => turn.request_id === pending.requestId)) {
    messages.push(messageHtml("user", "Игрок", pending.text));
    messages.push(pendingMessageHtml(pending.requestId, pending.status));
  }
  els.chatLog.innerHTML = messages.join("");
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
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
