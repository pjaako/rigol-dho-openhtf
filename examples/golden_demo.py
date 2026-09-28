"""Golden-waveform demo: teach a reference from known-good captures, then let a mask decide PASS
/ FAIL without an operator.

    python examples/golden_demo.py teach --fake -n 10 --golden golden/clock.npz   # no hardware
    python examples/golden_demo.py teach -n 10 --golden golden/clock.npz          # scope on USB
    python examples/golden_demo.py run --golden golden/clock.npz                  # web GUI, port 4444
    python examples/golden_demo.py run --fake --fake-defect gain --show-failures  # exercise a bad unit

`teach` takes `n` single captures, builds a `Golden` (golden.py) and saves it plus a picture of the
reference and mask. `run` is an OpenHTF station like operator_demo.py, but the verdict comes from
`golden.compare()` instead of an operator.
"""
import argparse
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `import common`; see operator_demo.py

import numpy as np
import openhtf as htf
from openhtf.output.callbacks import json_factory
from openhtf.output.servers import station_server
from openhtf.plugs import user_input
from openhtf.util import configuration, units

from capture import load_capture
from common import frequency_hz, waveform_npz_bytes
from fake_resource import FakeDhoResource
from golden import Golden, compare, teach
from rigol_dho_plug import RigolDhoPlug

DEFAULT_CAPTURE = ROOT / 'captures' / 'clock.yaml'
DEFAULT_GOLDEN = ROOT / 'golden' / 'clock.npz'
RESULTS = Path(__file__).resolve().parent / 'results'


def make_fake_scope_plug(defect: str | None = None) -> type:
    """A RigolDhoPlug backed by the fake, with the bounded 'clock' test signal (the default
    sawtooth touches both ends of the code range and would always look clipped to golden.py; see
    fake_resource.py). `defect` layers a bad-unit fault on top, for `--fake-defect`."""
    class _FakePlug(RigolDhoPlug):
        def __init__(self):
            super().__init__(resource=FakeDhoResource(signal='clock', defect=defect))
    return _FakePlug


def _plot_golden(golden: Golden) -> bytes:
    """Picture of the taught reference and its mask (no captured waveform to overlay)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import io

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=100)
    t_ms = golden.t * 1e3
    ax.fill_between(t_ms, golden.lo, golden.hi, color='#a8c8f0', alpha=0.6, linewidth=0, label='mask')
    ax.plot(t_ms, golden.ref, color='#404040', linewidth=1.0, label='reference')
    ax.set_xlabel('time, ms')
    ax.set_ylabel('voltage, V')
    ax.set_title(f'golden reference: n={golden.n_taught}  v_tol={golden.v_tol * 1e3:.1f} mV  '
                 f't_tol={golden.t_tol} samples')
    ax.grid(True, alpha=0.4)
    ax.margins(x=0)
    ax.legend(loc='upper right', fontsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    plt.close(fig)
    return buf.getvalue()


# ── phases (importable and runnable without the web GUI or a prompt) ───────

def capture_and_compare_phase(golden_path, capture_path):
    """Build the `capture_and_compare` phase for the golden file at `golden_path` and the capture
    conditions at `capture_path`. Swap in a fake scope with `.with_plugs(scope=...)`."""

    @htf.measures(
        htf.Measurement('mask_violations').equals(0),
        htf.Measurement('worst_excess').with_units(units.VOLT),
        htf.Measurement('worst_deviation').with_units(units.VOLT),
        htf.Measurement('vpp').with_units(units.VOLT),
        htf.Measurement('frequency').with_units(units.HERTZ),
    )
    @htf.plug(scope=RigolDhoPlug)
    def capture_and_compare(test, scope):
        """Apply the capture file, take one capture, compare it with the golden reference."""
        golden = Golden.load(golden_path)
        scope.apply_capture(capture_path)
        scope.single()
        scope.wait_stopped(timeout_s=5)
        w = scope.read_waveform(1)
        scope.run()

        comparison = compare(w, golden)
        test.measurements.mask_violations = comparison.violations
        test.measurements.worst_excess = comparison.worst_excess
        test.measurements.worst_deviation = comparison.worst_deviation
        test.measurements.vpp = float(w.v.max() - w.v.min())
        test.measurements.frequency = frequency_hz(w)

        title = (f'violations {comparison.violations}   worst excess '
                 f'{comparison.worst_excess * 1e3:.1f} mV')
        png = comparison.plot(title)
        test.attach('comparison.png', png, 'image/png')
        test.attach('waveform.npz', waveform_npz_bytes(w), 'application/octet-stream')
        test.state['comparison'] = comparison
        test.state['comparison_png'] = png

    return capture_and_compare


def show_failure_phase():
    """Build the `show_failure` phase: shown only when the comparison failed, only acknowledged
    (no text input), records nothing and cannot change the test outcome."""

    @htf.plug(prompts=user_input.UserInput)
    def show_failure(test, prompts):
        comparison = test.state.get('comparison')
        if comparison is None or comparison.passed:
            return htf.PhaseResult.SKIP
        message = (
            f'Golden comparison FAILED: {comparison.violations} sample(s) outside the mask, '
            f'worst excess {comparison.worst_excess * 1e3:.1f} mV, '
            f'worst deviation {comparison.worst_deviation * 1e3:.1f} mV.'
        )
        image = 'data:image/png;base64,' + base64.b64encode(test.state['comparison_png']).decode('ascii')
        prompts.prompt(message, text_input=False, image_url=image)
        return htf.PhaseResult.CONTINUE

    return show_failure


# ── teach ────────────────────────────────────────────────────────────────

def cmd_teach(args) -> int:
    configuration.CONF.load(rigol_dho_resource=args.resource)
    capture = load_capture(args.capture)
    scope = make_fake_scope_plug()() if args.fake else RigolDhoPlug()
    try:
        scope.apply_capture(capture)
        waveforms = []
        for _ in range(args.n):
            scope.single()
            scope.wait_stopped(timeout_s=5)
            waveforms.append(scope.read_waveform(1))
        scope.run()
    finally:
        scope.tearDown()

    golden = teach(waveforms, v_tol=args.v_tol, t_tol=args.t_tol, capture=capture)
    golden.save(args.golden)
    png_path = Path(str(args.golden) + '.png')
    png_path.write_bytes(_plot_golden(golden))

    worst_deviation = max(float(np.max(np.abs(w.v - golden.ref))) for w in waveforms)
    print(f'taught from {golden.n_taught} captures')
    print(f'v_tol chosen: {golden.v_tol * 1e3:.1f} mV   t_tol: {golden.t_tol} samples')
    print(f'worst deviation seen while teaching: {worst_deviation * 1e3:.1f} mV')
    print(f'saved {args.golden} and {png_path}')
    return 0


# ── run ──────────────────────────────────────────────────────────────────

def cmd_run(args) -> int:
    if not Path(args.golden).exists():
        print(f'{args.golden}: no golden file. Run `golden_demo.py teach --golden {args.golden}` first.',
              file=sys.stderr)
        return 1

    configuration.CONF.load(station_server_port=args.port, rigol_dho_resource=args.resource)
    RESULTS.mkdir(exist_ok=True)

    phase = capture_and_compare_phase(args.golden, args.capture)
    if args.fake:
        phase = phase.with_plugs(scope=make_fake_scope_plug(args.fake_defect))
    phases = [phase]
    if args.show_failures:
        phases.append(show_failure_phase())

    with station_server.StationServer() as server:
        print(f'Web GUI: http://localhost:{args.port}  (from another machine use this machine\'s address)')
        while True:
            test = htf.Test(*phases, test_name='golden_demo')
            test.add_output_callbacks(server.publish_final_state)
            test.add_output_callbacks(json_factory.OutputToJSON(
                str(RESULTS / '{dut_id}.{start_time_millis}.json'), indent=2))
            passed = test.execute(test_start=user_input.prompt_for_test_start())
            print('result:', 'PASS' if passed else 'FAIL')
            if args.once:
                break
    return 0


# ── command line ───────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--fake', action='store_true', help='use the fake scope, no hardware')
    common.add_argument('--resource', default='', help='VISA resource; default: first Rigol on USB')
    common.add_argument('--capture', default=str(DEFAULT_CAPTURE), help='capture conditions YAML')
    common.add_argument('--golden', default=str(DEFAULT_GOLDEN), help='golden .npz file')

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    teach_p = sub.add_parser('teach', parents=[common], help='teach a golden reference')
    teach_p.add_argument('-n', type=int, default=10, help='number of captures to teach from')
    teach_p.add_argument('--v-tol', type=float, default=None, help='voltage tolerance, volts (default: automatic)')
    teach_p.add_argument('--t-tol', type=int, default=2, help='time tolerance, samples')

    run_p = sub.add_parser('run', parents=[common], help='run the station, verdict from the golden mask')
    run_p.add_argument('--port', type=int, default=4444, help='port of the web GUI')
    run_p.add_argument('--once', action='store_true', help='run one test and exit')
    run_p.add_argument('--show-failures', action='store_true',
                        help='show the comparison plot to the operator when a capture fails')
    run_p.add_argument('--fake-defect', choices=['shift', 'gain', 'glitch'], default=None,
                        help='inject a bad-unit fault into the fake scope (with --fake)')
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == 'teach':
        return cmd_teach(args)
    return cmd_run(args)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
