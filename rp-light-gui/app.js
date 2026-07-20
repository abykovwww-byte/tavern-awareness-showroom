const appState = {
  worldpacks: [],
  modelProfiles: [],
  parties: [],
  activeParty: null,
  partyState: null,
  history: null,
  proposals: [],
  busy: false,
};

const els = {
  partyList: document.querySelector("#partyList"),
  activeWorld: document.querySelector("#activeWorld"),
  activePartyTitle: document.querySelector("#activePartyTitle"),
  gatewayDot: document.querySelector("#gatewayDot"),
  gatewayStatus: document.querySelector("#gatewayStatus"),
  chatLog: document.querySelector("#chatLog"),
  messageForm: document.querySelector("#messageForm"),
  messageInput: document.querySelector("#messageInput"),
  partyMeta: document.querySelector("#partyMeta"),
  stateSummary: document.querySelector("#stateSummary"),
  proposalList: document.querySelector("#proposalList"),
  toast: document.querySelector("#toast"),
  partyDialog: document.querySelector("#partyDialog"),
  partyForm: document.querySelector("#partyForm"),
  partyTitleInput: document.querySelector("#partyTitleInput"),
  worldSelect: document.querySelector("#worldSelect"),
  characterNameInput: document.querySelector("#characterNameInput"),
  characterDescriptionInput: document.querySelector("#characterDescriptionInput"),
  modelSelect: document.querySelector("#modelSelect"),
  worldPreview: document.querySelector("#worldPreview"),
  worldInstruction: document.querySelector("#worldInstruction"),
  checkForm: document.querySelector("#checkForm"),
};

document.querySelector("#refreshButton").addEventListener("click", () => boot());
document.querySelector("#stateRefreshButton").addEventListener("click", () => reloadActiveParty());
document.querySelector("#newPartyButton").addEventListener("click", openPartyDialog);
document.querySelector("#closePartyDialog").addEventListener("click", closePartyDialog);
document.querySelector("#cancelPartyButton").addEventListener("click", closePartyDialog);
document.querySelector("#worldPreviewButton").addEventListener("click", previewWorldInstruction);
document.querySelector("#worldApplyButton").addEventListener("click", applyWorldProposal);
document.querySelector("#worldDiscardButton").addEventListener("click", discardWorldProposal);
document.querySelector("#rollbackButton").addEventListener("click", rollbackParty);
els.worldSelect.addEventListener("change", renderWorldPreview);
els.messageForm.addEventListener("submit", sendMessage);
els.partyForm.addEventListener("submit", createParty);
els.checkForm.addEventListener("submit", runCheck);

boot();

async function boot() {
  try {
    setGatewayStatus("sync", false);
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
    renderPartyList();
    const savedPartyId = localStorage.getItem("rp-light-gui-active-party");
    const active = appState.parties.find((party) => party.id === savedPartyId) || appState.parties[0] || null;
    if (active) {
      await selectParty(active.id);
    } else {
      appState.activeParty = null;
      renderAll();
    }
  } catch (error) {
    setGatewayStatus("offline", false);
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
  const [party, partyState, history, proposals] = await Promise.all([
    apiGet(`/api/parties/${partyId}`),
    apiGet(`/api/parties/${partyId}/state`),
    apiGet(`/api/parties/${partyId}/history`),
    apiGet(`/api/parties/${partyId}/world/proposals`),
  ]);
  appState.activeParty = party.party;
  appState.partyState = partyState.state;
  appState.history = history;
  appState.proposals = proposals.proposals || [];
  renderAll();
}

function renderAll() {
  renderPartyList();
  renderHeader();
  renderMeta();
  renderState();
  renderChat();
  renderProposals();
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
      return `<button class="party-card${active}" data-party-id="${escapeHtml(party.id)}">
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
  if (!party) {
    els.partyMeta.innerHTML = `<dt>status</dt><dd>empty</dd>`;
    return;
  }
  els.partyMeta.innerHTML = [
    ["world", party.worldpack?.title || party.worldpack_id],
    ["player", party.player_character?.name || party.player_character_id],
    ["model", party.model_profile?.model || party.model_profile_id],
    ["party_id", party.id],
    ["state", party.state_campaign_id],
  ]
    .map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd>`)
    .join("");
}

function renderState() {
  const state = appState.partyState;
  if (!state) {
    els.stateSummary.innerHTML = `<div class="state-item">State не загружен.</div>`;
    return;
  }
  const meta = state.meta || {};
  const player = state.player || {};
  const resources = compactJson(player.resources || {});
  const threads = Array.isArray(state.active_threads) ? state.active_threads.slice(0, 4) : [];
  const relationships = state.relationships || {};
  const relRows = Object.entries(relationships)
    .slice(0, 5)
    .map(
      ([key, value]) =>
        `${escapeHtml(key)}: trust ${escapeHtml(value.trust ?? "-")}, suspicion ${escapeHtml(value.suspicion ?? "-")}`,
    );
  els.stateSummary.innerHTML = [
    stateItem("Версия", `v${meta.state_version ?? "-"} · turn ${meta.turn ?? "-"}`),
    stateItem("Локация", player.location || "unknown"),
    stateItem("Ресурсы", resources),
    stateItem("Отношения", relRows.length ? relRows.join("<br>") : "нет записей"),
    stateItem("Нити", threads.length ? threads.map((thread) => escapeHtml(thread.description || thread.id)).join("<br>") : "нет активных"),
  ].join("");
}

function renderChat() {
  const turns = appState.history?.turns || [];
  if (!appState.activeParty) {
    els.chatLog.innerHTML = `<div class="empty-chat">Создай или выбери партию.</div>`;
    return;
  }
  if (!turns.length) {
    els.chatLog.innerHTML = `<div class="empty-chat">Партия готова. Первый ход начнёт историю.</div>`;
    return;
  }
  const messages = [];
  for (const turn of turns) {
    messages.push(messageHtml("user", "Игрок", turn.player_message));
    messages.push(messageHtml("assistant", "GM", turn.narrative_response));
  }
  els.chatLog.innerHTML = messages.join("");
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

function renderProposals() {
  if (!appState.proposals.length) {
    els.proposalList.innerHTML = `<div class="proposal">Нет pending preview.</div>`;
    return;
  }
  els.proposalList.innerHTML = appState.proposals
    .map(
      (proposal) => `<div class="proposal">
        <strong>${escapeHtml(proposal.proposal_id)}</strong><br>
        turn ${proposal.turn ?? "-"} · ops ${proposal.operations ?? 0}
      </div>`,
    )
    .join("");
}

function openPartyDialog() {
  renderDialogOptions();
  renderWorldPreview();
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
  els.characterNameInput.value = "Игрок";
  els.characterDescriptionInput.value = pack?.manifest?.player_role || "";
}

function renderWorldPreview() {
  const pack = selectedWorldpack();
  if (!pack) {
    els.worldPreview.textContent = "Нет доступных worldpacks.";
    return;
  }
  els.worldPreview.innerHTML = `<strong>${escapeHtml(pack.title)}</strong><br>${escapeHtml(pack.premise || pack.slug)}`;
  if (!els.partyTitleInput.value) {
    els.partyTitleInput.value = `${pack.title}: партия`;
  }
  if (!els.characterDescriptionInput.value) {
    els.characterDescriptionInput.value = pack.manifest?.player_role || "";
  }
}

async function createParty(event) {
  event.preventDefault();
  const worldpackId = els.worldSelect.value;
  const modelProfileId = els.modelSelect.value;
  try {
    setBusy(true);
    const draft = await apiPost("/api/player-characters/draft", {
      worldpack_id: worldpackId,
      name: els.characterNameInput.value.trim(),
      concept: els.characterDescriptionInput.value.trim(),
    });
    const character = await apiPost("/api/player-characters", draft.draft);
    const party = await apiPost("/api/parties", {
      title: els.partyTitleInput.value.trim(),
      worldpack_id: worldpackId,
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

async function sendMessage(event) {
  event.preventDefault();
  const text = els.messageInput.value.trim();
  if (!text || !appState.activeParty) return;
  els.messageInput.value = "";
  appendPendingMessage(text);
  try {
    setBusy(true);
    await apiPost(`/api/parties/${appState.activeParty.id}/messages`, { content: text });
    await reloadActiveParty();
  } catch (error) {
    showToast(error.message);
    await reloadActiveParty();
  } finally {
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
    showToast("Preview создан.");
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
    showToast("Preview применён.");
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
    showToast("Preview отменён.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function rollbackParty() {
  if (!appState.activeParty) return;
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

function appendPendingMessage(text) {
  if (!appState.history) appState.history = { turns: [] };
  els.chatLog.insertAdjacentHTML("beforeend", messageHtml("user", "Игрок", text));
  els.chatLog.insertAdjacentHTML("beforeend", messageHtml("assistant", "GM", "…"));
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

async function apiGet(path) {
  return api(path);
}

async function apiPost(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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

function stateItem(title, body) {
  return `<div class="state-item"><strong>${escapeHtml(title)}</strong>${body}</div>`;
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

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
