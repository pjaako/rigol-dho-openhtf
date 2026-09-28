# AGENTS.md

OpenHTF plug for Rigol DHO800/DHO900 scopes. Read `SPEC.md`; it is the contract.

- Python env: `.venv/` (openhtf 1.6.1, pyvisa 1.16.2, pyvisa-py 0.8.1, pyusb, numpy, pytest). `pytest` and `python` on PATH come from it. Do not install packages.
- Coder agents never touch the real scope; they use `fake_resource.FakeDhoResource`. The project-owner agent may use it remotely, also with nobody on site, as long as the state is saved first and every change can be undone over the same connection. Never change the scope's LAN settings over LAN.
- This directory is on NTFS: `chmod` does nothing. Do not rely on file modes.
- Do not reinvent transports: PyVISA handles USBTMC and IEEE-488.2 binary blocks.
- `SPEC.md` is the original contract; where it disagrees with `README.md` ("Things the manual does not tell you"), README wins: it records what the real scope does.
- Everything must run without hardware: `bench_usb.py --fake`, `example_test.py --fake`, `pytest -q`.
- Validate conversions against a known signal (probe-compensation output, compare with `:MEAS:ITEM? VTOP/VBAS/VPP`) and reject clipped captures. A conclusion drawn from clipped data ("the manual's formula is wrong") cost one wrong release of this plug.
- Samples are always 16-bit: `read_waveform` defaults to `fmt='WORD'`; do not switch code to BYTE to save time or space.
- To save memory and disk keep `Waveform.raw` (uint16) plus `Waveform.preamble`; do not store the float64 `t`/`v` arrays, recompute them (formulas in README).
- When a measurement is meant to compare tools, do not drive the instrument with one of the tools under comparison. Use an independent control (`tools/bare_socket_check.py`) and say which confounders remain.
- A fake instrument only knows what we told it. Acceptance on the real scope found two defects the 44 fake-based tests could not: an unknown header times out instead of answering, and the scope undoes `:REC:WREC:ENAB ON` ~0.9 s after a setup block. Every feature gets a run on hardware before it is called done, and each such finding goes back into the fake.
- Capture conditions are YAML files in `captures/` (chosen by the user over Python dicts and typed Python for readability); `capture.py` is the single source for keys, choices and units, and `capture.schema.json` is generated from it (`python capture.py schema`), never edited by hand.
- Hand-written hardware checks must wrap the work in `try: ... finally: plug.tearDown()`. One check raised halfway and left the user's scope reset; it was recovered from a setup block saved beforehand (`get_state()`); such dumps are private and git-ignored. OpenHTF itself always calls `tearDown`.
- Set OpenHTF config keys after importing the plug module (`CONF.load(...)` before the key is declared is lost).
- Run `mypy --strict capture.py` with a checker from outside the project venv; it must stay clean.
- Machine comparison uses `golden.py` (mask around a taught reference). A golden file is valid only for the capture file, probe and scope it was taught with; `compare` refuses anything else. Do not loosen those checks to make a test pass.
- To exercise a running OpenHTF station without a browser use `tools/operator_robot.py <answer> [<answer> ...]`.
- OpenHTF's operator prompt shows one image (`image_url`), a text field and one button. Two images need one composite picture; two buttons do not exist in stock OpenHTF 1.6.1.
- Screen capture: `:DISP:DATA? PNG` returns the scope screen as an IEEE-488.2 block (about 120 KB, 1024x600), measured over the raw socket on 2026-09-27. There is no plug method for it yet.
- This repository is public. Addresses in the text are documentation examples (`192.0.2.x`), the serial number is a placeholder. Real addresses, serial numbers, setup dumps (`setups/*.bin`) and `HANDOFF.md` stay out of git. The tools in `tools/` take the scope address from the environment variable `SCOPE_HOST`.
