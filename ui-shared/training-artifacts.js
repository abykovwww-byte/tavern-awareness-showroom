(function trainingArtifactsModule(global) {
  "use strict";

  const renderers = new Set([
    "credential-form", "otp-form", "file-share", "document-approval",
    "payment-review", "tracking-form", "meeting-join", "survey-form",
    "support-download",
  ]);
  const themes = new Set(["office-blue", "office-neutral", "service-green", "warning-amber", "minimal-light"]);
  const queue = [];
  let draining = null;

  function eventId() {
    if (global.crypto?.randomUUID) return `evt_${global.crypto.randomUUID()}`;
    return `evt_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  }

  function validArtifact(artifact) {
    return Boolean(
      artifact
      && artifact.schema_version === "rp-gateway.training-artifact.v1"
      && renderers.has(artifact.renderer)
      && themes.has(artifact.theme)
      && typeof artifact.artifact_id === "string"
      && typeof artifact.display_url === "string"
      && Array.isArray(artifact.field_ids)
      && Array.isArray(artifact.actions),
    );
  }

  function eventPayload(artifact, eventType, filledFieldIds = []) {
    return {
      event_id: eventId(),
      artifact_id: artifact.artifact_id,
      artifact_revision: artifact.artifact_revision,
      event_type: eventType,
      filled_field_ids: [...new Set(filledFieldIds)],
    };
  }

  function enqueue(sender, payload) {
    queue.push({ sender, payload });
    void drain();
    return payload.event_id;
  }

  async function drain() {
    if (draining) return draining;
    draining = (async () => {
      while (queue.length) {
        const item = queue[0];
        try {
          await item.sender(item.payload);
          queue.shift();
        } catch (_error) {
          break;
        }
      }
    })();
    try {
      await draining;
    } finally {
      draining = null;
    }
  }

  async function flush() {
    await drain();
    if (queue.length) throw new Error("Не удалось зафиксировать действие на учебном сайте. Повторите отправку.");
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function slot(artifact, id, fallback = "") {
    const value = artifact.slots?.[id];
    return typeof value === "string" && value.trim() ? value : fallback;
  }

  function fieldLabel(artifact, fieldId) {
    const slotId = `${String(fieldId).replace(/-/g, "_")}_label`;
    return slot(artifact, slotId, String(fieldId).replace(/-/g, " "));
  }

  function clearInputs(inputs) {
    inputs.forEach((input) => { input.value = ""; });
  }

  function openArtifact(artifact, sender) {
    const overlay = element("div", `training-site-overlay theme-${artifact.theme}`);
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", slot(artifact, "page_title", "Учебный сайт"));

    const browser = element("section", `training-site-browser renderer-${artifact.renderer}`);
    const chrome = element("header", "training-site-chrome");
    const dots = element("span", "training-site-dots", "● ● ●");
    dots.setAttribute("aria-hidden", "true");
    chrome.append(dots, element("span", "training-site-url", artifact.display_url));

    const page = element("form", "training-site-page");
    page.setAttribute("autocomplete", "off");
    page.noValidate = true;
    page.append(
      element("p", "training-site-kind", "Интерактивная учебная страница"),
      element("h2", "training-site-title", slot(artifact, "page_title", "Учебный сайт")),
      element("p", "training-site-subtitle", slot(artifact, "page_subtitle", "")),
    );

    ["file_title", "document_title", "invoice_title", "package_title", "organizer_label"].forEach((id) => {
      if (slot(artifact, id)) page.append(element("div", "training-site-card", slot(artifact, id)));
    });
    ["file_meta", "amount_label"].forEach((id) => {
      if (slot(artifact, id)) page.append(element("p", "training-site-meta", slot(artifact, id)));
    });

    const inputs = [];
    artifact.field_ids.forEach((fieldId) => {
      const label = element("label", "training-site-field");
      const labelText = element("span", "training-site-field-label", fieldLabel(artifact, fieldId));
      const input = element("input", "training-site-input");
      const configuredType = artifact.field_types?.[fieldId] || "text";
      input.type = configuredType === "password" ? "password" : configuredType === "email" ? "email" : "text";
      input.inputMode = configuredType === "otp" ? "numeric" : configuredType === "email" ? "email" : "text";
      input.autocomplete = "off";
      input.name = `simulation-${artifact.artifact_id}-${fieldId}-${eventId()}`;
      input.dataset.fieldId = fieldId;
      label.append(labelText, input);
      inputs.push(input);
      page.append(label);
    });

    const status = element("p", "training-site-status");
    status.setAttribute("role", "status");
    const actions = element("div", "training-site-actions");
    if (artifact.actions.includes("submit")) {
      const submit = element("button", "training-site-primary", slot(artifact, "submit_label", "Продолжить"));
      submit.type = "submit";
      actions.append(submit);
    }
    if (artifact.actions.includes("report")) {
      const report = element("button", "training-site-secondary", "Сообщить о странице");
      report.type = "button";
      report.addEventListener("click", () => {
        enqueue(sender, eventPayload(artifact, "reported"));
        status.textContent = "Действие зафиксировано";
      });
      actions.append(report);
    }
    const close = element("button", "training-site-secondary", "Закрыть");
    close.type = "button";
    close.addEventListener("click", () => {
      clearInputs(inputs);
      if (artifact.actions.includes("close")) enqueue(sender, eventPayload(artifact, "site_closed"));
      overlay.remove();
    });
    actions.append(close);

    page.addEventListener("submit", (event) => {
      event.preventDefault();
      const filled = inputs.filter((input) => input.value.trim() !== "").map((input) => input.dataset.fieldId);
      enqueue(sender, eventPayload(artifact, "form_submitted", filled));
      clearInputs(inputs);
      status.textContent = slot(artifact, "post_submit_message", "Действие выполнено");
    });

    page.append(actions, status);
    browser.append(chrome, page);
    overlay.append(browser);
    document.body.append(overlay);
    (inputs[0] || close).focus();
  }

  function mount(host, artifacts, sender) {
    if (!host || typeof sender !== "function") return;
    host.replaceChildren();
    (Array.isArray(artifacts) ? artifacts : []).filter(validArtifact).forEach((artifact) => {
      const button = element("button", "training-artifact-trigger");
      button.type = "button";
      button.append(
        element("span", "training-artifact-trigger-label", "Открыть учебный сайт"),
        element("span", "training-artifact-trigger-url", artifact.display_url),
      );
      button.addEventListener("click", () => {
        enqueue(sender, eventPayload(artifact, "link_opened"));
        openArtifact(artifact, sender);
      });
      host.append(button);
    });
  }

  global.TrainingArtifacts = { mount, flush, drain, eventPayload, validArtifact, supportedRenderers: [...renderers] };
})(globalThis);
