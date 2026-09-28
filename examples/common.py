"""Helpers shared by the example demos (operator_demo.py, golden_demo.py)."""
import io

import numpy as np


def frequency_hz(w) -> float:
    """Frequency from rising edges through the mid level, with hysteresis against noise on the edges."""
    lo, hi = np.percentile(w.v, 5), np.percentile(w.v, 95)
    if hi - lo < 1e-3:
        return 0.0
    low_thr, high_thr = lo + 0.3 * (hi - lo), lo + 0.7 * (hi - lo)
    edges, armed = [], False
    for t, v in zip(w.t, w.v):
        if v < low_thr:
            armed = True
        elif v > high_thr and armed:
            edges.append(t)
            armed = False
    return float(1 / np.mean(np.diff(edges))) if len(edges) > 1 else 0.0


def waveform_npz_bytes(w) -> bytes:
    """Serialize raw samples + preamble (enough to rebuild volts and time, and 4x smaller than
    the float arrays) the way both demos attach `waveform.npz`."""
    buf = io.BytesIO()
    np.savez_compressed(buf, raw=w.raw, **w.preamble)
    return buf.getvalue()
