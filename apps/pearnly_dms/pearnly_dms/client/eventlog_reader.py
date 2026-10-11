# -*- coding: utf-8 -*-
"""DMS 原生"使用记录"（`eventlog`）读取器：一条**变更流水**，只读、无框架。

为什么需要它：一个 DMS 被几十上百人共用，我们这边拿的是缓存。别人新增/改了客户、
车型、地址，缓存不会自己知道；全靠重扫既慢又浪费。DMS 自己为每次增删改记了一行
使用记录，于是"哪几条变了"可以直接问它，再**只补读被点名的那几条**。

协议（2026-09-25 在测试租户实测，与 `docs/work/2026-09-24-dms-snapshot-protocol.md` 一致）：

* 入口 `eventlog/component/showdata.php`
* 参数 `sd`（自由搜索）、`ftd=<模块>||<类别>`、`sdtamt`（每页条数）、`sdtpage`（第几页，从 1 起）
* 类别 1＝登录类事件，2＝模块事件（**我们要的是 2**）
* 返回：有货 `dt::` + 行 / 无货 `ndt::` / 被拒 `err::`
* 顺序：按流水号**从新到旧**（实测第 1 页 100 行流水号严格递减）

**2026-10-11 对方改版**（曼谷 10:43 改了 `eventlog/component/js/view.js`，新增"按日期删除使用记录"；
11:54 起两个租户同时变样，正式与测试 DMS 同一台服务器）：

* 一行从"一个 `<p>` 里 7 个 `<span>`"变成"每项一个 `<p>`"，顺序变成 账号 / 来源 IP / 佛历日期 / 时分秒 /
  模块 / 动作 / 被改记录标识；行标识从 `<流水号>||2` 变成 `<流水号>||`。两种形状都认。
* **不再回总数**：不传 `sdtamt` 时回的是最新一行，不是 `dt::<总数>`。
* **分页多给一行、页与页重叠**：`sdtamt=3&sdtpage=1` 回 4 行，第 2 页从第 4 行起（实测流水号
  991–988、988–985）。所以调用方一律按流水号去重，不按"这一页有几行"推断。

于是"有没有新变化"改看**最新一行的流水号**（`head`），不再比总数 —— 对方能按日期删旧记录之后，
总数本来也不可靠，流水号只增不减。

旧形状（2026-09-25 实测）：不传 `sdtamt` 时回 `dt::<总数>`；一行 7 个 `<span>`：账号 / 佛历日期 /
时分秒 / 来源 IP / 模块 / 动作 / 被改记录标识。

**没有"所有模块"这一个过滤值**（实测：不传 `ftd` 只回类别 1 的登录事件；`ftd="||2"`
不是全模块；`ftd="ทั้งหมด||2"` 回 `ndt::`）。所以只能按模块逐个查 —— 每个模块用它
**最新一行的流水号**判断"有没有新变化"，没有变化就只花 1 个请求。

**被改记录标识可能是空的**：实测确实存在没有标识的行。那不是"没变化"，是"看不出来
变了哪一条"——调用方必须回退到整组重读，绝不能当无事发生。所以本模块**只如实读出来**，
判断留给 `snapshots/change_sync`。

行形状、列数、流水号格式不符一律判失败：把残缺的一页当成"没有新变化"正是最难查的错。
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import math
import re

from pearnly_dms.contracts.client_base import DmsClientError


#: 原生列表端点（页面 `eventlog/view.php` 的取数口）。
ENDPOINT = "eventlog/component/showdata.php"

#: 类别 2 ＝ 模块事件（增删改）。类别 1 是登录/登出，不带来数据变化。
CATEGORY = "2"

#: 流水号 + 类别，例如 `831||2`；2026-10-11 改版后类别为空（`991||`）。
_IDENTITY = re.compile(r"([1-9][0-9]{0,11})\|\|2?\Z")

#: 我们认识的动作名（泰文原生）。其余动作照原样返回，由调用方判定。
ACTION_ADD = "เพิ่ม"
ACTION_EDIT = "แก้ไข"
ACTION_DELETE = "ลบ"
KNOWN_ACTIONS = frozenset((ACTION_ADD, ACTION_EDIT, ACTION_DELETE))

#: 一条记录的列数（账号/日期/时间/IP/模块/动作/被改记录）—— 旧形状 `<span>` 的顺序。
COLUMNS = ("account", "date", "time", "ip", "module", "action", "target")
#: 2026-10-11 改版后每项一个 `<p>` 的顺序。
P_COLUMNS = ("account", "ip", "date", "time", "module", "action", "target")


@dataclass(frozen=True)
class EventLogLimits:
    """单次读取的上限。任何一项超限都判失败，不返回残缺页。"""

    page_size: int = 200
    max_pages: int = 20
    max_response_bytes: int = 8 * 1024 * 1024
    max_seconds: float = 20.0

    def __post_init__(self):
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value <= 0 for value in self.__dict__.values()):
            raise ValueError("Event log limits must be positive")
        if not 1 <= self.page_size <= 500:
            raise ValueError("Event log page size outside the native bound")


def _fail(reason):
    # 绝不把响应正文、HTML 或凭据放进错误与日志。
    raise DmsClientError("DMS usage log incomplete: " + reason, "ERR_DMS_SNAPSHOT_INCOMPLETE")


@dataclass(frozen=True)
class Event:
    """流水里的一行。`seq` 是单调流水号，`target` 是被改记录的原生标识（可能为空）。"""

    seq: int
    account: str
    date: str
    time: str
    ip: str
    module: str
    action: str
    target: str

    @property
    def names_record(self):
        """能否指向具体记录。空就不行 —— 那时只能整组重读。"""
        return bool(self.target) and self.action in KNOWN_ACTIONS


class _EventRows(HTMLParser):
    """只认原生行：`<div data-val="<seq>||2" ...>`（改版后 `<seq>||`）里恰好 7 项。

    旧形状 7 项是 `<span>`，改版后每项一个 `<p>`（`<p>` 里没有 `<span>`）；同一行两种混着出现、
    行外出现 `<span>`/`<p>` 都判形状不对。形状不对立即置 `invalid`，绝不"跳过看不懂的行"——
    少一行就等于漏一条变更。`records` 里每行是 `(列名顺序, 值)`。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.records = []
        self.level = 0
        self.record = None
        self.record_level = 0
        self.spans, self.paragraphs = [], []
        self.in_span = self.in_p = False
        self.p_has_span = False
        self.buffer = self.p_buffer = ""
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "div":
            self.level += 1
            if "data-val" in attributes:
                if (self.record is not None
                        or set(attributes) != {"data-val", "data-allo", "data-cf"}):
                    self.invalid = True
                self.record, self.record_level = True, self.level
                self.spans, self.paragraphs = [], []
        elif tag in ("span", "p"):
            if self.record is None or self.in_span:
                # 行外的 span / p 不属于任何记录；span 里再套标签也不是原生形状。
                self.invalid = True
            elif tag == "span":
                self.in_span, self.buffer = True, ""
                self.p_has_span = True
            elif self.in_p:
                self.invalid = True
            else:
                self.in_p, self.p_has_span, self.p_buffer = True, False, ""

    def handle_data(self, data):
        if self.in_span:
            self.buffer += data
        elif self.in_p:
            self.p_buffer += data
        elif self.record is None and data.strip():
            # 行外有字（例如旧的 `dt::230` 总数）：不是一页行，不能当成"没有记录"。
            self.invalid = True

    def handle_endtag(self, tag):
        if tag == "span":
            if self.in_span and self.record is not None:
                self.spans.append(self.buffer.strip())
            self.in_span = False
        elif tag == "p":
            if self.in_p and self.record is not None:
                if self.p_has_span:
                    if self.p_buffer.strip():
                        self.invalid = True   # 旧形状的 `<p>` 只装 span，自己不该有字
                else:
                    self.paragraphs.append(self.p_buffer.strip())
            self.in_p = False
        elif tag == "div":
            if self.record is not None and self.level == self.record_level:
                if self.spans and self.paragraphs:
                    self.invalid = True
                self.records.append((COLUMNS, self.spans) if self.spans
                                    else (P_COLUMNS, self.paragraphs))
                self.record = None
            self.level -= 1
            if self.level < 0:
                self.invalid = True


class Usage:
    """一次读取的用量（请求数 + 字节数），用于把单轮同步限制在预算内。"""

    def __init__(self, limits):
        self.limits = limits
        self.requests = 0
        self.bytes = 0

    def note(self, body):
        size = len(str(body or "").encode("utf-8"))
        self.bytes += size
        if size > self.limits.max_response_bytes:
            _fail("response exceeds the byte bound")


def _request_params(module, *, page_number=None, size=None):
    if not isinstance(module, str) or not module or module != module.strip() or len(module) > 100:
        _fail("invalid module name")
    if any(character in module for character in "|&?#"):
        _fail("invalid module name")
    params = {"sd": "", "ftd": module + "||" + CATEGORY}
    if page_number is not None:
        params["sdtamt"] = str(size)
        params["sdtpage"] = str(page_number)
    return params


def _body_text(body):
    if not isinstance(body, str):
        _fail("non-text response")
    head = body.lstrip()
    if head.startswith("err::"):
        _fail("the usage log refused the read")
    # `ndt::` 是**正常**的"没有数据"，不是失败：当失败会让"这个模块一条都没有"
    # 与"读不到"长得一样，那正是最难查的错。
    if not head.startswith(("dt::", "ndt::")):
        _fail("unexpected list response")
    return head


def head(client, module, *, limits=None, usage=None):
    """这个模块最新一行的流水号（没有任何记录为 0）。返回 `(流水号, 用量)`。

    取代原先的"事件总数"（2026-10-11 对方不再回总数）：流水号只增不减，对方按日期删旧记录
    也不影响它。只要 1 页、每页 1 行（对方会多给一行，取最大的那个）。
    """
    limits = limits or EventLogLimits()
    events, usage = page(client, module, page_number=1,
                         limits=EventLogLimits(page_size=1, max_pages=1,
                                               max_response_bytes=limits.max_response_bytes,
                                               max_seconds=limits.max_seconds),
                         usage=usage or Usage(limits))
    return (max(event.seq for event in events) if events else 0), usage


def page(client, module, *, page_number=1, limits=None, usage=None):
    """一页流水（从新到旧）。`ndt::` 或没有行都返回 `[]`。"""
    limits = limits or EventLogLimits()
    usage = usage or Usage(limits)
    if page_number < 1 or page_number > limits.max_pages:
        _fail("page number outside the native bound")
    usage.requests += 1
    body = client._post_text(ENDPOINT, _request_params(module, page_number=page_number,
                                                       size=limits.page_size))
    head = _body_text(body)
    usage.note(body)
    if head.startswith("ndt::"):
        return [], usage
    parser = _EventRows()
    parser.feed(head[len("dt::"):])
    parser.close()
    # 统计原始属性个数：解析器漏掉一行（例如尾部被截断）会与记录数不等。`dt::` 却一行都没有也不是原生形状。
    if (parser.invalid or parser.level or parser.record is not None or not parser.records
            or len(parser.records) != len(re.findall(r"\bdata-val\s*=", body, re.I))):
        _fail("row shape changed")
    events = []
    for identity, (columns, values) in zip(re.findall(r'<div data-val="([^"]*)"', body),
                                           parser.records):
        matched = _IDENTITY.fullmatch(identity)
        if matched is None or len(values) != len(columns):
            _fail("row shape changed")
        event = dict(zip(columns, values))
        if not all(event[key] for key in ("account", "date", "time", "module", "action")):
            _fail("row shape changed")
        if event["module"] != module:
            # 过滤器没被遵守 —— 继续会把别的模块的变化记到错的游标上。
            _fail("row module does not match the requested module")
        events.append(Event(seq=int(matched.group(1)), **event))
    if any(newer.seq <= older.seq for newer, older in zip(events, events[1:])):
        # 原生是严格从新到旧；乱序时"翻到游标为止"的判断就不成立。
        _fail("rows are not newest first")
    return events, usage
