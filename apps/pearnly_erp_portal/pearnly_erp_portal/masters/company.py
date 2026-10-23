"""公司：本租户自己的主体资料（抬头、税号、分支、联系方式、地址）。

用户 2026-09-23 先要"就一个公司就好了"，同一天又说"**我要做一租户多公司**" ——
一个租户可以有**多家**公司，各家的单据、仓库、库存互相分开。做法照官方（ERPNext）：

· **公司**就是这张表的一条记录（官方 `Company` 也是一个列表/树）；
· **当前公司**是用户默认值 `company`（`frappe.defaults.get_user_default`，官方同一个键），
  官方还会回落到 User Permission 里的默认公司；
· **公司之间的隔离**用官方的 `User Permission`（`allow=ERP Company`）—— 给某个用户指一家
  或几家，框架就按它过滤；没指就按"全部"（官方默认，员工要收窄就在团队页里勾）。

默认公司不存在时自动建一条，否则新租户一来就先看到空页面。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import contracts, tax_ids

DOCTYPE = "ERP Company"
NOT_FOUND = "Company not found"
NAME_REQUIRED = "A company needs a name"
INVALID = "Invalid value"

# 「单号前缀」（doc_prefix）不在这里：编号规则是写死的（PE / SI / IR / IS / AD），这个字段从未接上，
# 用户 2026-09-28 定为隐藏（录制问题 #15）。库里的列与旧值保留，不读不写。
LIST_FIELDS = ("name", "company_name", "short_name", "tax_id", "branch", "phone",
               "contact_person", "contact_email", "promptpay_id",
               "fiscal_year_start_month", "is_default", "is_active", "pos_enabled",
               "address", "note")

# 收银支持的四种收款方式（与 `cashier/contracts.py` 的 `PAYMENT_METHODS` 同一集合）。
# 新建公司默认四种全开（用户 2026-09-24："收款方式添加，默认给他几个字段"）——
# 泰国店里这四种就是常态，先给全、不需要的再勾掉。
POS_PAYMENT_METHODS = ("cash", "card", "promptpay", "transfer")


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


def ensure_default() -> str:
    """默认公司：没有就在第一次需要时建一条空档，资料页才有东西可填。"""
    existing = frappe.db.get_value(DOCTYPE, {"is_default": 1}, "name")
    if existing:
        return existing
    any_company = frappe.db.get_value(DOCTYPE, {}, "name", order_by="creation asc")
    if any_company:
        frappe.db.set_value(DOCTYPE, any_company, "is_default", 1)
        frappe.cache.delete_key("bootinfo")  # set_value 不走 on_change，公司清单缓存要自己清
        return any_company
    doc = frappe.get_doc({"doctype": DOCTYPE, "company_name": frappe.conf.get("pearnly_company_name")
                          or "My Company", "is_default": 1, "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


# ── 公司作用域（当前公司 / 能用哪些公司 / 切换）───────────────────────────────

def allowed_companies(user=None) -> list:
    """当前用户能用哪些公司：有 User Permission 就按它（官方隔离件），否则全部启用的。

    顺序把默认公司排前面，这样"没选过"的人拿到的就是租户的默认那家。
    """
    user = user or frappe.session.user
    order_by = "is_default desc, company_name asc"
    if user == "Administrator":
        rows = frappe.get_all(DOCTYPE, {"is_active": 1}, ["name"], order_by=order_by)
        return [row["name"] for row in rows]
    permitted = frappe.get_all("User Permission", filters={"user": user, "allow": DOCTYPE},
                               pluck="for_value")
    if permitted:
        rows = frappe.get_all(DOCTYPE, {"name": ["in", permitted], "is_active": 1}, ["name"], order_by=order_by)
        return [row["name"] for row in rows]
    rows = frappe.get_all(DOCTYPE, {"is_active": 1}, ["name"], order_by=order_by)
    return [row["name"] for row in rows]


def active_company(user=None) -> str:
    """当前公司。官方口径：用户默认值 `company`（没设就看 User Permission 的默认那条），
    再回落租户的默认公司；都不行就报错，不猜一个出来。
    """
    user = user or frappe.session.user
    allowed = allowed_companies(user)
    if not allowed:
        frappe.throw(_("No company is available for this account"), frappe.PermissionError)
    from frappe.defaults import get_user_default
    chosen = get_user_default("company", user) or frappe.db.get_default("company")
    if chosen in allowed:
        return chosen
    return allowed[0]


def company_options(user=None) -> list:
    """切换公司要的那份清单：名字 + 抬头 + 是不是当前这家。"""
    user = user or frappe.session.user
    current = active_company(user)
    rows = frappe.get_all(DOCTYPE, {"name": ["in", allowed_companies(user)]},
                          ["name", "company_name", "short_name"],
                          order_by="is_default desc, company_name asc")
    return [{"name": row["name"], "company_name": row["company_name"] or row["name"],
             "short_name": row["short_name"] or "", "is_current": row["name"] == current}
            for row in rows]


def set_active_company(name, user=None) -> dict:
    """切换当前公司：官方做法就是写用户默认值（谁都能切自己那一份，不用管理员）。"""
    user = user or frappe.session.user
    name = str(name or "").strip()
    if name not in allowed_companies(user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    frappe.defaults.set_user_default("company", name, user)
    return {"company": name, "company_name": frappe.db.get_value(DOCTYPE, name, "company_name")}


# ── 收银台开关（公司级）──────────────────────────────────────────────────────
#
# 用户 2026-09-24："新增是否启用 POS 收银台 …… 不勾选就不显示 pos、记录、交接班，
# 以及团队管理里的收银"。所以这一位决定的不是"某台机器"，而是**这家公司做不做收银**：
# 关掉之后入口（侧栏那三页）、岗位（团队页的"收银"）、服务端能力（`cashier.*`）一起收。
#
# 判据一律在服务端（菜单显隐只是减负）：`require_pos()` 挂在收银域每个入口上。

def pos_enabled(company=None, user=None) -> bool:
    """这家公司有没有开收银台（没给公司就按"当前公司"）。"""
    try:
        name = document_company(company, user)
    except Exception:
        return False
    return bool(frappe.db.get_value(DOCTYPE, name, "pos_enabled"))


def pos_enabled_any(user=None) -> bool:
    """这个账号**能用**的公司里，有任意一家开了收银台。

    侧栏那三页、团队页的"收银"岗位都看它：一个租户里只要还有一家公司做收银，
    入口与岗位就该在（进哪一家由当前公司决定）。
    """
    user = user or frappe.session.user
    names = allowed_companies(user)
    if not names:
        return False
    return bool(frappe.db.get_value(DOCTYPE, {"name": ["in", names], "pos_enabled": 1}, "name"))


def require_pos(company=None, user=None) -> str:
    """收银域的每个入口都先过这一关：这家公司没开收银台就拒绝。回公司名。"""
    name = document_company(company, user)
    if not pos_enabled(name, user):
        frappe.throw(_("The cashier is not enabled for this company"), frappe.PermissionError)
    return name


def payment_methods_of(company=None, user=None) -> list:
    """这家公司收银收哪几种钱（建公司时勾的那几种；老公司由迁移补成四种全开）。

    回的是**服务端 key**（cash/card/promptpay/transfer，见 `cashier/contracts.py`），
    界面文案由各自的词条表翻。
    """
    return configured_payment_methods(document_company(company, user))


def configured_payment_methods(company) -> list:
    """同上，但不按当前账号核公司范围：给已经取到手的单据（班次 / 收银台）读它那家公司的配置。"""
    rows = frappe.get_all("ERP Company Payment Method", filters={"parent": company or ""},
                          pluck="method", order_by="idx asc")
    return [m for m in rows if m] or list(POS_PAYMENT_METHODS)


def document_company(value=None, user=None) -> str:
    """单据上的公司：给了就用给的（必须在可用范围内），没给就用当前公司。"""
    user = user or frappe.session.user
    value = str(value or "").strip()
    if not value:
        return active_company(user)
    if value not in allowed_companies(user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return value


def scope(user=None) -> dict:
    """列表/汇总的默认过滤：只看当前公司。与 `guard.scope_filters()`（本人/全部）叠着用。"""
    return {"company": active_company(user)}


def for_document(name) -> dict:
    """单据抬头那一块：给了公司名就取它，没有就取默认公司（导出件/打印用）。"""
    target = str(name or "").strip()
    if not target or not frappe.db.exists(DOCTYPE, target):
        target = ensure_default()
    doc = frappe.get_doc(DOCTYPE, target)
    return doc.as_dict()


def list_companies(*, include_inactive=False, limit=50) -> list[dict]:
    guard.require("company.read")
    filters = {} if include_inactive else {"is_active": 1}
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by="is_default desc, company_name asc",
                           limit_page_length=int(limit))


def create_company(*, company_name, warehouse_name=None, warehouses=None, terminals=None,
                   payment_methods=None, **fields) -> dict:
    """新建一家公司，**一次把主档配齐**（用户 2026-09-24："新建公司可以把有些主档全部配齐
    …… 仓库可以加多个，收银台也是 …… 第一次配好后后面的数据就能打通了"）。

    传进来的形状（都可选，缺了就按最小可用配）：

    · `warehouses`：仓库名列表，**可以多个**；第一个（或 `warehouse_name`）就是这家公司最先
      建的那个仓（"默认仓"那一套本轮已经删掉，这里只是"先建哪个"）。
    · `terminals`：收银台列表 `[{terminal_name, warehouse}]`，**可以多台**；`warehouse` 指
      上面某个仓库名（给了就用它，没给就用第一个仓）。**收银台卖货的仓是显式写上去的**。
    · `payment_methods`：这家公司收哪几种钱（`cash/card/promptpay/transfer` 里挑）；
      不给就四种全开（泰国店里的常态）。
    · `pos_enabled`：这家公司做不做收银，走 `fields`（`_apply`）。
    """
    guard.require("company.manage")
    name = contracts.clean(company_name, contracts.MAX_NAME)
    if not name:
        frappe.throw(_(NAME_REQUIRED))
    doc = frappe.get_doc({"doctype": DOCTYPE, "company_name": name, "is_active": 1,
                          "pos_enabled": 1})
    _apply(doc, fields)
    doc.insert()
    if "pos_enabled" not in fields:
        doc.pos_enabled = 1                    # 新公司默认做收银，不要的再勾掉

    made = _create_warehouses(doc.name, warehouse_name, warehouses)
    if not made:
        frappe.throw(_("A company needs at least one warehouse"))
    places = {row["warehouse_name"]: row["name"] for row in made}
    counters = _create_terminals(doc.name, terminals, made[0]["name"], places)
    _apply_payment_methods(doc, payment_methods)
    frappe.db.commit()
    return {"name": doc.name, "warehouse": made[0]["name"], "warehouses": made,
            "terminals": counters,
            "payment_methods": [row.method for row in doc.get("payment_methods") or []]}


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = frappe.parse_json(value) if value.strip().startswith("[") else value.split("\n")
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _create_warehouses(company: str, first_name, extra) -> list[dict]:
    """把表单上那几行仓库建出来（**可以多个**，按给的顺序）。

    第一行（或单独给的 `warehouse_name`，都没有就 `Main`）走 `ensure_for_company`：
    它会复用同名仓，保证"这家公司至少有一个仓"。其余按名字建，重名就复用。
    """
    from pearnly_erp_portal.inventory import warehouses

    names = [str(row or "").strip() for row in _as_list(extra)]
    first = str(first_name or "").strip() or (names[0] if names else "Main")
    made = []
    first_id = warehouses.ensure_for_company(company, first)
    made.append({"name": first_id,
                 "warehouse_name": frappe.db.get_value("ERP Warehouse", first_id, "warehouse_name")})
    for wanted in [row for row in names if row and row != first]:
        existing = frappe.db.get_value("ERP Warehouse",
                                        {"company": company, "warehouse_name": wanted}, "name")
        if existing:
            made.append({"name": existing, "warehouse_name": wanted})
            continue
        doc = frappe.get_doc({"doctype": "ERP Warehouse", "warehouse_name": wanted,
                              "company": company, "is_active": 1})
        doc.insert(ignore_permissions=True)
        made.append({"name": doc.name, "warehouse_name": wanted})
    return made


def _create_terminals(company: str, rows, first_warehouse: str, places: dict) -> list[str]:
    """新建公司表单上那几台收银台（**可以多台**）。

    一台都没给就老实建一台（那是以前 `terminals.ensure_default` 懒建的位置）——
    收银台没有仓就卖不了货，所以它身上那个仓必须是**显式**的。
    """
    from pearnly_erp_portal.cashier import terminals

    wanted = _as_list(rows)
    if not wanted:
        return [terminals.ensure_default(company, warehouse=first_warehouse)]
    made = []
    for index, row in enumerate(wanted):
        if not isinstance(row, dict):
            row = {"terminal_name": row}
        label = str(row.get("terminal_name") or "").strip()
        if not label:
            continue
        place = places.get(str(row.get("warehouse") or "").strip()) or first_warehouse
        result = terminals.create_terminal(terminal_name=label, warehouse=place,
                                          is_default=index == 0, company=company)
        made.append(result["name"])
    return made or [terminals.ensure_default(company, warehouse=first_warehouse)]


def _apply_payment_methods(doc, methods) -> None:
    """这家公司收哪几种钱。空 = 四种全开，与 `payment_methods_of` 同一口径。"""
    wanted = [str(row or "").strip() for row in _as_list(methods)] or list(POS_PAYMENT_METHODS)
    doc.set("payment_methods", [])
    for key in POS_PAYMENT_METHODS:            # 按固定顺序落行：前后端看到的顺序一致
        if key in wanted:
            doc.append("payment_methods", {"method": key, "label": key})
    doc.save(ignore_permissions=True)


def get_profile(name=None) -> dict:
    """公司资料：不带名字就是默认公司。

    多回两个判据给页面用，前端不再复刻一份角色清单：

    · `can_write`：能不能改这张资料（服务端 `company.manage`）；
    · `can_lookup`：能不能按税号查泰国税务局（服务端 `tax.lookup`，与供应商/
      客户两页同一个能力码）—— 没这个能力的岗位那边不出现那颗按钮。
    """
    guard.require("company.read")
    doc = _get(document_company(name))
    payload = {field: doc.get(field) for field in LIST_FIELDS}
    payload["can_write"] = guard.has("company.manage")
    payload["can_lookup"] = guard.has("tax.lookup")
    # 收银那一节：这家公司收哪几种钱、有哪几台收银台、以及"能不能改"。
    # 收发银台的管理判据放在**公司**这一侧（`company.manage`）—— 建公司时就要配，
    # 而那时不一定有人已经拿到收银岗位（见 `create_company`）。
    payload["payment_methods"] = [row.method for row in doc.get("payment_methods") or []]
    payload["terminals"] = terminals_of(doc.name)
    payload["can_manage_pos"] = guard.has("company.manage")
    # 这家公司接了 Express 账本（CC 桌面版报过它的账套）：管理员可在这一屏手动同步主档与库存（`express.api.sync_company`）
    from pearnly_erp_portal.express import push as express_push

    payload["can_sync_express"] = guard.has("team.manage") and express_push.linked(doc.name)
    if payload["can_sync_express"]:
        # 同步在后台做：最近一次的账套、时间、状态与失败原因（`express.masters._record`）给这一屏显示
        payload["express"] = {field: doc.get(field) for field in
                              ("express_book", "express_synced_at", "express_sync_state", "express_sync_note")}
    return payload


def terminals_of(company=None) -> list[dict]:
    """这家公司的收银台（公司页「收银台」那一节；只要 `company.read` 就看得见）。

    收银台卖货的仓在 `warehouse` / `warehouse_name` 上 —— 它就是这家公司做收银时的
    "货从哪个仓出"，是显式值（"默认仓"那一套已经删掉了）。
    """
    guard.require("company.read")
    name = document_company(company)
    rows = frappe.get_list("ERP POS Terminal", filters={"company": name},
                           fields=["name", "terminal_name", "warehouse", "is_default", "is_active"],
                           order_by="is_default desc, terminal_name asc", limit_page_length=0)
    for row in rows:
        row["warehouse_name"] = frappe.db.get_value(
            "ERP Warehouse", row.get("warehouse"), "warehouse_name") or row.get("warehouse") or ""
    return rows


def selling_warehouse(company=None) -> str | None:
    """这家公司**卖货的那个仓** = 收银台上配的那个仓（一个店一个仓）。

    为什么要有这一位：收银台卖货是从它自己的仓扣的，进货/出入库如果还按商品上那个遗留的
    "默认仓"落货，两边就是两个仓 —— 卖货仓一路负库存、成本立不起来，收发存报表的金额列
    自然一直是空的（用户 2026-09-24 实测报的就是这个："退货退款作废后的物料仓库数量、金额，
    以及收发存报表，数据要关联上，不要只顾 POS 这边"）。他的口径是"**进货就进卖货那个仓**"。

    口径与 `cashier.terminals.place_of` 一致：只认收银台自己身上写着的仓，不猜。
    多台启用收银台指向**不同**的仓时不替谁拿主意，回 None，由调用方按"唯一的仓 / 报错"处理。
    """
    name = document_company(company)
    places = {str(row or "").strip() for row in frappe.get_all(
        "ERP POS Terminal", filters={"company": name, "is_active": 1},
        pluck="warehouse", limit_page_length=0) or []}
    places.discard("")
    return next(iter(places)) if len(places) == 1 else None


def create_terminal(*, company_name=None, terminal_name, warehouse=None, is_default=False) -> dict:
    """在公司页里加一台收银台（判据是 `company.manage`，见 `get_profile` 的说明）。"""
    guard.require("company.manage")
    from pearnly_erp_portal.cashier import terminals as service

    return service.create_terminal(terminal_name=terminal_name, warehouse=warehouse,
                                   is_default=is_default,
                                   company=document_company(company_name), internal=True)


def update_terminal(name, **changes) -> dict:
    """改一台收银台：名字 / 卖货的仓 / 启用停用（同样走 `company.manage`）。"""
    guard.require("company.manage")
    from pearnly_erp_portal.cashier import terminals as service

    return service.update_terminal(name, internal=True, **changes)


def set_payment_methods(company=None, methods=None) -> dict:
    """这家公司收哪几种钱（公司页那一节）。空 = 四种全开。"""
    guard.require("company.manage")
    doc = _get(document_company(company))
    _apply_payment_methods(doc, methods)
    frappe.db.commit()
    return {"name": doc.name,
            "payment_methods": [row.method for row in doc.get("payment_methods") or []]}


def save_terminals(company=None, rows=None) -> dict:
    """公司页「收银台」那张表：一次提交格子里的改动（新增 / 改名 / 换卖货的仓 / 启停）。

    **不做删除**：要停用就把状态那格取消勾选（数据留着，历史班次与小票还指着它）。
    换仓时那个值是仓库的名字或编号，**必须解析到这家公司真实存在的仓**（"默认仓"没了，
    写错就会落到别的公司账上 → 一律报错，不静默）。
    """
    guard.require("company.manage")
    from pearnly_erp_portal.cashier import terminals as service
    from pearnly_erp_portal.inventory import warehouses

    name = document_company(company)
    for row in _as_list(rows):
        if not isinstance(row, dict):
            continue
        label = str(row.get("terminal_name") or "").strip()
        if not label:
            frappe.throw(_(NAME_REQUIRED))
        wanted = str(row.get("warehouse") or "").strip()
        place = warehouses.required(wanted, company=name) if wanted else None
        active = 1 if row.get("is_active") in (True, 1, "1", "true") else 0
        existing = str(row.get("name") or "").strip()
        if existing and frappe.db.exists("ERP POS Terminal", existing):
            changes = {"terminal_name": label, "is_active": active}
            if place:
                changes["warehouse"] = place
            service.update_terminal(existing, internal=True, **changes)
        else:
            service.create_terminal(terminal_name=label, warehouse=place, company=name,
                                    internal=True)
    frappe.db.commit()
    return {"name": name, "terminals": terminals_of(name)}


def update_company(name, **fields) -> dict:
    guard.require("company.manage")
    doc = _get(document_company(name))
    _apply(doc, fields)
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}


def update_profile(**fields) -> dict:
    """改公司资料（默认公司）。"""
    guard.require("company.manage")
    return update_company(ensure_default(), **fields)


def _apply(doc, fields) -> None:
    for field, limit in (("company_name", contracts.MAX_NAME), ("short_name", contracts.MAX_SHORT),
                         ("tax_id", contracts.MAX_TAX_ID), ("branch", contracts.MAX_CONTACT),
                         ("phone", contracts.MAX_PHONE), ("contact_person", contracts.MAX_CONTACT),
                         ("contact_email", contracts.MAX_EMAIL),
                         ("promptpay_id", contracts.MAX_PROMPTPAY),
                         ("address", contracts.MAX_ADDRESS),
                         ("note", contracts.MAX_NOTE)):
        if field not in fields:
            continue
        try:
            value = contracts.clean(fields[field], limit)
        except ValueError:
            frappe.throw(_(INVALID))
        if field == "company_name" and not value:
            frappe.throw(_(NAME_REQUIRED))
        if field == "tax_id":
            tax_ids.require_valid(value, previous=doc.get("tax_id"), thai_required=True)
        doc.set(field, value)
    if "fiscal_year_start_month" in fields:
        if not contracts.fiscal_month_ok(fields["fiscal_year_start_month"]):
            frappe.throw(_("The fiscal year start month must be between 1 and 12"))
        doc.fiscal_year_start_month = int(fields["fiscal_year_start_month"])
    for field in ("is_default", "is_active"):
        if field in fields:
            doc.set(field, 1 if fields[field] in (True, 1, "1", "true") else 0)
    # 收银台开关（公司级）：勾了这家公司做收银，不勾就没有那三页、没有收银岗位、收银接口拒绝。
    if "pos_enabled" in fields:
        doc.pos_enabled = 1 if fields["pos_enabled"] in (True, 1, "1", "true") else 0
