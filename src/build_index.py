import sys
sys.stdout.reconfigure(encoding="utf-8")

import json
import os
import chromadb
from embeddings import MODEL_NAME, embed_documents
from paths import CHROMA_DIR, PARSED_DIR

BATCH_SIZE = 64


def build_index(domain: str, mode: str = "rebuild"):
    """mode='rebuild' deletes and recreates the collection from the full
    chunks file (original behavior). mode='append' only embeds chunks whose
    IDs aren't already in the collection, and adds them to what's there."""
    chunks_path = os.path.join(PARSED_DIR, f"{domain}_chunks.jsonl")
    os.makedirs(CHROMA_DIR, exist_ok=True)

    with open(chunks_path, encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f]
    print(f"[{domain}] {len(chunks)} total chunks in file")

    client = chromadb.PersistentClient(path=CHROMA_DIR)
    existing_names = {getattr(c, "name", c) for c in client.list_collections()}

    if mode == "rebuild" or domain not in existing_names:
        if domain in existing_names:
            client.delete_collection(domain)
        collection = client.create_collection(name=domain, metadata={"hnsw:space": "cosine"})
        to_embed = chunks
    else:
        collection = client.get_collection(domain)
        existing_ids = set(collection.get(include=[])["ids"])
        to_embed = [c for c in chunks if c["chunk_id"] not in existing_ids]
        print(f"[{domain}] Appending - {len(to_embed)} new chunks, {len(existing_ids)} already indexed")

    if not to_embed:
        print(f"[{domain}] Nothing new to index")
        return collection.count()

    texts = [c["text"] for c in to_embed]
    print(f"Encoding with {MODEL_NAME}...")
    embeddings = embed_documents(texts, batch_size=BATCH_SIZE, show_progress=True).tolist()

    metadatas = [{
        "arxiv_id": c["arxiv_id"],
        "paper_title": c["paper_title"],
        "published": c["published"],
        "domain": c["domain"],
        "section_title": c["section_title"],
        "section_type": c["section_type"],
        "section_type_inherited": bool(c["section_type_inherited"]),
        "n_words": c["n_words"],
        "sentences_json": json.dumps(c["sentences"], ensure_ascii=False),
    } for c in to_embed]

    for i in range(0, len(to_embed), 256):
        collection.add(
            ids=[c["chunk_id"] for c in to_embed[i:i + 256]],
            documents=texts[i:i + 256],
            embeddings=embeddings[i:i + 256],
            metadatas=metadatas[i:i + 256],
        )
        print(f"Indexed {min(i + 256, len(to_embed))}/{len(to_embed)}")

    print(f"\nCollection '{domain}' now holds {collection.count()} chunks")
    return collection.count()


if __name__ == "__main__":
    domain = sys.argv[1] if len(sys.argv) > 1 else "efficient_attention"
    mode = sys.argv[2] if len(sys.argv) > 2 else "rebuild"
    build_index(domain, mode)