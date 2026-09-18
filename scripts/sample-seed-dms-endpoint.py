# -*- coding: utf-8 -*-
"""把老产品里那套 MR.ERP DMS 连接写进本站的 `Pearnly ERP Endpoint`。

由 `scripts/sample-seed-dms-endpoint.sh` 送进容器、用 `bench console` 跑。

为什么要有这一步：新平台要跑真实链路（识别 → 查重 → 建档 → 推送），
前提是站点里有一条真能登录的 DMS 连接。老产品那边已经跑通、凭据也在本机
（见 `scripts/import-legacy-endpoints.py`），不该让人再手抄一遍。

**用哪个账号**：脚本只认 `dmstest` —— 老产品文档
（`docs/integrations/mrerp-dms-ui-test-plan.md`）写明的 DMS 测试账号，
客户账号一律不碰（那是生产数据）。

⚠️ 老产品文档还有一条约束：**`dmstest` 只允许一个活跃登录**。
跑之前确认没有别的会话占着这个账号，否则会互相踢下线。

密码落到 Frappe 的 `Password` 字段 → 存进 `__Auth` 表，**不明文入库**；
终端只打印脱敏结果。
"""


def main():
    import json
    import os

    import frappe

    SRC = "/tmp/dms-endpoint.json"
    # 这个 DocType 的编号由命名序列自动给（`EP-{#####}`），`label` 才是给人看的名字
    DOC_LABEL = "MR.ERP DMS（测试账号）"

    if not os.path.exists(SRC):
        print("FAIL 没找到 %s（宿主脚本应该先把它送进来）" % SRC)
        print("RESULT: FAIL")
        return

    with open(SRC, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    def mask(v):
        v = str(v or "")
        return ("%s***(%d位)" % (v[:3], len(v))) if v else "(空)"

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    for key in ("system_url", "username", "password"):
        check("样本里带 %s" % key, bool(data.get(key)), data.get(key) if key != "password" else mask(data.get(key)))

    if not (data.get("system_url") and data.get("username") and data.get("password")):
        print("RESULT: FAIL")
        return

    check("用的是测试账号 dmstest（不碰客户账号）", data["username"] == "dmstest", mask(data["username"]))

    existing = frappe.db.get_value("Pearnly ERP Endpoint", {"label": DOC_LABEL}, "name")
    if existing:
        doc = frappe.get_doc("Pearnly ERP Endpoint", existing)
        action = "更新"
    else:
        doc = frappe.new_doc("Pearnly ERP Endpoint")
        action = "新建"

    doc.label = DOC_LABEL
    doc.adapter = "mrerp_dms"
    doc.enabled = 1
    doc.system_url = data["system_url"]
    doc.username = data["username"]
    doc.password = data["password"]          # Password 字段 → 进 __Auth，不明文入库
    doc.admin_username = data.get("admin_username") or ""
    doc.admin_password = data.get("admin_password") or ""
    # ⚠️ DMS 连接**绝不能**参与发票自动推送（老产品踩过：发票会被推到 DMS）
    doc.auto_push = 0
    doc.notes = "从老产品 erp_endpoints 导入；账号为老产品的 DMS 测试账号 dmstest。"

    if action == "新建":
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)
    frappe.db.commit()

    check("%s成功" % action, True, doc.name)

    # 回读校验：字段真的落库了（不是只在内存里）
    back = frappe.get_doc("Pearnly ERP Endpoint", doc.name)
    check("回读 adapter", back.adapter == "mrerp_dms", back.adapter)
    check("回读 system_url", back.system_url == data["system_url"], back.system_url)
    check("回读 username", back.username == data["username"], mask(back.username))
    check("回读 enabled", bool(back.enabled), back.enabled)
    check("回读 auto_push=0（不参与发票推送）", not back.auto_push, back.auto_push)
    # Password 字段回读会拿到掩码值，所以判"非空"而不是判等
    check("回读 password 已存（非空）", bool(back.get_password("password", raise_exception=False)), "已存")
    check("回读 admin_password 已存（非空）", bool(back.get_password("admin_password", raise_exception=False)), "已存")

    # 密码**不许**明文躺在主表里：Frappe 的 Password 字段应进 `__Auth`。
    # 这条必须自己验，不能假设框架一定做对了（凭据泄露是最贵的一类错）。
    row = frappe.db.sql(
        "select password, admin_password from `tabPearnly ERP Endpoint` where name=%s",
        doc.name, as_dict=True,
    )
    col_text = " ".join(str((row[0].get(k) or "")) for k in ("password", "admin_password")) if row else ""
    leaked = data["password"] and data["password"] in col_text
    check("主表列里没有明文密码", not leaked,
          "有明文" if leaked else "主表该列是掩码/空，真值在 __Auth")
    auth_rows = frappe.db.sql(
        "select fieldname from __Auth where doctype='Pearnly ERP Endpoint' and name=%s",
        doc.name, as_dict=True,
    )
    auth_fields = {r["fieldname"] for r in auth_rows}
    check("__Auth 里存了 password", "password" in auth_fields, sorted(auth_fields))
    check("__Auth 里存了 admin_password", "admin_password" in auth_fields, sorted(auth_fields))

    failed = [r for r in results if not r[1]]
    print("")
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


main()
