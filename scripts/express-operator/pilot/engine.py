"""试点执行器：草稿单 → 固定流程按键 → AI 只处理意外 → 录完读账套核对。

四种方式（--mode）：
- haiku_fixed：固定流程；关键点由 Haiku 5.5 看截图核对；出意外先 Haiku 接手，不行再 Opus 5.5（主推）；
- opus_fixed：同上，核对和接手都用 Opus 5.5；
- opus_vision：Opus 5.5 全程看图自己录（只跑几张看上限）；
- direct：现有直接写入（见 direct_write.py，不走这里）。

每张单：记成本、用时、读回是否全对、AI 接手几次、要不要人、卡在哪、Express 有没有崩。
"""
from __future__ import annotations

import base64
import json
import re
import time
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
    """固定流程这一步之后画面不对（不认识的弹窗、该有的框没出现）。交给 AI 接手。"""


class Engine:
    def __init__(self, agent, claude, ledger: Ledger, flows: Dict, book: str, mode: str, out_dir: Path,
                 *, lines_checkpoint: bool = True, log: Callable[[str], None] = print):
        self.agent, self.claude, self.ledger, self.flows, self.book = agent, claude, ledger, flows, book
        self.mode_name, self.mode = mode, MODES[mode]
        self.out_dir = Path(out_dir)
        self.lines_checkpoint = lines_checkpoint
        self.log = log
        self.form: Optional[str] = None
        self.baseline: set = set()
        self.dialogs_visible: Optional[bool] = None  # 弹窗是不是独立窗口、读得到字（第一次真跑才知道）
        self.windows: List[Dict] = []
        sw, sh = flows["screen"]
        self.screen = (sw, sh)
        self.shot_size = (1280, 720)
        self.scale = sw / self.shot_size[0]
        self.rules = [dict(r, rx=re.compile(r["match"])) for r in flows["dialogs"]]
        self.rec: Dict = {}
        self.draft: Optional[Draft] = None

    # ── 记录 ────────────────────────────────────────────────────────────────
    def _event(self, kind: str, **data) -> None:
        self.rec["events"].append({"t": round(time.monotonic() - self._t0, 2), "kind": kind, **data})

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
        """处理认得出的弹窗（独立窗口、读得到字）。认不出的交给 AI。"""
        for _ in range(4):
            pending = []
            for w in self._dialogs():
                rule = self._match(w)
                if rule is None:
                    raise Unexpected(f"不认识的弹窗：{self._text(w)[:200] or '（读不到字）'}")
                if rule.get("auto"):
                    pending.append((w, rule))
            if not pending:
                return
            w, rule = pending[0]
            self.dialogs_visible = True
            if rule.get("outcome") == "blocked":
                raise Blocked(self._text(w)[:200])
            self._event("dialog", name=rule["name"], how="fixed")
            self.act([{"do": "keys", "text": rule["keys"]}, {"do": "wait", "ms": 600}])
        raise Unexpected("弹窗关不掉")

    def screenshot(self, name: str) -> str:
        reply = self.act([{"do": "screenshot", "size": list(self.shot_size)}])
        png = reply["results"][0]["png"]
        shots = self.out_dir / "shots" / self.draft.doc_id
        shots.mkdir(parents=True, exist_ok=True)
        (shots / f"{len(self.rec['events']):03d}-{name}.png").write_bytes(base64.b64decode(png))
        return png

    # ── 模型 ────────────────────────────────────────────────────────────────
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

    def _recover(self, where: str, problem: str, expect_text: str, expect: Dict) -> None:
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
            if status == "resolved" and self.checkpoint(f"{where}.verify", expect_text, expect, recover=False):
                entry["resolved_by"] = model
                return
        raise NeedHuman(where, f"AI 接手后仍不对：{problem}")

    def cu_loop(self, model: str, goal: str, max_turns: int, effort: str, purpose: str) -> Tuple[str, str]:
        png = self.screenshot(purpose.replace(":", "-"))
        messages: List[Dict] = [{"role": "user", "content": [{"type": "text", "text": goal}, M.png_block(png)]}]
        last_prompt = 0
        for turn in range(max_turns):
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
            self._auto_crash_only()
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
            self._auto_crash_only()
            return ok
        if name == "key":
            self.act([{"do": "key", "text": inp["text"], "repeat": inp.get("repeat", 1)}, {"do": "wait", "ms": 400}])
            self._auto_crash_only()
            return ok
        if name == "hold_key":
            self.act([{"do": "hold", "text": inp["text"], "seconds": min(float(inp.get("duration", 1)), 10)}])
            return ok
        if name == "wait":
            self.act([{"do": "wait", "ms": int(min(float(inp.get("duration", 1)), 10) * 1000)}])
            return ok
        raise ValueError(f"不支持的成员 {name}")

    def _auto_crash_only(self) -> None:
        for w in self._dialogs():
            rule = self._match(w)
            if rule and rule.get("outcome") == "blocked":
                raise Blocked(self._text(w)[:200])

    # ── 固定流程 ────────────────────────────────────────────────────────────
    def _anchor(self, name: str) -> Optional[List[int]]:
        a = self.flows["anchors"].get(name)
        return a["xy"] if a and a.get("xy") else None

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

    def ensure_form(self, doc_type: str) -> None:
        if self.form == doc_type:
            return
        name = DOC_NAMES[doc_type]
        menu = self.flows["menus"][doc_type]
        acts = [{"do": "activate"}, {"do": "wait", "ms": 300}, {"do": "us_layout"}]
        for k in menu:
            acts += [{"do": "keys", "text": k}, {"do": "wait", "ms": 350}]
        acts.append({"do": "wait", "ms": self.flows["form_open_ms"]})
        self.act(acts)
        self.baseline = {w["hwnd"] for w in self.windows}
        self.form = doc_type
        self._event("form_open", doc_type=doc_type, windows=[(w["title"], w["cls"]) for w in self.windows])
        self.checkpoint("form_open", f"已打开「{name}」单据窗口并最大化，处于浏览状态（没有在新建 / 编辑），没有弹窗。",
                        {"form": doc_type})
        self.baseline = {w["hwnd"] for w in self.windows}

    def _fmt(self, text: str, line=None) -> str:
        d = self.draft
        values = {"party": d.party_code, "date": d.date_be, "ref": d.ref_no, "remark": d.remark}
        if line is not None:
            values.update({"item": line.item_code, "loc": line.loc, "qty": line.qty, "price": line.unit_price})
        return text.format(**values)

    def run_fixed(self) -> None:
        d, f = self.draft, self.flows
        T, name = d.doc_type, DOC_NAMES[d.doc_type]
        self.ensure_form(T)
        self.act([{"do": "activate"}, {"do": "us_layout"}])
        header_text = (f"正在新建一张{name}（单号显示 {T}NEW），表头对方码 {d.party_code}、日期 {d.date_be}"
                       + (f"、对方单号 {d.ref_no}" if d.ref_no else "") + "，明细还是空的，没有弹窗。")
        new_xy = self._anchor("toolbar.new")
        self.step("new", [{"do": "click", "x": new_xy[0], "y": new_xy[1]}, {"do": "wait", "ms": 1500}],
                  header_text, {"stage": "header"})
        for field in f["header"][T]:
            spec = f["header_fields"][field]
            value = self._fmt(spec["value"])
            if spec.get("skip_if_empty") and not value:
                continue
            acts: List[Dict] = []
            xy = self._anchor(f"{T}.{field}")
            if xy:
                acts += [{"do": "click", "x": xy[0], "y": xy[1]}, {"do": "wait", "ms": 250}]
            if spec.get("select_all"):
                acts.append({"do": "keys", "text": "{HOME}+{END}"})
            acts += [{"do": "type", "text": value}, {"do": "keys", "text": spec["after"]},
                     {"do": "wait", "ms": spec["wait_ms"]}]
            self.step(f"header.{field}", acts, header_text, {"stage": "header"})
        self.checkpoint("header", header_text, {"stage": "header"})
        grid = self._anchor(f"{T}.grid")
        self.act([{"do": "click", "x": grid[0], "y": grid[1]}, {"do": "wait", "ms": 400}])
        for index, line in enumerate(d.lines, start=1):
            line_text = (f"明细已录到第 {index} 行：品码 {line.item_code}、数量 {line.qty}、单价 {line.unit_price}；"
                         f"光标在下一行的品码格，没有弹窗。")
            for op in f["line"]:
                self._line_op(op, line, f"line{index}", line_text)
            if self.lines_checkpoint:
                self.checkpoint(f"line{index}", line_text, {"stage": "line", "lines": index})
        for op in f["after_lines"]:
            self.step("after_lines", [{"do": "keys", "text": op["text"]}, {"do": "wait", "ms": op["wait_ms"]}],
                      "明细录完，空行已退掉", {"stage": "lines_done"})
        totals = d.totals()
        before_text = f"新建中的{name}，明细共 {len(d.lines)} 行，含税合计 {totals['total']}，没有弹窗，还没保存。"
        self.checkpoint("before_save", before_text, {"stage": "before_save", "lines": len(d.lines),
                                                     "total": str(totals["total"])})
        save_xy = self._anchor("toolbar.save")
        saved_text = f"刚保存的{name}已有正式单号（不再是 {T}NEW），没有弹窗。"
        self.step("save", [{"do": "click", "x": save_xy[0], "y": save_xy[1]},
                           {"do": "wait", "ms": f["save_wait_ms"]}], saved_text, {"stage": "saved", "saved": True})
        self.checkpoint("after_save", saved_text, {"stage": "saved", "saved": True})

    def _line_op(self, op: Dict, line, where: str, line_text: str) -> None:
        expect = {"stage": "line"}
        kind = op["op"]
        if kind == "type":
            self.step(where, [{"do": "type", "text": self._fmt(op["text"], line)}], line_text, expect)
        elif kind == "keys":
            self.step(where, [{"do": "keys", "text": op["text"]}, {"do": "wait", "ms": op.get("wait_ms", 300)}],
                      line_text, expect)
        elif kind == "loc":
            acts = ([{"do": "keys", "text": "{HOME}+{END}"}, {"do": "type", "text": line.loc}] if line.loc else [])
            self.step(where, acts + [{"do": "keys", "text": "{ENTER}"}, {"do": "wait", "ms": 400}], line_text, expect)
        elif kind == "expect_dialog":
            found = [w for w in self._dialogs() if (self._match(w) or {}).get("name") == op["name"]]
            if found:
                self.dialogs_visible = True
            elif self.dialogs_visible:
                self._recover(where, f"该出的「{op['name']}」框没出现", line_text, expect)
                return
            else:
                self.dialogs_visible = False if self.dialogs_visible is None else self.dialogs_visible
            self.step(where, [{"do": "keys", "text": op["keys"]}, {"do": "wait", "ms": op.get("wait_ms", 500)}],
                      line_text, expect)
        else:
            raise ValueError(f"流程里不认识的步骤 {kind}")

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
        self.rec = {"doc_id": draft.doc_id, "doc_type": draft.doc_type, "mode": self.mode_name,
                    "lines": len(draft.lines), "started": time.strftime("%Y-%m-%d %H:%M:%S"), "events": [],
                    "usd": "0", "usd_by_model": {}, "calls": 0, "checkpoints": 0, "cu_turns": 0, "fallbacks": [],
                    "human": 0, "stuck_at": "", "crashed": False, "blocked": False, "api_s": 0.0, "agent_s": 0.0,
                    "max_prompt": 0}
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
        except Crash as e:
            outcome, self.rec["crashed"], self.rec["stuck_at"] = "crash", True, str(e)[:300]
        except BudgetStop as e:
            outcome, self.rec["stuck_at"] = "budget", str(e)
        self.rec["outcome"] = outcome
        if outcome != "crash":
            try:
                rows = self.act([{"do": "book_rows", "book": self.book, "since": before,
                                  "tables": ["APTRN", "ARTRN", "STCRD", "ISVAT"]}], windows=False)
                result = C.compare(draft, rows["results"][0]["rows"])
            except Exception as e:  # noqa: BLE001
                result = {"ok": False, "docnum": "", "diffs": [{"field": "readback", "want": "", "got": str(e)}],
                          "notes": []}
        else:
            result = {"ok": False, "docnum": "", "diffs": [], "notes": ["Express 崩了，没读回"]}
        self.rec.update({"readback_ok": result["ok"], "docnum": result["docnum"], "diffs": result["diffs"]})
        self.rec["first_try"] = bool(result["ok"] and not self.rec["fallbacks"] and not self.rec["human"])
        self.rec["seconds"] = round(time.monotonic() - self._t0, 1)
        if outcome in ("need_human", "crash"):
            self.form = None  # 画面状态不明，下一张从菜单重新打开
        return self.rec
