"""实验站点上把一家测试公司按正式方式标成「已接 Express」，并用离线夹具走一遍公司页「同步 Express」落仓库（多仓闭环 P1）。

只给 ERP 实验室（erp-pos-lab / 回归实验室）用，写的都是实验站点上的合成数据，不连真账套、不发外部请求：
  1. 公司更新接口给这家公司设 13 位税号 + 分店（`masters.company.update_company`）；
  2. 按 CC 桌面版心跳报账套名单的同一入口（`pearnly_core.cc_desktop.books.replace`）报一份假路径的账套 →
     `express.push.linked()` 为真、启动数据当前公司带 `express_linked`；
  3. 核：ERP 里新建仓被拒（中英文案）；公司页「同步 Express」在没有在线电脑时回 offline、不落库（`masters.start`）；
  4. 离线夹具代替电脑读回的主档，交给读回后落库的同一个函数（`masters.apply`）：原有的仓认作 Express 主仓，Express 新建的仓出现在 ERP。

    docker exec -i -w /home/frappe/frappe-bench <bench 容器> env/bin/python /workspace/scripts/erp-express-linked-lab-fixture.py \\
        erp-pos-a.localhost "ERP 预览二店" purchase-preview@example.test

最后一行打印 JSON 结果（ok 为真即全部按预期）。
"""
import json
import os
import sys

import frappe

DEVICE = "lab-fake-device-"  # 假电脑：只在实验站点上；每家公司一台（`books.replace` 整份替换一台电脑的名单，同一台报第二家会删掉第一家）


def lab_tax(company_name) -> str:
    """每家实验公司一个合成税号（过泰国税号校验位）：同一个税号报过的账套会让别家公司也算「已接」。"""
    import hashlib

    body = "0105" + str(int(hashlib.sha256(company_name.encode()).hexdigest(), 16))[:8]
    return body + str((11 - sum(int(d) * (13 - i) for i, d in enumerate(body)) % 11) % 10)


def main(site, company_name, user):
    # 只在实验室容器（ERP / POS 实验室或回归实验室，各自 compose 里设的标记）里、对 *.localhost 的实验站点跑
    if "1" not in (os.environ.get("PEARNLY_ERP_POS_LAB"), os.environ.get("PEARNLY_REGRESSION_LAB")) or not str(site).endswith(".localhost"):
        raise SystemExit("This fixture requires a dedicated ERP laboratory site")
    os.chdir("/home/frappe/frappe-bench/sites")
    frappe.init(site=site, sites_path=".")
    frappe.connect()
    frappe.set_user(user)
    from frappe.utils import now_datetime
    from pearnly_core.cc_desktop import books
    from pearnly_erp_portal.express import masters, push
    from pearnly_erp_portal.inventory import warehouses
    from pearnly_erp_portal.masters import company as companies

    out = {}
    co = frappe.db.get_value("ERP Company", {"company_name": company_name}, "name")
    tax = lab_tax(company_name)
    companies.update_company(co, tax_id=tax, branch="00000")
    out["linked_before"] = push.linked(co)
    books.replace(DEVICE + tax, [{"path": "C:\\PearnlyExpress\\LAB\\" + tax, "company": company_name, "tax_id": tax, "branch": "00000",
                            "writable": True, "period_start": "2026-01-01", "period_end": "2026-12-31", "uid": "LAB"}],
                  now_datetime())
    frappe.db.commit()
    out["linked"] = push.linked(co)
    frappe.defaults.set_user_default("company", co, user)
    frappe.clear_cache(user=user)
    out["boot_express_linked"] = next((row.get("express_linked") for row in companies.company_options(user) if row["is_current"]), None)
    for lang in ("zh", "en"):
        frappe.local.lang = lang
        try:
            warehouses.create_warehouse(warehouse_name="ERP new warehouse", company=co)
            out[f"create_{lang}"] = "created"
        except frappe.ValidationError as error:
            frappe.db.rollback()
            out[f"create_{lang}"] = str(error)
    out["sync_button"] = masters.start(co).get("state")
    frappe.db.commit()
    first = frappe.get_all("ERP Warehouse", filters={"company": co}, pluck="warehouse_name", order_by="creation asc", limit_page_length=1)
    counts = masters.apply(co, "LAB", {"warehouses": [{"code": "01", "name": first[0] if first else "Main"},
                                                      {"code": "02", "name": "Express warehouse 02"}],
                                       "settings": {"mainloc": "01"}, "products": [], "stock": [], "customers": [], "suppliers": []})
    frappe.db.commit()
    out["apply_warehouses"] = counts.get("warehouses")
    out["warehouses"] = frappe.get_all("ERP Warehouse", filters={"company": co}, fields=["warehouse_name", "express_code", "is_active"],
                                       order_by="express_code asc")
    out["ok"] = (not out["linked_before"] and out["linked"] and out["boot_express_linked"] is True
                 and "Express" in out["create_zh"] and out["create_en"].startswith("Create the warehouse in Express")
                 and out["sync_button"] in ("offline", "outdated", "reading")
                 and {row.express_code for row in out["warehouses"]} >= {"01", "02"})
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
