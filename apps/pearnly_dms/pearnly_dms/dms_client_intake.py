# -*- coding: utf-8 -*-
"""DMS 客户交互层：查重 / 建改（按用户编辑后的字段）/ 地址级联 / 称谓主档。

支撑那条两步流：**识别 → 可编辑面板 → 确认推送**。

字段契约（界面 ↔ DMS 表单名）：

    prefix_id→selprefix · name→txtcusname · people_id→txtpeopleid · tax_id→txttaxid
    birthday_be→txtbirthday · phone→txttel
    地址 house_no/moo/soi/road/province_id/district_id/subdistrict_id/zipcode_id →
    txthousenum/txtmoo/txtsoi/txtroad/selprovinces/seldistricts/selsubdistricts/selzipcodes
    （三段地址各带后缀 ""/_ct/_sd，同值）

照搬老产品 `services/erp/mrerp_dms_client_intake.py`。三个真机踩过的坑写在下面代码里，
**都不要"顺手改好"**：

1. 保存走 `cus/new.php`（建）/ `cus/edit.php`（改），要带 `stsel` + `idsel` 上下文；
2. **必填 select（称谓/地址四级）不许猜**：新建时先清掉新表单自带的默认地址，
   再把每个值拿去和**实时**父级链核对 —— 显式给值且对得上才放行，否则拦下让人去选。
   （老实现是"空了就拿第一项补"，结果客户被填上凭空捏造的称谓；线上 `b78697f3` 禁掉了。
   见 `dms_customer_geo.py` 顶部。）
3. **PHP 返回 200 / 没有 err:: 不代表写库成功** → 建完要用搜索复核，改完要重读姓名复核。
"""

from __future__ import annotations

import html
import logging
import re
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from pearnly_dms.dms_client_base import DmsClientError
from pearnly_dms.dms_customer_geo import clear_customer_select_defaults, validate_customer_selects

logger = logging.getLogger(__name__)

# 界面友好键 → DMS 客户表单字段名（非地址部分）
_IDENTITY_MAP = {
    "prefix_id": "selprefix",
    "name": "txtcusname",
    "people_id": "txtpeopleid",
    "tax_id": "txttaxid",
    "birthday_be": "txtbirthday",
    "phone": "txttel",
    "tel_work": "txttelwork",
    "tel_home": "txttelhome",
    "email": "txtemail",
    "line_id": "txtlineid",
    "facebook": "txtfacebook",
    "credit_day": "txtcreditday",
}
# 分店（สาขา）= 隐藏 branchcusval（主档 id）+ txtbranchcus（显示码）配对的 typeahead。
# 身份证录入**不改分店**：只读显示当前码，保存时由载入的表单默认值原样保留
# （所以它不在可写映射里）。
_BRANCH_VAL = "branchcusval"
_BRANCH_CODE = "txtbranchcus"
# 地址友好键 → DMS 字段前缀（三段地址各加 ""/_ct/_sd 后缀）
_ADDR_MAP = {
    "house_no": "txthousenum",
    "building": "txtbuilding",
    "floor": "txtfloor",
    "room": "txtroom",
    "village": "txtvillage",
    "moo": "txtmoo",
    "soi": "txtsoi",
    "road": "txtroad",
    "province_id": "selprovinces",
    "district_id": "seldistricts",
    "subdistrict_id": "selsubdistricts",
    "zipcode_id": "selzipcodes",
}
_ADDR_SUFFIXES = ("", "_ct", "_sd")
# 地址下拉「选中标签」键 → DMS select 名（各加 ""/_ct/_sd 后缀）· 仅供显示现值
_GEO_LABEL_MAP = {
    "province_name": "selprovinces",
    "district_name": "seldistricts",
    "subdistrict_name": "selsubdistricts",
    "zipcode_name": "selzipcodes",
}


class DmsClientIntakeMixin:
    # ── 读 ──────────────────────────────────────────────────────────
    def lookup_customer(self, people_id: str) -> Dict[str, Any]:
        """按身份证号查 DMS 客户。命中就回当前全字段（给面板的"历史资料"）。"""
        cid = self.search_customer(people_id) if (people_id or "").strip() else None
        if not cid:
            return {"found": False, "customer_id": None, "fields": {}}
        return {"found": True, "customer_id": cid, "fields": self.read_customer(cid)}

    def read_customer(self, customer_id: str) -> Dict[str, str]:
        """按客户号**直读**全字段。直读不经搜索，所以不受列表缓存的
        "新客隐身期"影响（2026-07-19 三案实锤：新建客户对新会话隐身数分钟）
        —— 台账兜底核对靠的就是它。"""
        page = self._post_text("cus/form.php", {"status": "e", "id": str(customer_id)})
        return self._extract_customer_fields(self._parse_form_defaults(page), page)

    def search_customers_detailed(self, text: str, limit: int = 10) -> List[Dict[str, str]]:
        """按 码/姓名/证号 搜客户，解析多行候选（给相似匹配用）。
        返回 `[{customer_id, cuscode, name, people_id}, ...]`，按命中顺序。"""
        term = (text or "").strip()
        if not term:
            return []
        body = self._post_text(
            "cus/component/showdata.php",
            {
                "sdtamt": str(limit),
                "sdtpage": "1",
                "sd": term,
                "selcolsort": "1",
                "selcolsorttype": "1",
            },
        )
        return self._parse_customer_rows(body)

    def _parse_customer_rows(self, body: str) -> List[Dict[str, str]]:
        """解析 `cus/component/showdata.php` 的结果行。空结果体以 `ndt::` 开头。
        每行：`<div data-val=客户号 onclick=ctllistdata>` → detaildata 两列
        （列1：客户码 + 姓名；列2：身份证号）· `colnodt`/`-` 占位视为空。"""
        rows: List[Dict[str, str]] = []
        for block in re.split(r'(?=<div\s+data-val=")', body):
            m = re.match(r'<div\s+data-val="([^"]+)"[^>]*onclick="ctllistdata', block)
            if not m:
                continue
            cid = m.group(1)
            dd = re.search(
                r'<div class="detaildata">(.*?)</div>\s*<div class="statuscf"', block, re.S
            )
            inner = dd.group(1) if dd else block
            cols = re.findall(r"<div>(.*?)</div>", inner, re.S)
            col0 = self._row_texts(cols[0]) if cols else []
            col1 = self._row_texts(cols[1]) if len(cols) > 1 else []
            rows.append(
                {
                    "customer_id": cid,
                    "cuscode": col0[0] if len(col0) > 0 else "",
                    "name": col0[1] if len(col0) > 1 else (col0[0] if col0 else ""),
                    "people_id": col1[0] if col1 else "",
                }
            )
        return rows

    @staticmethod
    def _row_texts(col_html: str) -> List[str]:
        """取一列里各 `<p>` 的纯文本；占位 `-` 归一为空串。"""
        out: List[str] = []
        for pm in re.finditer(r"<p[^>]*>(.*?)</p>", col_html, re.S):
            t = html.unescape(re.sub(r"<.*?>", "", pm.group(1))).strip()
            out.append("" if t == "-" else t)
        return out

    def list_prefixes(self) -> List[List[str]]:
        """称谓主档**双来源合并**：客户新表（cus）+ 订车快速建客表（drfcbc）。
        按首列 DMS ID 去重、保留首次出现顺序（客户表在前）；单个来源取数失败
        不拖累另一个。"""
        merged: List[List[str]] = []
        seen: Set[str] = set()
        for path, select_name in (
            ("cus/form.php", "selprefix"),
            ("drfcbc/form.php", "selprefix_fs"),
        ):
            for row in self._prefix_options(path, select_name):
                if row[0] not in seen:
                    seen.add(row[0])
                    merged.append(row)
        return merged

    def _prefix_options(self, path: str, select_name: str) -> List[List[str]]:
        """拉单个称谓来源的选项；HTTP/解析失败视为该来源无选项（降级，不中断合并）。"""
        try:
            page = self._post_text(path, {"status": "n"})
            return self._select_options(page, select_name)
        except Exception:
            logger.debug("[dms] %s 的称谓选项取不到", path, exc_info=True)
            return []

    def list_geo(self, level: str, parent_id: str = "") -> List[List[str]]:
        """四级联动选项：provinces 无 parent；其余按上一级 id 取。
        返回 `[[id, label], ...]`。"""
        if level == "provinces":
            page = self._post_text("cus/form.php", {"status": "n"})
            return self._select_options(page, "selprovinces")
        endpoints = {
            "districts": ("cus/component/listdistricts.php", "selprovinces"),
            "subdistricts": ("cus/component/listsubdistricts.php", "seldistricts"),
            "zipcodes": ("cus/component/listzipcodes.php", "selsubdistricts"),
        }
        if level not in endpoints or not parent_id:
            return []
        path, key = endpoints[level]
        return self._fetch_options(path, {key: parent_id})

    # ── 写 ──────────────────────────────────────────────────────────
    def save_customer(
        self,
        *,
        fields: Dict[str, Any],
        mode: str,
        customer_id: Optional[str] = None,
        addresses: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[str, bool]:
        """建（`mode='create'`）或覆盖（`mode='overwrite'`）客户。
        返回 `(customer_id, converted)` —— `converted=True` 表示 create 因幂等/撞码
        被转成了覆盖，回执里要**如实说"อัปเดต"**。

        覆盖：载入现表单 → 合并面板字段（没动过的字段原样保留）→ 提交。

        字段按"**键在就写**"：传了空串 = 真的清空 DMS；没传这个键才保留原值。
        `addresses` 给 `{""/_ct/_sd: 地址块}` 时三段分别写；不给就用 `fields` 里的
        单套地址写满三处（向后兼容）。
        """
        if mode not in ("create", "overwrite"):
            raise DmsClientError(f"bad save mode: {mode!r}", "ERR_DMS_CUSTOMER_SAVE")
        with self._writer_session():
            return self._save_customer_locked(
                fields=fields, mode=mode, customer_id=customer_id, addresses=addresses
            )

    @contextmanager
    def _writer_session(self) -> Iterator[None]:
        """客户档**写**操作（建/改）走 admin 凭据组会话（配了 admin 时）；读操作不受影响。

        没配 admin（`_resolve_admin_transport()` → None）就原样用当前会话 ——
        单凭据路径行为**逐字节不变**。整段写流程（含载表单、复核读）都在同一会话内，
        保证一致。
        """
        admin = self._resolve_admin_transport()
        if admin is None:
            yield
            return
        prev = self.transport
        self.transport = admin
        try:
            yield
        finally:
            self.transport = prev

    def _save_customer_locked(
        self,
        *,
        fields: Dict[str, Any],
        mode: str,
        customer_id: Optional[str] = None,
        addresses: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[str, bool]:
        # 幂等：一个身份证 = DMS 一个客户（编号/身份证号唯一）。create 前先按身份证号查，
        # 已存在就转成更新它 —— 修「撞客户编号重复」，并自愈「客户建了但订车失败」后的重推。
        converted = False
        if mode == "create":
            existing = self.search_customer((fields.get("people_id") or "").strip())
            if existing:
                mode, customer_id, converted = "overwrite", existing, True
        if mode == "overwrite" and not customer_id:
            raise DmsClientError("overwrite needs customer_id", "ERR_DMS_CUSTOMER_SAVE")

        form_args = {"status": "n"} if mode == "create" else {"status": "e", "id": customer_id}
        form_html = self._post_text("cus/form.php", form_args)
        data = self._parse_form_defaults(form_html)
        data["stsel"] = "n" if mode == "create" else "e"
        if mode == "overwrite":
            data["idsel"] = customer_id
        if mode == "create" and not (fields.get("people_id") and data.get("txtcuscode")):
            data["txtcuscode"] = fields.get("people_id") or data.get("txtcuscode") or ""

        if mode == "create":
            # 新建表单自带一组省/县/区/邮编预选值：不清掉，客户就会带着一个
            # 没人选过的地址被存进 DMS（见 dms_customer_geo 顶部第 1 条）。
            clear_customer_select_defaults(data)
        self._apply_customer_fields(data, fields, addresses)
        self._guard_required_selects(data, form_html)

        endpoint = "cus/new.php" if mode == "create" else "cus/edit.php"
        resp = self.transport.post(self._url(endpoint), data=data, timeout_ms=60000)
        body = (resp.text or "").strip()
        if mode == "create" and body.startswith("err::") and "ซ้ำ" in body:
            # 「ซ้ำ」（重复）= 记录其实已经在，只是查重搜索漏检了
            # （DMS 搜索偶发空返，2026-07-19 实锤：建档后 3 分钟同号搜不到 → 重复建档被拒）。
            # 复搜转覆盖，**不把"已存在"当失败抛给用户**；仍搜不到才如实报错。
            existing = self._research_customer((fields.get("people_id") or "").strip())
            if existing:
                cid, _ = self._save_customer_locked(
                    fields=fields, mode="overwrite", customer_id=existing, addresses=addresses
                )
                return cid, True
        if resp.status_code != 200 or body.startswith("err::"):
            raise DmsClientError(
                f"customer {mode} failed: {resp.status_code} {body[:200]!r}",
                "ERR_DMS_CUSTOMER_SAVE",
            )
        return self._verify_saved(mode, fields, customer_id), converted

    def _research_customer(self, people_id: str) -> Optional[str]:
        """撞唯一键后的复搜：搜索端点偶发空返，隔 1.5s 共试 3 次再放弃。"""
        if not people_id:
            return None
        for attempt in range(3):
            if attempt:
                time.sleep(1.5)
            cid = self.search_customer(people_id)
            if cid:
                return cid
        return None

    # ── 内部 ────────────────────────────────────────────────────────
    def _extract_customer_fields(self, data: Dict[str, str], form_html: str = "") -> Dict[str, str]:
        out = {fk: data.get(dms, "") for fk, dms in _IDENTITY_MAP.items()}
        if form_html:
            out["prefix_name"] = self._geo_label(form_html, "selprefix", out["prefix_id"])
        # 三套地址各自抽出（户籍 ""·联系 _ct·寄送 _sd），供全字段表单分区显示。
        for sfx in _ADDR_SUFFIXES:
            for fk, dms in _ADDR_MAP.items():
                out[fk + sfx] = data.get(dms + sfx, "")
            # 地址下拉的选中标签（显示现值用，光有 id 不够）：府/县/区/邮编
            if form_html:
                for fk, dms in _GEO_LABEL_MAP.items():
                    out[fk + sfx] = self._geo_label(form_html, dms + sfx, data.get(dms + sfx, ""))
        out["cuscode"] = data.get("txtcuscode", "")
        out["branch_id"] = data.get(_BRANCH_VAL, "")
        out["branch_code"] = data.get(_BRANCH_CODE, "")
        return out

    def _geo_label(self, form_html: str, select_name: str, value: str) -> str:
        if not value:
            return ""
        for v, label in self._select_options(form_html, select_name):
            if v == value:
                return label
        return ""

    def _apply_customer_fields(
        self,
        data: Dict[str, str],
        f: Dict[str, Any],
        addresses: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        # **所见即所存**：字段【存在】（键在）就写，空串 = 真的清空 DMS；
        # 字段【缺省】（键不在）才保留载入的 DMS 原值。界面表单的初值来自 DMS，
        # 没动过的字段原样回写（=保留），手动清空的字段送空串（=清除）。
        for fk, dms in _IDENTITY_MAP.items():
            if fk in f:
                data[dms] = str(f.get(fk) or "")
        # 个人主体：税号留空时取身份证号（DMS 里个人客户的税号就是身份证号）
        if not f.get("tax_id") and f.get("people_id"):
            data["txttaxid"] = str(f["people_id"])
        # 三套地址：给了 addresses 就各套分别写；否则单套写满三处（向后兼容）。
        blocks = addresses if addresses else {sfx: f for sfx in _ADDR_SUFFIXES}
        for sfx, blk in blocks.items():
            if sfx not in _ADDR_SUFFIXES:
                continue
            blk = blk or {}
            for fk, dms in _ADDR_MAP.items():
                if fk in blk:
                    data[dms + sfx] = str(blk.get(fk) or "")

    def _guard_required_selects(self, data: Dict[str, str], form_html: str) -> None:
        """称谓与地址四级必须与**实时**级联对账；空值/被删值不再拿第一项补齐。"""
        validate_customer_selects(self, data, form_html)

    def _verify_saved(self, mode: str, fields: Dict[str, Any], customer_id: Optional[str]) -> str:
        """**PHP 成功码 ≠ 真写库**（这条在老产品是铁律）：
        建 → 用搜索复核；改 → 重读姓名复核。"""
        if mode == "create":
            cid = self.search_customer(fields.get("people_id") or fields.get("name") or "")
            if not cid:
                raise DmsClientError(
                    "customer create returned success but search could not find row",
                    "ERR_DMS_CUSTOMER_SAVE",
                )
            return cid
        page = self._post_text("cus/form.php", {"status": "e", "id": customer_id})
        saved = self._parse_form_defaults(page)
        want = str(fields.get("name") or "").strip()
        if want and saved.get("txtcusname", "").strip() != want:
            raise DmsClientError(
                "customer overwrite returned success but re-read name mismatch",
                "ERR_DMS_CUSTOMER_SAVE",
            )
        return customer_id

    def _select_options(self, page: str, name: str) -> List[List[str]]:
        m = re.search(
            r'<select[^>]+name="%s"[^>]*>(.*?)</select>' % re.escape(name), page, re.S | re.I
        )
        return self._parse_options(m.group(1)) if m else []
