"""Plain semantic search over one collection, without citation-graph expansion.
Debug helper - the app uses search_with_graph.py."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import sys
sys.stdout.reconfigure(encoding="utf-8")
import chromadb
from embeddings import embed_query
from paths import CHROMA_DIR

DOMAIN = "efficient_attention"

query = " ".join(sys.argv[1:]) or "how does FlashAttention reduce memory usage?"

q_emb = embed_query(query).tolist()

collection = chromadb.PersistentClient(path=CHROMA_DIR).get_collection(DOMAIN)
res = collection.query(query_embeddings=[q_emb], n_results=5)

print(f"\nQuery: {query}\n" + "=" * 70)
for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
    print(f"\n[{1 - dist:.3f}] {meta['paper_title'][:65]}")
    print(f"        {meta['section_title'][:50]}  ({meta['section_type']})")
    print(f"        {doc[:250]}...")
