# Review 2 — fixes required (supersedes SPEC.md where they differ)

Pass 1 produced a correct plug per SPEC.md, but (a) SPEC.md itself was wrong in three places, found by testing
on the real scope, and (b) `bench_usb.py` / `example_test.py` have bugs because they were never executed.
From now on **everything must be runnable without hardware** through a `--fake` flag, and you must run it.

Work through the numbered items in order. Run `pytest -q` after each file you change.

## Facts measured on the real scope (DHO814, firmware 00.01.05)

- VISA name: `USB0::6833::1101::DHO8A000000000::0::INSTR`. Vendor id is **decimal 6833** (= 0x1AB1).
- `:TRIG:STAT?` while running returns `TD` (also `RUN`, `AUTO`, `WAIT`). Only `STOP` means stopped.
- `:ACQ:MDEP?` returns e.g. `1.0000E+04`. `:ACQ:MDEP` **accepts** `10k`, `1E5`, `100000` and **rejects** `1.0000E+05`.
- `:WAV:PRE?` field `points`: always 1000 in NORM/MAX. In RAW it equals the current `:WAV:STAR`..`:WAV:STOP`
  window, **not** the memory depth.
- The voltage formula in the Rigol manual is wrong on this firmware. `yorigin` must be **ignored**:
  `v = (raw - yreference) * yincrement`. (With channel offset 0.1 V the preamble reports yorigin=58 but the
  samples are not shifted.)
- One read of the whole record is fastest (10 M points in 1.0 s). Chunked reads only add ~25 ms per chunk.

## rigol_dho_plug.py

1. Discovery: `rm.list_resources('USB?*::INSTR')`, take the first name whose second `::`-separated field equals
   0x1AB1 when parsed with `int(field, 0)` (both `6833` and `0x1AB1` match).
2. Keep the resource manager in `self._rm`; `tearDown` closes the resource, then `self._rm` if the plug created it.
3. Remove the unused `import datetime`.
4. `memory_depth() -> int`: `int(float(self.query(':ACQ:MDEP?')))`.
   `set_memory_depth(depth)` accepts `int` or `str`; an `int` is sent as a plain integer (`:ACQ:MDEP 100000`),
   a `str` is sent unchanged.
5. `read_waveform(self, channel, mode='NORM', fmt='BYTE', chunk_points=None)`:
   - validation and the RAW-needs-STOP check stay as they are;
   - send `:WAV:SOUR`, `:WAV:MODE`, `:WAV:FORM`;
   - `n = self.memory_depth()` if `mode == 'RAW'` else `1000`;
   - send `:WAV:STAR 1` and `:WAV:STOP <n>`, then `pre = self.preamble()`; if `pre['points'] != n` raise `RuntimeError`;
   - `chunk_points=None` means one read of all `n` points (no further STAR/STOP needed). Otherwise read windows of
     at most `chunk_points` points, sending `:WAV:STAR`/`:WAV:STOP` before each `:WAV:DATA?`;
   - `v = (raw.astype(np.float64) - pre['yreference']) * pre['yincrement']` — **no yorigin**;
   - `t` unchanged.

## fake_resource.py

6. After `:SING` the status is `WAIT` for the first `:TRIG:STAT?` query and `STOP` from the second on.
7. `:ACQ:MDEP x` accepts `10k`/`1M` suffixes, plain integers and `1E5`; stores an integer. `:ACQ:MDEP?` returns
   it formatted like the scope: `f'{value:.4E}'` (e.g. `1.0000E+04`). Constructor: `memory_depth=10000`.
   Remove the `points` constructor argument: the record length is 1000 in NORM/MAX and `memory_depth` in RAW.
8. `:WAV:PRE?` field `points` = 1000 in NORM/MAX, and `STOP - STAR + 1` in RAW. Report a **non-zero yorigin**
   (`58` for BYTE, `15000` for WORD) so tests prove the plug ignores it. yreference is `128` for BYTE and
   `32768` for WORD; yincrement `4.000000e-02` for BYTE and `1.5625e-04` for WORD.

## bench_usb.py

9. `--resource NAME`: open it with `pyvisa.ResourceManager('@py').open_resource(NAME)` and pass the object to
   `RigolDhoPlug(resource=...)`. Without `--resource` use `RigolDhoPlug()`.
10. New flag `--fake`: `RigolDhoPlug(resource=FakeDhoResource())`; PyVISA must not be imported in this mode.
11. `--chunks` is a comma list where each item is an integer or the word `all` (= `chunk_points=None`).
    Default `all,1000000,250000`. Scenario names use the item text, e.g. `raw_10k_BYTE_all`.
    Skip a chunk size that is larger than the record.
12. `waveforms_s = 1000 / median_ms`.
13. State restore in `finally`: remember `memory_depth()` (an int) and `trigger_status()` at start; at the end
    call `run()`, `set_memory_depth(initial_depth)`, then `stop()` only if the initial status was `STOP`.
14. Set the memory depth while the scope is running: `run()`, `set_memory_depth(depth)`, short sleep, `stop()`,
    wait until `trigger_status() == 'STOP'` (timeout 10 s), then time the reads.
15. For `norm_*` scenarios take `points` from the returned `Waveform`.
16. Print a real markdown table: header row, `|---|` separator row, `|`-delimited rows.

## example_test.py

17. Rewrite with the real OpenHTF API; the phase is a module-level function:
    ```python
    @htf.measures(
        htf.Measurement('vpp').with_units(units.VOLT),
        htf.Measurement('num_points'),
    )
    @htf.plug(scope=RigolDhoPlug)
    def capture_waveform(test, scope):
        w = scope.read_waveform(1, mode='NORM', fmt='BYTE')
        test.measurements.vpp = float(w.v.max() - w.v.min())
        test.measurements.num_points = len(w.raw)
    ```
    (`from openhtf.util import units`). There is no `test.scope`, `test.record_measurement` or
    `htf.ConsoleOutputCallback`.
18. `--fake` flag: a subclass of `RigolDhoPlug` whose `__init__` injects `FakeDhoResource()`; substitute it with
    `capture_waveform.with_plugs(scope=FakePlug)` if that API exists in the installed OpenHTF
    (`.venv/lib/python3.13/site-packages/openhtf`), otherwise use what does.
19. Exit code 0 when the OpenHTF test passed, 1 otherwise; print `vpp=<value> num_points=<value>` on success.

## tests/test_plug.py

20. Update existing tests to the new behaviour (no yorigin, `memory_depth()` is an int, fake constructor).
    Expected volts for BYTE: `v[5] == (5 - 128) * 0.04`.
21. Add tests for: item 1 (both `6833` and `0x1AB1` names, with a stub resource manager, no real PyVISA I/O);
    item 5 RAW with `memory_depth=2500`: default reads one window `1..2500`; `chunk_points=1000` reads
    `1..1000`, `1001..2000`, `2001..2500`; item 6; item 7.
22. Add tests that run `python bench_usb.py --fake --repeats 3 --depths 10k --chunks all,4000 --out <tmp>` and
    `python example_test.py --fake` via `subprocess.run([sys.executable, ...], cwd=<repo root>)`: return code 0,
    bench output contains `norm_byte` and `raw_10k_BYTE_4000` and no `ERROR`, `<tmp>/bench.json` parses as JSON,
    example output contains `num_points=1000`.
23. Delete `tests/__init__.py` if the tests pass without it.

Done means: `pytest -q` passes, both scripts run with `--fake`, `git status` shows no stray files.
