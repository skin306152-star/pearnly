# -*- coding: utf-8 -*-
"""基线解析与分类：新增 / 改动 / 删除 / 移动 / 译文改动。

基线（`--base auto`）：
  1. 环境变量 COPY_GATE_BASE 有值且不是全 0：取它和 HEAD 的 merge-base（CI：PR 传 base.sha，推 main 传 event.before）
  2. 否则 `git merge-base origin/main HEAD`（分支推送、本地、提交钩子）
  3. 都取不到：退出码 2，判失败，不跳过
显式 `--base <rev>` 同样取 merge-base(<rev>, HEAD)：给的是祖先就是它本身。

分类（按词条 id = 来源:键）：
  新增       基线里没有这个键
  改动       键还在，权威语言（有中文看中文，否则看英文）变了；无键的术语表行 msgid 一改会变成"删一条加一条"，
             同一 diff 段里一减一加且文字相近的、或中文相同且英文相似度 ≥ 0.6 的，配对成改动 / 译文改动
  移动       换了键、四种语言文字都没变：不算改动（防止靠换键名绕开登记，换键 + 改字仍按新增 + 删除算）
  删除       只列出，不拦
  译文改动   权威语言没变、其他语言变了：不需要登记，但变了的那门语言照样过内容判据
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field

from . import repo, sources

ZERO = "0" * 40


class BaseError(RuntimeError):
    pass


def resolve_base(root: str, base: str, head: str = "HEAD") -> str:
    """返回基线提交（完整 SHA）。"""
    tried = []
    candidates = []
    if base and base != "auto":
        candidates.append(base)
    else:
        env = (os.environ.get("COPY_GATE_BASE") or "").strip()
        if env and env.strip("0"):
            candidates.append(env)
        candidates.append("origin/main")
    for candidate in candidates:
        try:
            return repo.git(root, "merge-base", candidate, head).strip()
        except repo.GitError as exc:
            tried.append("%s（%s）" % (candidate, str(exc).splitlines()[0] if str(exc) else "无"))
    raise BaseError("取不到基线：" + "；".join(tried))


@dataclass
class Change:
    kind: str  # new / changed / translation / moved / deleted
    entry: object = None  # 当前版本（删除时为 None）
    old: object = None  # 基线版本（新增时为 None）
    langs: set = field(default_factory=set)  # 变了的语言（新增 = 全部）

    @property
    def ident(self) -> str:
        return (self.entry or self.old).id


def _authority(entry_a, entry_b) -> str:
    has_zh = (entry_a and entry_a.texts.get("zh")) or (entry_b and entry_b.texts.get("zh"))
    return "zh" if has_zh else "en"


def _diff_langs(old, new) -> set:
    return {lang for lang in sources.LANGS if (old.texts.get(lang) or "") != (new.texts.get(lang) or "")}


def _texts_key(entry) -> tuple:
    return tuple(entry.texts.get(lang, "") for lang in sources.LANGS)


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _pair(old, new) -> Change:
    langs = _diff_langs(old, new)
    if not langs:
        return Change("moved", new, old, set())
    kind = "changed" if _authority(old, new) in langs else "translation"
    return Change(kind, new, old, langs)


def classify(base, cur) -> list:
    """base / cur：sources.Snapshot。"""
    changes = []
    base_ids, cur_ids = set(base.entries), set(cur.entries)
    for ident in sorted(base_ids & cur_ids):
        old, new = base.entries[ident], cur.entries[ident]
        langs = _diff_langs(old, new)
        if not langs:
            continue
        kind = "changed" if _authority(old, new) in langs else "translation"
        changes.append(Change(kind, new, old, langs))

    added = {i: cur.entries[i] for i in cur_ids - base_ids}
    removed = {i: base.entries[i] for i in base_ids - cur_ids}

    # 移动：同一来源里换了键，文字一个字没变
    by_text: dict = {}
    for ident, entry in removed.items():
        by_text.setdefault((entry.source, _texts_key(entry)), []).append(ident)
    for ident in sorted(added):
        entry = added[ident]
        bucket = by_text.get((entry.source, _texts_key(entry)))
        if bucket:
            old_id = bucket.pop(0)
            changes.append(Change("moved", entry, removed.pop(old_id), set()))
            del added[ident]

    # 术语表：同一 diff 段里一减一加、文字相近的配对（msgid 改写、换键改字都在这里接住）
    for path, cur_lines in cur.glossary_lines.items():
        base_lines = base.glossary_lines.get(path)
        if base_lines is None:
            continue
        base_map = base.glossary_line_ids.get(path, {})
        cur_map = cur.glossary_line_ids.get(path, {})
        matcher = difflib.SequenceMatcher(None, base_lines, cur_lines, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "replace":
                continue
            for offset in range(min(i2 - i1, j2 - j1)):
                old_id = base_map.get(i1 + offset + 1)
                new_id = cur_map.get(j1 + offset + 1)
                if old_id not in removed or new_id not in added:
                    continue
                old, new = removed[old_id], added[new_id]
                if max(_similar(old.texts.get("en", ""), new.texts.get("en", "")),
                       _similar(old.texts.get("zh", ""), new.texts.get("zh", ""))) < 0.5:
                    continue
                changes.append(_pair(old, new))
                del removed[old_id], added[new_id]

    # 无键行：中文相同、英文相似度 ≥ 0.6，是 msgid 改写（中文没变 → 译文改动）
    for new_id in sorted(added):
        new = added[new_id]
        if new.extra.get("keyed", True) or not new.texts.get("zh"):
            continue
        best, score = None, 0.6
        for old_id, old in removed.items():
            if old.source != new.source or old.extra.get("keyed", True) or old.texts.get("zh") != new.texts["zh"]:
                continue
            ratio = _similar(old.texts.get("en", ""), new.texts.get("en", ""))
            if ratio >= score:
                best, score = old_id, ratio
        if best:
            changes.append(_pair(removed.pop(best), new))
            del added[new_id]

    for ident in sorted(added):
        changes.append(Change("new", added[ident], None, set(sources.LANGS)))
    for ident in sorted(removed):
        changes.append(Change("deleted", None, removed[ident], set()))
    return changes


def changed_code_paths(base_tree, cur_tree) -> list:
    """两棵树之间内容变了的代码文件（新增、修改、删除都算）。"""
    base_paths = {p for p in base_tree.paths() if sources.is_code(p)}
    cur_paths = {p for p in cur_tree.paths() if sources.is_code(p)}
    out = []
    for path in sorted(base_paths | cur_paths):
        if base_tree.blob_id(path) != cur_tree.blob_id(path):
            out.append(path)
    return out


def new_code_refs(base_tree, cur_tree, paths) -> list:
    """改动文件里新出现的代码引用。"""
    base_tree.prefetch(paths)
    cur_tree.prefetch(paths)
    out = []
    for path in paths:
        before = {ref.ident for ref in sources.code_refs(path, base_tree.read(path))}
        for ref in sources.code_refs(path, cur_tree.read(path)):
            if ref.ident not in before:
                out.append(ref)
                before.add(ref.ident)
    return out


def all_code_refs(tree) -> list:
    paths = [p for p in tree.paths() if sources.is_code(p)]
    tree.prefetch(paths)
    out = []
    for path in paths:
        out.extend(sources.code_refs(path, tree.read(path)))
    return out


def manual_translation_rows(base, cur) -> list:
    """翻译包里新增、而术语表生成不出来的 msgid（手工行）。"""
    out = []
    for path, msgids in sorted(cur.translations.items()):
        app = sources.TRANSLATION.match(path).group(1)
        own = cur.glossary_msgids.get(app, set())
        fresh = msgids - (base.translations.get(path, set()) if base else set())
        for msgid in sorted(fresh - own):
            out.append(sources.CodeRef("csv-manual", msgid, path, 0))
    return out
