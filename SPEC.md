# SPEC: OpenHTF plug for Rigol DHO800/DHO900 oscilloscopes

Target instrument: Rigol DHO814 (USB id `1ab1:044d`), controlled with SCPI over USBTMC through **PyVISA**
(`pyvisa` + `pyvisa-py` backend, already installed in `.venv`). Do not write your own USBTMC or IEEE-488.2
block parsing: PyVISA already does both. Python 3.13, dependencies limited to what is installed:
`openhtf`, `pyvisa`, `pyvisa-py`, `numpy`, `pytest`.

**The real oscilloscope is NOT available to you.** Never open a real VISA resource in tests. Everything you
run must use the fake resource described below.

## Files to create

```
rigol_dho_plug.py          the plug (single module)
fake_resource.py           FakeDhoResource, a stand-in for a PyVISA resource, used by tests
tests/test_plug.py         unit tests (pytest), no hardware
bench_usb.py               hardware benchmark CLI (you write it, you do NOT run it)
example_test.py            minimal OpenHTF test using the plug (you do NOT run it against hardware)
```

## 1. `rigol_dho_plug.py`

```python
from openhtf.plugs import BasePlug
from openhtf.util import configuration
CONF = configuration.CONF
CONF.declare('rigol_dho_resource', default_value='',
             description='VISA resource name of the scope; empty = first USB instrument with vendor 0x1AB1')
```

### `Waveform` (a `typing.NamedTuple`)

| field | type | meaning |
|---|---|---|
| `t` | `np.ndarray` float64 | time of each sample in seconds |
| `v` | `np.ndarray` float64 | voltage of each sample in volts |
| `raw` | `np.ndarray` uint8 or uint16 | samples exactly as received |
| `preamble` | `dict` | parsed `:WAV:PRE?`, keys below |

### `class RigolDhoPlug(BasePlug)`

`__init__(self, resource=None)`
- `resource` is any object with the PyVISA message-based interface (`write`, `query`, `query_binary_values`,
  `close`, attributes `timeout`, `chunk_size`). Tests pass a `FakeDhoResource`.
- If `resource is None`: open `pyvisa.ResourceManager('@py')`; use `CONF.rigol_dho_resource` if non-empty,
  otherwise the first name returned by `rm.list_resources('USB?*::0x1AB1::?*::INSTR')`; raise `RuntimeError`
  with a clear message if nothing is found.
- After obtaining the resource set `timeout = 10000` (ms), `chunk_size = 1024 * 1024`,
  `read_termination = '\n'`, `write_termination = '\n'`. Do not send `*RST`.

Methods (all SCPI strings exactly as written):

| method | SCPI | returns |
|---|---|---|
| `write(cmd: str)` | `cmd` | `None` |
| `query(cmd: str)` | `cmd` | stripped `str` |
| `idn()` | `*IDN?` | `str` |
| `run()` / `stop()` / `single()` | `:RUN` / `:STOP` / `:SING` | `None` |
| `trigger_status()` | `:TRIG:STAT?` | `str`, one of `RUN STOP AUTO WAIT TD` |
| `memory_depth()` | `:ACQ:MDEP?` | `str` |
| `set_memory_depth(depth: str)` | `:ACQ:MDEP <depth>` e.g. `10k`, `1M`, `AUTO` | `None` |
| `measure(item: str, channel: int)` | `:MEAS:ITEM? <item>,CHAN<channel>` e.g. `VPP` | `float` |
| `preamble()` | `:WAV:PRE?` | `dict` |
| `read_waveform(...)` | see below | `Waveform` |
| `tearDown()` | closes the resource | `None` |

`preamble()` parses the 10 comma-separated fields, in this order, into a dict with these keys and types:
`format` int (0 BYTE, 1 WORD, 2 ASC), `type` int (0 NORM, 1 MAX, 2 RAW), `points` int, `count` int,
`xincrement` float, `xorigin` float, `xreference` float, `yincrement` float, `yorigin` float, `yreference` float.
`points` may arrive as `"1000"` or `"1.000000e+03"`; handle both.

`read_waveform(self, channel: int, mode: str = 'NORM', fmt: str = 'BYTE', chunk_points: int = 1_000_000) -> Waveform`
1. Validate: `channel` in 1..4, `mode` in `NORM MAX RAW`, `fmt` in `BYTE WORD`; else `ValueError`.
2. If `mode == 'RAW'` and `trigger_status() != 'STOP'`: raise `RuntimeError` (RAW memory can only be read while stopped).
3. Send, in this order: `:WAV:SOUR CHAN<channel>`, `:WAV:MODE <mode>`, `:WAV:FORM <fmt>`.
4. `pre = self.preamble()`; `n = pre['points']`.
5. Read the samples in chunks of at most `chunk_points`. For each chunk send `:WAV:STAR <first>` and
   `:WAV:STOP <last>` (1-based, inclusive), then
   `resource.query_binary_values(':WAV:DATA?', datatype='B' or 'H', is_big_endian=False, container=np.array)`
   (`'B'` for BYTE, `'H'` for WORD). Concatenate chunks. If the total differs from `n`, raise `RuntimeError`.
6. Convert: `v = (raw.astype(np.float64) - pre['yorigin'] - pre['yreference']) * pre['yincrement']`,
   `t = (np.arange(n) - pre['xreference']) * pre['xincrement'] + pre['xorigin']`.

## 2. `fake_resource.py`

`class FakeDhoResource` emulating the subset above, with no hardware and no PyVISA import:
- attributes `timeout`, `chunk_size`, `read_termination`, `write_termination`, and `log: list[str]` recording
  every command string passed to `write`, `query` and `query_binary_values`, in order.
- constructor `FakeDhoResource(points=1000, status='RUN', memory_depth='10k')`.
- state changed by `write`: `:RUN`/`:STOP`/`:SING` (status `RUN`/`STOP`/`WAIT`), `:ACQ:MDEP x`,
  `:WAV:SOUR`, `:WAV:MODE`, `:WAV:FORM`, `:WAV:STAR n`, `:WAV:STOP n`.
- `query`: `*IDN?` → `RIGOL TECHNOLOGIES,DHO814,FAKE0000000001,00.01.02`; `:TRIG:STAT?`; `:ACQ:MDEP?`;
  `:WAV:PRE?` → `<format>,<type>,<points>,1,1.000000e-06,-5.000000e-04,0,4.000000e-02,0,127`
  (format/type follow the current `:WAV:FORM` / `:WAV:MODE`; for WORD use yincrement `1.5625e-04` and
  yreference `32768`); `:MEAS:ITEM? VPP,CHAN1` → `3.300000e+00`. Unknown query → raise `ValueError`.
- `query_binary_values(':WAV:DATA?', datatype, is_big_endian, container)` returns samples
  `STAR..STOP` of the deterministic sequence `sample[i] = i % 256` (BYTE) or `(i * 257) % 65536` (WORD),
  `i` 0-based, as `container(...)` with dtype uint8/uint16.
- `close()` sets `closed = True`.

## 3. `tests/test_plug.py`

Must cover at least:
1. `idn()` and `measure('VPP', 1)` return the values above.
2. `preamble()` types, including `points` given as `1.000000e+03`.
3. `read_waveform(1)` NORM/BYTE: 1000 points, `raw[5] == 5`, `v[5] == (5 - 127) * 0.04`, `t[0] == -5e-4`,
   `t[1] - t[0] == 1e-6` (use `pytest.approx`).
4. WORD format returns uint16 and correct volts.
5. Chunking: with `points=2500, chunk_points=1000` the log contains `:WAV:STAR 1`, `:WAV:STOP 1000`,
   `:WAV:STAR 1001`, `:WAV:STOP 2000`, `:WAV:STAR 2001`, `:WAV:STOP 2500` in order, and `raw` equals the
   full sequence.
6. `mode='RAW'` while status is `RUN` raises `RuntimeError`; after `stop()` it works.
7. Invalid channel / mode / fmt raise `ValueError`.
8. `tearDown()` closes the resource.
9. OpenHTF integration: a subclass whose `__init__` injects a `FakeDhoResource`, used through `@htf.plug`
   in a phase with one measurement (`vpp`, in range 3.0..3.6 V); run `htf.Test(phase).execute(test_start=lambda: 'dut1')`
   and assert it returns `True` (test passed).

## 4. `bench_usb.py` (write only, do not run)

CLI: `python bench_usb.py [--resource NAME] [--channel 1] [--repeats 20] [--depths 10k,100k,1M,10M] [--chunks 250000,1000000] [--out bench_results]`.
Uses `RigolDhoPlug` against the real scope. Remembers the initial memory depth and run state and restores
both in a `finally`. Measures with `time.perf_counter()` and prints one markdown table, also written as
JSON to `<out>/bench.json`:

| scenario | what is timed | reported |
|---|---|---|
| `idn` | 50 × `idn()` | median and p95 latency in ms |
| `norm_byte`, `norm_word` | `repeats` × `read_waveform(ch, 'NORM', fmt)` while running | points, median ms, waveforms/s |
| `raw_<depth>_<fmt>_<chunk>` | for each depth × fmt (BYTE, WORD) × chunk: set depth, `single()`/wait until `STOP` (timeout 10 s, else `stop()`), then 3 × `read_waveform(ch, 'RAW', fmt, chunk)` | points, median s, points/s, MiB/s |

A failing scenario is reported in the table as `ERROR: <message>` and the benchmark continues.

## 5. `example_test.py`

A runnable OpenHTF test with one phase that reads channel 1 in NORM mode, records `vpp` and `num_points`
as measurements, and attaches nothing else. No station server, no output callbacks except the console summary.

## Done means

`pytest -q` passes, `python -c "import bench_usb, example_test"` imports without touching hardware
(guard CLI code with `if __name__ == '__main__':`), and the working tree contains only the files listed above
plus what was already there.
