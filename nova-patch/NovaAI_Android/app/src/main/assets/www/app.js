const $ = (selector) => document.querySelector(selector);
const chat = $("#chat");
const promptBox = $("#prompt");
const sendBtn = $("#sendBtn");

const readJson = (key, fallback) => {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    return value == null ? fallback : value;
  } catch (_) {
    return fallback;
  }
};

let mode = localStorage.getItem("nova.mode") || "solo";
let settings = readJson("nova.settings", {});
let sessions = readJson("nova.sessions", []);
let currentSessionId = localStorage.getItem("nova.currentSession") || "";
let history = [];
let busy = false;
let callbackCounter = 0;
const pending = new Map();
const welcomeMarkup = $("#welcome")?.outerHTML || "";
const MODEL_NAME = "Qwen3.5 9B";
const MAX_SESSIONS = 18;
const MAX_MESSAGES = 100;
const MAX_MESSAGE_CHARS = 14000;

function makeId() {
  return (globalThis.crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : "nova-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

function normaliseSession(session) {
  return {
    id: String(session?.id || makeId()),
    title: String(session?.title || "Новый чат").slice(0, 80),
    updatedAt: Number(session?.updatedAt || Date.now()),
    messages: Array.isArray(session?.messages) ? session.messages.slice(-MAX_MESSAGES) : []
  };
}

function ensureSessions() {
  sessions = Array.isArray(sessions) ? sessions.map(normaliseSession) : [];
  if (!sessions.length) {
    const oldHistory = readJson("nova.history", []);
    const migrated = Array.isArray(oldHistory) ? oldHistory.filter(m => m && (m.role === "user" || m.role === "assistant")) : [];
    sessions.push({
      id: makeId(),
      title: migrated.find(m => m.role === "user")?.content?.slice(0, 36) || "Новый чат",
      updatedAt: Date.now(),
      messages: migrated.slice(-MAX_MESSAGES)
    });
  }
  if (!sessions.some(s => s.id === currentSessionId)) currentSessionId = sessions[0].id;
  sessions.sort((a, b) => b.updatedAt - a.updatedAt);
  activeSession().messages = activeSession().messages || [];
  history = activeSession().messages.slice();
  persistSessions();
}

function activeSession() {
  return sessions.find(s => s.id === currentSessionId) || sessions[0];
}

function persistSessions() {
  try {
    sessions.sort((a, b) => b.updatedAt - a.updatedAt);
    sessions = sessions.slice(0, MAX_SESSIONS);
    localStorage.setItem("nova.sessions", JSON.stringify(sessions));
    localStorage.setItem("nova.currentSession", currentSessionId);
    localStorage.setItem("nova.history", JSON.stringify(history.slice(-MAX_MESSAGES)));
  } catch (error) {
    // Keep the active conversation if storage is close to the WebView quota.
    try {
      sessions = [activeSession()].filter(Boolean);
      sessions[0].messages = history.slice(-40).map(m => ({ ...m, content: String(m.content).slice(-5000) }));
      localStorage.setItem("nova.sessions", JSON.stringify(sessions));
      localStorage.setItem("nova.currentSession", currentSessionId);
    } catch (_) {}
  }
}

function saveHistory() {
  const session = activeSession();
  if (!session) return;
  history = history.slice(-MAX_MESSAGES).map(m => ({
    role: m.role,
    content: String(m.content || "").slice(0, MAX_MESSAGE_CHARS),
    label: String(m.label || ""),
    at: Number(m.at || Date.now())
  }));
  session.messages = history;
  session.updatedAt = Date.now();
  const firstUser = history.find(m => m.role === "user");
  if (firstUser && (session.title === "Новый чат" || !session.title)) {
    session.title = firstUser.content.replace(/\s+/g, " ").trim().slice(0, 42) || "Новый чат";
  }
  persistSessions();
  renderSidebar();
}

function setMode(next) {
  mode = next === "council" ? "council" : "solo";
  localStorage.setItem("nova.mode", mode);
  document.querySelectorAll(".mode").forEach(button => {
    button.classList.toggle("active", button.dataset.mode === mode);
    button.setAttribute("aria-pressed", button.dataset.mode === mode ? "true" : "false");
  });

  const welcomeText = $("#welcomeText");
  if (welcomeText) {
    welcomeText.textContent = mode === "solo"
      ? "Спроси о чём угодно, попроси разобраться в сложной теме или вместе создай что-то новое."
      : "Сравни ответы нескольких настроенных моделей. Они могут ошибаться — проверяй важные выводы.";
  }
  const composerNote = $("#composerNote");
  if (composerNote) composerNote.textContent = mode === "council"
    ? "Запрос будет отправлен подключённым облачным моделям"
    : "Qwen3.5 9B · локальная обработка";
  const connectionStatus = $("#connectionStatus");
  if (connectionStatus) {
    if (mode === "council") {
      connectionStatus.textContent = "Совет ИИ · " + getProviders().length + " моделей";
    } else {
      try {
        const status = JSON.parse(window.NovaBridge?.getLocalModelStatus?.() || "{}");
        connectionStatus.textContent = status.ready ? "Qwen3.5 9B · готова" : "Qwen3.5 9B · локально";
      } catch (_) {
        connectionStatus.textContent = "Qwen3.5 9B · локально";
      }
    }
  }
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function inlineFormat(value) {
  let html = value;
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__(.+?)__/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  html = html.replace(/_([^_\n]+)_/g, "<em>$1</em>");
  html = html.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');
  html = html.replace(/(https?:\/\/[^\s<]+)/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
  return html;
}

function renderMarkdown(raw) {
  const codeBlocks = [];
  let source = String(raw || "").replace(/\r\n?/g, "\n");
  source = source.replace(/```([^\n`]*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const token = "NOVA_CODE_BLOCK_" + codeBlocks.length + "_END";
    codeBlocks.push({ token, lang: String(lang || "").trim(), code: String(code || "").replace(/\n$/, "") });
    return token;
  });

  const escapedLines = escapeHtml(source).split("\n");
  const output = [];
  let paragraph = [];
  let listType = "";

  const closeList = () => {
    if (!listType) return;
    output.push(listType === "ol" ? "</ol>" : "</ul>");
    listType = "";
  };
  const flushParagraph = () => {
    if (!paragraph.length) return;
    output.push("<p>" + paragraph.map(line => inlineFormat(line)).join("<br>") + "</p>");
    paragraph = [];
  };
  const openList = (type) => {
    flushParagraph();
    if (listType !== type) {
      closeList();
      output.push(type === "ol" ? "<ol>" : "<ul>");
      listType = type;
    }
  };

  for (const rawLine of escapedLines) {
    const line = rawLine.trimEnd();
    const codeBlock = codeBlocks.find(block => block.token === line.trim());
    if (codeBlock) {
      flushParagraph();
      closeList();
      const label = escapeHtml(codeBlock.lang || "Код");
      output.push(
        '<div class="code-block"><div class="code-header"><span>' + label +
        '</span><button class="copy-code" type="button" data-action="copy-code">Копировать</button></div>' +
        '<pre><code>' + escapeHtml(codeBlock.code) + '</code></pre></div>'
      );
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      closeList();
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length;
      output.push("<h" + level + ">" + inlineFormat(heading[2]) + "</h" + level + ">");
      continue;
    }
    const bullet = line.match(/^\s*[-*+]\s+(.+)$/);
    if (bullet) {
      openList("ul");
      output.push("<li>" + inlineFormat(bullet[1]) + "</li>");
      continue;
    }
    const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (numbered) {
      openList("ol");
      output.push("<li>" + inlineFormat(numbered[1]) + "</li>");
      continue;
    }
    if (/^\s*&gt;\s?/.test(line)) {
      flushParagraph();
      closeList();
      output.push('<blockquote>' + inlineFormat(line.replace(/^\s*&gt;\s?/, "")) + '</blockquote>');
      continue;
    }
    if (/^\s*[-*_]{3,}\s*$/.test(line)) {
      flushParagraph();
      closeList();
      output.push("<hr>");
      continue;
    }
    closeList();
    paragraph.push(line);
  }
  flushParagraph();
  closeList();
  return output.join("") || "<p></p>";
}

function addMessage(role, content, label, persist = true) {
  $("#welcome")?.remove();
  const message = document.createElement("article");
  message.className = "message " + role;
  message.dataset.raw = String(content || "");
  const body = document.createElement("div");
  body.className = "message-body";

  if (role === "assistant") {
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = "N";
    avatar.setAttribute("aria-hidden", "true");
    message.append(avatar);
    const heading = document.createElement("div");
    heading.className = "message-label";
    heading.textContent = label || "Nova";
    body.append(heading);
  }

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  if (role === "assistant") bubble.innerHTML = renderMarkdown(content);
  else bubble.textContent = String(content || "");
  body.append(bubble);

  if (role === "assistant") {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    actions.innerHTML =
      '<button class="message-action" type="button" data-action="copy-message" aria-label="Копировать ответ">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg><span>Копировать</span></button>' +
      '<button class="message-action" type="button" data-action="regenerate" aria-label="Повторить ответ">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 9A7 7 0 0 1 18 6l2 6M4 12l2 6a7 7 0 0 0 11.9-3"/></svg><span>Повторить</span></button>';
    body.append(actions);
  }

  message.append(body);
  chat.append(message);
  if (persist) {
    history.push({ role, content: String(content || ""), label: label || "", at: Date.now() });
    saveHistory();
  }
  scrollToBottom();
  return message;
}

function scrollToBottom() {
  requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight; });
}

function renderChat() {
  chat.innerHTML = "";
  if (!history.length) {
    if (welcomeMarkup) chat.innerHTML = welcomeMarkup;
  } else {
    history.forEach(message => addMessage(message.role, message.content, message.label, false));
  }
  scrollToBottom();
}

function renderSidebar() {
  const list = $("#historyList");
  if (!list) return;
  list.innerHTML = "";
  const visible = sessions.slice().sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_SESSIONS);
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "Здесь появятся ваши чаты.";
    list.append(empty);
    return;
  }
  visible.forEach(session => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item" + (session.id === currentSessionId ? " active" : "");
    button.setAttribute("aria-current", session.id === currentSessionId ? "page" : "false");
    const icon = document.createElement("span");
    icon.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l1.7-3.4A7.5 7.5 0 1 1 20 11.5z"/></svg>';
    const title = document.createElement("span");
    title.textContent = session.title || "Новый чат";
    button.append(icon, title);
    button.addEventListener("click", () => switchSession(session.id));
    list.append(button);
  });
}

function createWelcome() {
  if (welcomeMarkup) chat.innerHTML = welcomeMarkup;
}

function createNewChat() {
  closeSidebar();
  if (!history.length) {
    promptBox.focus();
    return;
  }
  const session = { id: makeId(), title: "Новый чат", updatedAt: Date.now(), messages: [] };
  sessions.unshift(session);
  currentSessionId = session.id;
  history = [];
  persistSessions();
  renderChat();
  renderSidebar();
  setMode(mode);
  promptBox.focus();
}

function switchSession(id) {
  if (id === currentSessionId) {
    closeSidebar();
    return;
  }
  saveHistory();
  if (!sessions.some(s => s.id === id)) return;
  currentSessionId = id;
  localStorage.setItem("nova.currentSession", currentSessionId);
  history = activeSession().messages.slice();
  renderChat();
  renderSidebar();
  closeSidebar();
}

function openSidebar() {
  $("#sidebar")?.classList.add("open");
  $("#sidebarOverlay")?.classList.add("open");
}
function closeSidebar() {
  $("#sidebar")?.classList.remove("open");
  $("#sidebarOverlay")?.classList.remove("open");
}

function makeMessages(userText) {
  const system = "Ты — Nova AI, компетентный помощник. Отвечай по-русски, если пользователь пишет по-русски; иначе используй его язык. Выполняй саму задачу, а не только объясняй её. Проверяй числа и код, указывай неопределённость, не выдумывай факты. Дай краткий вывод, а затем необходимые шаги и обоснование без раскрытия скрытых внутренних рассуждений.";
  const previous = history.slice(-15, -1)
    .filter(message => message.role === "user" || message.role === "assistant")
    .map(message => ({ role: message.role, content: message.content }));
  return [{ role: "system", content: system }, ...previous, { role: "user", content: userText }];
}

function apiCall(provider, messages) {
  return new Promise(resolve => {
    if (!window.NovaBridge?.chat) {
      resolve({ ok: false, provider: provider.name, error: "Сетевой модуль недоступен" });
      return;
    }
    const id = "nova-" + (++callbackCounter);
    const timeout = setTimeout(() => {
      pending.delete(id);
      resolve({ ok: false, provider: provider.name, error: "Превышено время ожидания" });
    }, 60000);
    pending.set(id, result => {
      clearTimeout(timeout);
      result.provider = provider.name;
      resolve(result);
    });
    try {
      window.NovaBridge.chat(JSON.stringify({
        endpoint: provider.endpoint,
        apiKey: provider.key || "",
        modelName: provider.model,
        messages
      }), id);
    } catch (error) {
      clearTimeout(timeout);
      pending.delete(id);
      resolve({ ok: false, provider: provider.name, error: error.message || "Ошибка запроса" });
    }
  });
}

window.__novaApiCallback = (id, raw) => {
  const done = pending.get(id);
  if (!done) return;
  pending.delete(id);
  try { done(JSON.parse(raw)); }
  catch (_) { done({ ok: false, error: "Не удалось разобрать ответ API." }); }
};

window.__novaLocalProgress = (id, message) => {
  const active = $(".typing-text");
  if (active) active.textContent = String(message || "Локальная модель работает…");
};

function localCall(messages) {
  return new Promise(resolve => {
    if (!window.NovaBridge?.localChat) {
      resolve({ ok: false, error: "Локальная нейросеть недоступна в этой сборке." });
      return;
    }
    const id = "nova-local-" + (++callbackCounter);
    const timeout = setTimeout(() => {
      pending.delete(id);
      resolve({ ok: false, error: "Модель работает слишком долго. Проверь свободную память и повтори запрос." });
    }, 20 * 60 * 1000);
    pending.set(id, result => {
      clearTimeout(timeout);
      resolve(result);
    });
    try { window.NovaBridge.localChat(JSON.stringify({ messages }), id); }
    catch (error) {
      clearTimeout(timeout);
      pending.delete(id);
      resolve({ ok: false, error: error.message || "Ошибка локальной модели" });
    }
  });
}

function getProviders() {
  const list = Array.isArray(settings.providers) ? settings.providers : [];
  if (!list.length && settings.endpoint && settings.modelName) {
    return [{
      name: settings.providerName || "Настроенный API",
      endpoint: settings.endpoint,
      model: settings.modelName,
      key: settings.apiKey || ""
    }];
  }
  return list.filter(provider => provider && provider.endpoint && provider.model).slice(0, 3);
}

async function answerWithFreeServices(userText) {
  const messages = makeMessages(userText);
  const providers = getProviders();
  if (mode === "solo") {
    const local = await localCall(messages);
    if (local?.ok && local.answer?.trim()) {
      return { answer: local.answer, source: local.provider || MODEL_NAME + " · локально" };
    }
    if (providers.length) {
      const fallback = await apiCall(providers[0], messages);
      if (fallback.ok && fallback.answer?.trim()) {
        return {
          answer: fallback.answer,
          source: fallback.provider,
          meta: "Локальная модель недоступна; резервный ответ получен от настроенного облачного API."
        };
      }
      return { error: (local?.error || "Локальная модель недоступна.") + "\nРезервный API: " + (fallback.error || "нет ответа") };
    }
    return { error: local?.error || "Локальная модель не ответила." };
  }

  if (providers.length < 2) {
    return { error: "Для режима «Совет ИИ» настрой минимум две совместимые модели. Локальная модель работает отдельно в режиме Nova." };
  }
  const results = await Promise.all(providers.map(provider =>
    apiCall(provider, messages).catch(error => ({
      ok: false, provider: provider.name, error: error.message || "Ошибка запроса"
    }))
  ));
  const good = results.filter(result => result.ok && result.answer && result.answer.trim());
  if (!good.length) {
    return {
      error: "Ни одна модель не ответила. " +
        results.map(result => (result.provider || "Модель") + ": " + (result.error || "пустой ответ")).join(" · ")
    };
  }
  const sections = good.map(result => "Модель: " + result.provider + "\n" + result.answer);
  const combined = "## Сравнение ответов\n\n" + sections.join("\n\n---\n\n") +
    "\n\n*Это сравнение ответов, а не автоматическая проверка фактической точности.*";
  return { answer: combined, source: "Совет ИИ" };
}

function showTyping() {
  const wrapper = document.createElement("div");
  wrapper.className = "typing";
  wrapper.id = "activeTyping";
  wrapper.innerHTML = '<div class="typing-avatar">N</div><div class="typing-content"><span class="typing-text">Запускаю локальную модель…</span><span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span></div>';
  chat.append(wrapper);
  scrollToBottom();
  return wrapper;
}

async function executeAnswer(userText) {
  const typing = showTyping();
  typing.querySelector(".typing-text").textContent =
    mode === "council" ? "Сравниваю ответы подключённых моделей…" : "Подготавливаю Qwen3.5 9B…";
  try {
    const result = await answerWithFreeServices(userText);
    typing.remove();
    if (result.answer) {
      addMessage("assistant", result.answer, "Nova · " + (result.source || (mode === "council" ? "Совет ИИ" : MODEL_NAME)));
      if (result.meta) addMessage("assistant", result.meta, "Дополнительная информация", false);
    } else {
      addMessage("assistant", result.error || "Не удалось получить ответ. Попробуй ещё раз.", "Не удалось ответить");
    }
  } catch (error) {
    typing.remove();
    addMessage("assistant", "Ошибка: " + (error.message || String(error)), "Ошибка");
  } finally {
    typing.remove();
    busy = false;
    sendBtn.disabled = false;
    promptBox.focus();
  }
}

async function send(text) {
  text = String(text || "").trim();
  if (!text || busy) return;
  busy = true;
  sendBtn.disabled = true;
  promptBox.value = "";
  promptBox.style.height = "auto";
  addMessage("user", text, "Вы");
  await executeAnswer(text);
}

function regenerateLastAnswer() {
  if (busy) return;
  let userIndex = -1;
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i].role === "user") { userIndex = i; break; }
  }
  if (userIndex < 0) return;
  const userText = history[userIndex].content;
  history = history.slice(0, userIndex + 1);
  saveHistory();
  renderChat();
  busy = true;
  sendBtn.disabled = true;
  executeAnswer(userText);
}

async function copyText(text) {
  const value = String(text || "");
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      const area = document.createElement("textarea");
      area.value = value;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    return true;
  } catch (_) {
    return false;
  }
}

function flashButton(button, label) {
  const previous = button.textContent;
  button.textContent = label;
  setTimeout(() => { if (button.isConnected) button.textContent = previous; }, 1200);
}

document.querySelectorAll(".mode").forEach(button =>
  button.addEventListener("click", () => setMode(button.dataset.mode))
);

$("#composer").addEventListener("submit", event => {
  event.preventDefault();
  send(promptBox.value);
});

promptBox.addEventListener("input", () => {
  promptBox.style.height = "auto";
  promptBox.style.height = Math.min(promptBox.scrollHeight, 160) + "px";
});

promptBox.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("#composer").requestSubmit();
  }
});

chat.addEventListener("click", event => {
  const suggestion = event.target.closest(".suggestion");
  if (suggestion) {
    send(suggestion.dataset.prompt || suggestion.textContent.trim());
    return;
  }
  const action = event.target.closest("[data-action]");
  if (!action) return;
  const type = action.dataset.action;
  if (type === "regenerate") {
    regenerateLastAnswer();
  } else if (type === "copy-message") {
    const article = action.closest(".message");
    copyText(article?.dataset.raw || "").then(ok => flashButton(action.querySelector("span") || action, ok ? "Скопировано" : "Не удалось"));
  } else if (type === "copy-code") {
    const code = action.closest(".code-block")?.querySelector("code")?.textContent || "";
    copyText(code).then(ok => flashButton(action, ok ? "Скопировано" : "Не удалось"));
  }
});

$("#newChat").addEventListener("click", createNewChat);
$("#brandHome").addEventListener("click", event => {
  event.preventDefault();
  createNewChat();
});
$("#sidebarToggle").addEventListener("click", openSidebar);
$("#sidebarClose").addEventListener("click", closeSidebar);
$("#sidebarOverlay").addEventListener("click", closeSidebar);

$("#clearChat").addEventListener("click", () => {
  if (!history.length || !confirm("Очистить сообщения в этом чате?")) return;
  history = [];
  const session = activeSession();
  if (session) {
    session.messages = [];
    session.title = "Новый чат";
    session.updatedAt = Date.now();
  }
  saveHistory();
  renderChat();
  renderSidebar();
  setMode(mode);
});

$("#settingsOpen").addEventListener("click", () => {
  const providers = getProviders();
  const first = providers[0] || {};
  $("#providerName").value = first.name || settings.providerName || "";
  $("#endpoint").value = first.endpoint || settings.endpoint || "";
  $("#apiKey").value = first.key || settings.apiKey || "";
  $("#modelName").value = first.model || settings.modelName || "";
  $("#endpoint2").value = providers[1]?.endpoint || "";
  $("#providerName2").value = providers[1]?.name || "";
  $("#apiKey2").value = providers[1]?.key || "";
  $("#modelName2").value = providers[1]?.model || "";
  $("#endpoint3").value = providers[2]?.endpoint || "";
  $("#providerName3").value = providers[2]?.name || "";
  $("#apiKey3").value = providers[2]?.key || "";
  $("#modelName3").value = providers[2]?.model || "";
  $("#settingsDialog").showModal();
});

$("#providerPreset").addEventListener("change", event => {
  if (event.target.value === "openrouter") {
    $("#providerName").value = "OpenRouter";
    $("#endpoint").value = "https://openrouter.ai/api/v1/chat/completions";
    $("#modelName").value = "openrouter/free";
  } else if (event.target.value === "groq") {
    $("#providerName").value = "Groq";
    $("#endpoint").value = "https://api.groq.com/openai/v1/chat/completions";
    $("#modelName").value = "llama-3.3-70b-versatile";
  } else if (event.target.value === "custom") {
    $("#providerName").value = "Совместимый API";
  }
});

$("#settingsClose").addEventListener("click", () => $("#settingsDialog").close());

$("#settingsForm").addEventListener("submit", event => {
  event.preventDefault();
  settings = {
    providers: [
      { name: $("#providerName").value.trim() || "Модель 1", endpoint: $("#endpoint").value.trim(), key: $("#apiKey").value, model: $("#modelName").value.trim() },
      { name: $("#providerName2").value.trim() || "Модель 2", endpoint: $("#endpoint2").value.trim(), key: $("#apiKey2").value, model: $("#modelName2").value.trim() },
      { name: $("#providerName3").value.trim() || "Модель 3", endpoint: $("#endpoint3").value.trim(), key: $("#apiKey3").value, model: $("#modelName3").value.trim() }
    ].filter(provider => provider.endpoint && provider.model)
  };
  try { localStorage.setItem("nova.settings", JSON.stringify(settings)); }
  catch (_) { alert("Не удалось сохранить настройки. Проверь свободное место в данных приложения."); }
  $("#settingsDialog").close();
  setMode(mode);
});

$("#forgetKey").addEventListener("click", () => {
  settings.apiKey = "";
  if (Array.isArray(settings.providers)) settings.providers.forEach(provider => { provider.key = ""; });
  localStorage.setItem("nova.settings", JSON.stringify(settings));
  ["apiKey", "apiKey2", "apiKey3"].forEach(id => { $("#" + id).value = ""; });
});

$("#wipeAll").addEventListener("click", () => {
  if (!confirm("Удалить всю историю чатов, настройки и локальные данные Nova?")) return;
  ["nova.history", "nova.sessions", "nova.currentSession", "nova.settings", "nova.mode"].forEach(key => localStorage.removeItem(key));
  sessions = [];
  history = [];
  settings = {};
  $("#settingsDialog").close();
  location.reload();
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape") closeSidebar();
});

ensureSessions();
renderSidebar();
renderChat();
setMode(mode);
