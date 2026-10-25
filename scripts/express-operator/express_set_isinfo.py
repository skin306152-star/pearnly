"""改账套 ISINFO 的指定字段（例：NEGALLOW=N CHKNEGLEV=2），改完与基线副本逐字段比对。
**会写账套**：只用于获准的测试账套（默认服务器测试账套 70EXP\\test）；首选在 Express 设置画面里改（7→1→3→1），本脚本只在 Express 没开时用。
用法：python express_set_isinfo.py FIELD=VALUE ...（以会话用户身份跑，网络盘权限在它身上）"""
import dbf, sys, os, datetime, hashlib
B = r"\\accserver\d$\account\70EXP\test"
B0 = r"C:\PearnlyStress\_book_record\b0"
out = open(r"C:\PearnlyStress\_acc_cc\isinfoset.txt", "w", encoding="utf-8")
def P(*a): print(*a, file=out, flush=True)
sets = dict(a.split("=", 1) for a in sys.argv[1:])
newest = max((os.path.getmtime(os.path.join(B, f)), f) for f in os.listdir(B) if os.path.isfile(os.path.join(B, f)))
P("账套最新修改", datetime.datetime.fromtimestamp(newest[0]).strftime("%m-%d %H:%M:%S"), newest[1])
t = dbf.Table(os.path.join(B, "ISINFO.DBF"), codepage="cp874"); t.open(dbf.READ_WRITE)
r = t[0]
P("改前", {k: str(r[k]).strip() for k in sets})
with r:
    for k, v in sets.items(): r[k] = v
t.close()
t = dbf.Table(os.path.join(B, "ISINFO.DBF"), codepage="cp874"); t.open(dbf.READ_ONLY); r = t[0]
P("改后", {k: str(r[k]).strip() for k in sets}); t.close()
a = dbf.Table(os.path.join(B0, "ISINFO.DBF"), codepage="cp874"); a.open(dbf.READ_ONLY)
b = dbf.Table(os.path.join(B, "ISINFO.DBF"), codepage="cp874"); b.open(dbf.READ_ONLY)
diff = [(k, str(a[0][k]).strip(), str(b[0][k]).strip()) for k in a.field_names if str(a[0][k]) != str(b[0][k])]
P("与 b0 不同的字段", diff, "行数", len(a), len(b))
