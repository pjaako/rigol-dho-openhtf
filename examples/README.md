# Examples

## operator_demo.py

An OpenHTF station with the web GUI: it sets the scope up from `captures/clock.yaml`, captures channel 1,
shows the waveform to the operator and records the operator's PASS / FAIL.

![operator prompt with the captured waveform](operator_demo.jpg)

```bash
.venv/bin/python examples/operator_demo.py --fake                                    # no hardware
.venv/bin/python examples/operator_demo.py                                           # scope on USB
.venv/bin/python examples/operator_demo.py --resource TCPIP::192.0.2.10::5555::SOCKET
```

Open `http://<address of this machine>:4444` (the server listens on all interfaces).

1. Enter a DUT id. The test starts.
2. The waveform appears with Vpp and frequency in the title.
3. Type `P` or `PASS`, `F` or `FAIL`. Anything else is asked again.
4. The result is shown in the GUI and the next test waits for a DUT id. `--once` stops after one test.

What is recorded per test, in `examples/results/<dut>.<time>.json`:

| Measurement | Meaning | Limit |
|---|---|---|
| `vpp` | peak-to-peak voltage | none, informational |
| `frequency` | from rising edges, with hysteresis | none, informational |
| `num_points` | samples read | none |
| `operator_verdict` | the operator's answer | must be `PASS` |

Attachments: `waveform.png` (what the operator saw) and `waveform.npz` (16-bit raw samples plus the
preamble, from which volts and time can be rebuilt).

The scope is returned to the state it was found in after every test.

Checked on 2026-09-28 from a browser on another machine, with the fake and with the real DHO814: a FAIL
answer, a PASS answer and a wrong answer that is asked again.

Options: `--port N` (default 4444), `--once`, `--fake`, `--resource NAME`.
Requires `matplotlib` in addition to the project's packages.
