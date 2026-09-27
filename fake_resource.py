"""FakeDhoResource – stand-in for a PyVISA resource used by tests."""

from __future__ import annotations

import numpy as np


def _parse_depth(value: str) -> int:
    """Parse a memory depth string like '10k', '1M', '1E5', or '100000' into an int."""
    value = value.strip().upper()
    multipliers = {'K': 1000, 'M': 1000000}
    if value[-1] in multipliers:
        return int(float(value[:-1]) * multipliers[value[-1]])
    return int(float(value))


class FakeDhoResource:
    """Emulate the subset of PyVISA message-based interface needed by RigolDhoPlug."""

    def __init__(self, status: str = 'RUN', memory_depth: int = 10000):
        self.timeout = 10000
        self.chunk_size = 1024 * 1024
        self.read_termination = '\n'
        self.write_termination = '\n'
        self.log: list[str] = []

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

    # ── write / query ─────────────────────────────────────────

    def write(self, cmd: str) -> None:
        self.log.append(cmd)
        cmd = cmd.strip()

        if cmd == ':RUN':
            self._status = 'RUN'
        elif cmd == ':STOP':
            self._status = 'STOP'
        elif cmd == ':SING':
            self._status = 'WAIT'
            self._sing_wait_count = 0
        elif cmd.startswith(':REC:WREC:ENAB '):
            self._recording = cmd.split()[1].upper() in ('ON', '1')
        elif cmd.startswith(':ACQ:MDEP '):
            self._memory_depth = _parse_depth(cmd.split(None, 1)[1])
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

        raise ValueError(f'Unknown query: {cmd}')

    def query_binary_values(self, cmd: str, datatype: str, is_big_endian: bool, container):
        self.log.append(cmd)
        if cmd != ':WAV:DATA?':
            raise ValueError(f'Unexpected binary query: {cmd}')

        fmt = self._wav_form
        start = self._wav_star - 1  # convert to 0-based
        stop = self._wav_stop - 1   # convert to 0-based

        if fmt == 'BYTE':
            data = np.array([i % 256 for i in range(start, stop + 1)], dtype=np.uint8)
        else:
            data = np.array([(i * 257) % 65536 for i in range(start, stop + 1)], dtype=np.uint16)

        return container(data)

    def close(self) -> None:
        self.closed = True

    def set_status(self, status: str) -> None:
        """Helper for tests to change trigger status."""
        self._status = status
