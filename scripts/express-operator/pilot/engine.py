"""试点执行器：草稿单（产品识别结果）→ 键盘固定流程 → 不截图读状态核对 → 录完读 DBF → 只在都判断不了时截图给 AI。

按主会话 10-26 定的优先级分四层，每层记覆盖次数：
1. **键盘**：菜单编号、回车 / Tab、剪贴板 + Shift+Insert，按操作手册的顺序走，不靠定位格子
   （工具栏「新建 / 保存」没找到快捷键前用坐标，单独记次数）；
2. **Windows 系统接口读状态（不截图）**：每格打完读焦点控件文字（Win32 WM_GETTEXT / UI Automation），
   每步读 Express 的顶层窗口清单认弹窗（4079、4224、-70 …）；
3. **读 DBF**：保存后看单头表行数确认存上；录完逐字段核对草稿单和标准答案；
4. **截图给 AI**：前三层判断不了才用——读不到字的弹窗、核对不符、读不到格子内容时保存前看一眼；
   出意外先 Haiku 5.5 接手，不行 Opus 5.5。

方式（--mode）：haiku_fixed（主推）/ opus_fixed / opus_vision（全程看图，只跑几张）；直接写入见 direct_write.py。
"""
from __future__ import annotations

import base64
import re
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import compare as C
from . import models as M
from . import pricing
from .draft import Draft
from .ledger import BudgetStop, Ledger

MODES = {
    "haiku_fixed": {"check": pricing.HAIKU, "chain": [(pricing.HAIKU, 8, "medium"), (pricing.OPUS, 14, "medium")]},
    "opus_fixed": {"check": pricing.OPUS, "chain": [(pricing.OPUS, 14, "medium")]},
    "opus_vision": {"check": pricing.OPUS, "chain": [], "vision": (pricing.OPUS, 60, "medium")},
}
DOC_NAMES = {"RR": "赊购单（ซื้อเงินเชื่อ）", "HP": "现购单（ซื้อเงินสด）", "IV": "赊销单（ขายเงินเชื่อ）",
             "HS": "现销单（ขายเงินสด）"}
PROMPT_LIMIT = 95_000  # 单张对话的提示上限（Haiku 超 10 万 token 单价翻 5 倍；也是任务书的口径）


class NeedHuman(Exception):
    def __init__(self, where: str, why: str):
        super().__init__(f"{where}: {why}")
        self.where, self.why = where, why


class Crash(Exception):
    """Express 没了，或弹了网络读写错误（-70）。按手册：先别点，停下查网络。"""


class Blocked(Exception):
    """负库存被 Express 拦下（4224 / 4091）：这张单录不进去，是业务结果，不是操作失误。"""


class Unexpected(Exception):
    """固定流程这一步之后状态不对（认不出的弹窗、格子内容不符、该出的框没出）。交给 AI 接手。"""


def _norm(text: str) -> str:
    # Express 把码里的不换行空格显示成「•」（10-26 实测客户码「นาย ทดสอบ」），核对时当成同一个字符
    return re.sub(r"[\s,\u2022\u00a0]", "", str(text or "")).upper()


def same(want: str, seen: str) -> bool:
    """格子里读到的和要打的是不是一回事：数字按数值比（1,000.00 = 1000），其余去空格、逗号后全等。"""
    a, b = _norm(want), _norm(seen)
    try:
        return Decimal(a) == Decimal(b)
    except Exception:  # noqa: BLE001
        return a == b


class Engine:
    def __init__(self, agent, claude, ledger: Ledger, flows: Dict, book: str, mode: str, out_dir: Path,
                 *, presave_check: str = "auto", evidence: bool = True, log: Callable[[str], None] = print):
        self.agent, self.claude, self.ledger, self.flows, self.book = agent, claude, ledger, flows, book
        self.mode_name, self.mode = mode, MODES[mode]
        self.out_dir = Path(out_dir)
        self.presave_check = presave_check  # auto：本张有格子读不到才在保存前截图核对；always / never
        self.evidence = evidence  # 保存后截一张图留证（只存本机，不给模型）
        self.log = log
        self.form: Optional[str] = None
        self.baseline: set = set()
        self.dialogs_visible: Optional[bool] = None  # 弹窗是不是独立窗口、读得到字（第一次真跑才知道）
        self.windows: List[Dict] = []
        sw, sh = flows["screen"]
        self.shot_size = (1280, 720)
        self.scale = sw / self.shot_size[0]
        self.rules = [dict(r, rx=re.compile(r["match"])) for r in flows["dialogs"]]
        self.rec: Dict = {}
        self.draft: Optional[Draft] = None
        self._unread = 0
        self._line_recovered = False
        self.form_spec: Dict = {}
        self.labels: Dict[int, str] = {}

    # ── 记录 ────────────────────────────────────────────────────────────────
    def _event(self, kind: str, **data) -> None:
        self.rec["events"].append({"t": round(time.monotonic() - self._t0, 2), "kind": kind, **data})

    def _layer(self, name: str, n: int = 1) -> None:
        self.rec["layers"][name] = self.rec["layers"].get(name, 0) + n

    def _usage(self, model: str, usage, purpose: str, seconds: float) -> None:
        usd = self.ledger.record(model=model, usage=usage, purpose=purpose, doc_id=self.draft.doc_id,
                                 mode=self.mode_name, seconds=seconds)
        self.rec["usd"] = str(Decimal(self.rec["usd"]) + usd)
        by = self.rec["usd_by_model"]
        by[model] = str(Decimal(by.get(model, "0")) + usd)
        self.rec["calls"] += 1
        self.rec["api_s"] = round(self.rec["api_s"] + seconds, 2)
        self.rec["max_prompt"] = max(self.rec["max_prompt"], M.prompt_size(usage))

    # ── 代理 ────────────────────────────────────────────────────────────────
    def act(self, actions: List[Dict], windows: bool = True) -> Dict:
        t = time.monotonic()
        reply = self.agent.act(actions, windows)
        self.rec["agent_s"] = round(self.rec.get("agent_s", 0) + time.monotonic() - t, 2)
        for a in actions:
            if a["do"] in ("keys", "key", "type", "paste"):
                self._layer("keys")
            elif a["do"] == "click":
                self._layer("clicks")
        if windows:
            self.windows = reply.get("windows", [])
            self._health()
        for res in reply["results"]:
            if not res.get("ok") and not res.get("skipped"):
                if res.get("refused"):
                    raise Unexpected(f"守卫拒绝：{res['error']}")
                raise Unexpected(f"动作出错：{res['error']}")
        return reply

    def _health(self) -> None:
        if not self.windows:
            raise Crash("Express 窗口没了（进程退出？）")
        for w in self.windows:
            rule = self._match(w)
            if rule and rule.get("outcome") == "crash":
                raise Crash(f"Express 报错：{self._text(w)[:200]}")

    @staticmethod
    def _text(w: Dict) -> str:
        return (w.get("title", "") + " " + " ".join(w.get("texts", []))).strip()

    def _match(self, w: Dict) -> Optional[Dict]:
        text = self._text(w)
        for rule in self.rules:
            if rule["rx"].search(text):
                return rule
        return None

    def _dialogs(self) -> List[Dict]:
        return [w for w in self.windows if w["hwnd"] not in self.baseline]

    def _auto_dialogs(self) -> None:
        """第 2 层：认得出的弹窗（独立窗口、读得到字）按规则处理；认不出的交给 AI。"""
        for _ in range(4):
            pending = []
            for w in self._dialogs():
                rule = self._match(w)
                if rule is None:
                    raise Unexpected(f"认不出的弹窗：{self._text(w)[:200] or '（读不到字）'}")
                if rule.get("auto"):
                    pending.append((w, rule))
            if not pending:
                return
            w, rule = pending[0]
            self.dialogs_visible = True
            self._layer("win32")
            if rule.get("outcome") == "blocked":
                raise Blocked(self._text(w)[:200])
            self._event("dialog", name=rule["name"], how="win32")
            press = {"do": "dialog_button", "text": rule["button"]} if rule.get("button") else {"do": "keys", "text": rule["keys"]}
            self.act([press, {"do": "wait", "ms": 600}])
        raise Unexpected("弹窗关不掉")

    def screenshot(self, name: str, for_ai: bool = True) -> str:
        reply = self.act([{"do": "screenshot", "size": list(self.shot_size)}])
        png = reply["results"][0]["png"]
        shots = self.out_dir / "shots" / self.draft.doc_id
        shots.mkdir(parents=True, exist_ok=True)
        (shots / f"{len(self.rec['events']):03d}-{name}.png").write_bytes(base64.b64decode(png))
        self.rec["shots_ai" if for_ai else "shots_evidence"] += 1
        return png

    # ── 第 2 层：读焦点格 ───────────────────────────────────────────────────
    def read_focus(self) -> Tuple[str, str]:
        """回 (层, 文字)：win32 / uia 读到焦点格的文字，读不到回 ("none", "")。"""
        st = self.act([{"do": "state"}], windows=False)["results"][0]
        win = (st.get("focus") or {}).get("text") or ""
        if win.strip():
            return "win32", win
        value = (st.get("uia") or {}).get("value") or ""
        if value.strip():
            return "uia", value
        return "none", ""

    def verify_typed(self, where: str, value: str, expect_text: str, expect: Dict) -> None:
        layer, seen = self.read_focus()
        if layer == "none":
            self._layer("unreadable")
            self._unread += 1
            return
        if same(value, seen):
            self._layer(layer)
            return
        self._event("mismatch", where=where, want=value, seen=seen[:80], layer=layer)
        self._recover(where, f"格子里是「{seen[:60]}」，应为「{value}」", expect_text, expect)

    # ── 第 4 层：模型 ────────────────────────────────────────────────────────
    def checkpoint(self, where: str, expect_text: str, expect: Dict, *, model: Optional[str] = None,
                   recover: bool = True) -> bool:
        model = model or self.mode["check"]
        png = self.screenshot(where)
        prompt = f"期望：{expect_text}\n草稿单：\n{self.draft.describe()}"
        self.ledger.guard(model, 4000, 2000)
        t = time.monotonic()
        answer, usage = self.claude.checkpoint(model, png, prompt, expect)
        self._usage(model, usage, f"check:{where}", time.monotonic() - t)
        self.rec["checkpoints"] += 1
        self._layer("vision")
        problem = self._judge(answer, expect)
        self._event("check", where=where, model=model, state=answer.get("state"), problem=problem,
                    saw=answer.get("what_i_see", "")[:120])
        if not problem:
            return True
        if not recover:
            return False
        self._recover(where, problem, expect_text, expect)
        return True

    def _judge(self, answer: Dict, expect: Dict) -> str:
        state = answer.get("state")
        if state == "dialog":
            return f"有弹窗：{answer.get('message_no', '')} {answer.get('dialog_text', '')[:150]}"
        if state != "as_expected":
            return f"画面不符（{state}）：{answer.get('what_i_see', '')[:150]}"
        if "lines" in expect and answer.get("lines_seen") not in (None, expect["lines"]):
            return f"明细行数看到 {answer.get('lines_seen')}，应为 {expect['lines']}"
        if "total" in expect and answer.get("total_seen"):
            seen = re.sub(r"[^\d.]", "", answer["total_seen"])
            if seen and Decimal(seen) != Decimal(expect["total"]):
                return f"合计看到 {answer['total_seen']}，应为 {expect['total']}"
        if expect.get("saved") and answer.get("docnum_seen", "").upper().endswith("NEW"):
            return f"单号还是 {answer['docnum_seen']}，没存上"
        return ""

    def _recover(self, where: str, problem: str, expect_text: str, expect: Dict, reverify=None) -> None:
        entry = {"where": where, "problem": problem[:200], "resolved_by": ""}
        self.rec["fallbacks"].append(entry)
        if not self.mode["chain"]:
            raise NeedHuman(where, problem)
        goal = (f"固定流程在「{where}」这一步出了意外：{problem}\n"
                f"你的目标：处理掉意外，让画面达到「{expect_text}」。\n"
                + ("这一步本来就是保存：确认保存成功即可，不要重复保存。\n" if expect.get("saved") else "不要保存。\n")
                + f"草稿单：\n{self.draft.describe()}\n做完调用 finish。")
        for model, turns, effort in self.mode["chain"]:
            status, note = self.cu_loop(model, goal, turns, effort, f"fallback:{where}")
            self._event("fallback", where=where, model=model, status=status, note=note[:200])
            if status == "need_human":
                raise NeedHuman(where, note)
            if status == "resolved":
                # 复核先用第 2 层（不截图），判断不了才截图给模型
                ok = reverify() if reverify else None
                if ok is None:
                    ok = self.checkpoint(f"{where}.verify", expect_text, expect, recover=False)
                if ok:
                    entry["resolved_by"] = model
                    if where.startswith("line"):
                        self._line_recovered = True
                    return
        raise NeedHuman(where, f"AI 接手后仍不对：{problem}")

    def cu_loop(self, model: str, goal: str, max_turns: int, effort: str, purpose: str) -> Tuple[str, str]:
        png = self.screenshot(purpose.replace(":", "-"))
        messages: List[Dict] = [{"role": "user", "content": [{"type": "text", "text": goal}, M.png_block(png)]}]
        last_prompt = 0
        for _ in range(max_turns):
            estimate = (last_prompt + 4000) if last_prompt else 14000
            if estimate > PROMPT_LIMIT:
                return "need_human", "单张对话快到 10 万 token 上限"
            self.ledger.guard(model, estimate, 8000)
            t = time.monotonic()
            resp = self.claude.cu_turn(model, messages, effort)
            self._usage(model, M.usage_of(resp), purpose, time.monotonic() - t)
            self.rec["cu_turns"] += 1
            last_prompt = M.prompt_size(M.usage_of(resp))
            if resp.stop_reason == "refusal":
                return "need_human", "模型拒答"
            content = M.blocks(resp)
            messages.append({"role": "assistant", "content": resp.content})
            finish = M.finish_from(content)
            uses = [b for b in content if b.get("type") == "tool_use"]
            if not uses:
                messages.append({"role": "user", "content": [{"type": "text", "text": "请继续操作，或调用 finish。"}]})
                continue
            results, failed, last_member, did_input = [], False, None, False
            for b in uses:
                if b["name"] == "finish":
                    results.append({"type": "tool_result", "tool_use_id": b["id"], "content": "收到"})
                    continue
                base = {"type": "tool_result", "tool_use_id": b["id"], "toolset_name": "computer"}
                if failed:
                    results.append({**base, "is_error": True,
                                    "content": "Not executed: an earlier computer action in this turn failed."})
                    continue
                try:
                    content_out = self.member(b["name"], b.get("input") or {})
                    results.append({**base, "content": content_out})
                    last_member = results[-1]
                    did_input = did_input or b["name"] not in ("screenshot", "zoom", "cursor_position", "wait")
                    if b["name"] in ("screenshot", "zoom"):
                        did_input = False
                except (Crash, Blocked):
                    raise
                except Exception as e:  # noqa: BLE001 — 回给模型
                    failed = True
                    results.append({**base, "is_error": True, "content": f"Error: {e}"})
            if finish:
                return finish.get("status", "need_human"), finish.get("note", "")
            if did_input and not failed and last_member is not None:
                time.sleep(0.4)
                last_member["content"] = list(last_member["content"]) + [M.png_block(self.screenshot("auto"))]
            messages.append({"role": "user", "content": results})
        return "need_human", f"{max_turns} 轮还没做完"

    def _xy(self, coord) -> Tuple[int, int]:
        return int(round(coord[0] * self.scale)), int(round(coord[1] * self.scale))

    def member(self, name: str, inp: Dict) -> List[Dict]:
        """电脑操作工具集成员 → 代理动作。坐标从截图空间（1280×720）换回屏幕（1920×1080）。"""
        ok = [{"type": "text", "text": "OK"}]
        if name == "screenshot":
            return [M.png_block(self.screenshot("cu"))]
        if name == "zoom":
            x0, y0 = self._xy(inp["region"][:2])
            x1, y1 = self._xy(inp["region"][2:])
            reply = self.act([{"do": "zoom", "region": [x0, y0, x1, y1]}])
            self.rec["shots_ai"] += 1
            return [M.png_block(reply["results"][0]["png"])]
        clicks = {"left_click": ("left", 1), "right_click": ("right", 1), "middle_click": ("middle", 1),
                  "double_click": ("left", 2), "triple_click": ("left", 3)}
        if name in clicks:
            button, count = clicks[name]
            if inp.get("coordinate"):
                x, y = self._xy(inp["coordinate"])
            else:
                x, y = self.act([{"do": "cursor"}], windows=False)["results"][0]["pos"]
            self.act([{"do": "click", "x": x, "y": y, "button": button, "count": count, "mods": inp.get("text", "")},
                      {"do": "wait", "ms": 300}])
            self._blocked_check()
            return ok
        if name == "left_click_drag":
            x0, y0 = self._xy(inp["start_coordinate"])
            x1, y1 = self._xy(inp["coordinate"])
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
            if inp.get("coordinate"):
                x, y = self._xy(inp["coordinate"])
            else:
                x, y = self.act([{"do": "cursor"}], windows=False)["results"][0]["pos"]
            self.act([{"do": "scroll", "x": x, "y": y, "direction": inp["scroll_direction"],
                       "amount": inp.get("scroll_amount", 3)}])
            return ok
        if name == "type":
            self.act([{"do": "type", "text": inp["text"]}, {"do": "wait", "ms": 200}])
            self._blocked_check()
            return ok
        if name == "key":
            self.act([{"do": "key", "text": inp["text"], "repeat": inp.get("repeat", 1)}, {"do": "wait", "ms": 400}])
            self._blocked_check()
            return ok
        if name == "hold_key":
            self.act([{"do": "hold", "text": inp["text"], "seconds": min(float(inp.get("duration", 1)), 10)}])
            return ok
        if name == "wait":
            self.act([{"do": "wait", "ms": int(min(float(inp.get("duration", 1)), 10) * 1000)}])
            return ok
        raise ValueError(f"不支持的成员 {name}")

    def _blocked_check(self) -> None:
        for w in self._dialogs():
            rule = self._match(w)
            if rule and rule.get("outcome") == "blocked":
                raise Blocked(self._text(w)[:200])

    # ── 固定流程（按 10-26 在 69EXP\test 上实测的录法）──────────────────────
    def step(self, where: str, actions: List[Dict], expect_text: str, expect: Dict) -> None:
        try:
            try:
                self.act(actions)
            except Unexpected as e:
                if "守卫拒绝" not in str(e):
                    raise
                self._event("refocus", where=where)
                self.act([{"do": "activate"}, {"do": "wait", "ms": 400}])
                self.act(actions)
            self._auto_dialogs()
        except Unexpected as e:
            self._recover(where, str(e), expect_text, expect)

    def command(self, name: str) -> Dict:
        """工具栏命令（新建 / 保存 / 取消 / 作废）：给主窗口发命令号，不靠坐标（第 1 层：等同按按钮）。"""
        self._layer("keys")
        return self.act([{"do": "command", "cmd": self.flows["commands"][name]}, {"do": "wait", "ms": 300}])

    def state(self) -> Dict:
        st = self.act([{"do": "state"}], windows=False)["results"][0]
        self.windows = st.get("windows", self.windows)
        self._health()
        return st

    def toolbar_on(self) -> set:
        r = self.act([{"do": "toolbar"}], windows=False)["results"][0]
        return {b["cmd"] for b in r["buttons"] if b["enabled"]}

    def field_map(self) -> Dict[int, Dict]:
        r = self.act([{"do": "fields"}], windows=False)["results"][0]
        return {f["hwnd"]: f for f in r["fields"]}

    def _value_seen(self, label: str, value: str, seen: str) -> bool:
        if self.form_spec["header"]["values"].get(label) == "vat_mode":
            return seen.strip().startswith(value)
        if label in self.form_spec["header"].get("dates", []):
            return re.sub(r"\D", "", seen) == self.draft.date.replace("-", "")
        return same(value, seen)

    def ensure_form(self, doc_type: str) -> None:
        spec = self.flows["forms"][doc_type]
        self.form_spec = spec
        fg = (self.act([{"do": "activate"}, {"do": "wait", "ms": 300}]).get("fg") or {}).get("title", "")
        if self.form == doc_type and spec["title"] in fg:
            return
        # 一次只开一种单据窗口：每个单据窗口各带一条工具栏，开着几个时读不准哪条（10-26 实测）。Ctrl+F4 关掉当前的
        for _ in range(6):
            if "[" not in fg:
                break
            reply = self.act([{"do": "key", "text": "ctrl+F4"}, {"do": "wait", "ms": 1200}])
            if any("1312" in w.get("title", "") for w in self.windows):
                self.act([{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 1000}])
            fg = (self.act([{"do": "wait", "ms": 100}]).get("fg") or {}).get("title", "")
        acts = [{"do": "us_layout"}, {"do": "key", "text": "alt"}, {"do": "wait", "ms": 300}]  # 先单独按 Alt 激活菜单栏
        for k in spec["menu"]:
            acts += [{"do": "keys", "text": k.lstrip("%")}, {"do": "wait", "ms": 400}]
        reply = self.act(acts + [{"do": "wait", "ms": self.flows["form_open_ms"]}])
        title = (reply.get("fg") or {}).get("title", "")
        self._event("form_open", doc_type=doc_type, title=title[:80])
        if spec["title"] in title:
            self._layer("win32")
        else:
            self.checkpoint("form_open", f"已打开「{spec['title']}」单据窗口，处于浏览状态，没有弹窗。", {"form": doc_type})
        self.form = doc_type
        self.baseline = {w["hwnd"] for w in self.windows}
        self.labels = {}

    def walk_header(self, header_text: str) -> None:
        """表头：每按一次回车读焦点落在哪一格（句柄 → 标签），要填的格先全选再打，打完读回核对，填到 stop_after 那一格为止。"""
        d, spec = self.draft, self.form_spec["header"]
        values = {label: {"date": d.date_be, "party": d.party_code, "ref": d.ref_no,
                          "vat_mode": "1" if d.vat_included else "2"}[key]
                  for label, key in spec["values"].items()}
        hdr = {"stage": "header"}
        for _ in range(30):
            st = self.state()
            self._auto_dialogs()
            focus = st.get("focus") or {}
            if focus.get("hwnd") not in self.labels:
                self.labels.update({h: f["label"] for h, f in self.field_map().items()})
            label = self.labels.get(focus.get("hwnd"), "")
            if not label:
                self._recover("header", f"焦点不在表头的格子上（{focus.get('cls')}）", header_text, hdr)
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
                    self._event("mismatch", where=label, want=value, seen=seen[:60])
                    self._recover(f"header.{label}", f"「{label}」里是「{seen.strip()[:40]}」，应为「{value}」", header_text, hdr)
            self.step(f"header.{label}", [{"do": "keys", "text": "{ENTER}"}, {"do": "wait", "ms": spec.get("wait_ms", 350)}],
                      header_text, hdr)
            if label == spec["stop_after"]:
                return
        self._recover("header", "回车 30 次还没走到表头最后一格", header_text, hdr)

    def _cell(self) -> Dict:
        st = self.state()
        self._auto_dialogs()
        return st.get("focus") or {}

    def _type_cell(self, where: str, value: str, line_text: str) -> Dict:
        self.step(where, [{"do": "keys", "text": "{HOME}+{END}"}, {"do": "type", "text": value}, {"do": "wait", "ms": 200}],
                  line_text, {"stage": "line"})
        st = self.state()
        self._auto_dialogs()
        layer, seen = self.seen(st)
        if layer == "none":
            self._layer("unreadable")
            self._unread += 1
        elif same(value, seen):
            self._layer(layer)
        else:
            self._event("mismatch", where=where, want=value, seen=seen[:60])
            self._recover(where, f"格子里是「{seen.strip()[:40]}」，应为「{value}」", line_text, {"stage": "line"})
        return st.get("focus") or {}

    @staticmethod
    def seen(st: Dict) -> Tuple[str, str]:
        """焦点格的内容：先 Win32（WM_GETTEXT），读不到再 UI Automation 的值；都读不到回 ("none", "")。"""
        text = (st.get("focus") or {}).get("text") or ""
        if text.strip():
            return "win32", text
        value = (st.get("uia") or {}).get("value") or ""
        if value.strip():
            return "uia", value
        return "none", ""

    def enter_lines(self) -> None:
        d, spec = self.draft, self.form_spec
        for index, line in enumerate(d.lines, start=1):
            where = f"line{index}"
            # AI 接手某一行时的目标：把这一行录完整、光标停在下一行品码格；接手后这一行剩下的固定步骤跳过
            text = (f"明细第 {index} 行已录完整：品码 {line.item_code}、仓 {line.loc or '默认'}、数量 {line.qty}、"
                    f"单价 {line.unit_price}；光标停在下一行（第 {index + 1} 行）的品码格，这一格是空的；没有弹窗；不要保存。")
            self._line_recovered = False
            if index == 1:
                self.step(where, [{"do": "grid_click", "offset": spec["grid_offset"]}, {"do": "wait", "ms": 400}], text,
                          {"stage": "line"})
            start = self._cell().get("rect") or [0, 0, 0, 0]
            ops = [lambda: self._type_cell(f"{where}.item", line.item_code, text),
                   lambda: self.step(where, [{"do": "keys", "text": "{ENTER}"}, {"do": "wait", "ms": 1000}], text,
                                     {"stage": "line"}),
                   lambda: line.loc and self._type_cell(f"{where}.loc", line.loc, text),
                   lambda: self.step(where, [{"do": "keys", "text": "{ENTER}"}, {"do": "wait", "ms": 400}], text,
                                     {"stage": "line"}),
                   lambda: self._type_cell(f"{where}.qty", line.qty, text),
                   lambda: self.step(where, [{"do": "keys", "text": "{ENTER}"}, {"do": "wait", "ms": 900}], text,
                                     {"stage": "line"}),
                   lambda: self.close_unit_dialog(where, text),
                   lambda: self._type_cell(f"{where}.price", line.unit_price, text)]
            for op in ops:
                op()
                if self._line_recovered:
                    break
            if self._line_recovered:
                continue
            # 单价之后回车走完这一行：焦点回到下一行的品码格（同一列、往下一行）为止，每步认弹窗
            for _ in range(6):
                self.step(where, [{"do": "keys", "text": "{ENTER}"}, {"do": "wait", "ms": 400}], text, {"stage": "line"})
                rect = self._cell().get("rect") or [0, 0, 0, 0]
                if abs(rect[0] - start[0]) < 8 and rect[1] > start[1] + 5:
                    self._layer("win32")
                    break
            else:
                self._recover(where, "这一行回车走不到下一行", text, {"stage": "line"})

    def close_unit_dialog(self, where: str, text: str) -> None:
        """单位框：Express 的框里回车是跳格不是确定（10-26 实测），所以按框里的「ตกลง」。"""
        name = self.form_spec["unit_dialog"]
        hit = [w for w in self.windows if name in self._text(w)]
        if not hit:
            st = self.state()
            hit = [w for w in st.get("windows", []) if name in self._text(w)]
        if not hit:
            return  # 有的商品不弹单位框
        self._layer("win32")
        self.step(where, [{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 600}], text, {"stage": "line"})

    def saved_check(self, T: str) -> str:
        """第 2 层确认存上：单号格已是正式号（前缀 + 数字，不含 NEW）。存上了回单号，否则回空串。
        账套文件头的行数要过一阵才刷新（10-26 实测），不拿它当判据；DBF 留到最后逐字段核对。"""
        docnum = self.docnum_text()
        ok = docnum.upper().startswith(T) and "NEW" not in docnum.upper() and re.search(r"\d", docnum)
        return docnum if ok and not self.in_edit() else ""

    def in_edit(self) -> bool:
        """编辑 / 新建中：「保存」亮着（一次只开一个单据窗口，工具栏读得准），或单号格含 NEW。
        赊销单新建后单号格先只显示「IV」，录了客户才变成 IV**NEW**（10-26 实测），所以两样看一样即可。"""
        return self.flows["commands"]["save"] in self.toolbar_on() or "NEW" in self.docnum_text().upper()

    def docnum_text(self) -> str:
        box = [f for f in self.field_map().values() if f["label"] == self.form_spec["docnum_label"]]
        return box[0]["text"].strip() if box else ""

    def check_totals(self, before_text: str) -> bool:
        """保存前读底部合计（不截图）：税前、税、含税都和草稿单对上才存。读不到才截图。"""
        want = self.draft.totals()
        boxes = {}
        for f in self.field_map().values():
            if f["label"]:
                boxes.setdefault(f["label"], []).append(f)
        problems, read = [], 0
        for key, label in self.form_spec["totals"].items():
            cands = sorted(boxes.get(label, []), key=lambda f: f["rect"][0])
            if not cands:
                continue
            seen = re.sub(r"[^\d.\-]", "", cands[-1]["text"])  # 同一行有比率和金额两格时取最右的金额格
            if not seen:
                continue
            read += 1
            if Decimal(seen) != want[key]:
                problems.append(f"{label} 是 {seen}，应为 {want[key]}")
        if read and not problems:
            self._layer("win32")
            return False
        if problems:
            self._recover("before_save", "；".join(problems), before_text, {"stage": "before_save", "total": str(want["total"])})
        else:
            self.checkpoint("before_save", before_text, {"stage": "before_save", "lines": len(self.draft.lines),
                                                         "total": str(want["total"])})
        return True  # 已经看过一眼了

    def _fmt(self, text: str, line=None) -> str:
        d = self.draft
        values = {"party": d.party_code, "date": d.date_be, "ref": d.ref_no, "remark": d.remark, "T": d.doc_type}
        if line is not None:
            values.update({"item": line.item_code, "loc": line.loc, "qty": line.qty, "price": line.unit_price})
        return text.format(**values)

    def run_fixed(self) -> None:
        d = self.draft
        T, name = d.doc_type, DOC_NAMES[d.doc_type]
        self.ensure_form(T)
        cmds = self.flows["commands"]
        header_text = (f"正在新建一张{name}（单号显示 {T}**NEW**），表头对方码 {d.party_code}、日期 {d.date_be}"
                       + (f"、对方单号 {d.ref_no}" if d.ref_no else "") + "，没有弹窗。")
        self.command("new")
        self.act([{"do": "wait", "ms": 1200}])
        if self.in_edit():
            self._layer("win32")  # 「保存」亮了，或单号格变成「前缀**NEW**」= 进了新建状态
        else:
            self._recover("new", "点新建后没进新建状态", header_text, {"stage": "header"})
        self.walk_header(header_text)
        self.enter_lines()
        totals = d.totals()
        before_text = f"新建中的{name}，明细共 {len(d.lines)} 行，含税合计 {totals['total']}，没有弹窗，还没保存。"
        looked = self.check_totals(before_text)
        if not looked and (self._unread and self.presave_check == "auto" or self.presave_check == "always"):
            self.checkpoint("before_save", before_text, {"stage": "before_save", "lines": len(d.lines),
                                                         "total": str(totals["total"])})
        saved_text = f"单号栏是正式单号（{T} 后面跟数字，不含 NEW），没有弹窗。"
        self.command("save")
        self.act([{"do": "wait", "ms": self.flows["save_wait_ms"]}])
        try:
            self._auto_dialogs()
        except Unexpected as e:
            self._recover("save", str(e), saved_text, {"stage": "saved", "saved": True},
                          reverify=lambda: self.saved_check(T) or None)
        saved = False
        for _ in range(8):
            saved = self.saved_check(T)
            if saved:
                break
            self.act([{"do": "wait", "ms": 600}], windows=False)
            self._auto_dialogs()
        docnum = saved or ""
        if saved:
            self._layer("win32")
            self._event("saved", docnum=docnum)
        else:
            self.checkpoint("after_save", saved_text, {"stage": "saved", "saved": True})
        if self.evidence:
            self.screenshot("saved", for_ai=False)

    def abandon(self) -> None:
        """单据被业务规则拦下：关掉弹窗、整张取消（不存）。取消也要读「1312 确认取消」框按「ตกลง」，不用 Esc（Esc 会存）。"""
        try:
            for w in self._dialogs():
                if "ตกลง" in " ".join(w.get("texts", [])).replace("&", ""):
                    self.act([{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 600}])
            if self.in_edit():
                self.command("cancel")
                self.act([{"do": "wait", "ms": 900}])
                if any("1312" in w.get("title", "") for w in self.windows):
                    self.act([{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 900}])
            # 已有明细时 Express 的「取消」会直接存档（10-26 实测 RR0000010）：存了就用作废命令作废，单号作废不复用
            docnum = self.saved_check(self.draft.doc_type)
            if docnum:
                self.command("void")
                self.act([{"do": "wait", "ms": 1000}])
                if any("ยกเลิกเอกสาร" in " ".join(w.get("texts", [])) for w in self._dialogs()):
                    self.act([{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 1500}])
                self._event("voided", docnum=docnum)
                self.rec.setdefault("voided", []).append(docnum)
            self._event("abandon", ok=not self.in_edit())
        except Exception as e:  # noqa: BLE001
            self._event("abandon", ok=False, error=str(e)[:200])
            self.form = None

    def run_vision(self) -> None:
        model, turns, effort = self.mode["vision"]
        d = self.draft
        goal = (f"任务：在 Express 里录入这张{DOC_NAMES[d.doc_type]}并保存。如果对应的单据窗口还没打开，先从菜单打开并最大化；"
                f"然后新建、照草稿单填表头和明细、核对合计、保存；看到单号变成正式号后调用 finish(resolved)。\n"
                f"草稿单：\n{d.describe()}")
        status, note = self.cu_loop(model, goal, turns, effort, "vision")
        self._event("vision", status=status, note=note[:200])
        if status != "resolved":
            raise NeedHuman("vision", note)

    # ── 一张单 ──────────────────────────────────────────────────────────────
    def run(self, draft: Draft) -> Dict:
        self.draft = draft
        self._t0 = time.monotonic()
        self._unread = 0
        self.rec = {"doc_id": draft.doc_id, "doc_type": draft.doc_type, "mode": self.mode_name,
                    "lines": len(draft.lines), "started": time.strftime("%Y-%m-%d %H:%M:%S"), "events": [],
                    "usd": "0", "usd_by_model": {}, "ocr_usd": str(draft.ocr_usd or "0"), "calls": 0,
                    "checkpoints": 0, "cu_turns": 0, "fallbacks": [], "human": 0, "stuck_at": "", "crashed": False,
                    "blocked": False, "api_s": 0.0, "agent_s": 0.0, "max_prompt": 0, "layers": {}, "shots_ai": 0,
                    "shots_evidence": 0}
        outcome = "done"
        before = self.act([{"do": "book_counts", "book": self.book}], windows=False)["results"][0]["counts"]
        try:
            if "vision" in self.mode:
                self.run_vision()
            else:
                self.run_fixed()
        except NeedHuman as e:
            outcome, self.rec["human"], self.rec["stuck_at"] = "need_human", 1, f"{e.where}: {e.why}"[:300]
        except Blocked as e:
            outcome, self.rec["blocked"], self.rec["stuck_at"] = "blocked", True, str(e)[:300]
            self.abandon()
        except Crash as e:
            outcome, self.rec["crashed"], self.rec["stuck_at"] = "crash", True, str(e)[:300]
        except BudgetStop as e:
            outcome, self.rec["stuck_at"] = "budget", str(e)
        self.rec["outcome"] = outcome
        rows = None
        if outcome != "crash":
            try:
                rows = self.act([{"do": "book_rows", "book": self.book, "since": before,
                                  "tables": ["APTRN", "ARTRN", "STCRD", "ISVAT"]}], windows=False)["results"][0]["rows"]
                result = C.compare(draft, rows)
                self._layer("dbf")
            except Exception as e:  # noqa: BLE001
                result = {"ok": False, "docnum": "", "diffs": [{"field": "readback", "want": "", "got": str(e)}],
                          "notes": []}
        else:
            result = {"ok": False, "docnum": "", "diffs": [], "notes": ["Express 崩了，没读回"]}
        self.rec.update({"readback_ok": result["ok"], "docnum": result["docnum"], "diffs": result["diffs"]})
        if draft.answer and rows is not None:
            truth = C.compare_answer(draft, rows)
            self.rec.update({"answer_ok": truth["ok"], "answer_diffs": truth["diffs"]})
        self.rec["first_try"] = bool(result["ok"] and not self.rec["fallbacks"] and not self.rec["human"])
        self.rec["seconds"] = round(time.monotonic() - self._t0, 1)
        self.rec["usd_total"] = str(Decimal(self.rec["usd"]) + Decimal(self.rec["ocr_usd"]))
        if outcome in ("need_human", "crash"):
            self.form = None  # 画面状态不明，下一张从菜单重新打开
        return self.rec
