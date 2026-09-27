# AGENTS.md

OpenHTF plug for Rigol DHO800/DHO900 scopes. Read `SPEC.md`; it is the contract.

- Python env: `.venv/` (openhtf 1.6.1, pyvisa 1.16.2, pyvisa-py 0.8.1, pyusb, numpy, pytest). `pytest` and `python` on PATH come from it. Do not install packages.
- The real scope is only for the human/manager to touch. Agents use `fake_resource.FakeDhoResource`.
- This directory is on NTFS: `chmod` does nothing. Do not rely on file modes.
- Do not reinvent transports: PyVISA handles USBTMC and IEEE-488.2 binary blocks.
- `SPEC.md` is the original contract; where it disagrees with `README.md` ("Things the manual does not tell you"), README wins: it records what the real scope does.
- Everything must run without hardware: `bench_usb.py --fake`, `example_test.py --fake`, `pytest -q`.
