"""OpenHTF plug for Rigol DHO800/DHO900 oscilloscopes."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from openhtf.plugs import BasePlug
from openhtf.util import configuration

from rigol_dho_openhtf.capture import Capture, load_capture

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
CONF.declare(
    'rigol_dho_restore_state',
    default_value=True,
    description=(
        'Restore the scope to the state it was in when the plug was created, '
        'in tearDown()'
    ),
)


def _parse_number(value) -> float | None:
    """Parse int/float/str (with an optional k/M/G suffix) to a float, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        suffixes = {'k': 1e3, 'M': 1e6, 'G': 1e9}
        if s and s[-1] in suffixes:
            try:
                return float(s[:-1]) * suffixes[s[-1]]
            except ValueError:
                return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _values_match(sent, readback: str) -> bool:
    """Compare a value passed to apply_setup with the scope's read-back string."""
    if isinstance(sent, bool):
        return readback.strip() in (('1', 'ON') if sent else ('0', 'OFF'))
    read_num = _parse_number(readback)
    if str(sent).strip().casefold() == 'auto' and read_num is not None:
        return True   # the scope answers AUTO with the value it picked, e.g. 6.2500E+05
    sent_num = _parse_number(sent)
    if sent_num is not None and read_num is not None:
        return math.isclose(sent_num, read_num, rel_tol=1e-6, abs_tol=1e-12)
    return str(sent).strip().casefold() == readback.strip().casefold()


class Waveform(NamedTuple):
    """Waveform capture from the scope."""
    t: np.ndarray[float, np.dtype[np.float64]]  # time of each sample (seconds)
    v: np.ndarray[float, np.dtype[np.float64]]  # voltage of each sample (volts)
    raw: np.ndarray  # samples exactly as received (uint8 or uint16)
    preamble: dict  # parsed :WAV:PRE?


class RigolDhoPlug(BasePlug):
    _RECORDING_RESTORE_DELAY_S = 1.2
    # Not carried by the setup block (measured). MODE comes before STAR/STOP: it decides their range.
    _WAV_HEADERS = (':WAV:SOUR', ':WAV:MODE', ':WAV:FORM', ':WAV:STAR', ':WAV:STOP')
    # Read back after a setup block to see whether the scope loaded it. A dropped block changes
    # nothing at all (measured), so a few settings that tests usually change are enough.
    _CHECK_HEADERS = (':TIM:MAIN:SCAL', ':TIM:MAIN:OFFS', ':CHAN1:DISP', ':CHAN1:SCAL', ':CHAN1:OFFS',
                      ':CHAN2:DISP', ':CHAN2:SCAL', ':CHAN2:OFFS')
    _BLOCK_LOAD_ATTEMPTS = 3
    """OpenHTF plug wrapping a Rigol DHO scope via PyVISA."""

    auto_placeholder = True

    def __init__(self, resource=None, restore_state=None):
        if resource is not None:
            self._resource = resource
        else:
            import pyvisa
            # PyVISA hands out one ResourceManager per backend to the whole process: never close it,
            # that would close the sessions of every other instrument plug in the same test.
            rm = pyvisa.ResourceManager('@py')
            if CONF.rigol_dho_resource:
                resource_name = CONF.rigol_dho_resource
            else:
                found = rm.list_resources('USB?*::INSTR')
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
                    raise RuntimeError(
                        'No Rigol DHO scope found on USB. '
                        'Set --rigol_dho_resource or connect a scope.'
                    )
            self._resource = rm.open_resource(resource_name)
        self._resource.timeout = 10000
        self._resource.chunk_size = 1024 * 1024
        self._resource.read_termination = '\n'
        self._resource.write_termination = '\n'
        self._wav_setup = None  # (channel, mode, fmt, n) last sent; skips ~100 ms of re-configuration

        self._restore_state = (
            CONF.rigol_dho_restore_state if restore_state is None else restore_state
        )
        self._initial_state = self.get_state() if self._restore_state else None

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

    # ── screenshot ────────────────────────────────────────────

    def screenshot(self, path: str | Path | None = None) -> bytes:
        """Return the scope screen as PNG bytes (1024x600 RGBA on the DHO814); also write it to `path` if given.

        Read-only on the scope and independent of the waveform setup, so it does not reset `_wav_setup`."""
        data = self._resource.query_binary_values(
            ':DISP:DATA? PNG', datatype='B', container=bytes
        )
        if data[:8] != b'\x89PNG\r\n\x1a\n' or data[-8:-4] != b'IEND':
            raise RuntimeError(
                f'Scope screen is not a complete PNG ({len(data)} bytes, starts {data[:8]!r})'
            )
        if path is not None:
            Path(path).write_bytes(data)
        return data

    # ── setup, reset & state restore ────────────────────────────

    def check_errors(self) -> list[str]:
        """Read :SYST:ERR? until the code is 0 (at most 20 reads)."""
        errors = []
        for _ in range(20):
            resp = self.query(':SYST:ERR?')
            code = resp.split(',', 1)[0].strip()
            if code == '0':
                break
            errors.append(resp)
        return errors

    def write_checked(self, cmd: str) -> None:
        self.write(cmd)
        errors = self.check_errors()
        if errors:
            raise RuntimeError(f'{cmd!r} produced errors: {errors}')

    def reset(self) -> None:
        """*RST the scope. No *OPC? (it always returns 0 on this firmware)."""
        self.write('*RST')
        time.sleep(0.5)
        self.check_errors()
        self._wav_setup = None

    def wait_stopped(self, timeout_s: float = 10.0, poll_s: float = 0.05) -> None:
        deadline = time.perf_counter() + timeout_s
        while True:
            if self.trigger_status() == 'STOP':
                return
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    f'Scope did not reach STOP within {timeout_s} s'
                )
            time.sleep(poll_s)

    def get_state(self) -> dict:
        block = self._resource.query_binary_values(
            ':SYST:SET?', datatype='B', container=bytes
        )
        recording = self.query(':REC:WREC:ENAB?') == '1'
        wav = {header: self.query(f'{header}?') for header in self._WAV_HEADERS}
        check = {header: self.query(f'{header}?') for header in self._CHECK_HEADERS}
        return {'block': block, 'recording': recording, 'wav': wav, 'check': check}

    def set_state(self, state: dict) -> None:
        self.check_errors()   # stale errors must not be blamed on the restore
        # Measured: after a :MEAS:ITEM? query followed by any other query the scope drops the block
        # without an error; a repeated load takes, mostly the first one.
        check = state.get('check', {})
        for attempt in range(1, self._BLOCK_LOAD_ATTEMPTS + 1):
            self._resource.write_binary_values(
                ':SYST:SET ', state['block'], datatype='B'
            )
            not_loaded = [h for h, value in check.items() if self.query(f'{h}?') != value]
            if not not_loaded:
                break
            self.logger.warning('set_state: setup block not loaded (attempt %d): %s', attempt, not_loaded)
        else:
            raise RuntimeError(
                f'set_state: the scope did not load the setup block in {self._BLOCK_LOAD_ATTEMPTS} '
                f'attempts, still different: {not_loaded}'
            )
        self._wav_setup = None
        # The block does not carry the waveform read settings. They go before the recording
        # switch: written after it, recording was found off again in 2 of 4 restores.
        wav = state.get('wav', {})
        for header, value in wav.items():
            self.write(f'{header} {value}')
        not_restored = [h for h, value in wav.items() if self.query(f'{h}?') != value]
        if not_restored:
            raise RuntimeError(f'set_state: not restored: {not_restored}')
        # The block does not carry the recording switch either. Measured: about 0.9 s after a
        # block is loaded the scope switches recording off, so switching it on earlier
        # does not last. Wait, set it, then confirm that it stayed.
        if state['recording']:
            want = '1'
            time.sleep(self._RECORDING_RESTORE_DELAY_S)
            self.write(':REC:WREC:ENAB ON')
            time.sleep(0.3)
        else:
            want = '0'
            self.write(':REC:WREC:ENAB OFF')
        if self.query(':REC:WREC:ENAB?') != want:
            raise RuntimeError('set_state: recording switch was not restored')
        errors = self.check_errors()
        if errors:
            raise RuntimeError(f'set_state produced errors: {errors}')

    def apply_setup(self, setup: dict) -> None:
        """Write and verify a dict of SCPI header -> int/float/bool/str value."""
        failures = []
        for key, value in setup.items():
            sent = 'ON' if value is True else 'OFF' if value is False else value
            if isinstance(sent, float):
                sent = format(sent, '.9G')   # 0.00019999999999999998 was read by the scope as 198 us
            self.write(f'{key} {sent}')
            errors = self.check_errors()
            try:
                readback = self.query(f'{key}?')
            except Exception as exc:  # noqa: BLE001 - an unknown header is never answered
                readback = None
                errors = errors + [f'no answer to {key}?: {type(exc).__name__}'] + self.check_errors()
            if errors or not _values_match(value, readback):
                failures.append(
                    f'{key}: sent={value!r} readback={readback!r} errors={errors}'
                )
        self._wav_setup = None
        if failures:
            raise RuntimeError('apply_setup failed: ' + '; '.join(failures))

    def apply_capture(self, capture: Capture | str | Path, reset: bool = True) -> Capture:
        """Apply a `Capture` (or the path to a capture YAML file) to the scope.

        Loading a path happens before anything is sent to the scope, so a broken file raises
        `capture.CaptureError` without side effects. `reset=True` (the default) resets the scope
        first, so the capture describes the difference from factory defaults.
        """
        if not isinstance(capture, Capture):
            capture = load_capture(capture)
        if reset:
            self.reset()
        self.apply_setup(capture.to_scpi())
        return capture

    # ── lifecycle ─────────────────────────────────────────────

    def tearDown(self) -> None:
        if self._restore_state and self._initial_state is not None:
            try:
                self.set_state(self._initial_state)
            except Exception as exc:  # noqa: BLE001 - must not block closing the resource
                self.logger.warning('Failed to restore scope state: %s', exc)
        self._resource.close()
