"""Finds citation-linked paper pairs that both report numeric results -
candidates for testing contradiction detection."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import sys
sys.stdout.reconfigure(encoding="utf-8")
import json
import re

from paths import PARSED_DIR

DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "efficient_attention"
CHUNKS_PATH = os.path.join(PARSED_DIR, f"{DOMAIN}_chunks.jsonl")
GRAPH_PATH = os.path.join(PARSED_DIR, f"{DOMAIN}_citation_graph.jsonl")

chunks = [json.loads(l) for l in open(CHUNKS_PATH, encoding="utf-8")]
edges = [json.loads(l) for l in open(GRAPH_PATH, encoding="utf-8")]

print(f"Loaded {len(chunks)} chunks, {len(edges)} citation edges\n")

by_paper = {}
for c in chunks:
    by_paper.setdefault(c["arxiv_id"], []).append(c)

NUMBER_PATTERN = re.compile(
    r"\d+(\.\d+)?\s*(%|[x×]|ms|s|gb|mb|tokens?|flops?)\b",
    re.IGNORECASE
)
for e in edges:
    a_id, b_id = e["citing_id"], e["cited_id"]
    a_all = by_paper.get(a_id, [])
    b_all = by_paper.get(b_id, [])
    a_results = [c for c in a_all if c["section_type"] == "results"]
    b_results = [c for c in b_all if c["section_type"] == "results"]
    a_numeric = [c for c in a_results if NUMBER_PATTERN.search(c["text"])]
    b_numeric = [c for c in b_results if NUMBER_PATTERN.search(c["text"])]

    print(f"Pair: {a_id} <-> {b_id}")
    print(f"  {a_id}: {len(a_all)} total chunks, {len(a_results)} results-type, {len(a_numeric)} with numbers")
    print(f"  {b_id}: {len(b_all)} total chunks, {len(b_results)} results-type, {len(b_numeric)} with numbers")

    if a_numeric and b_numeric:
        print(f"\n  {'=' * 60}\n  MATCH FOUND\n")
        for c in a_numeric[:2]:
            print(f"    [{a_id}] {c['text'][:180]}...")
        for c in b_numeric[:2]:
            print(f"    [{b_id}] {c['text'][:180]}...")
    print()
