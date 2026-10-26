"""离线自检用：假 Express（实现和测试机代理同一个 act 接口）+ 假模型（按模拟器状态回答，回同样形状的响应）。

不是 Express 的仿真，只模拟执行器要面对的几件事：菜单开单据窗、点工具栏、表头回车跳格、明细格顺序、
单位框、4079、陌生弹窗、弹窗是否是独立窗口、负库存拦截、-70 崩溃、保存写账。用来验证执行器的流程、接手、记账、核对逻辑。
"""
from __future__ import annotations

import base64
import datetime as dt
import re
import struct
import zlib
from decimal import Decimal
from typing import Dict, List, Optional

from .draft import Draft, money

GRID_COLS = ["item", "loc", "qty", "price", "disc", "amount"]
CELL_W, ROW_H = 120, 22


def tiny_png() -> str:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = zlib.compress(b"\0\xff\xff\xff")
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode()


def _tokens(spec: str) -> List[str]:
    """SendKeys 子集拆成键：'{ENTER}'、'+{END}'、'%1'、单个字符。"""
    out, i, prefix = [], 0, ""
    while i < len(spec):
        ch = spec[i]
        if ch in "+^%":
            prefix += ch
            i += 1
            continue
        if ch == "{":
            end = spec.index("}", i)
            name = spec[i + 1:end].split(" ")
            out += [prefix + "{" + name[0].upper() + "}"] * (int(name[1]) if len(name) > 1 else 1)
            i = end + 1
        else:
            out.append(prefix + ch)
            i += 1
        prefix = ""
    return out


class FakeExpress:
    def __init__(self, flows: Dict, *, dialogs_visible: bool = True, inject: Optional[Dict] = None,
                 vat_inclusive=("HS",)):
        self.flows = flows
        self.anchors = {k: tuple(a["xy"]) for k, a in flows["anchors"].items() if a.get("xy")}
        self.dialogs_visible = dialogs_visible
        self.inject = inject or {}  # {"party:P002": {...弹窗}, "crash_on_save:5001": True, "block_item:X": True, "typo_qty:DOCID": "50"}
        self.vat_inclusive = vat_inclusive
        self.menu: List[str] = []
        self.form: Optional[str] = None
        self.editing = False
        self.head: Dict[str, str] = {}
        self.grid: List[Dict[str, str]] = []
        self.focus = None
        self.select_all = False
        self.dialogs: List[Dict] = []
        self.hwnd_seq = 500
        self.alive = True
        self.sold = set()
        self.counters: Dict[str, int] = {}
        self.book: Dict[str, List[Dict]] = {"APTRN": [], "ARTRN": [], "STCRD": [], "ISVAT": []}
        self.last_docnum = ""
        self.cursor = [0, 0]
        self.current_doc_id = ""

    # ── 画面 ───────────────────────────────────────────────────────────────
    def dialog(self, name: str, text: str, after=None) -> None:
        self.hwnd_seq += 1
        self.dialogs.append({"hwnd": self.hwnd_seq, "name": name, "text": text, "after": after})

    def windows(self) -> List[Dict]:
        if not self.alive:
            return []
        out = [{"hwnd": 100, "title": "Express Accounting", "cls": "ExpressMain", "rect": [0, 0, 1920, 1040],
                "owned": False, "texts": []}]
        if self.dialogs_visible:
            out += [{"hwnd": d["hwnd"], "title": "Message", "cls": "#32770", "rect": [700, 400, 1200, 560],
                     "owned": True, "texts": [d["text"]]} for d in self.dialogs]
        return out

    def cell_xy(self, row: int, col: str) -> List[int]:
        gx, gy = self.anchors[f"{self.form}.grid"]
        return [gx + GRID_COLS.index(col) * CELL_W, gy + row * ROW_H]

    def complete_rows(self) -> List[Dict]:
        return [r for r in self.grid if r.get("item") and r.get("qty") and r.get("price")]

    def total(self) -> Decimal:
        gross = sum((money(Decimal(r["qty"]) * Decimal(r["price"])) for r in self.complete_rows()), Decimal(0))
        if self.form in self.vat_inclusive:
            return gross
        return gross + money(gross * Decimal("0.07"))

    # ── 输入 ───────────────────────────────────────────────────────────────
    def _header_order(self) -> List[str]:
        return self.flows["header"][self.form]

    def _set_focus(self, focus) -> None:
        self.focus = focus
        self.select_all = True

    def _type(self, text: str) -> None:
        if self.dialogs or self.focus is None:
            return  # 有弹窗时字被吞掉
        if self.focus[0] == "head":
            key = self.focus[1]
            self.head[key] = text if self.select_all else self.head.get(key, "") + text
        else:
            _, row, col = self.focus
            while len(self.grid) <= row:
                self.grid.append({})
            cell = self.grid[row]
            if col == "qty" and f"typo_qty:{self.current_doc_id}" in self.inject:
                text = self.inject[f"typo_qty:{self.current_doc_id}"]
            cell[col] = text if self.select_all else cell.get(col, "") + text
        self.select_all = False

    def _enter(self) -> None:
        if self.dialogs:
            d = self.dialogs.pop()
            if d.get("after"):
                d["after"]()
            return
        if self.focus is None:
            return
        if self.focus[0] == "head":
            order = self._header_order()
            key = self.focus[1]
            spec = f"party:{self.head.get('party', '')}"
            if key == "party" and spec in self.inject:
                info = self.inject.pop(spec)
                self.dialog("unknown", info["text"])
            nxt = order.index(key) + 1 if key in order else len(order)
            self._set_focus(("head", order[nxt]) if nxt < len(order) else None)
            return
        _, row, col = self.focus
        cell = self.grid[row]
        if col == "item":
            if f"block_item:{cell.get('item')}" in self.inject:
                self.dialog("neg", "Message#4224 ยอดคงเหลือของสินค้าติดลบ")
            cell.setdefault("loc", "01")
            self._set_focus(("grid", row, "loc"))
        elif col == "loc":
            self._set_focus(("grid", row, "qty"))
        elif col == "qty":
            def after_unit():
                key = (self.head.get("party"), cell.get("item"))
                if self.form in ("IV", "HS") and key not in self.sold:
                    self.sold.add(key)
                    self.dialog("4079", "Message#4079 ลูกค้ารายนี้ไม่เคยซื้อสินค้านี้ กรุณาระบุราคา")
            self.dialog("unit", "ป้อนหน่วยนับที่ขาย / ซื้อ", after=after_unit)
            self._set_focus(("grid", row, "price"))
        elif col == "price":
            self._set_focus(("grid", row, "disc"))
        elif col == "disc":
            self._set_focus(("grid", row, "amount"))
        else:
            self.grid.append({})
            self._set_focus(("grid", row + 1, "item"))

    def _esc(self) -> None:
        if self.dialogs:
            self.dialogs.pop()
            return
        if self.focus and self.focus[0] == "grid":
            row = self.focus[1]
            if row < len(self.grid) and not any(self.grid[row].get(c) for c in ("item", "qty", "price")):
                self.grid.pop(row)
                self.focus = None

    def _key(self, token: str) -> None:
        if token.startswith("%") and len(token) == 2:
            self.menu = [token]
            return
        if self.menu and len(token) == 1 and token.isdigit():
            self.menu.append(token)
            for doc_type, path in self.flows["menus"].items():
                if self.menu == path:
                    self.form, self.editing, self.menu = doc_type, False, []
            return
        self.menu = []
        name = token.lstrip("+^%")
        if name == "{ENTER}":
            self._enter()
        elif name == "{ESC}":
            self._esc()
        elif name in ("{HOME}", "{END}"):
            self.select_all = True
        elif name == "{TAB}":
            self._enter()
        elif len(name) == 1:
            self._type(name)

    def _click(self, x: int, y: int) -> None:
        if self.dialogs:
            return
        hit = None
        for name, (ax, ay) in self.anchors.items():
            if abs(ax - x) <= 15 and abs(ay - y) <= 10:
                if name.startswith("toolbar.") or name.startswith(f"{self.form}."):
                    hit = name
                    break
        if hit == "toolbar.new" and self.form and not self.editing:
            self.editing, self.head, self.grid, self.focus = True, {}, [], ("head", "party")
        elif hit == "toolbar.save" and self.editing:
            self._save()
        elif hit == "toolbar.cancel":
            self.editing, self.focus = False, None
        elif hit and hit.endswith(".grid") and self.editing:
            self._set_focus(("grid", 0, "item"))
        elif hit and self.editing:
            self._set_focus(("head", hit.split(".", 1)[1]))
        elif self.editing and self.form:
            gx, gy = self.anchors[f"{self.form}.grid"]
            col, row = round((x - gx) / CELL_W), round((y - gy) / ROW_H)
            if 0 <= col < len(GRID_COLS) and 0 <= row <= len(self.grid):
                self._set_focus(("grid", row, GRID_COLS[col]))

    def _save(self) -> None:
        party = self.head.get("party", "")
        if f"crash_on_save:{party}" in self.inject:
            self.dialog("smb", r"Error -70 (90621) Reading File \\ACCSERVER\D$\ACCOUNT\69EXP\TEST\STMAS.CDX")
            return
        rows = self.complete_rows()
        if not party or not rows:
            self.dialog("unknown", "กรุณาระบุข้อมูลให้ครบ")
            return
        T = self.form
        self.counters[T] = self.counters.get(T, 0) + 1
        docnum = f"{T}{self.counters[T]:07d}"
        d, m, y = self.head.get("docdate", "01/01/69").split("/")
        day = dt.date(int(y) + 2500 - 543, int(m), int(d))
        gross = sum((money(Decimal(r["qty"]) * Decimal(r["price"])) for r in rows), Decimal(0))
        if T in self.vat_inclusive:
            vat = money(gross * Decimal(7) / Decimal(107))
            net, total = gross - vat, gross
        else:
            vat = money(gross * Decimal("0.07"))
            net, total = gross, gross + vat
        head_table = "APTRN" if T in ("RR", "HP") else "ARTRN"
        party_col, ref_col = ("SUPCOD", "REFNUM") if head_table == "APTRN" else ("CUSCOD", "YOUREF")
        self.book[head_table].append({"_row": len(self.book[head_table]), "_del": False, "DOCNUM": docnum,
                                      "DOCDAT": day.isoformat(), party_col: party,
                                      ref_col: self.head.get("billno", ""), "NETVAL": float(net),
                                      "VATAMT": float(vat), "NETAMT": float(total)})
        for i, r in enumerate(rows, start=1):
            self.book["STCRD"].append({"_row": len(self.book["STCRD"]), "_del": False, "DOCNUM": docnum,
                                       "SEQNUM": str(i), "STKCOD": r["item"], "LOCCOD": r.get("loc", "01"),
                                       "TRNQTY": float(r["qty"]), "UNITPR": float(r["price"]),
                                       "TRNVAL": float(money(Decimal(r["qty"]) * Decimal(r["price"])))})
        self.book["ISVAT"].append({"_row": len(self.book["ISVAT"]), "_del": False, "DOCNUM": docnum,
                                   "AMT01": float(net), "VAT01": float(vat)})
        self.editing, self.focus, self.last_docnum = False, None, docnum

    # ── 代理接口 ───────────────────────────────────────────────────────────
    def do(self, a: Dict) -> Dict:
        op = a["do"]
        if not self.alive:
            raise RuntimeError("Express 没在运行")
        if op in ("activate", "us_layout", "wait", "maximize_main", "move", "mouse", "hold", "scroll", "drag"):
            return {}
        if op == "keys":
            for t in _tokens(a["text"]):
                self._key(t)
            return {}
        if op == "key":
            names = {"return": "{ENTER}", "enter": "{ENTER}", "escape": "{ESC}", "tab": "{TAB}", "home": "{HOME}",
                     "end": "{END}"}
            parts = a["text"].lower().split("+")
            for _ in range(int(a.get("repeat", 1))):
                if parts[0] == "alt" and len(parts) == 2:
                    self._key("%" + parts[1])
                else:
                    self._key(names.get(parts[-1], parts[-1]))
            return {}
        if op in ("type", "paste"):
            self._type(a["text"])
            return {}
        if op == "click":
            self.cursor = [a["x"], a["y"]]
            self._click(a["x"], a["y"])
            return {}
        if op == "cursor":
            return {"pos": self.cursor}
        if op in ("screenshot", "zoom"):
            return {"png": tiny_png(), "size": [1280, 720]}
        if op == "book_counts":
            return {"counts": {k: len(v) for k, v in self.book.items()}}
        if op == "book_rows":
            since = a.get("since", {})
            return {"rows": {k: [dict(r) for r in v[since.get(k, 0):]] for k, v in self.book.items()
                             if k in (a.get("tables") or self.book)}}
        if op == "windows":
            return {"windows": self.windows()}
        raise RuntimeError(f"假代理不认识 {op}")

    def act(self, actions: List[Dict], windows: bool = True) -> Dict:
        results, failed = [], False
        for a in actions:
            if failed:
                results.append({"ok": False, "skipped": True})
                continue
            try:
                results.append({"ok": True, **self.do(a)})
            except Exception as e:  # noqa: BLE001
                failed = True
                results.append({"ok": False, "error": str(e)})
        if any(d["name"] == "smb" for d in self.dialogs):
            pass
        out = {"results": results, "ms": 1}
        if windows:
            out["windows"] = self.windows()
        return out


# ── 假模型 ─────────────────────────────────────────────────────────────────
class _Block(dict):
    def __getattr__(self, name):
        return self.get(name)

    def model_dump(self):
        return dict(self)


class _Resp:
    def __init__(self, content: List[Dict], usage: Dict, stop_reason: str = "tool_use"):
        self.content = [_Block(c) for c in content]
        self.usage = usage
        self.stop_reason = stop_reason


class FakeClaude:
    """按模拟器真实状态回答。``haiku_gives_up=True`` 时 Haiku 接手一律 escalate（测升级到 Opus）；
    ``blind=True`` 时核对一律说「相符」（测读回能不能查出录错）。"""

    def __init__(self, sim: FakeExpress, *, haiku_gives_up: bool = False, blind: bool = False):
        self.sim, self.haiku_gives_up, self.blind = sim, haiku_gives_up, blind
        self.draft: Optional[Draft] = None
        self.plans: Dict[str, List[List[Dict]]] = {}
        self.calls: List[str] = []

    def _mismatch(self, upto: Optional[int] = None) -> Optional[List[Dict]]:
        """草稿单前 upto 行和画面第一处不一样的地方 → 修正动作（截图坐标）。"""
        s, d = self.sim, self.draft
        rows = s.grid
        for i, line in enumerate(d.lines[:upto]):
            if i >= len(rows):
                return None
            for col, want in (("item", line.item_code), ("qty", line.qty), ("price", line.unit_price)):
                if rows[i].get(col) != want:
                    x, y = s.cell_xy(i, col)
                    returns = len(GRID_COLS) - GRID_COLS.index(col)  # 回车到下一行品码格，流程才能接着录
                    return [{"name": "left_click", "input": {"coordinate": [x / 1.5, y / 1.5]}},
                            {"name": "type", "input": {"text": want}},
                            {"name": "key", "input": {"text": "Return", "repeat": returns}},
                            {"name": "screenshot", "input": {}}]
        return None

    def checkpoint(self, model, png, prompt, expect):
        self.calls.append(f"check:{model}")
        s, d = self.sim, self.draft
        usage = {"input_tokens": 2400, "output_tokens": 110}
        base = {"what_i_see": "", "dialog_text": "", "message_no": "", "lines_seen": len(s.complete_rows()),
                "total_seen": str(s.total()), "docnum_seen": s.last_docnum if not s.editing else f"{s.form}NEW"}
        if s.dialogs and not self.blind:
            m = re.search(r"\d{3,4}", s.dialogs[-1]["text"])
            return {**base, "state": "dialog", "dialog_text": s.dialogs[-1]["text"],
                    "message_no": m.group(0) if m else ""}, usage
        if self.blind:
            want = {**base, "state": "as_expected"}
            if "lines" in expect:
                want["lines_seen"] = expect["lines"]
            if "total" in expect:
                want["total_seen"] = expect["total"]
            return want, usage
        stage = expect.get("stage") or ("form" if "form" in expect else "")
        ok = True
        if "form" in expect:
            ok = s.form == expect["form"] and not s.editing
        elif stage == "header":
            ok = s.editing and s.head.get("party") == d.party_code
        elif stage == "line":
            n = expect.get("lines")
            if n:
                ok = len(s.complete_rows()) >= n and self._mismatch(n) is None
        elif stage == "saved":
            ok = not s.editing and bool(s.last_docnum)
        return {**base, "state": "as_expected" if ok else "wrong"}, usage

    def cu_turn(self, model, messages, effort, max_tokens=8000):
        self.calls.append(f"cu:{model}")
        s = self.sim
        usage = {"input_tokens": 9000 + 1500 * len(messages), "output_tokens": 260,
                 "cache_read_input_tokens": 6000 if len(messages) > 1 else 0,
                 "cache_creation_input_tokens": 0 if len(messages) > 1 else 6000}
        first = messages[0]["content"][0]["text"]
        n = len(messages)

        def uses(items):
            return [{"type": "tool_use", "id": f"tu_{n}_{i}", "name": it["name"], "input": it["input"],
                     **({"toolset_name": "computer"} if it["name"] != "finish" else {})} for i, it in enumerate(items)]

        if first.startswith("任务："):  # 全程看图：按计划逐批做
            key = self.draft.doc_id
            if key not in self.plans:
                self.plans[key] = self._vision_plan()
            plan = self.plans[key]
            return _Resp(uses(plan.pop(0)), usage)
        if model.startswith("claude-haiku") and self.haiku_gives_up:
            return _Resp(uses([{"name": "finish", "input": {"status": "escalate", "note": "看不懂这个弹窗"}}]), usage)
        if s.dialogs:
            return _Resp(uses([{"name": "key", "input": {"text": "Return"}}, {"name": "screenshot", "input": {}}]),
                         usage)
        fix = self._mismatch()
        if fix:
            return _Resp(uses(fix), usage)
        return _Resp(uses([{"name": "finish", "input": {"status": "resolved", "note": "已处理"}}]), usage)

    def _vision_plan(self) -> List[List[Dict]]:
        s, d = self.sim, self.draft
        A = s.anchors
        menu = self.sim.flows["menus"][d.doc_type]
        sc = lambda xy: [xy[0] / 1.5, xy[1] / 1.5]  # noqa: E731
        batches: List[List[Dict]] = [[{"name": "screenshot", "input": {}}]]
        if s.form != d.doc_type:
            keys = [{"name": "key", "input": {"text": "alt+" + menu[0][1]}}]
            keys += [{"name": "key", "input": {"text": k}} for k in menu[1:]]
            batches.append(keys + [{"name": "screenshot", "input": {}}])
        batches.append([{"name": "left_click", "input": {"coordinate": sc(A["toolbar.new"])}},
                        {"name": "left_click", "input": {"coordinate": sc(A[f"{d.doc_type}.party"])}},
                        {"name": "type", "input": {"text": d.party_code}}, {"name": "key", "input": {"text": "Return"}},
                        {"name": "type", "input": {"text": d.date_be}}, {"name": "key", "input": {"text": "Return"}}]
                       + ([{"name": "type", "input": {"text": d.ref_no}}, {"name": "key", "input": {"text": "Return"}},
                           {"name": "type", "input": {"text": d.date_be}}, {"name": "key", "input": {"text": "Return"}}]
                          if d.doc_type in ("RR", "HP") else []))
        batches.append([{"name": "left_click", "input": {"coordinate": sc(A[f"{d.doc_type}.grid"])}}])
        for line in d.lines:
            steps = [{"name": "type", "input": {"text": line.item_code}}, {"name": "key", "input": {"text": "Return"}}]
            if line.loc:
                steps += [{"name": "key", "input": {"text": "Home"}}, {"name": "key", "input": {"text": "shift+End"}},
                          {"name": "type", "input": {"text": line.loc}}]
            steps += [{"name": "key", "input": {"text": "Return"}}, {"name": "type", "input": {"text": line.qty}},
                      {"name": "key", "input": {"text": "Return"}}, {"name": "key", "input": {"text": "Return"}}]
            batches.append(steps + [{"name": "screenshot", "input": {}}])
            batches.append([{"name": "_dialogs", "input": {}}])
            batches.append([{"name": "type", "input": {"text": line.unit_price}},
                            {"name": "key", "input": {"text": "Return", "repeat": 3}}])
        batches.append([{"name": "key", "input": {"text": "Escape"}},
                        {"name": "left_click", "input": {"coordinate": sc(A["toolbar.save"])}},
                        {"name": "screenshot", "input": {}}])
        batches.append([{"name": "finish", "input": {"status": "resolved", "note": "已保存"}}])
        out = []
        for b in batches:  # 「看一眼有没有 4079」：有就回车
            if b[0]["name"] == "_dialogs":
                out.append(None)
            else:
                out.append(b)
        return _LazyPlan(out, self)


class _LazyPlan(list):
    """全程看图计划里的「看一眼弹窗」在执行到时才决定（模型看到截图才知道有没有弹窗）。"""

    def __init__(self, items, fake):
        super().__init__(items)
        self.fake = fake

    def pop(self, index=0):
        item = super().pop(index)
        if item is None:
            if self.fake.sim.dialogs:
                return [{"name": "key", "input": {"text": "Return"}}, {"name": "screenshot", "input": {}}]
            return [{"name": "screenshot", "input": {}}]
        return item
