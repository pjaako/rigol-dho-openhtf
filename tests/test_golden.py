"""Tests for golden.py: teach/compare against a mask built from known-good captures, and the
golden_demo.py phases and CLI (fake scope only; see AGENTS.md, the real scope is off limits)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import openhtf as htf
import pytest

from rigol_dho_openhtf.capture import load_capture
from rigol_dho_openhtf.fake_resource import FakeDhoResource
from rigol_dho_openhtf.golden import Golden, GoldenMismatch, compare, teach
from rigol_dho_openhtf import RigolDhoPlug, Waveform

ROOT = Path(__file__).resolve().parent.parent
CLOCK_YAML = ROOT / 'captures' / 'clock.yaml'

# ── synthetic waveforms: square wave 0..3 V, 1 kHz, 1000 points, 10 us step, 20 mV Gaussian noise ──
#
# yincrement/yorigin/yreference below are chosen so a 0..3.15 V signal (the widest defect, offset
# +150 mV, stays well inside the representable range without clipping; this is a different,
# roomier preamble than fake_resource's, which is calibrated for its own default sawtooth).
# phase=0.25 keeps the square wave's edges away from sample 0 and sample 999, so mask edge effects
# at the (deliberately non-wrapping) ends of the record don't confuse the edge-jitter test.

N = 1000
DT = 10e-6
FREQ = 1000.0
DUTY = 0.5
AMP = 3.0
OFFSET = 0.0
SIGMA = 0.02
PHASE0 = 0.25
T = np.arange(N) * DT
PREAMBLE = {
    'format': 1, 'type': 0, 'points': N, 'count': 1,
    'xincrement': DT, 'xorigin': 0.0, 'xreference': 0.0,
    'yincrement': 1e-4, 'yorigin': 0.0, 'yreference': 32768.0,
}


def _base_square(n=N, dt=DT, freq=FREQ, duty=DUTY, amp=AMP, offset=OFFSET, phase=PHASE0):
    period = 1.0 / freq
    frac = np.mod(np.arange(n) * dt / period + phase, 1.0)
    return np.where(frac < duty, amp, 0.0) + offset


def _to_waveform(v, t=None, preamble=None):
    t = T if t is None else t
    preamble = dict(PREAMBLE if preamble is None else preamble)
    yinc, yor, yref = preamble['yincrement'], preamble['yorigin'], preamble['yreference']
    raw = np.clip(np.round(v / yinc + yor + yref), 1, 65534).astype(np.uint16)
    v_actual = (raw.astype(np.float64) - yor - yref) * yinc
    return Waveform(t=t.copy(), v=v_actual, raw=raw, preamble=preamble)


def _good_capture(rng):
    return _to_waveform(_base_square() + rng.normal(0, SIGMA, N))


def _taught(rng, n=10, **teach_kwargs):
    return teach([_good_capture(rng) for _ in range(n)], **teach_kwargs)


# ── 1. teach on 10, compare 50 fresh good captures: 0 failures ─────────────

def test_fresh_good_captures_all_pass():
    rng = np.random.default_rng(1)
    g = _taught(rng)
    failures = sum(not compare(_good_capture(rng), g).passed for _ in range(50))
    assert failures == 0


# ── 2. each defect fails ────────────────────────────────────────────────────

def _glitch_signal():
    v = _base_square().copy()
    v[500:505] = AMP / 2  # 50 us at a 10 us step
    return v


_DEFECT_SIGNALS = {
    'amplitude -5%': _base_square(amp=AMP * 0.95),
    'offset +150 mV': _base_square(offset=0.15),
    'frequency +0.5%': _base_square(freq=FREQ * 1.005),
    'glitch 50 us': _glitch_signal(),
    'duty 55%': _base_square(duty=0.55),
    'inverted': AMP - _base_square(),
    'flat line': np.full(N, AMP / 2),
}


@pytest.mark.parametrize('name', list(_DEFECT_SIGNALS))
def test_each_defect_fails(name):
    rng = np.random.default_rng(1)
    g = _taught(rng)
    w = _to_waveform(_DEFECT_SIGNALS[name] + rng.normal(0, SIGMA, N))
    c = compare(w, g)
    assert c.violations > 0
    assert not c.passed


# ── 3. edge jitter needs a time tolerance ───────────────────────────────────

def test_edge_jitter_passes_with_t_tol_2_fails_with_t_tol_0():
    rng = np.random.default_rng(1)
    waveforms = [_good_capture(rng) for _ in range(10)]
    g2 = teach(waveforms, t_tol=2)
    g0 = teach(waveforms, t_tol=0)

    period_samples = round((1.0 / FREQ) / DT)
    one_sample = 1.0 / period_samples
    jittered = _to_waveform(_base_square(phase=PHASE0 + one_sample) + rng.normal(0, SIGMA, N))

    assert compare(jittered, g2).passed
    assert not compare(jittered, g0).passed


# ── 4. no wrap-around ───────────────────────────────────────────────────────

def test_envelope_does_not_wrap_around_the_ends():
    n = 20
    t = np.arange(n) * DT
    v = np.zeros(n)
    v[0] = -5.0
    v[-1] = 5.0
    preamble = dict(PREAMBLE, points=n)
    waveforms = [_to_waveform(v, t=t, preamble=preamble) for _ in range(3)]

    g = teach(waveforms, t_tol=2)
    assert g.lo[0] < -1.0    # stays low
    assert g.hi[-1] > 1.0    # stays high
    assert g.hi[0] < 1.0     # not contaminated by the +5 V at the far end
    assert g.lo[-1] > -1.0   # not contaminated by the -5 V at the far start


# ── 5. automatic v_tol has a floor and scales with margin ───────────────────

def test_automatic_v_tol_floor_and_margin_scaling():
    display_step = 256 * PREAMBLE['yincrement']

    w = _good_capture(np.random.default_rng(1))
    g_identical = teach([w, w, w], t_tol=2)  # zero spread: hits the floor
    assert g_identical.v_tol == pytest.approx(display_step)

    rng = np.random.default_rng(1)
    varied = [_good_capture(rng) for _ in range(10)]
    g1 = teach(varied, t_tol=2, margin=1.0)
    g4 = teach(varied, t_tol=2, margin=4.0)
    assert g1.v_tol > display_step
    assert g4.v_tol == pytest.approx(4 * g1.v_tol)


# ── 6. teach refuses ─────────────────────────────────────────────────────

def test_teach_refuses_fewer_than_3():
    rng = np.random.default_rng(1)
    with pytest.raises(ValueError, match='at least 3'):
        teach([_good_capture(rng), _good_capture(rng)])


def test_teach_refuses_different_lengths():
    rng = np.random.default_rng(1)
    good = [_good_capture(rng) for _ in range(3)]
    short = good[0]._replace(t=good[0].t[:-1], v=good[0].v[:-1], raw=good[0].raw[:-1])
    with pytest.raises(ValueError, match='points'):
        teach([short, good[1], good[2]])


def test_teach_refuses_different_time_steps():
    rng = np.random.default_rng(1)
    good = [_good_capture(rng) for _ in range(3)]
    different_dt = good[0]._replace(t=good[0].t * 1.1)
    with pytest.raises(ValueError, match='time axis'):
        teach([different_dt, good[1], good[2]])


def test_teach_refuses_a_clipped_capture():
    rng = np.random.default_rng(1)
    good = [_good_capture(rng) for _ in range(3)]
    clipped = good[0]._replace(raw=np.array(good[0].raw).copy())
    clipped.raw[0] = 0
    with pytest.raises(ValueError, match='clipped'):
        teach([clipped, good[1], good[2]])


# ── 7. compare refuses ───────────────────────────────────────────────────

def test_compare_refuses_different_point_count():
    rng = np.random.default_rng(1)
    g = _taught(rng)
    w = _good_capture(rng)
    short = w._replace(t=w.t[:-1], v=w.v[:-1], raw=w.raw[:-1])
    with pytest.raises(GoldenMismatch, match='point count'):
        compare(short, g)


def test_compare_refuses_different_time_step():
    rng = np.random.default_rng(1)
    g = _taught(rng)
    w = _good_capture(rng)
    stretched = w._replace(t=w.t * 1.1)
    with pytest.raises(GoldenMismatch, match='time step'):
        compare(stretched, g)


def test_compare_refuses_different_time_origin():
    rng = np.random.default_rng(1)
    g = _taught(rng)
    w = _good_capture(rng)
    shifted = w._replace(t=w.t + 5 * DT)
    with pytest.raises(GoldenMismatch, match='time origin'):
        compare(shifted, g)


def test_compare_refuses_a_clipped_capture():
    rng = np.random.default_rng(1)
    g = _taught(rng)
    w = _good_capture(rng)
    raw = np.array(w.raw).copy()
    raw[0] = 65535
    clipped = w._replace(raw=raw)
    with pytest.raises(GoldenMismatch, match='clipped'):
        compare(clipped, g)


# ── 8. save / load round trip ────────────────────────────────────────────

def test_save_load_round_trip(tmp_path):
    rng = np.random.default_rng(1)
    capture = load_capture(CLOCK_YAML)
    g = _taught(rng, capture=capture)

    path = tmp_path / 'sub' / 'clock.npz'
    g.save(path)
    assert path.exists()

    loaded = Golden.load(path)
    np.testing.assert_array_equal(loaded.t, g.t)
    np.testing.assert_array_equal(loaded.ref, g.ref)
    np.testing.assert_array_equal(loaded.lo, g.lo)
    np.testing.assert_array_equal(loaded.hi, g.hi)
    assert loaded.v_tol == pytest.approx(g.v_tol)
    assert loaded.t_tol == g.t_tol
    assert loaded.n_taught == g.n_taught
    assert loaded.capture_scpi == g.capture_scpi
    assert loaded.created == g.created

    with np.load(path, allow_pickle=False) as d:
        assert set(d.files) >= {'t', 'ref', 'lo', 'hi', 'v_tol', 't_tol', 'n_taught',
                                 'capture_scpi', 'created'}


def test_load_missing_field_raises(tmp_path):
    path = tmp_path / 'broken.npz'
    np.savez_compressed(
        path, t=np.zeros(3), ref=np.zeros(3), lo=np.zeros(3), hi=np.zeros(3),
        v_tol=np.array(0.1), t_tol=np.array(2), n_taught=np.array(3),
        created=np.array('2026-01-01T00:00:00+00:00'),
    )  # capture_scpi missing
    with pytest.raises(ValueError) as exc_info:
        Golden.load(path)
    assert str(path) in str(exc_info.value)


# ── 9. Comparison.plot() ─────────────────────────────────────────────────

def test_plot_returns_png_bytes_for_pass_and_fail():
    rng = np.random.default_rng(1)
    g = _taught(rng)
    passing = compare(_good_capture(rng), g)
    failing = compare(_to_waveform(np.full(N, AMP / 2) + rng.normal(0, SIGMA, N)), g)
    assert passing.passed
    assert not failing.passed
    for c in (passing, failing):
        png = c.plot('title')
        assert png[:8] == b'\x89PNG\r\n\x1a\n'


# ── 10. fake defects differ from the default output ─────────────────────

def _fake_raw(defect):
    fr = FakeDhoResource(defect=defect)
    return RigolDhoPlug(resource=fr, restore_state=False).read_waveform(1).raw


@pytest.mark.parametrize('defect', ['shift', 'gain', 'glitch'])
def test_fake_defect_differs_from_default(defect):
    assert not np.array_equal(_fake_raw(None), _fake_raw(defect))


def test_fake_resource_rejects_unknown_defect_or_signal():
    with pytest.raises(ValueError):
        FakeDhoResource(defect='nonsense')
    with pytest.raises(ValueError):
        FakeDhoResource(signal='nonsense')


# ── 11. demo phases on the fake ──────────────────────────────────────────

import golden_demo  # noqa: E402  (examples/ is on pytest's pythonpath, see pyproject.toml)


def test_demo_teach_then_run_on_the_fake(tmp_path):
    golden_path = tmp_path / 'clock.npz'
    capture = load_capture(CLOCK_YAML)
    scope = golden_demo.make_fake_scope_plug()()
    try:
        scope.apply_capture(capture)
        waveforms = []
        for _ in range(10):
            scope.single()
            scope.wait_stopped(timeout_s=5)
            waveforms.append(scope.read_waveform(1))
        scope.run()
    finally:
        scope.tearDown()

    g = teach(waveforms, capture=capture)
    g.save(golden_path)

    good_phase = golden_demo.capture_and_compare_phase(str(golden_path), str(CLOCK_YAML)) \
        .with_plugs(scope=golden_demo.make_fake_scope_plug(None))
    assert htf.Test(good_phase, test_name='good').execute(test_start=lambda: 'dut') is True

    for defect in ('shift', 'gain', 'glitch'):
        bad_phase = golden_demo.capture_and_compare_phase(str(golden_path), str(CLOCK_YAML)) \
            .with_plugs(scope=golden_demo.make_fake_scope_plug(defect))
        result = htf.Test(bad_phase, test_name=defect).execute(test_start=lambda: 'dut')
        assert result is False, defect


def test_show_failure_skips_on_pass_and_prompts_on_fail():
    """show_failure_phase()'s function directly (no station server, no plug injection): SKIP and
    no prompt when the comparison passed; one acknowledge-only prompt, CONTINUE, when it failed.
    Called directly rather than through htf.Test because openhtf's UserInput plug cannot be
    substituted via with_plugs (it doesn't declare auto_placeholder) and, unanswered, would block
    the test for a very long time."""
    show_failure = golden_demo.show_failure_phase().func

    class _Passed:
        passed = True

    class _Failed:
        passed = False
        violations = 3
        worst_excess = 0.01
        worst_deviation = 0.02

    class _Prompts:
        def __init__(self):
            self.calls = []

        def prompt(self, message, text_input=False, timeout_s=None, cli_color='', image_url=None):
            self.calls.append((message, text_input, image_url))
            return ''

    class _Test:
        def __init__(self, state):
            self.state = state

    prompts = _Prompts()
    result = show_failure(_Test({'comparison': _Passed()}), prompts)
    assert result == htf.PhaseResult.SKIP
    assert prompts.calls == []

    prompts = _Prompts()
    png = b'\x89PNG\r\n\x1a\nrest'
    result = show_failure(_Test({'comparison': _Failed(), 'comparison_png': png}), prompts)
    assert result == htf.PhaseResult.CONTINUE
    assert len(prompts.calls) == 1
    message, text_input, image_url = prompts.calls[0]
    assert text_input is False
    assert image_url is not None


# ── 12. `golden_demo.py teach --fake` from the command line ─────────────

def test_teach_cli_runs_in_fake_mode(tmp_path):
    golden_path = tmp_path / 'clock.npz'
    r = subprocess.run(
        [sys.executable, 'examples/golden_demo.py', 'teach', '--fake',
         '--golden', str(golden_path), '-n', '5'],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert golden_path.exists()
    assert Path(str(golden_path) + '.png').exists()


# ── 13. existing tests keep passing unmodified: verified by the full test run ──
