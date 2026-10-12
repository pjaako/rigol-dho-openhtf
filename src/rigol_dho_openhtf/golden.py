"""Compare a captured waveform with a golden reference taught from known-good captures.

`teach()` builds a `Golden` (reference + voltage/time mask) from at least three good captures.
`compare()` checks a new capture against it and reports where and how far it strayed. Both refuse
clipped input (see README "Comparing with a golden waveform"). numpy is used throughout; matplotlib
is only imported inside `Comparison.plot()`.

The first and last `t_tol` samples are not judged: the envelope cannot be widened with samples
beyond the record, so there the mask has the voltage tolerance only (measured 2026-10-12: a final
edge 1-2 samples early failed with ~2.95 V excess although everything inside the record agreed).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from rigol_dho_openhtf.plug import Waveform


class GoldenMismatch(ValueError):
    """Raised by `compare()` when a waveform cannot be compared against a golden reference."""


@dataclass(frozen=True)
class Golden:
    """A taught reference waveform plus the mask built around it."""
    t: np.ndarray            # seconds, float64
    ref: np.ndarray          # volts, mean of the taught captures
    lo: np.ndarray           # lower mask, volts
    hi: np.ndarray           # upper mask, volts
    v_tol: float             # volts, as used to build the mask
    t_tol: int                # samples, as used to build the mask
    n_taught: int
    capture_scpi: dict       # Capture.to_scpi() the captures were taken with, {} if unknown
    created: str             # ISO 8601, UTC

    def save(self, path: str | Path) -> None:
        """Write one `.npz` (np.savez_compressed); strings and the dict are stored as JSON text
        so the file loads back with `allow_pickle=False`."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            t=self.t, ref=self.ref, lo=self.lo, hi=self.hi,
            v_tol=np.array(self.v_tol, dtype=np.float64),
            t_tol=np.array(self.t_tol, dtype=np.int64),
            n_taught=np.array(self.n_taught, dtype=np.int64),
            capture_scpi=np.array(json.dumps(self.capture_scpi)),
            created=np.array(self.created),
        )

    @staticmethod
    def load(path: str | Path) -> 'Golden':
        path = Path(path)
        required = ('t', 'ref', 'lo', 'hi', 'v_tol', 't_tol', 'n_taught', 'capture_scpi', 'created')
        with np.load(path, allow_pickle=False) as d:
            missing = [name for name in required if name not in d.files]
            if missing:
                raise ValueError(f'{path}: missing field(s) {missing}')
            return Golden(
                t=d['t'], ref=d['ref'], lo=d['lo'], hi=d['hi'],
                v_tol=float(d['v_tol']), t_tol=int(d['t_tol']), n_taught=int(d['n_taught']),
                capture_scpi=json.loads(str(d['capture_scpi'])),
                created=str(d['created']),
            )


@dataclass(frozen=True)
class Comparison:
    """Result of comparing one waveform against a `Golden`."""
    violations: int                    # samples outside the mask
    worst_excess: float                # largest distance outside the mask, volts; 0.0 when inside
    worst_deviation: float             # largest |v - ref|, volts
    first_violation_s: float | None    # time of the first violating sample, or None
    passed: bool                       # violations == 0
    t: np.ndarray
    v: np.ndarray
    ref: np.ndarray
    lo: np.ndarray
    hi: np.ndarray
    violation_mask: np.ndarray         # bool, True where the sample is outside the mask
    ignored_samples: int = 0           # samples at the two ends that were not judged (2 * t_tol)

    def plot(self, title: str = '') -> bytes:
        """Render a 900x420 PNG: the mask as a filled band, the reference as a thin line, the
        capture on top, violating samples marked in red. Time in ms, volts."""
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(9, 4.2), dpi=100)
        t_ms = self.t * 1e3
        ax.fill_between(t_ms, self.lo, self.hi, color='#a8c8f0', alpha=0.6, linewidth=0, label='mask')
        ax.plot(t_ms, self.ref, color='#606060', linewidth=0.8, label='reference')
        ax.plot(t_ms, self.v, color='#c8a000', linewidth=1.0, label='capture')
        if self.violation_mask.any():
            ax.plot(t_ms[self.violation_mask], self.v[self.violation_mask], '.', color='red',
                    markersize=4, label='violation')
        ax.set_xlabel('time, ms')
        ax.set_ylabel('voltage, V')
        if title:
            ax.set_title(title)
        ax.grid(True, alpha=0.4)
        ax.margins(x=0)
        ax.legend(loc='upper right', fontsize=8)
        fig.tight_layout()
        import io
        buf = io.BytesIO()
        fig.savefig(buf, format='png')
        plt.close(fig)
        return buf.getvalue()


def _max_code(raw: np.ndarray) -> int:
    return 255 if raw.dtype == np.uint8 else 65535


def _is_clipped(w: Waveform) -> bool:
    """A capture is clipped if any raw sample sits at 0 or at the top code (README: 'Clipped data
    lies')."""
    maxcode = _max_code(w.raw)
    return bool(w.raw.min() <= 0 or w.raw.max() >= maxcode)


def _display_step(preamble: dict) -> float:
    """One display step in volts: the scope's screen shows 8 bits regardless of `fmt`."""
    if preamble.get('format') == 1:  # WORD
        return 256 * preamble['yincrement']
    return float(preamble['yincrement'])  # BYTE


def _rolling_extreme(x: np.ndarray, half: int, op: Callable[..., np.ndarray]) -> np.ndarray:
    """Sliding-window min/max over a window of `2*half+1` samples. Edges are padded by repeating
    the first/last sample (no wrap-around; `np.roll` would wrap and was explicitly ruled out)."""
    if half <= 0:
        return x.copy()
    padded = np.pad(x, (half, half), mode='edge')
    n = len(x)
    stacked = np.stack([padded[i:i + n] for i in range(2 * half + 1)])
    return op(stacked, axis=0)


def teach(waveforms: list[Waveform], *, v_tol: float | None = None, t_tol: int = 2,
          margin: float = 2.0, capture=None) -> Golden:
    """Teach a `Golden` from at least three known-good captures.

    `v_tol=None` picks the tolerance automatically: `margin` times the largest distance by which
    any taught capture leaves the reference's envelope, but never less than one display step.

    `t_tol` must be smaller than half the record: `compare()` does not judge the first and last
    `t_tol` samples, because the envelope there cannot be widened with samples that do not exist.
    """
    waveforms = list(waveforms)
    if len(waveforms) < 3:
        raise ValueError(f'teach needs at least 3 waveforms, got {len(waveforms)}')

    n = len(waveforms[0].t)
    if 2 * t_tol >= n:
        raise ValueError(f't_tol must be smaller than half the record ({t_tol} samples, {n} points)')
    t0 = waveforms[0].t
    for i, w in enumerate(waveforms):
        if len(w.t) != n:
            raise ValueError(f'waveform {i} has {len(w.t)} points, expected {n} (from waveform 0)')
        if not np.array_equal(w.t, t0):
            raise ValueError(f'waveform {i} has a different time axis than waveform 0')
        if _is_clipped(w):
            raise ValueError(f'waveform {i} is clipped (raw samples touch 0 or the top code)')

    v_stack = np.stack([w.v for w in waveforms])
    ref = v_stack.mean(axis=0)
    env_min = _rolling_extreme(ref, t_tol, np.min)
    env_max = _rolling_extreme(ref, t_tol, np.max)

    if v_tol is None:
        excess = np.maximum(env_min - v_stack, v_stack - env_max)
        spread = float(np.maximum(excess, 0.0).max())
        v_tol = max(margin * spread, _display_step(waveforms[0].preamble))
    lo = env_min - v_tol
    hi = env_max + v_tol

    return Golden(
        t=t0.copy(), ref=ref, lo=lo, hi=hi, v_tol=float(v_tol), t_tol=int(t_tol),
        n_taught=len(waveforms),
        capture_scpi=capture.to_scpi() if capture is not None else {},
        created=datetime.now(timezone.utc).isoformat(),
    )


def compare(waveform: Waveform, golden: Golden) -> Comparison:
    """Compare `waveform` against `golden`. Refuses incompatible or clipped input with
    `GoldenMismatch` instead of returning a result.

    The first and last `golden.t_tol` samples are not judged (`violation_mask` is False there):
    the mask cannot be widened past the record ends, so an edge arriving a sample or two early
    at the end failed with ~2.95 V excess although the rest of the record agreed. A defect that
    lies only in those samples goes unnoticed. `worst_deviation` still covers all samples."""
    if len(waveform.t) != len(golden.t):
        raise GoldenMismatch(
            f'point count differs: waveform has {len(waveform.t)}, golden has {len(golden.t)}'
        )

    dt_w = waveform.t[1] - waveform.t[0] if len(waveform.t) > 1 else 0.0
    dt_g = golden.t[1] - golden.t[0] if len(golden.t) > 1 else 0.0
    if dt_g and abs(dt_w - dt_g) > 1e-6 * abs(dt_g):
        raise GoldenMismatch(f'time step differs: waveform {dt_w:g} s, golden {dt_g:g} s')

    if abs(waveform.t[0] - golden.t[0]) > 0.5 * abs(dt_g):
        raise GoldenMismatch(
            f'time origin differs: waveform {waveform.t[0]:g} s, golden {golden.t[0]:g} s'
        )

    if _is_clipped(waveform):
        raise GoldenMismatch('capture is clipped (raw samples touch 0 or the top code)')

    v = waveform.v
    below = golden.lo - v
    above = v - golden.hi
    excess = np.maximum(below, above)
    violation_mask = excess > 0
    ignored = min(2 * golden.t_tol, len(v))
    if golden.t_tol > 0:
        violation_mask[:golden.t_tol] = False
        violation_mask[len(v) - golden.t_tol:] = False
    violations = int(violation_mask.sum())
    worst_excess = float(np.maximum(excess, 0.0)[violation_mask].max()) if violations else 0.0
    worst_deviation = float(np.max(np.abs(v - golden.ref)))
    first_violation_s = float(waveform.t[violation_mask][0]) if violations else None

    return Comparison(
        violations=violations, worst_excess=worst_excess, worst_deviation=worst_deviation,
        first_violation_s=first_violation_s, passed=(violations == 0),
        t=waveform.t, v=v, ref=golden.ref, lo=golden.lo, hi=golden.hi,
        violation_mask=violation_mask, ignored_samples=ignored,
    )
