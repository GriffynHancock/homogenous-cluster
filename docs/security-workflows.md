# Security workflows as n8n demos: cryptanalysis, CTF, OSINT, web pentesting

**Date:** 2026-08-23 | **Status:** research and design only. **Nothing was
installed, and nothing was run on the cluster, on any lab host, or against any
third-party host for this document.** The only commands executed on node 1 were
read-only `apt-cache` queries against the *already-present* package index (no
network, no install) and `git` metadata. Everything else came from the web or
from files already in this repo.

**Who this is for.** The instructor designing the second half of the
cybersecurity syllabus, after `docs/teaching-labs.md`.

**Labelling.** Every claim is **CONFIRMED** (verified against a primary source —
the tool's own source, its own licence file, its own docs, or a file on this
machine), **REPORTED** (someone else states it, unverified here), or
**INFERRED** (reasoning or arithmetic from other labelled facts). **No
web-sourced performance number is presented as if measured on this hardware.**
Every number attributed to this fleet comes from `docs/measurements.md`, via
F58's formula, and nowhere else.

**Site-specific detail is deliberately absent.** IPs, hostnames and ports live
in `network.md`, which is gitignored. This file uses `<class-llm-node>`,
`<class-apps-node>`, `<tools-host>`, `<juice-shop>`, `<dvwa>` throughout.

---

## 0. The verdict, first

**Eight workflows are viable. They are numbered `S1`–`S8` rather than 8–15
because `docs/teaching-labs.md` owns Labs 1–7 and this document does not edit
it.** Each is a candidate for adoption there.

| # | Workflow | Area | Target | LLM calls **in class** | Node-seconds / student | ×20 on one node |
|---|---|---|---|---:|---:|---:|
| **S1** | Cipher triage: the identification is deterministic | crypto | static artefacts | 2 | **87 s** | 29 min |
| **S2** | What a password costs — measured on this hardware | crypto / creds | synthetic shadow file | 1 | **65 s** | 22 min |
| **S3** | Hint engine driven by the solve webhook | CTF | Juice Shop | 2 | **76 s** | 25 min |
| **S4** | Recon is a parser problem: asset diff | OSINT / recon | own DMZ hosts | 1 | **50 s** | 17 min |
| **S5** | Fuzzing is a loop, triage is a funnel | web pentest | DVWA | 1 | **41 s** | 14 min |
| **S6** | The async scanner: sqlmap-as-a-service | web pentest | DVWA | 1 | **58 s** | 19 min |
| **S7** | Grading the machine: writeup audit | CTF / crypto | transcripts | **0** | **0 s** | 0 min |
| **S8** | Metadata is the leak | OSINT | seeded synthetic corpus | 1 | **40 s** | 13 min |

**Every one of them fits a 50-minute session for 20 students on one node**, and
S7 costs nothing at all. That is not an accident — it is the result of applying
F58's formula *first* and designing the workflow around the answer, which is the
discipline `docs/teaching-labs.md` §1 established and this document inherits.

**The three headline conclusions:**

1. **n8n earns its place in exactly three of these eight** — S3, S6 and, more
   weakly, S2 — and for one reason each time: **there is a wait, or there is an
   inbound event.** The others are better as a script with n8n used only as the
   *teaching surface* (§3).
2. **An LLM adds nothing to cryptanalysis, and the evidence is unambiguous
   enough to teach as a result rather than assert as an opinion** (§4). The best
   model measured on the best public benchmark scores **1.91% on Vigenère**
   while `CyberChef` identifies and breaks the same class of cipher
   deterministically, in milliseconds, with a confidence score.
3. **The single most important architectural finding is negative and structural:
   n8n's `Execute Command` node is blocked by default and n8n itself names it as
   the first node to exclude when "your users might be untrustworthy."** So
   `nmap`/`ffuf`/`hashcat`/`john` cannot be driven from n8n the obvious way.
   **The correct answer is better than the obvious one** — the `SSH` node to a
   disposable tools host, or the tool's own REST API where it has one (§3.3).
   `sqlmap`, `ZAP` and `CyberChef` all have one.

---

## 1. Scope: what this document covers, and what it deliberately does not

`docs/teaching-labs.md` already designs seven labs: prompt injection,
generate-then-detect phishing, log/alert triage, OSINT synthesis over a seeded
persona corpus, synthetic media literacy, measuring an AI service from outside,
and supply chain. **None of that is repeated here.**

This document covers the four areas the operator named that the existing set
does not: **decryption/cryptanalysis, CTF solving, OSINT beyond persona
synthesis, and web penetration testing.**

**One deliberate overlap, and it is a saving rather than a duplication.** S8
(metadata forensics) uses **the same seeded synthetic persona corpus that Lab 4
already requires the instructor to build**, and Lab 4's build recipe already
specifies *"EXIF coordinates on a generated image that match an address in the
job ad."* S8 is the lab that actually opens that image. One overnight corpus
build serves two labs; S8 should not be scheduled before Lab 4 exists.

**Two guardrails are treated as fixed and are not relitigated anywhere below:**

- Every target is **the operator's own deliberately-vulnerable lab host or a
  static artefact**. No workflow points at a third-party host and no design
  assumes an internet-facing target. In S4 and S6 this is enforced *by workflow
  construction* — the target list is workflow-owned data, not a student-editable
  field — and making students attack that enforcement is itself part of the lab.
- **OSINT uses seeded synthetic personas.** No live scraping of real people.
  This is already `docs/teaching-labs.md` §5's position and S4/S8 keep it: S4's
  "OSINT" is against the lab's own infrastructure, and S8's is against fabricated
  documents.

---

## 2. What the fleet actually offers a workflow author

Restated compactly because every design below depends on it.

| Fact | Value | Source |
|---|---|---|
| Planning formula | `node-seconds ≈ prompt_tokens/16.6 + generated_tokens/9.7` | F58, from `docs/measurements.md` |
| One node, one minute | ~1,000 prompt **or** ~580 generated tokens | F58 |
| **Per-student budget, 20 students, 50 min, one node** | **150 node-seconds, spendable as any mix** | INFERRED from the above |
| Slot context | `n_ctx_slot = 8192` at `-c 32768 --parallel 4` | `CLAUDE.md`; check `journalctl -u llama-server@8080 \| grep n_ctx_slot` |
| Concurrency | `--parallel 4`. **Never 8** — prefill collapses 56% | `CLAUDE.md`, F58 |
| Token counting | `POST /tokenize` is free and takes no slot | F49 |
| Per-group credentials | `--api-key` takes a comma-separated **list**; `--api-key-file` one per line | F58, CONFIRMED from b10369's README |
| n8n → local LLM | one OpenAI credential, Base URL `http://<class-llm-node>:8080/v1`, **no custom code** | F54, CONFIRMED from n8n source |

**A budgeting rule this document adds, because it is easy to miss and it
doubles a workflow's cost:** n8n's **Guardrails** node splits into two kinds, and
one of them is a whole extra LLM call.

**CONFIRMED**, from n8n's own docs
(<https://raw.githubusercontent.com/n8n-io/n8n-docs/main/docs/integrations/builtin/core-nodes/n8n-nodes-langchain.guardrails.md>):

> *"This node requires a Chat Model node to be connected to its Model input when
> using the **Check Text for Violations** operation with LLM-based guardrails."*
> … *"Many guardrail checks (like Jailbreak, NSFW, and Topical Alignment) are
> LLM-based and use this connection to evaluate the input text."*

| Guardrail | Kind | Cost on this fleet |
|---|---|---|
| Keywords, Custom Regex, PII, Secret Keys, URLs | pattern-based | **zero tokens** |
| Jailbreak, NSFW, Topical Alignment, Custom | LLM-based | **a full extra call, budgeted as one** |

**So the Guardrails node is this project's own build rule rendered as a UI
element** — the cheap deterministic checks and the expensive probabilistic ones
sitting in the same box, visibly labelled. That is worth pointing at in front of
a class regardless of which workflow it appears in. Use the pattern-based ones
freely; budget every LLM-based one.

---

## 3. The governing question: when does n8n's *shape* add something?

n8n is HTTP calls, branching, loops, waiting, fan-out and glue. It is not a
faster shell. The honest question for each workflow is whether the orchestration
is load-bearing or decorative.

**CONFIRMED core node inventory** — enumerated from n8n's own docs repository
(<https://api.github.com/repos/n8n-io/n8n-docs/contents/docs/integrations/builtin/core-nodes>),
not from memory. The ones that matter here:

`httpRequest`, `code`, `crypto`, `ssh`, `executeCommand`, `webhook`,
`respondToWebhook`, `wait`, `if`, `switch`, `filter`, `merge`, `splitInBatches`
(Loop Over Items), `splitOut`, `aggregate`, `summarize`, `sort`, `limit`,
`removeDuplicates`, `compareDatasets`, `set`, `xml`, `html`, `extractFromFile`,
`convertToFile`, `dataTable`, `stopAndError`, `executeWorkflow`, `jwt`, `totp`,
`graphql`, plus the LangChain root nodes `guardrails`, `informationExtractor`,
`textClassifier` and the `outputParserStructured` sub-node.

Three of those deserve to be called out because they change what is possible:

- **`xml`** parses `nmap -oX` output with no code at all.
- **`compareDatasets`** does baseline-vs-current diffing with no code at all.
- **`dataTable`** gives a per-class scoreboard with no Postgres and no code.

### 3.1 The five conditions under which n8n beats a script

1. **There is a wait.** A long-running tool that must be polled — `sqlmapapi`,
   ZAP, Missing Link. `Wait` + `If` + loop-back is exactly this, and it is
   *visible*. A shell script does it with `nohup`, a PID file and no
   observability.
2. **There is an inbound event.** Juice Shop's `SOLUTIONS_WEBHOOK` fires when a
   student solves a challenge. A script has no listener; a `Webhook` trigger is
   one node.
3. **There is fan-out over twenty students with shared state.** `dataTable`
   plus per-student webhook paths gives a live scoreboard. (Note the trap from
   `docs/n8n-feasibility.md`: **webhook paths are unique instance-wide** — twenty
   students all choosing `/webhook/test` collide. Namespace them in the shared
   workflow, not in the student's hands.)
4. **The graph is the artefact being taught.** In S5 the whole lesson is *where
   in the funnel the LLM sits*. On a canvas you can point at the node and ask
   "why is this after the filter and not before it?" A shell pipeline hides the
   answer inside `|`.
5. **Structured-output enforcement is a node, not code.** `outputParserStructured`
   makes "model output is a protocol, not an answer" literally visible, and its
   auto-fixing option makes the retry loop visible too. **CONFIRMED**,
   <https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.outputparserstructured>.

### 3.2 The four conditions under which it does not

1. **Straight-line transforms with no wait and no event.** `exiftool -json | jq`
   is finished before you have opened the canvas. Building it in n8n adds a Code
   node — i.e. F54's security hole — for no gain.
2. **Tight loops over thousands of items.** n8n materialises every item as a
   workflow item. `ffuf`'s ~4,600 raw results should be reduced *before* they
   enter n8n, not inside it. This is S5's design constraint and also its lesson.
3. **Anything needing a real shell on the n8n host.** See §3.3.
4. **Anything whose cost is dominated by the tool rather than the glue.** If the
   scan takes eleven minutes, the orchestrator's efficiency is noise.

### 3.3 The Execute Command finding, and the better answer

**CONFIRMED**, from n8n's own security docs, already recorded in
`docs/n8n-feasibility.md` §3.5: `NODES_EXCLUDE` should start with
`["n8n-nodes-base.executeCommand", "n8n-nodes-base.readWriteFile"]` — n8n names
these two itself as the starting set *"if your users might be untrustworthy"* —
and **Execute Command is blocked by default.**

So the obvious design (n8n shells out to `nmap`) is unavailable, and should stay
unavailable. **Two replacements, and both are architecturally better:**

**(a) Use the tool's REST API where it has one.** This turns a CLI into an
HTTP-native n8n citizen with zero code and zero shell:

| Tool | API | Confirmed shape |
|---|---|---|
| **sqlmap** | `sqlmapapi.py -s` | `POST /task/new` → `POST /option/{id}/set` → `POST /scan/{id}/start` → `GET /scan/{id}/status` → `GET /scan/{id}/data` (REPORTED, from community documentation and the project's own issue tracker; `sqlmapapi.py` itself is in the repo — <https://github.com/sqlmapproject/sqlmap/blob/master/sqlmapapi.py>) |
| **CyberChef** | `cyberchef-server` | `POST /bake`, `POST /batch/bake`, `POST /magic` (CONFIRMED, <https://github.com/gchq/CyberChef-server>) |
| **ZAP** | its own REST API | REPORTED; not verified here |
| **Juice Shop** | `GET /api/Challenges`, `SOLUTIONS_WEBHOOK` | CONFIRMED, <https://pwning.owasp-juice.shop/companion-guide/latest/part4/integration.html> |
| **Missing Link** | its own FastAPI | CONFIRMED, `docs/n8n-feasibility.md` §4.2 |

**(b) Use the `SSH` node against a disposable tools host.** **CONFIRMED**,
<https://raw.githubusercontent.com/n8n-io/n8n-docs/main/docs/integrations/builtin/core-nodes/n8n-nodes-base.ssh.md>:
the node's operations are *"Execute a command"*, *"Download a file"* and
*"Upload a file"*, and the command field is *"Enter the command to execute on
the remote device."*

**Why this is better than `executeCommand`, stated as design rationale rather
than as security advice:** the blast radius of a student's runaway command lands
on a machine that is rebuilt from image between classes, not on the machine
holding every other student's workflows and credentials. It also puts F44's
CPU-contention problem somewhere harmless — `hashcat` and `john` saturate every
core they are given, and `docs/teaching-labs.md` §2 already forbids that on an
inference node. **The tools host is therefore a requirement of S2, S4 and S5,
not an optimisation.**

**One caveat, stated plainly:** the SSH node holds a credential, and
`docs/n8n-feasibility.md` §3.4 records n8n's own text that in the default
internal task-runner mode *"anyone who can edit a workflow could potentially
read your database, encryption key, stored credentials, and environment
variables."* **So the tools-host account must be worth stealing as little as
possible** — its own unprivileged user, on a machine that is disposable by
design. That is a placement decision, and it is the same one `docs/teaching-labs.md`
already made for Lab 7's throwaway VM.

---

## 4. Where the LLM adds nothing: cryptanalysis, stated as a result

**This section is the most useful thing in the document, and it is a negative
result.** The operator asked for scepticism about LLMs at cryptanalysis. The
scepticism is warranted, it is measurable, and the measurement is a better lab
than any workflow that hides it.

### 4.1 The evidence

**CipherBank** (2,358 problems, 5 domains, 9 algorithms;
<https://arxiv.org/abs/2504.19093>) — **REPORTED**, these are the paper's
numbers, not ours:

| Cipher | Best model's accuracy (Claude-3.5 Sonnet) |
|---|---:|
| Rot13 | 83.21% |
| Atbash | 75.19% |
| Polybius | 72.90% |
| Reverse | 63.93% |
| ParityShift | 58.21% |
| WordShift | 39.12% |
| SwapPairs | 6.87% |
| DualAvgCode | 4.96% |
| **Vigenère** | **1.91%** |
| **Overall** | **45.14%** |

Two details make this sharper for our purposes:

- **The reasoning model did worse.** o1 scored 40.59% overall, *below* the
  general chat model, which the paper reads as *"current reasoning optimizations
  inadequately address cryptographic challenges."* Our incumbent is a reasoning
  MoE, and F35 records that there is no universal thinking-off switch.
- **Open-weight models collapse.** Mixtral-8x22B **0.30%**, Qwen2.5-72B
  **0.55%**, QwQ-32B-Preview **0.76%**. **INFERRED:** gpt-oss-120b in front of a
  class will look like these numbers, not like Claude-3.5's.

A second benchmark reports the same shape from the other end: **near-zero on
polyalphabetic ciphers, and 0% on properly implemented AES/RSA/ECC**
(REPORTED, <https://arxiv.org/html/2505.24621v1>).

**The one place the picture is genuinely different, and it should be stated so
the section is not overclaiming.** `CryptanalysisBench`
(<https://arxiv.org/html/2607.18538v1>) finds frontier models **65–86% on
primitives with known practical breaks**, and reports genuinely novel results
including a full 128-bit key-recovery attack on the unmodified SpoC AEAD. But
read what the task *is*: that benchmark is about **reasoning over a cipher's
design and proofs**, and the paper explicitly distinguishes itself from *"toy
ciphers such as Vigenère (broken since the 19th century)"*. It is a literature-
and-argument task, which is prose, which is the thing a model is for. **It is
not ciphertext decryption, and it is a frontier-model result on hardware that is
not this.**

**So the finding is precise, not blanket:** an LLM is bad at *executing*
cryptanalysis — search, arithmetic, key recovery — and that is the part a
classroom cipher exercise consists of. Do not extrapolate a general "LLMs can't
do crypto"; extrapolate "the computable part is computable, so compute it."

### 4.2 What replaces it, and it is not close

`CyberChef` is Apache 2.0, Crown Copyright, and `cyberchef-server` exposes it
over HTTP (**CONFIRMED**, <https://github.com/gchq/CyberChef-server>). Its
`/magic` endpoint *"performs automatic detection of encoded data."* It does
deterministically, in milliseconds, with a confidence score, and with a
reproducible answer, the exact task the LLM scores 45% on.

Alongside it, three classical statistics that decide a classical cipher outright
and are one Code node each: **index of coincidence** (distinguishes
monoalphabetic from polyalphabetic and recovers Vigenère key length),
**chi-squared against expected letter frequency** (scores a candidate shift),
and **Shannon entropy** (tells you the input is not a classical cipher at all).

**The comparison a student should be made to draw is not "which is more
accurate" — it is that one of them costs a measurable slice of the class's
shared inference budget and the other costs nothing.** That is a better security
lesson than the accuracy gap.

### 4.3 The general rule this generates

Stated once here and applied throughout §6:

> **Ask the model for prose. Ask code for everything else.**
>
> An LLM adds nothing to: cipher identification; frequency analysis; key
> recovery; brute force of any kind; parsing structured tool output (`nmap` XML,
> `ffuf` JSON, `exiftool` JSON); deciding whether an exploit worked; and saying
> where a claim came from (`CLAUDE.md`: ~38% on the *easier* task of merely
> validating a location).
>
> An LLM adds something to: writing a narrative from a table that code built;
> explaining a vulnerability class to a student who has the exploit but not the
> concept; generating hypotheses over a **small, pre-reduced** candidate set; and
> classifying prose where a regex genuinely cannot.

**And a note about CTF solving specifically, because the operator named it.**
LLM agents on public CTF benchmarks are **REPORTED** at roughly **12–24%**
solve rates: NYU CTF Bench ~14.4% across evaluated models, GPT-4.1 ~16.94%,
D-cipher-web 24.07% against vanilla D-cipher's 12.59%
(<https://arxiv.org/abs/2406.05590>, <https://cybench.github.io/>), **with the
benchmark authors themselves flagging possible data contamination.** The famous
"LLM agents can autonomously hack websites" result
(<https://arxiv.org/abs/2402.06664>) has a documented and specific critique —
prompts, agent code and outputs unreleased; the agent had web search; **11 of
the vulnerabilities had public exploits, several as the first search result** —
so the capability demonstrated may be *retrieval and assembly* rather than
analysis (REPORTED, <https://www.thestack.technology/llms-autonomously-hack-nada/>).
**S3 is built on that critique rather than around it:** the model is given the
challenge's own published description and hint and asked to explain the
technique class, which is retrieval-and-assembly done honestly, and the
verification is a webhook.

---

## 5. How every budget below was computed

F58's formula, applied identically each time, with the token counts stated so
the instructor can re-derive or contest them. **All budgets are INFERRED** —
they are arithmetic on measured aggregates, not fresh measurements.

Two conventions:

- **Generated tokens include the reasoning channel.** On this model the analysis
  tokens are billed as generation. Set `reasoning_effort: "low"` in the *shared*
  credential (F58 / `docs/teaching-labs.md` §1.4 measured ~31% fewer completion
  tokens, with the caveat that the figure lives in a source comment rather than
  `docs/measurements.md`).
- **`max_tokens` has a floor, not just a ceiling.** F21: a reasoning model that
  hits `max_tokens` mid-thought returns **empty content**, and a student will
  read that as "the cluster is broken." Set it in the shared workflow.

**Pre-flight, in every workflow:** a `POST /tokenize` call (free, takes no slot,
F49) followed by an `If` that refuses oversized input with a message saying why.
One student pasting a 12,000-token log otherwise eats a third of the class's
budget *and* hits the `n_ctx_slot = 8192` hard error.

---

## 6. The workflows

Format per workflow: **objective — node graph — deterministic vs LLM — token
budget — target — what the student learns — defensive counterpart — n8n
verdict.**

---

### S1 — Cipher triage: the identification is deterministic

**Area:** decryption / cryptanalysis. **Target:** static artefacts only.

**Learning objective.** Three: (a) classical cipher identification and breaking
are *computable*, so they get computed; (b) the LLM's one legitimate role in the
pipeline is choosing between candidate plaintexts on grounds of *meaning*, and
even that is a fallback; (c) a confident answer on a high-entropy input is a
**fabrication signal**, and you can build the detector for it.

**Node graph.**

```
Form Trigger  (student pastes ciphertext; the field is text, the target is nothing)
  → Code:  Shannon entropy, index of coincidence, chi-squared, char histogram
  → If:    entropy > threshold?
       ├─ TRUE  → Stop and Error: "this is not a classical cipher — refuse"   [no LLM]
       └─ FALSE ↓
  → HTTP Request → cyberchef-server  POST /magic
  → Switch on detected encoding (base64 / hex / rot-N / xor / unknown)
  → HTTP Request → cyberchef-server  POST /batch/bake   (all 25 rotations, or the XOR keyspace)
  → Code:  English-likeness score = dictionary hit rate against /usr/share/dict/words
  → Sort → Limit 3
  → If:  exactly one candidate above threshold?
       ├─ TRUE  → done.                                                        [no LLM]
       └─ FALSE ↓
  → Basic LLM Chain  (ONE call: "which of these three is coherent English?
                      answer with the index only")
      └─ Structured Output Parser:  {"index": integer}
  → Code:  resolve index → plaintext.  Reject any index out of range.
```

**Deterministic vs LLM.** Everything above the `Basic LLM Chain` is
deterministic. The LLM is reached **only** when the deterministic scorer cannot
separate the top candidates, it never sees the ciphertext, it is never asked to
decrypt, and its answer is an integer that code resolves — which is
`CLAUDE.md`'s "hand it a label and ask it to repeat the label" rule, applied to
crypto.

**The second arm, which is the actual lesson.** Each student also makes one
**naive** call: paste the same ciphertext with *"decrypt this"*. Compare.
CipherBank predicts what happens (§4.1) and the students will see it on their
own hardware.

**Token budget (per student).**

| Call | Prompt | Generated | node-seconds |
|---|---:|---:|---:|
| Naive arm ("decrypt this") | 200 | 400 | 12.0 + 41.2 = **53.2** |
| Disambiguation (3 candidates + instruction) | 360 | 120 | 21.7 + 12.4 = **34.1** |
| **Total** | | | **87 s** |

×20 students = **29 minutes of one node**, inside a 50-minute session. The
instructor should pre-generate a backup set of naive-arm outputs overnight in
case the queue is deeper than expected — but the live failure is worth seeing.

**What the student learns.** That the pipeline they built calls the model on
roughly one input in ten and never for the hard part; and that the naive arm is
both slower and wrong.

**Defensive counterpart.** Feed the same pipeline **modern** ciphertext —
AES-CBC output the instructor generated. The deterministic path refuses at the
entropy gate. The naive LLM arm produces a confident, entirely fabricated
"plaintext". **Students then write the entropy gate themselves and tune the
threshold against a labelled set of classical/modern/random inputs, reporting
false-positive and false-negative rates.** The defensive artefact is a
*refusal*, which is the same thing `extract_content` does in this project's own
pipeline.

**n8n verdict: the orchestration is decorative; the canvas is the point.** There
is no wait and no event. A 40-line Python script does this. **Build it in n8n
anyway, because the branch structure is the artefact being taught** — you can
point at the `If` that skips the LLM and ask "how often does this branch fire,
and what did it save?" and the students can answer in node-seconds.

---

### S2 — What a password costs, measured on this hardware

**Area:** cryptanalysis / credential hygiene. **Target:** a synthetic shadow
file the instructor generates.

**Learning objective.** The cost of a password is a **number you measure**, not
an adjective; the defensive lever is the **hash function and its cost
parameter**, not the complexity policy; and a CPU-only cluster is the honest
place to learn this because the numbers are small enough to watch.

**Node graph.**

```
Form Trigger (student submits 5 candidate passwords + picks a hash: descrypt / md5crypt / bcrypt-{5,8,12})
  → Code: build the synthetic hash entries          [deterministic]
  → SSH  → <tools-host>:  john --test=10 --format=<chosen>        [deterministic]
  → SSH  → <tools-host>:  john --wordlist=<curated> --format=<chosen> <file>
  → Code: parse c/s and cracked/uncracked            [deterministic]
  → Data Table: append to the class scoreboard       [deterministic]
  → Summarize: aggregate by hash format              [deterministic]
  → Basic LLM Chain (ONE call: write the policy recommendation from THIS table)
      └─ Structured Output Parser: {recommendation: string, cited_numbers: number[]}
  → Code: assert every number in cited_numbers appears in the table. Drop the rest.
```

**Deterministic vs LLM.** Everything except one call. The LLM turns a table into
a paragraph, and code then checks that every number in the paragraph came from
the table — which is `docs/faithfulness-cascade.md`'s cheap deterministic check,
implemented in eight lines of a Code node.

**The CPU-only reality is the lab, not a limitation.** `hashcat` is in Debian 12
main but `Depends: pocl-opencl-icd | opencl-icd` (CONFIRMED from the local
package index), and **pocl is REPORTED as not officially supported by the
hashcat project**, with a history of detection failures across versions
(<https://github.com/hashcat/hashcat/issues/3021>). Debian's `john` is **core
1.9.0, not jumbo** (CONFIRMED from `apt-cache show john`) — its description
names *"several crypt(3) password hash types … Kerberos AFS and Windows
NT/2000/XP/2003 LM hashes"*, which is precisely the crypt(3) family this lab
needs and nothing more. **Use `john`; keep `hashcat` as an optional second arm
and expect to debug pocl.**

**Do not quote a cracking rate from anywhere — including from this document.**
The lab is `john --test` on the tools host, and the students read their own
number. That is this project's own rule ("performance claims must come from
measurement on the hardware") taught as an exercise, and it removes every
web-sourced number from the syllabus.

**Token budget (per student).** One call, table ≈ 650 prompt, 250 generated:
650/16.6 + 250/9.7 = 39.2 + 25.8 = **65 node-seconds**. ×20 = **22 minutes**.

**What the student learns.** That moving from `md5crypt` to `bcrypt` cost 12
changes the attacker's economics by orders of magnitude while changing the
user's experience by nothing; and that the LLM's paragraph was right only
because code checked it.

**Defensive counterpart.** Built in: re-run the identical wordlist against
bcrypt at rising cost factors and watch the wall clock. Then the second half —
**salting**: the same weak password, hashed with and without a per-user salt,
and the measured difference in a multi-user attack. The deliverable is the
students' own cost table plus the checked recommendation paragraph.

**Wordlist provenance — a licence and ethics item (§8).** Use a **synthetic**
list, or `wamerican` (Debian main, CONFIRMED present in the index) with mangling
rules. **Do not use `rockyou` or other breach-derived lists.** SecLists is MIT
(CONFIRMED, <https://github.com/danielmiessler/SecLists>) but its
`Passwords/Leaked-Databases/` content is real people's credentials from real
breaches; the repository licence covers the collection, not the provenance of
what is in it. This project killed a corpus source over CC BY-NC-ND
(`docs/corpus-selection.md`); the same care applies here for a different reason.

**n8n verdict: weakly justified, and the justification is the scoreboard.** The
`SSH` → parse → `Data Table` → `Summarize` chain is real orchestration across
twenty concurrent students with shared state, which a script does badly. The
rest is glue.

---

### S3 — Hint engine driven by the solve webhook

**Area:** CTF solving. **Target:** OWASP Juice Shop (MIT) on the DMZ lab.

**Learning objective.** Separate *knowing about* a vulnerability class from
*exploiting* one. Show that the LLM's real contribution to CTF work is recall
and explanation, and that **it cannot tell you whether your exploit worked** —
only the target can, from outside. That is F36's lesson in a form a student can
act on.

**The two API facts this is built on, both CONFIRMED**
(<https://pwning.owasp-juice.shop/companion-guide/latest/part4/integration.html>):

- `GET /api/Challenges` returns every challenge with `id`, `key`, `name`,
  `category`, `tags`, `description`, `difficulty`, `hint` and solved status.
- **`SOLUTIONS_WEBHOOK`** makes the instance POST on every solve, with a payload
  containing `solution.challenge`, `hintsAvailable`, `hintsUnlocked`,
  **`cheatScore` (0..1)**, `totalCheatScore`, `issuedOn`, plus `ctfFlag` and an
  `issuer` block.

**Node graph.**

```
Webhook Trigger  ← Juice Shop SOLUTIONS_WEBHOOK
  → Data Table: record {student, challenge, cheatScore, issuedOn}   [deterministic]
  → HTTP Request: GET /api/Challenges                                [deterministic]
  → Filter: unsolved → Sort by difficulty → Limit 1                  [deterministic]
  → Basic LLM Chain (ONE call: given THIS challenge's own description
      and hint, explain the TECHNIQUE CLASS. Do not produce a payload.)
      └─ Structured Output Parser: {technique: string, why_it_works: string, what_to_read: string}
  → Guardrails (Check Text for Violations, PATTERN-BASED only:
      URLs, Secret Keys, Custom Regex for payload-shaped strings)    [zero tokens]
       ├─ Fail  → Data Table: log + return the challenge's own published hint instead
       └─ Pass  → Respond to Webhook: deliver the hint
```

**Deterministic vs LLM.** The next-target selection, the state, the guardrail
and — critically — **the verification** are all deterministic. The webhook
firing *is* the proof of a solve. The model is never asked "did that work?"

**Token budget (per student).** Two hints × (320 prompt + 180 generated):
2 × (19.3 + 18.6) = **75.8 node-seconds**. ×20 = **25 minutes**. **Two hints,
not four** — four takes the class over budget and the scarcity is itself
pedagogically useful.

**What the student learns.** That a hint is cheap and an exploit is not; and
that the model's contribution was retrieval and explanation, which is exactly
what the critique of the "autonomous hacking" literature says it was all along
(§4.3).

**Defensive counterpart — and it is unusually good, because the data is already
there.** Part B turns the same webhook stream into a **detection** exercise. The
payload carries `cheatScore` and `hintsUnlocked`; the Juice Shop access log
carries the requests. Students write a **deterministic** rule that flags a solve
as suspicious — solved with no preceding requests to the relevant endpoint,
solved faster than the shortest observed honest solve, solved in an order that
implies a walkthrough. Then Part C: **they try to evade their own rule**, and
Part D: **they improve it.** Detect → evade → detect again, all against their
own instance, with an artefact (the detection rule) that is defensive by
construction.

**n8n verdict: STRONG. This one is genuinely n8n-shaped.** An inbound webhook
from twenty students, persistent shared state, and a conditional response. A
shell script cannot sit and listen. **This is the workflow to demo first if you
want to show what n8n is for.**

---

### S4 — Recon is a parser problem: asset discovery and diff

**Area:** OSINT / recon. **Target:** the lab's own DMZ hosts, from a
workflow-owned list.

**Learning objective.** The first thing an attacker builds is an inventory, and
the best defence against that is *having built it first, and knowing when it
changes*. Also: reading structured tool output is a **parser** problem, and
pricing the alternative in node-seconds settles the argument.

**Node graph.**

```
Schedule Trigger (weekly)  ─┐
Manual Trigger (in class)  ─┴→ Set: TARGETS = <lab CIDR>     [workflow-owned constant, NOT a student field]
  → Code: assert every target is inside the lab CIDR; Stop and Error otherwise
  → SSH → <tools-host>:  nmap -sV -oX - $TARGETS
  → XML node: parse nmap's XML natively                       [zero code]
  → Split Out: one item per host/port
  → Compare Datasets: current vs last week's Data Table baseline
       ├─ "In A only"  → NEW
       ├─ "In B only"  → REMOVED
       └─ "Different"  → CHANGED
  → If: any diff rows?
       ├─ FALSE → Data Table: record "no change", END        [no LLM]
       └─ TRUE  ↓
  → Basic LLM Chain (ONE call, over THE DIFF ONLY:
      "write the change narrative for this asset inventory")
  → Data Table: store narrative + promote current to baseline
```

**Deterministic vs LLM.** The scan, the parse, the diff and the guard are all
deterministic and all zero-code except the CIDR assertion. The LLM sees **only
the diff**, never the scan.

**The arithmetic the students must do themselves — this is the lab.** A 3-host
`nmap -sV -oX` output is comfortably ~8,000 tokens. Feeding it to the model
costs `8000/16.6 = 482 node-seconds ≈ 8 minutes` — **for one student, once, to
read a file the `XML` node parses in milliseconds and exactly.** Twenty students
doing that is 2.7 hours. **They compute that with F58's formula and then decide
where the LLM goes.**

**Token budget (per student).** One narrative call, diff ≈ 450 prompt, 220
generated: 27.1 + 22.7 = **50 node-seconds**. ×20 = **17 minutes**.

**What the student learns.** That "AI reads the scan output" is not a feature,
it is a 480-fold cost increase for a worse answer; and that the useful place for
a model is at the narrow end, describing change.

**Defensive counterpart.** The workflow *is* the defensive artefact — a baseline
and a change alarm. The offensive half is the recognition that this inventory is
exactly what an attacker builds first. **Then the second half, which is the
sharper one:** students attack the CIDR guard they just wrote. Decimal-encoded
addresses (`2130706433`), short forms (`127.1`), IPv6-mapped forms, a hostname
that resolves inside the range, and a hostname that resolves *differently on the
second lookup*. They fix the guard, and then they read n8n's own SSRF-protection
documentation and see that they have just rediscovered it
(<https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/enable-ssrf-protection.md>).

**Guardrail note.** The target list being workflow-owned rather than a student
field is not a convenience — it is the mechanism by which "no third-party host"
is enforced in code rather than in a lab-sheet instruction. **A rule a student
can edit is not a rule**, which is the same reasoning `docs/teaching-labs.md`
gives for artefact markers being applied by the shared workflow.

**n8n verdict: WEAK on orchestration, STRONG on the teaching surface.** The
`XML` and `Compare Datasets` nodes doing this with zero code is the demo. But
`nmap -oX | xsltproc` plus `diff` is a five-line script, and the scheduled
version is a cron job. **Be honest about that in front of the class** — it is
itself a useful lesson about when workflow tools earn their keep.

---

### S5 — Fuzzing is a loop, triage is a funnel

**Area:** web penetration testing. **Target:** DVWA (GPL-3.0) on the DMZ lab.

**Learning objective.** Content discovery produces thousands of results of which
almost all are noise. **The funnel is deterministic and the LLM sits at the
narrow end** — which is exactly F58's log-triage lesson transplanted to the web,
and deliberately so: seeing the same shape twice in two domains is what makes it
transferable.

**Node graph.**

```
Form Trigger (student picks a target from a DROPDOWN of lab hosts + a wordlist)
  → Code: assert target ∈ allow-list                            [deterministic]
  → SSH → <tools-host>:  ffuf -w <curated> -u http://<dvwa>/FUZZ -of json -o - -p 0.1
  → Code: parse ffuf JSON                                       [deterministic]
  → Filter: status class                                        [deterministic]
  → Code: response-size clustering (auto-calibration — drop the modal size)
  → Remove Duplicates → Sort by "interestingness" → Limit 10
  → Basic LLM Chain (ONE call over the 10 survivors:
      "which of these paths suggests an administrative or backup function, and why?")
      └─ Structured Output Parser: [{path, class, reason}]
  → Code: assert every returned `path` is one of the 10. DROP the rest.
  → Data Table: record
```

**Deterministic vs LLM.** The entire funnel. The LLM sees ten strings.

**The number that is the lab.** A default `ffuf` run against DVWA with a
medium wordlist returns on the order of 4,600 result rows. At ~12 tokens each
that is ~55,200 prompt tokens: `55200/16.6 = 3,325 node-seconds = 55 minutes`
**for one student**. Twenty students is **18.5 hours**. **The students compute
that themselves before they are allowed to build the funnel**, and then the
funnel's existence needs no justification.

**Why the reduction happens outside n8n and not inside it.** n8n materialises
every item; 4,600 items through a `Filter` node is slow and pointless. The
`Code` node that parses `ffuf`'s JSON does the first cut in the same pass.
**That is §3.2's second condition, met in practice.**

**Token budget (per student).** One call, 10 paths ≈ 300 prompt, 220 generated:
18.1 + 22.7 = **41 node-seconds**. ×20 = **14 minutes** — the cheapest LLM lab
in the set.

**What the student learns.** Where in a pipeline a model belongs, expressed as a
cost; and that an output parser which drops invented paths is not pedantry —
run it a few times and one will be invented.

**Defensive counterpart, in three moves.** (1) Students open the **server's own
access log** for the fuzz run they just performed and write a deterministic
detection rule — request rate, 404 ratio, user-agent, path entropy. (2) They
re-run `ffuf` with `-p` delay, a rotated user-agent and a smaller list, and see
their rule miss. (3) They improve the rule, and articulate what it now costs in
false positives. **Detection first, evasion second, better detection third** —
so the artefact the student leaves with is a detection rule, not a scanner
profile.

**n8n verdict: MEDIUM.** The orchestration is thin, but the funnel's *shape on a
canvas* is the entire pedagogical payload, and `Limit`/`Remove Duplicates`/
`Sort` being visible nodes makes the narrowing literal. Worth building in n8n
for that reason alone.

---

### S6 — The async scanner: sqlmap-as-a-service

**Area:** web penetration testing. **Target:** DVWA, from a workflow-owned
dropdown.

**Learning objective.** Long-running security tooling is a **job queue, not a
command**; and the LLM's role in a scan report is bounded to prose that code can
check. **This is the workflow where n8n's shape is doing the most work, and it
is deliberately the same shape as Missing Link.**

**Node graph.**

```
Form Trigger (student picks one of N pre-declared DVWA endpoints — DROPDOWN, not free text)
  → Code: assert endpoint ∈ allow-list                        [deterministic]
  → HTTP Request: POST http://<tools-host>:8775/task/new
  → HTTP Request: POST /option/{taskid}/set   {url, level, risk, batch:true}
  → HTTP Request: POST /scan/{taskid}/start
  → ┌───────────────────────────────────────────────┐
    │ Wait 30s                                      │
    │   → HTTP Request: GET /scan/{taskid}/status   │
    │   → If status == "terminated"? ──── no ───────┘
    └──────────── yes ────────────↓
  → HTTP Request: GET /scan/{taskid}/data
  → Code: extract injection points, technique, DBMS, banner     [deterministic]
  → Basic LLM Chain (ONE call: "write impact and remediation for THIS finding,
      using this template and only these facts")
      └─ Structured Output Parser: {impact, remediation, cited_facts[]}
  → Code: assert every cited_fact appears in the extracted data. Drop the rest.
  → Data Table + Respond to Webhook
```

**The API shape is REPORTED, not confirmed here.** `sqlmapapi.py` exists in the
project (<https://github.com/sqlmapproject/sqlmap/blob/master/sqlmapapi.py>) and
runs as `sqlmapapi.py -s -H <ip> -p <port>`; the endpoint set above
(`/task/new`, `/option/{id}/set|get|list`, `/scan/{id}/start|stop|status|data`,
`/task/{id}/delete`) comes from community documentation and the project's issue
tracker rather than official docs, which do not appear to exist. **Verify it
against the installed version before building the lab** — this is the single
highest-risk unverified item in this document.

**Deterministic vs LLM.** Everything except one paragraph, and that paragraph is
fact-checked in code against the scanner's own output.

**Token budget (per student).** One call ≈ 520 prompt, 260 generated:
31.3 + 26.8 = **58 node-seconds**. ×20 = **19 minutes**. Note the *scan* takes
minutes of wall clock and **zero** LLM tokens — which is why this fits.

**What the student learns.** Submit → poll → collect, as an architecture, in a
second domain. They have already seen it in Missing Link (or will, in Lab 3).
**Saying so out loud is the point:** the async job pattern is not a
document-summarisation trick, it is what you do whenever the work outlasts the
request.

**Defensive counterpart, in three moves.** (1) Re-run the **identical** scan
against DVWA at security level low → medium → high → impossible, and record
where it stops working. (2) Read the `impossible` source — DVWA is PHP and the
fix is a prepared statement, visible in about six lines. **The remediation the
model wrote is checked against the code that actually implements it.** (3) Build
the deterministic detection rule from the MySQL/Apache logs the scan generated.

**n8n verdict: STRONGEST in the set.** A poll loop with a `Wait`, a real
terminal condition, visible intermediate state, and an inbound form. This is
what n8n is *for*, and a shell script doing the same thing with `nohup` and a
PID file is a good thing to show beside it.

---

### S7 — Grading the machine: a writeup faithfulness audit

**Area:** CTF / crypto, and the project's own core build rule. **Target:**
transcripts and artefacts from S1/S3/S5/S6.

**Learning objective.** A plausible technical writeup can be **mechanically
graded** when the ground truth is a transcript, and the fraction of unsupported
steps is a number. This is the deterministic-check discipline of
`docs/faithfulness-cascade.md`, taught by making the students build it.

**Overnight (instructor).** The cluster generates a writeup for each of ~10
challenges the class has solved, from the recorded transcript.

**In class — node graph, and note what is absent.**

```
Manual Trigger
  → HTTP Request / Data Table: fetch writeup + its transcript
  → Code: extract every command, hash, flag, path, HTTP status from the writeup
  → Compare Datasets: writeup claims vs transcript facts
       ├─ In both        → SUPPORTED
       ├─ Writeup only   → UNSUPPORTED
       └─ Different      → CONTRADICTED
  → Summarize: counts per class
  → Data Table: the class's grading table
                                         (there is no LLM node in this graph)
```

**Deterministic vs LLM. There is no LLM call in the class-time path. Zero
tokens.**

**Token budget.** In class: **0**. Overnight: 10 writeups × (1,200 prompt + 700
generated) = `12000/16.6 + 7000/9.7 = 723 + 722 = 1,445 node-seconds ≈ 24
minutes` on one node, once.

**How this differs from Lab 4 Part C, which also audits model output.** Lab 4
audits a *dossier* against *documents*, and the judgement of whether a claim is
supported is human. **Here the ground truth is a machine-readable transcript, so
the audit is 100% deterministic and produces a hard number.** Running both, in
that order, teaches the distinction that matters: some checks can be automated
completely and some cannot, and knowing which is which is the skill.

**What the student learns.** That the writeup was fluent and partly false, and
that the check which caught it was `in`, not inference. Then they read
`missing_link/cascade.py` and see the production version of what they just
wrote — the same move F58 recommends for the log lab, and it works here for the
same reason.

**Defensive counterpart.** This *is* the defensive exercise. Its output is the
number that matters: what fraction of the machine's steps were unsupported.

**n8n verdict: WEAK, and worth saying so.** `Compare Datasets` is nice and the
grading table is nice; a Python script is fine. Build it in n8n only if it is
adjacent to the workflows the students already have open.

---

### S8 — Metadata is the leak

**Area:** OSINT. **Target:** the seeded synthetic persona corpus from
`docs/teaching-labs.md` Lab 4. **No real people, no scraping.**

**Learning objective.** The OSINT harm in *documents* is metadata and revision
history; it is entirely deterministic to extract; and redaction must be
**verified with the same tool that found the leak.**

**Prerequisite.** Lab 4's corpus must exist. Lab 4 already specifies planting
"EXIF coordinates on a generated image that match an address in the job ad" —
S8 opens that image. Extend the corpus build with DOCX/PDF/JPEG carrying planted
author names, GPS, software versions, template paths and PDF incremental-save
revisions.

**Node graph.**

```
Manual Trigger
  → SSH → <tools-host>:  exiftool -json -r <corpus-dir>          [deterministic]
  → Split Out: one item per file
  → Filter: keep only the ~15 fields that actually leak          [deterministic]
  → Aggregate per document → Merge with the persona Data Table
  → Code: deterministic join → the link graph                    [deterministic]
  → Basic LLM Chain (ONE call over the JOINED GRAPH, not the raw JSON:
      "what does this tell an attacker? two paragraphs.")
  → Data Table
```

**The second arm is an instructor artefact, not a live call.** Overnight, the
cluster is *also* asked to find the links from the **raw** `exiftool` JSON.
Students diff the model's link list against the deterministic join. It will miss
real links and invent absent ones, and the students can prove both because the
instructor planted them.

**Token budget (per student).** One call over the joined graph ≈ 350 prompt,
180 generated: 21.1 + 18.6 = **40 node-seconds**. ×20 = **13 minutes**.

*Instructor overnight:* the raw-JSON arm, 12 documents × (1,400 prompt + 250
generated) = `16800/16.6 + 3000/9.7 = 1,012 + 309 = 1,321 node-seconds ≈ 22
minutes`. **If it were run live instead it would cost 110 node-seconds per
student — 37 minutes for the class, i.e. three quarters of the session for the
arm that is supposed to be the wrong way round.** Stating that on the lab sheet
is worth more than hiding it.

**What the student learns.** That `exiftool` found in one pass what the model
found partially and embellished; and that the join which produced the link graph
was a `JOIN`, not a judgement.

**Defensive counterpart — and it has a satisfying sting.** Students sanitise:
`exiftool -all=`, a PDF re-save, a DOCX properties strip. Then **re-run the
identical pipeline and prove it is clean.** They will find something survives —
a PDF `/ID`, a `docProps/app.xml` template path, or the re-saving application's
own freshly injected metadata. **"Prove your redaction worked with the tool that
found the leak" is the deliverable**, and the failure is the lesson.

**n8n verdict: WEAK.** `exiftool -json | jq` plus a join is a script. Build it
in n8n only for consistency with the rest of the set — and be honest that the
`Merge`/`Aggregate` nodes are convenience, not capability.

---

## 7. Tools: what the fleet does not have, Debian 12 status, and cost

### 7.1 Availability

**CONFIRMED** by `apt-cache policy` against the package index already on node 1
(read-only; no network, no install). This node's `sources.list` carries
`bookworm main`, `bookworm-updates main`, `bookworm-security main` and
`non-free-firmware` — **`contrib` and `non-free` are not enabled.**

| Tool | In Debian 12 `main`? | Candidate version | Installed here |
|---|---|---|---|
| `nmap` | **yes** | 7.93+dfsg1-1 | no |
| `ffuf` | **yes** | 1.1.0-1+b8 | no |
| `hashcat` (+ `hashcat-data`) | **yes** | 6.2.6+ds1-1+b1 | no |
| `john` / `john-data` | **yes** (core, **not jumbo**) | 1.9.0-2 | no |
| `sqlmap` | **yes** | 1.7.2-1 | no |
| `pocl-opencl-icd` (hashcat's CPU backend) | **yes** | 3.1-3+deb12u1 | no |
| `hydra`, `gobuster`, `whatweb`, `dirb`, `wfuzz`, `medusa`, `cewl`, `hashid` | **yes** | various | no |
| `libimage-exiftool-perl` | **yes** | 12.57+dfsg-1 | no |
| `python3-scapy` | **yes** | 2.5.0+dfsg-2 | no |
| `wamerican` / `wbritish` (dictionaries) | **yes** | 2020.12.07-2 | no |
| **`zaproxy`** | **NO — "No such package" in bookworm** (CONFIRMED, <https://packages.debian.org/bookworm/zaproxy>) | — | no |
| **`nikto`** | **in bookworm but `non-free`** (CONFIRMED, <https://packages.debian.org/bookworm/nikto>), so it needs a component this node does not enable | 1:2.1.5-3.1 | no |
| **`nuclei`**, **`seclists`**, **`wordlists`**, **`cyberchef`** | **not in the index at all** | — | no |

**So: everything S1–S8 needs is in Debian 12 `main` except three things**, and
each has a clean answer:

- **CyberChef / cyberchef-server** — Node.js, from npm or its own container.
  Apache 2.0.
- **ZAP** — not in Debian; ship from the project's own distribution (Apache 2.0)
  if a lab needs it. **No workflow above requires it** — it is listed only
  because the operator named it.
- **Wordlists** — build a small curated list rather than pulling SecLists
  (see §8).

### 7.2 Resource cost, and where each tool must run

**F44 decides this, not preference.** From `docs/teaching-labs.md` §2: even at
`nice -n 15`, a CPU-bound sidecar and `llama-server` were caught at 378.9% and
336.8% CPU on the same four cores, and the sidecar's own rate degraded 9-fold.

| Tool | Cost profile | Where it must run |
|---|---|---|
| `john`, `hashcat` | **saturates every core given** | `<tools-host>` **only**. Never on an inference node — this is the F44 case exactly. |
| `sqlmap` | modest CPU on the scanner; the **target** is what suffers | scanner on `<tools-host>`; target is the disposable DVWA VM |
| `ffuf` | network- and target-bound; trivial CPU with `-p` delay | `<tools-host>` |
| `nmap -sV` | modest; version probes are the slow part | `<tools-host>` |
| `exiftool` | negligible | `<tools-host>` |
| `cyberchef-server` | negligible per request | `<class-apps-node>` beside n8n |
| Juice Shop / DVWA | Node.js / PHP+MariaDB; a scan makes them the bottleneck | **disposable VMs on an isolated segment**, rebuilt from image between classes |

**The topology this implies, extending `docs/teaching-labs.md` §2 by exactly one
machine:**

| Machine | Runs |
|---|---|
| `<research-node>` | `llama-server`, research Missing Link. Untouched by the class. |
| `<class-llm-node>` | `llama-server` only. No student-controlled CPU consumer. |
| `<class-apps-node>` (node 3) | n8n, ComfyUI, class Missing Link, nginx, **cyberchef-server** |
| **`<tools-host>` (new, disposable)** | `nmap`, `ffuf`, `john`, `hashcat`, `sqlmapapi`, `exiftool`. Reached **only** by n8n's SSH credential. Rebuilt from image between classes. |
| **target VMs (disposable)** | Juice Shop, DVWA, Lab 7's vulnerable ComfyUI |

**`<tools-host>` is the one new requirement this document adds.** It can be a VM
on `<class-apps-node>`; it does not need to be a fourth physical machine. What
it must be is **disposable**, because it is where every student-triggered
command lands.

---

## 8. Licences, and the items left open

This project killed a corpus source over CC BY-NC-ND
(`docs/corpus-selection.md`), so licences get checked rather than assumed.

| Component | Licence | Label | Note |
|---|---|---|---|
| **OWASP Juice Shop** | MIT | CONFIRMED (<https://github.com/juice-shop/juice-shop>) | clean |
| **DVWA** | GPL-3.0 | CONFIRMED (<https://github.com/digininja/DVWA>) | clean. If a modified DVWA is redistributed as part of a course image, ship the source — trivially satisfied. |
| **CyberChef / CyberChef-server** | Apache 2.0, Crown Copyright | CONFIRMED (<https://github.com/gchq/CyberChef-server>) | clean |
| **hashcat** | MIT | CONFIRMED (<https://github.com/hashcat/hashcat/blob/master/docs/license.txt>) | clean |
| **ffuf** | MIT | CONFIRMED (<https://github.com/ffuf/ffuf/blob/master/LICENSE>) | clean |
| **sqlmap** | GPLv2 (or later) | CONFIRMED (<https://github.com/sqlmapproject/sqlmap/blob/master/LICENSE>) | clean |
| **John the Ripper** | GPL, with exceptions | REPORTED (<https://www.openwall.com/john/doc/LICENSE.shtml>) | Debian ships it in `main` |
| **exiftool** | Perl Artistic / GPL | INFERRED from Debian `main` inclusion | not separately verified |
| **`nmap`** | **Nmap Public Source License** — GPLv2-derived, with added terms | **CONFIRMED but read it** (<https://nmap.org/npsl/>) | **The one real flag.** The NPSL *"prohibits redistribution and use of Nmap within proprietary hardware and software products"* and requires an OEM licence for embedding. Nmap's own text says the authors *"believe it is compliant with the Open Source Definition, but we haven't gone through their certification process."* **Running it to teach is not at issue. Bundling it into a course VM image that is sold, or into the eventual Claude Skill's generated artefacts, is** — check before that happens, not after. |
| **`nikto`** | Debian classifies it **`non-free`** | CONFIRMED (<https://packages.debian.org/bookworm/nikto>) | No workflow above needs it. If it is wanted, that is a deliberate `non-free` decision. |
| **SecLists** | MIT | CONFIRMED (<https://github.com/danielmiessler/SecLists>) | **The licence is not the issue; the provenance is.** `Passwords/Leaked-Databases/` is real people's credentials from real breaches. **S2 uses a synthetic list or `wamerican`.** An MIT licence on a collection says nothing about the rights in what was collected — the same distinction F58 already recorded for SpamAssassin, whose readme says *"copyright for the text in the messages remains with the original senders"*, which is a fact and not a grant. |
| **CTF benchmark datasets** (NYU CTF Bench, Cybench, InterCode-CTF) | **not verified** | — | **No workflow above uses them.** They are cited in §4 as evidence, not adopted as material. If one is ever adopted, check its licence and its challenge-authors' terms separately — a benchmark repo's licence frequently does not cover the challenge content. |
| **n8n** | Sustainable Use Licence | CONFIRMED, F54 / `docs/n8n-feasibility.md` §1 | **The open item is unchanged: the grey edge still wants one email to `license@n8n.io`.** Nothing in this document narrows or widens it. |

---

## 9. What was NOT verified, and would need to be

Listed so nobody mistakes a design for a tested thing.

1. **The `sqlmapapi` endpoint set (S6).** REPORTED from community sources; there
   is no official documentation. **Highest-risk item here.** Fifteen minutes
   against a local `sqlmapapi.py -s` settles it.
2. **Whether `hashcat` works at all on this hardware via pocl (S2).** REPORTED
   as unsupported by the hashcat project, with a history of detection failures.
   `john` is the primary path for exactly this reason; treat hashcat as an
   optional arm.
3. **Whether Debian's core `john` 1.9.0 covers every hash format S2 wants.** Its
   description names the crypt(3) family, LM and Kerberos AFS; the jumbo format
   zoo is not there. Confirm the exact `--format` list with `john --list=formats`
   before writing the lab sheet.
4. **Every token count in §6.** They are *estimates of input size*, and F49 says
   `WORDS_PER_TOKEN` guessing is wrong by up to 2× in both directions. **Run one
   real example of each workflow through `POST /tokenize` and correct the table.**
   The *formula* is sound (F58 reproduces `llama-batched-bench`'s own totals);
   the *inputs* to it here are guesses.
5. **Whether gpt-oss-120b's structured-output compliance survives twenty
   concurrent students.** The `outputParserStructured` auto-fix option sends
   parse errors back to the model — **which is a second LLM call, unbudgeted.**
   Measure the retry rate on one workflow before relying on the budgets.
6. **The n8n Guardrails node's pattern-based checks doing what their names say.**
   The LLM/pattern split in §2 is CONFIRMED from n8n's docs; the *quality* of the
   PII and Secret-Keys detectors is not, and S3 leans on the pattern-based ones.
7. **Juice Shop's `cheatScore` semantics (S3's defensive half).** The field's
   existence and range are CONFIRMED from the integration docs; how it is
   computed, and therefore whether it is a fair detection signal, is not.
8. **The class-model question, still open from F58.** A sub-1B model is
   unmeasured on this fleet. Every budget above assumes gpt-oss-120b at the
   measured 16.6/9.7. **One hour of `llama-bench` would change S1 and S3's shape
   by a factor that matters** — S1 in particular, where two calls per student is
   the constraint.

---

## 10. Summary: the three sentences worth keeping

**On n8n.** It earns its place where there is a **wait** or an **inbound event** —
S6's poll loop and S3's solve webhook — and everywhere else it is a teaching
surface rather than an engine, which is a legitimate reason to use it but should
be said out loud rather than implied.

**On the LLM.** In eight security workflows the model is called **at most twice
per student**, never on raw tool output, never to decide whether something
worked, and never to do arithmetic — and the workflows are better for it, which
is `CLAUDE.md`'s build rule arriving at the same answer from a completely
different domain.

**On cryptanalysis specifically.** 1.91% on Vigenère for the best model on the
best public benchmark, against a deterministic tool that solves the same class
in milliseconds with a confidence score. **Teaching that as a measured result is
worth more than any workflow that papers over it**, and it is the clearest
example this syllabus will get of the difference between a system that uses a
model and a system that is impressed by one.
