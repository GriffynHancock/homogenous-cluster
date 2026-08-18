# Market research: what office workers actually use for document summarisation, and what its output looks like

Commissioned to answer one operator question directly, and to run the inverse
of the check `docs/FINDINGS.md` F37 item 3 recommends: this project has twice
nearly copied "the shape everyone else built" for a job that didn't need it
(RAG-QA tools for summarisation; a chat frontend for a batch workload). This
document asks whether, this time, the standard shape reflects a real user
expectation Missing Link is failing to meet.

**Method note on evidence quality.** Every claim below is labelled
**CONFIRMED** (checked against the vendor's own product documentation or a
primary technical source), **REPORTED** (stated by someone else — a vendor,
a journalist, a survey — not independently verified here), or **INFERRED**
(my own reasoning from other labelled facts). Vendor blog posts and product
pages are **REPORTED at best**, and marked as such even when they describe
their own product, because "what the product does" and "what the vendor
says it does" are not the same claim. Nothing here was measured on this
project's own hardware; that distinction matters given this project's
history of unmeasured numbers (a misread 75% RAM rule, a never-measured
"gigabit" LAN, a "3–6 retries" figure that was actually 2) propagating into
decisions.

---

## Direct answer

**What office workers use, in two tiers:**

1. **What is paid for and deployed:** Microsoft 365 Copilot (Word/Outlook/
   Teams), Google Gemini in Workspace, Adobe Acrobat AI Assistant, and
   purpose-built tools like NotebookLM and Glean for internal knowledge.
   Microsoft alone reports **~20 million paid Copilot seats** at Q3 FY26,
   used at **41% of its enterprise customer base** (REPORTED, Microsoft
   earnings disclosures — see §1). Meeting-summary tools (Otter, Fireflies)
   are a large adjacent category.
2. **What individuals do regardless of what's deployed:** paste documents
   into ChatGPT or Claude's chat window, ad hoc, often on personal accounts
   outside IT's visibility. Cyberhaven's telemetry reports this rose from
   **10.7% to 34.8%** of corporate data pasted into AI tools being
   "sensitive," 2023→2025 (REPORTED, vendor DLP telemetry — see §1). This
   second tier is exactly the sovereignty problem `CLAUDE.md` exists to
   solve, and it is genuinely widespread.

**What the output looks like, across nearly every deployed (tier-1) tool:**
**bullets or short structured prose, with numbered inline citations that are
clickable and jump to the source passage.** This is consistent across
Microsoft Copilot, Google Gemini, NotebookLM, Adobe Acrobat AI Assistant,
Glean, and even ChatGPT's own file-search feature. **Unattributed prose is
the exception among vendor-built, enterprise-facing tools, not the norm** —
every one of them was built, after the fact, to answer "why should I trust
this," and citations were the answer the market converged on independently.
The one place unattributed prose IS the norm is tier 2 — a raw ChatGPT/Claude
chat reply to a pasted document — which is the shadow-IT behaviour this
project exists to displace, not a bar it should aim to clear.

**Missing Link's current output is unattributed prose** (`missing_link/
worker.py` `PROMPTS`/`REDUCE_PROMPTS` — map and reduce prompts that produce
plain text with no citation markers, confirmed by reading the source). That
places it, on this specific axis, closer to tier-2 shadow-IT behaviour than
to the tier-1 tools its target users already have on their desktops. See
"Expectations this project would violate" below — this is flagged there as
the single most consequential finding.

Worth noting for calibration: the project already has more of the
*substrate* for this than the prompts suggest. `missing_link/audit.py` and
`db.chunk_summaries` (with `start_char`/`end_char` offsets, confirmed by
reading `missing_link/db.py`) implement an offline faithfulness-audit hop
that matches final-summary sentences back to source chunks. It exists and
works, but it is a CLI/offline tool — nothing in `app.py` or the templates
wires it into what the user actually sees on a job page. The gap identified
here is a UI/output-format gap on top of already-built machinery, not a
build-from-zero gap.

---

## 1. What is actually in use

### Tier 1 — deployed, paid, sanctioned

- **Microsoft 365 Copilot.** **REPORTED**, Microsoft's own Q3 FY26 earnings
  disclosure (via secondary reporting, not fetched from Microsoft IR
  directly): ~20 million paid enterprise seats, up from 15 million the prior
  quarter; Copilot used by **41%** of Microsoft 365 enterprise customers;
  >90% of the Fortune 500 use it. [sqmagazine.co.uk](https://sqmagazine.co.uk/copilot-statistics/),
  [Luminix](https://www.useluminix.com/reports/company-overviews/the-latest-on-microsoft-s-ai-strategy-spring-2026/source/3).
  Take the specific figures as vendor-adjacent and directional, not audited.
- **Google Gemini in Workspace.** **REPORTED** to be bundled into Docs,
  Sheets, Slides, and Drive with active feature rollout through March 2026.
  [blog.google](https://blog.google/products-and-platforms/products/workspace/gemini-workspace-updates-march-2026/).
  No independent seat-count found for Gemini specifically (Google reports
  Workspace subscriber counts, not Gemini-feature-usage counts).
- **Adobe Acrobat AI Assistant.** **REPORTED** (Adobe's own documentation) to
  summarise PDF/Word/PowerPoint up to 120 pages, generating linked citations.
  [Adobe](https://helpx.adobe.com/acrobat/desktop/explore-pdf-spaces/view-citations.html).
- **NotebookLM.** **REPORTED**, Google's own docs and independent write-ups.
  Popular for research/synthesis work specifically because of its citation
  model (§2).
- **Glean.** **REPORTED**, enterprise search + "Deep Research" agent that
  produces citation-backed, 5–10 page reports pulling from connected
  internal systems (Confluence, Drive, Slack, Jira, SharePoint), with
  permission-aware indexing. [glean.com](https://www.glean.com/perspectives/top-ai-assistants-for-accurate-source-citations).
- **Otter.ai / Fireflies** for meetings — a large, separate but related
  category (§2, §5).

### Tier 2 — ad hoc, unsanctioned, exactly this project's target problem

This is the behaviour `CLAUDE.md` frames as the sovereignty failure mode,
and the evidence is that it is common, not rare:

- Cyberhaven's Data Security Report (**REPORTED**, vendor DLP telemetry, not
  independently auditable methodology): **11% of data pasted into ChatGPT
  and similar tools was confidential** (2024 report), rising to **34.8% of
  corporate data being sensitive** by 2025, up from 10.7% two years earlier.
  Data volume pasted into AI tools grew **485% year-over-year 2023→2024**.
  [Cyberhaven](https://www.cyberhaven.com/blog/4-2-of-workers-have-pasted-company-data-into-chatgpt).
- Salesforce survey (**REPORTED**, secondary citation, original not
  fetched): **27% of enterprise employees** say they've entered confidential
  company data into public AI tools; **65%** use at least one AI tool not
  approved by IT.
- LayerX study (**REPORTED**): **77%** of AI users copy-paste data into
  chatbot queries; **82%** of those pastes are from personal accounts
  invisible to company oversight.
- Microsoft's own 2025 Work Trend Index (**REPORTED**, cited secondarily):
  **78%** of AI users at work bring their own AI tools outside IT approval —
  Microsoft's own flagship survey documenting the exact problem its own
  Copilot product is meant to solve.
- [Sources: Airia](https://airia.com/blog/shadow-ai-statistics-key-data-points-every-ciso-needs-in-2026/),
  [The Register](https://www.theregister.com/2025/10/07/gen_ai_shadow_it_secrets),
  [Cyberhaven](https://www.cyberhaven.com/blog/4-2-of-workers-have-pasted-company-data-into-chatgpt),
  [Worklytics](https://www.worklytics.co/blog/track-if-employees-use-chatgpt).

**Read these numbers as directional, not precise.** They come from AI-DLP
vendors with a commercial interest in the finding ("your staff are leaking
data, buy our tool"), and none of the underlying methodologies were
inspected here. But multiple independent vendors converge on the same
qualitative picture, and it matches this project's own stated premise almost
exactly. **This is the strongest evidence in this document that the tier-2
behaviour `CLAUDE.md` worries about is real and common**, which validates
the project's premise even though the specific percentages should not be
quoted as measured facts.

---

## 2. What the output actually looks like

This is the operator's central question. Findings per tool:

| Tool | Format | Length | Cites source? | Mechanism | Exposes uncertainty? |
|---|---|---|---|---|---|
| **MS Copilot (Word)** | Prose paragraph, panel above doc | Short | **Yes** — numbered inline citations | Hover shows snippet; click jumps to passage in doc | No explicit uncertainty marker; known to under-cite later/middle content |
| **Google Gemini (Docs)** | Side-panel summary | Short–medium | **Yes**, per Google's docs | Citations exportable as "Works Cited" | Not found |
| **NotebookLM** | Prose + numbered citations ("grey ovals") | Short–medium | **Yes**, always | Click jumps to exact source paragraph | Grounds answers in retrieved chunks only — refusal/hedging behaviour when ungrounded is the implicit uncertainty signal |
| **Adobe Acrobat AI Assistant** | Prose with numbered clickable refs | Short | **Yes** | Reference includes doc title, section, page number; click highlights source | Not found |
| **Glean (Deep Research)** | Structured multi-page report (5–10 pages) | Long | **Yes**, "fully linked citations" | Pulls from multiple connected systems | Not found |
| **ChatGPT / Claude, plain chat with pasted/uploaded text** | **Unattributed prose** | Variable | **No**, by default in a bare chat turn | — | No |
| **ChatGPT, native File Search (API/some UI paths)** | Prose with inline citation markers | Variable | **Yes**, but implemented via hidden Unicode placeholder characters swapped for clickable citations client-side; reported to sometimes leak as raw text (`fileciteturn0file2...`) | — | No |
| **Otter.ai / Fireflies** | Structured: decisions / key points / action items as separate sections | Short, bulleted | **Yes** — every summary point links to the timestamped transcript | Click jumps to audio/video moment | No |

**Sources:** [Windows Forum on Copilot citations](https://windowsforum.com/windows-news.4/copilot-in-word-adds-inline-citations-for-source-grounded-drafts.440813/),
[MS Support](https://support.microsoft.com/en-us/office/create-a-summary-of-your-document-with-copilot-in-word-79bb7a0a-3bf7-41fe-8c09-56f855b669bf),
[Google Workspace update](https://blog.google/products-and-platforms/products/workspace/gemini-workspace-updates-march-2026/),
[learnprompting.org NotebookLM guide](https://learnprompting.org/blog/notebooklm-guide),
[Adobe citation docs](https://helpx.adobe.com/acrobat/desktop/explore-pdf-spaces/view-citations.html),
[Glean](https://www.glean.com/perspectives/top-ai-assistants-for-accurate-source-citations),
[OpenAI community forum on file-search citation markers](https://community.openai.com/t/unexpected-citation-markers-appearing-in-text-output-when-using-file-search/1362380),
[Fireflies knowledge base](https://guide.fireflies.ai/articles/9547055509-Fireflies-AI-Meeting-Summaries:-View,-Customise,-Expand,-Regenerate),
[Grain Fireflies vs Otter comparison](https://grain.com/blog/fireflies-vs-otter).

**Is unattributed prose the norm or the exception?** Among tools vendors
built specifically for enterprise document work, **it is the exception.**
Every mainstream deployed tool checked here — Copilot, Gemini, NotebookLM,
Acrobat, Glean, even ChatGPT's own file-grounded mode — converged
independently on numbered, clickable, source-jumping citations as the answer
to "why should I trust this." That degree of independent convergence across
competing vendors is itself evidence this is a real user expectation and not
a fad: multiple companies with no reason to copy each other's UI arrived at
the same shape.

**Caveat, and it matters for this project specifically.** Citations are
retrofit trust theatre in one specific, important sense: a citation number
proves the model *referenced* a passage, not that the sentence attached to
it is *faithful* to that passage. **CONFIRMED** for NotebookLM: Microsoft's
own Copilot docs admit the model "may focus on the beginning and end of a
file and give less attention to material in the middle" even while
producing citations for the parts it does cover — i.e., citations do not
protect against the "lost in the middle" failure this project's map-reduce
chunking is specifically designed to avoid (`worker.py` docstring cites
arXiv:2307.03172 for exactly this). So a naive "just add citation numbers"
fix would visually resemble the market standard without solving the
faithfulness problem this project actually cares about more than any
competitor does. Missing Link's `chunk_summaries` + `audit.py` substrate
(char-offset-level, checked against the reduce step, not just against
retrieval) is architecturally closer to solving the real problem than a
citation-number UI alone would be — the gap is that it isn't surfaced.

---

## 3. Long documents: what actually happens, and does the user find out

**Microsoft Copilot in Word — CONFIRMED via Microsoft's own support
documentation and corroborated by a Microsoft Q&A user report of the exact
failure mode:**
- Microsoft's guidance: summarisation works best under ~15,000 words (~20
  pages); broader reasoning/reference can span up to ~300 pages / 1.5M
  words, but Microsoft explicitly recommends shorter documents "for
  comprehensive, end-to-end summaries." [MS Support](https://support.microsoft.com/en-au/topic/keep-it-short-and-sweet-a-guide-on-the-length-of-documents-that-you-provide-to-copilot-66de2ffd-deb2-4f0c-8984-098316104389).
- **CONFIRMED user-facing message exists**, but is generic: "The document
  content has been truncated to meet size constraints." — this is at least
  a disclosed truncation, not a fully silent one. [datastudios.org](https://www.datastudios.org/post/microsoft-copilot-context-window-and-document-limits-how-much-content-can-be-read-summarized-and-in-practice).
- **But it does not tell you *what* was cut**, and Microsoft's own docs
  independently confirm the "focuses on beginning and end, gives less
  attention to the middle" pattern for documents that fit inside the window
  but are long. So the *disclosed* failure (truncation past the limit) and
  the *undisclosed* failure ("lost in the middle" within the limit) are
  both present, and only one is surfaced to the user.
- A live Microsoft Q&A thread (**REPORTED**, single user report, not
  independently reproduced here) reports Copilot silently "does not read
  entire doc" below the documented limit in some cases — i.e. even the
  disclosed-truncation message doesn't always fire when it should.
  [Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/5815687/copilot-in-word-now-does-not-read-entire-doc-paylo).

**ChatGPT / Claude, general chat context — CONFIRMED context limits exist
(Claude 200K tokens standard, up to 1M on some tiers; ChatGPT ~128K),
REPORTED behaviour on overflow varies by product surface:**
- Claude: reported to silently drop the *earliest* content from context
  when the limit is exceeded across a long conversation with multiple
  documents, with only a generic "getting long" warning, not a specific
  "this content was dropped" notice. **REPORTED**, not independently
  verified against Anthropic's own documentation in this pass.
  [MemX](https://memx.app/fix/claude-conversation-too-long/).
- Both products enforce **hard per-file limits with explicit errors**
  (Claude: 30MB/file, PDFs capped at 1000 pages, "Uploaded file is too
  large") rather than silently truncating a single oversized file — the
  failure mode for a single huge document is a loud rejection, not silent
  truncation. The silent-drop risk is specifically a **multi-document,
  long-conversation** phenomenon, not a single-huge-document one.
  [fast.io](https://fast.io/resources/claude-file-upload-limit/), [Claude support](https://support.claude.com/en/articles/8241126-upload-files-to-claude).

**NotebookLM — CONFIRMED, Google's own limits documentation:** hard cap of
500,000 words / 200MB **per source**; a document over that size **fails to
import at all** rather than being silently truncated. [elephas.app](https://elephas.app/blog/notebooklm-source-limits).
This is the cleanest failure mode found in this research: loud and total,
not silent and partial.

**Adobe Acrobat AI Assistant — REPORTED, Adobe's own product page:** caps
at 120 pages for summarisation. Behaviour beyond that limit (reject vs.
truncate) not established in this pass — flagged in "what I could not
establish."

### Answer to the question as posed

**Mixed, and worth being precise about, because "mainstream tools silently
truncate" is not uniformly true.** The pattern that actually emerges:

- **Hard, disclosed limits are common** (NotebookLM's reject-on-import,
  Claude's per-file size errors) — these are not silent, they are loud
  failures the user cannot miss.
- **Soft degradation within a stated limit is the more common and more
  dangerous failure**, and it is largely *undisclosed*: "focuses on
  beginning and end," reduced fidelity in the 20–300 page range for Word,
  and general "lost in the middle" behaviour that no vendor surfaces as a
  warning to the user. This is the failure mode this project's map-reduce
  chunking is built to avoid (`CLAUDE.md`, `worker.py`), and on the
  evidence gathered here, **it is a genuine, still-current differentiator**
  — not one mainstream tools have already solved. **INFERRED**, composing
  several REPORTED/CONFIRMED points above; not measured head-to-head
  against Missing Link's own output.
- Where truncation is silent (100+ page Word documents receiving reduced
  attention to mid-document content without any UI signal), it is
  effectively invisible to the user in the same way this project's own
  chunk-budget bug was invisible in `docs/FINDINGS.md` F34 — a summary that
  looks finished because nothing in the UI says otherwise.

---

## 4. What regulated / sovereignty-constrained organisations do today

**Australian legal sector.** **CONFIRMED via professional body statements**
(not vendor pages): the Legal Profession Uniform Law Australian Solicitors'
Conduct Rules 2015 (ASCR rule 9.1) and Barristers' Rules (BR rule 114)
create a confidentiality obligation solicitors read as **forbidding** entry
of confidential/privileged client information into public AI chatbots
including ChatGPT and Microsoft 365 Copilot, absent contractual assurances.
[Baker McKenzie](https://resourcehub.bakermckenzie.com/en/resources/global-attorney-client-privilege-guide/asia-pacific/australia/topics/07---artificial-intelligence),
[Victorian Legal Services Board](https://lsbc.vic.gov.au/news-updates/news/statement-use-artificial-intelligence-australian-legal-practice),
[Queensland Bar Association guidelines](https://www.qldbar.asn.au/baq/v1/viewDocument?documentId=3041).
This is **"forbid it outright"** as an option, live and current — the Law
Council of Australia has flagged the same risk at national level.

**Australian government.** **CONFIRMED, concrete example:** Victoria's
Office of the Information Commissioner **ordered** the state's child
protection agency in 2024 to IP/DNS-block generative AI after staff entered
substantial personal information about a specific child into ChatGPT to
draft a risk report. [Medianama](https://www.medianama.com/2024/10/223-australian-information-commissioner-halts-genai-use-for-child-protection-agency-as-chatgpt-downplays-risk/).
Separately, the **Department of Home Affairs blocked ChatGPT** for staff
(later opened under "restricted and approved arrangements"); the
**Departments of Social Services and Health blocked it** too. [iTnews](https://www.itnews.com.au/news/home-affairs-blocks-public-servants-from-using-chatgpt-596130),
[Cyber Daily](https://www.cyberdaily.au/policy/9091-home-affairs-blocks-chatgpt-use-within-government).
So the **"forbid it outright"** option is not hypothetical in this project's
own jurisdiction — it happened, at both state and federal level, and at
least once *because* staff did the exact tier-2 pasting behaviour this
project is meant to prevent.

At the same time, **Microsoft 365 Copilot has an IRAP assessment covering
PROTECTED-level workloads**, per the Australian Government's own assessment
register, and is positioned by Microsoft for onshore-processed Australian
government use. **REPORTED**, needs care: search results here could not
confirm Copilot specifically (as opposed to the base M365 platform) is
separately and currently listed in-scope on the Service Trust Portal — flag
this rather than assert it. [aguidetocloud.com](https://www.aguidetocloud.com/blog/microsoft-365-copilot-data-residency-anz-government/),
[windowsforum.com](https://windowsforum.com/windows-news.4/microsoft-azure-dynamics-365-microsoft-365-irap-update-for-protected-workloads-in-australia.407798/).
This is the **"licence a compliant cloud tenancy"** option, and it is real
and actively marketed for exactly this sector.

**Australian healthcare.** **REPORTED**, a national AI-in-healthcare policy
roadmap exists and explicitly names data sovereignty as a strategic concern
— "failure to hold sovereignty over foundational AI models... could see
Australian health technology subject to geopolitical decisions from outside
the country." [aph.gov.au document](https://www.aph.gov.au/DocumentStore.ashx?id=98f1de72-aca7-429b-aa08-8aa4393586fa&subId=760056),
[Pharmacy Daily](https://pharmacydaily.com.au/news/sovereignty-key-to-health-ai-future/121578).
Ambient clinical scribing and note summarisation are explicitly named as
permitted, high-value applications.

**Is there a real market of on-prem sovereign AI vendors, or does this
project have no peers?** **There is a real, if young, market — REPORTED
across multiple independent vendors, none independently audited here:**

- **customllm.au** — offers both cloud-in-Australia and genuine on-prem
  ("deploy your custom LLM entirely within your own data centre") for
  Australian healthcare and government, priced **$8,000–$25,000/month**
  (healthcare) by bed count. **No customer case studies or independent
  verification found on the page fetched** — this reads as a marketing
  page with unverified accuracy/compliance claims (96.8% summarisation
  accuracy, 94% coding accuracy — REPORTED, vendor's own unaudited
  figures). [customllm.au/healthcare](https://customllm.au/custom-llm-for-healthcare),
  [customllm.au/government](https://customllm.au/custom-llm-for-government).
- **IOTAI Australia** — REPORTED to sell on-prem LLM deployment (NVIDIA DGX
  Spark hardware) targeting Australian legal, healthcare and finance SMEs,
  explicitly citing legal privilege and AHPRA/RACGP guidance as the driver.
  [iotai.com.au](https://www.iotai.com.au/services/sovereign-ai).
- **Blue Crystal Solutions** — REPORTED to offer private LLM deployment
  (cloud-in-Australia or on-prem) to South Australian government agencies,
  explicitly tied to a **$28 million** state government AI funding program,
  from "$500/month." [CRN](https://www.crn.com.au/news/2025/ai/blue-crystal-solutions-launches-private-llm-service-for-sovereign-ai-in-australia).
- **AusGPT / AussieGPT** — checked directly (WebFetch on ausgpt.com.au).
  **These are NOT on-prem.** They are cloud services running third-party
  models (OpenAI/ChatGPT-family) hosted on Azure inside Australian data
  centres, differentiated by data residency and privacy contractual terms,
  not by the air-gapped/organisation-owned-hardware model this project
  uses. Useful as evidence the **"licence a compliant cloud tenancy"**
  option has smaller, Australia-specific players too, not just Microsoft
  and Google.

**Honest synthesis, not shaped to flatter the project:** the market
evidence supports a **three-way split**, not a clean "forbid vs. compliant
cloud" binary as the research questions framed it:

1. **Forbid outright** — confirmed real (Victorian child protection, some
   federal departments).
2. **Buy a compliant cloud tenancy** — confirmed real and the largest
   segment by seat count (Microsoft's IRAP-assessed Copilot, AusGPT-style
   Australian-hosted wrappers around the same frontier models).
3. **Buy or build genuinely on-prem/sovereign infrastructure** — real, but
   this segment appears **small, young, and vendor-marketing-heavy** on the
   evidence gathered here. Multiple vendors exist and are actively selling
   into this exact niche (Australian health/legal/government, on-prem,
   faithfulness-motivated), which means **this project is not inventing a
   category that doesn't exist** — but none of the vendor pages found here
   offered independently verifiable customer evidence, case studies, or
   measured accuracy figures of the kind `CLAUDE.md` demands of this
   project's own claims. If this project's "measure everything" discipline
   were applied to these competitors' marketing claims, most would fail it.

**What could not be established:** actual customer counts, deployment
scale, or independent performance verification for any of the on-prem
Australian vendors found. All figures from customllm.au, IOTAI and Blue
Crystal are vendor-reported and unverified.

---

## 5. What users complain about

**Generic, unfocused summaries — CONFIRMED as a widely repeated complaint,
across independent sources, not vendor-specific:**
- "Vague purpose produces generic summaries... skip information that might
  be relevant... give the same importance to all topics." [Towards Data
  Science](https://towardsdatascience.com/do-not-use-chatgpt-only-to-summarize-text-bd2001db8ce7/).
- Academic critique (REPORTED, not independently verified here): summarised
  text is sometimes "generat[ed]... based more on what [the model has] seen
  in training than on the text you gave them" — i.e. summaries can drift
  toward generic, plausible-sounding language rather than grounded content.
  [ea.rna.nl](https://ea.rna.nl/2024/05/27/when-chatgpt-summarises-it-actually-does-nothing-of-the-kind/).

**Fabrication / hallucination in meeting and document summaries —
REPORTED, several independent incident write-ups, none independently
reproduced here:**
- Otter: "struggles the moment people start talking fast, talking over each
  other, or using industry jargon... flattened into something vaguely
  coherent but not actually accurate." [mrsproductivity.medium.com](https://mrsproductivity.medium.com/ai-meeting-assistants-i-tested-otter-fireflies-fathom-and-5-others-116d10ebbfce).
- "Consensus fabrication" — AI notetakers stating agreement where none was
  reached — and "topic inflation" — elevating minor comments to key
  takeaways. Attributed to a named researcher in one write-up; **this
  specific attribution was not independently verified** and should be
  treated as REPORTED, single-source.
- A named incident (ClearPath Analytics, REPORTED, single source, company
  and figures not independently verified): 63% of quarterly OKR check-ins
  reportedly failed because an AI notetaker (Fireflies) missed
  action items "buried in follow-up questions or 'let's revisit' clauses."
  [source article, unverified](https://www.sybill.ai/blogs/fireflies-vs-otter-ai) —
  **flagged as low-confidence**, reads like case-study marketing copy for a
  competing product; do not treat the 63% figure as reliable.

**No way to verify a claim — this is the citation gap in reverse, and it is
the complaint that citations were built to answer.** Not found as an
explicit, quotable complaint thread in this pass, but it is the load-bearing
motivation Adobe, Microsoft, Google and NotebookLM all cite in their own
product documentation for *why* they built citations — i.e. it is
**INFERRED from vendor design decisions being a response to the same
unstated complaint**, not from a user survey saying the words "I can't
verify this."

**Poor handling of tables and scanned documents — CONFIRMED as a repeated,
mechanism-level finding across multiple summarisation-tool reviews:**
- "Tables tend to be returned as plain lines of text, columns may be read
  in the wrong order, headers/footers... leak into the main content."
  [scispace.com](https://scispace.com/resources/the-ocr-trap-why-scanned-pdfs-break-your-ai-and-how-to-fix-them/).
- Scanned/degraded documents (faded scans, unusual fonts, handwriting,
  skew, glare) cause **character-level misreads** (0↔O, 1↔l), dropped
  diacritics, and words broken across line wraps. **REPORTED**, general
  OCR-literature summary, not tool-specific benchmarking.
- Directly relevant to this project: **Missing Link's own PDF handling bug**
  (`docs/FINDINGS.md`, the "off-by-one `substr(document,1,5)='%PDF'`"
  incident referenced in `docs/REQUIREMENTS.md`) is the same class of
  failure the mainstream tools are independently reported to have. This is
  not a solved problem anywhere in the market — it is a shared, ongoing
  weak point across this project and its competitors alike.

**Complaints about Copilot specifically — CONFIRMED, direct primary-source
evidence (Microsoft's own support forum), not third-party reporting:**
- Multiple live threads on `learn.microsoft.com/answers` document
  fabricated dates, unsourced claims, and "durable facts" being
  contradicted or violated across sessions. One specific thread (REPORTED,
  single user, unverified beyond the forum post itself) logs **24 "durable
  fact" violations in a 5-day window**, 9 of them after the facts had
  supposedly been locked and confirmed. [Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/5659498/systemic-failure-copilot-for-windows-violates-dura).
  Treat the specific count as one user's unverified log, not an audited
  figure — but the phenomenon (contradicted facts across sessions) recurs
  across several independent threads, which is stronger evidence than any
  single one.

---

## 6. Expectations this project would violate, ranked

Ranked by how much they matter for the stated target user (Australian
health/legal/education/government/community-services staff producing
overnight document summaries on sensitive material). For each: whether it
is a **genuine gap** Missing Link should consider closing, or a **shape
borrowed from a different job** that this project is right to keep
rejecting.

### 1. No citations / source attribution — GENUINE GAP, highest priority

Every deployed tier-1 tool surveyed (§2) ships numbered, clickable
citations back to the source document as a baseline, table-stakes feature —
not a differentiator, an expectation. Missing Link's map/reduce prompts
(`missing_link/worker.py`) produce plain prose with no citation markers.
For a workload `CLAUDE.md` itself calls a **security property**
("Faithfulness is a security property here... a fabricated fact in a
summary is a failure of the same order as a leak"), shipping *less*
attribution than every mainstream competitor is a real deficiency, not a
cosmetic one — and it is a deficiency the project's own architecture is
closer to fixing than most competitors, because `chunk_summaries` already
carries `start_char`/`end_char` per chunk and `audit.py` already does
sentence-to-chunk matching offline. **The gap is surfacing that machinery
in the job page, not building it from nothing.** Recommend treating this as
the top follow-up item, separate from this research task.

### 2. Exposing uncertainty / "the document says" vs. "I infer" — GENUINE GAP, but partially addressed by prompt design

None of the tier-1 tools surveyed were found to expose explicit uncertainty
markers either (§2 table — "not found" in every row but NotebookLM's
implicit grounding-refusal behaviour). So this is not a bar competitors
clear that Missing Link fails. But it *is* a documented complaint category
(§5, generic/fabricated summaries) and `CLAUDE.md`'s own prompts already
instruct the model to "say so" when the text is inconclusive
(`PROMPTS["summarise"]`). This is worth strengthening but is **not** a case
of falling behind a shipped market standard — it is ahead of one.

### 3. Silent degradation on long documents — Missing Link is **already ahead here**, not behind

§3 found that mainstream tools' worst failure mode (soft, undisclosed
quality loss within a stated length limit — Word's "focuses on beginning
and end") is exactly the "lost in the middle" problem this project's
map-reduce chunking (`CHUNK_TOKENS = 4096`, 10% overlap) is specifically
designed to avoid, on the evidence cited in `worker.py`'s own docstring
(arXiv:2307.03172, arXiv:2310.00785). **This is a genuine, current
differentiator in this project's favour**, not a gap. Worth stating
explicitly in the project's own materials rather than assuming it needs
defending.

### 4. Sub-minute turnaround / chat follow-up — SHAPE BORROWED FROM A DIFFERENT JOB, correctly rejected

`CLAUDE.md` is explicit and, on this research, correctly so: "submit
overnight, read in the morning," "nobody is waiting at a prompt." None of
the research above surfaces a user complaint of the form "the overnight
summary took too long" — the complaints are about quality (generic,
fabricated, unverifiable), never about async turnaround being
unacceptable for a batch document task. Interactive chat follow-up (as in
ChatGPT/Claude/NotebookLM's Q&A mode) is a **different tool for a different
moment** — asking a follow-up question about a document that's already
been read is not the same task as producing the first read. Do not add
this; it would be re-committing the exact error F37 item 3 warns against.

### 5. Exports to Word/PDF — LIKELY GENUINE, LOW-STAKES GAP

Every deployed tool surveyed (Copilot, Gemini, Glean, NotebookLM) offers
export to a document format office workers already use downstream (Word,
PDF, "Works Cited" blocks). Missing Link currently offers a raw-text page
(`docs/REQUIREMENTS.md`, "a web page to see the output, at least as raw
text printed into a text box" — already delivered). Raw text is sufficient
for reading and copy-paste but not for handing a finished summary onward
inside an organisation's normal document workflow (attaching to a case
file, pasting into a report template with formatting intact). **Genuine but
minor** — worth a cheap "download as .docx/.pdf" affordance eventually, not
urgent relative to item 1.

### 6. Working on a whole folder/batch at once — ALREADY MET

`docs/REQUIREMENTS.md` records this was explicitly requested and delivered
(`POST /batch`, multi-file). Tier-1 tools vary here — Glean and NotebookLM
handle multi-source corpora well; Copilot and Acrobat are single-document
per summary — so Missing Link is at or ahead of the market median already.
Not a gap.

### 7. Tables and scanned PDFs — SHARED WEAKNESS, NOT A COMPETITIVE GAP

§5 found this is a widely reported weakness **across the market**, not a
place competitors have solved and Missing Link hasn't. Missing Link had its
own PDF-handling bug in the same family (`docs/REQUIREMENTS.md`
cross-reference to the F-series PDF incident). Worth fixing on its own
merits, but not something to chase because "everyone else does it better" —
on the evidence gathered here, they mostly don't.

### 8. Exposing citations that are technically present but not faithfulness-checked — a trap to avoid, not an expectation to meet

Noted in §2: citations prove reference, not faithfulness — Microsoft's own
documentation admits Copilot cites unevenly across a long document while
still under-attending to its middle. If Missing Link adds citation markers
(item 1) without also surfacing the `audit.py` faithfulness hop, it would
match the market's *cosmetic* standard while missing the substance the
market standard doesn't actually deliver either. Worth naming explicitly so
a future "add citations" pass doesn't stop at parity with a shallow
industry norm.

---

## What I could not establish

- **Independent, audited usage figures for any tool.** Every adoption
  number in this document (Copilot seats, Gemini rollout scope, tier-2
  pasting percentages) traces to the vendor itself or a DLP vendor with a
  commercial interest in a high number. None were cross-checked against a
  third, disinterested source.
- **Whether Microsoft 365 Copilot specifically (not just base M365) is
  currently, separately listed as IRAP PROTECTED-assessed** on the
  Australian Government Service Trust Portal — the sources found asserted
  this but one explicitly flagged uncertainty about Copilot's separate
  listing status. Would need direct access to the Service Trust Portal to
  confirm.
- **Real customer counts or independent verification for any Australian
  on-prem/sovereign LLM vendor** (customllm.au, IOTAI, Blue Crystal). All
  figures found are vendor-reported and none could be cross-checked.
- **A concrete example of an Australian health, legal, education, or
  community-services organisation that has actually deployed on-prem
  hardware in the shape this project builds** (salvaged/idle hardware, not
  purchased appliance). Everything found in the "on-prem" tier was a
  commercial vendor selling new/purpose-bought hardware or managed
  infrastructure, not a story matching this project's specific "we already
  own the hardware" premise. This is the single most decision-relevant gap
  in what could be found — it means the "we have a room of old computers"
  framing in `CLAUDE.md` may be genuinely novel relative to the market
  found here, or it may simply be that such stories don't get published.
  Cannot distinguish those two explanations from this research.
- **Adobe Acrobat's behaviour beyond its stated 120-page limit** — reject
  vs. truncate was not established.
- **Whether NotebookLM's, Glean's, or Adobe's citation-click actually
  verifies faithfulness** or only proves the passage was retrieved/
  referenced — flagged as a likely gap (§6 item 8) but not independently
  tested against a real document in this pass.
- **Direct, first-party user reviews** (G2, Capterra, Trustpilot) for
  Copilot/Gemini summarisation quality specifically — search results
  surfaced secondary write-ups and Microsoft's own support forum rather
  than raw review-site data; a dedicated pass through G2/Capterra review
  text was not completed.
- **A rigorous, non-vendor comparison of tier-2 (ad hoc chat) usage
  specifically for document summarisation** as opposed to AI use in
  general. The shadow-IT statistics in §1 are about AI use broadly, not
  document-summarisation use specifically, and the summarisation-specific
  slice of that behaviour could not be isolated from the sources found.
