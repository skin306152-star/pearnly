"""Express 账套 DBF 直写器(无人值守录入法 · 绕过界面写账套 DBF)。

输入 = Track A `purchase_adapter` 规整好的 P1 采购载荷。一张赊购(RR)写 6 表:
ISRUN(增号)→ APTRN(单头)→ ISVAT(进项税)→ GLJNL+GLJNLIT(分录 Dr 采购/Dr 进项税
= Cr 应付)→ APBAL(供应商应付按月桶累加)。明细行走 purchase_lines 路由:选库存的行真入
STCRD/STMAS 并追加「借 存货 / 贷 购货」重分类分录,其余行照旧不碰库存。

⚠️ 风险高(替闭源 Express 复刻过账)。强制护栏:**三重一致性闸 = 写盘目标 == 客户所选账套
(目录+账套名+ISINFO 公司)· 缺所选 fail-safe 拒** · 写前备份 · 失败整体回滚 · 读回校验借贷平 ·
REFNUM+SUPCOD 幂等 · reindex 重建 CDX。金额用 decimal 算、写库转 double(VFP `B`)·日期存公历。

真机写 live 账套留客户机器(本模块可对只读副本/合成表全 headless 测)。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

import dbf

from . import cdx_reindex
from . import dbf_detail
from . import account_rules
from . import dbf_schema as S
from . import fiscal_period
from . import master_create
from . import purchase_lines
from . import stock_location
from . import stock_template
from . import write_verify
from .consistency import account_company_matches
from .write_result import DbfWriteResult  # noqa: F401  re-export · dbf_sales 与测试从本模块取
from .dbf_fields import (
    _f,
    amount_fields,
    audit_fields,
)  # noqa: F401  抽出·再导出供 dbf_sales 复用
from .dbf_schema import account_dir_allowed  # noqa: F401  re-export · 逻辑在 dbf_schema
from .logger import get_logger
from .purchase_adapter import (
    ALLOWED_ACCOUNT_SETS,
    ERR_ENTRY_NOT_BALANCED,
    PurchaseEntry,
    build_purchase_entry,
    validate_purchase_payload,
)

log = get_logger(__name__)

ERR_VALIDATION = "PAYLOAD_INVALID"
ERR_ACCOUNT_DIR = "ACCOUNT_DIR_NOT_ALLOWED"
ERR_COMPANY = "ACCOUNT_COMPANY_MISMATCH"
ERR_NEW_SUPPLIER = "SUPPLIER_NOT_IN_APMAS"
ERR_FIELD_WIDTH = "FIELD_TOO_LONG"
ERR_WRITE = "DBF_WRITE_FAILED"
ERR_VERIFY = "READBACK_VERIFY_FAILED"
ERR_INDEX = "CDX_REINDEX_FAILED"  # CDX 重建/索引回查失败 → 回滚,Express 查不到
ERR_WAITING_LOCK = "ACCOUNT_BUSY_LOCKED"  # Express 占用账套 → 回滚,稍后自动重试(非失败)
ERR_SUPPLIER_DUP = "SUPPLIER_DUP_SUSPECTED"  # 疑似重复供应商 → 不建,转人工确认
ERR_SUPPLIER_AMBIGUOUS = "SUPPLIER_BRANCH_AMBIGUOUS"  # 同税号多分店档定不出唯一 → 转人工
ERR_STOCK_IN = "STOCK_IN_FAILED"  # 采购入库路失败(混行/数量成本非法/建档失败)→ 交会计
# 载荷 4 的仓库码不合格 / 不在这套账的仓库代码表(ISTAB 21)里 → 整单不写,不回落主仓(见 stock_location)
ERR_LOCATION = stock_location.ERR_LOCATION

# 真表定宽列上限(逆向 DATAT):超长 → 写前拒,绝不溢出/截断坏 Express。
_MAX_REFNUM = 15
_MAX_SUPCOD = 10
_MAX_TAXID = 15
_MAX_ACCNUM = 15


def _field_overflow(entry: PurchaseEntry) -> Optional[str]:
    """定宽列超长检查(写前快速失败)。返回错误描述或 None。"""
    checks = [
        ("ref_no (REFNUM)", entry.ref_no, _MAX_REFNUM),
        ("supplier_code (SUPCOD)", entry.supplier_code, _MAX_SUPCOD),
        ("tax_id (TAXID)", entry.supplier_tax_id, _MAX_TAXID),
    ]
    for label, val, limit in checks:
        if len((val or "").encode("cp874", "replace")) > limit:
            return f"{label} exceeds {limit} bytes: {val!r}"
    for ln in entry.lines:
        acc = str(ln.get("acc") or "")
        if len(acc.encode("cp874", "replace")) > _MAX_ACCNUM:
            return f"ACCNUM exceeds {_MAX_ACCNUM} bytes: {acc!r}"
    return None


def _open(account_dir: Path, name: str, mode):
    t = dbf.Table(str(account_dir / f"{name}.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(mode)
    return t


def _department_accounts(account_dir: Path) -> frozenset:
    """科目表里「使用部门」= Y 的科目:Express 在这些科目的分录行上记单据的部门码,其余科目不记。

    真账(69SINCER)进项 1702/1702 行照此规律(52-01 进货成本、11-01 现金带部门,53 费用不带);
    2026-09-28 真机复测推送的分录一律没带部门。读不到科目表 / 没有这一列 → 空集(照旧不带)。"""
    try:
        t = _open(account_dir, "GLACC", dbf.READ_ONLY)
    except Exception:
        return frozenset()
    try:
        if "USEDEP" not in t.field_names:
            return frozenset()
        return frozenset(
            (r.ACCNUM or "").strip()
            for r in S.live_rows(t)
            if (r.USEDEP or "").strip().upper() == "Y"
        )
    finally:
        t.close()


def _gl_line(docnum, seqit, docdat, accnum, descrp, trntyp, amt, depcod, departmental) -> dict:
    row = {
        "VOUCHER": docnum,
        "SEQIT": seqit,
        "VOUDAT": docdat,
        "ACCNUM": accnum,
        "DESCRP": descrp,
        "TRNTYP": trntyp,
        "AMOUNT": float(amt),
    }
    if depcod and str(accnum).strip() in departmental:
        row["DEPCOD"] = depcod
    return row


def _next_docnum(aptrn, prefix: str, docdate_be: str) -> str:
    """DOCNUM = PREFIX + 佛历YYMMDD + '-' + 当日序号(3位)。扫 APTRN 取当日最大 +1。

    ⚠️ 这里**故意连软删行一起扫**(与 _verify 相反)。号是给人看的凭证编号,
    删掉的 RR690531-002 在会计手上、在打印件上仍是"用过的号";跳过软删行就会把它再发一次,
    PACK 前后同号两张单撞在一起。查重问"还算不算数",发号问"用没用过",判据本就不同。
    """
    head = f"{prefix}{docdate_be}-"
    seq = 0
    for r in aptrn:
        dn = (r.DOCNUM or "").strip()
        if dn.startswith(head):
            try:
                seq = max(seq, int(dn[len(head) :]))
            except ValueError:
                continue
    return f"{head}{seq + 1:03d}"


def _bump_isrun(
    account_dir: Path, prefix: str, jnltyp: str = S.JNLTYP_PURCHASE
) -> None:
    """该单别计数器 +1(Express 内部连号;DOCNUM 仍按当日序号生成)。无该行则跳过。

    软删的计数器行不是 Express 在用的那条,写它等于计数器白涨(且掩盖"这套账没有该单别"
    这个事实)。找不到活行就跳过 —— 与原行为一致,只是不再往幽灵行上写。
    """
    t = _open(account_dir, "ISRUN", dbf.READ_WRITE)
    try:
        for r in S.live_rows(t):
            if (r.PREFIX or "").strip() == prefix and (
                r.JNLTYP or ""
            ).strip() == jnltyp:
                cur = (r.DOCNUM or "").strip()
                width = len(cur) or 7
                try:
                    nxt = int(cur) + 1
                except ValueError:
                    return
                with r as rec:
                    rec.DOCNUM = str(nxt).zfill(width)
                return
    finally:
        t.close()


# 选择性备份只复制这些扩展名(表数据 + 复合索引 + 备注),够 _restore 整表还原。
_BACKUP_EXTS = (".DBF", ".CDX", ".FPT")


# 选择性备份放本机(2026-09-28 实测:放在账套旁的共享盘上,一张单备份 5.6–7.4 秒、删快照 1.3 秒,
# 占整张单 10–12 秒的大半;放本机只从共享盘读一遍,删快照也不走网络)。快照只在写盘事务窗口内有用,
# 成功或回滚后即删;进程中途被杀留下的,下次备份时清掉 7 天前的。
_LOCAL_BACKUP_KEEP_S = 7 * 24 * 3600


def _local_backup_root() -> Path:
    base = os.environ.get("PEARNLY_WRITE_BACKUP_DIR") or (
        os.path.join(os.environ["LOCALAPPDATA"], "Pearnly", "write-backups")
        if os.environ.get("LOCALAPPDATA")
        else os.path.join(tempfile.gettempdir(), "pearnly-write-backups")
    )
    root = Path(base)
    root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - _LOCAL_BACKUP_KEEP_S
    for old in root.iterdir():
        try:
            if old.is_dir() and old.stat().st_mtime < cutoff:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            continue
    return root


def _backup(account_dir: Path, tables: Optional[tuple] = None) -> Path:
    """写前备份。`tables` 非空 → 只复制这些表的数据/索引/备注文件到本机(选择性备份,远快于
    整账套 copytree);为 None → 整目录复制到账套旁(向后兼容其它调用方)。
    `_restore` 遍历备份目录里的文件还原,天然兼容选择性备份(只还原备过的表)。
    """
    parent = account_dir.parent if tables is None else _local_backup_root()
    dest = parent / f"{account_dir.name}_pearnly_bak_{time.strftime('%Y%m%d-%H%M%S')}"
    n = 0
    while dest.exists():
        n += 1
        dest = parent / f"{account_dir.name}_pearnly_bak_{time.strftime('%Y%m%d-%H%M%S')}_{n}"
    if tables is None:
        shutil.copytree(account_dir, dest)
        return dest
    wanted = {t.upper() for t in tables}
    dest.mkdir(parents=True)
    for src in account_dir.iterdir():
        if (
            src.is_file()
            and src.stem.upper() in wanted
            and src.suffix.upper() in _BACKUP_EXTS
        ):
            shutil.copy2(src, dest / src.name)
    return dest


def _restore(account_dir: Path, backup_dir: Path) -> None:
    for src in backup_dir.iterdir():
        if src.is_file():
            shutil.copy2(src, account_dir / src.name)


class StepClock:
    """一张单写盘各步耗时(秒),写进日志一行 `[timing]`,用来找推送慢在哪(2026-09-28 压测:约 10 秒一张、
    每第三张 30–35 秒,现有日志看不出卡在哪一步)。只计时,不改任何写盘行为。"""

    def __init__(self, direction: str):
        self.direction = direction
        self.started = self.last = time.monotonic()
        self.steps: Dict[str, float] = {}

    def lap(self, name: str) -> None:
        now = time.monotonic()
        self.steps[name] = round(now - self.last, 2)
        self.last = now

    def log(self, docnum: str, outcome: str) -> None:
        total = round(time.monotonic() - self.started, 2)
        log.info(
            "[timing] %s %s %s total=%.2fs %s",
            self.direction,
            docnum or "-",
            outcome,
            total,
            " ".join(f"{k}={v:.2f}" for k, v in self.steps.items()),
        )


def _discard_backup(backup_dir: Optional[Path]) -> None:
    """事务收尾 → 删 per-write 快照。安全网只在写盘事务窗口内有用:校验通过(已提交)或
    已 _restore(已回滚)后,快照即死重;不删则每张票在账套盘留一份,日积月累吃满磁盘。
    账套级恢复另有夜间 PACK 快照(保留最近 K 份)兜底,与此无关。"""
    if backup_dir:
        shutil.rmtree(backup_dir, ignore_errors=True)


def write_purchase(
    payload: Dict[str, Any],
    account_dir: str | Path,
    *,
    userid: str = S.PEARNLY_USERID,
    depcod: str = "",
    make_backup: bool = True,
    allowed_sets: tuple = ALLOWED_ACCOUNT_SETS,
    selected_dir: str = "",
    expected_company: str = "",
    index_batch=None,
) -> DbfWriteResult:
    """把一张采购票直写进账套 DBF。失败整体回滚,绝不留半截单。

    三重一致性闸(客户所选账套·缺所选 fail-safe 拒):allowed_sets=账套名(C3)+
    selected_dir=写盘目录(C2)+ expected_company=目标 ISINFO 公司(防同尾目录串账套)。
    """
    clock = StepClock("purchase")
    v = validate_purchase_payload(payload, allowed=allowed_sets)
    if not v.ok:
        return DbfWriteResult(
            False, error_code=ERR_VALIDATION, error=f"{v.error_code}: {v.detail}"
        )
    entry = build_purchase_entry(payload)

    account_dir = Path(account_dir)
    ambiguous = S.path_ambiguity(account_dir) or S.path_ambiguity(selected_dir)
    if ambiguous:  # 路径会被系统改写落点 → 宁可不写,也不能静默写进本机影子目录
        return DbfWriteResult(
            False,
            error_code=ERR_ACCOUNT_DIR,
            error=(
                f"账套路径不完整,系统会把落点改写成 {ambiguous!r}"
                f"(原值 {str(account_dir)!r} · UNC 是不是少写了一个反斜杠?)"
            ),
        )
    if not S.account_dir_allowed(account_dir, selected_dir):
        return DbfWriteResult(
            False,
            error_code=ERR_ACCOUNT_DIR,
            error=f"write target {account_dir} != selected account dir {selected_dir!r}",
        )
    co_ok, co_reason = account_company_matches(account_dir, expected_company)
    if not co_ok:
        return DbfWriteResult(False, error_code=ERR_COMPANY, error=co_reason)
    if not (entry.supplier_code or entry.supplier_name):
        return DbfWriteResult(
            False,
            error_code=ERR_NEW_SUPPLIER,
            error="supplier has neither code nor name",
        )
    overflow = _field_overflow(entry)
    if overflow:
        return DbfWriteResult(False, error_code=ERR_FIELD_WIDTH, error=overflow)
    # 载荷 4:逐行写进哪个仓(没带仓库码 → None,整条写路与以前逐字节相同)
    try:
        locations = stock_location.plan(account_dir, payload)
    except stock_location.LocationError as exc:
        return DbfWriteResult(False, error_code=ERR_LOCATION, error=str(exc))

    docdate_be = str(payload["docdate_be"])
    prefix = entry.doctype  # RR / HP
    docdat = S.be_yymmdd_to_greg(docdate_be)
    vatprd = S.be_yymmdd_to_greg(payload.get("vat_period_be") or docdate_be)
    base, vat, total = (
        Decimal(entry.base_amount),
        Decimal(entry.vat_amount),
        Decimal(entry.total_amount),
    )
    has_vat = vat > 0

    # 不查重:账套里已有同号 / 同票的单照写(用户 2026-09-30、2026-10-08:Express 手工录入也不拦重复)。
    try:
        existing_code = entry.supplier_code or (
            ""
            if entry.supplier_confirmed_new
            else master_create.find_supplier_code(
                account_dir,
                entry.supplier_tax_id,
                entry.supplier_name,
                branch=entry.supplier_branch,
            )
        )
    except master_create.AmbiguousPartyMatch as exc:
        # 连锁同税号多档且分店号/名字定不出唯一 → 转人工,绝不静默挂错分店。
        return DbfWriteResult(False, error_code=ERR_SUPPLIER_AMBIGUOUS, error=str(exc))
    if existing_code:
        entry.supplier_code = existing_code
    elif not entry.supplier_confirmed_new and master_create.suspected_supplier_dup(account_dir, entry):
        # 新供应商疑似重复(核心名相似·税号不同)→ 不静默建重复户,转人工确认。
        return DbfWriteResult(False, error_code=ERR_SUPPLIER_DUP, error="dup")

    clock.lap("checks")
    backup_dir = _backup(account_dir, S.PURCHASE_BACKUP_TABLES + stock_location_tables(locations)) \
        if make_backup else None
    clock.lap("backup")
    docnum = ""
    moves = [] if locations is not None else None
    try:
        # 新供应商 → 先在 APMAS 建档(在备份-回滚事务内)拿到码
        entry.supplier_code = master_create.ensure_supplier(
            account_dir, entry, created_date=docdat, userid=userid
        )
        created_supplier = bool(not existing_code)
        docnum, line_modes, has_stock, notes, gl_rows = _write_all(
            account_dir,
            entry,
            payload,
            docdat,
            vatprd,
            base,
            vat,
            total,
            has_vat,
            prefix,
            userid,
            depcod,
            **_located(locations, moves),
        )
        stloc_expected, notes = record_locations(account_dir, moves, notes)
        clock.lap("write")
        detail_n = (
            len(entry.items) if (entry.items_status == "ok" and entry.items) else 0
        )
        _reindex_or_defer(
            account_dir,
            S.WRITE_TABLES,
            detail_n,
            "APMAS" if created_supplier else "",
            index_batch,
            **({"more": S.LOCATION_TABLES} if stloc_expected else {}),
        )
        clock.lap("reindex")
        _verify(
            account_dir,
            docnum,
            total,
            has_vat,
            base=base,
            detail_n=detail_n,
            has_stock=has_stock,
        )
        verify_locations(account_dir, stloc_expected)
        clock.lap("verify")
        _verify_index(account_dir, cdx_reindex.TAG_AP_DOCNUM, docnum)
        clock.lap("index_check")
        # 写完顺手核对(第 3 期第 6 条):只报不回滚,随回执 meta.verify 交回
        verify = write_verify.check(account_dir, write_verify.Expected(
            side="purchase", docnum=docnum, party_code=entry.supplier_code, docdat=docdat,
            rectyp=S.RECTYP[prefix], refnum=entry.ref_no, lines=gl_rows,
            tax_id=entry.supplier_tax_id, branch=entry.supplier_branch, has_vat=has_vat))
        clock.lap("write_check")
        _discard_backup(backup_dir)  # 校验通过(已提交)→ 删快照,磁盘恒定
        clock.lap("cleanup")
        clock.log(docnum, "ok")
        return DbfWriteResult(
            True,
            docnum=docnum,
            backup_dir=None,
            party_code=entry.supplier_code,
            created_party=created_supplier,
            line_modes=line_modes,
            notes=notes,
            verify=verify,
        )
    except Exception as e:  # 任何失败 → 回滚还原
        clock.lap("failed_step")
        if backup_dir:
            _restore(account_dir, backup_dir)
            _discard_backup(
                backup_dir
            )  # 已回滚 → 删快照(保留路径串作 rolled_back 标记)
        clock.lap("rollback")
        clock.log(docnum, "rolled_back")
        code = _error_code(e)
        log.exception("dbf write failed, rolled back")
        return DbfWriteResult(
            False,
            error_code=code,
            error=str(e),
            backup_dir=str(backup_dir) if backup_dir else None,
            evidence=e.evidence if isinstance(e, _WriteError) else None,
        )


class _WriteError(Exception):
    """写盘失败。args 保持 (message[, code]) —— 既有 len(e.args)>1 / args[1] 取码、
    str(e) 取文本的判读一字不变;evidence 只挂属性不进 args,避免诊断数据串进错误文本。"""

    def __init__(self, message, code=None, evidence=None):
        if code is not None:
            super().__init__(message, code)
        else:
            super().__init__(message)
        self.evidence = evidence


def _error_code(e: Exception) -> str:
    """异常 → 云端错误码。入库路的失败单独给码,会计一眼知道是"库存"这步卡住而非写盘出错。"""
    if isinstance(e, purchase_lines.StockLineError):
        # 入库路新建库存品挂的存货科目组不合格:单独给码,产品端让人选一次组(见 stock_template.ERR_STOCK_GROUP)
        return (stock_template.ERR_STOCK_GROUP if isinstance(e.__cause__, stock_template.InventoryAccountUnfit)
                else ERR_STOCK_IN)
    if isinstance(e, _WriteError) and len(e.args) > 1:
        return e.args[1]
    return ERR_WRITE


def _vat_branch(table, orgnum: int) -> dict:
    """税记录的分店号列(真表有 ORGNUM;老表没有这一列就不写)。"""
    return {"ORGNUM": int(orgnum or 0)} if "ORGNUM" in {f.upper() for f in table.field_names} else {}


def _write_all(
    account_dir,
    entry,
    payload,
    docdat,
    vatprd,
    base,
    vat,
    total,
    has_vat,
    prefix,
    userid,
    depcod="",
    *,
    locations=None,
    moves=None,
) -> tuple:
    """写完采购六表,返回 (凭证号, 逐行 mode, 有无库存行, 诚实标注, 实际写的分录 [(SEQIT, ACCNUM, 借贷, 金额)])。"""
    rectyp = S.RECTYP[prefix]
    paid = prefix == "HP"
    # 按这套账自己的设置(2026-10-08):这本单据簿挂哪本日记账、设没设默认部门;赊购的应付行记供应商档上的科目、
    # 到期日按供应商档的付款期(以前恒用账套默认应付科目、到期日 = 单据日期)。
    book = account_rules.book(account_dir, prefix)
    jnltyp = book.get("JNLTYP") or S.JNLTYP_PURCHASE
    depcod = depcod or book.get("DEPCOD", "")
    duedat = None
    # 期末盘存制的账套:采购借记账套的「购货」科目(ISINFO.ACCNUM18),不借存货、也不做「借存货 / 贷购货」重分类
    # (以前一律借账套默认存货科目 ACCNUM04,7 套期末盘存制账套记错科目)。只换默认存货科目,会计选过的科目不动。
    periodic = account_rules.periodic_inventory(account_dir)
    if periodic:
        debits = [ln for ln in entry.lines if str(ln.get("side", "")).upper() == "D"]
        stock_default = account_rules.default_account(account_dir, "ACCNUM04")
        purchases = account_rules.default_account(account_dir, "ACCNUM18")
        if debits and stock_default and str(debits[0].get("acc") or "") == stock_default \
                and account_rules.postable_account(account_dir, purchases):
            debits[0]["acc"] = purchases
    credits = [ln for ln in entry.lines if str(ln.get("side", "")).upper() == "C"]
    payterm = 0
    if paid:
        # 现购:Express 原生贷现金(ISINFO.ACCNUM00),完全不碰应付(真实账套 2165 张 HP 无一例外;2026-10-08 核对)。
        # 以前贷应付、表头却写已付 → 应付控制科目虚挂一笔。
        cash = account_rules.default_account(account_dir, "ACCNUM00")
        if credits and cash and account_rules.postable_account(account_dir, cash):
            credits[0]["acc"] = cash
    else:
        if credits:
            credits[0]["acc"] = account_rules.control_account(
                account_dir, "APMAS", entry.supplier_code, str(credits[0].get("acc") or "")
            )
        payterm = account_rules.party_terms(account_dir, "APMAS", entry.supplier_code)[1]
        duedat = account_rules.due_date(docdat, payterm)
    name = entry.supplier_name
    # 凭证表头与进项税记录的分店号 = 这家供应商档的分店号(税表分店一栏;真账一致 99%+)。
    orgnum = master_create.party_orgnum(account_dir, "APMAS", "SUPCOD", entry.supplier_code)
    flgvat, amount, aftdisc, total_, netval, netamt = amount_fields(
        paid, has_vat, base, vat, total
    )

    # V1 安全明细(镜像销项):仅 items_status==ok 落 STCRD,NXTSEQ 必须=明细行数。
    write_detail = entry.items_status == "ok" and bool(entry.items)
    nxtseq_override = {"NXTSEQ": str(len(entry.items))} if write_detail else {}

    ap = _open(account_dir, "APTRN", dbf.READ_WRITE)
    try:
        docnum = _next_docnum(ap, prefix, str(payload["docdate_be"]))
        ap.append(
            {
                "RECTYP": rectyp,
                "DOCNUM": docnum,
                "DOCDAT": docdat,
                "REFNUM": entry.ref_no,
                "VATPRD": vatprd if has_vat else None,
                "SUPCOD": entry.supplier_code,
                "FLGVAT": flgvat,
                "VATRAT": float(entry.vat_rate),
                "AMOUNT": amount,
                "AFTDISC": aftdisc,
                "TOTAL": total_,
                "NETVAL": netval,
                "VATAMT": _f(vat),
                "NETAMT": netamt,
                "REMAMT": 0.0 if paid else netamt,
                "PAYAMT": netamt if paid else 0.0,
                "CSHPAY": netamt if paid else 0.0,
                "PVATPRORAT": "-",
                "DOCSTAT": "N",
                "USERID": userid,
                **audit_fields(docdat, depcod, has_vat, orgnum, duedat),
                # 单据上的付款天数(真账 RR 89% 等于供应商档的);老合成表没有这一列就不写
                **({"PAYTRM": payterm} if "PAYTRM" in ap.field_names else {}),
                **nxtseq_override,
            }
        )
    finally:
        ap.close()

    # 明细行路由:选库存的行真入库并回吐「借 存货 / 贷 购货」重分类分录,接在本单分录之后。
    gl_rows = purchase_lines.journal_rows(entry)
    line_modes, reclass = [], []
    if write_detail:
        line_modes, reclass = purchase_lines.write_detail(
            account_dir, docnum, docdat, entry, gl_rows, depcod=depcod, userid=userid,
            **_located(locations, moves),
        )
        gl_rows += [] if periodic else reclass

    if has_vat:
        iv = _open(account_dir, "ISVAT", dbf.READ_WRITE)
        try:
            iv.append(
                {
                    "VATREC": S.ISVAT_VATREC_INPUT,
                    "RECTYP": rectyp,
                    "VATPRD": vatprd,
                    "VATDAT": docdat,
                    "DOCDAT": docdat,
                    "DOCNUM": docnum,
                    "REFNUM": entry.ref_no,
                    "DESCRP": name[:60],
                    "AMT01": _f(base),
                    "VAT01": _f(vat),
                    "DOCSTAT": "N",
                    "TAXID": entry.supplier_tax_id,
                    "PRENAM": entry.supplier_prename[:15],
                    **_vat_branch(iv, orgnum),
                }
            )
        finally:
            iv.close()

    descrp = (f"ซื้อเชื่อจาก {name}" if prefix == "RR" else f"ซื้อสดจาก {name}")[:50]
    gj = _open(account_dir, "GLJNL", dbf.READ_WRITE)
    try:
        gj.append(
            {
                "JNLTYP": jnltyp,
                "VOUDAT": docdat,
                "VOUCHER": docnum,
                "DESCRP": descrp,
                "TRNSTAT": "P",
                "DOCSTAT": "N",
                "CREBY": userid,
            }
        )
    finally:
        gj.close()

    unbalanced = purchase_lines.imbalance(gl_rows)
    if unbalanced:  # 宁可整单失败,也不写出一张不平的凭证
        raise _WriteError(unbalanced, ERR_ENTRY_NOT_BALANCED)

    departmental = _department_accounts(account_dir) if depcod else frozenset()
    gi = _open(account_dir, "GLJNLIT", dbf.READ_WRITE)
    try:
        for seqit, accnum, trntyp, amt in gl_rows:
            gi.append(
                _gl_line(docnum, seqit, docdat, accnum, descrp, trntyp, amt, depcod, departmental)
            )
    finally:
        gi.close()

    _bump_isrun(account_dir, prefix, jnltyp)
    notes = _update_apbal(account_dir, prefix, entry.supplier_code, docdat, total)
    return docnum, line_modes, purchase_lines.has_stock_lines(line_modes), notes, [row[:3] for row in gl_rows]


def _update_apbal(
    account_dir: Path, doctype: str, supcod: str, docdat, total: Decimal
) -> dict:
    """采购应付按单别 × 会计期分桶累加。RR(赊购)→ 'RR{期}',HP(现购)→ 'HP{期}'。

    单别分桶的真账反证(BANKREC):Express 原生 HP 单精确落 HP 桶、RR 桶为 0 —— 一律塞 RR
    桶会把现购挂进赊购月报。期号是 ISPRD 财年期号 + 年槽,不是单号里的日历月;判不出就不写
    桶,把原因回给调用方上报(见 _update_arbal 同款理由)。
    """
    field, why = fiscal_period.resolve_month_field(account_dir, docdat, doctype.upper())
    if field is None:
        log.warning("APBAL 月桶未写(会计需人工补记):%s", why)
        return {"apbal_not_written": why}
    t = _open(account_dir, "APBAL", dbf.READ_WRITE)
    try:
        # 同 _update_arbal:命中软删行 = 应付余额加进 Express 看不见的行。没有活行就 append。
        for r in S.live_rows(t):
            if (r.SUPCOD or "").strip() == supcod:
                with r as rec:
                    rec[field] = float(Decimal(str(rec[field] or 0)) + total)
                return {}
        t.append({"SUPCOD": supcod, field: _f(total)})
    finally:
        t.close()
    return {}


def _reindex(account_dir: Path, tables=S.WRITE_TABLES) -> None:
    """重建 CDX。优先后台 Harbour runner(真重建 Express CDX);runner 不在(未发版/
    兼容机)→ 回落旧 dbf reindex(对 CDX 无效·靠夜间 PACK 兜)。runner 失败抛错触发回滚。"""
    if cdx_reindex.runner_available():
        try:
            cdx_reindex.reindex(account_dir, tables)
        except cdx_reindex.CdxBusyError as e:
            raise _WriteError(str(e), ERR_WAITING_LOCK) from e
        except cdx_reindex.CdxReindexError as e:
            raise _WriteError(str(e), ERR_INDEX) from e
        return
    log.warning("cdx_reindex_runner 不在,回落 dbf reindex(CDX 靠 PACK 兜)")
    for name in tables:
        t = _open(account_dir, name, dbf.READ_WRITE)
        try:
            t.reindex()
        finally:
            t.close()


def _reindex_or_defer(
    account_dir, write_tables, detail_n, party_table, index_batch, more=()
) -> None:
    """逐票重建交易表;主档(明细/商品/科目 + 新建对手方)按是否批量决定逐票或批末重建。

    index_batch 非空:逐票只重建交易表(够 _verify_index 回查 + ack),主档累积到队列空一次性
    重建(消逐票 N 次主档重建的间隔卡顿·见 cdx_reindex.IndexBatch);为空:逐票全量重建。
    party_table 为新建对手方主档(ARMAS/APMAS),无则空串。
    """
    extra = S.MASTER_EXTRA_TABLES if detail_n else ()
    if party_table:
        extra += (party_table,)
    extra += tuple(more)  # 载荷 4 动过 STLOC(新建了品 × 仓的行要进 Express 的索引)
    if index_batch is not None and extra:
        _reindex(account_dir, write_tables)
        index_batch.add(account_dir, extra)
    else:
        _reindex(account_dir, write_tables + extra)


def _located(locations, moves=None) -> dict:
    """载荷 4 才往下层传仓库与变动收集(``locations`` / ``moves``);没带仓库码的单调用与以前一字不差。"""
    if locations is None:
        return {}
    return {"locations": locations} if moves is None else {"locations": locations, "moves": moves}


def stock_location_tables(locations) -> tuple:
    """载荷 4 的单多备份 STLOC;没带仓库码的单不多备份(与以前相同)。"""
    return S.LOCATION_TABLES if locations is not None else ()


def record_locations(account_dir, moves, notes):
    """载荷 4:把这张单的库存变动记进各仓结存 STLOC → (读回要核的终值, 合并后的 notes)。没带仓库码(moves 是 None)
    什么都不做、notes 原样。"""
    if moves is None:
        return {}, notes
    try:
        expected, extra = stock_location.apply(account_dir, moves)
    except stock_location.LocationError as e:
        raise _WriteError(str(e), ERR_LOCATION) from e
    return expected, ({**(notes or {}), **extra} if extra else notes)


def verify_locations(account_dir, expected) -> None:
    """载荷 4:读回 STLOC,不符按读回失败整单回滚。"""
    try:
        stock_location.verify(account_dir, expected)
    except stock_location.LocationVerifyError as e:
        raise _WriteError(str(e), ERR_VERIFY, e.evidence) from e


def _verify_index(account_dir: Path, header: tuple, docnum: str) -> None:
    """V3 索引回查:按 Express 自己的 DOCNUM 索引确认单头可被查到。runner 不在则跳过
    (无法回查·已回落 PACK 路)。回查不中 → 抛 _WriteError 触发回滚。"""
    if not cdx_reindex.runner_available():
        return
    try:
        cdx_reindex.verify_indexed(account_dir, header, docnum)
    except cdx_reindex.CdxBusyError as e:
        raise _WriteError(str(e), ERR_WAITING_LOCK) from e
    except cdx_reindex.CdxReindexError as e:
        raise _WriteError(str(e), ERR_INDEX) from e


def _verify(
    account_dir: Path,
    docnum: str,
    total: Decimal,
    has_vat: bool,
    *,
    base: Optional[Decimal] = None,
    detail_n: int = 0,
    has_stock: bool = False,
) -> None:
    """读回:APTRN 行在、含税额对、GLJNLIT 借贷平且应付贷方==含税、有税则 ISVAT 行在;
    有明细则行数==NXTSEQ + 行合计≈税前(走库存的行带成本签名,不查"无成本")。"""
    # 读回只看还算数的行(同销项 _verify):软删的旧单/旧分录若也算进来,会拿旧行的金额
    # 判新单、把旧分录累加进借贷合计,好端端的一张单被判失败回滚。
    ap = _open(account_dir, "APTRN", dbf.READ_ONLY)
    try:
        hit = next(
            (r for r in S.live_rows(ap) if (r.DOCNUM or "").strip() == docnum), None
        )
        if hit is None or abs(Decimal(str(hit.NETAMT)) - total) > Decimal("0.02"):
            ev = {
                "kind": "readback_verify",
                "table": "APTRN",
                "docnum": docnum,
                "expected_total": float(total),
            }
            if hit is not None:
                ev["found_total"] = float(hit.NETAMT)
            raise _WriteError("APTRN row missing or NETAMT mismatch", ERR_VERIFY, ev)
        if detail_n and (hit.NXTSEQ or "").strip() != str(detail_n):
            nxt = (hit.NXTSEQ or "").strip()
            raise _WriteError(
                f"APTRN.NXTSEQ {hit.NXTSEQ!r} != line count {detail_n}",
                ERR_VERIFY,
                {
                    "kind": "readback_verify",
                    "table": "APTRN",
                    "docnum": docnum,
                    "expected_lines": detail_n,
                    "found_lines": int(nxt) if nxt.isdigit() else None,
                },
            )
    finally:
        ap.close()

    if detail_n:
        try:
            err = dbf_detail.verify_detail(
                account_dir, docnum, base, detail_n, allow_stock_impact=has_stock
            )
        except dbf_detail.DetailVerifyError as e:
            # 明细读回不符:message 一字不改(云端按文本 match 的既有断言依赖),证据随第三参带走。
            raise _WriteError(e.message, ERR_VERIFY, evidence=e.evidence) from e
        if err:
            raise _WriteError(err, ERR_VERIFY)

    gi = _open(account_dir, "GLJNLIT", dbf.READ_ONLY)
    try:
        dr = cr = ap_cr = Decimal("0")
        rows = 0
        for r in S.live_rows(gi):
            if (r.VOUCHER or "").strip() == docnum:
                rows += 1
                amt = Decimal(str(r.AMOUNT))
                if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT:
                    dr += amt
                else:
                    cr += amt
                    if (r.SEQIT or "").strip() == S.SEQIT["ap"]:
                        ap_cr += amt
        # 走库存时多一对重分类分录(借存货/贷购货),dr/cr 各 + 入库额仍平。故校验「借贷平 +
        # 应付贷方==含税总额」,不再要求总借方==总额(否则重分类会被误判成不平)。
        if rows < 2 or dr != cr or ap_cr != total:
            raise _WriteError(
                f"GL not balanced (dr={dr} cr={cr} ap={ap_cr} total={total} rows={rows})",
                ERR_VERIFY,
                {
                    "kind": "readback_verify",
                    "table": "GLJNLIT",
                    "docnum": docnum,
                    "expected_total": float(total),
                    "found_total": float(ap_cr),
                    "dr": float(dr),
                    "cr": float(cr),
                    "rows": rows,
                },
            )
    finally:
        gi.close()

    if has_vat:
        iv = _open(account_dir, "ISVAT", dbf.READ_ONLY)
        try:
            if not any((r.DOCNUM or "").strip() == docnum for r in S.live_rows(iv)):
                raise _WriteError(
                    "ISVAT row missing",
                    ERR_VERIFY,
                    {"kind": "readback_verify", "table": "ISVAT", "docnum": docnum},
                )
        finally:
            iv.close()
