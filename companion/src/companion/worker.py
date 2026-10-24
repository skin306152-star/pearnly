# -*- coding: utf-8 -*-
"""出站拉取运行时:轮询循环骨架 + 单票 DBF 直写 + 周期心跳。

托盘 `PollWorker` 与 CLI `step_poll_loop` 共用 `run_poll_loop` 这层壳(消两份轮询骨架);
差异(状态信号 vs 日志)由回调注入,核心循环只一份。每张票写盘后即重建索引(per-write
reindex)→ Express 立即可查,无需任何额外整理步骤。
"""

from __future__ import annotations

import datetime
import ntpath
import time
from dataclasses import replace
from typing import Callable, Optional

from .config import CompanionConfig
from .logger import activity, get_logger
from . import projection_refresh, task_journal
from .queue_client import QueueClient, QueueClientConfig

log = get_logger(__name__)


def _norm_path(value: object) -> str:
    path = str(value or "").strip().replace("/", "\\").rstrip("\\")
    return ntpath.normcase(ntpath.normpath(path)) if path else ""


ERR_ACCOUNT_UNREACHABLE = "ACCOUNT_SET_UNREACHABLE"
# 写前核文件号（多台电脑，ADR-021，1.7.0 起）：服务端随任务下发登记里这份账套的文件号 ``account_uid``；这台电脑上找不到
# 这份账套、或找到的目录文件号不同（换了一份、备份副本）→ 不写，按「等账套」回报这个码（与服务端
# queue_contracts.ERR_ACCOUNT_UID_MISMATCH 同名）：服务端把这台对这份账套的可达标 stale、要整份重报，单子交给别的电脑。
ERR_ACCOUNT_UID_MISMATCH = "ERR_ACCOUNT_UID_MISMATCH"
# 账套暂时读不到时就地再探的等待（秒）：网络共享抖一下多半几秒内恢复；一直读不到时每轮约 15 秒回报一次，
# 不对服务端空转（「等账套」不烧重试次数，下一轮马上又会领到这张）。
UNREACHABLE_RECHECK_SECONDS = (5.0, 5.0, 5.0)
# 文件号对不上回到排队后本机的冷却：同一张单短时间内又派回这台（服务端还没把这台对这份账套标 stale）时，回报后先歇一会儿
# 再领，不原地空转、不每轮都整套重扫。第一次对不上不等（尽快交给别的电脑）。
UID_MISMATCH_COOLDOWN_SECONDS = 20.0
UID_MISMATCH_REPEAT_WINDOW = 120.0
_uid_mismatch_seen: dict = {}   # 任务号 → 上次在这台对不上的时刻（monotonic）


class AccountUnreachable(Exception):
    """账套登记了、也授权了，只是这会儿读不到（登记表打不开 / 账套文件夹连不上）：稍后自动重试，不算失败。

    2026-09-28 录制实测：上一张刚写完、网络共享卡了约 80 秒，下一张被判「账套不可用」直接失败，
    服务端连着重试 3 次也在这几秒里用完，转了人工。"""


def _probe_task_account(root: str, requested_set: str):
    """在授权根目录里找这份账套：找到且可写 → 账套；没登记 / 文件夹在但不可写 → None（要人处理）；
    读不到 → AccountUnreachable。"""
    import os

    from .account_probe import probe_account_sets

    try:
        accounts = probe_account_sets(root)
    except Exception as exc:
        raise AccountUnreachable(f"account registry unreadable: {exc}") from exc
    selected = next(
        (
            account
            for account in accounts
            if _norm_path(account.path) == _norm_path(requested_set)
        ),
        None,
    )
    if selected is None or selected.writable:
        return selected
    if not os.path.isdir(str(selected.path or "")):
        raise AccountUnreachable(f"account folder unreachable: {selected.path}")
    return None


def _task_settings(cfg: CompanionConfig, payload: dict):
    """Resolve a task-bound account only from roots and SCCOMP rows visible on this PC.

    None = 这台电脑上没有这份账套或没有写入权限（要人处理）；AccountUnreachable = 暂时读不到（稍后重试）。"""
    from .secret_store import load_express_settings

    current = load_express_settings(cfg)
    requested_set = str((payload or {}).get("account_set") or "").strip()
    requested_root = str((payload or {}).get("account_root") or "").strip()
    if not requested_set:
        return current
    if not requested_root:
        return (
            current
            if _norm_path(requested_set) == _norm_path(current.account_set)
            else None
        )
    roots = [
        current.express_root,
        *(getattr(cfg, "authorized_roots", None) or ()),
    ]
    allowed_root = next(
        (root for root in roots if _norm_path(root) == _norm_path(requested_root)), None
    )
    if not allowed_root:
        return None
    for wait in (*UNREACHABLE_RECHECK_SECONDS, None):
        try:
            selected = _probe_task_account(allowed_root, requested_set)
            break
        except AccountUnreachable:
            if wait is None:
                raise
            time.sleep(wait)
    if selected is None:
        return None
    return replace(
        current,
        express_root=str(allowed_root),
        account_dir=str(selected.path),
        account_company=str(selected.company),
        account_set=str(selected.path),
        account_set_row=int(selected.row),
    )


def task_account_dir(cfg: CompanionConfig, payload: dict) -> str:
    """这张单要写的账套目录（有人值守时先探它有没有被 Express 占用，gui_tray_workers）。

    任务带了账套：只在它的数据根是这台电脑授权过的根目录时才认（与 ``_task_settings`` 同一条规则，不探服务端给的任意路径），
    就是那份账套的目录；没带账套（旧任务）用这个成员的默认账套。认不出回空串（不探，交给写入时如实回报）。"""
    from .secret_store import load_express_settings

    current = load_express_settings(cfg)
    requested_set = str((payload or {}).get("account_set") or "").strip()
    requested_root = str((payload or {}).get("account_root") or "").strip()
    if not requested_set:
        return current.account_dir or ""
    if not requested_root:
        return requested_set if _norm_path(requested_set) == _norm_path(current.account_set) else ""
    roots = [current.express_root, *(getattr(cfg, "authorized_roots", None) or ())]
    if not any(_norm_path(root) == _norm_path(requested_root) for root in roots if root):
        return ""
    return requested_set


def uid_mismatch(expected: object, account_dir: str, uid_of=None) -> bool:
    """服务端下发的文件号与本机这份账套目录的文件号确实不同。本机读不出文件号（不是 Windows、文件系统不给）不算不同：
    核不了就照旧按路径写（同旧版）。"""
    want = str(expected or "").strip().lower()
    if not want or not account_dir:
        return False
    if uid_of is None:
        from .device_facts import file_uid as uid_of
    actual = str(uid_of(account_dir) or "").strip().lower()
    return bool(actual) and actual != want


def _note_uid_mismatch(task_id: str, now: Optional[float] = None) -> bool:
    """记下这张单在这台对不上文件号；短时间内第二次起回 True（要本机冷却）。"""
    moment = time.monotonic() if now is None else now
    for key, seen in list(_uid_mismatch_seen.items()):
        if moment - seen > UID_MISMATCH_REPEAT_WINDOW:
            _uid_mismatch_seen.pop(key, None)
    repeat = task_id in _uid_mismatch_seen
    _uid_mismatch_seen[task_id] = moment
    return repeat


def _opening_items(payload: dict):
    """Pearnly 补期初卡透传的期初行 → set_opening_stock 期望的形状。

    Pearnly 端键名是 `key`(=品名),映射成 `name`;日期 'YYYY-MM-DD' 解析成 date
    (解析不出留 None → set_opening_stock 回落单据日期)。空/缺 → None(该票无期初,走常规过账)。
    """
    raw = (payload or {}).get("opening_stock")
    if not raw:
        return None
    out = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        name = str(it.get("key") or it.get("name") or "").strip()
        if not name:
            continue
        ds = str(it.get("date") or "").strip()[:10]
        try:
            d = datetime.date.fromisoformat(ds) if ds else None
        except ValueError:
            d = None
        out.append(
            {
                "name": name,
                "qty": it.get("qty"),
                "unit_cost": it.get("unit_cost"),
                "unit": it.get("unit") or "",
                "date": d,
            }
        )
    return out or None


def _handle_dbf_task(
    cfg: CompanionConfig, qc: QueueClient, task, index_batch=None
) -> None:
    """DBF 直写一张票:先验 payload_version,再按 direction 路由进项/销项,成功回 DOCNUM,
    失败如实 ack。版本不支持 → 诚实失败,绝不落 DBF 一行(见 payload_contract)。

    index_batch 非空 → 主档(STMAS/STCRD/ISACC/ARMAS/APMAS)索引延到队列空一次性重建
    (逐票只重建交易表·见 cdx_reindex.IndexBatch);为空 → 逐票全量重建(旧行为·测试/headless)。
    """
    from .dbf_writer import write_purchase
    from .dbf_sales import write_sale
    from .purchase_adapter import allowed_account_sets
    from .payload_contract import SUPPORTED_PAYLOAD_VERSIONS, check_payload_version

    payload = task.payload or {}
    version_err = check_payload_version(payload)
    if version_err:
        got = payload.get("payload_version", 1)
        supported = sorted(SUPPORTED_PAYLOAD_VERSIONS)
        msg = (
            f"payload_version {got!r} is not supported by this CC Desktop version "
            f"(supports {supported}); update CC Desktop to the latest version"
        )
        log.warning(
            f"✗ dbf task {task.id} unsupported payload_version: got={got} supported={supported}"
        )
        qc.ack_failure(
            task.id,
            msg,
            error_code=version_err,
            meta={"got": got, "supported": supported},
        )
        return "failed"

    # 同一个任务上次已经写进去、只是回执没送到（断网 / 关机）：照上次的回执回报，不写第二张。
    # 只认任务号，会计再推一次是新任务、照写（不查重，见 task_journal）。放在找账套之前：写过的就是写过了，
    # 这会儿账套找不到、文件号对不上都不能让它回到排队交给别的电脑再写一遍。
    done = task_journal.written(task.id)
    if done is not None:
        log.info(f"✓ dbf task {task.id} already written → {done.get('docnum')}")
        qc.ack_success(
            task.id,
            extracted_invoice_no=done.get("docnum"),
            line_modes=done.get("line_modes"),
            meta={**(done.get("meta") or {}), "written": False, "duplicate_skipped": True},
        )
        return None
    expected_uid = str(payload.get("account_uid") or "").strip()
    # 方法无关「读设置」seam:DBF 直写只需所选账套整组(不需登录密码)。
    try:
        st = _task_settings(cfg, payload)
    except AccountUnreachable as exc:
        # 暂时读不到：按「等账套」回报（不烧重试次数），连上后自动再推，不落 DBF 一行。
        log.warning(f"… dbf task {task.id} account unreachable, retry later: {exc}")
        qc.ack_failure(
            task.id,
            "the account set cannot be reached right now",
            error_code=ERR_ACCOUNT_UNREACHABLE,
            outcome="waiting_lock",
        )
        return "waiting_lock"
    # 多台电脑（1.7.0 起）：服务端是按这台电脑报过「够得到这份账套」才交过来的。这台上找不到它、或找到的是另一份
    # （文件号不同）→ 不写，回到排队交给别的电脑（不再转人工）。旧服务端不带 account_uid，照旧。
    if expected_uid and (st is None or uid_mismatch(expected_uid, st.account_dir)):
        log.warning(f"… dbf task {task.id} account uid mismatch on this PC, back to the queue")
        from . import master_refresh

        repeat = _note_uid_mismatch(str(task.id))
        if not repeat:
            master_refresh.rescan_soon()  # 服务端要这台整份重报：报上去的文件号得是现在的
        qc.ack_failure(
            task.id,
            "the account set on this computer is not the registered one",
            error_code=ERR_ACCOUNT_UID_MISMATCH,
            outcome="waiting_lock",
        )
        if repeat:
            time.sleep(UID_MISMATCH_COOLDOWN_SECONDS)   # 租约已放回：别的电脑照常能领，这台歇一会儿
        return "waiting_lock"
    # fail-safe:所选账套不齐(缺目录或账套名)→ 拒写,绝不瞎写。
    if st is None or not st.has_selection():
        log.warning(f"✗ dbf task {task.id} account not registered or not writable on this PC")
        qc.ack_failure(
            task.id,
            "task account is not registered, writable, or available on this PC",
            error_code="NO_ACCOUNT_SELECTION",
        )
        return "failed"
    # 方向第一类公民(09 铁律):标签定死表族,绝不串。
    direction = str(payload.get("direction") or "purchase").strip().lower()
    # 三重一致性闸期望值全来自客户所选账套(配对锁定):账套名 + 目录 + 公司。
    common = dict(
        # 部门码:账套设置里选一次、随载荷下发(2026-09-28 压测:原账 SUTI、推送为空);没下发用本机配置。
        depcod=str(payload.get("depcod") or "").strip() or cfg.default_depcod,
        allowed_sets=allowed_account_sets(st.account_set),
        selected_dir=st.account_dir,
        expected_company=st.account_company,
        index_batch=index_batch,
    )
    # 同一份账套可能还有另一个站点的 CC 桌面版在写（Cowork 与 ERP 各派单，ADR-025）：写期间锁住账套目录里的锁文件，
    # 锁不到就按「等账套」回报（不算失败），过一会儿服务端再派
    from .account_write_lock import ERR_WRITER_BUSY, Busy, held

    try:
        with held(st.account_dir):
            if direction == "sales":
                # 会计补的期初(库存卖但客户账里此品零库存 → 先垫库存再过账)· 与销售同事务原子成败。
                res = write_sale(
                    payload, st.account_dir, opening_stock=_opening_items(payload), **common
                )
            else:
                res = write_purchase(payload, st.account_dir, **common)
    except Busy:
        log.info(f"… dbf task {task.id} another computer is writing this account set, retry later")
        qc.ack_failure(
            task.id,
            "another computer is writing this account set",
            error_code=ERR_WRITER_BUSY,
            outcome="waiting_lock",
        )
        return "waiting_lock"
    meta = _push_meta(res, direction, st.account_dir)
    if res.ok:
        log.info(f"✓ dbf task {task.id} → {res.docnum}")
        task_journal.remember(
            task.id, {"docnum": res.docnum, "line_modes": res.line_modes, "meta": meta}
        )
        qc.ack_success(
            task.id,
            extracted_invoice_no=res.docnum,
            line_modes=res.line_modes,
            meta=meta,
        )
        activity(
            "log_recorded",
            lang=cfg.ui_lang,
            inv=res.docnum or str(task.invoice_no or "?"),
        )
        return None
    outcome = _outcome_for(res.error_code)
    log.warning(f"✗ dbf task {task.id} {outcome}: {res.error_code} · {res.error}")
    qc.ack_failure(
        task.id, res.error, error_code=res.error_code, outcome=outcome, meta=meta
    )
    activity("log_record_fail", lang=cfg.ui_lang, err=res.error or res.error_code)
    return outcome  # waiting_lock 等 → 调用方据此设托盘状态(占用≠失败)


def erp_scope(cfg, rows: list) -> list:
    """ERP 成员（共用通道）只报归这个 ERP 的账套（1.12.0）：只报所选数据根（``erp_roots``）里的账套，选了哪些报哪些（用户 10-24：
    「只报 CC 里勾选的数据根」）。不再按同税号把别的年度文件夹带进来——白天局域网服务器上线后会把没选的 S 盘真账也报上去、
    在 ERP 里建出只有 S 盘账套的公司（10-24 验收）。没选 = 一份都不报。Cowork 成员照旧整份报。"""
    from . import channel
    from .express_machine import under

    if getattr(cfg, "channel", channel.COWORK) != channel.CORE:
        return rows
    # 勾的可以是年度文件夹，也可以是上一级位置（C:\PearnlyExpress）：位置下面以后新建的年度文件夹照样报（主会话 10-24）
    scope = [root for root in (getattr(cfg, "erp_roots", None) or ()) if root]
    return [row for row in rows if any(under(str(row.get("root") or ""), place) for place in scope)]


def _outcome_for(error_code: Optional[str]) -> Optional[str]:
    """DBF 写错误码 → 云端 V3 outcome(细粒度状态)。未映射 → None(走默认 failed)。"""
    from . import stock_template
    from .dbf_writer import ERR_INDEX, ERR_WAITING_LOCK
    from .dbf_sales import ERR_CUSTOMER_AMBIGUOUS, ERR_CUSTOMER_DUP
    from .dbf_writer import (
        ERR_FIELD_WIDTH,
        ERR_LOCATION,
        ERR_SUPPLIER_AMBIGUOUS,
        ERR_SUPPLIER_DUP,
    )

    return {
        ERR_WAITING_LOCK: "waiting_lock",  # Express 占用 → 重试不烧次数
        ERR_INDEX: "rolled_back",  # 写了一半已恢复备份
        ERR_SUPPLIER_DUP: "needs_review",  # 疑似重复供应商 → 人工确认
        ERR_CUSTOMER_DUP: "needs_review",
        # 同税号多档定不出 / 字段超宽:重试结论也一样,要人选档或改票(2026-09-28 压测:先按普通失败重试 3 次)。
        ERR_CUSTOMER_AMBIGUOUS: "needs_review",
        ERR_SUPPLIER_AMBIGUOUS: "needs_review",
        ERR_FIELD_WIDTH: "needs_review",
        # 仓库码不在这套账的仓库代码表里:重试结论一样,要人改单或在 Express 建仓
        ERR_LOCATION: "needs_review",
        # 新建库存品的存货科目组不合格 / 没配:要人选一次组(1.11.0)
        stock_template.ERR_STOCK_GROUP: "needs_review",
        # ERP 的单写进已关账的会计期:重试结论一样(1.12.0)
        "PERIOD_LOCKED": "needs_review",
    }.get(error_code or "")


def _push_meta(res, direction: str, account_dir: str) -> dict:
    """构造回传云端的富元数据(诚实展示·见 doc32 §6)。"""
    from .version import VERSION

    from . import dbf_schema as S

    m = {
        "companion_version": VERSION,
        "doc_type": "sales" if direction == "sales" else "purchase",
        "account_dir": str(account_dir or ""),
    }
    # 配置里的路径若会被系统改写落点(UNC 少反斜杠→写进本机 C: 影子目录),上报真实落点。
    # 一致时不报,保持 meta 精简;写入闸已先行拒绝这种路径,这里只为留痕可诊断。
    resolved = S.path_ambiguity(account_dir)
    if resolved:
        m["account_dir_resolved"] = resolved
    if res.docnum:
        m["docnum"] = res.docnum
    # ERP 里实际用的客户/供应商码。导出复核表要靠它:会计清理误建的档、或核对配对
    # 对不对,光有"新建/复用"没有码没法动手。
    party = str(getattr(res, "party_code", "") or "").strip()
    if party:
        m["party_code"] = party
    # 「写成功但有一部分没写」必须自己说出来:会计期间判不出时应收/应付月桶不写,对账单会少
    # 一笔而试算表照样平 —— 不报就是静默漏账,几个月后才由会计对账发现。
    notes = getattr(res, "notes", None)
    if notes:
        m["notes"] = dict(notes)
    # 「到底进没进去」的唯一判据(同一任务重发、上次已写过时为 False,见 task_journal)。
    m["written"] = bool(res.written)
    # 失败时的结构化现场证据(读回校验/明细不符)→ 云端据此定位卡在哪个表哪一行,不用
    # 再让会计把红字一个字一个字抄回来。成功路径恒无(见 DbfWriteResult.evidence)。
    if getattr(res, "evidence", None):
        m["evidence"] = res.evidence
    if res.ok:
        m["index_requery"] = True  # write_* 仅在 _verify_index 回查命中后才 ok
        key = "created_customer" if direction == "sales" else "created_supplier"
        m[key] = bool(getattr(res, "created_party", False))
        # True = 同一任务重发、上次已写过,本次未重写(docnum 是上次写的那张)。
        m["duplicate_skipped"] = bool(getattr(res, "skipped", False))
        # 写完顺手核对(数字会计员第 3 期第 6 条):{state, fields, party}。随 meta 进 task_journal,重发时照样带回。
        if getattr(res, "verify", None):
            m["verify"] = dict(res.verify)
    if (getattr(res, "error_code", "") or "") in (
        "CDX_REINDEX_FAILED",
        "ACCOUNT_BUSY_LOCKED",
    ):
        m["rolled_back"] = bool(getattr(res, "backup_dir", None))
    return m


def route_task(cfg: CompanionConfig, qc: QueueClient, task, index_batch=None):
    """单票路由:全部走 DBF 直写(采购/销项按 direction 标签在 _handle_dbf_task 内分表)。
    返回 _handle_dbf_task 的 outcome(None=写成功 · 非空=失败/等待)。"""
    return _handle_dbf_task(cfg, qc, task, index_batch=index_batch)


def _sync_account_set(cfg: CompanionConfig, hb: dict) -> None:
    """以云端为准同步账套名(C3 期望值·与 payload.account_set 同源);云端没回则不动本地所选。"""
    if not isinstance(hb, dict):
        return
    new_set = str(hb.get("account_set") or "").strip()
    if new_set and new_set != (cfg.account_set or ""):
        cfg.update(account_set=new_set).save()
        log.info("[heartbeat] 账套名同步自云端 · account_set=%s", new_set)


def _sync_connection_identity(cfg: CompanionConfig, hb: dict) -> None:
    connection = hb.get("connection") if isinstance(hb, dict) else None
    if not isinstance(connection, dict):
        return
    changes = {
        "endpoint_id": str(connection.get("endpoint_id") or "").strip(),
        "endpoint_name": str(connection.get("endpoint_name") or "").strip(),
        "pearnly_account": str(connection.get("pearnly_account") or "").strip(),
    }
    if any(getattr(cfg, key, "") != value for key, value in changes.items()):
        cfg.update(**changes).save()


def _remember_roots(cfg: CompanionConfig, authorized: list, found) -> None:
    """「重新读取」找到的新年度文件夹记进授权名单:以后照常扫描,推送时也认。"""
    if not isinstance(found, list):
        return
    known = {_norm_path(root) for root in authorized}
    added = [str(root) for root in found if str(root or "").strip() and _norm_path(root) not in known]
    if added:
        cfg.update(authorized_roots=[*authorized, *added]).save()
        log.info("[heartbeat] 新授权年度文件夹 %d 个", len(added))


def _offer_update(hb, on_update_available) -> None:
    """服务端随心跳回复带来我们最新发布的版本（`companion_latest`）：比本机新就交给托盘提示并更新。

    不另外定时去查（用户 2026-10-09：不要定时任务做更新检查，发布后客户端能收到就行）；心跳本来就每分钟一次。"""
    if on_update_available is None or not isinstance(hb, dict):
        return
    latest = hb.get("companion_latest")
    if not isinstance(latest, dict):
        return
    from .version import VERSION, version_tuple

    version = str(latest.get("version") or "").strip()
    url = str(latest.get("url") or "").strip()
    if version and url and version_tuple(version) > version_tuple(VERSION):
        on_update_available({"version": version, "url": url})


_CATALOG_INTERVAL_S = 1800  # 目录+指纹上报节流(慢于心跳 · 上万条不必每拍重扫重传)
_FULL_REPORT_S = 1800  # 账套名单没变时也至少每 30 分钟整份上传一次（服务端万一丢了也能补上）


def _report_digest(account_sets, accounts) -> str:
    """账套名单 + 科目表的指纹：和上次整份上传的一样，这一拍就只报在线。"""
    import hashlib
    import json

    raw = json.dumps([account_sets or [], accounts], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _run_periodic(
    cfg: CompanionConfig, qc, state: dict, on_update_available=None
) -> None:
    """循环每拍调:到点则上报账套心跳(回复里带着最新发布版本,见 _offer_update)。绝不抛(隔离·不拖垮直写)。"""
    now = time.time()
    refresh_request_id, refresh_scope, refresh_account_set = projection_refresh.command(
        state
    )
    if refresh_request_id or now - state.get("last_heartbeat", 0.0) >= max(
        10, cfg.heartbeat_interval_s
    ):
        state["last_heartbeat"] = now
        try:
            from . import update_block
            from .payload_contract import SUPPORTED_PAYLOAD_VERSIONS
            from .version import VERSION

            root = cfg.resolved_express_root()
            authorized = list(getattr(cfg, "authorized_roots", None) or ())
            profile_id = str(getattr(cfg, "profile_id", "") or "")
            projection_refresh.load_account_sets(state, profile_id, cfg.account_dir or "")
            master, master_version, refresh_error = projection_refresh.collect_endpoint(
                state,
                root or "",
                cfg.account_dir or "",
                now,
                bool(refresh_request_id and refresh_scope == "endpoint"),
                authorized,
                # 配对时检测到的年度文件夹全部授权（网页第一步再选）：新建的年度文件夹也照样找进来，
                # 只授权过一个年度的也一样（2026-10-08：原来只在授权多个、且有人点「重新读取」时才找）。
                discover=True,
            )
            # 扫得慢时先交「进行中」快照（读完的数据根换成新的、其余沿用上次报的，master_refresh / merge_partial）：
            # 照常上报，但不算网页「重新读取」已完成、不存本机清单、不记新年度文件夹，等完整快照。
            complete = master is not None and not master.get("partial")
            if master is not None:
                if complete:
                    _remember_roots(cfg, authorized, master.get("roots"))
                    authorized = list(getattr(cfg, "authorized_roots", None) or ())
                projection_refresh.apply_endpoint_snapshot(
                    state, master, master_version
                )
                if complete:
                    projection_refresh.save_account_sets(state, profile_id)
            # 这次启动还没扫完（大数据根要几分钟）：账套先用本机存的上次清单、科目先用本机索引里上次的科目表
            # （load_account_sets）；本机没有科目表时不报（报「0 个」服务端会盖掉上一份，这期间要人选的科目没有选项，
            # 2026-09-28 真机复测）。头一次启动、本机也没有清单时账套照旧报空：不报的话服务端会把目录换成只剩所选的那一个。
            known_sets = erp_scope(cfg, state.get("reported_account_sets") or [])
            known_accts = state.get("reported_accounts")
            sets = known_sets or []
            accts = known_accts or []
            # 随心跳上报客户所选账套整组(顶层 4 键·对齐云端契约)。account_set 唯一键 = 账套 PATH。
            selected = {
                "account_set": cfg.account_set or "",
                "account_dir": cfg.account_dir or "",
                "account_company": cfg.account_company or "",
                "account_set_row": int(cfg.account_set_row or 0),
                "express_root": root or "",
            }
            mapping = {
                k: (getattr(cfg, k, "") or "")
                for k in (
                    "revenue_acc",
                    "ar_acc",
                    "vat_output_acc",
                    "fallback_acc",
                    "ap_acc",
                    "vat_input_acc",
                )
            }
            catalog = None
            catalog_version = 0.0
            routine_catalog = False
            refreshed_accounts = None
            refresh_ready = False
            if refresh_request_id and refresh_scope == "account_set":
                # 云端请读的那份账套（可以不是本机所选的，1.5.1 起云端会按需请读）：只读、不改所选账套；
                # 读回的目录与科目表这一拍随 master_refresh_account_set 上报，云端存到那份账套名下。
                (
                    catalog,
                    refreshed_accounts,
                    catalog_version,
                    refresh_error,
                    refresh_ready,
                ) = projection_refresh.collect_account(
                    state, sets, refresh_account_set, now, roots=[root or "", *authorized]
                )
            elif (
                cfg.account_dir
                and now - state.get("last_catalog", 0.0) >= _CATALOG_INTERVAL_S
            ):
                from .catalog_refresh import request_catalog

                catalog, catalog_version = request_catalog(
                    cfg.account_dir,
                    newer_than=float(state.get("last_catalog", 0.0) or 0.0),
                )
                routine_catalog = True
            if refreshed_accounts is not None:
                accts = known_accts = refreshed_accounts
            refresh_fields, acknowledged_refresh = projection_refresh.heartbeat_ack(
                refresh_request_id,
                refresh_scope,
                refresh_account_set,
                endpoint_ready=complete,
                account_ready=refresh_ready,
                error=refresh_error,
            )
            # 心跳瘦身（2026-10-08）：账套名单与科目表和上次整份上传的一样、服务端也认「没变」时，只报在线；
            # 下面几种照旧整份上传：网页要求重新读取、这次带目录、距上次整份已满 30 分钟、刚启动或换了配对码（state 重置）。
            digest = _report_digest(known_sets, known_accts)
            slim = bool(
                state.get("unchanged_ok")
                and not refresh_request_id
                and catalog is None
                and digest == state.get("sent_digest")
                and now - float(state.get("sent_full_at", 0.0) or 0.0) < _FULL_REPORT_S
            )
            hb = qc.health(
                account_sets=None if slim else known_sets,
                accounts=None if slim else known_accts,
                account_sets_unchanged=slim,
                method=cfg.method,
                selected=selected,
                mapping=mapping,
                catalog=catalog,
                companion_version=VERSION,
                max_payload_version=max(SUPPORTED_PAYLOAD_VERSIONS),
                # 自动更新被 Windows 拦下（版本 + 时刻）；没被拦报 None，云端据此清掉旧值（update_block.py）
                update_blocked=update_block.reported(),
                **refresh_fields,
            )
            if not slim:
                state["sent_digest"] = digest
                state["sent_full_at"] = now
            state["unchanged_ok"] = bool(isinstance(hb, dict) and hb.get("account_sets_unchanged_ok"))
            if catalog_version and routine_catalog:
                # 只记所选账套的例行上报：按账套读别的账套不推迟所选账套每 30 分钟那一次
                state["last_catalog"] = catalog_version
            _sync_account_set(cfg, hb)
            _sync_connection_identity(cfg, hb)
            _offer_update(hb, on_update_available)
            next_refresh = hb.get("master_refresh") if isinstance(hb, dict) else None
            command_kept = projection_refresh.remember(state, next_refresh)
            if not command_kept and acknowledged_refresh:
                projection_refresh.clear(state)
            log.info(
                "[heartbeat] 上报账套 %d 个 · 可写 %d · 科目 %d",
                len(sets),
                sum(1 for s in sets if s.get("writable")),
                len(accts),
            )
        except Exception as e:
            log.warning("[heartbeat] 上报失败(忽略): %s", e)


# 连续 N 次心跳/拉取失败才判离线 —— 容忍部署重启(~20s)/ 瞬时网络闪断,
# 避免一拍打空就误报「连不上」。单拍 ~poll_interval,3 次≈1 分多钟。
OFFLINE_AFTER_FAILS = 3


def run_poll_loop(
    cfg: CompanionConfig,
    *,
    handle_task: Callable[[QueueClient, object], object],
    should_stop: Callable[[], bool],
    on_connect: Optional[Callable[[QueueClient], None]] = None,
    on_poll_error: Optional[Callable[[Exception], None]] = None,
    on_poll_ok: Optional[Callable[[], None]] = None,
    on_batch_done: Optional[Callable[[], None]] = None,
    on_update_available: Optional[Callable[[dict], None]] = None,
    on_tick: Optional[Callable[[], None]] = None,
) -> None:
    """出站拉取循环壳:周期心跳/自动更新检查 → poll → 交 handle_task。GUI/CLI 共用。

    handle_task(qc, task):单票处理(直写 · 含自身 ack 与状态 · 返回值不被本循环消费,成败计数
    由 GUI 在回调内自行结算)。should_stop():True 即退。on_connect(qc):连接就绪(启动一次)。
    on_poll_error(exc):连续失败达 OFFLINE_AFTER_FAILS 次才回调(判离线)。on_poll_ok():
    失败后首个成功拍回调(断线恢复 → 托盘回绿,不必重配对)。on_batch_done():队列转空且本批
    【处理过至少一张(无论成败)】时回调 → 由回调按成功/失败张数弹诚实三态通知。
    on_update_available({version,url}):发现新版回调(托盘据此提示更新)。
    on_tick():每拍开头调一次(哪怕这拍 poll/写盘随后卡住,已盖过时标)—— 供看门狗判存活。
    """
    state = {"last_heartbeat": 0.0}
    qc_cfg = QueueClientConfig(
        backend_url=cfg.backend_url,
        bearer_token=cfg.bearer_token,
        companion_id=cfg.companion_id,
    )
    fail_streak = 0
    processed = (
        0  # 本批已处理票数(成功或失败均计;队列转空即结算批次通知,由回调按成败分态)
    )
    with QueueClient(qc_cfg) as qc:
        if on_connect:
            on_connect(qc)
        while not should_stop():
            if on_tick:
                on_tick()  # 盖存活时标(在本拍任何阻塞调用之前)→ 看门狗据此判卡死
            try:
                _run_periodic(cfg, qc, state, on_update_available)
                task = qc.poll()
                take_refresh = getattr(qc, "take_master_refresh", None)
                refresh_command = take_refresh() if callable(take_refresh) else None
                if projection_refresh.remember(state, refresh_command):
                    state["last_heartbeat"] = 0.0
            except Exception as e:
                fail_streak += 1
                if fail_streak >= OFFLINE_AFTER_FAILS and on_poll_error:
                    on_poll_error(e)
                time.sleep(cfg.poll_interval_ms / 1000)
                continue
            # 这一拍成功:若之前在失败中(含已判离线)→ 通知恢复,托盘自动回在线。
            if fail_streak:
                fail_streak = 0
                if on_poll_ok:
                    on_poll_ok()
            if task is None:
                if processed and on_batch_done:
                    try:
                        on_batch_done()
                    except Exception:
                        log.exception("[after-batch] 完成通知崩溃(隔离 · 不影响直写)")
                processed = 0
                time.sleep(cfg.poll_interval_ms / 1000)
                continue
            handle_task(qc, task)
            processed += 1
