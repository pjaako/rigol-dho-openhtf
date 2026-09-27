"""Independent check: stdlib socket only. No PyVISA, no plug, no USB session."""
import socket, time, statistics, hashlib, sys
s = socket.create_connection(("192.0.2.10", 5555), 10); s.settimeout(60); s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
def w(c): s.sendall(c.encode() + b"\n")
def rline():
    b = bytearray()
    while not b.endswith(b"\n"): b += s.recv(65536)
    return b.decode().strip()
def q(c): w(c); return rline()
def rexact(n):
    b = bytearray()
    while len(b) < n: b += s.recv(min(1 << 20, n - len(b)))
    return bytes(b)
def qbin(c):
    w(c); assert rexact(1) == b"#"; nd = int(rexact(1)); n = int(rexact(nd)); d = rexact(n); rexact(1); return d
def med(fn, n=40):
    ts = []
    for _ in range(n):
        t = time.perf_counter(); fn(); ts.append((time.perf_counter() - t) * 1e3)
    return statistics.median(ts), min(ts)
print("idn:", q("*IDN?"))
tb0, d0 = q(":TIM:MAIN:SCAL?"), q(":ACQ:MDEP?")
try:
    if sys.argv[1] == "latency":
        for state in ("RUN", "STOP"):
            for tb in ("1E-3", "1E-6", "1E-1"):
                w(":RUN"); w(f":TIM:MAIN:SCAL {tb}"); time.sleep(1.5)
                if state == "STOP": w(":STOP"); time.sleep(0.8)
                a = med(lambda: q("*IDN?")); b = med(lambda: q(":TRIG:STAT?")); c = med(lambda: q("*OPC?"))
                w(":WAV:SOUR CHAN1"); w(":WAV:MODE NORM"); w(":WAV:FORM WORD")
                d = med(lambda: qbin(":WAV:DATA?"), 20)
                print(f"{state:4} timebase {tb:>5} s/div | *IDN? median {a[0]:6.2f} min {a[1]:6.2f} ms | :TRIG:STAT? {b[0]:6.2f} | *OPC? {c[0]:6.2f} | screen data {d[0]:6.2f} ms")
    else:
        w(":RUN"); w(":ACQ:MDEP 10k"); time.sleep(1)
        w(":REC:WREC:ENAB ON"); time.sleep(0.5); w(":REC:WREC:FRAM 20"); w(":REC:WREC:FINT 5E-2"); time.sleep(0.3)
        w(":REC:WREC:OPER RUN"); t = time.perf_counter()
        while q(":REC:WREC:OPER?") != "STOP" and time.perf_counter() - t < 60: time.sleep(0.05)
        print("recorded", q(":REC:WREP:FMAX?"), "frames, status", q(":TRIG:STAT?"))
        w(":WAV:SOUR CHAN1"); w(":WAV:FORM WORD")
        for mode, n in (("NORM", 1000), ("RAW", 10000)):
            hs = []
            for k in (1, 7, 14, 20):
                w(f":REC:WREP:FCUR {k}"); time.sleep(0.5)
                w(f":WAV:MODE {mode}"); w(":WAV:STAR 1"); w(f":WAV:STOP {n}")
                hs.append(hashlib.md5(qbin(":WAV:DATA?")).hexdigest()[:8])
            print(f"{mode}: {hs} -> distinct {len(set(hs))}/4")
finally:
    w(":REC:WREC:ENAB OFF"); time.sleep(0.4); w(":RUN"); time.sleep(0.3); w(f":TIM:MAIN:SCAL {tb0}"); w(f":ACQ:MDEP {int(float(d0))}"); time.sleep(0.5)
    print("restored: timebase", q(":TIM:MAIN:SCAL?"), "depth", q(":ACQ:MDEP?"), "status", q(":TRIG:STAT?"), "recording", q(":REC:WREC:ENAB?")); s.close()
