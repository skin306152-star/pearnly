# -*- coding: utf-8 -*-
"""代录离线回放用：假 Express（实现 ``Desk`` 同一个 ``act`` 接口）、假账套（``Book`` 接口）、假模型站点（``transport``）。

按试点 31516fff 在服务器版 69EXP\\test 上实测的录法，模拟执行器要面对的事（不是 Express 的仿真）：
- 菜单：先单独按 Alt 再按数字；单据窗口开着时 Alt+数字 弹 Message#.40；Ctrl+F4 关单据窗口；给了 ``menus``（菜单树，同
  ``menu_tree`` 的形状）时按真菜单找命令号（``menu.resolve``，同后台做法），``menu_forms`` 是命令号 → 单种、``menu_titles``
  是命令号 → 窗口里的单据名（定制菜单开出的窗口名可能不同）；
- 工具栏命令号（新建 / 保存 / 取消 / 作废），编辑中「保存」才亮，灰的命令拒绝；
- 表头按回车逐格跳（标签顺序照实测）；单号格新建后是「前缀**NEW**」（赊销 / 现销先只显示前缀，录了客户才带 NEW）；
  日期打「日/月/佛历两位年」，格子里读出来是公历 yyyymmdd；对方码不存在回车弹选择框；
  价格类型跟对方主档变（inject「incl_party:X」= 这家默认含税）；
- 明细表格回车进不去，要 grid_click；单位框里回车是跳到「倍数」格，要按「ตกลง」；
- 已有明细时按 Esc 或「取消」会直接存档（陷阱）；保存后单号变正式号；作废要确认框按「ตกลง」；
- 弹窗：单位框、4079 / 4108（提醒）、1029（对方单号已存在）、陌生弹窗、4224（负库存拦）、-70（崩）。
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
import struct
import zlib
from decimal import Decimal
from typing import Dict, List, Optional

from companion.express_operator import menu as M
from companion.express_operator.desk import Refused, run
from companion.express_operator.draft import Draft, Line, money
from companion.secret_store import ExpressSettings

NEW, SAVE, CANCEL, VOID = 53504, 53507, 53512, 53515
GRID_COLS = ["item", "loc", "qty", "price", "disc", "amount"]
PARTY = {"RR": "ผู้จำหน่าย", "HP": "ผู้จำหน่าย", "IV": "รหัสลูกค้า", "HS": "รหัสลูกค้า"}
DATES = {"วันที่", "ลวท."}
PRICE = "ประเภทราคา"
PRICE_TEXT = {"0": "0 - ไม่มี VAT", "1": "1 - รวม VAT", "2": "2 - แยก VAT"}
PARTIES = ("P001", "P002", "5001", "CASH", "นาย\xa0ทดสอบ")
ITEMS = ("501780", "500310", "501871")


def tiny_png() -> str:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\0\xff\xff\xff")) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode()


def _tokens(spec: str) -> List[str]:
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


def _date_text(typed: str) -> str:
    try:
        d, m, y = typed.strip().split("/")
        return dt.date(int(y) + 2500 - 543, int(m), int(d)).strftime("%Y%m%d")
    except (ValueError, TypeError):
        return typed


def express_settings(account_dir: str, *, exe: str = "", company: str = "x", account_set: str = "TEST") -> ExpressSettings:
    """测试用真实的 Express 设置（``secret_store.ExpressSettings``，写账线交给代录的就是它，不带站点与令牌）。"""
    return ExpressSettings(method="dbf", express_root="", express_exe=exe, account_dir=account_dir,
                           account_company=company, account_set=account_set, account_set_row=0)


def samples() -> List[Draft]:
    """四种单各一张（码是虚构的，只在假 Express 里用）。"""
    return [
        Draft("S-RR-1", "RR", "2026-10-20", "P001", [Line("501780", "5", "120.00"), Line("500310", "2", "35.50", "02")],
              ref_no="INV-7781"),
        Draft("S-HP-1", "HP", "2026-10-21", "P002", [Line("501871", "1", "999.00")], ref_no="RC-1102"),
        Draft("S-IV-1", "IV", "2026-10-22", "5001", [Line("501780", "3", "180.00")]),
        Draft("S-HS-1", "HS", "2026-10-23", "CASH", [Line("500310", "4", "53.50")], vat_included=True),
    ]


class FakeExpress:
    def __init__(self, recipe_data: Dict, *, inject: Optional[Dict] = None, readable: str = "win32",
                 parties=PARTIES, items=ITEMS, company: str = "บริษัท ทดสอบ จำกัด", exe: str = "",
                 menus: Optional[List[Dict]] = None, menu_forms: Optional[Dict[int, str]] = None,
                 menu_titles: Optional[Dict[int, str]] = None):
        self.data = recipe_data
        self.readable = readable
        self.inject = inject or {}
        self.parties, self.items, self.company, self.exe = set(parties), set(items), company, exe
        self.menu_active, self.menu = False, []
        self.menus, self.menu_forms, self.menu_titles = menus, dict(menu_forms or {}), dict(menu_titles or {})
        self.menu_sent: List[int] = []  # 按真菜单发出的命令号
        self.form_name = ""  # 窗口里的单据名（空 = 配方标题）
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
        self.book: Dict[str, List[Dict]] = {"APTRN": [], "ARTRN": [], "STCRD": [], "ISVAT": [], "GLJNL": []}
        self.refs = set()
        self.cursor = [0, 0]
        self.unit_mult = "1.000"
        self.trap_saves = 0
        self.esc_pressed = 0
        self.log: List[str] = []
        # 慢的 Express（11-01 录制机：另开着一个 Express 时第一次开表单、存档都比平常慢）：inject「slow_form」「slow_save」
        # = 还要等几次（engine 的 wait）表单标题才出来 / 存档才落下
        self.form_delay = 0
        self.save_delay = 0

    # ── 画面 ───────────────────────────────────────────────────────────────
    def labels(self) -> List[str]:
        return self.data["forms"][self.form]["header"]["labels"] if self.form else []

    def docnum_label(self) -> str:
        return self.data["forms"][self.form]["docnum_label"]

    def title(self) -> str:
        if self.dialogs:
            return self.dialogs[-1]["title"]
        if self.form and not self.form_delay:
            name = self.form_name or self.data["forms"][self.form]["title"]
            return f"Express Accounting - {name} - [{name}]"
        return "Express Accounting"

    def dialog(self, name: str, title: str, text: str, buttons=("ตกลง",), after=None) -> None:
        self.hwnd_seq += 1
        self.dialogs.append({"hwnd": self.hwnd_seq, "name": name, "title": title, "text": text, "buttons": list(buttons),
                             "after": after})

    def windows(self) -> List[Dict]:
        if not self.alive:
            return []
        out = [{"hwnd": 100, "title": "Express Accounting", "cls": "Afx:400000:8", "rect": [0, 0, 1920, 1040],
                "owned": False, "texts": [self.company]}]
        out += [{"hwnd": d["hwnd"], "title": d["title"], "cls": "#32770", "rect": [700, 400, 1200, 560],
                 "owned": True, "texts": [d["text"]] + ["&" + b for b in d["buttons"]], "buttons": ["&" + b for b in d["buttons"]]}
                for d in self.dialogs]
        if self.exe:
            for w in out:
                w["exe"] = self.exe
        return out

    def incl(self) -> bool:
        return self.head.get(PRICE, "").startswith("1")

    def complete_rows(self) -> List[Dict]:
        return [r for r in self.grid if r.get("item") and r.get("qty") and r.get("price")]

    def totals(self) -> Dict[str, Decimal]:
        gross = sum((money(Decimal(r["qty"]) * Decimal(r["price"])) for r in self.complete_rows()), Decimal(0))
        if self.head.get(PRICE, "").startswith("0"):  # ไม่มี VAT：不算税
            return {"net": gross, "vat": Decimal(0), "total": gross, "shown": gross}
        if self.incl():
            vat = money(gross * 7 / Decimal(107))
            return {"net": gross - vat, "vat": vat, "total": gross, "shown": gross}
        vat = money(gross * Decimal("0.07"))
        return {"net": gross, "vat": vat, "total": gross + vat, "shown": gross}

    def fields(self) -> List[Dict]:
        out = []
        for i, label in enumerate(self.labels()):
            out.append({"hwnd": 1000 + i, "cls": "ComboBox" if label == PRICE else "Edit",
                        "rect": [200, 100 + i * 30, 360, 128 + i * 30], "text": self.head.get(label, ""), "label": label})
        if self.form:
            t = self.totals()
            for j, (label, key) in enumerate((("จำนวนเงิน", "shown"), ("%", "vat"), ("จำนวนเงินรวมทั้งสิ้น", "total"))):
                out.append({"hwnd": 2000 + j, "cls": "Edit", "rect": [1694, 800 + j * 34, 1852, 832 + j * 34],
                            "text": f"{t[key]:.2f}", "label": label})
        return out

    def focus_info(self) -> Dict:
        if self.dialogs:
            unit = self.focus and self.focus[0] == "unit"
            return {"hwnd": self.dialogs[-1]["hwnd"] + 1, "cls": "Edit", "text": self.unit_mult if unit else "",
                    "rect": [800, 450, 900, 480]}
        if self.focus is None:
            return {"hwnd": 0, "cls": "", "text": "", "rect": None}
        if self.focus[0] == "head":
            i = self.labels().index(self.focus[1])
            return {"hwnd": 1000 + i, "cls": "ComboBox" if self.focus[1] == PRICE else "Edit",
                    "text": self.head.get(self.focus[1], ""), "rect": [200, 100 + i * 30, 360, 128 + i * 30]}
        _, row, col = self.focus
        cell = self.grid[row].get(col, "") if row < len(self.grid) else ""
        if col == "no":  # 账套设了「允许插入明细行」（ISINFO.INSERTIT = Y）：最左多一栏可编辑的行号，窄
            return {"hwnd": 5000, "cls": "Edit", "text": cell, "rect": [50, 470 + row * 30, 95, 498 + row * 30]}
        x = 100 + GRID_COLS.index(col) * 150
        return {"hwnd": 5000, "cls": "Edit", "text": cell, "rect": [x, 470 + row * 30, x + 140, 498 + row * 30]}

    # ── 输入 ───────────────────────────────────────────────────────────────
    def _type(self, text: str) -> None:
        if self.dialogs:
            if self.dialogs[-1]["name"] == "unit" and self.focus == ("unit", "mult"):
                self.unit_mult = text  # 单位框里打字进「倍数」格（实测陷阱）
            return
        if self.focus is None:
            return
        if self.focus[0] == "head":
            label = self.focus[1]
            if label == PRICE:
                self.head[label] = PRICE_TEXT.get(text[:1], self.head.get(label, ""))
            elif label in DATES and self.select_all:
                self.head[label] = _date_text(text)
            else:
                self.head[label] = ("" if self.select_all else self.head.get(label, "")) + text
        else:
            _, row, col = self.focus
            while len(self.grid) <= row:
                self.grid.append({})
            cell = self.grid[row]
            key = f"typo_{col}:{self.head.get(PARTY[self.form], '').strip()}"
            if key in self.inject:
                text = self.inject.pop(key)
            cell[col] = text if self.select_all else cell.get(col, "") + text
            if col == "no":
                cell[col] = cell[col][:3]  # 行号栏只收 3 个字（11-19 测试机2：品码读回「000」「53-」）
        self.select_all = False

    def _set_focus(self, focus) -> None:
        self.focus = focus
        self.select_all = False

    def _enter(self) -> None:
        if self.dialogs:
            d = self.dialogs[-1]
            if d["name"] == "unit":
                self.focus, self.select_all = ("unit", "mult"), True
                return
            if d["name"] != "picker":
                self._press(d, d["buttons"][0])
            return
        if self.focus is None:
            return
        if self.focus[0] == "head":
            label = self.focus[1]
            order = self.labels()
            if label == PARTY[self.form]:
                code = self.head.get(label, "").strip()
                if f"party:{code}" in self.inject:
                    self.dialog("unknown", "ExpressI", self.inject.pop(f"party:{code}")["text"])
                if code not in self.parties:
                    self.dialog("picker", "", "ค้นหา", buttons=("ตกลง", "ยกเลิก", "เพิ่ม <Alt+&A>"))
                    return
                if f"incl_party:{code}" in self.inject:
                    self.head[PRICE] = PRICE_TEXT["1"]
                if self.form in ("IV", "HS"):
                    self.head[self.docnum_label()] = f"{self.form}**NEW**"
            if label == "วันที่" and "period_outside" in self.inject:
                self.dialog("70", "Message#. 70", "วันที่ไม่อยู่ในงวด")
            if label == "เลขที่บิล":
                ref = self.head.get(label, "").strip()
                if ref and ref in self.refs:
                    self.dialog("1029", "Message#. 1029", f"รหัส '{ref}' นี้มีอยู่แล้ว")
            nxt = order.index(label) + 1
            self._set_focus(("head", order[nxt]) if nxt < len(order) else None)
            return
        _, row, col = self.focus
        cell = self.grid[row] if row < len(self.grid) else {}
        if col == "no":  # 行号栏回车走到品码栏；空行的品码格这时填满空格（11-20 测试机2 实录：20 个空格，存档时当品码去查）
            if not cell.get("item"):
                cell["item"] = " " * 20
            self._set_focus(("grid", row, "item"))
            return
        if col == "item":
            if cell.get("item") not in self.items:
                self.dialog("picker", "", "ค้นหา", buttons=("ตกลง", "ยกเลิก", "เพิ่ม <Alt+&A>"))
                return
            if f"block_item:{cell.get('item')}" in self.inject:
                self.dialog("neg", "Message#. 4224", "ยอดคงเหลือของสินค้าติดลบ")
            cell.setdefault("loc", "01")
            self._set_focus(("grid", row, "loc"))
            if row == 0 and self.form:
                self._prewrite()
        elif col == "loc":
            self._set_focus(("grid", row, "qty"))
        elif col == "qty":
            def after_unit():
                party = self.head.get(PARTY[self.form], "")
                if self.form in ("IV", "HS") and (party, cell.get("item")) not in self.sold:
                    self.sold.add((party, cell.get("item")))
                    self.dialog("4079", "Message#. 4079", "ลูกค้ารายนี้ไม่เคยซื้อสินค้านี้")
                if f"warn4108:{party}" in self.inject:
                    self.dialog("4108", "Message#. 4108", "คำเตือน! ไม่มีประวัติการซื้อจากผู้จำหน่ายรายนี้")
            self.unit_mult = "1.000"

            def show_unit():
                self.dialog("unit", "ป้อนหน่วยนับที่ซื้อ", "หน่วยย่อยนับเป็น", buttons=("ตกลง", "ยกเลิก"), after=after_unit)
                self.dialogs[-1]["row"] = row
                self.focus = ("grid", row, "price")

            # 慢的 Express：单位框过几次等待才弹，这之前光标还在数量格（inject「late_unit」= 还要等几次）
            self.unit_delay = int(self.inject.pop("late_unit", 0))
            if self.unit_delay:
                self.pending_unit = show_unit
            else:
                show_unit()
        elif col == "price":
            self._set_focus(("grid", row, "disc"))
        elif col == "disc":
            self._set_focus(("grid", row, "amount"))
        else:
            self.grid.append({})
            self._set_focus(("grid", row + 1, "no" if self.inject.get("line_no_column") else "item"))

    def _prewrite(self) -> None:
        """录到第一行明细时 Express 先写一张金额 0 的单头占号（实测）；保存时才填金额。"""
        if self.head.get("_docnum"):
            return
        T = self.form
        self.counters[T] = self.counters.get(T, 0) + 1
        docnum = f"{T}{self.counters[T]:07d}"
        self.head["_docnum"] = docnum
        table = "APTRN" if T in ("RR", "HP") else "ARTRN"
        party_col, ref_col = ("SUPCOD", "REFNUM") if table == "APTRN" else ("CUSCOD", "YOUREF")
        dd = self.head.get("วันที่", "20261026")
        day = dt.date(int(dd[:4]), int(dd[4:6]), int(dd[6:8])) if dd.isdigit() and len(dd) == 8 else dt.date(2026, 1, 1)
        self.book[table].append({"_row": len(self.book[table]), "_del": False, "DOCNUM": docnum, "DOCSTAT": "N",
                                 "DOCDAT": day.isoformat(), party_col: self.head.get(PARTY[T], "").strip(),
                                 ref_col: self.head.get("เลขที่บิล", "").strip(), "NETVAL": 0.0, "VATAMT": 0.0,
                                 "NETAMT": 0.0})

    def _press(self, d: Dict, button: str) -> None:
        self.dialogs.remove(d)
        if d["name"] == "unit":
            if button == "ตกลง" and d.get("after"):
                d["after"]()
            self.focus, self.select_all = ("grid", d.get("row", 0), "price"), True
        elif d["name"] == "1312" and button == "ตกลง":
            # 取消后单号格显示上一张已存的单（10-26 实机）
            last = self.counters.get(self.form, 0)
            self.editing, self.grid, self.focus = False, [], None
            self.head = {self.docnum_label(): f"{self.form}{last:07d}"} if last else {}
        elif d["name"] == "void" and button == "ตกลง":
            self._void()
        elif d["name"] == "choice":
            self.choice_answered = True
            self._save()
        elif d["name"] == "basis":
            self.basis_answered = True
            self.basis_button = button
            self._save()
        elif d["name"] == "smb":
            self.alive = False

    def _trap_save(self) -> None:
        self.trap_saves += 1
        self.save_delay = int(self.inject.pop("slow_trap", 0))  # 有明细时「取消」就是存档：慢的 Express 要等几次才落下
        if not self.save_delay:
            self._save()

    def _key_token(self, token: str) -> None:
        name = token.lstrip("+^%")
        if self.menu_active and len(name) == 1 and name.isalnum():
            self.menu.append(name)
            for doc_type, spec in self.data["forms"].items():
                if self.menu == [str(k) for k in spec["menu"]]:
                    self.form, self.editing, self.menu_active, self.menu = doc_type, False, False, []
                    self.head, self.grid, self.focus = {}, [], None
            return
        if token.startswith("%") and len(token) == 2:
            if self.form:
                self.dialog("40", "Message#. 40", "กรุณาสร้างแบบฟอร์มจากต้นฉบับ")
            else:
                self.menu_active, self.menu = True, [name]
            return
        if name == "{ENTER}" or name == "{TAB}":
            self._enter()
        elif name == "{ESC}":
            self.esc_pressed += 1
            if self.dialogs:
                self.dialogs.pop()
            elif self.editing and self.grid:
                self._trap_save()
        elif name in ("{HOME}", "{END}"):
            self.select_all = True
        elif len(name) == 1:
            self._type(name)

    def _command(self, cmd: int) -> None:
        if self.dialogs:
            raise Refused("dialog open")
        if cmd == NEW and self.form and not self.editing:
            self.editing, self.grid = True, []
            self.head = {label: "" for label in self.labels()}
            self.head["วันที่"] = "20261026"
            self.head[self.docnum_label()] = self.form if self.form in ("IV", "HS") else f"{self.form}**NEW**"
            self.head[PRICE] = PRICE_TEXT["1"] if self.form == "HS" else PRICE_TEXT["2"]
            self._set_focus(("head", self.labels()[0]))
        elif cmd == SAVE and self.editing:
            self.save_delay = int(self.inject.pop("slow_save", 0))
            if not self.save_delay:
                self._save()
        elif cmd == CANCEL and self.editing:
            if self.complete_rows():
                self._trap_save()
            else:
                self.dialog("1312", "Message#. 1312", "กรุณายืนยัน เพื่อยกเลิกการเพิ่มหรือแก้ไข", buttons=("ตกลง", "ยกเลิก"))
        elif cmd == VOID and not self.editing and self.form and self.head.get(self.docnum_label(), "")[2:].isdigit():
            self.dialog("void", "ExpressI", "กรุณาเลือกตกลง เพื่อยืนยันให้ยกเลิกเอกสาร", buttons=("ตกลง", "ยกเลิก"))

    def _save(self) -> None:
        party = self.head.get(PARTY[self.form], "").strip()
        if any(r.get("item") and not r["item"].strip() for r in self.grid):
            # 有一行的品码格是一串空格（打开了却没录）：Express 存档时拿它去查，弹品码选择框（11-20 测试机2 item_missing 的真因）
            self.dialog("picker", "", "ค้นหา", buttons=("ตกลง", "ยกเลิก", "เพิ่ม <Alt+&A>"))
            return
        if f"crash_on_save:{party}" in self.inject:
            self.inject.pop(f"crash_on_save:{party}")
            self.dialog("smb", "ExpressI", r"Error -70 (90621) Reading File \\ACCSERVER\ACCOUNT\69EXP\TEST\STMAS.CDX")
            return
        if self.inject.get("odd_mid_save") and not getattr(self, "odd_shown", False):
            # 存档途中弹一个配方里没有的框：引擎认不出，单停在存档中途（11-12 实机是销项税口径框，11-13 已进配方）
            self.odd_shown = True
            self.dialog("odd", "ExpressI", "ข้อความที่ไม่รู้จักระหว่างบันทึก", buttons=("ตกลง", "ยกเลิก"))
            return
        if self.inject.get("choice_mid_save") and not getattr(self, "choice_answered", False):
            # 存档途中问一个配方里没有的业务选择（两个业务钮）：Express 这时已写下金额 0 的单头和明细行，分录与税还没写
            # （11-13 测试机2 半截单 IV0000045 的原生形状）。人答了才接着存完；Express 被关就停在这个半截状态
            self._prewrite()
            self._stock_rows()
            self.dialog("choice", "ExpressI", "เลือกวิธีบันทึกรายการนี้", buttons=("แบบ ก", "แบบ ข"))
            return
        if self.inject.get("sales_basis_on_save") and not getattr(self, "basis_answered", False):
            # 11-13 实机（测试机2 70EXP\\test）：有服务品的赊销单存档途中问服务品销项税口径，答了才接着存
            self.dialog("basis", "ExpressI", "ภาษีขายของสินค้าบริการในที่นี้เป็นแบบ", buttons=("เกณฑ์สิทธิ", "เกณฑ์เงินสด"))
            return
        if self.inject.get("basis_on_save") and not getattr(self, "basis_answered", False):
            # 10-29 实机：有服务品的赊购单存档途中问服务品进项税口径，答了才接着存
            self.dialog("basis", "ExpressI", "ภาษีซื้อของสินค้าบริการในที่นี้เป็นแบบ", buttons=("เกณฑ์สิทธิ", "เกณฑ์เงินสด"))
            return
        self.basis_answered = False
        self._prewrite()
        rows = self.complete_rows()
        docnum = self.head.pop("_docnum")
        t = self.totals()
        table = "APTRN" if self.form in ("RR", "HP") else "ARTRN"
        head = next(r for r in self.book[table] if r["DOCNUM"] == docnum)
        head.update({"NETVAL": float(t["net"]), "VATAMT": float(t["vat"]), "NETAMT": float(t["total"])})
        if getattr(self, "basis_button", ""):  # 答了服务品税口径框：照 Express 记进表头（เกณฑ์เงินสด → '2'，否则 '1'）
            head["SRV_VATTYP"] = "2" if self.basis_button == "เกณฑ์เงินสด" else "1"
            self.basis_button = ""
        self.refs.add(head.get("REFNUM", ""))
        self._stock_rows(docnum)
        if rows and t["vat"]:
            self.book["ISVAT"].append({"_row": len(self.book["ISVAT"]), "_del": False, "DOCNUM": docnum,
                                       "AMT01": float(t["net"]), "VAT01": float(t["vat"])})
        self.book["GLJNL"].append({"_row": len(self.book["GLJNL"]), "_del": False, "VOUCHER": docnum})
        self.head[self.docnum_label()] = docnum
        self.editing, self.focus = False, None
        self.choice_answered = False
        if self.inject.get("odd_after_save"):  # 存好之后又弹一个配方里没有的框（单已完整入库）
            self.dialog("odd", "ExpressI", "ข้อความที่ไม่รู้จักหลังบันทึก")

    def _stock_rows(self, docnum: str = "") -> None:
        """明细行进 STCRD（存档时写；保存途中被问时已先写好，存完不重复写）。"""
        docnum = docnum or self.head.get("_docnum", "")
        if any(r["DOCNUM"] == docnum for r in self.book["STCRD"]):
            return
        for i, r in enumerate(self.complete_rows(), start=1):
            self.book["STCRD"].append({"_row": len(self.book["STCRD"]), "_del": False, "DOCNUM": docnum, "SEQNUM": str(i),
                                       "STKCOD": r["item"], "LOCCOD": r.get("loc", "01"), "TRNQTY": float(r["qty"]),
                                       "UNITPR": float(r["price"]),
                                       "TRNVAL": float(money(Decimal(r["qty"]) * Decimal(r["price"])))})

    def _void(self) -> None:
        docnum = self.head.get(self.docnum_label(), "")
        for table in ("APTRN", "ARTRN"):
            for r in self.book[table]:
                if r["DOCNUM"] == docnum:
                    r.update({"DOCSTAT": "C", "NETVAL": 0.0, "VATAMT": 0.0, "NETAMT": 0.0})
        for r in self.book["STCRD"] + self.book["ISVAT"]:
            if r["DOCNUM"] == docnum:
                r["_del"] = True
        self.log.append(f"void {docnum}")

    # ── Desk 接口 ──────────────────────────────────────────────────────────
    def do(self, a: Dict) -> Dict:
        op = a["do"]
        if op == "windows":
            return {"windows": self.windows()}
        if not self.alive:
            raise RuntimeError("Express not running")
        if op == "wait":
            if not a.get("ms"):
                return {}  # 只刷新、不等：时间没走
            if self.form_delay:
                self.form_delay -= 1
            if self.save_delay:
                self.save_delay -= 1
                if not self.save_delay:
                    self._save()
            if getattr(self, "unit_delay", 0):
                self.unit_delay -= 1
                if not self.unit_delay:
                    self.pending_unit()
            return {}
        if op in ("us_layout", "maximize_main", "move", "mouse", "scroll", "drag"):
            return {}
        if op == "fit":
            self.fits = getattr(self, "fits", 0) + 1
            return {"moved": []}
        if op == "activate":
            return {"title": self.title()}
        if op == "menu_tree":
            return {"tree": self.menus or []}
        if op == "menu" and self.menus is not None:
            cmd, _ = M.resolve(self.menus, [str(k) for k in a["path"]], str(a.get("name") or ""))
            self.menu_sent.append(cmd)
            if cmd in self.menu_forms:
                self.form, self.editing, self.head, self.grid, self.focus = self.menu_forms[cmd], False, {}, [], None
                self.form_name = self.menu_titles.get(cmd, "")
            return {"cmd": cmd}
        if op == "menu":
            self.do({"do": "key", "text": "alt"})
            for k in a["path"]:
                self._key_token(str(k))
            self.form_delay = int(self.inject.pop("slow_form", 0))
            return {}
        if op == "close_form":
            return self.do({"do": "key", "text": "ctrl+F4"})
        if op == "company_pick":  # 公司列表上选公司（行号 + 查找 + 确定）：记下怎么选的，进 inject["picked"] 给的那家公司
            self.picks = getattr(self, "picks", []) + [{k: a.get(k) for k in ("row", "name")}]
            self.dialogs = [d for d in self.dialogs if "เรียงใหม่ <Tab>" not in d["buttons"]]
            if self.inject.get("picked"):
                self.company = self.inject["picked"]
            return {"row": a.get("row"), "search": "ok"}
        if op == "login_fill":
            login = next((d for d in self.dialogs if a["title"] in d["title"]), None)
            if login is None:
                raise Refused("login window not found")
            self.logged_in_as = a["user"] if a["password"] == self.inject.get("password", "secret") else None
            self.dialogs.remove(login)
            if self.logged_in_as is None:
                self.dialog("login", a["title"], "รหัสผู้ใช้", buttons=("OK", "Cancel"))  # 密码不对：登录窗还在
                if self.inject.get("login_message"):
                    self.dialog("login_message", "Express", self.inject["login_message"], buttons=("OK",))
            self.login_fills = getattr(self, "login_fills", 0) + 1
            return {"sent": True}
        if op == "keys":
            for t in _tokens(a["text"]):
                self._key_token(t)
            return {}
        if op == "key":
            text = a["text"].lower()
            names = {"return": "{ENTER}", "enter": "{ENTER}", "escape": "{ESC}", "tab": "{TAB}", "home": "{HOME}",
                     "end": "{END}"}
            for _ in range(int(a.get("repeat", 1))):
                if text == "alt":
                    self.menu_active, self.menu = True, []
                elif text == "ctrl+f4":
                    if self.editing and self.complete_rows():
                        self._trap_save()
                    elif self.editing:
                        self.dialog("1312", "Message#. 1312", "กรุณายืนยัน", buttons=("ตกลง", "ยกเลิก"))
                    else:
                        self.form = None
                else:
                    self._key_token(names.get(text.split("+")[-1], text.split("+")[-1]))
            return {}
        if op in ("type", "paste"):
            self.log.append(f"type {a['text']}")
            self._type(a["text"])
            return {}
        if op == "command":
            if a["cmd"] == SAVE and not self.editing:
                raise Refused("command disabled")
            self._command(a["cmd"])
            return {"cmd": a["cmd"]}
        if op == "toolbar":
            on = {NEW, CANCEL} | ({SAVE} if self.editing else {VOID})
            return {"buttons": [{"cmd": int(c), "enabled": int(c) in on} for c in a.get("cmds", [])]}
        if op == "fields":
            out = self.fields()
            if self.readable == "none":
                out = [dict(f, text="") for f in out]
            elif self.inject.get("blank_docnum") and self.form and not self.editing:
                # 存好以后单号格一时读不出字（11-01 录制机，存对的单被当成画面不对）
                out = [dict(f, text="") if f.get("label") == self.docnum_label() else f for f in out]
            return {"fields": out}
        if op == "state":
            f = self.focus_info()
            if self.readable != "win32":
                f = dict(f, text="")
            return {"fg": {"title": self.title(), "exe": "ExpressI.exe"}, "windows": self.windows(), "focus": f}
        if op == "grid_click":
            if self.editing and not self.dialogs:
                if not self.grid:
                    self.grid.append({})
                # 行号栏可编辑的账套：点品码栏，新行的焦点却停在最左的行号栏（11-19 测试机2 NLTTEST / TEST2 打开 INSERTIT 复现）
                self._set_focus(("grid", 0, "no" if self.inject.get("line_no_column") else "item"))
            return {"xy": [210, 471]}
        if op == "dialog_button":
            if not self.dialogs:
                raise Refused("no dialog")
            d = self.dialogs[-1]
            if a["text"] not in d["buttons"]:
                raise Refused(f"no button {a['text']}")
            self._press(d, a["text"])
            return {"pressed": a["text"]}
        if op == "click":
            self.cursor = [a["x"], a["y"]]
            return {}
        if op == "cursor":
            return {"pos": self.cursor}
        if op in ("screenshot", "zoom"):
            return {"png": tiny_png(), "screen": [1920, 1080]}
        if op == "close_express":
            self.alive, self.form, self.editing, self.dialogs = False, None, False, []
            return {"sent": "WM_CLOSE"}
        raise Refused(f"unknown action {op}")

    def act(self, actions: List[Dict], windows: bool = True) -> Dict:
        out = {"results": run(self.do, actions)}
        if windows:
            out["windows"] = self.windows()
            out["fg"] = {"title": self.title(), "exe": "ExpressI.exe"}
        return out


class FakeBook:
    """假账套：读的是假 Express 写进去的行，另带主档（APMAS / ARMAS / STMAS）。"""

    def __init__(self, sim: FakeExpress, *, masters: Optional[Dict[str, List[Dict]]] = None):
        self.sim = sim
        self.masters = masters if masters is not None else {
            "APMAS": [{"SUPCOD": c, "VATRAT": 7, "_del": False} for c in ("P001", "P002")],
            "ARMAS": [{"CUSCOD": c, "VATRAT": 7, "_del": False} for c in ("5001", "CASH", "นาย\xa0ทดสอบ")],
            "STMAS": [{"STKCOD": c, "_del": False} for c in ITEMS]}

    def _table(self, name: str) -> List[Dict]:
        return self.masters.get(name) if name in self.masters else self.sim.book.get(name, [])

    def counts(self, tables=("APTRN", "ARTRN", "STCRD", "ISVAT", "GLJNL")) -> Dict[str, int]:
        return {t: len(self._table(t)) for t in tables}

    def rows_since(self, since: Dict[str, int], tables=("APTRN", "ARTRN", "STCRD", "ISVAT")) -> Dict[str, List[Dict]]:
        return {t: [dict(r) for r in self._table(t)[since.get(t, 0):]] for t in tables}

    def codes(self, table: str, column: str) -> set:
        return {str(r.get(column) or "").strip() for r in self._table(table) if not r.get("_del")}


class FakeSite:
    """假模型站点（``ComputerUseHelper`` 的 transport）：按假 Express 的真实状态回答。

    接手时直接把假 Express 改成草稿单的样子（模型具体怎么点不是这里要测的；要测的是引擎的分层、接手、核对）。
    ``haiku_gives_up``：Haiku 一律 escalate（测升级到 Opus）；``blind``：截图核对一律说相符（测读回能不能查出录错）；
    ``budget_after``：第几次请求起回 budget。"""

    def __init__(self, sim: FakeExpress, *, haiku_gives_up: bool = False, blind: bool = False,
                 budget_after: Optional[int] = None):
        self.sim, self.haiku_gives_up, self.blind, self.budget_after = sim, haiku_gives_up, blind, budget_after
        self.draft: Optional[Draft] = None
        self.calls: List[str] = []

    def _want_head(self) -> Dict[str, str]:
        d, T = self.draft, self.sim.form
        values = {"วันที่": d.date.replace("-", ""), PARTY[T]: d.party_code, PRICE: PRICE_TEXT[d.vat_mode]}
        if T in ("RR", "HP"):
            values.update({"เลขที่บิล": d.ref_no, "ลวท.": d.date.replace("-", "")})
        return values

    def _fix(self) -> bool:
        s, d = self.sim, self.draft
        if s.dialogs:
            dlg = s.dialogs[-1]
            s._press(dlg, "ยกเลิก" if dlg["name"] == "picker" else dlg["buttons"][0])
            return True
        changed = False
        for label, want in self._want_head().items():
            if want and s.head.get(label, "").strip() != want:
                s.head[label] = want
                changed = True
        if s.focus and s.focus[0] == "grid":
            upto = min(len(d.lines), len(s.grid))
            for i, line in enumerate(d.lines[:upto]):
                for col, want in (("item", line.item_code), ("qty", line.qty), ("price", line.unit_price)):
                    if s.grid[i].get(col) != want:
                        s.grid[i][col] = want
                        changed = True
                s.grid[i].setdefault("loc", "01")
            if changed and upto:
                s.grid.append({})
                s._set_focus(("grid", len(s.grid) - 1, "item"))
        return changed

    def __call__(self, kind: str, request: Dict) -> Dict:
        self.calls.append(f"{kind}:{request['model']}")
        if self.budget_after is not None and len(self.calls) > self.budget_after:
            return {"error": "budget"}
        s = self.sim
        if kind == "check":
            base = {"what_i_see": "", "dialog_text": "", "message_no": "", "lines_seen": len(s.complete_rows()),
                    "total_seen": str(s.totals()["total"]) if s.form else "",
                    "docnum_seen": s.head.get(s.docnum_label(), "") if s.form else "", "state": "as_expected"}
            if self.blind:  # 看不出问题：只说相符，不报行数和合计
                base.update(lines_seen=None, total_seen="")
            if s.dialogs and not self.blind:
                m = re.search(r"\d{3,4}", s.dialogs[-1]["title"] + s.dialogs[-1]["text"])
                base.update(state="dialog", dialog_text=s.dialogs[-1]["text"], message_no=m.group(0) if m else "")
            return {"content": [{"type": "text", "text": json.dumps(base, ensure_ascii=False)}], "stop_reason": "end_turn"}
        n = len(request["messages"])

        def uses(items):
            return {"content": [{"type": "tool_use", "id": f"tu_{n}_{i}", "name": it["name"], "input": it["input"]}
                                for i, it in enumerate(items)], "stop_reason": "tool_use"}

        if request["model"].startswith("claude-haiku") and self.haiku_gives_up:
            return uses([{"name": "finish", "input": {"status": "escalate", "note": "unknown"}}])
        if self._fix():
            return uses([{"name": "screenshot", "input": {}}])
        return uses([{"name": "finish", "input": {"status": "resolved", "note": "done"}}])
