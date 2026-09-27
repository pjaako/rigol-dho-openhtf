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
    w = p.read_waveform(1, fmt='BYTE')
    assert isinstance(w, Waveform)
    assert len(w.t) == 1000
    assert len(w.v) == 1000
    assert len(w.raw) == 1000
    assert w.raw[5] == 5
    assert w.v[5] == pytest.approx((5 - 58 - 128) * 0.04)
    assert w.t[0] == pytest.approx(-5e-4)
    assert w.t[1] - w.t[0] == pytest.approx(1e-6)


# ── 4. WORD format ──────────────────────────────────────────

def test_read_waveform_word():
    p = _plug()
    w = p.read_waveform(1, fmt='WORD')
    assert w.raw.dtype == np.uint16
    assert len(w.raw) == 1000
    # sample[0] = (0 * 257) % 65536 = 0
    expected_v0 = (0 - 15000 - 32768) * 1.5625e-04
    assert w.v[0] == pytest.approx(expected_v0)


# ── 5. Chunking (RAW mode with memory_depth > chunk_points) ──

def test_chunking():
    p = _plug(memory_depth=2500, status='STOP')
    w = p.read_waveform(1, mode='RAW', chunk_points=1000, fmt='BYTE')
    log = p._resource.log
    star_stop = [l for l in log if l.startswith(':WAV:STAR ') or l.startswith(':WAV:STOP ')]
    assert star_stop == [
        ':WAV:STAR 1', ':WAV:STOP 2500',
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
    assert len(w.raw) == 10000


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


# ── 10. auto_placeholder for with_plugs ──────────────────────

def test_auto_placeholder():
    assert RigolDhoPlug.auto_placeholder is True


# ── 11. Fake trigger status delay ───────────────────────────

def test_fake_trigger_status_delay():
    fr = FakeDhoResource(status='READY')
    p = RigolDhoPlug(resource=fr)
    assert p.trigger_status() == 'READY'
    fr.set_status('RUN')
    assert p.trigger_status() == 'RUN'  # immediate when no delay


# ── 12. MDEP suffix handling ─────────────────────────────────

def test_mdep_k_suffix():
    fr = FakeDhoResource(memory_depth=5000)
    p = RigolDhoPlug(resource=fr)
    p.set_memory_depth('5K')
    assert p.memory_depth() == 5000


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


# ── added in review: discovery, RAW windows, runnable scripts ──

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class _StubRM:
    def __init__(self, names):
        self.names, self.opened, self.closed = names, None, False

    def list_resources(self, pattern):
        return tuple(self.names)

    def open_resource(self, name):
        self.opened = name
        return FakeDhoResource()

    def close(self):
        self.closed = True


@pytest.mark.parametrize('name', [
    'USB0::6833::1101::DHO8A000000000::0::INSTR',
    'USB0::0x1AB1::0x044D::DHO8A000000000::INSTR',
])
def test_discovery_matches_decimal_and_hex_vendor(monkeypatch, name):
    import pyvisa
    rm = _StubRM(['USB0::1234::1::OTHER::0::INSTR', name])
    monkeypatch.setattr(pyvisa, 'ResourceManager', lambda backend: rm)
    plug = RigolDhoPlug()
    assert rm.opened == name
    plug.tearDown()
    assert rm.closed


def test_discovery_no_scope(monkeypatch):
    import pyvisa
    monkeypatch.setattr(pyvisa, 'ResourceManager', lambda backend: _StubRM(['USB0::1234::1::X::0::INSTR']))
    with pytest.raises(RuntimeError):
        RigolDhoPlug()


def test_raw_default_is_one_window_of_memory_depth():
    fr = FakeDhoResource(status='STOP', memory_depth=2500)
    w = RigolDhoPlug(resource=fr).read_waveform(1, mode='RAW', fmt='BYTE')
    assert len(w.raw) == 2500
    assert fr.log.count(':WAV:DATA?') == 1
    assert ':WAV:STOP 2500' in fr.log
    np.testing.assert_array_equal(w.raw, np.arange(2500) % 256)


def test_raw_chunked_windows():
    fr = FakeDhoResource(status='STOP', memory_depth=2500)
    w = RigolDhoPlug(resource=fr).read_waveform(1, mode='RAW', chunk_points=1000, fmt='BYTE')
    windows = [c for c in fr.log if c.startswith((':WAV:STAR', ':WAV:STOP'))][2:]
    assert windows == [':WAV:STAR 1', ':WAV:STOP 1000', ':WAV:STAR 1001', ':WAV:STOP 2000',
                       ':WAV:STAR 2001', ':WAV:STOP 2500']
    np.testing.assert_array_equal(w.raw, np.arange(2500) % 256)


def test_yorigin_is_applied():
    """Channel offset arrives as a non-zero yorigin and must be subtracted."""
    w = _plug().read_waveform(1, fmt='BYTE')
    assert w.preamble['yorigin'] == 58
    assert w.v[58 + 128] == pytest.approx(0.0)


def test_bench_runs_in_fake_mode(tmp_path):
    r = subprocess.run([sys.executable, 'bench_usb.py', '--fake', '--repeats', '3', '--depths', '10k',
                        '--chunks', 'all,4000', '--out', str(tmp_path)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert 'norm_byte' in r.stdout and 'raw_10k_BYTE_4000' in r.stdout and 'ERROR' not in r.stdout
    assert isinstance(json.loads((tmp_path / 'bench.json').read_text()), list)


def test_example_runs_in_fake_mode():
    r = subprocess.run([sys.executable, 'example_test.py', '--fake'], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert 'num_points=1000' in r.stdout


def test_repeated_reads_skip_reconfiguration_and_can_reuse_preamble():
    fr = FakeDhoResource()
    plug = RigolDhoPlug(resource=fr)
    w1 = plug.read_waveform(1)
    fr.log.clear()
    w2 = plug.read_waveform(1, preamble=w1.preamble)
    assert fr.log == [':WAV:DATA?']
    np.testing.assert_array_equal(w1.v, w2.v)
    fr.log.clear()
    plug.read_waveform(2)                      # different channel: full setup again
    assert ':WAV:SOUR CHAN2' in fr.log and ':WAV:PRE?' in fr.log
    plug.write(':WAV:FORM BYTE')               # raw command invalidates the cache
    fr.log.clear()
    plug.read_waveform(2)
    assert ':WAV:FORM WORD' in fr.log


def test_default_format_is_16_bit():
    fr = FakeDhoResource()
    w = RigolDhoPlug(resource=fr).read_waveform(1)
    assert ':WAV:FORM WORD' in fr.log
    assert w.raw.dtype == np.uint16
    assert w.preamble['format'] == 1


def test_raw_is_refused_while_recording_is_enabled():
    fr = FakeDhoResource(status='STOP')
    plug = RigolDhoPlug(resource=fr)
    plug.write(':REC:WREC:ENAB ON')
    with pytest.raises(RuntimeError, match='recording'):
        plug.read_waveform(1, mode='RAW')
    assert len(plug.read_waveform(1, mode='NORM').raw) == 1000      # frames stay readable in NORM
    plug.write(':REC:WREC:ENAB OFF')
    assert len(plug.read_waveform(1, mode='RAW').raw) == 10000
