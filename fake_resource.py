"""FakeDhoResource – stand-in for a PyVISA resource used by tests."""

from __future__ import annotations

import pickle

import numpy as np

# Factory defaults for headers handled by the generic settings store (see
# FakeDhoResource.write/query). Values written as Python numbers are formatted
# as f'{x:.6E}' when read back; values written as strings are upper-cased as-is.
DEFAULTS = {
    ':TIM:MAIN:SCAL': 2e-6,
    ':TIM:MAIN:OFFS': 0,
    ':CHAN1:SCAL': 0.05,
    ':CHAN1:OFFS': 0,
    ':CHAN1:COUP': 'DC',
    ':CHAN1:DISP': '1',
    ':CHAN1:PROB': 1,
    ':TRIG:EDGE:SOUR': 'CHAN1',
    ':TRIG:EDGE:LEV': 0,
    ':TRIG:EDGE:SLOP': 'POS',
    ':TRIG:SWE': 'AUTO',
    ':ACQ:TYPE': 'NORM',
    ':TRIG:MODE': 'EDGE',
    ':CHAN2:DISP': '0',
    ':CHAN2:SCAL': 0.05,
    ':CHAN2:OFFS': 0,
    ':CHAN2:COUP': 'DC',
    ':CHAN2:PROB': 1,
    ':CHAN3:DISP': '0',
    ':CHAN3:SCAL': 0.05,
    ':CHAN3:OFFS': 0,
    ':CHAN3:COUP': 'DC',
    ':CHAN3:PROB': 1,
    ':CHAN4:DISP': '0',
    ':CHAN4:SCAL': 0.05,
    ':CHAN4:OFFS': 0,
    ':CHAN4:COUP': 'DC',
    ':CHAN4:PROB': 1,
}


def _parse_depth(value: str) -> int:
    """Parse a memory depth string like '10k', '1M', '1E5', or '100000' into an int."""
    value = value.strip().upper()
    multipliers = {'K': 1000, 'M': 1000000}
    if value[-1] in multipliers:
        return int(float(value[:-1]) * multipliers[value[-1]])
    return int(float(value))


def _format_written(raw: str) -> str:
    """Format a generic-store raw write value the way the scope reads it back."""
    s = raw.strip()
    if s.upper() in ('ON', 'OFF'):
        return '1' if s.upper() == 'ON' else '0'
    try:
        return f'{float(s):.6E}'
    except ValueError:
        return s.upper()


def _format_default(value) -> str:
    """Format a DEFAULTS entry (typed, not a raw write string) for read-back."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'{float(value):.6E}'
    s = str(value).strip().upper()
    return '1' if s == 'ON' else '0' if s == 'OFF' else s


# Golden-comparison test signal (opt-in, `signal='clock'`): a bounded square wave that stays
# well inside the code range, unlike the default sawtooth below (which deliberately touches 0 and
# the top code and would always look clipped). One period spans the whole default 1000-point
# screen capture (xincrement 1 us => 1 kHz), 50% duty cycle.
_CLOCK_PERIOD = 1000
_CLOCK_DUTY = 500
_CLOCK_LOW = {'BYTE': 64, 'WORD': 16384}
_CLOCK_HIGH = {'BYTE': 191, 'WORD': 49151}
_MID_CODE = {'BYTE': 128, 'WORD': 32768}
_MAX_CODE = {'BYTE': 255, 'WORD': 65535}
_DEFECTS = (None, 'shift', 'gain', 'glitch')
_SIGNALS = (None, 'clock')


class FakeDhoResource:
    """Emulate the subset of PyVISA message-based interface needed by RigolDhoPlug."""

    def __init__(self, status: str = 'RUN', memory_depth: int = 10000,
                 reject: dict[str, str] | None = None, defect: str | None = None,
                 signal: str | None = None):
        self.timeout = 10000
        self.chunk_size = 1024 * 1024
        self.read_termination = '\n'
        self.write_termination = '\n'
        self.log: list[str] = []

        if defect not in _DEFECTS:
            raise ValueError(f'defect must be one of {_DEFECTS}, got {defect!r}')
        if signal not in _SIGNALS:
            raise ValueError(f'signal must be one of {_SIGNALS}, got {signal!r}')

        # Configurable state
        self._status = status  # RUN / STOP / WAIT
        self._recording = False  # :REC:WREC:ENAB
        self._memory_depth = memory_depth
        self._wav_mode = 'NORM'
        self._wav_form = 'BYTE'
        self._wav_star = 1
        self._wav_stop = 1
        self._sing_wait_count = 0  # tracks how many :TRIG:STAT? after SING
        self.closed = False
        self._defect = defect  # None (unchanged) / 'shift' / 'gain' / 'glitch'; see _waveform_samples
        self._signal = signal  # None (default sawtooth, unchanged) / 'clock' (bounded, for golden.py)

        # Generic settings store, error queue and injected rejections
        self._settings: dict[str, str] = {}
        self._error_queue: list[str] = []
        self._reject = reject or {}

    def _waveform_samples(self, idx: np.ndarray, fmt: str) -> np.ndarray:
        """Sample codes for absolute sample indices `idx` (0-based), fmt 'BYTE' or 'WORD'.

        With `defect=None` and `signal=None` this reproduces exactly the previous hard-coded
        sawtooth (i % 256 / (i * 257) % 65536), so existing behaviour and tests are unchanged.
        """
        shifted = idx - 5 if self._defect == 'shift' else idx
        if self._signal == 'clock':
            phase = np.mod(shifted, _CLOCK_PERIOD)
            base = np.where(phase < _CLOCK_DUTY, _CLOCK_HIGH[fmt], _CLOCK_LOW[fmt]).astype(np.float64)
        elif fmt == 'BYTE':
            base = np.mod(shifted, 256).astype(np.float64)
        else:
            base = np.mod(shifted.astype(np.int64) * 257, 65536).astype(np.float64)

        if self._defect == 'gain':
            mid = _MID_CODE[fmt]
            base = mid + 0.9 * (base - mid)
        if self._defect == 'glitch':
            base = np.where((idx >= 500) & (idx <= 504), _MID_CODE[fmt], base)

        codes = np.clip(np.round(base), 0, _MAX_CODE[fmt])
        return codes.astype(np.uint8 if fmt == 'BYTE' else np.uint16)

    # ── write / query ─────────────────────────────────────────

    def write(self, cmd: str) -> None:
        self.log.append(cmd)
        cmd = cmd.strip()

        header = cmd.split(None, 1)[0] if cmd else ''
        if header in self._reject:
            self._error_queue.append(self._reject[header])
            return

        if cmd == '*RST':
            self._settings.clear()
            self._error_queue.clear()
            self._status = 'RUN'
            self._recording = False
            self._memory_depth = 10000
            self._wav_mode = 'NORM'
            self._wav_form = 'BYTE'
            self._wav_star = 1
            self._wav_stop = 1
            self._sing_wait_count = 0
        elif cmd == ':RUN':
            self._status = 'RUN'
        elif cmd == ':STOP':
            self._status = 'STOP'
        elif cmd == ':SING':
            self._status = 'WAIT'
            self._sing_wait_count = 0
        elif cmd.startswith(':REC:WREC:ENAB '):
            self._recording = cmd.split()[1].upper() in ('ON', '1')
        elif cmd.startswith(':ACQ:MDEP '):
            value = cmd.split(None, 1)[1]
            if value.strip().upper() != 'AUTO':  # AUTO leaves the depth at whatever it resolved to
                self._memory_depth = _parse_depth(value)
        elif cmd == ':WAV:SOUR':
            pass
        elif cmd.startswith(':WAV:SOUR'):
            pass
        elif cmd == ':WAV:MODE':
            pass
        elif cmd.startswith(':WAV:MODE '):
            self._wav_mode = cmd.split(None, 1)[1]
        elif cmd == ':WAV:FORM':
            pass
        elif cmd.startswith(':WAV:FORM '):
            self._wav_form = cmd.split(None, 1)[1]
        elif cmd.startswith(':WAV:STAR '):
            self._wav_star = int(cmd.split()[1])
        elif cmd.startswith(':WAV:STOP '):
            self._wav_stop = int(cmd.split()[1])
        else:
            parts = cmd.split(None, 1)
            if len(parts) == 2:
                if parts[0] in DEFAULTS:
                    self._settings[parts[0]] = parts[1]
                else:   # the real scope rejects a header it does not know and never answers its query
                    self._error_queue.append('-100,"Command err"')

    def query(self, cmd: str) -> str:
        self.log.append(cmd)
        cmd = cmd.strip()

        if cmd == '*IDN?':
            return 'RIGOL TECHNOLOGIES,DHO814,FAKE0000000001,00.01.02'
        if cmd == ':TRIG:STAT?':
            # After :SING, first query returns WAIT, subsequent return STOP
            if self._status == 'WAIT':
                self._sing_wait_count += 1
                if self._sing_wait_count == 1:
                    return 'WAIT'
                else:
                    self._status = 'STOP'
                    return 'STOP'
            return self._status
        if cmd == ':REC:WREC:ENAB?':
            return '1' if self._recording else '0'
        if cmd == ':ACQ:MDEP?':
            return f'{self._memory_depth:.4E}'
        if cmd == ':SYST:ERR?':
            if self._error_queue:
                return self._error_queue.pop(0)
            return '0,"No error"'

        if cmd == ':WAV:PRE?':
            fmt_code = 0 if self._wav_form == 'BYTE' else 1
            type_code = {'NORM': 0, 'MAX': 1, 'RAW': 2}[self._wav_mode]

            if self._wav_mode == 'RAW':
                points = self._wav_stop - self._wav_star + 1
            else:
                points = 1000

            if self._wav_form == 'BYTE':
                yincrement = 4.000000e-02
                yreference = 128
                yorigin = 58
            else:
                yincrement = 1.5625e-04
                yreference = 32768
                yorigin = 15000

            return (
                f'{fmt_code},{type_code},{points},1,'
                f'1.000000e-06,-5.000000e-04,0,'
                f'{yincrement:.6e},{yorigin:.0f},{yreference}'
            )

        if cmd.startswith(':MEAS:ITEM?'):
            return '3.300000e+00'

        if cmd.endswith('?'):
            header = cmd[:-1]
            if header in self._settings:
                return _format_written(self._settings[header])
            if header in DEFAULTS:
                return _format_default(DEFAULTS[header])

        raise ValueError(f'Unknown query: {cmd}')

    def query_binary_values(self, cmd: str, datatype: str, is_big_endian: bool = False,
                             container=list):
        self.log.append(cmd)
        if cmd == ':SYST:SET?':
            blob = {
                'settings': dict(self._settings),
                'memory_depth': self._memory_depth,
                'status': self._status,
            }
            return container(pickle.dumps(blob))
        if cmd != ':WAV:DATA?':
            raise ValueError(f'Unexpected binary query: {cmd}')

        fmt = self._wav_form
        start = self._wav_star - 1  # convert to 0-based
        stop = self._wav_stop - 1   # convert to 0-based

        idx = np.arange(start, stop + 1)
        data = self._waveform_samples(idx, fmt)

        return container(data)

    def write_binary_values(self, message: str, values, datatype: str = 'B') -> None:
        self.log.append(message)
        if message.strip() != ':SYST:SET':
            raise ValueError(f'Unexpected binary write: {message}')
        blob = pickle.loads(bytes(values))
        self._settings = dict(blob['settings'])
        self._memory_depth = blob['memory_depth']
        self._status = blob['status']
        self._recording = False  # the setup block does not restore recording, like the real scope

    def close(self) -> None:
        self.closed = True

    def set_status(self, status: str) -> None:
        """Helper for tests to change trigger status."""
        self._status = status
