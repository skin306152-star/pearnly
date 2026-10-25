import os, sys, shutil, struct, tempfile
book, code, day = sys.argv[1], sys.argv[2], sys.argv[3]
tmp = tempfile.mkdtemp(); q = os.path.join(tmp, "x"); shutil.copy2(os.path.join(book, "STCRD.DBF"), q); d = open(q, "rb").read()
cnt, hl, rl = struct.unpack("<IHH", d[4:12]); o = 32; fs = []
while d[o] != 0x0D:
    fs.append((d[o:o+11].split(b"\0")[0].decode("latin1"), d[o+16], chr(d[o+11]))); o += 32
for i in range(cnt):
    r = d[hl+i*rl: hl+(i+1)*rl]; pos = 1; v = {"del": r[:1] == b"*"}
    for name, ln, t in fs:
        raw = r[pos:pos+ln]; pos += ln
        v[name] = round(struct.unpack("<d", raw)[0], 4) if (t == "B" and ln == 8) else raw.decode("cp874", "replace").strip()
    if v["STKCOD"] == code and v["DOCDAT"] >= day:
        print(i, v["del"], v["DOCNUM"], v["POSOPR"], v["LOCCOD"], "qty", v["XTRNQTY"], "val", v["XTRNVAL"], "MREMBAL", v["MREMBAL"], "MREMVAL", v["MREMVAL"], "MLOT", v["MLOTNUM"], "LOT", v["LOTBAL"])
