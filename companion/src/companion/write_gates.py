"""采购/销项写路的两道重复闸:查重(幂等/差异)与防重单(prior_docnum)。

闸序 = **查重 → 防重单**,与桥 `bridge/writepath/orchestrator` 一字对齐。2026-07-27 靶场
真跑证明两侧原本闸序相反(桥「幂等→期间→prior」· 小助手「prior→幂等」),同一载荷两边结论
相反:一次成功推送之后的任何重投都必然带着指向自己刚写那张、且还活着的 prior_docnum,防重单
排在前面就把这类重投判成硬失败,还叫会计去 Express 删一张本来该留着的好单。

采购与销项各写一遍就会漂(2026-07-25 软删行幂等只修了一侧是实账),故收成这一份,两侧靠
`open_trn` / `find_existing` 注入表族差异。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from . import dbf_schema as S
from . import duplicate_gate as D
from .logger import get_logger
from .write_result import DbfWriteResult

log = get_logger(__name__)

# 上一版单据还在 ERP 里 → 默认不落第二张,把现状摆给会计由她定。会计改过票号时既有的
# ref_no 幂等认不出来,不提示就会一张票记两遍账。销项/进项共用这一个码。
ERR_PRIOR_DOC_EXISTS = "PRIOR_DOC_STILL_IN_ERP"
ERR_DUPLICATE_DIFFERS = D.ERR_DUPLICATE_DIFFERS


def docnum_exists(trn, docnum: str) -> bool:
    """该凭证号是否还在账里(软删的不算)。给防重单闸用。"""
    want = (docnum or "").strip()
    if not want:
        return False
    for r in S.live_rows(trn):
        if (r.DOCNUM or "").strip() == want:
            return True
    return False


def check_duplicate(
    open_trn: Callable[[], Any],
    find_existing: Callable[[Any, str, str], Optional[str]],
    ref_no: str,
    party_code: str,
    payload: Dict[str, Any],
    *,
    base,
    vat,
    total,
    docdat,
    label: str,
) -> Tuple[str, Optional[DbfWriteResult]]:
    """查重闸。返回 (账套里同钥匙那张的号, 提前返回的结果或 None)。

    三种走向(口径见 duplicate_gate 模块头):内容一致的重投 → 幂等跳过;内容不同且未确认 →
    摆差异、零写入;内容不同且已确认 → 放行,由调用方真写第二张。
    """
    if not party_code:
        return "", None
    trn = open_trn()
    try:
        dup = (find_existing(trn, ref_no, party_code) or "").strip()
        erp_head = D.head_row(S.live_rows(trn), dup) if dup else {}
    finally:
        trn.close()
    if not dup:
        return "", None
    diffs = D.compare(erp_head, D.incoming_head(base, vat, total, docdat))
    if not diffs:
        return dup, DbfWriteResult(
            True, docnum=dup, skipped=True, notes=D.duplicate_note(dup)
        )
    if not D.confirmed(payload):
        log.info("%s 同钥匙内容不同 → 摆差异待确认:%s", label, dup)
        return dup, DbfWriteResult(
            False,
            error_code=ERR_DUPLICATE_DIFFERS,
            error=D.differs_message(dup, diffs),
            docnum=dup,
            duplicate=D.differs_meta(dup, diffs),
        )
    return dup, None


def check_prior_doc(
    open_trn: Callable[[], Any], payload: Dict[str, Any], dup: str, *, label: str
) -> Tuple[Optional[DbfWriteResult], str]:
    """防重单闸。返回 (拒单结果或 None, 被确认放行的上一版单号)。

    `prior == dup` 直接放过:那不是"上一版",那就是本单自己(重投必然自指)。
    """
    prior = str((payload or {}).get("prior_docnum") or "").strip()
    if not prior or prior == dup:
        return None, ""
    trn = open_trn()
    try:
        still_there = docnum_exists(trn, prior)
    finally:
        trn.close()
    if not still_there:
        return None, ""
    if D.confirmed(payload):
        return None, prior
    log.info("%s 上一版 %s 还在 → 摆提示待确认", label, prior)
    return (
        DbfWriteResult(
            False,
            error_code=ERR_PRIOR_DOC_EXISTS,
            error=D.prior_alive_message(prior, label),
            duplicate=D.prior_alive_meta(prior),
        ),
        "",
    )
