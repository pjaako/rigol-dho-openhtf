# Review 1 — fixes required

`rigol_dho_plug.py` and the tests are accepted except for items 1–3. `bench_usb.py` and `example_test.py`
contain bugs because they were never executed. From now on **everything must be runnable without hardware**:
both scripts get a `--fake` flag and you must run them with it.

Facts measured on the real scope (DHO814, firmware 00.01.05), use them:
- PyVISA (pyvisa-py) reports the resource as `USB0::6833::1101::DHO8A000000000::0::INSTR`: the vendor id is
  **decimal 6833**, not `0x1AB1`. The pattern in SPEC.md was wrong.
- While running, `:TRIG:STAT?` returns `TD` (also possible: `RUN`, `AUTO`, `WAIT`). Only `STOP` means stopped.
- `:ACQ:MDEP?` returns a number like `1.0000E+04`, not `10k`.

## rigol_dho_plug.py
1. Discovery: call `rm.list_resources('USB?*::INSTR')` and take the first name whose second `::`-separated
   field equals vendor 0x1AB1 when parsed with `int(field, 0)` (so both `6833` and `0x1AB1` match).
2. Keep the resource manager as `self._rm` (it must outlive the resource); in `tearDown` close the resource,
   then `self._rm` if the plug created it.
3. Remove the unused `import datetime`.

## fake_resource.py
4. After `:SING` the status is `WAIT` for the **first** `:TRIG:STAT?` query and `STOP` from the second on
   (emulates a completed single capture). `:ACQ:MDEP?` returns the depth as a number string, e.g.
   `1.0000E+04` for `10k`, `1.0000E+06` for `1M` (accept `k`/`M` suffixes and plain numbers in `:ACQ:MDEP x`).

## bench_usb.py
5. `--resource NAME` currently passes the *name string* as the resource object. Open it:
   `pyvisa.ResourceManager('@py').open_resource(NAME)` and pass that object; without `--resource` use
   `RigolDhoPlug()` auto-discovery.
6. New flag `--fake`: use `RigolDhoPlug(resource=FakeDhoResource(points=10000))`, no PyVISA import at all.
7. `--chunks` values must be converted to `int` before use.
8. `waveforms_s` must be `1000 / median_ms` (it was multiplied by `repeats`).
9. State restore: the scope was running if the initial status is anything other than `STOP`
   (then call `run()`), otherwise `stop()`. Restore the initial memory depth using the value read at start.
10. For `norm_*` scenarios take `points` from the returned `Waveform`, not from a second `preamble()` call.
11. Print a real markdown table (header row, `|---|` separator row, `|`-delimited rows).

## example_test.py
12. Rewrite with the real OpenHTF API. The phase must be a module-level function:
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
    `units` comes from `from openhtf.util import units`. There is no `test.scope`, no
    `test.record_measurement`, no `htf.ConsoleOutputCallback`.
13. `--fake` flag: use a subclass of `RigolDhoPlug` whose `__init__` injects `FakeDhoResource()`.
    Swap the plug class for the phase with `capture_waveform.with_plugs(scope=FakePlug)` — check that this
    API exists in the installed OpenHTF (`.venv/lib/python3.13/site-packages/openhtf`) and use what works.
14. The script exits with code 0 when the OpenHTF test passed, 1 otherwise, and prints
    `vpp=<value> num_points=<value>` on success.

## tests
15. Add tests that run `python bench_usb.py --fake --repeats 3 --depths 10k --chunks 4000,10000 --out <tmp>`
    and `python example_test.py --fake` with `subprocess.run([sys.executable, ...])`, assert return code 0,
    that the bench output contains `norm_byte` and `raw_10k_BYTE_4000` and no `ERROR`, and that
    `<tmp>/bench.json` parses as JSON. Add tests for items 1 and 4.
16. Delete `tests/__init__.py` if the tests pass without it.

Done means: `pytest -q` passes, both scripts run with `--fake`, working tree has no stray files.
