(function exposeStructuredContent(root) {
  "use strict";

  const BLOCKS = {
    "ПИСЬМО": {
      type: "email",
      fields: ["Канал", "От", "Кому", "Дата/время", "Тема", "Вложения", "Ссылки", "Тело", "Подпись"],
      required: ["От", "Тема", "Тело"],
    },
    "СООБЩЕНИЕ": {
      type: "messenger",
      fields: ["Канал", "Чат", "От", "Кому", "Дата/время", "Вложения", "Ссылки", "Текст"],
      required: ["Чат", "От", "Текст"],
    },
  };

  function markerFor(line) {
    const normalized = String(line || "").trim().toLocaleUpperCase("ru-RU");
    return Object.prototype.hasOwnProperty.call(BLOCKS, normalized) ? normalized : null;
  }

  function isSeparator(line) {
    return /^-{3,}$/.test(String(line || "").trim());
  }

  function splitTrailingPlayerPrompt(marker, lines) {
    if (marker !== "ПИСЬМО") return { blockLines: lines, prompt: "" };
    const signatureIndex = lines.findIndex((line) => /^Подпись:\s*/.test(String(line)));
    if (signatureIndex < 0) return { blockLines: lines, prompt: "" };
    let blankIndex = -1;
    for (let index = lines.length - 1; index > signatureIndex; index -= 1) {
      if (!String(lines[index]).trim()) {
        blankIndex = index;
        break;
      }
    }
    if (blankIndex < 0) return { blockLines: lines, prompt: "" };
    const prompt = lines.slice(blankIndex + 1).join("\n").trim();
    const playerPrompt = /^(?:что\s+(?:ты|вы|делаешь|делаете|будешь|будете|предпримешь|предпримете|ответишь|ответите)|как\s+(?:ты|вы|отвечаешь|отвечаете|ответишь|ответите|поступаешь|поступаете|поступишь|поступите|действуешь|действуете)|(?:твои|ваши)\s+действия)(?=\s|[,:;.!?？—-])[\s\S]*[?？]\s*$/iu;
    if (!playerPrompt.test(prompt)) return { blockLines: lines, prompt: "" };
    return { blockLines: lines.slice(0, blankIndex), prompt };
  }

  function parseBlock(marker, lines) {
    const definition = BLOCKS[marker];
    const knownFields = new Set(definition.fields);
    const fieldLines = {};
    let currentField = null;

    for (const line of lines) {
      const match = String(line).match(/^([^:]{1,40}):\s*(.*)$/);
      const label = match?.[1]?.trim();
      if (label && knownFields.has(label)) {
        currentField = label;
        fieldLines[label] = [match[2] || ""];
        continue;
      }
      if (currentField) {
        fieldLines[currentField].push(line);
      }
    }

    const raw = [marker, ...lines].join("\n").trim();
    const fields = Object.fromEntries(
      Object.entries(fieldLines).map(([label, valueLines]) => [label, valueLines.join("\n").trim()]),
    );
    if (!definition.required.every((label) => fields[label])) return { type: "text", text: raw };
    return { type: definition.type, fields, raw };
  }

  function parseStructuredNarrative(content) {
    const lines = String(content || "").replace(/\r\n?/g, "\n").split("\n");
    const segments = [];
    let plainLines = [];

    function flushPlain() {
      const text = plainLines.join("\n").trim();
      if (text) segments.push({ type: "text", text });
      plainLines = [];
    }

    for (let index = 0; index < lines.length;) {
      const marker = markerFor(lines[index]);
      if (!marker) {
        if (!isSeparator(lines[index])) plainLines.push(lines[index]);
        index += 1;
        continue;
      }

      flushPlain();
      index += 1;
      const blockLines = [];
      while (index < lines.length && !markerFor(lines[index]) && !isSeparator(lines[index])) {
        blockLines.push(lines[index]);
        index += 1;
      }
      const boundary = splitTrailingPlayerPrompt(marker, blockLines);
      const parsed = parseBlock(marker, boundary.blockLines);
      if (parsed.type === "text" && boundary.prompt) {
        segments.push(parseBlock(marker, blockLines));
      } else {
        segments.push(parsed);
        if (boundary.prompt) segments.push({ type: "text", text: boundary.prompt });
      }
      if (index < lines.length && isSeparator(lines[index])) index += 1;
    }

    flushPlain();
    return segments.length ? segments : [{ type: "text", text: String(content || "") }];
  }

  const api = { parseStructuredNarrative };
  root.StructuredContent = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
