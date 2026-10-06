# -*- coding: utf-8 -*-
"""DMS 客户交互层：查重 / 建改（按用户编辑后的字段）/ 地址级联 / 称谓主档。

支撑那条两步流：**识别 → 可编辑面板 → 确认推送**。

字段契约（界面 ↔ DMS 表单名）：

    prefix_id→selprefix · name→txtcusname · people_id→txtpeopleid · tax_id→txttaxid
    birthday_be→txtbirthday · phone→txttel
    地址 house_no/moo/soi/road/province_id/district_id/subdistrict_id/zipcode_id →
    txthousenum/txtmoo/txtsoi/txtroad/selprovinces/seldistricts/selsubdistricts/selzipcodes
    （三段地址各带后缀 ""/_ct/_sd：身份证地址照写，联系/寄送只在 DMS 里整段为空时补，
    见 `customer_merge.secondary_addresses`）

照搬老产品 `services/erp/mrerp_dms_client_intake.py`。三个真机踩过的坑写在下面代码里，
**都不要"顺手改好"**：

1. 保存走 `cus/new.php`（建）/ `cus/edit.php`（改），要带 `stsel` + `idsel` 上下文；
2. **必填 select（称谓/地址四级）不许猜**：新建时先清掉新表单自带的默认地址，
   再把每个值拿去和**实时**父级链核对 —— 显式给值且对得上才放行，否则拦下让人去选。
   （老实现是"空了就拿第一项补"，结果客户被填上凭空捏造的称谓；线上 `b78697f3` 禁掉了。
   见 `dms_customer_geo.py` 顶部。）
3. **PHP 返回 200 / 没有 err:: 不代表写库成功** → 自动匹配必须直读完整证号，写后重读提交的身份。
"""

from __future__ import annotations

import html
import logging
import re
import time
from contextlib import contextmanager
from html.parser import HTMLParser
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.id_validate import normalize_thai_id
from pearnly_dms.contracts import customer_merge
from pearnly_dms.intake.customer_geo import clear_customer_select_defaults, validate_customer_selects
from pearnly_dms.masters.master_rows import parse_rows as parse_bshsd_rows

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
# 身份证录入**不改已有分店**：保存时由载入的表单默认值原样保留（所以它不在可写映射里）；
# 只有空着时补 00000，见 `_ensure_customer_branch`。
_BRANCH_VAL = "branchcusval"
_BRANCH_CODE = "txtbranchcus"
_HEAD_OFFICE = "00000"
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


class _IdentitySearchRows(HTMLParser):
    """Only native customer row IDs; labels/codes are never identity evidence."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.depth = 0
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            self.depth += 1
        attributes = dict(attrs)
        candidate = "data-val" in attributes
        handler = str(attributes.get("onclick") or "").strip().startswith("ctllistdata")
        if candidate or handler:
            if (tag != "div" or not candidate or not handler or not attributes.get("data-val")
                    or len(attrs) != len(attributes)):
                self.invalid = True
            else:
                self.ids.append(attributes["data-val"])

    def handle_endtag(self, tag):
        if tag == "div":
            self.depth -= 1
            if self.depth < 0:
                self.invalid = True


class DmsClientIntakeMixin:
    # ── 读 ──────────────────────────────────────────────────────────
    def lookup_customer(self, people_id: str, *,
                        fallback_customer_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Only one native full-ID match is existing; search hits/ledger IDs are hints.

        A full ten-row page is possibly truncated, never proof of uniqueness or absence.
        At most nine search rows plus five recent ledger IDs are read, with duplicates
        removed. Unknown responses fail closed, not as 'new customer'.
        """
        target = normalize_thai_id(people_id)
        if not re.fullmatch(r"[0-9]{13}", target):
            raise DmsClientError("customer lookup requires a complete identity", "ERR_DMS_CUSTOMER_SAVE")
        fallback = list(dict.fromkeys(str(cid) for cid in (fallback_customer_ids or []) if cid))
        if len(fallback) > 5:
            raise DmsClientError("customer identity fallback limit exceeded", "ERR_DMS_CUSTOMER_SAVE")
        body = self._post_text("cus/component/showdata.php", {
            "sdtamt": "10", "sdtpage": "1", "sd": target,
            "selcolsort": "1", "selcolsorttype": "1",
        })
        parser = _IdentitySearchRows()
        parser.feed(body)
        parser.close()
        empty = body.lstrip().startswith("ndt::")
        # Counting raw attributes also detects an unparsed/malformed trailing row.
        if (parser.invalid or parser.depth or len(parser.ids) != len(re.findall(r"\bdata-val\s*=", body, re.I))
                or body.lstrip().startswith("err::") or (empty and parser.ids)
                or (not empty and not parser.ids)):
            raise DmsClientError("customer identity search response is incomplete", "ERR_DMS_CUSTOMER_SAVE")
        if len(parser.ids) >= 10:
            raise DmsClientError("customer identity search may be truncated", "ERR_DMS_CUSTOMER_SAVE")
        found = {"found": False, "customer_id": None, "fields": {}}
        for cid in dict.fromkeys(parser.ids + fallback):
            page = self._post_text("cus/form.php", {"status": "e", "id": cid})
            data = self._identity_form_data(page)
            if normalize_thai_id(data["txtpeopleid"]) != target:
                continue
            if found["found"]:
                raise DmsClientError("customer identity has multiple exact matches", "ERR_DMS_CUSTOMER_SAVE")
            found = {"found": True, "customer_id": cid,
                     "fields": self._extract_customer_fields(data, page)}
        return found

    def _identity_form_data(self, page: str) -> Dict[str, str]:
        data = self._parse_form_defaults(page)
        identity_keys = ("txtcuscode", "txtcusname", "txtpeopleid", "txttaxid")
        names = [self._attr(attrs, "name") for attrs in re.findall(r"<input\b([^>]*)>", page, re.I | re.S)]
        if (page.lstrip().startswith("err::")
                or not all(key in data and names.count(key) == 1 for key in identity_keys)):
            raise DmsClientError("customer identity form is incomplete", "ERR_DMS_CUSTOMER_SAVE")
        return data

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
        baseline: Optional[Dict[str, Any]] = None,
        on_attempt=None,
    ) -> Tuple[str, bool]:
        """建（`mode='create'`）或覆盖（`mode='overwrite'`）客户。
        返回 `(customer_id, False)`。实时发现已存在不自动覆盖，必须重新确认。

        覆盖：载入现表单 → 合并面板字段（没动过的字段原样保留）→ 提交。

        字段按"**键在就写**"：传了空串 = 真的清空 DMS；没传这个键才保留原值。
        `addresses` 给 `{""/_ct/_sd: 地址块}` 时三段分别写；不给就用 `fields` 里的
        单套地址写满三处（向后兼容）。覆盖时再按 DMS 的规则收口：联系/寄送两段在 DMS
        里已有内容就保留，只有整段为空才用身份证地址补（`customer_merge.secondary_addresses`）。
        """
        if mode not in ("create", "overwrite"):
            raise DmsClientError(f"bad save mode: {mode!r}", "ERR_DMS_CUSTOMER_SAVE")
        with self._writer_session():
            return self._save_customer_locked(
                fields=fields, mode=mode, customer_id=customer_id, addresses=addresses,
                baseline=baseline, on_attempt=on_attempt,
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
        baseline: Optional[Dict[str, Any]] = None,
        on_attempt=None,
    ) -> Tuple[str, bool]:
        desired = customer_merge.flatten(fields, addresses)
        if (mode == "overwrite" and baseline is not None and "tax_id" not in fields
                and normalize_thai_id(fields.get("people_id") or "")
                == normalize_thai_id(baseline.get("people_id") or "")):
            desired.pop("tax_id", None)  # unchanged identity does not authorize a tax edit
        if mode == "create":
            existing = self.lookup_customer(fields.get("people_id") or "")
            if existing["found"]:
                raise customer_merge.CustomerConflict("ERR_DMS_CUSTOMER_EXISTS",
                    existing["customer_id"], existing["fields"], desired)
            fields, addresses = customer_merge.split(desired)
        if mode == "overwrite" and not customer_id:
            raise DmsClientError("overwrite needs customer_id", "ERR_DMS_CUSTOMER_SAVE")

        form_args = {"status": "n"} if mode == "create" else {"status": "e", "id": customer_id}
        form_html = self._post_text("cus/form.php", form_args)
        data = self._parse_form_defaults(form_html)
        if mode == "overwrite":
            current = self._extract_customer_fields(self._identity_form_data(form_html), form_html)
            present = tuple(suffix for suffix in customer_merge.SUFFIXES[1:]
                            if re.search(rf'<select\b[^>]*name="selprovinces{suffix}"', form_html, re.I))
            desired = customer_merge.secondary_addresses(desired, current, present)
            desired, conflicts = customer_merge.merge(baseline, desired, current)
            if conflicts:
                raise customer_merge.CustomerConflict("ERR_DMS_CUSTOMER_CHANGED",
                    customer_id, current, desired, conflicts)
            fields, addresses = customer_merge.split(desired)
            if not desired:
                return str(customer_id), False
        data["stsel"] = "n" if mode == "create" else "e"
        if mode == "overwrite":
            data["idsel"] = customer_id
        if mode == "create" and not (fields.get("people_id") and data.get("txtcuscode")):
            data["txtcuscode"] = fields.get("people_id") or data.get("txtcuscode") or ""

        if mode == "create":
            # 新建表单自带一组省/县/区/邮编预选值：不清掉，客户就会带着一个
            # 没人选过的地址被存进 DMS（见 dms_customer_geo 顶部第 1 条）。
            clear_customer_select_defaults(data)
        self._apply_customer_fields(data, fields, addresses, derive_tax=False)
        self._guard_required_selects(data, form_html)
        self._ensure_customer_branch(data)

        endpoint = "cus/new.php" if mode == "create" else "cus/edit.php"
        if on_attempt:
            on_attempt()
        try:
            resp = self.transport.post(self._url(endpoint), data=data, timeout_ms=60000)
        except Exception as exc:
            raise customer_merge.unknown() from exc
        body = (resp.text or "").strip()
        if mode == "create" and body.startswith("err::") and "ซ้ำ" in body:
            # A duplicate rejection is not proof that THIS request created it.
            existing = self._research_customer((fields.get("people_id") or "").strip())
            if existing:
                current = self.read_customer(existing)
                raise customer_merge.CustomerConflict("ERR_DMS_CUSTOMER_EXISTS",
                    existing, current, desired)
        if resp.status_code != 200:
            raise customer_merge.unknown()
        if body.startswith("err::"):
            exc = DmsClientError(
                f"customer {mode} failed: {resp.status_code} {body[:200]!r}",
                "ERR_DMS_CUSTOMER_SAVE",
            )
            exc.response_body = {"submitted": False, "rejected": True}
            raise exc
        try:
            return self._verify_saved(mode, fields, customer_id, addresses), False
        except Exception as exc:
            raise customer_merge.unknown() from exc

    def _research_customer(self, people_id: str) -> Optional[str]:
        """撞唯一键后的复搜：搜索端点偶发空返，隔 1.5s 共试 3 次再放弃。"""
        if not people_id:
            return None
        for attempt in range(3):
            if attempt:
                time.sleep(1.5)
            cid = self.lookup_customer(people_id)["customer_id"]
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
        *, derive_tax: bool = True,
    ) -> None:
        # **所见即所存**：字段【存在】（键在）就写，空串 = 真的清空 DMS；
        # 字段【缺省】（键不在）才保留载入的 DMS 原值。界面表单的初值来自 DMS，
        # 没动过的字段原样回写（=保留），手动清空的字段送空串（=清除）。
        for fk, dms in _IDENTITY_MAP.items():
            if fk in f:
                data[dms] = str(f.get(fk) or "")
        # 个人主体：税号留空时取身份证号（DMS 里个人客户的税号就是身份证号）
        if derive_tax and not f.get("tax_id") and f.get("people_id"):
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

    def _ensure_customer_branch(self, data: Dict[str, str]) -> None:
        """客户的「สาขา」为空时补成 00000（总公司）。

        DMS 网页保存客户时这一栏必填；`cus/edit.php` 收到空值会被数据库外键拒绝，只回一句
        `err::ข้อมูลดังกล่าวถูกใช้งานในระบบ`（2026-10-06 在测试 DMS 上复现：空分店原样提交被拒，
        只补分店即成功）。真实车行约 95% 的老客户这一栏是空的。个人客户开票就是 00000；
        已有分店（企业客户的分支号）原样保留；表单里没有这一栏就不碰。00000 在各家 DMS 的
        内部 id 不同（ris 是 1、测试账套是 16），每次实时查。
        """
        if _BRANCH_VAL not in data or str(data[_BRANCH_VAL] or "").strip():
            return
        rows = parse_bshsd_rows(_BRANCH_CODE, self._post_text("cus/component/bshsd.php", {
            "bshsdamt": "200", "bshsdcurrpage": "1", "elemname": _BRANCH_CODE, "sdt": _HEAD_OFFICE,
        }))
        if rows is None:
            raise DmsClientError("DMS customer branch list unavailable", "ERR_DMS_MASTER_UNAVAILABLE")
        matches = [row for row in rows if len(row) > 1 and str(row[1]).strip() == _HEAD_OFFICE]
        if len(matches) != 1:
            raise DmsClientError(f"DMS has {len(matches)} customer branches coded {_HEAD_OFFICE}",
                                 "ERR_DMS_CUSTOMER_BRANCH")
        data[_BRANCH_VAL], data[_BRANCH_CODE] = str(matches[0][0]), _HEAD_OFFICE

    def _guard_required_selects(self, data: Dict[str, str], form_html: str) -> None:
        """称谓与地址四级必须与**实时**级联对账；空值/被删值不再拿第一项补齐。"""
        validate_customer_selects(self, data, form_html)

    def _verify_saved(self, mode: str, fields: Dict[str, Any], customer_id: Optional[str],
                      addresses=None) -> str:
        """**PHP 成功码 ≠ 真写库**（这条在老产品是铁律）：
        建 → 唯一完整证号复核；改 → 直读提交的身份字段（含隐式税号）。"""
        if mode == "create":
            look = self.lookup_customer(fields.get("people_id") or "")
            if not look["found"]:
                raise DmsClientError(
                    "customer create returned success but exact identity was not found",
                    "ERR_DMS_CUSTOMER_SAVE",
                )
            customer_id, saved = look["customer_id"], look["fields"]
        else:
            page = self._post_text("cus/form.php", {"status": "e", "id": customer_id})
            saved = self._extract_customer_fields(self._identity_form_data(page))
        expected = customer_merge.flatten(fields, addresses, derive_tax=False)
        for key, value in expected.items():
            normalize = normalize_thai_id if key in ("people_id", "tax_id") else str.strip
            if normalize(str(saved.get(key) or "")) != normalize(value):
                raise DmsClientError("customer write identity readback mismatch", "ERR_DMS_CUSTOMER_SAVE")
        return customer_id

    def _select_options(self, page: str, name: str) -> List[List[str]]:
        m = re.search(
            r'<select[^>]+name="%s"[^>]*>(.*?)</select>' % re.escape(name), page, re.S | re.I
        )
        return self._parse_options(m.group(1)) if m else []
