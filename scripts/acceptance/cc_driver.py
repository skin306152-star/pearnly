"""CC 对话驱动（验收测试标准 §3.4、§5.5）：场景只对接口 `Conversation` 写，用哪一层由场景登记的 `driver` 定。

    talk = open_conversation(scenario.get("driver", "real"), product="cowork", mode="direct", company=…, ledger=…)
    talk.attach([文件…]); talk.say("录一下"); talk.click("plan", "write"); talk.stop(); talk.wait_idle()
    record = talk.record()     # cc_lines.Record：对话、工具调用、卡片、喊停；写入（writes）由场景用账本前后对比补上
    talk.cost()                # 这段对话到现在花了多少美元（场景每一步后 ctx.spend）

两层：
- `lab-fake`（`LabConversation`）：本机回归实验栈（`infrastructure/compose/regression-lab.yml`）站点上的真对话，模型换成假智能体
  （`scripts/flows/lab_agents.py`：回显「[lab-agent] + 原文前 40 字」，原文带 `[lab-ask]{…}[/lab-ask]` 出提问卡、
  `[lab-tool]{"name":…,"input":…}[/lab-tool]` 调指定工具）。费用恒为 0。走站点的 HTTP 接口（`pearnly_core.assistant.api`：
  create / upload / send / confirm / interrupt / run_tool / events / delete），用 Desk 会话 sid（`scripts/smoke/session.py`）登录；
  页面替用户做的那几件也照做（附件说明拼进消息、回复中再说的话排到这一轮结束、卡片点「允许」带作答、推送线程没接手时提醒执行）。
  只认假智能体：bench 容器的 ANTHROPIC_BASE_URL 不是指向实验栈的假智能体就不连（不花真模型的钱）。
  调用方要先占住 `lab:<栈名>`（`locks.hold`；验收场景写进 resources），没占住、别人占着都不连，记「没测到」。
- `real`（`RealConversation`）：真模型。走的是同一套站点接口，只是站点换成测试机实验栈 / 本机实验栈上接了真模型的那个站点
  （环境变量 PEARNLY_ACC_REAL_BASE / _SITE / _USER / _TARGET，没配就抛 Unmet 记「没测到」）；Desk 会话经共用件冒烟的
  `scripts/smoke/run.py::Remote` 在站点容器里建；费用按会话事件里的模型用量折算（同 `scripts/e2e/stress.py::usage_usd`），
  场景每一步后 `ctx.spend`，到额即停。生产一律不连。**11-13 写好、只有离线单测，没连过真站点。**

`record_from_events(events)` 是纯函数：站点 `assistant.api.events` 读回的会话事件 → `cc_lines.Record`（离线单测
`tests/test_acceptance_cc_driver.py`）。事件形状的依据：引擎事件（user.message / agent.message / agent.custom_tool_use /
user.custom_tool_result / user.interrupt / session.status_*）照 `scripts/flows/lab_agents.py` 与
`apps/pearnly_core/pearnly_core/assistant/contracts.py`；本站事件 `pearnly.tool_run`（`contracts.tool_run_event`）、
`pearnly.write_plan`（`write_plan.event`）、`pearnly.stopped`（`stopped.event`）、`pearnly.answer`（`contracts.answer_event`）；
附件说明的写法照 `frontend/assistant/src/lib/managed-agents/events.ts` 的 `attachmentNote`。

验证状态（11-12）：`record_from_events`、`ask_answers`、工厂两个分支、没有实验栈 / 没占锁 / 指向真模型时记「没测到」——只有
离线单测。`LabConversation` 连实验栈**还没实跑过**（当晚实验栈一直被发布窗口的全套流程占着）：HTTP 调用、CSRF、轮询节奏都未经实测。
"""
from __future__ import annotations

import atexit
import importlib.util
import json
import mimetypes
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _lib(name: str):
    """同目录的共用模块：已加载就用那一份（运行器先加载 acc_errors，Unmet 要是同一个类）。"""
    import sys

    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


class _Late:
    """`raise Unmet(...)` 时才取 acc_errors 里的类：运行器会重新加载 acc_errors，驱动先加载时绑定的旧类它认不出。"""

    def __init__(self, name: str):
        self.name = name

    def __call__(self, *args):
        import sys

        return getattr(sys.modules.get("acc_errors") or _lib("acc_errors"), self.name)(*args)


Unmet, Infra = _Late("Unmet"), _Late("Infra")

# Opus 5.5 每百万 token 美元（输入 / 输出 / 写缓存 / 读缓存）；用量取会话事件 span.model_request_end（同 scripts/e2e/stress.py 的 PRICE）
PRICE = {"input_tokens": 5.0, "output_tokens": 25.0, "cache_creation_input_tokens": 6.25, "cache_read_input_tokens": 0.5}
REAL_TARGETS = ("local", "lab")  # 本机实验栈 / 测试机实验栈；生产不连
# 「写入」快捷回复的字（frontend/assistant/src/i18n.ts 的 recordNow）：点了等同打出这句发出（thread.tsx RecordQuickReply）
RECORD_NOW = {"en": "Write", "zh": "写入", "th": "บันทึก", "ja": "書き込む"}
TOOL_USES = ("agent.custom_tool_use", "agent.tool_use", "agent.mcp_tool_use")
RAN = ("running", "done", "error")
ASK_TOOL = "ask_user"
# 卡片挂着时用户直接打字（sessions.send）：站点先发 user.interrupt 再发这句，同一个请求里；这不是用户喊停
TYPED_GAP = 2.0
# frontend/assistant/src/lib/managed-agents/events.ts 的 splitAttachmentNote
ATTACHED = re.compile(r"(?:^|\n\n)\[Attached files?, mounted read-only in the sandbox:\n((?:- .+ → .+\n?)+)\]$")


# ── 接口 ──────────────────────────────────────────────────────────────────────
class Conversation:
    """场景只对这个接口写。`click(card, choice)`：card 是 plan / ask / allow；plan 的 choice 是 write，ask 的是选项文字
    （或第几个选项、或「其他」里打的字），allow 的是 allow / deny。点不到（卡片没出）回 False，不抛。"""

    target = ""

    def __init__(self, product: str, mode: str, company: str = "", ledger: str = ""):
        self.product, self.mode, self.company, self.ledger = product, mode, company, ledger

    def attach(self, files: list) -> None:
        raise NotImplementedError

    def say(self, text: str) -> None:
        raise NotImplementedError

    def click(self, card: str, choice) -> bool:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def wait_idle(self, seconds: int = 180) -> None:
        raise NotImplementedError

    def record(self):
        raise NotImplementedError

    def cost(self) -> float:
        raise NotImplementedError

    def evidence(self) -> dict:
        """失败时留的服务端原值（标准 §5.3）：这段对话在站点上的编号与读回的全部会话事件原文。调用后这段对话不再被收尾删掉。"""
        raise NotImplementedError


def open_conversation(target: str, product: str, mode: str, company: str = "", ledger: str = "", **options) -> Conversation:
    """按场景登记的 `driver` 开一段对话：lab-fake = 本机实验栈 + 假智能体；real = 真模型（没配站点时抛 Unmet）。"""
    if target == "real":
        return RealConversation(product, mode, company, ledger, **options)
    if target == "lab-fake":
        return LabConversation(product, mode, company, ledger, **options)
    raise ValueError(f"不认识的对话驱动 {target!r}（只有 lab-fake / real）")


# ── 会话事件 → 对话记录（纯函数）───────────────────────────────────────────────
def epoch(value) -> float | None:
    """RFC3339（引擎的 processed_at，`Z` 结尾；本站事件同样换算成 UTC）→ 秒。读不出回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text(event: dict) -> str:
    return "".join(block.get("text", "") for block in event.get("content") or [] if isinstance(block, dict) and block.get("type") == "text")


def split_attachments(text: str) -> tuple[str, list[str]]:
    """用户消息 = 用户的话 + 附件说明（页面拼的）→ (用户的话, [文件名])。"""
    match = ATTACHED.search(text)
    if not match:
        return text, []
    names = [line[2:].split(" → ", 1)[0] for line in match.group(1).strip().splitlines() if line.startswith("- ")]
    return text[:match.start()], names


def _answer_of(result_text: str):
    """提问卡交回模型的结果（ask.result 的 JSON，前面可能带 reply_language）→ (摘要, 逐题作答)。摘要是第一题选的选项
    （多个用「、」连），没选写的是「其他」里的字，跳过的是 skipped。"""
    try:
        rows = json.loads(result_text).get("answers") or []
    except (TypeError, ValueError, AttributeError):
        return None, []
    if not rows:
        return None, []
    first = rows[0]
    summary = "、".join(first.get("labels") or []) or first.get("other") or ("skipped" if first.get("skipped") else "")
    return summary, rows


def record_from_events(events: list):
    """站点读回的会话事件（`assistant.api.events`，可以是多次增量拼起来的、有重复）→ `cc_lines.Record`。

    - turns：user.message（去掉附件说明，文件名放 files）、agent.message、pearnly.stopped（被停掉的半段，stopped=True）。
    - tools：真的执行了的调用（at 是执行的时刻：要「允许」的取点允许那一刻）；被拒绝、被作废、还在等的不算执行。
    - cards：ask = 调 ask_user；allow = 本站登记成等「允许」（pearnly.tool_run awaiting_approval）或引擎那边要确认
      （user.tool_confirmation）；plan = 写入计划（pearnly.write_plan），开始了（started）算用户点头，answered_at 是开始的时刻。
    - stops：user.interrupt 的时刻；卡片挂着时用户打字、站点先停那一轮（其后 TYPED_GAP 秒内有用户消息）不算喊停。
    - writes 留空（实验栈没有账本；真写场景由账本前后对比补上）。
    注意：本站执行记录与写入计划在整段重放里只带此刻的状态；要分出「等过允许」「计划等过点头」，要用对话进行中逐次增量读到的事件
    （LabConversation 就是这么攒的）。"""
    lines = _lib("cc_lines")
    seen, unique = set(), []
    for event in events or []:
        key = event.get("id")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(event)
    ordered = [event for _, event in sorted(enumerate(unique), key=lambda pair: (epoch(pair[1].get("processed_at")) or 0.0, pair[0]))]

    runs: dict[str, list] = {}       # 调用编号 → [(状态, 时刻)]
    answers: dict[str, tuple] = {}   # pearnly.answer：调用编号 → (allow / deny, 时刻)
    results: dict[str, tuple] = {}   # user.custom_tool_result：调用编号 → (原文, 时刻, 出错)
    confirms: dict[str, tuple] = {}  # user.tool_confirmation（引擎那边要确认的）：调用编号 → (allow / deny, 时刻)
    plans: dict[str, list] = {}
    turns, calls, interrupts = [], [], []
    for event in ordered:
        kind, at = event.get("type"), epoch(event.get("processed_at"))
        if kind == "user.message":
            said, files = split_attachments(_text(event))
            turns.append({"role": "user", "text": said, "at": at, "files": files})
        elif kind == "agent.message":
            turns.append({"role": "assistant", "text": _text(event), "at": at})
        elif kind == "pearnly.stopped":
            turns.append({"role": "assistant", "text": _text(event), "at": at, "stopped": True})
        elif kind in TOOL_USES:
            calls.append(event)
        elif kind == "pearnly.tool_run":
            runs.setdefault(event.get("tool_use_id"), []).append((event.get("status"), at))
        elif kind == "pearnly.answer":
            answers.setdefault(event.get("tool_use_id"), (event.get("result"), at))
        elif kind == "user.custom_tool_result":
            results.setdefault(event.get("custom_tool_use_id"), (_text(event), at, bool(event.get("is_error"))))
        elif kind == "user.tool_confirmation":
            confirms.setdefault(event.get("tool_use_id"), (event.get("result"), at))
        elif kind == "user.interrupt":
            interrupts.append(at)
        elif kind == "pearnly.write_plan":
            plans.setdefault(event.get("plan"), []).append((event.get("status"), at, event.get("count")))

    tools, cards = [], []
    for call in calls:
        use_id, name, at = call.get("id"), call.get("name") or "", epoch(call.get("processed_at"))
        history = runs.get(use_id, [])
        states = [state for state, _ in history]
        tool = {"id": use_id, "name": name, "at": at, "input": call.get("input") or {}, "status": states[-1] if states else None}
        if name == ASK_TOOL and call.get("type") == "agent.custom_tool_use":
            questions = (call.get("input") or {}).get("questions") or []
            headers = [str(q.get("header") or "") for q in questions if isinstance(q, dict)]
            card = {"kind": "ask", "id": use_id, "at": at, "topic": headers[0] if headers else "", "headers": headers,
                    "answer": None, "answered_at": None, "answers": []}
            if "cancelled" not in states and use_id in results and not results[use_id][2]:
                card["answer"], card["answers"] = _answer_of(results[use_id][0])
                card["answered_at"] = results[use_id][1]
            cards.append(card)
            tools.append(tool)
            continue
        if "awaiting_approval" in states or use_id in answers or use_id in confirms:
            later = [(state, when) for state, when in history if state in (*RAN, "denied")]
            if use_id in answers:
                answer, answered_at = answers[use_id]
            elif use_id in confirms:
                answer, answered_at = confirms[use_id]
            elif later:
                answer, answered_at = ("allow" if later[0][0] in RAN else "deny"), later[0][1]
            else:
                answer, answered_at = None, None  # 作废（停了 / 打字代替）或还在等
            cards.append({"kind": "allow", "id": use_id, "at": at, "topic": name, "answer": answer, "answered_at": answered_at})
            if answer == "allow":
                tools.append({**tool, "at": max(value for value in (at, answered_at) if value is not None)})
            continue
        if states and all(state in ("cancelled", "denied") for state in states):
            continue  # 没执行
        tools.append(tool)

    for plan, history in plans.items():
        started = next((when for state, when, _ in history if state == "started"), None)
        cards.append({"kind": "plan", "id": plan, "at": history[0][1], "topic": plan, "count": history[-1][2],
                      "answer": "write" if started is not None else None, "answered_at": started,
                      "status": history[-1][0]})

    def card_open(card, moment) -> bool:
        return card["kind"] in ("ask", "allow") and (card["at"] or 0) <= moment and (card["answered_at"] is None or card["answered_at"] > moment)

    stops = []
    for moment in interrupts:
        if moment is None:
            continue
        typed = any(turn["role"] == "user" and turn["at"] is not None and moment <= turn["at"] <= moment + TYPED_GAP for turn in turns)
        if typed and any(card_open(card, moment) for card in cards):
            continue
        stops.append(moment)
    cards.sort(key=lambda card: card["at"] or 0.0)
    tools.sort(key=lambda tool: tool["at"] or 0.0)
    return lines.Record(turns=turns, tools=tools, cards=cards, writes=[], stops=stops)


def usage_usd(events: list) -> float:
    """会话事件里的模型用量 → 美元（只算 CC 引擎的模型调用；识别费用不在会话事件里，场景另记）。"""
    total = 0.0
    for event in events or []:
        if event.get("type") == "span.model_request_end":
            usage = event.get("model_usage") or {}
            total += sum(int(usage.get(key) or 0) * price for key, price in PRICE.items()) / 1_000_000
    return round(total, 4)


def ask_answers(questions: list, choice) -> list:
    """提问卡的作答（页面交给 assistant.api.confirm 的 answers，按题目顺序 [{labels} | {other} | {skipped}]）：
    choice 是列表时逐题对应；是一项时给第一题，其余题选第一个选项（同卡片默认选中推荐项、推荐项排第一）。
    一项可以是选项文字、第几个选项（从 1 起的整数）、None（跳过），对不上选项的文字算「其他」里打的字。"""
    picks = list(choice) if isinstance(choice, (list, tuple)) else [choice]
    out = []
    for index, question in enumerate(questions):
        labels = [str(option.get("label")) for option in question.get("options") or [] if isinstance(option, dict)]
        pick = picks[index] if index < len(picks) else (labels[0] if labels else None)
        if pick is None:
            out.append({"skipped": True})
        elif isinstance(pick, int) and not isinstance(pick, bool) and 1 <= pick <= len(labels):
            out.append({"labels": [labels[pick - 1]]})
        elif str(pick) in labels:
            out.append({"labels": [str(pick)]})
        else:
            out.append({"labels": [], "other": str(pick)})
    return out


# ── 本机实验栈 + 假智能体 ─────────────────────────────────────────────────────
class LabConversation(Conversation):
    """本机回归实验栈站点上的一段直写对话（假智能体，零费用）。见文件头。环境变量可改：
    PEARNLY_FLOWS_LAB（栈名，默认 pearnly-regression-lab）、PEARNLY_ACC_LAB_SITE（站点，默认 <产品>-new.regression.localhost）、
    PEARNLY_ACC_LAB_USER（默认 admin@<产品>.regression）、PEARNLY_ACC_DOCKER（docker 命令）。"""

    target = "lab-fake"
    POLL = 0.6
    CARD_WAIT = 90

    def __init__(self, product: str, mode: str, company: str = "", ledger: str = "", stack: str = "", site: str = "",
                 user: str = "", keep: bool = False):
        super().__init__(product, mode, company, ledger)
        if mode != "direct":
            raise Unmet(f"lab-fake 只接直写对话；{mode} 在实验栈上没接（代录要电脑端）")
        self.stack = stack or os.environ.get("PEARNLY_FLOWS_LAB", "pearnly-regression-lab")
        self.site = site or os.environ.get("PEARNLY_ACC_LAB_SITE") or f"{product}-new.regression.localhost"
        self.user = user or os.environ.get("PEARNLY_ACC_LAB_USER") or f"admin@{product}.regression"
        self.docker = os.environ.get("PEARNLY_ACC_DOCKER", "docker")
        self.container = f"{self.stack}-bench-1"
        self.keep = keep
        self.events: list[dict] = []
        self._ids: set = set()
        self._anchor = None
        self._files: list[dict] = []
        self._lang = "zh"
        self._reminded: set = set()
        self._csrf = ""
        self.sid = self.session_id = ""
        self.base = self._ready()
        self.sid = self._desk_session()
        self._begin()

    def _begin(self) -> None:
        atexit.register(self.close)
        made = self._post("create", {"project": self.company} if self.company else {})
        self.session_id = made.get("id") or ""
        if not self.session_id:
            raise Infra(f"站点 {self.site} 建对话没回编号：{made}")

    # 前置：栈在跑、指向假智能体、本进程占着 lab 锁、站点通
    def _run(self, *args, timeout=60) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self.docker, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise Unmet(f"没有 docker（{self.docker}）：本机没有实验栈") from None
        except subprocess.TimeoutExpired:
            raise Infra(f"docker {args[0]} 超过 {timeout} 秒没回") from None

    def _ready(self) -> str:
        for name in (self.container, f"{self.stack}-agents-1"):
            state = self._run("inspect", "-f", "{{.State.Running}}", name)
            if state.returncode or state.stdout.strip() != "true":
                raise Unmet(f"实验栈 {self.stack} 没在跑（{name}）：lab-fake 不自己起栈")
        env = self._run("inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", self.container).stdout
        base = next((line.split("=", 1)[1] for line in env.splitlines() if line.startswith("ANTHROPIC_BASE_URL=")), "")
        if not re.fullmatch(r"https?://agents:\d+/?", base):
            raise Unmet(f"实验栈 {self.stack} 的 ANTHROPIC_BASE_URL 不是假智能体（{base or '没设'}）：不连，免得花真模型的钱")
        locks = _lib("locks")
        resource = f"lab:{self.stack}"
        try:
            handle = locks._open(resource)
        except locks.Busy as busy:
            record = busy.record or {}
            mine = str(record.get("pid")) == str(os.getpid()) or (
                record.get("mode") == "lease" and record.get("holder") == os.environ.get("PEARNLY_LOCK_HOLDER"))
            if not mine:
                raise Unmet(f"实验栈被占用，不连：{busy}") from None
        else:
            lease = locks._lease_of(locks._read(handle))
            handle.close()
            if lease:
                raise Unmet(f"实验栈被占用，不连：{locks.describe(resource, lease)}")
            raise Unmet(f"没占住 {resource}：先 locks.hold（验收场景写进 resources）再开对话")
        port = self._run("port", self.container, "8000").stdout.strip().splitlines()
        address = port[0].strip() if port else ""
        if not address:
            raise Unmet(f"实验栈 {self.container} 没有对外的网页端口")
        base_url = "http://" + address.replace("0.0.0.0", "127.0.0.1")
        try:
            self._http("GET", base_url + "/api/method/ping", headers={"Host": self.site})
        except (Infra, RuntimeError) as error:
            raise Unmet(f"实验栈站点 {self.site} 连不上：{error}") from None
        return base_url

    def _desk_session(self) -> str:
        out = self._run("exec", "-w", "/home/frappe/frappe-bench/sites", self.container, "../env/bin/python",
                        "/workspace/scripts/smoke/session.py", self.site, self.user, timeout=120)
        sid = next((line.split()[1] for line in out.stdout.splitlines() if line.startswith("SID ")), "")
        if not sid:
            raise Unmet(f"实验栈站点 {self.site} 给 {self.user} 建不了会话：{(out.stderr or out.stdout)[-300:]}")
        return sid

    # HTTP
    def _http(self, method: str, url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 60):
        request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", "replace")
            if error.code >= 500:
                raise Infra(f"{method} {url.split('?')[0]} → {error.code}：{text[-300:]}") from None
            raise RuntimeError(f"{method} {url.split('?')[0]} → {error.code}：{text[-400:]}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise Infra(f"{method} {url.split('?')[0]} 连不上：{error}") from None
        return json.loads(body or b"{}") if body[:1] in (b"{", b"[") else {}

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"Host": self.site, "Cookie": f"sid={self.sid}", "Accept": "application/json", **(extra or {})}
        if self._csrf:
            headers["X-Frappe-CSRF-Token"] = self._csrf
        return headers

    def _call(self, method: str, name: str, fields: dict, body: bytes | None = None, content_type: str = ""):
        url = f"{self.base}/api/method/pearnly_core.assistant.api.{name}"
        if method == "GET":
            return self._http("GET", url + "?" + urllib.parse.urlencode(fields), headers=self._headers()).get("message")
        data = body if body is not None else urllib.parse.urlencode(fields).encode()
        kind = content_type or "application/x-www-form-urlencoded"
        try:
            return self._http("POST", url, data, self._headers({"Content-Type": kind})).get("message")
        except RuntimeError as error:
            if "CSRFTokenError" not in str(error) or self._csrf:
                raise
            # 这个会话打开过页面才有 CSRF 令牌：照页面从 /assistant 取一次再发
            page = urllib.request.urlopen(urllib.request.Request(f"{self.base}/assistant", headers=self._headers()), timeout=60)
            found = re.search(r"csrf_token\s*[=:]\s*[\"']([^\"']+)", page.read().decode("utf-8", "replace"))
            if not found:
                raise
            self._csrf = found.group(1)
            return self._http("POST", url, data, self._headers({"Content-Type": kind})).get("message")

    def _post(self, name: str, fields: dict):
        return self._call("POST", name, fields) or {}

    # 读事件（攒起来：本站执行记录与写入计划的中间状态只有逐次增量读得到）
    def poll(self) -> dict:
        fields = {"session_id": self.session_id, "status": 1}
        if self._anchor:
            fields["after"] = self._anchor
        page = self._call("GET", "events", fields) or {}
        if page.get("partial"):
            return {}
        for event in page.get("events") or []:
            key = event.get("id")
            if key and key in self._ids:
                continue
            if key:
                self._ids.add(key)
            self.events.append(event)
            if key and not str(event.get("type") or "").startswith("pearnly.") and not str(key).startswith("pearnly_gap_"):
                self._anchor = key
        return page.get("run") or {}

    def _busy(self, run: dict) -> bool:
        """模型在工作（页面显示停止键）：在跑，或停着等的调用里有本站正在执行的（挂起等后台结论的不算）。"""
        if run.get("status") == "running":
            return True
        if run.get("status") == "requires_action":
            parked = set(run.get("parked") or [])
            settled = set(run.get("awaiting_approval") or []) | set(run.get("waiting") or [])
            return bool(parked - settled)
        return False

    def _remind(self, run: dict) -> None:
        """推送线程没接手的自定义工具调用：照页面提醒站点执行一次（`assistant.api.run_tool`，兜底）。"""
        known = {event.get("tool_use_id") for event in self.events if event.get("type") == "pearnly.tool_run"}
        for use_id in run.get("parked") or []:
            if use_id not in known and use_id not in self._reminded:
                self._reminded.add(use_id)
                try:
                    self._post("run_tool", {"session_id": self.session_id, "tool_use_id": use_id})
                except RuntimeError:
                    pass  # 推送线程已先执行 / 不是自定义工具：同页面，不当失败

    def wait_idle(self, seconds: int = 180) -> None:
        deadline, since = time.monotonic() + seconds, time.monotonic()
        while True:
            run = self.poll()
            if run and not self._busy(run):
                return
            if run.get("status") == "requires_action" and time.monotonic() - since > 3:
                self._remind(run)
            if time.monotonic() > deadline:
                raise Infra(f"等了 {seconds} 秒对话还在跑（{run.get('status')}）")
            time.sleep(self.POLL)

    # 用户的动作
    def attach(self, files: list) -> None:
        for item in files:
            path = Path(str(item))
            if not path.is_file():
                path = _lib("samples").resolve(str(item))  # 样票名（tests/acceptance/samples.toml）→ 现生成的图片；不认识的名字抛 Unmet
            boundary = "----acc" + uuid.uuid4().hex
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\n{self.session_id}\r\n"
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
                    f"Content-Type: {mime}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
            stored = self._call("POST", "upload", {}, body, f"multipart/form-data; boundary={boundary}") or {}
            if not stored.get("mountPath"):
                raise RuntimeError(f"附件 {path.name} 没传上：{stored}")
            self._files.append({"filename": stored.get("filename") or path.name, "mountPath": stored["mountPath"]})

    def say(self, text: str) -> None:
        """照页面发一句：带上已传的附件说明；CC 正在回（不是在等卡片）时排到这一轮结束再发（页面的排队）。"""
        self._lang = _lib("cc_lines").language_of(text)
        run = self.poll()
        if self._busy(run):
            self.wait_idle()
        note = ""
        if self._files:
            noun = "file, mounted read-only in the sandbox" if len(self._files) == 1 else "files, mounted read-only in the sandbox"
            note = f"\n\n[Attached {noun}:\n" + "\n".join(f"- {f['filename']} → {f['mountPath']}" for f in self._files) + "]"
        self._post("send", {"session_id": self.session_id, "text": text + note})
        self._files = []

    def _open_cards(self, run: dict) -> dict:
        """此刻等用户点的卡片：调用编号 → 那次调用（工具名、入参）。"""
        waiting = set(run.get("awaiting_approval") or [])
        return {event["id"]: event for event in self.events if event.get("type") in TOOL_USES and event.get("id") in waiting}

    def click(self, card: str, choice) -> bool:
        if card == "plan":
            self.wait_idle()
            latest: dict[str, str] = {}
            for event in self.events:
                if event.get("type") == "pearnly.write_plan":
                    latest[event.get("plan")] = event.get("status")
            if "awaiting" not in latest.values():
                return False  # 页面上没有「写入」可点
            self._post("send", {"session_id": self.session_id, "text": RECORD_NOW.get(self._lang, RECORD_NOW["zh"])})
            return True
        if card not in ("ask", "allow"):
            raise ValueError(f"不认识的卡片 {card!r}（plan / ask / allow）")
        deadline = time.monotonic() + self.CARD_WAIT
        while True:
            run = self.poll()
            found = [(use_id, call) for use_id, call in self._open_cards(run).items()
                     if (call.get("name") == ASK_TOOL) == (card == "ask")]
            if found:
                break
            if run and not self._busy(run) and run.get("status") != "requires_action":
                return False  # 这一轮结束了也没出这种卡片
            if time.monotonic() > deadline:
                return False
            if run.get("status") == "requires_action":
                self._remind(run)
            time.sleep(self.POLL)
        use_id, call = found[-1]
        if card == "ask":
            item = {"tool_use_id": use_id, "result": "allow", "answers": ask_answers((call.get("input") or {}).get("questions") or [], choice)}
        else:
            item = {"tool_use_id": use_id, "result": "deny" if choice == "deny" else "allow"}
        self._post("confirm", {"session_id": self.session_id, "confirmations": json.dumps([item], ensure_ascii=False)})
        return True

    def stop(self) -> None:
        self._post("interrupt", {"session_id": self.session_id})

    def record(self):
        self.poll()
        return record_from_events(self.events)

    def cost(self) -> float:
        return 0.0

    def evidence(self) -> dict:
        self.keep = True  # 失败的现场不清理：对话留在站点上，直到有人看过
        try:
            self.poll()
        except Exception as error:  # noqa: BLE001 —— 站点这时读不到也要把已攒的事件交出去
            return {"site": self.site, "session_id": self.session_id, "events": self.events, "poll_error": str(error)}
        return {"site": self.site, "session_id": self.session_id, "events": self.events}

    def close(self) -> None:
        """删掉这段对话（keep=True 时留着）、注销这次建的 Desk 会话。可重复调。"""
        if self.session_id and not self.keep:
            try:
                self._post("delete", {"session_id": self.session_id})
            except Exception:  # noqa: BLE001 —— 收尾尽力而为
                pass
        self.session_id = "" if not self.keep else self.session_id
        if self.sid:
            self._drop_desk_session()
            self.sid = ""

    def _drop_desk_session(self) -> None:
        self._run("exec", "-w", "/home/frappe/frappe-bench/sites", self.container, "../env/bin/python",
                  "/workspace/scripts/smoke/session.py", self.site, self.user, "drop", self.sid, timeout=120)


# ── 真模型（测试机实验栈 / 本机实验栈上接了真模型的站点）────────────────────────
class RealConversation(LabConversation):
    """真模型的一段直写对话。与 lab-fake 走同一套站点接口与同一套「照页面做」的规矩，只换三样：连哪个站点、Desk 会话怎么建、
    费用怎么算。环境变量（都不是密钥；没配齐就是「没测到」）：
      PEARNLY_ACC_REAL_BASE     站点地址（如 https://testcowork.pearnly.com，或本机实验栈 http://127.0.0.1:18772）
      PEARNLY_ACC_REAL_SITE     站点名（Host；默认取地址里的主机名）
      PEARNLY_ACC_REAL_USER     登录的账号（测试租户的员工；CC 桌面版写账场景要与电脑上登录的是同一人）
      PEARNLY_ACC_REAL_TARGET   local（本机实验栈）/ lab（测试机实验栈，默认）；生产不连
    占用（测试机、账套、实验栈）由场景登记的 resources 管，这里不查锁。"""

    target = "real"

    def __init__(self, product: str, mode: str, company: str = "", ledger: str = "", base: str = "", site: str = "",
                 user: str = "", where: str = "", keep: bool = False, remote=None):
        Conversation.__init__(self, product, mode, company, ledger)
        if mode != "direct":
            raise Unmet(f"真模型驱动现在只接直写对话；{mode} 要桌面会话，还没接")
        self.base = (base or os.environ.get("PEARNLY_ACC_REAL_BASE", "")).rstrip("/")
        self.user = user or os.environ.get("PEARNLY_ACC_REAL_USER", "")
        self.where = where or os.environ.get("PEARNLY_ACC_REAL_TARGET", "lab")
        missing = [name for name, value in (("PEARNLY_ACC_REAL_BASE", self.base), ("PEARNLY_ACC_REAL_USER", self.user)) if not value]
        if missing:
            raise Unmet("CC 真模型驱动没配：" + "、".join(missing) + "（见 cc_driver.RealConversation）")
        if self.where not in REAL_TARGETS:
            raise Unmet(f"CC 真模型驱动只连实验栈（{' / '.join(REAL_TARGETS)}），不连 {self.where}")
        self.site = site or os.environ.get("PEARNLY_ACC_REAL_SITE") or (urllib.parse.urlsplit(self.base).hostname or "")
        self.keep = keep
        self.events, self._ids, self._anchor, self._files = [], set(), None, []
        self._lang, self._reminded, self._csrf = "zh", set(), ""
        self.sid = self.session_id = ""
        self._remote = remote or _lib("targets").Targets().site(self.where)
        try:
            self._http("GET", self.base + "/api/method/ping", headers={"Host": self.site})
        except (RuntimeError, urllib.error.URLError) as error:
            raise Unmet(f"站点 {self.site}（{self.base}）连不上：{error}") from None
        except Exception as error:  # acc_errors.Infra（_Late 取不到类型，按名字认）
            if type(error).__name__ != "Infra":
                raise
            raise Unmet(f"站点 {self.site}（{self.base}）连不上：{error}") from None
        self.sid = self._session_script()
        if not self.sid:
            raise Unmet(f"站点 {self.site} 给 {self.user} 建不了 Desk 会话")
        self._begin()

    def _session_script(self, *extra: str) -> str:
        out = self._remote.run(self.product, HERE.parent / "smoke" / "session.py", self.site, self.user, *extra, timeout=180)
        return next((line.split()[1] for line in out.splitlines() if line.startswith("SID ")), "")

    def _drop_desk_session(self) -> None:
        self._session_script("drop", self.sid)

    def cost(self) -> float:
        return usage_usd(self.events)
