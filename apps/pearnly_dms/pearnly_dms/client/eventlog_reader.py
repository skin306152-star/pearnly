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
* 计数：不传 `sdtamt` 时回 `dt::<总数>`（实测 `dt::230`）
* 一行 7 个 `<span>`：账号 / 佛历日期 / 时分秒 / 来源 IP / 模块 / 动作 / 被改记录标识
* 顺序：按流水号**从新到旧**（实测第 1 页 100 行流水号严格递减）

**没有"所有模块"这一个过滤值**（实测：不传 `ftd` 只回类别 1 的登录事件；`ftd="||2"`
不是全模块；`ftd="ทั้งหมด||2"` 回 `ndt::`）。所以只能按模块逐个查 —— 每个模块用它的
**事件总数**判断"有没有新变化"，没有变化就只花 1 个请求。

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

#: 计数响应（不传分页参数时）。
_COUNT = re.compile(r"dt::(0|[1-9][0-9]{0,8})\s*\Z")

#: 流水号 + 类别，例如 `831||2`。
_IDENTITY = re.compile(r"([1-9][0-9]{0,11})\|\|2\Z")

#: 我们认识的动作名（泰文原生）。其余动作照原样返回，由调用方判定。
ACTION_ADD = "เพิ่ม"
ACTION_EDIT = "แก้ไข"
ACTION_DELETE = "ลบ"
KNOWN_ACTIONS = frozenset((ACTION_ADD, ACTION_EDIT, ACTION_DELETE))

#: 一条记录的列数（账号/日期/时间/IP/模块/动作/被改记录）。
COLUMNS = ("account", "date", "time", "ip", "module", "action", "target")


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
    """只认原生行：`<div data-val="<seq>||2" ...>` 里恰好 7 个 `<span>`。

    形状不对立即置 `invalid`，绝不"跳过看不懂的行"——少一行就等于漏一条变更。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.records = []
        self.level = 0
        self.record = None
        self.record_level = 0
        self.in_span = False
        self.buffer = ""
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "div":
            self.level += 1
            if "data-val" in attributes:
                if (self.record is not None
                        or set(attributes) != {"data-val", "data-allo", "data-cf"}):
                    self.invalid = True
                self.record, self.record_level = [], self.level
        elif tag == "span":
            if self.record is None:
                # 行外的 span 不属于任何记录：出现即形状可疑。
                self.invalid = True
            else:
                self.in_span, self.buffer = True, ""

    def handle_data(self, data):
        if self.in_span:
            self.buffer += data

    def handle_endtag(self, tag):
        if tag == "span":
            if self.in_span and self.record is not None:
                self.record.append(self.buffer.strip())
            self.in_span = False
        elif tag == "div":
            if self.record is not None and self.level == self.record_level:
                self.records.append(self.record)
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


def count(client, module, *, limits=None, usage=None):
    """这个模块的事件总数。**不传分页参数** —— 传了就不回总数（实测）。"""
    limits = limits or EventLogLimits()
    usage = usage or Usage(limits)
    usage.requests += 1
    body = client._post_text(ENDPOINT, _request_params(module))
    head = _body_text(body)
    usage.note(body)
    if head.startswith("ndt::"):
        return 0, usage
    match = _COUNT.fullmatch(body.strip())
    if not match:
        _fail("unexpected count response")
    return int(match.group(1)), usage


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
    parser.feed(body)
    parser.close()
    # 统计原始属性个数：解析器漏掉一行（例如尾部被截断）会与记录数不等。
    if (parser.invalid or parser.level or parser.record is not None
            or len(parser.records) != len(re.findall(r"\bdata-val\s*=", body, re.I))):
        _fail("row shape changed")
    events = []
    for identity, values in zip(re.findall(r'<div data-val="([^"]*)"', body), parser.records):
        matched = _IDENTITY.fullmatch(identity)
        if matched is None or len(values) != len(COLUMNS):
            _fail("row shape changed")
        event = dict(zip(COLUMNS, values))
        if not all(event[key] for key in ("account", "date", "time", "module", "action")):
            _fail("row shape changed")
        if event["module"] != module:
            # 过滤器没被遵守 —— 继续会把别的模块的变化记到错的游标上。
            _fail("row module does not match the requested module")
        events.append(Event(seq=int(matched.group(1)), **event))
    return events, usage
