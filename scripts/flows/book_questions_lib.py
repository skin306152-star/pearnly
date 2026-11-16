"""开始前四题（A3 / P2-2）四条流程共用的站点脚本：单位、负库存、出库成本、现金科目。

实验栈没有真电脑读账套：站点里临时放一份账套资料（`express.facts`，同 `erp_express_new_item` / `cc_operator_stop_choice`），
cleanup 原样还原；记住的四题选择按对象前缀删掉；建的单作废。只用 new 数据集（接了 Express）。

每条都走「没定 → 开始前被问 → 答了 → 准备好的载荷照写 → 第二次不问」，另各核一处「只有一个合格选项 / 后台直写不新增停下」：
- 单位、现金科目、负库存走网页提交前的弹窗（`api.preflight`，表单还没存）；
- 出库成本走 CC 直写的开始前确认卡片（`precheck.collect_document`，单据已存；网页弹窗不问成本）。
"""
from __future__ import annotations

import uuid

from erp_lib import company as dataset_company
from erp_lib import void_documents

USER = "admin@erp.regression"
ACCOUNTS = ("11-01-01-00", "11-02-01-00", "11-04-01-00", "52-08-00-00", "41-01-00-00", "11-03-01-00", "21-04-01-00",
            "51-03-00-00", "21-01-01-00", "11-06-01-00")
MAPPING = {"revenue_acc": "41-01-00-00", "ar_acc": "11-03-01-00", "vat_output_acc": "21-04-01-00", "fallback_acc": "51-03-00-00",
           "ap_acc": "21-01-01-00", "vat_input_acc": "11-06-01-00"}


def tax_id(seed: str) -> str:
    """编造的 13 位税号（校验位照泰国税号算，ERP 建对方档要核）。"""
    digits = [int(c) for c in ("01055" + seed)[:12].ljust(12, "0")]
    check = (11 - sum(d * (13 - i) for i, d in enumerate(digits)) % 11) % 10
    return "".join(map(str, digits)) + str(check)


def prepare(lab, manifest, case: str, body: str) -> dict:
    """站点里：放账套资料（``settings`` / ``products`` / ``cash`` 由 ``body`` 前面的变量给）、跑 ``body``、打印 ``out``。"""
    tag = uuid.uuid4().hex[:6]
    company = dataset_company(manifest)
    warehouse = manifest["warehouses"][0]["name"]
    lab.keep_fields(manifest["site"], "ERP Company", company, ["express_sync_state"])
    script = f"""
import os
from pearnly_erp_portal.express import api, book_answers, choices, facts, precheck, push, target
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.masters import customers
from pearnly_erp_portal.products import service as products
from pearnly_erp_portal.purchasing import suppliers

frappe.set_user({USER!r})
company, warehouse, tag = {company!r}, {warehouse!r}, {tag!r}
companies.set_active_company(company)
key = target.pick(push._company(company), frappe.utils.nowdate())["book"]["key"]
path = facts._path(key)
backup = path + ".flow-" + tag
if os.path.exists(path):
    os.replace(path, backup)
out = {{"company": company, "book": key, "path": path, "backup": backup, "docs": [], "tag": tag}}
ACCOUNTS = [{{"code": code, "name": code, "type": code[0]}} for code in {list(ACCOUNTS)!r}]

def book(products_rows, settings, cash="11-01-01-00", accounts=None):
    facts.save(key, {{"version": 2, "customers": [], "suppliers": [], "departments": [], "products": products_rows,
                     "units": [{{"code": "ถง", "name": "ถัง"}}, {{"code": "ลง", "name": "ลัง"}}, {{"code": "กก", "name": "กิโลกรัม"}}],
                     "stock": [], "settings": settings, "cash_account": cash,
                     "facts": {{"accounts": accounts or ACCOUNTS, "mapping": {MAPPING!r},
                               "catalog": {{"isacc_candidates": [{{"code": "DM", "desc": "วัตถุดิบ", "inv_acc": "11-04-01-00",
                                                                  "fit": True, "used_by": 4}}]}}}}}})

def asked(found):
    return [{{"kind": i["kind"], "subject": i["subject"], "options": [o.get("code") for o in i.get("options") or []],
              "entry": [o.get("code") for o in i.get("options") or [] if o.get("entry")], "value": i.get("value") or i.get("current") or ""}}
            for i in found]

def mine(found, kinds=("unit", "negative_stock", "cost_basis", "cash_account")):
    return [i for i in asked(found) if i["kind"] in kinds]

FOUR = ("unit", "negative_stock", "cost_basis", "cash_account")

def settle(doctype, form, needed):
    # 四题以外的题（对方档新建 / 已有、物料……）照弹窗的预选答掉（没预选取第一个选项），收尾时删掉这些记忆
    given = [{{"kind": i["kind"], "subject": i["subject"], "value": i.get("current") or (i.get("options") or [{{}}])[0].get("code")}}
             for i in needed if i["kind"] not in FOUR and (i.get("current") or i.get("options"))]
    if given:
        out.setdefault("others", []).extend([g["kind"], g["subject"]] for g in given)
        return api.preflight(doctype, json.dumps(form), json.dumps(given))["needed"]
    return needed

def background(doctype, form):
    # 后台直写那条路（不在开始前判定里）：只套用记住的值
    result, _vat, _unmapped = push._prepare_body(doctype, company, push.form_document(doctype, form, company))
    return result

{body}
print(json.dumps(out, default=str))
"""
    return {**lab.python(manifest["site"], script), "case": case}


def cleanup(lab, manifest, params):
    """账套资料还原、这份账套名下四题的记忆删掉（对象都以账套键开头）、物料题的记忆删掉、建的单作废。成功、失败、超时都跑。"""
    lab.python(manifest["site"], f"""
import os
from pearnly_erp_portal.express import choices
frappe.set_user({USER!r})
path, backup, book = {params.get("path")!r}, {params.get("backup")!r}, {params.get("book")!r}
if path and os.path.exists(path):
    os.remove(path)
if backup and os.path.exists(backup):
    os.replace(backup, path)
if book:
    for row in frappe.get_all(choices.CHOICE, filters={{"company": {params.get("company")!r},
                                                       "kind": ["in", ["unit", "negative_stock", "cost_basis", "cash_account"]],
                                                       "subject": ["like", book + "|%"]}}, pluck="name", limit_page_length=0):
        frappe.delete_doc(choices.CHOICE, row, ignore_permissions=True, force=True)
for kind, subject in [["item", s] for s in {params.get("item_subjects") or []!r}] + {params.get("others") or []!r}:
    found = choices._name({params.get("company")!r}, kind, subject)
    if frappe.db.exists(choices.CHOICE, found):
        frappe.delete_doc(choices.CHOICE, found, ignore_permissions=True, force=True)
frappe.db.commit()
print("{{}}")
""")
    for doctype in ("Purchase Document", "ERP Sales Document"):
        names = [name for kind, name in params.get("docs") or [] if kind == doctype]
        if names:
            void_documents(lab, manifest["site"], names, doctype=doctype)


def steps(params, camera_code=None):
    return []  # 全在站点里核：页面那张弹窗 / 卡片按服务端给的题目画（guided-frontend 已认数字输入），页面另由引导卡流程覆盖


def judge(params, results, camera_code=None):
    return []
