import json

import pytest

from verify_citations import (extract_claim_sentences, extract_claims, keyword_overlap, missing_numbers,
                              parse_source_numbers, verify_answer)


@pytest.mark.parametrize("block, expected", [
    ("[Source 2]", [2]),
    ("[Sources 1, 3]", [1, 3]),
    ("[Sources 1, 3 and 4]", [1, 3, 4]),
    ("[Source 1, Source 2]", [1, 2]),
    ("[Source 1; Source 4]", [1, 4]),
    ("[Sources: 1, 2]", [1, 2]),
    ("[Sources 2-4]", [2, 3, 4]),
    # gpt-oss line markers are not source numbers
    ("【Source 4†L7-L12】", [4]),
    ("【Source 1†L1-L4】", [1]),
    ("【Source 1†L1-L4, Source 3†L2】", [1, 3]),
    ("【4†L10-L20】", [4]),
    ("(see Source 5)", [5]),
    ("(Source 1, Source 2)", [1, 2]),
    # a figure reference after the list is not a source
    ("(Source 5, Table 2)", [5]),
])
def test_parse_source_numbers(block, expected):
    assert parse_source_numbers(block) == expected


def test_multiple_citation_blocks_in_one_sentence_all_count():
    claims, markers, _ = extract_claims("It is faster [Source 1] and smaller [Source 2].")
    assert claims[0]["sources"] == [1, 2]
    assert [m["sources"] for m in markers] == [[1], [2]]
    assert all(m["claim"] == 0 for m in markers)


def test_citation_after_full_stop_attaches_to_previous_sentence():
    claims, _, _ = extract_claims("FlashAttention tiles the computation. [Source 3]")
    assert len(claims) == 1
    assert claims[0]["text"] == "FlashAttention tiles the computation."
    assert claims[0]["sources"] == [3]


def test_markdown_is_stripped_from_claims():
    claims, _, _ = extract_claims("- **Tiling** keeps the matrix *off-chip* [Source 1].")
    assert claims[0]["text"] == "Tiling keeps the matrix off-chip."


def test_source_column_backs_the_whole_table_row():
    text = ("| Aspect | New method | Old method | Source |\n"
            "|---|---|---|---|\n"
            "| Speed | 2x faster than the baseline | standard tiling | [Source 2], [Source 3] |")
    claims, markers, uncited = extract_claims(text)
    assert [c["text"] for c in claims] == ["Speed; 2x faster than the baseline; standard tiling"]
    assert claims[0]["sources"] == [2, 3]
    assert len(markers) == 2
    assert uncited == 0


def test_table_cells_with_their_own_citations_are_separate_claims():
    text = ("| Method | Finding |\n"
            "|---|---|\n"
            "| FlashAttention [Source 1] | Tiles the attention matrix to stay in SRAM [Source 2] |")
    claims, _, _ = extract_claims(text)
    assert [(c["text"], c["sources"]) for c in claims] == [
        ("FlashAttention", [1]), ("Tiles the attention matrix to stay in SRAM", [2])]


def test_trailing_dash_before_citation_is_dropped():
    claims, _, _ = extract_claims("- “around 2× speedup” (general statement) – [Source 1]")
    assert claims[0]["text"] == "“around 2× speedup” (general statement)"


def test_latex_norm_pipes_do_not_split_sentences():
    claims, _, _ = extract_claims(r"The kernel uses \(\|x\|^2\) as a normaliser for every key vector [Source 1].")
    assert len(claims) == 1
    assert claims[0]["sources"] == [1]


def test_abbreviations_do_not_end_sentences():
    claims, _, _ = extract_claims("Prior work (e.g. Smith et al. 2019) prunes keys after scoring [Source 4].")
    assert len(claims) == 1


def test_uncited_sentences_are_counted_but_headings_are_not():
    text = ("## Summary of the main memory findings in these papers\n"
            "The method never stores the full attention matrix in GPU memory at all.\n"
            "It recomputes attention in the backward pass instead [Source 1].")
    claims, _, uncited = extract_claims(text)
    assert len(claims) == 1
    assert uncited == 1


def test_display_math_is_not_an_uncited_sentence():
    text = ("It draws $S$ i.i.d. samples and averages them [Source 1].\n"
            r"\[ \exp\!\bigl(q_n^\top k_m\bigr)=\mathbb{E}_{\omega}\bigl[\xi(q_n,\omega)\,\xi(k_m,\omega)\bigr] \]")
    claims, _, uncited = extract_claims(text)
    assert claims[0]["text"] == "It draws $S$ i.i.d. samples and averages them."
    assert uncited == 0


def test_mostly_uncited_answer_is_not_well_supported():
    hits = [_hit("FlashAttention reduces memory reads and writes.")]
    answer = ("FlashAttention reduces memory reads and writes [Source 1]. "
              "It was the first algorithm to ever make attention linear in memory usage. "
              "It is used by every large language model trained in the last five years.")
    s = verify_answer(answer, hits, use_semantic=False)["summary"]
    assert (s["claims_supported"], s["uncited_sentences"]) == (1, 2)
    assert s["verdict"] == "mostly_supported"


def test_backwards_compatible_view():
    assert extract_claim_sentences("Fast [Source 2].") == [{"sentence": "Fast.", "cited_sources": [2]}]


def test_keyword_overlap_uses_stems():
    assert keyword_overlap("reduces memory reads", "it reduce the memory read count") == 1.0
    assert keyword_overlap("quantum chromodynamics", "attention kernels") == 0.0


@pytest.mark.parametrize("claim, haystack, expected", [
    ("reaches 230 TFLOPs/s", "up to 230 TFLOPs/s on A100", []),
    ("reaches 250 TFLOPs/s", "up to 230 TFLOPs/s on A100", ["250"]),
    ("a 1,000 token window", "a 1000 token window", []),
    ("72.0% utilisation", "72% utilisation", []),
    ("FlashAttention-2 uses 2 passes", "one pass", []),   # bare single digits are ignored
    ("3x faster", "about 2x faster", ["3"]),             # ...unless they carry a unit
])
def test_missing_numbers(claim, haystack, expected):
    assert missing_numbers(claim, haystack) == expected


def _hit(text, title="A Paper", published="2023-01-01"):
    return {"text": text, "similarity": 0.8, "via": "semantic",
            "meta": {"paper_title": title, "published": published,
                     "sentences_json": json.dumps([text])}}


def test_verify_answer_statuses_without_embeddings():
    hits = [_hit("FlashAttention reduces memory reads and writes between GPU memory levels."),
            _hit("Music generation with relative attention.")]
    answer = ("FlashAttention reduces memory reads and writes [Source 1]. "
              "It also composes symphonies [Source 2]. "
              "It was invented in 1850 [Source 7].")
    report = verify_answer(answer, hits, use_semantic=False)
    statuses = [c["citations"][0]["status"] for c in report["claims"]]
    assert statuses == ["supported", "unsupported", "invalid"]
    s = report["summary"]
    assert (s["claims"], s["claims_supported"], s["claims_unsupported"]) == (3, 1, 2)
    assert s["invalid"] == 1
    assert s["verdict"] == "weakly_supported"


def test_wrong_number_downgrades_supported_claim():
    hits = [_hit("FlashAttention-2 reaches 230 TFLOPs/s on an A100 GPU.")]
    report = verify_answer("FlashAttention-2 reaches 250 TFLOPs/s on an A100 GPU [Source 1].", hits,
                           use_semantic=False)
    citation = report["claims"][0]["citations"][0]
    assert citation["missing_numbers"] == ["250"]
    assert citation["status"] == "partial"


def test_each_citation_is_checked_against_the_part_of_the_sentence_it_backs():
    hits = [_hit("FlashAttention reaches 230 TFLOPs/s on an A100 GPU."),
            _hit("EVA measures memory on an RTX 3090 for 16 heads.")]
    answer = ("FlashAttention reaches 230 TFLOPs/s on an A100 GPU [Source 1]; "
              "EVA measures memory on an RTX 3090 for 16 heads [Source 2].")
    report = verify_answer(answer, hits, use_semantic=False)
    assert len(report["claims"]) == 1
    citations = report["claims"][0]["citations"]
    assert [c["status"] for c in citations] == ["supported", "supported"]
    # 3090 and 16 belong to the second half, so they aren't "missing" from Source 1
    assert [c["missing_numbers"] for c in citations] == [[], []]
    assert "segments" not in report["claims"][0]


def test_answer_without_citations():
    report = verify_answer("Attention is a weighted average of value vectors.", [], use_semantic=False)
    assert report["summary"]["verdict"] == "no_citations"
    assert report["summary"]["score"] is None


def test_uncited_refusal_is_not_covered():
    report = verify_answer("The sources do not cover this question.", [], use_semantic=False)
    assert report["summary"]["verdict"] == "not_covered"


@pytest.mark.parametrize("answer, expected", [
    ("The sources you provided do not contain any information about the cost of training GPT-4.", True),
    ("The provided excerpts don't report ImageNet accuracy for 2026.", True),
    ("None of the sources mention the training budget.", True),
    ("There is no information in these passages about pricing.", True),
    ("FlashAttention does not materialize the attention matrix, which reduces memory [Source 1].", False),
    ("The method does not require retraining and provides a 2x speedup [Source 2].", False),
])
def test_declines(answer, expected):
    from verify_citations import declines
    assert declines(answer) is expected


def test_declined_answer_gets_neutral_verdict():
    hits = [_hit("FlashAttention-2 trains GPT-2 in minutes on 8 A100 GPUs.")]
    answer = ("The sources do not contain any information about the cost of training GPT-4. "
              "The only related detail is that FlashAttention-2 trains GPT-2 in minutes [Source 1].")
    s = verify_answer(answer, hits, use_semantic=False)["summary"]
    assert s["declined"] is True
    assert s["verdict"] == "not_covered"
