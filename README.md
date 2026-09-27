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

## Ethernet (measured 2026-09-27, direct cable, 100 Mbit/s link)

Set the OpenHTF config key `rigol_dho_resource` to `TCPIP::192.0.2.10::INSTR` (VXI-11) or
`TCPIP::192.0.2.10::5555::SOCKET` (raw socket). The scope does not offer HiSLIP (port 4880 closed).
No code differs from USB; PyVISA provides all three transports.

| | USB | VXI-11 | Raw socket 5555 |
|---|---|---|---|
| Query latency (`*IDN?`) | 22-24 ms | 23 ms | 24 ms |
| Screen waveform, preamble reused | 24 ms (42/s) | 26 ms (37/s) | 28 ms (37/s) |
| RAW 1 M points, WORD | 0.29 s | 0.48 s | 0.50 s |
| RAW 10 M points, WORD | 1.7 s (11.3 MiB/s) | 3.3 s (5.7 MiB/s) | 3.0 s (6.5 MiB/s) |
| RAW 25 M points, WORD | 4.0 s (11.8 MiB/s) | 7.9 s (6.0 MiB/s) | 7.1 s (6.7 MiB/s) |

- The ~24 ms per query is the scope's own SCPI processing time: it is the same on every transport.
- Ethernet tops out at 6-7 MiB/s, about half of what the 100 Mbit/s link could carry and 1.8x slower than
  USB. USB is the fastest transport for this scope; Ethernet's advantages are cable length, galvanic
  isolation and sharing.
- Raw socket is ~10 % faster than VXI-11 on large transfers, equal otherwise.
- Check the negotiated link speed (`cat /sys/class/net/<if>/speed`). A bad cable negotiated 10 Mbit/s and
  capped transfers at 1 MiB/s (10 M points WORD in 18 s).
- Static addressing over SCPI: `:LAN:MAN ON`, then `:LAN:AUT OFF` (rejected while manual mode is off),
  `:LAN:DHCP OFF`, `:LAN:IPAD`, `:LAN:SMAS`, `:LAN:GAT`, `:LAN:APPL`. Auto IP wins over manual while it is on.

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

## Independent control measurement (2026-09-27)

The comparisons below used the plug over USB to set up the scope, which could itself have caused the common
results. `tools/bare_socket_check.py` repeats both checks with nothing but a stdlib TCP socket in a fresh
process: no PyVISA, no plug, no open USB session.

- Query latency: 21.3 ms minimum, 22-24 ms median, identical in RUN and STOP and at 1 us, 1 ms and 100 ms
  per division. It is a fixed cost inside the scope.
- Recorded frames: NORM reads gave 4 distinct frames out of 4, RAW reads gave the same record 4 times.

Both remaining confounders were then removed with the user's help:

- USB cable physically unplugged: latency unchanged (21.4 ms minimum, 22-23 ms median).
- Series recorded by hand from the front panel (Record menu, 20 frames), then only read by
  `tools/read_recorded_frames.py`: NORM 4 distinct frames of 4 (934-962 of 1000 samples differ between
  frames), RAW the identical 10 000 samples for every frame.

So both are properties of the scope on firmware 00.01.05: a fixed ~22 ms per query, and deep-memory (RAW)
readout that ignores the selected replay frame. Which acquisition the RAW data belongs to could not be
determined; the frames differ only by noise and it matches all of them equally well.

Open: UltraAcquire segments are browsed with "Navigate segments", not with record/replay, so the earlier
UltraAcquire test used the wrong mechanism. Whether segments can be read by SCPI is untested.

## Other projects, run against the same scope (2026-09-27)

All five were read first (no shell calls, no foreign hosts), then run over Ethernet at 100 Mbit/s; ours runs
over USB with a fresh preamble on every read. Times are for one waveform of channel 1.

| Project | Transport, format | Screen (1000 pts) | Memory 1 M | Memory 10 M |
|---|---|---|---|---|
| this plug | USB, WORD, volts + time | 51 ms | 0.35 s | 1.9 s |
| [rigol-dho-scpi](https://github.com/sendu2wfdx/rigol-dho-scpi) | raw socket, WORD, bytes only | 27 ms | 0.32 s | 2.6 s |
| [rigol-dho-mcp](https://github.com/mattjax16/rigol-dho-mcp) | raw socket, BYTE, 250k chunks | 113 ms | 0.48 s | 4.3 s |
| [labmcp-rigol-scope](https://pypi.org/project/labmcp-rigol-scope/) | raw socket, BYTE, chunks | 216 ms | 0.74 s | 5.5 s |
| [pydho800](https://github.com/MasterJubei/pydho800) | raw socket, ASCII | 119 ms | 3.4 s | 36 s |
| [rigol-rc](https://github.com/y0v1737/rigol-rc) | VXI-11, WORD, per-sample `struct` loop | 90 ms | 15 s | > 150 s |

- Nothing beats USB + one binary read. The only faster number, rigol-dho-scpi's 27 ms, is a bare
  `:WAV:DATA?` with no preamble and no conversion; the plug does the same in 24 ms with `preamble=` reuse.
- The one transferable trick: a plain TCP socket with `TCP_NODELAY` and large `recv` is ~10 % faster than
  PyVISA's socket resource on 10 M points (2.6 s vs 2.95 s). Not worth a second transport implementation.
- rigol-dho-scpi loses sync when a connection is reused: it leaves the newline after a binary block unread.
- **Recorded frames:** none of the projects implements record/replay, and with frames selected by
  `:REC:WREP:FCUR` all five behave like the plug: screen reads follow the frame, memory (RAW) reads return
  the same record for every frame. It is the scope's behaviour, not a client bug. UltraAcquire
  (`:ACQ:TYPE ULTR`) offers no frame read-out commands either.
- Also checked: [TofuPilot's PyVISA + OpenHTF guide](https://www.tofupilot.com/guides/getting-started-with-pyvisa-control-test-instruments-from-python)
  for the plug pattern. No OpenHTF plug for this scope family existed.

## Files

`rigol_dho_plug.py` the plug · `fake_resource.py` hardware-free stand-in · `tests/` · `bench_usb.py` ·
`example_test.py` · `udev/` · `tools/bare_socket_check.py` · `SPEC.md` the original spec (superseded where this README differs).
