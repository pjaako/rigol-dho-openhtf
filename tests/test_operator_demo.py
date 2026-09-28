"""Tests for examples/operator_demo.py: the composite picture and the two phases (fake scope only;
see AGENTS.md, the real scope is off limits)."""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

import numpy as np
import openhtf as htf
from PIL import Image

from fake_resource import FakeDhoResource

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'examples'))
import operator_demo  # noqa: E402  (path must be set up first)

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def _screen() -> bytes:
    return FakeDhoResource().query_binary_values(':DISP:DATA? PNG', datatype='B', container=bytes)


def _plot() -> bytes:
    from rigol_dho_plug import RigolDhoPlug
    w = RigolDhoPlug(resource=FakeDhoResource(), restore_state=False).read_waveform(1)
    return operator_demo.plot_png(w, 'test')


def test_composite_png_stacks_screen_over_plot():
    screen, plot = _screen(), _plot()
    out = Image.open(io.BytesIO(operator_demo.composite_png(screen, plot)))
    plot_height = Image.open(io.BytesIO(plot)).height
    assert out.width == 1024
    assert out.height == 600 + plot_height
    top = np.asarray(out.convert('RGB'))[:600]
    assert np.array_equal(top, np.asarray(Image.open(io.BytesIO(screen)).convert('RGB')))


def _run_capture_phase():
    """Run capture_waveform on the fake; return (test record, the fake resource used)."""
    resources, records = [], []

    class Plug(operator_demo.FakePlug):
        def __init__(self):
            super().__init__()
            resources.append(self._resource)

    test = htf.Test(operator_demo.capture_waveform.with_plugs(scope=Plug), test_name='t')
    test.add_output_callbacks(records.append)
    test.execute(test_start=lambda: 'dut')
    return records[0], resources[0]


def test_capture_waveform_attaches_plot_screen_and_samples():
    record, _ = _run_capture_phase()
    phase = record.phases[-1]
    assert phase.outcome.name == 'PASS'
    assert {'waveform.png', 'screen.png', 'waveform.npz'} <= set(phase.attachments)
    assert phase.attachments['screen.png'].data[:8] == PNG_SIGNATURE


def test_screenshot_is_taken_after_the_waveform_read_and_before_run():
    _, resource = _run_capture_phase()
    log = resource.log
    shot = log.index(':DISP:DATA? PNG')
    assert log.index(':WAV:DATA?') < shot
    assert ':RUN' in log[shot:]          # run() comes after the screenshot
    assert ':RUN' not in log[:shot]


def test_operator_judges_shows_the_composite():
    judge = operator_demo.operator_judges.func
    composite = operator_demo.composite_png(_screen(), _plot())

    class _Prompts:
        def __init__(self):
            self.calls = []

        def prompt(self, message, text_input=False, timeout_s=None, cli_color='', image_url=None):
            self.calls.append(image_url)
            return 'P'

    class _Measurements:
        pass

    class _Test:
        state = {'prompt_png': composite, 'waveform_title': 'title'}
        measurements = _Measurements()

    prompts = _Prompts()
    judge(_Test(), prompts)
    prefix = 'data:image/png;base64,'
    assert prompts.calls[0].startswith(prefix)
    assert base64.b64decode(prompts.calls[0][len(prefix):]) == composite
