// Shared UI primitives: icons, element builder, toasts, modals, menus, formatting.

// 24px stroke icons (several adapted from Lucide, ISC licence).
const ICONS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  book: '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
  sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  send: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  retry: '<path d="M21 12a9 9 0 1 1-2.64-6.36L21 8"/><path d="M21 3v5h-5"/>',
  trash: '<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  sparkles: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.7 1.8 1.8.7-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  checkCircle: '<circle cx="12" cy="12" r="10"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/>',
  alertCircle: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
  xCircle: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  arrowDown: '<path d="M12 5v14M19 12l-7 7-7-7"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  message: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
};

export function icon(name, cls = "icon") {
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

export const LOGO = `<svg class="logo" viewBox="0 0 32 32" aria-hidden="true"><rect class="bg" width="32" height="32" rx="8.5"/><path class="ink" d="M11 8h7.2l4.3 4.3V22.5A1.5 1.5 0 0 1 21 24H11a1.5 1.5 0 0 1-1.5-1.5v-13A1.5 1.5 0 0 1 11 8z" fill="none" stroke-width="1.8" stroke-linejoin="round"/><path class="ink" d="M18 8v4.5h4.5" fill="none" stroke-width="1.8" stroke-linejoin="round"/><path class="ink" d="m12.8 17.6 2.3 2.3 4.2-4.6" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** Build an element from an HTML string (first element). */
export function html(markup) {
  const t = document.createElement("template");
  t.innerHTML = markup.trim();
  return t.content.firstElementChild;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---------------------------------------------------------------- formatting
export const fmtInt = n => (n ?? 0).toLocaleString();
export const plural = (n, word, many = word + "s") => `${fmtInt(n)} ${n === 1 ? word : many}`;
export function fmtDuration(ms) {
  if (ms == null) return "";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
}
export function fmtBytes(b) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}
export function fmtRelative(ts) {
  if (!ts) return "";
  const s = (Date.now() - ts) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric", year: s > 31536000 ? "numeric" : undefined });
}
export function slugify(text) {
  let s = String(text).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40).replace(/_+$/, "");
  if (s.length < 3) s = (s + "_papers").replace(/^_/, "");
  return s;
}
export const DOMAIN_ID = /^[a-z0-9][a-z0-9_-]{1,46}[a-z0-9]$/;

// ---------------------------------------------------------------- toasts
export function toast(message, { type = "info", action = null, timeout = 4500 } = {}) {
  const iconName = { success: "checkCircle", error: "alertCircle", info: "info" }[type] || "info";
  const node = html(`<div class="toast ${type}" role="status">${icon(iconName)}<div class="t-msg"></div></div>`);
  node.querySelector(".t-msg").textContent = message;
  if (action) {
    const btn = html(`<button class="btn btn-sm">${escapeHtml(action.label)}</button>`);
    btn.addEventListener("click", () => { action.onClick(); dismiss(); });
    node.appendChild(btn);
  }
  const close = html(`<button class="icon-btn sm" aria-label="Dismiss">${icon("x")}</button>`);
  close.addEventListener("click", () => dismiss());
  node.appendChild(close);
  document.getElementById("toasts").appendChild(node);
  let timer = timeout ? setTimeout(dismiss, timeout) : null;
  node.addEventListener("mouseenter", () => clearTimeout(timer));
  node.addEventListener("mouseleave", () => { if (timeout) timer = setTimeout(dismiss, 2000); });
  function dismiss() {
    clearTimeout(timer);
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 180);
  }
  return dismiss;
}

// ---------------------------------------------------------------- modal
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
let openModals = [];

/** Open a dialog. `content` is an element; returns { close, root }. */
export function openModal({ title, content, wide = false, footer = null, onClose = null, labelId = "dlg-" + Math.random().toString(36).slice(2) }) {
  const previousFocus = document.activeElement;
  const overlay = html(`<div class="overlay"><div class="dialog ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-labelledby="${labelId}">
      <div class="dialog-head"><h2 id="${labelId}"></h2><button class="icon-btn" aria-label="Close">${icon("x")}</button></div>
    </div></div>`);
  const dialog = overlay.firstElementChild;
  dialog.querySelector("h2").textContent = title;
  dialog.appendChild(content);
  if (footer) dialog.appendChild(footer);

  const close = () => {
    if (!overlay.isConnected) return;
    overlay.remove();
    openModals = openModals.filter(m => m !== entry);
    document.removeEventListener("keydown", onKey, true);
    onClose?.();
    previousFocus?.focus?.();
  };
  const onKey = e => {
    if (openModals[openModals.length - 1] !== entry) return;
    if (e.key === "Escape") { e.preventDefault(); close(); }
    if (e.key === "Tab") {
      const items = $$(FOCUSABLE, dialog).filter(n => n.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  overlay.addEventListener("mousedown", e => { if (e.target === overlay) close(); });
  dialog.querySelector(".dialog-head .icon-btn").addEventListener("click", close);
  document.addEventListener("keydown", onKey, true);
  document.getElementById("modalRoot").appendChild(overlay);
  const entry = { close, root: dialog };
  openModals.push(entry);
  requestAnimationFrame(() => {
    const target = $("[autofocus]", dialog) || $$(FOCUSABLE, dialog.querySelector(".dialog-body") || dialog)[0];
    target?.focus();
  });
  return entry;
}

export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise(resolve => {
    const body = html(`<div class="dialog-body"><p style="margin:0;color:var(--text-2);line-height:1.6"></p></div>`);
    body.querySelector("p").textContent = message;
    const foot = html(`<div class="dialog-foot"><button class="btn btn-ghost" data-act="cancel">Cancel</button><button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-act="ok" autofocus></button></div>`);
    foot.querySelector('[data-act="ok"]').textContent = confirmLabel;
    let result = false;
    const modal = openModal({ title, content: body, footer: foot, onClose: () => resolve(result) });
    foot.querySelector('[data-act="cancel"]').onclick = () => modal.close();
    foot.querySelector('[data-act="ok"]').onclick = () => { result = true; modal.close(); };
  });
}

// ---------------------------------------------------------------- dropdown menu
let activeMenu = null;

/** Show a dropdown under `anchor`. items: [{label, sub, value, selected, disabled}] | {separator} | {heading}. */
export function openMenu(anchor, items, onSelect, { align = "left", width } = {}) {
  closeMenu();
  const menu = html(`<div class="menu" role="menu"></div>`);
  if (width) menu.style.minWidth = width + "px";
  let selectedBtn = null;
  for (const item of items) {
    if (item.separator) { menu.appendChild(html(`<div class="menu-sep"></div>`)); continue; }
    if (item.heading) { const h = html(`<div class="menu-label"></div>`); h.textContent = item.heading; menu.appendChild(h); continue; }
    const btn = html(`<button class="menu-item" role="menuitem"><span class="mi-check">${item.selected ? icon("check") : item.icon ? icon(item.icon) : ""}</span><span class="mi-text"><div class="mi-title"></div></span></button>`);
    btn.querySelector(".mi-title").textContent = item.label;
    if (item.sub) { const sub = html(`<div class="mi-sub"></div>`); sub.textContent = item.sub; btn.querySelector(".mi-text").appendChild(sub); }
    btn.disabled = !!item.disabled;
    if (item.selected) selectedBtn = btn;
    btn.addEventListener("click", () => { closeMenu(); onSelect(item.value); });
    menu.appendChild(btn);
  }
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  let left = align === "right" ? r.right - mw : r.left;
  left = Math.max(12, Math.min(left, window.innerWidth - mw - 12));
  let top = r.bottom + 6;
  if (top + mh > window.innerHeight - 12) top = Math.max(12, r.top - mh - 6);
  menu.style.left = left + "px";
  menu.style.top = top + "px";

  const buttons = $$(".menu-item:not(:disabled)", menu);
  let index = Math.max(0, buttons.indexOf(selectedBtn));
  const focus = i => { index = (i + buttons.length) % buttons.length; buttons[index]?.focus(); };
  focus(index);
  const onKey = e => {
    if (e.key === "Escape") { e.preventDefault(); closeMenu(); anchor.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); focus(index + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); focus(index - 1); }
    else if (e.key === "Tab") closeMenu();
  };
  const onDown = e => { if (!menu.contains(e.target) && !anchor.contains(e.target)) closeMenu(); };
  document.addEventListener("keydown", onKey, true);
  setTimeout(() => document.addEventListener("mousedown", onDown), 0);
  window.addEventListener("resize", closeMenu, { once: true });
  activeMenu = { menu, cleanup: () => { document.removeEventListener("keydown", onKey, true); document.removeEventListener("mousedown", onDown); } };
}

export function closeMenu() {
  if (!activeMenu) return;
  activeMenu.cleanup();
  activeMenu.menu.remove();
  activeMenu = null;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = Object.assign(document.createElement("textarea"), { value: text });
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

export function downloadFile(name, content, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
