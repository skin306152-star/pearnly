"""对账预设：规矩类的不确定只问人一次，按账户 / 公司记住，以后同类照答案处理（用户 2026-09-30 批准）。

三个对账口共用这套口径：
- 检测：对账时遇到系统自己定不了、但定一次规矩就能定的地方（``questions``），不猜，列成一个问题；
- 回答：人选一个答案即生效（没有另外的确认步骤），存成这个账户 / 公司的预设，已识别的数据当场重算，不再识别、不扣费；
- 复用：以后同一账户 / 公司的同类情况直接按预设处理、结果里标「按预设」，不再问；
- 不硬套：预设的应用本身带校验（汇总入账只收合计正好相等、只有一种凑法的组），数据对不上预设的照旧列出来交人看。
读错字（像素级）的问题不在这里：那类靠余额链 / 合计核对指出位置，交人改数。

纯逻辑：不依赖框架、数据库或网络；存取在 ``preset_store``。
"""
import re
from collections import Counter

YES, NO = "yes", "no"

# 认不出银行的通用版式账单（读账单时的银行码 ``generic``）在范围里的写法。
GENERIC = "GENERIC"

# 银行：这个账户的「多笔对一笔」是汇总入账（总账逐笔、银行一笔合并入账；或反过来信用卡逐笔、总账一笔合计）。
GROUPED = "grouped"

# 收入：总账里这个收入科目不在税报里（利息、其他收入等不开增值税发票的），不参与对账。按「公司税号 + 科目」记。
EXCLUDED = "excluded"

# 银行：这个账户（银行 + 账号尾号）对总账哪个科目。全科目总账里几个科目都像、分不出时问；答案是科目号（不是选项）。
GL_ACCOUNT = "gl_account"
# 银行：这个账户的刷卡入账是扣了手续费的净额，按这个费率（百分数文字，如 "1.95"）配总账刷卡毛额；答 "no" 表示不是。
CARD_FEE = "card_fee"
# 答案是自由文字（科目号）的规则，不是固定选项。
FREE = None
# 答了要用原文件重跑一遍的规则（读总账时就按科目筛了，存下的数据里没有别的科目）；识别结果复用，不再识别。
RERUN = {("bank", GL_ACCOUNT)}

RULES = {
    "bank": {GROUPED: (YES, NO), GL_ACCOUNT: FREE, CARD_FEE: FREE},
    "income": {EXCLUDED: (YES, NO)},
    "vat": {},
}


def valid(kind, rule, value):
    rules = RULES.get(kind, {})
    if rule not in rules:
        return False
    if rule == CARD_FEE:
        from pearnly_cowork.reconciliation.bank_card_fee import parse_rate

        return value == NO or parse_rate(value) is not None
    if rules[rule] is FREE:
        return isinstance(value, str) and 0 < len(value.strip()) <= 40
    return value in rules[rule]


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def account_tail(values):
    """几份账单读出的账号取最常见的末四位（读出的账号有全号、有尾号；没有就空）。"""
    tails = Counter(_digits(value)[-4:] for value in values if len(_digits(value)) >= 4)
    return tails.most_common(1)[0][0] if tails else ""


def statement_tail(bank_code, files):
    """这份对账的账号尾号：读成功的几份账单（``parse_info.stmt_files`` 的记录）里最常见的。

    认不出银行的通用版式只算账单上读出的账号（``account_read``：识别读出的账户、表头旁标着「账号」的），不算退回的工作表名：
    按月分表的表名是月份 / 年份（Jan 2026、01-2569），当尾号会让同工作区别家公司的通用版式账单共用一份预设（复查 2026-10-13）。
    这条之前建的通用版式任务没有 ``account_read``，照旧不记。"""
    generic = str(bank_code or "").strip().upper() == GENERIC
    return account_tail(item.get("account") for item in files or []
                        if item.get("ok") and (item.get("account_read") or not generic))


def ledger_account(codes):
    """总账行里最常见的科目号（单科目总账没指定科目时任务上的科目是空的）。"""
    counted = Counter(str(code or "").strip() for code in codes if str(code or "").strip())
    return counted.most_common(1)[0][0] if counted else ""


def bank_scope(bank_code, tail, gl_account_code):
    """银行预设按「银行 + 账号尾号 + 总账科目」记：一个工作区里有多家客户公司，同一银行、同一科目号可能是
    不同公司的账户，所以至少要有尾号或科目号之一，否则不记（不问，照旧只提示）。

    认不出银行的通用版式（``GENERIC``：表格账单、文件名没写银行的照片）只有读出 4 位尾号才记（尾号只算账单上读出的，见
    ``statement_tail``）：没有银行这一维，只凭科目号会把同工作区别家公司的答案套过来。不用识别读出的银行名补银行码：
    逐月读法可能不一样，记忆键要稳定。"""
    bank = str(bank_code or "").strip().upper()
    tail, account = _digits(tail)[-4:], str(gl_account_code or "").strip()
    if not bank or not (tail or account) or (bank == GENERIC and len(tail) < 4):
        return ""
    return "|".join(("bank", bank, tail or "-", account or "-"))


def income_scope(tax_id, account):
    """收入预设按「本公司税号（税报表头）+ 总账科目」记；税号读不出不记（不问）。"""
    tax, account = _digits(tax_id), str(account or "").strip()
    if len(tax) != 13 or not account or len(account) > 40:
        return ""
    return "|".join(("income", tax, account))


def income_prefix(tax_id):
    tax = _digits(tax_id)
    return f"income|{tax}|" if len(tax) == 13 else ""


def account_scope(bank_code, tail):
    """科目对应按「银行 + 账号尾号」记（科目号正是要问的，不能拿来当范围）；没有尾号不记。"""
    return bank_scope(bank_code, tail, "") if _digits(tail) else ""


def scope_label(scope):
    """给人看的范围：``KBANK ···4821 · 1112-01``、``0105556012345 · 4201``；通用版式不写银行：``···4821 · 1112-01``。"""
    parts = str(scope or "").split("|")
    if len(parts) == 3 and parts[0] == "income":
        return f"{parts[1]} · {parts[2]}"
    if len(parts) == 4 and parts[0] == "bank":
        _kind, bank, tail, account = parts
        head = " ".join(item for item in (bank if bank != GENERIC else "", f"···{tail}" if tail != "-" else "") if item)
        return " · ".join(item for item in (head, account if account != "-" else "") if item)
    return str(scope or "")
