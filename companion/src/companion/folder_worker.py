# -*- coding: utf-8 -*-
"""文件夹自动录入的后台线程：按云端下发的规则定时扫本机文件夹、上传新文件、把结果表格放回文件夹。

与写账套的那条线完全分开（影响评估 2026-10-06）：
- 自己一条守护线程、自己的 httpx 客户端，不进 PollWorker / 写入线、不碰看门狗的存活时标，
  上传再慢也不会被当成「卡死」重启，也不拖慢 DBF 写入；
- 本机没给任何连接设允许读取的根目录时，这个线程根本不启动（零线程、零网络、零文件）；
- 每个连接的机器请求一个接一个发（不与心跳 / 领任务争连接锁），原件一份一份传，不占满办公室上行；
- 自动推送、写账套仍走原来的云端队列与 PollWorker，这里一行不碰。

同步顺带的几件事（2026-10，CC 闭环第一批；协议号不变，旧服务端不认的键照旧忽略）：
- 各文件夹现有几份（根目录行的 ``counts`` / ``partial``）与各任务待处理几份（``rule_pending``），给网页卡片与任务列表；
- 云端随回复带来的对话提醒（``notices``，请求带 ``notices_after`` 才有）交给托盘弹系统通知；
- 回复里的最新版本（``companion_latest``）：只读文件夹的成员没有心跳，也从这里收到新版。
"""

from __future__ import annotations

import base64
import hashlib
import math
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import folder_plan as plan
from .queue_client import _device_name
from .folder_client import FolderClient, Refused, put_direct
from .folder_store import Ledger, connection_key, roots
from .logger import activity, get_logger
from .version import VERSION

log = get_logger(__name__)

IDLE_SECONDS = 60
# 根目录下的文件夹名与各文件夹现有几份一起重扫，间隔看上一次花了多久（2026-10）：一秒内看全就每轮都刷（放进文件后一两分钟
# 网页就看得到）；更慢就每 2 分钟（原只报文件夹名时的节奏，10-09 由 15 分钟改来：新建的子文件夹要等很久才出现在网页下拉）；
# 数不完（太大、网络共享太慢，见 folder_plan.SURVEY_*）就每 10 分钟，免得每分钟把整棵树数一遍。
FOLDER_REPORT_SECONDS = 2 * 60
FOLDER_REPORT_CHEAP_SECONDS = 1.0
FOLDER_REPORT_PARTIAL_SECONDS = 10 * 60
UNSTABLE_ROUNDS = 3
# 服务端「这会儿不收、过后会收」的原因：留着补传。其余（类型、大小…）是这份文件本身不收，不再传。
RETRY_CODES = {"cowork.folder.owner_not_ready", "cowork.folder.rule_disabled", "cowork.folder.disabled",
               "cowork.folder.step_error"}
# 同步回复里的对话提醒：字段与服务端 folder_intake/contracts.NOTICE_FIELDS 逐字一致（tests/test_companion_contract.py 守住）。
NOTICE_FIELDS = ("id", "title", "text", "created", "session")
NOTICE_LIMIT = 20


class FolderIntake:
    def __init__(self, configs: Callable[[], Iterable], *, client_factory=FolderClient,
                 clock: Callable[[], float] = time.time, lang: Callable[[], str] = lambda: "",
                 on_status: Optional[Callable[[str, str], None]] = None,
                 on_notices: Optional[Callable[[str, list], None]] = None,
                 on_update: Optional[Callable[[str, str], None]] = None,
                 timer: Callable[[], float] = time.monotonic) -> None:
        self._configs = configs
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
        self._thread: Optional[threading.Thread] = None
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
        self._thread = threading.Thread(target=self._loop, name="pearnly-folder-intake", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def running(self) -> bool:
        """线程还在（停下要等它做完手上这一步，例如一份正在上传的文件）。"""
        return bool(self._thread and self._thread.is_alive())

    def _active(self) -> list:
        try:
            return [cfg for cfg in list(self._configs() or []) if not getattr(cfg, "paused", False)
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
                try:
                    self.round(cfg, key)
                    self._status(cfg, "idle")
                except Refused as error:
                    log.info("文件夹录入本轮跳过: %s", error)
                    self._status(cfg, "offline")
                except Exception:
                    log.exception("文件夹录入本轮失败")
                    self._status(cfg, "offline")
            self._stop.wait(IDLE_SECONDS)

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
            local_roots = roots(key)
            # device：本机名，网页文件夹下拉在有几台小助手时用它区分电脑（MR.ERP 任务可借用任一台，2026-10-09）。
            # rule_pending：上一轮算好的各任务待处理数（与 rule_health 同一节奏）；notices_after：对话提醒收到哪一刻为止。
            response = client.sync({"version": VERSION, "device": _device_name(), "roots": self._report(key, local_roots),
                                    "runs": list(ledger.runs), "watch": ledger.watch(),
                                    "rule_health": self._health.get(key, {}),
                                    "rule_pending": self._rule_pending.get(key, {}),
                                    "notices_after": ledger.notices_after})
            if not response.get("ok"):
                return
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
                if "last_request" not in state:
                    state["last_request"] = 0
                root = by_id.get(rule.get("root_id"))
                if rule.get("existing"):
                    # 新任务：不拍底，到点处理文件夹里本任务还没处理过的全部文件（改过文件夹的老任务也转成这样，旧底丢掉）。
                    state.pop("baseline", None)
                    state.pop("baseline_since", None)
                else:
                    self._baseline(state, rule, root, offset)
                due = plan.due(rule, state, now + offset)
                if due:
                    self._run(client, cfg, ledger, rule, root, due, offset)
                    ledger.save()
                    touched = True
                elif root is not None:
                    touched = self._retry(client, cfg, ledger, rule, root) or touched
            self._deliver(client, ledger, response.get("entries") or {}, by_id)
            if not self._stop.is_set():
                self._count_pending(key, ledger, response.get("rules") or [], touched)
        finally:
            ledger.save()
            client.close()

    def _report(self, key: str, local_roots: list) -> list:
        """根目录、下级文件夹名与各文件夹现有几份（``counts`` / ``partial``）；间隔按上一次花的时间定（见
        FOLDER_REPORT_*），根目录变了立即重扫。几个根目录共用一份时限与条数（folder_plan.SURVEY_*），用完就停：
        没轮到的根目录不带 ``counts``（网页不显示几份，不用 0 冒充）。"""
        now = self._clock()
        ids = [row["id"] for row in local_roots]
        cached = self._folders.get(key)
        if cached and cached["ids"] == ids and now - cached["at"] < _report_interval(cached["cost"], cached["complete"]):
            return cached["report"]
        started = self._timer()
        deadline, budget = started + plan.SURVEY_SECONDS, [plan.SURVEY_ENTRIES]
        report, seen, complete = [], {}, True
        for row in local_roots:
            path = Path(row["path"])
            folders = plan.folders_of(path)
            line = {"id": row["id"], "label": row["label"], "folders": folders, "share": plan.share_path(row["path"])}
            if complete:
                try:
                    files, complete = plan.survey(path, deadline=deadline, budget=budget, clock=self._timer)
                except Exception:
                    log.exception("文件夹计数失败")
                    complete = False
                else:
                    line["counts"] = plan.folder_counts(files, ["", *folders])
                    line["partial"] = not complete
                    seen[row["id"]] = files
            report.append(line)
        self._folders[key] = {"at": now, "ids": ids, "report": report, "cost": self._timer() - started,
                              "complete": complete}
        self._seen[key] = seen
        self._surveyed[key] = self._surveyed.get(key, 0) + 1
        return report

    def _count_pending(self, key: str, ledger: Ledger, rules: list, touched: bool) -> None:
        """各任务还有几份没处理（不在台账、也不在老任务的拍底里），下一次同步报给云端。只在文件夹重新数过、这一轮传过
        文件或任务变了时重算（文件多时逐份比对台账也要时间）；根目录没数到的任务不报（网页不显示，不用 0 冒充）。"""
        basis = (self._surveyed.get(key, 0),
                 tuple((rule.get("name"), rule.get("root_id"), rule.get("folder"), bool(rule.get("existing")),
                        rule.get("active_since"), (ledger.rules.get(rule.get("name")) or {}).get("baseline_since"))
                       for rule in rules))
        if not touched and self._rule_pending_basis.get(key) == basis:
            return
        self._rule_pending_basis[key] = basis
        seen = self._seen.get(key) or {}
        pending = {}
        for rule in rules:
            name, files = rule.get("name"), seen.get(rule.get("root_id"))
            if not name or files is None:
                continue
            state = ledger.rules.get(name) or {}
            if rule.get("existing"):
                baseline: set = set()
            elif state.get("baseline_since") == float(rule.get("active_since") or 0):
                baseline = set(state.get("baseline") or [])
            else:
                continue  # 老任务还没拍底（负责人还没准备好，规则没跑过）：不知道几份
            pending[name] = plan.pending_count(
                files, rule.get("folder") or "",
                lambda relpath, size, mtime, rule_name=name: ledger.known(rule_name, relpath, size, mtime) is not None,
                baseline)
        self._rule_pending[key] = pending

    def _notices(self, cfg, ledger: Ledger, items) -> None:
        """云端随同步回复带来的对话提醒（请求带了 notices_after 才有；最多 20 条、12 小时内，按时间先后）：合形状的
        交给托盘弹系统通知（托盘按 ``notice:<id>`` 去重，对话页实时收到的同一条不再弹），并记下收到哪一刻为止。"""
        if not isinstance(items, list):
            return
        fresh = [notice for notice in (_notice(row) for row in items[:NOTICE_LIMIT]) if notice]
        if not fresh:
            return
        ledger.notices_after = max(ledger.notices_after, *(notice["created"] for notice in fresh))
        ledger.save()  # 马上记下：后面的运行可能要传很久，中途退出也不再重弹
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
        包括同一个盘里「剪切移动」进来的旧文件（Windows 移动不改创建时间，光看创建时间会漏掉）。"""
        since = float(rule.get("active_since") or 0)
        if root is None or state.get("baseline_since") == since:
            return
        folder = plan.rule_folder(root["path"], rule.get("folder") or "")
        state["baseline"] = sorted(c.relpath.casefold() for c in plan.scan(folder)
                                   if not plan.is_new(c, since, offset))
        state["baseline_since"] = since

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
        run_key = uuid.uuid4().hex
        record = {"rule": rule["name"], "run_key": run_key, "trigger": due.trigger, "found": 0, "new_files": 0,
                  "skipped": 0, "upload_failed": 0}
        if due.slot is not None:
            # 这次运行属于哪个定时时刻（epoch 秒；立即运行没有）：云端按它把同一时刻的结果合成一份简报
            record["slot"] = due.slot
        state = ledger.rule_state(rule["name"])

        def handled():
            # 只有这一次真的跑完才记「这个时刻处理过」；中途被拒（服务端暂时不认）下一轮重跑。
            if due.slot is not None:
                state["last_slot"] = due.slot
            if due.request is not None:
                state["last_request"] = due.request

        if root is None:
            record.update(note="root_missing", scanned=True)
            ledger.runs.append(record)
            handled()
            return
        baseline = set(state.get("baseline") or [])
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
        complete = True
        for candidate in plan.scan(folder, errors):
            if self._stop.is_set():
                complete = False
                break
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
                fresh = next((row for row in plan.scan(candidate.path.parent)
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
        if complete and not errors:
            # 整个文件夹都看过了：文件已不在的墓碑与「不收」记录清掉（读不全时不清，免得网络抖一下就把墓碑丢了）
            ledger.forget_missing(rule["name"], seen)
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
        for name, status in entries.items():
            if self._stop.is_set():
                return
            row = ledger.by_entry(name)
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


def _notice(row) -> Optional[dict]:
    """一条对话提醒（字段 NOTICE_FIELDS）：形状不对就丢掉，不让一条坏数据拦住其余的。"""
    if not isinstance(row, dict) or any(field not in row for field in NOTICE_FIELDS):
        return None
    notice_id, title, text, created, session = (row[field] for field in NOTICE_FIELDS)
    session = "" if session is None else session
    if not isinstance(notice_id, str) or not 0 < len(notice_id) <= 200 or not isinstance(title, str) \
            or not isinstance(text, str) or not (title or text) or not isinstance(session, str) \
            or isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created):
        return None
    return {"id": notice_id, "title": title[:200], "text": text[:4000], "created": float(created),
            "session": session[:200]}


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
