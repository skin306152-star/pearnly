# -*- coding: utf-8 -*-
"""重复投递的处置口径:系统自作主张的重投照挡,会计改了内容主动再推则提示不拦。

2026-07-27 TEST 靶场实测(S3):把 1070 改成 2140、票号不动、重推 —— 幂等钥匙只有
REFNUM+SUPCOD+RECTYP,金额不在钥匙里 —— 判为重复,返回 ok=true / skipped=true,而账套里
仍是 1070。会计核完表改的数字被静默丢弃,她却以为推成功了。万能口的主场景正是「核完表改了
数字塞回来说推这批」,她不会再核第二遍,所以这条谎会一路走到申报。

于是把「是不是她的本意」分开处置:

  · **系统的重投**(网络丢包重发 / 自动重试 / 连点两下 / ack 丢失后云端重投):内容指纹与
    账套里那张一模一样 → 照旧幂等跳过。她没想推第二次,挡住是帮她。
  · **她改了内容主动再推**:内容指纹对不上 → 不写、也不静默,把「ERP 里现在是多少 / 你这份
    是多少」逐字段摆回去,由她定(再落一张 / 去 Express 改原单 / 算了)。带
    `duplicate_confirmed=true` 重推即真写新单,选哪个都执行。

判据是**内容指纹**不是钥匙:钥匙只回答「是不是同一张票」,指纹回答「内容动没动过」。

本模块零依赖(只用标准库)。桥与小助手各存一份**逐字节相同**的副本 —— 两条在产写路不共享
包根,而「同一载荷两侧结论必须一致」是硬要求,靠 test_duplicate_gate 的副本一致性断言钉死。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

# 检出「同钥匙、内容不同」→ 本次不写,把差异摆给会计看。重试多少次结论都一样,只有人能定,
# 所以云端该归 needs_review,别当暂时失败一遍遍重刷。
ERR_DUPLICATE_DIFFERS = "DUPLICATE_CONTENT_DIFFERS"

# 「知道了,还是推」的载荷参数。四条写路(采购/销项/四类新单据/防重单闸)共用同一个名字 ——
# 会计在界面上点的是同一个按钮,参数分家只会让某条路悄悄不认。
CONFIRM_PARAM = "duplicate_confirmed"

REASON_DIFFERS = "content_differs"  # 同钥匙但内容对不上
REASON_PRIOR_ALIVE = "prior_version_alive"  # 上一版凭证号还活在账套里

# 账套里的钱是 VFP double,载荷侧是 Decimal —— 严格比会把系统自己的重投判成「内容不同」,
# 于是每次网络重发都要惊动会计。与写盘读回校验同一个容差,两处判「同一张单」才不会打架。
MONEY_TOL = Decimal("0.02")

_CENT = Decimal("0.01")

# 单头可比字段:(载荷侧键, DBF 列, 给会计看的中文名, 是不是钱)。
# NETVAL/VATAMT/NETAMT 三列在赊/现/无税三种 FLGVAT 口径下含义恒定(见 dbf_fields.amount_fields),
# 拿它们比才不会因为单别不同把同一张单判成两张。
HEAD_FIELDS: Tuple[Tuple[str, str, str, bool], ...] = (
    ("base_amount", "NETVAL", "税前金额", True),
    ("vat_amount", "VATAMT", "税额", True),
    ("total_amount", "NETAMT", "含税总额", True),
    ("doc_date", "DOCDAT", "单据日期", False),
)


@dataclass(frozen=True)
class FieldDiff:
    """一处内容差异。`in_erp` 是账套里现在的值,`incoming` 是这次推的值。"""

    field: str
    label: str
    in_erp: str
    incoming: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "field": self.field,
            "label": self.label,
            "in_erp": self.in_erp,
            "incoming": self.incoming,
        }


def confirmed(payload: Optional[Mapping[str, Any]]) -> bool:
    """会计是否已按下「知道了,还是推」。

    只认真正的真值:字符串 'false'/'0' 从表单/查询串里过来是常态,当成 True 就等于替她按了
    确认 —— 而这个按钮的后果是账上多一张单。
    """
    v = (payload or {}).get(CONFIRM_PARAM)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(v)


def incoming_head(base: Any, vat: Any, total: Any, doc_date: Any) -> Dict[str, Any]:
    """本次载荷的单头指纹投影(键与 HEAD_FIELDS 的载荷侧键对齐)。"""
    return {
        "base_amount": base,
        "vat_amount": vat,
        "total_amount": total,
        "doc_date": doc_date,
    }


def head_row(rows: Iterable[Any], docnum: str) -> Dict[str, Any]:
    """从活行里取出该凭证号的单头投影(只取要比的列)。找不到返回空 dict。

    调用方传 live_rows(软删行不算数):会计在 Express 删掉的那张不该参与比对。

    同号多张时取**最后**一张。销项的 DOCNUM 就是票面票号,会计确认「还是推」之后落的第二张
    与第一张同号;取第一条就永远拿旧的那张去比,而队列是 at-least-once —— 丢一次 ack 重投
    就再比出一次差异、再落一张,每丢一次多一张且无上界。取最后一张,重投比的是刚写的那张,
    内容一致 → 幂等跳过,自愈。
    """
    want = (docnum or "").strip()
    if not want:
        return {}
    cols = tuple(c for _, c, _, _ in HEAD_FIELDS)
    latest: Dict[str, Any] = {}
    for r in rows:
        if str(getattr(r, "DOCNUM", "") or "").strip() == want:
            latest = {c: getattr(r, c, None) for c in cols}
    return latest


def compare(
    erp_row: Mapping[str, Any], incoming: Mapping[str, Any]
) -> Tuple[FieldDiff, ...]:
    """账套里那张 vs 本次载荷,逐字段比出差异。空元组 = 内容指纹一致 = 系统的重投。

    读不出来的列跳过不比:拿「读不到」当「不同」,会把纯粹的网络重发逼成人工确认。真正会
    动的那几列(金额/日期)在 APTRN/ARTRN 上恒存在,跳过只发生在表结构异常时。
    """
    out = []
    for key, column, label, is_money in HEAD_FIELDS:
        got, want = erp_row.get(column), incoming.get(key)
        if got is None or want is None:
            continue
        if is_money:
            a, b = _money(got), _money(want)
            if a is None or b is None or abs(a - b) <= MONEY_TOL:
                continue
            out.append(FieldDiff(key, label, _money_text(a), _money_text(b)))
        else:
            a, b = _day(got), _day(want)
            if a is None or b is None or a == b:
                continue
            out.append(FieldDiff(key, label, a.isoformat(), b.isoformat()))
    return tuple(out)


def differs_meta(docnum: str, diffs: Tuple[FieldDiff, ...]) -> Dict[str, Any]:
    """检出「同钥匙内容不同」时回云端的结构化差异(管家/录入台照它渲染)。"""
    return {
        "reason": REASON_DIFFERS,
        "docnum": docnum,
        "confirm_param": CONFIRM_PARAM,
        "fields": [d.as_dict() for d in diffs],
    }


def differs_message(docnum: str, diffs: Tuple[FieldDiff, ...]) -> str:
    detail = " · ".join(f"{d.label} {d.in_erp} → {d.incoming}" for d in diffs)
    return (
        f"这张票之前推过,ERP 里是 {docnum}({detail})· 本次一个字节都没写 · "
        f"确认要再落一张就带 {CONFIRM_PARAM}=true 重推;想改原单请在 Express 里改 {docnum}"
    )


def prior_alive_meta(docnum: str) -> Dict[str, Any]:
    """上一版凭证号还活着时回云端的结构化提示。

    `fields` 恒空:票号一改幂等就认不出账套里那张,拿不到可逐列对照的既有单头 —— 能诚实说
    的只有「上一版还在,号是这个」。硬编个差异表比不说更坏。
    """
    return {
        "reason": REASON_PRIOR_ALIVE,
        "docnum": docnum,
        "confirm_param": CONFIRM_PARAM,
        "fields": [],
    }


def prior_alive_message(docnum: str, label: str = "单据") -> str:
    return (
        f"ERP 里上一版{label} {docnum} 还在 · 本次一个字节都没写 · "
        f"确认要再落一张就带 {CONFIRM_PARAM}=true 重推;"
        f"想让改动落到原单请在 Express 里改或删 {docnum}"
    )


def duplicate_note(docnum: str) -> Dict[str, str]:
    """幂等命中(内容一致的重投被挡下)时的诚实标注。

    布尔 `skipped` 之外必须有一句人话:推送日志上「这次真写进去了」与「本来就有,我们什么
    都没做」长得一模一样,只给布尔等于让会计去 ERP 里翻。
    """
    return {
        "duplicate_of": (
            f"账套里已有内容完全相同的单 {docnum} · 判为系统重投(网络重发/自动重试/"
            f"连点两下),本次一个字节都没写 · 改过金额或日期的重推不会走到这里,"
            f"那种会先把差异摆给你看再由你定"
        )
    }


def confirmed_note(docnum: str) -> Dict[str, str]:
    """会计确认后真落第二张时的诚实标注 —— 账上从此有两张,得让她知道。"""
    if not docnum:
        return {}
    return {
        "duplicate_confirmed": (
            f"你确认过「知道了,还是推」· 本单是新落的第 2 张,ERP 里 {docnum} 仍在 · "
            f"两张都在账上,如需只留一张请在 Express 里删掉不要的那张"
        )
    }


def _money(v: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _money_text(v: Decimal) -> str:
    return f"{v.quantize(_CENT):,}"


def _day(v: Any) -> Optional[dt.date]:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None
