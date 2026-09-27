"""Prototype for variant B: capture conditions as readable text with units (YAML), checked when loaded."""
import re
from pathlib import Path

import yaml

from capture import Capture, Channel, Edge, PerDiv, Seconds, Volts

_PREFIX = {"": 1.0, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "n": 1e-9, "k": 1e3}
_NUM = r"(?P<num>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
_QUANTITY = re.compile(rf"^\s*{_NUM}\s*(?P<prefix>[munµk]?)(?P<unit>V|s)(?P<perdiv>\s*/\s*div)?\s*$")


class CaptureError(ValueError):
    """All problems found in one capture description, each with the path of the offending key."""


def _quantity(text, want_unit: str, per_div: bool, where: str, problems: list[str]):
    m = _QUANTITY.match(str(text))
    expected = f"a value like '1 {'m' if want_unit == 's' else ''}{want_unit}{'/div' if per_div else ''}'"
    if not m:
        problems.append(f"{where}: {text!r} is not {expected}")
        return None
    if m["unit"] != want_unit:
        problems.append(f"{where}: {text!r} is in {'volts' if m['unit'] == 'V' else 'seconds'}, expected {expected}")
        return None
    if bool(m["perdiv"]) != per_div:
        problems.append(f"{where}: {text!r} {'must not have' if m['perdiv'] else 'is missing'} '/div', expected {expected}")
        return None
    q = (Volts if want_unit == "V" else Seconds)(float(m["num"]) * _PREFIX[m["prefix"]])
    return PerDiv(q) if per_div else q


def _keys(d, allowed: set[str], required: set[str], where: str, problems: list[str]) -> dict:
    if not isinstance(d, dict):
        problems.append(f"{where}: expected a block of settings, got {d!r}")
        return {}
    for k in d:
        if k not in allowed:
            import difflib
            hint = difflib.get_close_matches(str(k), allowed, 1)
            problems.append(f"{where}.{k}: unknown setting" + (f", did you mean '{hint[0]}'?" if hint else f" (allowed: {', '.join(sorted(allowed))})"))
    for k in required - d.keys():
        problems.append(f"{where}.{k}: missing")
    return d


def _choice(v, allowed: tuple, where: str, problems: list[str], default=None):
    if v is None:
        return default
    if v not in allowed:
        problems.append(f"{where}: {v!r} is not one of {', '.join(map(str, allowed))}")
        return default
    return v


def parse_capture(data: dict, source: str = "capture") -> Capture:
    problems: list[str] = []
    top = _keys(data, {"timebase", "trigger", "memory", "ch1", "ch2", "ch3", "ch4", "extra"}, {"timebase", "trigger"}, source, problems)
    timebase = _quantity(top.get("timebase"), "s", True, f"{source}.timebase", problems) if "timebase" in top else None
    channels = {}
    for name in ("ch1", "ch2", "ch3", "ch4"):
        if name in top:
            c = _keys(top[name], {"scale", "offset", "coupling", "probe"}, {"scale"}, f"{source}.{name}", problems)
            scale = _quantity(c.get("scale"), "V", True, f"{source}.{name}.scale", problems) if "scale" in c else None
            offset = _quantity(c.get("offset", "0 V"), "V", False, f"{source}.{name}.offset", problems)
            coupling = _choice(c.get("coupling"), ("DC", "AC", "GND"), f"{source}.{name}.coupling", problems, "DC")
            probe = _choice(c.get("probe"), (1, 10, 100), f"{source}.{name}.probe", problems, 1)
            if scale is not None and offset is not None:
                channels[name] = Channel(scale=scale, offset=offset, coupling=coupling, probe=probe)
    trigger = None
    if "trigger" in top:
        t = _keys(top["trigger"], {"type", "source", "level", "slope", "mode"}, {"source", "level"}, f"{source}.trigger", problems)
        _choice(t.get("type"), ("edge",), f"{source}.trigger.type", problems, "edge")
        src = _choice(t.get("source"), ("ch1", "ch2", "ch3", "ch4"), f"{source}.trigger.source", problems)
        level = _quantity(t.get("level"), "V", False, f"{source}.trigger.level", problems) if "level" in t else None
        slope = _choice(t.get("slope"), ("rising", "falling", "either"), f"{source}.trigger.slope", problems, "rising")
        mode = _choice(t.get("mode"), ("auto", "normal", "single"), f"{source}.trigger.mode", problems, "normal")
        if src and level is not None:
            if src not in top:
                problems.append(f"{source}.trigger.source: {src!r} is the trigger source but {src} is not described")
            trigger = Edge(source=src, level=level, slope=slope, mode=mode)
    memory = _choice(str(top.get("memory", "auto")), ("auto", "1k", "10k", "100k", "1M", "10M", "25M"), f"{source}.memory", problems, "auto")
    if problems:
        raise CaptureError("\n  ".join([f"{len(problems)} problem(s) in {source}:"] + problems))
    return Capture(timebase=timebase, trigger=trigger, memory=memory, extra=top.get("extra", {}), **channels)


def load_capture(path) -> Capture:
    path = Path(path)
    return parse_capture(yaml.safe_load(path.read_text()), source=path.name)
