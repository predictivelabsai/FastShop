const esc = value => String(value ?? "");

export async function readAgentStream(response, onEvent) {
  if (!response.ok || !response.body) throw new Error("The assistant could not start.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find(item => item.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)));
    }
  }
}

function sparkle() {
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("viewBox", "0 0 20 20");
  icon.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M10 1.5 11.8 8l6.7 2-6.7 2-1.8 6.5L8.2 12 1.5 10l6.7-2L10 1.5Z");
  icon.append(path);
  return icon;
}

export function renderMessages(container, rows = []) {
  container.replaceChildren();
  for (const row of rows) {
    if (!row || !["user", "assistant"].includes(row.role)) continue;
    const article = document.createElement("article");
    article.className = `agent-message ${row.role}`;
    if (row.id) article.dataset.messageId = row.id;
    if (row.role === "assistant") {
      const marker = document.createElement("span");
      marker.className = "agent-avatar";
      marker.append(sparkle());
      article.append(marker);
    }
    const body = document.createElement("div");
    body.className = "agent-message-body";
    body.textContent = esc(row.content);
    if (row.role === "assistant" && row.running) {
      const working = document.createElement("div");
      working.className = "agent-working";
      const spinner = document.createElement("span");
      spinner.className = "agent-spinner";
      spinner.setAttribute("aria-hidden", "true");
      const label = document.createElement("strong");
      label.textContent = "Working";
      working.append(spinner, label);
      body.append(working);
    }
    if (row.role === "assistant" && row.events?.length) {
      const rows = document.createElement(row.running ? "div" : "details");
      rows.className = row.running ? "agent-live-steps" : "agent-changes";
      if (!row.running) {
        const summary = document.createElement("summary");
        summary.textContent = `${row.events.length} ${row.events.length === 1 ? "edit" : "edits"}`;
        rows.append(summary);
      }
      const list = document.createElement("div");
      for (const event of row.events) {
        const step = document.createElement("span");
        const failed = event.status === "error" || event.error;
        const done = event.status === "done" || event.complete;
        step.className = `agent-step ${failed ? "is-error" : done ? "is-done" : "is-running"}`;
        const mark = document.createElement("span");
        mark.setAttribute("aria-hidden", "true");
        mark.textContent = failed ? "!" : done ? "✓" : "";
        step.append(mark, document.createTextNode(event.label || "Working on your page"));
        list.append(step);
      }
      rows.append(list);
      body.append(rows);
    }
    if (row.error) {
      const error = document.createElement("div");
      error.className = "agent-error";
      error.setAttribute("role", "alert");
      error.append(document.createTextNode(row.error));
      if (row.retry) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.dataset.retryMessage = row.retry;
        retry.textContent = "Try again";
        error.append(retry);
      }
      body.append(error);
    }
    if (row.has_checkpoint && row.id) {
      const restore = document.createElement("button");
      restore.type = "button";
      restore.className = "agent-restore";
      restore.dataset.restoreMessage = row.id;
      restore.textContent = "Restore to here";
      body.append(restore);
    }
    article.append(body);
    container.append(article);
  }
}

export function renderAction(container, action, onDecision) {
  const card = document.createElement("section");
  card.className = "agent-actions";
  card.dataset.actionCard = action.id;
  const heading = document.createElement("strong");
  heading.textContent = "Review this change";
  const description = document.createElement("p");
  description.textContent = action.summary || action.kind || "The assistant needs your approval.";
  card.append(heading, description);
  for (const decision of ["confirm", "decline"]) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.actionId = action.id;
    button.dataset.decision = decision;
    button.textContent = decision === "confirm" ? "Confirm" : "Decline";
    button.addEventListener("click", async () => {
      if (card.dataset.state && card.dataset.state !== "pending") return;
      card.dataset.state = decision === "confirm" ? "confirming" : "declining";
      card.querySelectorAll("button").forEach(item => { item.disabled = true; });
      heading.textContent = decision === "confirm" ? "Confirming…" : "Declining…";
      const result = await onDecision(action.id, decision);
      card.dataset.state = result.ok ? (decision === "confirm" ? "confirmed" : "declined") : "pending";
      heading.textContent = result.ok ? (decision === "confirm" ? "Confirmed" : "Declined") : "Could not complete this action";
      description.textContent = result.message || description.textContent;
      card.querySelectorAll("button").forEach(item => { item.disabled = result.ok; });
    });
    card.append(button);
  }
  container.append(card);
  return card;
}

export function wireRestore(container, onRestore) {
  container.addEventListener("click", async event => {
    const button = event.target.closest("[data-restore-message]");
    if (!button || !container.contains(button)) return;
    if (button.nextElementSibling?.classList.contains("agent-restore-confirm")) return;
    const prompt = document.createElement("div");
    prompt.className = "agent-restore-confirm";
    prompt.append(document.createTextNode("Restore the draft to this point? "));
    const yes = document.createElement("button");
    yes.type = "button";
    yes.textContent = "Restore draft";
    yes.addEventListener("click", async () => {
      yes.disabled = true;
      const ok = await onRestore(button.dataset.restoreMessage);
      if (ok) prompt.textContent = "Draft restored.";
      else yes.disabled = false;
    });
    const no = document.createElement("button");
    no.type = "button";
    no.textContent = "Keep current draft";
    no.addEventListener("click", () => prompt.remove());
    prompt.append(yes, no);
    button.after(prompt);
  });
}
