"""One shared embedding model for indexing, search, relevance filtering and
citation verification. Loading it is the slowest part of startup, so it is
loaded once, lazily, and reused by every caller in the process."""
import threading

MODEL_NAME = "BAAI/bge-small-en-v1.5"

# bge models expect this prefix on queries but NOT on documents.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_model = None
_lock = threading.Lock()


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                # Imported here so modules that only need the constants above
                # (and the test suite) don't pay for loading torch.
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_query(text: str):
    return get_model().encode(QUERY_PREFIX + text, normalize_embeddings=True)


def embed_documents(texts: list, batch_size: int = 64, show_progress: bool = False):
    return get_model().encode(texts, batch_size=batch_size,
                              show_progress_bar=show_progress,
                              normalize_embeddings=True)
