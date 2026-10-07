import os
import json
import requests
import pandas as pd
from lxml import etree

from paths import PARSED_DIR, RAW_PDFS_DIR, project_relative
from parse_papers import GROBID_BASE
from tei import header_fields
from .base import DocumentFetcher


def _read_header(pdf_path: str) -> dict:
    """Ask GROBID for just the header: title, authors, date, abstract."""
    info = {"title": "", "authors": "", "published": "n.d.", "abstract": ""}
    try:
        with open(pdf_path, "rb") as f:
            resp = requests.post(
                f"{GROBID_BASE}/processHeaderDocument",
                files={"input": f},
                data={"consolidateHeader": "0"},
                headers={"Accept": "application/xml"},
                timeout=120,
            )
        if resp.status_code != 200:
            return info
        root = etree.fromstring(resp.content)
    except Exception:
        return info

    fields = header_fields(root)
    return {k: v or info[k] for k, v in fields.items()}


class UploadFetcher(DocumentFetcher):
    """Catalogs PDFs the user uploaded into data/raw_pdfs/<domain>/ and writes
    the same metadata CSV the arXiv fetcher produces, so every later stage
    (parse, chunk, index, citation graph) runs unchanged."""

    def fetch(self, domain: str, config: dict, progress=None) -> int:
        pdf_dir = os.path.join(RAW_PDFS_DIR, domain)
        metadata_path = os.path.join(PARSED_DIR, f"{domain}_metadata.csv")
        names_path = os.path.join(PARSED_DIR, f"{domain}_upload_names.json")
        os.makedirs(PARSED_DIR, exist_ok=True)

        if not os.path.isdir(pdf_dir):
            return 0

        original_names = {}
        if os.path.exists(names_path):
            with open(names_path, encoding="utf-8") as f:
                original_names = json.load(f)

        records = pd.read_csv(metadata_path).to_dict("records") if os.path.exists(metadata_path) else []
        seen = {r["arxiv_id"] for r in records}

        pending = [f for f in sorted(os.listdir(pdf_dir))
                   if f.lower().endswith(".pdf") and f[:-4] not in seen]
        for i, filename in enumerate(pending, 1):
            doc_id = filename[:-4]
            if progress:
                progress(f"reading {i}/{len(pending)} uploaded PDFs")

            pdf_path = os.path.join(pdf_dir, filename)
            header = _read_header(pdf_path)
            records.append({
                "arxiv_id": doc_id,
                "title": header["title"] or original_names.get(doc_id, doc_id),
                "authors": header["authors"],
                "published": header["published"],
                "abstract": header["abstract"],
                "pdf_path": project_relative(pdf_path),
                "domain": domain,
                "source_query": "upload",
            })
            seen.add(doc_id)
            pd.DataFrame(records).to_csv(metadata_path, index=False)
            print(f"Cataloged {doc_id}: {records[-1]['title']}")

        return len(records)
