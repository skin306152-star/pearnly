"""Verify company master, company profile and customers on the dedicated laboratory.

Real Site path with synthetic data: default company auto-created and used by the
profile page, editable profile fields with bounds, customer normalisation by buyer
type, deactivation, per-job access, the Thai Revenue Department tax-id lookup
(cache round trip on a captured real response, offline failure, who may call it)
and tenant isolation.
"""
import json
import os
from pathlib import Path
import sys

import frappe

CONTROL_APP = "/home/frappe/frappe-bench/apps/pearnly_control"
SITE = os.environ.get("PEARNLY_ERP_POS_LAB_SITE", "erp-pos-a.localhost")
OTHER_SITE = "erp-pos-b.localhost" if SITE != "erp-pos-b.localhost" else "erp-pos-a.localhost"
ALLOWED_SITES = {"erp-pos-a.localhost", "erp-pos-b.localhost"}
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")

OWNER = "erp-pos-owner@example.com"
SHOP = "erp-pos-products@example.com"
CASHIER = "erp-pos-cashier-shift@example.com"
SALES = "erp-pos-sales@example.com"
PURCHASE = "erp-pos-purchase-job@example.com"
OUTSIDER = "erp-pos-outsider@example.com"

# 泰国税务局（RD）查询用的税号：一个**真实**的（夹具就是它的实时响应），一个显然不存在的。
TAX_ID = "0105546015062"
TAX_ID_ABSENT = "9999999999999"


def connect(site=SITE):
    if os.environ.get("PEARNLY_ERP_POS_LAB") != "1":
        raise SystemExit("Dedicated ERP/POS laboratory required")
    if site not in ALLOWED_SITES or not (SITE_ROOT / ".erp-pos-lab").is_file():
        raise SystemExit("Dedicated lab Site marker required")
    os.chdir(SITE_ROOT)
    frappe.init(site=site, sites_path=str(SITE_ROOT))
    frappe.connect()
    if not frappe.conf.get("pearnly_erp_pos_lab") or frappe.conf.get("db_host") != "db":
        raise SystemExit("Refusing a non-lab Site")
    frappe.flags.mute_emails = True
    return site


def as_user(user, func, *args, **kwargs):
    frappe.set_user(user)
    try:
        return True, func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the exception type is the evidence
        return False, f"{type(exc).__name__}: {exc}"[:300]


def drop_user(email):
    if frappe.db.exists("User", email):
        frappe.delete_doc("User", email, force=True, ignore_permissions=True)
        frappe.db.commit()


def ensure_member(email, roles):
    drop_user(email)
    frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                    "send_welcome_email": 0,
                    "roles": [{"role": "Desk User"}, *({"role": r} for r in roles)]
                    }).insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(user=email)


def main():
    site = connect()
    frappe.set_user("Administrator")
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.masters import company, customers

    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    if not frappe.db.exists("User", OWNER):
        ensure_member(OWNER, [])
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)
    ensure_member(SHOP, ["ERP Products"])
    ensure_member(CASHIER, ["ERP Cashier"])
    ensure_member(SALES, ["ERP Sales"])
    ensure_member(PURCHASE, ["ERP Purchase"])
    ensure_member(OUTSIDER, [])

    results = {}
    results["master_doctypes"] = sorted(
        d for d in ("ERP Company", "ERP Customer") if frappe.db.exists("DocType", d))

    # 干净起点：本脚本的客户与公司。
    for name in frappe.get_all("ERP Customer", filters={"customer_name": ["like", "Lab Buyer%"]},
                               pluck="name"):
        frappe.delete_doc("ERP Customer", name, force=True, ignore_permissions=True)
    for name in frappe.get_all("ERP Company", filters={"company_name": ["like", "Lab Company%"]},
                               pluck="name"):
        frappe.delete_doc("ERP Company", name, force=True, ignore_permissions=True)
    frappe.db.commit()

    # 公司主档：默认公司自动出现（资料页才有东西可填）。
    default = company.ensure_default()
    results["default_company"] = bool(default)
    ok, profile = as_user(OWNER, company.get_profile)
    results["profile_readable_by_owner"] = ok
    results["profile_is_default"] = profile.get("name") == default if ok else profile

    # 资料字段：备注、财年起始月，越界要拒；税号校验位不对要拒（单号前缀已隐藏，不再读写）。
    ok, saved = as_user(OWNER, company.update_profile, note="LAB",
                        fiscal_year_start_month=1, tax_id="0105550000007",
                        company_name="Lab Company A")
    results["update_profile"] = ok
    ok, err = as_user(OWNER, company.update_profile, fiscal_year_start_month=13)
    results["bad_fiscal_month_allowed"] = ok
    ok, err = as_user(OWNER, company.update_profile, tax_id="0105599999999")
    results["bad_tax_id_checksum_allowed"] = ok
    results["profile_after"] = {k: frappe.db.get_value("ERP Company", default, k)
                                for k in ("company_name", "note", "tax_id",
                                          "fiscal_year_start_month")}

    # 第二个公司：只有默认那条进资料页，列表能看到两条。
    ok, created = as_user(OWNER, company.create_company, company_name="Lab Company B",
                          short_name="LCB")
    results["create_company"] = ok
    ok, companies = as_user(OWNER, company.list_companies)
    results["company_count"] = len(companies) if ok else companies
    ok, profile_again = as_user(OWNER, company.get_profile)
    results["profile_still_default"] = (
        profile_again.get("name") == default if ok else profile_again)

    # 客户：公司买方带税号与分店，匿名买家清掉税号与分店。建客户归销售岗位（客户名录），
    # 收银在客户表上只读 —— 与旧线上"客户目录是网页页、pos 只挑已有买方"一致。
    ok, buyer = as_user(SALES, customers.create_customer, customer_name="Lab Buyer Co",
                        party_type="company", tax_id="0105550000015", branch_type="branch",
                        branch_no="00002", address="Bangkok", contact_phone="02-000-0000")
    results["create_customer"] = ok
    results["create_customer_error"] = None if ok else buyer
    buyer_name = buyer.get("name") if ok else None
    results["buyer_row"] = frappe.db.get_value(
        "ERP Customer", buyer_name,
        ["party_type", "tax_id", "branch_type", "branch_no"], as_dict=True) if ok else None

    ok, walkin = as_user(SALES, customers.create_customer, customer_name="Lab Buyer Walk-in",
                         party_type="anonymous", tax_id="9999999999999",
                         branch_type="branch", branch_no="00009")
    results["create_walkin"] = ok
    results["create_walkin_error"] = None if ok else walkin
    walkin_name = walkin.get("name") if ok else None
    results["walkin_row"] = frappe.db.get_value(
        "ERP Customer", walkin_name, ["party_type", "tax_id", "branch_type"],
        as_dict=True) if ok else None

    # 停用后默认列表看不到，但记录仍在。
    ok, off = as_user(SALES, customers.set_active, buyer_name, 0)
    results["deactivate_customer"] = ok
    results["deactivate_customer_error"] = None if ok else off
    ok, listed = as_user(CASHIER, customers.list_customers)
    results["hidden_after_deactivate"] = ok and all(r["name"] != buyer_name for r in listed)
    ok, listed_all = as_user(CASHIER, customers.list_customers, include_inactive=True)
    results["visible_with_inactive"] = ok and any(r["name"] == buyer_name for r in listed_all)
    ok, back = as_user(SALES, customers.set_active, buyer_name, 1)
    results["reactivate_customer"] = ok
    results["reactivate_customer_error"] = None if ok else back

    # 岗位：收银员能读客户（开单要选客户）但不能建、不能改；商品岗位既不能读也不能改；
    # 公司资料所有业务岗位可读、只有管理员可改。
    ok, err = as_user(SHOP, customers.list_customers)
    results["product_job_can_read_customers"] = ok
    ok, err = as_user(SHOP, customers.update_customer, buyer_name, short_name="X")
    results["product_job_can_write_customers"] = ok
    ok, err = as_user(CASHIER, customers.create_customer, customer_name="Lab Buyer Cashier")
    results["cashier_can_create_customers"] = ok
    results["cashier_create_error"] = None if ok else err
    ok, err = as_user(CASHIER, customers.update_customer, buyer_name, short_name="X")
    results["cashier_can_write_customers"] = ok
    results["cashier_write_error"] = None if ok else err
    ok, _ = as_user(CASHIER, company.get_profile)
    results["cashier_can_read_profile"] = ok
    ok, err = as_user(CASHIER, company.update_profile, note="NO")
    results["cashier_can_write_profile"] = ok
    ok, err = as_user(OUTSIDER, customers.list_customers)
    results["outsider_can_read_customers"] = ok
    ok, err = as_user(OUTSIDER, company.get_profile)
    results["outsider_can_read_profile"] = ok

    # 泰国税务局（RD）税号查询。解析口径的离线断言在 `tests/test_erp_tax_lookup.py`；
    # 这里验的是**站点里那条链路**：真实响应 → 缓存进 `ERP Tax Lookup Cache` → 第二次
    # 直接命中缓存不再打外部接口；接口连不上时只回原因、不抛异常（本实验室容器就是
    # 连不上那种）；以及谁有权查。夹具是 2026-09-23 从 `rdws.rd.go.th` 抓下来的。
    from unittest.mock import patch

    from pearnly_erp_portal.masters import api as masters_api

    fixture = (Path(__file__).resolve().parents[1] / "tests/fixtures"
               / f"rd_vat_{TAX_ID}.xml").read_text(encoding="utf-8")
    cache_key = f"TAX-{TAX_ID}-0-vat"
    results["tax_cache_doctype"] = bool(frappe.db.exists("DocType", "ERP Tax Lookup Cache"))

    ok, denied = as_user(CASHIER, masters_api.api_lookup_tax, tax_id=TAX_ID)
    results["tax_lookup_denied_for_cashier"] = not ok and "PermissionError" in str(denied)
    ok, denied = as_user(OUTSIDER, masters_api.api_lookup_tax, tax_id=TAX_ID)
    results["tax_lookup_denied_for_outsider"] = not ok and "PermissionError" in str(denied)
    ok, bad = as_user(SALES, masters_api.api_lookup_tax, tax_id="123")
    results["tax_lookup_bad_format"] = bad.get("error") if isinstance(bad, dict) else bad

    frappe.db.delete("ERP Tax Lookup Cache", {"tax_id": ["in", [TAX_ID, TAX_ID_ABSENT]]})
    frappe.db.commit()
    with patch("pearnly_erp_portal.masters.rd._soap_post", return_value=fixture) as soap:
        ok_sales, first = as_user(SALES, masters_api.api_lookup_tax, tax_id=TAX_ID)
        ok_purchase, _ = as_user(PURCHASE, masters_api.api_lookup_tax, tax_id=TAX_ID)
        first_data = first.get("data", {}) if isinstance(first, dict) else {}
        results["tax_lookup_allowed_for_sales"] = ok_sales and bool(first.get("ok"))
        results["tax_lookup_allowed_for_purchase"] = ok_purchase
        results["tax_lookup_name"] = first_data.get("name")
        results["tax_lookup_address"] = first_data.get("address")
        results["tax_lookup_first_is_fresh"] = first.get("cached") is False
        ok, second = as_user(SALES, masters_api.api_lookup_tax, tax_id=TAX_ID)
        results["tax_lookup_cached_second_time"] = bool(second.get("cached")) if ok else second
        results["tax_lookup_http_calls"] = soap.call_count
    results["tax_cache_rows"] = frappe.db.count("ERP Tax Lookup Cache", {"tax_id": TAX_ID})

    # 外部接口连不上（或税局真的没这家）时：只回原因、不抛异常，录入照常能存。
    ok, offline = as_user(SALES, masters_api.api_lookup_tax, tax_id=TAX_ID_ABSENT)
    results["tax_lookup_offline_raised"] = not ok
    results["tax_lookup_offline_error"] = offline.get("error") if isinstance(offline, dict) else offline

    # 岗位限制也要落到**表上**，不能只落在服务层：直连 REST 的路径不经过服务层的
    # guard，如果表上给了写权限，员工绕开页面就能改。
    def table_perms(doctype, role):
        return frappe.db.get_value(
            "Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0,
                               "if_owner": 0},
            ["read", "write", "create"], as_dict=True) or {}

    results["company_perms"] = {role: table_perms("ERP Company", role)
                                for role in ("ERP Admin", "ERP Sales", "ERP Cashier")}
    results["customer_perms"] = {role: table_perms("ERP Customer", role)
                                 for role in ("ERP Admin", "ERP Sales", "ERP Cashier")}

    frappe.set_user("Administrator")
    results["site_a_customers"] = frappe.db.count("ERP Customer")
    results["site_a_companies"] = frappe.db.count("ERP Company")

    frappe.destroy()
    connect(OTHER_SITE)
    # 隔离要验的是"**本轮的夹具**没有漏到另一个站点"，不是"另一个站点必须是空的"：
    # 演示站上留着给用户看的样本（客户/公司），空站断言会被那些样本误伤。
    # 本轮的夹具是唯一命名的（Lab Buyer Co / Lab Company A/B），照名字查就行。
    results["other_site_leaked_customers"] = frappe.db.count(
        "ERP Customer", {"customer_name": ["like", "Lab Buyer%"]})
    results["other_site_leaked_companies"] = frappe.db.count(
        "ERP Company", {"company_name": ["like", "Lab Company%"]})
    # 税号查询的缓存也只落在查它的那个站点。查完就删掉：那是 7 天可再生的缓存，
    # 不是要留着给用户看的样本。
    results["other_site_tax_cache_rows"] = frappe.db.count(
        "ERP Tax Lookup Cache", {"tax_id": ["in", [TAX_ID, TAX_ID_ABSENT]]})
    frappe.destroy()
    connect(SITE)
    frappe.set_user("Administrator")
    results["tax_cache_rows_cleaned"] = frappe.db.count(
        "ERP Tax Lookup Cache", {"tax_id": ["in", [TAX_ID, TAX_ID_ABSENT]]})
    frappe.db.delete("ERP Tax Lookup Cache", {"tax_id": ["in", [TAX_ID, TAX_ID_ABSENT]]})
    frappe.db.commit()
    results["tax_cache_rows_left"] = frappe.db.count(
        "ERP Tax Lookup Cache", {"tax_id": ["in", [TAX_ID, TAX_ID_ABSENT]]})

    expected = {
        "default_company": True,
        "profile_readable_by_owner": True,
        "profile_is_default": True,
        "update_profile": True,
        "bad_fiscal_month_allowed": False,
        "bad_tax_id_checksum_allowed": False,
        "create_company": True,
        "profile_still_default": True,
        "create_customer": True,
        "create_walkin": True,
        "deactivate_customer": True,
        "hidden_after_deactivate": True,
        "visible_with_inactive": True,
        "reactivate_customer": True,
        "product_job_can_read_customers": False,
        "product_job_can_write_customers": False,
        "cashier_can_create_customers": False,
        "cashier_can_write_customers": False,
        "cashier_can_read_profile": True,
        "cashier_can_write_profile": False,
        "outsider_can_read_customers": False,
        "outsider_can_read_profile": False,
        # 税号查询：采购/销售岗位能查，收银与无岗位不能；第二次命中缓存、只打一次外部接口。
        "tax_cache_doctype": True,
        "tax_lookup_denied_for_cashier": True,
        "tax_lookup_denied_for_outsider": True,
        "tax_lookup_bad_format": "invalid_format",
        "tax_lookup_allowed_for_sales": True,
        "tax_lookup_allowed_for_purchase": True,
        "tax_lookup_first_is_fresh": True,
        "tax_lookup_cached_second_time": True,
        "tax_lookup_http_calls": 1,
        "tax_cache_rows": 1,
        "tax_lookup_offline_raised": False,
        # 另一站点不许出现本轮的夹具（不要求它是空站：演示站留着用户看的样本）。
        "other_site_leaked_customers": 0,
        "other_site_leaked_companies": 0,
        "other_site_tax_cache_rows": 0,
        "tax_cache_rows_left": 0,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}

    buyer_row = results.get("buyer_row") or {}
    if not isinstance(buyer_row, dict) or buyer_row.get("tax_id") != "0105550000015" \
            or buyer_row.get("branch_no") != "00002":
        problems["buyer_kept_company_fields"] = buyer_row
    walkin_row = results.get("walkin_row") or {}
    if not isinstance(walkin_row, dict) or walkin_row.get("tax_id") is not None \
            or walkin_row.get("branch_type") is not None:
        problems["walkin_should_drop_tax_and_branch"] = walkin_row
    if results.get("profile_after", {}).get("note") != "LAB":
        problems["profile_note"] = results.get("profile_after")
    company_count = results.get("company_count")
    if not isinstance(company_count, int) or company_count < 2:
        problems["company_count"] = company_count
    if results.get("master_doctypes") != ["ERP Company", "ERP Customer"]:
        problems["master_doctypes"] = results.get("master_doctypes")
    # 回填的抬头与地址要跟税局的响应一致：旧项目那套标签名在这条真实响应上会丢省和区。
    if results.get("tax_lookup_name") != "บริษัท ซินเซียร์ไอซ์ จำกัด":
        problems["tax_lookup_name"] = results.get("tax_lookup_name")
    if "กรุงเทพมหานคร 10510" not in str(results.get("tax_lookup_address") or ""):
        problems["tax_lookup_address"] = results.get("tax_lookup_address")
    if results.get("tax_lookup_offline_error") not in (
            "invalid_format", "not_found", "not_vat_registered", "rd_unreachable", "parse_error"):
        problems["tax_lookup_offline_error"] = results.get("tax_lookup_offline_error")

    company_perms = results.get("company_perms") or {}
    if not (company_perms.get("ERP Admin") or {}).get("write"):
        problems["admin_cannot_write_company"] = company_perms.get("ERP Admin")
    if not (company_perms.get("ERP Sales") or {}).get("read"):
        problems["sales_job_cannot_read_company"] = company_perms.get("ERP Sales")
    if (company_perms.get("ERP Sales") or {}).get("write"):
        problems["sales_job_can_write_company"] = company_perms.get("ERP Sales")
    customer_perms = results.get("customer_perms") or {}
    if not (customer_perms.get("ERP Sales") or {}).get("create"):
        problems["sales_job_cannot_create_customer"] = customer_perms.get("ERP Sales")
    cashier_customer = customer_perms.get("ERP Cashier") or {}
    if not cashier_customer.get("read") or cashier_customer.get("create") \
            or cashier_customer.get("write"):
        problems["cashier_customer_perms"] = cashier_customer

    print(json.dumps({"site": site, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
