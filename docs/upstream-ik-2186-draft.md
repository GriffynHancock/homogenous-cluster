# DRAFT — upstream comment for ikawrakow/ik_llama.cpp#2186

**NOT FILED.** Written 2026-08-18 for the operator to review and send.
Recommendation from the search: comment on the existing issue #2186 rather
than opening a new one — it is the same reasoning error, already reported on
the CUDA path, and no issue anywhere covers the CPU backend at gpt-oss +
`--parallel > 1`. Note PR #2171 (open, unmerged, evolving) already implements
the `n_seq_max==1` gate at graph-build level, which is backend-agnostic and
would likely close this case too.

---

# DRAFT -- for operator review, NOT posted

Target: comment on https://github.com/ikawrakow/ik_llama.cpp/issues/2186
("Bug: CUDA flash-attention SWA tail slice is unsound with --parallel > 1
(silently drops in-window cells)")

Reasoning for commenting here rather than filing a new issue: #2186 already
diagnoses the general mechanism this hits ("sound for one sequence... not
sound for several") and proposes the fix. It's titled/scoped to CUDA and its
one comment is also CUDA (different arch). No issue found covers the CPU
backend. Same mechanism, same fix, and PR #2171 -- an unrelated, unmerged,
still-evolving SWA-ring-cache PR -- happens to already implement exactly the
suggested `n_seq_max == 1` gate at the graph-build level, which would
incidentally close this CPU case too, since it is backend-agnostic (the gate
sits above where CPU vs CUDA is selected).

---

## Draft comment text

Same root cause reproduces on the CPU backend (`ggml/src/iqk/iqk_flash_attn.cpp`),
where it aborts loudly instead of corrupting output silently.

**Repro:** `ik_llama.cpp` commit `8337e4cd` (2026-08-15), CPU only (no CUDA
build), gpt-oss-120b-F16 (`n_swa=128`), flags:
```
llama-server -m gpt-oss-120b-F16.gguf -t 4 -c 32768 --parallel 4 \
  --host 0.0.0.0 --port 8080 --no-warmup --jinja
```
`-fa` was never passed -- the server enables flash attention by default for
this model and logs `flash_attn = 1`. Sequential `/v1/chat/completions`
requests, ~1000-1400 token prompts. Deterministic on request 5 (the first
request that reuses a slot after the other 3 slots have written KV cells),
reproduced twice from a clean restart:

```
iqk_flash_attn_noalibi: found empty attention mask: nek1 = 512, first_k = 512
/opt/ik_llama.cpp/src/ggml/src/iqk/iqk_flash_attn.cpp:347: Fatal error
```

**Mechanism:** `iqk_flash_attn.cpp`'s SWA tail-slice (guarded by
`n_swa > 0 && mask`) keeps only the last `nblock*256` KV cells, on the same
append-order assumption this issue already names for the CUDA slice. gpt-oss
sets `n_swa=128`, giving `nblock*256 = 512` -- the `nek1=512` above. It holds
for one sequence; it does not hold for 4 interleaved slots, where the last
512 cells can belong entirely to other sequences, the current sequence's mask
comes back wholly masked, and the abort added by #1923 (for #1910) fires.
That abort is a diagnostic added after this issue's mechanism was already
present, not a fix for it.

One consequence worth flagging on its own: the abort fires inside
`ggml_print_backtrace`'s `fork()`+`gdb` path, and `fork()` from a
multithreaded server hands the child every lock the other compute threads
were holding. The child deadlocks reaching `execlp`, so the parent blocks in
`waitpid()` forever and the process never actually exits -- it hangs alive,
still holding the listening socket, answering nothing. A restart policy or
port check will not see this as a failure.

For what it's worth, your suggested fix (gate on `n_seq_max == 1`) looks like
it would close this CPU case too, not just CUDA: PR #2171 already implements
that exact gate (`can_use_kv_swa_reduction`, `n_seq_max == 1`) around where
`op_params[4]` (n_swa) gets set on the flash-attn op, and that happens at the
graph-build level shared by every backend -- so CPU would stop setting n_swa
in this configuration too, not just CUDA.
