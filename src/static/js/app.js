// App shell: sidebar, top bar, composer, data loading, settings and shortcuts.
import { state, hooks, domainById } from "./state.js";
import { store, settings } from "./store.js";
import { api } from "./api.js";
import { renderThread, ask, stopStreaming, currentSession, sessionMarkdown } from "./chat.js";
import { openLibrary, refreshLibrary } from "./library.js";
import { $, html, icon, LOGO, escapeHtml, fmtInt, plural, toast, openMenu, openModal, confirmDialog, downloadFile } from "./ui.js";

const appEl = $("#app");
const chatList = $("#chatList");
const chatSearch = $("#chatSearch");
const question = $("#question");
const sendBtn = $("#sendBtn");

// ------------------------------------------------------------------ theme
const media = window.matchMedia("(prefers-color-scheme: light)");
function applyTheme() {
  const pref = settings.get().theme;
  const theme = pref === "system" ? (media.matches ? "light" : "dark") : pref;
  document.documentElement.dataset.theme = theme;
}
media.addEventListener("change", applyTheme);
settings.onChange(applyTheme);
applyTheme();

$(".brand").insertAdjacentHTML("afterbegin", LOGO);

// ------------------------------------------------------------------ sidebar
function dateGroup(ts) {
  const d = new Date(ts);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = 86400000;
  if (d >= today) return "Today";
  if (d >= today - day) return "Yesterday";
  if (d >= today - 7 * day) return "Previous 7 days";
  if (d >= today - 30 * day) return "Previous 30 days";
  return "Older";
}

function renderSidebar() {
  const q = chatSearch.value.trim().toLowerCase();
  const sessions = store.all()
    .filter(s => s.messages.length)
    .filter(s => !q || (s.title || "").toLowerCase().includes(q) || s.messages.some(m => m.role === "user" && m.text.toLowerCase().includes(q)))
    .sort((a, b) => b.updatedAt - a.updatedAt);

  chatList.innerHTML = "";
  if (!sessions.length) {
    chatList.appendChild(html(`<div class="chat-empty">${q ? "No chats match." : "Your chats will appear here."}</div>`));
  }
  let group = null;
  for (const s of sessions) {
    const g = dateGroup(s.updatedAt);
    if (g !== group) { group = g; chatList.appendChild(html(`<div class="chat-group">${g}</div>`)); }
    const domain = domainById(s.domain);
    const item = html(`<div class="chat-item ${s.id === state.currentId ? "active" : ""}" role="button" tabindex="0">
        <div class="title"></div><div class="tag"></div>
        <div class="item-actions">
          <button class="icon-btn sm" data-act="rename" title="Rename" aria-label="Rename chat">${icon("edit")}</button>
          <button class="icon-btn sm" data-act="delete" title="Delete" aria-label="Delete chat">${icon("trash")}</button>
        </div></div>`);
    item.querySelector(".title").textContent = s.title || "New chat";
    item.querySelector(".tag").textContent = domain?.display_name || s.domain;
    if (state.streaming?.sessionId === s.id) item.querySelector(".tag").insertAdjacentHTML("afterbegin", `<span class="spinner" style="display:inline-block;width:9px;height:9px;margin-right:6px;vertical-align:-1px"></span>`);
    item.addEventListener("click", e => { if (!e.target.closest(".item-actions")) selectSession(s.id); });
    item.addEventListener("keydown", e => { if (e.key === "Enter" && e.target === item) selectSession(s.id); });
    item.querySelector('[data-act="rename"]').addEventListener("click", () => renameSession(item, s));
    item.querySelector('[data-act="delete"]').addEventListener("click", () => deleteSession(s));
    item.addEventListener("dblclick", () => renameSession(item, s));
    chatList.appendChild(item);
  }
  renderSidebarFooter();
}

function renameSession(item, s) {
  const title = item.querySelector(".title");
  const input = html(`<input class="rename-input" maxlength="80" aria-label="Chat title">`);
  input.value = s.title;
  title.replaceWith(input);
  input.focus(); input.select();
  let done = false;
  const finish = save => {
    if (done) return;
    done = true;
    if (save && input.value.trim()) { s.title = input.value.trim(); store.save(); }
    renderSidebar();
  };
  input.addEventListener("keydown", e => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") { e.stopPropagation(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
  input.addEventListener("click", e => e.stopPropagation());
}

function deleteSession(s) {
  if (state.streaming?.sessionId === s.id) stopStreaming();
  const removed = store.remove(s.id);
  if (state.currentId === s.id) { state.currentId = null; setHash(null); renderAll(); }
  else renderSidebar();
  toast(`Deleted “${removed.title || "chat"}”`, { action: { label: "Undo", onClick: () => { store.restore(removed); renderSidebar(); } } });
}

function renderSidebarFooter() {
  const builds = $("#builds");
  builds.innerHTML = "";
  for (const job of state.jobs) {
    const failed = job.state === "failed";
    const pill = html(`<button class="build-pill ${failed ? "failed" : ""}">${failed ? icon("alertCircle") : `<span class="spinner"></span>`}<div><strong></strong><span></span></div></button>`);
    pill.querySelector("strong").textContent = job.display_name;
    pill.querySelector("span").textContent = failed ? "Build failed - open to retry" : `${job.state}${job.detail ? ` · ${job.detail}` : ""}`;
    pill.addEventListener("click", () => openLibrary({ select: job.domain }));
    builds.appendChild(pill);
  }
  $("#libraryCount").textContent = state.domains.length ? state.domains.length : "";

  const health = $("#health");
  const h = state.health;
  let dot = "bad", text = "Server unreachable";
  if (h) {
    const names = h.providers.map(p => ({ groq: "Groq", gemini: "Gemini" }[p] || p));
    dot = names.length ? "ok" : "bad";
    text = names.length ? `Connected · ${names.join(", ")}` : "No LLM API key configured";
    if (names.length && !h.grobid) { dot = "warn"; text += " · PDF parser offline"; }
  }
  health.innerHTML = `<span class="dot ${dot}"></span><span></span>`;
  health.querySelector("span:last-child").textContent = text;
  health.title = h ? `Version ${h.version}${h.grobid ? "" : " - GROBID isn't running, so new collections can't be built"}` : "The API isn't responding";
}

chatSearch.addEventListener("input", renderSidebar);

// ------------------------------------------------------------------ top bar
function activeDomainId() {
  return currentSession()?.domain || state.selectedDomain;
}

function renderTopbar() {
  const domain = domainById(activeDomainId());
  const btn = $("#collectionBtn");
  btn.querySelector(".cb-name").textContent = domain ? domain.display_name : state.domainsLoaded ? "No collection" : "Loading…";
  btn.querySelector(".cb-meta").textContent = domain ? `${plural(domain.papers, "paper")} · ${fmtInt(domain.chunks)} passages` : (currentSession() ? currentSession().domain : "");
  $("#exportBtn").disabled = !currentSession()?.messages.length;
}

$("#collectionBtn").addEventListener("click", e => {
  const current = activeDomainId();
  const items = [{ heading: "Collections" }];
  for (const d of state.domains) items.push({ label: d.display_name, sub: `${plural(d.papers, "paper")} · ${d.source === "upload" ? "Uploaded PDFs" : "arXiv"}`, value: d.id, selected: d.id === current });
  if (!state.domains.length) items.push({ label: "No collections yet", disabled: true });
  items.push({ separator: true }, { label: "Manage library…", value: "__library", icon: "book" }, { label: "New collection…", value: "__new", icon: "plus" });
  openMenu(e.currentTarget, items, value => {
    if (value === "__library") return openLibrary();
    if (value === "__new") return openLibrary({ create: true });
    switchCollection(value);
  }, { width: 300 });
});

function switchCollection(id) {
  const s = currentSession();
  if (s?.domain === id) return;
  if (s?.messages.length) {
    startChat(id);
    toast(`New chat in ${domainById(id)?.display_name || id}`, { timeout: 2500 });
  } else {
    state.selectedDomain = id;
    renderAll();
  }
}

$("#exportBtn").addEventListener("click", () => {
  const s = currentSession();
  if (!s) return;
  const name = (s.title || "chat").replace(/[^\w\- ]+/g, "").trim().slice(0, 50) || "chat";
  downloadFile(`${name}.md`, sessionMarkdown(s), "text/markdown");
});
$("#newChatTop").addEventListener("click", () => startChat(activeDomainId()));
$("#menuToggle").addEventListener("click", () => appEl.classList.toggle("sidebar-open"));
$("#scrim").addEventListener("click", () => appEl.classList.remove("sidebar-open"));

// ------------------------------------------------------------------ composer
function renderComposer() {
  const s = currentSession();
  const domain = domainById(activeDomainId());
  const usable = !!domain && (!s || domainById(s.domain));
  question.disabled = !usable;
  question.placeholder = !state.domainsLoaded ? "Loading…" : !state.domains.length ? "Build a collection in the Library to start asking" : usable ? `Ask about ${domain.display_name}…` : "This chat's collection was deleted";
  const streaming = !!state.streaming;
  sendBtn.classList.toggle("stop", streaming);
  sendBtn.innerHTML = icon(streaming ? "stop" : "send");
  sendBtn.setAttribute("aria-label", streaming ? "Stop generating" : "Send");
  sendBtn.title = streaming ? "Stop (Esc)" : "Send (Enter)";
  sendBtn.disabled = !streaming && (!usable || !question.value.trim());

  const st = settings.get();
  const model = state.models.find(m => m.id === st.model && m.available) || state.models.find(m => m.id === "auto");
  $("#modelBtn span").textContent = model ? model.label.replace(" (best available)", "") : "Auto";
  $("#sourcesBtn span").textContent = `${st.topK} passages`;
}

function autosize() {
  question.style.height = "auto";
  question.style.height = Math.min(question.scrollHeight, 220) + "px";
}

question.addEventListener("input", () => { autosize(); renderComposer(); });
question.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    submit();
  } else if (e.key === "Escape" && state.streaming) {
    stopStreaming();
  }
});
sendBtn.addEventListener("click", () => (state.streaming ? stopStreaming() : submit()));

function submit() {
  const text = question.value.trim();
  if (!text || state.streaming || question.disabled) return;
  question.value = "";
  autosize();
  ask(text);
}

$("#modelBtn").addEventListener("click", e => {
  const current = settings.get().model;
  const items = [{ heading: "Answer with" }, ...state.models.map(m => ({
    label: m.label, value: m.id, selected: m.id === current, disabled: !m.available,
    sub: !m.available ? "API key not configured" : m.id === "auto" ? "Groq first, Gemini as a fallback" : null,
  }))];
  openMenu(e.currentTarget, items, id => { settings.set({ model: id }); renderComposer(); }, { width: 260 });
});

$("#sourcesBtn").addEventListener("click", e => {
  const current = settings.get().topK;
  const options = [[3, "Focused · fastest"], [5, "Balanced (default)"], [8, "Broader context"], [12, "Widest · slower"]];
  openMenu(e.currentTarget, [{ heading: "Passages to retrieve" }, ...options.map(([n, sub]) => ({ label: `${n} passages`, sub, value: n, selected: n === current }))],
    n => { settings.set({ topK: n }); renderComposer(); }, { width: 230 });
});

// ------------------------------------------------------------------ sessions & routing
function setHash(id) {
  const target = id ? `#/chat/${id}` : "";
  if (location.hash !== target) history.replaceState(null, "", target || location.pathname + location.search);
}

function selectSession(id, { silent = false } = {}) {
  const s = store.get(id);
  state.currentId = s ? id : null;
  if (s) state.selectedDomain = s.domain;
  setHash(state.currentId);
  appEl.classList.remove("sidebar-open");
  renderSidebar();
  renderTopbar();
  renderComposer();
  if (!silent) renderThread();
}

function startChat(domainId) {
  state.currentId = null;
  if (domainId && domainById(domainId)) state.selectedDomain = domainId;
  setHash(null);
  appEl.classList.remove("sidebar-open");
  renderAll();
  question.focus();
}

function renderAll() {
  renderSidebar();
  renderTopbar();
  renderComposer();
  renderThread();
}

Object.assign(hooks, { renderSidebar, renderTopbar, renderComposer, renderThread, refreshCollections, selectSession, startChat });

$("#newChatBtn").addEventListener("click", () => startChat(activeDomainId()));
$("#libraryBtn").addEventListener("click", () => openLibrary());
$("#settingsBtn").addEventListener("click", openSettings);

window.addEventListener("hashchange", () => {
  const id = location.hash.match(/^#\/chat\/(.+)$/)?.[1];
  if (id && id !== state.currentId && store.get(id)) selectSession(id);
});

// ------------------------------------------------------------------ data
let pollTimer = null;
let knownJobs = new Map();

async function refreshCollections() {
  try {
    const [domains, jobs] = await Promise.all([api.domains(), api.jobs()]);
    state.domains = domains;
    state.jobs = jobs;
    state.domainsLoaded = true;
  } catch (e) {
    if (!state.domainsLoaded) toast(`Couldn't load collections: ${e.message}`, { type: "error", timeout: 8000 });
    return;
  }

  // Announce builds that finished or failed since the last poll.
  for (const [id, prev] of knownJobs) {
    const now = state.jobs.find(j => j.domain === id);
    if (!now && domainById(id)) {
      const d = domainById(id);
      toast(`${d.display_name} is ready - ${plural(d.papers, "paper")} indexed.`, { type: "success", timeout: 9000, action: { label: "Start chatting", onClick: () => startChat(id) } });
    } else if (now?.state === "failed" && prev !== "failed") {
      toast(`Building ${now.display_name} failed: ${now.detail}`, { type: "error", timeout: 9000, action: { label: "Details", onClick: () => openLibrary({ select: id }) } });
    }
  }
  knownJobs = new Map(state.jobs.map(j => [j.domain, j.state]));

  if (!domainById(state.selectedDomain)) {
    // Default to the largest collection - usually the most interesting one to explore first.
    state.selectedDomain = [...state.domains].sort((a, b) => b.chunks - a.chunks)[0]?.id || null;
  }
  renderSidebar();
  renderTopbar();
  renderComposer();
  if (!currentSession()?.messages.length) renderThread();
  refreshLibrary();

  clearTimeout(pollTimer);
  if (state.jobs.some(j => j.state !== "failed")) pollTimer = setTimeout(refreshCollections, 3000);
}

async function refreshHealth() {
  try {
    state.health = await api.health();
  } catch {
    state.health = null;
    setTimeout(refreshHealth, 10000);
  }
  renderSidebarFooter();
}

async function loadModels() {
  try { state.models = await api.models(); } catch { state.models = [{ id: "auto", label: "Auto (best available)", available: true }]; }
  renderComposer();
}

// ------------------------------------------------------------------ settings
function openSettings() {
  const st = settings.get();
  const body = html(`<div class="dialog-body">
      <div class="settings-section"><h3>Appearance</h3>
        <div class="setting-row"><div class="sr-text"><div class="sr-title">Theme</div></div>
          <div class="segmented" role="radiogroup" aria-label="Theme">
            <button data-theme="dark">Dark</button><button data-theme="light">Light</button><button data-theme="system">System</button>
          </div></div></div>
      <div class="settings-section"><h3>Answers</h3>
        <div class="setting-row"><div class="sr-text"><div class="sr-title">Model</div><div class="sr-sub">Auto uses Groq first and falls back to Gemini.</div></div>
          <select class="select" data-f="model" style="width:220px;height:36px"></select></div>
        <div class="setting-row"><div class="sr-text"><div class="sr-title">Passages per question</div><div class="sr-sub">More context can help comparisons but answers slower.</div></div>
          <select class="select" data-f="topk" style="width:220px;height:36px">
            <option value="3">3 · focused</option><option value="5">5 · balanced</option><option value="8">8 · broader</option><option value="12">12 · widest</option></select></div></div>
      <div class="settings-section"><h3>Admin access</h3>
        <div class="field" style="margin:0"><label for="set-key">Admin API key</label>
          <input class="input" id="set-key" type="password" autocomplete="off" placeholder="Only needed if the server sets API_KEY">
          <div class="hint">${state.health?.auth_required ? "This server requires a key to create, edit or delete collections." : "This server doesn't require one."} Stored only in this browser.</div></div></div>
      <div class="settings-section"><h3>Your chats</h3>
        <div class="setting-row"><div class="sr-text"><div class="sr-title">${plural(store.all().filter(s => s.messages.length).length, "chat")} saved in this browser</div><div class="sr-sub">Chats never leave your browser unless you export them.</div></div>
          <div style="display:flex;gap:8px"><button class="btn btn-sm" data-act="export">${icon("download")} Export</button><button class="btn btn-sm btn-danger" data-act="clear">${icon("trash")} Delete all</button></div></div></div>
      <div class="settings-section"><h3>Keyboard</h3>
        <div class="shortcut-list">
          <span><kbd>Enter</kbd></span><span>Send · <kbd>Shift</kbd> + <kbd>Enter</kbd> for a new line</span>
          <span><kbd>/</kbd></span><span>Focus the question box</span>
          <span><kbd>Esc</kbd></span><span>Stop an answer, or close a dialog</span>
          <span><kbd>Ctrl</kbd> + <kbd>K</kbd></span><span>Switch collection</span>
        </div></div>
      <div class="settings-section"><h3>About</h3>
        <div style="font-size:13px;color:var(--text-3)">Scientific Claim Verifier ${escapeHtml(state.health?.version || "")} · <a href="/docs" target="_blank" rel="noopener">API docs</a></div></div>
    </div>`);

  const seg = $(".segmented", body);
  const paintSeg = () => seg.querySelectorAll("button").forEach(b => { b.classList.toggle("active", b.dataset.theme === settings.get().theme); b.setAttribute("aria-checked", String(b.dataset.theme === settings.get().theme)); });
  seg.querySelectorAll("button").forEach(b => b.addEventListener("click", () => { settings.set({ theme: b.dataset.theme }); paintSeg(); }));
  paintSeg();

  const modelSel = $('[data-f="model"]', body);
  for (const m of state.models) {
    const o = html(`<option></option>`);
    o.value = m.id; o.textContent = m.label + (m.available ? "" : " (not configured)"); o.disabled = !m.available;
    modelSel.appendChild(o);
  }
  modelSel.value = st.model;
  modelSel.addEventListener("change", () => { settings.set({ model: modelSel.value }); renderComposer(); });
  const topk = $('[data-f="topk"]', body);
  topk.value = String(st.topK);
  if (topk.value !== String(st.topK)) topk.value = "5";
  topk.addEventListener("change", () => { settings.set({ topK: Number(topk.value) }); renderComposer(); });
  const key = $("#set-key", body);
  key.value = st.apiKey;
  key.addEventListener("change", () => settings.set({ apiKey: key.value.trim() }));

  $('[data-act="export"]', body).addEventListener("click", () => {
    downloadFile(`claim-verifier-chats-${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(store.all(), null, 2), "application/json");
  });
  $('[data-act="clear"]', body).addEventListener("click", async () => {
    const ok = await confirmDialog({ title: "Delete all chats?", message: "Every chat saved in this browser will be permanently deleted. Collections on the server are not affected.", confirmLabel: "Delete all chats", danger: true });
    if (!ok) return;
    if (state.streaming) stopStreaming();
    store.clear();
    state.currentId = null;
    setHash(null);
    modal.close();
    renderAll();
    toast("All chats deleted", { type: "success" });
  });
  const modal = openModal({ title: "Settings", content: body });
}

// ------------------------------------------------------------------ shortcuts
document.addEventListener("keydown", e => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if (e.key === "/" && !typing && !document.querySelector(".overlay")) {
    e.preventDefault();
    question.focus();
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && !document.querySelector(".overlay")) {
    e.preventDefault();
    $("#collectionBtn").click();
  } else if (e.key === "Escape" && state.streaming && !document.querySelector(".overlay, .menu")) {
    stopStreaming();
  }
});

// ------------------------------------------------------------------ boot
(async function boot() {
  const fromHash = location.hash.match(/^#\/chat\/(.+)$/)?.[1];
  const latest = [...store.all()].filter(s => s.messages.length).sort((a, b) => b.updatedAt - a.updatedAt)[0];
  const initial = store.get(fromHash) || latest || null;
  state.currentId = initial?.id || null;
  state.selectedDomain = initial?.domain || null;
  setHash(state.currentId);
  renderAll();
  await Promise.all([refreshHealth(), loadModels(), refreshCollections()]);
  renderThread();
  if (window.innerWidth > 900) question.focus();
})();
