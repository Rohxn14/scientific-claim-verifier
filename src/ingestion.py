"""Builds a collection ("domain") end to end as a background job - fetch ->
parse -> chunk -> index -> link citations - and manages collections on disk."""
import json
import logging
import os
import re
import shutil
import threading
import time

import chromadb
import pandas as pd
import yaml

from paths import CONFIG_DOMAINS_DIR, CHROMA_DIR, RAW_PDFS_DIR, PARSED_DIR, DATA_DIR
from config_loader import list_domains, load_domain_config
from fetchers import FETCHERS
from parse_papers import parse_papers, grobid_is_alive, enrich_metadata_from_tei
from extract_sections import extract_sections
from build_index import build_index
from build_citation_graph import build_citation_graph

log = logging.getLogger(__name__)

# Job status is kept in memory and mirrored to data/jobs.json, so it survives
# a restart. Jobs that were mid-flight when the process died are marked
# failed on startup (see load_jobs); every stage is resumable, so a retry
# picks up where the job stopped.
JOB_STATUS = {}
JOBS_PATH = os.path.join(DATA_DIR, "jobs.json")
ACTIVE_STATES = {"queued", "fetching", "parsing", "chunking", "indexing", "linking citations"}

# GROBID and the embedding step are CPU-heavy, so only one ingestion runs at
# a time; other requests wait in the "queued" state.
_INGEST_LOCK = threading.Lock()
_JOBS_FILE_LOCK = threading.Lock()


def _save_jobs():
    with _JOBS_FILE_LOCK:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = JOBS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(JOB_STATUS, f, indent=1)
        os.replace(tmp, JOBS_PATH)


def load_jobs():
    """Restore job state at startup."""
    try:
        with open(JOBS_PATH, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    for domain, job in saved.items():
        if job.get("state") in ACTIVE_STATES:
            job = {**job, "state": "failed", "failed_stage": job["state"],
                   "detail": "Interrupted by a server restart - retry to resume where it stopped"}
        JOB_STATUS[domain] = job
    _save_jobs()


def _set(domain: str, state: str, detail: str = ""):
    now = time.time()
    previous = JOB_STATUS.get(domain, {})
    job = {
        "state": state,
        "detail": detail,
        "started_at": now if state == "queued" else previous.get("started_at", now),
        "updated_at": now,
    }
    if state == "failed":
        # Remember where it stopped, so the UI can show which step broke.
        job["failed_stage"] = previous.get("state") if previous.get("state") in ACTIVE_STATES else previous.get("failed_stage")
    JOB_STATUS[domain] = job
    _save_jobs()


def queue_job(domain: str, detail: str = ""):
    _set(domain, "queued", detail)


def _client():
    return chromadb.PersistentClient(path=CHROMA_DIR)


def _chroma_collection_names() -> set:
    # Older Chroma returns Collection objects, newer returns plain names.
    return {getattr(c, "name", c) for c in _client().list_collections()}


def _paths(domain: str) -> dict:
    return {
        "metadata": os.path.join(PARSED_DIR, f"{domain}_metadata.csv"),
        "chunks": os.path.join(PARSED_DIR, f"{domain}_chunks.jsonl"),
        "references": os.path.join(PARSED_DIR, f"{domain}_references.jsonl"),
        "graph": os.path.join(PARSED_DIR, f"{domain}_citation_graph.jsonl"),
        "upload_names": os.path.join(PARSED_DIR, f"{domain}_upload_names.json"),
        "completed_queries": os.path.join(PARSED_DIR, f"{domain}_completed_queries.txt"),
        "pdf_dir": os.path.join(RAW_PDFS_DIR, domain),
        "tei_dir": os.path.join(PARSED_DIR, domain, "tei"),
        "config": os.path.join(CONFIG_DOMAINS_DIR, f"{domain}.yaml"),
    }


def delete_domain(domain: str):
    if domain in _chroma_collection_names():
        _client().delete_collection(domain)

    p = _paths(domain)
    for path in [p["pdf_dir"], os.path.join(PARSED_DIR, domain)]:
        if os.path.isdir(path):
            shutil.rmtree(path)
    for key in ("metadata", "chunks", "references", "graph", "completed_queries", "upload_names", "config"):
        if os.path.exists(p[key]):
            os.remove(p[key])

    JOB_STATUS.pop(domain, None)
    _save_jobs()


# ---------------------------------------------------------------- listing

def display_name_for(domain: str, cfg: dict) -> str:
    return cfg.get("display_name") or domain.replace("_", " ").replace("-", " ").title()


def config_or_empty(domain: str) -> dict:
    try:
        return load_domain_config(domain)
    except FileNotFoundError:
        return {}


def _paper_rows(domain: str) -> list:
    path = _paths(domain)["metadata"]
    if not os.path.exists(path):
        return []
    return pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records")


def _count_lines(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _summary(domain: str, cfg: dict, client=None) -> dict:
    p = _paths(domain)
    try:
        chunks = (client or _client()).get_collection(domain).count()
    except Exception:
        chunks = 0
    updated = os.path.getmtime(p["metadata"]) if os.path.exists(p["metadata"]) else None
    return {
        "id": domain,
        "display_name": display_name_for(domain, cfg),
        "description": cfg.get("description", ""),
        "source": cfg.get("source", "arxiv"),
        "papers": len(_paper_rows(domain)),
        "chunks": chunks,
        "citation_links": _count_lines(p["graph"]),
        "example_questions": cfg.get("example_questions", [])[:6],
        "updated_at": updated,
    }


def is_ready(domain: str) -> bool:
    job = JOB_STATUS.get(domain)
    if job and job["state"] != "ready":
        return False
    return domain in list_domains() and domain in _chroma_collection_names()


def ready_domains() -> list:
    """Domains that have a config AND a built index AND no unfinished job."""
    client = _client()
    names = {getattr(c, "name", c) for c in client.list_collections()}
    ready = []
    for d in sorted(list_domains()):
        job = JOB_STATUS.get(d)
        if job and job["state"] != "ready":
            continue
        if d in names:
            ready.append(_summary(d, config_or_empty(d), client))
    return ready


def list_jobs() -> list:
    """Collections that are being built or whose build failed."""
    jobs = []
    for domain, job in JOB_STATUS.items():
        if job["state"] == "ready":
            continue
        cfg = config_or_empty(domain)
        jobs.append({"domain": domain, "display_name": display_name_for(domain, cfg),
                     "source": cfg.get("source", "arxiv"), **job})
    return sorted(jobs, key=lambda j: -j.get("updated_at", 0))


_relevance_cache = {}  # domain -> (metadata mtime, topic, {paper_id: score})


def _paper_relevance(domain: str, cfg: dict, rows: list) -> dict:
    """How well each paper matches the collection's topic - helps spot papers
    a broad arXiv query pulled in by accident. Cached until metadata changes."""
    if not (cfg.get("description") or cfg.get("topic_text")) or not rows:
        return {}   # a bare name ("My uploads") says nothing about the topic
    topic = ". ".join(filter(None, [cfg.get("display_name"), cfg.get("description"),
                                    cfg.get("topic_text")]))
    mtime = os.path.getmtime(_paths(domain)["metadata"])
    cached = _relevance_cache.get(domain)
    if cached and cached[0] == mtime and cached[1] == topic:
        return cached[2]
    from fetch_papers import relevance_scores
    scores = relevance_scores(topic, [(r.get("title", ""), r.get("abstract", "")) for r in rows])
    result = {r["arxiv_id"]: round(s, 3) for r, s in zip(rows, scores)}
    _relevance_cache[domain] = (mtime, topic, result)
    return result


def domain_detail(domain: str) -> dict:
    cfg = config_or_empty(domain)
    rows = _paper_rows(domain)
    detail = _summary(domain, cfg) if domain in _chroma_collection_names() else {
        "id": domain, "display_name": display_name_for(domain, cfg),
        "description": cfg.get("description", ""), "source": cfg.get("source", "arxiv"),
        "papers": len(rows), "chunks": 0, "citation_links": 0,
        "example_questions": cfg.get("example_questions", []), "updated_at": None,
    }

    chunk_counts = {}
    chunks_path = _paths(domain)["chunks"]
    if os.path.exists(chunks_path):
        with open(chunks_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    pid = json.loads(line)["arxiv_id"]
                    chunk_counts[pid] = chunk_counts.get(pid, 0) + 1

    try:
        relevance = _paper_relevance(domain, cfg, rows)
    except Exception as e:  # never let a scoring hiccup break the detail view
        log.warning("relevance scoring failed for %s: %s", domain, e)
        relevance = {}

    papers = []
    for r in rows:
        pid = r["arxiv_id"]
        published = r.get("published", "")
        papers.append({
            "id": pid,
            "title": r.get("title") or pid,
            "authors": r.get("authors", ""),
            "year": published[:4] if published[:4].isdigit() else None,
            "source": "upload" if r.get("source_query") == "upload" else "arxiv",
            "url": None if pid.startswith("up_") else f"https://arxiv.org/abs/{pid}",
            "chunks": chunk_counts.get(pid, 0),
            "relevance": relevance.get(pid),
        })

    detail.update({
        "papers_list": papers,
        "queries": cfg.get("arxiv_queries", []),
        "job": JOB_STATUS.get(domain),
    })
    return detail


# ------------------------------------------------------------ management

def request_blocker(domain: str, allow_append: bool = False):
    job = JOB_STATUS.get(domain)
    if job and job["state"] in ACTIVE_STATES:
        return "is already being prepared"
    if not allow_append and domain in _chroma_collection_names() and (not job or job["state"] == "ready"):
        return "already exists"
    return None


def save_domain_config(domain: str, config: dict):
    os.makedirs(CONFIG_DOMAINS_DIR, exist_ok=True)
    with open(os.path.join(CONFIG_DOMAINS_DIR, f"{domain}.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)


def update_domain_config(domain: str, **fields) -> dict:
    cfg = config_or_empty(domain) or {"name": domain}
    for key, value in fields.items():
        if value is not None:
            cfg[key] = value
    save_domain_config(domain, cfg)
    return cfg


def _rewrite_jsonl(path: str, keep):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            if keep(item):
                f.write(json.dumps(item, ensure_ascii=False) + "\n")


def remove_paper(domain: str, paper_id: str):
    """Remove one paper from a collection: its chunks in the index, its rows in
    every pipeline file, its citation edges, and its PDF/TEI on disk."""
    p = _paths(domain)
    rows = _paper_rows(domain)
    if paper_id not in {r["arxiv_id"] for r in rows}:
        raise KeyError(paper_id)
    if len(rows) <= 1:
        raise ValueError("this is the collection's only paper - delete the collection instead")

    # Callers check request_blocker() first: this must not run while the same
    # collection is being built, since both rewrite the same files.
    if domain in _chroma_collection_names():
        _client().get_collection(domain).delete(where={"arxiv_id": paper_id})
    pd.DataFrame([r for r in rows if r["arxiv_id"] != paper_id]).to_csv(p["metadata"], index=False)
    _rewrite_jsonl(p["chunks"], lambda c: c.get("arxiv_id") != paper_id)
    _rewrite_jsonl(p["references"], lambda r: r.get("arxiv_id") != paper_id)
    _rewrite_jsonl(p["graph"], lambda e: paper_id not in (e.get("citing_id"), e.get("cited_id")))
    for path in (os.path.join(p["pdf_dir"], f"{paper_id}.pdf"),
                 os.path.join(p["tei_dir"], f"{paper_id}.tei.xml")):
        if os.path.exists(path):
            os.remove(path)
    if os.path.exists(p["upload_names"]):
        with open(p["upload_names"], encoding="utf-8") as f:
            names = json.load(f)
        names.pop(paper_id, None)
        with open(p["upload_names"], "w", encoding="utf-8") as f:
            json.dump(names, f)


def paper_pdf_path(domain: str, paper_id: str):
    path = os.path.join(_paths(domain)["pdf_dir"], f"{paper_id}.pdf")
    return path if os.path.isfile(path) else None


def _suggest_example_questions(domain: str):
    """Best effort: ask the LLM for a few starter questions for the empty-chat
    screen of a freshly built collection."""
    cfg = config_or_empty(domain)
    if cfg.get("example_questions"):
        return
    titles = [r.get("title", "") for r in _paper_rows(domain)][:30]
    if not titles:
        return
    from generation import generate, fast_model_choice
    bullets = "\n".join(f"- {t}" for t in titles)
    prompt = f"""Here are the titles of the research papers in a collection:
{bullets}

Write 4 specific questions a researcher could ask that these papers can answer:
one factual lookup, one comparison across papers (if there is more than one),
one about methods, and one about limitations or open problems. Ask about the
papers' content, using their own terminology. Each under 15 words. Return ONLY
a JSON array of 4 strings."""
    try:
        text, _ = generate(prompt, model_choice=fast_model_choice())
        match = re.search(r"\[.*\]", text, re.DOTALL)
        questions = [q.strip() for q in json.loads(match.group()) if isinstance(q, str) and q.strip()]
        if questions:
            update_domain_config(domain, example_questions=questions[:4])
    except Exception as e:
        log.warning("could not generate example questions for %s: %s", domain, e)


def run_ingestion(domain: str, config: dict):
    with _INGEST_LOCK:
        try:
            if not grobid_is_alive():
                raise RuntimeError("The PDF parser (GROBID) is not running - start it, then retry")

            _set(domain, "fetching")
            n_papers = FETCHERS[config["source"]].fetch(
                domain, config, progress=lambda msg: _set(domain, "fetching", msg))
            if n_papers == 0:
                raise RuntimeError("No papers found for these queries")

            _set(domain, "parsing", f"{n_papers} papers fetched")
            parse_papers(domain, progress=lambda msg: _set(domain, "parsing", msg))
            enrich_metadata_from_tei(domain)

            _set(domain, "chunking")
            n_chunks = extract_sections(domain)

            _set(domain, "indexing", f"{n_chunks} chunks")
            build_index(domain, mode="append")

            _set(domain, "linking citations")
            n_edges = build_citation_graph(domain)
            _suggest_example_questions(domain)

            _set(domain, "ready", f"{n_papers} papers, {n_chunks} chunks, {n_edges} citation links")
        except Exception as e:
            log.exception("ingestion of %s failed", domain)
            _set(domain, "failed", str(e))
