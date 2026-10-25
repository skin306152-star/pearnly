"""只读：列出 STCRD 里 POSOPR 9（销售出库）且 XUNITPR/XTRNVAL 为 0 或空、未删除的行，并附 ARTRN 的建单用户。"""
import struct, sys
B = sys.argv[1]
def load(n):
    d = open(B + "\\" + n + ".DBF", "rb").read()
    cnt, hl, rl = struct.unpack("<IHH", d[4:12]); o = 32; fs = []
    while d[o] != 0x0D:
        fs.append((d[o:o+11].split(b"\0")[0].decode("latin1"), d[o+16], chr(d[o+11]))); o += 32
    rows = []
    for i in range(cnt):
        r = d[hl+i*rl: hl+(i+1)*rl]; pos = 1; v = {"_del": r[:1] == b"*"}
        for name, ln, t in fs:
            raw = r[pos:pos+ln]; pos += ln
            v[name] = round(struct.unpack("<d", raw)[0], 4) if (t == "B" and ln == 8) else raw.decode("cp874", "replace").strip()
        rows.append(v)
    return rows
ar = {r["DOCNUM"]: r for r in load("ARTRN")}
tot = 0; hits = []
for r in load("STCRD"):
    if r["_del"] or r["POSOPR"] != "9": continue
    tot += 1
    xu = r["XUNITPR"]; xv = r["XTRNVAL"]
    if not xu or not xv or float(xu or 0) == 0 or float(xv or 0) == 0:
        a = ar.get(r["DOCNUM"], {})
        hits.append((r["DOCNUM"], str(r["DOCDAT"]), r["STKCOD"], r["LOCCOD"], r["TRNQTY"], r["XUNITPR"], r["XTRNVAL"], a.get("USERID"), a.get("CHGDAT")))
print("POSOPR9 rows", tot, "zero-cost", len(hits))
for h in hits: print(h)
