import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import json
import pandas as pd
from rapidfuzz import fuzz, process
from paths import PARSED_DIR

MATCH_THRESHOLD = 88


def build_citation_graph(domain: str, threshold: int = MATCH_THRESHOLD) -> int:
    """Match each paper's bibliography against the domain's own corpus titles
    to find in-corpus citation edges. Returns the number of edges found."""
    metadata_path = os.path.join(PARSED_DIR, f"{domain}_metadata.csv")
    refs_path = os.path.join(PARSED_DIR, f"{domain}_references.jsonl")
    graph_path = os.path.join(PARSED_DIR, f"{domain}_citation_graph.jsonl")

    meta_df = pd.read_csv(metadata_path)
    corpus_titles = dict(zip(meta_df["title"], meta_df["arxiv_id"]))
    title_list = list(corpus_titles.keys())

    with open(refs_path, encoding="utf-8") as f:
        refs = [json.loads(line) for line in f]

    edges = []
    for paper in refs:
        citing_id = paper["arxiv_id"]
        citing_title = paper["paper_title"]

        for ref in paper["references"]:
            if not ref.get("title"):
                continue

            match = process.extractOne(ref["title"], title_list, scorer=fuzz.token_sort_ratio)
            if match is None:
                continue

            matched_title, score, _ = match
            if score >= threshold:
                cited_id = corpus_titles[matched_title]
                if cited_id == citing_id:
                    continue
                edges.append({
                    "citing_id": citing_id,
                    "citing_title": citing_title,
                    "cited_id": cited_id,
                    "cited_title": matched_title,
                    "match_score": score,
                })

    with open(graph_path, "w", encoding="utf-8") as f:
        for e in edges:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(f"[{domain}] Found {len(edges)} in-corpus citation edges (threshold={threshold})\n")
    for e in sorted(edges, key=lambda x: -x["match_score"]):
        print(f"[{e['match_score']}] {e['citing_title'][:45]:45s} -> {e['cited_title'][:45]}")

    total_refs = sum(len(p["references"]) for p in refs)
    refs_with_title = sum(1 for p in refs for r in p["references"] if r.get("title"))
    if total_refs:
        print(f"\nTotal references across corpus: {total_refs}")
        print(f"References with a parsed title: {refs_with_title} ({refs_with_title / total_refs:.0%})")

    return len(edges)


if __name__ == "__main__":
    domain = sys.argv[1] if len(sys.argv) > 1 else "efficient_attention"
    build_citation_graph(domain)