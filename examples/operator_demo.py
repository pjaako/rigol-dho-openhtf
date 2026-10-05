"""Operator demo: capture a waveform, show it in the OpenHTF web GUI, let the operator decide PASS / FAIL.

    python examples/operator_demo.py --fake                     # no hardware
    python examples/operator_demo.py                            # scope on USB
    python examples/operator_demo.py --resource TCPIP::192.0.2.10::5555::SOCKET

Then open http://<this machine>:4444 in a browser, enter a DUT id, look at the waveform, answer P or F.
"""
import argparse
import base64
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import openhtf as htf
from PIL import Image
from openhtf.output.callbacks import json_factory
from openhtf.output.servers import station_server
from openhtf.plugs import user_input
from openhtf.util import configuration, units

from common import frequency_hz, waveform_npz_bytes  # next to this script; 'examples' collides with a
# package of the same name in site-packages, so this can't be `from examples.common import ...`
from rigol_dho_openhtf import RigolDhoPlug

CAPTURE = ROOT / 'captures' / 'clock.yaml'
RESULTS = Path(__file__).resolve().parent / 'results'
ANSWERS = {'P': 'PASS', 'PASS': 'PASS', 'F': 'FAIL', 'FAIL': 'FAIL'}


class FakePlug(RigolDhoPlug):
    def __init__(self):
        from rigol_dho_openhtf.fake_resource import FakeDhoResource
        super().__init__(resource=FakeDhoResource())


def plot_png(w, title: str) -> bytes:
    """Render the waveform the way the operator should judge it: volts over milliseconds."""
    fig, ax = plt.subplots(figsize=(10.24, 4.2), dpi=100)
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


def composite_png(screen_png: bytes, plot_png: bytes) -> bytes:
    """One picture for the prompt (stock OpenHTF shows one image): the scope screen on top, the plot below.
    Both are 1024 px wide, so neither is scaled."""
    screen = Image.open(io.BytesIO(screen_png)).convert('RGB')
    plot = Image.open(io.BytesIO(plot_png)).convert('RGB')
    out = Image.new('RGB', (screen.width, screen.height + plot.height))
    out.paste(screen, (0, 0))
    out.paste(plot, (0, screen.height))
    buf = io.BytesIO()
    out.save(buf, format='PNG')
    return buf.getvalue()


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
    screen = scope.screenshot()   # before run(): the screen still shows the capture that was read
    scope.run()
    test.measurements.vpp = float(w.v.max() - w.v.min())
    test.measurements.frequency = frequency_hz(w)
    test.measurements.num_points = len(w.raw)

    title = f'CH1   Vpp {test.measurements.vpp:.3f} V   f {test.measurements.frequency:.1f} Hz'
    png = plot_png(w, title)
    test.attach('waveform.png', png, 'image/png')
    test.attach('screen.png', screen, 'image/png')
    test.attach('waveform.npz', waveform_npz_bytes(w), 'application/octet-stream')
    test.state['prompt_png'] = composite_png(screen, png)
    test.state['waveform_title'] = title


@htf.measures(
    htf.Measurement('operator_verdict').equals('PASS'),
)
@htf.plug(prompts=user_input.UserInput)
def operator_judges(test, prompts):
    """Show the waveform and ask the operator."""
    image = 'data:image/png;base64,' + base64.b64encode(test.state['prompt_png']).decode('ascii')
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
