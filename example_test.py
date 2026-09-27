"""Minimal OpenHTF test using the Rigol DHO plug.

Usage:
    python example_test.py          # real scope on USB
    python example_test.py --fake   # no hardware
"""
import argparse
import sys

import openhtf as htf
from openhtf.util import units

from rigol_dho_plug import RigolDhoPlug


@htf.measures(
    htf.Measurement('vpp').with_units(units.VOLT),
    htf.Measurement('num_points'),
)
@htf.plug(scope=RigolDhoPlug)
def capture_waveform(test, scope):
    """Read channel 1 in NORM mode and record vpp and num_points."""
    w = scope.read_waveform(1, mode='NORM', fmt='BYTE')
    test.measurements.vpp = float(w.v.max() - w.v.min())
    test.measurements.num_points = len(w.raw)
    print(f'vpp={test.measurements.vpp:.4f} num_points={test.measurements.num_points}')


class FakePlug(RigolDhoPlug):
    def __init__(self):
        from fake_resource import FakeDhoResource
        super().__init__(resource=FakeDhoResource())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fake', action='store_true', help='use the fake scope')
    args = parser.parse_args()
    phase = capture_waveform.with_plugs(scope=FakePlug) if args.fake else capture_waveform
    passed = htf.Test(phase).execute(test_start=lambda: 'example_dut')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
