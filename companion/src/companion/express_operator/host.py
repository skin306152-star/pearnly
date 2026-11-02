# -*- coding: utf-8 -*-
"""代录宿主：写账线（``worker``）领到 ``entry_mode=operator`` 的单时交到这里，CC 窗口经桥（``tomo_bridge``）在这里看进度、控制。

- 写账队列一次交一张；同一批（载荷 ``operator.batch.id``）的单共用一个 ``batch.Runner``：第一张时开 Express（或接管已开着的、
  核对公司），每种单第一次先探路；批次最后一张（``index == total``）录完或人点停止 → 正常退出 Express；
  队列空了还剩没来的单（被别处拦下）时，空闲 ``IDLE_CLOSE_S`` 秒后也退出，释放授权名额；
- 后台做法（``bg_desk``）：不碰真键盘鼠标，人照常用电脑、锁屏都不停；只有人在 Express 里操作才暂停（``presence``）；
- 账套锁由写账线拿着（与直写同一把 PEARNLY.LCK）；放弃即作废、两道作废闸、断点续录都在 ``batch`` / ``engine`` 里；
- 批次文件与存档截图只在本机（``%APPDATA%\\Pearnly\\operator``），截图满 30 天按批次日期自动删（开始前、结束后、CC 启动时）。

不代录时不导入本模块（写账线按需导入），不开线程、不轮询。

执行目标（ADR-026）：按载荷 ``operator.target`` 选装配函数（:data:`ASSEMBLERS`）。``express`` 就是 :func:`express_factory`（原来的
那段装配原样）；``mrerp``（网页，:func:`mrerp_factory` → ``mrerp_operator.host.assemble``）只在开关打开时装（与心跳报的能力同一个
开关，``payload_contract.operator_targets``）；没装的目标不开批次、不碰任何账本，这一张按「不支持」回执。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from . import contract as K
from .draft import Draft, from_payload

IDLE_CLOSE_S = 120
RECENT_SHOWN = 50  # CC 窗口里列出的最近任务数
# 本机记下「代录开 / 接管的 Express」：进程号 → 批次、账套、程序，正常退出后删掉（主会话 10-27）
EXPRESS_MARKS = "express.json"
# CC 启动后等这么久：原批次的单没派回来接管上次留下的 Express，才按 Express 原生方式收拾掉它
ORPHAN_GRACE_S = 180
_MARKS_LOCK = threading.Lock()


def read_marks(root: Path) -> Dict[str, Dict]:
    try:
        found = json.loads((Path(root) / EXPRESS_MARKS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _write_marks(root: Path, marks: Dict[str, Dict]) -> None:
    path = Path(root) / EXPRESS_MARKS
    if not marks:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(marks, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def mark_opened(root: Path, pid: int, batch_id: str, exe: str, account_dir: str) -> None:
    with _MARKS_LOCK:
        marks = read_marks(root)
        marks[str(int(pid))] = {"batch": batch_id, "exe": exe, "account_dir": account_dir, "at": time.time()}
        _write_marks(root, marks)


def unmark(root: Path, pid: int) -> None:
    with _MARKS_LOCK:
        marks = read_marks(root)
        if marks.pop(str(int(pid)), None) is not None:
            _write_marks(root, marks)


def express_alive(pid: int) -> bool:
    """这个进程号还是不是一个在跑的 ExpressI（进程号会被别的程序复用：名字对不上的不认）。"""
    import sys

    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return False
        code = wintypes.DWORD()
        if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:  # STILL_ACTIVE
            return False
        return os.path.basename(buffer.value).lower() == "expressi.exe"
    finally:
        kernel.CloseHandle(handle)


def operator_root() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Pearnly" / "operator"


class Host:
    def __init__(self, root: Optional[Path] = None, *, factory: Optional[Callable] = None,
                 notify: Callable[[Dict], None] = lambda _s: None, clock: Callable[[], float] = time.time,
                 alive: Callable[[int], bool] = express_alive, assemblers: Optional[Dict[str, Callable]] = None):
        self.root = Path(root or operator_root())
        self.factory = factory or default_factory
        # 每种执行目标一个装配函数；``factory`` 是 Express 的（测试注入假 Express 用它）；MR.ERP 只在开发开关打开时装（ADR-026）
        self.assemblers: Dict[str, Callable] = {**enabled_assemblers(), K.TARGET_EXPRESS: self.factory,
                                                **(assemblers or {})}
        self.notify, self.clock, self.alive = notify, clock, alive
        self.started_at = clock()
        self._orphans_done = False
        self.runners: Dict[str, object] = {}
        self.parts: Dict[str, Dict] = {}  # 批次号 → 这一批的 desk / evidence / presence（实时画面、证据用）
        self.last_used: Dict[str, float] = {}
        self.lock = threading.Lock()

    # ── 写账线 ────────────────────────────────────────────────────────────
    def handle(self, task_id: str, payload: Dict, settings, member=None) -> Dict:
        """录一张。回 ``{"status": saved / human / stopped / skipped / waiting, "docnum", "reason", "detail", "meta"}``。

        ``settings``：这张单的 Express 设置（``ExpressSettings``：程序、账套目录、公司）；``member``：领到这张单的成员配置
        （``CompanionConfig``：站点地址、令牌、通道），AI 接手的模型调用走它的通道（11-05 测试机：原来拿 ExpressSettings
        建通道，没有令牌，每一批都没接上 AI）。

        ``task_id``：写账件号（写账线给 ``task_journal.key_of``：同一件写账每次派都一样，投递号会换）。同一件再派来照上次的结果；
        换了号是站点按原批次号交来的新指令，已停的这一批重新打开（``batch.Runner.add``）。"""
        info = (payload.get("operator") or {}).get("batch") or {}
        batch_id = str(info.get("id") or f"single-{task_id}")
        target = K.target_of(payload)
        factory = self.assemblers.get(target)
        if factory is None:
            # 这一版还不会录这个目标（ADR-026）：不开批次、不碰任何账本，这一张转人工（原因沿用「这种单暂不支持」）
            return {"status": "human", "reason": "unsupported",
                    "detail": f"operator target {target[:20]!r} is not available in this CC Desktop version",
                    "meta": {"batch": batch_id, "target": target[:20]}}
        # 单据号用站点上这张单的编号（载荷 operator.batch.doc），页面按它对上是哪一张；旧载荷没有就用写账任务号。
        # 别的目标（MR.ERP）的装配函数自带草稿形状与「设置」（公司、账套、连接都在载荷里，没有本机账套）
        draft = (getattr(factory, "draft_of", None) or from_payload)(str(info.get("doc") or task_id), payload)
        if settings is None and callable(getattr(factory, "settings_of", None)):
            settings = factory.settings_of(payload)
        with self.lock:
            runner = self.runners.get(batch_id) or self._start(batch_id, info, settings, factory, member)
        from .engine import NeedHuman

        try:
            runner.add(draft, task_id)
            doc = runner.run_one(draft.doc_id)
        except NeedHuman as error:
            # 整批停在哪一步、看到了什么（没截图时也查得到）；停在半路的这张记等人（原因同整批），再交来时先读账套判断存没存
            evidence = str(getattr(error, "evidence", "") or "")
            runner.halt(draft.doc_id, error.reason, error.detail, evidence=evidence)
            self._finish(batch_id)
            meta = {"batch": batch_id, **({"evidence": evidence} if evidence else {})}
            return {"status": "waiting" if error.reason in ("network_down",) else "human", "reason": error.reason,
                    "detail": error.detail[:K.DETAIL_MAX], "meta": meta}
        except Exception as error:  # noqa: BLE001 — 最后一道兜底（批次没接住的意外）：同上收尾成「认不出的画面」，不留 running
            detail = f"{type(error).__name__}: {error}"[:K.DETAIL_MAX]
            runner.halt(draft.doc_id, "unknown_screen", detail, hold=True)
            self._finish(batch_id)
            return {"status": "human", "reason": "unknown_screen", "detail": detail, "meta": {"batch": batch_id}}
        self.last_used[batch_id] = self.clock()
        total = int(info.get("total") or 0)
        index = int(info.get("index") or 0)
        if runner.state.status == K.BATCH_STOPPED or (total and index >= total):
            self._finish(batch_id)
        return self._result(batch_id, doc)

    def close_express(self, payload: Dict) -> None:
        """这张单要先建主档（``masters``）：这一批开着 Express 就先正常退出，下一张再开、重新核公司（主档索引要独占重建）。"""
        info = (payload.get("operator") or {}).get("batch") or {}
        runner = self.runners.get(str(info.get("id") or ""))
        if runner is not None:
            runner.close_express()

    def idle(self) -> None:
        """写账线队列空时调：剩下的单迟迟不来，就先退出 Express 释放授权名额；CC 启动满 ``ORPHAN_GRACE_S`` 还没有批次在跑，
        收拾上次 CC 留下的 Express（一次）。"""
        now = self.clock()
        for batch_id in [b for b, used in self.last_used.items() if now - used >= IDLE_CLOSE_S]:
            self._finish(batch_id)
        if not self._orphans_done and not self.runners and now - self.started_at >= ORPHAN_GRACE_S:
            self._orphans_done = True
            self.recover_orphans()

    def recover_orphans(self) -> list:
        """上次 CC 开 / 接管、没正常退出的 Express（本机记下的进程号，主会话 10-27）：原批次没在跑、进程还是 ExpressI 的，绑定它
        按 Express 原生方式收拾（``Runner.recover_orphan``：没进公司按取消；正在录的按放弃走作废两道保护，再正常退出）。
        不是本机记下的 Express 一律不碰；进程已经没了的只删记录。回收拾过的进程号。"""
        from types import SimpleNamespace

        from .batch import BatchState, BatchStore
        from .session import claimed

        done = []
        for key, info in read_marks(self.root).items():
            pid, batch_id = int(key), str(info.get("batch") or "")
            if batch_id in self.runners or pid in claimed():
                continue  # 这一批正在跑：它自己接管
            if not self.alive(pid):
                unmark(self.root, pid)
                continue
            store = BatchStore(self.root / "batches")
            try:
                state = store.load(batch_id)
            except (OSError, ValueError, KeyError, TypeError):
                state = BatchState(batch_id or f"express-{pid}", "", "", str(info.get("account_dir") or ""), [])
            settings = SimpleNamespace(account_dir=state.account_dir or str(info.get("account_dir") or ""),
                                       account_company=state.company, account_set=state.account_set, express_root="",
                                       express_exe=str(info.get("exe") or ""))
            runner = None
            try:
                runner, _parts = self.factory(state, store, settings, self.root, self.notify, member=None)  # 只收拾，不接 AI
                runner.session._claim(pid)
                runner.recover_orphan()
                done.append(pid)
            except Exception:  # noqa: BLE001 — 收拾不了的留给人（记录照删，不每次空闲都再试）
                pass
            finally:
                unmark(self.root, pid)
                if runner is not None:
                    runner.session.release()
                    self.notify(runner.snapshot())
        return done

    def _start(self, batch_id: str, info: Dict, settings, factory: Optional[Callable] = None, member=None):
        from .batch import BatchState, BatchStore

        state = BatchState(batch_id, str(getattr(settings, "account_company", "") or ""),
                           str(getattr(settings, "account_set", "") or ""), str(settings.account_dir), [],
                           start_at=float(info.get("start_at") or 0), site=str(info.get("company") or ""))
        store = BatchStore(self.root / "batches")
        try:
            state = store.load(batch_id)  # CC 重启后同一批接着来：续用批次文件（断点续录不重录）
            state.site = state.site or str(info.get("company") or "")
        except (OSError, ValueError, KeyError):
            store.save(state)
        runner, parts = (factory or self.factory)(state, store, settings, self.root, self.notify, member=member)
        self.runners[batch_id], self.parts[batch_id] = runner, parts
        return runner

    def _finish(self, batch_id: str) -> None:
        runner = self.runners.get(batch_id)
        self.last_used.pop(batch_id, None)
        if runner is not None:
            try:
                runner.finish()
            finally:
                presence = self.parts.get(batch_id, {}).get("presence")
                if presence is not None:
                    presence.close()  # 录完摘键鼠钩子
                self.notify(runner.snapshot())

    def _result(self, batch_id: str, doc) -> Dict:
        meta = {"batch": batch_id, "docnum": doc.docnum, "voided": doc.voided, "placeholders": doc.placeholders,
                "reports": doc.reports, "seconds": doc.seconds, "reason": doc.reason}
        if doc.reason == "menu_choice":  # 站点据此问一次走哪个菜单、记住后按 operator.form_choice 再交
            meta.update(doc_type=doc.doc_type, menu_options=list(doc.menu_options))
        if doc.evidence:  # 转人工那一刻的截图（本机路径）：站点单独处理，不在说明里
            meta["evidence"] = doc.evidence
        if doc.status == K.SAVED:
            return {"status": "saved", "docnum": doc.docnum, "meta": meta}
        if doc.status == K.SKIPPED:
            return {"status": "stopped" if doc.reason == "stopped" else "skipped", "reason": doc.reason or "skipped",
                    "meta": meta}
        if doc.status == K.ENTERING:
            return {"status": "waiting", "reason": doc.reason or "network_wait", "meta": meta}
        return {"status": "human", "reason": doc.reason or "unknown_screen", "detail": doc.detail[:K.DETAIL_MAX],
                "meta": meta}

    # ── CC 窗口（桥）──────────────────────────────────────────────────────
    def snapshots(self) -> list:
        """录单中的批次 + 本机批次文件里最近的批次（CC 重启后任务列表照样在，已完成的可回看，10-27 实机：原来重启后清空）。"""
        from .batch import BatchStore, snapshot_of
        from .evidence import Evidence

        live = [{**runner.snapshot(), "live": True} for runner in self.runners.values()]
        seen = {item["id"] for item in live}
        store, stored = BatchStore(self.root / "batches"), []
        for batch_id in store.recent(RECENT_SHOWN):
            if batch_id in seen:
                continue
            try:
                state = store.load(batch_id)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            # 本机没在跑（CC 重启前那一批、或剩下的单还没来）：照实标出来，进度以站点为准（页面问站点后经 settle 收尾）
            stored.append({**snapshot_of(state, now=self.clock(), evidence_state=Evidence(self.root / "evidence", batch_id).state),
                           "live": False})
        return live + stored

    def control(self, batch_id: str, action: str, doc_id: str = "", results: Optional[Dict] = None) -> bool:
        runner = self.runners.get(batch_id)
        if action == "delete":
            return self.delete(batch_id)
        if action == "settle":
            return self.settle(batch_id, results or {})
        if runner is None:
            return False
        if action == "pause":
            runner.pause()
        elif action == "resume":
            runner.resume()
            presence = (self.parts.get(batch_id) or {}).get("presence")
            if presence is not None:
                presence.resume_now()
        elif action == "stop":
            if runner.stop_now():  # 这时没有单在录：当场停、正常退出 Express（不等下一张领到）
                self._finish(batch_id)
        elif action == "skip" and doc_id:
            runner.skip(doc_id)
        else:
            return False
        return True

    def settle(self, batch_id: str, results: Dict) -> bool:
        """本机没在跑的任务按站点的结论收尾（主会话 10-27：不留僵尸「进行中」）。``results``：``{单据号: {"state": saved /
        queued / not_done / cancelled / unknown, "docnum", "reason"}}``（页面经共用层 ``status`` 问来）。已入账的记上 Express
        单号；未完成的转「等人处理」带原因；站点已撤回的记跳过（原因 stopped）；仍在队列、认不出的不动（单子来了接着录）。
        没有单再等时整批收尾：有撤回的记「已停止」，否则「已完成」。录单中的任务不碰。"""
        from .batch import BatchStore

        if not batch_id or batch_id in self.runners:
            return False
        store = BatchStore(self.root / "batches")
        try:
            state = store.load(batch_id)
        except (OSError, ValueError, KeyError, TypeError):
            return False
        cancelled = False
        for d in state.docs:
            found = results.get(d.doc_id) if isinstance(results, dict) else None
            if not isinstance(found, dict) or d.status in K.DONE_STATES:
                continue
            kind = found.get("state")
            if kind == "saved":
                d.status, d.reason = K.SAVED, ""
                d.docnum = str(found.get("docnum") or d.docnum or "")
            elif kind == "not_done":
                d.status, d.reason = K.WAITING, str(found.get("reason") or d.reason or "unknown_screen")[:300]
            elif kind == "cancelled":
                d.status, d.reason, cancelled = K.SKIPPED, "stopped", True
        if not any(d.status in (K.PENDING, K.ENTERING) for d in state.docs) \
                and state.status not in (K.BATCH_FINISHED, K.BATCH_STOPPED):
            state.status = K.BATCH_STOPPED if cancelled or any(d.reason == "stopped" for d in state.docs) \
                else K.BATCH_FINISHED
            state.current, state.finished = "", state.finished or self.clock()
        store.save(state)
        return True

    def delete(self, batch_id: str) -> bool:
        """删掉一个已结束的任务：只删本机的批次记录与存档截图，不动账套里任何东西；录单中的不能删。"""
        import shutil

        from .batch import BatchStore
        from .evidence import _safe

        if not batch_id or batch_id in self.runners:
            return False
        BatchStore(self.root / "batches").delete(batch_id)
        shutil.rmtree(self.root / "evidence" / _safe(batch_id), ignore_errors=True)
        return True

    def show(self, batch_id: str = "") -> bool:
        """「显示 Express 窗口」：把这个任务在用的 Express（没有就这台电脑上开着的那一个）切到前面给人看。是人点的，可以抢前台。"""
        desk = (self.parts.get(batch_id) or {}).get("desk")
        if desk is None:
            from .win32_desk import IS_WIN, WindowsDesk

            desk = WindowsDesk("") if IS_WIN else None
        try:
            return bool(desk and desk.show_main())
        except Exception:  # noqa: BLE001
            return False

    def frame(self, batch_id: str) -> str:
        """实时画面（只给桌面窗口）：只画 Express 主窗口，不截别的窗口；Express 没开时回空。"""
        desk = (self.parts.get(batch_id) or {}).get("desk")
        if desk is None:
            return ""
        try:
            return desk.screenshot([960, 540])["png"]
        except Exception:  # noqa: BLE001
            return ""


def express_program(settings) -> str:
    """开哪个 Express 程序：这张单所在数据根（年度文件夹）里的 ExpressI.exe；没有才用这个成员配的。
    成员配的是配对时那一年的（10-27 实机：69EXP 的单用了成员默认的 68EXP 程序）；直写不开程序，代录要开对年度的。"""
    root = str(getattr(settings, "express_root", "") or "").strip()
    if root:
        candidate = Path(root) / "ExpressI.exe"
        if candidate.is_file():
            return str(candidate)
    return str(getattr(settings, "express_exe", "") or "")


def front_of(desk) -> Dict:
    """整台电脑的前台窗口，标上是不是这一批绑定的那个 Express（``mine``；还没绑定时任何 Express 都算）。"""
    from .win32_desk import WindowsDesk

    fg = WindowsDesk.foreground(desk)
    fg["mine"] = desk.pid is None or int(fg.get("pid") or 0) == int(desk.pid)
    return fg


def express_factory(state, store, settings, root: Path, notify, member=None):
    """Express 的装配（Windows 上的真实组装）：后台做法（不碰真键盘鼠标）+ 真账套 + 本机截图。``settings`` 是 Express 设置，
    ``member`` 是成员配置（AI 通道用它的站点与令牌；没有就不接 AI，认不出的画面交人）。"""
    from . import credentials
    from . import recipe as R
    from .batch import Runner, no_lock
    from .bg_desk import BackgroundDesk
    from .book import DbfBook
    from .engine import Engine
    from .evidence import Evidence, prune
    from .presence import Presence, WindowsSignals
    from .probe import MenuMemory, ProbeStore, probe_form
    from .session import Session

    recipe = R.load()
    exe = express_program(settings)
    desk = BackgroundDesk(exe)
    book = DbfBook(settings.account_dir)
    session = Session(desk, recipe, express_exe=exe, account_dir=str(settings.account_dir),
                      company_name=str(getattr(settings, "account_company", "") or ""),
                      credential_target=credentials.target("default"),  # 这个 Windows 用户的 Express 登录，一份
                      on_claim=lambda pid: mark_opened(root, pid, state.id, exe, str(settings.account_dir)),
                      on_gone=lambda pid: unmark(root, pid),
                      own_pids=lambda: [int(pid) for pid in read_marks(root)])
    evidence_root = root / "evidence"
    evidence = Evidence(evidence_root, state.id)
    holder: Dict = {}
    presence = Presence(WindowsSignals(), lambda: desk.last_input_tick,
                        stop_requested=lambda: holder["runner"].stop_requested(),
                        on_pause=lambda r: holder["runner"].presence_paused(r),
                        on_resume=lambda: holder["runner"].presence_resumed(),
                        background=True, foreground=lambda: front_of(desk))
    # AI 接手（规则表认不出的画面）：模型调用经产品站点（`companion.operator_ai`，密钥只在服务端）；连不上站点的成员照旧判断不了就交人
    from .helper import ComputerUseHelper
    from ..operator_ai import transport_for

    transport = transport_for(member, state.id)  # 成员配置（站点、令牌）；不是 settings（ExpressSettings 没有令牌）
    probes = ProbeStore(root / "probe.json")
    account_key = str(settings.account_dir).lower()
    engine = Engine(desk, recipe, book, presence=presence, evidence=evidence.save,
                    helper=ComputerUseHelper(transport, batch=state.id) if transport else None,
                    menus=MenuMemory(probes, account_key, recipe.version))

    def probe(doc_type):
        cached = probes.get(account_key, recipe.version, doc_type)
        if cached is not None:
            return cached
        result = probe_form(engine, doc_type)
        probes.put(account_key, recipe.version, doc_type, result)
        return result

    runner = Runner(state, store, engine, session, book, probe=probe, lock=no_lock, on_change=notify,
                    housekeeping=lambda: prune(evidence_root), evidence_state=evidence.state,
                    pauses=lambda: presence.evidence)
    holder["runner"] = runner
    return runner, {"desk": desk, "presence": presence, "evidence": evidence}


default_factory = express_factory  # 旧名：收拾上次留下的 Express（``recover_orphans``）与测试仍按它取


def mrerp_factory(state, store, settings, root: Path, notify):
    """MR.ERP 网页的装配（``mrerp_operator.host.assemble``）：领到 MR.ERP 代录单才导入那个包。"""
    from ..mrerp_operator.host import assemble

    return assemble(state, store, settings, root, notify)


def _mrerp_settings(payload: Dict):
    from ..mrerp_operator.host import settings_of

    return settings_of(payload)


def _mrerp_draft(doc_id: str, payload: Dict):
    from ..mrerp_operator.host import draft_of

    return draft_of(doc_id, payload)


mrerp_factory.settings_of = _mrerp_settings
mrerp_factory.draft_of = _mrerp_draft

# 执行目标 → 装配函数（ADR-026）。默认与 ``payload_contract.OPERATOR_TARGETS``（心跳报的能力）一致：只有 Express；
# MR.ERP 只在开发开关打开时才装、才报（``enabled_assemblers`` 与 ``payload_contract.operator_targets`` 同一个开关）。
ASSEMBLERS: Dict[str, Callable] = {K.TARGET_EXPRESS: express_factory}
OPTIONAL_ASSEMBLERS: Dict[str, Callable] = {K.TARGET_MRERP: mrerp_factory}


def enabled_assemblers() -> Dict[str, Callable]:
    """这台 CC 现在装哪些目标：与心跳报的 ``payload_contract.operator_targets()`` 一致。"""
    from ..payload_contract import operator_targets

    return {target: ASSEMBLERS.get(target) or OPTIONAL_ASSEMBLERS[target] for target in operator_targets()}


_HOST: Optional[Host] = None


def host(notify: Optional[Callable[[Dict], None]] = None) -> Host:
    """托盘进程里只有一个宿主（写账线与桥共用）。"""
    global _HOST
    if _HOST is None:
        _HOST = Host(notify=notify or (lambda _s: None))
    elif notify is not None:
        _HOST.notify = notify
    return _HOST


def startup_tidy(root: Optional[Path] = None) -> None:
    """CC 启动时扫一次过期截图（不开线程、不轮询）；本机记着上次留下的 Express 时先建好宿主，写账线空闲满
    ``ORPHAN_GRACE_S`` 后由它收拾（``Host.idle``）。"""
    from .evidence import prune

    base = Path(root or operator_root())
    try:
        prune(base / "evidence")
    except Exception:  # noqa: BLE001
        pass
    if root is None and read_marks(base):
        host()
