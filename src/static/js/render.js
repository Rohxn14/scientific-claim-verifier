// Answer rendering: Markdown + LaTeX math + inline citation chips.
// Pipeline: protect code -> pull out math -> marked -> DOMPurify -> KaTeX -> chips.
import { escapeHtml } from "./ui.js";

// Mirrors CITATION_BLOCK / parse_source_numbers in src/verify_citations.py -
// both sides must find the same blocks in the same order, so the UI can line
// its chips up with the server's per-citation verification "markers".
const CITATION_BLOCK = /\[\s*Sources?\s*:?\s*\d[^\[\]]*\]|【\s*(?:Sources?\s*:?\s*)?\d[^】]*】|\(\s*(?:see\s+(?:also\s+)?|cf\.?\s+|e\.g\.,?\s+)?Sources?\s*:?\s*\d[^()]*\)/gi;
const DAGGER = /†[^,;】\])]*/g;
const NUMBER_ITEM = /(Sources?\s*:?\s*|\s*(?:[,;&]|\band\b)\s*|\s*)(\d+)(?:\s*[-–]\s*(\d+))?/gi;

export function parseSourceNumbers(block) {
  const text = block.replace(DAGGER, "").slice(1, -1);
  const allowBare = block.trimStart().startsWith("【");
  const numbers = [];
  let lastEnd = null;
  for (const m of text.matchAll(NUMBER_ITEM)) {
    const prefix = m[1];
    const ok = /^sources?/i.test(prefix.trim())
      || (lastEnd !== null && m.index === lastEnd && prefix.trim() !== "")
      || (allowBare && numbers.length === 0 && text.slice(0, m.index).trim() === "");
    if (!ok) continue;
    const lo = Number(m[2]);
    const hi = m[3] ? Number(m[3]) : lo;
    for (let n = lo; n <= Math.min(hi, lo + 20); n++) if (!numbers.includes(n)) numbers.push(n);
    lastEnd = m.index + m[0].length;
  }
  return numbers;
}

let purifyConfigured = false;
function sanitize(markup) {
  if (!window.DOMPurify) return escapeHtml(markup);
  if (!purifyConfigured) {
    window.DOMPurify.addHook("afterSanitizeAttributes", node => {
      if (node.tagName === "A") {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      }
    });
    purifyConfigured = true;
  }
  // No images or inline styles: model output can be steered by text inside a
  // retrieved PDF, and a markdown image is a classic way to leak data out.
  return window.DOMPurify.sanitize(markup, {
    FORBID_TAGS: ["img", "style", "iframe", "form", "input", "button", "svg", "math"],
    FORBID_ATTR: ["style"],
  });
}

function renderMath(tex, display) {
  if (!window.katex) return escapeHtml(display ? `$$${tex}$$` : `$${tex}$`);
  try {
    return window.katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: "ignore", trust: false, output: "htmlAndMathml" });
  } catch {
    return `<code>${escapeHtml(tex)}</code>`;
  }
}

const MATH_TOKEN = i => `KTXMATH${i}KTX`;

/** Markdown (with math) -> sanitized HTML string. */
export function markdownToHtml(source) {
  const code = [];
  const math = [];
  let text = String(source || "")
    .replace(/```[\s\S]*?(?:```|$)|`[^`\n]+`/g, m => { code.push(m); return `\u0000${code.length - 1}\u0000`; })
    .replace(/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g, (m, a, b) => { math.push([a ?? b, true]); return MATH_TOKEN(math.length - 1); })
    .replace(/\\\(([\s\S]+?)\\\)|(?<![\\$\w])\$(?![\s$])([^$\n]+?)(?<![\s\\])\$(?!\d)/g, (m, a, b) => { math.push([a ?? b, false]); return MATH_TOKEN(math.length - 1); })
    .replace(/\u0000(\d+)\u0000/g, (_, i) => code[Number(i)]);

  let out = window.marked ? window.marked.parse(text, { gfm: true, breaks: false }) : `<p>${escapeHtml(text)}</p>`;
  out = sanitize(out);
  return out.replace(/KTXMATH(\d+)KTX/g, (_, i) => renderMath(math[Number(i)][0], math[Number(i)][1]));
}

/**
 * Render an answer into `container`, turning citation blocks into chips
 * (numbered in document order via data-marker). Returns the number of citation
 * blocks found, so callers can check it lines up with the server's markers.
 */
export function renderAnswer(container, source) {
  container.innerHTML = markdownToHtml(source);

  for (const table of container.querySelectorAll("table")) {
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    table.replaceWith(wrap);
    wrap.appendChild(table);
  }

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return node.parentElement.closest("code, pre, .katex, a") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);

  let marker = 0;
  for (const node of nodes) {
    const value = node.nodeValue;
    CITATION_BLOCK.lastIndex = 0;
    if (!CITATION_BLOCK.test(value)) continue;
    CITATION_BLOCK.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of value.matchAll(CITATION_BLOCK)) {
      frag.append(value.slice(last, m.index));
      const numbers = parseSourceNumbers(m[0]);
      const group = document.createElement("span");
      group.className = "cite-group";
      for (const n of numbers) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "cite";
        chip.textContent = n;
        chip.dataset.source = n;
        chip.dataset.marker = marker;
        chip.setAttribute("aria-label", `Source ${n}`);
        group.appendChild(chip);
      }
      if (!numbers.length) group.textContent = m[0];
      frag.append(group);
      marker += 1;
      last = m.index + m[0].length;
    }
    frag.append(value.slice(last));
    node.replaceWith(frag);
  }
  return marker;
}

/** Build a status lookup from the server's verification report. */
export function verificationLookup(verification, renderedBlockCount) {
  if (!verification?.markers || verification.markers.length !== renderedBlockCount) return null;
  return (markerIndex, source) => {
    const marker = verification.markers[markerIndex];
    if (!marker || marker.claim == null) return null;
    const claim = verification.claims[marker.claim];
    return claim?.citations.find(c => c.source === source)?.status || null;
  };
}

/** Plain-text excerpt with the given evidence sentences highlighted. */
export function highlightExcerpt(text, quotes) {
  const ranges = [];
  for (const q of quotes) {
    if (!q) continue;
    const at = text.indexOf(q);
    if (at !== -1) ranges.push([at, at + q.length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const r of ranges) {
    const prev = merged[merged.length - 1];
    if (prev && r[0] <= prev[1]) prev[1] = Math.max(prev[1], r[1]);
    else merged.push([...r]);
  }
  let out = "", pos = 0;
  for (const [s, e] of merged) {
    out += escapeHtml(text.slice(pos, s)) + `<mark>${escapeHtml(text.slice(s, e))}</mark>`;
    pos = e;
  }
  return out + escapeHtml(text.slice(pos));
}
