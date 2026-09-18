# -*- coding: utf-8 -*-
"""计费底座的产品侧验证（在**产品站点**里跑）。

由 `scripts/sample-verify-billing.sh` 送进容器、用 `bench console` 跑，
阶段由环境变量 `PHASE` 指定：

  gate    余额为 0 时，recognize_id_card 必须**在调模型之前**就被 402 拦下
          （拦在模型前是关键：拦晚了就是白花钱）
  status  直接问控制面"能不能干" —— 充值到账后这里必须放行
  charge  走真实的扣费口记一笔，并回读余额

界面上的"余额 / 充值 / 流水"三件事由 shell 那侧的 HTTP 断言覆盖。
"""


def main():
    import os

    import frappe

    phase = (os.environ.get("PHASE") or "gate").strip()
    fails = []

    def check(label, ok, detail=""):
        print(("PASS " if ok else "FAIL ") + label + ((" -- " + str(detail)) if detail else ""))
        if not ok:
            fails.append(label)

    from pearnly_core import billing_client

    print("  站点:", frappe.local.site, "| 租户:", frappe.conf.get("pearnly_tenant"),
          "| 计费底座:", frappe.conf.get("pearnly_billing_api"))
    check("站点拿到了计费配置", billing_client.configured())

    if phase == "gate":
        from pearnly_dms.dms_ocr_service import DmsOcrError, recognize_id_card

        # 余额闸排在"端点存在"之后（照老产品的顺序：先看配置对不对，再看钱够不够）。
        # 所以这里先保证站点上有一条最简端点 —— 闸在登录 DMS **之前**，
        # 端点里的账号密码根本用不到，不需要真凭据。
        if not frappe.db.exists("Pearnly ERP Endpoint", {"adapter": "mrerp_dms", "enabled": 1}):
            frappe.get_doc({
                "doctype": "Pearnly ERP Endpoint",
                "label": "计费验证用（最小端点）",
                "adapter": "mrerp_dms",
                "enabled": 1,
                "system_url": "https://www.mrerp4sme.com/dms/index.php",
                "username": "billing-verify",
                "password": "billing-verify",
            }).insert(ignore_permissions=True)
            frappe.db.commit()
            print("  （建了一条最小端点，只为走到余额闸）")

        img = "/tmp/ocr-real/images"
        names = sorted(os.listdir(img)) if os.path.isdir(img) else []
        if not names:
            print("FAIL 没有样本图（/tmp/ocr-real/images）")
            print("RESULT: FAIL")
            return
        with open(os.path.join(img, names[0]), "rb") as fh:
            content = fh.read()
        try:
            recognize_id_card("Administrator", content, names[0], "image/jpeg")
            check("余额为 0 时被拦下", False, "居然放行了 —— 会先花钱再记账")
        except DmsOcrError as exc:
            check("余额为 0 时被拦下（402）",
                  exc.code == "insufficient_balance" and exc.http_status == 402,
                  "%s/%s" % (exc.code, exc.http_status))
            check("拦住时带上了余额与估价", "balance_thb" in (exc.detail or {}),
                  str(exc.detail)[:120])

    elif phase == "status":
        st = billing_client.status("pdf", 1, tier="A")
        check("充值到账后余额闸放行", bool(st.get("allowed")), st.get("error_code") or "")
        print("    余额 ฿%s，本次估价 ฿%s" % (st.get("balance_thb"), st.get("estimate_thb")))
        b = billing_client.status("pdf", 1, tier="B")
        if st.get("estimate_thb") and b.get("estimate_thb"):
            ratio = float(st["estimate_thb"]) / float(b["estimate_thb"])
            check("A 档单价高于 B 档（实测 %.2f 倍）" % ratio, ratio > 1.5, ratio)

    elif phase == "charge":
        before = billing_client.wallet().get("balance_thb")
        res = billing_client.charge("dms", "pdf", 1, tier="A", reference="verify:billing",
                                    description="计费底座验证")
        check("扣费成功", bool(res.get("ok")), str(res)[:150])
        after = res.get("balance_after")
        check("扣了钱（余额变小）",
              after is not None and float(after) < float(before), "%s → %s" % (before, after))
        print("    扣费 ฿%s，余额 ฿%s → ฿%s" % (res.get("charged_thb"), before, after))

    else:
        print("FAIL 未知阶段:", phase)
        fails.append("phase")

    print("RESULT: " + ("ALL PASS" if not fails else ("FAIL " + ",".join(fails))))
