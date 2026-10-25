import struct, sys
d = open(sys.argv[1] + r"\STMAS.DBF", "rb").read()
n, hl, rl = struct.unpack("<IHH", d[4:12]); o = 32; fs = []
while d[o] != 0x0D:
    fs.append((d[o:o+11].split(b"\0")[0].decode("latin1"), d[o+16])); o += 32
import collections; c = collections.Counter(); ex = []
for i in range(n):
    r = d[hl+i*rl: hl+(i+1)*rl]; pos = 1; v = {}
    for name, ln in fs:
        v[name] = r[pos:pos+ln].decode("cp874", "replace").strip(); pos += ln
    c[v["NEGALLOW"]] += 1
    if v["STKCOD"] in ("501871", "500310", "502324", "501780"): ex.append((v["STKCOD"], repr(v["NEGALLOW"])))
print(dict(c), ex)
