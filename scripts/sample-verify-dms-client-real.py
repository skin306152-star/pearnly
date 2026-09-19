# -*- coding: utf-8 -*-
"""DMS 业务客户端验证（B 部分）：**对真系统只读验证**。

A 部分（假管道）验的是"规则对不对"；这一半验的是"解析认不认得出真页面的东西" ——
老产品的解析全是按真实 HTML 写死的正则，换个模板就会静默返回空。
所以必须拿真页面过一遍。

**这一轮只读**：查称谓/地区主档、搜客户、读客户、拉主档。不建客户、不建单。
（建档和推送是写操作，另起一轮，且只对测试账号。）

由 `scripts/sample-verify-dms-client-real.sh` 送进容器、用独立 python 跑。
"""


def main():
    import frappe

    from pearnly_dms.adapter.session import MrerpDmsAdapter

    ENDPOINT_LABEL = "MR.ERP DMS（测试账号）"
    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def note(label, detail):
        print("  " + label + ": " + str(detail))

    name = frappe.db.get_value("Pearnly ERP Endpoint", {"label": ENDPOINT_LABEL}, "name")
    check("找到端点 %s" % ENDPOINT_LABEL, bool(name), name)
    if not name:
        print("RESULT: FAIL")
        return

    try:
        with MrerpDmsAdapter.from_endpoint(name, headless=True) as adapter:
            adapter.login()
            cl = adapter.client()
            note("业务客户端", type(cl).__name__)

            # ── 称谓主档（双来源合并） ────────────────────────────────
            prefixes = cl.list_prefixes()
            check("称谓主档取到", len(prefixes) > 0, "%d 项" % len(prefixes))
            if prefixes:
                note("称谓样例", [p[1] for p in prefixes[:5]])
                check("称谓是 [id, 名] 两列", all(len(p) == 2 for p in prefixes))

            # ── 地区四级联动 ──────────────────────────────────────────
            provinces = cl.list_geo("provinces")
            check("府（provinces）取到", len(provinces) > 0, "%d 项" % len(provinces))
            if provinces:
                note("府样例", [p[1] for p in provinces[:4]])
                pid = provinces[0][0]
                districts = cl.list_geo("districts", pid)
                check("县（districts）按府级联取到", len(districts) > 0, "%d 项" % len(districts))
                if districts:
                    did = districts[0][0]
                    subs = cl.list_geo("subdistricts", did)
                    check("区（subdistricts）级联取到", len(subs) > 0, "%d 项" % len(subs))
                    if subs:
                        zips = cl.list_geo("zipcodes", subs[0][0])
                        check("邮编（zipcodes）级联取到", len(zips) > 0, "%d 项" % len(zips))
                        if zips:
                            note("邮编样例", zips[0])

            # ── 搜客户 + 直读客户 ────────────────────────────────────
            hits = cl.search_customers_detailed("ทดสอบ", limit=5)
            check("按关键词搜到客户", len(hits) > 0, "%d 条" % len(hits))
            if hits:
                note("命中样例", hits[0])
                check("搜索行有客户号与姓名",
                      bool(hits[0].get("customer_id")) and bool(hits[0].get("name")), hits[0])
                cid = hits[0]["customer_id"]
                fields = cl.read_customer(cid)
                check("直读客户拿到字段", bool(fields.get("cuscode")), "cuscode=%r" % fields.get("cuscode"))
                check("直读客户拿到姓名", bool(fields.get("name")), "name=%r" % fields.get("name"))
                check("直读客户拿到地址块",
                      any(k.startswith("house_no") for k in fields),
                      sorted(k for k in fields if k.startswith("house_no")))

            # 按身份证号查重（拿一个显然不存在的号，只读、不会建东西）
            absent = cl.lookup_customer("9999999999999")
            check("查一个不存在的身份证号 → found=False", absent.get("found") is False, absent)

            # ── 主档全量（连接向导要的那些） ──────────────────────────
            masters = cl.fetch_masters()
            for key, label in (("cars", "车型"), ("branches", "分店"),
                               ("advisors", "顾问"), ("prefixes", "称谓"),
                               ("employees", "员工")):
                rows = masters.get(key) or []
                check("主档 %s 取到" % label, len(rows) > 0, "%d 行" % len(rows))
            if masters.get("cars"):
                note("车型样例", masters["cars"][0][:3])
            if masters.get("advisors"):
                note("顾问样例", masters["advisors"][0][:3])
            if masters.get("employees"):
                note("员工样例（只打印编号/姓名两列，不打登录名）",
                     [masters["employees"][0].get("code"), masters["employees"][0].get("name")])

            # ── 主档里真存在的一行，必须解析出同形的 ref ───────────────
            if masters.get("cars"):
                real_id = str(masters["cars"][0][0])
                ref = cl._ref_from_default("txtcar", real_id, "", "")
                check("钉真实车型 id → 解析回同一行", ref.id == real_id, ref)
                # 缓存命中：同一会话内同参不该重复打接口
                before = len(getattr(cl, "_bshsd_memo", {}))
                cl._ref_from_default("txtcar", real_id, "", "")
                check("主档同一会话内走备忘（不重复打接口）",
                      len(getattr(cl, "_bshsd_memo", {})) == before,
                      "%d 条备忘" % before)
    except Exception as exc:  # noqa: BLE001 — 验证脚本就是要如实报出任何错
        check("对真系统跑只读验证", False, "%s: %s" % (type(exc).__name__, str(exc)[:300]))
        import traceback
        traceback.print_exc()

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    import sys

    import frappe

    site = sys.argv[1] if len(sys.argv) > 1 else "qd65956.localhost"
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        main()
    finally:
        frappe.destroy()
