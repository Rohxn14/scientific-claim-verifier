"""API tests with retrieval and the LLM stubbed out - no network, no models."""
import json

import pytest
from fastapi.testclient import TestClient

import api
import ingestion


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(api, "grobid_is_alive", lambda timeout=2: False)
    # Not used as a context manager, so the lifespan (job restore, model
    # warm-up) doesn't run.
    return TestClient(api.app)


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(ingestion, "is_ready", lambda domain: domain == "demo")


SOURCE = {"index": 1, "paper_id": "2205.14135v2", "paper_title": "FlashAttention", "year": "2022",
          "section": "Abstract", "section_type": "abstract", "similarity": 0.81, "via": "semantic",
          "linked_from": None, "text": "FlashAttention is IO-aware.", "url": "https://arxiv.org/abs/2205.14135v2"}
VERIFICATION = {
    "claims": [{"text": "It is IO-aware.", "sources": [1], "status": "supported",
                "citations": [{"source": 1, "status": "supported", "lexical": 0.9, "semantic": 0.9,
                               "evidence": "FlashAttention is IO-aware.", "missing_numbers": []}]}],
    "markers": [{"claim": 0, "sources": [1]}],
    "summary": {"claims": 1, "claims_supported": 1, "claims_partial": 0, "claims_unsupported": 0,
                "citations": 1, "supported": 1, "partial": 0, "unsupported": 0, "invalid": 0,
                "uncited_sentences": 0, "score": 1.0, "declined": False, "verdict": "well_supported"},
}


def test_health_reports_configuration(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["grobid"] is False
    assert body["auth_required"] is False


def test_root_redirects_to_ui(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/ui/"


def test_ui_is_served(client):
    r = client.get("/ui/")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()


@pytest.mark.parametrize("path", ["/domains/..a", "/domains/a..b", "/domains/A_B", "/domains/x"])
def test_invalid_collection_ids_never_reach_the_filesystem(client, monkeypatch, path):
    def boom(_):
        raise AssertionError("delete_domain must not be called")
    monkeypatch.setattr(ingestion, "delete_domain", boom)
    assert client.delete(path).status_code == 404


@pytest.mark.parametrize("name", ["..", ".", "../data", "a/b"])
def test_dot_segments_are_rejected(name):
    # httpx normalises "/domains/.." before sending, so check the guard directly
    # (a raw "DELETE /domains/.." used to rmtree the whole data/ directory).
    with pytest.raises(api.HTTPException) as exc:
        api.domain_id(name)
    assert exc.value.status_code == 404


def test_query_rejects_out_of_range_and_unknown_model(client, ready):
    base = {"domain": "demo", "question": "why?"}
    assert client.post("/query", json={**base, "top_k": 500}).status_code == 422
    assert client.post("/query", json={**base, "model": "gpt-9"}).status_code == 422
    assert client.post("/query", json={**base, "question": "   "}).status_code == 422
    assert client.post("/query", json={**base, "history": [{"role": "system", "text": "x"}]}).status_code == 422


def test_query_unknown_collection_is_404(client, ready):
    assert client.post("/query", json={"domain": "missing", "question": "why?"}).status_code == 404


def test_query_returns_answer_sources_and_verification(client, ready, monkeypatch):
    def fake_answer(domain, question, top_k, model_choice, history):
        assert (domain, question, top_k) == ("demo", "why?", 5)
        return {"answer": "It is IO-aware [Source 1].", "provider": "groq", "model": "m",
                "search_query": question, "sources": [SOURCE], "verification": VERIFICATION,
                "timings": {"retrieval_ms": 1, "generation_ms": 2, "verification_ms": 3, "total_ms": 6},
                "hits": [object()]}
    monkeypatch.setattr(api, "answer", fake_answer)

    body = client.post("/query", json={"domain": "demo", "question": "why?"}).json()
    assert body["sources"][0]["url"].startswith("https://arxiv.org/abs/")
    assert body["verification"]["summary"]["verdict"] == "well_supported"
    assert "hits" not in body


def test_query_generation_failure_is_502(client, ready, monkeypatch):
    def failing(*args, **kwargs):
        raise RuntimeError("quota exhausted")
    monkeypatch.setattr(api, "answer", failing)
    r = client.post("/query", json={"domain": "demo", "question": "why?"})
    assert r.status_code == 502
    assert "quota exhausted" in r.json()["detail"]


def _events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_stream_emits_events_in_order(client, ready, monkeypatch):
    def fake_stream(domain, question, top_k, model_choice, history):
        yield {"type": "status", "stage": "retrieving"}
        yield {"type": "sources", "sources": [SOURCE], "search_query": question}
        yield {"type": "token", "text": "It is "}
        yield {"type": "token", "text": "IO-aware [Source 1]."}
        yield {"type": "done", "answer": "It is IO-aware [Source 1].", "verification": VERIFICATION}
    monkeypatch.setattr(api, "stream_answer", fake_stream)

    r = client.post("/query/stream", json={"domain": "demo", "question": "why?"})
    assert r.headers["content-type"].startswith("text/event-stream")
    kinds = [kind for kind, _ in _events(r.text)]
    assert kinds == ["status", "sources", "token", "token", "done"]


def test_stream_reports_failures_as_error_event(client, ready, monkeypatch):
    def broken(*args, **kwargs):
        yield {"type": "status", "stage": "retrieving"}
        raise RuntimeError("provider down")
    monkeypatch.setattr(api, "stream_answer", broken)
    kind, data = _events(client.post("/query/stream", json={"domain": "demo", "question": "q"}).text)[-1]
    assert kind == "error"
    assert "provider down" in data["detail"]


def test_admin_endpoints_require_key_when_configured(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    payload = {"domain": "new_topic", "arxiv_queries": ["graph transformers"]}
    assert client.post("/domains/request", json=payload).status_code == 401
    assert client.post("/domains/request", json=payload, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/health").json()["auth_required"] is True


def test_cannot_delete_collection_while_it_is_building(client, monkeypatch):
    monkeypatch.setitem(ingestion.JOB_STATUS, "building_now", {"state": "parsing", "detail": ""})
    r = client.delete("/domains/building_now")
    assert r.status_code == 409


def test_upload_rejects_non_pdf_before_writing(client, monkeypatch):
    monkeypatch.setattr(ingestion, "request_blocker", lambda domain, allow_append=False: None)
    r = client.post("/domains/upload", data={"domain": "my_uploads"},
                    files=[("files", ("notes.pdf", b"not really a pdf", "application/pdf"))])
    assert r.status_code == 400
    assert "not a PDF" in r.json()["detail"]


def test_request_rejects_bad_collection_id(client):
    r = client.post("/domains/request", json={"domain": "../evil", "arxiv_queries": ["x y"]})
    assert r.status_code == 422


def test_rate_limit_errors_get_a_readable_message(client, ready, monkeypatch):
    def limited(*args, **kwargs):
        raise RuntimeError("All providers failed. Last error: Error code: 429 - {'error': 'Rate limit reached'}")
    monkeypatch.setattr(api, "answer", limited)
    detail = client.post("/query", json={"domain": "demo", "question": "q"}).json()["detail"]
    assert "rate-limited" in detail
    assert "{" not in detail
