"""Citation verification: checks every cited claim in a generated answer
against the source passage(s) it cites.

For each (claim, cited source) pair we compute:
  - lexical:  share of the claim's content words and numbers that also appear
              in the passage (or the paper's title/year, which the model saw)
  - semantic: cosine similarity between the claim and the passage's best
              matching sentence (or adjacent pair), returned as the evidence
  - missing_numbers: figures in the claim that never appear in the passage -
              a wrong number is the most damaging kind of unsupported claim
and combine them into one status: supported / partial / unsupported / invalid.

Run directly to answer a question and print the check:
    python verify_citations.py efficient_attention "how does FlashAttention reduce memory usage?"
"""
import json
import re
import sys
import unicodedata

# Calibrated on the efficient_attention eval answers: cited pairs vs. the same
# claims scored against passages they did NOT cite (see README, Evaluation).
# Passages on the same topic routinely reach 0.80 cosine with a claim they
# don't support, but those always share few words with it - so a high
# semantic score only counts as support together with some lexical overlap.
LEXICAL_SUPPORTED = 0.50
SEMANTIC_SUPPORTED = 0.80
LEXICAL_FLOOR = 0.30      # minimum overlap for a semantic "supported"
LEXICAL_PARTIAL = 0.35    # the original keyword-overlap threshold
SEMANTIC_PARTIAL = 0.70
LEXICAL_PARTIAL_FLOOR = 0.20

# One citation "block": [Source 2], [Sources 1, 3], [Source 1; Source 4],
# 【Source 4†L7-L12】 (gpt-oss style - the †L7-L12 line markers are NOT source
# numbers), 【4†L1】, and parenthetical (Source 5) / (see Sources 1 and 2).
# static/js/citations.js mirrors this pattern; keep the two in sync.
CITATION_BLOCK = re.compile(
    r"\[\s*Sources?\s*:?\s*\d[^\[\]]*\]"
    r"|【\s*(?:Sources?\s*:?\s*)?\d[^】]*】"
    r"|\(\s*(?:see\s+(?:also\s+)?|cf\.?\s+|e\.g\.,?\s+)?Sources?\s*:?\s*\d[^()]*\)",
    re.IGNORECASE,
)
_DAGGER = re.compile(r"†[^,;】\]\)]*")
_NUMBER_ITEM = re.compile(r"(Sources?\s*:?\s*|\s*(?:[,;&]|\band\b)\s*|\s*)(\d+)(?:\s*[-–]\s*(\d+))?",
                          re.IGNORECASE)

STOPWORDS = {"the", "a", "an", "is", "are", "of", "to", "in", "and", "for",
             "on", "with", "by", "as", "this", "that", "it", "or", "from",
             "we", "be", "which", "at", "into", "can", "using", "used",
             "source", "sources", "its", "their", "these", "those", "than",
             "has", "have", "was", "were", "also", "such", "both", "while"}

_ABBREVIATIONS = ("e.g.", "i.e.", "i.i.d.", "w.r.t.", "a.k.a.", "et al.", "etc.", "vs.", "cf.",
                  "fig.", "figs.", "eq.", "eqs.", "sec.", "tab.", "no.", "approx.", "resp.", "ref.", "refs.")

# Figures worth checking: anything with a decimal point, anything >= 10, or a
# small number carrying a unit-like suffix (3x, 2×, 5%). Bare single digits
# ("FlashAttention-2", "two phases") are too noisy to flag.
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s*(?:%|×|x\b))?", re.IGNORECASE)


def _normalize_spaces(text: str) -> str:
    """Collapse exotic Unicode whitespace (narrow no-break space, etc.) to a
    plain space. One character in, one character out, so offsets survive."""
    return "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in text)


def parse_source_numbers(block: str) -> list:
    """Source numbers cited by one citation block, in order, de-duplicated.

    A number only counts if it follows the word "Source(s)" or continues a list
    that did ('Sources 1, 3 and 4', 'Sources 2-4'); this keeps line markers
    (†L1-L4) and stray figures ('Source 5, Table 2') out."""
    text = _DAGGER.sub("", _normalize_spaces(block))[1:-1]
    allow_bare = block.lstrip().startswith("【")
    numbers, last_end = [], None
    for m in _NUMBER_ITEM.finditer(text):
        prefix = m.group(1)
        if prefix.strip().lower().startswith("source"):
            ok = True
        elif last_end is not None and m.start() == last_end and prefix.strip():
            ok = True
        elif allow_bare and not numbers and not text[:m.start()].strip():
            ok = True
        else:
            ok = False
        if not ok:
            continue
        lo = int(m.group(2))
        hi = int(m.group(3)) if m.group(3) else lo
        for n in range(lo, min(hi, lo + 20) + 1):
            if n not in numbers:
                numbers.append(n)
        last_end = m.end()
    return numbers


def _only_citations(fragment: str) -> bool:
    return bool(CITATION_BLOCK.search(fragment)) and not re.sub(r"[\s.,;:!?|–—-]", "", CITATION_BLOCK.sub("", fragment))


def _units(text: str):
    """(start, end, whole) spans of lines. Markdown table rows are split into
    cells - unless a cell holds nothing but citations (a "Source" column), in
    which case those citations back the whole row, so the row is one claim.
    Only table rows are split on '|', since LaTeX norms (\\|x\\|) use it too."""
    for line in re.finditer(r"[^\n]+", text):
        s, body = line.start(), line.group()
        if re.match(r"^\s*\|.*\|\s*$", body):
            cells = [(s + c.start(), s + c.end()) for c in re.finditer(r"[^|]+", body)]
            if any(_only_citations(text[a:b]) for a, b in cells):
                yield s, line.end(), True
            else:
                for a, b in cells:
                    yield a, b, False
        else:
            yield s, line.end(), False


def _sentence_spans(text: str) -> list:
    spans = []
    for start, end, whole in _units(text):
        if whole:
            spans.append((start, end))
            continue
        unit, cut = text[start:end], 0
        for m in re.finditer(r"[.!?](?=\s)", unit):
            if unit[:m.end()].lower().endswith(_ABBREVIATIONS):
                continue
            spans.append((start + cut, start + m.end()))
            cut = m.end()
        spans.append((start + cut, end))
    spans = [(s, e) for s, e in spans if text[s:e].strip()]

    # A citation placed after the full stop ("...memory. [Source 1]") is its
    # own fragment here - fold it back into the sentence it belongs to.
    merged = []
    for s, e in spans:
        if merged and _only_citations(text[s:e]):
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def clean_claim(sentence: str) -> str:
    """Claim text without citation markers or markdown/LaTeX syntax."""
    s = CITATION_BLOCK.sub("", sentence)
    s = re.sub(r"^\s*(?:#+|>+|[-*•+]|\d+[.)])\s+", "", s)
    s = s.replace("**", "").replace("__", "").replace("`", "")
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*]+?)\*(?![\w*])", r"\1", s)
    s = re.sub(r"\\[()\[\]]", "", s)
    s = re.sub(r"^\s*\|", "", s)
    s = re.sub(r"(?<!\\)\s*\|\s*(?=\S)", "; ", s)   # table cell separators, not LaTeX \|norms\|
    s = re.sub(r"(;\s*)+", "; ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    return s.strip(" |,;:–—-")


def _has_words(fragment: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9]", clean_claim(fragment)))


def extract_claims(answer_text: str) -> tuple:
    """Returns (claims, markers, uncited).

    claims:  [{"text", "sources"}] - one per sentence carrying citations
    markers: [{"claim", "sources"}] - every citation block in document order,
             so the UI can colour each inline citation by its claim's status
    uncited: number of substantial sentences that carry no citation at all
    """
    text = _normalize_spaces(answer_text or "")
    claims, uncited, claim_spans = [], 0, []
    for s, e in _sentence_spans(text):
        sentence = text[s:e]
        blocks = list(CITATION_BLOCK.finditer(sentence))
        if not blocks:
            stripped = sentence.strip()
            # Prose words only - lines of LaTeX are not uncited claims.
            prose = re.findall(r"(?<![\\\w])[A-Za-z]{2,}", stripped)
            if len(prose) >= 8 and not stripped.startswith(("#", "|")) and not stripped.endswith(":"):
                uncited += 1
            continue
        # "X [Source 1]; Y [Source 3]." - each citation backs the words since
        # the previous one, so remember those segments per source. Adjacent
        # citations ("[Source 1][Source 2]") share a segment.
        groups, prev_end = [], 0
        for b in blocks:
            segment = sentence[prev_end:b.start()]
            numbers = parse_source_numbers(b.group())
            if _has_words(segment) or not groups:
                groups.append([segment, list(numbers)])
            else:
                groups[-1][1].extend(numbers)
            prev_end = b.end()
        if _has_words(sentence[prev_end:]):
            groups[-1][0] += sentence[prev_end:]   # trailing words go with the last citation

        sources, segments = [], {}
        for segment, numbers in groups:
            for n in numbers:
                if n not in sources:
                    sources.append(n)
                if len(groups) > 1 and _has_words(segment):
                    segments[n] = "; ".join(filter(None, [segments.get(n), clean_claim(segment)]))
        clean = clean_claim(sentence)
        if not sources or len(clean) < 3:
            continue
        claim_spans.append((s, e))
        claims.append({"text": clean, "sources": sources, "segments": segments})

    markers = []
    for m in CITATION_BLOCK.finditer(text):
        claim_idx = next((i for i, (s, e) in enumerate(claim_spans) if s <= m.start() < e), None)
        markers.append({"claim": claim_idx, "sources": parse_source_numbers(m.group())})
    return claims, markers, uncited


def extract_claim_sentences(answer_text: str) -> list:
    """Backwards-compatible view used by the eval scripts."""
    claims, _, _ = extract_claims(answer_text)
    return [{"sentence": c["text"], "cited_sources": sorted(c["sources"])} for c in claims]


def _stem(word: str) -> str:
    """Light suffix stripping so 'reduces'/'reduced'/'reduce' match, without
    mangling short words ('speed', 'need') or Latin endings ('analysis')."""
    if len(word) > 4 and not word.endswith(("ss", "us", "is")):
        for suffix, replacement in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", "e"), ("s", ""), ("ly", "")):
            stem = word[: -len(suffix)] + replacement
            if word.endswith(suffix) and len(stem) >= 4:
                word = stem
                break
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    return word


def _tokens(text: str) -> set:
    text = text.lower()
    words = {_stem(w) for w in re.findall(r"[a-z]+", text)
             if w not in STOPWORDS and len(w) > 2}
    numbers = set(re.findall(r"\d+\.?\d*", text))
    return words | numbers


def keyword_overlap(claim: str, chunk_text: str) -> float:
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return 1.0
    return len(claim_tokens & _tokens(chunk_text)) / len(claim_tokens)


def _numbers(text: str, significant_only: bool) -> set:
    found = set()
    for m in _NUMBER.finditer(text):
        whole, frac, unit = m.group(1).replace(",", ""), m.group(2) or "", m.group(3)
        frac = frac.rstrip("0").rstrip(".")
        if significant_only and not (frac or unit or int(whole) >= 10):
            continue
        found.add(whole + frac)
    return found


def missing_numbers(claim: str, haystack: str) -> list:
    present = _numbers(haystack, significant_only=False)
    return sorted(_numbers(claim, significant_only=True) - present, key=lambda n: float(n))


def _passage_sentences(hit: dict) -> list:
    try:
        sentences = json.loads(hit["meta"].get("sentences_json") or "[]")
    except (TypeError, ValueError):
        sentences = []
    if not sentences:
        sentences = re.split(r"(?<=[.!?])\s+", hit["text"])
    sentences = [s.strip() for s in sentences if s.strip()]
    substantial = [s for s in sentences if len(s.split()) >= 4]
    return substantial or sentences or [hit["text"]]


def _windows(sentences: list) -> list:
    """Single sentences plus adjacent pairs - claims often merge two."""
    return sentences + [f"{a} {b}" for a, b in zip(sentences, sentences[1:])]


def _status(lexical: float, semantic: float, missing: list) -> str:
    if lexical >= LEXICAL_SUPPORTED or (semantic >= SEMANTIC_SUPPORTED and lexical >= LEXICAL_FLOOR):
        status = "supported"
    elif (lexical >= LEXICAL_PARTIAL or semantic >= SEMANTIC_SUPPORTED
          or (semantic >= SEMANTIC_PARTIAL and lexical >= LEXICAL_PARTIAL_FLOOR)):
        status = "partial"
    else:
        status = "unsupported"
    if missing and status == "supported":
        status = "partial"
    return status


_RANK = {"supported": 3, "partial": 2, "unsupported": 1, "invalid": 0}

# "The provided excerpts do not contain...", "None of the sources mention..." -
# an answer that (correctly) declines. Only its opening is checked, so a
# later "...does not report X" inside a real answer doesn't count.
_DECLINE = re.compile(
    r"\b(?:sources?|excerpts?|passages?|papers?|context|documents?)\b[^.]{0,40}?"
    r"\b(?:do|does|did)(?:\s+not|n['’]t)\b[^.]{0,30}?"
    r"\b(?:contain|provide|include|mention|report|cover|address|discuss|give|state|answer)"
    r"|\bnone of the (?:sources|excerpts|passages|papers)\b"
    r"|\bno (?:information|data|details?)\b[^.]{0,60}?\b(?:sources?|excerpts?|passages?|papers?)\b",
    re.IGNORECASE,
)


def declines(answer_text: str) -> bool:
    """Does the answer open by saying the sources don't cover the question?"""
    return bool(_DECLINE.search(_normalize_spaces(answer_text or "")[:400]))


def verify_answer(answer_text: str, hits: list, use_semantic: bool = True) -> dict:
    """Check every cited claim in `answer_text` against `hits` (the retrieved
    passages, where hits[0] is [Source 1]). See module docstring."""
    claims, markers, uncited = extract_claims(answer_text)

    # Texts to score: each claim, plus - for sentences with several citations -
    # the segment each citation actually backs.
    texts, text_index = [], {}
    for c in claims:
        for t in [c["text"], *c["segments"].values()]:
            if t not in text_index:
                text_index[t] = len(texts)
                texts.append(t)

    best = {}  # (text index, source number) -> (similarity, evidence window)
    if use_semantic and texts:
        from embeddings import embed_documents
        cited = sorted({n for c in claims for n in c["sources"] if 1 <= n <= len(hits)})
        windows = {n: _windows(_passage_sentences(hits[n - 1])) for n in cited}
        flat = [w for n in cited for w in windows[n]]
        text_vecs = embed_documents(texts)
        window_vecs = embed_documents(flat) if flat else []
        offset = 0
        for n in cited:
            block = window_vecs[offset: offset + len(windows[n])]
            offset += len(windows[n])
            sims = text_vecs @ block.T  # (n_texts, n_windows); vectors are normalized
            for ti in range(len(texts)):
                j = int(sims[ti].argmax())
                best[(ti, n)] = (float(sims[ti][j]), windows[n][j])

    for claim in claims:
        segments = claim.pop("segments")
        results = []
        for n in claim["sources"]:
            if not 1 <= n <= len(hits):
                results.append({"source": n, "status": "invalid", "lexical": 0.0,
                                "semantic": None, "evidence": None, "missing_numbers": []})
                continue
            hit = hits[n - 1]
            meta = hit["meta"]
            haystack = f"{hit['text']} {meta.get('paper_title', '')} {str(meta.get('published', ''))[:4]}"
            # Figures are checked against the words this citation backs, so a
            # number from another part of the sentence isn't blamed on it.
            missing = missing_numbers(segments.get(n, claim["text"]), haystack)
            candidates = []
            for t in dict.fromkeys(filter(None, [claim["text"], segments.get(n)])):
                lexical = keyword_overlap(t, haystack)
                semantic, evidence = best.get((text_index[t], n), (None, None))
                status = _status(lexical, semantic or 0.0, missing)
                candidates.append((_RANK[status], semantic or 0.0, lexical, status, semantic, evidence))
            _, _, lexical, status, semantic, evidence = max(candidates, key=lambda c: c[:3])
            results.append({
                "source": n,
                "status": status,
                "lexical": round(lexical, 3),
                "semantic": round(semantic, 3) if semantic is not None else None,
                "evidence": evidence,
                "missing_numbers": missing,
            })
        claim["citations"] = results
        claim["status"] = max((r["status"] for r in results), key=_RANK.get)

    def count(items, status):
        return sum(1 for s in items if s == status)

    citation_statuses = [r["status"] for c in claims for r in c["citations"]]
    claim_statuses = [c["status"] for c in claims]
    n = len(claims)
    score = ((count(claim_statuses, "supported") + 0.5 * count(claim_statuses, "partial")) / n) if n else None
    if score is None:
        verdict = "no_citations"
    elif score >= 0.85 and "invalid" not in citation_statuses:
        verdict = "well_supported"
    elif score >= 0.6:
        verdict = "mostly_supported"
    else:
        verdict = "weakly_supported"
    if verdict == "well_supported" and uncited > n:
        verdict = "mostly_supported"   # most of the answer can't be checked at all
    declined = declines(answer_text)
    if declined:
        # Statements of absence ("none of the sources report X") can't be
        # confirmed by comparing them with a passage, so grading a correct
        # refusal as weakly supported would mislead. Its citations are still checked.
        verdict = "not_covered"

    return {
        "claims": claims,
        "markers": markers,
        "summary": {
            "claims": n,
            "claims_supported": count(claim_statuses, "supported"),
            "claims_partial": count(claim_statuses, "partial"),
            "claims_unsupported": count(claim_statuses, "unsupported") + count(claim_statuses, "invalid"),
            "citations": len(citation_statuses),
            "supported": count(citation_statuses, "supported"),
            "partial": count(citation_statuses, "partial"),
            "unsupported": count(citation_statuses, "unsupported"),
            "invalid": count(citation_statuses, "invalid"),
            "uncited_sentences": uncited,
            "score": round(score, 3) if score is not None else None,
            "declined": declined,
            "verdict": verdict,
        },
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    from generate_answer import generate_answer

    domain = sys.argv[1] if len(sys.argv) > 1 else "efficient_attention"
    query = " ".join(sys.argv[2:]) or "how does FlashAttention reduce memory usage?"
    answer_text, hits, provider = generate_answer(domain, query)
    report = verify_answer(answer_text, hits)

    print(f"\n{'=' * 70}\nCITATION CHECK (answered by: {provider})\n{'=' * 70}")
    for c in report["claims"]:
        for r in c["citations"]:
            sem = f"{r['semantic']:.2f}" if r["semantic"] is not None else "-"
            print(f"\n[{r['status'].upper()}] Source {r['source']}  (semantic {sem}, overlap {r['lexical']:.2f})")
            print(f"  Claim:    {c['text'][:110]}")
            if r["evidence"]:
                print(f"  Evidence: {r['evidence'][:110]}")
            if r["missing_numbers"]:
                print(f"  Numbers not in source: {', '.join(r['missing_numbers'])}")
    s = report["summary"]
    print(f"\n{s['claims']} claims: {s['claims_supported']} supported, {s['claims_partial']} partial, "
          f"{s['claims_unsupported']} unsupported - verdict: {s['verdict']}")
