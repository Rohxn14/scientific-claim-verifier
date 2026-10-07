class DocumentFetcher:
    def fetch(self, domain: str, config: dict, progress=None) -> int:
        """Fetches documents for a domain, saves metadata CSV + PDFs to disk
        (same layout fetch_papers.py already produces). Returns paper count.
        `progress`, if given, is called with short human-readable updates."""
        raise NotImplementedError
