# ComfyUI on this cluster — feasibility survey

**Date:** 2026-08-23 | **Status:** research only. Nothing was installed, nothing
was run on the cluster, and no number in this file was measured here.

**Why this exists.** The operator's instructor asked whether **ComfyUI** can be
added to the cluster, which is becoming a teaching resource for a
**cybersecurity course** — a playground for "how can AI help and harm in a
cybersecurity context?" — alongside the existing document-summarisation job.
Students would reach it over the LAN.

**Labelling.** Every claim below is marked **CONFIRMED** (verified against a
primary source), **REPORTED** (someone else states it), or **INFERRED**
(derived here, from arithmetic or from a source that does not state it
directly). **No web-sourced performance number is presented as if it were
measured on this hardware.** Only `docs/measurements.md` may be cited for that,
and nothing in this file belongs in it.

---

## 0. The verdict, first

**(b), with a real (a) carve-out and a hard (c) for video.**

| Workload | Verdict | Basis |
|---|---|---|
| **1–4-step distilled models** (SD-Turbo, SDXS, LCM, SDXL-Turbo) at 512×512 | **(a) viable for real student use** — order of **20–60 s per image**, one at a time per node | INFERRED from two independent REPORTED CPU anchors, §2.3 |
| **Standard SD1.5**, 512×512, 20 steps | **(b) overnight batch only** — order of **10 minutes per image per node** | INFERRED, §2.2 |
| **SDXL** at 1024×1024, 20–30 steps | **(b) barely, and not worth it** — order of **1–2 hours per image** | INFERRED, §2.4 |
| **Any video diffusion** (WAN, SVD, AnimateDiff, LTX) | **(c) not viable — needs a GPU** | INFERRED from step-cost arithmetic, §2.5; consistent with REPORTED "many minutes to hours per short clip" |

**And the framing the operator already half-guessed is correct, sharply so.**
The operator asked for student templates that are "high RAM, low compute".
Image and video diffusion is the exact opposite. The one CPU-only diffusion
benchmark found with a published memory figure peaks at **6.94 GB RSS**
([REPORTED, optimum-benchmark](https://huggingface.co/datasets/optimum-benchmark/cpu/blob/9bc4f1b30e6b6f3bea351034a6705fb86da2c99d/cpu_inference_diffusers_stable-diffusion_CompVis/stable-diffusion-v1-4/benchmark.json))
— **5% of one node's 131.8 GB.** Diffusion leaves the resource this fleet
actually has almost entirely idle and saturates the resource it most lacks.

`CLAUDE.md`'s argument is that old hardware is useful for **memory-bound** work.
It has never claimed old hardware is useful for **compute-bound** work, and
F12 is the reason: 4 cores, no AVX-512, and a measured 28.2 GB/s that is only
37% of the bus because the CPU cannot generate enough memory-level parallelism
to saturate it. Diffusion is compute-bound convolution. This is the wrong
hardware for it, and saying so is the honest answer.

**What IS "high RAM, low compute" on this fleet is the thing already running**
— a sparse-MoE LLM, where capacity is total parameters (RAM) and speed is
active parameters (bandwidth). So the recommendation in §7 is not "no": it is
**ComfyUI as a node-graph front end onto the existing `llama-server`**, which
puts the heavy work back on the engine this hardware is good at and keeps the
visual-pipeline pedagogy that presumably motivated the request.

---

## 1. What ComfyUI is, in the terms this project cares about

**CONFIRMED** from the official docs and the upstream repo:

- A single Python process serving a web UI on port **8188** by default, with a
  node-graph editor in the browser and **one sequential FIFO execution queue**
  behind it ([Startup Flags](https://docs.comfy.org/development/comfyui-server/startup-flags),
  [Routes](https://docs.comfy.org/development/comfyui-server/comms_routes)).
- CPU-only mode exists and is officially listed as supported hardware. The flag
  is `--cpu`, and its own help text is **"Use CPU for everything (slow)."**
  ([CONFIRMED, Startup Flags](https://docs.comfy.org/development/comfyui-server/startup-flags);
  [System Requirements](https://docs.comfy.org/installation/system_requirements)
  lists "CPU-only mode (slower)" among supported options).
- The system-requirements page **publishes no minimum RAM or VRAM figure at
  all**, and names **CUDA 13.0** for the NVIDIA path (CONFIRMED, same page).
  That CUDA version matters — see §3.
- **`--listen`** binds it to the LAN; **`--port`** moves it; **`--multi-user`**
  exists but does far less than its name suggests (§4).

**REPORTED**, from a third party, and it is the most honest one-paragraph
summary of CPU mode found anywhere:

> "CPU mode is good for learning the node graph, testing that a workflow is
> wired correctly before running it somewhere faster, and generating the
> occasional image where you do not care how long it takes. It is not good for
> iteration, and iteration is most of the work in image generation."
> — [Wireflow](https://www.wireflow.ai/blog/best-comfyui-alternative-no-gpus-in-2026)

The same source aggregates community reports as **"several minutes to close to
half an hour"** for a single small image on CPU (REPORTED). That is a range,
not a measurement, but §2 lands inside it from two independent directions.

---

## 2. The numbers

### 2.1 The anchors (all REPORTED, none measured here)

| # | Source | Hardware | Model / config | Result |
|---|---|---|---|---|
| **A** | [HF/Intel, *Accelerating Stable Diffusion Inference on Intel CPUs*](https://huggingface.co/blog/stable-diffusion-inference-intel) | Xeon **Sapphire Rapids**, 64 vCPU (**32 cores**), AVX-512 + AMX | SD, **512×512, 20 steps**, vanilla PyTorch **fp32** | **32.3 s** |
| **A′** | same | same | same, but IPEX + bf16 + DPMSolver | **5.05 s** |
| **A″** | same | same | same, but OpenVINO with static 512×512 reshape | **4.7 s** (6.9× over A) |
| **B** | [optimum-benchmark, HF dataset](https://huggingface.co/datasets/optimum-benchmark/cpu/blob/9bc4f1b30e6b6f3bea351034a6705fb86da2c99d/cpu_inference_diffusers_stable-diffusion_CompVis/stable-diffusion-v1-4/benchmark.json) (+ [config](https://huggingface.co/datasets/optimum-benchmark/cpu/blob/b0cecbec067c93a9b09113134068700a79f10be2/cpu_inference_diffusers_stable-diffusion_CompVis/stable-diffusion-v1-4/benchmark_config.json)) | AMD **EPYC 7763**, **4 cores allocated** (Zen 3, AVX2, no AVX-512) | SD-v1-4, batch 1, **2 steps**, PyTorch 2.3.1 CPU | **48.8 s**, peak **6.94 GB** RSS |
| **C** | [FastSD CPU](https://github.com/rupeshs/fastsdcpu) | Core **i7-12700** (8P+4E, AVX2, no AVX-512) | SD-Turbo, OpenVINO + TAESD, **512×512, 1 step** | **1.7 s** |
| **C′** | same | same | SDXS-512-0.9, OpenVINO + TAESD, 512×512, 1 step | **0.82 s** |
| **C″** | same | same | SDXL-Turbo, OpenVINO + TAESDXL, 512×512, 1 step | **2.5 s** |
| **C‴** | same | same | Hyper-SD SDXL, OpenVINO + TAESDXL, **768×768**, 1 step | **6.3 s** |

Note on **B**: the config does not record a resolution. Diffusers' SD-1.x
pipeline default is 512×512, so 512×512 is **INFERRED**, not confirmed. B is
the single most useful anchor because its core count (4) and ISA class (AVX2,
no AVX-512) match this fleet more closely than anything else found.

### 2.2 Standard SD1.5 on this fleet — **INFERRED, ~10 minutes per image**

Two independent routes, both **INFERRED**:

**Route 1, from B.** 48.8 s covers 2 UNet steps plus fixed costs (text encode,
scheduler setup, VAE decode). Taking the fixed cost as ~8–10 s leaves roughly
**20 s per UNet step on 4 Zen 3 cores**. 20 steps → **~410 s ≈ 7 min**.
Broadwell's per-core throughput on AVX2 FP32 ML kernels is lower than Zen 3's
at comparable clock; at 0.55–0.7× that gives **10–13 min** on one node.

**Route 2, from A.** 32.3 s on 32 Sapphire Rapids cores. Scale to 4 cores (×8)
→ 258 s. AVX-512 → AVX2 halves the FP32 vector width (×2) → ~516 s. SPR's
IPC/clock advantage over Broadwell (~1.3×) → **~670 s ≈ 11 min**.

**The two routes land at 7–13 minutes for one 512×512, 20-step SD1.5 image on
one node**, and that sits inside the REPORTED "several minutes to close to half
an hour" band. Call it **~10 minutes**, INFERRED, and do not quote it as
measured.

**What that means operationally:** one node produces **~6 images/hour**.
ComfyUI executes one prompt at a time (§4), so a class of 20 students each
submitting one image queues **~3.3 hours** on a single node. That is the whole
case against (a) for standard workflows.

### 2.3 The rescue — few-step distilled models, and this is the (a) carve-out

The cost is linear in step count, and distilled models cut steps by 5–25×.
SD-Turbo, SDXS and LCM produce a usable 512×512 image in **1–4 steps** instead
of 20–50 (REPORTED, anchors C/C′/C″).

At ~20 s per step (INFERRED, §2.2, plain PyTorch, no OpenVINO):

| Config | Steps | **INFERRED time per 512×512 image, one node** |
|---|---:|---:|
| SD-Turbo / SDXS | 1 | **~25–35 s** (one step + fixed cost) |
| LCM / LCM-LoRA | 4 | **~90–120 s** |
| SD1.5 standard | 20 | ~10 min |

**~30 seconds per image is a workable classroom exercise.** It is not
interactive iteration, but it is well inside "submit and watch a progress bar",
and it makes a real generative-image demonstration possible on this hardware
without buying anything.

**OpenVINO could improve this further, but it is UNVERIFIED on Broadwell and
should not be planned around.** Anchor A″ shows OpenVINO with static reshape at
6.9× the fp32 PyTorch baseline on Sapphire Rapids (REPORTED). Two problems:

1. **OpenVINO's published support list does not include Xeon E5 v4.** The exact
   list is "…6th - 14th generation Intel® Core™ processors; 1st - 5th generation
   Intel® Xeon® Scalable Processors…" — Broadwell-EP E5 v4 is neither
   ([CONFIRMED, OpenVINO system-requirements source](https://github.com/openvinotoolkit/openvino/blob/master/docs/articles_en/about-openvino/release-notes-openvino/system-requirements.rst)).
   A REPORTED release note says the CPU plugin now requires **AVX2 as a
   minimum** and drops SSE-only paths; these nodes do have AVX2 (F7,
   `docs/measurements.md`), so it may well work — but "not on the list" is
   where this project has been burned before, and it needs a one-hour test
   before anyone depends on it.
2. **ComfyUI does not use OpenVINO natively.** It goes through a third-party
   custom node ([openvino-dev-samples/comfyui_openvino](https://github.com/openvino-dev-samples/comfyui_openvino),
   CONFIRMED it exists and targets Intel CPU/GPU/NPU; **CONFIRMED it publishes
   no benchmarks and says nothing about pre-Skylake CPUs**). Adding a custom
   node is exactly the risk surface §5 is about.

**Recommendation:** treat plain PyTorch + turbo models as the plan, and
OpenVINO as an experiment with a measured before/after — which is what this
project's north star requires anyway.

### 2.4 SDXL — **(b) at best**

SDXL's UNet is ~2.6 B parameters against SD1.5's ~0.86 B, and 1024×1024 is 4×
the latent area. Per-step cost is therefore roughly **10–12× SD1.5's**
(INFERRED, spec arithmetic). At 20–30 steps that is **1.5–2.5 hours per image**
on one node. Anchor C‴ is consistent in direction: even with OpenVINO and a
1-step Hyper-SD model, 768×768 costs 3.7× the 512×512 SD-Turbo time on the same
i7 (REPORTED).

Overnight batch could produce a handful of SDXL images per node per night. It
is not a class exercise.

### 2.5 Video — **(c), and not marginally**

A video diffusion step denoises **every frame's latent simultaneously**. A
WAN 2.1 1.3B clip at 480p × 81 frames is on the order of **20–80× the per-step
cost of one 512×512 image** (INFERRED, from frame count and latent area; not
sourced). At 20–30 steps that is **tens of hours to days per five-second clip**
on one node.

The REPORTED position is directionally the same and much less precise:
CPU-only video generation "technically works but is painfully slow — plan for
many minutes to hours per short clip"
([Local AI Master, REPORTED](https://localaimaster.com/blog/local-text-to-video-low-vram)),
and CPU-only image generation alone is described there as "impractical".

**If video is any part of what the instructor has in mind, the answer is a GPU.
There is no configuration of this hardware that makes it work.** See §3.

---

## 3. The GPUs on hand, and the cheapest card that would change the answer

### 3.1 The Quadro P600 — verified present, and the answer is still "don't"

`lspci` on node 1, read-only, this session:

```
01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GP107GL [Quadro P600] [10de:1cb2] (rev a1)
```

`CLAUDE.md` records the P600 as unusable for compute. **That judgement was made
about LLM layers and it was a memory-capacity argument** — 2 GB cannot hold
meaningful layers of a target model. Diffusion is a different question, because
SD1.5 at 512×512 genuinely does fit in 2 GB with offloading, so the P600
deserved a fresh look. It got one. The answer is still no, for two reasons, the
second of which is decisive.

**Reason 1 — the speedup would be modest.** Vendor specs (CONFIRMED,
[NVIDIA Quadro P600 datasheet](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/productspage/quadro/quadro-desktop/quadro-pascal-p600-data-sheet-us-nv-704532-r1.pdf)):
384 CUDA cores, **1.195 TFLOPS FP32**, **64 GB/s**, 2 GB GDDR5, 40 W. Against
the node's ~448 GFLOPS FP32 peak (INFERRED: 4 cores × 3.5 GHz × 8 FP32 lanes ×
2 FMA × 2 FMA ports) and its **measured** 28.2 GB/s (`docs/measurements.md`),
that is ~2.7× the compute and ~2.3× the bandwidth. And the gain would be eaten
back: 2 GB forces `--lowvram`/`--novram`, which streams weights from system RAM
across PCIe for every layer (REPORTED behaviour of those flags), and Pascal has
no usable FP16 compute path — compute capability 6.1 runs FP16 at **1/64 of
FP32** rate, so frameworks fall back to FP32
([REPORTED, NVIDIA developer forums](https://forums.developer.nvidia.com/t/fp16-support-on-gtx-1060-and-1080/53256)),
doubling the bytes that have to stream. Realistically ~2×, on a workload that
needs ~50×.

**Reason 2, decisive — using it means pinning to an unpatched stack, on a box
whose whole purpose is teaching security.** PyTorch **removed Maxwell and
Pascal from its CUDA 12.8 binaries** as of 2.8
([CONFIRMED, PyTorch dev-discuss announcement](https://dev-discuss.pytorch.org/t/cuda-toolkit-version-and-architecture-support-update-maxwell-and-pascal-architecture-support-removed-in-cuda-12-8-and-12-9-builds/3128)
— note the announcement text says "Maxwell and Pascal" without listing SM
numbers; sm_61 is the P600's), and **CUDA 13.0 drops Pascal entirely, with
supported architectures starting at Turing** (REPORTED). ComfyUI's own system
requirements now name **CUDA 13.0** for NVIDIA (CONFIRMED). So the P600 path is:
old PyTorch, old CUDA, old ComfyUI, indefinitely, no security updates.

**Verdict: do not use the P600. Two times faster is not worth an unpatchable
dependency stack on a machine students are invited to attack.**

### 3.2 If the instructor wants image *and* video: the cheapest card

**Recommendation: a used NVIDIA RTX 3060 12 GB, one card, in one node.**

- REPORTED, and it is the consensus budget answer across every 2026 buying
  guide checked: "for budgets below $300, a used RTX 3060 12 GB … remains the
  cleanest budget answer for ComfyUI", running "Flux.1-dev fp8, SDXL at full
  quality, and all SD 1.5 variants", with the caveat that it will not "feel
  fast for full-precision FLUX or ambitious local AI video"
  ([popularai.org](https://www.popularai.org/p/rtx-3060-comfyui-performance-2026),
  [ComfyUI Wiki GPU buying guide](https://comfyui-wiki.com/en/install/install-comfyui/gpu-buying-guide)).
- REPORTED: "Twelve gigabytes is the floor for image generation, while sixteen
  gigabytes is the comfort tier… for FLUX.1 and video models you need 16–24 GB."
  So 12 GB buys SD1.5, SDXL and *small* video models comfortably, and 5B-class
  video models with quantisation. It does not buy 720p 14B video.
- It is Ampere (sm_86), which is inside every current PyTorch and CUDA support
  window — the opposite of the P600 problem.

**Two hardware gotchas specific to a ThinkStation P510, both worth checking
before ordering:**

1. **Power connector.** The P500/P510 shipped with 490 W, 650 W and 850 W PSU
   options; **the 490 W assembly provides a single 6-pin PCIe drop**, while the
   650 W and 850 W shipped a dual 6+8 assembly (REPORTED,
   [Lenovo P500/P700/P900 power configurator](https://download.lenovo.com/pccbbs/thinkcentre_pdf/next_gen_power_configurator_v1.6.pdf)
   and [Lenovo forums](https://forums.lenovo.com/t5/ThinkStation-Workstations/Thinkstation-P500-and-RTX-3060/m-p/5218979)).
   An RTX 3060 wants a single 8-pin at ~170 W. **Check which PSU is in the
   machine before buying the card**; a 6-to-8 adapter on a 490 W unit is the
   kind of thing that works until it doesn't.
2. **Do not substitute an Intel Arc B580** on price. It is the credible new-card
   alternative in the same bracket (REPORTED), but Arc's performance depends on
   **Resizable BAR**, which C612/X99-era workstation firmware generally does not
   expose. That is INFERRED, not confirmed for this specific board — but it is a
   cheap thing to verify and an expensive thing to discover after purchase.

**Where to put it:** node 3 (arriving, ~128 GB RAM, 1 TB storage). One GPU node
running ComfyUI, two CPU nodes running the document workload, is a clean split
that also solves §6 outright.

---

## 4. Deployment shape — how do students share one instance?

**Short answer: they queue, and there is no fairness in the queue.**

- **CONFIRMED**: one ComfyUI process executes prompts from **one sequential
  FIFO queue**. `POST /prompt` validates a workflow, enqueues it and returns a
  `prompt_id` and queue position. The original report in the upstream
  multi-user discussion: *"all the process (image generation) do not occur
  simultaneously but are queued as if there were a single user."*
  ([Discussion #4185](https://github.com/Comfy-Org/ComfyUI/discussions/4185),
  see also [#1927](https://github.com/comfyanonymous/ComfyUI/issues/1927),
  [#1819](https://github.com/comfyanonymous/ComfyUI/issues/1819),
  [#5941](https://github.com/Comfy-Org/ComfyUI/discussions/5941)).
- **REPORTED**: there is **no built-in fair-share or priority scheduler**. A
  student who queues ten 20-step jobs blocks everyone behind them until they
  finish — which at §2.2's ~10 min/image is most of a lesson.
- **REPORTED, upstream maintainer position**, and it is worth quoting because it
  changes meaning on CPU: *"AI image generation utilizes the GPU fully… the GPU
  usage portion cannot be parallelized."* On this fleet, substitute "the four
  cores" — the constraint is the same and it is *worse*, because §6.
- **`--multi-user` does not do what the name implies.** Its own help text is
  **"Enable per-user storage"** (CONFIRMED, Startup Flags). It is a file-based
  profile system for **settings and workflow templates only**, with **no
  authentication**, and an open issue reports that **outputs from different
  users still land in the same folder**
  ([Issue #3417](https://github.com/comfyanonymous/ComfyUI/issues/3417)).

### The shape that actually works here

**One ComfyUI process per node, and a dispatcher in front.** REPORTED as the
standard fix ("start one ComfyUI process per GPU, each bound to a device and
unique port; route new jobs to the least-busy instance by polling queue length
or using the WebSocket status stream"). On CPU the unit is the node, not the
GPU — which is **exactly the replication architecture this project already
measured and validated** (~1.8× on two nodes, ~90% of linear, F-series
replication measurement in `docs/measurements.md`). The same reasoning applies:
independent processes, no shared hot path, throughput scales with R.

**For the directory page at :80,** the minimal honest build is:

| Port | Service | Notes |
|---|---|---|
| **80** | nginx, static index page | Links to everything below. Also the natural place to put HTTP basic auth (§5) and per-node round-robin. |
| 8188 | ComfyUI, `--listen <lan-ip> --port 8188` | One per playground node. |
| 8000 | Missing Link job console | Existing. |
| 8080 | `llama-server` | **Do not expose to students.** ComfyUI reaches it server-side (§7); students should not be able to submit arbitrary prompts to the document engine. |

nginx in front is not decoration — it is the only place authentication can
live (§5), and the only place a per-student rate limit can live.

---

## 5. Multi-user, isolation, and what one student can break for everyone

### 5.1 There is no authentication. At all.

**CONFIRMED**, stated flatly by security research on the project: ComfyUI
*"does not include built-in authentication or authorization features"*
([Snyk Labs](https://labs.snyk.io/resources/hacking-comfyui-through-custom-nodes/)).
An open feature request for built-in auth notes that *"the recommended
workaround is to use a reverse proxy (like Nginx or Caddy) to implement basic
HTTP authentication"*
([Issue #10653](https://github.com/comfyanonymous/ComfyUI/issues/10653)); a
separate discussion asks for auth to be **mandatory by default**
([Discussion #5165](https://github.com/Comfy-Org/ComfyUI/discussions/5165)).

Consequently **every route is unauthenticated by default**, and the route list
is not small — roughly 20 native endpoints (CONFIRMED,
[Routes](https://docs.comfy.org/development/comfyui-server/comms_routes)).
The ones that matter for a shared box:

| Endpoint | What any student on the LAN can do with it |
|---|---|
| `POST /prompt` | Submit work — including a flood |
| `POST /queue` | **Delete other people's queued jobs, or clear the whole queue** |
| `POST /interrupt` | **Cancel whatever is currently running, for anyone** |
| `POST /history` | Delete everyone's history |
| `POST /free` | **Unload the models**, forcing a multi-minute reload |
| `POST /upload/image` | Write files into `input/` |
| `GET /view` | Read files back out — the path-handling attack surface |
| `GET /system_stats`, `/object_info`, `/extensions` | Enumerate the box and its installed extensions |

**So the answer to "what can a student break for everyone else" is: everything,
instantly, from a browser address bar, with no exploit required.** That is
before any vulnerability is involved.

### 5.2 Custom nodes are arbitrary Python, executed on startup

**CONFIRMED mechanism**: ComfyUI loads custom nodes at server startup by
directly importing source files from `./custom_nodes` via
`importlib.util.spec_from_file_location()` and `module_spec.loader.exec_module()`
— *"any Python file placed in that directory gets executed upon restart without
restrictions"* (Snyk Labs). The ecosystem is **1,300+ community extensions**
with no uniform security standard, and workflow JSON files shared publicly are
*"rarely"* security-reviewed before being imported.

**This is not theoretical. Named, dated, real:**

| CVE / incident | What it is |
|---|---|
| CVE-2024-21574 | ComfyUI-Manager pip injection (REPORTED, Snyk) |
| CVE-2024-21575 | ComfyUI-Impact-Pack path traversal (REPORTED, Snyk) |
| CVE-2024-21576 | ComfyUI-Bmad-Nodes unsafe `eval` (REPORTED, Snyk) |
| **CVE-2025-67303** | **Unauthenticated arbitrary file upload → RCE in ComfyUI-Manager**, full server compromise, no auth needed. Fixed in Manager **v3.38** ([Tencent Xuanwu Lab](https://xlab.tencent.com/en/2026/01/06/xlab-26-001/), [Doyensec advisory](https://www.doyensec.com/resources/Doyensec_Advisory_ComfyUI_Manager_RCE_via_Custom_Node_Install.pdf)) |
| **CVE-2026-68771** | **Unauthenticated RCE in ComfyUI v0.23.0** — unsafe pickle deserialization in the `LoadTrainingDataset` node. Fixed in **0.28.0** ([Rapid7](https://www.rapid7.com/db/vulnerabilities/cve-2026-68771/)) |
| **In-the-wild botnet, 2026** | **1,000+ exposed instances** mass-exploited into an XMRig (Monero) + lolMiner (Conflux) cryptomining and Hysteria V2 proxy botnet with a Flask C2. Entry was *misconfiguration, not a CVE*: "some custom nodes accept raw Python code as input and run it directly without requiring any authentication". **And when a vulnerable node was absent, the attackers used ComfyUI-Manager to install one, then retried** ([The Hacker News](https://thehackernews.com/2026/04/over-1000-exposed-comfyui-instances.html)) |

That last row is the one to read twice. **ComfyUI-Manager is itself the
privilege-escalation primitive** — it turns "this box has no vulnerable node"
into "this box can install one".

### 5.3 Removing the low-hanging fruit — concretely

The operator asked for this explicitly. In rough order of value:

1. **Do not install ComfyUI-Manager on the shared instance.** This removes the
   single largest documented attack path and the botnet's actual escalation
   step. If it must be present, current versions gate installs behind
   `allow_git_url_install` and `allow_pip_install` in `config.ini` (which
   **moved out of** `security_level` in recent versions — REPORTED, so read the
   version's own docs rather than assuming `security_level = strong` still
   covers it), and pin Manager ≥ **v3.38** for CVE-2025-67303.
2. **Run with `--disable-all-custom-nodes` plus `--whitelist-custom-nodes <dir>…`**
   for a small reviewed set (CONFIRMED flags). Students get the approved nodes;
   nobody gets arbitrary Python.
3. **Pin ComfyUI ≥ 0.28.0** (CVE-2026-68771) and put patching on a schedule.
   Two unauthenticated RCEs inside about a year is the base rate here.
4. **nginx basic auth on :80**, proxying to 8188, with 8188 bound to localhost
   or firewalled to the nginx host. Nothing else can make submissions
   attributable, and attribution is what makes a classroom work.
5. **Dedicated unprivileged user + a locked-down systemd unit**:
   `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`,
   `PrivateTmp=yes`, `ReadOnlyPaths=` on the models tree, `ReadWritePaths=`
   only on `output`/`temp`/`user`, and **no cluster secrets in the unit's
   environment** — Snyk lists environment-variable credential theft as a
   standard post-exploitation step.
6. **No outbound internet from the ComfyUI host.** The botnet's whole business
   model is egress. This is also consistent with `CLAUDE.md`'s posture that the
   cluster belongs on its own segment.
7. **`--max-upload-size`** (default 100 MB, CONFIRMED) tuned down, plus an
   nginx request-rate limit, so one student cannot fill the disk or the queue.

Third-party auth extensions exist but **should not be relied on**:
ComfyUI-Sentinel (login, IP filtering, JWT, per-user input/output directories)
is **archived and deprecated as of 25 May 2026** (CONFIRMED from its own repo),
pointing at a fork. Its own README warns it *"does not guarantee absolute
protection"*. An abandoned auth layer is worse than a reverse proxy you
understand.

### 5.4 The part the instructor will actually like

**Everything in §5.2 is the syllabus, not just the risk register.** A
cybersecurity course asking "how can AI help and harm?" has, in ComfyUI, an
unusually complete worked example sitting on one port:

- an **unauthenticated-by-default web service** that 1,000+ organisations put on
  the internet anyway;
- a **software supply chain** of 1,300+ unreviewed extensions, with a package
  manager that doubles as an exploitation primitive;
- **unsafe pickle deserialization** as a live CVE, which is the canonical
  Python-security lesson;
- **path traversal** in a node, which is the canonical web lesson;
- a **real in-the-wild campaign** with named malware, C2 infrastructure and
  persistence mechanisms to analyse;
- and a **metadata-leak lesson that costs zero compute**: ComfyUI's `SaveImage`
  node embeds the **entire workflow JSON in the output PNG's metadata**, which
  is why dragging a generated PNG back onto the canvas reconstructs the graph
  (REPORTED, [ComfyUI docs, image-to-image tutorial](https://docs.comfy.org/tutorials/basic/image-to-image);
  [Civitai writeup](https://civitai.com/articles/26592/the-workflow-in-a-png-trick-in-comfyui)).
  Every image a student publishes carries their prompts, model choices, LoRAs
  and file paths. That is a five-minute exercise with a real lesson and it runs
  at zero cost on any hardware.

**So the strong recommendation is two instances, not one:**

| Instance | Purpose | Hardening |
|---|---|---|
| **Shared / hardened** | Students build and run workflows | Everything in §5.3 |
| **Throwaway target** | The exploitation exercise | Deliberately unpatched, in a disposable VM or container on an isolated VLAN, **never on a node that runs `llama-server`**, and rebuilt from an image between classes |

The throwaway is where §6's snapshot requirement actually lives, and it is much
easier to satisfy if it is a VM you destroy rather than a service you repair.

---

## 6. Snapshot and recovery — what state ComfyUI keeps

| Path | Contents | Size | In the snapshot? |
|---|---|---|---|
| `models/` | Checkpoints, VAEs, LoRAs, upscalers | **Large** — SD1.5 ~2–4 GB each, SDXL ~6.5 GB, video models 10–30 GB | **No.** Separate read-only bind mount, reproduced from a manifest |
| `custom_nodes/` | **Arbitrary Python** | Small | **As a manifest** (repo URL + pinned commit), never as a tarball — you do not want to restore an attacker's node |
| `user/` | Settings, templates, saved workflows, Manager snapshots | Small | **Yes** — this is the students' actual work |
| `input/` | Student uploads | Small–medium | Optional; treat as untrusted |
| `output/` | Generated images, **with workflows embedded in the PNGs** | Grows | **Yes** — and note this doubles as workflow backup (§5.4) |
| `temp/` | Scratch | Transient | No |
| The Python venv | Mutated by any `pip install` a node triggers | ~5–10 GB | **No — recreate it.** This is the reason the honest recovery unit is the whole tree, not a directory |

**CONFIRMED**: ComfyUI supports **`--base-directory`**, which relocates *models,
custom_nodes, input, output, temp and user* under one path, plus individual
overrides `--input-directory`, `--output-directory` and `--user-directory`
that take precedence over it (Startup Flags). The models tree is additionally
relocatable via an extra-model-paths config file. **Point `--base-directory` at
a dedicated mutable path and the models tree at a separate, read-only mount.**
That one decision makes everything below easy: mutable state is one subtree,
the big immutable thing is outside it.

**REPORTED**: ComfyUI-Manager has its own snapshot feature that records the
installed custom-node set, writing to
`<user_directory>/default/ComfyUI-Manager/snapshots`. It is the right *idea* —
manifest, not tarball — but §5.3 recommends not installing Manager on the
shared box, so **reproduce the idea in a provisioning script instead**: a
`comfyui-nodes.txt` of `repo-url@commit` lines, applied by a re-runnable
installer. This is the same pattern as `cluster/models.json` + `models.sh`, and
it matches `CLAUDE.md`'s standing rule that provisioning is *"a Debian preseed
plus a re-runnable `setup.sh`, not a disk image"*.

**Recommended recovery model, in preference order:**

1. **Container or VM, models bind-mounted read-only from the host.** "Recover"
   = destroy and recreate from an image. This is the only option that also
   recovers from a *compromise* rather than a mistake, and §5.2's threat model
   says compromise is the case to plan for. Node 3's 1 TB makes this
   comfortable.
2. **Provisioning script + nightly tar of `user/` and `output/` only.** Models
   excluded, venv excluded, `custom_nodes` reproduced from the manifest.
   Recovery = delete the tree, re-run the script, untar the two directories.
   Consistent with existing conventions, no new technology.
3. Filesystem snapshots (btrfs/ZFS/LVM-thin) of the `--base-directory` subvolume
   if the host filesystem already supports it. Cheap if available, not worth
   reformatting for.

**One network note:** models fetched onto the box cross the **measured 100 Mb/s
link at 11.18 MB/s** (`docs/measurements.md`, F28), so an SD1.5 checkpoint is
~6 min and an SDXL checkpoint ~10 min. Not a constraint — but if twenty students
each pull their own checkpoint from HuggingFace through that link, they are
sharing the pipe with the cluster's own model distribution. Pre-stage a curated
model set; do not let students download.

---

## 7. Resource contention with the day job — and the recommendation

### 7.1 F44 says co-location is not free, and ComfyUI is a worse case than F44's

**F44, CONFIRMED on this hardware** (`docs/FINDINGS.md`): even at `nice -n 10`
/ `-n 15` throughout, a CPU-bound sidecar and `llama-server` were caught at
**378.9%** and **336.8% CPU simultaneously on the same 4 physical cores** (load
average 8.23, against 0.6–0.9 idle). The niced process's own rate degraded from
**4.7 s/claim to 41.7 s/claim — nearly 9× slower** — in a later pass, and
`llama-server`'s throughput visibly suffered in the same window. F44's
conclusion: *"`nice` sets scheduling priority, not an exemption from sharing 4
physical cores."*

**ComfyUI on CPU is a strictly harder case than the sidecar F44 measured**, and
for two reasons (INFERRED, but the mechanism is not in doubt):

1. **Duration.** F44's sidecar ran in bursts. A ComfyUI generation holds all
   cores for its **entire** runtime — 30 s for a turbo image, ~10 min for a
   standard one, hours for SDXL. There is no gap for the other job to use.
2. **F12's bottleneck is shared.** Generation on `llama-server` runs at **~99%
   of the node's achievable memory bandwidth** for dense models and **~61%** for
   sparse MoE (`docs/measurements.md`). Diffusion convolutions contend for the
   same 28.2 GB/s that F12 already shows the CPU cannot saturate on its own.
   The two workloads fight over the exact resource the whole architecture is
   built around.

**Expect both jobs to at least roughly halve each other's throughput for the
whole overlap**, and F44's measured 9× degradation of the deprioritised process
says the split can be far worse than even. **`nice` alone is not a mitigation —
F44 tested precisely that and it failed.**

### 7.2 How to schedule around it, ranked

1. **Dedicated node — best.** Put ComfyUI on **node 3** when it arrives and
   never run the document workload there. Costs one unit of replication factor
   (R goes 3 → 2 rather than 1 → 2), buys total isolation, and matches the
   separation-of-concerns reasoning `CLAUDE.md` already applies to the watchdog
   appliance. **This is the recommendation.**
2. **Time windows — cheapest good answer, and nearly free here.** The two
   workloads are *naturally disjoint in time*. Missing Link's premise is
   explicitly overnight work — "submit overnight, read in the morning", "nobody
   is waiting at a prompt" — while a class runs in daylight. A systemd timer
   that starts `comfyui.service` at 08:00 and stops it at 18:00, paired with a
   Missing Link queue that only dispatches outside that window, costs almost
   nothing and exploits the project's own async premise. **Do this even with a
   dedicated node, as belt and braces.**
3. **CPU pinning** — `AllowedCPUs=` / `CPUQuota=` on the ComfyUI unit, bounding
   it to 1–2 cores. Bounds the damage but does not remove it (F12: the memory
   bus is shared regardless of core assignment), and multiplies ComfyUI's
   already-marginal times by 2–4×. Use only if 1 and 2 are both impossible.
4. **`nice` alone** — **do not rely on this.** F44 is the finding that says so.

### 7.3 And a hard rule

**Never put the deliberately-vulnerable teaching instance (§5.4) on a node that
runs `llama-server` or Missing Link.** A cryptominer on the coordinator would
be indistinguishable, at first, from a slow document job — and this project has
already learned twice (F36, F39, F40) how expensive a wrong diagnosis of "the
server is just busy" gets.

---

## 8. What to tell the instructor

**Yes, ComfyUI can be added, and here is the honest shape of it.**

1. **Image generation works on the CPU nodes only with 1–4-step distilled
   models** (SD-Turbo, SDXS, LCM) at 512×512, at roughly **20–60 seconds per
   image, one at a time per node** (INFERRED, §2.3). That is a real classroom
   exercise. Standard 20-step SD1.5 is **~10 minutes an image** and belongs in
   an overnight batch. SDXL is ~1–2 hours an image.
2. **Video generation is not possible on this hardware.** Not slow — out of
   reach by one to two orders of magnitude (§2.5). If video is wanted, it needs
   a GPU: a **used RTX 3060 12 GB** in one node, after checking that machine's
   PSU has an 8-pin PCIe drop (§3.2). The Quadro P600 already in the box will
   not do it and should not be tried (§3.1).
3. **ComfyUI has no authentication and one shared queue.** Any student can
   cancel, clear or flood everyone else's work from a browser, with no exploit
   involved (§5.1). It needs nginx basic auth in front, custom nodes disabled by
   default, ComfyUI-Manager left off, and a locked-down systemd unit (§5.3).
4. **The security posture is a feature for this course.** Two unauthenticated
   RCEs (CVE-2025-67303, CVE-2026-68771), a 1,300-extension unreviewed supply
   chain, unsafe pickle deserialization, path traversal, and a real 1,000-host
   cryptomining botnet — all in one application. **Run two instances**: a
   hardened shared one for building workflows, and a deliberately vulnerable
   disposable one, in a VM on an isolated segment, as the exploitation target
   (§5.4).
5. **Put it on its own node, or run it in daylight hours only.** F44 confirmed
   on this hardware that even a niced sidecar starves `llama-server` on a
   4-core node; ComfyUI is a worse case (§7).
6. **The best fit for this hardware is not diffusion at all.** These nodes are
   RAM-rich and compute-poor. **Wire ComfyUI to the existing `llama-server`
   over its OpenAI-compatible API** and the node graph becomes a visual builder
   for LLM pipelines running on the engine this fleet is genuinely good at —
   prompt-injection demonstrations, phishing-email generation and detection,
   log triage, policy summarisation, adversarial-prompt red-teaming. Multiple
   maintained custom node packs do exactly this: e.g.
   [ComfyUI-OpenAI-API](https://github.com/bgreene2/ComfyUI-OpenAI-API),
   [comfyui-llamacpp-client](https://github.com/fidecastro/comfyui-llamacpp-client),
   [ComfyUI-Simple-LlamaCPP-Client](https://github.com/ai-joe-git/ComfyUI-Simple-LlamaCPP-Client)
   (CONFIRMED these exist and target OpenAI-compatible / `llama-server`
   endpoints; **none was installed or tested**, and every one of them is a
   custom node, i.e. arbitrary Python — pick one, read it, pin it).
   Keep a turbo-model image workflow alongside it as the "what a diffusion
   model is, and why detecting its output matters" exhibit.

**The one-sentence version:** ComfyUI belongs here as a *pipeline builder* and
as a *security teaching target*, not as an image factory — and the moment
anyone wants video, the conversation is about a $300 graphics card, not about
tuning.

---

## 9. What was NOT verified, and would need to be

Listed so nobody mistakes this survey for a measurement.

- **Nothing here was run on the cluster.** The only command executed was a
  read-only `lspci` on node 1 to confirm the Quadro P600 is physically present.
- **Every time figure for this hardware is INFERRED** from third-party
  benchmarks on different CPUs. The obvious cheap experiment: install ComfyUI
  in a scratch venv on a node that is *not* serving, run SD1.5 at 512×512/20
  steps and SD-Turbo at 512×512/1 step, and record both in
  `docs/measurements.md`. That is under an hour and it replaces this entire
  section with facts.
- **Whether OpenVINO runs at all on Broadwell-EP** (§2.3). Broadwell is not on
  the support list; AVX2 is present. Untested.
- **Whether the P600 would even load a driver stack** old enough for Pascal
  alongside anything else on the node. Not attempted — and §3.1 argues it should
  not be.
- **Resizable BAR availability on the ThinkStation P510** (§3.2), which decides
  whether Intel Arc is a real alternative.
- **Which PSU is actually fitted** to each P510 in the fleet (§3.2).
- **Video step-cost arithmetic** (§2.5) is INFERRED from frame count and latent
  area, not from a published benchmark. The conclusion is robust to being wrong
  by 3× in either direction; the recommendation does not change.
