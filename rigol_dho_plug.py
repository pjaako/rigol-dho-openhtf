"""OpenHTF plug for Rigol DHO800/DHO900 oscilloscopes."""

from __future__ import annotations

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

    auto_placeholder = True

    def __init__(self, resource=None):
        self._rm = None
        if resource is not None:
            self._resource = resource
        else:
            import pyvisa
            self._rm = pyvisa.ResourceManager('@py')
            if CONF.rigol_dho_resource:
                resource_name = CONF.rigol_dho_resource
            else:
                found = self._rm.list_resources('USB?*::INSTR')
                resource_name = None
                for name in found:
                    parts = name.split('::')
                    if len(parts) >= 3:
                        try:
                            if int(parts[1], 0) == 0x1AB1:
                                resource_name = name
                                break
                        except ValueError:
                            pass
                if resource_name is None:
                    self._rm.close()
                    self._rm = None
                    raise RuntimeError(
                        'No Rigol DHO scope found on USB. '
                        'Set --rigol_dho_resource or connect a scope.'
                    )
            self._resource = self._rm.open_resource(resource_name)
        self._resource.timeout = 10000
        self._resource.chunk_size = 1024 * 1024
        self._resource.read_termination = '\n'
        self._resource.write_termination = '\n'
        self._wav_setup = None  # (channel, mode, fmt, n) last sent; skips ~100 ms of re-configuration

    # ── low-level ──────────────────────────────────────────────

    def write(self, cmd: str) -> None:
        if cmd.upper().startswith((':WAV', ':ACQ')):
            self._wav_setup = None
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

    def memory_depth(self) -> int:
        return int(float(self.query(':ACQ:MDEP?')))

    def set_memory_depth(self, depth) -> None:
        # The scope rejects its own '1.0000E+05' format; ints and '100k'/'1E5' work.
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
        fmt: str = 'WORD',
        chunk_points: int = None,
        preamble: dict = None,
    ) -> Waveform:
        """Read one waveform as 16-bit samples (fmt='WORD', the project default).

        Pass `preamble` from a previous Waveform to skip the
        :WAV:PRE? query (saves ~24 ms) when scale, offset and timebase have not changed."""
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
        if mode == 'RAW' and self.query(':REC:WREC:ENAB?') == '1':
            raise RuntimeError(
                'Waveform recording is enabled: RAW would return a capture that is none of '
                'the recorded frames. Read frames with mode=\'NORM\' or disable recording '
                '(:REC:WREC:ENAB OFF).'
            )

        # 3. Configure the waveform source only when something changed
        n = self.memory_depth() if mode == 'RAW' else 1000
        setup = (channel, mode, fmt, n)
        if setup != self._wav_setup:
            self._resource.write(f':WAV:SOUR CHAN{channel}')
            self._resource.write(f':WAV:MODE {mode}')
            self._resource.write(f':WAV:FORM {fmt}')
            self._resource.write(':WAV:STAR 1')
            self._resource.write(f':WAV:STOP {n}')
            preamble = None  # a cached preamble is not valid for a new setup
        elif chunk_points is not None:
            self._resource.write(':WAV:STAR 1')
            self._resource.write(f':WAV:STOP {n}')

        # 4. Preamble (in RAW its point count is the STAR..STOP window, so set the window first)
        pre = preamble if preamble is not None else self.preamble()
        if pre['points'] != n:
            raise RuntimeError(
                f'Expected {n} points, preamble says {pre["points"]}'
            )
        self._wav_setup = setup if chunk_points is None else None

        # 6. Read samples
        datatype = 'B' if fmt == 'BYTE' else 'H'

        if chunk_points is None:
            # Already set STAR 1 / STOP n above; one read of all n points
            data = self._resource.query_binary_values(
                ':WAV:DATA?',
                datatype=datatype,
                is_big_endian=False,
                container=np.array,
            )
            raw = data
        else:
            # Chunked reads - override STAR/STOP per window
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

        # 7. Convert to volts and time. Verified against the scope's own VTOP/VBAS/VPP on a 3 Vpp
        #    square wave at channel offsets 0, -1.5 and +1 V, BYTE and WORD (firmware 00.01.05).
        v = (raw.astype(np.float64) - pre['yorigin'] - pre['yreference']) * pre['yincrement']
        t = (np.arange(n, dtype=np.float64) - pre['xreference']) * pre['xincrement'] + pre['xorigin']

        return Waveform(t=t, v=v, raw=raw, preamble=pre)

    # ── lifecycle ─────────────────────────────────────────────

    def tearDown(self) -> None:
        self._resource.close()
        if self._rm is not None:
            self._rm.close()
            self._rm = None
