"""OpenHTF plug for Rigol DHO800/DHO900 oscilloscopes."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from openhtf.plugs import BasePlug
from openhtf.util import configuration

if TYPE_CHECKING:
    import pyvisa

CONF = configuration.CONF
CONF.declare(
    'rigol_dho_resource',
    default_value='',
    description=(
        'VISA resource name of the scope; '
        'empty = first USB instrument with vendor 0x1AB1'
    ),
)


class Waveform(NamedTuple):
    """Waveform capture from the scope."""
    t: np.ndarray[float, np.dtype[np.float64]]  # time of each sample (seconds)
    v: np.ndarray[float, np.dtype[np.float64]]  # voltage of each sample (volts)
    raw: np.ndarray  # samples exactly as received (uint8 or uint16)
    preamble: dict  # parsed :WAV:PRE?


class RigolDhoPlug(BasePlug):
    """OpenHTF plug wrapping a Rigol DHO scope via PyVISA."""

    def __init__(self, resource=None):
        if resource is not None:
            self._resource = resource
        else:
            import pyvisa
            rm = pyvisa.ResourceManager('@py')
            if CONF.rigol_dho_resource:
                resource_name = CONF.rigol_dho_resource
            else:
                found = rm.list_resources('USB?*::0x1AB1::?*::INSTR')
                if not found:
                    rm.close()
                    raise RuntimeError(
                        'No Rigol DHO scope found on USB. '
                        'Set --rigol_dho_resource or connect a scope.'
                    )
                resource_name = found[0]
            self._resource = rm.open_resource(resource_name)
        self._resource.timeout = 10000
        self._resource.chunk_size = 1024 * 1024
        self._resource.read_termination = '\n'
        self._resource.write_termination = '\n'

    # ── low-level ──────────────────────────────────────────────

    def write(self, cmd: str) -> None:
        self._resource.write(cmd)

    def query(self, cmd: str) -> str:
        return self._resource.query(cmd).strip()

    # ── identification & control ──────────────────────────────

    def idn(self) -> str:
        return self.query('*IDN?')

    def run(self) -> None:
        self.write(':RUN')

    def stop(self) -> None:
        self.write(':STOP')

    def single(self) -> None:
        self.write(':SING')

    def trigger_status(self) -> str:
        return self.query(':TRIG:STAT?')

    def memory_depth(self) -> str:
        return self.query(':ACQ:MDEP?')

    def set_memory_depth(self, depth: str) -> None:
        self.write(f':ACQ:MDEP {depth}')

    # ── measurements ──────────────────────────────────────────

    def measure(self, item: str, channel: int) -> float:
        return float(self.query(f':MEAS:ITEM? {item},CHAN{channel}'))

    # ── waveform acquisition ──────────────────────────────────

    def preamble(self) -> dict:
        raw = self.query(':WAV:PRE?')
        fields = raw.split(',')
        return {
            'format': int(fields[0]),
            'type': int(fields[1]),
            'points': int(float(fields[2])),
            'count': int(fields[3]),
            'xincrement': float(fields[4]),
            'xorigin': float(fields[5]),
            'xreference': float(fields[6]),
            'yincrement': float(fields[7]),
            'yorigin': float(fields[8]),
            'yreference': float(fields[9]),
        }

    def read_waveform(
        self,
        channel: int,
        mode: str = 'NORM',
        fmt: str = 'BYTE',
        chunk_points: int = 1_000_000,
    ) -> Waveform:
        # 1. Validate
        if channel not in (1, 2, 3, 4):
            raise ValueError(f'channel must be 1-4, got {channel}')
        if mode not in ('NORM', 'MAX', 'RAW'):
            raise ValueError(f'mode must be NORM/MAX/RAW, got {mode}')
        if fmt not in ('BYTE', 'WORD'):
            raise ValueError(f'fmt must be BYTE/WORD, got {fmt}')

        # 2. RAW memory check
        if mode == 'RAW' and self.trigger_status() != 'STOP':
            raise RuntimeError(
                'RAW memory can only be read while scope is STOPped'
            )

        # 3. Set waveform parameters
        self.write(f':WAV:SOUR CHAN{channel}')
        self.write(f':WAV:MODE {mode}')
        self.write(f':WAV:FORM {fmt}')

        # 4. Read preamble
        pre = self.preamble()
        n = pre['points']

        # 5. Read samples in chunks
        datatype = 'B' if fmt == 'BYTE' else 'H'
        chunks: list[np.ndarray] = []
        first = 1
        while first <= n:
            last = min(first + chunk_points - 1, n)
            self.write(f':WAV:STAR {first}')
            self.write(f':WAV:STOP {last}')
            data = self._resource.query_binary_values(
                ':WAV:DATA?',
                datatype=datatype,
                is_big_endian=False,
                container=np.array,
            )
            chunks.append(data)
            first = last + 1

        raw = np.concatenate(chunks)
        if len(raw) != n:
            raise RuntimeError(
                f'Expected {n} samples, got {len(raw)}'
            )

        # 6. Convert to volts and time
        v = (raw.astype(np.float64) - pre['yorigin'] - pre['yreference']) * pre['yincrement']
        t = (np.arange(n, dtype=np.float64) - pre['xreference']) * pre['xincrement'] + pre['xorigin']

        return Waveform(t=t, v=v, raw=raw, preamble=pre)

    # ── lifecycle ─────────────────────────────────────────────

    def tearDown(self) -> None:
        self._resource.close()
