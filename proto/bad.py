from capture import Capture, Channel, Edge, V, mV, ms, us, div

A = Capture(timebase=1 * V / div, trigger=Edge(source="ch1", level=1.5 * V))          # volts where time is expected
B = Capture(timebase=1 * ms / div, trigger=Edge(source="ch1", level=1.5 * ms))        # level in milliseconds
C = Capture(timebase=1 * ms / div, trigger=Edge(source="ch5", level=1.5 * V))         # no such channel
D = Capture(timebase=1 * ms / div, trigger=Edge(source="ch1", level=1.5 * V, slope="up"))   # no such slope
E = Capture(timebase=1 * ms, trigger=Edge(source="ch1", level=1.5 * V))               # forgot "/ div"
F = Capture(timebase=1 * ms / div, triger=Edge(source="ch1", level=1.5 * V))          # typo in the field name
G = Capture(timebase=1 * ms / div, trigger=Edge(source="ch1", level=1.5 * V), memory="2M")  # unsupported depth
