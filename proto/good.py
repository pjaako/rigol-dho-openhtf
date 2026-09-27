from capture import Capture, Channel, Edge, V, mV, ms, us, div

CLOCK_CAPTURE = Capture(
    timebase = 1 * ms / div,
    ch1      = Channel(scale=1 * V / div, offset=-1.5 * V, coupling="DC"),
    trigger  = Edge(source="ch1", level=1.5 * V, slope="rising", mode="normal"),
    memory   = "1M",
)
print(CLOCK_CAPTURE)
for k, v in CLOCK_CAPTURE.to_scpi().items(): print(f"   {k:16} {v}")
