"""Minimal OpenHTF test using the Rigol DHO plug.

Run against real hardware:
    python example_test.py

No station server, no output callbacks – console summary only.
"""

from __future__ import annotations


if __name__ == '__main__':
    import openhtf as htf
    from rigol_dho_plug import RigolDhoPlug

    @htf.plug(scope=RigolDhoPlug)
    def capture_waveform(test):
        """Read channel 1 in NORM mode and record vpp and num_points."""
        w = test.scope.read_waveform(1, mode='NORM', fmt='BYTE')
        test.record_measurement('vpp', float(w.v.max() - w.v.min()))
        test.record_measurement('num_points', len(w.raw))

    test = htf.Test(capture_waveform)
    test.add_output_callback(htf.ConsoleOutputCallback())
    test.execute(test_start=lambda: 'example_dut')
