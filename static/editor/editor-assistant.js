/* Theme editor Assistant mode. editor.js loads this only when the store assistant is installed. */
import { readAgentStream, renderAction, renderMessages, wireRestore } from "../agent-chat.js";

export function createAssistantPanel(ctx) {
  const { boot, $, $$, esc, icon, blockIcon, label } = ctx;
  const site = encodeURIComponent(boot.site_id);
  const configured = Boolean(boot.features?.assistant_configured);
  const settingsHref = boot.features?.assistant_settings_href || `/admin/sites/${site}/settings/assistant`;
  let messages = [], conversation = null, abort = null, pending = [];

  function renderUnconfigured() {
    $("#left-actions").innerHTML = "";
    $("#left-content").innerHTML = `<section class="agent-unconfigured-panel" aria-label="Assistant not connected" data-agent-unconfigured><span class="agent-empty-icon" aria-hidden="true">${icon("sparkles", 20)}</span><strong>Connect an AI model to use the assistant</strong><p>Add your own API key from xAI, OpenAI, OpenRouter or another OpenAI-compatible provider. The key is encrypted and used only for this store.</p><a href="${esc(settingsHref)}">Connect a model</a></section>`;
  }

  function render() {
    if (ctx.mode() !== "assistant") return;
    if (!configured) return renderUnconfigured();
    const context = ctx.locate(ctx.selected())?.block;
    const oldBox = $(".agent-messages"),
      bottom = !oldBox || oldBox.scrollHeight - oldBox.scrollTop - oldBox.clientHeight < 48,
      oldScroll = oldBox?.scrollTop || 0;
    // Re-rendering on every streamed event must not lose what the merchant is typing.
    const previous = $("#agent-input"),
      draft = previous?.value || "",
      hadFocus = previous && document.activeElement === previous,
      caret = previous ? [previous.selectionStart, previous.selectionEnd] : null;
    $("#left-actions").innerHTML = `<button type="button" id="agent-new" class="panel-action" title="Start a new conversation">${icon("square-pen", 15)}<span>New chat</span></button>`;
    const contextChip = context
      ? `<div class="agent-context">${blockIcon(context.type, 14)}<span>Editing: <strong>${esc(label(context))}</strong></span><button type="button" id="agent-clear-context" aria-label="Clear selected block" title="Clear selected block">${icon("x", 14)}</button></div>`
      : "";
    const sendLabel = abort ? "Stop assistant response" : "Send message";
    $("#left-content").innerHTML = `<section class="agent-panel" aria-label="Assistant conversation"><div class="agent-messages agent-chat-shared" aria-live="polite" aria-relevant="additions text"></div><form id="agent-form"><div class="agent-composer">${contextChip}<div class="agent-composer-row"><textarea id="agent-input" rows="1" maxlength="4000" aria-label="Message the assistant" placeholder="${context ? "Describe a change to this block…" : "Describe a change…"}"></textarea><button id="agent-send" type="submit" class="${abort ? "is-stop" : ""}" aria-label="${sendLabel}" title="${abort ? "Stop" : "Send"}">${abort ? icon("square", 14) : icon("arrow-up", 16)}</button></div></div><div class="agent-compose-hint">Enter to send, Shift and Enter for a new line</div></form></section>`;
    const box = $(".agent-messages");
    renderMessages(box, messages, decide);
    if (!messages.length) {
      box.innerHTML = `<div class="agent-empty"><span class="agent-empty-icon" aria-hidden="true">${icon("sparkles", 20)}</span><strong>What would you like to change?</strong><p>Ask for an edit to this page, or a question about your store.</p><div class="agent-suggestions"><button>Make the hero headline shorter</button><button>Add an FAQ section to this page</button><button>Check this page draft for missing alt text</button><button>Review shipping, tax and store policies</button><button>Which products sold best this month?</button></div></div>`;
    }
    for (const action of pending) renderAction(box, action, decide);
    wireRestore(box, async (messageId) => {
      const result = await fetch(`/admin/sites/${site}/editor/agent/restore`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": boot.csrf_token },
        body: JSON.stringify({ message_id: messageId }),
      });
      if (result.ok) { await ctx.load(); ctx.setMode("assistant"); ctx.renderLeft(); }
      else ctx.toast("Could not restore this change.", true);
      return result.ok;
    });
    $("#agent-new").onclick = () => { conversation = null; messages = []; pending = []; render(); $("#agent-input").focus(); };
    $$(".agent-suggestions button").forEach((b) => (b.onclick = () => { $("#agent-input").value = b.textContent; send(); }));
    $("#agent-form").onsubmit = (e) => { e.preventDefault(); abort ? abort.abort() : send(); };
    $("#agent-clear-context")?.addEventListener("click", () => { ctx.select(null); $("#agent-input")?.focus(); });
    const input = $("#agent-input");
    const grow = () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 192)}px`; };
    input.oninput = grow;
    if (draft) { input.value = draft; grow(); }
    if (hadFocus) { input.focus(); if (caret) input.setSelectionRange(caret[0], caret[1]); }
    input.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#agent-form").requestSubmit(); } };
    $$("[data-retry-message]").forEach((b) => (b.onclick = () => { $("#agent-input").value = b.dataset.retryMessage; send(); }));
    $$("[data-action-decision]").forEach((button) => (button.onclick = () => decide(button.dataset.actionId, button.dataset.actionDecision)));
    // Follow the conversation while at the end; otherwise keep the reader's place.
    box.scrollTop = bottom ? box.scrollHeight : oldScroll;
  }

  async function decide(actionId, decision) {
    const action = [...messages.flatMap((message) => message.actions || []), ...pending]
      .find((item) => item.id === actionId);
    if (!action || action.status !== "pending" || action.submitting)
      return { ok: false, message: "This action is no longer available." };
    action.submitting = decision;
    action.error = "";
    try {
      const result = await fetch(`/admin/sites/${site}/agent/actions/${encodeURIComponent(actionId)}/${decision}`, {
        method: "POST", headers: { "X-CSRF-Token": boot.csrf_token }, credentials: "same-origin",
        body: new URLSearchParams({ csrf_token: boot.csrf_token }),
      });
      const text = await result.text();
      let data = {};
      try { data = JSON.parse(text); }
      catch { if (!result.ok) throw Error(text || "The action could not be updated."); }
      if (!result.ok) throw Error(data.error || "The action could not be updated.");
      action.status = data.status;
      action.result = data.result || {};
      await loadHistory();
      return { ok: true, message: data.summary || "Action updated." };
    } catch (error) {
      action.error = error.message || "The action could not be updated. Try again.";
      return { ok: false, message: action.error };
    } finally {
      action.submitting = "";
    }
  }

  async function loadHistory() {
    if (!configured) return render();
    const result = await fetch(`/admin/sites/${site}/editor/agent/history`);
    if (result.ok) {
      const data = await result.json();
      conversation = data.conversation_id; messages = data.messages || []; pending = data.actions || [];
    }
    render();
  }

  async function send() {
    const input = $("#agent-input"), message = input.value.trim();
    if (!message) return;
    messages.push({ role: "user", content: message }); input.value = "";
    const answer = { role: "assistant", content: "", events: [] }; messages.push(answer);
    abort = new AbortController(); render();
    try {
      const result = await fetch(`/admin/sites/${site}/editor/agent`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": boot.csrf_token },
        credentials: "same-origin", signal: abort.signal,
        body: JSON.stringify({ message, page_id: boot.page_id,
          selection: ctx.selected() ? { block_id: ctx.selected() } : null, conversation_id: conversation }),
      });
      answer.running = true;
      await readAgentStream(result, (ev) => {
        if (ev.type === "conversation") conversation = ev.conversation_id || ev.id;
        if (ev.type === "assistant_text") answer.content += ev.text || "";
        if (ev.type === "tool_called") answer.events.push({ ...ev, status: "running" });
        if (ev.type === "tool_result") {
          const step = [...answer.events].reverse().find((item) => item.name === ev.name && item.status === "running");
          if (step) { let parsed = {}; try { parsed = JSON.parse(ev.summary || "{}"); } catch { /* Not JSON. */ } step.status = parsed.error ? "error" : "done"; }
          clearTimeout(window.agentRefreshTimer); window.agentRefreshTimer = setTimeout(() => ctx.load(), 300);
        }
        if (ev.type === "action_pending") { answer.actions = answer.actions || []; answer.actions.push({ ...ev, status: "pending", waiting_for_turn: true }); }
        if (ev.type === "done") { answer.running = false; (answer.actions || []).forEach((a) => (a.waiting_for_turn = false)); }
        if (ev.type === "error") { answer.running = false; (answer.actions || []).forEach((a) => (a.waiting_for_turn = false)); answer.error = "I couldn’t complete that change. Please try again."; answer.retry = message; }
        render();
      });
    } catch (e) {
      answer.running = false;
      if (e.name !== "AbortError") { answer.error = e.message === "The assistant could not start." ? "I couldn’t start that change." : "I couldn’t connect just now."; answer.retry = message; }
    } finally {
      abort = null;
      try {
        const result = await fetch(`/admin/sites/${site}/editor/agent/history`);
        if (result.ok) { const data = await result.json(); conversation = data.conversation_id; if (data.messages?.length) messages = data.messages; pending = data.actions || []; }
      } catch { /* Keep the streamed copy. */ }
      render();
    }
  }

  return { render, loadHistory };
}
