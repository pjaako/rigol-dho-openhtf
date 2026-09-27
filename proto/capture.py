"""Prototype: readable AND typed capture description. Python 3.12+ (PEP 695 generics), stdlib only."""
from dataclasses import dataclass, field
from typing import Literal, TypeAliasType, Union, get_args, get_origin, get_type_hints
import types


class _Quantity:
    """A float with a dimension. `1.5 * V` builds one; mixing dimensions is a type error."""
    __slots__ = ("value",)
    unit = "?"

    def __init__(self, value: float) -> None:
        self.value = float(value)

    def __rmul__(self, k: float):            # 1.5 * V
        return type(self)(k * self.value)

    def __neg__(self):
        return type(self)(-self.value)

    def __eq__(self, o: object) -> bool:
        return type(o) is type(self) and o.value == self.value   # type: ignore[attr-defined]

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
    def __repr__(self) -> str:
        return f"{self.q!r}/div"


V, mV = Volts(1), Volts(1e-3)
s, ms, us, ns = Seconds(1), Seconds(1e-3), Seconds(1e-6), Seconds(1e-9)
div = _Div()

type ChannelName = Literal["ch1", "ch2", "ch3", "ch4"]


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
    coupling: Literal["DC", "AC", "GND"] = "DC"
    probe: Literal[1, 10, 100] = 1
    def __post_init__(self) -> None: _validate(self)


@dataclass(frozen=True, kw_only=True, slots=True)
class Edge:
    source: ChannelName
    level: Volts
    slope: Literal["rising", "falling", "either"] = "rising"
    mode: Literal["auto", "normal", "single"] = "normal"
    def __post_init__(self) -> None: _validate(self)


@dataclass(frozen=True, kw_only=True, slots=True)
class Capture:
    timebase: PerDiv[Seconds]
    trigger: Edge
    ch1: Channel | None = None
    ch2: Channel | None = None
    ch3: Channel | None = None
    ch4: Channel | None = None
    memory: Literal["auto", "1k", "10k", "100k", "1M", "10M", "25M"] = "auto"
    extra: dict[str, str | float] = field(default_factory=dict)   # raw SCPI escape hatch
    def __post_init__(self) -> None: _validate(self)

    def to_scpi(self) -> dict[str, str | float | bool]:
        out: dict[str, str | float | bool] = {":TIM:MAIN:SCAL": self.timebase.q.value, ":ACQ:MDEP": self.memory.upper() if self.memory == "auto" else self.memory}
        for i, ch in enumerate((self.ch1, self.ch2, self.ch3, self.ch4), 1):
            out[f":CHAN{i}:DISP"] = ch is not None
            if ch is not None:
                out |= {f":CHAN{i}:PROB": ch.probe, f":CHAN{i}:COUP": ch.coupling, f":CHAN{i}:SCAL": ch.scale.q.value, f":CHAN{i}:OFFS": ch.offset.value}
        t = self.trigger
        out |= {":TRIG:MODE": "EDGE", ":TRIG:EDGE:SOUR": t.source.replace("ch", "CHAN"), ":TRIG:EDGE:SLOP": {"rising": "POS", "falling": "NEG", "either": "RFAL"}[t.slope],
                ":TRIG:EDGE:LEV": t.level.value, ":TRIG:SWE": {"auto": "AUTO", "normal": "NORM", "single": "SING"}[t.mode]}
        return out | self.extra
