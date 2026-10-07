import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import json
import re
import pandas as pd
from collections import Counter
from lxml import etree
from paths import PARSED_DIR

NS = {"tei": "http://www.tei-c.org/ns/1.0"}

TARGET_WORDS = 220
OVERLAP_SENTENCES = 1
MIN_CHUNK_WORDS = 25   # drop stray fragments: orphan captions, section stubs

unmatched_headings = Counter()


def classify_section(heading: str):
    """Return a canonical section type, or None if the heading is uninformative.

    None means 'inherit whatever section we're already in' - how subsections
    (e.g. '4.2 Baselines' under '4. Experiments') get handled correctly.
    """
    h = heading.strip()

    # Strip a genuine numbering/label prefix ("3.2 ", "IV. ", "B.4 ", "(a) ") -
    # but only when followed by a real delimiter, so words that merely start
    # with a numeral-like letter ("Introduction", "Implementation") are untouched.
    h = re.sub(r"^\(?(?:[0-9]+(?:\.[0-9]+)*|[A-Z]|[IVXLCDM]+)\)?[\.:\)]\s+", "", h)

    h = h.lower()
    h = re.sub(r"[^a-z\s]", " ", h)
    h = re.sub(r"\s+", " ", h).strip()

    if not h or h == "untitled":
        return None

    if "abstract" in h:
        return "abstract"
    if any(k in h for k in ["introduction", "motivation", "overview"]):
        return "introduction"
    if any(k in h for k in ["related work", "prior work", "previous work",
                            "literature", "background", "preliminaries",
                            "related literature"]):
        return "related_work"
    if any(k in h for k in ["method", "approach", "model", "architecture",
                            "algorithm", "formulation", "framework", "design",
                            "our ", "proposed", "implementation", "attention",
                            "training", "objective", "derivation", "theory",
                            "theoretical", "complexity", "kernel"]):
        return "method"
    if any(k in h for k in ["experiment", "result", "evaluation", "ablation",
                            "analysis", "benchmark", "dataset", "setup",
                            "baseline", "comparison", "empirical", "study",
                            "performance", "measurement"]):
        return "results"
    if any(k in h for k in ["conclusion", "discussion", "future work",
                            "limitation", "broader impact", "summary",
                            "concluding"]):
        return "conclusion"

    unmatched_headings[heading.strip()[:60]] += 1
    return None


def get_sentences(element):
    sentences = []
    for s in element.findall(".//tei:s", NS):
        text = re.sub(r"\s+", " ", " ".join(s.itertext())).strip()
        if text:
            sentences.append(text)
    if not sentences:
        text = re.sub(r"\s+", " ", " ".join(element.itertext())).strip()
        if text:
            sentences.append(text)
    return sentences


def chunk_sentences(sentences):
    chunks, current, current_words = [], [], 0
    for sent in sentences:
        words = len(sent.split())
        if current and current_words + words > TARGET_WORDS:
            chunks.append(current)
            current = current[-OVERLAP_SENTENCES:] if OVERLAP_SENTENCES else []
            current_words = sum(len(s.split()) for s in current)
        current.append(sent)
        current_words += words
    if current:
        chunks.append(current)
    return chunks


def extract_references(root):
    refs = []
    for bib in root.findall(".//tei:back//tei:biblStruct", NS):
        ref_id = bib.get("{http://www.w3.org/XML/1998/namespace}id")

        title_el = bib.find(".//tei:title[@type='main']", NS)
        title = re.sub(r"\s+", " ", " ".join(title_el.itertext())).strip() if title_el is not None else None

        authors = []
        for pers in bib.findall(".//tei:author//tei:persName", NS):
            name = " ".join(x.strip() for x in pers.itertext() if x.strip())
            if name:
                authors.append(name)

        date_el = bib.find(".//tei:date", NS)
        year = date_el.get("when")[:4] if (date_el is not None and date_el.get("when")) else None

        raw_el = bib.find(".//tei:note[@type='raw_reference']", NS)
        raw = re.sub(r"\s+", " ", " ".join(raw_el.itertext())).strip() if raw_el is not None else None

        refs.append({"ref_id": ref_id, "title": title, "authors": authors,
                     "year": year, "raw": raw})
    return refs


def process_paper(tei_path, meta_row):
    root = etree.parse(tei_path).getroot()
    arxiv_id = meta_row["arxiv_id"]
    chunks = []

    def add_chunks(section_title, section_type, inherited, sentences):
        for group in chunk_sentences(sentences):
            n_words = sum(len(s.split()) for s in group)
            if n_words < MIN_CHUNK_WORDS:
                continue
            chunks.append({
                "chunk_id": f"{arxiv_id}::{len(chunks):03d}",
                "arxiv_id": arxiv_id,
                "paper_title": meta_row["title"],
                "published": meta_row["published"],
                "domain": meta_row["domain"],
                "section_title": section_title,
                "section_type": section_type,
                "section_type_inherited": inherited,
                "sentences": group,
                "text": " ".join(group),
                "n_words": n_words,
            })

    abstract = root.find(".//tei:profileDesc//tei:abstract", NS)
    if abstract is not None:
        sents = get_sentences(abstract)
        if sents:
            add_chunks("Abstract", "abstract", False, sents)

    # Walk body sections in document order, carrying section context forward.
    current_type = "introduction"   # papers open in the introduction
    body = root.find(".//tei:text//tei:body", NS)
    if body is not None:
        for div in body.iter("{http://www.tei-c.org/ns/1.0}div"):
            head_el = div.find("tei:head", NS)
            heading = re.sub(r"\s+", " ", " ".join(head_el.itertext())).strip() if head_el is not None else "Untitled"

            classified = classify_section(heading)
            inherited = classified is None
            if classified is not None:
                current_type = classified

            sents = []
            for p in div.findall("tei:p", NS):
                sents.extend(get_sentences(p))
            if sents:
                add_chunks(heading, current_type, inherited, sents)

    return chunks, extract_references(root)


def extract_sections(domain: str) -> int:
    """TEI XML -> section-aware chunks + reference lists. Returns chunk count."""
    unmatched_headings.clear()

    metadata_path = os.path.join(PARSED_DIR, f"{domain}_metadata.csv")
    tei_dir = os.path.join(PARSED_DIR, domain, "tei")
    chunks_path = os.path.join(PARSED_DIR, f"{domain}_chunks.jsonl")
    refs_path = os.path.join(PARSED_DIR, f"{domain}_references.jsonl")

    df = pd.read_csv(metadata_path)
    all_chunks, all_refs = [], []

    for _, row in df.iterrows():
        tei_path = os.path.join(tei_dir, f"{row['arxiv_id']}.tei.xml")
        if not os.path.exists(tei_path):
            print(f"No TEI for {row['arxiv_id']}, skipping")
            continue
        try:
            chunks, refs = process_paper(tei_path, row)
        except Exception as e:
            print(f"Failed on {row['arxiv_id']}: {e}")
            continue
        all_chunks.extend(chunks)
        all_refs.append({"arxiv_id": row["arxiv_id"],
                         "paper_title": row["title"],
                         "references": refs})
        print(f"{row['arxiv_id']}: {len(chunks)} chunks, {len(refs)} references")

    if not all_chunks:
        raise RuntimeError(f"No chunks extracted for '{domain}' - no usable TEI files")

    with open(chunks_path, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with open(refs_path, "w", encoding="utf-8") as f:
        for r in all_refs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    cdf = pd.DataFrame(all_chunks)
    print(f"\nTotal: {len(all_chunks)} chunks from {len(all_refs)} papers")
    print("\nChunks by section type:")
    print(cdf["section_type"].value_counts())
    print(f"\nInherited section type: {cdf['section_type_inherited'].sum()} of {len(cdf)} chunks")
    print(f"Median chunk length: {cdf['n_words'].median():.0f} words")

    print("\nTop unclassified headings (inherited from parent):")
    for h, n in unmatched_headings.most_common(25):
        print(f"  {n:3d}  {h}")

    return len(all_chunks)


if __name__ == "__main__":
    domain = sys.argv[1] if len(sys.argv) > 1 else "efficient_attention"
    extract_sections(domain)