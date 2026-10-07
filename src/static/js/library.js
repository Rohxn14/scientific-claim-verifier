// The Library: browse collections, inspect and curate their papers, and build
// new ones from arXiv searches or uploaded PDFs.
import { state, hooks, domainById } from "./state.js";
import { api } from "./api.js";
import { settings } from "./store.js";
import { $, html, icon, escapeHtml, fmtInt, plural, fmtRelative, fmtBytes, toast, slugify, DOMAIN_ID, openModal, confirmDialog } from "./ui.js";

const STAGES = [
  ["queued", "Queued", "Waiting for the previous build to finish"],
  ["fetching", "Find papers", "Searching arXiv or reading uploaded files"],
  ["parsing", "Parse PDFs", "Extracting structured text with GROBID"],
  ["chunking", "Split into passages", "Section-aware chunks of ~220 words"],
  ["indexing", "Embed & index", "Vectors for semantic search"],
  ["linking citations", "Link citations", "Matching each bibliography against the collection"],
];
const ACTIVE = new Set(STAGES.map(s => s[0]));
// Papers scoring below this against the collection's topic get flagged; see
// fetch_papers.RELEVANCE_THRESHOLD (same embedding, same cut).
const OFF_TOPIC_BELOW = 0.6;
const MAX_FILES = 10;
const MAX_BYTES = 20 * 1024 * 1024;

let modal = null;
let view = null;   // { kind: "detail"|"job"|"create", id?, tab?, target? }
let els = null;
let createDraft = null;

export const isLibraryOpen = () => !!modal;

export function openLibrary({ select = null, create = false, uploadTo = null } = {}) {
  if (create) view = { kind: "create", tab: "arxiv" };
  else if (uploadTo) view = { kind: "create", tab: "upload", target: uploadTo };
  else view = viewFor(select || state.selectedDomain || state.domains[0]?.id || state.jobs[0]?.domain);
  if (!view) view = { kind: "create", tab: "arxiv" };

  if (modal) { render(); return; }
  createDraft = null;
  const content = html(`<div class="lib">
      <aside class="lib-side">
        <div class="lib-side-head"><button class="btn btn-primary btn-block" data-act="new">${icon("plus")} New collection</button></div>
        <div class="lib-list" role="listbox" aria-label="Collections"></div>
      </aside>
      <div class="lib-main"></div>
    </div>`);
  els = { list: $(".lib-list", content), main: $(".lib-main", content) };
  $('[data-act="new"]', content).addEventListener("click", () => { view = { kind: "create", tab: "arxiv" }; render(); });
  modal = openModal({ title: "Library", content, wide: true, onClose: () => { modal = null; els = null; } });
  hooks.refreshCollections();
  render();
}

function viewFor(id) {
  if (!id) return null;
  const job = state.jobs.find(j => j.domain === id);
  if (domainById(id)) return { kind: "detail", id };
  if (job) return { kind: "job", id };
  return null;
}

/** Called by app.js whenever collections/jobs were refreshed. */
export function refreshLibrary() {
  if (!modal) return;
  renderList();
  if (view?.kind === "job") {
    if (domainById(view.id) && !state.jobs.some(j => j.domain === view.id)) view = { kind: "detail", id: view.id };
    renderMain();
  } else if (view?.kind === "detail") {
    const job = state.jobs.find(j => j.domain === view.id);
    const box = els.main.querySelector(".append-progress");
    if (job && box) box.replaceWith(appendProgress(job));
    else if (!job && box) renderMain();
  }
}

function render() {
  renderList();
  renderMain();
}

// ------------------------------------------------------------------ side list
function renderList() {
  const list = els.list;
  list.innerHTML = "";
  const ids = new Set();
  const items = [];
  for (const d of state.domains) { ids.add(d.id); items.push({ id: d.id, name: d.display_name, domain: d, job: state.jobs.find(j => j.domain === d.id) }); }
  for (const j of state.jobs) if (!ids.has(j.domain)) items.push({ id: j.domain, name: j.display_name, job: j });
  items.sort((a, b) => a.name.localeCompare(b.name));

  if (!items.length) list.appendChild(html(`<div class="chat-empty">No collections yet.</div>`));
  for (const item of items) {
    const active = view && view.kind !== "create" && view.id === item.id;
    const btn = html(`<button class="lib-item ${active ? "active" : ""}" role="option" aria-selected="${active}">
        <div class="li-name"><span></span></div><div class="li-meta"></div></button>`);
    btn.querySelector(".li-name span").textContent = item.name;
    const meta = btn.querySelector(".li-meta");
    if (item.job && ACTIVE.has(item.job.state)) {
      btn.querySelector(".li-name").insertAdjacentHTML("beforeend", `<span class="spinner"></span>`);
      meta.textContent = `Building · ${stageLabel(item.job.state)}`;
    } else if (item.job?.state === "failed" && !item.domain) {
      btn.querySelector(".li-name").insertAdjacentHTML("beforeend", `<span class="badge bad">Failed</span>`);
      meta.textContent = "Build failed - open to retry";
    } else if (item.domain) {
      meta.textContent = `${plural(item.domain.papers, "paper")} · ${item.domain.source === "upload" ? "Uploaded PDFs" : "arXiv"}`;
    }
    btn.addEventListener("click", () => { view = viewFor(item.id) || view; render(); });
    list.appendChild(btn);
  }
}

const stageLabel = s => (STAGES.find(x => x[0] === s) || [s, s])[1].toLowerCase();

// ------------------------------------------------------------------ main pane
function renderMain() {
  const main = els.main;
  main.scrollTop = 0;
  if (view.kind === "create") return renderCreate(main);
  if (view.kind === "job") return renderJob(main, state.jobs.find(j => j.domain === view.id));
  return renderDetail(main, view.id);
}

async function renderDetail(main, id) {
  main.innerHTML = `<div class="empty-detail"><div><span class="spinner" style="display:inline-block"></span></div></div>`;
  let d;
  try {
    d = await api.domain(id);
  } catch (e) {
    if (view?.id !== id) return;
    main.innerHTML = "";
    main.appendChild(html(`<div class="notice error">${icon("alertCircle")}<div>Couldn't load this collection: ${escapeHtml(e.message)}</div></div>`));
    return;
  }
  if (view?.kind !== "detail" || view.id !== id || !els) return;
  main.innerHTML = "";
  main.appendChild(detailEl(d));
}

function detailEl(d) {
  const node = html(`<div>
      <div class="detail-head">
        <div class="dh-text"><h3 class="editable" title="Click to rename" tabindex="0"></h3><div class="detail-desc editable" title="Click to edit" tabindex="0"></div></div>
        <span class="badge ${d.source === "upload" ? "" : "accent"}">${icon(d.source === "upload" ? "folder" : "globe")}${d.source === "upload" ? "Uploaded PDFs" : "arXiv"}</span>
      </div>
      <div class="hero-stats" style="justify-content:flex-start;margin:0">
        <span class="stat-chip"><b>${fmtInt(d.papers)}</b> papers</span>
        <span class="stat-chip"><b>${fmtInt(d.chunks)}</b> passages</span>
        <span class="stat-chip"><b>${fmtInt(d.citation_links)}</b> citation links</span>
        ${d.updated_at ? `<span class="stat-chip">Updated ${escapeHtml(fmtRelative(d.updated_at * 1000))}</span>` : ""}
      </div>
      <div class="detail-actions">
        <button class="btn btn-primary" data-act="chat">${icon("message")} Start a chat</button>
        <button class="btn" data-act="add">${icon("upload")} Add PDFs</button>
        <button class="btn btn-ghost btn-danger" data-act="delete">${icon("trash")} Delete collection</button>
      </div>
      <div class="append-slot"></div>
      <div class="queries-slot"></div>
      <div class="section-title">Papers <span class="count-badge">${d.papers_list.length}</span></div>
      <div class="papers-toolbar">
        <div class="search-box">${icon("search")}<input type="search" placeholder="Filter papers" aria-label="Filter papers"></div>
        <button class="btn btn-sm btn-ghost" data-act="sort"></button>
      </div>
      <div class="papers"></div>
      <p class="hint" style="font-size:12px;color:var(--text-4);margin-top:10px">Relevance compares each paper's title and abstract with the collection's name and description. A broad search can pull in off-topic papers - removing them keeps answers focused.</p>
    </div>`);

  const title = $("h3", node);
  title.textContent = d.display_name;
  const desc = $(".detail-desc", node);
  desc.textContent = d.description || "Add a description…";
  if (!d.description) desc.style.fontStyle = "italic";
  makeEditable(title, d.display_name, false, value => saveField(d.id, { display_name: value }));
  makeEditable(desc, d.description, true, value => saveField(d.id, { description: value }));

  $('[data-act="chat"]', node).addEventListener("click", () => { modal?.close(); hooks.startChat(d.id); });
  $('[data-act="add"]', node).addEventListener("click", () => { view = { kind: "create", tab: "upload", target: d.id }; render(); });
  $('[data-act="delete"]', node).addEventListener("click", () => deleteCollection(d));

  const job = state.jobs.find(j => j.domain === d.id);
  if (job) $(".append-slot", node).replaceWith(appendProgress(job));

  if (d.queries?.length) {
    const q = html(`<div><div class="section-title">arXiv searches</div><div class="query-chips"></div></div>`);
    for (const text of d.queries) { const c = html(`<span class="query-chip"></span>`); c.textContent = text; $(".query-chips", q).appendChild(c); }
    $(".queries-slot", node).replaceWith(q);
  }

  let sort = d.papers_list.some(p => p.relevance != null) ? "relevance" : "title";
  const sortBtn = $('[data-act="sort"]', node);
  const filter = $('input[type="search"]', node);
  const box = $(".papers", node);
  const paint = () => {
    sortBtn.innerHTML = `${icon("sliders")} Sort: ${sort === "relevance" ? "least relevant first" : sort === "year" ? "newest" : "title"}`;
    const q = filter.value.trim().toLowerCase();
    let papers = d.papers_list.filter(p => !q || `${p.title} ${p.authors}`.toLowerCase().includes(q));
    if (sort === "relevance") papers.sort((a, b) => (a.relevance ?? 1) - (b.relevance ?? 1));
    else if (sort === "year") papers.sort((a, b) => (b.year || "").localeCompare(a.year || ""));
    else papers.sort((a, b) => a.title.localeCompare(b.title));
    box.innerHTML = "";
    if (!papers.length) box.appendChild(html(`<div class="paper"><span class="muted">No papers match.</span></div>`));
    for (const p of papers) box.appendChild(paperRow(d, p));
  };
  sortBtn.addEventListener("click", () => {
    const order = d.papers_list.some(p => p.relevance != null) ? ["relevance", "year", "title"] : ["year", "title"];
    sort = order[(order.indexOf(sort) + 1) % order.length];
    paint();
  });
  filter.addEventListener("input", paint);
  paint();
  return node;
}

function paperRow(d, p) {
  const href = p.url || api.pdfUrl(d.id, p.id);
  const row = html(`<div class="paper">
      <div class="p-main"><a class="p-title" target="_blank" rel="noopener"></a><div class="p-meta"></div></div>
      <div class="p-right"></div></div>`);
  const a = $(".p-title", row);
  a.href = href;
  a.textContent = p.title;
  a.title = p.title;
  const authors = p.authors ? (p.authors.split(", ").length > 3 ? p.authors.split(", ").slice(0, 3).join(", ") + " et al." : p.authors) : "";
  $(".p-meta", row).textContent = [authors, p.year, p.chunks ? plural(p.chunks, "passage") : "not indexed"].filter(Boolean).join(" · ");
  const right = $(".p-right", row);
  if (p.relevance != null && p.relevance < OFF_TOPIC_BELOW) {
    right.insertAdjacentHTML("beforeend", `<span class="badge warn" title="Relevance ${p.relevance.toFixed(2)} - may be off-topic">${icon("alert")}Off-topic?</span>`);
  } else if (p.relevance != null) {
    right.insertAdjacentHTML("beforeend", `<span class="muted mono" style="font-size:11.5px" title="Relevance to the collection topic">${p.relevance.toFixed(2)}</span>`);
  }
  const remove = html(`<button class="icon-btn sm p-remove" title="Remove from collection" aria-label="Remove paper">${icon("trash")}</button>`);
  remove.addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: "Remove this paper?",
      message: `“${p.title}” and its ${plural(p.chunks, "passage")} will be removed from ${d.display_name}. Answers will no longer draw on it.`,
      confirmLabel: "Remove paper", danger: true,
    });
    if (!ok) return;
    try {
      const updated = await api.removePaper(d.id, p.id);
      toast("Paper removed", { type: "success" });
      await hooks.refreshCollections();
      if (view?.kind === "detail" && view.id === d.id && els) { els.main.innerHTML = ""; els.main.appendChild(detailEl(updated)); }
    } catch (e) { adminError(e); }
  });
  right.appendChild(remove);
  return row;
}

function makeEditable(el, value, multiline, onSave) {
  const start = () => {
    const input = html(multiline ? `<textarea class="textarea" rows="2" maxlength="300"></textarea>` : `<input class="input" maxlength="80">`);
    input.value = value || "";
    input.style.marginBottom = "6px";
    el.replaceWith(input);
    input.focus();
    let done = false;
    const finish = async save => {
      if (done) return;
      done = true;
      const next = input.value.trim();
      if (save && next !== (value || "") && (next || multiline)) {
        if (await onSave(next)) return;
      }
      input.replaceWith(el);
    };
    input.addEventListener("keydown", e => {
      if (e.key === "Enter" && !(multiline && e.shiftKey)) { e.preventDefault(); finish(true); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
  };
  el.addEventListener("click", start);
  el.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); start(); } });
}

async function saveField(id, fields) {
  try {
    const updated = await api.updateDomain(id, fields);
    await hooks.refreshCollections();
    if (view?.kind === "detail" && view.id === id && els) { els.main.innerHTML = ""; els.main.appendChild(detailEl(updated)); }
    return true;
  } catch (e) {
    adminError(e);
    return false;
  }
}

async function deleteCollection(d) {
  const ok = await confirmDialog({
    title: `Delete ${d.display_name}?`,
    message: `This permanently deletes the collection's ${plural(d.papers, "paper")}, its search index and downloaded PDFs. Chats that used it stay readable but can't continue.`,
    confirmLabel: "Delete collection", danger: true,
  });
  if (!ok) return;
  try {
    await api.deleteDomain(d.id);
    toast(`${d.display_name} deleted`, { type: "success" });
    await hooks.refreshCollections();
    view = viewFor(state.domains[0]?.id) || { kind: "create", tab: "arxiv" };
    if (modal) render();
  } catch (e) { adminError(e); }
}

// ------------------------------------------------------------------ builds
function stepsEl(job) {
  const failed = job.state === "failed";
  const at = failed ? STAGES.findIndex(s => s[0] === job.failed_stage) : STAGES.findIndex(s => s[0] === job.state);
  const box = html(`<div class="build-steps"></div>`);
  STAGES.forEach(([key, title, detail], i) => {
    let cls = "", dot = "";
    if (job.state === "ready" || i < at) { cls = "done"; dot = icon("check"); }
    else if (i === at && failed) { cls = "failed"; dot = icon("x"); }
    else if (i === at) { cls = "active"; dot = `<span class="spinner"></span>`; }
    const step = html(`<div class="build-step ${cls}"><div class="bs-dot">${dot}</div><div class="bs-text"><div class="bs-title"></div><div class="bs-detail"></div></div></div>`);
    $(".bs-title", step).textContent = title;
    $(".bs-detail", step).textContent = i === at && job.detail && !failed ? job.detail : detail;
    box.appendChild(step);
  });
  return box;
}

function appendProgress(job) {
  const box = html(`<div class="append-progress notice info">${icon("info")}<div style="flex:1"></div></div>`);
  const text = $("div", box);
  if (ACTIVE.has(job.state)) text.textContent = `Adding papers: ${stageLabel(job.state)}${job.detail ? ` - ${job.detail}` : ""}…`;
  else if (job.state === "failed") text.textContent = `The last update failed: ${job.detail}`;
  return box;
}

function renderJob(main, job) {
  main.innerHTML = "";
  if (!job) { main.appendChild(html(`<div class="empty-detail">This build is no longer running.</div>`)); return; }
  const failed = job.state === "failed";
  const node = html(`<div>
      <div class="detail-head"><div class="dh-text"><h3></h3><div class="detail-desc"></div></div>
        <span class="badge ${failed ? "bad" : "accent"}">${failed ? "Failed" : "Building"}</span></div>
      <div class="slot"></div>
      <div class="detail-actions"></div>
    </div>`);
  $("h3", node).textContent = job.display_name;
  $(".detail-desc", node).textContent = failed
    ? "This build stopped before it finished. Every step resumes where it left off, so retrying is cheap."
    : `Started ${fmtRelative(job.started_at * 1000)}. Building takes a few minutes - you can close this window, the build keeps going.`;
  const slot = $(".slot", node);
  if (failed) {
    const err = html(`<div class="notice error">${icon("alertCircle")}<div></div></div>`);
    $("div", err).textContent = job.detail || "Unknown error";
    slot.appendChild(err);
    if (!state.health?.grobid && /grobid/i.test(job.detail || "")) slot.appendChild(grobidNotice());
  }
  slot.appendChild(stepsEl(job));
  const actions = $(".detail-actions", node);
  if (failed) {
    const retry = html(`<button class="btn btn-primary">${icon("retry")} Retry build</button>`);
    retry.addEventListener("click", async () => {
      try { await api.retryDomain(job.domain); await hooks.refreshCollections(); toast("Build restarted"); }
      catch (e) { adminError(e); }
    });
    const del = html(`<button class="btn btn-ghost btn-danger">${icon("trash")} Delete</button>`);
    del.addEventListener("click", () => deleteCollection({ id: job.domain, display_name: job.display_name, papers: 0 }));
    actions.append(retry, del);
  }
  main.appendChild(node);
}

// ------------------------------------------------------------------ create
function grobidNotice() {
  return html(`<div class="notice">${icon("alert")}<div><b>The PDF parser (GROBID) isn't running</b>, so new builds will fail. Start it with <code>docker compose up -d grobid</code> (or <code>docker run -p 8070:8070 grobid/grobid:0.8.2-crf</code>), then try again.</div></div>`);
}

function adminKeyNotice() {
  const box = html(`<div class="notice info">${icon("info")}<div style="flex:1"><div style="margin-bottom:8px">This server requires an admin key to create or change collections.</div>
      <div style="display:flex;gap:8px"><input class="input" type="password" placeholder="Admin API key" style="height:32px"><button class="btn btn-sm">Save key</button></div></div></div>`);
  $("button", box).addEventListener("click", () => {
    settings.set({ apiKey: $("input", box).value.trim() });
    toast("Admin key saved in this browser", { type: "success" });
    box.remove();
  });
  return box;
}

function adminError(e) {
  if (e.status === 401) toast("Admin key missing or wrong - add it in Settings.", { type: "error", timeout: 6000 });
  else toast(e.message, { type: "error", timeout: 6000 });
}

function renderCreate(main) {
  const target = view.target ? domainById(view.target) : null;
  main.innerHTML = "";
  const node = html(`<div>
      <h3>${target ? "Add PDFs" : "New collection"}</h3>
      <p class="detail-desc">${target ? `Upload papers into <b>${escapeHtml(target.display_name)}</b>. They're parsed, indexed and linked to the papers already there.` : "Gather papers to ask questions about - from an arXiv search, or your own PDFs."}</p>
      <div class="notices"></div>
      <div class="tabs" role="tablist" ${target ? "hidden" : ""}>
        <button class="tab" data-tab="arxiv" role="tab">${icon("globe")} Search arXiv</button>
        <button class="tab" data-tab="upload" role="tab">${icon("upload")} Upload PDFs</button>
      </div>
      <div class="tab-body"></div></div>`);
  const notices = $(".notices", node);
  if (state.health && !state.health.grobid) notices.appendChild(grobidNotice());
  if (state.health?.auth_required && !settings.get().apiKey) notices.appendChild(adminKeyNotice());

  for (const tab of node.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.tab === view.tab);
    tab.setAttribute("aria-selected", String(tab.dataset.tab === view.tab));
    tab.addEventListener("click", () => { view = { kind: "create", tab: tab.dataset.tab }; renderMain(); });
  }
  const body = $(".tab-body", node);
  body.appendChild(view.tab === "upload" ? uploadForm(target) : arxivForm());
  main.appendChild(node);
}

function nameAndId(defaultName = "") {
  const box = html(`<div class="row">
      <div class="field"><label>Name</label><input class="input" data-f="name" maxlength="80" placeholder="e.g. Football analytics"></div>
      <div class="field"><label>ID</label><input class="input mono" data-f="slug" maxlength="48" spellcheck="false"><div class="hint">Used in URLs and file names.</div></div>
    </div>`);
  const name = $('[data-f="name"]', box);
  const slug = $('[data-f="slug"]', box);
  let slugEdited = false;
  name.value = defaultName;
  if (defaultName) slug.value = uniqueSlug(slugify(defaultName));
  name.addEventListener("input", () => { if (!slugEdited) slug.value = name.value.trim() ? uniqueSlug(slugify(name.value)) : ""; validate(); });
  slug.addEventListener("input", () => { slugEdited = true; validate(); });
  const hint = $(".hint", box);
  function validate() {
    const v = slug.value.trim();
    const taken = state.domains.some(d => d.id === v) || state.jobs.some(j => j.domain === v && j.state !== "failed");
    const ok = DOMAIN_ID.test(v) && !taken;
    slug.classList.toggle("invalid", !!v && !ok);
    hint.className = v && !ok ? "error-text" : "hint";
    hint.textContent = !v || ok ? "Used in URLs and file names." : taken ? "A collection with this ID already exists." : "3-48 characters: lowercase letters, digits, _ or -";
    return ok;
  }
  return { box, name, slug, validate };
}

function uniqueSlug(base) {
  const taken = new Set([...state.domains.map(d => d.id), ...state.jobs.map(j => j.domain)]);
  let s = base, n = 2;
  while (taken.has(s)) s = `${base}_${n++}`;
  return s;
}

function arxivForm() {
  const draft = createDraft ||= { topic: "", details: "", queries: null, name: "", slug: "", per: 6 };
  const node = html(`<div>
      <div class="field"><label for="nc-topic">What should it cover?</label>
        <input class="input" id="nc-topic" maxlength="200" placeholder="e.g. Mixture-of-experts language models" autofocus></div>
      <div class="field"><label for="nc-details">More detail <span class="muted" style="font-weight:400">(optional)</span></label>
        <textarea class="textarea" id="nc-details" maxlength="500" placeholder="e.g. routing strategies, load balancing, and how they scale"></textarea>
        <div class="hint">Used to suggest searches and to skip off-topic papers. It also becomes the collection's description.</div></div>
      <button class="btn" data-act="suggest">${icon("sparkles")} Suggest arXiv searches</button>
      <div class="step2" hidden>
        <div class="section-title">arXiv searches</div>
        <div class="query-editor"></div>
        <button class="link-btn" data-act="addq" style="margin-top:8px">${icon("plus")} Add a search</button>
        <div style="height:18px"></div>
        <div class="name-slot"></div>
        <div class="field"><label>Papers per search: <b data-f="perv"></b></label>
          <input type="range" class="range" min="1" max="10" data-f="per">
          <div class="hint" data-f="perhint"></div></div>
        <button class="btn btn-primary" data-act="build">${icon("layers")} Build collection</button>
      </div></div>`);
  const topic = $("#nc-topic", node), details = $("#nc-details", node);
  topic.value = draft.topic; details.value = draft.details;
  topic.addEventListener("input", () => { draft.topic = topic.value; });
  details.addEventListener("input", () => { draft.details = details.value; });

  const step2 = $(".step2", node);
  const editor = $(".query-editor", node);
  const per = $('[data-f="per"]', node);
  const nid = nameAndId();
  $(".name-slot", node).replaceWith(nid.box);

  const paintQueries = () => {
    editor.innerHTML = "";
    draft.queries.forEach((q, i) => {
      const row = html(`<div class="query-row"><input class="input" maxlength="200" aria-label="Search ${i + 1}"><button class="icon-btn" aria-label="Remove search">${icon("x")}</button></div>`);
      const input = $("input", row);
      input.value = q;
      input.addEventListener("input", () => { draft.queries[i] = input.value; updatePer(); });
      $("button", row).addEventListener("click", () => { draft.queries.splice(i, 1); paintQueries(); updatePer(); });
      editor.appendChild(row);
    });
  };
  const updatePer = () => {
    draft.per = Number(per.value);
    $('[data-f="perv"]', node).textContent = per.value;
    const n = draft.queries.filter(q => q.trim()).length;
    $('[data-f="perhint"]', node).textContent = `Up to ${n * draft.per} papers from ${plural(n, "search", "searches")}; duplicates and off-topic results are skipped.`;
  };
  const showStep2 = () => {
    step2.hidden = false;
    paintQueries();
    per.value = draft.per;
    nid.name.value = draft.name; nid.slug.value = draft.slug;
    updatePer();
  };
  per.addEventListener("input", updatePer);
  nid.name.addEventListener("input", () => { draft.name = nid.name.value; draft.slug = nid.slug.value; });
  nid.slug.addEventListener("input", () => { draft.slug = nid.slug.value; });
  if (draft.queries) showStep2();

  const suggest = $('[data-act="suggest"]', node);
  suggest.addEventListener("click", async () => {
    if (topic.value.trim().length < 3) { topic.focus(); toast("Describe the topic in a few words first.", { type: "error" }); return; }
    suggest.disabled = true;
    suggest.innerHTML = `<span class="spinner"></span> Thinking of good searches…`;
    try {
      const res = await api.suggestQueries(topic.value.trim(), details.value.trim());
      draft.queries = res.suggested_queries;
      draft.name = res.suggested_display_name;
      draft.slug = uniqueSlug(res.suggested_domain_slug);
      showStep2();
      nid.validate();
      $("input", editor)?.focus();
    } catch (e) { adminError(e); }
    finally { suggest.disabled = false; suggest.innerHTML = `${icon("sparkles")} Suggest again`; }
  });
  $('[data-act="addq"]', node).addEventListener("click", () => {
    if (draft.queries.length >= 8) { toast("Up to 8 searches per collection.", { type: "error" }); return; }
    draft.queries.push("");
    paintQueries();
    editor.lastElementChild.querySelector("input").focus();
  });

  const build = $('[data-act="build"]', node);
  build.addEventListener("click", async () => {
    const queries = draft.queries.map(q => q.trim()).filter(Boolean);
    if (!queries.length) { toast("Add at least one search.", { type: "error" }); return; }
    if (!nid.validate()) { nid.slug.focus(); return; }
    const id = nid.slug.value.trim();
    build.disabled = true;
    try {
      await api.requestDomain({
        domain: id, arxiv_queries: queries, max_per_query: draft.per,
        display_name: nid.name.value.trim(), description: details.value.trim(), topic: topic.value.trim(),
      });
      createDraft = null;
      toast(`Building ${nid.name.value.trim() || id} - this takes a few minutes.`);
      await hooks.refreshCollections();
      view = { kind: "job", id };
      render();
    } catch (e) { adminError(e); build.disabled = false; }
  });
  return node;
}

function uploadForm(target) {
  const files = [];
  const node = html(`<div>
      <div class="ident"></div>
      <div class="field"><label>Description <span class="muted" style="font-weight:400">(optional)</span></label><input class="input" data-f="desc" maxlength="300" placeholder="What these papers are about"></div>
      <div class="dropzone" tabindex="0" role="button" aria-label="Choose PDF files">
        ${icon("upload")}<div><b>Drop PDFs here</b> or click to browse</div>
        <div style="font-size:12px;margin-top:4px">Up to ${MAX_FILES} files, 20 MB each</div>
        <input type="file" accept="application/pdf,.pdf" multiple hidden>
      </div>
      <ul class="file-list"></ul>
      <div style="margin-top:16px"><button class="btn btn-primary" data-act="upload" disabled>${icon("upload")} Upload & build</button></div>
    </div>`);
  let nid = null;
  if (target) {
    $(".ident", node).remove();
    $('[data-f="desc"]', node).closest(".field").remove();
  } else {
    nid = nameAndId();
    $(".ident", node).replaceWith(nid.box);
  }
  const zone = $(".dropzone", node);
  const input = $('input[type="file"]', node);
  const list = $(".file-list", node);
  const submit = $('[data-act="upload"]', node);

  const paint = () => {
    list.innerHTML = "";
    files.forEach((f, i) => {
      const li = html(`<li class="file-item">${icon("file")}<span class="f-name"></span><span class="f-size">${fmtBytes(f.size)}</span><button class="icon-btn sm" aria-label="Remove file">${icon("x")}</button></li>`);
      $(".f-name", li).textContent = f.name;
      $("button", li).addEventListener("click", () => { files.splice(i, 1); paint(); });
      list.appendChild(li);
    });
    submit.disabled = !files.length;
    submit.innerHTML = `${icon("upload")} ${target ? `Add ${plural(files.length, "PDF")}` : files.length ? `Upload ${plural(files.length, "PDF")} & build` : "Upload & build"}`;
  };
  const add = fileList => {
    for (const f of fileList) {
      if (!/\.pdf$/i.test(f.name) && f.type !== "application/pdf") { toast(`${f.name} isn't a PDF`, { type: "error" }); continue; }
      if (f.size > MAX_BYTES) { toast(`${f.name} is over 20 MB`, { type: "error" }); continue; }
      if (files.some(x => x.name === f.name && x.size === f.size)) continue;
      if (files.length >= MAX_FILES) { toast(`Up to ${MAX_FILES} files at a time`, { type: "error" }); break; }
      files.push(f);
    }
    paint();
  };
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { add(input.files); input.value = ""; });
  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", e => { e.preventDefault(); zone.classList.remove("over"); add(e.dataTransfer.files); });

  submit.addEventListener("click", async () => {
    if (nid && !nid.validate()) { nid.slug.focus(); return; }
    const id = target ? target.id : nid.slug.value.trim();
    submit.disabled = true;
    submit.innerHTML = `<span class="spinner"></span> Uploading…`;
    try {
      await api.uploadDomain({ domain: id, files, displayName: nid?.name.value.trim(), description: $('[data-f="desc"]', node)?.value.trim() });
      toast(target ? "Upload received - adding papers to the collection." : "Upload received - building the collection.");
      await hooks.refreshCollections();
      view = target ? { kind: "detail", id } : { kind: "job", id };
      render();
    } catch (e) { adminError(e); paint(); }
  });
  paint();
  return node;
}
