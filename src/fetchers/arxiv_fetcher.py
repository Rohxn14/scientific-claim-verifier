import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from fetch_papers import fetch_papers, RELEVANCE_THRESHOLD
from .base import DocumentFetcher

class ArxivFetcher(DocumentFetcher):
    def fetch(self, domain: str, config: dict, progress=None) -> int:
        return fetch_papers(
            domain,
            config["arxiv_queries"],
            config.get("max_per_query", 6),
            topic_text=config.get("topic_text"),
            relevance_threshold=config.get("relevance_threshold", RELEVANCE_THRESHOLD),
            progress=progress,
        )
