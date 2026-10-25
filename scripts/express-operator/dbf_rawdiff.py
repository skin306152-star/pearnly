"""只读：按原始字节逐表比对两份账套副本（不依赖 dbf 库解析数值，STMAS 等也能比）。输出 JSON：每表行数、改动行（字段旧→新）、新增行、删除标记变化。"""
import os, sys, struct, json
a, b, outp = sys.argv[1], sys.argv[2], sys.argv[3]
def load(p):
    if not os.path.exists(p): return None, []
    d = open(p, "rb").read()
    n, hl, rl = struct.unpack("<IHH", d[4:12]); o = 32; fs = []
    while d[o] != 0x0D:
        fs.append((d[o:o+11].split(b"\0")[0].decode("latin1"), d[o+16], chr(d[o+11]))); o += 32
    rows = []
    for i in range(n):
        r = d[hl+i*rl: hl+(i+1)*rl]
        pos = 1; vals = []
        for name, ln, t in fs:
            raw = r[pos:pos+ln]; pos += ln
            if t == "B" and ln == 8: vals.append(repr(round(struct.unpack("<d", raw)[0], 6)))
            elif t == "I" and ln == 4: vals.append(str(struct.unpack("<i", raw)[0]))
            else: vals.append(raw.decode("cp874", "replace").strip())
        rows.append((r[:1] == b"*", vals))
    return [f for f, _, _ in fs], rows
out = {}
names = sorted({n.upper() for n in os.listdir(a) if n.upper().endswith(".DBF")} | {n.upper() for n in os.listdir(b) if n.upper().endswith(".DBF")})
for n in names:
    fa, ra = load(os.path.join(a, n)); fb, rb = load(os.path.join(b, n))
    if ra == rb: continue
    fields = fb or fa; ch = []
    for i in range(max(len(ra), len(rb))):
        x = ra[i] if i < len(ra) else None; y = rb[i] if i < len(rb) else None
        if x == y: continue
        if x is None:
            ch.append({"row": i, "new": {f: v for f, v in zip(fields, y[1]) if v not in ("", "0", "0.00", ".00")}, "deleted": y[0]})
        elif y is None:
            ch.append({"row": i, "gone": True})
        else:
            diff = {f: [x[1][k], y[1][k]] for k, f in enumerate(fields) if x[1][k] != y[1][k]}
            if x[0] != y[0]: diff["_deleted"] = [x[0], y[0]]
            key = y[1][0]
            ch.append({"row": i, "key": key, "diff": diff})
    out[n] = {"rows": [len(ra), len(rb)], "changed": len(ch), "rows_changed": ch}
open(outp, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
print({k: (v["rows"], v["changed"]) for k, v in out.items()})
