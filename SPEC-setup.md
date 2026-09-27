# SPEC: capture setup, reset and state restore for `RigolDhoPlug`

Design decision: the plug stays thin. It does **not** get one setter per scope setting. Test conditions are
data that live in the test (a dict of SCPI header -> value); the plug applies and verifies them, can reset the
scope to a known state, and hands the scope back the way it found it.

**The real oscilloscope is NOT available to you.** Do not open `192.0.2.10` or any VISA resource. Everything
must work and be tested with `fake_resource.FakeDhoResource`. Read `AGENTS.md`, `README.md` and the existing
code first. Do not change the behaviour of existing methods. Do not commit.

## Facts measured on the real scope (DHO814, firmware 00.01.05) — build on these

- `*RST` restores factory defaults. The scope answers queries again within 0.34 s and the defaults are
  already in place. LAN settings survive the reset.
- `*OPC?` **always returns `0`** on this firmware. Never use it to wait for anything.
- `:SYST:ERR?` returns `0,"No error"` or e.g. `-222,"Data out of range"`, `-100,"Command err"`,
  `-200,"Command execute failed"`. It is a queue: read until it returns code 0.
- `:SYST:SET?` returns the whole setup as one IEEE-488.2 binary block (2883 bytes, 33 ms).
  `:SYST:SET <block>` loads it; the settings are in place 0.03 s later.
- The setup block does **not** restore waveform recording: after loading, `:REC:WREC:ENAB?` is `0` even if
  it was `1` when the block was taken. It must be restored separately.
- Read-back formats differ from what was written: `:TIM:MAIN:SCAL 1E-3` reads back `1.000000E-3`,
  `:ACQ:MDEP 1M` reads back `1.0000E+06`, `:TRIG:EDGE:LEV 1.5` reads back `1.500000E0`,
  `:CHAN1:COUP DC` reads back `DC`, `:TRIG:SWE NORM` reads back `NORM`, `:CHAN1:DISP ON` reads back `1`.
- Every query costs ~22 ms.

## 1. New methods on `RigolDhoPlug`

| method | behaviour |
|---|---|
| `check_errors() -> list[str]` | Reads `:SYST:ERR?` until the code is 0 (at most 20 reads). Returns the error strings found, empty list if none. |
| `write_checked(cmd: str) -> None` | `write(cmd)`, then `check_errors()`; raises `RuntimeError` naming the command and the errors if any. |
| `reset() -> None` | Sends `*RST`, sleeps 0.5 s, drains the error queue, invalidates the waveform setup cache. No `*OPC?`. |
| `wait_stopped(timeout_s: float = 10.0, poll_s: float = 0.05) -> None` | Polls `trigger_status()` until `STOP`; raises `TimeoutError` otherwise. |
| `get_state() -> dict` | `{'block': bytes, 'recording': bool}`: block from `query_binary_values(':SYST:SET?', datatype='B', container=bytes)`, recording from `:REC:WREC:ENAB?`. |
| `set_state(state: dict) -> None` | `write_binary_values(':SYST:SET ', state['block'], datatype='B')`, sleep 0.1 s, then `:REC:WREC:ENAB ON` or `OFF` per `state['recording']`, invalidate the waveform setup cache, raise `RuntimeError` if `check_errors()` is not empty. |
| `apply_setup(setup: dict) -> None` | See below. |

`apply_setup(setup)`:
- keys are SCPI headers without `?` (e.g. `':TIM:MAIN:SCAL'`), values are `int`, `float`, `bool` or `str`.
  `bool` is sent as `ON` / `OFF`. Order of the dict is the order of the writes.
- For each item: `write(f'{key} {value}')`, `check_errors()`. Then read back with `query(key + '?')` and compare:
  1. if the sent value is `bool`: read-back must be `1`/`ON` for True, `0`/`OFF` for False;
  2. else if both sides parse as numbers (the sent value may be a string with a `k`, `M` or `G` suffix such
     as `1M` = 1e6): equal within `math.isclose(rel_tol=1e-6, abs_tol=1e-12)`;
  3. else: equal ignoring case and surrounding whitespace.
- All items are attempted. If any item produced an error or a mismatch, raise `RuntimeError` whose message
  lists every failing key with the value sent, the value read back and the scope's error text.
- Invalidate the waveform setup cache at the end.

## 2. Returning the scope as found

- `__init__(self, resource=None, restore_state=None)`. `restore_state=None` means: use the new config key
  `CONF.declare('rigol_dho_restore_state', default_value=True, description=...)`.
- When enabled, `__init__` stores `self._initial_state = self.get_state()` after the resource is configured,
  and `tearDown()` calls `self.set_state(self._initial_state)` before closing. A failure while restoring is
  logged with `self.logger.warning` and must not prevent the resource from being closed.
- `bench_usb.py` must pass `restore_state=False` (it restores what it changes itself). Keep it working.

## 3. `fake_resource.py`

Extend `FakeDhoResource` without breaking existing behaviour:
- A generic settings store: any write of the form `<header> <value>` that is not already handled specially is
  stored under `<header>`; the query `<header>?` returns it in the scope's read-back format: numbers as
  `f'{x:.6E}'`, `ON`/`OFF` as `1`/`0`, other strings upper-cased. Querying a header that was never set
  returns its factory default from a small `DEFAULTS` dict containing at least: `:TIM:MAIN:SCAL` 2e-6,
  `:TIM:MAIN:OFFS` 0, `:CHAN1:SCAL` 0.05, `:CHAN1:OFFS` 0, `:CHAN1:COUP` `DC`, `:CHAN1:DISP` `1`,
  `:CHAN1:PROB` 1, `:TRIG:EDGE:SOUR` `CHAN1`, `:TRIG:EDGE:LEV` 0, `:TRIG:EDGE:SLOP` `POS`, `:TRIG:SWE` `AUTO`,
  `:ACQ:TYPE` `NORM`. A header in neither place raises `ValueError` as before.
- Error queue: `:SYST:ERR?` pops the oldest error or returns `0,"No error"`. Constructor argument
  `reject: dict[str, str] | None` maps a header to an error string (e.g.
  `{':TIM:MAIN:SCAL': '-222,"Data out of range"'}`): a write to that header queues the error and leaves the
  value unchanged.
- `*RST`: restores every setting, the memory depth (10000), the waveform selection and recording (off) to
  defaults, sets the status to `RUN`, empties the error queue.
- `*OPC?` returns `0` (like the real scope).
- `:SYST:SET?` via `query_binary_values(..., container=bytes)` returns a bytes blob encoding the fake's
  settings, memory depth and status, but **not** the recording flag. `write_binary_values(':SYST:SET ', data,
  datatype='B')` restores from such a blob and sets recording to off, like the real scope.
- `write_binary_values` is recorded in `log` as its message string.

## 4. Tests (`tests/test_plug.py`, add; do not weaken existing tests)

1. `check_errors` returns `[]` on a clean scope and the queued errors, in order, otherwise; the queue is empty afterwards.
2. `write_checked` raises on a rejected header and passes otherwise.
3. `apply_setup` happy path with all value kinds: float `1e-3`, int, `'1M'` for `:ACQ:MDEP`, `'DC'`, `'norm'` (lower case), `True`.
4. `apply_setup` with one rejected header: raises, the message names that header, and the other items were still applied.
5. `apply_setup` invalidates the waveform cache (the next `read_waveform` re-sends `:WAV:SOUR`).
6. `reset` restores defaults, clears errors, and does not send `*OPC?`.
7. `wait_stopped`: returns after `single()`; raises `TimeoutError` with a short timeout while running.
8. `get_state` / `set_state` round trip restores changed settings and the recording flag.
9. With `restore_state=True`: settings changed during the "test" are back after `tearDown()`, and the resource is closed. With `restore_state=False`: `:SYST:SET?` is never sent.
10. A restore failure in `tearDown` still closes the resource.
11. Existing subprocess tests for `bench_usb.py --fake` and `example_test.py --fake` still pass.

## 5. `example_test.py`

Show the intended style: a module-level `CAPTURE` dict, and in the phase `scope.reset()`,
`scope.apply_setup(CAPTURE)`, then read and record `vpp` and `num_points` as before. `--fake` must still work
and still print `vpp=<value> num_points=<value>`.

## 6. `README.md`

Add a section "Setting up a capture" after the first code example: the dict-in-the-test pattern, the new
methods in a small table, that the plug restores the scope in `tearDown` (and how to turn that off), and the
measured facts from the top of this file. Keep it under 50 lines. Do not edit other sections.

## Done means

`.venv/bin/python -m pytest -q` passes, `.venv/bin/python bench_usb.py --fake --repeats 3 --depths 10k --chunks all`
and `.venv/bin/python example_test.py --fake` run, `git status` shows changes only in `rigol_dho_plug.py`,
`fake_resource.py`, `tests/test_plug.py`, `example_test.py`, `bench_usb.py`, `README.md`.
Report: what you changed, the final pytest line, and anything in this spec you found wrong or ambiguous.
