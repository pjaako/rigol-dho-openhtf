"""Hardware benchmark CLI for Rigol DHO scope.

Usage:
    python bench_usb.py [--resource NAME] [--channel 1] [--repeats 20]
                        [--depths 10k,100k,1M,10M] [--chunks 250000,1000000]
                        [--out bench_results]
    python bench_usb.py --fake --repeats 3 --depths 10k --chunks all,4000 --out <tmp>

Do NOT run this against a scope you need right now.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

from rigol_dho_plug import RigolDhoPlug


def _parse_list(value: str, default: list) -> list:
    if not value:
        return default
    return value.split(',')


def _parse_chunks(value: str, default: list) -> list:
    """Parse chunk list: integers or 'all' (meaning None/chunk_points=None)."""
    items = _parse_list(value, default)
    result = []
    for item in items:
        if item.lower() == 'all':
            result.append(None)
        else:
            result.append(int(item))
    return result


def _fmt_depth(depth: str) -> str:
    return depth


def _percentile(data: list[float], pct: float) -> float:
    s = sorted(data)
    k = (len(s) - 1) * pct / 100.0
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[-1]
    return s[f] + (k - f) * (s[c] - s[f])


def _fmt_ms(ms: float) -> str:
    return f'{ms:.1f}'


def _fmt_s(s: float) -> str:
    return f'{s:.4f}'


def _fmt_mib(n_bytes: int, s: float) -> str:
    return f'{n_bytes / (1024 * 1024) / s:.2f}'


def _print_table(results: list[dict], headers: list[str]):
    """Print a real markdown table."""
    print('| ' + ' | '.join(headers) + ' |')
    print('|' + '|'.join('---' for _ in headers) + '|')
    for r in results:
        vals = []
        for h in headers:
            v = r.get(h, '')
            vals.append(str(v) if v != '' else '')
        print('| ' + ' | '.join(vals) + ' |')


def bench(plug: RigolDhoPlug, args) -> list[dict]:
    """Run all benchmark scenarios. Returns list of result dicts."""
    results: list[dict] = []

    # ── idn ──────────────────────────────────────────────────
    try:
        latencies: list[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            plug.idn()
            latencies.append((time.perf_counter() - t0) * 1000)
        results.append({
            'scenario': 'idn',
            'median_ms': _fmt_ms(statistics.median(latencies)),
            'p95_ms': _fmt_ms(_percentile(latencies, 95)),
        })
    except Exception as exc:
        results.append({'scenario': 'idn', 'error': f'ERROR: {exc}'})

    # ── norm_byte / norm_word ────────────────────────────────
    # *_reuse_pre passes the previous preamble back in, skipping the :WAV:PRE? query
    for fmt, label, reuse in [('BYTE', 'norm_byte', False), ('WORD', 'norm_word', False),
                              ('BYTE', 'norm_byte_reuse_pre', True), ('WORD', 'norm_word_reuse_pre', True)]:
        try:
            times: list[float] = []
            w = plug.read_waveform(args.channel, 'NORM', fmt)   # first call configures the scope, not timed
            for _ in range(args.repeats):
                t0 = time.perf_counter()
                w = plug.read_waveform(args.channel, 'NORM', fmt, preamble=w.preamble if reuse else None)
                times.append((time.perf_counter() - t0) * 1000)
            points = len(w.raw)
            median_ms = statistics.median(times)
            wfps = 1000 / median_ms
            results.append({
                'scenario': label,
                'points': points,
                'median_ms': _fmt_ms(median_ms),
                'waveforms_s': f'{wfps:.1f}',
            })
        except Exception as exc:
            results.append({'scenario': label, 'error': f'ERROR: {exc}'})

    # ── raw_<depth>_<fmt>_<chunk> ────────────────────────────
    for depth in args.depths:
        for fmt in ('BYTE', 'WORD'):
            for chunk in args.chunks:
                chunk_label = 'all' if chunk is None else str(chunk)
                scenario = f'raw_{_fmt_depth(depth)}_{fmt}_{chunk_label}'
                try:
                    # Set memory depth while running
                    plug.run()
                    plug.set_memory_depth(depth)
                    time.sleep(0.05)
                    plug.stop()
                    # Wait for STOP (timeout 10 s)
                    deadline = time.perf_counter() + 10
                    while time.perf_counter() < deadline:
                        if plug.trigger_status() == 'STOP':
                            break
                        time.sleep(0.05)
                    else:
                        plug.stop()

                    # Skip chunk size larger than the record
                    if chunk is not None and chunk > plug.memory_depth():
                        continue

                    times: list[float] = []
                    for _ in range(3):
                        t0 = time.perf_counter()
                        w = plug.read_waveform(args.channel, 'RAW', fmt, chunk)
                        times.append(time.perf_counter() - t0)
                    n = len(w.raw)
                    median_s = statistics.median(times)
                    pts_s = n / median_s
                    mib_s = _fmt_mib(n * w.raw.itemsize, median_s)
                    results.append({
                        'scenario': scenario,
                        'points': n,
                        'median_s': _fmt_s(median_s),
                        'points_s': f'{pts_s:.0f}',
                        'mib_s': mib_s,
                    })
                except Exception as exc:
                    results.append({'scenario': scenario, 'error': f'ERROR: {exc}'})

    return results


def main():
    parser = argparse.ArgumentParser(description='Rigol DHO USB benchmark')
    parser.add_argument('--resource', default='', help='VISA resource name')
    parser.add_argument('--fake', action='store_true', help='Use fake resource')
    parser.add_argument('--channel', type=int, default=1)
    parser.add_argument('--repeats', type=int, default=20)
    parser.add_argument('--depths', default='10k,100k,1M,10M')
    parser.add_argument('--chunks', default='all,1000000,250000')
    parser.add_argument('--out', default='bench_results')
    args = parser.parse_args()

    args.depths = _parse_list(args.depths, ['10k', '100k', '1M', '10M'])
    args.chunks = _parse_chunks(args.chunks, [None, 1000000, 250000])

    # Create the plug
    # restore_state=False: this script restores what it changes itself (see the finally block below).
    if args.fake:
        from fake_resource import FakeDhoResource
        plug = RigolDhoPlug(resource=FakeDhoResource(), restore_state=False)
    elif args.resource:
        import pyvisa
        rm = pyvisa.ResourceManager('@py')
        resource = rm.open_resource(args.resource)
        plug = RigolDhoPlug(resource=resource, restore_state=False)
    else:
        plug = RigolDhoPlug(restore_state=False)

    # Save initial state
    initial_depth = plug.memory_depth()
    initial_status = plug.trigger_status()

    try:
        results = bench(plug, args)
    finally:
        try:
            plug.run()
            plug.set_memory_depth(initial_depth)
            if initial_status == 'STOP':
                plug.stop()
        except Exception:
            pass
        plug.tearDown()

    # Collect all header names in order
    headers = ['scenario']
    for r in results:
        for k in r:
            if k != 'scenario' and k not in headers:
                headers.append(k)

    _print_table(results, headers)

    # Write JSON
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, 'bench.json'), 'w') as f:
        json.dump(results, f, indent=2)


if __name__ == '__main__':
    main()
