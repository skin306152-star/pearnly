# -*- coding: utf-8 -*-
"""文件夹自动录入的后台线程：按云端下发的规则定时扫本机文件夹、上传新文件、把结果表格放回文件夹。

与写账套的那条线完全分开（影响评估 2026-10-06）：
- 自己一条守护线程、自己的 httpx 客户端，不进 PollWorker / 写入线、不碰看门狗的存活时标，
  上传再慢也不会被当成「卡死」重启，也不拖慢 DBF 写入；
- 本机没给任何连接设允许读取的根目录时，这个线程根本不启动（零线程、零网络、零文件）；
- 每个连接的机器请求一个接一个发（不与心跳 / 领任务争连接锁），原件一份一份传，不占满办公室上行；
- 自动推送、写账套仍走原来的云端队列与 PollWorker，这里一行不碰。

公司的文件夹（2026-10-15，CC 按公司分组；协议号不变）：
- 回复里的 ``max_roots``（一次收几个根目录，没有按 20）封顶上报，``roots_in_use``（任务或公司绑定用着的根目录）用来
  自动收回经公司添加、连续一小时没人用的根目录（没有这个键就不收；旧授权永不自动收回）；
- 刚经公司加了根目录：``report_roots_now`` 马上发一次只带根目录的快速同步（``roots_only``），并 ``wake()`` 让下一轮立刻开始；
- 下级文件夹名合计封顶 ``folder_plan.FOLDERS_TOTAL``（与服务端相等）。

同步顺带的几件事（2026-10，CC 闭环第一批；协议号不变，旧服务端不认的键照旧忽略）：
- 各文件夹现有几份（根目录行的 ``counts`` / ``partial``）与各任务待处理几份（``rule_pending``），给网页卡片与任务列表；
- 云端随回复带来的对话提醒（``notices``，请求带 ``notices_after`` 才有）交给托盘弹系统通知；
- 回复里的最新版本（``companion_latest``）：只读文件夹的成员没有心跳，也从这里收到新版。

一次运行中途被叫停（自动更新、退出、关机）不算这个时刻处理过：运行记录照报但不带 ``scanned``（``note`` 为
``interrupted``），下次启动同一时刻照样到点，接着用同一个运行号（云端是同一条运行记录、同一份完成简报），
已传过的文件台账认得、不再传。叫停只在两份文件之间生效，正在传的那一份传完。
"""

from __future__ import annotations

import base64
import hashlib
import math
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import folder_plan as plan
from . import update_block
from .config import member_active
from .queue_client import _device_name
from .folder_client import FolderClient, Refused, put_direct
from . import folder_store
from .folder_store import Ledger, connection_key, roots
from .logger import activity, get_logger
from .version import VERSION

log = get_logger(__name__)

IDLE_SECONDS = 60
# 根目录下的文件夹名与各文件夹现有几份一起重扫，间隔看上一次花了多久（2026-10）：一秒内看全就每轮都刷（放进文件后一两分钟
# 网页就看得到）；更慢就每 2 分钟（原只报文件夹名时的节奏，10-09 由 15 分钟改来：新建的子文件夹要等很久才出现在网页下拉）；
# 数不完（太大、网络共享太慢，见 folder_plan.SURVEY_*）就每 10 分钟才重数，免得每分钟把整棵树数一遍——这期间文件夹名
# 仍每 2 分钟刷新，计数沿用上一次的。
FOLDER_REPORT_SECONDS = 2 * 60
FOLDER_REPORT_CHEAP_SECONDS = 1.0
FOLDER_REPORT_PARTIAL_SECONDS = 10 * 60
UNSTABLE_ROUNDS = 3
# 服务端「这会儿不收、过后会收」的原因：留着补传。其余（类型、大小…）是这份文件本身不收，不再传。
RETRY_CODES = {"cowork.folder.owner_not_ready", "cowork.folder.rule_disabled", "cowork.folder.disabled",
               "cowork.folder.step_error"}
# 同步回复里的对话提醒：字段与服务端 folder_intake/contracts.NOTICE_FIELDS 逐字一致（tests/test_companion_contract.py 守住）。
# ``view``（1.9.1 起）：没有对话时点开哪一页（``company:<编号>`` = 这家公司的主档页）；旧服务端不带，当作定时任务页。
NOTICE_FIELDS = ("id", "title", "text", "created", "session", "view")
NOTICE_OPTIONAL = ("view",)
NOTICE_LIMIT = 20


class FolderIntake:
    def __init__(self, configs: Callable[[], Iterable], *, client_factory=FolderClient,
                 clock: Callable[[], float] = time.time, lang: Callable[[], str] = lambda: "",
                 on_status: Optional[Callable[[str, str], None]] = None,
                 on_notices: Optional[Callable[[str, list], None]] = None,
                 on_update: Optional[Callable[[str, str], None]] = None,
                 on_roots_changed: Optional[Callable[[], None]] = None,
                 timer: Callable[[], float] = time.monotonic,
                 announce: Optional[Callable] = None) -> None:
        self._configs = configs
        # announce(cfg)：MR.ERP 的只读文件夹成员没有取任务线程，在这里认电脑（device_link.Announcer.announce；托盘传入）
        self._announce = announce
        # 自动收回了经公司添加的根目录（本线程调）：托盘经 Qt 信号让设置页 / 侧栏重取本机状态。
        self._on_roots_changed = on_roots_changed
        # MR.ERP 的小助手成员（kind=folder）没有心跳：它在主窗口与托盘上的「在线 / 断开」就看这里每次同步成没成。
        self._on_status = on_status
        # on_notices(站点, [提醒…])：托盘弹系统通知；on_update(版本, 地址)：托盘的新版提示与自动更新。
        # 都在本线程调，托盘经 Qt 信号转回界面线程。
        self._on_notices = on_notices
        self._on_update = on_update
        self._client_factory = client_factory
        self._clock = clock
        self._timer = timer  # 数文件夹的时限与耗时（单调时钟）
        self._lang = lang
        self._stop = threading.Event()
        # wake()：加了 / 删了根目录，下一轮不等满 IDLE_SECONDS 就开始（线程正在一轮里时，这一轮做完马上再来一轮）。
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # 服务端回复里的上限与在用的根目录（{连接: …}，2026-10-15）：max_roots 没收到过按 plan.DEFAULT_MAX_ROOTS（旧服务端）；
        # roots_in_use 没收到过（旧服务端）就永不自动收回。_unused_since：经公司添加的根目录从哪一刻起不在用（只记在内存里，
        # 重启重新计时，偏保守）。
        self._max_roots: dict[str, int] = {}
        self._in_use: dict[str, frozenset] = {}
        self._unused_since: dict[tuple, float] = {}
        # 上一次上报的根目录行（{连接: {at, ids, report, cost, complete}}），见 _report。
        self._folders: dict[str, dict] = {}
        # 上一次数到的文件（{连接: {根目录编号: [Seen…]}}）与数过几次，算各任务待处理数用。
        self._seen: dict[str, dict] = {}
        self._surveyed: dict[str, int] = {}
        # 各任务待处理几份（{连接: {规则名: n}}），下一次同步报给云端；_rule_pending_basis 是算它时的依据，没变就不重算。
        self._rule_pending: dict[str, dict] = {}
        self._rule_pending_basis: dict[str, tuple] = {}
        # 上一轮看到的各规则文件夹在不在（{连接: {规则名: ok | root_missing | folder_missing}}），下一次同步报给云端，
        # 网页规则列表据此显示「文件夹不可用」（以前文件夹没了只在本机记一笔，规则静静地不跑）。
        self._health: dict[str, dict] = {}
        # 已告诉云端「这条连接没有文件夹」的连接（本次运行内）。文件夹删光后、以及每次启动，都要报一次空的，
        # 否则云端一直留着上一次报的文件夹（缓存 7 天），网页「连接」与定时任务还能选到已删掉的文件夹（2026-10-10 测试机）。
        self._told_empty: set[str] = set()
        # _seen 只留落在任务文件夹里的文件：留了哪些（{连接: {根目录编号: {规则文件夹比较键}}}；None = 刚数过、全留着）。
        self._seen_scope: dict[str, Optional[dict]] = {}
        # 正在跑一次运行或补传（逐份上传中）：托盘的自动更新等它跑完（busy）。
        self._working = False

    # ── 线程 ──────────────────────────────────────────────────────────────

    def wanted(self) -> bool:
        # MR.ERP 的小助手成员没加文件夹也要同步：网页靠它知道小助手已配好（显示「请在小助手中添加」而不是「连接小助手」）。
        return any(self._due(cfg, connection_key(cfg)) for cfg in self._active())

    def _due(self, cfg, key: str) -> bool:
        return bool(key) and bool(_folder_member(cfg) or roots(key) or key not in self._told_empty)

    def start(self) -> None:
        """有连接设了根目录（或还没向云端报过「没有文件夹」）才启动；已在跑就不重复启动。"""
        if self._thread and self._thread.is_alive():
            return
        if not self.wanted():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._loop, name="pearnly-folder-intake", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        """根目录加了或删了：线程没在跑就起来，在跑就让下一轮马上开始（不等满 IDLE_SECONDS）。"""
        self.start()
        self._wake.set()

    def max_roots(self, key: str) -> int:
        """服务端一次同步收几个根目录（回复里的 max_roots）；没收到过按旧服务端的 20。"""
        return self._max_roots.get(key) or plan.DEFAULT_MAX_ROOTS

    def roots_in_use(self, key: str) -> Optional[frozenset]:
        """最近一次回复里这条连接在用的根目录编号（任务或公司绑定用着的）；没收到过是 None。"""
        return self._in_use.get(key)

    def running(self) -> bool:
        """线程还在（停下要等它做完手上这一步，例如一份正在上传的文件）。"""
        return bool(self._thread and self._thread.is_alive())

    def busy(self) -> bool:
        """正在跑一次运行或补传（逐份上传中）：自动更新等这一轮跑完再装。"""
        return self._working

    @contextmanager
    def _work(self):
        self._working = True
        try:
            yield
        finally:
            self._working = False

    def _active(self) -> list:
        try:
            return [cfg for cfg in list(self._configs() or []) if member_active(cfg)
                    and getattr(cfg, "paired", True)]
        except Exception:
            return []

    def _loop(self) -> None:
        while not self._stop.is_set():
            for cfg in self._active():
                if self._stop.is_set():
                    return
                key = connection_key(cfg)
                if not self._due(cfg, key):
                    continue
                if self._announce is not None and _folder_member(cfg):
                    try:
                        self._announce(cfg)
                    except Exception:
                        log.exception("认电脑失败（忽略）")
                    if not member_active(cfg):
                        continue   # 这把密钥服务端说不能再用：等对话页重新登记（device_link）
                try:
                    self.round(cfg, key)
                    self._status(cfg, "idle")
                except Refused as error:
                    log.info("文件夹录入本轮跳过: %s", error)
                    # 配对失效（401）报 auth：托盘显示「连接已失效，请在设置中重新连接」（同 Express 成员心跳回 401）
                    self._status(cfg, "auth" if error.rejected else "offline")
                except Exception:
                    log.exception("文件夹录入本轮失败")
                    self._status(cfg, "offline")
            self._wake.wait(IDLE_SECONDS)
            self._wake.clear()

    def _status(self, cfg, key: str) -> None:
        if self._on_status is None or getattr(cfg, "kind", "express") != "folder":
            return
        try:
            self._on_status(str(getattr(cfg, "profile_id", "") or ""), key)
        except Exception:
            log.exception("文件夹成员状态回调失败")

    # ── 一轮 ──────────────────────────────────────────────────────────────

    def round(self, cfg, key: str) -> None:
        ledger = Ledger(key)
        client = self._client_factory(cfg.backend_url, cfg.bearer_token)
        try:
            stored = roots(key)
            # 服务端一次收几个根目录（回复里的 max_roots，没收到过按 20）：多的这次不报，也不当作这台电脑读得到
            # （服务端不认识它们，任务用不上；旧服务端收到超过 20 个会让整次同步失败）。
            local_roots = stored[:self.max_roots(key)]
            # device：本机名，网页文件夹下拉在有几台小助手时用它区分电脑（MR.ERP 任务可借用任一台，2026-10-09）。
            # rule_pending：上一轮算好的各任务待处理数（与 rule_health 同一节奏）；notices_after：对话提醒收到哪一刻为止。
            # update_blocked：自动更新被 Windows 拦下（同心跳；只读文件夹的 MR.ERP 成员没有心跳，靠这里报）。
            response = client.sync({"version": VERSION, "device": _device_name(), "roots": self._report(key, local_roots),
                                    "runs": list(ledger.runs), "watch": ledger.watch(),
                                    "rule_health": self._health.get(key, {}),
                                    "rule_pending": self._rule_pending.get(key, {}),
                                    "notices_after": ledger.notices_after or 0.0,
                                    "update_blocked": update_block.reported()})
            if not response.get("ok"):
                return
            self._limits(key, response)
            self._release(key, stored, response)
            if local_roots:
                self._told_empty.discard(key)
            else:
                self._told_empty.add(key)
            ledger.runs = []
            # 提醒与新版提示先交出去：后面的运行可能要传很久，不能拖慢提醒。
            self._notices(cfg, ledger, response.get("notices"))
            self._offer(response)
            now = self._clock()
            offset = float(response.get("server_time") or now) - now
            # 记下这一次回复里出现的任务；回复带了这台电脑读的全部任务名（rule_names，含停用的）时，才清掉很久没再出现的
            # 任务（已删除、换到别的电脑读）在台账里的行（folder_store.Ledger.saw_rules）。
            every = response.get("rule_names")
            ledger.saw_rules([rule.get("name") for rule in response.get("rules") or [] if isinstance(rule, dict)]
                             + ([name for name in every if isinstance(name, str)] if isinstance(every, list) else []),
                             now, complete=isinstance(every, list))
            by_id = {row["id"]: row for row in local_roots}
            self._health[key] = {rule["name"]: _folder_health(rule, by_id.get(rule.get("root_id")))
                                 for rule in response.get("rules") or [] if rule.get("name")}
            touched = False
            # 按云端下发的顺序跑（同一账套的存货进货任务排在销售之前，F7），本机不另排序。
            for rule in response.get("rules") or []:
                if self._stop.is_set():
                    break
                if not rule.get("ready"):
                    continue
                state = ledger.rule_state(rule["name"])
                # 本机第一次跑到这条任务（同步回复里出现过就记了 seen_at，以 last_request 为准：各版都在这里第一次记它）
                first = "last_request" not in state
                if "last_request" not in state:
                    state["last_request"] = 0
                root = by_id.get(rule.get("root_id"))
                if rule.get("existing"):
                    # 新任务：不拍底，到点处理文件夹里本任务还没处理过的全部文件（改过文件夹的老任务也转成这样，旧底丢掉）；
                    # 只有任务后来多收的扩展名补拍底（见 _added_baseline）。
                    moved = "baseline_since" in state
                    state.pop("baseline", None)
                    state.pop("baseline_since", None)
                    state.pop("baseline_exts", None)
                    self._added_baseline(state, rule, root, first or moved)
                else:
                    state.pop("seen_exts", None)
                    state.pop("added_baseline", None)
                    self._baseline(state, rule, root, offset)
                due = plan.due(rule, state, now + offset)
                if due:
                    with self._work():
                        self._run(client, cfg, ledger, rule, root, due, offset)
                    ledger.save()
                    touched = True
                elif root is not None:
                    with self._work():
                        touched = self._retry(client, cfg, ledger, rule, root) or touched
            self._deliver(client, ledger, response.get("entries") or {}, by_id)
            if not self._stop.is_set():
                self._count_pending(key, ledger, response.get("rules") or [], touched)
        finally:
            ledger.save()
            client.close()

    def _limits(self, key: str, response: dict) -> None:
        """记下回复里的 max_roots 与 roots_in_use（旧服务端不带，就保持原样：按 20 封顶、不自动收回）。"""
        limit = response.get("max_roots")
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            self._max_roots[key] = min(limit, plan.MAX_ROOTS)
        used = response.get("roots_in_use")
        if isinstance(used, list):
            self._in_use[key] = frozenset(item for item in used if isinstance(item, str))

    def _release(self, key: str, stored: list, response: dict) -> bool:
        """自动收回经公司添加、已没人用的根目录（规格 §7.3）：回复里带了 roots_in_use 才做；只收 ``origin`` 为
        ``company``、加了 RELEASE_SECONDS 以上、而且连续 RELEASE_SECONDS 不在 roots_in_use 里的。旧授权（没有
        ``origin``）永不自动收回。返回收回了没有。"""
        if not isinstance(response.get("roots_in_use"), list):
            return False
        used = self._in_use.get(key) or frozenset()
        now = self._clock()
        gone = []
        for row in stored:
            mark = (key, row.get("id"))
            if row.get("origin") != "company" or row.get("id") in used:
                self._unused_since.pop(mark, None)
                continue
            since = self._unused_since.setdefault(mark, now)
            try:
                added = float(row.get("added_at"))
            except (TypeError, ValueError):
                added = now  # 没记添加时间：从现在起算，至少再等一小时
            if now - added >= plan.RELEASE_SECONDS and now - since >= plan.RELEASE_SECONDS:
                gone.append(row)
        released = False
        for row in gone:
            if folder_store.remove_root(key, row["id"]):
                log.info("收回经公司添加、已 1 小时没人用的文件夹：%s", row.get("path"))
                released = True
            self._unused_since.pop((key, row.get("id")), None)
        if released and self._on_roots_changed is not None:
            try:
                self._on_roots_changed()
            except Exception:
                log.exception("文件夹变更回调失败")
        return released

    def report_roots_now(self, profile_id: str) -> bool:
        """快速同步（规格 §7.3）：刚经公司加了根目录，马上让服务端认得它，不等这一轮（线程可能正在跑运行或上传）。

        只报根目录（``roots_only``），新加的（上一次完整上报里没有的）带下级文件夹名，其余的不带 ``folders`` 键（服务端沿用
        上一次的）；不收任务、不报运行记录。用自己的一个客户端，可以在别的线程里调。返回服务端收下了没有（失败不要紧，
        下一轮完整同步照样报）。"""
        cfg = next((cfg for cfg in self._active() if str(getattr(cfg, "profile_id", "") or "") == str(profile_id)), None)
        key = connection_key(cfg) if cfg is not None else ""
        if not key:
            return False
        known = set((self._folders.get(key) or {}).get("ids") or ())
        lines = []
        for row in roots(key)[:self.max_roots(key)]:
            line = {"id": row["id"], "label": row["label"], "share": plan.share_path(row["path"])}
            if row["id"] not in known:
                line["folders"] = plan.folders_of(Path(row["path"]))
            lines.append(line)
        client = self._client_factory(cfg.backend_url, cfg.bearer_token)
        try:
            response = client.sync({"roots_only": True, "version": VERSION, "device": _device_name(),
                                    "roots": plan.cap_folders(lines)})
        except Exception as error:
            log.info("快速同步文件夹失败（下一轮再报）：%r", error)
            return False
        finally:
            client.close()
        if not isinstance(response, dict) or not response.get("ok"):
            return False
        self._limits(key, response)
        return True

    def _report(self, key: str, local_roots: list) -> list:
        """根目录、下级文件夹名与各文件夹现有几份（``counts`` / ``partial``）；间隔按上一次花的时间定（见
        FOLDER_REPORT_*），根目录变了立即重扫。几个根目录共用一份时限与条数（folder_plan.SURVEY_*），用完就停：
        没轮到的根目录不带 ``counts``（网页不显示几份，不用 0 冒充）；根目录本身读不到的也不带。上一次数不全时
        计数 10 分钟才重数，文件夹名照样每 2 分钟刷新（计数沿用上一次的）。"""
        now = self._clock()
        ids = [row["id"] for row in local_roots]

        def build(row, folders, measured):
            line = {"id": row["id"], "label": row["label"], "folders": folders, "share": plan.share_path(row["path"])}
            if measured is not None:
                # 票据与 POS 导出分开数：POS 导出只有推往 Express 的销售任务收（云端按任务加上）
                line["counts"], line["partial"], line["table_counts"] = measured
            return line

        cached = self._folders.get(key)
        if cached and cached["ids"] == ids:
            recount = _report_interval(cached["cost"], cached["complete"])
            if now - cached["counted"] < recount:
                if now - cached["at"] < min(recount, FOLDER_REPORT_SECONDS):
                    return cached["report"]
                report = plan.cap_folders([build(row, plan.folders_of(Path(row["path"])),
                                                  cached["measured"].get(row["id"])) for row in local_roots])
                cached.update(at=now, report=report)
                return report
        started = self._timer()
        deadline, budget = started + plan.SURVEY_SECONDS, [plan.SURVEY_ENTRIES]
        report, seen, measured, complete = [], {}, {}, True
        for row in local_roots:
            path = Path(row["path"])
            folders = plan.folders_of(path)
            if complete:
                try:
                    files, complete = plan.survey(path, deadline=deadline, budget=budget, clock=self._timer)
                except Exception:
                    log.exception("文件夹计数失败")
                    complete = False
                else:
                    if files is not None:  # None：根目录本身读不到，不知道几份
                        measured[row["id"]] = (plan.folder_counts(files, ["", *folders]), not complete,
                                               plan.folder_counts(files, ["", *folders], plan.TABLE_EXTENSIONS))
                        seen[row["id"]] = files
            report.append(build(row, folders, measured.get(row["id"])))
        plan.cap_folders(report)  # 下级文件夹名合计封顶（与服务端 FOLDERS_TOTAL 相等）
        self._folders[key] = {"at": now, "counted": now, "ids": ids, "report": report, "measured": measured,
                              "cost": self._timer() - started, "complete": complete}
        self._seen[key] = seen
        self._seen_scope[key] = None
        self._surveyed[key] = self._surveyed.get(key, 0) + 1
        return report

    def _count_pending(self, key: str, ledger: Ledger, rules: list, touched: bool) -> None:
        """各任务还有几份没处理（不在台账、也不在老任务的拍底里），下一次同步报给云端。只在文件夹重新数过、这一轮传过
        文件或任务变了时重算（文件多时逐份比对台账也要时间；同一根目录下的几条任务一起、文件只走一遍）；根目录没数到的
        任务不报（网页不显示，不用 0 冒充）。数过之后只留落在某个任务文件夹里的文件；之后新加的任务落在没留的文件夹里，
        这次先不报，下一轮重新数。"""
        basis = (self._surveyed.get(key, 0),
                 tuple((rule.get("name"), rule.get("root_id"), rule.get("folder"), bool(rule.get("existing")),
                        rule.get("active_since"), (ledger.rules.get(rule.get("name")) or {}).get("baseline_since"),
                        plan.taken_extensions(rule, ledger.rules.get(rule.get("name")) or {}))
                       for rule in rules))
        if not touched and self._rule_pending_basis.get(key) == basis:
            return
        self._rule_pending_basis[key] = basis
        seen = self._seen.get(key) or {}
        scope = self._seen_scope.get(key)
        by_root: dict[str, list] = {}
        for rule in rules:
            name, root = rule.get("name"), rule.get("root_id")
            if not name or root not in seen:
                continue
            if scope is not None and not _covered(plan.folder_key(rule.get("folder") or ""), scope.get(root) or ()):
                self._folders.pop(key, None)  # 下一轮整个重数
                continue
            state = ledger.rules.get(name) or {}
            if rule.get("existing"):
                baseline: set = set(state.get("added_baseline") or [])
            elif state.get("baseline_since") == float(rule.get("active_since") or 0):
                baseline = set(state.get("baseline") or [])
            else:
                continue  # 老任务还没拍底（负责人还没准备好，规则没跑过）：不知道几份
            by_root.setdefault(root, []).append((
                name, rule.get("folder") or "",
                lambda relpath, size, mtime, rule_name=name: ledger.known(rule_name, relpath, size, mtime) is not None,
                baseline, plan.taken_extensions(rule, state)))
        pending = {}
        for root, members in by_root.items():
            pending.update(plan.pending_counts(seen[root], members))
        self._rule_pending[key] = pending
        if scope is None:
            folders: dict[str, set] = {}
            for rule in rules:
                if rule.get("name") and rule.get("root_id") in seen:
                    folders.setdefault(rule["root_id"], set()).add(plan.folder_key(rule.get("folder") or ""))
            self._seen[key] = {root: plan.within(files, folders.get(root) or ()) for root, files in seen.items()}
            self._seen_scope[key] = folders

    def _notices(self, cfg, ledger: Ledger, items) -> None:
        """云端随同步回复带来的对话提醒（请求带了 notices_after 才有；最多 20 条、12 小时内，按时间先后）：合形状的
        交给托盘弹系统通知（托盘按 ``notice:<id>`` 去重，对话页实时收到的同一条不再弹），并记下收到哪一刻为止。"""
        if not isinstance(items, list):
            return
        fresh = [notice for notice in (_notice(row) for row in items[:NOTICE_LIMIT]) if notice]
        first = ledger.notices_after is None
        if not fresh:
            if first:
                ledger.notices_after = 0.0  # 收到过回复了：之后带来的都是新的
            return
        ledger.notices_after = max(ledger.notices_after or 0.0, *(notice["created"] for notice in fresh))
        ledger.save()  # 马上记下：后面的运行可能要传很久，中途退出也不再重弹
        if first:
            # 这条连接第一次收到（刚装、刚升级、重新配对）：最近 12 小时的多半已在手机或网页上看过，只弹最新一条
            fresh = [max(fresh, key=lambda notice: notice["created"])]
        if self._on_notices is None:
            return
        try:
            self._on_notices(str(getattr(cfg, "backend_url", "") or ""), fresh)
        except Exception:
            log.exception("对话提醒交给托盘失败")

    def _offer(self, response: dict) -> None:
        """回复里的最新版本（``companion_latest``，与心跳回复同一形状）：只读文件夹的成员没有心跳，也从这里收到新版；
        有心跳的连接两边都会带来，托盘同一版本只提示一次（auto_update.AutoUpdate.offer）。"""
        if self._on_update is None:
            return
        try:
            from .worker import _offer_update

            _offer_update(response, lambda update: self._on_update(update["version"], update["url"]))
        except Exception:
            log.exception("新版本提示失败")

    def _baseline(self, state: dict, rule: dict, root: Optional[dict], offset: float) -> None:
        """老任务（云端 ``existing`` 为假，2026-10 之前建的）第一次见到时（或它重新启用时），给文件夹里已有的文件拍一张底：

        这些是启用前就在的（按创建时间早于启用时刻），不处理；此后不在这张底里的文件都算新放进来的——
        包括同一个盘里「剪切移动」进来的旧文件（Windows 移动不改创建时间，光看创建时间会漏掉）。

        底按哪些扩展名拍的记在 ``baseline_exts``。任务后来多收了扩展名（升级到 1.5、云端给推往 Express 的销售任务下发
        POS 导出）：此刻已在文件夹里的这类文件补进底里（任务那时还不收它们，可能早已手工录过），之后放进来的才处理；
        这次没读全（网络共享抖了一下）就下一轮再补，补好之前这几种扩展名不扫（`plan.taken_extensions`）。"""
        since = float(rule.get("active_since") or 0)
        if root is None:
            return
        extensions = plan.rule_extensions(rule)
        folder = plan.rule_folder(root["path"], rule.get("folder") or "")
        if state.get("baseline_since") != since:
            state["baseline"] = sorted(c.relpath.casefold() for c in plan.scan(folder, extensions=extensions)
                                       if not plan.is_new(c, since, offset))
            state["baseline_since"] = since
            state["baseline_exts"] = list(extensions)
            return
        done = plan.baselined_extensions(state)
        added = tuple(ext for ext in extensions if ext not in done)
        if not added or not folder.is_dir():
            return
        errors: list = []
        found = {c.relpath.casefold() for c in plan.scan(folder, errors, extensions=added)}
        if errors:
            return
        state["baseline"] = sorted(set(state.get("baseline") or ()) | found)
        state["baseline_exts"] = [ext for ext in plan.KNOWN_EXTENSIONS if ext in done or ext in added]

    def _added_baseline(self, state: dict, rule: dict, root: Optional[dict], whole: bool) -> None:
        """新任务（云端 ``existing`` 为真）后来多收的扩展名：此刻已在文件夹里的这类文件记进 ``added_baseline``、不处理，
        之后放进来的才处理。1.5 之前建的新任务（2026-10-12 第一批起）那时只收 PDF / 图片，升级后云端给推往 Express 的
        销售任务下发 POS 导出：文件夹里早就在的表格可能已手工录过，不能当 POS 导出再记一遍账（同老任务补拍底的理由）。

        ``whole``：本机第一次见到这条任务、或老任务换了文件夹转成新任务——收的扩展名都算一开始就收（建任务时网页数的
        「现有 N 份」含 POS 导出，到点一起处理），不拍底。已经收着的记在 ``seen_exts``；没读全就下一轮再补，补好之前
        这几种扩展名不扫（`plan.taken_extensions`）。"""
        extensions = plan.rule_extensions(rule)
        if whole:
            state["seen_exts"] = list(extensions)
            state.pop("added_baseline", None)
            return
        seen = plan.seen_extensions(state)
        kept = [ext for ext in extensions if ext in seen]
        added = tuple(ext for ext in extensions if ext not in seen)
        if added and root is not None:
            folder = plan.rule_folder(root["path"], rule.get("folder") or "")
            errors: list = []
            found = {c.relpath.casefold() for c in plan.scan(folder, errors, extensions=added)} \
                if folder.is_dir() else set()
            if folder.is_dir() and not errors:
                state["added_baseline"] = sorted(set(state.get("added_baseline") or ()) | found)
                kept = list(extensions)
        state["seen_exts"] = kept

    def _retry(self, client, cfg, ledger: Ledger, rule: dict, root: dict) -> bool:
        """上一次没传成的（网络断了、文件还没写完、服务端暂时不收）每一轮补传，不等到下个月。返回这一轮传过没有。"""
        pending = [row for row in ledger.files.values() if row.get("rule") == rule["name"] and row.get("retry")]
        if not pending:
            return False
        folder = plan.rule_folder(root["path"], rule.get("folder") or "")
        record = {"rule": rule["name"], "run_key": uuid.uuid4().hex, "trigger": "manual", "found": 0,
                  "new_files": 0, "skipped": 0, "upload_failed": 0, "note": "retry"}
        for row in pending:
            if self._stop.is_set():
                break
            path = folder.joinpath(*row["relpath"].split("\\"))
            try:
                stat = path.stat()
            except OSError:
                ledger.files.pop(ledger.file_key(rule["name"], row["relpath"]), None)
                continue
            candidate = plan.Candidate(row["relpath"], path, stat.st_size, stat.st_mtime, plan.created_at(stat))
            if not plan.is_stable(candidate, self._clock()):
                continue
            record["found"] += 1
            self._send(client, cfg, ledger, rule, root, folder, record["run_key"], candidate, record)
        if record["found"]:
            record["scanned"] = True
            ledger.runs.append(record)
        return bool(record["found"])

    def _pending(self, ledger: Ledger, rule: dict, candidate: plan.Candidate, reason: str) -> None:
        ledger.files[ledger.file_key(rule["name"], candidate.relpath)] = {
            "rule": rule["name"], "relpath": candidate.relpath, "retry": True, "reason": reason}

    def _run(self, client, cfg, ledger: Ledger, rule: dict, root: Optional[dict], due: plan.Due,
             offset: float) -> None:
        state = ledger.rule_state(rule["name"])
        # 同一个时刻 / 同一次立即运行上次中途停下了：接着用那一次的运行号（云端同一条运行记录、同一份简报），
        # 已传上的份数接着算（这次扫描时台账认得它们，不再传）。
        current = state.get("open_run")
        if not (isinstance(current, dict) and current.get("run_key") and current.get("slot") == due.slot
                and current.get("request") == due.request):
            current = {"run_key": uuid.uuid4().hex, "slot": due.slot, "request": due.request, "new_files": 0}
        state["open_run"] = current
        record = {"rule": rule["name"], "run_key": current["run_key"], "trigger": due.trigger, "found": 0,
                  "new_files": int(current.get("new_files") or 0), "skipped": 0, "upload_failed": 0}
        if due.slot is not None:
            # 这次运行属于哪个定时时刻（epoch 秒；立即运行没有）：云端按它把同一时刻的结果合成一份简报
            record["slot"] = due.slot

        def handled():
            # 只有这一次真的跑完才记「这个时刻处理过」；中途被拒（服务端暂时不认）、被叫停，下一轮重跑。
            if due.slot is not None:
                state["last_slot"] = due.slot
            if due.request is not None:
                state["last_request"] = due.request
            state.pop("open_run", None)

        try:
            self._run_folder(client, cfg, ledger, rule, root, record, handled)
        finally:
            current["new_files"] = record["new_files"]

    def _run_folder(self, client, cfg, ledger: Ledger, rule: dict, root: Optional[dict], record: dict,
                    handled: Callable[[], None]) -> None:
        state = ledger.rule_state(rule["name"])
        run_key = record["run_key"]
        if root is None:
            record.update(note="root_missing", scanned=True)
            ledger.runs.append(record)
            handled()
            return
        # 老任务的底；新任务只有后来多收的扩展名补拍的底
        baseline = set(state.get("baseline") or []) | set(state.get("added_baseline") or [])
        folder = plan.rule_folder(root["path"], rule.get("folder") or "")
        if not folder.is_dir():  # 规则的子文件夹被删 / 改名:记明原因(网页运行记录显示「文件夹不可用」)
            record.update(note="folder_missing", scanned=True)
            ledger.runs.append(record)
            handled()
            return
        activity("log_folder_run", lang=self._lang(), name=rule.get("title") or "")
        waiting: list[plan.Candidate] = []
        seen: set[str] = set()
        errors: list = []
        # 只扫已经收着的扩展名（新收的先补拍底，见 _baseline / _added_baseline）
        extensions = plan.taken_extensions(rule, state)
        for candidate in plan.scan(folder, errors, extensions=extensions):
            if self._stop.is_set():
                # 被叫停（只在两份文件之间）：不算处理过、不带 scanned，下次启动同一时刻接着跑（同一个运行号）
                record["note"] = "interrupted"
                ledger.runs.append(record)
                return
            seen.add(candidate.relpath.casefold())
            record["found"] += 1
            if candidate.relpath.casefold() in baseline:
                record["skipped"] += 1
                continue
            if ledger.known(rule["name"], candidate.relpath, candidate.size, candidate.mtime):
                continue
            if not plan.is_stable(candidate, self._clock()):
                waiting.append(candidate)
                continue
            self._send(client, cfg, ledger, rule, root, folder, run_key, candidate, record)
        for _ in range(UNSTABLE_ROUNDS):
            if not waiting or self._stop.wait(plan.STABLE_SECONDS):
                break
            again, waiting = waiting, []
            for candidate in again:
                fresh = next((row for row in plan.scan(candidate.path.parent, extensions=extensions)
                              if row.path == candidate.path), None)
                if fresh is None:
                    continue
                fresh = plan.Candidate(candidate.relpath, fresh.path, fresh.size, fresh.mtime, fresh.created)
                if plan.is_stable(fresh, self._clock()):
                    self._send(client, cfg, ledger, rule, root, folder, run_key, fresh, record)
                else:
                    waiting.append(fresh)
        if waiting:
            record["note"] = f"still_writing:{len(waiting)}"
            for candidate in waiting:
                self._pending(ledger, rule, candidate, "still_writing")
        # 整个文件夹都看过了：文件已不在的墓碑与「不收」记录清掉；这次读不了的文件夹 / 文件下面的照留（网络抖一下
        # 不把墓碑丢了），别处照清
        ledger.forget_missing(rule["name"], seen, _unreadable(folder, errors))
        record["scanned"] = True
        ledger.runs.append(record)
        handled()
        activity("log_folder_done", lang=self._lang(), name=rule.get("title") or "", n=record["new_files"],
                 fail=record["upload_failed"])

    def _send(self, client, cfg, ledger: Ledger, rule: dict, root: dict, folder: Path, run_key: str,
              candidate: plan.Candidate, record: dict) -> None:
        try:
            digest = plan.sha256_of(candidate.path)
            answer = client.begin({"rule": rule["name"], "run_key": run_key, "relpath": candidate.relpath,
                                   "filename": candidate.path.name, "byte_size": candidate.size, "sha256": digest})
            if answer.get("skip") or not answer.get("ok"):
                record["upload_failed"] += 1
                code = answer.get("code") or answer.get("skip") or ""
                log.info("文件夹录入不收 %s: %s", candidate.relpath, code)
                if answer.get("skip") or code in RETRY_CODES:
                    self._pending(ledger, rule, candidate, code)  # 暂时不收（负责人 / 规则状态）：之后补传
                else:
                    # 这份文件本身不收（类型、大小…）：记下大小与时间，文件不变就不再传。
                    ledger.files[ledger.file_key(rule["name"], candidate.relpath)] = {
                        "rule": rule["name"], "relpath": candidate.relpath, "size": candidate.size,
                        "mtime": candidate.mtime, "rejected": code}
                return
            row = {"rule": rule["name"], "relpath": candidate.relpath, "size": candidate.size,
                   "mtime": candidate.mtime, "sha256": digest, "entry": answer["entry"],
                   "root_id": root["id"], "folder": rule.get("folder") or "",
                   "month": plan.month_of(self._clock()), "state": answer.get("state") or "registered",
                   "delivered": {}, "paths": {}}
            if row["state"] == "registered":
                if answer.get("route") == "direct":
                    if not answer.get("uploaded"):
                        put_direct(answer["url"], answer.get("headers") or {}, candidate.path, cfg.backend_url)
                    done = client.finish(answer["entry"])
                else:
                    content = candidate.path.read_bytes()
                    if len(content) != candidate.size or hashlib.sha256(content).hexdigest() != digest:
                        record["upload_failed"] += 1
                        self._pending(ledger, rule, candidate, "changed")  # 读的时候文件变了：下一轮再传
                        return
                    done = client.finish(answer["entry"], content)
                if not done.get("ok"):
                    record["upload_failed"] += 1
                    self._pending(ledger, rule, candidate, done.get("code") or "finish")
                    return
                row["state"] = done.get("state") or "uploaded"
            elif row["state"] == "failed":
                row.update(finished=True, finished_at=self._clock(), closed_at=self._clock())
            ledger.files[ledger.file_key(rule["name"], candidate.relpath)] = row
            record["new_files"] += 1
        except Refused:
            self._pending(ledger, rule, candidate, "refused")
            raise
        except Exception:
            record["upload_failed"] += 1
            self._pending(ledger, rule, candidate, "error")
            log.exception("文件夹录入上传失败 %s", candidate.relpath)

    # ── 结果表格 ────────────────────────────────────────────────────────────

    def _deliver(self, client, ledger: Ledger, entries: dict, by_id: dict) -> None:
        index = ledger.entry_index() if entries else {}
        for name, status in entries.items():
            if self._stop.is_set():
                return
            row = index.get(name)
            if row is None or not isinstance(status, dict):
                continue
            row["state"] = status.get("state") or row.get("state")
            if status.get("finished") and not row.get("finished"):
                row["finished"], row["finished_at"] = True, self._clock()
            root = by_id.get(row.get("root_id"))
            complete = True
            for item in status.get("items") or []:
                history, revision = item.get("history"), int(item.get("revision") or 0)
                if not history or row["delivered"].get(history) == revision:
                    continue
                if root is None or not self._write(client, row, root, name, item, revision):
                    complete = False
            if status.get("settled") and complete:
                row["closed_at"] = self._clock()

    def _write(self, client, row: dict, root: dict, entry: str, item: dict, revision: int) -> bool:
        history = item["history"]
        try:
            answer = client.workbook(entry, history)
            if not answer.get("ok"):
                return False
            folder = plan.rule_folder(root["path"], row.get("folder") or "")
            previous = row["paths"].get(history)
            if previous:
                target = Path(previous)
            else:
                taken = {Path(path).name.casefold() for path in row["paths"].values()}
                result = plan.result_folder(answer.get("result_folder"))
                month_dir = folder / result / row["month"]
                if month_dir.is_dir():
                    taken |= {child.name.casefold() for child in month_dir.iterdir()}
                target = plan.result_path(folder, row["month"], answer.get("filename") or item.get("filename"),
                                          taken, result)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".part")
            temporary.write_bytes(base64.b64decode(answer["content"], validate=True))
            temporary.replace(target)
            row["paths"][history] = str(target)
            row["delivered"][history] = revision
            return True
        except Refused:
            raise
        except PermissionError as error:
            # 表格正被 Excel 打开着（Windows 不让覆盖）：下次同步再写。
            log.info("结果表格暂时写不进 %s: %s", history, error)
            return False
        except Exception:
            log.exception("结果表格写入失败 %s", history)
            return False


def _report_interval(cost: float, complete: bool) -> float:
    """根目录行多久重扫一次：一秒内看全了每轮都刷，更慢每 2 分钟，看不全每 10 分钟。"""
    if not complete:
        return FOLDER_REPORT_PARTIAL_SECONDS
    return 0.0 if cost <= FOLDER_REPORT_CHEAP_SECONDS else FOLDER_REPORT_SECONDS


def _unreadable(folder: Path, errors: list) -> set:
    """这次扫描读不了的文件夹 / 文件（相对规则文件夹、casefold）；说不清是哪里（错误没带路径、就是规则文件夹本身、
    不在规则文件夹下）记 ``""``（整个规则文件夹）。"""
    found = set()
    for error in errors:
        name = getattr(error, "filename", None)
        try:
            parts = Path(name).relative_to(folder).parts if name else ()
        except (TypeError, ValueError):
            parts = ()
        found.add("\\".join(parts).casefold())
    return found


def _covered(folder: str, scope) -> bool:
    """规则文件夹（比较键）落在留下的文件夹（比较键）之一里面。"""
    return any(not kept or folder == kept or folder.startswith(kept + "\\") for kept in scope)


def _notice(row) -> Optional[dict]:
    """一条对话提醒（字段 NOTICE_FIELDS，``view`` 可以没有）：形状不对就丢掉，不让一条坏数据拦住其余的。"""
    if not isinstance(row, dict) or any(field not in row for field in NOTICE_FIELDS if field not in NOTICE_OPTIONAL):
        return None
    notice_id, title, text, created, session, view = (row.get(field) for field in NOTICE_FIELDS)
    session = "" if session is None else session
    if not isinstance(notice_id, str) or not 0 < len(notice_id) <= 200 or not isinstance(title, str) \
            or not isinstance(text, str) or not (title or text) or not isinstance(session, str) \
            or isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created):
        return None
    # 页面写法认不得的（形状由 tomo_links.view_query 再核一次）当作没有：照旧打开定时任务页，不丢整条
    view = view[:40] if isinstance(view, str) else ""
    return {"id": notice_id, "title": title[:200], "text": text[:4000], "created": float(created),
            "session": session[:200], "view": view}


def _folder_health(rule: dict, root: Optional[dict]) -> str:
    """这条规则的文件夹在不在:授权文件夹没了(移除 / 改名)、规则的子文件夹没了,还是正常。"""
    if root is None:
        return "root_missing"
    try:
        exists = plan.rule_folder(root["path"], rule.get("folder") or "").is_dir()
    except (OSError, ValueError):
        exists = False
    return "ok" if exists else "folder_missing"


def _folder_member(cfg) -> bool:
    """MR.ERP 的小助手成员（只读文件夹，2026-10-09）。"""
    return getattr(cfg, "kind", "express") == "folder"
