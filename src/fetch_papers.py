import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import time
import urllib.request
import arxiv
import pandas as pd
from paths import PARSED_DIR, RAW_PDFS_DIR, project_relative
from embeddings import embed_documents, embed_query

# bge-small scores almost any ML paper above 0.5 against an ML topic, so the
# cut has to sit well above that; 0.6 drops clearly off-topic papers when the
# topic text is descriptive (a sentence, not two words).
RELEVANCE_THRESHOLD = 0.6


def relevance_scores(topic_text: str, papers: list) -> list:
    """Cosine similarity of each (title, abstract) pair to the topic."""
    if not papers:
        return []
    topic = embed_query(topic_text)
    docs = embed_documents([f"{title}. {abstract}" for title, abstract in papers])
    return [float(s) for s in docs @ topic]


def fetch_papers(domain: str, queries: list, max_per_query: int = 6, topic_text: str = None,
                 relevance_threshold: float = RELEVANCE_THRESHOLD, progress=None) -> int:
    """Fetch papers for a domain from arXiv. Resumable: already-downloaded papers
    and already-successful queries are skipped. Returns total papers on disk.
    With topic_text, papers scoring below relevance_threshold are skipped."""
    pdf_dir = os.path.join(RAW_PDFS_DIR, domain)
    metadata_path = os.path.join(PARSED_DIR, f"{domain}_metadata.csv")
    completed_path = os.path.join(PARSED_DIR, f"{domain}_completed_queries.txt")

    os.makedirs(pdf_dir, exist_ok=True)
    os.makedirs(PARSED_DIR, exist_ok=True)

    client = arxiv.Client(page_size=10, delay_seconds=8, num_retries=8)

    if os.path.exists(metadata_path):
        existing_df = pd.read_csv(metadata_path)
        seen_ids = set(existing_df["arxiv_id"])
        records = existing_df.to_dict("records")
        print(f"[{domain}] Resuming - {len(seen_ids)} papers already fetched.")
    else:
        seen_ids = set()
        records = []

    if os.path.exists(completed_path):
        with open(completed_path, "r", encoding="utf-8") as f:
            completed_queries = set(line.strip() for line in f if line.strip())
    else:
        completed_queries = set()

    for qi, query in enumerate(queries, 1):
        if query in completed_queries:
            print(f"Skipping already-completed query: {query}")
            continue

        print(f"\n--- Query: {query} ---")
        if progress:
            progress(f"search {qi}/{len(queries)}: \"{query}\" · {len(records)} papers so far")
        query_succeeded = False
        max_query_attempts = 2

        for attempt in range(1, max_query_attempts + 1):
            try:
                search = arxiv.Search(
                    query=query,
                    max_results=max_per_query,
                    sort_by=arxiv.SortCriterion.Relevance,
                )
                results = [r for r in client.results(search) if r.get_short_id() not in seen_ids]
                scores = (relevance_scores(topic_text, [(r.title, r.summary) for r in results])
                          if topic_text else [None] * len(results))

                for result, score in zip(results, scores):
                    arxiv_id = result.get_short_id()
                    if arxiv_id in seen_ids:
                        continue

                    if score is not None and score < relevance_threshold:
                        print(f"Skipping (low relevance {score:.2f}): {arxiv_id} — {result.title}")
                        continue

                    seen_ids.add(arxiv_id)

                    filepath = os.path.join(pdf_dir, f"{arxiv_id}.pdf")
                    urllib.request.urlretrieve(result.pdf_url, filepath)

                    records.append({
                        "arxiv_id": arxiv_id,
                        "title": result.title,
                        "authors": ", ".join(a.name for a in result.authors),
                        "published": result.published.strftime("%Y-%m-%d"),
                        "abstract": result.summary.replace("\n", " "),
                        "pdf_path": project_relative(filepath),
                        "domain": domain,
                        "source_query": query,
                    })

                    print(f"Downloaded: {arxiv_id} - {result.title}")
                    pd.DataFrame(records).to_csv(metadata_path, index=False)
                    if progress:
                        progress(f"search {qi}/{len(queries)}: \"{query}\" · {len(records)} papers so far")

                query_succeeded = True
                break

            except Exception as e:
                print(f"Attempt {attempt}/{max_query_attempts} failed: {e}")
                if attempt < max_query_attempts:
                    time.sleep(20 * attempt)

        if query_succeeded:
            completed_queries.add(query)
            with open(completed_path, "w", encoding="utf-8") as f:
                f.write("\n".join(completed_queries))
        else:
            print(f"'{query}' failed - will retry on the next run.")

        time.sleep(10)

    print(f"\n[{domain}] Done. {len(records)} total papers in {metadata_path}")
    return len(records)

if __name__ == "__main__":
    from config_loader import load_domain_config

    domain = sys.argv[1] if len(sys.argv) > 1 else "efficient_attention"
    config = load_domain_config(domain)

    fetch_papers(
        domain,
        config["arxiv_queries"],
        config.get("max_per_query", 6),
        config.get("topic_text"),
        config.get("relevance_threshold", RELEVANCE_THRESHOLD),
    )
