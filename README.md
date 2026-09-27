# rigol-dho-openhtf

OpenHTF plug for Rigol DHO800/DHO900 oscilloscopes over USB (USBTMC) or LAN, built on PyVISA + pyvisa-py.
Developed and measured on a DHO814, firmware 00.01.05, Debian 13.

```python
import openhtf as htf
from rigol_dho_plug import RigolDhoPlug

@htf.measures(htf.Measurement('vpp'))
@htf.plug(scope=RigolDhoPlug)
def phase(test, scope):
    w = scope.read_waveform(1)              # screen data, 1000 points, 16-bit; volts in w.v, seconds in w.t
    test.measurements.vpp = float(w.v.max() - w.v.min())
```

Deep memory: `scope.set_memory_depth('1M'); scope.single()` … wait for `trigger_status() == 'STOP'` …
`scope.read_waveform(1, mode='RAW')`.

## Setup

```bash
uv venv .venv && uv pip install --python .venv/bin/python openhtf pyvisa pyvisa-py pyusb numpy pytest
sudo cp udev/99-rigol-dho.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules \
  && sudo udevadm trigger --attr-match=idVendor=1ab1      # user must be in group plugdev
.venv/bin/python -m pytest -q                              # 30 tests, no hardware needed
.venv/bin/python example_test.py [--fake]
.venv/bin/python bench_usb.py [--fake] --depths 10k,1M,10M,25M --chunks all
```

The resource is auto-discovered (first USB instrument with vendor 0x1AB1); override with the OpenHTF
config key `rigol_dho_resource`, e.g. `TCPIP::192.168.1.50::INSTR`.

## How fast is USB (measured 2026-09-27)

| What | Points | Time | Rate |
|---|---|---|---|
| Any query with a reply (`*IDN?`, `:TRIG:STAT?`) | – | 22–24 ms | ~41 queries/s |
| A command without a reply | – | 0.03 ms | – |
| `read_waveform` NORM, reusing the preamble | 1 000 | 24 ms | 42 waveforms/s |
| `read_waveform` NORM, fresh preamble each time | 1 000 | 48 ms | 21 waveforms/s |
| `read_waveform` RAW BYTE | 10 000 | 0.11 s | fixed overhead dominates |
| `read_waveform` RAW BYTE | 1 000 000 | 0.21 s | 4.9 Mpts/s |
| `read_waveform` RAW BYTE | 10 000 000 | 1.01 s | 9.9 Mpts/s, 9.5 MiB/s |
| `read_waveform` RAW BYTE | 25 000 000 | 2.28 s | 11.0 Mpts/s, 10.5 MiB/s |
| `read_waveform` RAW WORD | 25 000 000 | 4.03 s | 6.2 Mpts/s, 11.8 MiB/s |
| RAW BYTE in 250k-point chunks | 25 000 000 | 7.21 s | 3.5 Mpts/s |

Two limits: a floor of ~24 ms per query and a ceiling of ~10-12 MiB/s. Read the whole record in one
query (`chunk_points=None`, the default); chunking only adds per-query latency.

**Samples are always 16-bit.** `read_waveform` defaults to `fmt='WORD'` (uint16); that is the project
convention. `fmt='BYTE'` still exists but throws away resolution for a 1.8x faster deep-memory transfer
(25 M points: 2.3 s instead of 4.0 s); screen waveforms cost the same 24 ms either way.
Full table: `bench_results/bench.json`.

## Storing waveforms: keep `raw` + `preamble`

A `Waveform` has `t` and `v` as float64 (8 bytes per sample each) and `raw` as uint16 (2 bytes per sample).
`t` and `v` are fully determined by `raw` and the ten preamble numbers, so keep only those in memory and on
disk and compute volts and time when needed:

| 25 M-point capture | Size |
|---|---|
| `raw` (uint16) + preamble | 50 MB |
| `v` (float64) | 200 MB |
| `t` + `v` + `raw` | 450 MB |

```python
np.savez_compressed('capture.npz', raw=w.raw, **w.preamble)          # store
d = np.load('capture.npz')                                            # restore
v = (d['raw'].astype(np.float64) - d['yorigin'] - d['yreference']) * d['yincrement']
t = (np.arange(len(v)) - d['xreference']) * d['xincrement'] + d['xorigin']
```

For a series of captures stack the `raw` arrays into one 2-D uint16 array with one preamble per capture (or one
for all, if scale, offset and timebase did not change).

## Things the manual does not tell you (firmware 00.01.05)

- **Volts:** the manual's formula `v = (raw - yorigin - yreference) * yincrement` is correct. Verified on the
  probe-compensation square wave (3 Vpp) at channel offsets 0, -1.5 and +1 V, BYTE and WORD: the plug's
  low/high levels and Vpp match the scope's own VBAS/VTOP/VPP within 1 LSB.
- **Clipped data lies.** When the trace leaves the screen the samples saturate at 0 / 255 and their mean sits
  near mid-scale whatever the offset. Check `raw.min() > 0 and raw.max() < 255` (65535 for WORD) before
  trusting a waveform.
- In RAW mode the preamble's `points` is the `:WAV:STAR`..`:WAV:STOP` window, not the memory depth.
- `:ACQ:MDEP` rejects the format `:ACQ:MDEP?` returns (`1.0000E+05`); `100k`, `1E5` and `100000` work.
- `:TRIG:STAT?` returns `TD` while running; only `STOP` means stopped.
- `:MEAS:ITEM?` returns `9.9E+37` on the first query of an item and when the trace is clipped.
- pyvisa-py names the device `USB0::6833::1101::<serial>::0::INSTR` (decimal vendor id).
- Re-sending `:WAV:SOUR/MODE/FORM` costs ~80 ms per waveform; the plug caches the last setup.

## Memory and frame recording (measured 2026-09-27, one channel on)

Memory depth: 1k, 10k, 100k, 1M, 10M, 25M points; 25M is the maximum (`50M` is clamped to 25M).

The scope can record a series of triggered frames and replay them (`:REC:WREC:*`, `:REC:WREP:*`):

| Memory depth | Max frames (`:REC:WREC:FMAX?`) |
|---|---|
| 1k | 61 440 |
| 10k | 7 680 |
| 100k | 960 |
| 1M | 120 |
| 10M | 15 |
| 25M | 6 |

Sequence: `:REC:WREC:ENAB ON`, `:REC:WREC:FRAM n`, `:REC:WREC:FINT seconds`, `:REC:WREC:OPER RUN`, poll
`:REC:WREC:OPER?` until `STOP`, then for each frame `:REC:WREP:FCUR k` and `read_waveform(ch, mode='NORM')`.

- NORM download of recorded frames works: 200 frames x 1000 points in 5.3 s (37.5 frames/s).
- **RAW does not follow the selected frame**: every `:WAV:DATA?` in RAW mode returns the same record whatever
  `:REC:WREP:FCUR` is, also after a 1 s delay. Recorded frames are only available at screen resolution
  (1000 points, 8 bit). MAX mode returns no data during replay.
- For a series at full memory depth, loop `single()` / wait for `STOP` / `read_waveform(mode='RAW')` instead.

## Prior art checked

[pydho800](https://github.com/MasterJubei/pydho800) (BSD-3, LAN only, ASCII waveform transfer),
[rigol-dho-scpi](https://github.com/sendu2wfdx/rigol-dho-scpi) (no licence),
[TofuPilot's PyVISA + OpenHTF guide](https://www.tofupilot.com/guides/getting-started-with-pyvisa-control-test-instruments-from-python).
No OpenHTF plug for this scope family existed. Transport and IEEE-488.2 block parsing come from PyVISA.

## Files

`rigol_dho_plug.py` the plug · `fake_resource.py` hardware-free stand-in · `tests/` · `bench_usb.py` ·
`example_test.py` · `udev/` · `SPEC.md` the original spec (superseded where this README differs).
