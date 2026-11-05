import { readAgentStream, renderAction, renderMessages, wireRestore } from "../agent-chat.js";

for (const root of document.querySelectorAll("[data-agent-root], [data-agent-launcher]")) {
  const isDrawer = root.matches("[data-agent-launcher]");
  const messages = root.querySelector("#agent-messages");
  const list = root.querySelector("#agent-conversations");
  const select = root.querySelector("[data-agent-select]");
  const suggestions = root.querySelector("#agent-suggestions");
  const form = root.querySelector("[data-agent-form]");
  const input = form?.elements.message;
  const send = form?.querySelector('button[type="submit"]');
  const panel = root.querySelector(".agent-drawer");
  const overlay = root.querySelector(".agent-overlay");
  const launcher = root.querySelector(".agent-launch");
  const configured = root.dataset.agentConfigured !== "false";
  let conversationId = "";
  let rows = [];
  let pendingActions = [];
  let busy = false;
  let abortController = null;

  const contextHint = () => {
    const heading = document.querySelector("main h1")?.textContent?.trim() || document.title;
    return `Admin page context: ${heading}; route ${location.pathname}. Use this as a hint only.`;
  };
  const render = () => {
    if (!messages) return;
    renderMessages(messages, rows);
    for (const action of pendingActions) renderAction(messages, action, decideAction);
    if (suggestions) suggestions.hidden = rows.some(row => row.role === "user");
    messages.scrollTop = messages.scrollHeight;
  };
  const relativeTime = value => {
    const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000);
    const units = [[31536000, "year"], [2592000, "month"], [604800, "week"], [86400, "day"], [3600, "hour"], [60, "minute"]];
    for (const [size, unit] of units) if (Math.abs(seconds) >= size) {
      return new Intl.RelativeTimeFormat("en-GB", { numeric: "auto" }).format(Math.round(seconds / size), unit);
    }
    return "just now";
  };
  const loadConversations = async () => {
    const response = await fetch(root.dataset.conversations, { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const items = await response.json();
    if (list) {
      list.replaceChildren();
      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "agent-conversation-empty";
        empty.textContent = "Your conversations will appear here.";
        list.append(empty);
      }
      for (const item of items) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "agent-conversation";
        button.dataset.conversationId = item.id;
        button.setAttribute("aria-current", String(item.id === conversationId));
        const title = document.createElement("span");
        title.textContent = item.title || "New conversation";
        const time = document.createElement("time");
        time.dateTime = item.updated_at;
        time.textContent = relativeTime(item.updated_at);
        button.append(title, time);
        list.append(button);
      }
    }
    if (select) {
      select.replaceChildren(new Option("New chat", ""));
      for (const item of items) select.add(new Option(item.title || "New conversation", item.id));
      select.value = conversationId;
    }
  };
  const history = async id => {
    const url = new URL(root.dataset.history, location.origin);
    if (id) url.searchParams.set("conversation_id", id);
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const data = await response.json();
    conversationId = data.conversation_id || "";
    rows = (data.messages || []).filter(row => ["user", "assistant"].includes(row.role));
    pendingActions = data.actions || [];
    render();
    await loadConversations();
  };
  const postJson = (url, body, signal) => fetch(url, {
    method: "POST", signal,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": root.dataset.csrf },
    body: JSON.stringify({ ...body, csrf_token: root.dataset.csrf }),
  });
  const decideAction = async (id, decision) => {
    const response = await postJson(`/admin/sites/${root.dataset.siteId}/agent/actions/${id}/${decision}`, {});
    let result = {};
    try { result = await response.json(); } catch { /* Response may be empty. */ }
    if (response.ok) pendingActions = pendingActions.filter(action => action.id !== id);
    return { ok: response.ok, message: result.summary || result.error || "Action could not be completed." };
  };
  const restore = async messageId => {
    const response = await postJson(root.dataset.restore, { message_id: messageId });
    if (response.ok) {
      rows.push({ role: "assistant", content: "Draft restored." });
      render();
    }
    return response.ok;
  };
  if (messages) wireRestore(messages, restore);
  const submit = async value => {
    const message = value.trim();
    if (!message || busy || !configured) return;
    busy = true;
    abortController = new AbortController();
    rows.push({ role: "user", content: message });
    const answer = { role: "assistant", content: "", events: [], running: true };
    rows.push(answer);
    if (send) {
      send.textContent = "■";
      send.setAttribute("aria-label", "Stop assistant response");
      send.title = "Stop";
    }
    render();
    try {
      const prompt = isDrawer ? `${message}\n\n${contextHint()}` : message;
      const response = await postJson(root.dataset.stream, {
        message: prompt, page_id: root.dataset.pageId || "", conversation_id: conversationId || null,
      }, abortController.signal);
      await readAgentStream(response, event => {
          if (event.type === "conversation") conversationId = event.conversation_id || event.id || conversationId;
          if (event.type === "tool_called") answer.events.push({ ...event, status: "running" });
          if (event.type === "tool_result") {
            const step = [...answer.events].reverse().find(item => item.name === event.name && item.status === "running");
            if (step) {
              let result = {};
              try { result = JSON.parse(event.summary || "{}"); } catch { /* Keep progress text safe. */ }
              step.status = result.error ? "error" : "done";
            }
          }
          if (event.type === "action_pending") pendingActions.push({ id: event.id, kind: event.kind, summary: event.summary });
          if (event.type === "assistant_text") answer.content += event.text || "";
          if (event.type === "error") answer.error = event.message || "Something went wrong.";
          if (event.type === "done") answer.running = false;
          render();
      });
    } catch (error) {
      answer.running = false;
      if (error.name !== "AbortError") answer.error = error.message || "The assistant could not reply.";
    } finally {
      answer.running = false;
      busy = false;
      abortController = null;
      if (send) {
        send.textContent = "↑";
        send.setAttribute("aria-label", "Send message");
        send.title = "Send";
      }
      render();
      await loadConversations();
    }
  };

  form?.addEventListener("submit", event => {
    event.preventDefault();
    if (busy) { abortController?.abort(); return; }
    const value = input.value.trim();
    if (value) { input.value = ""; submit(value); }
  });
  input?.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); }
  });
  input?.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 192)}px`;
  });
  root.addEventListener("click", event => {
    const suggestion = event.target.closest("[data-suggestion]");
    if (suggestion) { submit(suggestion.dataset.suggestion); return; }
    const conversation = event.target.closest("[data-conversation-id]");
    if (conversation) { history(conversation.dataset.conversationId); return; }
    if (event.target.closest("[data-new-chat]")) {
      conversationId = ""; rows = []; pendingActions = []; render(); loadConversations(); input?.focus();
    }
    const retry = event.target.closest("[data-retry-message]");
    if (retry) submit(retry.dataset.retryMessage);
  });
  select?.addEventListener("change", () => history(select.value));

  if (isDrawer) {
    const focusable = () => [...panel.querySelectorAll('button:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')]
      .filter(item => !item.hidden && item.getAttribute("aria-hidden") !== "true");
    const open = async value => {
      root.classList.toggle("is-open", value);
      overlay.hidden = !value;
      launcher.setAttribute("aria-expanded", String(value));
      panel.setAttribute("aria-hidden", String(!value));
      panel.inert = !value;
      document.body.classList.toggle("agent-drawer-open", value);
      if (value) { await history(conversationId); (input || focusable()[0])?.focus(); }
      else launcher.focus();
    };
    launcher.addEventListener("click", () => open(true));
    root.querySelectorAll("[data-agent-close]").forEach(button => button.addEventListener("click", () => open(false)));
    document.addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "j") { event.preventDefault(); open(!root.classList.contains("is-open")); }
      if (event.key === "Escape" && root.classList.contains("is-open")) { event.preventDefault(); open(false); }
      if (event.key === "Tab" && root.classList.contains("is-open")) {
        const items = focusable();
        if (!items.length) { event.preventDefault(); return; }
        const first = items[0], last = items[items.length - 1];
        if (!panel.contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
        else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
  }
  loadConversations().then(() => { if (!isDrawer) history(""); });
}
