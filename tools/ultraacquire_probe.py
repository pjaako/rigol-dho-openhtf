import os
import socket, time, struct
s = socket.create_connection((os.environ.get("SCOPE_HOST", "192.0.2.10"), 5555), 10); s.settimeout(60); s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
def w(c): s.sendall(c.encode() + b"\n")
def rexact(n):
    b = bytearray()
    while len(b) < n: b += s.recv(min(1 << 20, n - len(b)))
    return bytes(b)
def rline():
    b = bytearray()
    while not b.endswith(b"\n"): b += s.recv(65536)
    return b.decode().strip()
def q(c): w(c); return rline()
import numpy as np
def err():
    e = q(":SYST:ERR?"); return "" if e.startswith("0") else f" [err {e}]"
def raw(n):
    w(":WAV:SOUR CHAN1"); w(":WAV:FORM WORD"); w(":WAV:MODE RAW"); w(":WAV:STAR 1"); w(f":WAV:STOP {n}")
    f = q(":WAV:PRE?").split(","); w(":WAV:DATA?"); assert rexact(1) == b"#"; nd = int(rexact(1)); nb = int(rexact(nd)); d = np.frombuffer(rexact(nb), "<u2").astype(float); rexact(1)
    return (d - float(f[8]) - float(f[9])) * float(f[7]), float(f[4]), int(float(f[2]))
while err(): pass
st = dict(tb=q(":TIM:MAIN:SCAL?"), lev=q(":TRIG:EDGE:LEV?"), tmo=q(":ACQ:ULTR:TIM?"), type=q(":ACQ:TYPE?"), depth=int(float(q(":ACQ:MDEP?"))), rec=q(":REC:WREC:ENAB?"))
print("state as found:", st)
try:
    w(":REC:WREC:ENAB OFF"); time.sleep(0.4); w(":RUN"); time.sleep(0.4)
    w(":TRIG:EDGE:LEV 1.5"); w(":TIM:MAIN:SCAL 2E-5"); time.sleep(0.5); w(":ACQ:TYPE ULTR"); time.sleep(1.2); w(":ACQ:ULTR:TIM 1"); time.sleep(0.3)
    for depth, frames in (("10k", 20), ("10k", 4), ("100k", 20), ("1M", 20)):
        w(":RUN"); time.sleep(0.3); w(f":ACQ:MDEP {depth}"); time.sleep(0.6); w(f":ACQ:ULTR:MAXF {frames}"); time.sleep(0.6)
        maxf = q(":ACQ:ULTR:MAXF?"); time.sleep(2.5); w(":STOP"); time.sleep(1.0)
        n = int(float(q(":ACQ:MDEP?"))); w(":NAV:ENAB ON"); w(":NAV:MODE FRAM"); time.sleep(0.5); nseg = q(":NAV:FRAM:END:FRAM?"); w(":NAV:ENAB OFF")
        t0 = time.perf_counter(); v, xinc, pn = raw(n); dt = time.perf_counter() - t0
        i = np.flatnonzero((v[:-1] < 1.5) & (v[1:] >= 1.5)); gaps = np.diff(i)
        print(f"depth {depth:>4} requested frames {frames:>2} -> MAXF {maxf} segments shown {nseg} | read {len(v)} pts in {dt:.2f} s, xinc {xinc*1e9:.0f} ns | rising edges {len(i)}, spacing in samples {sorted(set(gaps.tolist()))[:4]} = {np.median(gaps)*xinc*1e6 if len(gaps) else float('nan'):.1f} us (real signal period 1000 us){err()}")
finally:
    while err(): pass
    w(":RUN"); time.sleep(0.5); w(f":ACQ:ULTR:TIM {st['tmo']}"); w(f":ACQ:TYPE {st['type']}"); time.sleep(1.0)
    w(f":ACQ:MDEP {st['depth']}"); w(f":TIM:MAIN:SCAL {st['tb']}"); w(f":TRIG:EDGE:LEV {st['lev']}"); time.sleep(0.8)
    if st["rec"] == "1": w(":REC:WREC:ENAB ON"); time.sleep(0.4)
    print("restored:", dict(tb=q(":TIM:MAIN:SCAL?"), lev=q(":TRIG:EDGE:LEV?"), tmo=q(":ACQ:ULTR:TIM?"), type=q(":ACQ:TYPE?"), depth=q(":ACQ:MDEP?"), rec=q(":REC:WREC:ENAB?"), status=q(":TRIG:STAT?"))); s.close()
