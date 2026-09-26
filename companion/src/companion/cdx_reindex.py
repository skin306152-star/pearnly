"""后台重建 Express 账套 CDX 索引 + 索引回查(替换无效的 python `dbf` reindex)。

companion 用 python `dbf` 写完记录后,Express 仍看不到该单——它靠结构化复合索引 CDX
导航,而 `dbf.Table().reindex()` 对 Express CDX 是 no-op。本模块驱动随包内置的
`cdx_reindex_runner.exe`(Harbour DBFCDX · GTNUL 编译)在后台:
  · REINDEX —— 打开 Express 现存 CDX(tag 名/key 全保留),重建同名标签;
  · SEEK    —— 用 Express 自己的索引 key 回查刚写的单/明细/主档,确认可被 Express 查到。
等价 PACK/Reindex 的索引效果,但不开 Express、不需用户值守。

runner 不在(未发版/兼容机)→ `runner_available()` 返 False,调用方回落旧行为(CDX 靠夜间
PACK 兜),不报错。runner 在但失败/超时 → 抛 CdxReindexError,调用方回滚 + 转 needs_review。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .logger import get_logger

log = get_logger(__name__)

_RUNNER_NAME = "cdx_reindex_runner.exe"
_TIMEOUT_S = 90  # 单表;GLJNLIT 等大表经 SMB 重建留余量
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW:不闪控制台窗

# Express 固定索引(真 DATAT 实测·跨 AR/AP 同构):回查用。
TAG_AR_DOCNUM = ("ARTRN", "ARTRN1")  # 销项单头按 DOCNUM
TAG_AP_DOCNUM = ("APTRN", "APTRN1")  # 采购单头按 DOCNUM
TAG_STCRD_DOC = ("STCRD", "STCRD3")  # 明细按 DOCNUM+SEQNUM(前缀 DOCNUM 即命中首行)
TAG_STMAS_CODE = ("STMAS", "STMAS1")  # 商品主档按 STKCOD


class CdxReindexError(Exception):
    """runner 在但 REINDEX/回查失败 —— 调用方应回滚 + 转 needs_review/failed。"""


class CdxBusyError(CdxReindexError):
    """账套被占用(Express 正开着该表 → EXCLUSIVE 拿不到)→ 调用方回滚 + 转 waiting_lock 重试。"""


# 独占失败/被占用的错误特征(Harbour 错误描述 · 大小写无关)。SMB 上独占被拒时,USE 常未
# 建好工作区 → REINDEX 报 "Workarea not in use";一并归类为占用(交调用方走重试,不当硬失败)。
_BUSY_TOKENS = (
    "lock", "denied", "sharing", "used by", "being used", "占用", "在使用", "share",
    "workarea", "in use",
)


@dataclass
class SeekResult:
    found: bool
    recno: int = 0
    stkcod: str = ""
    docnum: str = ""
    seqnum: str = ""


def runner_path() -> Path:
    """frozen 打包后与主 exe 同目录;开发/测试走仓库 packaging/native/。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / _RUNNER_NAME
    return Path(__file__).resolve().parents[2] / "packaging" / "native" / _RUNNER_NAME


def runner_available() -> bool:
    return runner_path().is_file()


def _run(action: str, account_dir: Path, table: str, tag: str = "x", key: str = "x") -> str:
    """调 runner 一次,返回它写进本地临时 log 的文本(cp874 容错解码)。

    log 走本地临时文件(不在共享账套留垃圾、避并发干扰)。REINDEX/无关动作的 tag/key
    传占位 "x"(runner 忽略),只为保证显式 log 路径落在第 6 个位置参数上。
    """
    exe = runner_path()
    if not exe.is_file():
        raise CdxReindexError(f"runner missing: {exe}")
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as tf:
        logpath = tf.name  # with 退出即关闭,Windows 下 runner 才能独占写
    try:
        cmd = [str(exe), action, str(account_dir), table, tag or "x", key or "x", logpath]
        try:
            subprocess.run(
                cmd, timeout=_TIMEOUT_S, capture_output=True, creationflags=_NO_WINDOW
            )
        except subprocess.TimeoutExpired as e:
            # 超时多半是 EXCLUSIVE 被 Express 占着阻塞 → 当占用,转 waiting_lock 重试。
            raise CdxBusyError(
                f"{action} {table} 超时 {_TIMEOUT_S}s(Express 可能正占用账套)"
            ) from e
        return Path(logpath).read_text(encoding="cp874", errors="replace")
    finally:
        try:
            Path(logpath).unlink()
        except OSError:
            pass


def _runner_error(out: str) -> Optional[str]:
    m = re.search(r"^error=(.+)$", out, re.MULTILINE)
    return m.group(1).strip() if m else None


def reindex(account_dir: Path, tables: Iterable[str]) -> None:
    """逐表重建 CDX。任一表失败/未确认重建 → 抛 CdxReindexError(调用方回滚)。"""
    for t in tables:
        out = _run("REINDEX", Path(account_dir), t)
        if "reindex_done=.T." not in out:
            err = _runner_error(out) or out.strip().replace("\n", " ")[:200]
            low = err.lower()
            if any(tok in low for tok in _BUSY_TOKENS):
                raise CdxBusyError(f"REINDEX {t} 账套被占用: {err}")
            raise CdxReindexError(f"REINDEX {t} failed: {err}")


def seek(account_dir: Path, table: str, tag: str, key: str) -> SeekResult:
    """按 Express 索引 key 回查。命中返 found=True + 字段;未命中 found=False。"""
    out = _run("SEEK", Path(account_dir), table, tag, key)
    if "seek_found=.T." not in out:
        return SeekResult(False)
    recno = 0
    m = re.search(r"recno=(\d+)", out)
    if m:
        recno = int(m.group(1))

    def _grab(field: str) -> str:
        mm = re.search(rf"{field}=([^\s].*?)(?:\s+\w+=|$)", out)
        return mm.group(1).strip() if mm else ""

    return SeekResult(
        True, recno=recno, stkcod=_grab("stkcod"), docnum=_grab("doc"), seqnum=_grab("seq")
    )


class IndexBatch:
    """批量主档索引累积器:逐票把主档表(STMAS/STCRD/ISACC/ARMAS/APMAS)登记进来,
    队列转空时一次性重建——一批 N 张只重建一次主档索引,而非逐票 N 次(SMB 上每次重建
    都昂贵,逐票重建是「3 张间隔卡顿」的根因)。

    交易表(ARTRN/APTRN 等)仍逐票重建——逐票 `verify_indexed` 回查靠它;主档只影响
    Express 商品/客户查找的可见性,延到批末重建对账务正确性无影响(GL 由 companion 显式
    写 GLJNLIT,不依赖主档)。
    """

    def __init__(self) -> None:
        self._pending: dict = {}  # account_dir(str) -> set(主档表名)

    def add(self, account_dir, tables: Iterable[str]) -> None:
        self._pending.setdefault(str(account_dir), set()).update(t for t in tables if t)

    def flush(self) -> None:
        """重建累积的主档索引(每账套一次)。失败只记日志不抛——单据已记账且交易表回查
        通过,商品可见性延后由下批推送/夜间 PACK 兜底,绝不因可见性回滚已成功的记账。"""
        if not self._pending:
            return
        pending, self._pending = self._pending, {}
        for d, tables in pending.items():
            try:
                reindex(Path(d), tuple(sorted(tables)))
            except Exception:
                log.warning(
                    "[batch-index] 主档批量重建失败(已记账·可查性延后到下批/夜间PACK): %s",
                    d,
                    exc_info=True,
                )


def verify_indexed(account_dir: Path, header: tuple, docnum: str) -> None:
    """回查单头按 DOCNUM 索引必须命中(确认 Express 能查到)。失败抛 CdxReindexError。

    header = TAG_AR_DOCNUM(销项)/ TAG_AP_DOCNUM(采购)。
    """
    table, tag = header
    r = seek(Path(account_dir), table, tag, docnum)
    if not r.found:
        raise CdxReindexError(
            f"重建后 {table}.{tag} 回查不到 {docnum}(Express 仍查不到该单)"
        )
