# -*- coding: utf-8 -*-
"""DMS 表单 POST/解析 + 地址地理主档级联 + HTML 微解析。

这一层全是"跟 PHP 表单页对话"的脏活：把表单页里的 input/select/textarea 默认值抠出来，
因为**提交时必须带上完整表单** —— 只发几个字段会把其它字段冲空。

沿用老产品 `services/erp/mrerp_dms_client_forms.py` 的表单协议
（正则、字段名、级联接口路径都是真机勘察来的）。地址映射只认唯一命中，
不再继承旧实现的目录第一项兜底。
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import replace
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.models import DMSMasterRef


class DmsClientFormsMixin:
    # DMS 主档里的泰国行政区名可能带行政前缀，而身份证提示词要的是裸名。
    # 去掉前缀，主档查找才落得对行。
    _GEO_PREFIXES = ("จังหวัด", "อำเภอ", "ตำบล", "แขวง", "เขต", "จ.", "อ.", "ต.")

    def _post_text(self, path: str, data: Dict[str, Any]) -> str:
        resp = self.transport.post(self._url(path), data=data)
        if resp.status_code != 200:
            raise DmsClientError(f"{path} http={resp.status_code}", "ERR_DMS_TECHNICAL")
        return resp.text

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path)

    def _parse_form_defaults(self, page: str) -> Dict[str, str]:
        """把表单页的现有值抠成一份可提交的 dict（提交时以它做底再覆盖）。"""
        data: Dict[str, str] = {}
        for match in re.finditer(r"<input\b([^>]*)>", page, re.I | re.S):
            attrs = match.group(1)
            name = self._attr(attrs, "name")
            if not name:
                continue
            input_type = (self._attr(attrs, "type") or "text").lower()
            # 按钮和文件框不是数据；没勾的勾选框/单选框不能被当成"值"
            if input_type in {"button", "file", "submit"}:
                continue
            if input_type in {"checkbox", "radio"} and "checked" not in attrs.lower():
                continue
            data[name] = self._attr(attrs, "value") or ""
        for match in re.finditer(r"<select\b([^>]*)>(.*?)</select>", page, re.I | re.S):
            name = self._attr(match.group(1), "name")
            if name:
                data[name] = self._selected_value(match.group(2))
        for match in re.finditer(r"<textarea\b([^>]*)>(.*?)</textarea>", page, re.I | re.S):
            name = self._attr(match.group(1), "name")
            if name:
                data[name] = re.sub(r"<.*?>", "", match.group(2))
        return data

    def _norm_geo(self, name: str) -> str:
        # Only presentation equivalence, never municipality aliases/fuzzy matches.
        s = " ".join(unicodedata.normalize("NFKC", html.unescape(str(name or ""))).split())
        for prefix in self._GEO_PREFIXES:
            prefix = unicodedata.normalize("NFKC", prefix)
            if s.startswith(prefix):
                return s[len(prefix) :].strip()
        return s

    def _parse_options(self, options_html: str) -> List[List[str]]:
        """一段 `<option>` → `[[value, label], ...]`，丢掉空 value 的占位行。"""
        out: List[List[str]] = []
        for attrs, label in re.findall(r"<option([^>]*)>(.*?)</option>", options_html, re.S | re.I):
            value = self._attr(attrs, "value")
            if not value:
                continue
            out.append([value, html.unescape(re.sub(r"<.*?>", "", label).strip())])
        return out

    def _match_geo(self, options: List[List[str]], name: str) -> str:
        target = self._norm_geo(name)
        if not target:
            return ""
        matches = {str(value) for value, label in options
                   if value and self._norm_geo(label) == target}
        return next(iter(matches)) if len(matches) == 1 else ""

    def _resolve_address_geo(self, address, form_html: str):
        """地址文本 → DMS 主档 id，走客户表单自己的级联接口
        （listdistricts → listsubdistricts → listzipcodes）。

        只接受当前父级目录内的唯一精确匹配。缺名、缺目录或重复名字时，本级及
        子级留空让用户编辑；绝不继承新表单默认值或目录第一项。保存仍由实时
        父级链校验拦截缺项，不能为绕过第三方必填报错而编造地址。
        """
        address = replace(address, province_id="", district_id="", subdistrict_id="", zipcode_id="")
        prov = re.search(
            r'<select[^>]+name="selprovinces"[^>]*>(.*?)</select>', form_html, re.S | re.I
        )
        if not prov:
            return address
        provinces = self._parse_options(prov.group(1))
        province_id = self._match_geo(provinces, address.province_name)
        if not province_id:
            return address
        address = replace(address, province_id=province_id)

        districts = self._fetch_options(
            "cus/component/listdistricts.php", {"selprovinces": province_id}
        )
        district_id = self._match_geo(districts, address.district_name)
        if not district_id:
            return address
        address = replace(address, district_id=district_id)
        subdistricts = self._fetch_options(
            "cus/component/listsubdistricts.php", {"seldistricts": district_id}
        )
        subdistrict_id = self._match_geo(subdistricts, address.subdistrict_name)
        if not subdistrict_id:
            return address
        address = replace(address, subdistrict_id=subdistrict_id)
        zipcodes = self._fetch_options(
            "cus/component/listzipcodes.php", {"selsubdistricts": subdistrict_id}
        )
        return replace(address, zipcode_id=self._match_geo(zipcodes, address.zipcode))

    def _fetch_options(self, path: str, body: Dict[str, str]) -> List[List[str]]:
        return self._parse_options(self._post_text(path, body))

    def _apply_address_to_booking_form(self, data: Dict[str, str], address) -> None:
        data.update(
            {
                "txthousenum": address.house_no,
                "txtbuilding": address.building,
                "txtfloor": address.floor,
                "txtroom": address.room,
                "txtvillage": address.village,
                "txtmoo": address.moo,
                "txtsoi": address.soi,
                "txtroad": address.road,
                "provincesval": address.province_id,
                "txtprovinces": address.province_name,
                "districtsval": address.district_id,
                "txtdistricts": address.district_name,
                "subdistrictsval": address.subdistrict_id,
                "txtsubdistricts": address.subdistrict_name,
                "zipcodesval": address.zipcode_id,
                "txtzipcodes": address.zipcode,
            }
        )

    def _first_data_val(self, html_text: str) -> Optional[str]:
        match = re.search(r'data-val="([^"]+)"', html_text)
        return match.group(1) if match else None

    def _selected_value(self, options_html: str) -> str:
        """下拉当前值：有 selected 用它，否则用第一项（DMS 的行为）。"""
        selected = re.search(
            r'<option[^>]*value="([^"]*)"[^>]*selected[^>]*>', options_html, re.I | re.S
        )
        first = re.search(r'<option[^>]*value="([^"]*)"', options_html, re.I | re.S)
        node = selected or first
        return node.group(1) if node else ""

    def _attr(self, attrs: str, name: str) -> Optional[str]:
        match = re.search(rf'{re.escape(name)}="([^"]*)"', attrs, re.I)
        return html.unescape(match.group(1)) if match else None

    def _extra(self, ref: DMSMasterRef, idx: int) -> str:
        return str(ref.extra[idx]) if len(ref.extra) > idx else ""
