"use strict";

(() => {
  const STORAGE_KEY = "claims-assistant.conversation-id";
  const TOOL_LABELS = {
    get_provider_by_npi: "Provider lookup",
    get_provider_claim_summary: "Provider claims summary",
    get_claim_counts_by_status: "Claims by status",
  };
  const STATUS_LABELS = { success: "", error: "failed", rejected: "not allowed", timeout: "timed out" };

  const log = document.getElementById("log");
  const empty = document.getElementById("empty");
  const form = document.getElementById("composer");
  const input = document.getElementById("input");
  const send = document.getElementById("send");
  const badge = document.getElementById("env-badge");

  let conversationId = sessionStorage.getItem(STORAGE_KEY) || null;
  let busy = false;

  const fromTemplate = (id) => document.getElementById(id).content.firstElementChild.cloneNode(true);

  function append(node) {
    empty.hidden = true;
    log.appendChild(node);
    node.scrollIntoView({ block: "end", behavior: "smooth" });
    return node;
  }

  function addUser(text) {
    const el = fromTemplate("tpl-user");
    el.querySelector(".msg-text").textContent = text;
    append(el);
  }

  function addAssistant(text, toolCalls) {
    const el = fromTemplate("tpl-assistant");
    el.querySelector(".msg-text").textContent = text; // never innerHTML
    const receipt = el.querySelector(".receipt");
    for (const call of toolCalls || []) {
      const li = document.createElement("li");
      const label = TOOL_LABELS[call.name] || "Data lookup";
      const status = STATUS_LABELS[call.status] ?? call.status;
      li.textContent = status ? `${label}, ${status}` : `${label}, ${call.duration_ms} ms`;
      if (call.status !== "success") li.classList.add("failed");
      receipt.appendChild(li);
    }
    append(el);
  }

  function addError(message, retryText) {
    const el = fromTemplate("tpl-error");
    el.querySelector(".msg-text").textContent = message;
    el.querySelector(".retry").addEventListener("click", () => {
      el.remove();
      ask(retryText, { echo: false });
    });
    append(el);
  }

  function setBusy(value) {
    busy = value;
    send.disabled = value;
    input.setAttribute("aria-disabled", String(value));
  }

  async function ask(text, { echo = true } = {}) {
    if (busy || !text) return;
    if (echo) addUser(text);
    setBusy(true);
    const pending = append(fromTemplate("tpl-pending"));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 100_000);

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
        signal: controller.signal,
        credentials: "same-origin",
      });
      let body = null;
      try { body = await res.json(); } catch { /* non-JSON error page */ }
      pending.remove();

      if (res.status === 401) {
        addError("Your session has expired. Reload the page to sign in again.", text);
        return;
      }
      if (!res.ok || !body || typeof body.answer !== "string") {
        const msg = body?.error?.message || `The request failed (status ${res.status}).`;
        const ref = body?.request_id ? ` Reference: ${body.request_id}` : "";
        addError(msg + ref, text);
        return;
      }
      conversationId = body.conversation_id;
      sessionStorage.setItem(STORAGE_KEY, conversationId);
      addAssistant(body.answer, body.tool_calls);
    } catch (err) {
      pending.remove();
      addError(
        err.name === "AbortError"
          ? "No response after 100 seconds. Try a narrower question."
          : "Couldn't reach the server. Check your connection.",
        text,
      );
    } finally {
      clearTimeout(timer);
      setBusy(false);
      input.focus();
    }
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    autosize();
    ask(text);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  input.addEventListener("input", autosize);

  document.querySelectorAll(".example").forEach((btn) =>
    btn.addEventListener("click", () => ask(btn.textContent.trim())),
  );

  document.getElementById("new-chat").addEventListener("click", () => {
    conversationId = null;
    sessionStorage.removeItem(STORAGE_KEY);
    log.querySelectorAll(".msg").forEach((m) => m.remove());
    empty.hidden = false;
    input.focus();
  });

  fetch("/ready")
    .then((r) => r.json())
    .then((info) => {
      if (info?.mock_bedrock) {
        badge.textContent = "Mock model";
        badge.hidden = false;
      }
    })
    .catch(() => {});
})();
