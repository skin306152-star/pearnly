# -*- coding: utf-8 -*-
"""输出：Markdown 清单（--report）、终端摘要、全量审计统计（--audit）。

清单的用法：开发窗口交付时把它原样贴给用户和测试窗口 —— 改了什么文案一眼看全，不提交进仓库。
"""

from __future__ import annotations

from collections import Counter, defaultdict

from . import rules

LANG_COLS = (("zh", "中文"), ("en", "English"), ("th", "ไทย"), ("ja", "日本語"))
WIDTH = 60


def cell(text, width: int = WIDTH) -> str:
    if text is None or text == "":
        return "—"
    text = str(text).replace("\n", "⏎").replace("|", "\\|")
    if width and len(text) > width:
        text = text[: width - 1] + "…"
    return text


def _name(ident: str) -> str:
    return cell(ident.replace(":", "·", 1), 80)


def _marks(findings) -> str:
    seen = []
    for finding in findings:
        tag = finding.rule + ("" if finding.level == "block" else "↑" if finding.rule == "R-LEN" else "")
        if finding.level == "warn" and tag not in seen:
            seen.append(tag)
    return " ".join(seen)


def render_report(ctx) -> str:
    changes = ctx["changes"]
    by_kind = defaultdict(list)
    for change in changes:
        by_kind[change.kind].append(change)
    findings = ctx["findings"]
    per_entry = defaultdict(list)
    for finding in findings:
        per_entry[finding.entry_id].append(finding)
    blocks = [f for f in findings if f.level == "block"]
    warns = [f for f in findings if f.level == "warn"]
    approved = ctx["approved_ids"]
    changed = by_kind["changed"]
    lines = [
        "## 界面文案清单 基线 %s → %s" % (ctx["base_label"], ctx["head_label"]),
        "新增 %d · 改动 %d（已登记 %d / 未登记 %d）· 删除 %d · 译文改动 %d · 移动 %d · 拦截 %d · 标黄 %d · 豁免 %d（本次 +%d，失效 %d）"
        % (
            len(by_kind["new"]), len(changed), sum(1 for c in changed if c.ident in approved),
            sum(1 for c in changed if c.ident not in approved), len(by_kind["deleted"]),
            len(by_kind["translation"]), len(by_kind["moved"]), len(blocks), len(warns),
            ctx["exempt_total"], len(ctx["new_exempt"]), ctx["stale_exempt"],
        ),
        "",
    ]

    lines += ["### 1 改动已有文案（需用户先看）", ""]
    if changed:
        lines += ["| 来源·键 | 角色 | 中文 旧 → 新 | English | ไทย | 日本語 | 标记 | 登记 |", "|---|---|---|---|---|---|---|---|"]
        for change in changed:
            new, old = change.entry, change.old
            # 新文案不截断：用户确认后，这一节原样贴进 docs/work 记录，登记的出处要逐字找得到新文案
            pair = lambda lang: (  # noqa: E731
                "%s → %s" % (cell(old.texts.get(lang), 30), cell(new.texts.get(lang), 0))
                if lang in change.langs else cell(new.texts.get(lang))
            )
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                _name(change.ident), rules.ROLE_NAMES.get(new.role, new.role), pair("zh"), pair("en"), pair("th"),
                pair("ja"), _marks(per_entry[change.ident]), cell(approved.get(change.ident, "未登记")),
            ))
    else:
        lines.append("无")
    lines.append("")

    lines += ["### 2 新增文案", ""]
    if by_kind["new"]:
        lines += ["| 来源·键 | 角色 | 中文 | English | ไทย | 日本語 | 字数 | 标记 |", "|---|---|---|---|---|---|---|---|"]
        for change in by_kind["new"]:
            entry = change.entry
            size = rules.measure(entry.primary_lang, entry.primary)
            lines.append("| %s | %s | %s | %s | %s | %s | %d | %s |" % (
                _name(change.ident), rules.ROLE_NAMES.get(entry.role, entry.role),
                *(cell(entry.texts.get(lang)) for lang, _ in LANG_COLS), size, _marks(per_entry[change.ident]),
            ))
        hints = [c for c in by_kind["new"] if c.entry.role == "hint" or set(rules.key_tokens(c.entry.key)) & {
            "hint", "note", "intro", "help", "tip", "subtitle", "desc"}]
        if hints:
            lines += ["", "提示类新增（请判断是否该存在）：" + "、".join(_name(c.ident) for c in hints)]
    else:
        lines.append("无")
    lines.append("")

    lines += ["### 3 删除文案", ""]
    if by_kind["deleted"]:
        lines += ["| 来源·键 | 原中文 |", "|---|---|"]
        for change in by_kind["deleted"]:
            lines.append("| %s | %s |" % (_name(change.ident), cell(change.old.primary)))
    else:
        lines.append("无")
    lines.append("")

    lines += ["### 4 译文改动（中文未变）", ""]
    moved = by_kind["moved"]
    if by_kind["translation"] or moved:
        lines += ["| 来源·键 | 语言 | 旧 → 新 |", "|---|---|---|"]
        for change in by_kind["translation"]:
            for lang in sorted(change.langs):
                lines.append("| %s | %s | %s → %s |" % (
                    _name(change.ident), lang, cell(change.old.texts.get(lang), 30), cell(change.entry.texts.get(lang), 30)))
        for change in moved:
            lines.append("| %s | 移动 | %s → %s |" % (_name(change.ident), cell(change.old.id, 40), cell(change.entry.id, 40)))
    else:
        lines.append("无")
    lines.append("")

    lines += ["### 5 登记与豁免变动", ""]
    if ctx["new_exempt"] or ctx["new_approved"]:
        lines += ["| 文件 | 范围 | 规则 / 日期 | 文案 | 原因 / 出处 |", "|---|---|---|---|---|"]
        for row in ctx["new_approved"]:
            lines.append("| copy-approved.tsv | %s | %s %s | %s | %s |" % (
                cell(row[0], 80), row[1], row[3], cell(row[2]), cell(row[4])))
        for row in ctx["new_exempt"]:
            lines.append("| copy-exempt.tsv | %s | %s | %s | %s |" % (cell(row[0], 80), row[1], cell(row[2]), cell(row[3])))
    else:
        lines.append("无")
    lines.append("")

    if blocks:
        lines += ["### 6 拦截项", "", "| 来源·键 | 语言 | 规则 | 命中片段 | 说明 | 位置（文件:行） |", "|---|---|---|---|---|---|"]
        for finding in blocks:
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                _name(finding.entry_id or "—"), finding.lang or "—", finding.rule, cell(finding.snippet, 0),
                cell(finding.message, 0), cell(finding.loc, 0)))
        lines.append("")
    if ctx["rule_diff"]:
        lines += ["### 7 规则改动（copy-words.tsv）", "", "```diff"] + ctx["rule_diff"] + ["```", ""]
    return "\n".join(lines)


def render_console(ctx) -> str:
    by_kind = Counter(change.kind for change in ctx["changes"])
    findings = ctx["findings"]
    blocks = [f for f in findings if f.level == "block"]
    warns = [f for f in findings if f.level == "warn"]
    out = [
        "界面文案闸门：基线 %s → %s" % (ctx["base_label"], ctx["head_label"]),
        "  新增 %d · 改动 %d · 删除 %d · 译文改动 %d · 移动 %d · 代码引用新增 %d · 拦截 %d · 标黄 %d · 豁免 %d"
        % (by_kind["new"], by_kind["changed"], by_kind["deleted"], by_kind["translation"], by_kind["moved"],
           ctx["new_refs"], len(blocks), len(warns), ctx["exempt_total"]),
    ]
    if blocks:
        out.append("")
        out.append("拦下的文案（%d 处）：" % len(blocks))
        for finding in blocks:
            out.append("  [%s] %s %s「%s」—— %s  (%s)" % (
                finding.rule, finding.entry_id or "—", finding.lang or "", finding.snippet, finding.message, finding.loc))
        out.append("")
        if any(f.rule == "R-CHG" for f in blocks):
            out.append("改已有文案要先给用户看：把 --report 清单第 1 节贴进 docs/work 记录给用户确认，确认后登记到 "
                       "scripts/i18n/copy-approved.tsv（出处写那份记录的路径）。")
        out.append("口径：官方、简短、陈述事实；不写原因、过程、提示、欢迎语和开发说明，不用口语。")
        out.append("改文案过闸；确属例外的逐条登记 scripts/i18n/copy-exempt.tsv（绑定文案、写原因）。细则见 docs/development/interface-copy.md。")
    elif warns:
        out.append("  没有拦截；标黄 %d 处见 --report 清单。" % len(warns))
    return "\n".join(out)


def render_audit(findings, entries_by_source, refs_total, top: int = 50) -> str:
    blocks = [f for f in findings if f.level == "block"]
    warns = [f for f in findings if f.level == "warn"]
    lines = ["## 界面文案全量审计", "",
             "词条 %d · 代码引用 %d · 拦截级命中 %d · 标黄 %d（存量只报告，不拦）" % (
                 sum(entries_by_source.values()), refs_total, len(blocks), len(warns)), ""]
    lines += ["### 按规则", "", "| 规则 | 拦截级 | 标黄 |", "|---|---|---|"]
    rule_counts = defaultdict(lambda: [0, 0])
    for finding in findings:
        rule_counts[finding.rule][0 if finding.level == "block" else 1] += 1
    for rule, (b, w) in sorted(rule_counts.items()):
        lines.append("| %s | %d | %d |" % (rule, b, w))
    lines += ["", "### 按来源（拦截级）", "", "| 来源 | 词条数 | 命中词条 | 拦截级命中 | 各规则 |", "|---|---|---|---|---|"]
    by_source = defaultdict(list)
    for finding in blocks:
        source = (finding.entry_id or "代码:").split(":", 1)[0]
        by_source[source].append(finding)
    for source in sorted(set(entries_by_source) | set(by_source)):
        items = by_source.get(source, [])
        counts = Counter(f.rule for f in items)
        lines.append("| %s | %d | %d | %d | %s |" % (
            source, entries_by_source.get(source, 0), len({f.entry_id for f in items}), len(items),
            " ".join("%s %d" % kv for kv in sorted(counts.items()))))
    lines += ["", "### 按角色（拦截级）", "", "| 角色 | 命中 |", "|---|---|"]
    for role, count in Counter(getattr(f, "role", "") or "—" for f in blocks).most_common():
        lines.append("| %s | %d |" % (role, count))
    lines += ["", "### 前 %d 条拦截级明细" % top, "", "| 来源·键 | 语言 | 规则 | 命中片段 | 位置 |", "|---|---|---|---|---|"]
    for finding in blocks[:top]:
        lines.append("| %s | %s | %s | %s | %s |" % (
            _name(finding.entry_id or "—"), finding.lang or "—", finding.rule, cell(finding.snippet, 40), cell(finding.loc, 0)))
    return "\n".join(lines)
