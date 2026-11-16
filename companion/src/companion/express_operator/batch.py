# -*- coding: utf-8 -*-
"""一批单：进度落盘、暂停 / 停止 / 跳过、断点续录不重录、录完正常退出 Express。

- 一批只录一家公司的一本账套；整批持有这本账套的写者锁（``PEARNLY.LCK``，与直写同一把），期间直写单按「等账套」排队；
- 每张单开录前记下各表行数并落盘（状态「正在录」）；崩了 / 断电 / CC 重启后续录：先读这之后新增的行，找到同对方同日同金额
  的单就当已存（不重录），只找到金额 0 的空单头就交给人作废（不自动改已存的单），什么都没有才重录；同一张崩两次挂起；
- 暂停：录完当前这张再停；停止：当前这张存完或放弃（放弃即作废）后停；跳过：还没开始的单直接标跳过；
- 服务器连不上（-70）：什么都不点，每分钟只读探一次账套文件夹，10 分钟内恢复就重开 Express 续录，否则整批停；
- 必须人工的单挂起，其余照录；整批停的原因见 ``contract.BATCH_STOPPERS``；
- 停下以后站点按原批次号交来的新写账件（人在任务里作答、点重试、整批继续）是新指令：已停的这一批重新打开，这张重录（已存的
  回原单号、不重录；停下时正在录的先读账套判断存没存）；同一个写账件再派来（租约过期后派回这台）不是新指令，照上次的结果；
- 批次文件只存在本机（``%APPDATA%\\Pearnly\\operator``），删批次连同存档截图一起真删。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import book as B
from . import contract as K
from .draft import Draft
from .engine import Crash, NeedHuman, Stopped
from .helper import BudgetStop

EVENTS_SHOWN = 12


@dataclass
class DocState:
    doc_id: str
    doc_type: str
    draft: Dict
    status: str = K.PENDING
    docnum: str = ""
    voided: List[str] = field(default_factory=list)
    placeholders: List[str] = field(default_factory=list)  # 没存成时 Express 留下的金额 0 空单头，要人作废
    reason: str = ""
    detail: str = ""
    reports: List[Dict] = field(default_factory=list)
    issues: List[Dict] = field(default_factory=list)
    counts_before: Dict[str, int] = field(default_factory=dict)
    crashes: int = 0
    seconds: float = 0.0
    readback_ok: Optional[bool] = None
    # 最后交来这一张的写账件号（``task_journal.key_of``：同一件写账每次派都一样）。旧批次文件没有这一项，按空读
    task: str = ""
    # 停下时这张正在录、结果不明：再交来时先读账套判断存没存（同续录），不当新单重录。旧批次文件没有这一项，按否读
    recheck: bool = False
    # 原因 menu_choice 时子菜单里的候选菜单文字（回执 meta 带给站点问人）。旧批次文件没有这一项，按空读
    menu_options: List[str] = field(default_factory=list)
    # 转人工那一刻的截图（本机路径，回执 meta.evidence；1.12.15 起不进说明）。旧批次文件没有这一项，按空读
    evidence: str = ""
    # 保存途中停住、Express 原样留着等人答框（``engine._after_save_trouble``）：收尾不退出 Express。旧批次文件没有，按否读
    held: bool = False
    # 账套里留下的半截单 ``{"docnum", "missing"}``（回执 meta.partial）。旧批次文件没有，按空读
    partial: Dict = field(default_factory=dict)
    # 原因 dialog_choice：配方外业务选择框的样子 ``engine.dialog_info``（回执 meta.dialog，对话里出卡）。旧批次文件没有，按空读
    dialog: Dict = field(default_factory=dict)
    # 留着等人的这张按「未完成」回过的回执号与成员档案（``Host.note_late``）：人答完框、单在账套里存完后，用同一个回执号补报
    # 「已写入 + 原单号」（服务端只收这一种迟到终态）。补报成功或放手时没存完就清空。旧批次文件没有，按空读
    late_ack: str = ""
    late_profile: str = ""


@dataclass
class BatchState:
    id: str
    company: str
    account_set: str
    account_dir: str
    docs: List[DocState]
    status: str = K.BATCH_READY
    reason: str = ""
    start_at: float = 0.0  # 0 = 现在开始；否则是指定时刻（如下班后）
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    current: str = ""
    counts_start: Dict[str, int] = field(default_factory=dict)  # 批次开录前账套各表行数（作废闸：只认之后新建的单）
    detail: str = ""  # 整批停下时看到的画面文字（host 记）
    site: str = ""  # 站点上的公司编号（载荷 operator.batch.company）：CC 重启后页面拿它向站点核这一批
    held: str = ""  # Express 留着等人答框的那一张（单据号）：这期间不退出 Express、这本账套的别的单等着（account_busy）

    @classmethod
    def new(cls, company: str, account_set: str, account_dir: str, drafts: List[Draft], *, start_at: float = 0.0):
        docs = [DocState(d.doc_id, d.doc_type, asdict(d)) for d in drafts]
        return cls(uuid.uuid4().hex[:12], company, account_set, account_dir, docs, start_at=start_at)

    @classmethod
    def from_json(cls, data: Dict) -> "BatchState":
        docs = [DocState(**d) for d in data.pop("docs")]
        return cls(docs=docs, **data)

    def doc(self, doc_id: str) -> Optional[DocState]:
        return next((d for d in self.docs if d.doc_id == doc_id), None)


def snapshot_of(s: "BatchState", *, now: float, events=(), pauses=(), evidence_state=lambda _doc_id: "none") -> Dict:
    """批次给 CC 窗口看的样子（录单中的批次与 CC 重启后从本机批次文件读回的批次同一个形状）。"""
    by: Dict[str, int] = {}
    for d in s.docs:
        by[d.status] = by.get(d.status, 0) + 1
    done = [d for d in s.docs if d.status in K.DONE_STATES or d.status == K.WAITING]
    avg = (sum(d.seconds for d in done) / len(done)) if done else 0.0
    left = len([d for d in s.docs if d.status in (K.PENDING, K.ENTERING)])
    return {"id": s.id, "status": s.status, "reason": s.reason, "company": s.company, "site": s.site,
            "account_set": s.account_set, "held": s.held,
            "total": len(s.docs), "counts": by, "current": s.current,
            "index": next((i + 1 for i, d in enumerate(s.docs) if d.doc_id == s.current), 0),
            "events": list(events), "pauses": list(pauses), "started": s.started, "finished": s.finished,
            "created": s.created, "elapsed": round((s.finished or now) - s.started, 1) if s.started else 0.0,
            "remaining_estimate": round(avg * left, 1),
            "docs": [{"doc_id": d.doc_id, "doc_type": d.doc_type, "status": d.status, "docnum": d.docnum,
                      "voided": d.voided, "placeholders": d.placeholders, "reason": d.reason,
                      "held": d.held, "partial": d.partial,
                      "reports": d.reports, "issues": d.issues,
                      "seconds": d.seconds, "evidence": evidence_state(d.doc_id)} for d in s.docs]}


class BatchStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, batch_id: str) -> Path:
        return self.root / f"{batch_id}.json"

    def save(self, state: BatchState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path(state.id).with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(state), ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path(state.id))

    def load(self, batch_id: str) -> BatchState:
        return BatchState.from_json(json.loads(self.path(batch_id).read_text(encoding="utf-8")))

    def ids(self) -> List[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))

    def recent(self, limit: int) -> List[str]:
        """最近改过的批次（新的在前）。"""
        files = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.stem for p in files[:limit]]

    def delete(self, batch_id: str) -> None:
        self.path(batch_id).unlink(missing_ok=True)


def _write_lock(account_dir: str):
    from ..account_write_lock import held

    return held(account_dir)


def release(state: "BatchState") -> None:
    """放手：这一批不再留着 Express 等人（held 标记清掉）。"""
    for doc in state.docs:
        doc.held = False
    state.held = ""


class Held(Exception):
    """这本账套的 Express 正留着等人答框（``BatchState.held``）：这一张先不录，按「等账套」放回，人处理完再录。"""


class Runner:
    def __init__(self, state: BatchState, store: BatchStore, engine, session, book: B.Book, *,
                 probe: Optional[Callable[[str], Dict]] = None,
                 lock: Callable[[str], object] = _write_lock,
                 reachable: Callable[[], bool] = lambda: True,
                 on_change: Callable[[Dict], None] = lambda _s: None,
                 housekeeping: Callable[[], None] = lambda: None,
                 evidence_state: Callable[[str], str] = lambda _doc_id: "none",
                 pauses: Callable[[], List[Dict]] = lambda: [],
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time,
                 net_wait_s: float = 600, net_poll_s: float = 60,
                 draft_type: Callable[..., object] = Draft,
                 before: Optional[Callable[[object], Dict]] = None,
                 find_saved: Optional[Callable[[object, Dict], Dict]] = None):
        """``draft_type`` / ``before`` / ``find_saved``：别的执行目标（ADR-026，MR.ERP）换上自己的草稿形状、开录前记录、
        续录前查存没存；不传就是 Express 原来的（草稿 ``Draft``、账套各表行数、读之后新增的行）。"""
        self.state, self.store, self.engine, self.session, self.book = state, store, engine, session, book
        self.draft_type, self.before, self.find_saved = draft_type, before, find_saved
        self.probe, self.lock, self.reachable = probe, lock, reachable
        self.on_change, self.sleep, self.clock = on_change, sleep, clock
        self.housekeeping, self.evidence_state = housekeeping, evidence_state  # 截图到期清理（开始前、结束后各一次）
        self.pauses = pauses  # 每次「有人在用电脑」暂停的证据（presence.evidence），汇报里列出
        self.net_wait_s, self.net_poll_s = net_wait_s, net_poll_s
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._skip: set = set()
        self._paused_by: str = ""
        self._probed: set = set()
        # 下一张先开 / 接管 Express 并核公司：批中途为建主档退出过（close_express），或 CC 重启后接着同一批（批次已开始）
        self._reopen = bool(state.started)
        if hasattr(engine, "may_void"):
            engine.may_void = self.may_void  # 作废第二道闸由批次把关
        if hasattr(engine, "batch_id"):
            engine.batch_id = state.id  # 引擎写日志时带上批次号

    # ── 控制（CC 窗口经桥调用，任意线程）──────────────────────────────────────
    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def stop(self) -> None:
        self._stop.set()

    def skip(self, doc_id: str) -> None:
        """跳过一张：还没轮到的，轮到时不录；已经停在「需要处理」的（Express 里那张已经放弃、两道作废闸处理过），当场标成跳过，
        排在后面的单照常接着录（10-27 实机：原来点了没反应）。"""
        self._skip.add(doc_id)
        found = next((d for d in self.state.docs if d.doc_id == doc_id), None)
        if found is not None and found.status == K.WAITING:
            found.status = K.SKIPPED
            self._set()

    def stop_now(self) -> bool:
        """停止：这时没有单正在 Express 里录（``current`` 空）就当场停——没轮到的单标成停止跳过、批次停下，回 True 让调用方退出
        Express；正在录的那张由录单线程在这一张收尾时停（占了号的走作废两道闸）。"""
        self._stop.set()
        if self.state.current:
            return False
        for d in self.state.docs:
            if d.status == K.PENDING:
                d.status, d.reason = K.SKIPPED, "stopped"
        self._set(status=K.BATCH_STOPPED, reason="", current="")
        return True

    def stop_requested(self) -> bool:
        return self._stop.is_set()

    # presence 的回调：锁屏 / 有人在用时整批显示「已暂停」，原因带上
    def may_void(self, docnum: str) -> bool:
        """作废的第二道闸：本批次开录后账套里才新建的单号，且不是本批次已存好的单。读不了账套就不许作废。"""
        if not docnum or any(d.docnum == docnum and d.status == K.SAVED for d in self.state.docs):
            return False
        if not self.state.counts_start:
            return False
        try:
            rows = self.book.rows_since(self.state.counts_start, ["APTRN", "ARTRN"])
        except Exception:  # noqa: BLE001
            return False
        return any(str(r.get("DOCNUM") or "").strip() == docnum and not r.get("_del")
                   for table in rows.values() for r in table)

    def presence_paused(self, reason: str) -> None:
        self._paused_by = reason
        self._set(status=K.BATCH_PAUSED, reason=reason)

    def presence_resumed(self) -> None:
        self._paused_by = ""
        self._set(status=K.BATCH_RUNNING, reason="")

    # ── 状态 ────────────────────────────────────────────────────────────────
    def _set(self, **changes) -> None:
        for key, value in changes.items():
            setattr(self.state, key, value)
        self.store.save(self.state)
        self.on_change(self.snapshot())

    def snapshot(self) -> Dict:
        s = self.state
        events = self.engine.rec.get("events", [])[-EVENTS_SHOWN:] if s.current and self.engine.rec else []
        return snapshot_of(s, now=self.clock(), events=events, pauses=list(self.pauses()),
                           evidence_state=self.evidence_state)

    # ── 跑 ──────────────────────────────────────────────────────────────────
    def run(self) -> BatchState:
        s = self.state
        while s.start_at and self.clock() < s.start_at:
            if self._stop.is_set():
                self._set(status=K.BATCH_STOPPED, reason="")
                return s
            if s.reason != "waiting_start":
                self._set(reason="waiting_start")
            self.sleep(min(30.0, s.start_at - self.clock()))
        self._tidy()
        try:
            guard = self.lock(s.account_dir) if self.lock else nullcontext()
            with guard:
                if not s.counts_start:
                    s.counts_start = self.book.counts()
                self._set(status=K.BATCH_RUNNING, reason="", started=s.started or self.clock())
                try:
                    self.session.ensure_open()
                    self._probe()
                    self._loop()
                finally:
                    self._close()
                    self._tidy()
        except NeedHuman as e:
            self._set(status=K.BATCH_STOPPED, reason=e.reason)
        except BudgetStop:
            self._set(status=K.BATCH_STOPPED, reason="budget")
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "Busy":  # 别的电脑正在写这本账套：按「等账套」，过一会儿再开始
                self._set(status=K.BATCH_READY, reason="account_busy")
                return s
            raise
        return s

    # ── 一件一件来（写账队列每次交一张，代录宿主 ``host`` 用）─────────────────────
    def add(self, draft: Draft, task: str = "") -> DocState:
        """把交来的这一张放进批次。``task``：交来它的写账件号（同一件写账每次派都一样）。

        - 同一个写账件再派来（租约过期、CC 重启后派回这台）：不是新指令，原样不动，照上次的结果回执；
        - 换了写账件号（站点按原批次号交来的新指令：人在任务里作答、点重试、整批继续，10-30 实机）：已存的不重录、回原单号；
          等人 / 跳过的换上新草稿重录（作废过的单号留着进汇报；没写成的才会是这两种，重录不会重复入账）；停下时正在录的
          （结果不明）先读账套判断存没存；这一批已经停了就重新打开（第一张照常重开 Express、核登录与公司）。"""
        d = self.state.doc(draft.doc_id)
        if d is None:
            d = DocState(draft.doc_id, draft.doc_type, asdict(draft), task=task)
            self.state.docs.append(d)
            self._set()
            return d
        if task and task == d.task:
            return d
        d.task = task
        if d.held and self.state.held == d.doc_id and self.still_held():
            # 还留着等人答框：原样回上次的结论（带单号），等人处理完再说（人答完存好了的，still_held 已记成已写入）
            self._set()
            return d
        if d.status == K.SAVED:
            self._set()
            return d
        if d.status in (K.WAITING, K.SKIPPED, K.VOIDED):
            d.draft, d.doc_type, d.reason, d.detail, d.menu_options = asdict(draft), draft.doc_type, "", "", []
            if (d.recheck or d.placeholders or d.partial) and d.counts_before:
                # 账套里可能已有这张的行（保存后出事、留了半截单 / 空单头）：开录前行数留着，先读这之后新增的行，
                # 已完整回单号、半截带单号交人，确认一行都没有才从头录（11-13 设计要点 3：半截单再交不出第二张）
                d.status, d.recheck = K.ENTERING, False
            else:
                d.status = K.PENDING
                d.counts_before, d.issues, d.readback_ok = {}, [], None
        self._skip.discard(d.doc_id)
        self._reopen_batch()
        self._set()
        return d

    def _reopen_batch(self) -> None:
        """已停的这一批重新打开：清停止 / 暂停、原因与画面文字，下一张先开 Express、核登录与公司。"""
        s = self.state
        if s.status != K.BATCH_STOPPED:
            return
        self._stop.clear()
        self._pause.clear()
        self._reopen = self._reopen or bool(s.started)
        s.status, s.reason, s.detail, s.current, s.finished = K.BATCH_READY, "", "", "", 0.0

    def park(self, doc_id: str) -> None:
        """整批停下时正在录的这张（结果不明，还是「正在录」）：人点的停止记停止跳过，整批停的原因记等人；都标上「再交来时先读账套
        判断存没存」。不是正在录的不动。"""
        s, d = self.state, self.state.doc(doc_id)
        if d is None or d.status != K.ENTERING:
            return
        if s.reason:
            d.status, d.reason = K.WAITING, s.reason
        else:
            d.status, d.reason = K.SKIPPED, "stopped"
        d.recheck = True
        self._set(current="")

    def halt(self, doc_id: str, reason: str, detail: str = "", *, hold: bool = False, evidence: str = "") -> None:
        """宿主收尾整批停：批次记停止 + 原因 + 画面文字并落盘（不留 running）；正在录的这张按 ``park``；``hold``：还没开始的
        这张也记等人（同原因，意外发生在开 Express / 探路时）。"""
        s = self.state
        s.status, s.reason, s.detail = K.BATCH_STOPPED, reason, str(detail or "")[:K.DETAIL_MAX]
        self.park(doc_id)
        d = s.doc(doc_id)
        if hold and d is not None and d.status == K.PENDING:
            d.status, d.reason, d.detail = K.WAITING, reason, s.detail
        if evidence and d is not None:
            d.evidence = evidence
        self._set(current="")

    def run_one(self, doc_id: str) -> DocState:
        """录批次里的一张（写账线里调；锁由写账线拿着）。第一次先开 Express、核公司；每种单第一次先探路。
        整批停的原因（登录、公司、授权名额、服务器、预算）抛 ``NeedHuman``，由调用方回执。"""
        s, d = self.state, self.state.doc(doc_id)
        if d.status in K.DONE_STATES or d.status == K.WAITING:
            return d
        if self.still_held():
            raise Held(s.held)
        if self._stop.is_set() or s.status == K.BATCH_STOPPED:
            if d.status == K.ENTERING:  # 停下时正在录的那张（同一件再派来）：结果不明，不当跳过
                self.park(doc_id)
            else:
                d.status, d.reason = K.SKIPPED, "stopped"  # 人点了停止：剩下的单不录，按「没写、退回」回执
            self._set(status=K.BATCH_STOPPED, current="")
            return d
        if doc_id in self._skip and d.status == K.PENDING:
            d.status = K.SKIPPED
            self._set()
            return d
        while self._pause.is_set() and not self._stop.is_set():
            if s.status != K.BATCH_PAUSED:
                self._set(status=K.BATCH_PAUSED, reason="")
            self.sleep(1)
        try:
            self._prepare(d)
        except NeedHuman as e:
            if e.reason == "menu_choice":  # 探路时子菜单定不下来：只停这一张（候选带回站点），下一张同种单再探
                self._probed.discard(d.doc_type)
                d.status, d.reason, d.detail = K.WAITING, "menu_choice", e.detail[:K.DETAIL_MAX]
                d.menu_options = list(getattr(e, "options", []) or [])
                self._set(current="")
                return d
            raise
        except BudgetStop:
            raise NeedHuman("budget", "probe")
        except Stopped:
            d.status, d.reason = K.SKIPPED, "stopped"
            self._set(status=K.BATCH_STOPPED, reason="", current="")
            return d
        except Exception as e:  # noqa: BLE001 — 开 Express / 探路时没接住的意外：同 _one 的口径收尾，不留 running
            shot = self._shot(d.doc_id)
            text = f"{type(e).__name__}: {e}"
            d.status, d.reason, d.detail = K.WAITING, "unknown_screen", text[:K.DETAIL_MAX]
            d.evidence = shot or d.evidence
            if hasattr(self.engine, "form_open"):
                self.engine.form_open = None
            self._set(status=K.BATCH_STOPPED, reason="unknown_screen", current="", detail=d.detail)
            return d
        if d.status == K.WAITING:
            return d
        if not self._one(d) and d.status == K.ENTERING:
            if s.reason in K.BATCH_STOPPERS:
                raise NeedHuman(s.reason, "")  # 宿主记下原因与画面文字后同样 park
            self.park(doc_id)  # 停在半路（人点了停止、重开时要人选公司）：再交来时先读账套判断存没存
        return d

    def _prepare(self, d: DocState) -> None:
        """第一次先开 Express、核公司；每种单第一次先探路（菜单开表单、读标签）。异常由 ``run_one`` 收尾。"""
        s = self.state
        if not s.started:
            self._tidy()
            s.counts_start = s.counts_start or self.book.counts()
            self._set(status=K.BATCH_RUNNING, reason="", started=self.clock())
            self.session.ensure_open()
        else:
            if s.status != K.BATCH_RUNNING:
                self._set(status=K.BATCH_RUNNING, reason="")
            if self._reopen:
                self._reopen = False
                self.session.ensure_open()
        if self.probe and d.doc_type not in self._probed:
            self._probed.add(d.doc_type)
            self._hint(d)
            try:
                result = self.probe(d.doc_type)
            except NeedHuman as e:
                if e.reason == "unknown_screen":  # 探路时 AI 也接不住：截一张当时的画面，路径走 meta.evidence（不进说明）
                    shot = self._shot(d.doc_id)
                    if shot:
                        d.evidence = shot
                        e.evidence = shot
                raise
            if result is not None and not result.get("ok", True):
                d.status, d.reason, d.detail = K.WAITING, "recipe_mismatch", ",".join(result.get("missing", []))
                self._set()

    def _hint(self, d: DocState) -> None:
        """探路时没有草稿单：把这一张的菜单选择（站点记住的菜单文字、付款方式）交给引擎，子菜单多候选时照它选。"""
        if hasattr(self.engine, "menu_hint"):
            draft = d.draft or {}
            self.engine.menu_hint = (str(draft.get("form_choice") or ""), str(draft.get("pay_route") or ""))

    def _shot(self, doc_id: str) -> str:
        shot = getattr(self.engine, "evidence_shot", None)
        try:
            return shot(doc_id) if shot else ""
        except Exception:  # noqa: BLE001
            return ""

    def answer(self, doc_id: str, digest: str, button: str) -> Dict:
        """对话里答了留着的框（1.12.23 ④）：只认这一批正留着、还在等人的那一张；点了以后由 ``still_held`` 读账套判存完没有。"""
        s = self.state
        d = s.doc(doc_id) if s.held == doc_id else None
        if d is None or d.status != K.WAITING or not hasattr(self.engine, "answer_held"):
            return {"state": "not_held"}
        return self.engine.answer_held(d.doc_type, digest, button)

    def still_held(self) -> bool:
        """Express 还留着等人答框吗（只读，不点）。认不出的框还开着 → 真（不读账套：框没答完，单不会存完）。框没了 / Express 没了
        → 读一次账套：那张存完了记已写入（带原单号，等补报）；没存完放手，那张仍等人（再交来先读账套），不补报。"""
        s = self.state
        if not s.held:
            return False
        d = s.doc(s.held)
        opened = self.engine.open_unknown() if hasattr(self.engine, "open_unknown") else None
        if opened and d is not None and d.status == K.WAITING:
            return True
        if d is not None and d.status == K.WAITING and d.counts_before:
            try:
                draft = self.draft_type(**d.draft)
                found = (self.find_saved(draft, d.counts_before) if self.find_saved
                         else B.state_of(draft, self.book, d.counts_before))
            except Exception:  # noqa: BLE001 — 读不了：当没存完放手，再交来照样先读账套
                found = {}
            if found.get("docnum"):
                d.status, d.docnum, d.reason, d.detail, d.partial = K.SAVED, found["docnum"], "", "", {}
            else:
                d.late_ack, d.late_profile = "", ""
        release(s)
        self._set()
        if opened is not None and s.status in (K.BATCH_FINISHED, K.BATCH_STOPPED):
            # 这一批早已收尾（当时 Express 留着等人没退出）：现在框没了，正常退出 Express，释放授权名额（11-14 测试机2）
            self._close()
        return False

    def close_express(self) -> None:
        """批中途要建主档：正常退出 Express（开过才退），下一张从头开、核公司，单据窗口也从菜单重开（Express 重新读主档）。"""
        if self.state.started and not self._reopen:
            self._close()
            self.engine.form_open = None
            self._reopen = True

    def recover_orphan(self) -> None:
        """上次 CC 留下的 Express（宿主按本机记下的进程号认出、已绑定）没有单再来接着录时收拾掉（主会话 10-27）：还停在登录窗、
        公司列表的按取消关掉（什么都没写）；进了公司、正有一张在录的按放弃走作废两道保护（批次文件里有这张的草稿、开录前行数与
        批次开录前行数），再正常退出。这张记「CC 重启时中断」等人处理。"""
        s = self.state
        stage = self.session.stage()
        if stage in ("login", "company"):
            self.session.leave_unopened()
            return
        if stage == "work_date":  # 刚进公司、还在工作日期框：按确定（同开公司那一步），下面按主窗口收
            self.session.desk.act([{"do": "dialog_button", "text": "ตกลง"}], windows=False)
            self.sleep(2)
        d = next((d for d in s.docs if d.status == K.ENTERING), None)
        if d is not None:
            if self.engine.in_edit():
                self.engine.begin(Draft(**d.draft))
                self.engine._before = d.counts_before or None
                d.voided += self.engine.abandon()
                if self.engine.rec.get("void_refused"):
                    d.placeholders.append(self.engine.rec["void_refused"])
            d.status, d.reason = K.WAITING, "cc_restarted"
            self._set(current="")
        self.session.attached = True
        self.session.close()

    def finish(self) -> None:
        """批次最后一张录完（或停止）：正常退出 Express、清过期截图。Express 留着等人答框（``held``）时不退出。"""
        self._close()
        self._tidy()
        if self.state.status not in (K.BATCH_STOPPED,):
            self._set(status=K.BATCH_FINISHED, reason="", current="", finished=self.clock())

    def _tidy(self) -> None:
        try:
            self.housekeeping()
        except Exception:  # noqa: BLE001 — 清理失败不影响录单，下次再清
            pass

    def _close(self) -> None:
        if self.state.held:  # 存档停在人要答的框上：退出 Express 就把单断在半截（11-13 IV0000045），留给人
            self.engine.form_open = None
            return
        try:
            self.session.close()
        except Exception:  # noqa: BLE001 — 退不掉不掩盖批次结果；Express 留着，下次开录前会核对公司
            pass
        # 退出过 Express：这一批再有单来（作答、重试、整批继续）先重开、核公司，单据窗口也从菜单重开
        self._reopen = bool(self.state.started)
        self.engine.form_open = None

    def _probe(self) -> None:
        if not self.probe:
            return
        for doc_type in dict.fromkeys(d.doc_type for d in self.state.docs if d.status == K.PENDING):
            self._hint(next(d for d in self.state.docs if d.doc_type == doc_type and d.status == K.PENDING))
            try:
                result = self.probe(doc_type)
            except NeedHuman as e:
                if e.reason != "menu_choice":
                    raise
                continue  # 子菜单定不下来：不探，录到每一张时各按自己的选择开（定不下来那张停 menu_choice）
            if result is not None and not result.get("ok", True):
                for d in self.state.docs:
                    if d.doc_type == doc_type and d.status == K.PENDING:
                        d.status, d.reason, d.detail = K.WAITING, "recipe_mismatch", ",".join(result.get("missing", []))
                self._set()

    def _loop(self) -> None:
        s = self.state
        for d in s.docs:
            if d.status in K.DONE_STATES or d.status == K.WAITING:
                continue
            if self._stop.is_set():
                self._set(status=K.BATCH_STOPPED, reason="", current="")
                return
            if self._pause.is_set():
                self._set(status=K.BATCH_PAUSED, reason="", current="")
                while self._pause.is_set() and not self._stop.is_set():
                    self.sleep(1)
                if self._stop.is_set():
                    self._set(status=K.BATCH_STOPPED, reason="")
                    return
                self._set(status=K.BATCH_RUNNING, reason="")
            if d.doc_id in self._skip and d.status == K.PENDING:
                d.status = K.SKIPPED
                self._set()
                continue
            if not self._one(d):
                return
            if s.held:  # 留着等人答框：剩下的单先不录（等账套），人处理完再交
                self._set(status=K.BATCH_READY, reason="account_busy", current="")
                return
        self._set(status=K.BATCH_FINISHED, reason="", current="", finished=self.clock())

    def _one(self, d: DocState) -> bool:
        """录一张（含崩溃后的重开续录）。回 False 表示整批要停。"""
        draft = self.draft_type(**d.draft)
        while True:
            if d.status == K.ENTERING:
                found = (self.find_saved(draft, d.counts_before) if self.find_saved
                         else B.state_of(draft, self.book, d.counts_before))
                if found["docnum"]:
                    d.status, d.docnum = K.SAVED, found["docnum"]
                    d.reports.append({"reason": "express_restarted"})
                    self._set()
                    return True
                if found["placeholders"]:
                    # 崩溃留下的空单头照旧 placeholder_left；保存途中停下的半截单 partial_saved（带缺什么）
                    d.reason = "placeholder_left" if d.crashes else "partial_saved"
                    d.status, d.detail = K.WAITING, ",".join(found["placeholders"])
                    d.partial = dict((found.get("partial") or [{}])[-1]) if found.get("partial") else {}
                    d.recheck = True
                    self._set()
                    return True
                if d.crashes >= 2:
                    d.status, d.reason = K.WAITING, "crash_repeated"
                    self._set()
                    return True
            d.status, d.counts_before = K.ENTERING, (self.before(draft) if self.before else self.book.counts())
            self._set(current=d.doc_id)
            try:
                rec = self.engine.run(draft)
            except Stopped:
                self._set(status=K.BATCH_STOPPED, reason="", current="")
                return False
            except Exception as e:  # noqa: BLE001 — 引擎没接住的意外（认不出的框留在 Express 上、菜单命令被禁用）
                # 这张如实记「未完成」（原因 + 原文），不留「正在录」；Express 上留着认不出的东西不乱点，整批停下等人，
                # 后面的单不再一张张撞上去（11-01 录制机：一张出意外后其余每张都报「命令被禁用」、页面一直「进行中」）
                d.status, d.reason, d.detail = K.WAITING, "unknown_screen", f"{type(e).__name__}: {e}"[:K.DETAIL_MAX]
                self.engine.form_open = None
                self._set(status=K.BATCH_STOPPED, reason="unknown_screen", current="")
                return False
            d.seconds = round(d.seconds + rec.get("seconds", 0.0), 1)
            d.reports += rec.get("reports", [])
            d.voided += rec.get("voided", [])
            d.placeholders += rec.get("placeholders", [])
            if rec.get("recheck"):  # 保存键按下之后出的事：再交来时先读账套判断存没存，不从头重录（11-12）
                d.recheck = True
            d.partial = dict(rec.get("partial") or {})
            d.dialog = dict(rec.get("dialog") or {})
            if rec.get("held"):  # Express 原样留着等人答框：收尾不退出，这本账套的别的单等着
                d.held, self.state.held = True, d.doc_id
            readback = rec.get("readback") or {}
            d.readback_ok = readback.get("ok") if readback else None
            outcome = rec["outcome"]
            if outcome == "crash":
                d.crashes += 1
                self._set()
                if not self._recover_crash():
                    return False
                continue  # 还是「正在录」：回到循环开头先读账套判断
            if outcome == "saved":
                d.status, d.docnum, d.reason = K.SAVED, rec["docnum"], ""
            else:
                d.status, d.reason, d.detail = K.WAITING, rec.get("reason") or "unknown_screen", rec.get("detail", "")
                d.docnum = rec.get("docnum", "")
                d.menu_options = list(rec.get("menu_options") or [])
                d.evidence = str(rec.get("evidence") or "")
                if rec.get("void_refused"):  # 放弃时作废闸没过：那张半截单留着，交给人处理
                    d.placeholders.append(rec["void_refused"])
            self._set(current="")
            if d.reason in K.BATCH_STOPPERS:
                self._set(status=K.BATCH_STOPPED, reason=d.reason)
                return False
            return True

    def _recover_crash(self) -> bool:
        """服务器连不上：先别点，每 ``net_poll_s`` 只读探一次，恢复了就重开 Express；超时整批停。"""
        self._set(status=K.BATCH_PAUSED, reason="network_wait")
        waited = 0.0
        while not self.reachable():
            if self._stop.is_set() or waited >= self.net_wait_s:
                self._set(status=K.BATCH_STOPPED, reason="" if self._stop.is_set() else "network_down")
                return False
            self.sleep(self.net_poll_s)
            waited += self.net_poll_s
        try:
            self.session.close()
            self.session.ensure_open()
        except NeedHuman as e:
            self._set(status=K.BATCH_STOPPED, reason=e.reason)
            return False
        self._set(status=K.BATCH_RUNNING, reason="")
        return True


@contextmanager
def no_lock(_account_dir: str):
    yield
