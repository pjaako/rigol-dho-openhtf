# HANDOFF: rigol-dho-openhtf

Updated 2026-10-12, after the move to a new machine. This file is in git and public since 2026-10-05: no real addresses, serial numbers or private paths in it. Read `AGENTS.md` and `README.md` first; this file is only the moving part.

Way of working (the user, 2026-10-12): the agent in this directory is the **project owner**, not the coder.
It writes a spec per task (`SPEC-*.md` style: files, interfaces, SCPI strings, tests, "done means"), hands
it to a cheaper, faster subagent that only uses `FakeDhoResource` (Haiku for grunt work: mechanical edits,
searches, boilerplate tests, running checks; Sonnet for coding against a spec), reviews the result (diff,
`pytest`, the `--fake` runs, mypy, the rules in `AGENTS.md`) and does the acceptance on the real scope
itself. The `hortator` skill that used to describe this is not on the new machine.

## State

- `main` = `origin/main` = `a450b4d` (2026-10-05). No other branches. 149 tests, example and bench pass
  with `--fake`, `capture.schema.json` matches `rigol-dho-capture schema`, mypy clean (all re-checked
  2026-10-12 on the new machine).
- 2026-10-12: work moved to a new machine (Python 3.13, `uv`), a fresh clone: no `setups/*.bin`, no note
  from the sibling project, nothing on USB. The scope is reached **over LAN** (raw socket 5555 and VXI-11
  both open, ~27 ms per query; real address in `SCOPE_HOST`, ask the user). Only `*IDN?`, `:TRIG:STAT?`
  and `:SYST:ERR?` were sent so far; the scope was in AUTO sweep with an empty error queue. Nothing was
  saved or changed on it yet.
- 2026-10-12, later: `set_state()` reworked after the note from the generator project (SPEC-restore-run.md,
  accepted on the scope over VXI-11 and the raw socket): `:RUN` before the block prevents the drop after a
  measurement; the run/stop state is saved and restored; stopped states are restored by running one full
  sweep at the saved settings before `:STOP` (the stopped scope clamps timebase and `:WAV:STOP` to its record);
  a hung session raises a clear error. 172 tests. Open: the raw socket hangs on ~5 of 70 block loads
  (README); VXI-11 never did. The generator project's claim "the block is dropped while in STOP" was not
  reproduced (20 of 20 loads took while stopped); its real case was the measurement before the restore.
- mypy on that machine: plain `uvx mypy` picked Python 3.11 and reported 8 bogus syntax errors on `type`
  statements; run `uvx --python 3.13 mypy --strict --follow-imports=silent --python-version 3.13 src/rigol_dho_openhtf/capture.py`.
- `bench_usb.py --fake` overwrites `bench_results/bench.json`, which holds the real measurements of
  2026-09-27. Restore it with `git checkout -- bench_results/bench.json` after a fake run.
- The code is the package `src/rigol_dho_openhtf/` (`plug.py`, `capture.py`, `golden.py`,
  `fake_resource.py`), set up with `uv sync --all-extras --dev`. `python capture.py ...` became the command
  `rigol-dho-capture schema|check`. `plug.py` is not strict-clean under mypy (21 errors on 2026-10-05; only `capture.py` is required to be).
- Done 2026-10-05:
  - screenshot work and `:WAV` restore merged from `hortator-test` (accepted over LAN on 2026-09-28);
  - packaging; the next two were accepted on the DHO814 over USB:
  - `tearDown()` no longer closes PyVISA's shared ResourceManager (it killed other plugs' sessions);
  - `set_state()` checks that the setup block was loaded, reloads up to three times and raises. The scope
    drops a block after a `:MEAS:ITEM?` query followed by another query; the cause is not found.
- Known limit of that check: it reads back eight settings (timebase, channels 1 and 2). A test that changes
  none of them and measures can still leave the scope unrestored without notice.
- Every commit is public: scan for real addresses, the serial number and home paths before a push.

## The sibling project

- `../siglent-sdg-openhtf` (SDG2042X generator plug, its own owner agent) uses this package as a
  dependency, pinned to a commit. Its next task is a station demo: generator generates, scope measures.
- The two owner agents write each other untracked notes: `NOTE-*.md` in the other project's working tree,
  deleted by the reader, never committed. My last one is `../siglent-sdg-openhtf/NOTE-from-rigol-dho.md`
  (2026-10-05): pin `829ff0d`, what the restore check does and its limit.
- Asked for by that agent and not built (new features, not decided by the user): a helper that writes a
  whole `get_state()` result to a file, and a `measure()` that hides the first `9.9E+37`.
- Its note of 2026-10-12 (set_state fails in STOP) was read, acted on (SPEC-restore-run.md) and deleted;
  my answer is `../siglent-sdg-openhtf/NOTE-from-rigol-dho.md` (2026-10-12): re-pin, use VXI-11.

## Shared resources right now

- Scope: DHO814, firmware 00.01.05, on USB (`lsusb` shows `1ab1:044d`). On 2026-10-05 its LAN port
  (port 5555, static address set by us; the real address is not written here, ask the user) did not accept a connection from this PC; not investigated.
- PC side: NetworkManager profile `rigol-direct`, a static address in the scope's /24 on the wired interface, no default route.
- Scope as left 2026-10-05 about 05:50, read 5 s after the last restore: 2 us/div, 50 mV/div and 0 V
  offset on CH1, sweep AUTO, recording off, `:WAV` `CHAN1`, `NORM`, `WORD`, 1..1000, no errors. This is not
  the user's setup of 2026-09-27 (500 us/div, recording on); that one is in
  `setups/user_setup_2026-09-27.bin`. Blocks saved on 2026-10-05: `setups/user_setup_2026-10-05_packaging.bin`
  (block only) and `setups/user_state_2026-10-05_restore.bin` (pickled `get_state()` without `check`).
- CH1 is cabled to the generator's CH1 (BNC, no probe, no terminator); the generator's outputs were off.
  The generator belongs to the sibling project: the user offered access on 2026-10-05, I did not need it.
  Ask before using it.
- No model server is running. Ports 4444 (demo) and 8080 (llama-server) are free.

## Open items, in order

0. **Raw-socket hang on block loads** (README, 2026-10-12): not reproducible on demand, 5 of ~70 loads.
   Ideas not yet tried: capture the TCP stream of a hanging load (is the block split differently?), a
   reconnect-and-retry in `set_state()` using `resource.resource_name` (needs the hang to occur during a
   test to be accepted), USB check of the whole rework (today's acceptance was LAN only, USB is not cabled
   to this machine).

1. **Published** 2026-09-28: https://github.com/pjaako/rigol-dho-openhtf (public, branch `main`, MIT,
   31 commits by `pjaako` with Claude as co-author). Before the push the history was rewritten: serial
   number replaced by `DHO8A000000000`, scope address by `192.0.2.10`, PC address by `192.0.2.1`, session
   links removed from commit messages, screenshots cropped, the binary setup dump and this file removed
   from every commit.
   Pushes: on 2026-10-05 the agent's shell could not push (no SSH agent, the GitHub CLI token for
   `pjaako` was invalid); the user pushed `main` from their own terminal. After `gh auth login` this works:
   `git -c credential.helper='!gh auth git-credential' push https://github.com/pjaako/rigol-dho-openhtf.git main:main`.
   **`setups/*.bin` and real addresses must stay out of git; this file is committed and must not contain them.** Text in the
   repository uses placeholder addresses; the tools read the real one from `SCOPE_HOST`
   (set `SCOPE_HOST` to the scope's real address, which is not written here).
2. **Editor check** by the user: open `captures/clock.yaml` in PyCharm, change `coupling: DC` to `DCC`, see
   whether it is underlined. Colleagues use VS Code. Nobody has verified either editor.
3. **Signal generator** (its CH1 on scope CH1, plain BNC): decode UltraAcquire segments (why 20 480 samples
   per segment, relation to `MAXF`, timestamps) and measure the single-capture loop rate. Done 2026-10-12:
   the golden mask against real defects (README table; SPEC-golden-ends.md fixed the record ends). The
   generator is driven with bare SCPI over VXI-11 (`C1:BSWV`, `C1:OUTP`). The scope input carries a 50 ohm
   feed-through terminator since 2026-10-12 (the first one was defective and showed 2x the amplitude; the
   replacement was verified: `LOAD,50` and 0..3 V set gives 3.07 V on the scope, `LOAD,HZ` gives 1.54 V), so
   use `LOAD,50`. The golden table in README was measured before that, unterminated with `LOAD,HZ`.
   Restore the generator with `LOAD` first, then `BSWV` (an amplitude written under `LOAD,50` doubles when
   the load is switched to HZ afterwards).
4. `trigger.type` was dropped from the capture file format; it comes back when a second trigger type is needed.
5. Deep-memory attachments: OpenHTF keeps attachments in memory and base64 in JSON. 25 M points need an
   output callback that writes the waveform to its own file.
6. Two buttons PASS / FAIL in the web GUI: stock OpenHTF has none (the user remembered them from
   spintop-openhtf). Parked by the user.

## Do not

- Do not verify a restore by comparing setup blocks: a loaded block reads back with a few bytes changed.
- Do not send `:MEAS:CLE ALL` or `:MEAS:CLE ITEM1`: this firmware answers `-108`.

- Do not read deep memory (RAW) while waveform recording is enabled: it returns a capture that is none of
  the recorded frames. The plug refuses; do not bypass it.
- Do not use `*OPC?` to wait: it always answers 0 on this firmware.
- Do not conclude anything from clipped samples.
- Do not change the PC's network or udev configuration yourself; prepare the command for the user.
- Do not start local models when the user says the machine must stay quiet.
