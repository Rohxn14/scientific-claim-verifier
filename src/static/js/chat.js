// The conversation view: messages, streaming answers, sources and the citation check.
import { state, hooks, domainById, modelLabel } from "./state.js";
import { store, settings } from "./store.js";
import { api, ApiError } from "./api.js";
import { renderAnswer, verificationLookup, highlightExcerpt, markdownToHtml } from "./render.js";
import { html, icon, LOGO, escapeHtml, fmtInt, fmtDuration, plural, toast, copyText } from "./ui.js";

const thread = document.getElementById("thread");
const inner = document.getElementById("threadInner");
const jumpBtn = document.getElementById("jumpLatest");

export const currentSession = () => store.get(state.currentId);

const STATUS_TEXT = {
  supported: "Supported",
  partial: "Partly supported",
  unsupported: "Not supported",
  invalid: "Cites a missing source",
};
const STATUS_ICON = { supported: "checkCircle", partial: "alertCircle", unsupported: "xCircle", invalid: "xCircle" };
const VERDICT_TEXT = {
  well_supported: "Well supported",
  mostly_supported: "Mostly supported",
  weakly_supported: "Weakly supported",
  no_citations: "No cited claims",
  not_covered: "Not covered by sources",
};
const GENERIC_EXAMPLES = [
  "What are the main contributions of these papers?",
  "Which methods are compared, and how do they differ?",
  "What datasets or benchmarks are used for evaluation?",
  "What limitations do the authors acknowledge?",
];

// ------------------------------------------------------------------ scrolling
const nearBottom = () => thread.scrollHeight - thread.scrollTop - thread.clientHeight < 140;
function scrollToBottom(smooth = false) {
  thread.scrollTo({ top: thread.scrollHeight, behavior: smooth ? "smooth" : "auto" });
}
thread.addEventListener("scroll", () => {
  const session = currentSession();
  jumpBtn.hidden = nearBottom() || !session?.messages.length;
});
jumpBtn.addEventListener("click", () => scrollToBottom(true));

// ------------------------------------------------------------------ thread
export function renderThread({ keepScroll = false } = {}) {
  const session = currentSession();
  const scrollTop = thread.scrollTop;
  inner.innerHTML = "";
  jumpBtn.hidden = true;

  if (state.domainsLoaded && !state.domains.length && !session?.messages.length) {
    inner.appendChild(noCollectionsEl());
    return;
  }
  if (!session || !session.messages.length) {
    const domain = domainById(session?.domain || state.selectedDomain);
    if (domain) inner.appendChild(heroEl(domain));
    return;
  }
  if (state.domainsLoaded && !domainById(session.domain)) {
    inner.appendChild(html(`<div class="notice">${icon("alert")}<div>The collection this chat used (<b>${escapeHtml(session.domain)}</b>) no longer exists, so you can read the chat but not ask new questions in it.</div></div>`));
  }
  session.messages.forEach((m, i) => inner.appendChild(messageEl(session, m, i === session.messages.length - 1)));
  if (keepScroll) thread.scrollTop = scrollTop;
  else scrollToBottom();
}

function noCollectionsEl() {
  const node = html(`<div class="hero">${LOGO}
    <h1>Build your first collection</h1>
    <p class="hero-desc">A collection is a set of papers to ask questions about. Pull papers from arXiv by topic, or upload your own PDFs.</p>
    <button class="btn btn-primary">${icon("plus")} New collection</button></div>`);
  node.querySelector("button").addEventListener("click", () => import("./library.js").then(m => m.openLibrary({ create: true })));
  return node;
}

function heroEl(domain) {
  const examples = domain.example_questions?.length ? domain.example_questions.slice(0, 4) : GENERIC_EXAMPLES;
  const node = html(`<div class="hero">${LOGO}
    <h1></h1>
    <p class="hero-desc"></p>
    <div class="hero-stats">
      <span class="stat-chip"><b>${fmtInt(domain.papers)}</b> papers</span>
      <span class="stat-chip"><b>${fmtInt(domain.chunks)}</b> passages</span>
      ${domain.citation_links ? `<span class="stat-chip"><b>${fmtInt(domain.citation_links)}</b> citation links</span>` : ""}
    </div>
    <div class="examples-label">Try asking</div>
    <div class="examples"></div>
    <div class="how">
      <div class="how-step"><div class="n">1</div><b>Retrieve</b><span>Finds the most relevant passages, plus passages from papers linked by citations.</span></div>
      <div class="how-step"><div class="n">2</div><b>Answer</b><span>The model answers only from those passages and cites one for every fact.</span></div>
      <div class="how-step"><div class="n">3</div><b>Verify</b><span>Each cited claim is checked against its passage, and numbers are matched exactly.</span></div>
    </div></div>`);
  node.querySelector("h1").textContent = `Ask ${domain.display_name}`;
  const desc = node.querySelector(".hero-desc");
  if (domain.description) desc.textContent = domain.description; else desc.remove();
  const box = node.querySelector(".examples");
  for (const q of examples) {
    const btn = html(`<button class="example">${icon("sparkles")}<span></span></button>`);
    btn.querySelector("span").textContent = q;
    btn.addEventListener("click", () => ask(q));
    box.appendChild(btn);
  }
  return node;
}

function messageEl(session, m, isLast) {
  if (m.role === "user") {
    const node = html(`<div class="msg msg-user" data-id="${m.id}"><div class="bubble"></div></div>`);
    node.querySelector(".bubble").textContent = m.text;
    return node;
  }
  const node = html(`<article class="msg msg-assistant" data-id="${m.id}">
      <div class="msg-head">${LOGO}<span class="who">Answer</span><span class="meta"></span><div class="msg-actions"></div></div>
      <div class="stepper" hidden></div>
      <div class="answer"></div>
      <div class="extra"></div>
    </article>`);
  paintAssistant(node, session, m, isLast);
  return node;
}

function paintAssistant(node, session, m, isLast) {
  const meta = node.querySelector(".meta");
  const parts = [];
  if (m.model || m.provider) parts.push(escapeHtml(modelLabel(m.provider, m.model)));
  if (m.timings?.total_ms) parts.push(escapeHtml(fmtDuration(m.timings.total_ms)));
  meta.innerHTML = parts.map((p, i) => (i ? `<span class="sep"></span>` : "") + `<span>${p}</span>`).join("");

  const answer = node.querySelector(".answer");
  const extra = node.querySelector(".extra");
  if (!m.pending) extra.innerHTML = "";

  const actions = node.querySelector(".msg-actions");
  actions.innerHTML = "";

  if (m.pending) {
    node.setAttribute("aria-busy", "true");
    paintStepper(node, session, m);
    answer.classList.toggle("streaming", !!m.text);
    if (m.text) paintAnswerText(answer, m); else answer.innerHTML = "";
    if (m.sources?.length && !extra.querySelector(".sources-card")) extra.appendChild(sourcesCard(m));
    return;
  }

  node.removeAttribute("aria-busy");
  node.querySelector(".stepper").hidden = true;
  answer.classList.remove("streaming");

  const errorText = m.error === true ? m.text : m.error;
  if (errorText) {
    answer.innerHTML = "";
    if (m.error !== true && m.text) paintAnswerText(answer, m);
    const box = html(`<div class="msg-error">${icon("alertCircle")}<div class="err-body"><div></div></div></div>`);
    box.querySelector(".err-body div").textContent = errorText.replace(/^Error:\s*/, "");
    if (isLast && domainById(session.domain)) {
      const retry = html(`<button class="btn btn-sm" style="margin-top:10px">${icon("retry")} Try again</button>`);
      retry.addEventListener("click", () => retryLast(session));
      box.querySelector(".err-body").appendChild(retry);
    }
    extra.appendChild(box);
    return;
  }

  paintAnswerText(answer, m);
  if (m.stopped) extra.appendChild(html(`<div class="stopped-note">Stopped before the answer finished - its citations weren't checked.</div>`));
  if (m.verification) extra.appendChild(verifyCard(m));
  if (m.sources?.length) extra.appendChild(sourcesCard(m));

  const copy = html(`<button class="icon-btn sm" title="Copy answer" aria-label="Copy answer">${icon("copy")}</button>`);
  copy.addEventListener("click", async () => {
    if (await copyText(answerMarkdown(m))) toast("Answer copied with its sources", { type: "success", timeout: 2200 });
  });
  actions.appendChild(copy);
  if (isLast && domainById(session.domain)) {
    const retry = html(`<button class="icon-btn sm" title="Regenerate" aria-label="Regenerate answer">${icon("retry")}</button>`);
    retry.addEventListener("click", () => retryLast(session));
    actions.appendChild(retry);
  }
}

function paintAnswerText(el, m) {
  const blocks = renderAnswer(el, m.text || "");
  const lookup = verificationLookup(m.verification, blocks);
  if (!lookup) return;
  for (const chip of el.querySelectorAll(".cite")) {
    const status = lookup(Number(chip.dataset.marker), Number(chip.dataset.source));
    if (status) {
      chip.dataset.status = status;
      chip.setAttribute("aria-label", `Source ${chip.dataset.source}: ${STATUS_TEXT[status]}`);
    }
  }
}

function paintStepper(node, session, m) {
  const stepper = node.querySelector(".stepper");
  stepper.hidden = false;
  const steps = [];
  const isFollowUp = session.messages.indexOf(m) > 1;
  if (isFollowUp) steps.push(["rewriting", "Read the conversation"]);
  const nSources = m.sources?.length;
  steps.push(["retrieving", nSources ? `Found ${plural(nSources, "passage")}` : `Search ${domainById(session.domain) ? fmtInt(domainById(session.domain).chunks) + " passages" : "papers"}`]);
  steps.push(["generating", m.model ? `Write with ${modelLabel(m.provider, m.model)}` : "Write answer"]);
  steps.push(["verifying", "Check citations"]);
  const order = steps.map(s => s[0]);
  const current = Math.max(0, order.indexOf(m.stage));
  stepper.innerHTML = steps.map(([, label], i) => {
    const cls = i < current ? "done" : i === current ? "active" : "";
    const mark = i < current ? icon("check") : i === current ? `<span class="spinner"></span>` : `<span class="pending-dot"></span>`;
    return `${i ? `<span class="step-sep"></span>` : ""}<span class="step ${cls}">${mark}<span>${escapeHtml(label)}</span></span>`;
  }).join("");
}

// ------------------------------------------------------------------ verification card
function verifyCard(m) {
  const v = m.verification;
  const s = v.summary;
  const card = html(`<section class="card verify-card">
      <div class="card-head"><div class="card-title">${icon("shield")} Citation check</div>
        <div class="right"><span class="verdict ${s.verdict}">${VERDICT_TEXT[s.verdict] || s.verdict}</span></div></div>
      <div class="verify-body"></div></section>`);
  const body = card.querySelector(".verify-body");

  if (!s.claims) {
    body.innerHTML = s.declined
      ? `<div class="verify-stats">The answer says this collection doesn't cover the question, and cites nothing - so there was nothing to check.</div>`
      : `<div class="verify-stats">This answer doesn't cite any passage, so there was nothing to check${s.uncited_sentences ? ` - treat its ${plural(s.uncited_sentences, "uncited sentence")} with care` : ""}.</div>`;
    return card;
  }
  if (s.declined) {
    body.insertAdjacentHTML("beforeend", `<div class="verify-stats" style="margin:0 0 10px">The answer says this collection doesn't cover the question; the check below covers only the related details it cites.</div>`);
  }

  const unsupported = s.claims_unsupported;
  body.insertAdjacentHTML("beforeend", `
    <div class="verify-bar" role="img" aria-label="${s.claims_supported} supported, ${s.claims_partial} partly supported, ${unsupported} not supported">
      ${s.claims_supported ? `<span class="ok" style="flex:${s.claims_supported}"></span>` : ""}
      ${s.claims_partial ? `<span class="warn" style="flex:${s.claims_partial}"></span>` : ""}
      ${unsupported ? `<span class="bad" style="flex:${unsupported}"></span>` : ""}
    </div>
    <div class="verify-stats">
      <span class="k"><i style="background:var(--ok)"></i><b>${s.claims_supported}</b> supported</span>
      <span class="k"><i style="background:var(--warn)"></i><b>${s.claims_partial}</b> partly</span>
      <span class="k"><i style="background:var(--bad)"></i><b>${unsupported}</b> not supported</span>
      <span>of ${plural(s.claims, "cited claim")}${s.uncited_sentences ? ` · ${plural(s.uncited_sentences, "sentence")} without a citation` : ""}</span>
    </div>`);

  const toggle = html(`<button class="link-btn verify-toggle" aria-expanded="false">${icon("chevronDown")}<span>Show claim-by-claim check</span></button>`);
  const list = html(`<ol class="claims" hidden></ol>`);
  toggle.addEventListener("click", () => {
    const open = list.hidden;
    if (open && !list.childElementCount) fillClaims(list, m);
    list.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.querySelector("span").textContent = open ? "Hide claim-by-claim check" : "Show claim-by-claim check";
    toggle.querySelector("svg").style.transform = open ? "rotate(180deg)" : "";
  });
  body.append(toggle, list);
  return card;
}

function fillClaims(list, m) {
  const order = { unsupported: 0, invalid: 0, partial: 1, supported: 2 };
  const claims = [...m.verification.claims].sort((a, b) => order[a.status] - order[b.status]);
  for (const claim of claims) {
    const li = html(`<li class="claim st-${claim.status}">${icon(STATUS_ICON[claim.status])}<div class="claim-body"><div class="claim-text"></div></div></li>`);
    li.querySelector(".claim-text").innerHTML = markdownToHtml(claim.text);   // sanitized; renders any math
    for (const c of claim.citations) {
      const scores = c.semantic != null ? `match ${c.semantic.toFixed(2)} · overlap ${c.lexical.toFixed(2)}` : `overlap ${c.lexical.toFixed(2)}`;
      const row = html(`<div class="claim-cite"><div class="claim-cite-head">
          <button class="cite" data-source="${c.source}" data-status="${c.status}" type="button">${c.source}</button>
          <span class="status ${c.status}">${STATUS_TEXT[c.status]}</span>
          ${c.status !== "invalid" ? `<span class="scores">${scores}</span>` : ""}
        </div></div>`);
      if (c.evidence) {
        const q = html(`<p class="evidence"></p>`);
        q.textContent = c.evidence;
        row.appendChild(q);
      }
      if (c.status === "invalid") {
        row.appendChild(html(`<div class="num-warning">${icon("alert")}<span>Only ${plural(m.sources.length, "source was", "sources were")} retrieved - there is no Source ${c.source}.</span></div>`));
      }
      if (c.missing_numbers?.length) {
        const w = html(`<div class="num-warning">${icon("alert")}<span></span></div>`);
        w.querySelector("span").textContent = `${c.missing_numbers.join(", ")} ${c.missing_numbers.length === 1 ? "doesn't" : "don't"} appear in this passage`;
        row.appendChild(w);
      }
      li.querySelector(".claim-body").appendChild(row);
    }
    list.appendChild(li);
  }
  list.appendChild(html(`<li class="legend">Checked automatically: each claim is compared with the passage it cites (meaning and wording), and every figure must appear in that passage. Use it to decide what to double-check, not as proof.</li>`));
}

// ------------------------------------------------------------------ sources card
function evidenceFor(m, n) {
  return (m.verification?.claims || []).flatMap(c => c.citations.filter(x => x.source === n && x.evidence).map(x => x.evidence));
}

function sourcesCard(m) {
  const papers = new Set(m.sources.map(s => s.paper_id || s.paper_title)).size;
  const card = html(`<section class="card sources-card">
      <div class="card-head"><div class="card-title">${icon("layers")} Sources <span class="count-badge">${m.sources.length}</span></div>
      <div class="right muted" style="font-size:12.5px">from ${plural(papers, "paper")}</div></div>
      <ol class="source-list"></ol></section>`);
  const list = card.querySelector(".source-list");
  m.sources.forEach((s, i) => {
    const li = sourceItem(m, s, s.index || i + 1);
    li.hidden = i >= VISIBLE_SOURCES;
    list.appendChild(li);
  });
  if (m.sources.length > VISIBLE_SOURCES) {
    const more = html(`<div style="padding:0 16px 12px"><button class="link-btn">${icon("chevronDown")}<span>Show ${m.sources.length - VISIBLE_SOURCES} more</span></button></div>`);
    more.querySelector("button").addEventListener("click", () => showAllSources(card));
    card.appendChild(more);
  }
  return card;
}

const VISIBLE_SOURCES = 3;

function showAllSources(card) {
  card.querySelectorAll(".source[hidden]").forEach(li => { li.hidden = false; });
  card.querySelector(".link-btn")?.parentElement.remove();
}

function sourceItem(m, s, n) {
  const sim = Number(s.similarity) || 0;
  const width = Math.round(Math.max(0.06, Math.min(1, (sim - 0.5) / 0.42)) * 100);
  const li = html(`<li class="source" data-source="${n}">
      <button class="source-head" aria-expanded="false">
        <span class="source-num">${n}</span>
        <span class="source-main"><span class="source-title"></span><span class="source-meta"></span></span>
        <span class="sim" title="Similarity to the question"><span class="simbar"><i style="width:${width}%"></i></span>${sim.toFixed(2)}</span>
        ${icon("chevronDown", "icon chev")}
      </button>
      <div class="source-body" hidden></div></li>`);
  li.querySelector(".source-title").textContent = s.paper_title;
  li.querySelector(".source-title").title = s.paper_title;
  const meta = li.querySelector(".source-meta");
  meta.textContent = [s.year, s.section].filter(Boolean).join(" · ");
  if (s.via === "citation") {
    meta.insertAdjacentHTML("beforeend", ` <span class="badge violet" title="Added because it's linked by a citation to another retrieved paper">${icon("link")}citation link</span>`);
  }

  const head = li.querySelector(".source-head");
  const body = li.querySelector(".source-body");
  if (!s.text) {
    head.disabled = true;
    head.querySelector(".chev").style.visibility = "hidden";
  }
  head.addEventListener("click", () => toggleSource(li, m, s, n));
  return li;
}

function toggleSource(li, m, s, n, forceOpen = false) {
  const head = li.querySelector(".source-head");
  const body = li.querySelector(".source-body");
  if (!s.text) return;
  const open = forceOpen || body.hidden;
  if (open && !body.childElementCount) {
    const excerpt = html(`<p class="excerpt"></p>`);
    excerpt.innerHTML = highlightExcerpt(s.text, evidenceFor(m, n));
    body.appendChild(excerpt);
    const links = html(`<div class="source-links"></div>`);
    if (s.url) links.insertAdjacentHTML("beforeend", `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${icon("external")}arXiv page</a>`);
    const session = currentSession();
    if (s.paper_id && session) links.insertAdjacentHTML("beforeend", `<a href="${escapeHtml(api.pdfUrl(session.domain, s.paper_id))}" target="_blank" rel="noopener">${icon("file")}PDF</a>`);
    body.appendChild(links);
  }
  body.hidden = !open;
  head.setAttribute("aria-expanded", String(open));
}

// ------------------------------------------------------------------ citation chips
const popover = document.getElementById("popover");
let popTimer = null;
let popHide = null;

function messageFor(node) {
  const id = node.closest(".msg")?.dataset.id;
  return currentSession()?.messages.find(m => m.id === id) || null;
}

function showPopover(chip) {
  const m = messageFor(chip);
  if (!m) return;
  const n = Number(chip.dataset.source);
  const src = m.sources?.[n - 1];
  const status = chip.dataset.status;
  let evidence = null;
  if (m.verification && chip.dataset.marker !== undefined) {
    const marker = m.verification.markers?.[Number(chip.dataset.marker)];
    const claim = marker?.claim != null ? m.verification.claims[marker.claim] : null;
    evidence = claim?.citations.find(c => c.source === n)?.evidence || null;
  }
  if (!src) {
    popover.innerHTML = `<div class="pv-title">Source ${n} doesn't exist</div><div class="pv-meta">The answer cites a source number that wasn't among the ${m.sources?.length || 0} retrieved passages.</div>`;
  } else {
    const quote = evidence || (src.text ? src.text.slice(0, 240) + (src.text.length > 240 ? "…" : "") : "");
    popover.innerHTML = `<div class="pv-title"></div><div class="pv-meta"></div>
      ${status ? `<div class="pv-status ${status}">${icon(STATUS_ICON[status])}${STATUS_TEXT[status]}</div>` : ""}
      ${quote ? `<div class="pv-quote"></div>` : ""}
      <div class="pv-hint">${evidence ? "Best-matching sentence in the passage." : ""} Click to open the source.</div>`;
    popover.querySelector(".pv-title").textContent = `[${n}] ${src.paper_title}`;
    popover.querySelector(".pv-meta").textContent = [src.year, src.section].filter(Boolean).join(" · ");
    if (quote) popover.querySelector(".pv-quote").textContent = quote;
  }
  popover.hidden = false;
  const r = chip.getBoundingClientRect();
  const pw = popover.offsetWidth, ph = popover.offsetHeight;
  const left = Math.max(12, Math.min(r.left + r.width / 2 - pw / 2, window.innerWidth - pw - 12));
  let top = r.bottom + 8;
  if (top + ph > window.innerHeight - 12) top = r.top - ph - 8;
  popover.style.left = `${left}px`;
  popover.style.top = `${Math.max(12, top)}px`;
}

function hidePopover() {
  clearTimeout(popTimer);
  popover.hidden = true;
}

if (window.matchMedia("(hover: hover)").matches) {
  thread.addEventListener("mouseover", e => {
    const chip = e.target.closest(".answer .cite");
    if (!chip) return;
    clearTimeout(popHide);
    clearTimeout(popTimer);
    popTimer = setTimeout(() => showPopover(chip), 140);
  });
  thread.addEventListener("mouseout", e => {
    if (!e.target.closest(".answer .cite")) return;
    clearTimeout(popTimer);
    popHide = setTimeout(hidePopover, 120);
  });
  popover.addEventListener("mouseenter", () => clearTimeout(popHide));
  popover.addEventListener("mouseleave", hidePopover);
}
thread.addEventListener("scroll", hidePopover, { passive: true });

thread.addEventListener("click", e => {
  const chip = e.target.closest(".cite");
  if (!chip) return;
  hidePopover();
  const msgNode = chip.closest(".msg");
  const m = messageFor(chip);
  const n = Number(chip.dataset.source);
  const li = msgNode?.querySelector(`.source[data-source="${n}"]`);
  if (!m || !li) {
    toast(`Source ${n} isn't one of the retrieved passages.`, { type: "error", timeout: 3000 });
    return;
  }
  if (li.hidden) showAllSources(li.closest(".sources-card"));
  toggleSource(li, m, m.sources[n - 1] || {}, n, true);
  li.scrollIntoView({ behavior: "smooth", block: "center" });
  li.classList.remove("flash");
  void li.offsetWidth;
  li.classList.add("flash");
  setTimeout(() => li.classList.remove("flash"), 1600);
});

// ------------------------------------------------------------------ asking
function liveNode(id) {
  return inner.querySelector(`.msg[data-id="${id}"]`);
}

// While an answer streams, keep its last line in view - unless the reader has
// scrolled up, in which case leave them alone.
function followTarget(node) {
  const answer = node.querySelector(".answer");
  return answer.textContent.trim() ? answer : node.querySelector(".stepper");
}
function isFollowing(node) {
  return followTarget(node).getBoundingClientRect().bottom <= thread.getBoundingClientRect().bottom + 60;
}
function follow(node) {
  const over = followTarget(node).getBoundingClientRect().bottom - (thread.getBoundingClientRect().bottom - 28);
  if (over > 0) thread.scrollTop += over;
}

export async function ask(question) {
  question = question.trim();
  if (!question || state.streaming) return;
  let session = currentSession();
  if (session && !domainById(session.domain)) {
    toast("This chat's collection no longer exists - start a new chat.", { type: "error" });
    return;
  }
  const domainId = session?.domain || state.selectedDomain;
  if (!domainById(domainId)) {
    toast("Pick a collection first.", { type: "error" });
    return;
  }
  if (!session) {
    session = store.create(domainId);
    state.currentId = session.id;
    hooks.selectSession(session.id, { silent: true });
  }

  const history = session.messages
    .filter(m => m.text && !m.error && !m.pending)
    .slice(-6)
    .map(m => ({ role: m.role, text: m.text.slice(0, 8000) }));

  session.messages.push({ id: store.newMessageId(), role: "user", text: question, createdAt: Date.now() });
  if (!session.title) session.title = question.length > 70 ? question.slice(0, 67) + "…" : question;
  const msg = { id: store.newMessageId(), role: "assistant", text: "", sources: [], createdAt: Date.now(), pending: true, stage: history.length ? "rewriting" : "retrieving" };
  session.messages.push(msg);
  store.touch(session);
  hooks.renderSidebar();
  renderThread();
  scrollToBottom(true);

  const controller = new AbortController();
  state.streaming = { controller, sessionId: session.id, messageId: msg.id };
  hooks.renderComposer();

  let frame = null;
  let lastPaint = 0;
  const repaint = () => {
    if (frame) return;
    frame = requestAnimationFrame(now => {
      frame = null;
      if (now - lastPaint < 45) { repaint(); return; }
      lastPaint = now;
      const node = liveNode(msg.id);
      if (!node) return;
      const following = isFollowing(node);
      paintAssistant(node, session, msg, true);
      if (following) follow(node);
    });
  };

  const s = settings.get();
  const model = state.models.find(x => x.id === s.model && x.available) ? s.model : "auto";
  try {
    await api.streamQuery(
      { domain: session.domain, question, top_k: s.topK, model, history },
      {
        signal: controller.signal,
        onEvent(type, data) {
          if (type === "status") {
            msg.stage = data.stage;
            if (data.provider) { msg.provider = data.provider; msg.model = data.model; }
          } else if (type === "sources") {
            msg.sources = data.sources;
            msg.searchQuery = data.search_query;
          } else if (type === "token") {
            msg.text += data.text;
          } else if (type === "done") {
            Object.assign(msg, { text: data.answer, provider: data.provider, model: data.model, verification: data.verification, timings: data.timings, searchQuery: data.search_query });
          } else if (type === "error") {
            throw new ApiError(502, data.detail);
          }
          repaint();
        },
      },
    );
    if (!msg.verification) msg.error = "The connection closed before the answer finished.";
  } catch (e) {
    if (e.name === "AbortError") msg.stopped = true;
    else msg.error = e.message || String(e);
  } finally {
    if (frame) cancelAnimationFrame(frame);
    delete msg.pending;
    delete msg.stage;
    state.streaming = null;
    store.touch(session);
    const node = liveNode(msg.id);
    if (node) {
      const following = isFollowing(node);
      const fresh = messageEl(session, msg, true);
      node.replaceWith(fresh);
      if (following) (fresh.querySelector(".verify-card, .msg-error") || fresh).scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    hooks.renderComposer();
    hooks.renderSidebar();
  }
}

export function stopStreaming() {
  state.streaming?.controller.abort();
}

function retryLast(session) {
  if (state.streaming) return;
  const lastUser = [...session.messages].reverse().find(m => m.role === "user");
  if (!lastUser) return;
  const at = session.messages.lastIndexOf(lastUser);
  session.messages.splice(at);
  store.touch(session);
  ask(lastUser.text);
}

// ------------------------------------------------------------------ export
function sourcesMarkdown(m) {
  return (m.sources || []).map((s, i) => {
    const link = s.url ? ` - ${s.url}` : "";
    return `[${s.index || i + 1}] ${s.paper_title}${s.year ? ` (${s.year})` : ""} - ${s.section}${link}`;
  }).join("\n");
}

function answerMarkdown(m) {
  let out = m.text || "";
  if (m.sources?.length) out += `\n\nSources:\n${sourcesMarkdown(m)}`;
  const v = m.verification?.summary;
  if (v?.claims) out += `\n\nCitation check: ${v.claims_supported}/${v.claims} claims supported, ${v.claims_partial} partly, ${v.claims_unsupported} not supported.`;
  return out;
}

export function sessionMarkdown(session) {
  const domain = domainById(session.domain);
  const lines = [`# ${session.title || "Chat"}`, "", `Collection: ${domain?.display_name || session.domain}`, `Exported: ${new Date().toLocaleString()}`, ""];
  for (const m of session.messages) {
    if (m.role === "user") lines.push(`## Q: ${m.text}`, "");
    else if (m.error) lines.push(`_Error: ${m.error === true ? m.text : m.error}_`, "");
    else lines.push(answerMarkdown(m), "", "---", "");
  }
  return lines.join("\n");
}
