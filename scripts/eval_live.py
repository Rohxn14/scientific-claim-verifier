"""Live end-to-end evaluation, extending src/eval_harness.py.

Asks the harness's 20 questions plus 8 extra single-paper lookups and 6 extra
out-of-scope questions, and records for each: per-stage latency, which
provider/model answered, the rank of the expected paper, whether passages
pulled in through the citation graph were retrieved and cited, and the full
verification report. Everything is saved as JSON per question, so the
offline analysis (scripts/eval_extended.py) never has to call an LLM.

    python scripts/eval_live.py                 # the question set (resumable)
    python scripts/eval_live.py --failover      # failover drill
    python scripts/eval_live.py --stream 5      # time-to-first-token, streamed
    python scripts/eval_live.py --synthetic 40  # chunk-level retrieval, LLM-written questions

Outputs go to eval/extended/live/.
"""
import argparse
import csv
import json
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8")

from eval_harness import TEST_QUERIES  # noqa: E402
from paths import PROJECT_ROOT  # noqa: E402

OUT_DIR = os.path.join(PROJECT_ROOT, "eval", "extended", "live")
ANSWERS_DIR = os.path.join(OUT_DIR, "answers")
DOMAIN = "efficient_attention"

# Single-paper lookups for papers the original set never asks about, so the
# retrieval hit rate rests on more than 9 questions.
EXTRA_FACTUAL = [
    ("how does INT-FlashAttention quantize attention to INT8", "2409.16997"),
    ("how does FLASH-D hide the softmax division inside FlashAttention", "2505.14201"),
    ("how does the Routing Transformer decide which keys each query attends to", "2003.05997"),
    ("how does Block Sparse Flash Attention skip computation", "2512.07011"),
    ("how can linear attention be written as a recurrent neural network", "2006.16236"),
    ("how does gated sparse attention improve training stability", "2601.15305"),
    ("how does ContextPipe assemble context for long-horizon agents", "2609.00749"),
    ("how does Focus-dLLM accelerate long-context diffusion LLM inference", "2602.02159"),
]

# Plausible machine-learning questions the collection cannot answer.
EXTRA_OUT_OF_SCOPE = [
    "what learning rate schedule did the original BERT paper use for pre-training",
    "how is the reward model trained in InstructGPT",
    "how many GPU hours did DeepSeek-V3 pre-training take",
    "what GDT score did AlphaFold 2 reach at CASP14",
    "what is the API price per million tokens of GPT-4o",
    "who won the 2026 FIFA World Cup",
]

QUESTIONS = (
    [{**q, "set": "harness"} for q in TEST_QUERIES]
    + [{"query": q, "expected_arxiv_id": pid, "category": "factual", "set": "extra"} for q, pid in EXTRA_FACTUAL]
    + [{"query": q, "expected_arxiv_id": "", "category": "out_of_scope", "set": "extra"} for q in EXTRA_OUT_OF_SCOPE]
)

FIELDS = ["query", "category", "set", "expected_rank", "n_sources", "n_graph_sources", "graph_sources_cited",
          "n_claims", "claims_supported", "claims_partial", "claims_unsupported", "citations", "invalid",
          "uncited_sentences", "support_score", "verdict", "declined", "provider", "model",
          "retrieval_ms", "generation_ms", "verification_ms", "total_ms"]


def base_id(paper_id: str) -> str:
    return re.sub(r"v\d+$", "", paper_id.strip())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower())[:60]


def expected_rank(hits: list, expected: str):
    """1-based rank of the expected paper among the retrieved papers (semantic
    hits first, in order), or 0 if it was not retrieved; None if no target."""
    if not expected:
        return None
    papers = []
    for h in hits:
        pid = base_id(h["meta"]["arxiv_id"])
        if pid not in papers:
            papers.append(pid)
    return papers.index(expected) + 1 if expected in papers else 0


def row_for(item: dict, result: dict) -> dict:
    hits, v, t = result["hits"], result["verification"], result["timings"]
    s = v["summary"]
    graph = {i + 1 for i, h in enumerate(hits) if h["via"] != "semantic"}
    cited = {n for c in v["claims"] for n in c["sources"]}
    return {
        "query": item["query"], "category": item["category"], "set": item["set"],
        "expected_rank": expected_rank(hits, item["expected_arxiv_id"]),
        "n_sources": len(hits), "n_graph_sources": len(graph), "graph_sources_cited": len(graph & cited),
        "n_claims": s["claims"], "claims_supported": s["claims_supported"], "claims_partial": s["claims_partial"],
        "claims_unsupported": s["claims_unsupported"], "citations": s["citations"], "invalid": s["invalid"],
        "uncited_sentences": s["uncited_sentences"], "support_score": s["score"], "verdict": s["verdict"],
        "declined": s["declined"], "provider": result["provider"], "model": result["model"],
        **{k: t[k] for k in ("retrieval_ms", "generation_ms", "verification_ms", "total_ms")},
    }


def run_questions(pause: float, model: str):
    from generate_answer import answer
    os.makedirs(ANSWERS_DIR, exist_ok=True)
    csv_path = os.path.join(OUT_DIR, "results.csv")
    done = set()
    if os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            done = {r["query"] for r in csv.DictReader(f)}

    for i, item in enumerate(QUESTIONS, 1):
        if item["query"] in done:
            continue
        print(f"[{i}/{len(QUESTIONS)}] {item['query']}", flush=True)
        try:
            result = answer(DOMAIN, item["query"], model_choice=model)
        except Exception as e:
            print(f"   failed, will retry on the next run: {str(e)[:200]}", flush=True)
            time.sleep(pause)
            continue
        with open(os.path.join(ANSWERS_DIR, slug(item["query"]) + ".json"), "w", encoding="utf-8") as f:
            json.dump({**item, **result}, f, ensure_ascii=False, indent=1)
        row = row_for(item, result)
        new_file = not os.path.exists(csv_path)
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new_file:
                w.writeheader()
            w.writerow(row)
        print(f"   {row['provider']}/{row['model']}  {row['claims_supported']}/{row['n_claims']} supported  "
              f"{row['total_ms']} ms", flush=True)
        time.sleep(pause)


def failover_drill(n: int, pause: float):
    """Make the primary model (Groq gpt-oss-120b) fail the way a rate limit
    does, and check that 'auto' still answers - through the real remaining
    chain (Gemini, then Groq gpt-oss-20b) - for both the one-shot and the
    streaming path. Records which provider answered and the time lost."""
    import generation
    real_call, real_stream = generation.CALL_FUNCTIONS["groq"], generation.STREAM_FUNCTIONS["groq"]

    def failing_call(prompt, model):
        if model == "openai/gpt-oss-120b":
            raise RuntimeError("Error code: 429 - simulated rate limit for the drill")
        return real_call(prompt, model)

    def failing_stream(prompt, model):
        if model == "openai/gpt-oss-120b":
            raise RuntimeError("Error code: 429 - simulated rate limit for the drill")
        yield from real_stream(prompt, model)

    from generate_answer import build_prompt
    from search_with_graph import search_with_graph
    questions = [q for q in QUESTIONS if q["category"] != "out_of_scope"][:n]
    records = []
    for item in questions:
        hits = search_with_graph(DOMAIN, item["query"], k=5)
        prompt = build_prompt(item["query"], hits)
        for path in ("one-shot", "stream"):
            generation.CALL_FUNCTIONS["groq"], generation.STREAM_FUNCTIONS["groq"] = failing_call, failing_stream
            t0 = time.perf_counter()
            attempts = []
            try:
                if path == "one-shot":
                    text, provider, model = generation.generate_full(prompt, "auto")
                    ttft = None
                else:
                    provider, model, tokens = generation.stream(prompt, "auto")
                    ttft = time.perf_counter() - t0
                    text = "".join(tokens)
                ok = bool(text and text.strip())
            except Exception as e:
                provider, model, text, ok, ttft = None, None, "", False, None
                attempts.append(str(e)[:200])
            finally:
                generation.CALL_FUNCTIONS["groq"], generation.STREAM_FUNCTIONS["groq"] = real_call, real_stream
            rec = {"query": item["query"], "path": path, "answered": ok, "provider": provider, "model": model,
                   "seconds": round(time.perf_counter() - t0, 2),
                   "first_token_seconds": round(ttft, 2) if ttft else None, "errors": attempts}
            records.append(rec)
            print(json.dumps(rec), flush=True)
            time.sleep(pause)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "failover_drill.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, indent=1)


def stream_timing(n: int, pause: float):
    """Time to sources and time to first answer token on the streaming path
    the UI uses."""
    from generate_answer import stream_answer
    questions = [q for q in QUESTIONS if q["category"] != "out_of_scope"][:n]
    records = []
    for item in questions:
        t0 = time.perf_counter()
        marks, provider = {}, None
        for ev in stream_answer(DOMAIN, item["query"]):
            now = time.perf_counter() - t0
            if ev["type"] == "sources":
                marks.setdefault("sources_s", now)
            elif ev["type"] == "token":
                marks.setdefault("first_token_s", now)
            elif ev["type"] == "done":
                marks["done_s"] = now
                provider = f"{ev['provider']}/{ev['model']}"
        rec = {"query": item["query"], "provider": provider, **{k: round(v, 2) for k, v in marks.items()}}
        records.append(rec)
        print(json.dumps(rec), flush=True)
        time.sleep(pause)
    with open(os.path.join(OUT_DIR, "stream_timing.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, indent=1)
    if records:
        print("median first token:", statistics.median(r["first_token_s"] for r in records if "first_token_s" in r))


QUESTION_PROMPT = """Below is a passage from the research paper "{title}".
Write ONE specific question that this passage answers, phrased the way a researcher
would ask it without having seen the passage. Do not copy long phrases from it and
do not refer to "the passage" or "the paper". Return only the question.

Passage:
{text}"""


def synthetic_retrieval(n: int, pause: float):
    """Chunk-level retrieval benchmark: an LLM writes a question for each of n
    random passages; the passage the question came from is the target. Scores
    Recall@k and MRR for plain semantic search, and whether the app's
    citation-graph expansion adds anything."""
    import random
    import chromadb
    from embeddings import embed_query
    from generation import generate_full
    from paths import CHROMA_DIR, PARSED_DIR
    from search_with_graph import search_with_graph

    with open(os.path.join(PARSED_DIR, f"{DOMAIN}_chunks.jsonl"), encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f if line.strip()]
    pool = [c for c in chunks if c["n_words"] >= 80]
    sample = random.Random(11).sample(pool, n)
    questions_path = os.path.join(OUT_DIR, "synthetic_questions.json")
    questions = {}
    if os.path.exists(questions_path):
        with open(questions_path, encoding="utf-8") as f:
            questions = json.load(f)
    for c in sample:
        if c["chunk_id"] in questions:
            continue
        prompt = QUESTION_PROMPT.format(title=c["paper_title"], text=c["text"])
        for choice in ("gemini", "gemini", "groq-20b"):
            try:
                text, provider, model = generate_full(prompt, choice)
                questions[c["chunk_id"]] = {"question": text.strip().strip('"'), "model": model,
                                            "paper": c["arxiv_id"], "section_type": c["section_type"]}
                print(f"{c['chunk_id']}: {questions[c['chunk_id']]['question']}", flush=True)
                break
            except Exception as e:
                print(f"   {choice} failed: {str(e)[:120]}", flush=True)
                time.sleep(pause)
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(questions_path, "w", encoding="utf-8") as f:
            json.dump(questions, f, ensure_ascii=False, indent=1)

    collection = chromadb.PersistentClient(path=CHROMA_DIR).get_collection(DOMAIN)
    rows = []
    for chunk_id, q in questions.items():
        res = collection.query(query_embeddings=[embed_query(q["question"]).tolist()], n_results=20)
        ids = res["ids"][0]
        papers = []
        for meta in res["metadatas"][0]:
            if meta["arxiv_id"] not in papers:
                papers.append(meta["arxiv_id"])
        graph_ids = {h["meta"]["arxiv_id"] + "|" + h["text"][:60] for h in search_with_graph(DOMAIN, q["question"], k=5)}
        target = next(c for c in chunks if c["chunk_id"] == chunk_id)
        rows.append({
            "chunk_id": chunk_id, "question": q["question"], "section_type": q["section_type"],
            "chunk_rank": ids.index(chunk_id) + 1 if chunk_id in ids else None,
            "paper_rank": papers.index(q["paper"]) + 1 if q["paper"] in papers else None,
            "in_app_context": target["arxiv_id"] + "|" + target["text"][:60] in graph_ids,
        })

    def recall(key, k):
        return round(sum(1 for r in rows if r[key] and r[key] <= k) / len(rows), 3)

    summary = {
        "questions": len(rows),
        "chunk_recall@1": recall("chunk_rank", 1), "chunk_recall@5": recall("chunk_rank", 5),
        "chunk_recall@10": recall("chunk_rank", 10), "chunk_recall@20": recall("chunk_rank", 20),
        "chunk_mrr@20": round(sum(1 / r["chunk_rank"] for r in rows if r["chunk_rank"]) / len(rows), 3),
        "paper_hit@1": recall("paper_rank", 1), "paper_hit@5": recall("paper_rank", 5),
        "target_in_app_context": round(sum(r["in_app_context"] for r in rows) / len(rows), 3),
    }
    with open(os.path.join(OUT_DIR, "synthetic_retrieval.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="auto")
    parser.add_argument("--pause", type=float, default=30, help="seconds between questions (Groq free tier)")
    parser.add_argument("--failover", type=int, nargs="?", const=4, help="run the failover drill on N questions")
    parser.add_argument("--stream", type=int, nargs="?", const=5, help="time the streaming path on N questions")
    parser.add_argument("--synthetic", type=int, nargs="?", const=40,
                        help="chunk-level retrieval benchmark with N LLM-written questions")
    args = parser.parse_args()
    if args.synthetic:
        synthetic_retrieval(args.synthetic, args.pause)
    elif args.failover:
        failover_drill(args.failover, args.pause)
    elif args.stream:
        stream_timing(args.stream, args.pause)
    else:
        run_questions(args.pause, args.model)


if __name__ == "__main__":
    main()
