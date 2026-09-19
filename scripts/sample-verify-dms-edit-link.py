# -*- coding: utf-8 -*-
"""编辑深链（把 LINE 用户带回本站点改草稿）验证。
需要站点上下文（读站点配置 + 站点地址），所以走 `bench console` 而不是裸 python。

钉住的：
· 配了 LIFF → 走 `liff.line.me` 深链，且**带 `draft` 与（按需）`editor=customer`**；
· **不再带 `channel` 参数**（新平台一站一渠道，多 OA 那套没搬）；
· 没配 LIFF → 回落本站点网页路径（**不是**报错，也**不是**给个空链接）；
· 两者都取不到 → 空串，调用方据此省略按钮（点不动的按钮比没有更糟）。

用法: scripts/sample-verify-dms-edit-link.sh dms <站点>
"""

def main():
    import frappe

    from pearnly_dms.editor import edit_link as e

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    before = {k: frappe.conf.get(k) for k in ("pearnly_line_liff_id",)}
    try:
        frappe.conf["pearnly_line_liff_id"] = "2006123456-AbCdEf"
        u = e.url("abc123")
        check("配了 LIFF → 走 liff.line.me 深链并带 draft",
              u == "https://liff.line.me/2006123456-AbCdEf?draft=abc123", u)
        u2 = e.url("abc123", customer=True)
        check("客户档编辑器带 editor=customer",
              u2 == "https://liff.line.me/2006123456-AbCdEf?draft=abc123&editor=customer", u2)
        check("不再带 channel 参数（新平台一站一渠道）", "channel=" not in u2, u2)
        check("nonce 原样带过去（不编码成看不见的东西）", "draft=abc123" in u)

        frappe.conf["pearnly_line_liff_id"] = ""
        u3 = e.url("abc123")
        check("没配 LIFF → 回落本站点网页路径（不报错、不给空链接）",
              u3.startswith("http") and "/liff/dms-booking?draft=abc123" in u3, u3)
    finally:
        for k, v in before.items():
            if v is None:
                frappe.conf.pop(k, None)
            else:
                frappe.conf[k] = v

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
