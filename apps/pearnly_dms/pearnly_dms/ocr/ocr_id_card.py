# -*- coding: utf-8 -*-
"""泰国身份证 OCR（DMS 专用）。

引擎（模型调用、档位、密钥）在共用层 `pearnly_core/ocr.py`；这里只放**这个产品
自己的东西**：抽哪些字段、怎么归一。

照老产品搬（`services/ocr/id_card_extract.py`），**提示词一字未改** ——
那段提示词是拿真件调出来的（泰文月份表、"เม.ย. 是 04 不是 05"、
"不确定就返回空号，不要编一个 13 位"），改一个字都可能把准确率改坏。

只用 flash-lite 读一次（用户 2026-09-27 定，不再依赖 3.8）。ris 50 张真证件实测：
号码、生日 50/50，地址与 3.8 持平，弱项只在泰文姓名偶尔漏一个元音/声调符号；这类误读
由 DMS 主档比对兜住（`intake.customer_diff.master_name_if_close`，按证号找到的老客户姓名
足够像就按主档，不报假"改名"）。读三次投票实测不更准（同一处常三次都读错），已放弃。

两条业务规则也照搬：

1. **13 位身份证号是刚需**：读不出就置空、打 needs_review（老产品"升档再读一次"
   不再保留 —— 升档目标是 3.8；flash-lite 实测号码 50/50）。
2. **归零门槛**：号不是 13 位就置空 —— **绝不把不合规的号推给 DMS**。
   而且号/名/姓缺任一项就标 `needs_review`，不许进推送流程。
"""

import re

import frappe
from frappe import _

from pearnly_core import ocr

# ⚠️ 一字未改地照搬老产品 `services/ocr/id_card_extract.py:_ID_CARD_PROMPT`。
_ID_CARD_PROMPT = (
    "You are reading a THAI NATIONAL ID CARD (บัตรประจำตัวประชาชน) from OCR text.\n"
    "Extract ONLY the cardholder identity fields. Return STRICT JSON, no prose, "
    "no markdown fences. Use EMPTY STRING for any field you cannot read.\n"
    "Schema:\n"
    "{\n"
    '  "people_id": "the 13-digit Thai citizen ID, digits only (strip spaces/dashes)",\n'
    '  "prefix": "Thai title e.g. นาย / นาง / นางสาว / เด็กชาย / เด็กหญิง, else empty",\n'
    '  "first_name_th": "given name in Thai",\n'
    '  "last_name_th": "family name in Thai",\n'
    '  "date_of_birth_be": "date of birth as dd/mm/yyyy in BUDDHIST ERA (พ.ศ.). '
    "If the card shows a Thai month name, convert to numeric month. If the year is "
    'Gregorian (ค.ศ.), add 543 to make it Buddhist era.",\n'
    "Thai month abbreviations: ม.ค.=01 ก.พ.=02 มี.ค.=03 เม.ย.=04 พ.ค.=05 "
    "มิ.ย.=06 ก.ค.=07 ส.ค.=08 ก.ย.=09 ต.ค.=10 พ.ย.=11 ธ.ค.=12. Note: "
    "เม.ย. is month 04 (April), NOT 05. Convert strictly using this table.\n"
    '  "issue_date_be": "date of issue (วันออกบัตร / Date of Issue) as dd/mm/yyyy in '
    'Buddhist era, same era/format rules as date_of_birth_be. Empty if not printed.",\n'
    '  "expiry_date_be": "expiry date (วันบัตรหมดอายุ / Date of Expiry) as dd/mm/yyyy in '
    "Buddhist era, same era/format rules as date_of_birth_be. If the card says ตลอดชีพ "
    '(lifetime), return the literal string LIFETIME. Empty if not printed.",\n'
    '  "address_raw": "the full registered address line as printed",\n'
    '  "house_no": "house number (บ้านเลขที่)",\n'
    '  "moo": "village no (หมู่ที่) digits only, else empty",\n'
    '  "soi": "soi/alley, else empty",\n'
    '  "road": "road (ถนน), else empty",\n'
    '  "subdistrict": "tambon/khwaeng (ตำบล/แขวง) name without prefix word",\n'
    '  "district": "amphoe/khet (อำเภอ/เขต) name without prefix word",\n'
    '  "province": "province (จังหวัด) name without prefix word",\n'
    '  "zipcode": "5-digit postal code if present, else empty"\n'
    "}\n"
    "Do NOT invent a 13-digit number. If you are not sure it is exactly 13 digits, "
    "return an empty people_id."
)

# 身份证固定用 flash-lite（与站点 OCR 档位无关），不升档到 3.8。
ID_CARD_MODEL = ocr.MODE_MODEL_MAPS["economy"]["flash_lite"]


# 泰文称谓缩写 → 全称。身份证印缩写 น.ส.，而 DMS 称谓下拉是全称 นางสาว ——
# 不归一会匹配不到、回落错值（老产品踩过）。
_THAI_PREFIX_ALIASES = {
    "น.ส.": "นางสาว",
    "นส.": "นางสาว",
    "นส": "นางสาว",
    "ด.ช.": "เด็กชาย",
    "ดช.": "เด็กชาย",
    "ด.ญ.": "เด็กหญิง",
    "ดญ.": "เด็กหญิง",
}


class IdCardExtractError(RuntimeError):
    pass


def _normalize_thai_prefix(s: str) -> str:
    if not s:
        return ""
    return _THAI_PREFIX_ALIASES.get(s.replace(" ", ""), s)


def _normalize_be_date(s: str) -> str:
    """dd/mm/yyyy 归一到佛历。年份看着像公历（< 2200）就 +543。解析不出返回空串。"""
    if not s:
        return ""
    m = re.search(r"(\d{1,2})\D+(\d{1,2})\D+(\d{4})", s)
    if not m:
        return ""
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 2200:
        year += 543
    return f"{day:02d}/{month:02d}/{year}"


def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def normalize(data: dict) -> dict:
    """模型原始输出 → 可入库的结构。判据与门槛照搬老产品 `_normalize`。"""
    data = data if isinstance(data, dict) else {}
    people_id = _digits(data.get("people_id"))
    if len(people_id) != 13:
        people_id = ""  # 不合规的号一律置空，绝不推给 DMS

    first_name = str(data.get("first_name_th") or data.get("first_name") or "").strip()
    last_name = str(data.get("last_name_th") or data.get("last_name") or "").strip()
    birthday_be = _normalize_be_date(str(data.get("date_of_birth_be") or "").strip())
    issue_date_be = _normalize_be_date(str(data.get("issue_date_be") or "").strip())
    raw_expiry = str(data.get("expiry_date_be") or "").strip()
    # 终身证印 ตลอดชีพ（无到期日）原样透出 LIFETIME，其余走日期归一
    expiry_date_be = (
        "LIFETIME" if raw_expiry.upper() == "LIFETIME" else _normalize_be_date(raw_expiry)
    )

    address = {
        "house_no": str(data.get("house_no") or "").strip(),
        "moo": str(data.get("moo") or "").strip(),
        "soi": str(data.get("soi") or "").strip(),
        "road": str(data.get("road") or "").strip(),
        "subdistrict": str(data.get("subdistrict") or "").strip(),
        "district": str(data.get("district") or "").strip(),
        "province": str(data.get("province") or "").strip(),
        "zipcode": str(data.get("zipcode") or "").strip(),
        "address_raw": str(data.get("address_raw") or "").strip(),
        # 地址主数据 ID 不在这层解析（老产品 v1 也是先落文本，ID 映射算后续工作）
        "province_id": "",
        "district_id": "",
        "subdistrict_id": "",
        "zipcode_id": "",
    }
    if not address["house_no"] and address["address_raw"]:
        address["house_no"] = address["address_raw"][:120]

    missing = []
    if not people_id:
        missing.append("people_id")
    if not first_name:
        missing.append("first_name")
    if not last_name:
        missing.append("last_name")
    if not birthday_be:
        missing.append("birthday")

    # 门槛：号 + 名 + 姓 是"能推"的必要条件；缺任一就要人工核对，不进推送
    needs_review = bool(
        missing and any(f in missing for f in ("people_id", "first_name", "last_name"))
    )
    score = 4 - len(missing)
    confidence = "high" if score >= 4 else ("medium" if score >= 2 else "low")

    return {
        "id_card": {
            "people_id": people_id,
            "first_name": first_name,
            "last_name": last_name,
            "birthday_be": birthday_be,
            "issue_date_be": issue_date_be,
            "expiry_date_be": expiry_date_be,
            "prefix_name": _normalize_thai_prefix(str(data.get("prefix") or "").strip()),
            "address": address,
        },
        "confidence": confidence,
        "needs_review": needs_review,
        "missing_fields": missing,
        "raw": data,
    }


def extract(image_bytes: bytes) -> dict:
    """身份证图 → 归一后的字段。

    三种结果分得很清楚，**不要混**：

    · 读出来了 → 返回归一结果（里面用 `needs_review` 表达"这张图够不够格"）；
    · 服务是通的但没读出内容 → 抛 `IdCardExtractError`（图的问题）；
    · **根本调不动模型**（限流/配额/网络）→ 抛 `OcrUnavailable`（基础设施的问题），
      原样往上抛，**绝不伪装成"图不合格"**。
    """
    if not image_bytes:
        raise IdCardExtractError("empty image bytes")

    img = bytes(image_bytes)
    mime = ocr.detect_image_mime(img)

    # `OcrUnavailable` / `Deferred` 会从这里直接冒出去（刻意不 catch）：配额/限流是
    # 基础设施问题，上层要能分辨，不能落进"待人工核对"里。
    from pearnly_core.claude_ocr import client as claude_ocr
    if claude_ocr.uses_claude():
        # 本租户选了 Claude 识别（ADR-022）：只调 Anthropic，不调谷歌；字段名与下面 normalize 认的一致。
        read = claude_ocr.read("id_card", [(img, mime)])["read"]
        if read is not None and read.get("is_id_card") is False:
            read = {}
        data = read
    else:
        data = ocr.json_from_images(_ID_CARD_PROMPT, [(img, mime)], model=ID_CARD_MODEL)
    if not isinstance(data, dict):
        raise IdCardExtractError(
            _("Neither tier produced a result (network, key or model name could all be the cause)")
        )
    return normalize(data)


@frappe.whitelist()
def api_status():
    """DMS 这边看 OCR 能不能用。"""
    return {**ocr.api_status(), "task": "thai_id_card"}
