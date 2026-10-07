"""Helpers for reading GROBID's TEI XML output."""
import re

NS = {"tei": "http://www.tei-c.org/ns/1.0"}


def clean_text(el) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", " ".join(el.itertext())).strip()


def header_fields(root) -> dict:
    """Title, authors, date and abstract from a TEI document's header. Works
    for both processHeaderDocument and processFulltextDocument output."""
    info = {"title": "", "authors": "", "published": "", "abstract": ""}
    info["title"] = clean_text(root.find(".//tei:teiHeader//tei:titleStmt/tei:title", NS))

    names = []
    for pers in root.findall(".//tei:teiHeader//tei:sourceDesc//tei:author/tei:persName", NS):
        name = clean_text(pers)
        if name:
            names.append(name)
    info["authors"] = ", ".join(names)

    date_el = root.find(".//tei:teiHeader//tei:publicationStmt/tei:date", NS)
    if date_el is not None and date_el.get("when"):
        info["published"] = date_el.get("when")

    info["abstract"] = clean_text(root.find(".//tei:teiHeader//tei:profileDesc/tei:abstract", NS))
    return info
