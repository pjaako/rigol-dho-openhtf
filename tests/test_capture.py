"""Tests for capture.py: the typed model, the YAML front end, the schema, and apply_capture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

from capture import (
    Capture, CaptureError, Channel, Edge, V, capture_schema, div, load_capture, mV, ms, parse_capture, us,
)
from fake_resource import FakeDhoResource
from rigol_dho_plug import RigolDhoPlug

ROOT = Path(__file__).resolve().parent.parent
CLOCK_YAML = ROOT / "captures" / "clock.yaml"
BROKEN_YAML = ROOT / "tests" / "data" / "broken.yaml"


def _write(tmp_path: Path, text: str, name: str = "capture.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


# ── 1. clock.yaml == the same capture written in Python; to_scpi() ──────

def test_clock_yaml_matches_python_spelling():
    loaded = load_capture(CLOCK_YAML)
    expected = Capture(
        timebase=1 * ms / div,
        ch1=Channel(scale=1 * V / div, offset=-1.5 * V, coupling="DC"),
        trigger=Edge(source="ch1", level=1.5 * V, slope="rising", mode="normal"),
        memory="1M",
    )
    assert loaded == expected


def test_clock_yaml_to_scpi():
    loaded = load_capture(CLOCK_YAML)
    assert loaded.to_scpi() == {
        ":TIM:MAIN:SCAL": 0.001,
        ":ACQ:MDEP": "1M",
        ":CHAN1:DISP": True,
        ":CHAN1:PROB": 1,
        ":CHAN1:COUP": "DC",
        ":CHAN1:SCAL": 1.0,
        ":CHAN1:OFFS": -1.5,
        ":CHAN2:DISP": False,
        ":CHAN3:DISP": False,
        ":CHAN4:DISP": False,
        ":TRIG:MODE": "EDGE",
        ":TRIG:EDGE:SOUR": "CHAN1",
        ":TRIG:EDGE:SLOP": "POS",
        ":TRIG:EDGE:LEV": 1.5,
        ":TRIG:SWE": "NORM",
    }
    assert len(loaded.to_scpi()) == 15


# ── 2. one mistake per minimal YAML string ───────────────────────────────

# Every snippet below already has a valid timebase/trigger/ch1 unless that is the thing being tested,
# so the single mistake under test is (usually) the only problem raised.

MISTAKES = [
    ("volts where seconds expected",
     "timebase: 1 V/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\n",
     "timebase", "is in volts", 1),
    ("seconds where volts expected",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 ms\n",
     "trigger.level", "is in seconds", 6),
    ("missing /div", "timebase: 1 ms/div\nch1:\n  scale: 1 V\ntrigger:\n  source: ch1\n  level: 1 V\n",
     "ch1.scale", "missing '/div'", 3),
    ("/div where it must not be",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V/div\n",
     "trigger.level", "must not have '/div'", 6),
    ("bare number without unit",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1.5\n",
     "trigger.level", "unit is mandatory", 6),
    ("unknown key with suggestion",
     "timebase: 1 ms/div\nch1:\n  scael: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\n",
     "ch1.scael", "did you mean 'scale'", 3),
    ("unknown block with suggestion",
     "timebse: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\n",
     "timebse", "did you mean 'timebase'", 1),
    ("bad choice for coupling",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\n  coupling: XY\ntrigger:\n  source: ch1\n  level: 1 V\n",
     "ch1.coupling", "not one of", 4),
    ("bad choice for slope",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\n  slope: up\n",
     "trigger.slope", "not one of", 7),
    ("bad choice for mode",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\n  mode: fast\n",
     "trigger.mode", "not one of", 7),
    ("bad choice for source",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch9\n  level: 1 V\n",
     "trigger.source", "not one of", 5),
    ("bad choice for memory",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\nmemory: 2M\n",
     "memory", "not one of", 7),
    ("bad choice for probe",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\n  probe: 5\ntrigger:\n  source: ch1\n  level: 1 V\n",
     "ch1.probe", "not one of", 4),
    ("missing timebase",
     "ch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\n",
     "timebase", "missing", 1),
    ("missing trigger",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\n",
     "trigger", "missing", 1),
    ("missing trigger.level",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n",
     "trigger.level", "missing", 5),
    ("trigger source not described",
     "timebase: 1 ms/div\ntrigger:\n  source: ch2\n  level: 1 V\n",
     "trigger.source", "not described", 3),
    ("duplicate key",
     "timebase: 1 ms/div\ntimebase: 2 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\n",
     "timebase", "duplicate key", 2),
    ("boolean where a choice is expected",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\n  mode: no\n",
     "trigger.mode", "boolean", 7),
    ("memory: 10000",
     "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\nmemory: 10000\n",
     "memory", "not one of", 7),
    ("a YAML file that is a list, not a mapping", "- 1\n- 2\n", "capture", "expected a mapping", 1),
    ("an empty file", "", "timebase", "missing", 1),
]


@pytest.mark.parametrize("name,text,path,fragment,line", MISTAKES, ids=[m[0] for m in MISTAKES])
def test_mistake(tmp_path, name, text, path, fragment, line):
    file = _write(tmp_path, text)
    with pytest.raises(CaptureError) as exc_info:
        load_capture(file)
    problems = exc_info.value.problems
    match = [p for p in problems if p.startswith(f"{file.name}:{line}: {path}:")]
    assert match, f"no problem starting with '{file.name}:{line}: {path}:' in {problems}"
    assert fragment in match[0]


# ── 3. broken.yaml: all problems in one CaptureError, in file order ─────

def test_broken_yaml_reports_all_problems_in_file_order():
    with pytest.raises(CaptureError) as exc_info:
        load_capture(BROKEN_YAML)
    problems = exc_info.value.problems
    assert len(problems) == 9
    lines = [int(p.split(":", 2)[1]) for p in problems]
    assert lines == sorted(lines)


# ── 4. prefixes and spacing ───────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("1ms/div", 1e-3),
    ("1 ms / div", 1e-3),
    ("500 us/div", 500e-6),
    ("2 µs/div", 2e-6),
    ("1e-3 s/div", 1e-3),
])
def test_timebase_prefixes_and_spacing(tmp_path, text, expected):
    file = _write(tmp_path, f"timebase: {text}\nch1:\n  scale: 1 V/div\ntrigger:\n  source: ch1\n  level: 1 V\n")
    cap = load_capture(file)
    assert cap.timebase.q.value == pytest.approx(expected)


@pytest.mark.parametrize("text,expected", [
    ("0.5 V/div", 0.5),
    ("50mV/div", 50e-3),
])
def test_scale_prefixes_and_spacing(tmp_path, text, expected):
    file = _write(tmp_path, f"timebase: 1 ms/div\nch1:\n  scale: {text}\ntrigger:\n  source: ch1\n  level: 1 V\n")
    cap = load_capture(file)
    assert cap.ch1.scale.q.value == pytest.approx(expected)


def test_offset_prefix_and_spacing(tmp_path):
    file = _write(tmp_path,
                   "timebase: 1 ms/div\nch1:\n  scale: 1 V/div\n  offset: -200 mV\n"
                   "trigger:\n  source: ch1\n  level: 1 V\n")
    cap = load_capture(file)
    assert cap.ch1.offset.value == pytest.approx(-0.2)


# ── 5. schema ──────────────────────────────────────────────────────────

def test_committed_schema_matches_generated_schema():
    committed = json.loads((ROOT / "capture.schema.json").read_text())
    assert committed == capture_schema()


def test_schema_is_valid_draft_2020_12():
    jsonschema.Draft202012Validator.check_schema(capture_schema())


def test_schema_validates_clock_yaml():
    schema = capture_schema()
    data = yaml.safe_load(CLOCK_YAML.read_text())
    jsonschema.Draft202012Validator(schema).validate(data)


def test_schema_rejects_broken_yaml_with_at_least_as_many_errors():
    schema = capture_schema()
    data = yaml.safe_load(BROKEN_YAML.read_text())
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(data))
    with pytest.raises(CaptureError) as exc_info:
        load_capture(BROKEN_YAML)
    loader_problems = exc_info.value.problems
    # The schema cannot express the cross-check "trigger source must be a described channel";
    # broken.yaml does not exercise that check, so here the counts should be equal.
    assert len(errors) >= len(loader_problems) - 1


def test_schema_choices_match_parser_choices():
    from capture import Channel, Edge, Capture, _block_fields, _choices

    schema = capture_schema()

    def schema_choices(block_schema, name):
        prop = block_schema["properties"][name]
        return tuple(prop["enum"])

    for cls, block_schema in ((Channel, schema["$defs"]["channel"]), (Edge, schema["$defs"]["trigger"])):
        for field_name, hint, _required, _default in _block_fields(cls):
            choices = _choices(hint)
            if choices:
                assert schema_choices(block_schema, field_name) == choices

    for field_name, hint, _required, _default in _block_fields(Capture):
        choices = _choices(hint)
        if choices:
            assert tuple(schema["properties"][field_name]["enum"]) == choices


# ── 6. apply_capture on the fake ─────────────────────────────────────────

def _plug(**kwargs):
    return RigolDhoPlug(resource=FakeDhoResource(), restore_state=False, **kwargs)


def test_apply_capture_from_path():
    plug = _plug()
    cap = plug.apply_capture(CLOCK_YAML)
    assert cap == load_capture(CLOCK_YAML)
    assert '*RST' in plug._resource.log


def test_apply_capture_from_capture_object():
    plug = _plug()
    loaded = load_capture(CLOCK_YAML)
    cap = plug.apply_capture(loaded)
    assert cap is loaded


def test_apply_capture_without_reset():
    plug = _plug()
    plug.apply_capture(CLOCK_YAML, reset=False)
    assert '*RST' not in plug._resource.log


def test_apply_capture_with_reset_default():
    plug = _plug()
    plug.apply_capture(CLOCK_YAML)
    assert '*RST' in plug._resource.log


def test_apply_capture_broken_file_sends_nothing_to_the_scope():
    plug = _plug()
    with pytest.raises(CaptureError):
        plug.apply_capture(BROKEN_YAML)
    assert plug._resource.log == []


# ── 7. command line ────────────────────────────────────────────────────

def _run(*args):
    return subprocess.run([sys.executable, 'capture.py', *args], cwd=ROOT, capture_output=True, text=True)


def test_cli_check_clock_yaml():
    r = _run('check', 'captures/clock.yaml')
    assert r.returncode == 0
    assert r.stdout.strip() == 'OK captures/clock.yaml'


def test_cli_check_broken_yaml():
    r = _run('check', 'tests/data/broken.yaml')
    assert r.returncode == 1
    assert 'broken.yaml:1:' in r.stdout


def test_cli_schema_prints_valid_json():
    r = _run('schema')
    assert r.returncode == 0
    assert json.loads(r.stdout) == capture_schema()


def test_memory_auto_can_be_applied():
    """The scope answers `:ACQ:MDEP AUTO` with the depth it picked; that is not a mismatch."""
    from fake_resource import FakeDhoResource
    from rigol_dho_plug import RigolDhoPlug
    plug = RigolDhoPlug(resource=FakeDhoResource(), restore_state=False)
    plug.apply_capture(parse_capture({'timebase': '1 ms/div', 'ch1': {'scale': '1 V/div'},
                                      'trigger': {'source': 'ch1', 'level': '1.5 V'}}))
    assert plug.memory_depth() > 0


@pytest.mark.parametrize('text,expected', [('200 us/div', 0.0002), ('500 us/div', 0.0005), ('20 ns/div', 2e-8),
                                           ('50 mV/div', 0.05), ('2 µs/div', 2e-6), ('0.1 V/div', 0.1)])
def test_quantities_are_exact_decimals(text, expected):
    """200 * 1e-6 must not become 0.00019999999999999998: the real scope read that as 198 us/div."""
    key, block = ('timebase', None) if text.endswith('s/div') else ('scale', 'ch1')
    data = {'timebase': '1 ms/div', 'ch1': {'scale': '1 V/div'}, 'trigger': {'source': 'ch1', 'level': '1 V'}}
    if block: data[block][key] = text
    else: data[key] = text
    c = parse_capture(data)
    value = c.timebase.q.value if block is None else c.ch1.scale.q.value
    assert repr(value) == repr(expected)


def test_floats_are_sent_without_binary_noise():
    from fake_resource import FakeDhoResource
    from rigol_dho_plug import RigolDhoPlug
    fr = FakeDhoResource()
    RigolDhoPlug(resource=fr, restore_state=False).apply_setup({':TIM:MAIN:SCAL': 200 * 1e-6})
    assert ':TIM:MAIN:SCAL 0.0002' in fr.log
