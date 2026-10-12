"""文件夹自动录入的纯规则（不连库、不依赖框架）：路径、时间、规则默认值、防重键、小助手报文的形状。

规则口径见 `docs/work/2026-10-06-cowork-folder-intake.md`：一个文件夹 = 一条规则 = 一家公司 + 一个年度账套 +
进项或销项 + 服务或存货；只收 PDF / 图片；结果文件夹（`RESULT_FOLDERS`）由系统生成、不扫描。

新旧任务两种口径（2026-10-12，`docs/work/2026-10-12-cc-closed-loop.md`）：新任务（规则上 `include_existing`，同步里
下发 `existing=1`）到点处理文件夹（含子文件夹）里本任务还没处理过的所有文件，包括建任务前已有的；老任务照旧只收
启用之后放进来的（小助手按创建时间拍底）。同步里缺 `existing` 或为 0 就按老任务办，新旧服务端与小助手任意搭配都不会多处理。

先后（F7）：同一写入连接、同一账套里，存货销售文件等存货进货先写进 Express 再准备推送（`waits_for_purchases`）。
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
    """小助手上报的本机允许读取的根目录（只报编号、显示名与下级文件夹名；1.4.4 起另报各文件夹的文件数，不报文件）。"""
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
            for item in folders[:MAX_FOLDERS]:
                try:
                    clean.add(folder(item))
                except ContractError:
                    continue
            share = str(row.get("share") or "").strip()
            report = {"id": root_id(row.get("id")), "label": text(row.get("label"), maximum=255),
                      "folders": sorted(clean - {""}),
                      # 网络共享（含映射盘换算回的 \\服务器\共享…）：跨电脑比对文件夹是否重叠用；本机盘为空
                      "share": share if share.startswith("\\\\") and len(share) <= 500 else ""}
            if isinstance(row.get("counts"), Mapping):
                report["counts"] = _folder_counts(row["counts"], clean)
                # 数到时间或条数上限还没数完：显示「N+」
                report["partial"] = row.get("partial") is True
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


def moving(state: Any, registered_at: Optional[datetime], now: datetime) -> bool:
    """这份文件算不算「处理中」（列表、简报同一口径；时间用库里的站点时区裸时间，与 `now_datetime()` 同口径）。"""
    if state not in UNFINISHED:
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
