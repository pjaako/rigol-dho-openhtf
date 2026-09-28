# SPEC: screenshot of the scope screen, shown to the operator next to the plot

Decision behind this work: the user wants the operator to see the instrument's real screen together with
the plotted waveform. Stock OpenHTF shows one image per prompt, so the demo builds one composite picture.
The prompt is about 600 px wide in the browser, so the two pictures are stacked vertically (project
owner's decision; side by side would halve the size of both).

**The real scope is NOT available to you.** Use `fake_resource.FakeDhoResource` and the `--fake` flags.
Read `AGENTS.md`, `README.md`, `rigol_dho_plug.py`, `fake_resource.py`, `examples/operator_demo.py`,
`examples/README.md`, `tests/test_plug.py`, `tests/test_golden.py` (section 11, how demo phases are
tested) first. Do not commit.

## Facts measured on the real target (DHO814, firmware 00.01.05, LAN socket, 2026-09-28)

- `:DISP:DATA? PNG` answers with an IEEE-488.2 definite-length block: `#9`, nine length digits, the
  data, then `\n`. 10 reads: 73 460 to 114 574 bytes, 251 to 304 ms each.
- The data is a complete PNG: signature `89 50 4E 47 0D 0A 1A 0A`, IHDR 1024 x 600, bit depth 8,
  colour type 6 (RGBA), ends with an `IEND` chunk.
- PyVISA reads it with `query_binary_values(':DISP:DATA? PNG', datatype='B', container=bytes)`, the same
  call `get_state()` uses.
- The query changes nothing: trigger status, timebase, vertical scale, trigger level, recording switch,
  memory depth, `:WAV:MODE` and `:WAV:FORM` read the same before and after; `:SYST:ERR?` stays empty.
- It works while running and while stopped. Stopped after `:SING`, two screenshots in a row were
  byte-identical, and the waveform read before and after them was identical. While running, every
  screenshot differs.
- NOT measured: `:DISP:DATA?` with any other argument or with none. Do not send them.

## 1. `rigol_dho_plug.py`: `RigolDhoPlug.screenshot`

```python
def screenshot(self, path: str | Path | None = None) -> bytes:
```

- Sends exactly `:DISP:DATA? PNG` through `query_binary_values` as above, nothing else.
- Returns the PNG as `bytes`. If `path` is given, also writes the same bytes to that file.
- Raises `RuntimeError` when the answer does not start with the PNG signature or has no `IEND` chunk
  type in its last 8 bytes (`data[-8:-4] == b'IEND'`). Nothing is written to `path` in that case.
- Must not reset `self._wav_setup`: a screenshot between two `read_waveform` calls must not cost the
  waveform re-configuration.
- Place it in a section of its own after "waveform acquisition". No other method changes.

## 2. `fake_resource.py`

- `query_binary_values(':DISP:DATA? PNG', ...)` returns `container(<png bytes>)`: a valid PNG,
  1024 x 600, bit depth 8, colour type 6, built with the standard library only (`zlib`, `struct`);
  the fake must not import PIL or matplotlib. Any simple content will do (for example a dark
  background with a grid); keep the generator short.
- New constructor argument `screen: bytes | None = None`: when given, these bytes are returned instead
  (this is how tests inject a broken answer).
- Any other `:DISP:DATA?` form keeps raising `ValueError` like every unexpected binary query.
- The call is appended to `self.log` like the others. No state of the fake changes.

## 3. `examples/operator_demo.py`

- `capture_waveform` takes the screenshot **after** `read_waveform` and **before** `scope.run()`, so the
  screen shows the capture that was read.
- New function `composite_png(screen_png: bytes, plot_png: bytes) -> bytes`: one PNG, the scope screen
  on top, the plot below, no scaling of the screen. Width = width of the screen picture (1024); the plot
  is rendered 1024 px wide so that it needs no scaling either (change `plot_png`'s figure size
  accordingly, its height stays 420 px). Use Pillow (it is installed as a dependency of matplotlib);
  no new packages.
- Attachments: `waveform.png` (the plot, as before), `screen.png` (the screenshot, new),
  `waveform.npz` (as before). The composite is not attached; it can be rebuilt from the two.
- The prompt shows the composite. `test.state['waveform_png']` is replaced by a key holding the
  composite; choose a fitting name.
- `FakePlug`, the command line and the measurements stay as they are.

## 4. Tests

New tests for the plug go at the end of `tests/test_plug.py`; tests for the demo go into a new file
`tests/test_operator_demo.py`.

1. `screenshot()` on the fake returns `bytes` with the PNG signature, IHDR width 1024 and height 600.
2. `screenshot(path)` writes a file whose content equals the returned bytes (`tmp_path`).
3. `screenshot()` adds exactly one entry to the resource log: `:DISP:DATA? PNG`.
4. With `FakeDhoResource(screen=b'not a png')`: `RuntimeError`, and the file at `path` does not exist.
5. With a screen that has the signature but is cut off (the fake's PNG minus its last 20 bytes):
   `RuntimeError`.
6. `read_waveform(1)`, `screenshot()`, `read_waveform(1)`: the log entries added by the second read
   contain no `:WAV:SOUR`.
7. The fake rejects `:DISP:DATA? BMP` and `:DISP:DATA?` with `ValueError`.
8. `composite_png` of the fake's screen and a plot from `plot_png`: the result opens with Pillow, is
   1024 wide and `600 + plot height` high, and its top 600 rows equal the screen's pixels (compare in RGB).
9. `capture_waveform` with the fake plug, run through `htf.Test(...).execute(test_start=lambda: 'dut')`
   with an output callback that keeps the test record: the phase passes and has the attachments
   `waveform.png`, `screen.png` and `waveform.npz`; `screen.png` has the PNG signature.
10. In the fake's log of that run, `:DISP:DATA? PNG` comes after `:WAV:DATA?` and before `:RUN`.
    (Give the test access to the resource however is least intrusive, without changing `FakePlug`'s
    behaviour.)
11. `operator_judges` called directly with a stand-in for `prompts` (see `test_golden.py`): the
    `image_url` it passes starts with `data:image/png;base64,` and decodes to the composite.

All existing tests keep passing unmodified.

## 5. Documentation

- `README.md`: one row for `screenshot(path=None)` in the method table of "Setting up a capture", and the
  test count in "Setup". Nothing else.
- `examples/README.md`, section `operator_demo.py`: step 2 and the "Attachments" paragraph. Do not touch
  the "Checked on ..." sentence and do not replace `operator_demo.jpg`; the project owner does both
  after the run on hardware.
- Do not edit `AGENTS.md`, `SPEC*.md`, `HANDOFF.md`.

## Done means

- `.venv/bin/python -m pytest -q` passes (129 existing tests plus the new ones).
- `mypy --strict capture.py` is not affected (`capture.py` is not changed).
- With `.venv/bin/python examples/operator_demo.py --fake --once --port 4444` running in the
  background, `.venv/bin/python tools/operator_robot.py dut1 P` prints a `PROMPT:` line with
  `image yes` and a `RESULT:` line with outcome `PASS`; the demo process ends by itself; port 4444 is
  free afterwards.
- `git status --short` shows only: `rigol_dho_plug.py`, `fake_resource.py`, `examples/operator_demo.py`,
  `examples/README.md`, `README.md`, `tests/test_plug.py`, `tests/test_operator_demo.py`.

Report: files changed, the final test line verbatim, the output of the commands above, every place where
this spec was wrong or ambiguous or where you deviated and why, what is untested, and anything that looks
like a bug in existing code that you did not touch.
