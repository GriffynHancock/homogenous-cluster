# Corpus selection: do the operator's picks match the target market?

Answers one operator question directly: do the Privacy Act, NIST SPs, the ISM,
and multiple revisions of the same instrument **"make sense as source
documents considering the intended market"** — Australian health, legal,
education, government and community-services offices producing overnight
document summaries of sensitive material (`CLAUDE.md`, target user and
workloads).

**The resolution the whole task turns on:** these are governance documents an
office *reads*, not documents it *summarises* as work product. But real work
product can never enter a test corpus (`CLAUDE.md`, `docs/REQUIREMENTS.md`
implicitly, and the sovereignty argument this whole project exists to serve)
— so the actual requirement is not "what would this office summarise" but
**"what public document is structurally isomorphic to what this office would
summarise."** That reframing is answered below with a ranked shortlist, then
justified question-by-question.

**Method note on evidence quality**, same convention as `docs/market-
research.md`: every claim is **CONFIRMED** (checked against a primary source
— the publisher's own copyright page, an official register, a fetched
document), **REPORTED** (stated by a secondary source, not independently
verified here), or **INFERRED** (my own reasoning from labelled facts, or
absence of evidence honestly flagged as absence). Nothing here was measured
against the actual project corpus — this is a sourcing and licensing
assessment, not a repeat of `docs/chunk-boundary-measurement.md`.

---

## Ranked, actionable shortlist

Ordered by (structural fit to real work product) × (licence clean enough to
bulk-fetch and keep). "Question answered" is the specific thing adding this
document to the corpus buys, not a generic "diversity" justification.

| # | Source | Fetch as | Licence | Question it answers |
|---|---|---|---|---|
| **1** | **OAIC Commissioner-initiated investigation determinations** (`oaic.gov.au/__data/assets/pdf_file/.../*.pdf`) | Direct PDF, born-digital, ~101 available since Nov 2010 | **CONFIRMED CC BY 4.0 International** (oaic.gov.au copyright page) | Closest public proxy to an internal incident investigation: narrative fact-finding + numbered findings against statutory tests (the APPs), real personal/health-information incidents, real org names, real dates |
| **2** | **Privacy Act 1988 point-in-time compilations** (`legislation.gov.au/C2004A03712/{date}/{date}/text/original/pdf` or `/epub`) | Direct PDF/EPUB/HTML, clean born-digital text, dozens of dated compilations exist | **CONFIRMED CC BY 4.0** (Commonwealth "free use of legislation" policy, ag.gov.au) | The cleanest possible source for the **revision-diff experiment** (Q5) — small, real, dated textual deltas between compilations of one instrument |
| **3** | **Commonwealth Ombudsman investigation reports** | Direct PDF from ombudsman.gov.au | **CONFIRMED CC BY 4.0** | Second-best incident/complaint-investigation proxy; structurally close to #1 but a different regulator voice — widens the sample without changing the genre |
| **4** | **ISM quarterly editions** (March/June/September/December, each a full PDF, years of back issues at cyber.gov.au) | Direct PDF | **CONFIRMED CC BY 4.0 International** (cyber.gov.au copyright page) | Two jobs, neither is "governance reading practice": (a) large-PDF/dense-table/clause-density extraction stress test (Q4), (b) the **best available revision-diff pair** for *small, incremental, mostly-textual* changes edition-to-edition — sharper than the Privacy Act for that specific purpose because ISM editions are quarterly and heavily numbered-control structured |
| **5** | **Royal Commission final reports** (Aged Care, Disability, Robodebt) | Direct PDF, by volume/chapter, not whole document | **CONFIRMED CC BY 4.0 International** (each Royal Commission's own copyright page checked) | Long narrative findings + recommendations at the scale of a major internal board investigation or case review — the length regime the project's map-reduce chunking exists for |
| **6** | **Federal Court of Australia judgments**, fetched from **fedcourt.gov.au directly**, not scraped from AustLII | Individual PDF/HTML per judgment, hand-picked, small N | **CONFIRMED**: Commonwealth-produced court material is progressively CC-licensed (Attribution 3.0 AU); judgments themselves may be reproduced unaltered with attribution even before that rollout completes (fedcourt.gov.au/copyright) | Highest clause-marker and cross-reference density available (Q2) — a genuine stress case for the clause-density metric, and a proxy for board/legal advice documents with heavy conditional structure |
| **7** | **NIST SP 800-53** (Rev. 4 **and** Rev. 5) | Direct PDF from nvlpubs.nist.gov | **CONFIRMED public domain**, 17 U.S.C. §105 (nist.gov/copyrights-disclaimers) — the cleanest licence of anything on this list | Extraction stress test (dense control tables, cross-references by ID) **and** a second, structurally different revision-diff case: Rev4→Rev5 is a *major reorganisation*, not a small edit — good for testing whether a faithfulness checker over-reacts to structural churn that isn't a factual disagreement |
| **8** | **Coroners' findings**, cherry-picked, not bulk-scraped, and licence checked per state before use | Direct PDF from each state Coroners Court site | **Mixed — check per state, see Q6** | Health-adjacent narrative investigation with an operative numbered-findings tail (cause of death, recommendations) — a genuinely different register from OAIC/Ombudsman (life-and-death narrative rather than data-handling) |

**Demoted relative to the operator's original framing, with reasons:**

- **The ISM moves from "primary sourcing pick" to "extraction stress test +
  revision-diff pair."** It is real, licence-clean, and worth keeping — but
  not for the reason it was originally proposed (see Q4). It is not a good
  proxy for narrative work product; it is a good proxy for a *different*
  problem the corpus also needs: what happens when the pipeline meets a
  dense, numbered-control document that most resembles a policy manual an
  office would consult, not summarise.
- **AustLII as a bulk source is dropped**, not the underlying case law.
  AustLII's own Usage Policy explicitly excludes "AI-related uses" and bulk
  republication from its permission grant, independent of what its
  `robots.txt` (which is permissive for unlabelled crawlers) suggests — see
  Q6. Fetch individual judgments from the originating court's own site
  instead; AustLII is fine for a human to browse to *find* a citation, not
  for this project to scrape.
- **Hansard is not recommended at all**, not merely demoted — see Q6. Its
  licence (CC BY-NC-ND) prohibits derivative works, and chunking +
  summarising a document for an LLM pipeline is hard to characterise as
  anything other than a derivative use.

---

## Q1 — What do these offices actually summarise?

Evidence is sector-by-sector, and it is uneven — some sectors have clear,
CONFIRMED published document *structures*; none had a source that says in so
many words "and then a staff member summarises this into a shorter document,"
so the summarisation step itself is **INFERRED** throughout, from the shape
and volume of the underlying record types. Flagged plainly per sector.

**Community services (best-evidenced sector).** Victorian and Queensland
child-protection practice manuals **CONFIRMED** a standard **case note**
structure: date, practitioner, heading, a summary section, a details section,
a case-plan-decision section, a safety/risk section
([cpmanual.vic.gov.au](https://www.cpmanual.vic.gov.au/our-approach/roles-and-responsibilities/case-recording),
[Queensland Case Notes practice guide](https://cspm.csyw.qld.gov.au/getattachment/35e64bcd-35c4-40ad-87d0-e2516aa1763e/PG-Case-notes.pdf)).
These are the atomic unit; a caseworker periodically has to turn a long run of
them into a **case review, court report, or funding acquittal** — that
synthesis step is **INFERRED**, not found stated explicitly anywhere in this
pass, though the practice-guide language ("a case note is a summary of a
single interaction... providing a contextual summary") strongly implies a
second, higher-level summarisation layer exists above the note level. NSW's
child-protection *Youth Justice Case Note Manual* (CONFIRMED to exist as a
formal, published manual — itself evidence the sector treats note-writing as
a disciplined, structured practice, not free text) supports the inference.

**Health.** Clinical incident systems (Queensland's RiskMan, NSW's `ims+`,
successor to IIMS — **CONFIRMED** to exist by name via Queensland Health and
NSW Clinical Excellence Commission guidance) generate incident reports that
feed **Root Cause Analysis (RCA)** — **CONFIRMED**, Victoria and NSW health
department guidance describes RCA as "a detailed and thorough investigation…
[producing] a series of recommendations." An RCA document — narrative account
of what happened, contributing factors, then a numbered recommendations tail
— is exactly the shape summarised for hospital quality/safety committees.
**INFERRED** that a shorter summary is then produced for that committee
audience; not found stated explicitly, but the existence of dedicated
governance committees reviewing multiple RCAs (Queensland Health guideline)
makes a rolled-up summary format highly likely.

**Legal.** Professional-body and insurer guidance (Legal Practitioners'
Liability Committee, Legal Aid NSW/Vic) **CONFIRMED** that **file notes /
attendance notes** are a disciplined, near-mandatory practice — real-time
records of every client meeting, phone call, and court attendance, later
relied on for matter continuity and negligence defence. **Not found**: any
source describing board papers or a distinct legal "summarisation" workflow
in Australian practice-management material — the operator's mention of board
papers as a legal-sector artefact is **not evidenced here** and should be
treated as INFERRED / plausible-but-unconfirmed, closer to a general
governance-document pattern than something specifically documented for legal
practices.

**Education.** Records-retention schedules **CONFIRMED** the existence and
extreme sensitivity of **student welfare / incident records** — Victorian
schools must keep sexual-abuse-related incident records for 99 years, general
student-safety records for 75 years ([P&C Qld retention schedule](https://pandcsqld.com.au/common/Uploaded%20files/PandCs%20Qld/Essential%20Resources%20Policies%20Procedures/PandCs%20Qld_Record%20Retention%20Schedule_02.2025.pdf),
Victorian education records-management guidance). That retention length is
itself strong indirect evidence these are treated as high-consequence
narrative records, structurally close to case notes/incident reports above.
**Not found**: any description of a summarisation workflow specific to
schools (e.g. into an NCCD submission or a wellbeing-team briefing) — this is
**INFERRED** from the existence and sensitivity of the underlying records,
not confirmed as a described workflow.

**Government.** No dedicated evidence gathered in this pass beyond general
knowledge of departmental practice (briefing notes, Cabinet submissions, FOI
release packages). **This entire sector's Q1 answer is INFERRED**, not
evidenced — flagged in "what could not be established" below rather than
padded with unsupported specifics.

**Honest summary of Q1.** The clearest, most consistently evidenced unit
across sectors is: **a structured, dated, name-and-role-dense narrative
record of an event or interaction (case note, incident report, attendance
note), periodically rolled up into a longer narrative-plus-recommendations
document (case review, RCA, court report) for a governance or oversight
audience.** That two-tier shape — atomic record, then periodic narrative
rollup — is the structural target the public proxies in the shortlist are
chosen to match.

---

## Q2 — Structural properties that matter

Framed against the project's three measured axes — **chunk count at 4096
tokens** (length proxy), **clause-marker density** (`unless`/`except`/
`provided that`/etc., per `docs/chunk-boundary-measurement.md`'s fixed
marker list), and **numeric density** (dates/figures) — plus the extra axes
the task asks for: narrative vs. clause-structured, named-entity density,
cross-reference density, and source format.

| Document type | Length (chunks @4096) | Narrative or clause-structured | Clause-marker density | Date/figure/name density | Cross-references other docs? | Format |
|---|---|---|---|---|---|---|
| **Case note (single entry)** | Sub-chunk — many entries needed to fill even one chunk | Narrative, but telegraphic/elliptical | Low | **Very high** — every entry is a date + named subject + role | Rarely | Digital export (EHR/case system), sometimes flattened to PDF |
| **Clinical incident report / RCA** | 1–3 chunks | **Hybrid** — narrative body, numbered-recommendation tail | Low–moderate (recommendations carry conditional/escalation language) | High | Moderate — cites internal policy/procedure numbers | PDF, born-digital |
| **File note / attendance note** | Sub-chunk to 1 chunk | Narrative, factual, terse | Low | High (dates, names, times, court/venue) | Low | Born-digital |
| **OAIC determination** | 2–8 chunks | **Hybrid** — narrative fact-finding, then numbered findings against statutory tests | **Moderate–high** — statutory-test language ("reasonable steps," exemptions, "unless") | High (org names, dates, APP citations) | Moderate — cites Privacy Act sections/APPs | Clean born-digital PDF |
| **Ombudsman investigation report** | 2–6 chunks | Same hybrid shape as OAIC, different regulator voice | Moderate | High | Moderate | Clean PDF |
| **Royal Commission report (per chapter)** | Many chunks per chapter; the whole document is hundreds of chunks | Narrative chapters + numbered recommendations appendix | Moderate | Very high (dates, named witnesses, org names, exhibit numbers) | **High** — cites exhibits, hearing transcripts, other chapters | Clean PDF, some multi-column front matter |
| **Court judgment** | 3–20+ chunks, highly variable | **Heavily clause-structured** — this is the outlier vs. everything else in this table | **Highest of any type here** | High (parties, dates, dollar figures) | **Very high** — case citations, statute pin-cites | Clean HTML/PDF (from the court itself) |
| **Coroners' findings** | 2–6 chunks | Narrative + short operative numbered-findings tail | Low–moderate | Very high (dates central to cause-of-death findings) | Low–moderate | PDF, format quality varies by state/era |
| **ISM (whole document)** | Very many chunks — this is the largest single document in the set | **Not narrative at all** — a flat/hierarchical numbered control catalogue | **Highest of any type here**, alongside judgments, but from a completely different source — conditional applicability by classification, "must"/"should" | Low-moderate (revision dates, no named individuals) | **Very high** — internal control-ID cross-references | Clean PDF, but table-heavy and multi-column in places |
| **Privacy Act / NIST SP** | Very many chunks | Clause-structured, statute/standard register | **Very high** | Low (abstract "the entity," "an agency" — few real names/dates) | **Very high** — defined terms, section/sub-section refs | Legislation.gov.au: clean HTML/EPUB/PDF. NIST: clean PDF, dense control tables |

**Reading this table against Q1's answer:** the operator's originally named
documents (Privacy Act, ISM, NIST) cluster in the bottom-right of this
table — high clause density, high cross-reference density, **low named-entity
and date density, and no narrative at all**. Real case notes, incident
reports and file notes sit at the opposite corner — narrative or hybrid, low
clause density, **very high** date/name density. **The proxies actually
close to that corner are OAIC determinations, Ombudsman reports, Royal
Commission chapters, and coroners' findings** — which is the direct
justification for promoting them in the shortlist above.

---

## Q3 — Assessment of each candidate

Already tabulated in the shortlist with a one-line justification per entry;
the fuller reasoning:

- **OAIC determinations (promote to #1).** On the Q2 table this is the single
  closest match to "internal incident investigation": a real personal-
  information or data-breach incident, a named respondent organisation, a
  chronological fact-finding narrative, and a numbered determination against
  statutory tests. It is also the most abundant clean source found (101
  since 2010, individually addressable, born-digital PDF, confirmed CC BY
  4.0). This is the strongest single recommendation in this document.
- **Royal Commission reports (promote, use selectively).** Structurally
  right — narrative findings and recommendations, exactly the sector
  (aged care, disability, welfare administration) — but the *whole* report
  is hundreds to 900+ pages (Robodebt: **REPORTED** "over 900 pages, 3
  volumes" per secondary source, not independently paginated here). Use
  individual chapters or volumes as separate corpus documents, not the whole
  report as one item, or it will dominate every length-based statistic in
  the corpus.
- **Court judgments (keep, but source directly from courts, not AustLII).**
  Best available density of clause markers and cross-references — useful
  precisely because it is the extreme case, a genuine stress test for the
  clause-density metric. Obtaining it cleanly requires going to
  `fedcourt.gov.au` (or state equivalents) rather than scraping AustLII —
  see Q6.
- **Ombudsman/regulator determinations (promote, secondary to OAIC).** Same
  structural shape as OAIC, licence-clean (CC BY 4.0, CONFIRMED), useful for
  widening the sample of "regulator investigates and writes findings"
  documents beyond one regulator's house style.
- **Coroners' findings (keep, with a licensing caveat).** Best health-
  adjacent narrative-investigation proxy found — genuinely different
  register from data-handling regulators (life-and-death fact-finding,
  operative recommendations). Licence position is **not uniform across
  states** (Q6) — check the specific state's copyright page before bulk use,
  and prefer hand-picked findings over any scraping.
- **Hansard and parliamentary committee reports (do not use, or use only
  singly with legal review).** Structurally interesting (debate transcript
  register, procedurally dense) but low priority for this corpus's actual
  purpose, and the licence is the most restrictive found in this whole
  assessment (Q6) — demoted below every other candidate on licensing grounds
  alone, independent of structural fit.
- **Departmental clinical guidelines / policy manuals.** Plausible structural
  match to the ISM's own register (numbered, conditional, cross-referenced)
  — but **no specific manual was checked for licence terms in this pass**;
  treat as a category to investigate later, not a confirmed pick. Flagged in
  "what could not be established."
- **The operator's picks — Privacy Act, ISM, NIST — assessed honestly.**
  Structurally, these are the **worst-matched** items on the Q2 table to
  what Q1 found offices actually summarise: no narrative, sparse named
  entities and dates, and (per `docs/chunk-boundary-measurement.md`'s own
  finding) exactly the *rare* long-real-legal-document type that project's
  existing corpus doesn't yet have enough of to test clause-boundary
  severing on. They are not wasted picks — see Q4 and Q5 for what they are
  actually good for — but they should not be read as "representative of the
  target workload" documents, and the corpus should not lean on them for
  that role.

---

## Q4 — Does NIST make sense for an Australian market?

**Argued honestly, and the answer is the "keep it, for a different reason"
shape the task anticipated.**

The connection to Australian practice is **real but indirect, and should not
be overstated.** A single secondary source (a Medium post, **REPORTED**,
single-author, not independently corroborated) claims the ISM's own roadmap
explicitly aligns to "NIST 800-53 Rev 2." That specific claim is not strong
enough to hang an argument on. The more solid, **CONFIRMED** connection is
one level removed: NIST itself publishes an official crosswalk from SP
800-53 Rev. 5 to **ISO/IEC 27001**
([csrc.nist.gov](https://csrc.nist.rip/CSRC/media/Publications/sp/800-53/rev-5/final/documents/sp800-53r5-to-iso-27001-mapping.docx)),
and ISO 27001 is a standard the ISM and IRAP assessment process both operate
alongside in Australian practice (general knowledge, not independently
re-verified in this pass). So NIST reaches Australian practice **through
ISO 27001 as an intermediate**, not as a document Australian offices consult
directly day-to-day.

**What NIST is actually good for in this corpus, and it is a real, separate
justification:**

1. **Extraction stress test.** SP 800-53 Rev. 5 is **CONFIRMED** 492 pages
   (nist.gov publication metadata), dense with control-ID tables and
   cross-references — exactly the "large PDFs, dense tables, multi-column
   layout" case the task suggested checking for. This project's own
   `docs/FINDINGS.md` PDF-handling incident shows this class of extraction
   failure is a live risk, not a theoretical one.
2. **A structurally different revision-diff pair from the ISM's.** Rev. 4 →
   Rev. 5 was a **major reorganisation** of the control catalogue (general
   industry knowledge; not independently diffed line-by-line in this pass),
   not a small incremental edit. Pairing NIST's big-structural-change
   revision case against the ISM's small-incremental-change revision case
   (quarterly editions) gives the faithfulness-checker experiment (Q5) two
   different *kinds* of "real difference" to be tested against, not just two
   instances of the same kind.
3. **What it is not:** a proxy for anything an Australian health, legal,
   education, government or community-services office would itself
   summarise as work product. Do not justify its inclusion on Q1/Q2 grounds
   — it fails that test plainly, per the Q3 table.

---

## Q5 — Multiple revisions: what experiment does this actually enable?

**The experiment, stated precisely.** `docs/market-research.md` found **no
mainstream tool handles disagreeing sources at all** (§2, §6 — every citation
mechanism surveyed proves *reference*, not *faithfulness*, and none was found
to have an explicit mechanism for two source passages that say different
things). Feeding a faithfulness/audit pipeline **two revisions of the same
instrument, differing in a small, real, dated way**, and asking it to
summarise or answer a question against both, creates exactly the test case
that distinguishes two failure modes a single-document faithfulness checker
cannot even pose the question about:

- **A genuine difference between sources** — revision B really did change
  the retention period, or add an exemption, or shift a control's
  applicability — and a faithful summary of "what changed" or "what does the
  current version say" must reflect that, sourced to the correct revision.
- **A fabrication** — the model asserts a difference (or a similarity) that
  isn't actually present in either revision's text, which `docs/market-
  research.md`'s finding says today's citation-based UIs cannot catch
  because a citation number proves *reference*, not *correctness of the
  claim attached to it*.

**Two clean sources for this, with different diff character (per Q4):**

1. **Privacy Act 1988 point-in-time compilations** — `legislation.gov.au`
   publishes dated, versioned compilations of the same Act at a stable,
   predictable URL pattern
   (`/C2004A03712/{date}/{date}/text/original/{pdf|epub}` — **CONFIRMED**,
   URLs for the 2023-10-18 and 2026-06-04 compilations both found live in
   this pass). Differences between adjacent compilations are small, targeted
   amendments — exactly the "near-identical, differing in small, real ways"
   shape the operator asked for, and the cleanest possible text (born-digital
   HTML/EPUB, no OCR risk) to run the diff against.
2. **ISM quarterly editions** — a full PDF is republished roughly every
   quarter (**CONFIRMED**: March, June, September and December 2024 editions
   all independently found live at cyber.gov.au), each carrying the same
   numbered-control structure with incremental changes to individual
   controls between editions.

**What would have to be built to actually run this experiment — not
speculative, this is scoped from the project's own existing machinery**
(`docs/market-research.md`'s description of `missing_link/audit.py` and
`db.chunk_summaries`):

- A **document-pair or document-set input**, not the single-document input
  the pipeline currently assumes — the map/reduce prompts and chunker take
  one document today.
- A **prompt variant that asks explicitly about difference** ("what changed
  between revision A and revision B," or "does the current version still
  require X"), because the existing `summarise`/`report`/`qa` prompts
  (`docs/REQUIREMENTS.md`) have no multi-source-comparison framing at all.
- An **audit check that can attribute a claim to a specific revision**, not
  just a chunk — `chunk_summaries`' `start_char`/`end_char` offsets
  (`docs/market-research.md`) are per-document today; a cross-revision claim
  needs the offset plus which revision's text it came from.
- A **small, hand-built ground-truth set of real, dated differences** between
  chosen revision pairs (e.g., "clause X was added in the 2024 amendment"),
  so a checker's output can be scored against a known-correct answer rather
  than only inspected qualitatively.

None of this exists yet. This is a genuinely new capability, not a
relabelling of something already built — flagged plainly rather than implied
to be a small addition.

---

## Q6 — Licensing and reuse, checked per source

This gates everything, so treated as its own pass rather than folded into
the shortlist table's one-line summaries.

| Source | Copyright holder | Licence | Bulk download / redistribution | Flag |
|---|---|---|---|---|
| **OAIC determinations** | Commonwealth | **CONFIRMED CC BY 4.0 International** (oaic.gov.au/about-the-OAIC/copyright) | Yes — individually addressable PDFs, no `robots.txt`/ToU restriction found | None |
| **Commonwealth Ombudsman reports** | Commonwealth | **CONFIRMED CC BY 4.0** (ombudsman.gov.au) | Yes | None |
| **Privacy Act 1988 (legislation.gov.au)** | Commonwealth | **CONFIRMED CC BY 4.0** under the Commonwealth's "free use of legislation" policy (ag.gov.au/rights-and-protections/copyright/government-agencies) | Yes, per dated compilation | Separately, s.183 of the Copyright Act gives government a statutory use licence even absent CC — belt-and-braces here |
| **ISM (cyber.gov.au)** | Commonwealth (ASD) | **CONFIRMED CC BY 4.0 International** (cyber.gov.au/acsc/copyright) | Yes | None |
| **Royal Commission reports (Aged Care / Disability / Robodebt)** | Commonwealth | **CONFIRMED CC BY 4.0 International**, checked on each Commission's own `/copyright` page | Yes | None |
| **NIST SP 800-53 (Rev. 4 and 5)** | US Government (public domain) | **CONFIRMED public domain**, 17 U.S.C. §105 (nist.gov/copyrights-disclaimers); NIST notes foreign-jurisdiction copyright may theoretically apply but grants an irrevocable royalty-free right to reproduce worldwide | Yes | Cleanest licence position of anything on this list, ironically the one item furthest from the target sector |
| **Federal Court judgments (fedcourt.gov.au)** | Commonwealth for court-produced material; judgment authorship sits with the Judges | Court **"progressively" applying CC BY 3.0 AU**; even pre-rollout, unaltered reproduction with attribution is **CONFIRMED** permitted (fedcourt.gov.au/copyright) | Yes, per judgment, direct from the court | Fetch from the court, not AustLII (next row) |
| **AustLII (as a bulk source)** | AustLII claims copyright in its own added markup/citation layer; underlying judgments/legislation remain government/Crown material | AustLII's own **Usage Policy explicitly excludes "AI-related... or other automated systems"** from the reuse permission it grants, and states it will not act as a "data repository or re-supplier... via spidering, scraping, crawling" — **REPORTED**, from a WebSearch-summarised fetch of `austlii.edu.au/austlii/copyright.html` (the page itself returned HTTP 403 to direct WebFetch both from the primary and `classic.` mirror in this pass, so the exact wording could not be independently re-verified by this agent — treat the summary as REPORTED, not CONFIRMED, and re-check before relying on it) | **`robots.txt` is permissive** (only nine named AI/bot user-agents are disallowed; the generic `User-agent: *` rule is `Allow: /`, **CONFIRMED** by direct fetch) — **but the Usage Policy is a separate, stricter document from the robots.txt, and the two disagree.** `robots.txt` alone is not sufficient authorisation here. | **Do not bulk-fetch from AustLII for this project.** Get individual judgments from the originating court instead. |
| **Coroners' findings** | State Crown copyright, varies | **Mixed.** Victoria: "encourages... dissemination and re-use," own copyright page found, but the exact licence text (CC vs. a bespoke permission) was **not confirmed** in this pass — REPORTED from a WebSearch summary, not independently fetched. Other states not checked at all in this pass. | **Check per state before bulk use.** Some coronial findings are also de-identified only selectively, and findings can name a deceased person and their family — a sensitivity distinct from copyright that this project's own faithfulness/privacy posture should weigh even where reuse is technically permitted. | Hand-pick, don't scrape, until each state's terms are individually confirmed |
| **Hansard (Parliament of Australia)** | Parliament of Australia (parliamentary privilege applies to the underlying proceedings, separate from copyright) | **REPORTED CC BY-NC-ND 3.0 Australia** — Attribution-NonCommercial-**NoDerivs** | **NoDerivs is a real problem for this project specifically**, not a formality: chunking a document and feeding it through a summarisation pipeline that emits a transformed output is difficult to characterise as anything other than a derivative work. This is the **most legally uncertain item assessed in this document** for this project's specific use case. | **Recommend not using Hansard for this corpus.** If used at all, treat as a single hand-reviewed test case, not a bulk source, and get a clearer answer on whether internal, non-published test-corpus use even implicates the NoDerivs term before doing anything with the output. |
| **Departmental clinical guidelines / policy manuals** | Varies by department/state | **Not checked** — no specific manual was fetched or assessed for licence terms in this pass | Unknown | Treat as unconfirmed; check per document before use |

**The one structural point that applies across the whole table, worth
stating plainly for the record even though `CLAUDE.md` already implies it:**
this project's own repository is public even though the corpus itself is
stored outside it (task framing, and consistent with `docs/REQUIREMENTS.md`'s
general posture on what is and isn't committed). Everything recommended
above as CC BY or public domain can be freely referenced, quoted, or
re-published from this public repository if that ever becomes useful (e.g.
citing a specific passage in a written finding). **Hansard and any
coroners' findings whose licence isn't independently confirmed should be
treated as corpus-only, never quoted or excerpted into anything this
repository publishes**, until their terms are individually re-checked.

---

## What I could not establish

- **Any concrete, sector-specific evidence for Q1 in the government sector.**
  Nothing beyond general/background knowledge was found describing what an
  Australian government department (federal or state) actually summarises
  day to day (briefing notes, Cabinet submissions, FOI packages) — flagged
  as entirely INFERRED, not evidenced, in Q1.
- **Whether Australian legal practices produce anything resembling "board
  papers"** as a distinct, described workflow artefact. Not found in this
  pass; the operator's mention of this may be accurate but is unconfirmed
  here.
- **The exact wording of AustLII's Usage Policy.** The page
  (`austlii.edu.au/austlii/copyright.html` and its `classic.` mirror)
  returned HTTP 403 to direct fetch twice in this pass; the summary used
  above comes from a WebSearch synthesis of the page, not a directly
  re-verified quote. **Re-fetch and quote directly before treating the
  "no AI/automated use" restriction as settled policy** — it is plausible
  and consistent with AustLII's known public stance, but this specific pass
  could not independently confirm the exact wording.
- **Per-state licence terms for coroners' findings beyond Victoria.** Only
  Victoria's copyright page was found and even that was via a WebSearch
  summary, not a direct fetch. NSW, Queensland, WA and others were not
  checked at all.
- **Whether Microsoft/court "progressive CC licensing" has actually reached
  the specific judgment pages this project would want to fetch**, versus
  being a stated intent not yet fully rolled out across the Federal Court's
  site. The copyright page confirms the *policy direction* and the
  *unaltered-reproduction-with-attribution* fallback right, not that every
  judgment page currently carries a CC badge.
- **Any real-world confirmation that an Australian office in the target
  sectors has ever synthesised case notes/incident reports into a rollup
  document** (case review, RCA committee summary, funding acquittal) in the
  specific way this document infers. The retention-schedule and
  practice-manual evidence is suggestive, not a direct description of that
  synthesis step.
- **Licence terms for any specific departmental clinical guideline or policy
  manual.** Not checked at all — flagged as a category worth investigating
  later, not assessed here.
- **A page-by-page or line-by-line diff of NIST SP 800-53 Rev. 4 vs Rev. 5**,
  or of adjacent ISM quarterly editions. The "major reorganisation" claim for
  NIST and the "small incremental changes" claim for the ISM are both
  **INFERRED** from general knowledge of each publication's revision history,
  not independently verified by diffing the actual PDFs in this pass — do
  this before relying on the Q5 experiment design's premise that these two
  give qualitatively different diff sizes.
