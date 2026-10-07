import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import json
import time
import requests
import pandas as pd
from dotenv import load_dotenv
from lxml import etree
from paths import PARSED_DIR, RAW_PDFS_DIR
from tei import header_fields

load_dotenv()

# Override with GROBID_URL, e.g. http://grobid:8070 inside docker compose.
GROBID_BASE = os.environ.get("GROBID_URL", "http://localhost:8070").rstrip("/") + "/api"


def grobid_is_alive(timeout: float = 5) -> bool:
    try:
        return requests.get(f"{GROBID_BASE}/isalive", timeout=timeout).text.strip() == "true"
    except requests.RequestException:
        return False


def parse_papers(domain: str, progress=None) -> int:
    """PDF -> TEI XML via GROBID. Resumable. Returns how many papers have a
    TEI file afterwards; raises if none do."""
    metadata_path = os.path.join(PARSED_DIR, f"{domain}_metadata.csv")
    tei_dir = os.path.join(PARSED_DIR, domain, "tei")
    pdf_dir = os.path.join(RAW_PDFS_DIR, domain)
    os.makedirs(tei_dir, exist_ok=True)

    ids = list(pd.read_csv(metadata_path)["arxiv_id"])
    todo = [i for i in ids if not os.path.exists(os.path.join(tei_dir, f"{i}.tei.xml"))]
    print(f"[{domain}] {len(ids)} papers, {len(todo)} still need parsing")

    if todo and not grobid_is_alive():
        raise RuntimeError(f"GROBID is not reachable at {GROBID_BASE} - start the Docker container first")

    failed = []
    for n, arxiv_id in enumerate(todo, 1):
        if progress:
            progress(f"{n}/{len(todo)} papers")
        # Rebuilt from domain + ID on purpose: the pdf_path column in older
        # metadata files is a relative path that breaks outside the project root.
        pdf_path = os.path.join(pdf_dir, f"{arxiv_id}.pdf")
        tei_path = os.path.join(tei_dir, f"{arxiv_id}.tei.xml")

        if not os.path.exists(pdf_path):
            failed.append((arxiv_id, "pdf not found"))
            continue

        print(f"[{n}/{len(todo)}] Parsing {arxiv_id} ...", end=" ", flush=True)
        start = time.time()
        try:
            with open(pdf_path, "rb") as f:
                response = requests.post(
                    f"{GROBID_BASE}/processFulltextDocument",
                    files={"input": f},
                    data={
                        "consolidateHeader": "0",
                        "consolidateCitations": "0",
                        "includeRawCitations": "1",
                        "segmentSentences": "1",
                    },
                    timeout=300,
                )
            if response.status_code == 200:
                with open(tei_path, "w", encoding="utf-8") as out:
                    out.write(response.text)
                print(f"done in {time.time() - start:.1f}s")
            else:
                print(f"FAILED (HTTP {response.status_code})")
                failed.append((arxiv_id, f"HTTP {response.status_code}"))
        except Exception as e:
            print(f"FAILED ({e})")
            failed.append((arxiv_id, str(e)))

    n_done = sum(os.path.exists(os.path.join(tei_dir, f"{i}.tei.xml")) for i in ids)
    for aid, reason in failed:
        print(f"  failed: {aid}: {reason}")
    if n_done == 0:
        raise RuntimeError(f"GROBID produced no output for '{domain}'")
    return n_done


def enrich_metadata_from_tei(domain: str) -> int:
    """Fill in title/authors/date/abstract for uploaded papers from their parsed
    TEI. Uploads are cataloged before full parsing, so if the header lookup
    failed they carry placeholder titles (the file name). Returns rows updated."""
    metadata_path = os.path.join(PARSED_DIR, f"{domain}_metadata.csv")
    names_path = os.path.join(PARSED_DIR, f"{domain}_upload_names.json")
    tei_dir = os.path.join(PARSED_DIR, domain, "tei")
    if not os.path.exists(metadata_path):
        return 0

    original_names = {}
    if os.path.exists(names_path):
        with open(names_path, encoding="utf-8") as f:
            original_names = json.load(f)

    df = pd.read_csv(metadata_path, dtype=str, keep_default_na=False)
    updated = 0
    for i, row in df.iterrows():
        doc_id = row["arxiv_id"]
        tei_path = os.path.join(tei_dir, f"{doc_id}.tei.xml")
        if row.get("source_query") != "upload" or not os.path.exists(tei_path):
            continue
        try:
            fields = header_fields(etree.parse(tei_path).getroot())
        except Exception:
            continue

        title = row["title"]
        placeholder = (not title or title == doc_id or title == original_names.get(doc_id)
                       or " " not in title.strip())
        changed = False
        if placeholder and len(fields["title"].split()) >= 2:
            df.at[i, "title"] = fields["title"]
            changed = True
        for col, empty in (("authors", ("",)), ("published", ("", "n.d.")), ("abstract", ("",))):
            if row[col] in empty and fields[col]:
                df.at[i, col] = fields[col]
                changed = True
        updated += changed

    if updated:
        df.to_csv(metadata_path, index=False)
    return updated


if __name__ == "__main__":
    domain = sys.argv[1] if len(sys.argv) > 1 else "efficient_attention"
    parse_papers(domain)
    enrich_metadata_from_tei(domain)
