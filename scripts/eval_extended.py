"""Offline evaluation of the parts eval_harness.py does not measure. No LLM
calls: it works from saved answers, the built index and the parsed corpus.

    python scripts/eval_extended.py sheet     # blind annotation sheet for the verifier audit
    python scripts/eval_extended.py all       # every metric -> eval/extended/offline_metrics.json
    python scripts/eval_extended.py perturbation   # one metric, merged into that file

Metrics (see the project report for why each one was chosen):
  reproduce     re-verify the saved harness answers; the headline numbers
  verifier_vs_gold   verifier statuses against independent labels
                     (eval/extended/gold_labels.csv)
  discrimination     cited vs. retrieved-but-not-cited passages: does each
                     signal separate them (ROC-AUC)?
  perturbation       inject known errors into supported claims: how many
                     does the verifier catch, per error type?
  retrieval          expected-paper rank (MRR) on the harness set, and
                     title-as-query known-item retrieval over two collections
  relevance_filter   does the embedding relevance score separate on-topic
                     papers from off-topic ones?
  corpus             what ingestion produced
  verification_latency  time to verify one answer
"""
import csv
import json
import os
import random
import re
import statistics
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8")

from paths import PARSED_DIR, PROJECT_ROOT  # noqa: E402

EVAL_DIR = os.path.join(PROJECT_ROOT, "eval")
OUT_DIR = os.path.join(EVAL_DIR, "extended")
DOMAIN = "efficient_attention"
STATUSES = ["supported", "partial", "unsupported"]


# ------------------------------------------------------------------ helpers
def saved_answers():
    """(row, answer_text, hits) for every question in eval/results.csv - the
    run the README and the resume figures come from."""
    from eval_harness import raw_answer_path
    from search_with_graph import search_with_graph
    with open(os.path.join(EVAL_DIR, "results.csv"), newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for row in rows:
        with open(raw_answer_path(row["query"]), encoding="utf-8") as f:
            text = f.read()
        answer_text = text.split("\n\n", 1)[1] if text.startswith("Provider:") else text
        out.append((row, answer_text, search_with_graph(DOMAIN, row["query"], k=5)))
    return out


def pairs_from(saved):
    """One record per (claim, cited source) pair, with what the verifier said."""
    from verify_citations import extract_claims, verify_answer
    records = []
    for qi, (row, text, hits) in enumerate(saved):
        raw_claims, _, _ = extract_claims(text)
        report = verify_answer(text, hits)
        for ci, (raw, claim) in enumerate(zip(raw_claims, report["claims"])):
            for cit in claim["citations"]:
                n = cit["source"]
                hit = hits[n - 1] if 1 <= n <= len(hits) else None
                records.append({
                    "pair_id": f"q{qi:02d}c{ci:02d}s{n}",
                    "query": row["query"], "category": row["category"],
                    "claim": claim["text"], "backs": raw["segments"].get(n, ""),
                    "source": n, "paper": hit["meta"]["paper_title"] if hit else "",
                    "passage": hit["text"] if hit else "",
                    "verifier": cit["status"], "semantic": cit["semantic"], "lexical": cit["lexical"],
                    "missing_numbers": " ".join(cit["missing_numbers"]),
                    "claim_status": claim["status"], "claim_index": f"q{qi:02d}c{ci:02d}",
                })
    return records


def roc_auc(pos, neg):
    """Probability that a random positive scores above a random negative."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def cohen_kappa(a, b, labels):
    n = len(a)
    if not n:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


def pct(x, n):
    return round(x / n, 3) if n else None


# ------------------------------------------------------------------ annotation sheet
def write_sheet():
    """Blind sheet: claim + passage, no verifier output, shuffled, so the
    labels are not anchored on what the verifier said."""
    records = pairs_from(saved_answers())
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "verifier_pairs.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    blind = [{k: r[k] for k in ("pair_id", "claim", "backs", "source", "paper", "passage")} for r in records]
    random.Random(7).shuffle(blind)
    with open(os.path.join(OUT_DIR, "annotation_sheet.json"), "w", encoding="utf-8") as f:
        json.dump(blind, f, ensure_ascii=False, indent=1)
    print(f"{len(records)} pairs written")


# ------------------------------------------------------------------ metrics
def reproduce(saved):
    from verify_citations import verify_answer
    claims = Counter()
    per_cat = {}
    for row, text, hits in saved:
        s = verify_answer(text, hits)["summary"]
        for k in ("claims", "claims_supported", "claims_partial", "claims_unsupported",
                  "citations", "supported", "partial", "unsupported", "invalid", "uncited_sentences"):
            claims[k] += s[k]
        c = per_cat.setdefault(row["category"], Counter())
        c.update({"questions": 1, "claims": s["claims"], "supported": s["claims_supported"],
                  "partial": s["claims_partial"], "unsupported": s["claims_unsupported"]})
    n = claims["claims"]
    sentences = n + claims["uncited_sentences"]
    return {
        "questions": len(saved), **claims,
        "supported_pct": pct(claims["claims_supported"], n),
        "partial_pct": pct(claims["claims_partial"], n),
        "unsupported_pct": pct(claims["claims_unsupported"], n),
        "supported_or_partial_pct": pct(claims["claims_supported"] + claims["claims_partial"], n),
        "citation_coverage": pct(n, sentences),
        "invalid_citation_rate": pct(claims["invalid"], claims["citations"]),
        "per_category": per_cat,
    }


def verifier_vs_gold(records):
    path = os.path.join(OUT_DIR, "gold_labels.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as f:
        gold = {r["pair_id"]: r["label"] for r in csv.DictReader(f)}
    rows = [r for r in records if r["pair_id"] in gold and r["verifier"] != "invalid"]
    v = [r["verifier"] for r in rows]
    g = [gold[r["pair_id"]] for r in rows]
    confusion = {gl: {vl: sum(1 for a, b in zip(g, v) if a == gl and b == vl) for vl in STATUSES} for gl in STATUSES}

    def binary(labels):
        return ["supported" if x == "supported" else "not" for x in labels]

    vb, gb = binary(v), binary(g)
    tp = sum(1 for a, b in zip(gb, vb) if a == b == "supported")
    pred_sup, gold_sup = vb.count("supported"), gb.count("supported")
    flagged = sum(1 for a, b in zip(g, v) if a == "unsupported" and b != "supported")
    gold_unsup = g.count("unsupported")
    not_sup_caught = sum(1 for a, b in zip(gb, vb) if a == b == "not")

    # Claim level: a claim's status is its best citation's, as in the verifier.
    rank = {"supported": 2, "partial": 1, "unsupported": 0}
    claim_gold, claim_ver = {}, {}
    for r in rows:
        k = r["claim_index"]
        claim_gold[k] = max(claim_gold.get(k, "unsupported"), gold[r["pair_id"]], key=rank.get)
        claim_ver[k] = r["claim_status"]
    cg, cv = list(claim_gold.values()), [claim_ver[k] for k in claim_gold]
    return {
        "pairs": len(rows),
        "confusion_gold_rows_verifier_cols": confusion,
        "accuracy_3class": pct(sum(a == b for a, b in zip(g, v)), len(rows)),
        "kappa_3class": cohen_kappa(g, v, STATUSES),
        "accuracy_binary": pct(sum(a == b for a, b in zip(gb, vb)), len(rows)),
        "kappa_binary": cohen_kappa(gb, vb, ["supported", "not"]),
        "precision_supported": pct(tp, pred_sup),
        "recall_supported": pct(tp, gold_sup),
        "recall_not_supported": pct(not_sup_caught, len(gb) - gold_sup),
        "gold_unsupported_flagged": f"{flagged}/{gold_unsup}",
        "gold_distribution": {s: g.count(s) for s in STATUSES},
        "verifier_distribution": {s: v.count(s) for s in STATUSES},
        "claims": len(cg),
        "claim_gold_distribution": {s: cg.count(s) for s in STATUSES},
        "claim_verifier_distribution": {s: cv.count(s) for s in STATUSES},
        "claim_accuracy_3class": pct(sum(a == b for a, b in zip(cg, cv)), len(cg)),
        "claim_kappa_3class": cohen_kappa(cg, cv, STATUSES),
        "disagreements": [{"pair_id": r["pair_id"], "gold": gold[r["pair_id"]], "verifier": r["verifier"],
                           "semantic": r["semantic"], "lexical": r["lexical"], "claim": r["claim"][:160]}
                          for r in rows if gold[r["pair_id"]] != r["verifier"]],
    }


def discrimination(saved):
    """Each claim against every retrieved passage: the ones it cites
    (positives) and the ones it doesn't (negatives). The README's calibration
    method, re-run as a measurement."""
    from verify_citations import (_RANK, _passage_sentences, _status, _windows, extract_claims,
                                  keyword_overlap, missing_numbers)
    from embeddings import embed_documents
    pos, neg = [], []
    for row, text, hits in saved:
        claims, _, _ = extract_claims(text)
        if not claims or not hits:
            continue
        windows = [_windows(_passage_sentences(h)) for h in hits]
        claim_vecs = embed_documents([c["text"] for c in claims])
        for i, h in enumerate(hits, 1):
            wv = embed_documents(windows[i - 1])
            sims = claim_vecs @ wv.T
            hay = f"{h['text']} {h['meta'].get('paper_title', '')} {str(h['meta'].get('published', ''))[:4]}"
            for ci, c in enumerate(claims):
                sem = float(sims[ci].max())
                lex = keyword_overlap(c["text"], hay)
                st = _status(lex, sem, missing_numbers(c["text"], hay))
                (pos if i in c["sources"] else neg).append((sem, lex, _RANK[st], st))
    out = {"positive_pairs": len(pos), "negative_pairs": len(neg)}
    for name, idx in (("semantic", 0), ("lexical", 1), ("status_rank", 2)):
        out[f"auc_{name}"] = roc_auc([p[idx] for p in pos], [n[idx] for n in neg])
    out["cited_status"] = dict(Counter(p[3] for p in pos))
    out["not_cited_status"] = dict(Counter(n[3] for n in neg))
    out["not_cited_rated_supported"] = pct(sum(1 for n in neg if n[3] == "supported"), len(neg))
    out["not_cited_semantic_ge_0.80"] = pct(sum(1 for n in neg if n[0] >= 0.80), len(neg))
    out["median_semantic"] = {"cited": round(statistics.median(p[0] for p in pos), 3),
                              "not_cited": round(statistics.median(n[0] for n in neg), 3)}
    out["median_lexical"] = {"cited": round(statistics.median(p[1] for p in pos), 3),
                             "not_cited": round(statistics.median(n[1] for n in neg), 3)}
    return out


# Swaps used for the entity perturbation: a different system, unit or
# property of the same kind, so the sentence still reads naturally.
ENTITY_SWAPS = [
    ("FlashAttention-2", "Longformer"), ("FlashAttention", "Reformer"), ("A100", "V100"), ("H100", "TPU v4"),
    ("SRAM", "DRAM"), ("HBM", "SSD"), ("GPU", "CPU"), ("quadratic", "logarithmic"), ("linear", "cubic"),
    ("softmax", "sigmoid"), ("faster", "slower"), ("reduces", "increases"), ("fewer", "more"),
    ("higher", "lower"), ("lower", "higher"), ("ImageNet", "CIFAR-10"), ("AlayaDB", "Pinecone"),
    ("sparse", "dense"), ("memory", "energy"), ("forward", "backward"), ("training", "inference"),
]
_AUXILIARY = re.compile(r"\b(is|are|was|were|can|does|do|will)\b")
_VERB = re.compile(r"\b(reduces|achieves|uses|recomputes|requires|reaches|yields|outperforms|shrinks|keeps|computes|"
                   r"allows|captures|preserves|limits|measures|reports|provides|introduces|gives|offers|avoids|"
                   r"matches|exceeds|improves|takes|processes|attains|scales|describes|executes|notes|states)\b")


def _perturb_number(text):
    m = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)(?=\s*(?:%|×|x\b|[A-Za-z]))", text) or \
        re.search(r"(?<![\w.])(\d{2,}(?:\.\d+)?)", text)
    if not m:
        return None
    value = m.group(1)
    new = str(round(float(value) * 1.5 + 3, 1)).rstrip("0").rstrip(".") if "." in value else str(int(value) * 2 + 3)
    return text[:m.start(1)] + new + text[m.end(1):]


def _perturb_entity(text):
    for old, new in ENTITY_SWAPS:
        if re.search(rf"\b{re.escape(old)}\b", text):
            return re.sub(rf"\b{re.escape(old)}\b", new, text, count=1)
    return None


def _perturb_negation(text):
    """Reverse the claim's meaning with one inserted 'not', grammatically."""
    aux, verb = _AUXILIARY.search(text), _VERB.search(text)
    if verb and (not aux or verb.start() < aux.start()):
        word = verb.group(1)
        base = word[:-2] if word.endswith(("ches", "shes", "sses", "xes")) else word[:-1]
        return text[:verb.start()] + "does not " + base + text[verb.end():]
    if aux:
        return text[:aux.end()] + " not" + text[aux.end():]
    return None


def perturbation(saved, records):
    """Take citations the gold labels (or, without them, the verifier) call
    supported, break them in a known way, and re-verify against the same
    passage. Detection = the verifier no longer says 'supported'."""
    from verify_citations import verify_answer
    gold_path = os.path.join(OUT_DIR, "gold_labels.csv")
    gold = {}
    if os.path.exists(gold_path):
        with open(gold_path, newline="", encoding="utf-8") as f:
            gold = {r["pair_id"]: r["label"] for r in csv.DictReader(f)}
    hits_by_query = {row["query"]: hits for row, _, hits in saved}
    base = [r for r in records if r["verifier"] == "supported" and gold.get(r["pair_id"], "supported") == "supported"]

    def check(text, n, hits):
        claims = verify_answer(f"{text.rstrip('. ')} [Source {n}].", hits)["claims"]
        return claims[-1]["citations"][0] if claims else {"status": "unsupported", "semantic": None,
                                                          "lexical": 0.0, "missing_numbers": []}

    results = {k: {"tried": 0, "detected": 0, "missed_examples": [], "caught_examples": []}
               for k in ("unchanged", "number", "entity", "negation", "wrong_source")}
    for r in base:
        hits = hits_by_query[r["query"]]
        claim = r["backs"] or r["claim"]
        n = r["source"]
        variants = {"unchanged": (claim, n), "number": (_perturb_number(claim), n),
                    "entity": (_perturb_entity(claim), n), "negation": (_perturb_negation(claim), n)}
        own_paper = hits[n - 1]["meta"]["arxiv_id"]
        others = [i for i, h in enumerate(hits, 1) if h["meta"]["arxiv_id"] != own_paper]
        variants["wrong_source"] = (claim, random.Random(r["pair_id"]).choice(others)) if others else (None, n)
        for kind, (text, src) in variants.items():
            if not text:
                continue
            res = check(text, src, hits)
            caught = res["status"] != "supported"
            bucket = results[kind]
            bucket["tried"] += 1
            bucket["detected"] += caught
            examples = bucket["caught_examples" if caught else "missed_examples"]
            if len(examples) < 4 and kind != "unchanged":
                examples.append({"original": claim[:220], "perturbed": text[:220], "source": src,
                                 "status": res["status"], "semantic": res["semantic"],
                                 "lexical": res["lexical"], "missing_numbers": res["missing_numbers"]})
    for b in results.values():
        b["detection_rate"] = pct(b["detected"], b["tried"])
    results["unchanged"]["note"] = "unchanged supported claims re-checked in isolation (sanity check)"
    results["base_pairs"] = len(base)
    return results


def _paper_ranking(collection, query, k=40):
    from embeddings import embed_query
    res = collection.query(query_embeddings=[embed_query(query).tolist()], n_results=k)
    papers = []
    for meta in res["metadatas"][0]:
        pid = re.sub(r"v\d+$", "", meta["arxiv_id"])
        if pid not in papers:
            papers.append(pid)
    return papers


def retrieval(saved):
    import chromadb
    import pandas as pd
    from paths import CHROMA_DIR
    from eval_harness import TEST_QUERIES
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    out = {}

    # Harness questions with a known target paper, through the app's retriever.
    expected = {q["query"]: q["expected_arxiv_id"] for q in TEST_QUERIES if q["expected_arxiv_id"]}
    ranks = []
    for row, _, hits in saved:
        target = expected.get(row["query"])
        if not target:
            continue
        papers = []
        for h in hits:
            pid = re.sub(r"v\d+$", "", h["meta"]["arxiv_id"])
            if pid not in papers:
                papers.append(pid)
        ranks.append(papers.index(target) + 1 if target in papers else 0)
    out["harness_expected_paper"] = {
        "questions": len(ranks), "ranks": ranks,
        "hit_at_1": pct(sum(1 for r in ranks if r == 1), len(ranks)),
        "hit_at_any": pct(sum(1 for r in ranks if r), len(ranks)),
        "mrr": round(sum(1 / r for r in ranks if r) / len(ranks), 3) if ranks else None,
    }

    # Known-item: each paper's title as the query, against its own collection.
    for domain in ("efficient_attention", "arc_agi"):
        coll = client.get_collection(domain)
        meta = pd.read_csv(os.path.join(PARSED_DIR, f"{domain}_metadata.csv"))
        ranks = []
        for _, m in meta.iterrows():
            papers = _paper_ranking(coll, m["title"])
            target = re.sub(r"v\d+$", "", m["arxiv_id"])
            ranks.append(papers.index(target) + 1 if target in papers else 99)
        out[f"title_known_item_{domain}"] = {
            "papers": len(ranks),
            "hit_at_1": pct(sum(r == 1 for r in ranks), len(ranks)),
            "hit_at_3": pct(sum(r <= 3 for r in ranks), len(ranks)),
            "hit_at_5": pct(sum(r <= 5 for r in ranks), len(ranks)),
            "mrr": round(sum(1 / r for r in ranks) / len(ranks), 3),
        }
    return out


# Hand labels for the efficient_attention papers against that collection's
# topic ("efficient attention mechanisms"): on-topic, off-topic, or borderline
# (application papers whose contribution touches attention efficiency).
EA_LABELS = {
    "2302.04542": "on", "2209.15001": "on", "2602.02159": "on", "2504.10326": "on", "2409.16997": "on",
    "2608.18656": "on", "2507.11331": "on", "2505.14201": "on", "2205.14135": "on", "2307.08691": "on",
    "2006.16236": "on", "2601.15305": "on", "2512.07011": "on", "2003.05997": "on",
    "2206.03003": "off", "2002.00741": "off", "2503.11899": "off", "2605.06924": "off",
    "2503.10589": "off", "2309.01692": "off", "2402.04563": "off",
    "1809.04281": "borderline", "2305.11403": "borderline", "2609.00749": "borderline", "2605.26355": "borderline",
}


def relevance_filter():
    import pandas as pd
    from fetch_papers import RELEVANCE_THRESHOLD, relevance_scores
    topic = "Efficient Attention Mechanisms. Transformer efficiency research: FlashAttention, linear attention, sparse attention"
    papers = []
    for domain in ("efficient_attention", "arc_agi", "graph_neural_networks", "sports_science_test"):
        meta = pd.read_csv(os.path.join(PARSED_DIR, f"{domain}_metadata.csv"), dtype=str, keep_default_na=False)
        for _, m in meta.iterrows():
            pid = re.sub(r"v\d+$", "", m["arxiv_id"])
            label = EA_LABELS.get(pid, "off") if domain == "efficient_attention" else "off"
            papers.append({"id": pid, "domain": domain, "title": m["title"], "abstract": m["abstract"], "label": label})
    scores = relevance_scores(topic, [(p["title"], p["abstract"]) for p in papers])
    for p, s in zip(papers, scores):
        p["score"] = round(s, 3)
    on = [p["score"] for p in papers if p["label"] == "on"]
    off = [p["score"] for p in papers if p["label"] == "off"]
    off_inside = [p["score"] for p in papers if p["label"] == "off" and p["domain"] == DOMAIN]
    t = RELEVANCE_THRESHOLD
    kept_on = sum(s >= t for s in on)
    kept_off = sum(s >= t for s in off)
    best_t = max((x / 100 for x in range(40, 90)),
                 key=lambda x: sum(s >= x for s in on) + sum(s < x for s in off))
    return {
        "topic_text": topic, "threshold": t,
        "on_topic": len(on), "off_topic": len(off), "off_topic_in_collection": len(off_inside),
        "auc_all": roc_auc(on, off), "auc_within_collection": roc_auc(on, off_inside),
        "on_topic_kept_at_threshold": f"{kept_on}/{len(on)}",
        "off_topic_rejected_at_threshold": f"{len(off) - kept_off}/{len(off)}",
        "off_topic_in_collection_rejected": f"{sum(s < t for s in off_inside)}/{len(off_inside)}",
        "precision_at_threshold": pct(kept_on, kept_on + kept_off),
        "best_threshold_on_this_data": best_t,
        "median_score": {"on": statistics.median(on), "off_in_collection": statistics.median(off_inside),
                         "other_collections": statistics.median(p["score"] for p in papers if p["domain"] != DOMAIN)},
        "papers": [{k: p[k] for k in ("id", "domain", "label", "score", "title")}
                   for p in sorted(papers, key=lambda p: -p["score"])],
    }


def corpus():
    import pandas as pd
    out = {}
    for domain in ("efficient_attention", "arc_agi", "graph_neural_networks", "sports_science_test"):
        meta = pd.read_csv(os.path.join(PARSED_DIR, f"{domain}_metadata.csv"))
        with open(os.path.join(PARSED_DIR, f"{domain}_chunks.jsonl"), encoding="utf-8") as f:
            chunks = [json.loads(l) for l in f if l.strip()]
        with open(os.path.join(PARSED_DIR, f"{domain}_references.jsonl"), encoding="utf-8") as f:
            refs = [json.loads(l) for l in f if l.strip()]
        with open(os.path.join(PARSED_DIR, f"{domain}_citation_graph.jsonl"), encoding="utf-8") as f:
            edges = sum(1 for l in f if l.strip())
        words = [c["n_words"] for c in chunks]
        n_refs = sum(len(r["references"]) for r in refs)
        titled = sum(1 for r in refs for x in r["references"] if x.get("title"))
        out[domain] = {
            "papers": len(meta), "chunks": len(chunks), "chunks_per_paper": round(len(chunks) / len(meta), 1),
            "median_words": statistics.median(words), "p90_words": sorted(words)[int(0.9 * len(words))],
            "max_words": max(words), "over_target_220": sum(w > 220 for w in words),
            "section_types": dict(Counter(c["section_type"] for c in chunks)),
            "section_inherited_pct": pct(sum(c["section_type_inherited"] for c in chunks), len(chunks)),
            "references": n_refs, "references_with_title_pct": pct(titled, n_refs), "citation_edges": edges,
        }
    return out


def verification_latency(saved):
    from verify_citations import verify_answer
    verify_answer(saved[0][1], saved[0][2])   # warm the model
    times = []
    for _, text, hits in saved:
        t0 = time.perf_counter()
        verify_answer(text, hits)
        times.append((time.perf_counter() - t0) * 1000)
    return {"answers": len(times), "median_ms": round(statistics.median(times)),
            "max_ms": round(max(times)), "mean_ms": round(statistics.mean(times))}


def run_all(only=None):
    """Every metric, or just `only` (merged into the existing results file)."""
    saved = saved_answers()
    records = pairs_from(saved)
    path = os.path.join(OUT_DIR, "offline_metrics.json")
    results = {}
    if only and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            results = json.load(f)
    for name, fn in [("reproduce", lambda: reproduce(saved)),
                     ("verifier_vs_gold", lambda: verifier_vs_gold(records)),
                     ("discrimination", lambda: discrimination(saved)),
                     ("perturbation", lambda: perturbation(saved, records)),
                     ("retrieval", lambda: retrieval(saved)),
                     ("relevance_filter", relevance_filter),
                     ("corpus", corpus),
                     ("verification_latency", lambda: verification_latency(saved))]:
        if only and name != only:
            continue
        print(f"-- {name}", flush=True)
        results[name] = fn()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    shown = {only: results[only]} if only else {k: v for k, v in results.items() if k != "relevance_filter"}
    print(json.dumps(shown, indent=1, ensure_ascii=False)[:20000])


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "all"
    if command == "sheet":
        write_sheet()
    else:
        run_all(None if command == "all" else command)
