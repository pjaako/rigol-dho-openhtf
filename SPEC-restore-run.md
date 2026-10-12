# SPEC-restore-run: a command before the setup block, and the run/stop state restored

Change to `src/rigol_dho_openhtf/plug.py`, `src/rigol_dho_openhtf/fake_resource.py`, `tests/test_plug.py`.
Read `AGENTS.md` first. No hardware: everything runs with `FakeDhoResource`. Do not touch `capture.py`,
`golden.py`, `README.md` or the other specs.

## Facts, measured on the DHO814 (firmware 00.01.05) on 2026-10-12 over LAN

1. The known drop: after a `:MEAS:ITEM?` query followed by any other query, the next `:SYST:SET <block>` is
   not loaded. `set_state()` itself produces the "other query" (`check_errors()` before the block).
   - Over VXI-11 and USB the drop is silent and a second load takes (as in README).
   - **Over the raw socket (`::5555::SOCKET`) the session hangs instead**: the first query after the dropped
     block never answers (8 of 8, scope stopped or running). The scope itself is fine, a new connection
     answers at once, and the block was not loaded. The current `set_state()` therefore dies with
     `VisaIOError` (timeout) on the raw socket, and `tearDown()` leaves the scope unrestored.
   - **Any plain command written between the queries and the block prevents the drop**: `:RUN`, `:STOP`,
     `:CHAN1:DISP 1` and `*CLS` each gave 4 of 4 first-attempt loads, stopped and running (32 of 32 in
     all). Waiting does not help (README). Queries do not help (they are the trigger).
2. Being stopped does not drop blocks: after `:STOP`, after `:SING` + `STOP`, after a RAW read of 10k or
   100k points, with or without a NORM read, 20 of 20 loads took on the first attempt (both transports).
3. The block carries the run/stop state only partly:
   - a block saved while the scope was **stopped**, loaded while running: the scope **stops**;
   - a block saved while running, loaded after `:STOP`: the scope runs again (AUTO/TD);
   - a block saved while running, loaded after `:SING` and `STOP`: the scope **stays stopped** (6 of 6).
   So a test that does `single()` leaves the scope stopped after `tearDown()` although it was running before.
   The run/stop state has to be restored explicitly.

## Plug changes

`get_state()` adds one key: `'running': bool`, `self.trigger_status() != 'STOP'` (anything but `STOP`
counts as running: `RUN`, `AUTO`, `WAIT`, `TD`). Query it last, after `check`.

`set_state(state)`, new order; comments in the code must say why (facts 1 and 3 above, short):
1. `check_errors()` as now.
2. `self.write(':RUN')` — the plain command that prevents the drop (fact 1); also what the block needs to
   restart after a single capture (fact 3). Send it always, even if the saved state is stopped: the stop is
   restored in step 7.
3. The block, the `_CHECK_HEADERS` read-back and the retry loop as now. New: wrap each check query so that a
   resource timeout (`pyvisa.errors.VisaIOError` **or** the builtin `TimeoutError`; the fake raises the
   latter, `pyvisa` must stay an optional import in the plug as it is today) becomes
   `RuntimeError('set_state: the scope stopped answering after the setup block (a dropped block hangs a
   raw-socket session); reconnect')`. Do not retry after a hang: the session is dead.
4. `_wav_setup = None`, the `:WAV` settings, their read-back check, as now.
5. The recording switch, as now (delay, ON/OFF, read-back check).
6. Run/stop: if `'running'` is in `state` (states saved by older versions do not have it: then skip this
   step entirely, like a missing `'check'`):
   - running wanted: if `trigger_status() == 'STOP'`, `self.write(':RUN')`, `time.sleep(0.2)`, query again;
     raise `RuntimeError('set_state: the scope did not start running')` if still `STOP`.
   - stopped wanted: `self.write(':STOP')`, `time.sleep(0.2)`, raise
     `RuntimeError('set_state: the scope did not stop')` if `trigger_status() != 'STOP'`.
7. `check_errors()` and raise on errors, as now.

Keep all existing behaviour and messages that tests rely on. Update the docstrings of `get_state`/`set_state`
and the method table comment in `plug.py` if there is one. `tearDown()` is unchanged (it already catches
every exception and closes the resource).

## Fake changes (`FakeDhoResource`)

- Constructor gains `block_drop: str = 'silent'` (choices `'silent'`, `'hang'`; `ValueError` otherwise).
- The drop condition stays: `:MEAS:ITEM?` then another `query()` arms a drop of the next `:SYST:SET` block.
  New: **any `write()` of a command disarms it** (clears `_measured` and `_drop_block`), because that is what
  the real scope does (fact 1). `write_binary_values` is not a plain write.
- A dropped block in `'silent'` mode: as now (nothing changes, no error).
- A dropped block in `'hang'` mode: the fake sets `self._hung = True`; from then on every `query()` and
  `query_binary_values()` raises `TimeoutError('FakeDhoResource: session hung after a dropped setup block')`
  until `close()`. `write()` still works (a real raw socket accepts writes). Expose `hung` as a read-only
  property for tests.
- Run state and the block (fact 3): `write_binary_values(':SYST:SET')` sets `_status = 'STOP'` if the saved
  `blob['status'] == 'STOP'`; otherwise, if the fake is currently stopped **by `:SING`** it stays `STOP`,
  and if it is stopped by `:STOP` or running it becomes `RUN`. Track how the fake got stopped (a flag set by
  `:SING`, cleared by `:RUN`/`:STOP`/`*RST`). Keep the existing WAIT-then-STOP behaviour of `:TRIG:STAT?`
  after `:SING`.

## Tests (`tests/test_plug.py`), in addition to the existing ones, which must keep passing

1. `get_state()` has `'running'`: `True` on a fresh fake, `False` after `stop()`.
2. `set_state` writes `:RUN` after `:SYST:ERR?` and before `:SYST:SET ` (check order in `fr.log`).
3. After `measure('VPP', 1)` the block loads on the **first** attempt: `fr.log.count(':SYST:SET ') == 1`
   (replaces the expectation of 2 in `test_set_state_loads_the_block_again_when_the_scope_dropped_it_after_a_measurement`;
   rename or rewrite that test so its name is true). The retry test with the monkeypatched
   `write_binary_values` keeps `== 3`.
4. `block_drop='hang'`: a bare `write_binary_values(':SYST:SET ', ...)` after `measure` + a query hangs the
   fake (`fr.hung`, next query raises `TimeoutError`); through `set_state()` with the `:RUN` in place it does
   not hang and restores.
5. A monkeypatched check query that raises `TimeoutError` makes `set_state` raise `RuntimeError` matching
   `stopped answering`, with exactly one `:SYST:SET ` in the log.
6. Run/stop restore: state saved running; `single()`, `wait_stopped()`; `set_state(state)` → `trigger_status()`
   is not `STOP`. State saved stopped (`stop()` then `get_state()`); `run()`; `set_state(state)` → `STOP`.
   Also through `tearDown()` with `restore_state=True`, both directions.
7. A state dict without `'running'` restores without touching run/stop (no `:RUN`/`:STOP` after the block,
   only the one before it).
8. The fake alone: after `:SING` and two `:TRIG:STAT?` (→ `STOP`), loading a block saved in `RUN` keeps
   `STOP`; after `:STOP`, loading the same block gives `RUN`; a block saved in `STOP` loaded while `RUN`
   gives `STOP`.
9. Unknown `block_drop` raises `ValueError`.

## Done means

`uv run pytest -q` passes (149 existing + the new ones), `uv run python example_test.py --fake` and
`uv run python bench_usb.py --fake --repeats 1 --depths 10k` run (then `git checkout -- bench_results/bench.json`),
`uvx --python 3.13 mypy --strict --follow-imports=silent --python-version 3.13 src/rigol_dho_openhtf/capture.py`
is still clean. No other files changed. Report what you changed and anything in this spec that did not work
as written.

## Amendment after the first acceptance run (2026-10-12)

Accepted on the scope, both transports: running saved states (plain, after `single()` + RAW read +
measurement, with recording ON) restore and hold 5 s later, no retries. **Stopped saved states do not**: the
timebase came back as `4.000000E-6` instead of `2.000000E-6` in 7 of 7 runs. Measured facts:

4. A stopped scope clamps the timebase to what its stopped record can show: stopped at 1 ms/div, a write
   of `2E-6` or `1E-6` reads back `4.000000E-6` with no error. A block saved stopped at 2 us/div, loaded
   while running at 1 ms/div, reads back `2.000000E-6` right after the load and `4.000000E-6` from about
   1 s later (the clamp is applied late, so the check in step 3 cannot see it). The clamped value persists
   as the setting: after `:RUN` it still reads `4.000000E-6`. A slower saved timebase (100 ms/div) is not
   clamped.
5. After a block, `:RUN` takes effect late: `:TRIG:STAT?` still says `STOP` 0.2 s after it and `TD` at 0.5 s.
   Run/stop commands sent within ~0.5 s of a block load land out of order (`:RUN`, 0.3 s, `:STOP` ended
   running). `:SING` after a stopped block completes one acquisition and stops.
6. What holds (3 of 3): block, 1.5 s, `:RUN`, wait until running, write `:TIM:MAIN:SCAL 2E-6` (reads back
   `2.000000E-6` while running), 0.5 s, `:STOP`: `2.000000E-6` and `STOP` 3 s later.

### Plug: step 6 of `set_state` replaced

Record `t_block = time.perf_counter()` right after the last successful block write (step 3).
Add `_RUN_STOP_POLL_S = 2.0` (class constant) and a helper `_wait_status(want_stop: bool)` that polls
`trigger_status()` every 0.1 s for up to `_RUN_STOP_POLL_S` and returns `True` once `(status == 'STOP') == want_stop`.

- running wanted: if `trigger_status() == 'STOP'`: `self.write(':RUN')`; if `_wait_status(False)` is `False`,
  raise `RuntimeError('set_state: the scope did not start running')`. (Replaces the fixed 0.2 s sleep: fact 5.)
- stopped wanted (facts 4-6, say so in a comment):
  a. sleep until 1.5 s have passed since `t_block` (the recording step may already have used 1.2 s of it);
  b. `self.write(':RUN')`; `_wait_status(False)` or raise `RuntimeError('set_state: the scope did not start running')`;
  c. read the `check` headers back; for each one that differs write `f'{header} {value}'` with the saved
     read-back string (the scope accepts its own format), then read all back again and raise
     `RuntimeError(f'set_state: not restored while running: {still_different}')` if any differs;
  d. `time.sleep(0.5)` (one acquisition at the restored timebase; the stopped record itself cannot be restored);
  e. `self.write(':STOP')`; `_wait_status(True)` or raise `RuntimeError('set_state: the scope did not stop')`;
  f. read the `check` headers back once more and raise `RuntimeError(f'set_state: not restored after :STOP: {...}')` if any differs.
  A state without `'check'` skips c and f.

### Fake: the clamp

- `_record_scale`: the timebase (float) of the last acquisition. Set it to the current `:TIM:MAIN:SCAL`
  value whenever the fake starts acquiring (`:RUN`, `:SING`, `*RST`, a block load that leaves it running)
  and whenever `:TIM:MAIN:SCAL` is written while running. Initial value: the initial timebase.
- Clamp rule: while stopped, a `:TIM:MAIN:SCAL` value below `_record_scale / 250` becomes
  `_record_scale / 250` (one data point: 1 ms/div record, 4 us/div floor), stored in the scope's read-back
  format so that `:TIM:MAIN:SCAL?` returns e.g. `4.000000E-6`. Applied immediately to a direct write while
  stopped; for a block that leaves the fake stopped, applied **at the next `write()`** (the real scope applies
  it ~1 s later, after the plug's read-back check). The clamped value persists after `:RUN` (fact 4).
- `:RUN` in the fake may stay as it is (immediate); the plug's polling covers the real delay.

### Tests to add

10. Fake alone: stopped at 1 ms/div (run at `1E-3`, then `:STOP`), write `2E-6` → reads `4.000000E-6`; `:RUN`
    → still `4.000000E-6`; write `2E-6` while running → `2.000000E-6`.
11. Fake alone: block saved stopped at `2E-6`, loaded while running at `1E-3` by a bare `write_binary_values`:
    `:TIM:MAIN:SCAL?` is `2.000000E-6` right after, `4.000000E-6` after any `write()`.
12. Plug: state saved stopped at `2E-6` (apply `1E-3`? no: run, set `2E-6`, `stop()`, `get_state()`); then
    `run()`, `apply_setup({':TIM:MAIN:SCAL': 1e-3})`, `measure('VPP', 1)`, `set_state(state)` → `trigger_status()`
    is `STOP` and `:TIM:MAIN:SCAL?` is `2.000000E-6`; the log shows `:RUN`, then `:TIM:MAIN:SCAL 2.000000E-6`,
    then `:STOP` after the block. Same through `tearDown()`.
13. Plug: `_wait_status` raises the "did not start running" error when the fake is monkeypatched to keep
    answering `STOP` (patch `query` for `:TRIG:STAT?`), without waiting more than ~2.5 s.
Existing tests 6 and 7 (run/stop restore, state without `'running'`) must still pass; adjust the expected log
of test 2/7 only if the new order requires it. "Done means" is unchanged.

## Amendment 2 after the second acceptance run (2026-10-12)

Accepted: running states (socket and VXI-11, with recording ON) and stopped states at 2 us/div. Failed:
stopped states at a slow timebase (100 ms/div): `set_state: not restored: [':WAV:STOP']`, 9 of 9.

7. At slow timebases the scope clamps `:WAV:STOP` to the points acquired so far: running at 100 ms/div,
   `:WAV:STOP 1000` reads back `422`; right after a block saved stopped at 100 ms/div it reads back `5`
   (the block restarts the sweep). The clamped value persists as the setting, like the timebase (fact 4).
   After a full sweep and an explicit `:STOP`, writing the `:WAV` settings takes (`1000`) and holds 3 s later,
   at 100 ms/div (sweep 1.2 s) and 500 ms/div (sweep 6 s). At 2 us/div nothing is clamped.
8. The raw-socket hang after a block showed up 2 times in 7 restores of a stopped 100 ms/div state that
   followed a VXI-11 session, never with the socket alone (0 of 8), and the fresh session's next block load
   hung once too; the scope answered queries throughout and loaded blocks again within ~25 s. Not
   reproducible on demand today: the plug keeps raising the clear `RuntimeError`; no reconnect logic yet.

### Plug: the stopped path, final shape

In `set_state`, when `'running' in state and not state['running']`: **skip step 4** (the `:WAV` writes and
their check); do step 5 (recording) as now; then `_restore_stopped(state, t_block)`:
  a. sleep until 1.5 s after `t_block`;
  b. `:RUN`, `_wait_status(False)` or raise `'set_state: the scope did not start running'`;
  c. read the `check` headers back, write back the ones that differ, read back, raise
     `'set_state: not restored while running: [...]'` if any still differs;
  d. sleep for one full sweep: `min(max(0.5, 12 * tb), 12.0)` s, `tb = float(check[':TIM:MAIN:SCAL'])`
     (0.5 s if there is no `check`); comment: a stopped record is complete only after a full sweep, and
     the scope clamps `:WAV:STOP` (fact 7) and the timebase (fact 4) to an incomplete one; the cap keeps
     `tearDown()` under ~15 s at very slow timebases, where the final check then reports the clamp;
  e. `:STOP`, `_wait_status(True)` or raise `'set_state: the scope did not stop'`;
  f. write the `:WAV` settings (mode first, as step 4 does), read them and the `check` headers back, raise
     `RuntimeError(f'set_state: not restored after :STOP: {different}')` listing every header that differs;
  g. read `:REC:WREC:ENAB?` back once more and raise `'set_state: recording switch was not restored'` if it
     differs from `state['recording']` (the `:WAV` writes now come after the recording switch; in steady
     state they never switched it off, README).
The running path is unchanged (steps 4, 5, 6-running as implemented).

### Fake: the slow sweep

- A timebase of `0.05` s/div or more counts as a slow sweep. While the fake runs at a slow timebase, or is
  stopped by a block load at a slow timebase (not by an explicit `:STOP`), a `:WAV:STOP` write is clamped:
  it reads back `5`. After an explicit `:STOP` at that timebase the write takes. At fast timebases nothing
  changes. (The real clamp follows acquisition progress; the fake models only the order the plug must keep.)

### Tests to add

14. Fake alone: run at `1E-1`, `:WAV:STOP 1000` → `5`; `:STOP`, `:WAV:STOP 1000` → `1000`; at `2E-6`
    running → `1000`.
15. Plug: state saved stopped at `1E-1` (`run()`, write `1E-1`, `stop()`, `get_state()`); then `run()`,
    `apply_setup({':TIM:MAIN:SCAL': 1e-3})`, `measure('VPP', 1)`, `set_state(state)` → `trigger_status()`
    `STOP`, `:TIM:MAIN:SCAL?` `1.000000E-1`, `:WAV:STOP?` `1000`; in the log the last `:STOP` comes before
    the last `:WAV:STOP 1000`, and no `:WAV:` write appears between `:SYST:SET ` and that `:STOP`.
16. Plug: the stopped path with recording ON in the saved state restores recording (`:REC:WREC:ENAB?` `1`)
    and the `:WAV` settings.
17. Plug: a stopped-state restore where the fake is monkeypatched so `:WAV:STOP?` keeps answering `5`
    raises `RuntimeError` matching `not restored after :STOP`.
Test 12 stays valid (fast timebase). "Done means" unchanged.
