"""只读：先把账套拷到临时目录，再按原始字节列出某单号在各表的行（含删除标记）。"""
import os, sys, shutil, struct, json, tempfile
book, docs = sys.argv[1], sys.argv[2].split(",")
assert book.upper().startswith(("C:\\PEARNLYEXPRESS\\", "C:\\PEARNLYSTRESS\\"))
tmp = tempfile.mkdtemp(prefix="acc_doc_")
def load(n):
    p = os.path.join(book, n + ".DBF")
    if not os.path.exists(p): return [], []
    q = os.path.join(tmp, n + ".DBF"); shutil.copy2(p, q); d = open(q, "rb").read()
    cnt, hl, rl = struct.unpack("<IHH", d[4:12]); o = 32; fs = []
    while d[o] != 0x0D:
        fs.append((d[o:o+11].split(b"\0")[0].decode("latin1"), d[o+16], chr(d[o+11]))); o += 32
    rows = []
    for i in range(cnt):
        r = d[hl+i*rl: hl+(i+1)*rl]; pos = 1; v = {"_del": r[:1] == b"*", "_row": i}
        for name, ln, t in fs:
            raw = r[pos:pos+ln]; pos += ln
            v[name] = round(struct.unpack("<d", raw)[0], 4) if (t == "B" and ln == 8) else raw.decode("cp874", "replace").strip()
        rows.append(v)
    return [f for f, _, _ in fs], rows
out = {}
for t, key in (("STTRN", "DOCNUM"), ("STCRD", "DOCNUM"), ("GLJNL", "VOUCHER"), ("GLJNLIT", "VOUCHER"), ("ARTRN", "DOCNUM"), ("APTRN", "DOCNUM"), ("ISVAT", "DOCNUM")):
    fs, rows = load(t)
    hit = [{k: v for k, v in r.items() if v not in ("", 0.0, None, "0", "0.00")} for r in rows if r.get(key) in docs]
    if hit: out[t] = hit
fs, rows = load("ISRUN")
out["ISRUN"] = [{k: r[k] for k in ("DOCTYP", "PREFIX", "DOCNUM", "JNLTYP", "ACCNUM01", "POSDES")} for r in rows if r["PREFIX"] in ("OU", "JU", "TK", "HS")]
open(r"C:\PearnlyStress\_acc_cc\docrows.json", "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, default=str))
shutil.rmtree(tmp)
