"""Operator demo: capture a waveform, show it in the OpenHTF web GUI, let the operator decide PASS / FAIL.

    python examples/operator_demo.py --fake                     # no hardware
    python examples/operator_demo.py                            # scope on USB
    python examples/operator_demo.py --resource TCPIP::192.0.2.10::5555::SOCKET

Then open http://<this machine>:4444 in a browser, enter a DUT id, look at the waveform, answer P or F.
"""
import argparse
import base64
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import openhtf as htf
from openhtf.output.callbacks import json_factory
from openhtf.output.servers import station_server
from openhtf.plugs import user_input
from openhtf.util import configuration, units

from rigol_dho_plug import RigolDhoPlug

CAPTURE = ROOT / 'captures' / 'clock.yaml'
RESULTS = Path(__file__).resolve().parent / 'results'
ANSWERS = {'P': 'PASS', 'PASS': 'PASS', 'F': 'FAIL', 'FAIL': 'FAIL'}


class FakePlug(RigolDhoPlug):
    def __init__(self):
        from fake_resource import FakeDhoResource
        super().__init__(resource=FakeDhoResource())


def plot_png(w, title: str) -> bytes:
    """Render the waveform the way the operator should judge it: volts over milliseconds."""
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=100)
    ax.plot(w.t * 1e3, w.v, linewidth=1.0, color='#c8a000')
    ax.set_xlabel('time, ms')
    ax.set_ylabel('voltage, V')
    ax.set_title(title)
    ax.grid(True, alpha=0.4)
    ax.margins(x=0)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    plt.close(fig)
    return buf.getvalue()


def frequency_hz(w) -> float:
    """Frequency from rising edges through the mid level, with hysteresis against noise on the edges."""
    lo, hi = np.percentile(w.v, 5), np.percentile(w.v, 95)
    if hi - lo < 1e-3:
        return 0.0
    low_thr, high_thr = lo + 0.3 * (hi - lo), lo + 0.7 * (hi - lo)
    edges, armed = [], False
    for t, v in zip(w.t, w.v):
        if v < low_thr:
            armed = True
        elif v > high_thr and armed:
            edges.append(t)
            armed = False
    return float(1 / np.mean(np.diff(edges))) if len(edges) > 1 else 0.0


@htf.measures(
    htf.Measurement('vpp').with_units(units.VOLT),
    htf.Measurement('frequency').with_units(units.HERTZ),
    htf.Measurement('num_points'),
)
@htf.plug(scope=RigolDhoPlug)
def capture_waveform(test, scope):
    """Set the scope up from captures/clock.yaml and read channel 1."""
    scope.apply_capture(CAPTURE)
    scope.single()
    scope.wait_stopped(timeout_s=5)
    w = scope.read_waveform(1)
    scope.run()
    test.measurements.vpp = float(w.v.max() - w.v.min())
    test.measurements.frequency = frequency_hz(w)
    test.measurements.num_points = len(w.raw)

    title = f'CH1   Vpp {test.measurements.vpp:.3f} V   f {test.measurements.frequency:.1f} Hz'
    png = plot_png(w, title)
    test.attach('waveform.png', png, 'image/png')
    # raw samples + preamble are enough to rebuild volts and time, and 4x smaller than the float arrays
    npz = io.BytesIO()
    np.savez_compressed(npz, raw=w.raw, **w.preamble)
    test.attach('waveform.npz', npz.getvalue(), 'application/octet-stream')
    test.state['waveform_png'] = png
    test.state['waveform_title'] = title


@htf.measures(
    htf.Measurement('operator_verdict').equals('PASS'),
)
@htf.plug(prompts=user_input.UserInput)
def operator_judges(test, prompts):
    """Show the waveform and ask the operator."""
    image = 'data:image/png;base64,' + base64.b64encode(test.state['waveform_png']).decode('ascii')
    message = f"{test.state['waveform_title']}\n\nIs this waveform good? Type P (pass) or F (fail)."
    while True:
        answer = prompts.prompt(message, text_input=True, image_url=image).strip().upper()
        if answer in ANSWERS:
            break
        message = f'"{answer}" is not an answer. Type P (pass) or F (fail).'
    test.measurements.operator_verdict = ANSWERS[answer]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--fake', action='store_true', help='use the fake scope, no hardware')
    parser.add_argument('--resource', default='', help='VISA resource; default: first Rigol on USB')
    parser.add_argument('--port', type=int, default=4444, help='port of the web GUI')
    parser.add_argument('--once', action='store_true', help='run one test and exit')
    args = parser.parse_args()

    configuration.CONF.load(station_server_port=args.port, rigol_dho_resource=args.resource)
    capture = capture_waveform.with_plugs(scope=FakePlug) if args.fake else capture_waveform
    RESULTS.mkdir(exist_ok=True)

    with station_server.StationServer() as server:
        print(f'Web GUI: http://localhost:{args.port}  (from another machine use this machine\'s address)')
        while True:
            test = htf.Test(capture, operator_judges, test_name='operator_demo')
            test.add_output_callbacks(server.publish_final_state)
            test.add_output_callbacks(json_factory.OutputToJSON(
                str(RESULTS / '{dut_id}.{start_time_millis}.json'), indent=2))
            passed = test.execute(test_start=user_input.prompt_for_test_start())
            print('result:', 'PASS' if passed else 'FAIL')
            if args.once:
                break


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
