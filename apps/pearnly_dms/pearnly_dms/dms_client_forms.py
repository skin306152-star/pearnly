# -*- coding: utf-8 -*-
"""DMS 表单 POST/解析 + 地址地理主档级联 + HTML 微解析。

这一层全是"跟 PHP 表单页对话"的脏活：把表单页里的 input/select/textarea 默认值抠出来，
因为**提交时必须带上完整表单** —— 只发几个字段会把其它字段冲空。

照搬老产品 `services/erp/mrerp_dms_client_forms.py`，方法体一字未改
（正则、字段名、级联接口路径都是真机勘察来的）。
"""

from __future__ import annotations

import html
import re
from dataclasses import replace
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from pearnly_dms.dms_client_base import DmsClientError
from pearnly_dms.dms_models import DMSMasterRef


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
        s = html.unescape(str(name or "")).strip()
        for prefix in self._GEO_PREFIXES:
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
        for value, label in options:
            if self._norm_geo(label) == target:
                return value
        return ""

    def _resolve_address_geo(self, address, form_html: str):
        """地址文本 → DMS 主档 id，走客户表单自己的级联接口
        （listdistricts → listsubdistricts → listzipcodes）。

        ⚠️ **每一级都必须给出有效值**：`cus/new.php` 对空的 geo 下拉会报一个
        **误导性的"已被使用"错误**。所以 OCR 出来的名字匹配不上时，逐级回落到
        列表第一项（以及表单预选的府）—— 客户总能建成，而地址文本字段里仍然是
        真实的登记地址。
        """
        prov = re.search(
            r'<select[^>]+name="selprovinces"[^>]*>(.*?)</select>', form_html, re.S | re.I
        )
        if not prov:
            return address
        provinces = self._parse_options(prov.group(1))
        default_province = self._selected_value(prov.group(1)) or (
            provinces[0][0] if provinces else ""
        )
        province_id = self._match_geo(provinces, address.province_name) or default_province
        if not province_id:
            return address

        districts = self._fetch_options(
            "cus/component/listdistricts.php", {"selprovinces": province_id}
        )
        district_id = self._match_geo(districts, address.district_name) or (
            districts[0][0] if districts else ""
        )
        subdistricts = self._fetch_options(
            "cus/component/listsubdistricts.php", {"seldistricts": district_id}
        )
        subdistrict_id = self._match_geo(subdistricts, address.subdistrict_name) or (
            subdistricts[0][0] if subdistricts else ""
        )
        zipcodes = self._fetch_options(
            "cus/component/listzipcodes.php", {"selsubdistricts": subdistrict_id}
        )
        zipcode_id = self._match_geo(zipcodes, address.zipcode) or (
            zipcodes[0][0] if zipcodes else ""
        )
        return replace(
            address,
            province_id=province_id,
            district_id=district_id,
            subdistrict_id=subdistrict_id,
            zipcode_id=zipcode_id,
        )

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
