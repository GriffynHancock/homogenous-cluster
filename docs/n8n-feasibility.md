# n8n on this cluster: a feasibility survey

Commissioned to answer the instructor's question — **can n8n be added?** — now
that the cluster is also becoming a teaching resource for a cybersecurity
course ("how can AI help and harm in a cybersecurity context?"), with ~20
students reaching it over the LAN, alongside the existing overnight
document-summarisation workload.

**RESEARCH ONLY.** Nothing was installed and nothing was run on the cluster
beyond read-only inspection of what is already there (`ss -ltn`,
`apt-cache policy`, reading `/etc/default/*`, `GET /openapi.json` on the
already-running Missing Link, and reading the on-disk llama.cpp README of the
pinned build). No package was fetched onto a node, no service was touched.

**Method note on evidence quality.** Every claim is labelled **CONFIRMED**
(checked against a primary source — the actual licence file, n8n's own source
code, n8n's own documentation, or something read directly off this machine),
**REPORTED** (someone else said it and it was not verified here), or
**INFERRED** (reasoning from other labelled facts). No web-sourced performance
number is presented as if it were measured on this hardware. The only numbers
quoted as measured come from `docs/measurements.md`, per the standing rule in
`CLAUDE.md`.

Licence checking is established practice here, not pedantry: a corpus source
was already killed by a licence conflict (Hansard's CC BY-NC-ND, see
`docs/corpus-selection.md`).

---

## Direct answers

1. **Licence: permitted, with one grey edge worth an email.** An educational
   institution running n8n on its own internal hardware for its own students is
   "internal business purposes" under the Sustainable Use License, and n8n's own
   FAQ says "all use is allowed unless you are **selling a product, service, or
   module in which the value derives entirely or substantially from n8n
   functionality**". A cybersecurity course's value does not derive substantially
   from n8n. **The grey edge:** the same FAQ describes the prohibited shape as
   "making n8n available to your customers for them to connect their accounts and
   build workflows", and a fee-paying student is arguably a customer doing exactly
   that. Verdict: permitted, but the institution should send one email to
   `license@n8n.io` and keep the reply. §1.
2. **Multi-user: real accounts, unlimited, weak isolation, and it is not a
   security boundary.** Community edition supports unlimited users
   (CONFIRMED from source), and a Member's workflows and credentials are visible
   only to that Member and to the instance Owner. But **Projects, sharing and RBAC
   are all Enterprise**, there is no Admin role below Owner, and — decisively —
   n8n's own docs state that without external-mode task runners "anyone who can
   edit a workflow could potentially read your database, encryption key, stored
   credentials, and environment variables." One shared instance means one blast
   radius. §3.
3. **Local-LLM integration: yes, first-class, no custom code.** The built-in
   **OpenAI** credential has a **Base URL** field (CONFIRMED in n8n's source), and
   the OpenAI Chat Model node explicitly branches its model-list behaviour on
   whether the base URL is `api.openai.com` — i.e. non-OpenAI compatible endpoints
   are a supported, deliberate path. Point it at `http://<node>:8080/v1`, put any
   dummy string in the API key, and the AI Agent / Basic LLM Chain nodes work.
   Two concrete gotchas in §4 (the Responses API default, and the model name being
   a filesystem path).
4. **Deployment: Docker, on node 3, not on an inference node.** §6.

---

## 1. Licence

### 1.1 What the licence actually is

**CONFIRMED.** n8n's `LICENSE.md` on `master` (fetched 2026-08-23,
<https://raw.githubusercontent.com/n8n-io/n8n/master/LICENSE.md>) opens:

> Portions of this software are licensed as follows:
>
> - Content of branches other than the main branch (i.e. "master") are not licensed.
> - Source code files that contain ".ee." in their filename or ".ee" in their dirname are NOT licensed under the Sustainable Use License. To use source code files that contain ".ee." in their filename or ".ee" in their dirname you must hold a valid n8n Enterprise License specifically allowing you access to such source code files and as defined in "LICENSE_EE.md".
> - All third party components incorporated into the n8n Software are licensed under the original license provided by the owner of the applicable component.
> - Content outside of the above mentioned files or restrictions is available under the "Sustainable Use License" as defined below.

The operative grant and the operative limitation, quoted in full:

> ### Copyright License
>
> The licensor grants you a non-exclusive, royalty-free, worldwide, non-sublicensable, non-transferable license to use, copy, distribute, make available, and prepare derivative works of the software, in each case subject to the limitations below.
>
> ### Limitations
>
> You may use or modify the software only for your own internal business purposes or for non-commercial or personal use. You may distribute the software or provide it to others only if you do so free of charge for non-commercial purposes. You may not alter, remove, or obscure any licensing, copyright, or other notices of the licensor in the software. Any use of the licensor's trademarks is subject to applicable law.

The npm package metadata agrees that this is not a standard licence:
`"license": "SEE LICENSE IN LICENSE.md"` (**CONFIRMED**, npm registry
`https://registry.npmjs.org/n8n`, latest `2.35.7` published 2026-08-21).

n8n states plainly that it is **not open source**: "according to the Open
Source Initiative (OSI), open source licenses can't include limitations on use,
so we do not call ourselves open source" (**CONFIRMED**,
<https://docs.n8n.io/privacy-and-security/sustainable-use-license.md>).

### 1.2 Does an educational institution running it for students qualify?

The licence text itself gives two independent routes to "yes":

- **"your own internal business purposes"** — the institution runs it on its own
  hardware for its own teaching. Nothing is offered to third parties.
- **"or for non-commercial or personal use"** — the disjunction matters. Even if
  a course is not "business", non-commercial use is separately permitted.

n8n's own FAQ then narrows the restriction much further (**CONFIRMED**, same
docs page, verbatim):

> Our license restricts use to "internal business purposes". In practice this means all use is allowed unless you are **selling a product, service, or module in which the value derives entirely or substantially from n8n functionality**. Here are some examples that wouldn't be allowed:
>
> * White-labeling n8n and offering it to your customers for money.
> * Hosting n8n and charging people money to access it.
>
> All of the following examples are allowed under our license:
>
> * Using n8n to sync the data you control as a company, for example from a CRM to an internal database.
> * Creating an n8n node for your product or any other integration between your product and n8n.
> * Providing consulting services related to n8n, for example building workflows, custom features closely connect to n8n, or code that gets executed by n8n.
> * Supporting n8n, for example by setting it up or maintaining it on an internal company server.

(Emphasis added; the bullets are n8n's.)

**The verdict (INFERRED from the CONFIRMED text above): permitted.** A
cybersecurity course is not a product whose value "derives entirely or
substantially from n8n functionality" — n8n is one lab tool among many, and the
value is the teaching. The last allowed bullet, "setting it up or maintaining it
on an internal company server", is close to a direct description of what is
proposed. And the third allowed bullet explicitly permits *charging money* for
"building workflows … or code that gets executed by n8n", which is the closest
commercial analogue to teaching people to do the same.

### 1.3 The grey edge, stated honestly

One FAQ answer is phrased in a way that a careful reader should not skate past
(**CONFIRMED**, same page):

> **My company has a policy against using code that restricts commercial use – can I still use n8n?**
>
> Provided you are using n8n for internal business purposes, and **not making n8n available to your customers for them to connect their accounts and build workflows**, you should be able to use n8n.

(Emphasis added.) A classroom *is* "making n8n available to \[people\] for them
to connect their accounts and build workflows". Whether a fee-paying student is
a "customer" is the whole question. Two things cut in favour of the
institution: students are enrolled internal users of the institution rather than
purchasers of an n8n service, and the operative test the FAQ gives twice is
whether the *value of what you sell derives substantially from n8n*, which for a
cybersecurity course it does not.

There is a second, unrelated FAQ item worth knowing because it shapes what
students should be told to *do* in the tool (**CONFIRMED**, same page): using n8n
as a backend that "collects the user's own \[third-party\] credentials" is called
out as **NOT ALLOWED**, whereas a workflow using the *organisation's* credentials
is **ALLOWED**. This is written about embedding n8n in a product, not about
internal users, so it does not directly bind a classroom — but a course exercise
that has students harvest other people's third-party account credentials through
n8n is the pattern n8n names, and is best avoided on those grounds as well as
the obvious ones.

**Recommendation:** the licence question is answerable "yes" from the text, but
the cost of certainty is one email. n8n publishes `license@n8n.io` for exactly
this and invites the question three separate times on that page ("if you're
still unclear, email us"). Send it, describe the deployment in one paragraph
(institution-owned hardware, enrolled students, no fee for n8n access, no
third-party hosting), and **keep the reply with the project docs**. That
converts an INFERRED verdict into a written permission, which is what a
compliance conversation will actually want.

**One thing that is unambiguous and must be preserved:** "You may not alter,
remove, or obscure any licensing, copyright, or other notices of the licensor in
the software." So: no rebranding the UI, no stripping the n8n name from a
directory page, no "our automation platform" framing to students.

---

## 2. Resource profile

### 2.1 What n8n is, computationally

n8n is a Node.js application: one main process, an embedded SQLite database by
default, and a browser-based editor. Workflow steps are mostly HTTP calls and
data reshaping — **I/O-bound glue**, which is genuinely a better fit for
CPU-only hardware than, say, image generation.

**But "I/O-bound" is a property of the workloads n8n usually runs, not a
property n8n enforces.** The Code node executes arbitrary JavaScript (and, in
2.x, Python) supplied by whoever can edit a workflow. A student's `while(true)`
loop, or an accidental 100k-iteration Loop Over Items, is CPU-bound and n8n will
happily run it. **INFERRED, and it is the single most important resource fact
in this document:** on a shared 4-core node the "it's only glue" argument does
not survive contact with untrusted authors.

### 2.2 Numbers

**Nothing below was measured on this hardware.** These are REPORTED figures and
should be treated as order-of-magnitude only.

| Figure | Value | Label |
|---|---|---|
| Documented minimum | 2 vCPU / 2 GB RAM / 20 GB SSD | **REPORTED** — third-party guides, not n8n's own docs; n8n does not publish a hardware minimum on the pages surveyed |
| Idle footprint | ~100 MB RSS | **REPORTED** — third-party |
| "Production starts working" | 4 GB RAM / 2 vCPU; heavier setups 8 GB / 4 cores | **REPORTED** — third-party |
| n8n's own published benchmark | "up to 220 workflow executions per second on a single instance"; example single-instance test on a 4 GB AWS `c5a.large` with Postgres | **CONFIRMED as a vendor claim** — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/measure-performance.md>. It measures a two-node webhook workflow, i.e. the cheapest workflow that exists, and says nothing about a workflow that calls an LLM |
| npm package unpacked size | 31.1 MB for the `n8n` package alone, **159 direct dependencies** | **CONFIRMED** — npm registry metadata for 2.35.7 |

**INFERRED sizing for this case:** the n8n process itself is not the constraint
on a 128 GB node — even a pessimistic 2 GB is 1.6% of RAM. The constraints are
(a) CPU contention on 4 physical cores, and (b) the LLM behind it.

### 2.3 What ~20 students actually costs

The binding resource is **not n8n**, it is `llama-server`. From
`docs/measurements.md` (the only place performance numbers may be quoted from),
measured on node 1 with mainline llama.cpp b10369, gpt-oss-120b F16, `-t 4
-c 32768 --parallel 4`, read from the server's own `slot print_timing` lines:

- prefill **~16.3 tok/s** (flat across the run: 16.29 / 16.26 / 16.27 / 16.36)
- generation **~5.26 tok/s**
- **end-to-end wall clock 77–99 s** for requests of ~1,060–1,410 prompt tokens

**INFERRED consequence:** a student pressing "Test workflow" on an AI Agent node
with a short prompt waits on the order of a minute, and there are **4 slots**
(`--parallel 4`, and F24 says never 8). Twenty students in a lab session
clicking at once is a queue tens of minutes deep, not a lab exercise. Two
mitigations, neither free:

- **Give the class its own endpoint.** With node 3 arriving, R=3 replication
  means one `llama-server` could be dedicated to the class and the other(s) left
  for document work. This is the clean answer and it is already the project's
  scaling model (`CLAUDE.md`, "Replication buys speed, linearly").
- **Teach with a small model.** A 1–4 B model on the class endpoint would give
  interactive latency, and for "how can AI help and harm" demonstrations —
  prompt injection, data exfiltration through a tool call, over-trusting model
  output — a small model is arguably *better*, because it fails visibly.
  **INFERRED**, and worth an experiment before the first class.

### 2.4 Postgres or SQLite?

**CONFIRMED** (<https://docs.n8n.io/deploy/host-n8n/configure-n8n/choose-n8ns-database.md>):
SQLite at `~/.n8n/database.sqlite` is the default; Postgres is supported via
`DB_TYPE=postgresdb`; supported Postgres majors are 16/17/18 as of July 2026.
Notably, **n8n Cloud itself runs SQLite on Starter, Pro and legacy Enterprise
plans** — Postgres only on "Enterprise Scaling". That is a strong vendor signal
that SQLite is not a toy configuration.

**Recommendation: start on SQLite.** INFERRED from the above plus this
project's own experience: SQLite locking was one of the defects that got past
Missing Link's test suite (`CLAUDE.md`, verification section), so it is a known
sharp edge — but a single-process n8n with 20 light users is the case SQLite
handles, and adding Postgres adds a second service to back up, a second thing to
break, and a second thing a student can wedge. Move to Postgres only if
execution-history volume or a queue-mode migration forces it. The migration path
exists and is documented: `n8n export:entities` / `n8n import:entities` moves
between database types (**CONFIRMED**,
<https://docs.n8n.io/deploy/host-n8n/configure-n8n/use-the-command-line.md>).

### 2.5 Queue mode?

**CONFIRMED** (<https://docs.n8n.io/deploy/host-n8n/community-edition-features.md>):
**queue mode IS included in Community edition**; only *multi-main* mode is
Enterprise. Queue mode needs Redis plus worker processes.

**Recommendation: single process (regular mode). INFERRED.** Queue mode exists
to scale webhook throughput; this class's bottleneck is a 5 tok/s LLM, not n8n's
event loop. Adding Redis and workers buys nothing here and costs another service
and another set of processes that must share the same `N8N_ENCRYPTION_KEY`
(**CONFIRMED**, <https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/configuration-examples/set-a-custom-encryption-key.md>).

**But do set a concurrency limit anyway**, with one caveat that matters here.
**CONFIRMED** (<https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/control-concurrency.md>):

> In regular mode, n8n doesn't limit how many production executions may run at the same time.

`N8N_CONCURRENCY_PRODUCTION_LIMIT=n` fixes that — **but the same page states it
"applies only to production executions: those started from a webhook or trigger
node. It doesn't apply to any other kinds, such as manual executions"**. A
classroom is almost entirely manual executions (students clicking "Test
workflow"), so **the one concurrency control n8n gives you does not cover the
one thing students will do.** INFERRED consequence: the real backpressure has to
come from the LLM endpoint, not from n8n.

### 2.6 Where should n8n live? — and the F44 question

`docs/FINDINGS.md` **F44 (CONFIRMED)**: even at `nice -n 10`/`-n 15`, a
CPU-bound sidecar measurably starves `llama-server` on a 4-core node —
`llama-server` at 378.9% CPU alongside a niced process at 336.8%, load average
8.23 against 0.6–0.9 idle, and the sidecar's own rate degrading from 4.7 to
41.7 s/claim in a later pass.

The brief asked whether n8n's I/O-bound nature exempts it. **It does not, and
the reason is not n8n's baseline — it is the Code node.** An idle-to-light n8n
main process would be a defensible co-tenant. An n8n instance where twenty
students can submit arbitrary JavaScript is a CPU-bound sidecar *on demand*, and
F44 says what that does to an inference node. `nice` is not a fix; F44 measured
`nice` failing.

**Recommendation (INFERRED):**

- **Put n8n on node 3** when it arrives, and do **not** run `llama-server` on
  node 3 while the class is using it — or, if node 3 must also serve inference,
  accept that a student can degrade it and keep the document workload on nodes
  1–2.
- **Second-best:** the out-of-band agent appliance (the 8 GB laptop in
  `CLAUDE.md`'s "Operate" section) is a legitimate host for n8n — it is already
  separate hardware by design, and n8n's footprint fits 8 GB. The trade is that
  the appliance's job is liveness monitoring, and `CLAUDE.md` is explicit that
  "triage and batching may live on-node; LIVENESS may not" — putting a
  student-controlled CPU consumer on the monitor inverts that argument. **Do not
  do this.** Noted only so the option is visibly rejected rather than
  overlooked.
- **Do not co-locate n8n with `llama-server` on nodes 1 or 2.**

---

## 3. Multi-user and what a student can break

### 3.1 There are real user accounts, and there is no user limit

**CONFIRMED from source**, which matters because the community forum contains
the opposite claim. In `packages/cli/src/license.ts`:

```ts
getUsersLimit() {
    return this.getValue(LICENSE_QUOTAS.USERS_LIMIT) ?? UNLIMITED_LICENSE_QUOTA;
}
...
isWithinUsersLimit() {
    return this.getUsersLimit() === UNLIMITED_LICENSE_QUOTA;
}
```

with `UNLIMITED_LICENSE_QUOTA = -1` in `@n8n/constants`. With no licence key
there is no `quota:users` value, so the quota resolves to unlimited.
(<https://raw.githubusercontent.com/n8n-io/n8n/master/packages/cli/src/license.ts>,
<https://raw.githubusercontent.com/n8n-io/n8n/master/packages/%40n8n/constants/src/index.ts>)

A REPORTED claim to the contrary circulates ("The n8n Community plan does not
allow adding collaborators on self-hosted instances") — treat it as **wrong or
stale**; it is contradicted by both the source above and n8n's own edition
comparison.

SMTP is optional: "You can choose to manually copy and send invite links instead
of setting up SMTP. Note that if you skip this step, users can't reset
passwords" (**CONFIRMED**,
<https://docs.n8n.io/deploy/host-n8n/configure-n8n/user-management.md>).
**INFERRED:** for a class, hand-distributing invite links is fine, but budget
for the instructor doing password resets by hand — or stand up a local SMTP
relay.

Also **CONFIRMED** on that page: `N8N_USER_MANAGEMENT_DISABLED` and basic
auth were removed in n8n 1.0, and "No supported way to disable the login screen
exists in recent versions of n8n". There is no anonymous-access mode to fall
back to.

### 3.2 What Community edition does NOT give you

**CONFIRMED**, <https://docs.n8n.io/deploy/host-n8n/community-edition-features.md>.
The Community edition lacks, among others:

- **Projects** (the unit of RBAC)
- **Sharing** of workflows and credentials — and the page's own parenthetical is
  the isolation story in one line: *"(Only the instance owner and the user who
  creates them can access workflows and credentials)"*
- **SSO (SAML, LDAP)**
- Environments / Git version control, external secrets, log streaming,
  external binary storage, multi-main

**CONFIRMED**, <https://docs.n8n.io/administer/manage-users-and-access/understand-instance-roles.md>:
the three instance roles are Owner, Admin and Member, and **the Admin role is
self-hosted Enterprise only**. So on Community there are exactly two effective
tiers: **Owner (sees and edits everything) and Member (sees only their own).**
All the project-level roles (Project Admin/Editor/Viewer) are Enterprise
(<https://docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/see-available-roles.md>).

**INFERRED, and it is better news than expected:** Owner + N Members is
*structurally* the right shape for a classroom — instructor as Owner, each
student a Member with a private workspace. Students do not see each other's
workflows in the UI, and cannot delete them. This answers the brief's "one
shared workspace where any student can edit or delete another's workflows"
worry: **no, not through the UI.** But see 3.4 — the UI is not the boundary.

### 3.3 What a student can break for everyone, even staying inside the UI

All **CONFIRMED** from n8n's own docs unless marked:

- **Webhook path collisions.** "Webhook paths must be unique across the entire
  instance… If two users set the same path value: The path works for the first
  workflow that's run or published. Other workflows will error"
  (<https://docs.n8n.io/administer/manage-users-and-access/follow-best-practices.md>).
  Twenty students following the same tutorial will all pick `/webhook/test`.
- **Unbounded concurrent executions** (§2.5) — no default limit, and the limit
  that exists does not cover manual runs.
- **Memory.** "n8n doesn't restrict the amount of data each node can fetch and
  process… Allocation failed - JavaScript heap out of memory"
  (<https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/fix-memory-issues.md>).
  One process, so one student's runaway node takes the class down. The same page
  notes Docker restarts automatically on OOM while npm installs may need a manual
  restart — a point in Docker's favour for a teaching instance.
- **Tags.** Members can create tags but not delete them; tags and Variables are
  explicitly global and *not* subject to RBAC
  (<https://docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/see-available-roles.md>).
  Cosmetic, but it is shared mutable state.
- **The LLM endpoint.** **INFERRED:** nothing in n8n rate-limits outbound calls,
  so a student's Loop Over Items across 500 items pointed at
  `http://node:8080/v1` will saturate all four slots for as long as it takes.
- **Missing Link.** **CONFIRMED** by reading `GET /openapi.json` on the running
  instance: Missing Link exposes `POST /jobs`, `POST /batch`, `POST /corpus`,
  `POST /corpus/{doc_id}/delete`, `POST /jobs/{job_id}/cancel`,
  `POST /jobs/reorder` and `GET /jobs/{job_id}/result` **with no authentication
  scheme in the schema**. Any student who can reach n8n can reach Missing Link,
  and can therefore cancel, reorder or delete the real document work. See §4.2.

### 3.4 The Code node — and why the UI separation is not a security boundary

The brief asked for the "well-known set of considerations" about the Code node
on a shared instance. n8n states it themselves, and it is blunter than the
folklore. **CONFIRMED**,
<https://docs.n8n.io/deploy/host-n8n/configure-n8n/set-up-task-runners.md>:

> **Always use task runners in production**
>
> Task runners are the only isolation layer between user-provided code and n8n. Without them, or with internal mode, **anyone who can edit a workflow could potentially read your database, encryption key, stored credentials, and environment variables.**
>
> In production, and on any instance holding sensitive data, use external mode plus the measures in Hardening task runners. Skipping external mode to save hosting costs is only a reasonable tradeoff on isolated instances that hold nothing but trusted or mock data.

And on internal mode specifically:

> In internal mode, the n8n instance launches the task runner as a child process, **which is insecure by design**. … Because the runner runs as the same user on the same host as n8n, code that escapes the runner's sandbox has the same access as n8n, including to stored credentials.

**The default is the insecure one.** `N8N_RUNNERS_MODE` defaults to `internal`,
and `N8N_RUNNERS_ENABLED` is marked "**deprecated** from n8n 2.0" — i.e. task
runners are always on in 2.x, in internal mode, unless you configure otherwise
(**CONFIRMED**,
<https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/task-runners.md>).

**So: on a default Community install, per-user workflow privacy is a UI
convention, not a boundary.** A student who writes a Code node can, per n8n's
own text, potentially reach the database — which is where every other student's
workflows and every credential live. For a course that is explicitly about *how
AI systems can be harmed*, this is worth stating twice, because a student will
try it, and that is arguably the lesson.

**External mode** is the fix and it needs a second process: the
`task-runner-launcher`, normally deployed as the `n8nio/runners` sidecar
container (**CONFIRMED**, same page). The launcher is also published as a
standalone Linux binary — `task-runner-launcher-1.4.7-linux-amd64.tar.gz`,
released 2026-06-10 (**CONFIRMED**, GitHub releases API for
`n8n-io/task-runner-launcher`) — so a non-Docker systemd deployment is
*possible*, but n8n documents it only as a sidecar and step 4 of its own setup
guide says "Deploy the launcher as a sidecar container". **INFERRED: this is a
second, strong reason to choose Docker** (§6).

Hardening measures n8n recommends on top (**CONFIRMED**,
<https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/harden-task-runners.md>):
the `-distroless` image variant, running as `nobody` (65532), a read-only root
filesystem with an `emptyDir` on `/tmp`, and an AppArmor rule
`audit deny @{PROC}/[0-9]*/{environ,mounts} rwl,` to stop code reading env vars
out of `/proc`.

### 3.5 Low-hanging fruit to remove, even in a DMZ

The operator asked for the obvious stuff closed. All of these are **CONFIRMED**
as existing controls; whether they are sufficient is a judgement, not a fact,
and per `CLAUDE.md` this project does not write security guidance — this is a
list of levers, not an assurance.

| Lever | Setting | Source |
|---|---|---|
| Block shell and filesystem nodes | `NODES_EXCLUDE: "[\"n8n-nodes-base.executeCommand\", \"n8n-nodes-base.readWriteFile\"]"` — n8n names these two itself as the starting set, "if your users might be untrustworthy". Execute Command is blocked by default | [block-specific-nodes](https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/block-specific-nodes.md) |
| Stop Code-node access to env vars | `N8N_BLOCK_ENV_ACCESS_IN_NODE=true` (**default is `false`** — i.e. env access is ON out of the box) | [security env vars](https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/security.md) |
| Keep module imports off | `NODE_FUNCTION_ALLOW_BUILTIN` / `NODE_FUNCTION_ALLOW_EXTERNAL` — unset by default, and n8n "disables importing modules by default". **Do not set these to `*`** | [nodes env vars](https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/nodes.md) |
| Confine file reads | `N8N_RESTRICT_FILE_ACCESS_TO=<dir>`; `N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES` already defaults `true` | same |
| Isolate the Code node properly | `N8N_RUNNERS_MODE=external` + hardening (§3.4) | [task runners](https://docs.n8n.io/deploy/host-n8n/configure-n8n/set-up-task-runners.md) |
| Turn off the public API | `N8N_PUBLIC_API_DISABLED=true` | [disable the public API](https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/disable-the-public-api.md) |
| Enforce MFA / restrict personal-space publishing | `N8N_MFA_ENFORCED_ENABLED`, `N8N_PERSONAL_SPACE_PUBLISHING_ENABLED`, `N8N_PERSONAL_SPACE_SHARING_ENABLED` (via `N8N_SECURITY_POLICY_MANAGED_BY_ENV=true`) | [security env vars](https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/security.md) |
| SSRF protection | there is a dedicated page for it | [enable SSRF protection](https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/enable-ssrf-protection.md) |
| Self-audit | `n8n audit` / the security audit endpoint | [run security audits](https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/run-security-audits.md) |

**One REPORTED item, flagged rather than relied on.** A user reported on
2026-01-22 that on a shared instance they could select and use *other users'*
private credentials in workflow nodes — "I can also select the privately stored
credentials of all other users there — and thus, for example, read, change, or
delete all their events"
(<https://community.n8n.io/t/security-risk-using-credentials-from-other-users-in-the-same-n8n-instance/253898>).
**Not verified here, no n8n staff response visible in the thread, and the
reporter's own role on that instance is not stated** — if they were the Owner,
the behaviour is documented and expected. Recorded because it is the exact
failure mode a classroom would hit, and because it is cheap to test on day one
(create two Members, have one look for the other's credential in a node
dropdown). **That test is the single highest-value thing to run before students
arrive.**

---

## 4. Connecting n8n to THIS cluster

This is the integration that makes n8n worth having rather than a separate toy.
**It works, with no custom code.**

### 4.1 The local LLM: `llama-server`'s OpenAI-compatible API

**CONFIRMED — n8n side, from source, not from docs** (n8n's published docs for
the OpenAI credential do not mention the Base URL field at all, which is why
this was checked against the code).

`packages/nodes-base/credentials/OpenAiApi.credentials.ts` defines:

```ts
{
    displayName: 'Base URL',
    name: 'url',
    type: 'string',
    default: 'https://api.openai.com/v1',
    description: 'Override the default base URL for the API',
},
```

and its credential test is `GET {baseURL}/models`.
(<https://raw.githubusercontent.com/n8n-io/n8n/master/packages/nodes-base/credentials/OpenAiApi.credentials.ts>)

`packages/@n8n/nodes-langchain/nodes/llms/LMChatOpenAi/LmChatOpenAi.node.ts`
loads the model dropdown from `{baseURL}/models` and then filters the results
with a rule whose *first two clauses exist purely to accommodate non-OpenAI
endpoints*:

```ts
// If the baseURL is not set or is set to api.openai.com, include only chat models
pass: `={{
    ($parameter.options?.baseURL && !$parameter.options?.baseURL?.startsWith('https://api.openai.com/')) ||
    ($credentials?.url && !$credentials.url.startsWith('https://api.openai.com/')) || …
}}`
```

and the node carries a built-in notice: *"When using non-OpenAI models via
\"Base URL\" override, not all models might be chat-compatible or support other
features, like tools calling or JSON response format."* Self-hosted
OpenAI-compatible endpoints are a **deliberately supported path**, not a hack.

**CONFIRMED — cluster side, from the on-disk README of the pinned build**
(`/opt/llama.cpp/src/tools/server/README.md`, VERSION `b10369`, commit
`6e62ba53`): `llama-server` implements `GET /v1/models`,
`POST /v1/chat/completions`, `POST /v1/completions`, `POST /v1/embeddings` and
`POST /v1/responses`.

**So the whole integration is: create one OpenAI credential with Base URL
`http://<node-ip>:8080/v1` and any non-empty API key** (the field is
`required: true` in n8n; llama-server ignores it unless started with
`--api-key`, and its own examples use `sk-no-key-required`). Then drop in an
**AI Agent**, **Basic LLM Chain** or **OpenAI** node and pick the model. No Code
node, no HTTP Request node, no custom node.

**Three gotchas, all CONFIRMED, all cheap to fix:**

1. **The model name will be a filesystem path.** llama.cpp's README: *"By
   default, model `id` field is the path to model file, specified via `-m`. You
   can set a custom value for model `id` field via `--alias`."* The current unit
   (`/etc/default/llama-server` + `cluster/llama-server@.service`, read on node 1)
   does **not** pass `--alias`, so the dropdown will read
   `/opt/models/gpt-oss-120b/gpt-oss-120b-F16.gguf`. Cosmetic, but confusing in a
   teaching context. **Suggested (orchestrator's call, not done here): add
   `--alias gpt-oss-120b` to the unit.** Note this is a shared unit — changing it
   affects Missing Link's requests too, so it needs the usual measured-change
   discipline.
2. **"Use Responses API" defaults to ON.** In `LmChatOpenAi.node.ts` the
   `responsesApiEnabled` property is `default: true` for node typeVersion ≥ 1.3.
   llama.cpp b10369 *does* implement `/v1/responses`, but its README says
   plainly: *"This endpoint works by converting Responses request into Chat
   Completions request"* — a shim, not a native implementation. **Tell students
   to switch the node to Chat Completions**, and expect the Responses-only
   features (built-in web search, file search, code interpreter) to be absent.
3. **The node-level "Base URL" option is hidden on current node versions** —
   `displayOptions: { hide: { '@version': [{ _cnd: { gte: 1.1 } }] } }`. Set the
   base URL **in the credential**, not in the node's Options. This is a common
   source of "I can't find the Base URL field" confusion.

**Not applicable:** the *Ollama Chat Model* node exists and also takes a base
URL, but it speaks Ollama's own API, which `llama-server` does not serve. Use the
OpenAI node.

### 4.2 Missing Link

**CONFIRMED** by `GET http://127.0.0.1:8000/openapi.json` on the running
instance (title `Missing Link`, version `0.1.0`), rather than by guessing
routes. The interesting endpoints:

| Method | Path | Body |
|---|---|---|
| `POST` | `/jobs` | `multipart/form-data`: `kind` (**required**, string), `document` (string, default `""`), `upload` (file, optional) |
| `POST` | `/batch` | `multipart/form-data`: `files` (array, required) |
| `POST` | `/corpus` | `multipart/form-data`: `files` (required), `genre`, `note` |
| `GET` | `/api/jobs`, `/api/jobs/{job_id}` | — |
| `GET` | `/jobs/{job_id}/result`, `/jobs/{job_id}/progress`, `/jobs/{job_id}/text` | — |
| `POST` | `/jobs/{job_id}/cancel`, `/jobs/{job_id}/revive`, `/jobs/reorder`, `/jobs/ack` | — |
| `POST` | `/corpus/{doc_id}/delete` | — |
| `GET` | `/health` | — |

n8n's **HTTP Request** node sends `multipart/form-data` natively, so
"submit a document, poll until done, fetch the result" is a three-node workflow
with **no code**: HTTP Request (`POST /jobs`) → Wait → HTTP Request
(`GET /api/jobs/{id}`) in a loop → HTTP Request (`GET /jobs/{id}/result`).
**INFERRED**, from the schema above plus n8n's documented node set; not built or
tested.

**The blocker to resolve first: there is no auth on Missing Link.** The
OpenAPI document declares no security scheme, and the unit binds
`--host 0.0.0.0 --port 8000` (read from `cluster/missing-link.service` and
`/etc/default/missing-link`). Handing twenty students LAN access to a machine
that also serves an unauthenticated `POST /corpus/{doc_id}/delete` and
`POST /jobs/reorder` is a decision the orchestrator should make explicitly.
Options, in increasing order of work: put the class on a network segment that
cannot reach port 8000; bind Missing Link to `127.0.0.1` and reverse-proxy the
staff UI; or add auth to Missing Link. **Out of scope for this document —
reported, not solved.**

### 4.3 What this buys the course

**INFERRED**, offered as material for the instructor rather than as findings:
with a local OpenAI-compatible endpoint wired into n8n, the "help and harm"
syllabus becomes runnable rather than theoretical — prompt injection through a
webhook-triggered agent, exfiltration via a tool call, an LLM asked to triage
alerts and quietly hallucinating a CVE, and the difference between an LLM's
self-report and an external measurement (which is F36/F40's lesson in this repo,
and a genuinely good security lesson). All of it on data that never leaves the
building, which is the project's whole premise.

---

## 5. Snapshot and recovery

### 5.1 What state exists

**CONFIRMED** from n8n docs:

| State | Where (SQLite default) |
|---|---|
| Workflows, credentials (encrypted), executions, users, tags, settings | `~/.n8n/database.sqlite` |
| Encryption key (if not set by env) | `~/.n8n/config` — "n8n creates a random encryption key automatically on the first launch and saves it in the `~/.n8n` folder" |
| Binary data, source-control assets, logs | elsewhere under `~/.n8n` |

Under Docker that whole directory is the `n8n_data` volume mounted at
`/home/node/.n8n`. n8n's own note: even with Postgres, "the directory still
contains other important data like encryption keys… it's best to continue
mapping a persistent volume".

### 5.2 The credentials trap, which is the thing to get right

**CONFIRMED:** credentials in the database are encrypted with the instance
encryption key. If `N8N_ENCRYPTION_KEY` is not set, n8n **generates a random one
on first launch and stores it in `~/.n8n`**. n8n's own CLI documentation says
`export:credentials --all --decrypted` exists precisely so "you can use this to
migrate from one installation to another that has a **different secret key**".

**INFERRED, but it follows directly:** restore the database without the matching
key and you get every workflow back and **not one working credential** — the
failure surfaces as "Credentials could not be decrypted. The likely reason is
that a different `encryptionKey` was used", which is **REPORTED** widely across
n8n's community forum and GitHub (e.g.
<https://github.com/n8n-io/n8n/issues/12949>,
<https://community.n8n.io/t/credentials-could-not-be-decrypted-the-likely-reason-is-that-a-different-encryptionkey-was-used-to-encrypt-the-data/10219>).
This is exactly the "restore that silently doesn't restore" the brief warned
about.

**So, non-negotiably:**

1. **Set `N8N_ENCRYPTION_KEY` explicitly, before the first launch, before a
   single credential exists.** Never let n8n generate it.
2. **Store that key somewhere that is not the backup** — it is the one secret
   the backup cannot protect.
3. Note that n8n 2.x adds an optional two-layer model
   (`N8N_ENV_FEAT_ENCRYPTION_KEY_ROTATION=true` introduces a rotatable *data*
   key protected by the instance key). **CONFIRMED**, and equally confirmed:
   "Enabling encryption key rotation is a one-way change. There's no rollback
   path." **Do not enable it on a teaching instance.**

### 5.3 A recovery plan that actually recovers

**INFERRED**, assembled from the CONFIRMED mechanisms above. Because this is a
playground students are expected to break, the useful primitive is *restore to a
known-good class baseline*, not incremental backup.

- **Golden baseline.** Once configured, with the instructor's workflows and the
  cluster credential in place, stop n8n and take a **cold copy of the whole
  `~/.n8n` directory (or the Docker volume)**. Copying SQLite while n8n is
  running is the classic way to capture a torn database; stop the service first.
  Keep `N8N_ENCRYPTION_KEY` in the unit file / compose file, i.e. *outside* the
  snapshot, so it survives independently and is identical on restore.
- **Restore = stop, replace directory, start.** With the same key, credentials
  decrypt. Test the restore once, before the first class, on a machine where you
  can afford for it to fail — a restore that has never been exercised is a plan,
  not a backup. (This repo's own history: 41 tests passed against a pipeline that
  had never processed a document, F34.)
- **Per-student rescue without a full rollback.** `n8n export:workflow --backup
  --output=backups/latest/` writes one file per workflow; `export:credentials
  --backup` does the same for credentials (encrypted). Run it nightly from cron.
  This lets you restore one student's workflow without resetting the class.
- **Portable/decrypted escape hatch.** `n8n export:credentials --all --decrypted`
  produces plaintext credentials — the only form that survives a lost key.
  **CONFIRMED** that it exists; **obviously** it must not be left on disk.
- **Full logical dump.** `n8n export:entities --outputDir=./outputs` +
  `n8n import:entities --inputDir ./outputs --truncateTables true` is the
  database-type-independent path, and is what you would use to move SQLite →
  Postgres later.

All CLI commands: <https://docs.n8n.io/deploy/host-n8n/configure-n8n/use-the-command-line.md>.

---

## 6. Deployment

### 6.1 Docker vs npm vs systemd — and this is now a clear call

**CONFIRMED**, <https://docs.n8n.io/deploy/host-n8n/install-options/install-with-npm.md>:

> **npm-based installs are deprecated from n8n 3.0.**

and separately, the AI Assistant feature is unavailable on npm installs.
Current release is **2.35.7** (published 2026-08-21), `next`/`rc` is 2.36.5,
`engines.node: ">=22.22"` (**CONFIRMED**, npm registry). So 3.0 is not out yet
and npm still works today — but it is on notice, and a teaching resource that
outlives one semester will hit it.

**Read off node 1 directly:** Debian 12 bookworm; `node` is **22.23.2** from
the NodeSource repo (already installed, so the npm route would work right now);
**Docker, Podman and Redis are not installed**; `docker.io` 20.10.24, `podman`
4.3.1, `redis-server` 7.0.15, `nginx` 1.22.1 and `caddy` 2.6.2 are all available
from the Debian repos.

**Recommendation: Docker.** Reasons, in order of weight:

1. **External-mode task runners are the only real isolation for the Code node
   (§3.4), and n8n ships and documents them as a sidecar container.** The
   standalone launcher binary exists, but going off the documented path on the
   one component whose entire job is containment is the wrong place to be
   creative.
2. n8n's own docs note the Docker image **restarts automatically on OOM**
   whereas npm installs "might need … manual restart" — and a class *will* OOM
   it.
3. Version pinning and rollback are a tag change, which matters when a mid-term
   n8n upgrade breaks a student's workflow.
4. The npm route's deprecation clock is already running.

The cost is real and should be stated: Docker is another daemon on a fleet that
currently has none, `docker.io` in bookworm is 20.10 (old), and the image pull
needs internet access to Docker Hub. **INFERRED:** if the fleet is or becomes
air-gapped, plan for `docker save`/`docker load` from a machine that does have
internet — and note that the 100 Mb/s LAN (F28, 93.8 Mbit/s measured) makes
moving a several-hundred-MB image between nodes a matter of a minute or two, not
hours, unlike the 65 GB model distribution.

**A plain systemd unit around the npm install is the fallback** if Docker is
refused: `Type=simple`, `EnvironmentFile=/etc/default/n8n`, `Restart=always`,
running as a dedicated unprivileged user with `ProtectSystem=strict` and a
writable `~/.n8n`. It works, it matches how `llama-server` and `missing-link`
are already run in this repo (which is an argument for consistency), and it
gives up external-mode task runners in practice.

### 6.2 Ports, LAN access and the directory page

Current listeners on node 1 (**CONFIRMED**, `ss -ltn`): `8080` llama-server,
`8000` Missing Link, `50052` rpc-server, `22` ssh, `631` cups, `8231`
localhost-only. **Port 80 is free.**

n8n defaults to **5678** (`N8N_PORT`), listens on `::` by default
(`N8N_LISTEN_ADDRESS`), and `N8N_PROTOCOL` defaults to `http` (**CONFIRMED**,
[deployment env vars](https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/deployment.md)).
Keeping 5678 is fine and avoids a collision.

**The one thing that will silently break LAN access, and it is worth putting in
bold: `N8N_SECURE_COOKIE` defaults to `true`.** n8n's docs describe it as
"Ensures that cookies are only sent over HTTPS", and the source
(`packages/cli/src/auth/auth.service.ts`) sets the auth cookie with
`secure: cookieOverrides?.secure ?? secure` straight from that config, with **no
localhost exemption in that path** (**CONFIRMED**). **INFERRED consequence:**
served over plain `http://<lan-ip>:5678`, browsers will refuse to store the auth
cookie and students will bounce back to the login screen with no useful error.
Either terminate TLS in front of n8n, or set `N8N_SECURE_COOKIE=false`.

**For the directory page at :80** — a small nginx or Caddy on port 80 serving a
static index that links to n8n, Missing Link and anything else, and optionally
reverse-proxying them onto paths. If you reverse-proxy, **CONFIRMED**
requirements:

- Set `N8N_HOST`, `N8N_PORT`, `N8N_PROTOCOL`, `N8N_EDITOR_BASE_URL` so n8n
  generates correct URLs (in invite emails especially)
  (<https://docs.n8n.io/administer/manage-users-and-access/follow-best-practices.md>).
- Set `N8N_PROXY_HOPS=1` (default `0`).
- **Do not put n8n on a sub-path.** n8n's own warning: "Combining `N8N_PATH`
  with reverse proxies can cause folder navigation issues. Use a subdomain … or
  use `N8N_PATH` without reverse proxy." **INFERRED: keep the directory page as
  a page of links to `host:5678` / `host:8000`, not a path-rewriting proxy.**
  That is also the simplest thing that works, and it keeps n8n's WebSocket-based
  editor out of the discussion.
- n8n's editor uses WebSockets; a proxy that does not upgrade connections will
  produce an editor that loads and then does nothing. **REPORTED/INFERRED**, not
  verified here.

### 6.3 A starting configuration

**INFERRED** — this is a proposal to be reviewed, not a validated
configuration, and per this project's north star none of it should be treated as
settled before it has been run.

```
# identity / access
N8N_ENCRYPTION_KEY=<generated once, stored outside the backup>
N8N_SECURE_COOKIE=false            # only if serving plain HTTP on the LAN
N8N_PORT=5678
N8N_HOST=<lan-ip-or-name>
N8N_EDITOR_BASE_URL=http://<lan-ip-or-name>:5678

# blast radius
NODES_EXCLUDE=["n8n-nodes-base.executeCommand","n8n-nodes-base.readWriteFile"]
N8N_BLOCK_ENV_ACCESS_IN_NODE=true
N8N_RESTRICT_FILE_ACCESS_TO=/srv/n8n-scratch
N8N_PUBLIC_API_DISABLED=true
# leave NODE_FUNCTION_ALLOW_BUILTIN / _EXTERNAL unset

# code isolation (needs the runners sidecar)
N8N_RUNNERS_MODE=external
N8N_RUNNERS_AUTH_TOKEN=<secret>

# housekeeping
N8N_CONCURRENCY_PRODUCTION_LIMIT=8   # does NOT cover manual executions (§2.5)
GENERIC_TIMEZONE=Australia/...
EXECUTIONS_DATA_PRUNE / _MAX_AGE     # check current names before use
```

---

## 7. What is NOT established, and what to do about it

Listed because this project's expensive mistakes have all come from treating an
unmeasured plausible thing as settled.

1. **No resource number here was measured on this hardware.** Every RAM/CPU
   figure in §2.2 is REPORTED from third parties. **Measure it:** stand n8n up on
   node 3 or a laptop, idle it, then run a 20-execution burst, and record RSS and
   CPU in `docs/measurements.md`. Until then, "n8n is light" is a belief.
2. **The cross-user credential visibility report (§3.5) is unverified.** Two
   Member accounts and five minutes settle it. Highest value per minute of
   anything in this document.
3. **The Base URL integration has not been executed.** It is confirmed from
   source that the field exists and that llama.cpp serves the endpoints; nobody
   has yet clicked through an AI Agent node against
   `http://<node>:8080/v1`. Expect to discover something in the seam — this repo's
   record is that every real defect lived there.
4. **Class latency is a guess.** §2.3's "tens of minutes for twenty students" is
   INFERRED from measured single-request figures. Run five concurrent requests
   against one `llama-server` and measure before promising a lab session.
5. **The licence answer is textual, not written.** Get the email.
6. **Missing Link has no auth and would be reachable by students.** Decide
   deliberately (§4.2).

---

## Sources

Primary (licence and product):

- n8n `LICENSE.md`, `master` — <https://github.com/n8n-io/n8n/blob/master/LICENSE.md> (raw fetched 2026-08-23)
- Sustainable use license FAQ — <https://docs.n8n.io/privacy-and-security/sustainable-use-license.md>
- Compare editions — <https://docs.n8n.io/deploy/host-n8n/community-edition-features.md>
- Instance roles — <https://docs.n8n.io/administer/manage-users-and-access/understand-instance-roles.md>
- RBAC roles — <https://docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/see-available-roles.md>
- User management (self-hosted) — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/user-management.md>
- User management best practices — <https://docs.n8n.io/administer/manage-users-and-access/follow-best-practices.md>
- Set up task runners — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/set-up-task-runners.md>
- Harden task runners — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/harden-task-runners.md>
- Task runner env vars — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/task-runners.md>
- Security env vars — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/security.md>
- Nodes env vars — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/nodes.md>
- Deployment env vars — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/deployment.md>
- Block specific nodes — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/block-specific-nodes.md>
- Choose n8n's database — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/choose-n8ns-database.md>
- Control concurrency — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/control-concurrency.md>
- Enable queue mode — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/enable-queue-mode.md>
- Fix memory issues — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/fix-memory-issues.md>
- Measure performance — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/measure-performance.md>
- Set a custom encryption key — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/configuration-examples/set-a-custom-encryption-key.md>
- Rotate encryption keys — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/rotate-encryption-keys.md>
- Server CLI — <https://docs.n8n.io/deploy/host-n8n/configure-n8n/use-the-command-line.md>
- Install with Docker — <https://docs.n8n.io/deploy/host-n8n/install-options/install-with-docker.md>
- Install with npm — <https://docs.n8n.io/deploy/host-n8n/install-options/install-with-npm.md>

Primary (source code, fetched 2026-08-23):

- `packages/nodes-base/credentials/OpenAiApi.credentials.ts` — Base URL field
- `packages/@n8n/nodes-langchain/nodes/llms/LMChatOpenAi/LmChatOpenAi.node.ts` — base-URL routing, model listing, Responses API default, hidden node-level option
- `packages/cli/src/license.ts` + `packages/@n8n/constants/src/index.ts` — users quota
- `packages/cli/src/auth/auth.service.ts` — auth cookie `secure` flag
- npm registry metadata for `n8n` — <https://registry.npmjs.org/n8n>
- `n8n-io/task-runner-launcher` releases + `docs/setup.md`

Primary (this cluster, read-only):

- `/opt/llama.cpp/src/tools/server/README.md` (VERSION `b10369`, commit `6e62ba53`) — OpenAI-compatible endpoints, `--alias`
- `GET http://127.0.0.1:8000/openapi.json` — Missing Link routes and form fields
- `cluster/llama-server@.service`, `cluster/missing-link.service`, `/etc/default/llama-server`, `/etc/default/missing-link`
- `ss -ltn`, `apt-cache policy`, `/etc/os-release` on node 1
- `docs/measurements.md` (prefill/generation/wall-clock), `docs/FINDINGS.md` F28, F44

Secondary (REPORTED, third-party — resource figures and the credential-visibility report):

- <https://community.n8n.io/t/security-risk-using-credentials-from-other-users-in-the-same-n8n-instance/253898>
- <https://github.com/n8n-io/n8n/issues/12949>
- <https://community.n8n.io/t/credentials-could-not-be-decrypted-the-likely-reason-is-that-a-different-encryptionkey-was-used-to-encrypt-the-data/10219>
- <https://www.cherryservers.com/blog/n8n-self-hosting-requirements>
- <https://community.n8n.io/t/self-host-hardware-requirements/12843>
- <https://blog.ishosting.com/en/self-host-n8n-requirements>
