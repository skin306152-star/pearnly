"""Invoice sanity from ec2e5c957e6b with Decimal arithmetic and acyclic same-page rules.

Warning prose is retained for evidence. A warning is a review decision, not
permission to post. infer_missing_discount explicitly mutates its input; call
it on a copy and retain DISCOUNT_INFERRED_PREFIX in the review evidence.
"""
from __future__ import annotations
from decimal import Decimal
from typing import List
from pearnly_cowork.ocr.money import normalize_id as _digits, normalize_money as _money, valid_thai_tax_id as _valid_tax_id
from pearnly_cowork.ocr.validation.math import exact_money as _exact_money

DISCOUNT_INFERRED_PREFIX = 'discount_inferred:'
_TOL = Decimal('0.5')
_VAT_RATE = Decimal('0.07')
_RECON_REL = Decimal('0.02')

def _line_subtotals(invoice) -> List[Decimal]:
    out: List[Decimal] = []
    for it in getattr(invoice, 'items', None) or []:
        amt = _money(getattr(it, 'subtotal', None))
        if amt is not None and amt > 0:
            out.append(amt)
    return out

@_exact_money
def vat_inclusive_lines(line_sum: Decimal, sub: Decimal, vat: Decimal | None, total: Decimal | None, discount: Decimal | None=None) -> bool:
    """明细列印的是含税价(VAT 内含)吗 — 是的话行和 > 小计是票面本来的样子,不是读错。

    泰国 ABB 小票(ใบกำกับภาษีอย่างย่อ · Ocha/POS 出单)逐行印含税价,表尾才把含税
    合计倒推成税前小计 + VAT:行和 2690.00 配小计 2514.02,差的 175.98 正是那笔税。
    规则 6 原样判会把这一整类票当成「结构不可能」,每张都白回落一次 Vision 重跑。

    三条同时成立才放行,比折扣分支还紧:
      ① 行和 ≈ 含税合计   — 含税印法的直接证据;任一行读错就对不上,照样被抓
      ② 小计 + VAT ≈ 合计 — 税前/税/含税三者自洽
      ③ VAT ≈ 净额 × 7%   — 小计是按法定税率倒推的,不是随手一个数
    ② ③ 与规则 4b/4c 判据重复是刻意的:豁免必须自证,不能因为别处闸被改松而跟着变宽。
    判据全走数字不看 document_type —— 票种是模型自己标的,ABB 常被标成 tax_invoice,
    拿它当前提等于把误杀留给标错的那批。
    """
    if vat is None or vat <= 0 or total is None or (total <= 0):
        return False
    if abs(line_sum - total) > max(_TOL, total * _RECON_REL):
        return False
    if abs(sub - (discount or Decimal('0.0')) + vat - total) > max(_TOL, total * _RECON_REL):
        return False
    return vat_ratio_mismatch(sub, vat, discount) is None

@_exact_money
def line_sum_mismatch(sub, lines: List[Decimal], symmetric: bool=False, *, discount: Decimal | None=None, total: Decimal | None=None, vat: Decimal | None=None) -> Decimal | None:
    """明细行和是否与小计不平,不平则返回行和(供调用方拼消息),平则 None。

    单侧(规则 6·默认)只判「行和 > 小计」——行只会漏读不会多出钱,合法漏行
    (行和 < 小计)不误杀。双侧(规则 7·多票页收紧,见 sanity_multi)连「行和 <
    小计」也判,因为跨票错配会把行和往两边带偏,不能再放过偏低的一侧。
    两侧共用同一容差,消息措辞由调用方按各自语境拼(单票"结构不可能" vs
    多票页"跨票错配"),不在此耦合。
    """
    if sub is None or sub <= 0 or (not lines) or (len(lines) < 2):
        return None
    line_sum = sum(lines)
    tol = max(_TOL, sub * _RECON_REL)
    if discount and discount > 0:
        after_discount = line_sum - discount
        if abs(after_discount - sub) <= tol:
            return None
        if total is not None and abs(after_discount - total) <= max(tol, total * _RECON_REL):
            return None
    if line_sum > sub + tol or (symmetric and line_sum < sub - tol):
        if vat_inclusive_lines(line_sum, sub, vat, total, discount):
            return None
        return line_sum
    return None

@_exact_money
def vat_ratio_mismatch(sub: Decimal | None, vat: Decimal | None, discount: Decimal | None) -> str | None:
    """VAT 是否为净额的法定 7% — 与规则 4b(总额勾稽)独立的第二道校验。

    抓「小计/VAT/总额三者被同时误读却仍恰好互相自洽」这类规则 4b 抓不住的自洽性
    幻觉(sanity.py 顶部诚实边界写的缺口之一;NBC 折扣票实案 2026-07-08:69→60/
    8→3 两位误读,sub+vat=total 仍平,但 VAT 不再是净额的 7%)。折扣两种印法
    (折前/折后)任一口径对上即放行,不误杀折扣票(同规则 4b/f003)。
    """
    if vat is None or vat <= 0 or sub is None or (sub <= 0):
        return None
    gross_expected = sub * _VAT_RATE
    net_sub = sub - (discount or Decimal('0.0'))
    net_expected = net_sub * _VAT_RATE if net_sub > 0 else gross_expected
    diff = min(abs(vat - gross_expected), abs(vat - net_expected))
    tol = max(_TOL, min(gross_expected, net_expected) * _RECON_REL)
    if diff <= tol:
        return None
    return f'VAT {vat} 既非小计 {sub} × 7%({gross_expected:.2f})也非净额(折后){net_sub:.2f} × 7%({net_expected:.2f})(差 {diff:.2f}) — 疑小计/VAT 单数位误读(自洽性幻觉)'

@_exact_money
def evaluate_sanity(invoice) -> List[str]:
    """返回硬否决原因列表(空=通过)。命中任一 → 调用方强制转人工,绝不 auto。

    每条规则都保守:只在「结构上不可能对」时触发,宁可漏抓(交给软闸/人工)也不误杀
    正常票(误杀 = 凭空增加人工量 + 失去信任)。

    同页多票(additional_invoices):附加票逐张过核心规则 + 规则 7 跨票错配核对,
    见 sanity_multi(该模块延迟导入本模块的共享件;本模块可放心顶层导它,无环)。
    """
    if getattr(invoice, 'is_not_invoice', False):
        return []
    return _core_reasons(invoice) + multi_invoice_reasons(invoice)

def _core_reasons(invoice) -> List[str]:
    reasons: List[str] = []
    sub = _money(getattr(invoice, 'subtotal', None))
    vat = _money(getattr(invoice, 'vat', None))
    total = _money(getattr(invoice, 'total_amount', None))
    discount = _money(getattr(invoice, 'discount', None))
    is_credit_note = getattr(invoice, 'document_type', '') == 'credit_note'
    if not is_credit_note:
        for (name, val) in (('total_amount', total), ('subtotal', sub), ('vat', vat)):
            if val is not None and val < 0:
                reasons.append(f'{name} 为负数({val}) — 发票金额不可能为负')
    (st, bt) = (_digits(getattr(invoice, 'seller_tax', None)), _digits(getattr(invoice, 'buyer_tax', None)))
    if st and bt and (len(st) >= 10) and (st == bt):
        reasons.append(f'卖方税号 == 买方税号({st}) — 大概率串了表头税号')
    lines = _line_subtotals(invoice)
    if total is not None and total > 0 and lines and (not (discount and discount > 0)):
        biggest = max(lines)
        if total < biggest - _TOL:
            reasons.append(f'总额 {total} < 单条明细 {biggest} — 不可能(疑选错列)')
    if vat is None and sub is not None and (total is not None) and (sub > 0):
        net = sub - (discount or Decimal('0.0'))
        diff = total - net
        expected_vat = net * _VAT_RATE
        ok_zero = abs(diff) <= _TOL
        ok_vat = abs(diff - expected_vat) <= max(_TOL, expected_vat * _RECON_REL)
        if not ok_zero and (not ok_vat):
            reasons.append(f'缺 VAT 且总额 {total} != 净额 {net:.2f}(小计 {sub} − 折扣 {discount or 0}),差 {diff:.2f} 既非 0 也非 7%({expected_vat:.2f}) — 勾稽不平')
    if vat is not None and sub is not None and (total is not None) and (sub > 0):
        gross_diff = abs(sub + vat - total)
        net_diff = abs(sub - (discount or Decimal('0.0')) + vat - total)
        if min(gross_diff, net_diff) > _TOL:
            reasons.append(f'总额 {total} != 小计 {sub} − 折扣 {discount or 0} + VAT {vat}(差 {min(gross_diff, net_diff):.2f}) — 勾稽不平(疑漏折扣行/选错列)')
    ratio_reason = vat_ratio_mismatch(sub, vat, discount)
    if ratio_reason:
        reasons.append(ratio_reason)
    for (name, raw) in (('seller_tax', st), ('buyer_tax', bt)):
        if len(raw) == 13 and (not _valid_tax_id(raw)):
            reasons.append(f'{name} {raw} 校验位不符 — 13 位但 MOD-11 不过(疑读错一位)')
    line_sum = line_sum_mismatch(sub, lines, discount=discount, total=total, vat=vat)
    if line_sum is not None:
        reasons.append(f'明细行和 {line_sum:.2f} > 小计 {sub} — 结构不可能(疑小计/总额读错一位)')
    return reasons

def credit_note_review_reason(invoice) -> str | None:
    """贷记单(ใบลดหนี้)= 方向性单据,一律转人工确认,不许当普通发票静默过账。

    P1 台账 #8(2026-07-05 实弹):票面负数被读成正数 → 退货冲销当正常进账,方向反。
    金额符号真伪机器无法自证(真实贷记单正负印法都有),方向只能人工把关。
    两条链(直读/Vision 路)共用本判定。
    """
    if getattr(invoice, 'is_not_invoice', False):
        return None
    if getattr(invoice, 'document_type', '') != 'credit_note':
        return None
    return 'credit_note — 冲销方向需人工确认(金额符号以票面为准)'

@_exact_money
def infer_missing_discount(invoice) -> str | None:
    """折扣确定性反推:折扣缺失但「小计 + VAT − 总额」的差额 D 恰好使
    (小计 − D) × 7% ≈ VAT 时,票面几乎必然印了一行 ส่วนลด 被漏抓 → 回填 D。

    双重勾稽(差额 + VAT 税基)同时成立才回填,单一差额不动手 —— 那可能是选错列,
    交给规则 4b 转人工。回填成功返回说明文字(进 validation_warnings 留痕),否则 None。

    ⚠️ 回填会改写票面钱字段,且改完 _check_amount_math 与本模块硬闸都自动放行
    (实测 triggers 由 ['amount math fail...'] 变 [])。消费方必须据 DISCOUNT_INFERRED_PREFIX
    强制留人:闸的职责是报告差额,不是把票改到闸能过。
    """
    if getattr(invoice, 'is_not_invoice', False):
        return None
    if _money(getattr(invoice, 'discount', None)) is not None:
        return None
    sub = _money(getattr(invoice, 'subtotal', None))
    vat = _money(getattr(invoice, 'vat', None))
    total = _money(getattr(invoice, 'total_amount', None))
    if sub is None or vat is None or total is None or (sub <= 0) or (vat <= 0):
        return None
    d = sub + vat - total
    if d <= _TOL:
        return None
    expected_vat = (sub - d) * _VAT_RATE
    if abs(expected_vat - vat) > max(_TOL, vat * _RECON_REL):
        return None
    invoice.discount = f'{d:.2f}'
    return f'{DISCOUNT_INFERRED_PREFIX} 票面折扣 {d:.2f} 未被提取,由勾稽差额+7%税基双重校验反推回填'

def multi_invoice_reasons(invoice) -> List[str]:
    extras = [x for x in getattr(invoice, 'additional_invoices', None) or [] if not getattr(x, 'is_not_invoice', False)]
    if not extras:
        return []
    reasons: List[str] = []
    for (i, extra) in enumerate(extras, start=2):
        reasons.extend((f'同页第{i}张: {r}' for r in _core_reasons(extra)))
    for (i, inv) in enumerate([invoice] + extras, start=1):
        sub = _money(getattr(inv, 'subtotal', None))
        line_sum = line_sum_mismatch(sub, _line_subtotals(inv), symmetric=True, total=_money(getattr(inv, 'total_amount', None)), vat=_money(getattr(inv, 'vat', None)))
        if line_sum is not None:
            reasons.append(f'同页第{i}张: 明细行和 {line_sum:.2f} ≠ 小计 {sub} — 多票页疑金额跨票错配')
    return reasons
