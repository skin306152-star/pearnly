# -*- coding: utf-8 -*-
"""MR.ERP 账本这一侧：续录前查这张存没存（``find_saved``）、存档后找回这张并在查看页逐字段核对（``verify``）。

列表口径与服务端导入直写的回读同一份（``pearnly_core.mrerp.push.readback``，CC 不能导入服务端代码，这里写同口径的解析，
``tests/test_mrerp_operator_readback_parity.py`` 拿同一份 HTML 证明两边结论一致）：
- ``{aptran,artran,arse}/allview.php?idmenu=N`` 先把菜单写进会话；整页只有「กรุณากำหนดการเชื่อมโยง …」/「กรุณาป้อนข้อมูล …」时列表不能用；
- ``component/showdata.php`` 每行一个 ``<p>``，前四格：单号、日期（佛历）、名称、金额；查看链接 ``allform.php?id=N&status=vie``；
  查不到是 ``<p id="nodata">``。列表搜索框按单号 / 名称 / 金额（数字时也按日期）``LIKE`` 查，**不按对方单号查**。

所以「这张存没存」这样判（评估 §14.3「存档后结果不明」）：
1. 开录前（批次 ``before``）在这个菜单的列表里按含税合计搜一次，记下当时已有的同日同额的单（查看页编号），叫「开录前清单」；
2. 续录前 / 存档后再按同样条件搜：**不在开录前清单里**的同日同额单，逐张打开查看页核对对方码、对方单号（和我们填的单号），
   对上的就是这一张——记单号、不重录；没有才重录。开录前清单没取到（那时网络就断了）时引擎根本不会走到存档，所以没有就是没存。
推送不查重不拦：开录前已有的同号同额单不算这一张，照录。
"""
from __future__ import annotations

import html as _html
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..express_operator.engine import NeedHuman
from .draft import Draft, dec, iso_date, money
from .recipe import Recipe
from .session import Kicked

NOT_LINKED_MARK = "กรุณากำหนดการเชื่อมโยง"
SETUP_MISSING_MARK = "กรุณาป้อนข้อมูล"
BASELINE = "_baseline"
HEADER = "เลขที่เอกสาร"  # #showdata 的第一行是列名（allview.php），不是单
MAX_CANDIDATES = 5

_ROW = re.compile(r"<p\b(?![^>]*\bid=[\"']nodata[\"'])[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_SPAN = re.compile(r"<span\b[^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_PREFIX = re.compile(r"^[A-Za-z0-9\-/.]{0,12}$")
_VIEW = re.compile(r"allform\.php\?id=(\d+)&(?:amp;)?status=vie", re.IGNORECASE)


def _text(fragment: str) -> str:
    return " ".join(_html.unescape(_TAG.sub(" ", fragment or "")).split())


@dataclass(frozen=True)
class ListedRow:
    docno: str
    date: str
    name: str
    amount: str


def listing_problem(page: str) -> Optional[str]:
    """allview 页不能当回读依据的原因：没关联日记账 / 未完成设置；能用返回 None。（同服务端 readback.listing_problem）"""
    text = page or ""
    if NOT_LINKED_MARK in text:
        return "not_linked"
    if SETUP_MISSING_MARK in text:
        return "setup_missing"
    return None


def parse_rows(page: str) -> List[ListedRow]:
    """showdata 片段 → 每张单的前四格（同服务端 readback.parse_rows）。"""
    rows = []
    for body in _ROW.findall(page or ""):
        cells = [_text(cell) for cell in _SPAN.findall(body)]
        if len(cells) >= 4 and cells[0]:
            rows.append(ListedRow(*cells[:4]))
    return rows


def parse_listing(page: str) -> List[Dict[str, str]]:
    """同 ``parse_rows``，每行多带查看页编号（``id``，没有查看权限的行为空）。"""
    out = []
    for body in _ROW.findall(page or ""):
        cells = [_text(cell) for cell in _SPAN.findall(body)]
        if len(cells) >= 4 and cells[0]:
            found = _VIEW.search(body)
            out.append({"docno": cells[0], "date": cells[1], "name": cells[2], "amount": cells[3],
                        "id": found.group(1) if found else ""})
    return out


def find(rows: List[ListedRow], invoice_no: str) -> Optional[ListedRow]:
    """以我方单号结尾、前面只多一个菜单前缀的那一行；没有或不止一行都返回 None（同服务端 readback.find）。"""
    ours = str(invoice_no or "").strip()
    if not ours:
        return None
    hits = [row for row in rows if row.docno.endswith(ours) and _PREFIX.match(row.docno[:-len(ours)])]
    return hits[0] if len(hits) == 1 else None


def same_amount(a, b) -> bool:
    x, y = (dec(str("" if v is None else v).replace(",", "")) for v in (a, b))
    return x is not None and y is not None and money(x) == money(y)


def same_text(a, b) -> bool:
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


class UnsureStore:
    """点了存档、结果不明就停下的单（被踢、网络断后整批停了）：记在本机（单据号 → 开录前清单），这张下次再来时（同一批续录、
    换一批重派、CC 重启后都算）先按清单查存没存，查到就不重录。只记单据号、网址、账套与查看页编号，不记单据内容与任何凭据。"""

    def __init__(self, path: Optional[Path]):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._memory: Dict[str, Dict] = {}

    def _load(self) -> Dict[str, Dict]:
        if self.path is None:
            return dict(self._memory)
        try:
            found = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def _write(self, data: Dict[str, Dict]) -> None:
        if self.path is None:
            self._memory = dict(data)
            return
        if not data:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    @staticmethod
    def key(account: str, doc_id: str) -> str:
        return f"{account}|{doc_id}"

    def get(self, account: str, doc_id: str) -> Optional[Dict]:
        with self._lock:
            return self._load().get(self.key(account, doc_id))

    def put(self, account: str, doc_id: str, baseline: Dict[str, int]) -> None:
        with self._lock:
            data = self._load()
            data[self.key(account, doc_id)] = {"baseline": dict(baseline), "at": round(time.time(), 1)}
            self._write(data)

    def drop(self, account: str, doc_id: str) -> None:
        with self._lock:
            data = self._load()
            if data.pop(self.key(account, doc_id), None) is not None:
                self._write(data)


class MrerpBook:
    """``session`` 是 ``session.MrerpSession``；``supported(doc_type)`` 为假的单不开页面（配方没核对）。"""

    def __init__(self, session, recipe: Recipe, *, supported: Callable[[str], bool] = lambda _t: True,
                 unsure: Optional[UnsureStore] = None):
        self.session, self.recipe, self.supported = session, recipe, supported
        self.baselines: Dict[str, Dict[str, int]] = {}  # 单据号 → 这一张的开录前清单（取不到时没有 ``_baseline``）
        self.unsure = unsure or UnsureStore(None)

    @property
    def account(self) -> str:
        conn = getattr(self.session, "conn", {}) or {}
        return f"{str(conn.get('url') or '').lower()}#{conn.get('comidyear', '')}"

    def mark_unsure(self, doc_id: str) -> None:
        """记下「点了存档、结果不明」。已经记着的不覆盖：最早那份开录前清单才不含这一张。"""
        if self.ready(doc_id) and self.unsure.get(self.account, doc_id) is None:
            self.unsure.put(self.account, doc_id, self.baseline(doc_id))

    def unsure_baseline(self, doc_id: str) -> Optional[Dict[str, int]]:
        found = self.unsure.get(self.account, doc_id)
        return dict(found.get("baseline") or {}) if found else None

    def settle(self, doc_id: str) -> None:
        self.unsure.drop(self.account, doc_id)

    # ── 批次接口 ─────────────────────────────────────────────────────────
    def counts(self) -> Dict[str, int]:
        return {}  # 网页账本没有「各表行数」；开录前清单按单取（``before``）

    def rows_since(self, _before) -> Dict:
        return {}

    def before(self, draft: Draft) -> Dict[str, int]:
        """开录前清单：``{"_baseline": 1, "<查看页编号>": 1, ...}``；取不到（网络断）回 ``{"_baseline": 0}``，引擎见了不往下录。"""
        self.baselines[draft.doc_id] = {BASELINE: 0}
        form = self.recipe.form(draft.doc_type)
        if form is None or not self.supported(draft.doc_type):
            return {BASELINE: 0}
        try:
            rows = self.search(draft)
        except NeedHuman:
            raise
        except Exception:  # noqa: BLE001 — 网络断 / 被踢：交给引擎（它会照样碰到并按那个结论收尾）
            return {BASELINE: 0}
        self.baselines[draft.doc_id] = {BASELINE: 1, **{row["id"]: 1 for row in rows if row["id"]}}
        return dict(self.baselines[draft.doc_id])

    def ready(self, doc_id: str) -> bool:
        return bool(self.baselines.get(doc_id, {}).get(BASELINE))

    def baseline(self, doc_id: str) -> Dict[str, int]:
        return dict(self.baselines.get(doc_id) or {})

    def find_saved(self, draft: Draft, before: Dict) -> Dict:
        """续录前：开录前清单之后新出现、核对得上的那一张。读不了列表 → 整批等网络（``network_down``），不冒险重录。"""
        if not (before or {}).get(BASELINE):
            return {"docnum": "", "placeholders": []}
        try:
            found = self.find_new(draft, before)
        except NeedHuman:
            raise
        except Kicked as error:
            raise NeedHuman("login_failed", f"kicked: {error}"[:300]) from None
        except Exception as error:  # noqa: BLE001
            raise NeedHuman("network_down", f"cannot check MR.ERP list: {type(error).__name__}") from None
        if found and found.get("others"):
            raise NeedHuman("readback_mismatch", "saved more than once: " + ", ".join([found["docnum"], *found["others"]]))
        if found:
            self.settle(draft.doc_id)
        return {"docnum": (found or {}).get("docnum", ""), "placeholders": [], "id": (found or {}).get("id", "")}

    # ── 列表 ────────────────────────────────────────────────────────────
    def list_path(self, draft: Draft) -> str:
        form = self.recipe.form(draft.doc_type)
        return self.recipe.list["view"].replace("{module}", form.module).replace("{menu}", str(draft.menu))

    def open_list(self, draft: Draft) -> Dict:
        state = self.session.goto(self.list_path(draft))
        page = self.session.page
        problem = listing_problem(page.text("", limit=4000))
        if problem:
            raise NeedHuman("recipe_mismatch", f"MR.ERP list not usable: {problem}")
        self.session.where = (self.recipe.form(draft.doc_type).module, str(draft.menu))
        return state

    def search(self, draft: Draft) -> List[Dict[str, str]]:
        """在这个菜单的列表里按含税合计搜（页面原本的方式：搜索框打值、点 Search，页面带 ``?searchdata=`` 重新打开、
        再自己把结果追加进 ``#showdata``），回同日同额的行。"""
        self.open_list(draft)
        page = self.session.page
        lst = self.recipe.list
        net = draft.totals()["net"]
        page.fill(lst["search_id"], f"{net:.2f}", focus=False)
        before = page.state()["loads"]
        page.click(lst["search_button"])
        self.session.inspect(page.wait_load(before, self.recipe.list_s))

        def rows():
            fragment = page.html(lst["rows"])
            if fragment is None:
                return None
            listed = [r for r in parse_listing(fragment) if r["docno"] != HEADER]
            return {"rows": listed} if listed or "nodata" in fragment else None

        found = page.wait_for(rows, self.recipe.list_s)
        if not found:
            raise NeedHuman("recipe_mismatch", "MR.ERP list did not answer the search")
        date = draft.date_th
        return [r for r in found["rows"] if same_amount(r["amount"], net) and r["date"] == date]

    def find_new(self, draft: Draft, before: Dict) -> Optional[Dict]:
        """开录前清单之后新出现的同日同额单里，查看页核对得上对方码 / 对方单号（/ 我们填的单号）的那一张。
        核对得上的不止一张（存档请求在断开的连接上被浏览器自动重发过一次，MR.ERP 写了两张）：带 ``others`` 回，由调用方交人。"""
        new = [r for r in self.search(draft) if r["id"] and r["id"] not in before][:MAX_CANDIDATES]
        hits = []
        for row in new:
            seen = self.read_view(draft, row["id"])
            if self._ours(draft, seen):
                hits.append({"docnum": seen["docnum"], "id": row["id"]})
        if not hits:
            return None
        return {**hits[0], "others": [h["docnum"] for h in hits[1:]]}

    def _ours(self, draft: Draft, seen: Dict) -> bool:
        if not same_text(seen.get("party_code"), draft.party_code):
            return False
        if draft.ref_no and not same_text(seen.get("ref_no"), draft.ref_no):
            return False
        if draft.docnum and seen.get("number_editable") and not str(seen.get("docnum", "")).endswith(draft.docnum):
            return False
        return True

    # ── 查看页 ──────────────────────────────────────────────────────────
    def read_view(self, draft: Draft, record_id: str) -> Dict:
        form = self.recipe.form(draft.doc_type)
        spec = form.module_spec
        self.session.goto(form.view_path(record_id))
        page = self.session.page
        ids = [spec["docnum"]["prefix_id"], spec["docnum"]["number_id"], spec["party_id"], spec.get("ref_id", ""),
               "txtdate", *form.totals.values()]
        n = len(draft.lines)
        for i in range(1, n + 2):
            ids += [form.line_id(name, i) for name in ("code", "qty", "price", "total")]
        values = page.read([i for i in ids if i])
        lines = []
        for i in range(1, n + 2):
            code = values.get(form.line_id("code", i))
            if code is None:
                break
            lines.append({"code": code, "qty": values.get(form.line_id("qty", i)) or "",
                          "price": values.get(form.line_id("price", i)) or "",
                          "total": values.get(form.line_id("total", i)) or ""})
        return {"docnum": f"{values.get(spec['docnum']['prefix_id']) or ''}{values.get(spec['docnum']['number_id']) or ''}",
                "number_editable": bool(draft.docnum), "party_code": values.get(spec["party_id"]) or "",
                "ref_no": values.get(spec.get("ref_id", "")) or "", "date": values.get("txtdate") or "",
                "totals": {k: values.get(v) or "" for k, v in form.totals.items()}, "lines": lines, "id": record_id}

    def verify(self, draft: Draft, record_id: str) -> Dict:
        """查看页逐字段核对：日期、对方码、对方单号、每行品码 / 数量 / 单价 / 金额、税前 / 税 / 含税合计。"""
        seen = self.read_view(draft, record_id)
        diffs: List[str] = []
        if iso_date(seen["date"]) != draft.date:
            diffs.append(f"date {seen['date']}")
        if not same_text(seen["party_code"], draft.party_code):
            diffs.append(f"party {seen['party_code']}")
        if draft.ref_no and not same_text(seen["ref_no"], draft.ref_no):
            diffs.append(f"ref {seen['ref_no']}")
        if len(seen["lines"]) != len(draft.lines):
            diffs.append(f"lines {len(seen['lines'])}")
        for i, (line, got) in enumerate(zip(draft.lines, seen["lines"]), 1):
            if not same_text(got["code"], line.code):
                diffs.append(f"line {i} code {got['code']}")
            if not same_amount(got["qty"], line.qty):
                diffs.append(f"line {i} qty {got['qty']}")
            if not same_amount(got["price"], line.price):
                diffs.append(f"line {i} price {got['price']}")
            if not same_amount(got["total"], line.amount):
                diffs.append(f"line {i} total {got['total']}")
        want = draft.totals()
        for key, value in seen["totals"].items():
            if not same_amount(value, want[key]):
                diffs.append(f"{key} {value}")
        return {"ok": not diffs, "docnum": seen["docnum"], "diffs": diffs[:12], "id": record_id}
