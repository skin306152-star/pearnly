"""文件夹自动录入的纯规则（不连库、不依赖框架）：路径、时间、规则默认值、防重键、小助手报文的形状。

规则口径见 `docs/work/2026-10-06-cowork-folder-intake.md`：一个文件夹 = 一条规则 = 一家公司 + 一个年度账套 +
进项或销项 + 服务或存货；收 PDF / 图片，推往 Express 的销售任务另收 POS 导出的 Excel / CSV（`TABLE_EXTENSIONS`，
2026-10-13，按天录销售、不识别不扣费）；结果文件夹（`RESULT_FOLDERS`）由系统生成、不扫描。

新旧任务两种口径（2026-10-12，`docs/work/2026-10-12-cc-closed-loop.md`）：新任务（规则上 `include_existing`，同步里
下发 `existing=1`）到点处理文件夹（含子文件夹）里本任务还没处理过的所有文件，包括建任务前已有的；老任务照旧只收
启用之后放进来的（小助手按创建时间拍底）。同步里缺 `existing` 或为 0 就按老任务办，新旧服务端与小助手任意搭配都不会多处理。

先后（F7）：同一写入连接、同一账套里，存货销售文件等存货进货先写进 Express 再准备推送（`waits_for_purchases`）。

提醒与简报（F2 / F3）：定时时刻与小助手逐位一致（`latest_slot` / `next_slot`），负责人自选的提醒时刻（`remind_times`，
默认前一天 08:00、14:00）与提醒时段（`reminder_slot`）、合并与去重键，结果口径（`tally`，任务列表、运行记录、完成简报
同一个）与简报分组（`brief_groups`）。
"""
from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

RESULT_FOLDER = "_Pearnly结果"
# 结果文件夹按建规则那个人的网页语言起名（用户 2026-10-08），建好就记在规则上不再变；
# 这几个名字同时是与小助手的约定（扫描时一律跳过），所以写在这里而不放翻译包：译文改了，
# 盘上已有的文件夹仍要认得。没记名字的旧规则沿用 RESULT_FOLDER。
RESULT_FOLDERS = {"zh": RESULT_FOLDER, "th": "_ผลลัพธ์ Pearnly", "en": "_Pearnly Results", "ja": "_Pearnly結果"}
_RESULT_NAMES = frozenset(name.casefold() for name in RESULT_FOLDERS.values())
EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif")
# POS 导出（交接 L9）：推往 Express 的销售任务另收收银机导出的表格，到点与票据一起处理（`pos_import`，按天建票）。
# 与小助手 `folder_plan.TABLE_EXTENSIONS` 同一组，同步按任务下发（`rules[].extensions`）。旧版 Excel（.xls）镜像里不读：
# 照样登记，登记时就记失败、写明原因（`LEGACY_TABLE`，`pos.xls_not_supported`），不上传，结果与简报里看得到。
TABLE_EXTENSIONS = (".xlsx", ".xlsm", ".csv", ".xls")
LEGACY_TABLE = ".xls"
XLS_NOT_SUPPORTED = "pos.xls_not_supported"
POS_LAYOUT_NEEDED = "pos.layout_needed"  # POS 导出的列还没认好：停在 source_ready 等人答一次（算「停下待人」）
DIRECTIONS = ("purchase", "sales")
POSTING_KINDS = ("service", "stock")
# MR.ERP 在入口选付款方式（与「新建录入」同一口径：进项只有赊账，旧线上对 MR.ERP 进项现金明确拦截）。
MRERP_PAYMENTS = {"purchase": ("credit",), "sales": ("cash", "credit")}
# 规则可以预先定下的「这一类都这样处理」（与 push/express/choices.py 的选择项同名，对方档除外）
KINDS = ("payment", "vat", "account", "item", "department")
PAYMENT_VALUES = ("cash", "credit")
VAT_VALUES = ("claim", "expense")
ITEM_VALUES = ("__new__", "ask")
NEW = "__new__"
LAST_DAY = 0
MAX_PATH = 500
MAX_RELPATH = 1000
MAX_WATCH = 500
MAX_FOLDERS = 2000
# 一台电脑最多上报这么多个根目录（一家客户一个文件夹，ADR-020）；多出的只丢多余的，不让整次同步失败。
# 与 CC 桌面版同名常量一致（tests/test_companion_contract.py），同步回复里另带 `max_roots` 告诉电脑。
MAX_ROOTS = 200
# 所有根目录的下级文件夹名合计上限（报文大小）；超出只截断文件夹名，根目录本身保留。与 CC 桌面版一致。
FOLDERS_TOTAL = 20_000
# 小助手报的文件夹计数、各任务待处理数的上限（防坏报文）
MAX_COUNT = 10_000_000
MAX_PENDING = 500
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
    """每月几号（1–31，0 = 月末）几点（曼谷 HH:MM）。当月没有这一天（2 月的 29–31 日、小月的 31 日）就在当月
    最后一天跑（小助手 `folder_plan._slot` 取 min(这一天, 当月天数)，用户 2026-10-09 要能选 29 日）。"""
    if isinstance(day, str) and day.strip().isdigit():
        day = int(day.strip())
    if type(day) is not int or not 0 <= day <= 31:
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


def extensions_for(direction: Any, adapter: Any = "express") -> tuple:
    """这条任务收哪些文件：PDF / 图片；推往 Express 的销售任务另收 POS 导出（Excel / CSV）。"""
    return EXTENSIONS + TABLE_EXTENSIONS if direction == "sales" and adapter == "express" else EXTENSIONS


def is_table(name: Any) -> bool:
    """这份文件是 POS 导出（按天录销售，不交识别）。"""
    return str(name or "").lower().endswith(TABLE_EXTENSIONS)


def is_legacy_table(name: Any) -> bool:
    """旧版 Excel（.xls）：收进任务但读不了，登记即失败。"""
    return str(name or "").lower().endswith(LEGACY_TABLE)


def filename(value: Any, extensions: tuple = EXTENSIONS) -> str:
    name = text(value, maximum=255)
    if "\\" in name or "/" in name or not name.lower().endswith(tuple(extensions)):
        raise ContractError("cowork.folder.type_invalid")
    return name


def pos_workbook_name(source: Any) -> str:
    """POS 导出的结果表格（逐日一行）：`<原文件名>_POS.xlsx`，Windows 不认的字符换成 `-`。"""
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(source or "").rsplit(".", 1)[0]).strip(" .")[:80] or "POS"
    return base + "_POS.xlsx"


# ── CC 从已授权文件夹列文件、取文件（2026-10 CC 闭环 L2，`fetch.py`）─────────────────────────────

# 能取进 CC 对话的类型：与对话附件同一份（共用层 `pearnly_core.assistant.contracts.UPLOAD_EXTENSIONS`；本模块要能按路径
# 单独加载、不依赖共用层，所以照抄成字面值），也与小助手 `folder_fetch.FETCH_EXTENSIONS` 一致（两处都由测试锁住）。
FETCH_EXTENSIONS = (".csv", ".docx", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".json", ".md", ".pdf", ".png",
                    ".tsv", ".txt", ".webp", ".xls", ".xlsx")
# 小助手一次最多列这么多份（按修改时间新的在前）；再多就标 truncated，请用子文件夹、搜索词或类型缩小。
FETCH_LIST_MAX = 5000
FETCH_SEARCH_MAX = 100


def _browse_parts(raw: str) -> List[str]:
    # 每段原样保留（名字首尾带空格的文件夹 / 文件照样取得回），与小助手 `folder_fetch._parts` 同一规则：
    # 整条路径去掉首尾空白、按分隔符拆段，只丢空段；全是空白的段不收
    parts = [part for part in re.split(r"[\\/]+", raw) if part]
    for part in parts:
        if part in (".", "..") or not part.strip() or _BAD_PART.search(part) or part.endswith("."):
            raise ContractError("cowork.folder.fetch_path_invalid")
    return parts


def browse_folder(value: Any) -> str:
    """列文件的起点：根目录下的文件夹，`\\` 分隔，空串 = 根目录本身。同 `folder`，只是结果文件夹也可以看。"""
    raw = "" if value in (None, "") else text(value, maximum=MAX_PATH)
    return "\\".join(_browse_parts(raw))


def fetch_path(value: Any) -> str:
    """要取的一份文件：根目录下的相对路径。同 `relpath`（不许上跳、盘符、绝对路径、非法字符），但结果文件夹里的
    也可以取（里面是 Pearnly 结果表，会计可能改过）；类型要在 FETCH_EXTENSIONS 里。"""
    try:
        raw = text(value, maximum=MAX_RELPATH)
    except ContractError:
        raise ContractError("cowork.folder.fetch_path_invalid") from None
    parts = _browse_parts(raw)
    if not parts:
        raise ContractError("cowork.folder.fetch_path_invalid")
    if not parts[-1].lower().endswith(FETCH_EXTENSIONS):
        raise ContractError("cowork.folder.fetch_type_invalid")
    return "\\".join(parts)


def fetch_extensions(value: Any) -> List[str]:
    """只列这几种类型（如 ``[".xlsx", "pdf"]``）；空 = 全部能取的类型。"""
    if value in (None, "", []):
        return []
    if not isinstance(value, list) or len(value) > len(FETCH_EXTENSIONS):
        raise ContractError("cowork.folder.fetch_type_invalid")
    found: List[str] = []
    for item in value:
        ext = "." + str(item or "").strip().lower().lstrip(".")
        if ext not in FETCH_EXTENSIONS:
            raise ContractError("cowork.folder.fetch_type_invalid")
        if ext not in found:
            found.append(ext)
    return found


def fetched_listing(raw: Any) -> Dict[str, Any]:
    """小助手交回的文件清单 ``{files: [{path, name, size, modified, result_folder}], truncated, scanned}``：不合形状的项
    丢掉，路径照取文件的规则核一遍（列出来的都能直接拿去取）。"""
    if not isinstance(raw, Mapping):
        raise ContractError("cowork.folder.invalid")
    files = []
    for row in (raw.get("files") if isinstance(raw.get("files"), list) else [])[:FETCH_LIST_MAX]:
        if not isinstance(row, Mapping):
            continue
        size, modified = row.get("size"), row.get("modified")
        if type(size) is not int or size < 0 or isinstance(modified, bool) or not isinstance(modified, (int, float)) \
                or not 0 <= modified <= 32_503_680_000:  # epoch 秒，到公元 3000 年（也挡掉 NaN、无穷）
            continue
        try:
            path = fetch_path(row.get("path"))
        except ContractError:
            continue
        files.append({"path": path, "name": path.rsplit("\\", 1)[-1], "size": size, "modified": float(modified),
                      "result_folder": any(is_result_folder(part) for part in path.split("\\")[:-1])})
    scanned = raw.get("scanned")
    return {"files": files, "truncated": raw.get("truncated") is True,
            "scanned": scanned if type(scanned) is int and scanned >= 0 else len(files)}


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


def _depth_first(names: set) -> List[str]:
    return sorted(names, key=lambda name: (name.count("\\"), name.casefold(), name))


def roots(value: Any, keep_missing: bool = False) -> List[Dict[str, Any]]:
    """小助手上报的本机允许读取的根目录（只报编号、显示名与下级文件夹名；1.4.4 起另报各文件夹的文件数，不报文件）。
    最多收 `MAX_ROOTS` 个（多出的丢掉）；下级文件夹名合计最多 `FOLDERS_TOTAL` 个（超出先丢深层的），根目录本身保留。
    `keep_missing`（快速同步 `roots_only`）：上报行里没带 `folders` 键的根目录，结果里也不带 `folders` 与计数，
    由调用方沿用上一次的（`agent.remember_roots`）。"""
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ContractError("cowork.folder.invalid")
    result = []
    total = 0
    for row in value:
        if len(result) >= MAX_ROOTS:
            break
        # 一项不合规（显示名太长、文件夹名带非法字符……）只丢这一项，不让整次同步失败。
        try:
            if not isinstance(row, Mapping):
                continue
            missing = keep_missing and "folders" not in row
            folders = row.get("folders") or []
            if not isinstance(folders, list):
                folders = []
            clean = set()
            for item in folders[:MAX_FOLDERS]:
                try:
                    clean.add(folder(item))
                except ContractError:
                    continue
            clean.discard("")
            room = max(FOLDERS_TOTAL - total, 0)
            if len(clean) > room:
                clean = set(_depth_first(clean)[:room])
            share = str(row.get("share") or "").strip()
            report = {"id": root_id(row.get("id")), "label": text(row.get("label"), maximum=255),
                      # 网络共享（含映射盘换算回的 \\服务器\共享…）：跨电脑比对文件夹是否重叠用；本机盘为空
                      "share": share if share.startswith("\\\\") and len(share) <= 500 else ""}
            if missing:
                result.append(report)
                continue
            report["folders"] = sorted(clean)
            total += len(clean)
            if isinstance(row.get("counts"), Mapping):
                report["counts"] = _folder_counts(row["counts"], clean)
                # 数到时间或条数上限还没数完：显示「N+」
                report["partial"] = row.get("partial") is True
                # POS 导出（Excel / CSV）另数（只有推往 Express 的销售任务收它们，1.5 起）
                if isinstance(row.get("table_counts"), Mapping):
                    report["table_counts"] = _folder_counts(row["table_counts"], clean)
            result.append(report)
        except ContractError:
            continue
    return result


def _folder_counts(counts: Mapping, folders: set) -> Dict[str, int]:
    """文件夹计数（新任务卡片与表单的「文件夹里现有 N 份」）：{文件夹: 会被录入的文件数（含子文件夹）}，
    键只留上报的文件夹与根目录本身（""），按上报的写法存；0 不存。没带计数的根目录没有 `counts` 键 = 不知道，不用 0 冒充。"""
    known = {name.casefold(): name for name in folders | {""}}
    kept: Dict[str, int] = {}
    for key, value in list(counts.items())[:MAX_FOLDERS + 1]:
        try:
            name = known.get(folder(key).casefold())
        except ContractError:
            continue
        if name is not None and type(value) is int and 1 <= value <= MAX_COUNT:
            kept[name] = value
    return kept


def pending(value: Any) -> Dict[str, int]:
    """小助手上一轮算的各任务待处理数（{任务名: 份数}，1.4.4 起，`rule_pending`）：会被处理、但既不在本机台账也不在
    基线里的文件数。不合形状的项丢掉，不让一次同步失败。"""
    if not isinstance(value, Mapping):
        return {}
    return {name: count for name, count in list(value.items())[:MAX_PENDING]
            if isinstance(name, str) and 0 < len(name) <= 140 and type(count) is int and 0 <= count <= MAX_COUNT}


RULE_HEALTH = ("ok", "root_missing", "folder_missing")
# 结果表格的票状态：推送成功或票已在录入工作台删除，这张都不再变（小助手可以不再来问）。
DELETED = "deleted"
SETTLED = ("success", DELETED)


def health(value: Any) -> Dict[str, str]:
    """小助手报的各规则文件夹在不在：{规则名: ok | root_missing（授权文件夹已移除 / 改名） | folder_missing（规则的
    子文件夹没了）}。不合形状的项丢掉，不让一次同步失败。"""
    if not isinstance(value, Mapping):
        return {}
    return {str(name)[:140]: state for name, state in list(value.items())[:500]
            if isinstance(name, str) and state in RULE_HEALTH}


def share_path(share: str, folder: str) -> str:
    """网络共享上的规则文件夹（小写、`\\` 分隔）；不是网络共享返回空串。"""
    base = str(share or "").strip().rstrip("\\")
    if not base.startswith("\\\\"):
        return ""
    return "\\".join(part for part in [base, folder or ""] if part).casefold()


def path_nested(a: str, b: str) -> bool:
    """两个完整路径是否相同或互相包含。"""
    left, right = [p for p in a.split("\\") if p], [p for p in b.split("\\") if p]
    shorter = min(len(left), len(right))
    return bool(shorter) and left[:shorter] == right[:shorter]


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


def device(value) -> str:
    """小助手报的电脑名（只用于网页显示）：去掉控制字符，最多 60 字。"""
    text = "".join(ch for ch in str(value or "") if ch.isprintable()).strip()
    return text[:60]


# ── 读文件夹的是哪台电脑上的哪个成员（ADR-021 第 4 批，方案 2.7）────────────────────────────────────
# 读者 =（电脑，成员连接）：同一台电脑上 Express 与 MR.ERP 两个成员各自同步文件夹，同一个池里又有好几台电脑，只按连接
# 或只按电脑认都会让任务被处理两遍。对外（CC 工具、第 6 批的网页）给一个不透明的编号「电脑|连接」；只有连接的旧写法
# （第 6 批之前的网页、旧对话里的参数）由服务端按根目录认出是哪台电脑（`readers.resolve`）。
READER_SEP = "|"
_READER_PART = re.compile(r"^[A-Za-z0-9_.:@-]{1,140}$")


def reader_ref(device: Any, connection: Any) -> str:
    """不透明的读者编号；没有电脑（补丁没认出、旧数据）时就是连接名本身。"""
    device, connection = str(device or "").strip(), str(connection or "").strip()
    return f"{device}{READER_SEP}{connection}" if device else connection


def parse_reader(value: Any) -> tuple:
    """读者编号 → (电脑, 连接)；只有连接的旧写法电脑是空串。形状不对报 ``cowork.folder.root_invalid``（同「这台电脑没有
    这个根目录」：调用方本来就按它回）。"""
    text = str(value or "").strip()
    device, _sep, connection = text.rpartition(READER_SEP)
    if not _READER_PART.fullmatch(connection) or (device and not _READER_PART.fullmatch(device)):
        raise ContractError("cowork.folder.root_invalid")
    return device, connection


_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}$")


def session_ref(value: Any) -> str:
    """CC 对话编号（任务最近一次在哪段对话里建、改或启停，提醒挂回那段对话）：形状与共用层对话编号一致才认，否则当没有。"""
    return value if isinstance(value, str) and _SESSION.fullmatch(value) else ""


# ── 电脑通知（2026-10-12）：同步回复里带给小助手的定时任务提醒与完成简报 ─────────────────
NOTICE_FIELDS = ("id", "title", "text", "created", "session")
NOTICE_WINDOW = 12 * 3600
NOTICE_LIMIT = 20


def notices_after(value: Any, now: float) -> float:
    """小助手上次收到的最新一条提醒时刻（epoch 秒，`notices_after`）：不是数字当 0；夹在最近 12 小时之内
    （更早的不补发，未来的不信）。"""
    number = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
    if number != number:  # NaN
        number = 0.0
    return max(now - NOTICE_WINDOW, min(number, now))


def machine_notices(items: List[Mapping], names: Any) -> List[Mapping]:
    """带给这台电脑的提醒只留「涉及的任务全是本机读的」（2026-10-13）：一条提醒里只要有一个任务不是本机读的
    （别的电脑、别的客户的任务名与文件夹路径），整条不给；没有 `refs` 的也不给（说不清涉及谁）。"""
    mine = {str(name) for name in names or [] if name}
    kept = []
    for item in items or []:
        refs = item.get("refs") if isinstance(item, Mapping) else None
        if isinstance(refs, (list, tuple)) and refs and {str(ref) for ref in refs} <= mine:
            kept.append(item)
    return kept


# ── 定时时刻：与小助手 `folder_plan._slot` / `latest_slot` 逐位一致（曼谷 UTC+7，泰国不用夏令时） ────────
BANGKOK = timezone(timedelta(hours=7))
# 小助手报的运行所属时刻（`runs[].slot`）离服务端此刻超过这么久就不认，改用服务端自己算的
SLOT_RANGE = 40 * 86400


def slot_at(year: int, month: int, day: int, at: str) -> float:
    """某年某月按时开跑的时刻（epoch 秒）：0 = 月末，当月没有这一天（2 月的 29–31 日、小月的 31 日）就在月末。"""
    last = calendar.monthrange(year, month)[1]
    hour, minute = (int(part) for part in str(at)[:5].split(":"))
    return datetime(year, month, last if day == 0 else min(day, last), hour, minute, tzinfo=BANGKOK).timestamp()


def latest_slot(now: float, day: int, at: str) -> float:
    """不晚于 ``now`` 的最近一次按时开跑时刻（epoch 秒）。"""
    local = datetime.fromtimestamp(now, BANGKOK)
    current = slot_at(local.year, local.month, day, at)
    if current <= now:
        return current
    year, month = (local.year, local.month - 1) if local.month > 1 else (local.year - 1, 12)
    return slot_at(year, month, day, at)


def next_slot(now: float, day: int, at: str) -> float:
    """晚于 ``now`` 的最近一次按时开跑时刻（epoch 秒）：``latest_slot(t) <= t < next_slot(t)``，两者之间没有别的时刻。"""
    local = datetime.fromtimestamp(now, BANGKOK)
    current = slot_at(local.year, local.month, day, at)
    if current > now:
        return current
    year, month = (local.year, local.month + 1) if local.month < 12 else (local.year + 1, 1)
    return slot_at(year, month, day, at)


def run_slot(value: Any, now: float) -> Optional[float]:
    """小助手报的这次运行所属的定时时刻（`runs[].slot`，1.4.4 起，可缺）：是数字、且在服务端此刻前后 40 天内才认。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if abs(value - now) <= SLOT_RANGE else None


# ── 处理中（2026-10-12）：还没走到 done / not_invoice / failed 的文件 ─────────────────────
UNFINISHED = ("registered", "uploaded", "source_ready", "submitted", "recognized", "pushing")
# 登记后这么久还停在 registered 的不算处理中：上传没完成、盘上文件又被删的残留，后台推进不处理它们，「处理中」永远不会归零。
STALE_REGISTERED = timedelta(hours=24)


def moving(state: Any, registered_at: Optional[datetime], now: datetime, last_code: Any = "") -> bool:
    """这份文件算不算「处理中」（列表、简报同一口径；时间用库里的站点时区裸时间，与 `now_datetime()` 同口径）。
    POS 导出停着等人认列（`POS_LAYOUT_NEEDED`）不算处理中，算停下待人。"""
    if state not in UNFINISHED or layout_needed(state, last_code):
        return False
    if state != "registered":
        return True
    return registered_at is not None and now - registered_at <= STALE_REGISTERED


# ── 存货先进后出（F7，2026-10-12）：同一连接、同一账套里，存货进货先写进 Express，存货销售后写 ──────
# 只管存货任务；服务类与 MR.ERP 任务（posting_kind 为空）不等也不被等。进货停下待人（要人选、失败、转人工）就算有了结论，
# 不拦销售（等人不知道要多久，整批销售不能跟着停一夜）。时间一律用库里的站点时区裸时间。
ORDER_WAITING = UNFINISHED
ORDER_LOOKBACK = timedelta(hours=12)      # 比销售早登记这么久之内的进货算同一批
ORDER_MAX_WAIT = timedelta(hours=12)      # 销售最多等这么久
ORDER_UPLOAD_GRACE = timedelta(hours=2)   # 只登记、没传完的进货，这么久之后不再等
ORDER_RUN_GRACE = timedelta(minutes=60)   # 进货任务被立即运行、小助手还没报上这次运行，最多等这么久
WAITING_PURCHASES = "cowork.folder.waiting_purchases"


def layout_needed(state: Any, last_code: Any) -> bool:
    return state == "source_ready" and last_code == POS_LAYOUT_NEEDED


def stock_role(direction: Any, posting_kind: Any) -> str:
    """'purchase' / 'sale' / ''：只有存货任务参与先后。"""
    if posting_kind != "stock":
        return ""
    return {"purchase": "purchase", "sales": "sale"}.get(direction or "", "")


def purchases_first(rows: List[Any], direction=lambda row: row["direction"]) -> List[Any]:
    """同步下发顺序：进货任务在前，同方向内保持原顺序（稳定排序）。小助手按下发顺序逐个任务跑，同一时刻到点的进货文件先登记。"""
    return sorted(rows, key=lambda row: direction(row) == "sales")


def purchase_run_pending(run_request_at: Optional[datetime], last_reported_at: Optional[datetime],
                         now: datetime) -> bool:
    """存货进货任务刚被「立即运行」、小助手还没报上这次运行（只看非补传的运行记录）。"""
    if run_request_at is None or now - run_request_at > ORDER_RUN_GRACE:
        return False
    return last_reported_at is None or last_reported_at < run_request_at


def purchase_file_pending(state: Any, registered_at: Optional[datetime], sale_registered_at: datetime,
                          now: datetime) -> bool:
    """这份存货进货文件还在路上、且与这份销售算同一批。"""
    if registered_at is None or state not in ORDER_WAITING:
        return False
    if registered_at < sale_registered_at - ORDER_LOOKBACK:
        return False
    return not (state == "registered" and now - registered_at > ORDER_UPLOAD_GRACE)


def waits_for_purchases(sale_registered_at: Optional[datetime], files: List[Mapping], runs: List[Mapping],
                        now: datetime) -> bool:
    """这份存货销售文件是否还要等同账套的存货进货先写（files: [{state, registered_at}]，
    runs: [{run_request_at, last_reported_at}]）；登记满 12 小时后不再等。"""
    if sale_registered_at is None or now - sale_registered_at > ORDER_MAX_WAIT:
        return False
    return (any(purchase_run_pending(r.get("run_request_at"), r.get("last_reported_at"), now) for r in runs)
            or any(purchase_file_pending(f.get("state"), f.get("registered_at"), sale_registered_at, now)
                   for f in files))


# ── 运行前提醒（F2，2026-10-12；2026-10-13 起提醒时刻由负责人自选）──────────────────────────────
# 每个任务最多 3 个提醒时刻：运行日前几天（0 = 当天，最多 7 天）+ 曼谷 HH:MM；没选过（老任务、空）就是前一天 08:00 与 14:00。
# 每个提醒从它的时刻发到同一任务下一个更晚的提醒时刻（最后一个到运行时刻）为止，只发一次；窗口长、每 5 分钟看一次，
# 部署停机跨过整点也不漏。落在运行时刻当时或之后的提醒不发；「当天」的时间不早于运行时间就永远发不出，保存时就拒
# （`remind_times(…, run_time)`，`cowork.folder.remind_same_day`），不静默存下。
DEFAULT_REMIND_TIMES = [{"days_before": 1, "time": "08:00"}, {"days_before": 1, "time": "14:00"}]
REMIND_MAX = 3
REMIND_DAYS = 7
# 一条合并提醒 / 一份简报最多列这么多个任务，其余写「另有 N 项」
LIST_MAX = 10


def remind_times(value: Any, run_time: Any = None) -> List[Dict[str, Any]]:
    """提醒时刻（规则字段 `remind_times` 的 JSON，或已解析的列表）：``[{"days_before": 0–7, "time": "HH:MM"}]``，
    去重后按离运行由远到近排（天数大的在前，同一天时间早的在前）；空或没有 = 默认（前一天 08:00、14:00）。
    不是列表、超过 3 个、天数不在 0–7、时间不是 HH:MM 时抛 ContractError（`cowork.folder.invalid`）。给了任务的运行时间
    ``run_time``（HH:MM）时，「当天」的提醒时间不早于它（永远发不出）抛 `cowork.folder.remind_same_day`。"""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else None
        except ValueError as error:
            raise ContractError("cowork.folder.invalid") from error
    if value is None or value == []:
        return [dict(item) for item in DEFAULT_REMIND_TIMES]
    if not isinstance(value, list) or len(value) > REMIND_MAX:
        raise ContractError("cowork.folder.invalid")
    found = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ContractError("cowork.folder.invalid")
        days, at = item.get("days_before"), item.get("time")
        if isinstance(days, str) and days.strip().isdigit():
            days = int(days.strip())
        if type(days) is not int or not 0 <= days <= REMIND_DAYS or not isinstance(at, str) \
                or not _TIME.fullmatch(at.strip()):
            raise ContractError("cowork.folder.invalid")
        found.add((days, at.strip()))
    limit = str(run_time or "").strip()[:5]
    if _TIME.fullmatch(limit) and any(days == 0 and at >= limit for days, at in found):
        raise ContractError("cowork.folder.remind_same_day")
    return [{"days_before": days, "time": at} for days, at in sorted(found, key=lambda pair: (-pair[0], pair[1]))]


def remind_label(item: Mapping) -> str:
    """一个提醒时刻的标签（合并与去重键用）：前一天整点沿用原来的「08」「14」写法（上线前已发的那条不再发一次），
    其余是「<天数>d<HH:MM>」。同一运行时刻下，标签相同就是同一个提醒时刻。"""
    days, at = item["days_before"], item["time"]
    return at[:2] if days == 1 and at.endswith(":00") else f"{days}d{at}"


def reminder_slot(now: float, day: int, at: str, since: float = 0.0, times: Any = None) -> Optional[tuple]:
    """此刻该发的提醒：``(运行时刻, 标签, 时段开始)``（epoch 秒，标签见 `remind_label`）。``times`` 是这条任务的提醒时刻
    （`remind_times` 的输入，空 = 默认）。不在任一时段、或时段开始早于 ``since``（打开提醒、换负责人、改运行日 / 时间、
    改提醒时刻、重新启用之前的时段不补发）时为 None。"""
    run = next_slot(now, day, at)
    run_date = datetime.fromtimestamp(run, BANGKOK).date()
    moments = []
    for item in remind_times(times):
        date = run_date - timedelta(days=item["days_before"])
        hour, minute = (int(part) for part in item["time"].split(":"))
        start = datetime(date.year, date.month, date.day, hour, minute, tzinfo=BANGKOK).timestamp()
        if start < run:
            moments.append((start, remind_label(item)))
    moments.sort()
    for (start, label), end in zip(moments, [start for start, _label in moments[1:]] + [run]):
        if start <= now < end:
            return None if since and start < since else (run, label, start)
    return None


def day_word(now: float, run_at: float) -> str:
    """'today' / 'tomorrow' / 'later'（两天及以后，文字写日期）：按发出那一刻算（停机后补发的照实写）。"""
    gap = (datetime.fromtimestamp(run_at, BANGKOK).date() - datetime.fromtimestamp(now, BANGKOK).date()).days
    return "today" if gap <= 0 else "tomorrow" if gap == 1 else "later"


def month_day(epoch: float) -> tuple:
    """曼谷的（月, 日）。"""
    moment = datetime.fromtimestamp(epoch, BANGKOK)
    return moment.month, moment.day


def clock(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, BANGKOK).strftime("%H:%M")


def stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, BANGKOK).strftime("%Y-%m-%d %H:%M")


def month_of(epoch: float) -> str:
    """曼谷的年-月：与小助手放结果表格的月份文件夹（`folder_plan.month_of`）同一写法。"""
    return datetime.fromtimestamp(epoch, BANGKOK).strftime("%Y-%m")


def time_left(now: float, run_at: float) -> tuple:
    """离开跑还有（天, 时, 分），向下取整。"""
    minutes = max(0, int(run_at - now)) // 60
    return minutes // 1440, minutes % 1440 // 60, minutes % 60


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()[:48]


def remind_key(owner: str, run_at: float, label: str, reader: str) -> str:
    """一条提醒的去重键：负责人 + 运行时刻 + 提醒时刻（标签）+ 读文件夹的电脑（同一人、同一运行时刻、同一提醒时刻、
    同一台电脑只发一次）。"""
    return "cowork.folder.remind:" + _digest([owner, int(run_at), label, str(reader or "")])


def brief_key(owner: str, runs: List[str]) -> str:
    """一份简报的去重键：负责人 + 这批运行记录。"""
    return "cowork.folder.brief:" + _digest([owner, sorted(runs)])


def reminder_groups(rows: List[Mapping]) -> List[Dict[str, Any]]:
    """同一负责人、同一运行时刻、同一提醒时刻、同一台读文件夹的电脑的任务合成一条（行带 owner / run_at / label / reader /
    title / name）：组内按任务名排，组按运行时刻、时段、负责人、电脑排。按电脑分开（2026-10-13）：这条提醒会被那台电脑
    同步取回弹出，不能带上别的电脑的任务名与文件夹路径。"""
    groups: Dict[tuple, List[Mapping]] = {}
    for row in rows:
        groups.setdefault((row["owner"], row["run_at"], row["label"], str(row.get("reader") or "")), []).append(row)
    ordered = sorted(groups.items(), key=lambda item: (item[0][1], item[0][2], item[0][0], item[0][3]))
    return [{"owner": owner, "run_at": run_at, "label": label, "reader": reader,
             "rules": sorted(members, key=lambda row: (str(row.get("title") or ""), str(row.get("name") or "")))}
            for (owner, run_at, label, reader), members in ordered]


def full_path(*parts: Any) -> str:
    """电脑上的完整路径：根目录显示名（小助手报的本机完整路径，可能是 `\\\\服务器\\共享`）+ 规则文件夹 + …，
    `\\` 分隔，空段跳过。"""
    pieces = []
    for index, part in enumerate(parts):
        piece = str(part or "").strip()
        piece = piece.rstrip("\\/") if index == 0 else piece.strip("\\/")
        if piece:
            pieces.append(piece)
    return "\\".join(pieces)


# ── 结果口径（任务列表、运行记录、完成简报同一个 `tally`，2026-10-12）──────────────────────────
# 推送只有三种结局：录入中 / 成功 / 失败，转人工就是失败（用户 2026-09-26，录入列表同一口径）。另有自动推送没做、
# 等人的「待处理」：要人选一次（choice）、人已经动过这张票（human）、任务停用或负责人无权（task_off）、
# 跟随公司当年账套时这张票的账套定不下来（account_set：取不到、多于一份、没有电脑够得到，T1）。
# 票在录入工作台删了（deleted）与成功一样算有了结论，不计数。
CHOICE_KINDS = ("party", "item", "account", "payment", "vat", "department", "receipt")
IN_FLIGHT = ("prepared", "pending", "leased")
COMPANION_OFFLINE = "cowork.folder.companion_offline"
ACCOUNT_SET_NONE = "cowork.folder.account_set_none"
ACCOUNT_SET_AMBIGUOUS = "cowork.folder.account_set_ambiguous"
ACCOUNT_SET_UNREACHABLE = "cowork.folder.account_set_unreachable"
ACCOUNT_SET_STOPS = (ACCOUNT_SET_NONE, ACCOUNT_SET_AMBIGUOUS, ACCOUNT_SET_UNREACHABLE)
# 自动推前判过（数字会计员第 3 期第 4 条）：CC 判断的 B 类在 L0 / L1 改成提议、没过核对的 C 类停下，都留给人在待处理里推
AUTOMATION_PROPOSAL = "automation:proposal"
AUTOMATION_REVIEW = "automation:review"
MOVING_CODES = {"insufficient_balance": "low_balance", COMPANION_OFFLINE: "offline", WAITING_PURCHASES: "waiting_purchases"}


def ticket_state(outcome: Any, status: Any) -> tuple:
    """一张票的（类别, 原因）：类别 pushed / stopped / failed / moving / deleted，原因只在 stopped 时有
    （``choice:<种类,…>`` / ``human`` / ``task_off`` / ``account_set`` / ``review``：自动推前判成提议或要人核对）。

    ``outcome`` 是自动推送记下的这张票的去向（`push.push_one` 的 sent / held / human / failed / missing，
    规则停用或负责人无权时 `pump` 记的 stopped）；``status`` 是票上现在的推送状态（结果签名里的 `last_push_status`，
    人之后在网页推了会跟着变）。"""
    if status == "success":
        return "pushed", ""
    if status == DELETED:
        return "deleted", ""
    if status in IN_FLIGHT:
        return "moving", ""
    if status == "manual":
        return "failed", ""
    outcome = outcome if isinstance(outcome, Mapping) else {}
    kind = outcome.get("status")
    if kind == "held":
        reason = str(outcome.get("reason") or "")
        if reason in ACCOUNT_SET_STOPS:
            return "stopped", "account_set"
        if reason in (AUTOMATION_PROPOSAL, AUTOMATION_REVIEW):
            return "stopped", "review"
        return "stopped", "choice:" + (reason.split(":", 1)[1] if reason.startswith("choice_needed:") else "")
    if kind == "human":
        return "stopped", "human"
    if kind == "stopped":
        return "stopped", "task_off"
    return "failed", ""


def moving_reason(state: Any, last_code: Any) -> str:
    """处理中的文件为什么还没走完（简报括号里写）：上传中、余额不足、电脑离线、等进货先录入；其余不写。"""
    if state == "registered":
        return "uploading"
    return MOVING_CODES.get(str(last_code or ""), "")


def _outcome(value: Any) -> Mapping:
    if isinstance(value, Mapping):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def tally(files: List[Mapping], now: datetime) -> Dict[str, Any]:
    """一组文件（行带 state / last_code / outcome_json / registered_at）的结果。按票计：已推送、待处理（停下等人）、
    失败（另加失败的文件）、录入中（算进处理中）；按文件计：不是发票、处理中（`moving`：24 小时前登记、一直没传完的不算）。
    ``now`` 与 `registered_at` 同为站点时区裸时间。"""
    result: Dict[str, Any] = {"files": 0, "pushed": 0, "stopped": 0, "failed": 0, "not_invoice": 0, "moving": 0,
                              "stop_reasons": {}, "choice_kinds": [], "moving_reasons": {}}
    kinds = set()
    for row in files:
        result["files"] += 1
        state = row.get("state")
        if state in ("not_invoice", "failed"):
            result[state] += 1
        elif state == "done":
            outcome = _outcome(row.get("outcome_json"))
            for item in outcome.get("_signature") or []:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                category, reason = ticket_state(outcome.get(item[0]), item[1])
                if category == "deleted":
                    continue
                result[category] += 1
                if category == "stopped":
                    head, _sep, detail = reason.partition(":")
                    result["stop_reasons"][head] = result["stop_reasons"].get(head, 0) + 1
                    kinds.update(detail.split(","))
        elif layout_needed(state, row.get("last_code")):
            result["stopped"] += 1
            result["stop_reasons"]["pos_layout"] = result["stop_reasons"].get("pos_layout", 0) + 1
        elif moving(state, row.get("registered_at"), now):
            result["moving"] += 1
            why = moving_reason(state, row.get("last_code"))
            if why:
                result["moving_reasons"][why] = result["moving_reasons"].get(why, 0) + 1
    result["choice_kinds"] = [kind for kind in CHOICE_KINDS if kind in kinds]
    return result


ACCOUNT_SETS_SHOWN = 5


def account_sets(files: List[Mapping]) -> List[Dict[str, Any]]:
    """跟随公司当年账套的任务（T1）：这一组文件里的票各推往了哪份账套（自动推送记下的 ``account_set`` /
    ``account_label``）：``[{"account_set_key", "account_label", "tickets"}]``，票多的在前，最多
    :data:`ACCOUNT_SETS_SHOWN` 份。固定账套的任务不记，回空表。"""
    found: Dict[str, Dict[str, Any]] = {}
    for row in files:
        for name, item in _outcome(row.get("outcome_json")).items():
            if name.startswith("_") or not isinstance(item, Mapping) or not item.get("account_set"):
                continue
            key = str(item["account_set"])
            entry = found.setdefault(key, {"account_set_key": key, "account_label": str(item.get("account_label") or key),
                                           "tickets": 0})
            entry["tickets"] += 1
    return sorted(found.values(), key=lambda item: (-item["tickets"], item["account_label"]))[:ACCOUNT_SETS_SHOWN]


def not_uploaded(runs: List[Mapping]) -> int:
    """这几次运行没传上的文件数：上传失败 + 还没写完（`still_writing:N`）的，减去同一份简报里补传运行传上的。"""
    missed = sent_later = 0
    for run in runs:
        note = str(run.get("note") or "")
        if note == "retry":
            sent_later += int(run.get("new_files") or 0)
            continue
        missed += int(run.get("upload_failed") or 0)
        waiting = note.split(":", 1)[1] if note.startswith("still_writing:") else ""
        missed += int(waiting) if waiting.isdigit() else 0
    return max(0, missed - sent_later)


# ── 完成简报（F3，2026-10-12）：一份 = 同一负责人 + 同一定时时刻（或同一批立即运行）+ 同一台电脑 ─────────────
# 最近一次运行开始、最后一份文件走完都满这么久才发（2026-10-13 由 10 分钟缩短：简报已按电脑分开，同一台电脑同一轮的
# 运行记录在下一次同步一起报上来，不再等别的电脑；留几分钟给补传与晚到的运行记录）
BRIEF_SETTLE = timedelta(minutes=3)
BRIEF_BATCH = timedelta(minutes=10)   # 同一负责人的立即运行相隔这么久之内算同一批（CC 一次点几个任务出一份简报）
BRIEF_MAX_WAIT = ORDER_MAX_WAIT + timedelta(hours=1)  # 最多等这么久，到时带「处理中」照发（存货销售最多等进货 12 小时）
BRIEF_LOOKBACK = timedelta(days=3)     # 更早的不再发


def slot_group(value: Any) -> str:
    """定时 / 补跑运行的简报分组键（`brief_group`）：所属定时时刻（站点时区裸时间）的 ISO 串，精确到秒。"""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, microsecond=0).isoformat()
    return str(value or "").strip()[:19].replace(" ", "T")


def group_of(run: Mapping) -> str:
    """一条运行记录归哪一份简报（`brief_group`，2026-10-13）：定时 / 补跑 = 所属时刻（`slot_group`），立即运行 = 同一负责人
    相隔 10 分钟之内的那一批里第一次运行的记录名，补传 = 跟它补的那次运行。加这个字段之前记下的运行没有它：有所属时刻
    按时刻，没有就自成一份（不把同一负责人的立即运行都并在一起）。"""
    group = str(run.get("brief_group") or "")
    if group:
        return group
    return slot_group(run.get("slot_at")) if run.get("slot_at") else str(run.get("name") or "")


def brief_groups(runs: List[Mapping], files_by_run: Mapping[str, List[Mapping]], now: datetime, *,
                 settle: timedelta = BRIEF_SETTLE, max_wait: timedelta = BRIEF_MAX_WAIT,
                 lookback: timedelta = BRIEF_LOOKBACK) -> tuple:
    """还没发简报的运行（行带 name / run_owner / connection / brief_group / slot_at / started_at / scanned_at，可带
    ``reader``）按（负责人, 简报分组 `group_of`, 读文件夹的电脑 ``reader``——读者编号「电脑|连接」，没有就是运行的
    ``connection``）分组，回 ``(ready, stale)``，时间都是站点时区裸时间。
    按电脑分开（2026-10-13）：简报会被那台电脑同步取回弹出，不能带上别的电脑的任务；同一个池里几台电脑各读各的
    （ADR-021 第 4 批）也各是各的。

    ready 每项 ``{"owner", "slot_at", "reader", "runs", "complete"}``，按最早一次开始排：都扫描完、文件（行带 state /
    registered_at / finished_at）都不在路上（`moving`）、最近一次开始与最后一份文件走完（`finished_at`）都满 ``settle``
    的是完整的；最早一次开始满 ``max_wait`` 的不完整也发。``slot_at`` 是组里的定时时刻（立即运行为 None）。stale 是
    不再发的运行名：最早一次开始超过 ``lookback``，或没有负责人、没有开始时间（排不进时间，留着只会每 5 分钟再看一遍）。"""
    groups: Dict[tuple, List[Mapping]] = {}
    for run in runs:
        groups.setdefault((run.get("run_owner") or "", group_of(run),
                           str(run.get("reader") or run.get("connection") or "")), []).append(run)
    ready, stale = [], []
    for (owner, _group, reader), members in groups.items():
        started = [run["started_at"] for run in members if run.get("started_at")]
        names = sorted(run["name"] for run in members)
        if not owner or not started or now - min(started) > lookback:
            stale += names
            continue
        rows = [row for name in names for row in files_by_run.get(name) or []]
        quiet = max(started + [row["finished_at"] for row in rows if row.get("finished_at")])
        complete = (all(run.get("scanned_at") for run in members)
                    and not any(moving(row.get("state"), row.get("registered_at"), now, row.get("last_code"))
                                for row in rows)
                    and now - quiet >= settle)
        if complete or now - min(started) >= max_wait:
            slot = next((run["slot_at"] for run in members if run.get("slot_at")), None)
            ready.append((min(started), {"owner": owner, "slot_at": slot, "reader": reader, "runs": names,
                                         "complete": complete}))
    ready.sort(key=lambda item: item[0])
    return [group for _at, group in ready], sorted(stale)


# ── 公司文件夹（ADR-020）：公司主档按电脑各绑一个单据文件夹，绑定只是指针（读者、根目录编号、相对路径） ──────────
# 状态不存，每次读时按电脑最近一次同步现算（`company_folders.view`）。
FOLDER_STATES = ("ok", "waiting", "offline", "missing", "folder_missing")
FOLDER_WAITING_SECONDS = 600   # 刚选好、电脑还没报上来的这段时间算「等待电脑」，过了算「找不到」
REPORTED_DEPTH = 3             # 电脑只报到 3 层下级文件夹：更深的子文件夹报不报都不算缺


def _reported(roots: List[Mapping], root: str) -> Optional[Mapping]:
    return next((row for row in roots or [] if isinstance(row, Mapping) and row.get("id") == root), None)


def folder_state(fresh: bool, roots: List[Mapping], root: str, sub: str, bound_seconds: float) -> str:
    """绑定的文件夹此刻的状态：电脑 2 小时没同步 → offline；根目录不在上报里 → 刚绑的 waiting、否则 missing；
    子文件夹不在上报的 folders 里（不分大小写，只看 3 层以内）→ folder_missing；其余 ok。"""
    if not fresh:
        return "offline"
    found = _reported(roots, root)
    if found is None:
        return "waiting" if bound_seconds <= FOLDER_WAITING_SECONDS else "missing"
    if sub and len(sub.split("\\")) <= REPORTED_DEPTH and "folders" in found:
        known = {str(name).casefold() for name in found.get("folders") or []}
        if sub.casefold() not in known:
            return "folder_missing"
    return "ok"


def folder_label(roots: List[Mapping], root: str, sub: str, stored: str) -> str:
    """显示名：优先用电脑最新上报的根目录显示名加上子文件夹，电脑没报就用绑定时记下的。"""
    found = _reported(roots, root)
    base = str(found.get("label") or "") if found else ""
    if not base:
        return stored or ""
    return "\\".join(part for part in (base.rstrip("\\"), sub) if part)
