"""Semantic search over a domain's chunks, expanded along the citation graph:
for each retrieved paper, the best-matching passage from papers it cites (or
that cite it) is added too, so the answer can compare linked claims."""
import json
import sys
import os
import chromadb
from embeddings import embed_query
from paths import CHROMA_DIR, PARSED_DIR

TOP_K = 5
MAX_GRAPH_EXPANSION = 3  # extra passages pulled in via citation links, at most

_client = None
_graph_cache = {}  # domain -> (file mtime, neighbors)


def _get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_DIR)
    return _client


def _load_graph(domain: str) -> dict:
    graph_path = os.path.join(PARSED_DIR, f"{domain}_citation_graph.jsonl")
    try:
        mtime = os.path.getmtime(graph_path)
    except OSError:
        return {}
    cached = _graph_cache.get(domain)
    if cached and cached[0] == mtime:
        return cached[1]

    neighbors = {}
    with open(graph_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            neighbors.setdefault(e["citing_id"], set()).add(e["cited_id"])
            neighbors.setdefault(e["cited_id"], set()).add(e["citing_id"])
    _graph_cache[domain] = (mtime, neighbors)
    return neighbors


def search_with_graph(domain: str, query: str, k: int = TOP_K, max_expansion: int = MAX_GRAPH_EXPANSION):
    collection = _get_client().get_collection(domain)
    neighbors = _load_graph(domain)

    q_emb = embed_query(query).tolist()
    res = collection.query(query_embeddings=[q_emb], n_results=k)

    hits = []
    seen_papers = set()
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        hits.append({"text": doc, "meta": meta, "similarity": 1 - dist, "via": "semantic"})
        seen_papers.add(meta["arxiv_id"])

    graph_expanded = []
    for h in hits:
        for neighbor_id in sorted(neighbors.get(h["meta"]["arxiv_id"], set())):
            if neighbor_id in seen_papers or len(graph_expanded) >= max_expansion:
                continue
            seen_papers.add(neighbor_id)
            neighbor_hits = collection.query(
                query_embeddings=[q_emb], n_results=1,
                where={"arxiv_id": neighbor_id},
            )
            if neighbor_hits["documents"][0]:
                graph_expanded.append({
                    "text": neighbor_hits["documents"][0][0],
                    "meta": neighbor_hits["metadatas"][0][0],
                    "similarity": 1 - neighbor_hits["distances"][0][0],
                    "via": f"citation-link from {h['meta']['arxiv_id']}",
                })

    return hits + graph_expanded


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    domain = sys.argv[1] if len(sys.argv) > 1 else "efficient_attention"
    query = " ".join(sys.argv[2:]) or "how does FlashAttention reduce memory usage?"
    results = search_with_graph(domain, query)

    print(f"\nDomain: {domain}\nQuery: {query}\n" + "=" * 70)
    for r in results:
        m = r["meta"]
        print(f"\n[{r['similarity']:.3f}] ({r['via']}) {m['paper_title'][:55]}")
        print(f"        {m['section_title'][:45]}  ({m['section_type']})")
