import pytest
from fastapi import HTTPException

import os

from extract_sections import chunk_sentences, classify_section
from paths import RAW_PDFS_DIR, project_relative
from ratelimit import RateLimiter


@pytest.mark.parametrize("heading, expected", [
    ("Abstract", "abstract"),
    ("1 Introduction", "introduction"),
    ("2. Related Work", "related_work"),
    ("3.2 Proposed Method", "method"),
    ("IV. Experiments", "results"),
    ("6 Conclusion and Future Work", "conclusion"),
    ("Acknowledgements", None),   # unclassified headings inherit the parent section
    ("Untitled", None),
])
def test_classify_section(heading, expected):
    assert classify_section(heading) == expected


def test_numbering_prefix_does_not_eat_real_words():
    # "Implementation" starts with "I" (a roman numeral) but has no delimiter after it
    assert classify_section("Implementation Details") == "method"


def test_chunk_sentences_respects_target_and_overlaps_one_sentence():
    sentences = [" ".join(["word"] * 100)] * 5
    chunks = chunk_sentences(sentences)
    assert len(chunks) > 1
    for a, b in zip(chunks, chunks[1:]):
        assert a[-1] == b[0]   # one sentence of overlap between neighbours


class _Req:
    def __init__(self, host):
        self.client = type("C", (), {"host": host})()


def test_rate_limiter_blocks_after_limit_per_client():
    limiter = RateLimiter(limit=2, window_seconds=60)
    limiter(_Req("1.1.1.1"))
    limiter(_Req("1.1.1.1"))
    with pytest.raises(HTTPException) as exc:
        limiter(_Req("1.1.1.1"))
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers
    limiter(_Req("2.2.2.2"))   # other clients are unaffected


def test_rate_limiter_disabled_with_zero_limit():
    limiter = RateLimiter(limit=0)
    for _ in range(100):
        limiter(_Req("1.1.1.1"))


def test_stored_pdf_paths_do_not_depend_on_the_project_folder():
    pdf = os.path.join(RAW_PDFS_DIR, "my_domain", "2205.14135v2.pdf")
    assert project_relative(pdf) == "data/raw_pdfs/my_domain/2205.14135v2.pdf"
