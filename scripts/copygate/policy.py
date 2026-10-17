# -*- coding: utf-8 -*-
"""两张登记表：豁免（copy-exempt.tsv）与改已有文案的同意登记（copy-approved.tsv）。

豁免   范围 <TAB> 规则 <TAB> 文案 <TAB> 原因
         范围 = <来源>:<键>        某一条词条，文案写登记时的完整当前文案（有中文写中文），文案一变就失效
                术语:<词>[@<来源>]   业务专名，只对 R-DEV / R-MIX 生效，文案列写词本身
                代码:<路径>          某个文件里的一处代码引用（R-HARD），文案列写引用的原文
         规则 = 逗号分隔的规则 ID，不许 * 也不许空
         原因 = 具体理由，≥ 6 个字，不能只写「历史遗留 / 需要 / 暂时 / 同上」

登记   来源:键 <TAB> 语言 <TAB> 新文案 <TAB> 同意日期 <TAB> 出处
         新文案必须和当前值完全一致；之后再改要新登记一行。出处指向可追查的位置（主对话日期 + 窗口、docs/work 记录、提交说明）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import rules, sources

EXEMPTABLE = {
    "R-LEN", "R-SENT", "R-WHY", "R-TALK", "R-WE", "R-HINT", "R-NARR", "R-DEV", "R-MIX", "R-PAREN",
    "R-EG", "R-HELLO", "R-EMPTY", "R-NAME", "R-ALIGN", "R-LINE20", "R-HARD",
}
TERM_RULES = {"R-DEV", "R-MIX"}
VAGUE = {"历史遗留", "需要", "暂时", "同上", "无", "-", "—", "略"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Exemption:
    scope: str
    rules: frozenset
    text: str
    reason: str
    line: int

    @property
    def row(self) -> tuple:
        return (self.scope, ",".join(sorted(self.rules)), self.text, self.reason)


@dataclass
class Approval:
    ident: str
    lang: str
    text: str
    date: str
    where: str
    line: int

    @property
    def row(self) -> tuple:
        return (self.ident, self.lang, self.text, self.date, self.where)


def _problem(message: str, where: str, snippet: str = "") -> rules.Finding:
    return rules.Finding("R-EXEMPT", "block", "", snippet[:80], message, "", where)


class Policy:
    def __init__(self, exempt_rows: list, approved_rows: list, exempt_name: str, approved_name: str):
        self.problems = []
        self.exemptions = []
        self.approvals = []
        for line, cols in exempt_rows:
            where = "%s:%d" % (exempt_name, line)
            if len(cols) < 4:
                self.problems.append(_problem("豁免行要有 范围 / 规则 / 文案 / 原因 四列", where))
                continue
            scope, rule_text, text, reason = cols[0], cols[1], cols[2], "\t".join(cols[3:]).strip()
            ids = {r.strip() for r in rule_text.split(",") if r.strip()}
            if "*" in scope or "*" in rule_text or not ids:
                self.problems.append(_problem("豁免不许通配，也不许不写规则", where, scope))
                continue
            unknown = ids - EXEMPTABLE
            if unknown:
                self.problems.append(_problem("豁免里有不认识或不许豁免的规则：%s" % ",".join(sorted(unknown)), where, scope))
                continue
            if len(reason) < 6 or reason in VAGUE:
                self.problems.append(_problem("豁免原因要写具体理由（≥ 6 字，不能只写历史遗留 / 需要 / 暂时 / 同上）", where, scope))
                continue
            if scope.startswith("术语:") and not ids <= TERM_RULES:
                self.problems.append(_problem("术语行只能豁免 R-DEV / R-MIX", where, scope))
                continue
            if not text:
                self.problems.append(_problem("豁免要绑定文案（术语行写词本身）", where, scope))
                continue
            self.exemptions.append(Exemption(scope, frozenset(ids), text, reason, line))
        for line, cols in approved_rows:
            where = "%s:%d" % (approved_name, line)
            if len(cols) < 5 or not all(cols[:5]):
                self.problems.append(_problem("登记行要有 来源:键 / 语言 / 新文案 / 同意日期 / 出处 五列且都不为空", where))
                continue
            ident, lang, text, date, src = cols[0], cols[1], cols[2], cols[3], "\t".join(cols[4:]).strip()
            if lang not in sources.LANGS or ":" not in ident or not DATE.match(date) or len(src) < 4:
                self.problems.append(_problem("登记行格式不对（语言 zh/en/th/ja，日期 YYYY-MM-DD，出处要可追查）", where, ident))
                continue
            self.approvals.append(Approval(ident, lang, text.replace("\\n", "\n"), date, src, line))
        self._approved = {(a.ident, a.lang, a.text) for a in self.approvals}

    @property
    def terms(self) -> list:
        out = []
        for item in self.exemptions:
            if item.scope.startswith("术语:"):
                word, _, scope = item.scope[3:].partition("@")
                out.append((word.strip(), scope.strip()))
        return out

    def exempt_for(self, entry) -> frozenset:
        found = set()
        for item in self.exemptions:
            if item.scope == entry.id and item.text.replace("\\n", "\n") == entry.primary:
                found |= item.rules
        return frozenset(found)

    def exempt_ref(self, ref) -> frozenset:
        found = set()
        for item in self.exemptions:
            if item.scope == "代码:%s" % ref.path and item.text == ref.text:
                found |= item.rules
        return frozenset(found)

    def approved(self, ident: str, lang: str, text: str) -> bool:
        return (ident, lang, text) in self._approved

    def validate(self, snap, tree, exempt_name: str) -> list:
        """失效的豁免：键已不存在、文案已变、代码引用已不在文件里。"""
        out = list(self.problems)
        for item in self.exemptions:
            where = "%s:%d" % (exempt_name, item.line)
            if item.scope.startswith("术语:"):
                continue
            if item.scope.startswith("代码:"):
                path = item.scope[3:]
                text = tree.read(path)
                if text is None or item.text not in text:
                    out.append(_problem("豁免已失效（文件或引用已不在），请重新确认", where, item.scope))
                continue
            entry = snap.entries.get(item.scope)
            if entry is None:
                out.append(_problem("豁免已失效（键已不存在），请重新确认", where, item.scope))
            elif item.text.replace("\\n", "\n") != entry.primary:
                out.append(_problem("豁免已失效（文案已变：现为「%s」），请重新确认" % entry.primary[:40], where, item.scope))
        return out
