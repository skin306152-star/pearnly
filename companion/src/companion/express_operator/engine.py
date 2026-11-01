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

import re
import time
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import book as B
from .draft import Draft
from .helper import NoHelper, png_block
from .recipe import Form, Recipe

MAX_TAKEOVERS_PER_DOC = 3
# 固定流程读画面的宽限（11-01 录制机实测：另开着一个 Express、网络账套忙时，第一次开表单、存档都比平常慢；只读一次就判成
# 画面不对、作废了存对的单）。按等待次数算（测试里的等待是假的）；中途有人在本批的 Express 里动过就等人停手、重新计时再读
SLOW_STEP_MS = 500
SLOW_FORM_S = 15
SLOW_SAVE_S = 20
SAVED_UNREAD = "?"  # 画面核对已存上、单号格读不到字：单号以读账套为准
INPUT_OPS = {"keys", "key", "type", "paste", "click", "drag", "scroll", "grid_click", "command", "dialog_button", "menu",
             "close_form",
             "activate", "us_layout", "move", "mouse"}


class NeedHuman(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


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
                 may_void: Callable[[str], bool] = lambda _docnum: True):
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
        paused = bool(self.presence())
        self._time("paused", time.monotonic() - started)
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
            self.act([{"do": "dialog_button", "text": rule["button"]}, {"do": "wait", "ms": 600}])
        raise Unexpected("dialog does not close")

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
        answer = self.helper.check(png, f"期望：{expect_text}\n草稿单：\n{self._describe()}")
        self.rec["checkpoints"] += 1
        self._layer("vision")
        problem = f"no helper; {self._screen_note()}" if answer is None else self._judge(answer, expect)
        self._event("check", where=where, problem=problem)
        if not problem:
            return True
        if not recover:
            return False
        self._recover(where, problem, expect_text, expect)
        return True

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
        if len(self.rec["fallbacks"]) > MAX_TAKEOVERS_PER_DOC:
            raise NeedHuman("unknown_screen", f"{where}: {problem}")  # 同一张反复出意外：不再烧钱，交给人
        goal = (f"固定流程在「{where}」这一步出了意外：{problem}\n你的目标：处理掉意外，让画面达到「{expect_text}」。\n"
                + ("这一步本来就是保存：确认保存成功即可，不要重复保存。\n" if expect.get("saved") else "不要保存。\n")
                + f"草稿单：\n{self._describe()}\n做完调用 finish。")
        status, note = self.helper.take_over(goal, self)
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
        raise NeedHuman("unknown_screen", f"{where}: {problem}")

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
        raise ValueError(f"unsupported member {name}")

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
    def ensure_form(self, doc_type: str) -> None:
        form = self.recipe.form(doc_type)
        self.form = form
        fg = (self.act([{"do": "activate"}, {"do": "wait", "ms": 300}]).get("fg") or {}).get("title", "")
        if self.form_open == doc_type and form.title in fg:
            return
        # 一次只开一种单据窗口：每个单据窗口各带一条工具栏，开着几个时读不准哪条（试点实测）。Ctrl+F4 关掉当前的
        for _ in range(6):
            if "[" not in fg:
                break
            self.act([{"do": "close_form"}, {"do": "wait", "ms": 1200}])
            if any("1312" in w.get("title", "") for w in self.windows):
                self.act([{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 1000}])
            fg = (self.act([{"do": "wait", "ms": 100}]).get("fg") or {}).get("title", "")
        # 菜单路径（数字）：真键盘做法是先单独按 Alt 再按数字；后台做法按菜单项文字找到命令号直接发（desk 决定）
        title = (self.act([{"do": "menu", "path": form.menu}, {"do": "wait", "ms": self.recipe.form_open_ms}]).get("fg")
                 or {}).get("title", "")
        self._event("form_open", doc_type=doc_type)
        opened = lambda: form.title in self._fg_title()  # noqa: E731
        if form.title in title or self._wait_for(opened, SLOW_FORM_S):
            self._layer("win32")
        else:
            self.checkpoint("form_open", f"已打开「{form.title}」单据窗口，处于浏览状态，没有弹窗。", {"form": doc_type},
                            reverify=opened)
        self.form_open = doc_type
        self.baseline = {w["hwnd"] for w in self.windows}
        self.labels = {}

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

    def close_unit_dialog(self, where: str, text: str) -> None:
        """单位框：框里回车是跳格不是确定（试点实测），所以按框里的「ตกลง」。有的商品不弹。"""
        name = self.form.spec["unit_dialog"]
        hit = [w for w in self.windows if name in self._text(w)] or \
              [w for w in self.state().get("windows", []) if name in self._text(w)]
        if hit:
            self._layer("win32")
            self.step(where, [{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 600}], text,
                      {"stage": "line"})

    def enter_lines(self) -> None:
        self._stage = "line"
        for index, line in enumerate(self.draft.lines, start=1):
            where = f"line{index}"
            text = (f"明细第 {index} 行已录完整：品码 {line.item_code}、仓 {line.loc or '默认'}、数量 {line.qty}、"
                    f"单价 {line.unit_price}；光标停在下一行（第 {index + 1} 行）的品码格，这一格是空的；没有弹窗；不要保存。")
            self._line_recovered = False
            if index == 1:
                self.step(where, [{"do": "grid_click", "offset": self.form.spec["grid_offset"]},
                                  {"do": "wait", "ms": 400}], text, {"stage": "line"})
            start = self._cell().get("rect") or [0, 0, 0, 0]
            enter = lambda ms: self.step(where, [{"do": "keys", "text": "{ENTER}"}, {"do": "wait", "ms": ms}],  # noqa: E731
                                         text, {"stage": "line"})
            ops = [lambda: self._type_cell(f"{where}.item", line.item_code, text), lambda: enter(1000),
                   lambda: line.loc and self._type_cell(f"{where}.loc", line.loc, text), lambda: enter(400),
                   lambda: self._type_cell(f"{where}.qty", line.qty, text), lambda: enter(900),
                   lambda: self.close_unit_dialog(where, text),
                   lambda: self._type_cell(f"{where}.price", line.unit_price, text)]
            for op in ops:
                op()
                if self._line_recovered:
                    break
            if self._line_recovered:
                continue  # AI 接手时已把这一行录完、光标停在下一行
            # 单价之后回车走完这一行：焦点回到下一行的品码格（同一列、往下一行）为止
            for _ in range(6):
                enter(400)
                rect = self._cell().get("rect") or [0, 0, 0, 0]
                if abs(rect[0] - start[0]) < 8 and rect[1] > start[1] + 5:
                    self._layer("win32")
                    break
            else:
                self._recover(where, "Enter did not reach the next line", text, {"stage": "line"})

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
        if self.in_edit():
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
        doc_type = draft.doc_type if draft else doc_type
        self.form = self.recipe.form(doc_type) if doc_type else None
        self._t0 = time.monotonic()
        self._unread = 0
        self.rec = {"doc_id": draft.doc_id if draft else "", "doc_type": doc_type, "events": [], "reports": [],
                    "layers": {}, "checkpoints": 0, "fallbacks": [], "shots_ai": 0, "shots_evidence": 0, "voided": [],
                    "outcome": "", "reason": "", "detail": "", "docnum": "", "readback": None,
                    "placeholders": []}

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
        except NeedHuman as e:
            self.rec.update(outcome="need_human", reason=e.reason, detail=e.detail[:300])
            self.rec["voided"] = self.abandon()
        except Blocked as e:
            self.rec.update(outcome="blocked", reason=e.reason, detail=e.detail[:300])
            self.rec["voided"] = self.abandon()
        except Crash as e:
            self.rec.update(outcome="crash", reason=e.reason, detail=e.detail[:300])
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
                    self.rec.update(outcome="need_human", reason="readback_mismatch", docnum=result["docnum"])
                elif self.rec["outcome"] == "saved":
                    self.rec["docnum"] = result["docnum"] or docnum
                else:  # 没存成：录到明细时 Express 已写的金额 0 空单头（没作废掉的）报给人
                    self.rec["placeholders"] = [n for n in B.find_saved(draft, rows)["placeholders"]
                                                if n not in self.rec["voided"]]
            except Exception as e:  # noqa: BLE001 — 读不回不当成录对了
                self.rec["readback"] = {"ok": False, "error": str(e)[:200]}
                if self.rec["outcome"] == "saved":
                    self.rec.update(outcome="need_human", reason="readback_mismatch", docnum=docnum)
            if self.rec["outcome"] == "saved" and self.evidence:
                started = time.monotonic()
                self.evidence(draft.doc_id, self.screenshot("saved"))
                self._time("evidence", time.monotonic() - started)
        if self.rec["outcome"] in ("need_human", "crash"):
            self.form_open = None  # 画面状态不明，下一张从菜单重新打开
        self.rec["seconds"] = round(time.monotonic() - self._t0, 1)
        return self.rec
