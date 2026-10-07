"""Evaluation harness: answers a fixed question set and scores retrieval and
citation faithfulness. Resumable - already-scored questions are skipped, so a
crash or exhausted free-tier quota never costs completed work.

    python eval_harness.py                       # efficient_attention set
    python eval_harness.py --out ../eval/results_gemini.csv --model gemini
    python eval_harness.py --rescore             # re-verify saved answers, no LLM calls
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import argparse
import csv
import json
import os
import time
import re
from generate_answer import answer
from paths import PROJECT_ROOT
from search_with_graph import search_with_graph
from verify_citations import LEXICAL_PARTIAL, verify_answer

TEST_QUERIES = [
    # --- Factual retrieval (single-paper lookup) ---
    {"query": "how does FlashAttention reduce memory usage", "expected_arxiv_id": "2205.14135", "category": "factual"},
    {"query": "how does dilated neighborhood attention differ from standard attention", "expected_arxiv_id": "2209.15001", "category": "factual"},
    {"query": "what dataset or benchmark does DiNAT evaluate image classification on", "expected_arxiv_id": "2209.15001", "category": "factual"},
    {"query": "what GPU hardware was used to benchmark FlashAttention-2's training throughput", "expected_arxiv_id": "2307.08691", "category": "factual"},
    {"query": "what is the time complexity of standard self-attention", "expected_arxiv_id": "", "category": "factual"},
    {"query": "how does AlayaDB measure serving latency", "expected_arxiv_id": "2504.10326", "category": "factual"},
    {"query": "what is the SLO target used in AlayaDB's evaluation", "expected_arxiv_id": "2504.10326", "category": "factual"},
    {"query": "how do random features approximate the exponential kernel in linear attention", "expected_arxiv_id": "2302.04542", "category": "factual"},

    # --- Cross-paper comparison ---
    {"query": "how do linear attention methods approximate softmax attention", "expected_arxiv_id": "", "category": "comparison"},
    {"query": "what are the tradeoffs of sparse attention patterns", "expected_arxiv_id": "", "category": "comparison"},
    {"query": "how does FlashAttention-2 improve on FlashAttention", "expected_arxiv_id": "", "category": "comparison"},
    {"query": "how does sparse attention compare to linear attention as an efficiency strategy", "expected_arxiv_id": "", "category": "comparison"},
    {"query": "what evaluation benchmarks are commonly used to compare efficient attention methods", "expected_arxiv_id": "", "category": "comparison"},
    {"query": "how does dilated neighborhood attention's receptive field growth compare to standard local attention", "expected_arxiv_id": "2209.15001", "category": "comparison"},

    # --- Contradiction / agreement check (citation-linked pairs) ---
    {"query": "does AlayaDB report different performance numbers than the original FlashAttention paper", "expected_arxiv_id": "", "category": "contradiction"},
    {"query": "does AlayaDB's serving performance compare to the training throughput numbers reported for FlashAttention-2", "expected_arxiv_id": "", "category": "contradiction"},
    {"query": "do FlashAttention-2's own reported TFLOPs numbers stay consistent across the paper", "expected_arxiv_id": "2307.08691", "category": "contradiction"},
    {"query": "how does SystolicAttention's hardware approach relate to the original FlashAttention algorithm", "expected_arxiv_id": "", "category": "contradiction"},

    # --- Out-of-scope (should correctly refuse) ---
    {"query": "what is the current state-of-the-art accuracy on ImageNet as of 2026", "expected_arxiv_id": "", "category": "out_of_scope"},
    {"query": "how much does it cost to train a GPT-4 scale model", "expected_arxiv_id": "", "category": "out_of_scope"},
]

EVAL_DIR = os.path.join(PROJECT_ROOT, "eval")
RAW_ANSWERS_DIR = os.path.join(EVAL_DIR, "raw_answers")
FIELDNAMES = ["query", "category", "expected_hit", "top1_similarity", "n_claims", "claims_supported",
              "claims_partial", "claims_unsupported", "support_score", "n_citations", "n_weak_citations",
              "faithfulness_rate", "uncited_sentences", "declined", "provider", "model", "total_ms"]


def base_id(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id.strip())


def load_completed_queries(path):
    """Resume support: read whatever's already in the CSV so we never
    re-run (and re-spend free-tier quota on) a query we already scored."""
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {row["query"] for row in csv.DictReader(f)}


def append_result(path, row):
    """Write one row immediately. A crash on query 4 no longer costs the
    results from queries 1-3 — same pattern as fetch_papers.py."""
    file_exists = os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def raw_answer_path(query):
    return os.path.join(RAW_ANSWERS_DIR, re.sub(r"[^a-z0-9]+", "_", query.lower())[:60] + ".txt")


def score_query(item, domain, model_choice):
    result = answer(domain, item["query"], model_choice=model_choice)

    # Always save the raw text, regardless of outcome — this is what lets us
    # debug a zero-claim result after the fact instead of trying to guess
    # or reproduce it by rerunning (LLM outputs aren't deterministic).
    os.makedirs(RAW_ANSWERS_DIR, exist_ok=True)
    with open(raw_answer_path(item["query"]), "w", encoding="utf-8") as f:
        f.write(f"Provider: {result['provider']} ({result['model']})\n\n{result['answer']}")

    return score_row(item, result["hits"], result["verification"], result["provider"],
                     result["model"], result["timings"]["total_ms"])


def score_row(item, hits, v, provider, model, total_ms):
    expected_id = item["expected_arxiv_id"]
    retrieved_ids = [base_id(h["meta"]["arxiv_id"]) for h in hits]
    hit = (base_id(expected_id) in retrieved_ids) if expected_id else ""

    # Citation-level keyword faithfulness: the original metric, kept so runs
    # stay comparable over time. A citation is weak below the lexical cut.
    citations = [c for claim in v["claims"] for c in claim["citations"]]
    weak = sum(1 for c in citations if c["status"] == "invalid" or c["lexical"] < LEXICAL_PARTIAL)
    s = v["summary"]

    return {
        "query": item["query"],
        "category": item.get("category", ""),
        "expected_hit": hit,
        "top1_similarity": round(hits[0]["similarity"], 3) if hits else None,
        "n_claims": s["claims"],
        "claims_supported": s["claims_supported"],
        "claims_partial": s["claims_partial"],
        "claims_unsupported": s["claims_unsupported"],
        "support_score": s["score"],
        "n_citations": len(citations),
        "n_weak_citations": weak,
        "faithfulness_rate": round((len(citations) - weak) / len(citations), 2) if citations else None,
        "uncited_sentences": s["uncited_sentences"],
        "declined": s["declined"],
        "provider": provider,
        "model": model,
        "total_ms": total_ms,
    }


def rescore(path, domain):
    """Re-verify every saved answer with the current verifier. Retrieval is
    deterministic, so this needs no LLM calls - handy when tuning the verifier."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    items = {t["query"]: t for t in TEST_QUERIES}
    out = []
    for row in rows:
        raw = raw_answer_path(row["query"])
        if not os.path.exists(raw):
            print(f"  no saved answer for: {row['query']}")
            out.append(row)
            continue
        with open(raw, encoding="utf-8") as f:
            text = f.read()
        answer_text = text.split("\n\n", 1)[1] if text.startswith("Provider:") else text
        hits = search_with_graph(domain, row["query"], k=5)
        item = items.get(row["query"], {"query": row["query"], "expected_arxiv_id": "",
                                        "category": row.get("category", "")})
        out.append(score_row(item, hits, verify_answer(answer_text, hits), row["provider"],
                             row.get("model", ""), row.get("total_ms", "")))
        print(f"  rescored: {row['query']}")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(out)


def summarize(path) -> dict:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    def floats(key, subset=rows):
        return [float(r[key]) for r in subset if r.get(key) not in ("", "None", None)]

    def mean(values):
        return round(sum(values) / len(values), 3) if values else None

    hits = [r["expected_hit"] == "True" for r in rows if r["expected_hit"] not in ("", "None")]
    claims = sum(int(r["n_claims"]) for r in rows)
    answered = [r for r in rows if r.get("category") != "out_of_scope"]
    refusals = [r for r in rows if r.get("category") == "out_of_scope"]
    return {
        "questions": len(rows),
        "retrieval_hit_rate": round(sum(hits) / len(hits), 3) if hits else None,
        "retrieval_hits": f"{sum(hits)}/{len(hits)}",
        "claims": claims,
        "claims_supported_pct": round(sum(int(r["claims_supported"]) for r in rows) / claims, 3) if claims else None,
        "claims_partial_pct": round(sum(int(r["claims_partial"]) for r in rows) / claims, 3) if claims else None,
        "claims_unsupported_pct": round(sum(int(r["claims_unsupported"]) for r in rows) / claims, 3) if claims else None,
        "mean_support_score": mean(floats("support_score", answered)),
        "mean_faithfulness_rate": mean(floats("faithfulness_rate", answered)),
        "mean_uncited_sentences": mean(floats("uncited_sentences", answered)),
        "out_of_scope_declined": f"{sum(1 for r in refusals if r.get('declined') == 'True')}/{len(refusals)}",
        "in_scope_declined": f"{sum(1 for r in answered if r.get('declined') == 'True')}/{len(answered)}",
        "median_latency_ms": sorted(floats("total_ms"))[len(rows) // 2] if rows else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", default="efficient_attention")
    parser.add_argument("--model", default="auto", help="auto, groq-120b, groq-20b or gemini")
    parser.add_argument("--out", default=os.path.join(EVAL_DIR, "results.csv"))
    parser.add_argument("--pause", type=float, default=30,
                        help="seconds between questions (Groq's free tier allows ~2 a minute)")
    parser.add_argument("--rescore", action="store_true", help="re-verify saved answers without calling the LLM")
    args = parser.parse_args()

    if args.rescore:
        rescore(args.out, args.domain)
        summary = summarize(args.out)
        with open(os.path.splitext(args.out)[0] + "_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(summary, indent=2))
        return

    completed = load_completed_queries(args.out)
    if completed:
        print(f"Resuming — {len(completed)} queries already scored, skipping those.\n")

    for item in TEST_QUERIES:
        if item["query"] in completed:
            print(f"Skipping already-scored: {item['query']}")
            continue

        print(f"\nRunning: {item['query']}")
        try:
            row = score_query(item, args.domain, args.model)
            append_result(args.out, row)
            print(f"  Saved - {row['claims_supported']}/{row['n_claims']} claims supported.")
        except Exception as e:
            print(f"  Skipped after a failure — will retry on next run: {e}")

        time.sleep(args.pause)

    # Summary always reads from the full accumulated file, not just this run
    summary = summarize(args.out)
    summary_path = os.path.splitext(args.out)[0] + "_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\n{json.dumps(summary, indent=2)}\nWritten to {summary_path}")


if __name__ == "__main__":
    main()
