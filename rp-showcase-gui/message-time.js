(function attachMessageTime(root) {
  "use strict";

  function normalizeMessageDate(value) {
    if (value === null || value === undefined || value === "") return null;
    let date;
    if (value instanceof Date) {
      date = new Date(value.getTime());
    } else if (typeof value === "number" || /^\d+(?:\.\d+)?$/.test(String(value).trim())) {
      const numeric = Number(value);
      date = new Date(Math.abs(numeric) < 1e12 ? numeric * 1000 : numeric);
    } else {
      date = new Date(value);
    }
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatMessageTime(value, locale) {
    const date = normalizeMessageDate(value);
    if (!date) return null;
    const resolvedLocale = locale || undefined;
    return {
      text: new Intl.DateTimeFormat(resolvedLocale, {
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).format(date),
      title: new Intl.DateTimeFormat(resolvedLocale, {
        dateStyle: "medium",
        timeStyle: "medium",
      }).format(date),
      iso: date.toISOString(),
    };
  }

  const api = { formatMessageTime, normalizeMessageDate };
  root.MessageTime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
