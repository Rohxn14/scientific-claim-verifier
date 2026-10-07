// Browser-local persistence: chat sessions and user settings.
// Everything lives in localStorage, so it's per browser and never leaves it.
import { toast } from "./ui.js";

const SESSIONS_KEY = "scv_sessions";   // same key the first UI version used
const SETTINGS_KEY = "scv_settings";

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------- settings
const DEFAULT_SETTINGS = { theme: "dark", model: "auto", topK: 5, apiKey: "" };
let settingsCache = { ...DEFAULT_SETTINGS, ...read(SETTINGS_KEY, {}) };
const listeners = new Set();

export const settings = {
  get: () => settingsCache,
  set(patch) {
    settingsCache = { ...settingsCache, ...patch };
    write(SETTINGS_KEY, settingsCache);
    listeners.forEach(fn => fn(settingsCache));
  },
  onChange: fn => listeners.add(fn),
};

// ---------------------------------------------------------------- sessions
const newId = prefix => `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;

function migrate(session) {
  // Sessions from the first UI had no updatedAt and messages without ids.
  const messages = (session.messages || []).map(m => ({ id: m.id || newId("m"), createdAt: m.createdAt || session.createdAt, ...m }));
  return { title: "", ...session, messages, updatedAt: session.updatedAt || session.createdAt || Date.now() };
}

let sessions = read(SESSIONS_KEY, []).filter(s => s && s.id).map(migrate);

export const store = {
  all: () => sessions,
  get: id => sessions.find(s => s.id === id) || null,

  create(domain) {
    const now = Date.now();
    const session = { id: newId("s"), domain, title: "", messages: [], createdAt: now, updatedAt: now };
    sessions.push(session);
    this.save();
    return session;
  },

  remove(id) {
    const index = sessions.findIndex(s => s.id === id);
    if (index === -1) return null;
    const [removed] = sessions.splice(index, 1);
    this.save();
    return removed;
  },

  restore(session) {
    sessions.push(session);
    this.save();
  },

  clear() {
    sessions = [];
    this.save();
  },

  touch(session) {
    session.updatedAt = Date.now();
    this.save();
  },

  newMessageId: () => newId("m"),

  save() {
    if (write(SESSIONS_KEY, sessions)) return;
    // Over the storage quota: drop passage text from older answers (the
    // bulkiest part) and try again before giving up.
    const recent = new Set([...sessions].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 5).map(s => s.id));
    for (const s of sessions) {
      if (recent.has(s.id)) continue;
      for (const m of s.messages) for (const src of m.sources || []) delete src.text;
    }
    if (!write(SESSIONS_KEY, sessions)) {
      toast("Browser storage is full - older chats may not be saved. Export or delete some chats in Settings.", { type: "error", timeout: 8000 });
    }
  },
};
