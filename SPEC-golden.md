# SPEC: machine comparison of a waveform with a golden reference

Goal: a test decides PASS / FAIL by comparing the captured waveform with a reference taught from known-good
captures, instead of asking the operator. Method: a **mask** around the reference with a voltage tolerance and
a time tolerance.

**The real oscilloscope is NOT available to you.** Do not open `192.0.2.10` or any VISA resource; run the
scripts only with `--fake`. Read `AGENTS.md`, `README.md`, `examples/README.md`, `examples/operator_demo.py`,
`rigol_dho_plug.py`, `capture.py`, `fake_resource.py` first. Do not commit.

## Facts measured on the real scope (square wave 3 Vpp, 1 kHz, `captures/clock.yaml`, 1000 points)

- 21 captures: point-by-point deviation from the mean of 10 is at most 58 mV (rms 25 mV); Vpp repeats
  within 3.4 mV. One display step is 34 mV.
- A mask of +-100 mV and +-2 samples gave 0 false failures in 11 good captures and caught: amplitude -5 %,
  offset +80 mV, frequency +0.5 %, a single 50 us glitch, duty cycle 50 -> 55 %.
- Without a time tolerance one sample of edge jitter is an error of the full swing. The mask must widen in
  time around edges.
- A clipped capture (raw samples at 0 or 65535) looks plausible and is wrong. It must be refused.

## 1. `golden.py` (new; numpy, matplotlib only inside `plot`)

```python
@dataclass(frozen=True)
class Golden:
    t: np.ndarray            # seconds, float64
    ref: np.ndarray          # volts, mean of the taught captures
    lo: np.ndarray           # lower mask, volts
    hi: np.ndarray           # upper mask, volts
    v_tol: float             # volts, as used to build the mask
    t_tol: int               # samples, as used to build the mask
    n_taught: int
    capture_scpi: dict       # Capture.to_scpi() the captures were taken with, {} if unknown
    created: str             # ISO 8601, UTC
```

`teach(waveforms, *, v_tol=None, t_tol=2, margin=2.0, capture=None) -> Golden`
- `waveforms`: at least 3 `Waveform` objects with identical length and time axis, none clipped; otherwise
  `ValueError` that says which one and why.
- `ref` = point-wise mean. Envelope of the reference over a window of `2*t_tol+1` samples: running minimum
  and maximum, with the first and last sample repeated at the ends (**no wrap-around**, do not use `np.roll`).
- `v_tol=None` means automatic: `margin` times the largest distance by which any taught capture leaves that
  envelope, but never less than one display step (`256 * preamble['yincrement']` for WORD data, i.e. the
  8-bit screen resolution; `yincrement` for BYTE data). The tolerance comes from the spread of good captures.
- `lo = envelope_min - v_tol`, `hi = envelope_max + v_tol`.

`compare(waveform, golden) -> Comparison`
- Refuses, with `GoldenMismatch(ValueError)`: different number of points, time step differing by more than
  1e-6 relative, time origin differing by more than half a sample, clipped samples.
- `Comparison` (frozen dataclass): `violations: int` (samples outside the mask), `worst_excess: float`
  (largest distance outside the mask in volts, 0.0 when inside), `worst_deviation: float` (largest
  `|v - ref|`), `first_violation_s: float | None`, `passed: bool` (`violations == 0`), plus the arrays needed
  by `plot`.
- `Comparison.plot(title: str = '') -> bytes`: PNG, 900x420: the mask as a filled band, the reference as a
  thin line, the capture on top, violating samples marked in red, time in ms, volts. Use the `Agg` backend.

`Golden.save(path)` / `Golden.load(path)`: one `.npz` written with `np.savez_compressed`, loaded with
`allow_pickle=False` (strings and the dict stored as JSON text). Creates parent directories.
`load` raises `ValueError` naming the file if a field is missing.

## 2. `fake_resource.py`

Add a constructor argument `defect: str | None = None` so the fake can produce a "bad unit". Values:
`None` (unchanged behaviour), `'shift'` (samples delayed by 5), `'gain'` (amplitude scaled by 0.9 around
mid-scale), `'glitch'` (samples 500..504 forced to mid-scale). Existing behaviour and tests must not change.

## 3. `examples/golden_demo.py` (new)

Two subcommands, both accept `--fake`, `--resource NAME`, `--capture PATH` (default `captures/clock.yaml`),
`--golden PATH` (default `golden/clock.npz`).

- `teach [-n 10] [--v-tol VOLTS] [--t-tol SAMPLES]`: applies the capture file, takes `n` single captures,
  teaches, saves the golden file and `<golden>.png` (a picture of reference and mask), prints the tolerance
  it chose and the worst deviation seen. Returns the scope to its previous state.
- `run [--port 4444] [--once] [--show-failures] [--fake-defect shift|gain|glitch]`: an OpenHTF station with
  the web GUI like `operator_demo.py`. Phases:
  1. `capture_and_compare`: applies the capture file, single capture, `compare` with the golden file.
     Measurements: `mask_violations` must equal 0; `worst_excess` in volts, `worst_deviation` in volts,
     `vpp` in volts, `frequency` in hertz (informational). Attachments `comparison.png` and `waveform.npz`
     (raw + preamble, as in `operator_demo.py`).
  2. `show_failure`, only with `--show-failures` and only when the comparison failed: shows `comparison.png`
     to the operator with the numbers in the message; the operator only acknowledges (`text_input=False`).
     It records nothing and must not change the outcome.
  A missing golden file is an error before the station starts, telling the user to run `teach`.
- The phases must be importable and runnable without the web GUI and without a prompt, so that tests can
  execute `htf.Test(...).execute(test_start=lambda: 'dut')`.
- Reuse code from `operator_demo.py` where it is the same (frequency estimate, npz attachment): move it to a
  small shared module `examples/common.py` and import it from both demos, rather than copying it.

## 4. Tests (`tests/test_golden.py`, new)

Synthetic waveforms built in the test (square wave 0..3 V, 1 kHz, 1000 points, 10 us step, Gaussian noise
20 mV, seeded `np.random.default_rng(1)`), wrapped in the project's `Waveform` with a realistic preamble.

1. Teach on 10, compare 50 fresh good captures: 0 failures.
2. Each defect fails, with `violations > 0`: amplitude -5 %, offset +150 mV, frequency +0.5 %, one 50 us
   glitch, duty 55 %, inverted signal, flat line.
3. One sample of edge jitter passes with `t_tol=2` and fails with `t_tol=0`.
4. No wrap-around: a reference that is low at the start and high at the end keeps `lo[0]` low and `hi[-1]` high.
5. Automatic `v_tol` is at least one display step and scales with `margin`.
6. `teach` refuses: fewer than 3 captures, different lengths, different time steps, a clipped capture.
7. `compare` refuses: point count, time step, time origin, clipped capture (each with `GoldenMismatch`).
8. `save` / `load` round trip reproduces every field; the file loads with `allow_pickle=False`; a file with a
   missing field raises `ValueError`.
9. `Comparison.plot()` returns bytes that start with the PNG signature, for a passing and a failing comparison.
10. Fake defects: `FakeDhoResource(defect=...)` for each value differs from the default output.
11. Demo phases on the fake: teach through the fake, then a run with a good fake passes and a run with each
    `--fake-defect` fails, using `htf.Test(...).execute(test_start=lambda: 'dut')` without station server.
12. `python examples/golden_demo.py teach --fake --golden <tmp>/g.npz -n 5` exits 0 and writes both files.
13. All existing tests keep passing unmodified.

## 5. Documentation

- `examples/README.md`: a section for `golden_demo.py`: teach, run, what is measured, what the picture
  shows, the fake defects. At most 40 lines.
- `README.md`: a section "Comparing with a golden waveform" of at most 30 lines after the capture-file
  section: the idea, the two functions, how the tolerance is chosen, the measured facts above, and the
  conditions under which a golden file is valid (same capture file, working trigger, no clipping).
  Add `golden.py` to the file list.

## Done means

- `.venv/bin/python -m pytest -q -p no:cacheprovider` passes.
- `.venv/bin/python examples/golden_demo.py teach --fake --golden /tmp/scratch/g/clock.npz -n 5` works.
- `.venv/bin/python examples/operator_demo.py --help` still works (the refactoring into `examples/common.py`
  must not break it).
- No scratch files left; `git status --short` shows only: `golden.py`, `fake_resource.py`,
  `tests/test_golden.py`, `examples/golden_demo.py`, `examples/common.py`, `examples/operator_demo.py`,
  `examples/README.md`, `README.md`. Do not create or commit a `golden/` directory: the golden file for the
  real scope will be taught on the hardware by the project owner.

Report: files changed, the final pytest line verbatim, the output of the teach command, every place where
this spec was wrong or ambiguous or where you deviated and why, what is untested, and anything that looks like
a bug in existing code that you did not touch.
