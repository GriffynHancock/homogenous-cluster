# MiniCheck spike: does it run, is it fast enough, and does it survive negation?

**Date:** 2026-08-18. **Hardware:** node 1 (this box) — Debian 12, Python 3.11.2,
CPU-only, 4 physical cores / 8 threads, no AVX-512, no usable GPU (per
`CLAUDE.md`). **Scope:** MiniCheck-Flan-T5-Large (770M) primary,
MiniCheck-RoBERTa-Large (355M) as cross-check, per `docs/EVALUATION.md`
Priority 1. Everything below was actually run on this hardware in a throwaway
venv at
`/tmp/claude-1000/-home-debian1-homogenous-cluster/446bde09-7ffa-47e9-96f5-98eb28156db8/scratchpad/minicheck/`
— nothing under `missing-link/` was touched, no inference load was put on
`llama-server@8080` / `rpc-server@50052`.

Labels: **CONFIRMED** (I ran it and saw the output, or read primary source
directly), **REPORTED** (secondary source / given to me as a fact), **INFERRED**
(computed from CONFIRMED numbers elsewhere).

---

## VERDICT: yes, but not as a single-model auto-pass gate

**MiniCheck-Flan-T5-Large installs and runs on this hardware (CONFIRMED)** and
is fast enough to be a genuine background audit step, not a second cost
centre that doubles wall-clock (CONFIRMED, see §2 — it costs roughly a third
to two-fifths of the LLM-judge alternative, not double).

**But it must not be trusted alone.** I reproduced the arXiv:2511.07689
negation blind spot directly on this project's own material (CONFIRMED, §4):
Flan-T5-Large **inverted** a retention-obligation pair built in the style of
this project's flagship example (`CLAUDE.md`'s own "records must be retained
for seven years" language) — scoring the **true** claim as unsupported
(0.26) and the **fabricated negation** as supported (0.75). This is not a
borderline call; both scores land on the wrong side of the 0.5 threshold.
MiniCheck-RoBERTa-Large got the same pair right, and agreed with Flan-T5 on
the other four pairs. **So: run both models. Treat agreement as a pass
signal and disagreement as an automatic escalation to human or LLM-judge
review** — never ship a single-model pass/fail gate on legal/health
obligation clauses, this project's core material.

There is also a confirmed, dangerous **silent-truncation** failure mode
(§3): when a single "sentence" (as nltk's tokenizer defines it) exceeds
~2048 tokens, the tokenizer's `truncation=True` silently drops the claim
text entirely, and MiniCheck's own diagnostic output (`used_chunks`)
misreports what the model actually saw. This requires degenerate input
(missing sentence-ending punctuation — plausible in OCR'd or poorly
extracted documents, not confirmed on this project's real material, which
had only one usable row to test against).

**Net: MiniCheck is viable as a cheap, fast, dual-model triage signal that
flags likely fabrications for review. It is not viable as an unsupervised
faithfulness gate on its own**, given a confirmed, reproducible miss on
exactly the clause type this project cares most about.

---

## 1. Does it install and run at all? (CONFIRMED, yes — with undocumented friction)

Installed via the README's documented command:
```
pip install "minicheck @ git+https://github.com/Liyan06/MiniCheck.git@main"
```
into a throwaway venv. Three points of friction not in the README:

- **`accelerate` is required but undeclared.** `pyproject.toml`'s
  `dependencies` list is `torch, transformers, datasets, nltk, pandas, numpy,
  openai` — no `accelerate`. `Inferencer.__init__` calls
  `AutoModelForSeq2SeqLM.from_pretrained(ckpt, device_map="auto")`, and current
  `transformers` (5.15.0, resolved by the unpinned install) raises immediately:
  `ValueError: Using a device_map ... requires accelerate`. Fixed with `pip
  install accelerate`.
- **`nltk` needs `punkt_tab` downloaded separately**, not just `punkt`:
  `LookupError: Resource 'punkt_tab' not found`. Fixed with
  `nltk.download('punkt_tab')`. Nothing in the MiniCheck README mentions this.
- **The unpinned install pulls a full CUDA 13.0 build of torch** (`torch
  2.13.0+cu130`) on this GPU-less box — ~2GB+ of `nvidia-cu*` wheels that are
  dead weight (`torch.cuda.is_available()` returns `False`). Final venv:
  5.2 GB. A CPU-only wheel
  (`--index-url https://download.pytorch.org/whl/cpu`) would avoid this; I
  did not verify a clean minimal CPU-only install end-to-end (see §7).

Once past that, a hand-built trivial pair with a known answer:

```
doc = "The invoice number INV-4471 for $12,340.00 was issued to Riverside
       Community Health on 3 February 2026 and was paid in full by bank
       transfer on 12 March 2026."
claim_supported  = "The invoice was paid on 12 March 2026."
claim_fabricated = "The invoice is still unpaid and is now overdue."
```

**Output (CONFIRMED, MiniCheck-Flan-T5-Large):**
```
claim_supported  -> label 1, prob 0.9754301905632019
claim_fabricated -> label 0, prob 0.01345617976039648
```
Correct on both. `torch.get_num_threads()` defaulted to **4**, correctly
matching physical core count with no manual tuning needed (unlike
`rpc-server -t`, which CLAUDE.md notes needs explicit setting).

First model load (cold, downloading the ~5.9 GB checkpoint over an
unauthenticated HF Hub connection): **314.95 s (~5.25 min), one-time.**
Warm load from local cache: **3.44 s.**

---

## 2. Speed per (chunk, sentence) pair (CONFIRMED)

Timed against a realistic **3,767-token chunk** (target was `CHUNK_TOKENS` =
4096; the generated text landed slightly under) and a single claim sentence,
`MiniCheck-Flan-T5-Large`, 8 repeated pairs:

| Measure | Value |
|---|---:|
| Sequential, one `.score()` call per pair — times (s) | 30.7, 30.1, 24.2, 16.0, 17.3, 17.1, 18.7, 16.1 |
| Mean / median (sequential) | **21.26 s** / 17.97 s |
| Batched (8 claims, 1 `.score()` call vs. same chunk) | 132.43 s total → **16.55 s/pair** |

**Why it's this slow — confirmed mechanism, not guesswork.** MiniCheck
internally re-splits any document larger than its own default `chunk_size`
(500 words for Flan-T5) into sub-chunks and does **one forward pass per
sub-chunk**, then max-pools. A 3,767-token chunk becomes ~6–9 internal
sub-chunks. I verified this directly: feeding a document already sized at
MiniCheck's own native ~500-word granularity (`496` words) gives
`n_internal_chunks=1` and **2.44 s/pair mean** — an 8–9× speedup over the
same-content-scaled-up 4096-token version. This matters for anyone tuning the
calling convention later, though per `docs/EVALUATION.md`'s own finding
(score against the *whole* source chunk, not a fragment, because
whole-chunk scoping is what fixed the metric's degradation on long
documents) the full-chunk cost (~17–21 s/pair) is the realistic number for
this project's design, not the 2.44 s figure.

**Document-level projection** (25 chunks × 8 summary sentences/chunk = 200
pairs, INFERRED by scaling the measured per-pair cost):

| | Sequential | Batched |
|---|---:|---:|
| Total | 4252 s | 3311 s |
| Minutes | **70.9 min** | **55.2 min** |

**Against the LLM-judge alternative.** The task brief states reinvoking
gpt-oss-120b costs ~6–7 min/chunk. I traced that to
`docs/measurements.md`'s CONFIRMED numbers: pp2048 = 15.88 tok/s, tg128 =
6.05 tok/s. For one `CHUNK_TOKENS`=4096 prefill + `MAP_MAX_TOKENS`=1024
generation: 4096/15.88 + 1024/6.05 ≈ 258 s + 169 s ≈ **427 s ≈ 7.1
min/chunk** (INFERRED from CONFIRMED measurements, not itself directly
measured). For 25 chunks that's **~178 min** for one full LLM pass over the
document. **MiniCheck's audit pass (55–71 min) costs roughly a third to two
fifths of that** — a real, useful saving, and nowhere near
`docs/EVALUATION.md`'s worst-case worry of "roughly double total cluster
time." But it is **not free**: adding ~1 hour to an already multi-hour
document job is a second real cost centre for an overnight batch, not a
background nicety.

---

## 3. Maximum usable input length — and the silent-truncation failure mode (CONFIRMED)

Read directly from `minicheck/inference.py` (primary source, quoted): no
explicit length limit and **no error is ever raised** for over-length input.
`max_model_len` defaults are **2048 tokens** for Flan-T5-Large (512 for
RoBERTa-Large), and the final tokenization step is:
```python
model_inputs = self.tokenizer(['predict: ' + text for text in mini_batch],
    max_length=self.max_model_len, truncation=True, padding=True, ...)
```

**Normal documents: no problem, confirmed up to 18k tokens.** Because
MiniCheck auto-splits on sentence boundaries into ~500-word (Flan-T5)
sub-chunks internally and max-pools the per-sub-chunk scores, a fact placed
in the very last sentence of a growing document was found correctly all the
way out to the longest document tested:

| filler sentences | doc tokens | internal chunks | supported prob | fabricated prob | correct? |
|---:|---:|---:|---:|---:|---|
| 5 | 123 | 1 | 0.9643 | 0.0091 | yes |
| 20 | 423 | 1 | 0.9659 | 0.0092 | yes |
| 60 | 1,223 | 2 | 0.9658 | 0.0137 | yes |
| 120 | 2,423 | 4 | 0.9659 | 0.0137 | yes |
| 250 | 5,023 | 9 | 0.9688 | 0.0137 | yes |
| 500 | 10,023 | 17 | 0.9651 | 0.0137 | yes |
| 900 | 18,023 | 30 | 0.9706 | 0.0137 | yes |

Cost scales roughly linearly with internal chunk count (1 s at 123 tokens →
~75 s at 18,023 tokens), which is itself a caller-facing reason to keep
documents pre-chunked to something in the `CHUNK_TOKENS` neighborhood rather
than handing MiniCheck a whole raw document.

**The pathological case: silent, dangerous truncation, reproduced.** I built
a single 9,000-token run-on "sentence" with no sentence-ending punctuation
(nltk detects it as **one** sentence: `nltk_sentences_detected=1`), so
MiniCheck cannot internally split it — it becomes one oversized chunk that
hits the hard `max_length=2048, truncation=True` tokenizer call. Result:

```
n_chunks_produced=1
supported_prob=0.4553 label=0
fabricated_prob=0.4553 label=0     <- identical to the supported claim's score
```

Both the true and the fabricated claim scored the **same**, both wrongly
labeled unsupported. I decoded the actual truncated model input to confirm
why: **the claim text was entirely absent** — truncation cuts from the *end*
of the doc+claim string, and since the doc alone was 9,025 tokens (already
over the 2048 cap), the claim (which is appended after the doc) never made
it into the model's input at all:

```
TRUNCATED_TOKEN_COUNT=2048
claim text present in decoded truncated input? False
```

**And the diagnostic output actively misleads about this.** `used_chunks`
(what a caller would check to see "what did the model see") is populated
from the pre-truncation string, not the actual tokenized-and-truncated
input — so it reports the full original text, including the fact at the
end, even though the model itself never saw it. A caller trusting
`used_chunks` would wrongly conclude nothing was lost.

**Caveat on severity:** this requires a single nltk-detected "sentence" to
exceed ~2048 tokens — an edge case for normal prose, but plausible for this
project's real material: OCR'd documents with dropped periods, flattened
tables, semicolon/bullet-delimited lists, or heavily redacted text. I did
not have a real example of this in the one row of actual project data
available (see §5) to confirm it bites in practice — this is a demonstrated
mechanism, not an observed production failure.

---

## 4. The negation stress test — the decision-relevant result (CONFIRMED)

Five pairs built in the style of this project's real documents (retention,
access, breach notification, authorisation, destruction), each with a
`supported` claim and a `negated` claim that inverts the document's
obligation/permission.

| id | model | supported prob (want >0.5) | negated prob (want <0.5) | correct? |
|---|---|---:|---:|---|
| **retention_seven_years** | **Flan-T5-Large** | **0.258 (label 0)** | **0.748 (label 1)** | **NO — INVERTED** |
| retention_seven_years | RoBERTa-Large | 0.977 (label 1) | 0.370 (label 0) | yes |
| access_41_staff | Flan-T5-Large | 0.964 (1) | 0.025 (0) | yes |
| access_41_staff | RoBERTa-Large | 0.968 (1) | 0.038 (0) | yes |
| breach_notification_72h | Flan-T5-Large | 0.979 (1) | 0.016 (0) | yes |
| breach_notification_72h | RoBERTa-Large | 0.980 (1) | 0.127 (0) | yes |
| archive_authorisation | Flan-T5-Large | 0.981 (1) | 0.029 (0) | yes |
| archive_authorisation | RoBERTa-Large | 0.979 (1) | 0.056 (0) | yes |
| litigation_hold_destruction | Flan-T5-Large | 0.969 (1) | 0.025 (0) | yes |
| litigation_hold_destruction | RoBERTa-Large | 0.768 (1) | 0.071 (0) | yes |

The failing pair: document says *"Clinical records must be retained for
seven years from the date of last service, or until the client turns
twenty-five, whichever is later."* Supported claim: *"Records must be kept
for seven years from the last service."* Negated/fabricated claim: *"Records
must not be retained beyond seven years from the last service."*
**Flan-T5-Large scored the true claim as unsupported and the fabricated one
as supported — both wrong, both confidently so (0.26 and 0.75).**
RoBERTa-Large scored the same pair correctly (0.977 / 0.370).

This directly reproduces arXiv:2511.07689's finding that MiniCheck struggles
with negation, worst in legal text — on a clause type (retention duration)
that is this project's own flagship example in `CLAUDE.md`, and matches the
language of the one real document in the jobs database (§5). **This is not
a benchmark-only concern; it transfers.**

**Cross-model disagreement is the actionable signal.** On the one pair where
Flan-T5 failed, RoBERTa disagreed sharply (Flan-T5: 0.258/0.748 vs. RoBERTa:
0.977/0.370). On the four pairs both got right, they agreed within a
reasonable margin. A **dual-model check — escalate to review whenever the
two disagree by a wide margin — catches exactly the failure that a
single-model gate would silently pass through.**

---

## 5. Real data: the one row that exists (CONFIRMED, n=1 — sanity check, not a measurement)

`chunk_summaries` in `/opt/missing-link/jobs.sqlite` has exactly one row
(job `2b4c926a799a`, start_char 0, end_char 2202 — a health-records
retention memo, the one long document that persisted before failing). Read
read-only via `sqlite3.connect("file:...?mode=ro", uri=True)`. Its 3-sentence
summary, scored against its own source span with MiniCheck-Flan-T5-Large:

| sentence | prob | label | matches my own reading? |
|---|---:|---|---|
| "The 2025 audit ... deficiencies: (1) ... (2) ... (3) ..." | 0.9621 | 1 (supported) | yes |
| "Records must be kept for seven years from the last service (or until a minor turns 25, whichever is later)." | 0.9433 | 1 (supported) | yes |
| "The permission breach was corrected in March 2026." | 0.9837 | 1 (supported) | yes |

All three correctly scored supported, matching a manual read of the source.
**Small n — this is a plumbing sanity check, not evidence the model is
reliable**, especially given §4: an almost identically-worded retention
claim, tested in isolation with a shorter document and no repeated
corroborating context, was scored as UNsupported by the same model in the
same session. I did not run enough real-document rows to know whether
document length/redundancy is what saved this real-data case, or whether
this was luck.

---

## 6. Cross-check with RoBERTa-Large — folded into §4

Done: RoBERTa-Large (355M, `lytang/MiniCheck-RoBERTa-Large`) was run on all
five negation pairs. It caught the retention-obligation negation that
Flan-T5-Large missed, and agreed with Flan-T5-Large on the other four. See
the table in §4.

---

## 7. What this spike did NOT establish

- **A clean, minimal CPU-only install.** I worked around the bloated default
  CUDA-torch install rather than re-installing with the CPU-only wheel index
  — functionally identical result (CPU inference either way), but the lean
  production install path is unverified.
- **DeBERTa-v3-Large** (the third `docs/EVALUATION.md` candidate) was not
  tested — out of scope per the task brief (Flan-T5 primary, RoBERTa
  cross-check only).
- **Statistical confidence.** 8 timing pairs, 5 negation pairs, 1 real
  document. This is enough to prove the negation failure mode is real (it
  only takes one clean reproduction), not enough to estimate its *rate* on
  this project's actual document population.
- **Whether the silent-truncation failure mode (§3) occurs on real project
  documents.** Constructed synthetically; the one real DB row was
  well-punctuated and far under the length that triggers it.
- **`chunk_size` override as a speed optimisation.** §2 shows scoring at
  MiniCheck's native ~500-word granularity is ~8–9× faster than at
  `CHUNK_TOKENS`=4096, but I did not test explicitly passing
  `chunk_size=` to `.score()` as a tuning lever versus just accepting the
  slower whole-chunk-scoped cost that `docs/EVALUATION.md` calls for.
- **Thread-count tuning.** `torch.get_num_threads()` defaulted to 4
  (matching physical cores) with no manual intervention, so I did not need
  to A/B it the way `rpc-server -t` was measured — but I also didn't
  deliberately test whether an explicit `torch.set_num_threads()` changes
  anything here.
- **Multi-chunk batching** (batching pairs from *different* source chunks in
  one `.score()` call, not just multiple claims against the same chunk) —
  not tested; might change the batched-cost picture in §2.

---

## Artifacts

Scripts and raw logs for every run above are at
`/tmp/claude-1000/-home-debian1-homogenous-cluster/446bde09-7ffa-47e9-96f5-98eb28156db8/scratchpad/minicheck/`
(`test1_trivial.py` … `test5_realdata.py`, `test4b_confirm_truncation.py`,
and their `*_out.log` files) — scratch, not committed, but left in place in
case anyone wants to re-run or inspect them before this file is acted on.
