"""Debug helper: shows which claims the citation parser extracts from a pasted answer."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8")
import re
from verify_citations import extract_claim_sentences

# Paste the exact raw answer text from a failing query here
SAMPLE_TEXT = """**Relationship between SystolicAttention’s hardware design and the original FlashAttention algorithm**

| Aspect | Original FlashAttention (algorithmic description) | What SystolicAttention adds on the hardware side |
|--------|---------------------------------------------------|---------------------------------------------------|
| **Core computation** | FlashAttention reformulates the attention kernel as two matrix‑multiplications surrounded by reductions and element‑wise soft‑max operations (row‑max, exponentiation, row‑sum) in order to keep memory usage linear in the sequence length【Source 3】. | SystolicAttention implements *all* of these steps—both matrix multiplications **and** the non‑matrix‑multiplication operations (row‑max, exp2, row‑sum, scaling) *inside a single systolic array* (the FSA architecture) instead of off‑loading them to external vector/scalar units【Source 1】【Source 3】. |
| **Typical mapping on systolic arrays** | When FlashAttention runs on a conventional systolic array, the two mat‑muls are executed on the array while the soft‑max‑related ops are handled by separate units. Thisforces intermediate results to be shuttled back and forth through local SRAM, destroying overlap and creating heavy SRAM‑port contention【Source 3】. | SystolicAttention’s FSA removes this “ping‑pong” data movement. By fusing the entire FlashAttention pipeline into the array, it can stream data (e.g., the last column of Q first) and overlap the two mat‑muls with the soft‑max work, achieving a tightly coupled static dataflow【Source 1】【Source 2】. |
| **Scheduling / Overlap** | FlashAttention’s algorithm is tiled; software pipelines many tiles to amortize data‑movement overhead, but each tile still requires separate phases for the two mat‑muls and the soft‑max steps【Source 3】. | SystolicAttention schedules an *iteration* of FlashAttention’s inner loop so that after a single preload, the first mat‑mul starts, then the second mat‑mul proceeds while the soft‑max reductions are computed on‑the‑fly. The re‑scaling step is done only once per outer loop, using the accumulator after the second mat‑mul, costing only a few cycles (2 + 20) compared to the whole inner loop【Source 1】. |
| **Performance impact** | The original algorithm reduces memory footprint but on systolic hardware still suffers from up‑to‑8‑cycle overhead per tile due to preload and I/O delays when the two mat‑muls are independent【Source 1】. | With SystolicAttention, a FlashAttention tile of size *N×M* is processed in *5 + 10* cycles (instead of the 8‑2 cycles of the naïve mapping), plus a negligible re‑scaling cost, yielding substantially higher FLOPs/s utilization and negligible area/accuracy loss【Source 1】【Source 4】. |
| **Hardware architecture** | FlashAttention itself is a software algorithm; it can run on GPUs, CPUs, or accelerators but does not prescribe a specific compute fabric【Source 3】. | SystolicAttention introduces**FSA (Flash‑Systolic Array)**, a hardware‑efficient systolic array that can execute the full FlashAttention dataflow without leaving the array. The paper presents this as an “enhanced systolic array architecture” that unlocks new capabilities for FlashAttention on such accelerators【Source 2】【Source 4】. |

### Overall synthesis

- The **original FlashAttention algorithm** is a memory‑efficient reformulation of the attention operation that relies on two matrix multiplications plus a series of soft‑max‑related reductions. In typical systolic‑array accelerators these steps are split between the array (mat‑muls) and external units, causing costly data movement and preventing overlap【Source 3】.
- **SystolicAttention** builds on that algorithm by **fusing every step onto a single systolic array (FSA)**, enabling the non‑matrix operations (row‑max, exponentiation via `exp2`, row‑sum, scaling) to be performed in‑situ and overlapped with the two mat‑muls. This removes the back‑and‑forth transfers, reduces cycle counts per tile, and achieves higher hardware utilization with negligible extra overhead【Source 1】【Source 2】【Source 4】.
- Thus, SystolicAttention’s hardware approach is a **direct implementation‑level extension** of FlashAttention: it preserves the algorithm’s tiling and online‑softmax ideas but restructures the execution model so that the entire algorithm runs inside a specially designed systolic array rather than being partially off‑loaded. No disagreement among the cited sources is observed; they consistently describe SystolicAttention as a hardware‑centric realization that “fuses” FlashAttention within a single array.
"""

print("Raw repr of text (first 300 chars):")
# Show every line that actually mentions a source, since that's the only
# part relevant to the extraction bug
for line in SAMPLE_TEXT.split("\n"):
    if "source" in line.lower():
        print(repr(line))
claims = extract_claim_sentences(SAMPLE_TEXT)
print(f"\nExtracted {len(claims)} claims:")
for c in claims:
    print(f"  sources={c['cited_sources']}  text={c['sentence'][:80]!r}")