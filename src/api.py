"""HTTP API and web UI for the Scientific Claim Verifier.

Run from the src/ directory:
    uvicorn api:app --reload
then open http://localhost:8000 (UI) or http://localhost:8000/docs (API).
"""
import hashlib
import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from typing import List, Literal, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import ingestion
from auth import auth_required, require_api_key
from config_loader import list_domains, load_domain_config
from generation import AVAILABLE_MODELS, configured_providers, fast_model_choice, generate, model_catalog
from generate_answer import answer, stream_answer
from parse_papers import grobid_is_alive
from paths import PARSED_DIR, RAW_PDFS_DIR
from ratelimit import RateLimiter

__version__ = "1.0.0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scv.api")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Domain names become file names and Chroma collection names, so they must be
# tightly validated (this also blocks path traversal like "../../x").
DOMAIN_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,46}[a-z0-9]$")
DOMAIN_RULE = "collection id must be 3-48 chars: lowercase letters, digits, '_' or '-'"
PAPER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")

MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

query_limiter = RateLimiter(int(os.environ.get("RATE_LIMIT_PER_MINUTE", "20")))
admin_limiter = RateLimiter(int(os.environ.get("ADMIN_RATE_LIMIT_PER_MINUTE", "30")))


def _warm_up():
    """Load the embedding model in the background so the first question
    doesn't pay for it."""
    try:
        from embeddings import embed_query
        embed_query("warm up")
        log.info("embedding model ready")
    except Exception as e:
        log.warning("embedding warm-up failed: %s", e)


@asynccontextmanager
async def lifespan(app):
    ingestion.load_jobs()
    threading.Thread(target=_warm_up, daemon=True).start()
    if not configured_providers():
        log.warning("No LLM provider configured - set GROQ_API_KEY and/or GEMINI_API_KEY")
    yield


app = FastAPI(
    title="Scientific Claim Verifier API",
    version=__version__,
    description="Ask questions about a collection of research papers; every cited claim "
                "in the answer is checked against the passage it cites.",
    lifespan=lifespan,
)

_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    # Reject oversized uploads from the Content-Length header, before the
    # multipart body is spooled to disk.
    if request.url.path == "/domains/upload":
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_UPLOAD_FILES * MAX_UPLOAD_BYTES + 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Upload too large"})
    return await call_next(request)


app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/")


def domain_id(domain: str) -> str:
    if not DOMAIN_NAME.match(domain):
        raise HTTPException(status_code=404, detail=f"No collection named '{domain}'")
    return domain


def _require_ready(domain: str):
    if not DOMAIN_NAME.match(domain) or not ingestion.is_ready(domain):
        raise HTTPException(
            status_code=404,
            detail=f"Collection '{domain}' is unknown or not ready (see /domains/{domain}/status)",
        )


def _require_idle(domain: str):
    job = ingestion.JOB_STATUS.get(domain)
    if job and job["state"] in ingestion.ACTIVE_STATES:
        raise HTTPException(status_code=409, detail=f"Collection '{domain}' is being built - wait for it to finish")


def _error_text(e: Exception) -> str:
    text = str(e)
    if "429" in text or "rate limit" in text.lower() or "RESOURCE_EXHAUSTED" in text:
        return ("The language model is rate-limited right now (free-tier quota). "
                "Wait a minute and try again, or pick another model.")
    if "503" in text or "UNAVAILABLE" in text or "overloaded" in text.lower():
        return "The language model provider is overloaded right now. Try again in a moment, or pick another model."
    return f"Answer generation failed: {text[:300]}"


# ------------------------------------------------------------------ status

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": __version__,
        "providers": configured_providers(),
        "grobid": grobid_is_alive(timeout=2),
        "auth_required": auth_required(),
    }


@app.get("/models")
def get_models():
    return model_catalog()


# ------------------------------------------------------------------ query

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=20_000)


class QueryRequest(BaseModel):
    domain: str
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=12)
    model: str = "auto"
    history: List[ChatMessage] = Field(default_factory=list, max_length=20)

    @field_validator("question")
    @classmethod
    def non_blank(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("question must not be empty")
        return v

    @field_validator("model")
    @classmethod
    def known_model(cls, v):
        if v not in AVAILABLE_MODELS:
            raise ValueError(f"model must be one of: {', '.join(AVAILABLE_MODELS)}")
        return v


class Source(BaseModel):
    index: int
    paper_id: str
    paper_title: str
    year: Optional[str]
    section: str
    section_type: str
    similarity: float
    via: Literal["semantic", "citation"]
    linked_from: Optional[str]
    text: str
    url: Optional[str]


class CitationCheck(BaseModel):
    source: int
    status: Literal["supported", "partial", "unsupported", "invalid"]
    lexical: float
    semantic: Optional[float]
    evidence: Optional[str]
    missing_numbers: List[str]


class Claim(BaseModel):
    text: str
    sources: List[int]
    status: Literal["supported", "partial", "unsupported", "invalid"]
    citations: List[CitationCheck]


class Marker(BaseModel):
    claim: Optional[int]
    sources: List[int]


class VerificationSummary(BaseModel):
    claims: int
    claims_supported: int
    claims_partial: int
    claims_unsupported: int
    citations: int
    supported: int
    partial: int
    unsupported: int
    invalid: int
    uncited_sentences: int
    score: Optional[float]
    declined: bool
    verdict: Literal["well_supported", "mostly_supported", "weakly_supported", "no_citations", "not_covered"]


class Verification(BaseModel):
    claims: List[Claim]
    markers: List[Marker]
    summary: VerificationSummary


class Timings(BaseModel):
    retrieval_ms: int
    generation_ms: int
    verification_ms: int
    total_ms: int


class QueryResponse(BaseModel):
    answer: str
    provider: str
    model: str
    search_query: str
    sources: List[Source]
    verification: Verification
    timings: Timings


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(query_limiter)])
def query(req: QueryRequest):
    """Answer a question from a collection, with sources and citation checks."""
    _require_ready(req.domain)
    try:
        result = answer(req.domain, req.question, top_k=req.top_k, model_choice=req.model,
                        history=[m.model_dump() for m in req.history])
    except Exception as e:
        log.exception("query failed")
        raise HTTPException(status_code=502, detail=_error_text(e))
    result.pop("hits")
    return result


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/query/stream", dependencies=[Depends(query_limiter)])
def query_stream(req: QueryRequest):
    """Same as /query, streamed as server-sent events: `status` (pipeline stage),
    `sources`, `token` (answer text deltas), then `done` (full answer +
    verification) or `error`."""
    _require_ready(req.domain)

    def events():
        try:
            for event in stream_answer(req.domain, req.question, top_k=req.top_k, model_choice=req.model,
                                       history=[m.model_dump() for m in req.history]):
                yield _sse(event)
        except Exception as e:
            log.exception("streamed query failed")
            yield _sse({"type": "error", "detail": _error_text(e)})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------ collections

@app.get("/domains")
def get_domains():
    """Collections that are ready to query."""
    return ingestion.ready_domains()


@app.get("/jobs")
def get_jobs():
    """Collections that are being built, or whose build failed."""
    return ingestion.list_jobs()


@app.get("/domains/{domain}")
def get_domain(domain: str = Depends(domain_id)):
    if domain not in list_domains() and domain not in ingestion.JOB_STATUS:
        raise HTTPException(status_code=404, detail=f"No collection named '{domain}'")
    return ingestion.domain_detail(domain)


@app.get("/domains/{domain}/status")
def domain_status(domain: str = Depends(domain_id)):
    if domain in ingestion.JOB_STATUS:
        return {"domain": domain, **ingestion.JOB_STATUS[domain]}
    if ingestion.is_ready(domain):
        return {"domain": domain, "state": "ready", "detail": "pre-built"}
    raise HTTPException(status_code=404, detail=f"No collection or job named '{domain}'")


class DomainUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=80)
    description: Optional[str] = Field(None, max_length=300)
    example_questions: Optional[List[str]] = Field(None, max_length=8)


@app.patch("/domains/{domain}", dependencies=[Depends(require_api_key)])
def update_domain(update: DomainUpdate, domain: str = Depends(domain_id)):
    if domain not in list_domains():
        raise HTTPException(status_code=404, detail=f"No collection named '{domain}'")
    fields = update.model_dump(exclude_none=True)
    for key in ("display_name", "description"):
        if key in fields:
            fields[key] = fields[key].strip()
    if "example_questions" in fields:
        fields["example_questions"] = [q.strip()[:200] for q in fields["example_questions"] if q.strip()]
    ingestion.update_domain_config(domain, **fields)
    return ingestion.domain_detail(domain)


@app.delete("/domains/{domain}", dependencies=[Depends(require_api_key)])
def delete_domain_route(domain: str = Depends(domain_id)):
    _require_idle(domain)
    if domain not in list_domains() and domain not in ingestion.JOB_STATUS:
        raise HTTPException(status_code=404, detail=f"No collection named '{domain}'")
    ingestion.delete_domain(domain)
    return {"domain": domain, "status": "deleted"}


@app.post("/domains/{domain}/retry", dependencies=[Depends(require_api_key)])
def retry_domain(background_tasks: BackgroundTasks, domain: str = Depends(domain_id)):
    """Re-run a failed or interrupted build. Every stage resumes where it stopped."""
    _require_idle(domain)
    try:
        config = load_domain_config(domain)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No collection named '{domain}'")
    ingestion.queue_job(domain, "retrying")
    background_tasks.add_task(ingestion.run_ingestion, domain, config)
    return {"domain": domain, "status": "queued"}


@app.delete("/domains/{domain}/papers/{paper_id}", dependencies=[Depends(require_api_key)])
def remove_paper(paper_id: str, domain: str = Depends(domain_id)):
    if not PAPER_ID.match(paper_id):
        raise HTTPException(status_code=404, detail="No such paper")
    _require_idle(domain)
    try:
        ingestion.remove_paper(domain, paper_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No paper '{paper_id}' in '{domain}'")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ingestion.domain_detail(domain)


@app.get("/domains/{domain}/papers/{paper_id}/pdf")
def paper_pdf(paper_id: str, domain: str = Depends(domain_id)):
    path = ingestion.paper_pdf_path(domain, paper_id) if PAPER_ID.match(paper_id) else None
    if not path:
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{paper_id}.pdf"'})


class DomainRequest(BaseModel):
    domain: str
    arxiv_queries: list[str]
    max_per_query: int = 6
    display_name: str = Field("", max_length=80)
    description: str = Field("", max_length=300)
    topic: str = Field("", max_length=500)

    @field_validator("domain")
    @classmethod
    def valid_domain(cls, v):
        if not DOMAIN_NAME.match(v):
            raise ValueError(DOMAIN_RULE)
        return v

    @field_validator("arxiv_queries")
    @classmethod
    def valid_queries(cls, v):
        v = [q.strip() for q in v if q.strip()]
        if not 1 <= len(v) <= 8:
            raise ValueError("provide between 1 and 8 non-empty queries")
        if any(len(q) > 200 for q in v):
            raise ValueError("each query must be 200 characters or fewer")
        return v

    @field_validator("max_per_query")
    @classmethod
    def valid_max(cls, v):
        if not 1 <= v <= 10:
            raise ValueError("max_per_query must be between 1 and 10")
        return v


@app.post("/domains/request", dependencies=[Depends(require_api_key)])
def request_domain(req: DomainRequest, background_tasks: BackgroundTasks):
    """Build a new collection from arXiv search queries (runs in the background)."""
    blocker = ingestion.request_blocker(req.domain)
    if blocker:
        raise HTTPException(status_code=409, detail=f"Collection '{req.domain}' {blocker}")

    display_name = req.display_name.strip() or ingestion.display_name_for(req.domain, {})
    # The relevance filter compares each candidate paper with this text, and
    # works far better with a descriptive sentence than a two-word topic.
    topic_text = ". ".join(filter(None, [req.topic.strip() or display_name, req.description.strip()]))
    if len(topic_text.split()) < 6:
        topic_text = "; ".join([topic_text] + req.arxiv_queries)

    config = {
        "name": req.domain,
        "display_name": display_name,
        "description": req.description.strip(),
        "source": "arxiv",
        "arxiv_queries": req.arxiv_queries,
        "max_per_query": req.max_per_query,
        "topic_text": topic_text,
    }
    ingestion.save_domain_config(req.domain, config)
    ingestion.queue_job(req.domain)
    background_tasks.add_task(ingestion.run_ingestion, req.domain, config)
    return {"domain": req.domain, "status": "queued"}


@app.post("/domains/upload", dependencies=[Depends(require_api_key)])
async def upload_domain(
    background_tasks: BackgroundTasks,
    domain: str = Form(...),
    files: List[UploadFile] = File(...),
    display_name: str = Form(""),
    description: str = Form(""),
):
    """Build a collection from uploaded PDFs, or add PDFs to an existing one."""
    if not DOMAIN_NAME.match(domain):
        raise HTTPException(status_code=400, detail=DOMAIN_RULE)
    blocker = ingestion.request_blocker(domain, allow_append=True)
    if blocker:
        raise HTTPException(status_code=409, detail=f"Collection '{domain}' {blocker}")
    if not 1 <= len(files) <= MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"upload between 1 and {MAX_UPLOAD_FILES} PDF files")

    # Validate every file before writing anything to disk.
    validated = {}   # doc_id -> (bytes, display name); a dict also drops duplicate files
    for f in files:
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"'{f.filename}' exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        if not data.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail=f"'{f.filename}' is not a PDF file")
        doc_id = "up_" + hashlib.sha256(data).hexdigest()[:12]
        display = os.path.splitext(os.path.basename(f.filename or "document"))[0][:120]
        validated[doc_id] = (data, display)

    pdf_dir = os.path.join(RAW_PDFS_DIR, domain)
    os.makedirs(pdf_dir, exist_ok=True)
    os.makedirs(PARSED_DIR, exist_ok=True)
    for doc_id, (data, _) in validated.items():
        with open(os.path.join(pdf_dir, f"{doc_id}.pdf"), "wb") as out:
            out.write(data)

    # Merge with names from earlier uploads to this collection.
    names_path = os.path.join(PARSED_DIR, f"{domain}_upload_names.json")
    names = {}
    if os.path.exists(names_path):
        with open(names_path, encoding="utf-8") as f:
            names = json.load(f)
    names.update({doc_id: name for doc_id, (_, name) in validated.items()})
    with open(names_path, "w", encoding="utf-8") as out:
        json.dump(names, out)

    # Adding to an existing collection keeps its config (an arXiv collection
    # stays one); only this run uses the upload fetcher.
    config = ingestion.config_or_empty(domain) or {"name": domain, "source": "upload"}
    if display_name.strip():
        config["display_name"] = display_name.strip()[:80]
    if description.strip():
        config["description"] = description.strip()[:300]
    config.setdefault("display_name", ingestion.display_name_for(domain, {}))
    ingestion.save_domain_config(domain, config)

    ingestion.queue_job(domain, f"{len(validated)} files received")
    background_tasks.add_task(ingestion.run_ingestion, domain, {**config, "source": "upload"})
    return {"domain": domain, "status": "queued", "files_received": len(validated)}


class TopicRequest(BaseModel):
    topic: str
    details: str = Field("", max_length=500)

    @field_validator("topic")
    @classmethod
    def valid_topic(cls, v):
        v = v.strip()
        if not 3 <= len(v) <= 200:
            raise ValueError("topic must be 3-200 characters")
        return v


@app.post("/domains/suggest-queries", dependencies=[Depends(require_api_key), Depends(admin_limiter)])
def suggest_queries(req: TopicRequest):
    """Turn a plain-language topic into arXiv search queries and a collection id."""
    prompt = f"""A user wants to research scientific papers about: "{req.topic}"
{f'Additional context from the user: {req.details}' if req.details else ''}

Suggest 3-5 short, specific arXiv search queries (2-6 words each) that together
would find a good range of relevant papers on this topic. Return ONLY a JSON
array of strings, nothing else. Example: ["query one", "query two"]"""

    try:
        text, _ = generate(prompt, model_choice=fast_model_choice())
        match = re.search(r"\[.*\]", text, re.DOTALL)
        queries = json.loads(match.group()) if match else []
        queries = [q.strip() for q in queries if isinstance(q, str) and q.strip()][:8]
    except Exception:
        queries = []
    if not queries:
        queries = [req.topic]  # fall back to the raw topic if generation or parsing fails

    slug = re.sub(r"[^a-z0-9]+", "_", req.topic.lower()).strip("_")[:40].strip("_")
    if len(slug) < 3:
        slug = (slug + "_papers").strip("_")
    taken = set(list_domains())
    base, n = slug, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1

    return {
        "suggested_domain_slug": slug,
        "suggested_display_name": req.topic[:1].upper() + req.topic[1:80],
        "suggested_queries": queries,
    }
