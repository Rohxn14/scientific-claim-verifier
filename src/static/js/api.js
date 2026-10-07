// HTTP client for the FastAPI backend, including the server-sent-events stream.
import { settings } from "./store.js";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function errorMessage(data, res) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: ["body", "field"], msg}]
    return detail.map(e => `${(e.loc || []).slice(1).join(".") || "request"}: ${e.msg}`).join("; ");
  }
  if (res.status === 0 || !res.status) return "Can't reach the server";
  return `${res.status} ${res.statusText || "Request failed"}`;
}

function headers({ json = true, admin = false } = {}) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  const key = settings.get().apiKey;
  if (admin && key) h["X-API-Key"] = key;
  return h;
}

async function request(path, { method = "GET", body, admin = false, form = null, signal } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: headers({ json: !form && body !== undefined, admin }),
      body: form || (body !== undefined ? JSON.stringify(body) : undefined),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach the server - is it running?");
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, res));
  return data;
}

const enc = encodeURIComponent;

export const api = {
  health: () => request("/health"),
  models: () => request("/models"),
  domains: () => request("/domains"),
  jobs: () => request("/jobs"),
  domain: id => request(`/domains/${enc(id)}`),
  updateDomain: (id, fields) => request(`/domains/${enc(id)}`, { method: "PATCH", body: fields, admin: true }),
  deleteDomain: id => request(`/domains/${enc(id)}`, { method: "DELETE", admin: true }),
  retryDomain: id => request(`/domains/${enc(id)}/retry`, { method: "POST", admin: true }),
  removePaper: (id, paperId) => request(`/domains/${enc(id)}/papers/${enc(paperId)}`, { method: "DELETE", admin: true }),
  pdfUrl: (id, paperId) => `/domains/${enc(id)}/papers/${enc(paperId)}/pdf`,
  suggestQueries: (topic, details) => request("/domains/suggest-queries", { method: "POST", body: { topic, details }, admin: true }),
  requestDomain: payload => request("/domains/request", { method: "POST", body: payload, admin: true }),
  uploadDomain: ({ domain, files, displayName, description }) => {
    const form = new FormData();
    form.append("domain", domain);
    if (displayName) form.append("display_name", displayName);
    if (description) form.append("description", description);
    for (const f of files) form.append("files", f, f.name);
    return request("/domains/upload", { method: "POST", form, admin: true });
  },

  /** POST /query/stream and call onEvent(type, data) for each SSE event. */
  async streamQuery(body, { signal, onEvent }) {
    let res;
    try {
      res = await fetch("/query/stream", { method: "POST", headers: headers(), body: JSON.stringify(body), signal });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new ApiError(0, "Can't reach the server - is it running?");
    }
    if (!res.ok) {
      let data = null;
      try { data = await res.json(); } catch { /* not JSON */ }
      throw new ApiError(res.status, errorMessage(data, res));
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        let type = "message";
        const data = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) type = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        }
        if (data.length) onEvent(type, JSON.parse(data.join("\n")));
      }
    }
  },
};
