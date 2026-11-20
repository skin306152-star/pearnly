# -*- coding: utf-8 -*-
"""一张单的固定流程（试点 31516fff 在服务器版 69EXP\\test 上实测的录法，移植自 ``engine.py``）。

菜单打开表单 → 工具栏「新建」（命令号）→ 表头按回车走格、按标签填、每格读回 → 点进明细表格逐格录、每格读回、单位框按
「ตกลง」→ 读底部合计和草稿对上才存 → 「保存」→ 单号格变正式号才算存上 → 读 DBF 逐字段核对。

意外的处理（评估 §14）：认得出的弹窗按配方规则处理；认不出 / 读回不符 → AI 接手（``helper``），AI 也处理不了 → 必须人工；
负库存被拦、日期不在期间 → 这张放弃；放弃一律走 Express 原生的路：取消 → 读单号格确认已存 → 作废（绝不按 Esc、绝不删单）。
网络读写错（-70）→ 什么都不点，交给批次等服务器恢复后续录。锁屏 / 有人在用电脑 → 停在原地等（``presence``），回来后接着录，
每格照样读回，被人动过的格子会被读回发现。
"""
from __future__ import annotations

import hashlib
import re
import time
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..logger import get_logger
from . import book as B
from . import contract as K
from . import menu as M
from .desk import MenuAmbiguous
from .draft import Draft
from .helper import NoHelper, png_block

# 写进 companion.log（托盘进程的日志）：AI 接不上时的错误码，不含截图与请求体
log = get_logger(__name__)
from .recipe import Form, Recipe

MAX_TAKEOVERS_PER_DOC = 3
MENU_VERIFY_S = 10  # AI 用菜单开了窗口后，等单据窗口标题出来最多这么久
# 固定流程读画面的宽限（11-01 录制机实测：同一个 Express 里开 HP 表单，平常 0.3–3 秒，间歇会到 6–35 秒，开不开第二个 Express
# 都出现；只读一次就判成画面不对、作废了存对的单）。按等待次数算（测试里的等待是假的）；中途有人在本批的 Express 里动过就等人
# 停手、重新计时再读
SLOW_STEP_MS = 500
SLOW_FORM_S = 60
SLOW_SAVE_S = 60
UNIT_WAIT_S = 10  # 录完数量回车后等单位框（或光标到单价格）
SAVED_UNREAD = "?"  # 画面核对已存上、单号格读不到字：单号以读账套为准
INPUT_OPS = {"keys", "key", "type", "paste", "click", "drag", "scroll", "grid_click", "command", "dialog_button", "menu",
             "close_form",
             "activate", "us_layout", "move", "mouse"}


class NeedHuman(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail
        self.evidence = ""  # 截到的画面（本机路径）：宿主放进回执 meta.evidence，不进说明


# 中性按钮（确认 / 取消 / 关闭）：按了不替人做业务决定。认不出的框上有两个以上别的按钮 = 业务选择（如服务品税口径
# เกณฑ์สิทธิ / เกณฑ์เงินสด）：配方有规则按规则答，没有规则的 AI 只能看、不能点，原样留给人（11-13 设计要点 4）
NEUTRAL_BUTTONS = frozenset({"ตกลง", "ยกเลิก", "ปิด", "OK", "CANCEL", "CLOSE"})
PARTIAL = "partial_saved"


def _buttons(w: Dict) -> List[str]:
    return [b.replace("&", "").strip() for b in (w.get("buttons") or []) if b.replace("&", "").strip()]


def is_choice(w: Dict) -> bool:
    """这个框问的是业务选择：两个以上不是确认 / 取消 / 关闭的按钮。"""
    return sum(1 for b in _buttons(w) if b.upper() not in NEUTRAL_BUTTONS and b not in NEUTRAL_BUTTONS) >= 2


class ChoiceBox(Exception):
    """AI 接手时要在配方外的业务选择框上动手：工具层直接拦下（不只靠提示词），这张转人工、框原样留着。"""

    def __init__(self, text: str):
        super().__init__(f"business choice box: {text}")
        self.text = text


DIALOG_CHOICE = "dialog_choice"
DIALOG_TEXT_MAX, DIALOG_BUTTONS_MAX, DIALOG_BUTTON_MAX = 600, 8, 60


def dialog_info(w: Dict, doc_type: str, stage: str) -> Dict:
    """配方外业务选择框带回对话的样子（1.12.23 ③）：标题、正文（去掉按钮字）、按钮原文、单种、在哪一步（editing 录入中 /
    saving 保存途中），加指纹 ``digest``（标题 + 正文 + 按钮 + 单种的 sha1 前 16 位）。站点按指纹记住答案（``dialog_answers``）、
    CC 照点前也按它核对框没变。"""
    buttons = [b[:DIALOG_BUTTON_MAX] for b in _buttons(w)][:DIALOG_BUTTONS_MAX]
    title = str(w.get("title") or "").strip()[:DIALOG_BUTTON_MAX * 2]
    raw = set(b.replace("&", "").strip() for b in (w.get("buttons") or []))
    text = " ".join(t for t in (w.get("texts") or []) if t.replace("&", "").strip() not in raw).strip()[:DIALOG_TEXT_MAX]
    doc_type = str(doc_type or "").upper()
    digest = hashlib.sha1("\x1f".join([title, text, "\x1e".join(buttons), doc_type]).encode("utf-8")).hexdigest()[:16]
    return {"digest": digest, "title": title, "text": text, "buttons": buttons, "doc_type": doc_type,
            "stage": stage if stage in ("editing", "saving") else "editing"}


class DialogHeld(NeedHuman):
    """配方外的业务选择框、没有记住的答案：不点、单据窗口不关、Express 留着（``held``），回执原因 ``dialog_choice`` 带
    ``meta.operator.dialog``，对话里出卡让人选（1.12.23 ③）。"""

    def __init__(self, dialog: Dict):
        super().__init__(DIALOG_CHOICE, f"business choice box: {dialog.get('title', '')} {dialog.get('text', '')}"[:300])
        self.dialog = dict(dialog)


class MenuChoice(NeedHuman):
    """菜单子菜单里有几个命令、载荷的选择与付款方式都定不下来：只停这一张（原因 ``menu_choice``），候选带回站点问人。"""

    def __init__(self, doc_type: str, options: List[str]):
        super().__init__("menu_choice", f"{doc_type}: " + " | ".join(options))
        self.doc_type, self.options = doc_type, list(options)


class Blocked(Exception):
    """Express 按业务规则拦下（负库存、日期不在期间、授权）：这张录不进去，按放弃流程收尾。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


class Crash(Exception):
    """Express 没了，或弹了网络读写错误（-70）：先别点，交给批次。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


class Stopped(Exception):
    """人点了停止（等电脑空出来的时候）。"""


INTERRUPTED = "interrupted"


class Interrupted(NeedHuman):
    """有人在本批的 Express 里点过 / 打过字（1.12.23 ⑥）：人停手后核对画面对不上，或等满上限人还在点。不交 AI 去「修」人的操作，
    这张转人工（原因 ``interrupted``），结果 meta.interrupt 带 {stage, where, what}；对话里给「继续 / 停止」。"""

    def __init__(self, where: str, what: str, stage: str = "editing"):
        super().__init__(INTERRUPTED, f"{where}: {what}"[:300])
        self.interrupt = {"stage": stage if stage in ("editing", "saving") else "editing", "where": str(where)[:60],
                          "what": str(what)[:200]}


class Unexpected(Exception):
    """固定流程这一步之后状态不对：交给 AI 接手。"""


def _norm(text: str) -> str:
    # Express 把码里的不换行空格显示成「•」（试点实测客户码），核对时当成同一个字符
    return re.sub(r"[\s,• ]", "", str(text or "")).upper()


def same(want: str, seen: str) -> bool:
    """格子里读到的和要打的是不是一回事：数字按数值比（1,000.00 = 1000），其余去空格、逗号后全等。"""
    a, b = _norm(want), _norm(seen)
    try:
        return Decimal(a) == Decimal(b)
    except Exception:  # noqa: BLE001
        return a == b


class Engine:
    def __init__(self, desk, recipe: Recipe, book: B.Book, *, helper=None,
                 presence: Optional[Callable[[], None]] = None, evidence: Optional[Callable[[str, str], None]] = None,
                 log: Callable[[str], None] = lambda _m: None, sleep: Callable[[float], None] = time.sleep,
                 may_void: Callable[[str], bool] = lambda _docnum: True, menus=None):
        self.desk, self.recipe, self.book = desk, recipe, book
        self.helper = helper or NoHelper()
        self.presence = presence  # 发键前、读画面判对错前调用：锁屏 / 有人在用就在里面等（等过回真），人点停止就抛 Stopped
        self.evidence = evidence  # (doc_id, png_b64) → 存档截图（只存本机）
        self.log, self.sleep = log, sleep
        self.may_void = may_void  # 批次给的第二道闸：这个单号是不是本批次开录后新建、又不是本批次已存好的单
        self.form_open: Optional[str] = None
        self.baseline: set = set()
        self.windows: List[Dict] = []
        self.labels: Dict[int, str] = {}
        self.scale = 1.5
        self.rec: Dict = {}
        self.draft: Optional[Draft] = None
        self.form: Optional[Form] = None
        self._unread = 0
        self._line_recovered = False
        self._stage = ""
        self._before = None  # 这张开录前账套各表行数（放弃时只作废之后新出现的单号）
        # 记住的菜单路径（AI 选对后按「账套 + 配方版本 + 单种」记在本机 probe.json，``probe.MenuMemory``）：get(单种) / put(单种, 路径, 标题)
        self.menus = menus
        self.menu_hint: Tuple[str, str] = ("", "")  # 探路时（没有草稿单）由批次给：这一张的 form_choice、pay_route
        self._menu_options: Dict[str, List[str]] = {}  # 单种 → 这家子菜单里的候选（这一批里见过的）
        self._form_choice = ""  # 当前开着的单据窗口是按哪个候选开的
        self._ai_menu_path: Optional[List[str]] = None
        self.batch_id = ""  # 写日志用（批次给）

    # ── 记录 ────────────────────────────────────────────────────────────────
    def _event(self, kind: str, **data) -> None:
        self.rec["events"].append({"t": round(time.monotonic() - self._t0, 2), "kind": kind, **data})

    def _layer(self, name: str, n: int = 1) -> None:
        self.rec["layers"][name] = self.rec["layers"].get(name, 0) + n

    def _report(self, reason: str, **data) -> None:
        """自动处理后要在汇报里列出的事（重复单号、AI 接手解决、作废）。"""
        self.rec["reports"].append({"reason": reason, **data})

    # ── 操作 ────────────────────────────────────────────────────────────────
    def _time(self, name: str, seconds: float) -> None:
        timing = self.rec.setdefault("timing", {})
        timing[name] = round(timing.get(name, 0.0) + seconds, 3)

    def _person(self) -> bool:
        """锁屏 / 有人在本批的 Express 里动过：在里面等人停手。回这次等没等过。"""
        if not self.presence:
            return False
        started = time.monotonic()
        try:
            paused = bool(self.presence())
        except Interrupted as stop:  # 等满上限人还在点：带上这张停在哪
            stop.interrupt["stage"] = "saving" if self._save_pressed else "editing"
            stop.interrupt["where"] = stop.interrupt["where"] or str(self._stage or "")[:60]
            raise
        self._time("paused", time.monotonic() - started)
        if paused:
            self._touched = True  # 人碰过本批的 Express：之后核对不上就是人改的，不交 AI 去改回（⑥）
        return paused

    def _wait_for(self, check: Callable[[], Any], seconds: float) -> Any:
        """限时一次次读（``check`` 回真值即成）：Express 慢时多等一会儿，不只读一次就判画面不对。中途有人在本批的 Express
        里动过（点了、打了字）：等人停手后重新计时再读，人碰过的那一下不算画面不对（用户 10-30：被打断先停、再从断点接着）。
        回 ``check`` 最后一次的结果。"""
        tries = left = max(1, int(seconds * 1000 / SLOW_STEP_MS))
        while True:
            found = check()
            if found:
                return found
            if self._person():
                left = tries
                continue
            if left <= 0:
                return found
            left -= 1
            self.act([{"do": "wait", "ms": SLOW_STEP_MS}], windows=False)

    def _fg_title(self) -> str:
        return (self.act([{"do": "wait", "ms": 0}]).get("fg") or {}).get("title", "")

    def act(self, actions: List[Dict], windows: bool = True) -> Dict:
        if any(a["do"] in INPUT_OPS for a in actions):
            self._person()
        started = time.monotonic()
        reply = self.desk.act(actions, windows)
        for action, result in zip(actions, reply.get("results") or []):
            if action.get("do") == "grid_click" and isinstance(result, dict) and result.get("xy"):
                self._grid_xy = result["xy"]  # 点进明细的屏幕坐标（`_onto_item_column` 用；中途读状态不覆盖它）
        # 分步计时：等待单算一项（后台做法回实际等了多久，前台做法是写死的时长），其余算到这一批的第一个动作上
        waited = sum(res.get("waited", int(a.get("ms", 0)) / 1000) for a, res in zip(actions, reply["results"])
                      if a["do"] == "wait" and res.get("ok"))
        if waited:
            self._time("wait", waited)
        work = [a["do"] for a in actions if a["do"] != "wait"]
        self._time(work[0] if work else "wait_only", max(0.0, time.monotonic() - started - waited))
        for a in actions:
            if a["do"] in ("keys", "key", "type", "paste", "command", "dialog_button"):
                self._layer("keys")
            elif a["do"] in ("click", "grid_click"):
                self._layer("clicks")
        if windows:
            self.windows = reply.get("windows", [])
            self._health()
        for res in reply["results"]:
            if res.get("ambiguous") is not None:
                raise MenuAmbiguous(res["ambiguous"], res.get("path") or [])
            if not res.get("ok") and not res.get("skipped"):
                raise Unexpected(("refused: " if res.get("refused") else "error: ") + str(res.get("error", ""))[:200])
        return reply

    def _health(self) -> None:
        if not self.windows:
            raise Crash("network_wait", "Express window gone")
        for w in self.windows:
            rule = self._match(w)
            if rule and rule.get("outcome") == "crash":
                raise Crash(rule.get("reason", "network_wait"), self._text(w)[:200])

    @staticmethod
    def _text(w: Dict) -> str:
        return (w.get("title", "") + " " + " ".join(w.get("texts", []))).strip()

    def _match(self, w: Dict) -> Optional[Dict]:
        return self.recipe.match_dialog(self._text(w))

    def _button(self, rule: Dict) -> str:
        """规则要按的钮；``button_by_srv_vattyp`` 按草稿单的服务品税口径换钮（销项税口径框 11-13、进项税口径框 11-14），草稿没带就用默认钮。"""
        choice = self.draft.srv_vattyp if self.draft else ""
        return (rule.get("button_by_srv_vattyp") or {}).get(choice) or rule["button"]

    def _dialogs(self) -> List[Dict]:
        return [w for w in self.windows if w["hwnd"] not in self.baseline]

    def _auto_dialogs(self) -> None:
        """认得出的弹窗按配方规则处理；拦单的抛 Blocked；认不出的交给 AI。"""
        for _ in range(4):
            pending = []
            for w in self._dialogs():
                rule = self._match(w)
                if rule is None:
                    raise Unexpected(f"unknown dialog: {self._text(w)[:200]}")
                if rule.get("auto"):
                    pending.append((w, rule))
            if not pending:
                return
            w, rule = pending[0]
            self._layer("win32")
            if rule.get("outcome") == "blocked":
                raise Blocked(self._reason(rule), self._text(w)[:200])
            self._event("dialog", name=rule["name"])
            if rule.get("reason"):
                self._report(rule["reason"])
            self.act([{"do": "dialog_button", "text": self._button(rule)}, {"do": "wait", "ms": 600}])
        raise Unexpected("dialog does not close")

    def evidence_shot(self, doc_id: str) -> str:
        """留一张当时的截图（本机证据目录），回路径；没接证据目录、截不到回空串（画面状态不明时截图也可能失败）。
        不依赖这一张的记录（探路、开 Express 时出的意外也能截）。"""
        if not self.evidence:
            return ""
        try:
            res = self.desk.act([{"do": "screenshot", "size": [1280, 720]}], False)["results"][0]
            return str(self.evidence(f"{doc_id}-need-human", res["png"]) or "") if res.get("png") else ""
        except Exception:  # noqa: BLE001
            return ""

    def _need_human_shot(self, doc_id: str) -> None:
        """转人工的单留一张当时的截图（本机证据目录，同存成的单；不上传），路径附在说明后面，随任务回到待处理。截不到不影响转人工。"""
        self._fit()  # 截图前把超出屏幕的窗口挪回屏内，人看得到整个框
        path = self.evidence_shot(doc_id)
        if not path:
            return
        self.rec["evidence"] = str(path)  # 路径只走结果 meta.evidence，不进说明（1.12.15：说明留给 AI 结论原文）

    def screenshot(self, purpose: str) -> str:
        res = self.act([{"do": "screenshot", "size": [1280, 720]}], windows=False)["results"][0]
        screen = res.get("screen") or [1920, 1080]
        self.scale = screen[0] / 1280
        self.rec["shots_ai" if purpose != "saved" else "shots_evidence"] += 1
        return res["png"]

    # ── AI ──────────────────────────────────────────────────────────────────
    def checkpoint(self, where: str, expect_text: str, expect: Dict, *, recover: bool = True,
                   reverify: Optional[Callable[[], Any]] = None) -> bool:
        if reverify is not None and self._person() and reverify():
            self._event("check", where=where, problem="", after_pause=True)  # 人碰过 Express：等人停手后重读就对了
            return True
        png = self.screenshot(where)
        failed = ""
        try:
            answer = self.helper.check(png, f"期望：{expect_text}\n草稿单：\n{self._describe()}")
        except RuntimeError as error:  # 站点没接上 / 出错（预算用完是 BudgetStop，照旧往上抛）：当作判断不了
            failed = self._ai_unavailable(where, error)
            answer = None
        self.rec["checkpoints"] += 1
        self._layer("vision")
        problem = f"{self._ai_note(failed)}; {self._screen_note()}" if answer is None else self._judge(answer, expect)
        self._event("check", where=where, problem=problem)
        if not problem:
            return True
        if not recover:
            return False
        self._recover(where, problem, expect_text, expect)
        return True

    def _ai_note(self, failed: str = "") -> str:
        """判断不了时说明里写清是哪一种：这台没接 AI（成员通道没有 operator_ai）、AI 调用出错（站点回的错误码）、AI 没给答案。"""
        if failed:
            return f"ai unavailable: {failed}"
        return "ai not connected" if isinstance(self.helper, NoHelper) else "ai gave no answer"

    def _ai_unavailable(self, where: str, error: Exception) -> str:
        code = str(error)[:80] or type(error).__name__
        self._event("ai_unavailable", where=where, note=code)
        log.warning(f"operator ai unavailable · batch={self.batch_id or '-'} · doc={(self.rec or {}).get('doc_id') or '-'}"
                    f" · where={where} · error={code}")
        return code

    def _describe(self) -> str:
        return self.draft.describe() if self.draft else "（探路，没有草稿单）"

    @staticmethod
    def _judge(answer: Dict, expect: Dict) -> str:
        state = answer.get("state")
        if state == "dialog":
            return f"dialog {answer.get('message_no', '')} {answer.get('dialog_text', '')[:150]}"
        if state != "as_expected":
            return f"screen {state}: {answer.get('what_i_see', '')[:150]}"
        if "lines" in expect and answer.get("lines_seen") not in (None, expect["lines"]):
            return f"lines {answer.get('lines_seen')} != {expect['lines']}"
        if "total" in expect and answer.get("total_seen"):
            seen = re.sub(r"[^\d.]", "", answer["total_seen"])
            if seen and Decimal(seen) != Decimal(expect["total"]):
                return f"total {answer['total_seen']} != {expect['total']}"
        if expect.get("saved") and answer.get("docnum_seen", "").upper().endswith("NEW"):
            return "not saved"
        return ""

    def _recover(self, where: str, problem: str, expect_text: str, expect: Dict, reverify=None) -> None:
        self.rec["fallbacks"].append({"where": where, "problem": problem[:200]})
        if getattr(self, "_touched", False):  # 人在 Express 里动过、等人停手后画面对不上：如实交人，不让 AI 改人的操作
            self._event("interrupted", where=where, problem=problem[:200])
            raise Interrupted(where, problem, "saving" if self._save_pressed else "editing")
        choice = [w for w in self.windows if w.get("owned") and self._match(w) is None and is_choice(w)]
        if choice:  # 配方外的业务选择：不交 AI（它只能看不能点）；有人答过的照点，没有就原样留给人、带回对话
            if self._answer_choice(choice[0], where):
                ok = reverify() if reverify else None
                if ok is None:
                    ok = self.checkpoint(f"{where}.answered", expect_text, expect, recover=False)
                if ok:
                    return
            self._event("takeover", where=where, status="need_human", note="business choice box")
            raise DialogHeld(self._dialog(choice[0]))
        if len(self.rec["fallbacks"]) > MAX_TAKEOVERS_PER_DOC:
            raise NeedHuman("unknown_screen", f"{where}: {problem}")  # 同一张反复出意外：不再烧钱，交给人
        goal = (f"固定流程在「{where}」这一步出了意外：{problem}\n你的目标：处理掉意外，让画面达到「{expect_text}」。\n"
                + ("这一步本来就是保存：确认保存成功即可，不要重复保存。\n" if expect.get("saved") else "不要保存。\n")
                + f"草稿单：\n{self._describe()}\n做完调用 finish。")
        try:
            if isinstance(self.helper, NoHelper):
                status, note = "need_human", self._ai_note()
            else:
                self._fit()  # AI 看画面前：超出屏幕的窗口（报表、弹窗）挪回屏内，按钮看得到
                status, note = self.helper.take_over(goal, self)
        except ChoiceBox as box:  # AI 想在业务选择框上动手：拦下，框原样留给人、带回对话
            self._event("takeover", where=where, status="need_human", note="business choice box")
            found = self.choice_boxes()
            if found:
                raise DialogHeld(self._dialog(found[0])) from None
            raise NeedHuman("unknown_screen", f"business choice box · {where}: {box.text}"[:K.DETAIL_MAX]) from None
        except RuntimeError as error:  # 站点没接上 / 出错：交给人（预算用完是 BudgetStop，照旧往上抛）
            status, note = "need_human", self._ai_note(self._ai_unavailable(where, error))
        self._event("takeover", where=where, status=status, note=note[:200])
        if status == "resolved":
            ok = reverify() if reverify else None
            if ok is None:
                ok = self.checkpoint(f"{where}.verify", expect_text, expect, recover=False)
            if ok:
                self._report("ai_resolved", where=where)
                if where.startswith("line"):
                    self._line_recovered = True
                return
        # 说明里先写 AI 那一侧怎么了（没接 / 出错 / AI 自己的结论），再写固定流程的问题（菜单树等可能很长，放后面）
        ai = note if note.startswith("ai ") else f"ai: {note or status}"
        raise NeedHuman("unknown_screen", f"{ai} · {where}: {problem}"[:K.DETAIL_MAX])

    @property
    def background(self) -> bool:
        """这台电脑是不是后台做法（``bg_desk``：不动真实鼠标，只能键盘与 express_menu）。AI 接手按它给提示与工具。"""
        return bool(getattr(self.desk, "background", False))

    def _xy(self, coord) -> Tuple[int, int]:
        return int(round(coord[0] * self.scale)), int(round(coord[1] * self.scale))

    def member(self, name: str, inp: Dict) -> List[Dict]:
        """电脑操作工具集成员 → 动作。坐标从截图空间（1280×720）换回屏幕。"""
        ok = [{"type": "text", "text": "OK"}]
        if name == "screenshot":
            return [png_block(self.screenshot("takeover"))]
        if name == "zoom":
            x0, y0 = self._xy(inp["region"][:2])
            x1, y1 = self._xy(inp["region"][2:])
            return [png_block(self.act([{"do": "zoom", "region": [x0, y0, x1, y1]}])["results"][0]["png"])]
        clicks = {"left_click": ("left", 1), "right_click": ("right", 1), "middle_click": ("middle", 1),
                  "double_click": ("left", 2), "triple_click": ("left", 3)}
        if name in clicks or name in ("left_click_drag", "left_mouse_down", "left_mouse_up", "type", "key", "scroll") \
                or (name == "express_menu" and inp.get("path")):
            self._no_choice_box()
        if name in clicks:
            button, count = clicks[name]
            x, y = self._xy(inp["coordinate"]) if inp.get("coordinate") else \
                self.act([{"do": "cursor"}], windows=False)["results"][0]["pos"]
            self.act([{"do": "click", "x": x, "y": y, "button": button, "count": count}, {"do": "wait", "ms": 300}])
            self._blocked_check()
            return ok
        if name == "left_click_drag":
            (x0, y0), (x1, y1) = self._xy(inp["start_coordinate"]), self._xy(inp["coordinate"])
            self.act([{"do": "drag", "x0": x0, "y0": y0, "x1": x1, "y1": y1}])
            return ok
        if name == "mouse_move":
            x, y = self._xy(inp["coordinate"])
            self.act([{"do": "move", "x": x, "y": y}], windows=False)
            return ok
        if name in ("left_mouse_down", "left_mouse_up"):
            self.act([{"do": "mouse", "state": "down" if name.endswith("down") else "up"}])
            return ok
        if name == "cursor_position":
            x, y = self.act([{"do": "cursor"}], windows=False)["results"][0]["pos"]
            return [{"type": "text", "text": f"X={int(x / self.scale)}, Y={int(y / self.scale)}"}]
        if name == "scroll":
            x, y = self._xy(inp["coordinate"]) if inp.get("coordinate") else \
                self.act([{"do": "cursor"}], windows=False)["results"][0]["pos"]
            self.act([{"do": "scroll", "x": x, "y": y, "direction": inp["scroll_direction"],
                       "amount": inp.get("scroll_amount", 3)}])
            return ok
        if name in ("type", "key"):
            action = {"do": "type", "text": inp["text"]} if name == "type" else \
                {"do": "key", "text": inp["text"], "repeat": inp.get("repeat", 1)}
            if name == "key" and inp["text"].lower() in ("escape", "esc", "alt+f4"):
                raise ValueError("Esc / Alt+F4 are not allowed")  # Esc 会把半张单直接存进账
            self.act([action, {"do": "wait", "ms": 300}])
            self._blocked_check()
            return ok
        if name == "wait":
            self.act([{"do": "wait", "ms": int(min(float(inp.get("duration", 1)), 10) * 1000)}])
            return ok
        if name == "express_menu":
            return self._ai_menu(inp)
        raise ValueError(f"unsupported member {name}")

    def _ai_menu(self, inp: Dict) -> List[Dict]:
        """AI 接手的菜单工具（后台做法也能用：按命令号发，不按组合键）：``{"read": true}`` 读菜单；``{"path": [编号或文字…]}``
        打开那一项（同固定流程的 ``menu`` 动作、同一套守卫；落到多命令子菜单时抛 ``MenuAmbiguous``，不让 AI 猜）。"""
        path = [str(k).strip() for k in (inp.get("path") or []) if str(k).strip()]
        if inp.get("read") or not path:
            return [{"type": "text", "text": self.menu_summary() or "(menu not readable)"}]
        self._ai_menu_path = path
        self.act([{"do": "menu", "path": path, "name": self.form.name if self.form else ""},
                  {"do": "wait", "ms": self.recipe.form_open_ms}])
        return [{"type": "text", "text": f"sent; Express window title: {self._fg_title()[:150]}"}]

    def progress(self) -> str:
        """AI 接手另起一段对话时写进首条消息：固定流程知道的、Express 里已经落下的（停在哪一步、已录完哪几行、单号格、存没存）。
        读不到的项不写。"""
        parts = []
        if self._stage:
            parts.append(f"当前步骤：{self._stage}")
        d = self.draft
        if d and self._lines_done:
            rows = "；".join(f"第 {i} 行 品码 {l.item_code} 数量 {l.qty} 单价 {l.unit_price} 金额 {l.amount}"
                            for i, l in enumerate(d.lines[:self._lines_done], start=1))
            parts.append(f"明细已录完 {self._lines_done} 行（已占号写账）：{rows}")
        elif d and self._stage == "line":
            parts.append("明细还没有录完整的行")
        if self.form is not None:
            try:
                number = self.docnum_text()
                editing = self.in_edit()
            except Exception:  # noqa: BLE001 — 读不到就不写这一项
                number, editing = "", None
            if number:
                parts.append(f"单号格：{number}")
            if editing is not None:
                saved = bool(number) and "NEW" not in number.upper() and not editing
                parts.append("已保存（单号 " + number + "）" if saved else "还没保存（编辑中）" if editing else "不在编辑中")
        return "\n".join(parts)

    def menu_tree(self) -> List[Dict]:
        try:
            return self.act([{"do": "menu_tree"}], windows=False)["results"][0].get("tree") or []
        except Unexpected:
            return []

    def menu_summary(self) -> str:
        return M.summary(self.menu_tree(), focus=self.form.menu[0] if self.form else "")

    def _reason(self, rule: Dict) -> str:
        """主档里找不到码时 Express 弹选择框：表头上是对方没有，明细里是商品没有。"""
        reason = rule.get("reason", "unknown_screen")
        if reason == "master_missing":
            return "item_missing" if self._stage == "line" else "party_missing"
        return reason

    def _blocked_check(self) -> None:
        for w in self._dialogs():
            rule = self._match(w)
            if rule and rule.get("outcome") == "blocked":
                raise Blocked(self._reason(rule), self._text(w)[:200])

    def _dialog(self, w: Dict) -> Dict:
        return dialog_info(w, self.draft.doc_type if self.draft else "", "saving" if self._save_pressed else "editing")

    def _answer_choice(self, w: Dict, where: str) -> bool:
        """这个框人答过（载荷 ``dialog_answers`` 里有同一指纹、那个按钮还在框上）就照点，回真；否则回假、什么都不点。"""
        answers = getattr(self.draft, "dialog_answers", None) or {}
        info = self._dialog(w)
        button = answers.get(info["digest"])
        if not button or button not in info["buttons"]:
            return False
        self._event("dialog_answered", where=where, digest=info["digest"], button=button)
        self._report("dialog_answered", digest=info["digest"], button=button)
        self.act([{"do": "dialog_button", "text": button}, {"do": "wait", "ms": 900}])
        return True

    def choice_boxes(self) -> List[Dict]:
        """Express 上开着的、配方认不出的业务选择框（见 :func:`is_choice`）。"""
        return [w for w in self.windows if w.get("owned") and self._match(w) is None and is_choice(w)]

    def _fit(self) -> None:
        """超出屏幕的 Express 窗口挪回屏内（``desk.fit_rect``）；挪不了不影响录单。"""
        try:
            moved = self.act([{"do": "fit"}], windows=False)["results"][0].get("moved") or []
        except Exception:  # noqa: BLE001
            return
        if moved:
            self._event("fit", moved=moved[:5])

    def _no_choice_box(self) -> None:
        self.act([{"do": "wait", "ms": 0}])  # 刷新窗口清单
        found = self.choice_boxes()
        if found:
            raise ChoiceBox(self._text(found[0])[:200])

    def answer_held(self, doc_type: str, digest: str, button: str) -> Dict:
        """对话里人选了留着的那个业务选择框的某个钮（领单回复 ``operator_controls``，1.12.23 ④）：先核框还在、指纹没变、
        按钮还在，都对才点。回 ``{"state": pressed}``；框已不在回 ``gone``；Express 上是别的框回 ``changed`` 带新框的样子；
        按钮不在回 ``no_button``。只点这一下，不碰别的（保存途中的那张由空闲时的「留着等人」检查读账套、补报原回执号）。"""
        try:
            self.act([{"do": "wait", "ms": 0}])
        except Crash:
            return {"state": "gone"}
        boxes = [w for w in self.windows if w.get("owned") and self._match(w) is None and is_choice(w)]
        if not boxes:
            return {"state": "gone"}
        stage = "saving" if getattr(self, "_save_pressed", False) else "editing"
        for w in boxes:
            info = dialog_info(w, doc_type, stage)
            if info["digest"] != digest:
                continue
            if button not in info["buttons"]:
                return {"state": "no_button", "dialog": info}
            self.desk.act([{"do": "dialog_button", "text": button}, {"do": "wait", "ms": 900}], False)
            return {"state": "pressed"}
        return {"state": "changed", "dialog": dialog_info(boxes[0], doc_type, stage)}

    def open_unknown(self) -> Optional[List[Dict]]:
        """这时 Express 上开着的、配方认不出的框（只读，不点）。Express 已经没了回 None。"""
        try:
            self.act([{"do": "wait", "ms": 0}])
        except Crash:
            return None
        except Exception as error:  # noqa: BLE001 — 读不到：Express 的窗口一个都没有就是没了，否则当成还开着、不放手
            # 11-19 测试机2：Express 被强行结束后后台做法读它抛「no Express window」，以前一律当还开着，账套占到 CC 重启
            try:
                gone = not self.desk.windows()
            except Exception:  # noqa: BLE001 — 连窗口清单也读不到：不下「没了」的结论
                gone = False
            if gone:
                log.info("operator held check: Express is gone (%s: %s)", type(error).__name__, str(error)[:200])
                return None
            log.warning("operator held check: reading Express failed, treated as still open · %s: %s", type(error).__name__,
                        str(error)[:200])
            return [{"title": "?", "texts": []}]
        return [w for w in self.windows if w.get("owned") and self._match(w) is None]

    # ── 读状态 ──────────────────────────────────────────────────────────────
    def step(self, where: str, actions: List[Dict], expect_text: str, expect: Dict) -> None:
        try:
            try:
                self.act(actions)
            except Unexpected as e:
                if not str(e).startswith("refused"):
                    raise
                self._event("refocus", where=where)  # 前台被抢了：拉回 Express 再做一次
                self.act([{"do": "activate"}, {"do": "wait", "ms": 400}])
                self.act(actions)
            self._auto_dialogs()
        except Unexpected as e:
            self._recover(where, str(e), expect_text, expect)

    def command(self, name: str) -> Dict:
        return self.act([{"do": "command", "cmd": self.recipe.commands[name]}, {"do": "wait", "ms": 300}])

    def state(self) -> Dict:
        st = self.act([{"do": "state"}], windows=False)["results"][0]
        self.windows = st.get("windows", self.windows)
        self._health()
        return st

    def toolbar_on(self) -> set:
        r = self.act([{"do": "toolbar", "cmds": list(self.recipe.commands.values())}], windows=False)["results"][0]
        return {b["cmd"] for b in r["buttons"] if b["enabled"]}

    def field_map(self) -> Dict[int, Dict]:
        return {f["hwnd"]: f for f in self.act([{"do": "fields"}], windows=False)["results"][0]["fields"]}

    @staticmethod
    def seen(st: Dict) -> Tuple[str, str]:
        text = (st.get("focus") or {}).get("text") or ""
        return ("win32", text) if text.strip() else ("none", "")

    def in_edit(self) -> bool:
        """新建 / 编辑中：「保存」亮着，或单号格含 NEW（赊销单新建后单号格先只显示「IV」，录了客户才带 NEW）。"""
        return self.recipe.commands["save"] in self.toolbar_on() or "NEW" in self.docnum_text().upper()

    def docnum_text(self) -> str:
        box = [f for f in self.field_map().values() if f["label"] == self.form.docnum_label]
        return box[0]["text"].strip() if box else ""

    def saved_check(self, T: str) -> str:
        """存上了：单号格是正式号（前缀 + 数字，不含 NEW），且不在编辑中。回单号，没存上回空串。
        账套文件头的行数要过一阵才刷新（试点实测），不拿它当判据。"""
        docnum = self.docnum_text()
        ok = docnum.upper().startswith(T) and "NEW" not in docnum.upper() and re.search(r"\d", docnum)
        return docnum if ok and not self.in_edit() else ""

    # ── 固定流程 ────────────────────────────────────────────────────────────
    @staticmethod
    def _is_form(form: Form, title: str) -> bool:
        """前台是这种单的单据窗口：标题含配方的单据标题，或含「单种 - 」（同一种单分几套单号的定制菜单开出的窗口名可能不同）。"""
        return form.title in title or f"{form.doc_type} - " in title

    def _menu_hint_now(self) -> Tuple[str, str]:
        return (self.draft.form_choice, self.draft.pay_route) if self.draft else self.menu_hint

    def ensure_form(self, doc_type: str) -> None:
        form = self.recipe.form(doc_type)
        self.form = form
        fg = (self.act([{"do": "activate"}, {"do": "fit"}, {"do": "wait", "ms": 300}]).get("fg") or {}).get("title", "")
        options = self._menu_options.get(doc_type)
        want = M.pick(options, *self._menu_hint_now()) if options else ""
        if self.form_open == doc_type and self._is_form(form, fg) and (not options or (want and want == self._form_choice)):
            return
        # 一次只开一种单据窗口：每个单据窗口各带一条工具栏，开着几个时读不准哪条（试点实测）。Ctrl+F4 关掉当前的
        for _ in range(6):
            if "[" not in fg:
                break
            self.act([{"do": "close_form"}, {"do": "wait", "ms": 1200}])
            if any("1312" in w.get("title", "") for w in self.windows):
                self.act([{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 1000}])
            fg = (self.act([{"do": "wait", "ms": 100}]).get("fg") or {}).get("title", "")
        self.form_open, self._form_choice = None, ""
        opened = lambda: self._is_form(form, self._fg_title())  # noqa: E731
        # 菜单路径（数字）：真键盘做法是先单独按 Alt 再按数字；后台做法按菜单项文字找到命令号直接发（desk 决定）。
        # 找不到、改了名、灰掉（文字对不上）→ 进检查点交 AI 接手；子菜单里几个命令 → 按载荷的选择定，定不下来停这一张
        remembered = self.menus.get(doc_type) if self.menus else None
        path = list(remembered or form.menu)
        try:
            title, choice = self._open_menu(form, path)
        except Unexpected as error:
            title, choice = self._menu_takeover(form, str(error), opened)
        self._event("form_open", doc_type=doc_type, **({"choice": choice} if choice else {}))
        if self._is_form(form, title) or self._wait_for(opened, SLOW_FORM_S):
            self._layer("win32")
        else:
            self.checkpoint("form_open", f"已打开「{form.title}」单据窗口，处于浏览状态，没有弹窗。", {"form": doc_type},
                            reverify=opened)
        self.form_open, self._form_choice = doc_type, choice
        self.baseline = {w["hwnd"] for w in self.windows}
        self.labels = {}

    def _send_menu(self, form: Form, path: List[str]) -> str:
        reply = self.act([{"do": "menu", "path": path, "name": form.name}, {"do": "wait", "ms": self.recipe.form_open_ms}])
        return (reply.get("fg") or {}).get("title", "")

    def _open_menu(self, form: Form, path: List[str]) -> Tuple[str, str]:
        """发菜单。回 (前台标题, 子菜单里选的那一项；没选为空)。"""
        try:
            return self._send_menu(form, path), ""
        except MenuAmbiguous as ambiguous:
            return self._choose(form, ambiguous)

    def _choose(self, form: Form, ambiguous: MenuAmbiguous) -> Tuple[str, str]:
        """子菜单里有几个命令：用站点记住的菜单文字，或按单据的付款方式选唯一一项（``menu.pick``）；定不下来抛 ``MenuChoice``
        （只停这一张、不调 AI）。选中的不写进本机记忆（业务选择由站点记）。"""
        self._menu_options[form.doc_type] = list(ambiguous.options)
        choice = M.pick(ambiguous.options, *self._menu_hint_now())
        self._event("menu_choice", options=ambiguous.options[:8], choice=choice)
        if not choice:
            raise MenuChoice(form.doc_type, ambiguous.options)
        return self._send_menu(form, list(ambiguous.path) + [choice]), choice

    def _menu_takeover(self, form: Form, error: str, opened: Callable[[], bool]) -> Tuple[str, str]:
        """菜单对不上：交 AI 接手（它用 ``express_menu`` 读菜单、按文字选）。开对了窗口就把 AI 用的路径记进本机（同一本账套
        这种单下次直接用，不再调 AI）；AI 走到多命令子菜单时同固定流程按选择定；AI 也定不了照 ``_recover`` 转人工。"""
        self._ai_menu_path = None
        tree = self.menu_summary()
        problem = (f"menu: {error}\n这家公司的 Express 菜单（▸ 是子菜单）：\n{tree or '(读不到)'}\n"
                   f"用 express_menu 工具按菜单文字或编号打开「{form.title}」这种单据的窗口（名字可能不同，按意思找）；"
                   f"不要按 Alt 和数字。")
        expect_text = f"已打开「{form.title}」单据窗口，处于浏览状态，没有弹窗。"
        try:
            self._recover("form_menu", problem, expect_text, {"form": form.doc_type},
                          reverify=lambda: bool(self._wait_for(opened, MENU_VERIFY_S)))
        except MenuAmbiguous as ambiguous:
            title, choice = self._choose(form, ambiguous)
            if self.menus and self._is_form(form, title or self._fg_title()):
                self.menus.put(form.doc_type, list(ambiguous.path), "")
            return title, choice
        title = self._fg_title()
        if self.menus and self._ai_menu_path:
            self.menus.put(form.doc_type, self._ai_menu_path, title[:120])
            self._event("menu_learned", path=self._ai_menu_path)
        return title, ""

    def _value_seen(self, label: str, value: str, seen: str) -> bool:
        if self.form.values.get(label) == "vat_mode":
            return seen.strip().startswith(value)
        if label in self.form.header.get("dates", []):
            return re.sub(r"\D", "", seen) == self.draft.date.replace("-", "")
        return same(value, seen)

    def walk_header(self, header_text: str) -> None:
        """表头：每按一次回车读焦点落在哪一格（句柄 → 标签），要填的格先全选再打、打完读回核对，走到 stop_after 为止。"""
        d = self.draft
        values = {label: {"date": d.date_be, "party": d.party_code, "ref": d.ref_no,
                          "vat_mode": d.vat_mode}[key] for label, key in self.form.values.items()}
        hdr = {"stage": "header"}
        self._stage = "header"
        for _ in range(30):
            st = self.state()
            self._auto_dialogs()
            focus = st.get("focus") or {}
            if focus.get("hwnd") not in self.labels:
                self.labels.update({h: f["label"] for h, f in self.field_map().items()})
            label = self.labels.get(focus.get("hwnd"), "")
            if not label:
                self._recover("header", f"focus not on a header field ({focus.get('cls')})", header_text, hdr)
                return
            self._layer("win32")
            value = values.get(label)
            if value:
                # 下拉框（价格类型）只打首字选项，不按 Home / End（会跳到第一 / 最后一项）
                select = [] if focus.get("cls") == "ComboBox" else [{"do": "keys", "text": "{HOME}+{END}"}]
                self.step(f"header.{label}", select + [{"do": "type", "text": value}, {"do": "wait", "ms": 200}],
                          header_text, hdr)
                layer, seen = self.seen(self.state())
                if layer == "none":
                    self._layer("unreadable")
                    self._unread += 1
                elif self._value_seen(label, value, seen):
                    self._layer(layer)
                else:
                    self._recover(f"header.{label}", f"'{label}' is '{seen.strip()[:40]}', want '{value}'",
                                  header_text, hdr)
            self.step(f"header.{label}", [{"do": "keys", "text": "{ENTER}"},
                                          {"do": "wait", "ms": self.form.header.get("wait_ms", 350)}], header_text, hdr)
            if label == self.form.stop_after:
                return
        self._recover("header", "30 Enters did not reach the last header field", header_text, hdr)

    def _cell(self) -> Dict:
        st = self.state()
        self._auto_dialogs()
        return st.get("focus") or {}

    def _onto_item_column(self, where: str, text: str, x: Optional[int]) -> None:
        """每行录品码前焦点要在品码栏（``x`` 落在品码栏里：第 1 行是点进明细的屏幕横坐标，之后是第 1 行品码格左边 + 1）。
        账套设了「允许插入明细行」（ISINFO.INSERTIT = Y）时最左多一栏可编辑的行号，新行的焦点停在那里（11-19 测试机2：品码打进
        行号栏只剩 3 个字，S 盘 760 本里 206 本这样设）：焦点格整个在 ``x`` 左边就按回车往右走，最多 3 次。没有这一栏的账套焦点
        本来就在品码格，什么都不多做。"""
        if x is None:
            return
        for _ in range(3):
            rect = self._cell().get("rect") or []
            if len(rect) < 4 or rect[2] > x:
                return
            self._event("line_number_column", where=where)
            self.step(where, [{"do": "keys", "text": "{ENTER}"}, {"do": "wait", "ms": 300}], text, {"stage": "line"})

    def _type_cell(self, where: str, value: str, line_text: str) -> None:
        self.step(where, [{"do": "keys", "text": "{HOME}+{END}"}, {"do": "type", "text": value},
                          {"do": "wait", "ms": 200}], line_text, {"stage": "line"})
        st = self.state()
        self._auto_dialogs()
        layer, seen = self.seen(st)
        if layer == "none":
            self._layer("unreadable")
            self._unread += 1
        elif same(value, seen):
            self._layer(layer)
        else:
            self._recover(where, f"cell is '{seen.strip()[:40]}', want '{value}'", line_text, {"stage": "line"})

    def close_unit_dialog(self, where: str, text: str, qty_x: Optional[int] = None) -> None:
        """单位框：框里回车是跳格不是确定（试点实测），所以按框里的「ตกลง」。有的商品不弹。
        Express 慢时框会晚弹（11-01 录制机）：等到框弹出来，或光标已走到右边的单价格（这件商品不弹），最多 ``UNIT_WAIT_S``；
        不等就会把单价打进晚弹的单位框，整行错位。"""
        name = self.form.spec["unit_dialog"]
        for _ in range(max(1, int(UNIT_WAIT_S * 1000 / SLOW_STEP_MS))):
            hit = [w for w in self.windows if name in self._text(w)] or \
                  [w for w in self.state().get("windows", []) if name in self._text(w)]
            if hit:
                self._layer("win32")
                self.step(where, [{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 600}], text,
                          {"stage": "line"})
                return
            rect = self._cell().get("rect") or [0, 0, 0, 0]
            if qty_x is None or rect[0] > qty_x + 8:
                return  # 光标已到单价格：这件商品不弹单位框
            self.act([{"do": "wait", "ms": SLOW_STEP_MS}], windows=False)

    def enter_lines(self) -> None:
        self._stage = "line"
        for index, line in enumerate(self.draft.lines, start=1):
            self._lines_done = index - 1  # 前面这几行已录完（AI 接手换段时据此写明已落的行）
            where = f"line{index}"
            text = (f"明细第 {index} 行已录完整：品码 {line.item_code}、仓 {line.loc or '默认'}、数量 {line.qty}、"
                    f"单价 {line.unit_price}；光标停在下一行（第 {index + 1} 行）的品码格，这一格是空的；没有弹窗；不要保存。")
            self._line_recovered = False
            if index == 1:
                self._grid_xy = None
                self.step(where, [{"do": "grid_click", "offset": self.form.spec["grid_offset"]},
                                  {"do": "wait", "ms": 400}], text, {"stage": "line"})
                item_x = (self._grid_xy or [None])[0]
            else:
                item_x = item_left + 1  # 第 1 行品码格的左边：之后每行焦点在它左边（行号栏）就往右走
            self._onto_item_column(where, text, item_x)
            start = self._cell().get("rect") or [0, 0, 0, 0]
            if index == 1:
                item_left = start[0]
            enter = lambda ms: self.step(where, [{"do": "keys", "text": "{ENTER}"}, {"do": "wait", "ms": ms}],  # noqa: E731
                                         text, {"stage": "line"})
            qty_cell: Dict = {}
            ops = [lambda: self._type_cell(f"{where}.item", line.item_code, text), lambda: enter(1000),
                   lambda: line.loc and self._type_cell(f"{where}.loc", line.loc, text), lambda: enter(400),
                   lambda: self._type_cell(f"{where}.qty", line.qty, text),
                   lambda: qty_cell.update(self._cell()), lambda: enter(900),
                   lambda: self.close_unit_dialog(where, text, (qty_cell.get("rect") or [None])[0]),
                   lambda: self._type_cell(f"{where}.price", line.unit_price, text)]
            for op in ops:
                op()
                if self._line_recovered:
                    break
            if self._line_recovered:
                self._lines_done = index
                continue  # AI 接手时已把这一行录完、光标停在下一行
            # 单价之后回车走完这一行：焦点回到下一行的品码格（同一列、往下一行）为止
            for _ in range(6):
                enter(400)
                rect = self._cell().get("rect") or [0, 0, 0, 0]
                # 到了下一行的品码格就停；账套设了「允许插入明细行」时下一行先到行号栏（在品码格左边）——也停在那里，
                # 不把空行的品码格打开（打开就填满空格，存档时 Express 拿空格当品码去查、弹选择框，11-20 测试机2）
                if rect[0] < start[0] + 8 and rect[1] > start[1] + 5:
                    self._layer("win32")
                    break
            else:
                self._recover(where, "Enter did not reach the next line", text, {"stage": "line"})
            self._lines_done = index

    def check_totals(self, before_text: str) -> bool:
        """保存前读底部合计（不截图）：都和草稿对上才存。「จำนวนเงิน」按含税价录时显示含税额。回「是否已看过一眼」。"""
        want = self.draft.totals()
        if self.draft.vat_included:
            want = dict(want, net=want["total"])
        boxes: Dict[str, List[Dict]] = {}
        for f in self.field_map().values():
            if f["label"]:
                boxes.setdefault(f["label"], []).append(f)
        problems, read = [], 0
        for key, label in self.form.spec["totals"].items():
            cands = sorted(boxes.get(label, []), key=lambda f: f["rect"][0])
            seen = re.sub(r"[^\d.\-]", "", cands[-1]["text"]) if cands else ""  # 同一行有比率和金额两格时取最右的金额格
            if not seen:
                continue
            read += 1
            if Decimal(seen) != want[key]:
                problems.append(f"{label} {seen} != {want[key]}")
        if read and not problems:
            self._layer("win32")
            return False
        expect = {"stage": "before_save", "lines": len(self.draft.lines), "total": str(want["total"])}
        if problems:
            self._recover("before_save", "; ".join(problems), before_text, expect)
        else:
            self.checkpoint("before_save", before_text, expect)
        return True

    def run_fixed(self) -> str:
        d = self.draft
        T = d.doc_type
        self.ensure_form(T)
        header_text = (f"正在新建一张 {T} 单（单号显示 {T}**NEW**），表头对方码 {d.party_code}、日期 {d.date_be}"
                       + (f"、对方单号 {d.ref_no}" if d.ref_no else "") + "，没有弹窗。")
        self.command("new")
        self.act([{"do": "wait", "ms": 1200}])
        # Express 慢、或人刚好在这时点了 Express（11-01 录制机：「新建」晚了一会儿才生效，只读一次就判成画面不对）：限时再读
        if self.in_edit() or self._wait_for(self.in_edit, SLOW_FORM_S):
            self._layer("win32")
        else:
            self._recover("new", "not in new-document state", header_text, {"stage": "header"})
        self.walk_header(header_text)
        self.enter_lines()
        total = d.totals()["total"]
        before_text = f"新建中的 {T} 单，明细共 {len(d.lines)} 行，含税合计 {total}，没有弹窗，还没保存。"
        looked = self.check_totals(before_text)
        if not looked and self._unread:
            self.checkpoint("before_save", before_text, {"stage": "before_save", "lines": len(d.lines),
                                                         "total": str(total)})
        saved_text = f"单号栏是正式单号（{T} 后面跟数字，不含 NEW），没有弹窗。"
        self._save_pressed = True  # 从这里起出的任何意外都先读账套核这张存没存（_after_save_trouble）
        self.command("save")
        self.act([{"do": "wait", "ms": self.recipe.save_wait_ms}])
        try:
            self._auto_dialogs()
        except Unexpected as e:
            self._recover("save", str(e), saved_text, {"stage": "saved", "saved": True},
                          reverify=lambda: self.saved_check(T) or None)
        unread = False
        for _ in range(8):
            docnum = self.saved_check(T)
            if docnum:
                self._layer("win32")
                self._event("saved", docnum=docnum)
                return docnum
            if self.recipe.commands["save"] not in self.toolbar_on() and not self.docnum_text():
                unread = True
                break  # 「保存」已灰、单号格读不到字：看一眼画面，单号留给读账套核对
            self.act([{"do": "wait", "ms": 600}], windows=False)
            self._auto_dialogs()

        def saved() -> str:
            self.act([{"do": "wait", "ms": 0}])  # 刷新弹窗清单：存档途中弹出的认得的框照常处理
            self._auto_dialogs()
            return self.saved_check(T)

        docnum = "" if unread else self._wait_for(saved, SLOW_SAVE_S)  # 还在存（「保存」还亮着）：多等一会儿再判
        if docnum:
            self._layer("win32")
            self._event("saved", docnum=docnum)
            return docnum
        # 画面读不出单号：读账套认这张（对方 + 日期 + 对方单号 + 含税合计，同断点续录）。CC 里没接看图（helper 是 NoHelper），
        # 原来这里只能交给看图、判不了就作废（11-01 录制机：存对了的单因单号格一时读不到字被作废，HP0000125）
        docnum = self._wait_for(self._booked, SLOW_SAVE_S)
        if docnum:
            self._layer("dbf")
            self._event("saved", docnum=docnum, by="book")
            return docnum
        self.checkpoint("after_save", saved_text, {"stage": "saved", "saved": True}, reverify=lambda: self.saved_check(T))
        return self.saved_check(T) or SAVED_UNREAD

    def abandon(self) -> List[str]:
        """放弃这张：关掉提醒框 → 编辑中就点「取消」（还没明细时 Express 问 1312 → 按「ตกลง」，不写任何东西）→
        已有明细时「取消」会直接存档（试点实测）：读单号格确认存了 → 作废命令 → 确认框按「ตกลง」。回作废的单号。"""
        voided: List[str] = []
        try:
            for w in self._dialogs():
                rule = self._match(w)
                if rule is None or rule.get("outcome") == "crash":
                    raise Unexpected("unknown dialog left open")  # 认不出的框不乱点，留给人
                button = rule.get("close", "ตกลง")  # 选择框按「ตกลง」会选中第一项：配方写明用「ยกเลิก」关
                if button in " ".join(w.get("texts", [])).replace("&", ""):
                    self.act([{"do": "dialog_button", "text": button}, {"do": "wait", "ms": 600}])
            if self.in_edit():
                self.command("cancel")
                self.act([{"do": "wait", "ms": 900}])
                if any("1312" in w.get("title", "") for w in self.windows):
                    self.act([{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 900}])
                # 有明细时「取消」就是存档：存档途中的提问（如服务品进项税口径，10-29 实机）照配方答完，存好了才能作废；
                # 拦单的、认不出的抛出去，由下面的 except 记下、留给人
                self._auto_dialogs()
                # Express 慢时存档要一会儿：等它落下再读单号（11-01 录制机：只读一次没读到就当没存，半截单 HP0000123 留成了有效单）
                self._wait_for(lambda: not self.in_edit(), SLOW_SAVE_S)
            docnum = self.saved_check(self.draft.doc_type)
            # 两道硬保护（10-26 实机：取消后 Express 的单号格显示的是上一张已存的单，旧逻辑据此误作废了三张好单
            # HP0000026 / IV0000022 / HS0000007）：①账套里这一张开录后才新出现的单号；②本批次开录后新建、且不是本批次
            # 已存好的单（批次给的 may_void）。任何一道对不上都不作废，转「需要你」。
            if docnum and not (self._ours(docnum) and self.may_void(docnum)):
                self._event("abandon_skip_void", docnum=docnum)
                if self._ours(docnum):
                    self.rec["void_refused"] = docnum
                docnum = ""
            if docnum:
                self.command("void")
                self.act([{"do": "wait", "ms": 1000}])
                if any("ยกเลิกเอกสาร" in " ".join(w.get("texts", [])) for w in self._dialogs()):
                    self.act([{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 1500}])
                voided.append(docnum)
                self._report("voided", docnum=docnum)
            self._event("abandon", ok=not self.in_edit())
        except Exception as e:  # noqa: BLE001 — 收尾失败不掩盖原来的原因；画面状态不明，下一张从菜单重开
            self._event("abandon", ok=False, error=str(e)[:200])
            self.form_open = None
            # 认不出的框留在一张编辑中的单上：Express 原样留给人（退出就把单断在半截），再交来先读账套
            self.rec.update(held=True, recheck=True)
        return voided

    def _screen_note(self) -> str:
        """判不了画面时记下当时看到的（进「未完成」的明细，给人和排查用）：Express 当前窗口标题、新弹出的框里的字、单号格。"""
        try:
            fg = self._fg_title()
            boxes = [" ".join(w.get("texts", []))[:80] for w in self._dialogs()]
            number = self.docnum_text() if self.form else ""
        except Exception as e:  # noqa: BLE001 — 记不下来不影响交给人
            return f"note failed: {e}"[:120]
        return f"fg={fg.strip()[:60]} | dialogs={boxes} | docnum={number}"

    def _booked(self) -> str:
        """账套里这一张开录之后新出现、对方 / 日期 / 对方单号 / 含税合计都对上的单号；读不了、还没有回空串。"""
        if self._before is None or self.draft is None:
            return ""
        try:
            return B.find_saved(self.draft, self.book.rows_since(self._before))["docnum"]
        except Exception:  # noqa: BLE001 — 读不了就当还没看到，交给下面的判断
            return ""

    def _ours(self, docnum: str) -> bool:
        """这个单号是不是这一张开录之后才在账套里出现的（读开录前记下的行数之后的新行）。读不了就当不是，不作废。"""
        if self._before is None:
            return False
        try:
            rows = self.book.rows_since(self._before)
        except Exception:  # noqa: BLE001
            return False
        return any(str(h.get("DOCNUM") or "").strip() == docnum for h in B.heads_for(self.draft, rows))

    # ── 一张单 ──────────────────────────────────────────────────────────────
    def begin(self, draft: Optional[Draft], doc_type: str = "") -> None:
        """开始一段记录（一张单，或探路时 ``draft=None``）。"""
        self.draft = draft
        self._save_pressed = False
        self._touched = False
        doc_type = draft.doc_type if draft else doc_type
        self.form = self.recipe.form(doc_type) if doc_type else None
        self._t0 = time.monotonic()
        self._unread = 0
        self._lines_done = 0
        self.rec = {"doc_id": draft.doc_id if draft else "", "doc_type": doc_type, "events": [], "reports": [],
                    "layers": {}, "checkpoints": 0, "fallbacks": [], "shots_ai": 0, "shots_evidence": 0, "voided": [],
                    "outcome": "", "reason": "", "detail": "", "docnum": "", "readback": None,
                    "placeholders": []}

    def _need_human(self, draft: Draft, e: NeedHuman) -> None:
        """保存键按下之前就转人工：截画面、按放弃流程收尾（作废两道闸照旧）。"""
        self.rec.update(outcome="need_human", reason=e.reason, detail=e.detail[:K.DETAIL_MAX])
        if isinstance(e, MenuChoice):
            self.rec["menu_options"] = list(e.options)
        if isinstance(e, DialogHeld):
            self.rec["dialog"] = dict(e.dialog)
        if isinstance(e, Interrupted):
            self.rec["interrupt"] = dict(e.interrupt)
        self._need_human_shot(draft.doc_id)  # 作废前截下转人工那一刻的画面（2026-11-03：原来只截存成的单）
        self.rec["voided"] = self.abandon()

    def _after_save_trouble(self, draft: Draft, e: Exception) -> str:
        """保存键按下之后出了认不出的画面（11-12 IV0000043、11-13 IV0000045）。按类处理，不只哪一个框：

        - 先读账套（含凭证表）判这张：已完整 → 回单号（不放弃、不作废、不点 Express 上的东西）；
        - 没存完（半截：单头在、金额 / 明细 / 凭证 / 税缺一样或几样）→ 转人工，原因 ``partial_saved``，带单号与缺什么；
        - 什么都没看到 → 转人工（原因照旧）；
        - 后两种都 **不碰** Express 上的东西（「取消」在有明细时就是存档、作废闸也可能作废一张存好的单），标上 ``recheck``（再交来
          先读账套、绝不从头重录）和 ``held``（Express 原样留着等人答框，批次收尾 / 闲置都不退出 Express：一退出存档就断在半截）。"""
        reason = getattr(e, "reason", "unknown_screen")
        detail = str(getattr(e, "detail", "") or e)
        self.form_open = None  # Express 上留着认不出的东西：下一张从菜单重开（撞上就停在开窗那一步，不会再按保存）
        self._wait_for(self._booked, SLOW_SAVE_S)  # 存档慢：等单头金额落下（只读单据表，便宜）
        found = self._book_state()
        if found.get("docnum"):
            self._layer("dbf")
            self._event("saved", docnum=found["docnum"], by="book", after=reason, screen=detail[:200])
            self.rec.update(outcome="saved", screen_left=detail[:K.DETAIL_MAX])
            return found["docnum"]
        self.rec.update(outcome="need_human", reason=reason, detail=detail[:K.DETAIL_MAX], recheck=True, held=True)
        if found.get("partial"):
            self.rec.update(reason=PARTIAL, partial=dict(found["partial"][-1]))
        if isinstance(e, Interrupted):  # 保存途中人动过：账套里存完了上面已回单号；没存完照实交人
            self.rec["interrupt"] = dict(e.interrupt, stage="saving")
            if not found.get("partial"):
                self.rec["reason"] = INTERRUPTED
        if isinstance(e, DialogHeld):  # 保存途中的业务选择框：框的样子照带；账套里已留半截单仍按 partial_saved（1.12.22 的补报照旧）
            self.rec["dialog"] = dict(e.dialog)
            if not found.get("partial"):
                self.rec["reason"] = DIALOG_CHOICE
        self._event("held", partial=(found.get("partial") or [{}])[-1].get("docnum", ""))
        self._need_human_shot(draft.doc_id)
        return ""

    def _book_state(self) -> Dict:
        """读账套判这张（``book.state_of``）；读不了回空（当成没看到，照样转人工、不重录）。"""
        if self._before is None or self.draft is None:
            return {}
        try:
            return B.state_of(self.draft, self.book, self._before)
        except Exception:  # noqa: BLE001
            return {}

    def run(self, draft: Draft) -> Dict:
        """录一张。回 ``{"outcome": saved / need_human / blocked / crash, "reason", "docnum", "voided", "readback", ...}``。
        Stopped 原样往上抛（这张留在「正在录」，续录时先读账套判断）。"""
        self.begin(draft)
        if self.form is None or not self.form.verified:
            self.rec.update(outcome="need_human", reason="unsupported")
            return self.rec
        started = time.monotonic()
        before = self.book.counts()
        self._before = before
        self._time("book_counts", time.monotonic() - started)
        docnum = ""
        try:
            docnum = self.run_fixed()
            self.rec["outcome"] = "saved" if docnum else "need_human"
            if not docnum:
                self.rec["reason"] = "unknown_screen"
            if docnum == SAVED_UNREAD:
                docnum = ""
        except (NeedHuman, Unexpected) as e:
            if not self._save_pressed:
                if isinstance(e, Unexpected):
                    raise
                self._need_human(draft, e)
            else:
                docnum = self._after_save_trouble(draft, e)
        except Blocked as e:
            self.rec.update(outcome="blocked", reason=e.reason, detail=e.detail[:K.DETAIL_MAX])
            self.rec["voided"] = self.abandon()
        except Crash as e:
            self.rec.update(outcome="crash", reason=e.reason, detail=e.detail[:K.DETAIL_MAX])
            self.form_open = None
        if self.rec["outcome"] != "crash":
            try:
                started = time.monotonic()
                rows = self.book.rows_since(before)
                self._time("book_readback", time.monotonic() - started)
                result = B.compare(draft, rows, docnum, check_ref="ref" in self.form.values.values())
                self._layer("dbf")
                self.rec["readback"] = result
                if self.rec["outcome"] == "saved" and not result["ok"]:
                    self.rec.update(outcome="need_human", reason="readback_mismatch", docnum=result["docnum"], recheck=True)
                elif self.rec["outcome"] == "saved":
                    self.rec["docnum"] = result["docnum"] or docnum
                else:  # 没存成：录到明细时 Express 已写的金额 0 空单头（没作废掉的）报给人
                    self.rec["placeholders"] = [n for n in B.find_saved(draft, rows)["placeholders"]
                                                if n not in self.rec["voided"]]
            except Exception as e:  # noqa: BLE001 — 读不回不当成录对了
                self.rec["readback"] = {"ok": False, "error": str(e)[:200]}
                if self.rec["outcome"] == "saved":
                    self.rec.update(outcome="need_human", reason="readback_mismatch", docnum=docnum, recheck=True)
            if self.rec["outcome"] == "saved" and self.evidence:
                started = time.monotonic()
                self.evidence(draft.doc_id, self.screenshot("saved"))
                self._time("evidence", time.monotonic() - started)
        if self.rec["outcome"] in ("need_human", "crash"):
            self.form_open = None  # 画面状态不明，下一张从菜单重新打开
        self.rec["seconds"] = round(time.monotonic() - self._t0, 1)
        return self.rec
