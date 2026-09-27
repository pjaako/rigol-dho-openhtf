"""Capture conditions for a Rigol DHO scope: a typed model plus a readable YAML front end.

Python code builds a `Capture` with units (`Capture(timebase=1 * ms / div, ...)`); YAML is the front end for
people who do not know SCPI (`load_capture('captures/clock.yaml')`). Both go through the same validation
(`__post_init__`) and end at the same `Capture.to_scpi()`.

The allowed keys, choices and units are defined exactly once, as the dataclass fields below (their type
hints and defaults); `parse_capture`, `capture_schema` and `to_scpi` all read that definition instead of
repeating it. Adding an allowed value (e.g. a coupling mode) is a one-line change to the `Literal` alias.

Command line: `python capture.py schema` prints the JSON Schema; `python capture.py check FILE...` loads
each file and reports `OK` or its problems.
"""
import dataclasses
import difflib
import re
from decimal import Decimal
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeAliasType, Union, get_args, get_origin, get_type_hints


# ── quantities: a float with a dimension ─────────────────────────────────

class _Quantity:
    """A float with a dimension. `1.5 * V` builds one; mixing dimensions is a type error."""
    __slots__ = ("value",)
    unit = "?"

    def __init__(self, value: float) -> None:
        self.value = float(value)

    def __rmul__(self, k: float) -> "_Quantity":            # 1.5 * V
        return type(self)(k * self.value)

    def __neg__(self) -> "_Quantity":
        return type(self)(-self.value)

    def __eq__(self, o: object) -> bool:
        return type(o) is type(self) and o.value == self.value

    def __hash__(self) -> int:
        return hash((type(self), self.value))

    def __repr__(self) -> str:
        return f"{self.value:g} {self.unit}"


class Volts(_Quantity):
    unit = "V"
    def __rmul__(self, k: float) -> "Volts": return Volts(k * self.value)
    def __truediv__(self, d: "_Div") -> "PerDiv[Volts]": return PerDiv(self)


class Seconds(_Quantity):
    unit = "s"
    def __rmul__(self, k: float) -> "Seconds": return Seconds(k * self.value)
    def __truediv__(self, d: "_Div") -> "PerDiv[Seconds]": return PerDiv(self)


class _Div:
    pass


class PerDiv[Q: _Quantity]:
    """`1 * ms / div`"""
    def __init__(self, q: Q) -> None:
        self.q = q

    def __eq__(self, o: object) -> bool:
        return isinstance(o, PerDiv) and o.q == self.q

    def __hash__(self) -> int:
        return hash(("PerDiv", self.q))

    def __repr__(self) -> str:
        return f"{self.q!r}/div"


V, mV = Volts(1), Volts(1e-3)
s, ms, us, ns = Seconds(1), Seconds(1e-3), Seconds(1e-6), Seconds(1e-9)
div = _Div()

# ── the one source of truth for allowed choices ──────────────────────────
# Each of these is read by the parser, `to_scpi()` (via the dataclasses below) and the schema generator.

type ChannelName = Literal["ch1", "ch2", "ch3", "ch4"]
type Coupling = Literal["DC", "AC", "GND"]
type Probe = Literal[1, 10, 100]
type Slope = Literal["rising", "falling", "either"]
type TriggerMode = Literal["auto", "normal", "single"]
type Memory = Literal["auto", "1k", "10k", "100k", "1M", "10M", "25M"]


def _fits(v: object, hint: object) -> bool:
    """Runtime check of one value against its annotation: Literal, quantities, PerDiv[...], Optional."""
    if isinstance(hint, TypeAliasType):
        return _fits(v, hint.__value__)
    origin = get_origin(hint)
    if origin is Literal:
        return v in get_args(hint)
    if origin in (Union, types.UnionType):
        return any(_fits(v, h) for h in get_args(hint))
    if origin is PerDiv:
        return isinstance(v, PerDiv) and isinstance(v.q, get_args(hint)[0])
    if origin is dict:
        return isinstance(v, dict)
    return isinstance(v, hint) if isinstance(hint, type) else True


def _validate(obj: object) -> None:
    for name, hint in get_type_hints(type(obj)).items():
        v = getattr(obj, name)
        if not _fits(v, hint):
            raise TypeError(f"{type(obj).__name__}.{name} = {v!r} does not fit {hint}")


@dataclass(frozen=True, kw_only=True, slots=True)
class Channel:
    scale: PerDiv[Volts]
    offset: Volts = 0 * V
    coupling: Coupling = "DC"
    probe: Probe = 1
    def __post_init__(self) -> None: _validate(self)


@dataclass(frozen=True, kw_only=True, slots=True)
class Edge:
    source: ChannelName
    level: Volts
    slope: Slope = "rising"
    mode: TriggerMode = "normal"
    def __post_init__(self) -> None: _validate(self)


@dataclass(frozen=True, kw_only=True, slots=True)
class Capture:
    timebase: PerDiv[Seconds]
    trigger: Edge
    ch1: Channel | None = None
    ch2: Channel | None = None
    ch3: Channel | None = None
    ch4: Channel | None = None
    memory: Memory = "auto"
    extra: dict[str, str | float] = field(default_factory=dict)   # raw SCPI escape hatch
    def __post_init__(self) -> None: _validate(self)

    def to_scpi(self) -> dict[str, str | float | bool]:
        out: dict[str, str | float | bool] = {
            ":TIM:MAIN:SCAL": self.timebase.q.value,
            ":ACQ:MDEP": self.memory.upper() if self.memory == "auto" else self.memory,
        }
        for i, ch in enumerate((self.ch1, self.ch2, self.ch3, self.ch4), 1):
            out[f":CHAN{i}:DISP"] = ch is not None
            if ch is not None:
                out |= {
                    f":CHAN{i}:PROB": ch.probe,
                    f":CHAN{i}:COUP": ch.coupling,
                    f":CHAN{i}:SCAL": ch.scale.q.value,
                    f":CHAN{i}:OFFS": ch.offset.value,
                }
        t = self.trigger
        out |= {
            ":TRIG:MODE": "EDGE",
            ":TRIG:EDGE:SOUR": t.source.replace("ch", "CHAN"),
            ":TRIG:EDGE:SLOP": {"rising": "POS", "falling": "NEG", "either": "RFAL"}[t.slope],
            ":TRIG:EDGE:LEV": t.level.value,
            ":TRIG:SWE": {"auto": "AUTO", "normal": "NORM", "single": "SING"}[t.mode],
        }
        return out | self.extra


# ── shared introspection: allowed keys, choices and quantity kinds come from the dataclasses above ──

def _unwrap_alias(hint: object) -> object:
    """Follow a `type X = ...` alias to the type it stands for."""
    return hint.__value__ if isinstance(hint, TypeAliasType) else hint


def _choices(hint: object) -> tuple[Any, ...]:
    """The allowed values of a Literal (or aliased Literal) field, or () if `hint` is not one."""
    hint = _unwrap_alias(hint)
    return get_args(hint) if get_origin(hint) is Literal else ()


def _quantity_kind(hint: object) -> tuple[str, bool] | None:
    """('V'|'s', per_div) if `hint` is a quantity type (Volts/Seconds, or PerDiv[...] of one), else None."""
    hint = _unwrap_alias(hint)
    origin = get_origin(hint)
    if origin is PerDiv:
        inner = get_args(hint)[0]
        return inner.unit, True
    if isinstance(hint, type) and issubclass(hint, _Quantity):
        return hint.unit, False
    return None


def _block_fields(cls: type) -> list[tuple[str, object, bool, object]]:
    """(name, type hint, required, default) for each field of a dataclass, in declaration order."""
    hints = get_type_hints(cls)
    out = []
    for f in dataclasses.fields(cls):
        has_default = not (f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING)
        default = None
        if has_default:
            default = f.default_factory() if f.default_factory is not dataclasses.MISSING else f.default
        out.append((f.name, hints[f.name], not has_default, default))
    return out


CAPTURE_FIELDS = _block_fields(Capture)


# ── parsing: quantities as text, YAML with 1-based line marks ────────────

_PREFIX = {"": 1.0, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "n": 1e-9, "k": 1e3}
_PREFIX_CLASS = "".join(k for k in _PREFIX if k)
_NUM = r"(?P<num>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
_QUANTITY_RE = re.compile(rf"^\s*{_NUM}\s*(?P<prefix>[{_PREFIX_CLASS}]?)(?P<unit>V|s)(?P<perdiv>\s*/\s*div)?\s*$")
_BARE_NUMBER_RE = re.compile(rf"^\s*{_NUM}\s*$")


class CaptureError(ValueError):
    """All problems found in one capture description, in file order.

    `.problems` is the list of formatted lines, each `<file>:<line>: <key path>: <what is wrong>`.
    """
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))


def _fmt(source: str, line: int, path: str, message: str) -> tuple[int, str]:
    return line, f"{source}:{line}: {path}: {message}"


class _Marked(dict[str, Any]):
    """A parsed YAML mapping: a plain dict plus the 1-based line where the block starts, and where each
    key and value were written. Built by `_compose`; a plain dict (no marks) is also accepted everywhere
    this is used, for callers that build the data themselves."""
    def __init__(self, line: int) -> None:
        super().__init__()
        self.line = line
        self.key_line: dict[str, int] = {}
        self.value_line: dict[str, int] = {}


def _line_of(d: object, key: str, *, of_value: bool = False) -> int:
    if isinstance(d, _Marked):
        table = d.value_line if of_value else d.key_line
        return table.get(key, d.line)
    return 0


class _MarkedList(list[Any]):
    """A parsed YAML sequence, plus the 1-based line where it starts (see `_Marked`)."""
    def __init__(self, line: int) -> None:
        super().__init__()
        self.line = line


def _top_line(d: object) -> int:
    return d.line if isinstance(d, (_Marked, _MarkedList)) else 0


def _compose(text: str, source: str) -> tuple[object, list[tuple[int, str]]]:
    """Parse YAML text into plain Python values (mappings as `_Marked`), flagging duplicate keys."""
    import yaml  # type: ignore[import-untyped]

    problems: list[tuple[int, str]] = []
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    scalars = yaml.SafeLoader("")

    def walk(node: object, path: str) -> object:
        if isinstance(node, yaml.MappingNode):
            result = _Marked(node.start_mark.line + 1)
            for key_node, value_node in node.value:
                key = scalars.construct_object(key_node, deep=True)
                key_line = key_node.start_mark.line + 1
                key_path = f"{path}.{key}" if path else str(key)
                if isinstance(key, str) and key in result:
                    problems.append(_fmt(source, key_line, key_path, "duplicate key"))
                    continue
                value = walk(value_node, key_path if isinstance(key, str) else path)
                if isinstance(key, str):
                    result[key] = value
                    result.key_line[key] = key_line
                    result.value_line[key] = value_node.start_mark.line + 1
            return result
        if isinstance(node, yaml.SequenceNode):
            result_list = _MarkedList(node.start_mark.line + 1)
            result_list.extend(walk(child, path) for child in node.value)
            return result_list
        return scalars.construct_object(node, deep=True)

    if root is None:
        return _Marked(1), problems
    return walk(root, ""), problems


def _parse_quantity(raw: object, want_unit: str, per_div: bool, path: str, line: int,
                     problems: list[tuple[int, str]], source: str) -> object:
    text = str(raw)
    example = f"1 {'m' if want_unit == 's' else ''}{want_unit}{'/div' if per_div else ''}"
    expected = f"a value like '{example}'"
    m = _QUANTITY_RE.match(text)
    if not m:
        if _BARE_NUMBER_RE.match(text):
            problems.append(_fmt(source, line, path,
                                  f"{raw!r} has no unit; the unit is mandatory, expected {expected}"))
        else:
            problems.append(_fmt(source, line, path, f"{raw!r} is not {expected}"))
        return None
    if m["unit"] != want_unit:
        unit_name = "volts" if m["unit"] == "V" else "seconds"
        problems.append(_fmt(source, line, path, f"{raw!r} is in {unit_name}, expected {expected}"))
        return None
    if bool(m["perdiv"]) != per_div:
        verb = "must not have" if m["perdiv"] else "is missing"
        problems.append(_fmt(source, line, path, f"{raw!r} {verb} '/div', expected {expected}"))
        return None
    cls = Volts if want_unit == "V" else Seconds
    # Decimal arithmetic: 200 * 1e-6 is 0.00019999999999999998 in binary floats, and the scope
    # turned that into 198 us/div.
    q = cls(float(Decimal(m["num"]) * Decimal(repr(_PREFIX[m["prefix"]]))))
    return PerDiv(q) if per_div else q


def _parse_choice(raw: object, choices: tuple[Any, ...], path: str, line: int,
                   problems: list[tuple[int, str]], source: str) -> object:
    if isinstance(raw, bool):
        problems.append(_fmt(source, line, path, f"{raw!r}: YAML read this as a boolean; quote it"))
        return None
    if raw not in choices:
        problems.append(_fmt(source, line, path, f"{raw!r} is not one of {', '.join(map(str, choices))}"))
        return None
    return raw


def _check_unknown_keys(raw: dict[str, Any], allowed: set[str], path: str,
                         problems: list[tuple[int, str]], source: str) -> None:
    for key in raw:
        if key not in allowed:
            line = _line_of(raw, key)
            key_path = f"{path}.{key}" if path else str(key)
            hint = difflib.get_close_matches(str(key), sorted(allowed), 1)
            suggestion = f", did you mean {hint[0]!r}?" if hint else f" (allowed: {', '.join(sorted(allowed))})"
            problems.append(_fmt(source, line, key_path, "unknown setting" + suggestion))


def _parse_scalar_field(raw: dict[str, Any], name: str, hint: object, required: bool, default: object,
                         path: str, problems: list[tuple[int, str]], source: str, block_line: int) -> object:
    field_path = f"{path}.{name}" if path else name
    present = name in raw
    value_line = _line_of(raw, name, of_value=True)
    if not present:
        if required:
            problems.append(_fmt(source, block_line, field_path, "missing"))
        return default
    quantity = _quantity_kind(hint)
    if quantity is not None:
        unit, per_div = quantity
        return _parse_quantity(raw[name], unit, per_div, field_path, value_line, problems, source)
    return _parse_choice(raw[name], _choices(hint), field_path, value_line, problems, source)


def _parse_block(cls: type, raw: object, path: str, line: int,
                  problems: list[tuple[int, str]], source: str) -> dict[str, object] | None:
    """Parse one Channel/Edge block from its raw YAML value into kwargs for `cls(**kwargs)`."""
    if not isinstance(raw, dict):
        problems.append(_fmt(source, line, path, f"expected a block of settings, got {raw!r}"))
        return None
    fields = _block_fields(cls)
    _check_unknown_keys(raw, {name for name, *_ in fields}, path, problems, source)
    block_line = raw.line if isinstance(raw, _Marked) else line
    return {
        name: _parse_scalar_field(raw, name, hint, required, default, path, problems, source, block_line)
        for name, hint, required, default in fields
    }


def _parse_channel(data: dict[str, Any], name: str, problems: list[tuple[int, str]], source: str) -> dict[str, Any] | None:
    if name not in data:
        return None
    return _parse_block(Channel, data[name], name, _line_of(data, name, of_value=True), problems, source)


def _parse_trigger(data: dict[str, Any], problems: list[tuple[int, str]], source: str) -> dict[str, Any] | None:
    top_line = data.line if isinstance(data, _Marked) else 0
    if "trigger" not in data:
        problems.append(_fmt(source, top_line, "trigger", "missing"))
        return None
    return _parse_block(Edge, data["trigger"], "trigger", _line_of(data, "trigger", of_value=True),
                         problems, source)


def _parse_extra(data: dict[str, Any], problems: list[tuple[int, str]], source: str) -> dict[str, object]:
    if "extra" not in data:
        return {}
    raw = data["extra"]
    if not isinstance(raw, dict):
        problems.append(_fmt(source, _line_of(data, "extra", of_value=True), "extra",
                              f"expected a mapping, got {raw!r}"))
        return {}
    return dict(raw)


def parse_capture(data: object, source: str = "capture",
                   _problems: list[tuple[int, str]] | None = None) -> Capture:
    problems: list[tuple[int, str]] = [] if _problems is None else _problems
    top_line = _top_line(data)

    if not isinstance(data, dict):
        problems.append(_fmt(source, top_line, "capture", f"expected a mapping, got {data!r}"))
        data = _Marked(top_line)

    allowed = {name for name, *_ in CAPTURE_FIELDS}
    _check_unknown_keys(data, allowed, "", problems, source)

    kwargs: dict[str, object] = {
        "ch1": _parse_channel(data, "ch1", problems, source),
        "ch2": _parse_channel(data, "ch2", problems, source),
        "ch3": _parse_channel(data, "ch3", problems, source),
        "ch4": _parse_channel(data, "ch4", problems, source),
        "trigger": _parse_trigger(data, problems, source),
        "extra": _parse_extra(data, problems, source),
    }
    for name, hint, required, default in CAPTURE_FIELDS:
        if name not in kwargs:                              # timebase, memory
            kwargs[name] = _parse_scalar_field(data, name, hint, required, default, "", problems, source,
                                                top_line)

    trigger = kwargs["trigger"]
    if isinstance(trigger, dict) and trigger.get("source") is not None:
        src = trigger["source"]
        if src not in data:
            trigger_raw = data.get("trigger") if isinstance(data, dict) else None
            line = _line_of(trigger_raw, "source", of_value=True) if isinstance(trigger_raw, _Marked) \
                else top_line
            problems.append(_fmt(source, line, "trigger.source",
                                  f"{src!r} is the trigger source but {src} is not described"))

    if problems:
        raise CaptureError([msg for _, msg in sorted(problems, key=lambda p: p[0])])

    channels = {name: (Channel(**kwargs[name]) if kwargs[name] is not None else None)  # type: ignore[arg-type]
                for name in ("ch1", "ch2", "ch3", "ch4")}
    # kwargs is built dynamically from the field definitions above, so its declared value type is
    # `object`; every value has in fact already been checked against its field's real type (or the
    # function would have raised CaptureError above), but that is not something mypy can see here.
    return Capture(timebase=kwargs["timebase"], trigger=Edge(**kwargs["trigger"]),      # type: ignore[arg-type]
                    memory=kwargs["memory"], extra=kwargs["extra"], **channels)         # type: ignore[arg-type]


def load_capture(path: str | Path) -> Capture:
    path = Path(path)
    data, problems = _compose(path.read_text(), path.name)
    return parse_capture(data, source=path.name, _problems=problems)


# ── schema generation: same choices, same units, as data ─────────────────

_FIELD_DOC: dict[str, str] = {
    "timebase": "Horizontal sweep speed, seconds per division, e.g. '1 ms/div'.",
    "memory": "Acquisition memory depth: auto, 1k, 10k, 100k, 1M, 10M or 25M, e.g. '1M'.",
    "ch1": "Channel 1 settings; omit to leave the channel off.",
    "ch2": "Channel 2 settings; omit to leave the channel off.",
    "ch3": "Channel 3 settings; omit to leave the channel off.",
    "ch4": "Channel 4 settings; omit to leave the channel off.",
    "trigger": "Edge trigger settings, e.g. {source: ch1, level: 1.5 V}.",
    "extra": "Raw SCPI header: value, for settings this model does not cover, e.g. {':CHAN1:VERN': 'ON'}.",
    "scale": "Vertical scale, volts per division, e.g. '1 V/div'.",
    "offset": "Vertical offset, e.g. '-1.5 V'.",
    "coupling": "Input coupling, e.g. 'DC'.",
    "probe": "Probe attenuation ratio, e.g. 10.",
    "source": "Trigger source channel, e.g. 'ch1'.",
    "level": "Trigger level, e.g. '1.5 V'.",
    "slope": "Trigger edge slope, e.g. 'rising'.",
    "mode": "Trigger sweep mode, e.g. 'normal'.",
}

_QUANTITY_REF = {("V", False): "volts", ("V", True): "voltsPerDiv", ("s", True): "secondsPerDiv"}


def _quantity_pattern(unit: str, per_div: bool) -> str:
    pattern = rf"^\s*[-+]?\d+(\.\d+)?([eE][-+]?\d+)?\s*[{_PREFIX_CLASS}]?{unit}"
    if per_div:
        pattern += r"\s*/\s*div"
    return pattern + r"\s*$"


def _field_schema(hint: object) -> dict[str, Any]:
    quantity = _quantity_kind(hint)
    if quantity is not None:
        return {"$ref": f"#/$defs/{_QUANTITY_REF[quantity]}"}
    return {"enum": list(_choices(hint))}


def _block_schema(cls: type) -> dict[str, Any]:
    fields = _block_fields(cls)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [name for name, _, required, _ in fields if required],
        "properties": {
            name: _field_schema(hint) | {"description": _FIELD_DOC[name]}
            for name, hint, _, _ in fields
        },
    }


def capture_schema() -> dict[str, Any]:
    defs = {
        "volts": {"type": "string", "pattern": _quantity_pattern("V", False),
                   "description": "A voltage, e.g. '-1.5 V' or '50 mV'."},
        "voltsPerDiv": {"type": "string", "pattern": _quantity_pattern("V", True),
                         "description": "Volts per division, e.g. '1 V/div' or '50 mV/div'."},
        "secondsPerDiv": {"type": "string", "pattern": _quantity_pattern("s", True),
                           "description": "Seconds per division, e.g. '1 ms/div' or '500 us/div'."},
        "channel": _block_schema(Channel),
        "trigger": _block_schema(Edge),
    }
    properties = {}
    for name, hint, _, _default in CAPTURE_FIELDS:
        if name in ("ch1", "ch2", "ch3", "ch4"):
            properties[name] = {"$ref": "#/$defs/channel", "description": _FIELD_DOC[name]}
        elif name == "trigger":
            properties[name] = {"$ref": "#/$defs/trigger", "description": _FIELD_DOC[name]}
        elif name == "extra":
            properties[name] = {"type": "object", "description": _FIELD_DOC[name]}
        else:
            properties[name] = _field_schema(hint) | {"description": _FIELD_DOC[name]}

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Capture conditions for a Rigol DHO scope",
        "type": "object",
        "additionalProperties": False,
        "required": [name for name, _, required, _ in CAPTURE_FIELDS if required],
        "$defs": defs,
        "properties": properties,
    }


# ── command line ──────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    import json
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["schema"]:
        print(json.dumps(capture_schema(), indent=2))
        return 0
    if argv and argv[0] == "check" and len(argv) > 1:
        failed = False
        for path in argv[1:]:
            try:
                load_capture(path)
            except CaptureError as exc:
                print("\n".join(exc.problems))
                failed = True
            else:
                print(f"OK {path}")
        return 1 if failed else 0
    print("usage: capture.py schema | capture.py check FILE...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
