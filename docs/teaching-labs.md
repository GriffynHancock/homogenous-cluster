# Student lab exercises — "how can AI help and harm in a cybersecurity context?"

**Date:** 2026-08-23 | **Status:** design only. **Nothing was installed and
nothing was run on the cluster for this document.** The only commands executed
were read-only inspections of files already on node 1 (`/etc/default/llama-server`,
the pinned build's own `tools/server/README.md`, `missing-link/missing_link/*.py`)
and `git` metadata.

**Who this is for.** The instructor running a cybersecurity course on this
cluster, and whoever has to reset it at 8:55 on a Tuesday.

**Labelling.** Every claim is **CONFIRMED** (verified against a primary source
— a file on this machine, the tool's own source, or the tool's own docs),
**REPORTED** (someone else states it, unverified here), or **INFERRED**
(reasoning or arithmetic from other labelled facts). **No web-sourced
performance number is presented as measured on this hardware.** Every number
attributed to this hardware comes from `docs/measurements.md` and nowhere else,
per the standing rule in `CLAUDE.md`.

**Site-specific detail is deliberately absent.** IPs, hostnames and ports for
this deployment live in `network.md`, which is gitignored. This file uses
`<class-node>`, `<research-node>` and similar throughout.

---

## 0. The verdict, first

**Seven labs are viable. Four commonly-imagined exercises are not, and the
biggest of them is deepfake video.**

| # | Lab | Tool | Per-student class time | Per-student **node** time | Verdict |
|---|---|---|---:|---:|---|
| **1** | Prompt injection: direct, indirect, and which defences hold | n8n + shell | 60 min | **2.3 min** | viable, with a hard cap on prompt and `max_tokens` |
| **2** | AI-assisted phishing — generate one, then detect a hundred | n8n + shell | 75 min (or 2 × 50) | **0.9 min** | viable, comfortably |
| **3** | Log and alert triage — the funnel, not the firehose | Missing Link + shell | 2 × 50 min | **2.2 min** | viable **as an async job across two sessions**; not as a single sitting on one node |
| **4** | OSINT synthesis over a seeded synthetic corpus | Missing Link / n8n | 50 min | **1.2 min** | viable, because the model's pass is pre-run overnight |
| **5** | Synthetic media literacy — cost, metadata, detection | ComfyUI | 50 min | 30 s of image time; **0 LLM tokens** | viable **for images only**; video is out |
| **6** | Measuring an AI service from outside — availability as a security property | shell | 50 min | **~0** | viable, cheapest lab in the set, and it should be **first** |
| **7** | Supply chain: the community workflow and the custom node | throwaway ComfyUI VM | 50 min | **0** | viable, and it needs a disposable VM |

**Rejected:** deepfake *video* generation, SDXL/high-fidelity image generation
in class time, live OSINT against real people, fine-tuning or LoRA training,
in-class embedding/RAG builds, and anything that has to keep up with live
traffic. Reasons and numbers in §5.

**The single most important design fact is not a security fact, it is an
arithmetic one:** this cluster's LLM moves roughly **1,000 prompt tokens or 580
generated tokens per node-minute** (§1). A class of twenty in a fifty-minute lab
on one node has a total budget of about **50,000 prompt tokens and 30,000
generated tokens — for everyone, combined.** That is ~2,500 prompt and ~1,500
generated tokens per student for the whole session. **Every lab below is
designed against that number, and the labs that could not fit it were
restructured to move generation out of class time rather than pretended to
fit.**

---

## 1. The binding constraint: the class token budget

### 1.1 The measured inputs

All from `docs/measurements.md`, gpt-oss-120b F16 on a fleet node, mainline
llama.cpp b10369, `-t 4 --parallel 4`:

| Quantity | Measured | Where |
|---|---:|---|
| Prefill, sequential requests, server-reported | **16.3 tok/s** (16.29 / 16.26 / 16.27 / 16.36 — flat) | "ik_llama.cpp vs mainline … RELIABILITY A/B" |
| Generation, sequential, server-reported | **5.26 tok/s** | same |
| End-to-end wall clock, 1,060–1,410 prompt tokens, `max_tokens=64` | **76.8 – 98.9 s** | same |
| **Aggregate prefill at 4 concurrent slots** | **16.58 tok/s** | "Batching on sparse MoE", `llama-batched-bench -npl 4` |
| **Aggregate generation at 4 concurrent slots** | **9.73 tok/s** | same |
| Aggregate total at 4 concurrent slots | 14.54 tok/s | same |
| Replication across independent nodes | **~1.8× at N=2 (~90% of linear)** | "THE REPLICATION MEASUREMENT" |

**Two facts about that table decide the whole lab design.**

1. **Prefill does not get faster with concurrency.** 15.96 tok/s at batch 1,
   16.58 at batch 4 — a 4% change. Batching buys generation (1.79×) and nothing
   else, and it *collapses* at 8. So **reading is the expensive operation and
   more students does not make it cheaper.**
2. **Prefill is ~79% of document wall-clock on this hardware** (`CLAUDE.md`,
   from F27). A lab that hands the model a long input is spending almost all its
   time before a single token comes back.

### 1.2 The planning formula

**INFERRED — arithmetic on the measured aggregates, not itself a measurement:**

```
node-seconds  ≈  total_prompt_tokens / 16.6  +  total_generated_tokens / 9.7
```

**This formula is not a guess; it reproduces the published table it came from.**
`llama-batched-bench -npp 512 -ntg 128 -npl 4` moves 2,048 prompt and 512
generated tokens. 2048/16.58 = 123.5 s, which is the exact prefill wall-time
`docs/measurements.md` records for that run; 512/9.73 = 52.6 s; total 176.1 s
over 2,560 tokens = 14.54 tok/s, which is exactly the "Total t/s" the table
reports.

**Independent cross-check against the replication run** (4 concurrent requests
on one node, 6,469 prompt + 744 completion tokens, 425.5 s measured): the
formula predicts 466 s. It is **10% pessimistic**, which is the right direction
for a planning number. Note that run used a different engine and `-c 16384`, so
the agreement is corroboration, not validation.

### 1.3 What that buys a class

| | one node | two nodes (~1.8×) | three nodes (~2.7×, INFERRED by extension) |
|---|---:|---:|---:|
| Prompt tokens per minute | **~1,000** | ~1,800 | ~2,700 |
| Generated tokens per minute | **~580** | ~1,050 | ~1,570 |
| **50-min lab, 20 students — per student** | **~2,500 prompt / ~1,450 generated** | ~4,500 / ~2,600 | ~6,700 / ~3,900 |

**In plain terms: on one node, each student gets about four short LLM calls for
the entire lab.** That is the constraint. It is not a tuning problem — F11 says
generation already runs at ~99% of achievable bandwidth for dense models, F18
says raising `-ub` does not help prefill, and F12 says the four cores cannot
saturate their own memory bus. **The software tuning is exhausted; only
replication and workload design are left.**

### 1.4 Three levers that actually move it, and one that does not

**Lever 1 — move generation out of class time. This is the big one and it is
free.** The cluster's whole premise is that slowness is irrelevant when nobody
is waiting (`CLAUDE.md`, Missing Link). Anything a lab needs *as input* —
corpora, model dossiers, the phishing set, 20-step reference images — should be
generated by the instructor overnight as a batch job and handed to students as
an artefact. **Labs 2, 3, 4 and 5 all use this, and it is what makes them fit.**
It also makes the class-time activity *analysis*, which is the more valuable
skill anyway.

**Lever 2 — `reasoning_effort: "low"`.** `missing_link/worker.py` records, as
measured on gpt-oss-120b on 2026-08-17, same prompt, `--jinja` server: no kwargs
→ 89 completion tokens; `{"enable_thinking": false}` → 129 tokens (**ignored —
did nothing**, F35); `{"reasoning_effort": "low"}` → **61 tokens, 31% fewer**.
**Labelling caveat: those figures live in a source-code comment, not in
`docs/measurements.md`**, so under this project's own rule they are a strong
indication rather than a citable measurement — F35 itself (the `enable_thinking`
half) *is* a confirmed finding. On this model the analysis-channel reasoning is
billed as generated tokens, so a third off generation is a third off the most
expensive half of the class budget. Put it in the shared n8n
credential/workflow, not in each student's hands, and **re-measure it once if a
lab is going to depend on the margin.**

**Lever 3 — cap `max_tokens`, but not below the model's reasoning.** F21: a
reasoning model that hits `max_tokens` mid-thought returns **empty content, not
truncated content**. A student who sets `max_tokens=40` gets a blank answer and
concludes the cluster is broken. Set the floor in the shared workflow and say so
on the lab sheet — it is also a genuine lesson about model output being a
protocol that must be validated, not an answer.

**The lever that does not work: "just use a small model."** F54 suggested this
(*"a small model that fails visibly may be pedagogically better"*), and the
pedagogy argument stands — but **the speed argument is much weaker than it
sounds on this hardware.** `docs/measurements.md` has Qwen3-4B Q4_K_M on node 1:
pp512 **33.04 tok/s**, pp2048 **28.33**, tg128 **11.49**, against gpt-oss-120b's
pp512 **16.03**, pp2048 **15.88**, tg128 **6.05**. **A model with 1/29th the
parameters is about 1.8–2.1× faster at prefill and 1.9× at generation, not
10×** —
because prefill is compute-bound on four cores without AVX-512 (F7, F12, F18)
and that ceiling applies to every model. **INFERRED:** a sub-1B model would do
better than 4B, possibly much better on generation where bytes-per-token
dominates, but **nothing on this fleet has measured one.** If a class model is
wanted, that is a one-hour `llama-bench` run and it should happen before any lab
is designed around it — it changes Lab 1's shape by a factor that matters.

### 1.5 Two traps that will otherwise burn a lab session

- **`-c` is divided by `--parallel`.** `-c 32768 --parallel 4` gives
  `n_ctx_slot = 8192`. A student who pastes a 12,000-token log file gets a hard
  error, not a truncation. **Check it from the server's own startup log**
  (`journalctl -u llama-server@8080 | grep n_ctx_slot`), never infer it from
  `-c`. And do not raise `-c` for headroom: the measured control found `-c 65536`
  cost **33% more wall-clock on identical chunking** (`docs/measurements.md`,
  "Chunk-size sweep, extended").
- **`POST /tokenize` is free and authoritative** (F49). A pre-flight token count
  in the shared n8n workflow — refuse anything over ~2,000 tokens with a message
  saying why — costs nothing and stops one student from eating a third of the
  class budget by accident. **`WORDS_PER_TOKEN` guessing is wrong by 2× (F49);
  ask the server.**

---

## 2. Placement: which machine runs what on a class day

**This is settled by two findings, not by preference.**

**F44 (CONFIRMED on this hardware):** even at `nice -n 10`/`-n 15`, a CPU-bound
sidecar and `llama-server` were caught at 378.9% and 336.8% CPU simultaneously
on the same four physical cores, load average 8.23 against 0.6–0.9 idle, and the
sidecar's own rate degraded from 4.7 s/claim to **41.7 s/claim**. `nice` is not
a mitigation — F44 tested exactly that.

**F54 (CONFIRMED/REPORTED):** ComfyUI holds all cores for its entire runtime
with no gaps, and n8n's "it's only I/O glue" argument fails because the Code
node makes it CPU-bound on demand, at the request of twenty untrusted authors.

**F56 (CONFIRMED):** node 3 is joined, hardened, passing the #26500 gate, and is
a three-way bandwidth twin of nodes 1 and 2. N = 3.

**Recommended class-day topology:**

| Node | Runs | Notes |
|---|---|---|
| `<research-node-1>` | `llama-server`, the **research** Missing Link | Untouched by the class. Not reachable from the student segment. |
| `<class-llm-node>` | `llama-server` **only**, dedicated to the class | No student-controlled CPU consumer on it. This is the node §1's budget is about. |
| `<class-apps-node>` (node 3) | n8n, ComfyUI, the **class** Missing Link, nginx | No `llama-server`. A student's runaway Code node cannot starve the class's own inference. |
| **separate, disposable** | the deliberately-vulnerable ComfyUI target (Lab 7) | A VM on an isolated segment. **Never on a node that runs `llama-server` or Missing Link** — a cryptominer on an inference node is indistinguishable at first from a slow document job, and this project has misdiagnosed "the server is just busy" three times (F36, F39, F40). |

**Cost:** during class hours the research workload drops from R=3 to R=1. That
is acceptable and it is nearly free, because the two workloads are naturally
disjoint in time — Missing Link's premise is explicitly overnight work and a
class runs in daylight. A systemd timer that flips the class node between roles
at 08:00 and 18:00 is the cheapest possible implementation of that.

**Three levers on `llama-server` that exist and should be used, all CONFIRMED
from the pinned build's own README on disk (`/opt/llama.cpp/src/tools/server/README.md`,
b10369):**

- **`--api-key KEY`** — *"multiple keys can be provided as a comma-separated
  list"*, and `--api-key-file FNAME` reads one per line with `#` comments. **So
  per-group keys are available today with no new software.** Attribution is what
  makes a classroom work, and revoking a group is a file edit plus a restart.
- **`--alias gpt-oss-120b`** — the unit currently passes no alias, so n8n's
  model dropdown will render the full GGUF path (CONFIRMED, F54). Cosmetic, but
  confusing in front of a class. Note this is a **shared** unit; changing it
  affects Missing Link's requests too, so it needs the usual measured-change
  discipline.
- **`--no-slots`** — `GET /slots` is **enabled by default**. In this build the
  documented response contains per-slot sampling parameters, `n_ctx`,
  `is_processing`, `chat_format` and token counts; **it does not contain the
  prompt text** (CONFIRMED from the README's own example response in this build
  — worth re-checking live before relying on it). It is an activity and
  configuration enumeration surface, not a prompt-disclosure one. **Keep it on
  for Lab 6, which is built on it, and turn it off elsewhere if the instructor
  prefers.**

---

## 3. The labs

Each lab states: **objective — tool — time budget — what students do —
defensive counterpart — state written — instructor reset.**

Two conventions run through all of them:

- **Every offensive half is paired with its defensive half in the same session**,
  per the operator's guardrail. The generate-then-detect and attack-then-mitigate
  ordering is the pedagogy and it is also what keeps the artefacts useful rather
  than harmful.
- **Where an artefact could be mistaken for genuine, it carries a visible marker
  applied by the shared workflow, not by the student.** A marker a student can
  omit is not a marker.

---

### Lab 1 — Prompt injection: direct, indirect, and which defences actually hold

**Objective.** OWASP LLM01. Students discover, by doing it, that an LLM
**processes instructions and data on the same channel and cannot tell them
apart** ([REPORTED — OWASP Top 10 for LLM Applications 2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)),
that this is not a bug to be patched, and that the defences differ enormously in
how much they actually buy.

**Tool.** n8n for the indirect half (no-code, and the workflow *is* the
teaching artefact); plain `curl`/shell for the direct half, because iteration
speed matters and n8n's editor adds clicks per attempt.

**Time budget.** 60 minutes. **Node cost: 2.3 min/student → ~46 min on one
class node for 20 students, ~26 min on two.** That fits a 60-minute session on
one node only if the lab sheet enforces the caps below; it is comfortable on two.

The caps, which go on the lab sheet as rules, not suggestions:

| | Limit | Why |
|---|---:|---|
| System prompt | ≤ 120 tokens | it is re-prefilled on every attempt |
| Attack string | ≤ 60 tokens | same |
| `max_tokens` | **96**, set in the shared workflow | below ~80 the model returns **empty**, not short (F21) |
| `reasoning_effort` | `"low"`, set in the shared workflow | 31% fewer generated tokens, measured (§1.4) |
| Attempts per student | **8** | 8 × (180 prompt + 80 generated) = 1,440 + 640 tokens = 138 node-seconds |

**What students do.**

*Part A — direct injection (15 min).* The shared workflow sets a system message:
a support bot for a fictional company, holding one secret string it is told
never to reveal. Students get eight attempts to extract it. They will succeed;
that is the point. They record which framings worked and which did not.

*Part B — indirect injection (20 min).* **This is the important half, because it
is the shape of the real attack and the shape of this cluster's own workload.**
An n8n workflow reads a "customer email" from a local file and asks the model to
summarise it. The email — supplied by the instructor, then modified by students
— contains an instruction addressed to the model, hidden in an HTML comment or
in white-on-white text. Students watch a summariser take orders from the
document it was summarising. Then they write their own payload into a document
and hand it to a partner's workflow.

**The connection to make explicit:** Missing Link, running one node over, is a
document summariser that ingests uploaded PDFs. Part B is a live demonstration
of that system's threat model, on the same hardware, in the same building.

*Part C — defence, and measuring it (25 min).* Students try three mitigations
against **their own** Part A/B attacks and record the pass rate of each:

1. **Delimiters and spotlighting** — wrap untrusted content in a marked block
   and instruct the model to treat everything inside as data. Cheap. Reduces
   success; does not eliminate it.
2. **A quarantined second model** — the summariser has no privileges and its
   output is treated as data by a second call that does. This is Willison's Dual
   LLM pattern; **its author states plainly that it does not eliminate the risk**
   ([REPORTED](https://simonwillison.net/2025/Apr/11/camel/)), and CaMeL, the
   more rigorous successor, is reported at **67% mitigation on AgentDojo** —
   which is to say, a third still get through. *Costs a second call, so budget
   this part at two attempts, not eight.*
3. **Deterministic output validation** — the model's answer is checked **in
   code** against an allowlist before it is rendered. Does the returned string
   match one of the four permitted responses? Does every entity it names appear
   in the input? If not, it is dropped.

**The punchline, and it should be stated rather than left to be inferred:**
(1) and (2) reduce; (3) holds, because it does not require the model to be
trustworthy. This is `CLAUDE.md`'s own doctrine in a security costume —
*"prefer deterministic code to model judgement wherever the work is
computable"*, and *"model output is a protocol, not an answer"*. Students can
read `missing_link/worker.py`, where an invented `[Section 47]` citation is
**dropped rather than rendered**, and see the rule enforced in production code
on the machine they are attacking.

**Free bonus artefact, zero tokens: the reasoning channel.** gpt-oss emits its
chain-of-thought into a separate `analysis` channel
([REPORTED — OpenAI harmony format](https://github.com/openai/harmony)), and
this build exposes it: `--reasoning-format` selects `none` (leave thoughts
unparsed in `message.content`) or `deepseek` (put them in
`message.reasoning_content`), and per-request `reasoning_format` is accepted
(CONFIRMED, server README on disk). Show students the same request both ways.
**The lesson: "the model's internal reasoning" is an API field, not a secret —
and an application that logs the whole response object logs the chain-of-thought
into a log file that has a different audience than the answer did.**

**Defensive counterpart.** Part C, in the same session.

**State written.** n8n workflows and execution history; a handful of text files
in the workflow's read/write directory. Nothing on `llama-server` except KV
cache, which a restart clears.

**Instructor reset.** n8n restore-to-baseline (§6.2). ~2 minutes.

---

### Lab 2 — AI-assisted phishing: generate one, then detect a hundred

**Objective.** Two things, and the second matters more. (a) The "spot the bad
grammar" heuristic that a decade of security-awareness training rests on is
**dead**, and students should see it die by generating fluent, contextually
plausible pretext in one call. (b) The signals that actually survive — SPF/DKIM/
DMARC alignment, display-name versus envelope-From, URL host versus anchor text,
punycode, registration age — are **exactly the signals an LLM does not improve**,
because they are properties of the transport and the infrastructure, not of the
prose.

**Guardrail compliance, stated up front.** Students generate **exactly one**
message each, against an **instructor-supplied fictional organisation** on a
reserved `.example` domain. The shared n8n workflow appends a fixed banner and
an `X-Lab-Synthetic: true` header **in a Set node after the LLM node**, so
omitting it requires editing the shared workflow rather than deleting a line.
**No SMTP node exists on the instance** (`NODES_EXCLUDE`, §6.2) and the box has
no outbound egress, so nothing can be delivered anywhere. The deliverable is the
**detection scoresheet**, not the message; the messages are collected and
destroyed at reset.

**Tool.** n8n for generation; shell/Python for detection.

**Corpora.** For the "real" half of the mixed inbox:

- **SpamAssassin public corpus** — 6,047 messages (500 spam, 2,500 easy_ham,
  250 hard_ham, 1,400 easy_ham_2, 1,397 spam_2), **CONFIRMED from its own
  readme**, which states: *"Copyright for the text in the messages remains with
  the original senders."*
  ([source](https://spamassassin.apache.org/old/publiccorpus/readme.html))
  **Flag, honestly: that is a statement of fact, not a licence grant.** It is
  used constantly in published research and the archive is served publicly by
  the Apache Software Foundation, but **the instructor should make a deliberate
  decision to use it in class and must not redistribute it or commit it to this
  repository.** This project has already had a corpus source killed by a licence
  conflict (Hansard's CC BY-NC-ND, `docs/corpus-selection.md`), so the check is
  house practice, not pedantry.
- **Nazario phishing corpus** — REPORTED as the most widely used public phishing
  email dataset, hand-screened from the collector's own inbox, spanning 2015–2021
  in its later releases. Same caveat, more strongly: these are real messages
  containing real (attacker-controlled) addresses. Use in class, do not
  redistribute.

**Time budget.** 75 minutes, or better as two 50-minute sessions.
**Node cost: 0.9 min/student → ~17 min on one node for 20 students.**
Comfortably the cheapest LLM lab in the set, because it is one call each.

*Instructor overnight prep:* generate ~40 additional pretext messages across
several fictional organisations so the blind set is not just the class's own
work. **~34 min of node time overnight** (40 × (250 prompt + 350 generated)),
which is nothing.

**What students do.**

*Part A — generate, once (10 min).* One message each, from a fictional-company
brief. Students vary the pretext (invoice, HR policy, MFA reset, parcel) and
note how little steering it takes.

*Part B — detect, blind (25 min).* Students receive a mixed set: real historical
phish, real ham, and the class's own generations with banners stripped for the
blind test (retained in the instructor's master copy). They classify by hand and
score themselves. **Expect the AI-generated messages to be classified as
legitimate more often than the real phish** — that result is the lab.

*Part C — the checks that survive (25 min).* Students write deterministic
detectors over the same set:

- envelope-From vs header-From vs display-name mismatch
- SPF/DKIM/DMARC results as recorded in `Authentication-Results`
- URL host vs anchor text; punycode and homoglyph domains
- `Received` chain shape

They rescore. **These fire on the AI-generated messages exactly as hard as on
the human-written ones, because the model wrote the prose and not the headers.**
That is the transferable lesson, and it is the same lesson as Lab 1 Part C.

*Part D — the trap (15 min, and do not skip it).* Students run an off-the-shelf
"AI-generated text detector" over the mixed set. They will find both false
positives and false negatives. Then give them the research: a Stanford study
found detectors **misclassified more than half of 91 TOEFL essays by non-native
English speakers as AI-generated**, with one detector flagging ~98% of such
essays, while native-speaker text was rarely misclassified
([REPORTED](https://themarkup.org/machine-learning/2023/08/14/ai-detection-tools-falsely-accuse-international-students-of-cheating)).
The underlying cause is that predictable vocabulary reads as machine-written, so
the tool systematically penalises writers with constrained linguistic resources.

**The lesson to teach explicitly:** *"detect the AI" is not the defence. It is
an unreliable classifier with a discriminatory failure mode, and deploying it as
a control does harm of its own.* This is the sharpest "AI harms" moment
available in the whole set, and it costs no compute.

**Defensive counterpart.** Parts B, C and D — three quarters of the lab.

**State written.** n8n workflows and executions; generated messages in the
workflow directory.

**Instructor reset.** n8n restore-to-baseline; delete the generated-message
directory. Corpora are on a read-only mount and are never touched.

---

### Lab 3 — Log and alert triage: build the funnel, not the firehose

**Objective.** This is the lab that fits this hardware's actual strength —
document work — and it teaches the most operationally useful thing in the set:
**the LLM cannot read the data and never will, so the design problem is the
reduction that happens before it.** Then, having built the funnel, students
measure whether the model's narrative is *faithful* to the alerts it was
actually given. It will not be, and finding out how it fails is the second half.

**Dataset.** **AIT Alert Data Set (AIT-ADS)** — synthetic security alerts from
Suricata, Wazuh and AMiner, generated over the AIT Log Data Set V2's eight
simulated enterprise testbeds (mail server, file share, WordPress, VPN,
firewall), each subjected to labelled multi-step attack scenarios.
**2,655,821 alerts**, normalised to JSON, **CC BY 4.0**, 96.2 MB compressed
([CONFIRMED from the Zenodo record](https://zenodo.org/records/8263181); the
paper is [Landauer et al., CSET 2024](https://dl.acm.org/doi/fullHtml/10.1145/3675741.3675748)).
The underlying **AIT Log Data Set V2** is also CC BY 4.0
([REPORTED](https://zenodo.org/record/5789064)).

**This is the right dataset for three independent reasons:** it is **synthetic**
(no real people, no live capture, no ethics problem), it carries **line-level
ground truth** so students can be scored rather than merely impressed, and it is
**CC BY 4.0** so it can be redistributed to students with attribution.

**Tool.** Plain shell and Python for the funnel; **Missing Link** for the LLM
pass, because Missing Link is exactly this — an async queue for document work.

**Time budget.** **Two 50-minute sessions, and that structure is the honest
answer rather than a compromise.** Node cost, on a 1,500-token digest and 400
generated tokens: **2.2 min/student → ~44 min on one node, ~24 min on two.** A
3,000-token digest doubles it to 78 min on one node, which does not fit a single
sitting.

**So: students build and submit in session 1, and analyse in session 2.** They
watch their job sit in a queue behind nineteen others, which is not a defect of
the lab — it is the cluster's whole thesis (*"a summary that arrives overnight
beats one that never gets written"*) delivered as an experience rather than a
claim.

**What students do.**

*Session 1, Part A — confront the volume (15 min).* Students are handed one
testbed's alert stream and asked, first, to paste it into the model. It will not
fit: `n_ctx_slot` is 8,192 tokens and the stream is millions of alerts. **Let
them hit the wall.** Then compute, using §1.2's formula, what it would cost to
feed all 2.65M alerts through this cluster at any chunk size. The answer is
years. This is the lesson and it takes ten minutes.

*Session 1, Part B — build the funnel (30 min).* Deterministic reduction, in
code, no model involved:

- normalise and deduplicate identical alerts
- group by (signature, source host, destination host)
- bucket by time window
- suppress a known-benign baseline learned from an attack-free window
- rank the residue by rarity

**Target: under 1,500 tokens of digest from millions of alerts.** Students
compare their reduction ratios. Then they submit the digest to Missing Link and
go home.

*Session 2, Part C — audit the model (30 min).* Students read the incident
narrative their job produced and check every specific claim against the digest
that was actually supplied. **Expect invented CVE numbers, invented hostnames,
and confidently wrong attack chains.** They score: how many named entities in
the narrative appear in the input?

*Session 2, Part D — automate the audit (20 min).* Students write the check they
just did by hand: every hostname, IP, port and signature ID the model names must
appear verbatim in the input, or it is flagged. **This is a deterministic
containment check and it is 40 lines of Python.** They then read
`missing_link/cascade.py` on the machine in front of them, which is the
production version of the same idea, and `docs/faithfulness-cascade.md`, which
is the argument for it.

**The finding to hand them at the end, because it is this project's and it is
counterintuitive:** F41 — *a faithfulness classifier's reliability DEGRADES WITH
EVIDENCE LENGTH*, so the more context you give the checker, the worse the
checking gets; and F42 — *the reduce step launders a fabrication into the final
summary, and it was caught by string comparison, not by a model.* **The cheap
deterministic check outperformed the expensive probabilistic one, on this
hardware, on real output.**

**Defensive counterpart.** Parts C and D are the defence; the "harm" is what the
model does in Part C when left unchecked.

**State written.** Missing Link jobs, chunk-level progress rows and corpus
entries in a **SQLite DB, in WAL mode** (`docs/measurements.md`). See §6.3 — WAL
mode has a specific and easily-missed reset trap.

**Instructor reset.** Stop the class Missing Link, replace its DB with the
golden copy (**including `-wal` and `-shm`**), start. ~30 seconds.

---

### Lab 4 — OSINT synthesis over a seeded synthetic corpus

**Objective.** Three, in order of importance. (a) **The harm in OSINT is
aggregation, not disclosure** — no single fragment is secret; the dossier is.
(b) An LLM is unusually good at exactly that aggregation, which is the "AI
harms" point made concrete. (c) It also **invents links that were never there**,
which is the "and it lies confidently" point — and the students can prove it,
because the instructor planted the real links and knows what they are.

**No live scraping. No real people.** Per the operator's guardrail, and for the
practical reason that scraping from an institution's network creates problems
the course does not want. There is **no off-the-shelf synthetic OSINT corpus**
worth using that this survey could find, so the instructor seeds one — which is
better anyway, because ground truth is then known by construction.

**The corpus, and how to build it (instructor, overnight).** 8–12 fictional
personas. Identities from a synthetic-persona generator (Faker-style: names,
addresses, employers) on reserved `.example` domains, with **no real photographs
of anyone**. For each persona, the cluster generates overnight a scatter of
documents: a staff directory entry, two conference bios, a forum post, a job ad
naming a team, an internal-looking wiki page, and a CSV of usernames. **Links
are planted deliberately:** a username reused across two sites; a birth date in
one document and a "security question" answer in another; a project codename
that appears in a bio and in an unrelated procurement notice; EXIF coordinates
on a generated image that match an address in the job ad.

*Overnight cost:* ~12 personas × 6 documents × ~600 generated tokens ≈
**74 min of node time.** Plus the model's own synthesis pass (below), ~47 min.
**Both fit one night comfortably**, and this is precisely the workload the
cluster exists for.

**The pre-run pass is what makes the lab fit.** The instructor also runs, in the
same overnight batch, the model's synthesis over each persona's document bundle
— producing a dossier per persona. **Students are given the dossier as an
artefact to audit rather than waiting for it to generate.** That flips the lab
from "wait for the model" to "check the model", which is both cheaper and the
more valuable skill.

**Tool.** Missing Link or n8n for the one live call each student makes; shell
for the tracing.

**Time budget.** 50 minutes. **Node cost: 1.2 min/student → ~23 min on one
node** (one verification call each of ~800 prompt + 200 generated tokens).

**What students do.**

*Part A — do it by hand first (15 min).* Each student gets one persona's
document bundle and ten minutes to build a dossier manually. They will find some
of the planted links and miss others.

*Part B — read the machine's version (10 min).* They receive the pre-generated
dossier for the same persona. It will be more complete than theirs and will have
found links they missed. **That is the harm demonstrated: the aggregation cost
just went to near zero, and the aggregation was always the dangerous part.**

*Part C — audit it (15 min).* Every claim in the dossier is traced to the
document that supports it. **Students will find claims that trace to nothing.**
They classify each: supported / unsupported / contradicted. Compare against the
instructor's planted-link list. **Never ask the model where a claim came from** —
`CLAUDE.md` records that asking a model for a location scores ~38% on the
*easier* task of merely validating one. Resolve the label to a span in code.

*Part D — break the inference (10 min).* Given a persona's planted link chain,
students identify **the minimum set of documents whose removal breaks it**. This
is the defensive skill: footprint reduction is not "delete everything", it is
"find the pivot". They then make one live LLM call re-running the synthesis with
those documents removed, and check that the inference is actually gone.

**Defensive counterpart.** Parts C and D.

**State written.** One live job per student in the class Missing Link; the
corpus itself is on a read-only mount.

**Instructor reset.** Same as Lab 3 — restore the golden Missing Link DB. The
corpus is immutable and does not need resetting.

---

### Lab 5 — Synthetic media literacy: the cost, the metadata, and honest detection

**Start with what this hardware cannot do, because the alternative is promising
a class something that will not render.**

**Video generation is not reachable.** F54 / `docs/comfyui-feasibility.md`:
a video diffusion step denoises every frame's latent simultaneously, so a short
480p clip is **20–80× the per-step cost of one 512×512 image**, putting a
five-second clip at **tens of hours to days on one node** (INFERRED from frame
count and latent area; the conclusion survives being wrong by 3× in either
direction). **Do not design a deepfake-video lab here.** If the course must show
one, analyse a published example — that is a better exercise anyway, because
analysing is the skill and generating is not.

**What is reachable:** SD-Turbo or SDXS at **1 step, ~25–35 s per 512×512 image
per node** (INFERRED in `docs/comfyui-feasibility.md` §2.3 from two independent
REPORTED CPU anchors on 4-core-class AVX2 hardware; **not measured here**).
Standard 20-step SD1.5 is **~10 min/image** — overnight batch only. SDXL at
1024² is **1–2 h/image** — out.

**Licence note:** SD-Turbo / SDXL-Turbo ship under the **Stability AI
Non-Commercial Research Community License**, which explicitly names
*"applications in educational or creative tools"* and *"research on generative
models, including understanding the limitations of generative models"* among
permitted uses ([REPORTED](https://huggingface.co/stabilityai/sdxl-turbo/blob/main/LICENSE.md)).
That is this lab, described almost word for word. Read the actual `LICENSE.md`
of the specific checkpoint before class; Stability has revised these terms more
than once.

**Objective.** (a) See how cheap synthesis has become, by paying the cost
yourself and finding it is thirty seconds. (b) Learn that **everything you
publish carries metadata you did not intend to publish**. (c) Learn, honestly,
that **perceptual detection is a losing game and provenance plus process are
not.**

**Guardrails, as designed in.**

- **Consent is the default, not an afterthought.** Students may use their own
  likeness as an img2img source, **or** an instructor-supplied obviously
  fictional face. Nobody is required to use their own image and nobody uses
  anyone else's.
- **Provenance is applied by the shared workflow, in three layers**: a
  **burned-in visible caption** added by an overlay node after the sampler; the
  **workflow JSON ComfyUI already embeds in the output PNG** (CONFIRMED —
  dragging a generated PNG onto the canvas reconstructs the graph, which is why
  it is there); and, optionally, a **C2PA manifest** attached in a post-step.
  C2PA binds signed assertions about how an asset was made to the asset's bytes
  via hard (cryptographic) and soft (perceptual/watermark) bindings
  ([REPORTED — C2PA specification](https://spec.c2pa.org/specifications/specifications/2.4/specs/C2PA_Specification.html)).
  Doing it here means students have *seen* a content credential rather than
  heard about one.
- **Persona generation, if used at all, produces obviously fictional
  characters.** Never a named real person, never a public figure, never a
  face-swap onto a third party.
- **Outputs do not leave the box.** No egress from the ComfyUI host (which is
  also the botnet mitigation, §5 of `docs/comfyui-feasibility.md`).

**Tool.** ComfyUI on the class apps node.

**Time budget.** 50 minutes. **ComfyUI executes one prompt at a time from a
single FIFO queue** (CONFIRMED, `docs/comfyui-feasibility.md` §4), so
20 students × ~30 s = **~10 minutes of queue per node**, which fits. **Zero LLM
tokens** — this lab can run concurrently with another group's Lab 3 batch.

*Instructor overnight prep:* ~20 reference images at 20 steps ≈ **3.3 h on one
node.** Fine overnight, impossible in class — and telling students that number
is itself part of Part A.

**What students do.**

*Part A — the cost (15 min).* One image each. Then the arithmetic: on a
2016-vintage salvaged workstation with no GPU, using zero purchased software, a
plausible synthetic image costs thirty seconds. On a $300 used GPU it costs
under a second. **The barrier to entry is gone and it is not coming back.**

*Part B — the metadata lesson (15 min, zero compute).* Students drag a
neighbour's PNG onto the ComfyUI canvas and watch the entire graph reconstruct —
prompts, model choices, LoRAs, filesystem paths. Then EXIF on ordinary phone
photos. Two conclusions, and the second is the one they will not have thought
of: **you publish more than you meant to**, and **an adversary strips metadata
first, so its absence is itself a signal.**

*Part C — detection, honestly (20 min).* A mixed set: 1-step class generations,
the instructor's overnight 20-step images, and real photographs. Students sort
them, then are scored. Then the uncomfortable framing:

> Sorting 1-step 512×512 SD output from photographs is easy today. It was harder
> last year and it will be harder next year. **Nothing you learned in the last
> twenty minutes is a durable control.**

**The durable controls are: provenance (C2PA and signed capture), verification
by a second channel (call the person back on a number you already had), and
process (dual authorisation on payments, out-of-band confirmation of unusual
requests).** Tie it to the actual attack: voice- and video-cloned executive
fraud, which is defeated by a callback and not by squinting at pixels.

**Defensive counterpart.** Parts B and C — two thirds of the lab.

**State written.** ComfyUI `output/`, `input/`, `user/` — all under a single
`--base-directory` (§6.4).

**Instructor reset.** Destroy and recreate the container; models are on a
separate read-only bind mount and are not touched. ~1 minute.

**One modality left explicitly open: voice.** CPU-only voice cloning may be
reachable where video is not — the models are far smaller and the output is 1-D.
**This survey did not establish that and no number for it exists on this
hardware.** If the instructor wants it, it is a one-hour feasibility test
(generate one 10-second clip, time it) before anything is designed. **And it is
the highest-risk modality in the set for the guardrails**, because a cloned
voice of a real person *is* the live attack. If it proves viable: consented
pre-recorded student voices only, an audible watermark tone in the shared
workflow, and use it for the **detection** half only — do not run a
generate-a-target exercise on voice.

---

### Lab 6 — Measuring an AI service from outside: availability as a security property

**Put this one first in the semester.** It is the cheapest lab in the set, it
teaches the shared-resource etiquette every other lab depends on, and it is
built directly on the hardest lesson this project learned.

**Objective.** (a) **A service's self-report is not evidence.** (b) Availability
is a security property, and a monitoring system that trusts the thing it
monitors is a vulnerability class, not an implementation detail. (c) A shared
inference endpoint with four slots and no quota is a **denial-of-service surface
that requires no exploit** — one student's oversized prompt degrades nineteen
others.

**The material is this repository's own findings, and they are unusually good
teaching artefacts because they were painful:**

- **F36:** `llama-server` hung *alive* — accepting TCP, answering nothing,
  invisible to `Restart=always`.
- **F39:** the watchdog built to fix F36 then **killed a healthy job after 79
  minutes**, because `/health` is delivered through the very queue it is
  reporting on. *"`/health` is the server's opinion of itself."*
- **F40:** F36's diagnosis was **wrong**. `ggml_abort` forks from a
  multithreaded process, the parent blocks in `wait4()` forever, and **the
  forked children inherit the listening socket** — so `Restart=always` sees a
  live process *and a port check sees an open port*. Three independent liveness
  signals, all defeated at once.
- **F51 / its addendum:** the fix was a **byte-progress** signal, and the
  decisive measurement was that a receiving `rpc-server` burns **~0% CPU while
  1 MB/s of real payload is arriving** — so the CPU-progress signal that saved
  `llama-server` would not have saved the shard upload.

**The through-line, which is the lab's thesis:** *a liveness probe must test
neither the process nor the port, but progress.*

**Tool.** Plain shell. `curl`, `ss`, `systemctl`, `journalctl`.

**Time budget.** 50 minutes. **Node token cost: essentially zero** — the point
of the lab is measurement, not generation.

**What students do.**

*Part A — the three naive probes (15 min).* On the class node, students build
three monitors: process alive (`systemctl is-active`), port open (`ss -ltn` or a
TCP connect), and `/health` returns 200. All three go green. They then load the
server with a long request and watch `/health`'s *latency* climb while its
*status* stays 200. `docs/measurements.md` has the measured `/health` latency
behaviour and why its timeout is not diagnostic.

*Part B — defeat them (15 min).* `SIGSTOP` the server process, with permission,
on the class node only. Process: alive. Port: open. `/health`: hangs, which a
naive monitor with a generous timeout reads as "busy". **Then read F40 and
understand that this is not a contrived scenario — it is what actually happened
here, twice, and the second diagnosis corrected the first.**

*Part C — build the probe that works (10 min).* `GET /slots` (enabled by
default, CONFIRMED) reports `is_processing` and per-slot token counts; `GET
/metrics` is a Prometheus-compatible exporter behind `--metrics`. **A probe that
samples a counter twice and asks whether it moved cannot be fooled by a live
process or an open port.** Students build it and re-run Part B against it.

*Part D — denial of service without an exploit (10 min).* With permission and on
the class node only, one student issues a single request with a very large
prompt. Everyone watches their own latency. Then the mitigations, which are all
real levers on this build: **`--api-key` with a comma-separated per-group list**
(CONFIRMED from the on-disk README), an nginx request-rate limit, and a
**`POST /tokenize` pre-flight** in the shared workflow that refuses oversized
prompts before they reach a slot (F49: `/tokenize` is free and authoritative;
guessing token counts from word counts is wrong by 2×).

**Defensive counterpart.** Parts C and D.

**State written.** Nothing persistent.

**Instructor reset.** `systemctl restart llama-server@8080` on the class node.
**Time this once and put the number in the runbook** — F3 records that model
load is single-core serialised, and on a ~65 GB GGUF it is the single longest
step in any reset in this document. Measure it; do not estimate it.

---

### Lab 7 — Supply chain: the community workflow and the custom node

**Objective.** The most complete worked example of software supply-chain risk
available on one port. Students see arbitrary Python executed at startup from a
directory, a package manager that is itself the privilege-escalation primitive,
unsafe deserialisation as a live CVE, and a real in-the-wild campaign to analyse.

**All CONFIRMED / REPORTED in `docs/comfyui-feasibility.md` §5, with sources
there:** ComfyUI loads custom nodes at startup via
`spec_from_file_location()` + `exec_module()` across a ~1,300-extension
ecosystem with no uniform security standard; **CVE-2025-67303** is
unauthenticated arbitrary file upload → RCE in ComfyUI-Manager; **CVE-2026-68771**
is unauthenticated RCE via unsafe pickle deserialisation in ComfyUI ≤ 0.23.0;
and a 2026 campaign mass-exploited **1,000+ exposed instances** into an XMRig +
lolMiner cryptomining and proxy botnet whose escalation step, where no vulnerable
node was present, was **installing ComfyUI-Manager and retrying**.

**Tool.** A **deliberately unpatched, disposable ComfyUI VM** on an isolated
segment. **Never a node running `llama-server` or Missing Link** — §2's hard
rule.

**Time budget.** 50 minutes. **Zero LLM tokens, zero image generation.**

**What students do.** Read a shared workflow JSON before importing it and find
what it does. Inspect a `custom_nodes` directory and identify the code that runs
at import time. Reproduce one of the named CVEs against the throwaway instance.
Then, in the second half, harden a fresh instance: `--disable-all-custom-nodes`
plus `--whitelist-custom-nodes` for a reviewed set, no ComfyUI-Manager, pinned
version, no egress, an unprivileged systemd unit with `ProtectSystem=strict`,
and nginx basic auth in front of an interface that **has no authentication of
its own at all**.

**The framing that makes it a general lesson rather than a ComfyUI lesson:**
students have, in the previous six labs, downloaded models, imported workflows,
installed community nodes and pasted JSON from the internet. **Part of this lab
is auditing what they themselves did in labs 1–6.**

**Defensive counterpart.** The second half.

**Instructor reset.** **Rebuild the VM from its image. Always, between every
class.** This is the one lab where a successful student *is* a compromise, and
where repairing rather than replacing is the wrong instinct.

---

## 4. Where the labs fit together

| Week | Lab | Runs concurrently with | Why |
|---|---|---|---|
| 1 | **6 — measuring from outside** | anything | ~0 tokens; teaches the etiquette the rest depend on |
| 2 | **1 — prompt injection** | — | needs the class node to itself |
| 3 | **2 — phishing** | Lab 5 or 7 for another group | cheapest LLM lab |
| 4–5 | **3 — alert triage** (2 sessions) | Lab 5 or 7 between sessions | the queue wait is the point |
| 6 | **4 — OSINT synthesis** | Lab 5 or 7 | corpus and dossiers pre-generated |
| any | **5 — synthetic media** | any LLM lab | zero LLM tokens, ComfyUI on a different node |
| any | **7 — supply chain** | any LLM lab | zero cluster load, isolated VM |

**Labs 5 and 7 are the "shock absorbers".** They consume no LLM budget, so they
can be scheduled opposite any group that is waiting on a queue, which is how a
two-stream timetable stays inside §1's arithmetic.

---

## 5. Rejected, and why

**Saying so is more useful than a lab that times out in front of a class.**

| Exercise | Verdict | Reason |
|---|---|---|
| **Deepfake video generation** | **Not reachable. Not slow — out of reach by one to two orders of magnitude.** | INFERRED, F54 / `docs/comfyui-feasibility.md` §2.5: a video diffusion step denoises every frame's latent at once, so a 480p × 81-frame clip is 20–80× one image's per-step cost — **tens of hours to days per five-second clip on one node**. There is no configuration of this hardware that changes it. If video is required, it needs a GPU: a used **RTX 3060 12 GB**, after checking the P510's PSU has an 8-pin PCIe drop. The **Quadro P600 already fitted is rejected** — PyTorch dropped Pascal from CUDA 12.8 builds at 2.8, CUDA 13.0 drops sm_61 entirely, and ComfyUI's requirements now name CUDA 13.0, so using it means an unpatchable stack on a machine students are invited to attack. |
| **SDXL / high-fidelity images in class** | Overnight batch only | INFERRED, `docs/comfyui-feasibility.md` §2.4: **1.5–2.5 h per 1024² image** on one node. Even standard 20-step SD1.5 is ~10 min, so a class of 20 queues 3+ hours. |
| **Live OSINT against real people** | **Excluded by guardrail and by practicality** | Operator guardrail. Also: scraping real targets from an institution's network creates legal and reputational problems the course does not want, and produces no ground truth to grade against. Lab 4's seeded corpus is strictly better pedagogy because the answers are known. |
| **Fine-tuning or LoRA training** | Not viable | No usable GPU (above), and training a 120B MoE on 4 cores without AVX-512 is not a proposition. Even a small LoRA on SD1.5 is compute-bound on exactly the resource F12 says this fleet lacks. |
| **In-class embedding / RAG build** | Not viable in class; possible overnight | gpt-oss-120b is not an embedding model, so it needs a second model loaded; and embedding a corpus means one full prefill pass over every document at ~16 tok/s. Also, `docs/DESIGN-NOTES.md` analyses RAG for this project with numbers and does not adopt it — do not re-propose it as a lab without reading that first. |
| **Real-time / streaming AI SOC** | Not viable | 5.26 tok/s generation. Anything with "live" or "real-time" in the description is out by two orders of magnitude. The async framing is not a workaround, it is the correct architecture (F19). |
| **Sending generated phishing to anyone** | **Excluded** | No SMTP node on the instance, no egress from the host. The deliverable is the detection scoresheet. |
| **Long multi-turn red-team conversations** | Restructured, not rejected | A continuing conversation re-prefills its history unless it lands on the same slot with a warm prefix cache — with four slots and twenty students, **assume no cache hit** (INFERRED; not measured for this access pattern). Lab 1's single-turn, capped-length format is the same lesson at a tenth of the cost. |
| **"Just run a small model so it's fast"** | Weaker than it sounds — measure first | See §1.4. Measured on this fleet, a 4B dense model is only **~1.8× prefill and ~1.9× generation** against the 120B MoE, because prefill is compute-bound on four cores regardless of model size. A sub-1B model may be much better on generation; **nothing here has measured one.** One hour of `llama-bench` settles it. |
| **`--parallel 8` to serve more students** | **Actively harmful** | Measured: at batch 8, **prefill collapses 56%** and total system throughput (7.86 tok/s) falls **below batch 1** (11.50). Prefill wall-time went from 123.5 s to 557.8 s — 4.5× longer for 2× the work. `--parallel 4`, never 8. |

---

## 6. Recovery: what "reset the lab to a known state" means, per tool

**The operator asked for this explicitly, and it is the part that decides whether
the labs survive contact with a class.**

### 6.0 The principle

**For a playground students are expected to break, the useful primitive is
"restore to a known-good class baseline", not "incremental backup".** Nobody
wants a student's Tuesday-afternoon state back; they want Tuesday morning.

And, from `docs/comfyui-feasibility.md` §5.2 and Lab 7: **for anything students
are invited to attack, the recovery mechanism must also recover from a
*compromise*, not just from a mistake.** That rules out "repair the service in
place" and rules in "destroy and recreate from an image".

**Ranked, cheapest-that-actually-works first:**

1. **Container or VM re-create from an image, with a golden data volume
   restored from a tarball.** Recovers from compromise. This is the answer for
   ComfyUI and n8n.
2. **Provisioning script plus a golden-directory tarball.** No new technology,
   consistent with this project's standing rule that provisioning is *"a Debian
   preseed plus a re-runnable `setup.sh`, not a disk image"*. This is the answer
   for Missing Link.
3. **Filesystem snapshots (LVM-thin / btrfs) of the mutable subvolume**, if the
   host filesystem already supports it. Cheap if available; **not worth
   reformatting for**.

### 6.1 `llama-server` — the LLM endpoint

| | |
|---|---|
| **Persistent state** | **None.** KV cache only, which is per-slot and cleared by a restart. |
| **"Reset" means** | `systemctl restart llama-server@8080` on the class node. |
| **Cost** | Model load. **F3 (REPORTED, not measured here): model load is single-core serialised**, and on a ~65 GB GGUF this is plausibly the longest single step in any reset in this document. **Measure it once and write the number in the runbook rather than carrying an estimate.** |
| **Also available** | `POST /slots/{id}?action=erase` clears one slot's prompt cache without a restart (CONFIRMED, server README on disk) — much cheaper when a single slot is the problem. |
| **Attribution / revocation** | `--api-key` takes a comma-separated list; `--api-key-file` reads one per line with `#` comments. Per-group keys, revoked by editing a file and restarting. |
| **Trap** | The unit is **shared with the research workload**. Restarting it during class is free; restarting it while a research job is mid-flight destroys that job's in-flight work — which is exactly how F39 lost 10m55s. **Check before restarting.** This is the strongest argument for the §2 topology, where the class node is not a research node. |

### 6.2 n8n

| | |
|---|---|
| **Persistent state** | Everything in `~/.n8n` — `database.sqlite` holds workflows, encrypted credentials, executions, users, tags and settings; binary data and logs live alongside it. Under Docker this is one volume at `/home/node/.n8n`. (CONFIRMED, `docs/n8n-feasibility.md` §5.1.) |
| **"Reset" means** | Stop the container, replace the volume contents with the golden tarball, start. |
| **Cheapest mechanism** | **Docker volume re-create from a golden tarball.** Docker also restarts automatically on OOM, which matters because n8n is **one process** — one student's runaway node takes the whole class down (REPORTED, n8n's own docs on memory issues). |
| **THE trap, and it is the one that silently fails** | **Set `N8N_ENCRYPTION_KEY` explicitly, in the unit/compose file, BEFORE first launch and before a single credential exists.** If it is unset, n8n generates a random key on first launch and stores it **inside `~/.n8n`** — i.e. inside the thing you are about to overwrite. Restore with a mismatched key and you get every workflow back and **not one working credential**, surfacing as *"Credentials could not be decrypted"* (CONFIRMED mechanism + REPORTED failure, `docs/n8n-feasibility.md` §5.2). **The key must live outside the snapshot and be identical across restores.** |
| **Per-student rescue without a full rollback** | `n8n export:workflow --backup --output=…` writes one file per workflow; `export:credentials --backup` does the same. Run nightly from cron so one student's work can be restored without resetting the class. |
| **Do NOT enable** | `N8N_ENV_FEAT_ENCRYPTION_KEY_ROTATION` — n8n's own docs: *"Enabling encryption key rotation is a one-way change. There's no rollback path."* |
| **Pre-class hardening that also reduces reset frequency** | `NODES_EXCLUDE` for `executeCommand`, `readWriteFile` and any send-email node; `N8N_BLOCK_ENV_ACCESS_IN_NODE=true` (**default is `false`**); leave `NODE_FUNCTION_ALLOW_BUILTIN`/`_EXTERNAL` unset; `N8N_RESTRICT_FILE_ACCESS_TO=<dir>`; `N8N_PUBLIC_API_DISABLED=true`. All CONFIRMED in `docs/n8n-feasibility.md` §3.5. |
| **The boundary that is not a boundary** | Per-user workflow privacy on a default Community install is **a UI convention, not a security boundary**. n8n's own words: with task runners in the default `internal` mode, *"anyone who can edit a workflow could potentially read your database, encryption key, stored credentials, and environment variables."* **A student will try it, and arguably that is the lesson** — but it means the reset must be a full restore, and it means no credential on that instance may be worth stealing. |
| **Concurrency** | `N8N_CONCURRENCY_PRODUCTION_LIMIT` **explicitly does not apply to manual executions** — the only kind a classroom generates. **The real backpressure has to come from the LLM endpoint**, i.e. §6.1's per-group API keys plus the `/tokenize` pre-flight. |
| **Test the restore once, before the first class, on a machine you can afford to break.** | A restore that has never been exercised is a plan, not a backup. F34 is this project's own version of that lesson: 41 tests passed against a pipeline that had never processed a document. |

### 6.3 Missing Link

| | |
|---|---|
| **First, the non-negotiable** | **Run a SECOND instance for the class.** Its own SQLite file, its own port, its own `ML_AUTH_TOKEN`. **Students must not reach the research instance**, whose corpus is the measuring instrument for this project's actual work (F52). |
| **Auth exists now** | F54 found the live instance bound to `0.0.0.0:8000` with **no security scheme at all**, exposing `POST /corpus/{doc_id}/delete`, `/jobs/reorder` and `/jobs/{id}/cancel`. That is now closed: `missing_link/auth.py` is on `main` (commit `1532865`, *"one shared credential in front of the API (F54)"*), accepting HTTP Basic or Bearer against `ML_AUTH_TOKEN`. **Read its own module docstring before relying on it** — it says plainly that it is *"a door lock, not a security system"*: one shared secret, no users, no roles, no sessions, no audit trail. **A shared credential is not attribution.** For a class, the per-group `--api-key` on `llama-server` (§6.1) is the attribution lever, not this. |
| **Persistent state** | The job store SQLite DB (jobs, chunk-level progress, failure history), plus corpus rows and uploaded documents. |
| **"Reset" means** | Stop the class instance, copy the golden DB over it, start. ~30 seconds. |
| **THE trap** | **The job store is in WAL mode** (`docs/measurements.md`). A reset that copies only `missing-link.db` and leaves a stale `-wal` and `-shm` alongside it restores a database that is **not the one you think it is**. Either copy all three files together, or checkpoint (`PRAGMA wal_checkpoint(TRUNCATE)`) with the service stopped and copy the single file. **Stop the service first either way** — copying SQLite while it is being written is the classic way to capture a torn database. |
| **Second trap** | This repo's standing rule: **assert row counts before destructive SQL.** Run the `SELECT count(*)` with the *identical* predicate, print it, and stop if it is 0 or outside expectation. `CLAUDE.md` records a `substr(document,1,5)='%PDF'` off-by-one that matched 0 rows and was caught only because someone looked. **No hook catches this** — it is a wrong predicate, not a bad command. A reset script that deletes student jobs by predicate is exactly this hazard. |
| **Cheapest mechanism** | Golden DB file on the coldstore disk (§6.5) plus a two-line `reset-missing-link-class.sh`. |

### 6.4 ComfyUI

| | |
|---|---|
| **The decision that makes everything else easy** | **`--base-directory <mutable-path>`** relocates *models, custom_nodes, input, output, temp and user* under one path, with `--input-directory`, `--output-directory` and `--user-directory` overriding individually (CONFIRMED, ComfyUI startup flags). **Point `--base-directory` at a dedicated mutable path and the models tree at a separate read-only mount on NVMe.** Mutable state becomes one subtree; the big immutable thing sits outside it. |
| **What is in the snapshot** | `user/` (settings, templates, saved workflows — the students' actual work) and `output/` (generated images, which **double as workflow backups** because the graph is embedded in the PNG). |
| **What is NOT in the snapshot** | `models/` — reproduce from a manifest, never tarball it. `custom_nodes/` — a manifest of `repo-url@commit`, applied by a re-runnable installer, **never a tarball: you do not want to restore an attacker's node.** The Python venv — **recreate it**, because any node's `pip install` mutates it. |
| **"Reset" means** | Destroy the container, recreate from the image, re-bind the read-only models mount, restore `user/` and `output/` from the golden tarball. ~1 minute. |
| **Why container and not repair** | ComfyUI has **no authentication at all** and every route is unauthenticated by default — `POST /queue` (clear everyone's queue), `/interrupt` (cancel whatever is running), `/free` (unload the models), `/history` (delete everyone's history), `POST /upload/image`, `GET /view`. **Any student can wipe the class's work from a browser address bar with no exploit involved.** That is a reset you will perform often, so it must be fast, and it must survive the case where the student did something worse than clicking. |
| **The throwaway target (Lab 7)** | **Rebuild from image between every class, unconditionally.** No exceptions, no repairs. |
| **Model distribution note** | Do not let students download checkpoints. The link is **100 Mb/s (~11.2 MB/s measured, F28)** and they would be sharing it with the cluster's own model distribution. Pre-stage a curated set. |

### 6.5 Node 3's 1 TB disk — the snapshot target

**F56 corrects F53 and the news is good.** Mounted read-only and checked rather
than inferred: **128 MB used of 932 GB — a bare NTFS format with no prior
Windows data at all.** SMART **PASSED**, **2,789 power-on hours**, 0 reallocated
sectors, 0 pending. Effectively a new drive.

**F56's recommended layout, not executed pending the operator's say-so** because
it destroys the existing filesystem, empty or not: single GPT partition, ext4,
`LABEL=coldstore`, mounted `/srv/coldstore` from `fstab` **by UUID with
`nofail`** — a spinning disk that fails to mount must not block a headless boot.

**What goes on it:**

- golden tarballs: n8n `~/.n8n`, ComfyUI `user/` + `output/`, the Missing Link
  class DB
- nightly `n8n export:workflow --backup` output
- the seeded OSINT corpus, the AIT alert data, the phishing corpora, the
  reference image set — all read-mostly and all large
- **and, per F56, a fleet-local GGUF mirror**, which is the larger payoff: a
  re-provision then copies at ~120 MB/s off local rust instead of ~11.7 MB/s
  over the 100 Mb LAN — **~9 minutes per 65 GB instead of ~97.**

**What does NOT go on it: models that are actually served, and any live
database.** **F53/F16/F3: models stay on NVMe.** Model load is single-core
serialised (F3, REPORTED) and already the slowest step in a restart; running it
off 7,200 rpm rust makes the worst step materially worse. Spinning disk is excellent for
sequential tarballs and terrible for random small reads — so golden copies live
there, and the running copy lives on NVMe.

### 6.6 The reset runbook — what to actually build

**One script per tool, named `reset-<tool>.sh`, that an instructor can run in
front of a class without thinking.** Then **time each one and publish the
times**, because a 20-minute reset inside a 50-minute lab is not a reset.

**A pre-class checklist, run 15 minutes before, and it must exercise the thing
rather than count assertions** (F34: *"a test count is not evidence of working
software"* — 41 tests passed against a pipeline that had never processed a
document):

1. `systemctl is-active` on the class `llama-server`, n8n, ComfyUI, class
   Missing Link.
2. **One real end-to-end request through each**, and read the output. Not
   `/health` — F39 is the finding that says why: `/health` is the server's
   opinion of itself, delivered through the queue it is reporting on.
3. `journalctl -u llama-server@8080 | grep n_ctx_slot` — confirm the per-slot
   context is what the labs assume, from the server's own log, not from `-c`.
4. `df -h` on the class node and on `/srv/coldstore`.
5. Golden snapshots present, and **dated today or later than the last change you
   made**.
6. Class queue empty.

**And one rule for the reset scripts themselves, from this repo's conventions:**
kill by unit or by PID, **never by pattern** — `pkill -f` matches the caller's
own command line and has killed an agent's shell three times here. `systemctl
stop <unit>`, or `pgrep -f <pat>` → read the PIDs → `kill <pid>`.

---

## 7. What was NOT verified, and would need to be

Listed so nobody mistakes this design for a measurement.

- **Nothing in this document was run on the cluster.** No lab has been piloted.
  No tool named here (n8n, ComfyUI, `c2patool`) is installed.
- **§1.2's planning formula is arithmetic on measured aggregates, not a
  measurement.** It reproduces `llama-batched-bench`'s own published totals
  exactly and is 10% pessimistic against the replication run, but **it has never
  been checked against twenty concurrent human users**, which is a different
  arrival pattern from four scripted requests. **The cheapest possible
  validation: run one lab with five students before running it with twenty.**
- **Per-lab token estimates are estimates.** Prompt lengths are the instructor's
  to fix; the estimates assume the caps in each lab actually get enforced.
- **The 1-step SD-Turbo figure (~25–35 s/image) is INFERRED in
  `docs/comfyui-feasibility.md` from third-party CPU benchmarks on different
  CPUs.** It has never been run here. That is under an hour of work — install
  ComfyUI in a scratch venv on a non-serving node, run SD-Turbo at 512×512/1
  step and SD1.5 at 512×512/20 steps, and record both in
  `docs/measurements.md` — and it would replace an inference with a fact before
  a class depends on it.
- **Whether a small class model is worth it is unmeasured below 4B.** §1.4 shows
  4B buys less than intuition suggests. One `llama-bench` run on a sub-1B
  candidate settles it and changes Lab 1's shape.
- **CPU-only voice cloning feasibility is entirely unestablished** (Lab 5's
  closing note). One 10-second clip, timed, is the whole test.
- **`GET /slots` was read from this build's own README, not queried live.** The
  documented response contains no prompt text; **confirm that against the
  running server before telling a class it is safe to leave enabled.**
- **The AIT alert data has not been downloaded or inspected here** — its size
  (96.2 MB compressed, 178.7 GB expanded), licence (CC BY 4.0) and alert count
  (2,655,821) are read from its Zenodo record. **Someone should extract one
  testbed and confirm the digest actually reduces to under 1,500 tokens** before
  Lab 3 depends on it.
- **Corpus licences for Lab 2 are flagged, not resolved.** SpamAssassin's
  readme states copyright remains with the original senders — a fact, not a
  grant. **That is an instructor decision, and it should be a deliberate one.**
- **n8n's licence has one grey edge** (`docs/n8n-feasibility.md` §1.3) where a
  fee-paying student could be read as a "customer". **One email to
  `license@n8n.io`, reply kept with the project docs**, closes it.
- **The class-day topology in §2 assumes node 3 is available for apps.** It is
  joined and serving (F56); whether it is free during class hours is a
  scheduling decision, not a technical one.
