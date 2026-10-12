# SPEC-golden-ends: no time tolerance exists at the record ends, so compare must not judge them

Change to `src/rigol_dho_openhtf/golden.py` and `tests/test_golden.py` only. Read `AGENTS.md` first. No hardware.

## Fact, measured 2026-10-12 (generator square wave 1 kHz 0..3 V, `captures/clock.yaml`, 1000 points, 10 us/sample)

The reference's last rising edge lies exactly on the record boundary (at +5 ms, one sample past the end).
`_rolling_extreme` cannot widen the envelope with samples that do not exist, so at the last `t_tol` samples
the mask has the voltage tolerance only. Every capture whose final edge arrived 1 or 2 samples early failed
with exactly one violation at t = 4.99 ms and an excess of ~2.95 V: frequency +0.05 % and +0.1 % (2 of 2
each, although their edges inside the record moved by only 2 samples and were absorbed by `t_tol=2`), and a
pulse with 20 us edges (2 of 2, with the edges inside the record on the reference positions). The same
happens at the first `t_tol` samples for an edge arriving late there.

## Change

- `compare()` ignores the first `golden.t_tol` and the last `golden.t_tol` samples: `violation_mask` is
  `False` there whatever the values, and `violations`, `worst_excess`, `first_violation_s` and `passed` are
  computed from the remaining samples. `worst_deviation` stays over all samples (it is informational).
  With `t_tol == 0` nothing is ignored.
- `Comparison` gains `ignored_samples: int` (`2 * t_tol`, capped so it never exceeds the point count).
- Docstrings of `teach`/`compare` and the module docstring say why in one sentence each (the fact above).
- `Golden.load()` of an existing file keeps working (no new fields in the file).

## Tests (`tests/test_golden.py`, keep all existing ones)

1. A golden taught from square waves whose last edge is at the record boundary; a capture with that final
   edge 2 samples early passes with `t_tol=2`, and `ignored_samples == 4`; the same capture fails against a
   golden taught with `t_tol=0`.
2. A capture with a real defect inside the record (one sample 1 V off at index 500) still fails.
3. A defect inside the first or last `t_tol` samples is not reported (documented limit): a 1 V excursion at
   index 0 with `t_tol=2` passes; at index 2 it fails.
4. `worst_deviation` still reflects a deviation at the ignored ends.
5. A golden with `t_tol` larger than half the record (e.g. 3 points, `t_tol=2`) raises `ValueError` in
   `teach`: say "t_tol must be smaller than half the record".

## Done means

`uv run pytest -q` passes, `uv run python examples/golden_demo.py teach --fake -n 5 --golden /tmp/g.npz` works
(delete `/tmp/g.npz` after), mypy on `capture.py` unchanged. Report files changed and test count.
