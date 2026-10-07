// Shared app state. Modules read and mutate it; app.js owns the render hooks
// (assigned at startup) so feature modules can ask for a re-render without
// importing app.js and creating an import cycle.
export const state = {
  health: null,          // /health response, or null when unreachable
  models: [],            // /models
  domains: [],           // ready collections (/domains)
  jobs: [],              // builds in progress or failed (/jobs)
  domainsLoaded: false,
  selectedDomain: null,  // collection for a new chat
  currentId: null,       // open chat session id
  streaming: null,       // { controller, sessionId, messageId } while answering
};

export const hooks = {
  renderSidebar: () => {},
  renderTopbar: () => {},
  renderComposer: () => {},
  renderThread: () => {},
  refreshCollections: async () => {},
  selectSession: () => {},
  startChat: () => {},
};

export const domainById = id => state.domains.find(d => d.id === id) || null;

export function modelLabel(provider, model) {
  if (!model) return provider || "";
  const known = { "openai/gpt-oss-120b": "GPT-OSS 120B", "openai/gpt-oss-20b": "GPT-OSS 20B" };
  if (known[model]) return known[model];
  if (model.startsWith("gemini")) return model.split("-").map(w => w[0].toUpperCase() + w.slice(1)).join(" ");
  return model.split("/").pop();
}
