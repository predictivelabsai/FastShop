(() => {
  const roots = [...document.querySelectorAll('[data-agent-root], [data-agent-launcher]')];
  if (!roots.length) return;
  const escapeText = value => String(value ?? '');

  for (const root of roots) {
    const drawer = root.matches('[data-agent-launcher]');
    const siteId = root.dataset.siteId;
    const csrf = root.dataset.csrf;
    const streamUrl = root.dataset.stream;
    const historyUrl = root.dataset.history;
    const conversationsUrl = root.dataset.conversations;
    const restoreUrl = root.dataset.restore;
    const pageId = root.dataset.pageId || '';
    const messages = root.querySelector('#agent-messages');
    const list = root.querySelector('#agent-conversations');
    const select = root.querySelector('[data-agent-select]');
    const suggestions = root.querySelector('#agent-suggestions');
    const launcher = root.querySelector('.agent-launch');
    const panel = root.querySelector('.agent-drawer');
    let conversationId = '';
    let busy = false;

    const contextHint = () => {
      const heading = document.querySelector('main h1')?.textContent?.trim() || document.title;
      return `Admin page context: ${heading}; route ${location.pathname}. Use this as a hint only.`;
    };
    const bubble = (role, text, id = '') => {
      const item = document.createElement('article');
      item.className = `agent-message ${role}`;
      if (id) item.dataset.messageId = id;
      item.textContent = escapeText(text);
      messages.append(item);
      messages.scrollTop = messages.scrollHeight;
      return item;
    };
    const loadConversations = async () => {
      const response = await fetch(conversationsUrl, { headers: { Accept: 'application/json' } });
      if (!response.ok) return;
      const rows = await response.json();
      if (list) {
        list.replaceChildren();
        if (!rows.length) list.innerHTML = '<p class="agent-conversation-empty">Your conversations will appear here.</p>';
        for (const row of rows) {
          const button = document.createElement('button');
          button.type = 'button'; button.className = 'agent-conversation';
          button.dataset.conversationId = row.id; button.textContent = row.title || 'New conversation';
          button.setAttribute('aria-current', String(row.id === conversationId));
          list.append(button);
        }
      }
      if (select) {
        select.replaceChildren(new Option('New chat', ''));
        for (const row of rows) select.add(new Option(row.title || 'New conversation', row.id));
        select.value = conversationId;
      }
    };
    const renderHistory = async id => {
      const url = new URL(historyUrl, location.origin);
      if (id) url.searchParams.set('conversation_id', id);
      const response = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!response.ok) return;
      const data = await response.json();
      conversationId = data.conversation_id || '';
      messages.replaceChildren();
      for (const row of data.messages || []) {
        if (row.role === 'assistant') {
          const article = bubble('assistant', row.content, row.id);
          if (row.events?.length) {
            const steps = document.createElement('div'); steps.className = 'agent-steps';
            for (const item of row.events) {
              const step = document.createElement('div'); step.className = 'agent-step';
              step.textContent = `${item.status === 'error' ? 'Could not complete' : 'Completed'} · ${item.label || item.name}`;
              steps.append(step);
            }
            article.append(steps);
          }
          if (row.has_checkpoint) {
            const restore = document.createElement('button'); restore.type = 'button';
            restore.textContent = 'Restore this design'; restore.dataset.restoreMessage = row.id;
            article.append(restore);
          }
        } else bubble(row.role, row.content);
      }
      for (const action of data.actions || []) renderAction(action.id, action.kind, action.summary);
      if (suggestions) suggestions.hidden = Boolean(data.messages?.length);
      await loadConversations();
    };
    const renderAction = (id, kind, summary) => {
      const card = document.createElement('section'); card.className = 'agent-actions';
      const title = document.createElement('strong'); title.textContent = `Needs your confirmation · ${kind}`;
      const description = document.createElement('p'); description.textContent = summary;
      card.append(title, description);
      for (const decision of ['confirm', 'decline']) {
        const button = document.createElement('button'); button.type = 'button';
        button.textContent = decision === 'confirm' ? 'Confirm' : 'Decline';
        button.dataset.actionId = id; button.dataset.decision = decision;
        card.append(button);
      }
      messages.append(card); messages.scrollTop = messages.scrollHeight;
    };
    const postJson = async (url, body) => fetch(url, { method: 'POST', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': csrf,
    }, body: JSON.stringify({ ...body, csrf_token: csrf }) });
    const submit = async message => {
      if (!message || busy) return;
      busy = true;
      root.querySelectorAll('[data-agent-form] button').forEach(button => { button.disabled = true; });
      bubble('user', message);
      if (suggestions) suggestions.hidden = true;
      const steps = document.createElement('div'); steps.className = 'agent-steps';
      messages.append(steps);
      try {
        const text = drawer ? `${message}\n\n${contextHint()}` : message;
        const response = await postJson(streamUrl, { message: text, page_id: pageId, conversation_id: conversationId || null });
        if (!response.ok) {
          const detail = await response.text();
          throw new Error(detail || 'The assistant could not start this conversation.');
        }
        if (!response.body) throw new Error('The assistant response was empty.');
        const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = '';
        while (true) {
          const { value, done } = await reader.read(); if (done) break;
          pending += decoder.decode(value, { stream: true });
          const chunks = pending.split('\n\n'); pending = chunks.pop() || '';
          for (const chunk of chunks) {
            const line = chunk.split('\n').find(item => item.startsWith('data: ')); if (!line) continue;
            const event = JSON.parse(line.slice(6));
            if (event.type === 'conversation') { conversationId = event.conversation_id; await loadConversations(); }
            if (event.type === 'step_started') {
              const step = document.createElement('div'); step.className = 'agent-step'; step.textContent = `Step ${event.step}`; steps.append(step);
            }
            if (event.type === 'tool_called') {
              const step = document.createElement('div'); step.className = 'agent-step'; step.textContent = `Working · ${event.label || event.name}`; steps.append(step);
            }
            if (event.type === 'tool_result') {
              let failed = false;
              try { failed = Boolean(JSON.parse(event.summary || '{}').error); } catch (_) { /* Keep the progress label safe. */ }
              const step = document.createElement('div'); step.className = 'agent-step';
              step.textContent = `${failed ? 'Could not complete' : 'Finished'} · ${event.name}`; steps.append(step);
            }
            if (event.type === 'action_pending') renderAction(event.id, event.kind, event.summary);
            if (event.type === 'assistant_text') bubble('assistant', event.text);
            if (event.type === 'error') bubble('assistant', event.message || 'Something went wrong.');
          }
        }
      } catch (error) { bubble('assistant', error.message || 'The assistant could not reply.'); }
      finally { busy = false; root.querySelectorAll('[data-agent-form] button').forEach(button => { button.disabled = false; }); await loadConversations(); }
    };

    root.querySelector('[data-agent-form]')?.addEventListener('submit', event => {
      event.preventDefault(); const form = event.currentTarget; const input = form.elements.message;
      const value = input.value.trim(); if (value) { input.value = ''; submit(value); }
    });
    root.addEventListener('click', async event => {
      const suggestion = event.target.closest('[data-suggestion]');
      if (suggestion) { submit(suggestion.dataset.suggestion); return; }
      const conversation = event.target.closest('[data-conversation-id]');
      if (conversation) { await renderHistory(conversation.dataset.conversationId); return; }
      const fresh = event.target.closest('[data-new-chat]');
      if (fresh) { conversationId = ''; messages.replaceChildren(); if (suggestions) suggestions.hidden = false; await loadConversations(); return; }
      const restore = event.target.closest('[data-restore-message]');
      if (restore) {
        const response = await postJson(restoreUrl, { message_id: restore.dataset.restoreMessage });
        if (response.ok) bubble('assistant', 'Design restored as a draft.');
        return;
      }
      const action = event.target.closest('[data-action-id]');
      if (action) {
        action.disabled = true;
        const response = await postJson(`/admin/sites/${siteId}/agent/actions/${action.dataset.actionId}/${action.dataset.decision}`, {});
        const result = await response.json();
        const card = action.closest('.agent-actions');
        card.textContent = response.ok ? `${result.status === 'confirmed' ? 'Confirmed' : 'Declined'} · ${result.summary}` : (result.error || 'Action could not be completed.');
      }
    });
    select?.addEventListener('change', () => renderHistory(select.value));
    if (drawer) {
      const open = value => {
        root.classList.toggle('is-open', value); launcher.setAttribute('aria-expanded', String(value));
        panel.setAttribute('aria-hidden', String(!value));
        if (value) { root.querySelector('[data-agent-form] textarea')?.focus(); renderHistory(conversationId); }
      };
      launcher.addEventListener('click', () => open(!root.classList.contains('is-open')));
      root.querySelector('[data-agent-close]')?.addEventListener('click', () => open(false));
      document.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'j') { event.preventDefault(); open(!root.classList.contains('is-open')); }
        if (event.key === 'Escape' && root.classList.contains('is-open')) open(false);
      });
    }
    loadConversations().then(() => renderHistory(''));
  }
})();
