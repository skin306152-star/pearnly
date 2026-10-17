# -*- coding: utf-8 -*-
"""角色推断与各条判据。词表在 scripts/i18n/copy-words.tsv，这里只放"怎么用词表"和按角色的上限。

用户口径：界面文案一律官方、简短、干净、陈述事实；不解释原因、不用口语、不加欢迎语 / 说明段 / 提示语；
Windows 端在用户可见处只叫「CC 桌面版」。判据分两级：

  block —— 强特征，直接拦（口语、因果解释、第一人称、提示语头、开发痕迹、寒暄与表情、超出拦截线、句数超标、写死）
  warn  —— 弱特征，只在清单里标黄（交代系统行为、超出提醒线、长括号、中泰文里夹的生僻英文……）

各判据的阈值按 499d4417（1048 条正式化改写）改前 → 改后的语料校准：改前命中、改后不命中的才进 block。
"""

from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass

from . import sources

LANGS = sources.LANGS

# ── 角色 ─────────────────────────────────────────────────────────────────────

ROLE_NAMES = {
    "button": "按钮", "menu": "菜单", "label": "标签", "status": "状态", "title": "标题",
    "placeholder": "占位", "empty": "空态", "hint": "提示", "error": "出错", "confirm": "确认",
    "line": "LINE 卡片", "field_desc": "字段说明", "sr": "读屏", "email": "邮件", "unknown": "未知",
}

# 中文：提醒线 / 拦截线（字）
ZH_LIMITS = {
    "button": (8, 12), "menu": (8, 12), "label": (10, 16), "status": (8, 12), "title": (12, 16),
    "placeholder": (12, 16), "empty": (10, 16), "hint": (20, 40), "error": (30, 45), "confirm": (40, 50),
    "line": (30, 45), "field_desc": (30, 40), "sr": (40, 60), "email": (80, 150), "unknown": (30, 50),
}
# 英文：提醒线 / 拦截线（词）；除邮件与读屏外都不超过全局 30
EN_LIMITS = {
    "button": (3, 4), "menu": (3, 4), "label": (4, 6), "status": (4, 6), "title": (6, 10),
    "placeholder": (8, 12), "empty": (8, 12), "hint": (15, 20), "error": (20, 25), "confirm": (20, 30),
    "line": (20, 30), "field_desc": (20, 30), "sr": (30, 45), "email": (150, 250), "unknown": (20, 30),
}
TH_FACTOR = 2.7
TH_GLOBAL = 120
CC_WEB_HARD = 10  # CC 网页的按钮、菜单、状态

# 句数上限：1 句的角色；其余 2 句（事实 + 一个动作，或问句 + 后果）；邮件不限。
# 提示与字段说明按 499d4417 定稿语料放到 2 句（「按物料编码匹配，未匹配时按条码匹配；重复导入的行将被覆盖。」）
ONE_SENTENCE = {"button", "menu", "label", "status", "title", "placeholder", "empty"}
NO_SENTENCE_LIMIT = {"email"}

# R-EG：举例只允许在这些角色里
EG_ALLOWED = {"placeholder", "error", "field_desc"}
EG_BLOCKED = {"button", "menu", "label", "status", "title", "empty", "hint", "confirm"}
EG_KEYS = re.compile(r"^dms\.card\.qa\.TXT_ASK_|\.query\.ASK_|^dms\.card\.query\.ASK_")

_SPLIT_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def key_tokens(key: str) -> list:
    words = []
    for part in re.split(r"[.\-_/#\s]+", key):
        for piece in _SPLIT_CAMEL.split(part):
            if piece:
                words.append(piece.lower())
    return words


def role_of(entry, roles: dict) -> str:
    """按键名推断角色；术语表无键行看 DocType JSON（label / description），再看是不是问句。"""
    if entry.hint_role:
        return entry.hint_role
    key = entry.key
    if entry.source.startswith("pearnly_") and not entry.extra.get("keyed", True):
        en = entry.texts.get("en", "")
        role = roles.get(en)
        if role:
            return role
        return "confirm" if en.rstrip().endswith("?") else "unknown"
    if key.startswith("dms.card."):
        return "line"
    words = set(key_tokens(key))
    last = key_tokens(key)[-1] if key_tokens(key) else ""
    if words & {"instructions", "aria", "sr"}:
        return "sr"
    if "confirm" in words and not words & {"btn", "button"}:
        return "confirm"
    if words & {"err", "error", "errors", "failed", "invalid", "fail"} or key.startswith("ERR_"):
        return "error"
    if last == "empty" or {"empty", "state"} <= words:
        return "empty"  # emptyGroups（「空分组」开关）、emptyFile（出错）不是空态
    if words & {"placeholder", "ph"}:
        return "placeholder"
    if words & {"hint", "help", "subtitle", "note", "tip", "tips", "desc", "intro"}:
        return "hint"
    if words & {"title", "heading", "section", "sec", "header"}:
        return "title"
    if words & {"col", "column", "label", "lbl"}:
        return "label"
    if words & {"status", "state", "code"}:
        return "status"
    if last in {"btn", "button", "action", "save", "delete", "confirm"} or "btn" in words:
        return "button"
    if words & {"nav", "menu"}:
        return "menu"
    return "unknown"


def surface_of(entry, role: str) -> str:
    """文案在哪类界面：决定开发术语拦还是标黄。

    end      纯终端用户界面：CC 网页、CC 桌面版、登录页、DMS 的 LINE 卡片、导出给客户的四语表 —— 强弱术语都拦
    product  产品 Desk（租户站点的语义键与 msgid 文案）—— 强术语拦，弱术语标黄
    admin    控制面，以及 Desk 的字段标签与字段说明 —— 术语只标黄
    """
    if not entry.source.startswith("pearnly_"):
        return "end"
    if entry.key.startswith("dms.card."):
        return "end"
    if entry.source == "pearnly_control":
        return "admin"
    if entry.extra.get("keyed"):
        return "product"
    return "admin" if role in ("label", "field_desc") else "product"


def rules_role_name(role: str) -> str:
    return ROLE_NAMES.get(role, role)


# ── 词表 ─────────────────────────────────────────────────────────────────────


@dataclass
class Pattern:
    rule: str
    lang: str
    level: str
    regex: object
    note: str
    source: str


def load_words(path: str) -> dict:
    """{(规则, 语言): [Pattern]}。"""
    table: dict = {}
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                raise ValueError("%s 第 %d 行：至少要有 规则 / 语言 / 级别 / 正则 四列" % (path, number))
            rule, lang, level, pattern = parts[:4]
            note = parts[4] if len(parts) > 4 else ""
            table.setdefault((rule, lang), []).append(
                Pattern(rule, lang, level, re.compile(pattern), note, line)
            )
    return table


# ── 文字预处理与计数 ─────────────────────────────────────────────────────────

PLACEHOLDER = re.compile(r"\$\{[^}]*\}|\{[^{}\s]*\}|%\([A-Za-z_]+\)[sdf]|%[sdf]|\bv◇")
TAGS = re.compile(r"<[^<>]+>")
MASK = "◇"


def masked(text: str) -> str:
    return TAGS.sub(" ", PLACEHOLDER.sub(MASK, text))


def zh_len(text: str) -> int:
    t = masked(text)
    count = 2 * t.count(MASK)
    t = t.replace(MASK, "")
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.'’\-]*", t)
    count += 2 * len(words)
    t = re.sub(r"[A-Za-z0-9][A-Za-z0-9.'’\-]*", "", t)
    return count + len(re.sub(r"\s", "", t))


def en_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:['’.\-][A-Za-z0-9]+)*|◇", masked(text)))


def th_len(text: str) -> int:
    t = masked(text)
    return 2 * t.count(MASK) + len(re.sub(r"\s", "", t.replace(MASK, "")))


def measure(lang: str, text: str) -> int:
    if lang == "en":
        return en_words(text)
    if lang == "th":
        return th_len(text)
    return zh_len(text)


_EN_ABBR = re.compile(r"\b(e\.g\.|i\.e\.|etc\.|vs\.|No\.|Mr\.|Ms\.|Dr\.)|\d\.\d|…|\.\.\.")


def sentences(lang: str, text: str, role: str) -> int:
    t = masked(text)
    if lang == "en":
        t = _EN_ABBR.sub(" ", t)
        parts = re.split(r"[.!?](?=\s|$)" + ("" if role == "line" else r"|\n"), t)
    elif lang == "th":
        return 0  # 泰文无句号，粗分不可靠，只作参考，不判
    else:
        parts = re.split(r"[。！？；!?;]" + ("" if role == "line" else r"|\n"), t)
    return sum(1 for part in parts if sources.LETTERS.search(part) or re.search(r"[぀-ヿ]", part))


# ── 旧称：复用 tests/test_cc_desktop_name.py 的正则 ─────────────────────────


def _load_name_guard(root: str):
    path = os.path.join(root, "tests", "test_cc_desktop_name.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("_copy_gate_name_guard", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - 守卫载不进来就不判旧称，不让闸门整体崩
        return None
    return module


# ── 判定 ─────────────────────────────────────────────────────────────────────


@dataclass
class Finding:
    rule: str
    level: str  # block / warn
    lang: str
    snippet: str
    message: str
    entry_id: str = ""
    loc: str = ""
    role: str = ""


class Checker:
    def __init__(self, words: dict, root: str, terms: list = ()):
        self.words = words
        self.terms = list(terms)  # [(词, 适用来源或 "")]
        self.name_guard = _load_name_guard(root)

    def _patterns(self, rule: str, lang: str) -> list:
        return self.words.get((rule, lang), []) + self.words.get((rule, "any"), [])

    def _allow(self, rule: str, lang: str, text: str) -> bool:
        return any(p.regex.search(text) for p in self._patterns(rule, lang) if p.level == "allow")

    def line20_key(self, key: str) -> bool:
        return any(p.regex.search(key) for p in self.words.get(("R-LINE20-KEY", "key"), []))

    def _strip_terms(self, entry, text: str) -> str:
        for word, scope in self.terms:
            if scope and scope != entry.source:
                continue
            text = text.replace(word, " ")
        return text

    def check_entry(self, entry, langs, exempt: set = frozenset(), changed_from=None) -> list:
        """对一条词条的指定语言跑全部判据。exempt：这条词条已登记豁免的规则 ID。"""
        out = []
        role = entry.role or "unknown"
        for lang in langs:
            text = entry.texts.get(lang)
            if not text:
                continue
            for finding in self._check_text(entry, lang, text, role):
                finding.entry_id = entry.id
                finding.loc = entry.loc
                out.append(finding)
        out.extend(self._check_cross(entry, role, langs, changed_from))
        for finding in out:
            finding.entry_id = entry.id
            finding.loc = finding.loc or entry.loc
            finding.role = rules_role_name(role)
        return [f for f in out if f.rule not in exempt]

    def _lex_all(self, rule, lang, text):
        hits = []
        for pattern in self._patterns(rule, lang):
            if pattern.level in ("block", "warn", "soft"):
                match = pattern.regex.search(text)
                if match:
                    hits.append((pattern.level, match.group(0), pattern.note))
        return hits

    def _lex(self, rule, lang, text, level_override=None):
        hits = []
        for pattern in self._patterns(rule, lang):
            if pattern.level not in ("block", "warn"):
                continue
            match = pattern.regex.search(text)
            if match:
                hits.append((level_override or pattern.level, match.group(0), pattern.note))
        return hits

    def _check_text(self, entry, lang, raw, role) -> list:
        out = []
        text = masked(raw)
        add = lambda rule, level, snip, msg: out.append(Finding(rule, level, lang, snip, msg))  # noqa: E731
        lexical = lang in ("zh", "en", "th")

        # R-LEN
        if lang in ("zh", "en", "th"):
            size = measure(lang, raw)
            if lang == "en":
                warn_at, block_at = EN_LIMITS.get(role, EN_LIMITS["unknown"])
                unit = "词"
            else:
                warn_at, block_at = ZH_LIMITS.get(role, ZH_LIMITS["unknown"])
                if entry.source == "assistant" and role in ("button", "menu", "status"):
                    block_at = min(block_at, CC_WEB_HARD)
                    warn_at = min(warn_at, block_at)
                unit = "字"
                if lang == "th":
                    warn_at, block_at = round(warn_at * TH_FACTOR), round(block_at * TH_FACTOR)
                    if role != "email":
                        block_at = min(block_at, TH_GLOBAL)
                        warn_at = min(warn_at, block_at)
            if size > block_at:
                add("R-LEN", "block", "%d%s" % (size, unit), "%s超过拦截线 %d%s" % (ROLE_NAMES[role], block_at, unit))
            elif size > warn_at:
                add("R-LEN", "warn", "%d%s" % (size, unit), "%s超过提醒线 %d%s" % (ROLE_NAMES[role], warn_at, unit))

        # R-SENT
        if lang in ("zh", "en") and role not in NO_SENTENCE_LIMIT:
            count = sentences(lang, raw, role)
            limit = 1 if role in ONE_SENTENCE else 2
            if count >= 3 or count > limit:
                add("R-SENT", "block", "%d 句" % count, "%s最多 %d 句" % (ROLE_NAMES[role], limit))
            if role == "button" and re.search(r"[。.]$", raw.strip()):
                add("R-SENT", "warn", raw.strip()[-1], "按钮不带句号")

        if lexical:
            if role != "email":
                for level, snip, note in self._lex("R-WHY", lang, text):
                    add("R-WHY", level, snip, note)
                for level, snip, note in self._lex("R-WE", lang, text):
                    add("R-WE", level, snip, note)
                for level, snip, note in self._lex("R-HELLO", lang, text):
                    add("R-HELLO", level, snip, note)
            for level, snip, note in self._lex("R-TALK", lang, text):
                add("R-TALK", level, snip, note)
            for level, snip, note in self._lex("R-HINT", lang, text):
                add("R-HINT", level, snip, note)
            many = (sentences(lang, raw, role) if lang != "th" else 1) >= 2
            for level, snip, note in self._lex("R-NARR", lang, text):
                add("R-NARR", "block" if many else "warn", snip, note + ("（多句解释机制）" if many else ""))
        elif role != "email":
            for level, snip, note in self._lex("R-HELLO", lang, text):
                add("R-HELLO", level, snip, note)

        # R-DEV
        for level, snip, note in self._lex("R-DEV-HARD", lang, text):
            add("R-DEV", "block", snip, note)
        if "\n\n" in raw and role not in ("line", "email"):
            add("R-DEV", "block", "⏎⏎", "空行分段（Markdown / 开发说明）")
        stripped = self._strip_terms(entry, text)
        for level, snip, note in self._lex_all("R-DEV-TERM", lang, stripped):
            if level == "soft":
                level = "block" if entry.surface == "end" else "warn"
            elif level == "block" and entry.surface == "admin":
                level = "warn"
            add("R-DEV", level, snip, note)

        # R-MIX：中 / 泰文里夹的生僻英文（R-DEV 术语已在上面判过）
        if lang in ("zh", "th"):
            for word in re.findall(r"[A-Za-z][A-Za-z]{2,}", stripped):
                if self._allow("R-MIX-OK", lang, word):
                    continue
                if self._lex_all("R-DEV-TERM", "en", word):
                    continue
                add("R-MIX", "warn", word, "中 / 泰文里夹英文词（不在品牌与业务词白名单）")
                break

        # R-PAREN
        for match in re.finditer(r"[（(]([^）)]{6,})[）)]", text):
            inner = match.group(1).strip()
            if self._allow("R-PAREN-OK", lang, inner):
                continue
            why = self._lex("R-PAREN-WHY", lang if lang in ("zh", "en", "th") else "zh", inner)
            if why:
                add("R-PAREN", "block", match.group(0), "括号里补充解释（%s）" % why[0][1])
            else:
                add("R-PAREN", "warn", match.group(0), "长括号补充")

        # R-EG
        eg = self._lex("R-EG", lang, text)
        if eg and role not in EG_ALLOWED and not EG_KEYS.search(entry.key):
            level = "block" if role in EG_BLOCKED else "warn"
            add("R-EG", level, eg[0][1], "%s里不举例" % ROLE_NAMES[role])

        # R-EMPTY
        if role == "empty" and lang in ("zh", "en", "th"):
            if not self._allow("R-EMPTY", lang, text.strip()):
                add("R-EMPTY", "block", text.strip()[:12], "空态只写事实（暂无… / No … / ไม่มี…）")
            guide = self._lex("R-EMPTY-GUIDE", lang, text)
            if guide:
                add("R-EMPTY", "block", guide[0][1], "空态后不接操作引导")

        # R-NAME
        if self.name_guard is not None:
            bad = self.name_guard.offending(raw, self.name_guard.SHOWN)
            if bad:
                add("R-NAME", "block", bad, "Windows 端只叫「CC 桌面版」，CC 不叫助手")

        # R-LINE20
        if self.line20_key(entry.key) and len(raw) > 20:
            add("R-LINE20", "block", "%d 字符" % len(raw), "LINE 快捷按钮标签不能超过 20 字符")
        return out

    def _check_cross(self, entry, role, langs, changed_from) -> list:
        out = []
        zh, en, ja = entry.texts.get("zh", ""), entry.texts.get("en", ""), entry.texts.get("ja", "")
        if zh and en and ("zh" in langs or "en" in langs):
            zl, ew = zh_len(zh), en_words(en)
            if zl >= 4 and ew >= 12 and ew / zl > 1.0:
                out.append(Finding("R-ALIGN", "block", "en", "%d 词 / %d 字" % (ew, zl),
                                   "英文比中文长得多：英文 msgid 里藏着开发说明"))
            zs, es = sentences("zh", zh, role), sentences("en", en, role)
            if zs and es and zs != es and role not in NO_SENTENCE_LIMIT:
                out.append(Finding("R-SENT", "warn", "en", "%d / %d 句" % (zs, es), "中英文句数不一致"))
        if zh and ja and "ja" in langs and zh_len(ja) > 3 * max(zh_len(zh), 1):
            out.append(Finding("R-ALIGN", "warn", "ja", "", "日文长度超过中文 3 倍"))
        if changed_from is not None and "zh" in langs:
            stale = [l for l in ("en", "th", "ja") if entry.texts.get(l) and l not in langs]
            if stale:
                out.append(Finding("R-ALIGN", "warn", ",".join(stale), "", "中文改了，%s 译文未跟进" % "/".join(stale)))
        return out


# ── 代码引用（R-HARD）────────────────────────────────────────────────────────

BRANDS = re.compile(r"^(CC|Pearnly|Express|MR\.ERP|DMS|LINE|Cowork|ERP|PDF|CSV|Excel|POS|VAT|OCR|×|·|—|…|/)$")
REF_NAMES = {
    "py-msgid": "_() 的 msgid 不在术语表 / 翻译包里",
    "desk-msgid": "__() 的 msgid 不在术语表 / 翻译包里",
    "html-msgid": "页面 _() 的 msgid 不在术语表 / 翻译包里",
    "tool-label": "工具声明的 label / ask / 进行中说法不在术语表里",
    "py-bare": "frappe.throw / msgprint 写死文案（要包 _()）",
    "desk-key": "t(\"键\") 的键不在术语表里",
    "tsx-tr": "tr(\"…\") 的词不在术语表里",
    "tsx-t": "t(\"键\") 的键不在 i18n.ts 的 MESSAGES 里",
    "tsx-attr": "JSX 属性写死文案（走 i18n.ts / tr()）",
    "tsx-text": "JSX 文本写死文案（走 i18n.ts / tr()）",
    "html-cjk": "页面 / 邮件模板写死中文或泰文（走 _()）",
    "csv-manual": "翻译包新增了术语表里没有的行（手工行或改写后的残留；术语表才是唯一来源）",
}


def check_ref(ref, snap, known: set = None) -> list:
    """代码引用：在不在字典里、有没有写死。

    known：认作"在字典里"的 msgid。差异模式传「当前术语表 + 基线翻译包」：本次新加的翻译包手工行
    不能拿来给新代码兜底（新代码用的词必须进术语表）。默认用当前快照的全部（术语表 + 翻译包）。
    """
    kind, text = ref.kind, ref.text
    known = snap.msgids if known is None else known
    level = None
    if kind in ("py-msgid", "desk-msgid", "html-msgid"):
        if text not in known:
            # Frappe 自带词（Save、Not permitted…）不在本仓术语表里：短词只标黄，句子拦
            level = "block" if en_words(text) >= 3 else "warn"
    elif kind in ("tool-label", "desk-key", "tsx-tr"):
        if text not in known:
            level = "block"
    elif kind == "tsx-t":
        if text not in snap.assistant_keys:
            level = "block"
    elif kind in ("tsx-attr", "tsx-text"):
        value = text.split("=", 1)[1] if kind == "tsx-attr" else text
        if not BRANDS.match(value.strip()):
            level = "block"
    elif kind in ("py-bare", "html-cjk"):
        level = "block"
    elif kind == "csv-manual":
        # 生成器只合并不删：术语表里改写过的 msgid 会在翻译包留下旧行，分不清是手工加的还是残留，所以只标黄；
        # 新代码引用它时由上面的 msgid 判据拦（差异模式的 known 不含本次新加的翻译包行）
        level = "warn"
    if not level:
        return []
    return [Finding("R-HARD", level, "", text[:80], REF_NAMES[kind], "代码:%s" % ref.path, "%s:%d" % (ref.path, ref.line))]


def read_tsv_rows(path: str) -> list:
    """登记表（# 注释 + TSV 正文）的行：[(行号, [列…])]。"""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            rows.append((number, [c.strip() for c in line.split("\t")]))
    return rows


def parse_rows_text(text: str) -> list:
    rows = []
    for number, line in enumerate((text or "").split("\n"), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        rows.append((number, [c.strip() for c in line.split("\t")]))
    return rows
