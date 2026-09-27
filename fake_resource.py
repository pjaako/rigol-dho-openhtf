"""FakeDhoResource – stand-in for a PyVISA resource used by tests."""

from __future__ import annotations

import numpy as np


class FakeDhoResource:
    """Emulate the subset of PyVISA message-based interface needed by RigolDhoPlug."""

    def __init__(self, points: int = 1000, status: str = 'RUN', memory_depth: str = '10k'):
        self.timeout = 10000
        self.chunk_size = 1024 * 1024
        self.read_termination = '\n'
        self.write_termination = '\n'
        self.log: list[str] = []

        # Configurable state
        self._points = points
        self._status = status  # RUN / STOP / WAIT
        self._memory_depth = memory_depth
        self._wav_mode = 'NORM'
        self._wav_form = 'BYTE'
        self._wav_star = 1
        self._wav_stop = 1
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
        elif cmd.startswith(':ACQ:MDEP '):
            self._memory_depth = cmd.split(None, 1)[1]
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
            return self._status
        if cmd == ':ACQ:MDEP?':
            return self._memory_depth

        if cmd == ':WAV:PRE?':
            fmt_code = 0 if self._wav_form == 'BYTE' else 1
            type_code = {'NORM': 0, 'MAX': 1, 'RAW': 2}[self._wav_mode]
            if self._wav_form == 'BYTE':
                yincrement = 0.04
                yreference = 127.0
            else:
                yincrement = 1.5625e-04
                yreference = 32768.0
            return (
                f'{fmt_code},{type_code},{self._points:.6e},1,'
                f'1.000000e-06,-5.000000e-04,0,'
                f'{yincrement:.6e},0,{yreference:.0f}'
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
