import sys
import time

from generation import generate_full, stream as stream_text
from search_with_graph import search_with_graph
from memory import contextualize_question
from verify_citations import verify_answer


def build_prompt(query: str, hits: list, history: list = None) -> str:
    context_blocks = []
    for i, h in enumerate(hits, 1):
        m = h["meta"]
        via_note = "" if h["via"] == "semantic" else f" [retrieved via citation link to {h['via'].split('from ')[-1]}]"
        context_blocks.append(
            f"[Source {i}]{via_note} Paper: \"{m['paper_title']}\" ({m['published'][:4]})\n"
            f"Section: {m['section_title']} ({m['section_type']})\n"
            f"Text: {h['text']}"
        )
    context = "\n\n".join(context_blocks)

    history_block = ""
    if history:
        history_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['text']}"
            for m in history[-6:]
        )
        history_block = f"""
Conversation so far (for context on what the user means - NOT a source of facts):
{history_text}
"""

    has_graph_sources = any(h["via"] != "semantic" for h in hits)
    contradiction_instruction = (
        "\n- Some sources were included because they are cited by or cite another "
        "source above. Explicitly compare their claims: do they agree, extend one "
        "another, or report different numbers/conclusions for the same thing? "
        "State this clearly rather than only summarizing each in isolation."
        if has_graph_sources else ""
    )

    return f"""You are a research assistant answering questions strictly from the provided sources, which are excerpts from scientific papers.
{history_block}
Rules:
- Only use information present in the Sources below to support factual claims. The conversation history is only to help you understand what the user is referring to - never treat something said earlier in the conversation as a fact unless it is also backed by a source below.
- Every sentence that states a fact must end with a citation in exactly this form: [Source 2], or [Source 1, Source 3] for several sources - including opening and summary sentences. Place it before the sentence's full stop. Do not use any other citation style (no 【】 brackets, line numbers, footnotes or "(see Source 2)").
- Quote numbers exactly as the sources give them, and don't compute new figures the sources don't state.
- If the sources do not contain enough information to answer confidently, say so explicitly rather than guessing, and say what is missing.
- If sources disagree with each other, point out the disagreement rather than picking one silently.{contradiction_instruction}
- Start directly with the answer (no "Answer:" label or title), then give the supporting detail. Use Markdown (short paragraphs, bullet lists, or a table for comparisons) and LaTeX for math ($...$ inline, $$...$$ for display).

Sources:
{context}

Question: {query}

Answer:"""


SECTION_LABELS = {"abstract": "Abstract", "introduction": "Introduction", "related_work": "Related work",
                  "method": "Method", "results": "Results", "conclusion": "Conclusion"}


def serialize_sources(hits: list) -> list:
    """The retrieved passages as the API returns them; sources[i] is [Source i+1]."""
    out = []
    for i, h in enumerate(hits, 1):
        m = h["meta"]
        paper_id = m["arxiv_id"]
        published = str(m.get("published", ""))
        section = m["section_title"]
        if not section or section.strip().lower() == "untitled":
            # GROBID found no heading; fall back to the inferred section type.
            section = SECTION_LABELS.get(m.get("section_type"), "Body text")
        out.append({
            "index": i,
            "paper_id": paper_id,
            "paper_title": m["paper_title"],
            "year": published[:4] if published[:4].isdigit() else None,
            "section": section,
            "section_type": m["section_type"],
            "similarity": round(float(h["similarity"]), 4),
            "via": "semantic" if h["via"] == "semantic" else "citation",
            "linked_from": None if h["via"] == "semantic" else h["via"].split("from ")[-1],
            "text": h["text"],
            "url": None if paper_id.startswith("up_") else f"https://arxiv.org/abs/{paper_id}",
        })
    return out


def retrieve(domain: str, query: str, top_k: int = 5, history: list = None):
    search_query = contextualize_question(history or [], query)
    return search_query, search_with_graph(domain, search_query, k=top_k)


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def answer(domain: str, query: str, top_k: int = 5, model_choice: str = "auto", history: list = None) -> dict:
    """Retrieve, generate and verify in one call. Returns everything the API
    sends back, plus the raw hits."""
    t0 = time.perf_counter()
    search_query, hits = retrieve(domain, query, top_k, history)
    t_retrieval = _ms(t0)

    t1 = time.perf_counter()
    answer_text, provider, model = generate_full(build_prompt(query, hits, history), model_choice)
    t_generation = _ms(t1)

    t2 = time.perf_counter()
    verification = verify_answer(answer_text, hits)
    return {
        "answer": answer_text,
        "provider": provider,
        "model": model,
        "search_query": search_query,
        "sources": serialize_sources(hits),
        "verification": verification,
        "timings": {"retrieval_ms": t_retrieval, "generation_ms": t_generation,
                    "verification_ms": _ms(t2), "total_ms": _ms(t0)},
        "hits": hits,
    }


def stream_answer(domain: str, query: str, top_k: int = 5, model_choice: str = "auto", history: list = None):
    """Same pipeline as answer(), as a stream of events for the UI:
    status (stage changes), sources, token (answer text deltas), done."""
    t0 = time.perf_counter()
    history = history or []
    if history:
        yield {"type": "status", "stage": "rewriting"}
    search_query = contextualize_question(history, query)

    yield {"type": "status", "stage": "retrieving", "search_query": search_query}
    hits = search_with_graph(domain, search_query, k=top_k)
    t_retrieval = _ms(t0)
    yield {"type": "sources", "sources": serialize_sources(hits), "search_query": search_query}

    yield {"type": "status", "stage": "generating"}
    t1 = time.perf_counter()
    provider, model, tokens = stream_text(build_prompt(query, hits, history), model_choice)
    yield {"type": "status", "stage": "generating", "provider": provider, "model": model}
    parts = []
    for token in tokens:
        parts.append(token)
        yield {"type": "token", "text": token}
    answer_text = "".join(parts)
    t_generation = _ms(t1)

    yield {"type": "status", "stage": "verifying"}
    t2 = time.perf_counter()
    verification = verify_answer(answer_text, hits)
    yield {
        "type": "done",
        "answer": answer_text,
        "provider": provider,
        "model": model,
        "search_query": search_query,
        "verification": verification,
        "timings": {"retrieval_ms": t_retrieval, "generation_ms": t_generation,
                    "verification_ms": _ms(t2), "total_ms": _ms(t0)},
    }


def generate_answer(domain: str, query: str, top_k: int = 5, model_choice: str = "auto", history: list = None):
    """Returns (answer_text, hits, provider) - the eval harness and CLI entry point."""
    result = answer(domain, query, top_k, model_choice, history)
    return result["answer"], result["hits"], result["provider"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    domain = sys.argv[1] if len(sys.argv) > 1 else "efficient_attention"
    query = " ".join(sys.argv[2:]) or "how does FlashAttention-2 improve on FlashAttention?"
    result = answer(domain, query)

    print(f"\nDomain: {domain}\nQuestion: {query}  (rewritten for search: {result['search_query']})"
          f"  (answered by: {result['provider']} / {result['model']})\n{'=' * 70}")
    print(result["answer"])

    print(f"\n{'—' * 70}\nRetrieved sources:")
    for s in result["sources"]:
        tag = "" if s["via"] == "semantic" else f"  [citation link from {s['linked_from']}]"
        print(f"  [{s['index']}] ({s['similarity']:.3f}) {s['paper_title'][:55]} — {s['section'][:35]}{tag}")

    v = result["verification"]["summary"]
    print(f"\nVerification: {v['claims_supported']}/{v['claims']} claims supported, "
          f"{v['claims_partial']} partial, {v['claims_unsupported']} unsupported ({v['verdict']})")
