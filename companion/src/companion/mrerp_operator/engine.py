# -*- coding: utf-8 -*-
"""MR.ERP 一张单的固定流程（ADR-026 第 3 条、评估 §9、§14.1、§14.3）。

菜单号开列表 → 「Add」→ 表头（单号 / 日期 / 现金勾选 / 税率 / 对方 / 信用天数 / 对方单号）→ 明细逐行（选择框按码选商品，等页面
自己带出的成本价回来再填数量单价，每格读回）→ 读页面合计（页面自己的 ``calcgrandtotal`` 算的）与草稿对上才存 → 「Save」→
列表里找回这张（开录前清单之后新出现、核对得上的）→ 查看页逐字段核对 → 记 MR.ERP 单号 → 查看页截图存证（只存本机）。

- **放弃 = 不存、离开表单**（打开列表页）：MR.ERP 存档前服务器上什么都不写（表单提交到 ``allsave.php`` 才落库；第 0 步实测确认，
  待探路核对）。不删单、不用 MR.ERP 的取消 / 删除；
- **弹框**：页面的 alert / confirm 由子进程接管、不弹出，文字按配方规则判：被踢 / 登录被拒 / 拦下（期间、必填、存档出错）；
  认不出的交 ``helper``（能看文字的才用），判不了必须人工（``unknown_screen``）；
- **点了存档以后**结果不明（网络断、子进程没了、被踢回登录页——MR.ERP 的 ``checkuseruse.php`` 打出踢人脚本后**不停**，
  ``allsave.php`` 照样往下写库）：这张按「崩了」交回批次（``crash``），留在「正在录」，续录前由 ``book.find_saved`` 查存没存，
  查到就记单号、不重录；被踢的整批暂停、不重登；
- **读回不符**：不改已存的单，必须人工（``readback_mismatch``），汇报标红；
- 配方没核对过的单种（``verified: false``）默认回「不支持」，只有开发开关（``allow_unverified``）打开才录（假站点 / 探路）。
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from ..express_operator.engine import NeedHuman, Stopped
from ..express_operator.helper import NoHelper
from .book import MrerpBook, same_amount, same_text
from .draft import Draft
from .recipe import Form, Recipe
from .session import Kicked, MrerpSession, NetworkDown
from .web_desk import DeskGone, PageError

SUBMIT_GRACE_S = 1.5  # 点了存档后这么久页面还没开始加载、又弹了框：页面自己的检查拦下了，没提交


class Blocked(Exception):
    """页面按业务规则拦下 / 配方对不上：这张录不进去，放弃（不存）。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


class Engine:
    def __init__(self, session: MrerpSession, recipe: Recipe, book: MrerpBook, *, helper=None,
                 presence: Optional[Callable[[], bool]] = None, evidence: Optional[Callable[[str, str], None]] = None,
                 allow_unverified: bool = False, log: Callable[[str], None] = lambda _m: None):
        self.session, self.recipe, self.book = session, recipe, book
        self.helper = helper or NoHelper()
        self.presence, self.evidence, self.log = presence, evidence, log
        self.allow_unverified = allow_unverified
        self.form_open: Optional[str] = None
        self.rec: Dict = {}
        self._saving = False

    # ── 记录 ────────────────────────────────────────────────────────────
    def _event(self, kind: str, **data) -> None:
        self.rec["events"].append({"t": round(time.monotonic() - self._t0, 2), "kind": kind, **data})

    def supported(self, doc_type: str) -> bool:
        return self.recipe.supported(doc_type, allow_unverified=self.allow_unverified)

    @property
    def page(self):
        return self.session.page

    # ── 一张 ────────────────────────────────────────────────────────────
    def run(self, draft: Draft) -> Dict:
        """录一张。回 ``{"outcome": saved / need_human / blocked / crash, "reason", "docnum", "readback", ...}``。
        ``Stopped`` 原样往上抛（还没点存档的已离开表单，什么都没写）。"""
        self._t0 = time.monotonic()
        self._saving = False
        self.rec = {"doc_id": draft.doc_id, "doc_type": draft.doc_type, "events": [], "reports": [], "voided": [],
                    "placeholders": [], "outcome": "", "reason": "", "detail": "", "docnum": "", "readback": None,
                    "layers": {}, "shots_ai": 0, "shots_evidence": 0}
        form = self.recipe.form(draft.doc_type)
        if form is None or not self.supported(draft.doc_type):
            self.rec.update(outcome="need_human", reason="unsupported")
            return self._done()
        if not self.book.ready(draft.doc_id):
            # 开录前清单没取到（那时就连不上）：不往下录，交批次等恢复，续录前没清单 = 没存
            self.rec.update(outcome="crash", reason="network_wait", detail="no list baseline")
            return self._done()
        try:
            earlier = self.book.unsure_baseline(draft.doc_id)
            found = self.book.find_new(draft, earlier) if earlier else None
            if found:
                # 上次点了存档、结果不明就停了（被踢 / 网络断后整批停）：已经存上了，核对、记单号，不重录
                self._saving = True
                self._twice(found)
                self._event("found_saved", docnum=found["docnum"])
                self._readback(draft, found["id"], found["docnum"])
            else:
                record_id, docnum = self._enter(draft, form)
                self._readback(draft, record_id, docnum)
            self.book.settle(draft.doc_id)
        except Kicked as error:
            self.form_open = None
            if self._saving:  # 点了存档才被踢：存没存不知道，留「正在录」、本机记下，续录前先查
                self.book.mark_unsure(draft.doc_id)
                self.rec.update(outcome="crash", reason="account_in_use", detail=f"kicked after save: {error}"[:300])
            else:
                self.rec.update(outcome="need_human", reason="account_in_use", detail=f"kicked: {error}"[:300])
        except (NetworkDown, DeskGone) as error:
            self.form_open = None
            if self._saving:
                self.book.mark_unsure(draft.doc_id)
            self.rec.update(outcome="crash", reason="network_wait", detail=str(error)[:300])
        except Blocked as error:
            self.rec.update(outcome="need_human", reason=error.reason, detail=error.detail[:300])
            self.abandon(draft)
        except NeedHuman as error:
            if self._saving:  # 存上了却找不回 / 存成两张：本机记下，这张再来先查，不重录
                self.book.mark_unsure(draft.doc_id)
            self.rec.update(outcome="need_human", reason=error.reason, detail=error.detail[:300])
            self.abandon(draft)
        except PageError as error:
            if self._saving:
                self.book.mark_unsure(draft.doc_id)
                self.rec.update(outcome="crash", reason="network_wait", detail=f"after save: {error}"[:300])
            else:
                self.rec.update(outcome="need_human", reason="unknown_screen", detail=str(error)[:300])
                self.abandon(draft)
        except Stopped:
            if self._saving:
                self.book.mark_unsure(draft.doc_id)
            else:
                self.abandon(draft)
            raise
        return self._done()

    def _done(self) -> Dict:
        self.rec["seconds"] = round(time.monotonic() - self._t0, 1)
        return self.rec

    def abandon(self, draft: Draft) -> None:
        """放弃 = 不存、离开表单（打开这个菜单的列表）。被踢 / 页面没了就不动（关窗时一并丢掉）。"""
        if self.form_open is None or self.session.page is None or self.session.kicked:
            self.form_open = None
            return
        self.form_open = None
        try:
            self.session.goto(self.book.list_path(draft))
            self._event("abandoned")
        except Exception:  # noqa: BLE001 — 离不开也没关系：没存档就什么都没写，关窗时丢掉
            pass

    # ── 步骤 ────────────────────────────────────────────────────────────
    def _enter(self, draft: Draft, form: Form) -> tuple:
        spec = form.module_spec
        if self.session.where != (form.module, str(draft.menu)):
            self.book.open_list(draft)
        page = self.page
        before = page.state()["loads"]
        if page.read([self.recipe.list["add_id"]]).get(self.recipe.list["add_id"]) is None:
            raise Blocked("recipe_mismatch", "no Add button in the list (menu or permission)")
        page.click(self.recipe.list["add_id"])
        state = page.wait_load(before)
        self.session.where = ("", "")
        self.session.inspect(state)
        self.form_open = draft.doc_id
        self._dialogs("open")
        missing = [i for i, v in page.read(form.required_ids()).items() if v is None]
        if missing:
            raise Blocked("recipe_mismatch", "missing " + ",".join(missing[:8]))
        self._event("form", module=form.module)
        self._header(draft, form)
        self._lines(draft, form)
        if not same_amount(draft.discount or "0", "0"):
            self._fill(spec["discount_id"], f"{float(draft.discount):.2f}")
        receipt = spec.get("receipt") or {}
        if receipt.get("cash_id"):
            self._fill(receipt["cash_id"], f"{draft.totals()['net']:.2f}", check=False)
        self._totals(draft, form)
        number = page.read([spec["docnum"]["prefix_id"], spec["docnum"]["number_id"]])
        docnum = f"{number.get(spec['docnum']['prefix_id']) or ''}{number.get(spec['docnum']['number_id']) or ''}"
        return self._save(draft, form), docnum

    def _value(self, key: str, draft: Draft, form: Form):
        if key.startswith("defaults."):
            return draft.defaults.get(key.split(".", 1)[1], "")
        return {"docnum": draft.docnum, "date": draft.date_th, "cash": form.cash, "vat_rate": draft.vat_rate,
                "party_code": draft.party_code, "credit_days": draft.credit_days, "ref_no": draft.ref_no,
                "ref_date": draft.ref_date_th if (draft.ref_no or draft.ref_date) else draft.date_th,
                "delivery_date": draft.date_th}[key]

    def _header(self, draft: Draft, form: Form) -> None:
        page = self.page
        for field in form.header:
            value, kind, fid = self._value(field["key"], draft, form), field["kind"], field["id"]
            if field.get("when") == "editable":
                attrs = page.attrs([fid]).get(fid) or {}
                if attrs.get("readonly") or attrs.get("disabled"):
                    continue  # 这个菜单开了自动编号：单号以页面给的为准
                if not value:
                    raise Blocked("recipe_mismatch", f"{fid} needs a document number")
            if kind == "check":
                page.check(fid, bool(value))
            elif not value:
                continue  # 草稿没给：用页面带出的默认（页面自己的必填检查会拦空的）
            elif kind == "pick":
                self._pick(fid, str(value), settle=field.get("settle", ""))
            else:
                self._fill(fid, str(value), focus=kind != "date")
            self._dialogs(fid)
        self._event("header")

    def _lines(self, draft: Draft, form: Form) -> None:
        page, spec = self.page, form.lines
        for n, line in enumerate(draft.lines, 1):
            code_id = form.line_id("code", n)
            if page.read([code_id]).get(code_id) is None:
                page.click(spec["add_button"])
                if not page.wait_for(lambda: page.read([code_id]).get(code_id) is not None, 10):
                    raise Blocked("recipe_mismatch", f"row {n} did not appear")
            if not line.code:
                raise Blocked("item_missing", f"line {n} has no item code")
            price_id = form.line_id("price", n)
            before = page.read([price_id]).get(price_id)
            self._pick(code_id, line.code)
            if spec.get("price_settle"):
                # 选完商品页面自己去取标准成本价、回来覆盖单价：等它回来（或到点）再填，不然我们填的会被盖掉
                page.wait_for(lambda: page.read([price_id]).get(price_id) != before, self.recipe.settle_s)
            store = line.store or draft.defaults.get("store", "")
            if store and spec.get("store"):
                self._pick(form.line_id("store", n), store)
            self._fill(form.line_id("qty", n), _plain(line.qty))
            self._fill(price_id, _plain(line.price))
            total_id = form.line_id("total", n)
            got = page.read([total_id]).get(total_id)
            if not same_amount(got, line.amount):
                raise Blocked("unknown_screen", f"line {n} total {got} != {line.amount}")
            self._dialogs(code_id)
        self._event("lines", n=len(draft.lines))

    def _totals(self, draft: Draft, form: Form) -> None:
        seen = self.page.read(list(form.totals.values()))
        want = draft.totals()
        wrong = {k: seen.get(v) for k, v in form.totals.items() if not same_amount(seen.get(v), want[k])}
        if wrong:
            raise Blocked("unknown_screen", "totals " + ", ".join(f"{k} {v} != {want[k]}" for k, v in wrong.items()))
        self._event("totals", net=str(want["net"]))

    def _save(self, draft: Draft, form: Form) -> str:
        """点「Save」：页面自己的 checknull 先查（拦下 = alert、不提交，什么都没写）；提交后 allsave.php 写库、显示「Save Success」、
        一秒后回列表。提交以后结果不明的一律按「崩了」交回批次（续录前先查）。回这张的查看页编号。"""
        page, spec = self.page, form.module_spec
        self._dialogs("before_save")
        before = page.state()["loads"]
        page.click(spec["save_id"])
        clicked = time.monotonic()
        self._saving = True
        self._event("save_clicked")

        def outcome():
            st = page.state()
            if st["loads"] > before and not st["loading"]:
                return ("submitted", st)
            if st["loading"] or st["loads"] > before:
                return None  # 已提交、还在加载
            if page.dialogs and time.monotonic() - clicked >= SUBMIT_GRACE_S:
                return ("refused", st)
            return None

        found = page.wait_for(outcome, self.recipe.save_s)
        if not found:
            raise NetworkDown("no answer after save")
        kind, st = found
        if kind == "refused":
            # 没提交：页面自己的检查拦下了（必填、期间……）—— 什么都没写
            self._saving = False
            self._dialogs("save")
            raise Blocked("unknown_screen", "save did not submit")
        # 已提交：被踢（checkuseruse 打出踢人脚本后不停，allsave 照样写库）/ 网络断 → 结果不明，交批次续录前查
        self.session.inspect(st)
        body = page.text("", limit=4000)
        for dialog in page.take_dialogs():
            text = str(dialog.get("text") or "")
            rule = self.recipe.match_dialog(text)
            if rule and rule.get("outcome") == "blocked":
                # allsave.php 写库出错：autocommit 关着、出错即回滚 → 没存上（待探路核对）
                self._saving = False
                self.form_open = None
                raise Blocked(rule.get("reason", "unknown_screen"), text[:200])
            raise NetworkDown(f"dialog after save: {text[:120]}")
        now = str(page.state().get("url") or "")
        if self.recipe.saved_mark and self.recipe.saved_mark not in body and "/allview.php" not in now:
            # 既没看到「Save Success」、也没回到列表（allsave 一秒后自己跳回列表，机器慢时读到的可能已是列表）：结果不明
            raise NetworkDown("save result not shown")
        self._event("saved_page")
        self.form_open = None
        # 存好了：在列表里找回这一张（开录前清单之后新出现、核对得上的）
        found = self.book.find_new(draft, self.book.baseline(draft.doc_id))
        if not found:
            raise NeedHuman("readback_mismatch", "saved, but not found in the MR.ERP list")
        self._twice(found)
        return found["id"]

    def _twice(self, found: Dict) -> None:
        """同一张存成了不止一张（存档请求断在半路、浏览器自动重发）：不删不改，交人，单号都列出来。"""
        if found.get("others"):
            self.rec["docnum"] = found["docnum"]
            raise NeedHuman("readback_mismatch", "saved more than once: " + ", ".join([found["docnum"], *found["others"]]))

    def _readback(self, draft: Draft, record_id: str, typed: str) -> None:
        result = self.book.verify(draft, record_id)
        self.rec["readback"] = result
        docnum = result["docnum"] or typed
        if not result["ok"]:
            self.rec.update(outcome="need_human", reason="readback_mismatch", docnum=docnum,
                            detail="; ".join(result["diffs"])[:300])
        else:
            self.rec.update(outcome="saved", docnum=docnum)
        self._saving = False
        if self.evidence:
            png = self.page.screenshot()
            if png:
                self.evidence(draft.doc_id, png)
                self.rec["shots_evidence"] += 1
        self._event("verified", ok=result["ok"], docnum=docnum)

    # ── 小动作 ──────────────────────────────────────────────────────────
    def _fill(self, fid: str, value: str, *, focus: bool = True, check: bool = True) -> None:
        got = self.page.fill(fid, value, focus=focus)
        self._dialogs(fid)
        if check:
            seen = self.page.read([fid]).get(fid)
            if not (same_amount(seen, value) or same_text(seen, value)):
                raise Blocked("unknown_screen", f"{fid} reads {seen!r} after filling")
        return got

    def _pick(self, fid: str, code: str, *, settle: str = "") -> None:
        page = self.page
        before = page.read([settle]).get(settle) if settle else None
        try:
            page.pick(fid, code)
        except PageError as error:
            missing = "not found" in str(error)
            raise Blocked("party_missing" if missing and fid in self._party_ids() else
                          ("item_missing" if missing and fid.startswith("txtcode") else "recipe_mismatch"),
                          str(error)) from None
        if settle:
            page.wait_for(lambda: page.read([settle]).get(settle) != before, self.recipe.settle_s)
        seen = page.read([fid]).get(fid)
        if not same_text(seen, code):
            raise Blocked("unknown_screen", f"{fid} reads {seen!r} after choosing {code!r}")

    def _party_ids(self) -> set:
        return {f.module_spec["party_id"] for f in self.recipe.forms.values()}

    def _dialogs(self, stage: str) -> None:
        for dialog in self.page.take_dialogs():
            text = str(dialog.get("text") or "")
            rule = self.recipe.match_dialog(text)
            if rule is None:
                judged = self._ask_helper(text, stage)
                if judged:
                    continue
                raise Blocked("unknown_screen", f"{dialog.get('kind')}: {text[:200]}")
            if rule["outcome"] == "kicked":
                self.session.kicked, self.session.ready = True, False
                raise Kicked(text[:200])
            if rule["outcome"] == "login_failed":
                raise NeedHuman("login_failed", text[:200])
            raise Blocked(rule.get("reason", "unknown_screen"), text[:200])

    def _ask_helper(self, text: str, stage: str) -> bool:
        """认不出的弹框：能看文字的助手（站点经 CC 机器通道接，第 3 步）判得出「无害、继续」才继续；没接就必须人工。"""
        judge = getattr(self.helper, "judge_text", None)
        if not callable(judge):
            return False
        try:
            verdict = judge(text, stage)
        except Exception:  # noqa: BLE001
            return False
        if verdict == "continue":
            self.rec["reports"].append({"reason": "ai_resolved", "text": text[:120]})
            return True
        return False

    # ── 探路 ────────────────────────────────────────────────────────────
    def probe(self, draft_like: Draft) -> Dict:
        """新账套 / 新配方第一次先探路：打开列表 → Add → 读新增页上配方要用的格子在不在 → 离开（不存）。
        回 ``{"ok", "missing": [...]}``；对不上的这种单暂停（``recipe_mismatch``），不拿旧配方硬录。"""
        form = self.recipe.form(draft_like.doc_type)
        if form is None or not self.supported(draft_like.doc_type):
            return {"ok": True, "skipped": True}  # 不支持的由 run 回「不支持」，不开页面
        self.book.open_list(draft_like)
        page = self.page
        before = page.state()["loads"]
        if page.read([self.recipe.list["add_id"]]).get(self.recipe.list["add_id"]) is None:
            return {"ok": False, "missing": [self.recipe.list["add_id"]]}
        page.click(self.recipe.list["add_id"])
        state = page.wait_load(before)
        self.session.where = ("", "")
        self.session.inspect(state)
        self.form_open = draft_like.doc_id
        try:
            missing = [i for i, v in page.read(form.required_ids()).items() if v is None]
        finally:
            self.abandon(draft_like)
            page.take_dialogs()
        return {"ok": not missing, "missing": missing}


def _plain(value) -> str:
    text = str(value)
    return text.rstrip("0").rstrip(".") if "." in text else text
