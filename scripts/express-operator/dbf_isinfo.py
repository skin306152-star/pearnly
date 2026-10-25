import dbf, sys, json
t = dbf.Table(sys.argv[1] + "\\ISINFO.DBF"); t.open(dbf.READ_ONLY)
r = t[0]
keys = [f for f in t.field_names if any(k in f for k in ("NEG", "LOC", "CHK", "COSMTD", "PERPET", "AUTH"))]
print(json.dumps({k: str(r[k]).strip() for k in keys}, ensure_ascii=False))
t.close()
