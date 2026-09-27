"""Unit tests for rigol_dho_plug – no hardware."""

from __future__ import annotations

import numpy as np
import pytest

from fake_resource import FakeDhoResource
from rigol_dho_plug import RigolDhoPlug, Waveform


# ── helpers ──────────────────────────────────────────────────

def _plug(**kwargs):
    """Create a RigolDhoPlug backed by a FakeDhoResource."""
    fr = FakeDhoResource(**kwargs)
    return RigolDhoPlug(resource=fr)


# ── 1. idn() and measure() ──────────────────────────────────

def test_idn():
    p = _plug()
    assert p.idn() == 'RIGOL TECHNOLOGIES,DHO814,FAKE0000000001,00.01.02'


def test_measure_vpp():
    p = _plug()
    assert p.measure('VPP', 1) == pytest.approx(3.3)


# ── 2. preamble() types ─────────────────────────────────────

def test_preamble_types():
    p = _plug()
    pre = p.preamble()
    assert isinstance(pre['format'], int)
    assert isinstance(pre['type'], int)
    assert isinstance(pre['points'], int)
    assert pre['points'] == 1000
    assert isinstance(pre['count'], int)
    assert isinstance(pre['xincrement'], float)
    assert isinstance(pre['xorigin'], float)
    assert isinstance(pre['xreference'], float)
    assert isinstance(pre['yincrement'], float)
    assert isinstance(pre['yorigin'], float)
    assert isinstance(pre['yreference'], float)
    assert pre['format'] == 0  # BYTE


# ── 3. read_waveform NORM/BYTE ──────────────────────────────

def test_read_waveform_byte():
    p = _plug()
    w = p.read_waveform(1)
    assert isinstance(w, Waveform)
    assert len(w.t) == 1000
    assert len(w.v) == 1000
    assert len(w.raw) == 1000
    assert w.raw[5] == 5
    assert w.v[5] == pytest.approx((5 - 127) * 0.04)
    assert w.t[0] == pytest.approx(-5e-4)
    assert w.t[1] - w.t[0] == pytest.approx(1e-6)


# ── 4. WORD format ──────────────────────────────────────────

def test_read_waveform_word():
    p = _plug()
    w = p.read_waveform(1, fmt='WORD')
    assert w.raw.dtype == np.uint16
    assert len(w.raw) == 1000
    # sample[0] = (0 * 257) % 65536 = 0
    expected_v0 = (0 - 32768) * 1.5625e-04
    assert w.v[0] == pytest.approx(expected_v0)


# ── 5. Chunking ─────────────────────────────────────────────

def test_chunking():
    p = _plug(points=2500)
    w = p.read_waveform(1, chunk_points=1000)
    log = p._resource.log
    star_stop = [l for l in log if l.startswith(':WAV:STAR ') or l.startswith(':WAV:STOP ')]
    assert star_stop == [
        ':WAV:STAR 1', ':WAV:STOP 1000',
        ':WAV:STAR 1001', ':WAV:STOP 2000',
        ':WAV:STAR 2001', ':WAV:STOP 2500',
    ]
    # raw should equal the full deterministic sequence
    expected_raw = np.array([i % 256 for i in range(2500)], dtype=np.uint8)
    np.testing.assert_array_equal(w.raw, expected_raw)


# ── 6. RAW while running raises ─────────────────────────────

def test_raw_while_running():
    p = _plug(status='RUN')
    with pytest.raises(RuntimeError, match='STOP'):
        p.read_waveform(1, mode='RAW')
    # after stop it should work
    p.stop()
    w = p.read_waveform(1, mode='RAW')
    assert len(w.raw) == 1000


# ── 7. Invalid params ───────────────────────────────────────

@pytest.mark.parametrize('channel', [0, 5, -1])
def test_invalid_channel(channel):
    p = _plug()
    with pytest.raises(ValueError):
        p.read_waveform(channel)


@pytest.mark.parametrize('mode', ['INVALID', 'norm', ''])
def test_invalid_mode(mode):
    p = _plug()
    with pytest.raises(ValueError):
        p.read_waveform(1, mode=mode)


@pytest.mark.parametrize('fmt', ['WORDS', 'ascii', ''])
def test_invalid_fmt(fmt):
    p = _plug()
    with pytest.raises(ValueError):
        p.read_waveform(1, fmt=fmt)


# ── 8. tearDown() ───────────────────────────────────────────

def test_teardown():
    fr = FakeDhoResource()
    p = RigolDhoPlug(resource=fr)
    assert not fr.closed
    p.tearDown()
    assert fr.closed


# ── 9. OpenHTF integration ──────────────────────────────────

def test_openhtf_integration():
    import openhtf as htf

    class ScopePlug(RigolDhoPlug):
        def __init__(self):
            fr = FakeDhoResource()
            super().__init__(resource=fr)

    @htf.plug(scope=ScopePlug)
    @htf.measures('vpp')
    def measure_vpp(test, scope):
        vpp = scope.measure('VPP', 1)
        test.measurements['vpp'] = vpp

    test = htf.Test(measure_vpp)
    result = test.execute(test_start=lambda: 'dut1')
    assert result is True
