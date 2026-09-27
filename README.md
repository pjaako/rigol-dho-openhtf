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

## Setting up a capture

The plug stays thin: it has no per-setting setters. Test conditions are a dict of SCPI header -> value that
lives in the test, not in the plug.

```python
CAPTURE = {':TIM:MAIN:SCAL': 1e-3, ':CHAN1:SCAL': 0.5, ':CHAN1:COUP': 'DC'}

def phase(test, scope):
    scope.reset()
    scope.apply_setup(CAPTURE)   # writes each item, verifies the read-back, raises on any mismatch
    ...
```

| method | behaviour |
|---|---|
| `check_errors()` | drains `:SYST:ERR?`, returns the error strings found |
| `write_checked(cmd)` | `write()` then `check_errors()`, raises on any error |
| `reset()` | `*RST`, waits, drains errors; never sends `*OPC?` (always `0` on this firmware) |
| `wait_stopped(timeout_s=10.0)` | polls `trigger_status()` until `STOP` |
| `get_state()` / `set_state(state)` | full setup block (`:SYST:SET?`) plus the recording flag |
| `apply_setup(setup)` | writes and verifies every item; raises `RuntimeError` naming every failure |

By default the plug snapshots `get_state()` in `__init__` and calls `set_state()` in `tearDown()`, handing the
scope back the way it found it (a failure to restore is logged, not raised, and never blocks closing the
resource). Disable with `RigolDhoPlug(restore_state=False)` or the config key `rigol_dho_restore_state`;
`bench_usb.py` disables it because it restores what it changes itself.

Facts this is built on, measured on the real scope (DHO814, firmware 00.01.05):
- `*RST` restores factory defaults within 0.34 s; LAN settings survive it. `*OPC?` always returns `0`.
- `:SYST:SET?` reads the whole setup as one binary block (2883 bytes, 33 ms); `:SYST:SET <block>` restores
  it 0.03 s later, but **not** waveform recording — restore `:REC:WREC:ENAB` separately.
- Read-back formats differ from what was written, e.g. `:TIM:MAIN:SCAL 1E-3` reads back `1.000000E-3`,
  `:CHAN1:DISP ON` reads back `1`. Every query costs ~22 ms.

## Setup

```bash
uv venv .venv && uv pip install --python .venv/bin/python openhtf pyvisa pyvisa-py pyusb numpy pytest pyyaml jsonschema matplotlib
sudo cp udev/99-rigol-dho.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules \
  && sudo udevadm trigger --attr-match=idVendor=1ab1      # user must be in group plugdev
.venv/bin/python -m pytest -q                              # 100 tests, no hardware needed
.venv/bin/python example_test.py [--fake]
.venv/bin/python examples/operator_demo.py [--fake]        # web GUI on port 4444, see examples/README.md
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

### Verified on the scope (2026-09-27)

| Step | Time |
|---|---|
| `reset()` | 0.54 s |
| `apply_setup()` of 15 settings | 1.8 s (two queries per setting) |
| `tearDown()` with state restore, recording was on | 1.6 s |
| `tearDown()` with state restore, recording was off | 0.5 s |

- A value the scope clamps (`:TIM:MAIN:SCAL 5000` becomes 500) is reported through the read-back, without
  any error from the scope. A bad value (`:CHAN1:COUP XYZ`) gives `-222,"Data out of range"`.
- A header the scope does not know is never answered: the read-back times out (resource timeout, 10 s by
  default) and `apply_setup` reports it together with `-100,"Command err"`.
- Numbers must be sent clean. `200 * 1e-6` is `0.00019999999999999998` in binary floating point and the
  scope set 198 us/div from it. The capture parser uses decimal arithmetic and `apply_setup` sends floats
  with 9 significant digits.
- `:ACQ:MDEP AUTO` reads back as the depth the scope picked (e.g. `6.2500E+05`); `apply_setup` accepts that.
- `apply_capture('captures/clock.yaml')` takes 2.1 s including the reset; a file with mistakes is refused
  before anything is sent.
- About 0.9 s after a setup block is loaded the scope switches waveform recording off. `set_state` waits
  1.2 s before switching it back on and checks that it stayed.

### Capture conditions as a readable file

`captures/clock.yaml`, applied with `scope.apply_capture('captures/clock.yaml')` (`reset()`, then
`apply_setup(capture.to_scpi())`):

```yaml
timebase: 1 ms/div
ch1:
  scale: 1 V/div
  offset: -1.5 V
  coupling: DC
trigger:
  source: ch1
  level: 1.5 V
  slope: rising
  mode: normal
memory: 1M
```

`capture.py` is the typed model (`Capture`, `Channel`, `Edge`) and the YAML front end for it. Quantities are
strings `<number> <prefix><unit>`, prefixes `m u µ n k`, units `V`/`s`, `/div` where the field is per division.

| Field | Unit | Choices |
|---|---|---|
| `timebase` | s/div | required |
| `ch1`-`ch4` | — | each optional; `scale` required, `offset` V (default 0 V) |
| `ch*.coupling` | — | `DC`, `AC`, `GND` |
| `ch*.probe` | — | `1`, `10`, `100` |
| `trigger.source` | — | a described channel, required |
| `trigger.level` | V | required |
| `trigger.slope` | — | `rising`, `falling`, `either` |
| `trigger.mode` | — | `auto`, `normal`, `single` |
| `memory` | — | `auto`, `1k`, `10k`, `100k`, `1M`, `10M`, `25M` |
| `extra` | — | raw SCPI header: value, passed to `to_scpi()` last |

Mistakes are caught in four places:

| Stage | Who | Catches |
|---|---|---|
| While typing | editor with `capture.schema.json` | unknown keys, wrong choices, wrong unit or missing `/div` |
| Before commit / in CI | `python capture.py check captures/*.yaml` | the same, for people without such an editor |
| When the test starts | `load_capture()` | everything above plus cross-checks, all problems at once, with `<file>:<line>:` and hints |
| On the scope | `apply_setup()` (inside `apply_capture()`) | values the scope clamps or rejects |

`tests/data/broken.yaml` holds nine deliberate mistakes; the loader and the schema (`python capture.py
schema`, also committed as `capture.schema.json`) each report all nine.

Editor setup: VS Code (Red Hat YAML extension) picks up `.vscode/settings.json`
(`"yaml.schemas": {"./capture.schema.json": "captures/*.yaml"}`) automatically. PyCharm: Settings >
Languages & Frameworks > Schemas and DTDs > JSON Schema Mappings, add `capture.schema.json`, schema
version 2020-12, file path pattern `captures/*.yaml`.

The Python spelling still works directly, without YAML:

```python
from capture import Capture, Channel, Edge, V, ms, div
Capture(timebase=1 * ms / div, ch1=Channel(scale=1 * V / div, offset=-1.5 * V),
        trigger=Edge(source='ch1', level=1.5 * V))
```

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
readout that ignores the selected replay frame.

What RAW returns instead: with the trigger removed, 20 recorded frames had rising edges scattered over the
whole period (phases 840, 570, 264, 26, 964 us for frames 1, 10, 18, 19, 20), while the memory data had its
edge exactly at t = 0, as in a triggered capture. It matches none of the recorded frames. The deep-memory
buffer therefore seems to hold an acquisition from before the recording, and recording does not update it
(inferred from one experiment).

The plug refuses `mode='RAW'` while recording is enabled (`:REC:WREC:ENAB?` is 1) instead of returning
that unrelated capture.

### UltraAcquire segments (explored 2026-09-27, not yet understood well enough to build on)

Commands: `:ACQ:TYPE ULTR`, `:ACQ:ULTR:MAXF n`, `:ACQ:ULTR:TIM seconds`, `:NAV:ENAB ON`, `:NAV:MODE FRAM`,
`:NAV:FRAM:STAR:FRAM`, `:NAV:FRAM:END:FRAM`, `:NAV:FRAM:NEXT|BACK|STAR|END`.

- Segments are filled per trigger event, so a working trigger is required, and sampling stops at the
  timeout (default 1 ms). At 500 us/div only one segment fits; with `:ACQ:ULTR:TIM 1` and 20 us/div the
  scope showed 8-9 segments.
- Frame navigation commands are accepted but never change what `:WAV:DATA?` returns.
- A RAW read can contain many segments back to back: at depth 100k it held 5 trigger edges and at 1M it
  held 49, always 20 480 samples apart, while the real signal period was 1 ms. 1 M points took 0.41 s.
  At depth 10k the read held a single edge.
- Not understood: why 20 480 samples per segment, how the count relates to `MAXF` and to the 8-9 segments
  shown on screen, and whether segments carry timestamps. Until then the dependable way to get a series at
  full depth is a loop of `single()` / wait for `STOP` / `read_waveform(mode='RAW')`.

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

`rigol_dho_plug.py` the plug · `fake_resource.py` hardware-free stand-in · `capture.py` typed model + YAML
front end for capture conditions · `capture.schema.json` its generated JSON Schema · `captures/` capture
files · `tests/` (including `tests/data/broken.yaml`) · `bench_usb.py` · `example_test.py` · `udev/` ·
`tools/bare_socket_check.py` · `.vscode/settings.json` YAML schema mapping for VS Code ·
`SPEC.md`/`SPEC-setup.md`/`SPEC-capture.md` the original specs (superseded where this README differs).
