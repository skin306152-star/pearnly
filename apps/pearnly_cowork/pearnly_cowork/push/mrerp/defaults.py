"""推送时这家公司的导入值：按这次读到的主档与这份账套选过的值（准备推送时冻结的 `_mrerp_defaults`）算好放进 mappings。

规则见 `targets/mrerp_masters.py`；读取见 `connections/mrerp_master_reader.py`。生成器只从 mappings 取（见 `xlsx/defaults.py`
与各主档生成器的 `_mrerp_*` 覆盖键），算不出就整批停下、给出原因码请人选，不写任何别家公司的值。

- 表头 / 明细：分店、部门、工作、仓库、员工，销项另有销售区、运输；没选过就跟 MR.ERP 网页新建单据的默认（总店、
  按代码第一条）；销项客户档里有员工 / 区域 / 运输的优先用客户档的。
- 现金销售：现金收款用参考科目 2「เงินสด」，转账 / 刷卡用这份账套选的银行科目。
- 自建客户 / 供应商：类型、应收 / 应付科目照这家公司现有档案最常用的那组（没有明显多数才问）；销售员 / 区域 / 运输
  代码同表头；称谓按名称前缀对称谓表，分店按票面分店号。
- 自建商品：分类、单位；货品 8 个科目取参考科目，费用物料 8 个科目取这份账套选的费用科目；默认供应商取现金供应商
  （没有就取第一个供应商）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pearnly_cowork.connections.mrerp_master_reader import MasterReader
from pearnly_cowork.targets import mrerp_masters as rules

# 字段 → 从哪些接口读（先用这次会话里已经读过的，都没读过就读第一个）。
_MODULES = {
    "branch": ("fabillrev", "artran"), "dept": ("fabillrev", "artran"), "job": ("fabillrev", "artran"),
    "employee": ("fabillrev", "artran"), "store": ("stktraniss", "artran"),
    "area": ("armas", "artran"), "delivery": ("armas", "artran"),
    "customer_type": ("armas",), "supplier_type": ("apmas",), "customer_branch": ("armas", "apmas", "artran"),
    "product_group": ("stkmas",), "unit": ("stkmas",), "chart": ("armas", "apmas", "stkmas"),
}
_PRODUCT_REFERENCE = ("acc_rev", "acc_ret", "acc_dis", "acc_pur", "acc_purret", "acc_purdis", "acc_inv", "acc_cost")
# 「最常用的那组」从哪类现有档案抽样
_SAMPLE_MODULE = {"customer_type": "armas", "ar_account": "armas", "supplier_type": "apmas", "ap_account": "apmas",
                  "product_group": "stkmas", "unit": "stkmas"}
CASH_PARTY = "เงินสด"  # 与 xlsx/lookups.MRERP_CASH_CUSTOMER 同值（Pearnly 自建的现金客户 / 供应商码）


# MR.ERP 导入报「主档里找不到」（imp*/component/importpc.php）：表头 / 明细里按账套取的值对不上这家公司的主档，
# 重推还是一样，要人重选默认值（选过的值在 MR.ERP 里被删改过）。
MASTER_MISMATCH = ("ไม่พบข้อมูลสาขา", "ไม่พบข้อมูลแผนก", "ไม่พบข้อมูลงาน", "ไม่พบข้อมูลคลัง", "ไม่พบข้อมูลพนักงาน",
                   "ไม่พบข้อมูลพื้นที่การขาย", "ไม่พบข้อมูลประเภทขนส่ง", "ไม่พบข้อมูลรับอื่นๆ")


def tag_default_changed(reasons: List[Any]) -> List[str]:
    """失败原因里有主档对不上 → 置顶 ERR_MRERP_DEFAULT_CHANGED（保留原文，幂等），网页按它请人重选。"""
    out = [str(r) for r in (reasons or [])]
    if any(p in r for r in out for p in MASTER_MISMATCH) and rules.CHANGED not in out:
        return [rules.CHANGED] + out
    return out


class Missing(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class AccountDefaults:
    def __init__(self, reader: MasterReader, chosen: Dict[str, Any]):
        self.reader = reader
        self.chosen = {k: str(v).strip() for k, v in (chosen or {}).items() if str(v or "").strip()}
        self.observed: Dict[str, List[Dict[str, str]]] = {}

    # ── 取一个字段 ────────────────────────────────────────────────────────

    def _options(self, field: str) -> Optional[List[Dict[str, str]]]:
        key = "chart" if field in rules.ACCOUNT_FIELDS else field
        modules = _MODULES[key]
        loaded = [m for m in modules if self.reader.lists.get(m) is not None]
        for module in loaded or modules[:1]:
            values = rules.masters([self.reader.listbox(module) or {}]).get(key)
            if values is not None:
                self.observed[key] = values
                return values
        return None

    def value(self, field: str) -> Dict[str, str]:
        values = self._options(field)
        single = field not in rules.ACCOUNT_FIELDS
        default = ""
        if values and not self.chosen.get(field) and not (single and len(values) == 1):
            default = self._rule_default(field, values)
        hit, reason = rules.resolve(values, self.chosen.get(field), single=single, default=default)
        if reason:
            raise Missing(reason)
        return hit

    def _rule_default(self, field: str, values: List[Dict[str, str]]) -> str:
        """没选过又不止一个可用值：按规则取（网页默认 / 总店 / 现有档案最常用的那组 / 唯一存款科目）。"""
        key = "chart" if field in rules.ACCOUNT_FIELDS else field
        observed = {key: values, "head_branch": self.reader.known_head_branch()}
        suggested = None
        if field in _SAMPLE_MODULE:
            suggested = self.reader.suggested(_SAMPLE_MODULE[field])
            if suggested:
                self.observed.setdefault("suggested", {}).update(suggested)
        return rules.rule_default(observed, field, suggested)

    def chart(self) -> List[str]:
        return [v["code"] for v in self._options("chart") or []]

    # ── 表头 / 明细 / 收款 ────────────────────────────────────────────────

    def header(self, mappings: Dict[str, Any], histories: List[Dict[str, Any]], *, sales: bool,
               cash_sale: bool) -> None:
        """算表头与明细的值放进 mappings；算不出抛 Missing。销项读赊销接口（带客户档的员工 / 区域 / 运输）。"""
        if sales:
            self.reader.listbox("artran")
        head = {"branch": self.value("branch")["code"], "dept": self.value("dept")["code"],
                "job": self.value("job")["code"], "store": self.value("store")["code"],
                "employee": self.value("employee")["name"]}
        if sales:
            head.update(area=self.value("area")["name"], delivery=self.value("delivery")["name"])
            mappings["_mrerp_customer_header"] = rules.customer_defaults(self.reader.lists.get("artran"))
        mappings["_mrerp_header"] = head
        if cash_sale:
            from pearnly_cowork.push.mrerp.xlsx.sales_cash import _is_cash

            if any(_is_cash(h) for h in histories):
                mappings["_mrerp_receipt_account_cash"] = self._reference("cash")
            if not all(_is_cash(h) for h in histories):
                mappings["_mrerp_receipt_account_bank"] = self.value("bank_account")["code"]
        chart = self.chart()
        if chart:
            # 科目安全阀主动层：用到的科目码都要在这家公司的科目表里（push/mrerp/account_valve.py）
            mappings["_mrerp_accounts"] = chart

    def _reference(self, key: str) -> str:
        code = (self.reader.reference() or {}).get(rules.REFERENCE[key]) or ""
        if not code:
            raise Missing(rules.UNREADABLE)  # 这家公司在 MR.ERP 的参考科目没配这一项
        return code

    # ── 自建主档 ─────────────────────────────────────────────────────────

    def _branch_for(self, wanted: Any) -> str:
        codes = [v["code"] for v in self._options("customer_branch") or []]
        if not codes:
            raise Missing(rules.UNREADABLE)
        want = "".join(ch for ch in str(wanted or "") if ch.isdigit()).zfill(5)[-5:] if wanted else "00000"
        return want if want in codes else ("00000" if "00000" in codes else codes[0])

    def _prefix_for(self, name: Any) -> str:
        prefixes = self.reader.prefixes()
        if not prefixes:
            raise Missing(rules.UNREADABLE)
        return rules.prefix_for(name, prefixes)

    def customers(self, records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        """自建客户要的值：每个客户的称谓与分店，加上整批共用的结构码（`_mrerp_customer_*`）。"""
        shared = {"_mrerp_customer_type": self.value("customer_type")["code"],
                  "_mrerp_customer_account": self.value("ar_account")["code"],
                  "_mrerp_customer_salesman": self.value("employee")["code"],
                  "_mrerp_customer_area": self.value("area")["code"],
                  "_mrerp_customer_shipping": self.value("delivery")["code"]}
        return [{**r, "prefix": self._prefix_for(r.get("name")), "branch": self._branch_for(r.get("branch"))}
                for r in records], shared

    def suppliers(self, records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        self.reader.listbox("apmas")
        shared = {"_mrerp_supplier_type": self.value("supplier_type")["code"],
                  "_mrerp_supplier_account": self.value("ap_account")["code"]}
        return [{**r, "prefix": self._prefix_for(r.get("name")), "branch": self._branch_for(r.get("branch"))}
                for r in records], shared

    def products(self, *, expense: bool) -> Dict[str, str]:
        """自建商品 / 费用物料要的 `_mrerp_product_*`（费用物料另由 autocreate 换成费用类型与费用科目）。"""
        unit = self.value("unit")["code"]
        values = {"_mrerp_product_category": self.value("product_group")["code"],
                  **{f"_mrerp_product_unit_{k}": unit for k in ("count", "std", "buy", "sell")},
                  "_mrerp_product_supplier": self._product_supplier()}
        if expense:
            account = self.value("expense_account")["code"]
            values.update(_mrerp_expense_item_acc=account,
                          _mrerp_expense_item_category=values["_mrerp_product_category"],
                          **{f"_mrerp_product_{key}": account for key in _PRODUCT_REFERENCE})
        else:
            values.update({f"_mrerp_product_{key}": self._reference(key) for key in _PRODUCT_REFERENCE})
        return values

    def _product_supplier(self) -> str:
        # 商品主档的「รหัสผู้จำหน่าย」必填且要存在（impstkmas :761-787），MR.ERP 网页不预填：用现金供应商，没有就用第一个。
        codes = sorted(rules.known_codes(self.reader.listbox("apmas")))
        if not codes:
            raise Missing(rules.UNREADABLE)
        return CASH_PARTY if CASH_PARTY in codes else codes[0]
