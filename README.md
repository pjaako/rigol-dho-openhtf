# rigol-dho-openhtf

OpenHTF plug for Rigol DHO800/DHO900 oscilloscopes over USB (USBTMC) or LAN, built on PyVISA + pyvisa-py.
Developed and measured on a DHO814, firmware 00.01.05, Debian 13.

```python
import openhtf as htf
from rigol_dho_plug import RigolDhoPlug

@htf.measures(htf.Measurement('vpp'))
@htf.plug(scope=RigolDhoPlug)
def phase(test, scope):
    w = scope.read_waveform(1)              # screen data, 1000 points, volts in w.v, seconds in w.t
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

Two limits: a floor of ~24 ms per query and a ceiling of ~10–12 MiB/s. Read the whole record in one
query (`chunk_points=None`, the default); chunking only adds per-query latency. BYTE carries the same 8-bit
display data as WORD at half the bytes; WORD is only worth it when the 12-bit samples matter.
Full table: `bench_results/bench.json`.

## Things the manual does not tell you (firmware 00.01.05)

- **Volts:** `v = (raw - yreference) * yincrement`. The manual's formula also subtracts `yorigin`; doing so
  shifts the result by exactly the channel offset. Verified in NORM and RAW, BYTE and WORD.
  The gain (`yincrement`) has not been checked against a calibrated source yet.
- In RAW mode the preamble's `points` is the `:WAV:STAR`..`:WAV:STOP` window, not the memory depth.
- `:ACQ:MDEP` rejects the format `:ACQ:MDEP?` returns (`1.0000E+05`); `100k`, `1E5` and `100000` work.
- `:TRIG:STAT?` returns `TD` while running; only `STOP` means stopped.
- `:MEAS:ITEM?` returns `9.9E+37` on the first query of an item and when the trace is clipped.
- pyvisa-py names the device `USB0::6833::1101::<serial>::0::INSTR` (decimal vendor id).
- Re-sending `:WAV:SOUR/MODE/FORM` costs ~80 ms per waveform; the plug caches the last setup.

## Prior art checked

[pydho800](https://github.com/MasterJubei/pydho800) (BSD-3, LAN only, ASCII waveform transfer),
[rigol-dho-scpi](https://github.com/sendu2wfdx/rigol-dho-scpi) (no licence),
[TofuPilot's PyVISA + OpenHTF guide](https://www.tofupilot.com/guides/getting-started-with-pyvisa-control-test-instruments-from-python).
No OpenHTF plug for this scope family existed. Transport and IEEE-488.2 block parsing come from PyVISA.

## Files

`rigol_dho_plug.py` the plug · `fake_resource.py` hardware-free stand-in · `tests/` · `bench_usb.py` ·
`example_test.py` · `udev/` · `SPEC.md` the original spec (superseded where this README differs).
