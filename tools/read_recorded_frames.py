"""Read-only: select recorded frames and read them. Starts nothing, changes no acquisition setting."""
import os
import socket, time, hashlib, struct
s = socket.create_connection((os.environ.get("SCOPE_HOST", "192.0.2.10"), 5555), 10); s.settimeout(60); s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
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
print("state as found: status", q(":TRIG:STAT?"), "| record enabled", q(":REC:WREC:ENAB?"), "| record op", q(":REC:WREC:OPER?"), "| acquire type", q(":ACQ:TYPE?"))
nfr = int(q(":REC:WREP:FMAX?")); depth = int(float(q(":ACQ:MDEP?"))); cur0 = q(":REC:WREP:FCUR?")
print("recorded frames", nfr, "| memory depth", depth, "| current frame", cur0)
ks = sorted({1, max(1, nfr // 3), max(1, 2 * nfr // 3), nfr})
w(":WAV:SOUR CHAN1"); w(":WAV:FORM WORD")
res = {}
for mode, n in (("NORM", 1000), ("RAW", depth)):
    for k in ks:
        w(f":REC:WREP:FCUR {k}"); time.sleep(0.6)
        w(f":WAV:MODE {mode}"); w(":WAV:STAR 1"); w(f":WAV:STOP {n}")
        d = qbin(":WAV:DATA?"); res[(mode, k)] = struct.unpack(f"<{len(d)//2}H", d)
    ref = res[(mode, ks[0])]
    print(f"{mode}: points per frame {len(ref)}")
    for k in ks:
        v = res[(mode, k)]; diff = sum(1 for a, b in zip(ref, v) if a != b)
        print(f"   frame {k:>4}: md5 {hashlib.md5(struct.pack(f'<{len(v)}H', *v)).hexdigest()[:8]}  samples differing from frame {ks[0]}: {diff}")
    print(f"   distinct frames: {len({res[(mode, k)] for k in ks})}/{len(ks)}")
w(f":REC:WREP:FCUR {cur0}")
print("left as found: current frame", q(":REC:WREP:FCUR?"), "| status", q(":TRIG:STAT?")); s.close()
