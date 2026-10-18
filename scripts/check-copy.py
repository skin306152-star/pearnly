# -*- coding: utf-8 -*-
"""界面文案闸门：新增 / 改动的界面文案里的说明性碎碎念，提交前拦下；改了哪些文案，一份清单看全。

为什么有这道闸：check-i18n.py 已经让中文 / 泰文必须进字典，但"解释原因的长句、「这里会…」「请注意…」、
括号补充、开发术语、欢迎语"照样会以词条的形式进字典 —— 只靠"进字典"不够。用户口径：界面文案一律官方、
简短、干净、陈述事实；不解释原因、不用口语；能沿用官方组件自带的就用；不加欢迎语、说明段、提示语；
改**已有**的用户可见文案要先给用户看。

它判什么（细则见 docs/development/interface-copy.md，词表见 scripts/i18n/copy-words.tsv）：

  · 字典里的文案（术语表、CC 网页 i18n.ts、CC 桌面版 i18n.py、Keycloak 主题、四语表模块）：
    口语、原因解释、第一人称、提示语头、开发痕迹、寒暄与表情、按角色的长度与句数、举例、括号补充、旧称……
  · 代码里的文案引用（R-HARD）：_() / __() / tr() 的词不在字典里、JSX 与页面写死、翻译包手工行
  · 改已有文案（R-CHG）：权威语言（有中文看中文）改了，必须在 scripts/i18n/copy-approved.tsv 登记用户同意
  · 豁免与登记自检（R-EXEMPT）：失效、通配、原因空泛都拦

只判相对基线的新增与改动（差异模式，默认）；存量用 --audit 全量审计，只出报告。

用法：

    python3 scripts/check-copy.py                       # 差异模式：相对 merge-base(origin/main, HEAD)，当前 = 工作树（含未提交）
    python3 scripts/check-copy.py --report [--out F]    # 同时输出 Markdown 清单（四语并排：新增 / 改动 / 删除 / 豁免）
    python3 scripts/check-copy.py --base <rev> --head <rev>   # 比较两个提交
    python3 scripts/check-copy.py --repo <工作树> --base origin/main   # 只读地看另一个工作树
    python3 scripts/check-copy.py --staged --tree <暂存快照目录>  # 提交钩子用：只比 HEAD → 暂存快照（本次提交新增的问题）
    python3 scripts/check-copy.py --audit [--strict]    # 全量审计（--all 同义）；默认退出码 0
    python3 scripts/check-copy.py --unused-keys         # 术语表里代码没引用的键（参考，不拦）
    python3 scripts/check-copy.py --approve --source "<出处>"   # 用户确认清单第 1 节后，把未登记的改动一次写进登记表

退出码：差异模式有拦截 = 1；取不到基线 = 2。--strict 只影响 --audit（差异模式本来就拦）。
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from copygate import diff, policy, repo, report, rules, sources  # noqa: E402

WORDS_FILE = os.path.join(HERE, "i18n", "copy-words.tsv")
EXEMPT_FILE = os.path.join(HERE, "i18n", "copy-exempt.tsv")
APPROVED_FILE = os.path.join(HERE, "i18n", "copy-approved.tsv")
REL_WORDS = "scripts/i18n/copy-words.tsv"
REL_EXEMPT = "scripts/i18n/copy-exempt.tsv"
REL_APPROVED = "scripts/i18n/copy-approved.tsv"
UPSTREAM_FILE = os.path.join(HERE, "i18n", "upstream-msgids.txt")


def _toplevel() -> str:
    try:
        return repo.git(os.getcwd(), "rev-parse", "--show-toplevel").strip()
    except repo.GitError:
        return ROOT


def _assign_roles(snap, words=None, callsites=None, upstream=frozenset()) -> None:
    elsewhere = (callsites or {}).get("elsewhere")
    admin_only = (callsites or {}).get("admin_only") or set()
    for entry in snap.entries.values():
        own = entry.source.startswith("pearnly_")
        en = entry.texts.get("en")
        # 无键行的英文就是 msgid：Frappe / ERPNext 自带的 msgid 英文不归本仓（rules.Checker.check_entry 不判英文）
        if own and not entry.extra.get("keyed", True) and en in upstream:
            entry.extra["upstream_en"] = True
        # 只被控制面代码调用的共用层 msgid：按管理后台判（rules.surface_of）
        if own and (entry.key in admin_only or (en and en in admin_only)):
            entry.extra["code_admin"] = True
        if entry.extra.get("in_email"):
            # 邮件模板以外也用到的词条按它在界面上的角色判，只被邮件模板引用的才按邮件
            only_email = elsewhere is None or not (entry.key in elsewhere or entry.texts.get("en") in elsewhere)
            entry.hint_role = "email" if only_email else ""
        entry.role = rules.role_of(entry, snap.roles, words, callsites)
        entry.surface = rules.surface_of(entry, entry.role)


def _dev_patterns(words) -> list:
    return [p.regex for (rule, _lang), items in words.items() if rule == "R-DEV-TERM" for p in items]


def _load_policy(words) -> policy.Policy:
    pol = policy.Policy(
        rules.read_tsv_rows(EXEMPT_FILE), rules.read_tsv_rows(APPROVED_FILE), REL_EXEMPT, REL_APPROVED,
        _dev_patterns(words),
    )
    own = repo.WorkTree(ROOT, "闸门所在树")
    pol.verify_sources(own.read, REL_APPROVED)
    return pol


def _rows_from(tree, rel: str) -> set:
    text = tree.read(rel) if tree is not None else None
    return {tuple(cols) for _, cols in rules.parse_rows_text(text)} if text else set()


def _same_exempt(cols: tuple, row: tuple) -> bool:
    """同一条豁免：范围、文案、原因一致，规则集合一致（顺序不计）。"""
    if len(cols) < 4:
        return False
    rules_a = {r.strip() for r in cols[1].split(",") if r.strip()}
    rules_b = {r.strip() for r in row[1].split(",") if r.strip()}
    return cols[0] == row[0] and rules_a == rules_b and cols[2] == row[2] and "\t".join(cols[3:]).strip() == row[3]


def _merge_parents(root: str) -> list:
    """正在提交的是合并（解决冲突后 git commit，MERGE_HEAD 在）：合并进来的那些父提交。"""
    try:
        path = repo.git(root, "rev-parse", "--git-path", "MERGE_HEAD").strip()
    except repo.GitError:
        return []
    path = path if os.path.isabs(path) else os.path.join(root, path)
    try:
        with open(path, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]
    except OSError:
        return []


def _came_with_merge(change, merged) -> bool:
    """合并结果里这一条和某个合并进来的父提交一字不差（main 自己已有的行），或那边也删了：不是这次合并新加的。"""
    if change.entry is None:
        return all(change.old.id not in snap.entries for snap in merged)
    for snap in merged:
        old = snap.entries.get(change.entry.id)
        if old is not None and old.texts == change.entry.texts:
            return True
    return False


def _ref_came_with_merge(ref, trees, snaps, cache) -> bool:
    if ref.kind == "csv-manual":
        return any(ref.text in snap.translations.get(ref.path, set()) for snap in snaps)
    for index, tree in enumerate(trees):
        key = (index, ref.path)
        if key not in cache:
            cache[key] = {item.ident for item in sources.code_refs(ref.path, tree.read(ref.path))}
        if ref.ident in cache[key]:
            return True
    return False


def check_changes(changes, checker, pol) -> list:
    findings = []
    for change in changes:
        if change.kind not in ("new", "changed", "translation"):
            continue
        entry = change.entry
        exempt = pol.exempt_for(entry)
        findings += checker.check_entry(
            entry, change.langs, exempt, changed_from=change.old if change.kind == "changed" else None
        )
        if change.kind == "changed":
            lang = entry.primary_lang
            if not pol.approved(entry.id, lang, entry.texts.get(lang, "")):
                findings.append(rules.Finding(
                    "R-CHG", "block", lang, "%s → %s" % (change.old.texts.get(lang, "")[:30], entry.texts.get(lang, "")[:30]),
                    "改已有文案要先给用户看：把清单第 1 节给用户确认后登记", entry.id, entry.loc,
                    rules.rules_role_name(entry.role)))
    return findings


def check_refs(refs, snap, pol, known=None, upstream=frozenset()) -> list:
    findings = []
    for ref in refs:
        exempt = pol.exempt_ref(ref)
        findings += [f for f in rules.check_ref(ref, snap, known, upstream) if f.rule not in exempt]
    return findings


def run_diff(args, root, cur_tree, cur, checker, pol, upstream) -> int:
    head = args.head or "HEAD"
    base_arg = "HEAD" if args.staged and args.base == "auto" else args.base
    try:
        base_rev = diff.resolve_base(root, base_arg, head)
    except diff.BaseError as exc:
        print("界面文案闸门：%s。要么设 COPY_GATE_BASE，要么先 git fetch origin main。" % exc, file=sys.stderr)
        return 2
    base_tree = repo.GitTree(root, base_rev)
    base = sources.snapshot(base_tree)
    _assign_roles(base, checker.words)
    changes = diff.classify(base, cur)
    paths = diff.changed_code_paths(base_tree, cur_tree)
    refs = diff.new_code_refs(base_tree, cur_tree, paths) + diff.manual_translation_rows(base, cur)
    # 提交钩子遇到合并提交（把 main 并进任务分支、解决冲突后提交）：基线是第一父提交，main 带进来的行在它那里都算「新增」。
    # 只判合并结果里有、而第一父提交与合并进来的父提交里都没有的（解决冲突时手工新写的）；变基与普通提交不变
    merged_trees = [repo.GitTree(root, sha) for sha in _merge_parents(root)] if args.staged else []
    merged = [sources.snapshot(tree) for tree in merged_trees]
    if merged:
        changes = [c for c in changes if not _came_with_merge(c, merged)]
        cache: dict = {}
        refs = [r for r in refs if not _ref_came_with_merge(r, merged_trees, merged, cache)]

    findings = check_changes(changes, checker, pol)
    known = set().union(*cur.glossary_msgids.values(), *base.translations.values()) if cur.glossary_msgids else set()
    findings += check_refs(refs, cur, pol, known, upstream)
    # 豁免是否失效、本次新增的登记与豁免、规则改动：只在目标仓库自己带着这几张表时才比
    # （--repo 看一个还没变基到闸门的分支时，豁免表是本工作树的，和那个分支的文案对不上）
    own_config = cur_tree.read(REL_EXEMPT) is not None
    touched = {c.ident for c in changes} if args.staged else None
    stale = pol.validate(cur, cur_tree, REL_EXEMPT, touched) if own_config else list(pol.problems)
    findings += stale

    if args.approve:
        return approve(args, root, changes, pol)

    new_exempt, new_approved, rule_diff = [], [], []
    if own_config:
        base_exempt, base_approved = _rows_from(base_tree, REL_EXEMPT), _rows_from(base_tree, REL_APPROVED)
        new_exempt = [e.row for e in pol.exemptions if not any(_same_exempt(cols, e.row) for cols in base_exempt)]
        new_approved = [a.row for a in pol.approvals if not any(tuple(cols[:5]) == a.row for cols in base_approved)]
        base_words = base_tree.read(REL_WORDS)
        if base_words is not None:
            cur_words = cur_tree.read(REL_WORDS) or ""
            rule_diff = [line for line in difflib.unified_diff(
                base_words.splitlines(), cur_words.splitlines(), lineterm="", n=0) if not line.startswith(("---", "+++", "@@"))]
    # 词表删了行或改了行（放宽规则）、同一差异里又有文案新增 / 改动：拦 —— 词表改动单独提交、单独过 CI
    loosened = [line for line in rule_diff if line.startswith("-") and line[1:].strip() and not line[1:].lstrip().startswith("#")]
    if merged_trees:
        # 合并进来的父提交里也没有这一行：是 main 自己改的词表（它在 main 上单独过过 CI），不算这次合并放宽
        merged_words = [set((tree.read(REL_WORDS) or "").splitlines()) for tree in merged_trees]
        loosened = [line for line in loosened if all(line[1:] in words for words in merged_words)]
    copy_changes = [c for c in changes if c.kind in ("new", "changed", "translation")] or refs
    if loosened and copy_changes:
        findings.append(rules.Finding(
            "R-RULES", "block", "", loosened[0][1:60], "词表（copy-words.tsv）删改了规则，同时又有文案新增 / 改动：词表改动单独提交、单独过 CI",
            "", REL_WORDS))

    head_label = cur_tree.label
    if isinstance(cur_tree, repo.WorkTree) and not args.tree:
        try:
            head_label = "%s（工作树，含未提交）" % repo.rev_parse(root, "HEAD")[:8]
        except repo.GitError:
            pass
    approved_ids = {}
    for change in changes:
        if change.kind == "changed":
            lang = change.entry.primary_lang
            for a in pol.approvals:
                if a.ident == change.ident and a.lang == lang and a.text == change.entry.texts.get(lang, ""):
                    approved_ids[change.ident] = "%s %s" % (a.date, a.where)
    ctx = {
        "base_label": base_tree.label, "head_label": head_label, "changes": changes, "findings": findings,
        "approved_ids": approved_ids, "exempt_total": len(pol.exemptions), "new_exempt": new_exempt,
        "new_approved": new_approved, "stale_exempt": sum(1 for f in stale if "失效" in f.message),
        "rule_diff": rule_diff, "new_refs": len(refs),
    }
    print(report.render_console(ctx))
    if args.report:
        text = report.render_report(ctx)
        if args.out:
            mode = "a" if args.out == os.environ.get("GITHUB_STEP_SUMMARY") else "w"
            with open(args.out, mode, encoding="utf-8") as handle:
                handle.write(text + "\n")
        else:
            print()
            print(text)
    return 1 if any(f.level == "block" for f in findings) else 0


def approve(args, root, changes, pol) -> int:
    """用户确认清单之后：把当前全部未登记的改动写进登记表。只在用户确认之后执行（AGENTS.md）。

    出处必须是本工作树 docs/work/*.md 里的一份记录，且记录里逐字有每条新文案（把 --report 清单第 1 节贴进去）。"""
    source = (args.source or "").strip()
    docs = policy.WORK_DOC.findall(source)
    if not docs:
        print("--approve 要写 --source \"<docs/work/….md 记录路径> …\"：记录里贴着给用户确认的清单第 1 节", file=sys.stderr)
        return 2
    if os.path.realpath(root) != os.path.realpath(ROOT):
        print("--approve 只写本工作树的登记表；请在目标工作树里运行。", file=sys.stderr)
        return 2
    own = repo.WorkTree(ROOT)
    texts = [own.read(path) for path in docs]
    if not any(texts):
        print("出处记录不存在：%s" % "、".join(docs), file=sys.stderr)
        return 2
    today = datetime.date.today().isoformat()
    rows, missing = [], []
    for change in changes:
        if change.kind != "changed":
            continue
        entry = change.entry
        lang = entry.primary_lang
        text = entry.texts.get(lang, "")
        if pol.approved(entry.id, lang, text):
            continue
        if not any(doc and policy.doc_has(doc, text) for doc in texts):
            missing.append("%s「%s」" % (entry.id, text))
            continue
        rows.append("\t".join((entry.id, lang, text.replace("\n", "\\n"), today, source)))
    if missing:
        print("出处记录里没有这些新文案（先把清单第 1 节贴进记录、给用户确认）：", file=sys.stderr)
        for item in missing:
            print("  " + item, file=sys.stderr)
        return 2
    if not rows:
        print("没有未登记的改动。")
        return 0
    with open(APPROVED_FILE, "a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(row + "\n")
    print("已写入 %s（%d 行）：" % (REL_APPROVED, len(rows)))
    for row in rows:
        print("  " + row)
    return 0


def run_audit(args, cur_tree, cur, checker, pol, upstream) -> int:
    findings = []
    for entry in cur.entries.values():
        findings += checker.check_entry(entry, set(sources.LANGS), pol.exempt_for(entry))
    refs = diff.all_code_refs(cur_tree) + diff.manual_translation_rows(None, cur)
    findings += check_refs(refs, cur, pol, None, upstream)
    findings += pol.validate(cur, cur_tree, REL_EXEMPT)
    counts = {}
    for entry in cur.entries.values():
        counts[entry.source] = counts.get(entry.source, 0) + 1
    text = report.render_audit(findings, counts, len(refs), args.top)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print("审计结果写到 %s" % args.out)
    else:
        print(text)
    blocked = any(f.level == "block" for f in findings)
    return 1 if args.strict and blocked else 0


def run_unused(cur_tree, cur) -> int:
    paths = [p for p in cur_tree.paths() if sources.is_code(p) or p.endswith((".json", ".ts"))]
    cur_tree.prefetch(paths)
    corpus = "\n".join(cur_tree.read(p) or "" for p in paths)
    unused, dynamic = [], []
    for entry in sorted(cur.entries.values(), key=lambda e: e.id):
        if not entry.source.startswith("pearnly_") or not entry.extra.get("keyed"):
            continue
        if entry.key in corpus:
            continue
        prefix = re.split(r"(?<=[._])[^._]*$", entry.key)[0]
        (dynamic if prefix and prefix != entry.key and prefix in corpus else unused).append(entry)
    print("术语表里代码没有引用的键 %d 条（另有 %d 条可能是拼接出来的，只列数）：" % (len(unused), len(dynamic)))
    for entry in unused:
        print("  %s  %s" % (entry.id, entry.primary[:40]))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="界面文案闸门")
    parser.add_argument("--repo", help="仓库或工作树路径（默认：当前目录所在的仓库）")
    parser.add_argument("--base", default="auto", help="基线（默认 auto：COPY_GATE_BASE 或 origin/main 的 merge-base）")
    parser.add_argument("--head", help="当前版本取这个提交（默认读工作树，含未提交改动）")
    parser.add_argument("--tree", help="当前版本取这个目录（提交钩子的暂存快照）")
    parser.add_argument("--staged", action="store_true",
                        help="提交钩子：基线取 HEAD，只判本次提交新增的问题（分支累计的交给分支 CI）")
    parser.add_argument("--report", action="store_true", help="输出 Markdown 清单")
    parser.add_argument("--out", help="清单 / 审计写到这个文件（GITHUB_STEP_SUMMARY 追加）")
    parser.add_argument("--strict", action="store_true", help="差异模式本来就拦；--audit 时有拦截级命中退出码 1")
    parser.add_argument("--audit", "--all", dest="audit", action="store_true", help="全量审计（只报告）")
    parser.add_argument("--top", type=int, default=50, help="审计明细条数")
    parser.add_argument("--unused-keys", action="store_true", help="术语表里代码没引用的键")
    parser.add_argument("--approve", action="store_true", help="把未登记的改动写进 copy-approved.tsv（用户确认后才用）")
    parser.add_argument("--source", help="--approve 的出处")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.repo) if args.repo else _toplevel()
    if args.tree:
        cur_tree = repo.WorkTree(args.tree, "暂存快照")
    elif args.head:
        cur_tree = repo.GitTree(root, args.head)
    else:
        cur_tree = repo.WorkTree(root)
    words = rules.load_words(WORDS_FILE)
    upstream = rules.load_upstream(UPSTREAM_FILE)
    cur = sources.snapshot(cur_tree)
    _assign_roles(cur, words, sources.callsite_roles(cur_tree), upstream)
    pol = _load_policy(words)
    checker = rules.Checker(words, ROOT, pol.terms)

    if args.unused_keys:
        return run_unused(cur_tree, cur)
    if args.audit:
        return run_audit(args, cur_tree, cur, checker, pol, upstream)
    return run_diff(args, root, cur_tree, cur, checker, pol, upstream)


if __name__ == "__main__":
    sys.exit(main())
