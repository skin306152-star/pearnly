# -*- coding: utf-8 -*-
"""泰国税务局（RD）接口的**纯规则**：请求形状、响应解析、字段归一。

不 import 框架、不联网、不写库 —— 所以可以拿真实抓下来的响应离线断言
（`tests/test_erp_tax_lookup.py`）。多产品共用（2026-10-10 从 ERP `masters/` 移来，CC 的税号查询也用）：
查询流程见同目录 `service.py`，ERP 的缓存表落点见 `pearnly_erp_portal/masters/rd.py`。

为什么解析按**别名**取值：旧项目的解析表只认 `vProvinceName` / `vThumbolName` 这类
带 Name 的标签，而线上实际返回的是 `vProvince` / `vThambol` / `vAmphur`（抓下来的
真实响应就在测试夹具里）。只认一种形状的话，地址会缺省和缺区。
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

TIN_ENDPOINT = "https://rdws.rd.go.th/serviceRD3/checktinpinservice.asmx"
VAT_ENDPOINT = "https://rdws.rd.go.th/serviceRD3/vatserviceRD3.asmx"

TIN_SOAP_TPL = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:chec="https://rdws.rd.go.th/serviceRD3/checktinpinservice">
  <soap:Header/>
  <soap:Body>
    <chec:ServiceTIN>
      <chec:username>anonymous</chec:username>
      <chec:password>anonymous</chec:password>
      <chec:TIN>{tax_id}</chec:TIN>
    </chec:ServiceTIN>
  </soap:Body>
</soap:Envelope>"""

VAT_SOAP_TPL = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:vat="https://rdws.rd.go.th/serviceRD3/vatserviceRD3">
  <soap:Header/>
  <soap:Body>
    <vat:Service>
      <vat:username>anonymous</vat:username>
      <vat:password>anonymous</vat:password>
      <vat:TIN>{tax_id}</vat:TIN>
      <vat:Name></vat:Name>
      <vat:ProvinceCode>0</vat:ProvinceCode>
      <vat:BranchNumber>{branch_no}</vat:BranchNumber>
      <vat:AmphurCode>0</vat:AmphurCode>
    </vat:Service>
  </soap:Body>
</soap:Envelope>"""

# 地址拼装顺序（照旧项目那份），每段给一组别名。
ADDRESS_PARTS = (
    ("HouseNumber",), ("MooNumber", "Moo"), ("Yaek",), ("VillageName",), ("BuildingName",),
    ("RoomNumber",), ("FloorNumber",), ("SoiName", "Soi"), ("StreetName", "Street"),
    ("Thambol", "ThambolName", "ThumbolName", "TambolName"),
    ("Amphur", "AmphurName"), ("Province", "ProvinceName"), ("PostCode",),
)


def normalize_tin(value) -> str:
    """取 13 位数字（任何非数字都剥掉）；长度不对返回空串。"""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 13 else ""


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _inner_value(element):
    """`<vX><anyType>值</anyType></vX>` 这种包装里取最深的那个文本。"""
    own = (element.text or "").strip()
    if own:
        return own
    for sub in element.iter():
        if sub is element:
            continue
        text = (sub.text or "").strip()
        if text:
            return text
    return None


def raw_fields(xml_text: str) -> dict:
    """把响应里所有 `vXxx` 字段摊平成 `Xxx -> 值`（anyType/string 那类包装跳过）。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    fields: dict = {}
    for element in root.iter():
        tag = strip_ns(element.tag)
        if not tag or tag.lower() in ("anytype", "string", "envelope", "body", "header"):
            continue
        if not tag.startswith("v") or len(tag) < 2 or not tag[1].isupper():
            continue
        value = _inner_value(element)
        if not value:
            continue
        fields.setdefault(tag[1:], value)
    return fields


def pick(fields: dict, *names) -> str:
    """按别名取值；"-"（税局的空值写法）当没有。"""
    for name in names:
        value = str(fields.get(name) or "").strip()
        if value and value != "-":
            return value
    return ""


def truthy(fields: dict, *names) -> bool:
    return pick(fields, *names).lower() in ("true", "1", "yes", "y")


def full_address(fields: dict) -> str:
    return " ".join(part for part in (pick(fields, *names) for names in ADDRESS_PARTS) if part)


def full_name(fields: dict):
    title = pick(fields, "BranchTitleName", "BranchTitle", "TitleName", "Title")
    name = pick(fields, "BranchName", "Name")
    joined = " ".join(part for part in (title, name) if part)
    return joined or None


def branch_number(fields: dict) -> int:
    raw = pick(fields, "BranchNumber") or "0"
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def tin_payload(fields: dict, tin: str) -> dict:
    return {"valid": truthy(fields, "IsExist") and truthy(fields, "DigitOk"),
            "exists": truthy(fields, "IsExist"),
            "nid": pick(fields, "ID", "NID") or tin}


def vat_payload(fields: dict, tin: str, head_label: str, branch_label: str) -> dict:
    """VAT 服务响应 → 界面要的字段（`raw_fields` 原样留着给人对照）。"""
    number = branch_number(fields)
    return {
        "tax_id": pick(fields, "NID", "ID") or tin,
        "branch_no": number,
        "branch_label": head_label if number == 0 else branch_label.format(f"{number:05d}"),
        "name": full_name(fields),
        "address": full_address(fields),
        "post_code": pick(fields, "PostCode"),
        "province": pick(fields, "Province", "ProvinceName"),
        "vat_register_date": pick(fields, "BusinessFirstDate"),
        "raw_fields": fields,
    }
