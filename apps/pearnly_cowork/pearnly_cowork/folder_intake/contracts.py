"""文件夹自动录入的纯规则（不连库、不依赖框架）：路径、时间、规则默认值、防重键、小助手报文的形状。

规则口径见 `docs/work/2026-10-06-cowork-folder-intake.md`：一个文件夹 = 一条规则 = 一家公司 + 一个年度账套 +
进项或销项 + 服务或存货；只收 PDF / 图片；结果文件夹（`RESULT_FOLDERS`）由系统生成、不扫描。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Mapping, Optional

RESULT_FOLDER = "_Pearnly结果"
# 结果文件夹按建规则那个人的网页语言起名（用户 2026-10-08），建好就记在规则上不再变；
# 这几个名字同时是与小助手的约定（扫描时一律跳过），所以写在这里而不放翻译包：译文改了，
# 盘上已有的文件夹仍要认得。没记名字的旧规则沿用 RESULT_FOLDER。
RESULT_FOLDERS = {"zh": RESULT_FOLDER, "th": "_ผลลัพธ์ Pearnly", "en": "_Pearnly Results", "ja": "_Pearnly結果"}
_RESULT_NAMES = frozenset(name.casefold() for name in RESULT_FOLDERS.values())
EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif")
DIRECTIONS = ("purchase", "sales")
POSTING_KINDS = ("service", "stock")
PAYMENT_VALUES = ("cash", "credit")
VAT_VALUES = ("claim", "expense")
ITEM_VALUES = ("__new__", "ask")
NEW = "__new__"
LAST_DAY = 0
MAX_PATH = 500
MAX_RELPATH = 1000
MAX_WATCH = 500
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_ROOT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RUN_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_BAD_PART = re.compile(r'[<>:"|?*\x00-\x1f]')


class ContractError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def text(value: Any, *, maximum: int, optional: bool = False) -> str:
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum or "\x00" in value:
        raise ContractError("cowork.folder.invalid")
    return value.strip()


def result_folder_for(lang: Any) -> str:
    """建规则的人的界面语言 → 结果文件夹名；不认识的语言（含 zh-TW 这类带地区的）按主语言取，再不行用泰文。"""
    code = str(lang or "").split("-")[0].lower()
    return RESULT_FOLDERS.get(code) or RESULT_FOLDERS["th"]


def result_folder(value: Any) -> str:
    """规则上记的结果文件夹名；空或不是约定里的名字（旧规则）就是 RESULT_FOLDER。"""
    name = str(value or "")
    return name if name.casefold() in _RESULT_NAMES else RESULT_FOLDER


def is_result_folder(part: str) -> bool:
    return part.casefold() in _RESULT_NAMES


def folder(value: Any) -> str:
    """规则里的文件夹：允许读取的根目录下的相对路径，`\\` 分隔；空串 = 根目录本身。

    不许上跳（`..`）、盘符、绝对路径、非法字符，也不许落在系统生成的结果文件夹里。"""
    raw = "" if value in (None, "") else text(value, maximum=MAX_PATH)
    parts = [part.strip() for part in re.split(r"[\\/]+", raw) if part.strip()]
    for part in parts:
        if part in (".", "..") or _BAD_PART.search(part) or part.endswith(".") or is_result_folder(part):
            raise ContractError("cowork.folder.path_invalid")
    return "\\".join(parts)


def nested(a: str, b: str) -> bool:
    """两个规则文件夹是否相同或互相包含（同一根目录下）：包含时同一份文件会被两条规则按不同口径各推一次。"""
    left, right = a.casefold().split("\\") if a else [], b.casefold().split("\\") if b else []
    shorter = min(len(left), len(right))
    return left[:shorter] == right[:shorter]


def root_id(value: Any) -> str:
    if not isinstance(value, str) or not _ROOT_ID.fullmatch(value):
        raise ContractError("cowork.folder.root_invalid")
    return value


def schedule(day: Any, at: Any) -> tuple:
    """每月几号（1–28，0 = 月末）几点（曼谷 HH:MM）。"""
    if isinstance(day, str) and day.strip().isdigit():
        day = int(day.strip())
    if type(day) is not int or not 0 <= day <= 28:
        raise ContractError("cowork.folder.schedule_invalid")
    if not isinstance(at, str) or not _TIME.fullmatch(at.strip()):
        raise ContractError("cowork.folder.schedule_invalid")
    return day, at.strip()


def defaults(raw: Any, *, direction: str, posting_kind: str, accounts: Optional[set] = None,
             departments: Optional[set] = None) -> Dict[str, str]:
    """「这一类以后都这样处理」：只留这条规则用得到的项，值必须在允许的选项里。

    空值 = 这一类遇到时照现有规则停下等人选一次。`accounts` / `departments` 为 None 表示小助手还没读过这份账套，
    这时不核对代码是否存在（推送时现有规则会再核：选过的值主档里没有就当没选过）。"""
    if raw in (None, ""):
        raw = {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as error:
            raise ContractError("cowork.folder.defaults_invalid") from error
    if not isinstance(raw, Mapping):
        raise ContractError("cowork.folder.defaults_invalid")
    allowed = set(applicable(direction, posting_kind))
    result: Dict[str, str] = {}
    for kind, value in raw.items():
        value = str(value or "").strip()
        if kind not in allowed or not value:
            continue
        if len(value) > 60:
            raise ContractError("cowork.folder.defaults_invalid")
        if kind == "payment" and value not in PAYMENT_VALUES:
            raise ContractError("cowork.folder.defaults_invalid")
        if kind == "vat" and value not in VAT_VALUES:
            raise ContractError("cowork.folder.defaults_invalid")
        if kind == "item" and value not in ITEM_VALUES:
            raise ContractError("cowork.folder.defaults_invalid")
        if kind == "account" and accounts is not None and value not in accounts:
            raise ContractError("cowork.folder.defaults_invalid")
        if kind == "department" and departments is not None and value not in departments:
            raise ContractError("cowork.folder.defaults_invalid")
        result[kind] = value
    return result


def applicable(direction: str, posting_kind: str) -> List[str]:
    """这条规则会遇到哪几类「要人选一次」的决定（与 `push/express/choices.py` 同一组，对方档除外）。"""
    kinds = ["department"]
    if direction == "purchase":
        kinds += ["payment", "vat"]
        if posting_kind == "service":
            kinds.append("account")
    if posting_kind == "stock":
        kinds.append("item")
    return kinds


def default_value(defaults_map: Mapping[str, str], kind: str) -> str:
    """某一类的规则默认值；「停下选」（`ask`）与没设一样返回空。"""
    value = str((defaults_map or {}).get(kind) or "").strip()
    return "" if value == "ask" else value


def entry_key(rule: str, relpath: str, sha256: str) -> str:
    """一份文件在一条规则下的身份：规则 + 规范化相对路径 + 内容摘要；不含上传人、负责人或配对签发人。

    同一内容换了名字或位置是新上传（照常识别、照常扣费，用户 2026-10-02「上传识别都扣费」）。"""
    return hashlib.sha256(json.dumps([rule, relpath.casefold(), sha256]).encode()).hexdigest()


def relpath(value: Any) -> str:
    raw = text(value, maximum=MAX_RELPATH)
    parts = [part for part in re.split(r"[\\/]+", raw) if part]
    if not parts or any(part in (".", "..") or _BAD_PART.search(part) for part in parts):
        raise ContractError("cowork.folder.path_invalid")
    if any(is_result_folder(part) for part in parts[:-1]):
        raise ContractError("cowork.folder.path_invalid")
    return "\\".join(parts)


def filename(value: Any) -> str:
    name = text(value, maximum=255)
    if "\\" in name or "/" in name or not name.lower().endswith(EXTENSIONS):
        raise ContractError("cowork.folder.type_invalid")
    return name


def sha256(value: Any) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ContractError("cowork.folder.invalid")
    return value


def byte_size(value: Any, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ContractError("cowork.folder.size_invalid")
    return value


def run_key(value: Any) -> str:
    if not isinstance(value, str) or not _RUN_KEY.fullmatch(value):
        raise ContractError("cowork.folder.invalid")
    return value


def watch(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > MAX_WATCH:
        raise ContractError("cowork.folder.invalid")
    return [sha256(item) for item in value]


def roots(value: Any) -> List[Dict[str, Any]]:
    """小助手上报的本机允许读取的根目录（只报编号、显示名与下级文件夹名，不报文件）。"""
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise ContractError("cowork.folder.invalid")
    result = []
    for row in value:
        # 一项不合规（显示名太长、文件夹名带非法字符……）只丢这一项，不让整次同步失败。
        try:
            if not isinstance(row, Mapping):
                continue
            folders = row.get("folders") or []
            if not isinstance(folders, list):
                folders = []
            clean = set()
            for item in folders[:2000]:
                try:
                    clean.add(folder(item))
                except ContractError:
                    continue
            result.append({"id": root_id(row.get("id")), "label": text(row.get("label"), maximum=255),
                           "folders": sorted(clean - {""})})
        except ContractError:
            continue
    return result


def workbook_name(invoice_no: Any, seller_tax: Any, fallback: str, no_number: str = "") -> str:
    """结果表格的文件名：`发票号_卖方税号后4位.xlsx`；没有发票号用 `<no_number>原文件名.xlsx`
    （前缀由调用方按规则负责人的语言取词条 `cowork.folder.no_number_prefix`）。Windows 不认的字符换成 `-`。"""
    number = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(invoice_no or "").strip()).strip(" .")[:80]
    tail = re.sub(r"\D", "", str(seller_tax or ""))[-4:]
    if number:
        stem = f"{number}_{tail}" if tail else number
    else:
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", fallback.rsplit(".", 1)[0]).strip(" .")[:80] or "document"
        prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(no_number or "")).strip()[:40]
        stem = prefix + base
    return stem + ".xlsx"
