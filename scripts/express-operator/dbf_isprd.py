import os, sys, shutil, struct, tempfile
book = sys.argv[1]; tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "x"); shutil.copy2(os.path.join(book, "ISPRD.DBF"), q); d = open(q, "rb").read()
cnt, hl, rl = struct.unpack("<IHH", d[4:12]); o = 32; fs = []
while d[o] != 0x0D:
    fs.append((d[o:o+11].split(b"\0")[0].decode("latin1"), d[o+16])); o += 32
for i in range(cnt):
    r = d[hl+i*rl: hl+(i+1)*rl]; pos = 1; v = []
    for name, ln in fs:
        v.append(f"{name}={r[pos:pos+ln].decode('latin1').strip()}"); pos += ln
    print(" ".join(v)[:200])
