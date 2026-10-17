# -*- coding: utf-8 -*-
"""角色推断与各条判据。词表在 scripts/i18n/copy-words.tsv，这里只放"怎么用词表"和按角色的上限。

用户口径：界面文案一律官方、简短、干净、陈述事实；不解释原因、不用口语、不加欢迎语 / 说明段 / 提示语；
Windows 端在用户可见处只叫「CC 桌面版」。判据分两级：

  block —— 强特征，直接拦（口语、因果解释、推测、第一人称、提示语头、开发痕迹、寒暄与表情、超出拦截线、句数超标、写死）
  warn  —— 弱特征，只在清单里标黄（交代系统行为、超出提醒线、长括号、中泰文里夹的生僻英文……）

各判据的阈值按 499d4417（1048 条正式化改写）改前 → 改后的语料校准：改前命中、改后不命中的才进 block；
另用锁定镜像里 Frappe / ERPNext 官方中文、泰文译文（zh.po / th.po）核对误拦：官方写法过不了闸的规则要收窄。

中文是权威语言：「解释原因 / 推测 / 交代系统行为」三类（R-WHY、R-HEDGE、R-NARR）英文或泰文命中、而同一词条的中文
不拦时降为标黄 —— 中文「资料未确认，无法推送」对应英文 ", so it cannot be pushed" 是同一个事实加后果，不是碎碎念。
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
    "line": "LINE 卡片", "field_desc": "字段说明", "sr": "读屏", "email": "邮件", "option": "选项",
    "prompt": "替用户说的话", "guide": "权限引导", "unknown": "未知",
}

# 中文：提醒线 / 拦截线（字）。未知角色按提示收紧（键名不带角色词不能换来更宽的上限）
ZH_LIMITS = {
    "button": (8, 12), "menu": (8, 12), "label": (10, 16), "status": (8, 12), "title": (12, 16),
    "placeholder": (12, 16), "empty": (10, 16), "hint": (20, 40), "error": (30, 45), "confirm": (40, 50),
    "line": (30, 45), "field_desc": (30, 40), "sr": (40, 60), "email": (80, 150), "option": (10, 16),
    "prompt": (40, 60), "guide": (40, 80), "unknown": (20, 40),
}
# 英文：提醒线 / 拦截线（词）
EN_LIMITS = {
    "button": (3, 4), "menu": (3, 4), "label": (4, 6), "status": (4, 6), "title": (6, 10),
    "placeholder": (8, 12), "empty": (8, 12), "hint": (15, 20), "error": (20, 25), "confirm": (20, 30),
    "line": (20, 30), "field_desc": (20, 30), "sr": (30, 45), "email": (150, 250), "option": (4, 6),
    "prompt": (25, 40), "guide": (30, 50), "unknown": (15, 25),
}
TH_FACTOR = 2.7
TH_GLOBAL = 120
CC_WEB_HARD = 10  # CC 网页的按钮、菜单、状态

# 句数上限：1 句的角色；权限引导 3 句；其余 2 句（事实 + 一个动作，或问句 + 后果）；邮件与替用户说的话不限。
# 提示与字段说明按 499d4417 定稿语料放到 2 句（「按物料编码匹配，未匹配时按条码匹配；重复导入的行将被覆盖。」）
ONE_SENTENCE = {"button", "menu", "label", "status", "title", "placeholder", "empty", "option"}
THREE_SENTENCES = {"guide"}
NO_SENTENCE_LIMIT = {"email", "prompt"}

# 替用户发给 CC 的话（预填进输入框、由用户自己发出）：语气是用户的，只判长度、开发痕迹、旧称
PROMPT_SKIP = {"R-SENT", "R-WHY", "R-HEDGE", "R-TALK", "R-WE", "R-HINT", "R-HELLO", "R-EMOJI", "R-NARR", "R-EG",
               "R-PAREN", "R-EMPTY"}
# 以中文为准降级的判据（英文 / 泰文命中、中文不拦 → 标黄）
AUTHORITY_RULES = ("R-WHY", "R-HEDGE", "R-NARR")

# R-EG：举例只允许在这些角色里
EG_ALLOWED = {"placeholder", "error", "field_desc", "guide", "prompt"}
EG_BLOCKED = {"button", "menu", "label", "status", "title", "empty", "hint", "confirm", "option"}
EG_KEYS = re.compile(r"^dms\.card\.qa\.TXT_ASK_|\.query\.ASK_|^dms\.card\.query\.ASK_")

_SPLIT_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])")

# 键名任意一段带这些词：出错（优先于 code / action / save / header / column / status 这些"像别的角色"的词）
ERROR_WORDS = {"err", "error", "errors", "failed", "fail", "invalid", "missing", "unknown", "duplicate", "ambiguous",
               "mismatch", "denied", "forbidden", "conflict", "notfound"}
BUTTON_LAST = {"btn", "button", "action", "save", "delete", "confirm", "submit", "cancel"}


def key_tokens(key: str) -> list:
    words = []
    for part in re.split(r"[.\-_/#\s]+", key):
        for piece in _SPLIT_CAMEL.split(part):
            if piece:
                words.append(piece.lower())
    return words


def _last_segment(key: str) -> str:
    return re.split(r"[.#/]", key)[-1]


def _keypat(words: dict, rule: str, key: str) -> bool:
    return any(p.regex.search(key) for p in words.get((rule, "key"), []))


SHORT_ROLES = {"status", "label", "title", "menu"}
_SENTENCE_END = re.compile(r"[.。!！?？]\s*$")


def role_of(entry, roles: dict, words: dict = None, callsites: dict = None) -> str:
    """角色；按键名猜出的短角色（状态 / 标签 / 标题 / 按钮 / 菜单），文案却是带句号的整句时按未知判
    （stocktake.code_as_text「商品编码与条码须以文本保存。」是出错句，不是状态）。"""
    role, guessed = _role_of(entry, roles, words, callsites)
    if guessed and role in SHORT_ROLES:
        text = entry.texts.get("en") or entry.texts.get("zh") or ""
        if _SENTENCE_END.search(text) and not text.rstrip().endswith("…"):
            return "unknown"
    return role


def _role_of(entry, roles: dict, words: dict = None, callsites: dict = None) -> str:
    """角色。依次看：邮件（只被邮件模板引用）、键名模式（替用户说的话、权限引导、LINE 卡片）、调用点（frappe.throw、
    placeholder=、subject=、aria-*…）、DocType JSON（label / description / Select 选项）、键名、问号。"""
    words = words or {}
    callsites = callsites or {}
    key = entry.key
    en = entry.texts.get("en", "")
    if entry.hint_role:
        return entry.hint_role, False
    if entry.source == "assistant" and _keypat(words, "R-PROMPT-KEY", key):
        return "prompt", False
    if _keypat(words, "R-GUIDE-KEY", key):
        return "guide", False
    if key.startswith("dms.card."):
        return "line", False
    msgid_row = entry.source.startswith("pearnly_") and (not entry.extra.get("keyed", True) or key == en)
    site = callsites.get(key) or (callsites.get(en) if en else None)
    if msgid_row:
        role = roles.get(en)
        if role:
            return role, False
        if site:
            return site, False
        return ("confirm" if en.rstrip().endswith("?") else "unknown"), False
    if site:
        return site, False
    tokens = key_tokens(key)
    words_set = set(tokens)
    last_seg = _last_segment(key)
    last_tokens = key_tokens(last_seg)
    # 读屏：只认调用点实际交给 aria-* / screenReaderInstructions 的键（键名带 instructions 换不来更宽的上限）
    if words_set & {"instructions", "aria", "sr"} and last_seg in callsites.get("prop:sr", ()):
        return "sr", True
    if "confirm" in words_set and not words_set & {"btn", "button"}:
        return "confirm", True
    if (words_set & ERROR_WORDS or key.startswith("ERR_") or "not_found" in key
            or re.match(r"^no_", last_seg)):
        return "error", True
    if last_seg == "empty" or last_seg.endswith("Empty") or re.search(r"(^|[._])empty_?state$|emptyState$", key):
        return "empty", True  # emptyGroups（「空分组」开关）、w_invoice_no_empty（差异码）不是空态
    if words_set & {"placeholder", "ph"}:
        return "placeholder", True
    if words_set & {"hint", "help", "subtitle", "note", "tip", "tips", "desc", "intro", "step", "instruction"}:
        return "hint", True
    if words_set & {"title", "heading", "section", "sec", "header"}:
        return "title", True
    if words_set & {"col", "column", "label", "lbl"}:
        return "label", True
    if words_set & {"status", "state", "code"} or last_seg.endswith("_empty"):
        return "status", True
    if last_seg.lower() in BUTTON_LAST or (last_tokens and last_tokens[-1] in {"btn", "button"}) or last_seg.startswith("btn"):
        return "button", True
    if last_tokens and last_tokens[0] in {"nav", "menu"}:
        return "menu", True
    return "unknown", True


def surface_of(entry, role: str) -> str:
    """文案在哪类界面：决定开发术语拦还是标黄。

    end      纯终端用户界面：CC 网页、CC 桌面版、登录页、DMS 的 LINE 卡片、导出给客户的四语表 —— 强弱术语都拦
    product  产品 Desk（租户站点的语义键、msgid 文案、租户看得到的 DocType 标签与说明）—— 强术语拦，弱术语标黄
    admin    控制面，以及只有 System Manager 能读的 DocType 的标签 / 说明 / 选项 —— 术语只标黄
    """
    if not entry.source.startswith("pearnly_"):
        return "end"
    if entry.key.startswith("dms.card."):
        return "end"
    if entry.source == "pearnly_control":
        return "admin"
    if role in ("label", "field_desc", "option") and entry.extra.get("doctype_admin"):
        return "admin"
    return "product"


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


def load_upstream(path: str) -> set:
    """锁定镜像里 Frappe / ERPNext 自带的 msgid（scripts/i18n/upstream-msgids.txt；#! 开头是文件头）。"""
    found = set()
    if not os.path.exists(path):
        return found
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line or line.startswith("#!"):
                continue
            found.add(_UNESCAPE.sub(lambda m: _ESCAPES.get(m.group(1), m.group(1)), line))
    return found


_UNESCAPE = re.compile(r"\\(.)")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "s": " "}


# ── 文字预处理与计数 ─────────────────────────────────────────────────────────

PLACEHOLDER = re.compile(r"\$\{[^}]*\}|\{[^{}\s]*\}|%\([A-Za-z_]+\)[sdf]|%[sdf]|\bv◇")
TAGS = re.compile(r"<[^<>]+>")
MASK = "◇"
# 泰文上下标元音与声调符号（ั ิ ี ึ ื ุ ู ฺ ็ ่ ้ ๊ ๋ ์ ํ ๎）：不占字宽，计数前去掉
TH_MARKS = re.compile(r"[ัิ-ฺ็-๎]")


def masked(text: str) -> str:
    return TAGS.sub(" ", PLACEHOLDER.sub(MASK, text))


def zh_len(text: str) -> int:
    t = masked(text)
    count = 2 * t.count(MASK)
    t = t.replace(MASK, "")
    # 英文词、数字、中文里夹的泰文表单名（ภ.ง.ด.53）各记 2 字
    words = re.findall(r"[A-Za-z0-9ก-๛][A-Za-z0-9ก-๛.'’\-]*", t)
    count += 2 * len(words)
    t = re.sub(r"[A-Za-z0-9ก-๛][A-Za-z0-9ก-๛.'’\-]*", "", t)
    return count + len(re.sub(r"\s", "", t))


def en_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:['’.\-][A-Za-z0-9]+)*|◇", masked(text)))


def th_len(text: str) -> int:
    """泰文按字形数：上下标元音与声调符号不算。"""
    t = TH_MARKS.sub("", masked(text))
    return 2 * t.count(MASK) + len(re.sub(r"\s", "", t.replace(MASK, "")))


def measure(lang: str, text: str) -> int:
    if lang == "en":
        return en_words(text)
    if lang == "th":
        return th_len(text)
    return zh_len(text)


_EN_ABBR = re.compile(
    r"\b(e\.g\.|i\.e\.|etc\.|vs\.|No\.|Mr\.|MR\.|Ms\.|Dr\.|incl\.|excl\.|approx\.|Co\.|Ltd\.|Inc\.|Corp\.|Dept\.|Ref\.)"
    r"|\b[Nn]o\.(?=\s*[A-Za-z0-9|◇])|\d\.\d|…|\.\.\.")


def sentences(lang: str, text: str, role: str) -> int:
    t = masked(text)
    if lang == "en":
        t = _EN_ABBR.sub(" ", t)
        parts = re.split(r"[.!?](?=\s|$)" + ("" if role == "line" else r"|\n"), t)
    elif lang == "th":
        return 0  # 泰文无句号，粗分不可靠：句数不判（R-NARR 另按空格分段）
    else:
        parts = re.split(r"[。！？；!?;]" + ("" if role == "line" else r"|\n"), t)
    return sum(1 for part in parts if sources.LETTERS.search(part) or re.search(r"[぀-ヿ]", part))


def clauses(lang: str, text: str) -> int:
    """分句数：中文按逗号、分号、句号，英文按逗号、分号、句号，泰文按空格段。"""
    t = masked(text)
    if lang == "th":
        parts = re.split(r"\s+", t.strip())
    elif lang == "en":
        parts = re.split(r"[,;.!?—](?=\s|$)", _EN_ABBR.sub(" ", t))
    else:
        parts = re.split(r"[，,；;。！？!?、]", t)
    return sum(1 for part in parts if sources.LETTERS.search(part))


# ── 旧称：复用 tests/test_cc_desktop_name.py 的正则与 ALLOWED ──────────────


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


_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")
_NARR_ERROR_OK = re.compile(r"系统.{0,8}(会|将).{0,8}(读取|重试|继续)")


class Checker:
    def __init__(self, words: dict, root: str, terms: list = ()):
        self.words = words
        self.terms = list(terms)  # [(词, 适用来源或 "")]
        self.name_guard = _load_name_guard(root)
        allowed = getattr(self.name_guard, "ALLOWED", {}) if self.name_guard else {}
        self.name_allowed = set(allowed)

    def _patterns(self, rule: str, lang: str) -> list:
        return self.words.get((rule, lang), []) + self.words.get((rule, "any"), [])

    def _allow(self, rule: str, lang: str, text: str) -> bool:
        return any(p.regex.search(text) for p in self._patterns(rule, lang) if p.level == "allow")

    def _strip_allowed(self, rule: str, lang: str, text: str) -> str:
        for p in self._patterns(rule, lang):
            if p.level == "allow":
                text = p.regex.sub(" ", text)
        return text

    def line20_key(self, key: str) -> bool:
        return _keypat(self.words, "R-LINE20-KEY", key)

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
        authority = self._authority_levels(entry, role)
        for lang in langs:
            text = entry.texts.get(lang)
            if not text:
                continue
            for finding in self._check_text(entry, lang, text, role, authority):
                out.append(finding)
        out.extend(self._check_cross(entry, role, langs, changed_from))
        for finding in out:
            finding.entry_id = entry.id
            finding.loc = finding.loc or entry.loc
            finding.role = rules_role_name(role)
        if role == "prompt":
            out = [f for f in out if f.rule not in PROMPT_SKIP]
        return [f for f in out if f.rule not in exempt]

    def _authority_levels(self, entry, role) -> dict:
        """中文（权威语言）在 R-WHY / R-HEDGE / R-NARR 上的结论：{规则: 'block' / 'warn'}；没有中文返回 None。"""
        zh = entry.texts.get("zh")
        if not zh:
            return None
        levels = {}
        for finding in self._check_text(entry, "zh", zh, role, None, only=AUTHORITY_RULES):
            if levels.get(finding.rule) != "block":
                levels[finding.rule] = finding.level
        return levels

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

    def _escalates(self, lang, text) -> bool:
        return any(p.regex.search(text) for p in self._patterns("R-NARR", lang) if p.level == "escalate")

    def _dev_terms(self, entry, lang, text):
        """开发术语：本语言词表 + 中 / 泰文里夹的英文词再按英文词表判一遍（「无法连接 Redis」「token 已过期」）。"""
        hits = list(self._lex_all("R-DEV-TERM", lang, text))
        if lang in ("zh", "th", "ja"):
            latin = " ".join(_LATIN_WORD.findall(text))
            if latin:
                hits += self._lex_all("R-DEV-TERM", "en", latin)
        out = []
        for level, snip, note in hits:
            if level == "soft":
                level = "block" if entry.surface == "end" else "warn"
            elif level == "block" and entry.surface == "admin":
                level = "warn"
            out.append((level, snip, note))
        return out

    def _check_text(self, entry, lang, raw, role, authority, only=None) -> list:
        out = []
        text = masked(raw)

        def add(rule, level, snip, msg):
            if only is None or rule in only:
                out.append(Finding(rule, level, lang, snip, msg))

        lexical = lang in ("zh", "en", "th")

        # R-LEN
        if lang in ("zh", "en", "th") and only is None:
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
                    zh_warn = warn_at
                    warn_at, block_at = round(warn_at * TH_FACTOR), round(block_at * TH_FACTOR)
                    if role != "email":
                        block_at = min(block_at, TH_GLOBAL)
                        warn_at = min(warn_at, block_at)
            level = "block" if size > block_at else "warn" if size > warn_at else None
            # 泰文以中文为准：中文没超提醒线，泰文超了只标黄（法定名称与正常泰文本来就比中文长）
            if level == "block" and lang == "th" and entry.texts.get("zh") and zh_len(entry.texts["zh"]) <= zh_warn:
                level = "warn"
            if level:
                line = block_at if level == "block" else warn_at
                add("R-LEN", level, "%d%s" % (size, unit),
                    "%s超过%s线 %d%s" % (ROLE_NAMES[role], "拦截" if level == "block" else "提醒", line, unit))

        # R-SENT
        if lang in ("zh", "en") and role not in NO_SENTENCE_LIMIT and only is None:
            count = sentences(lang, raw, role)
            limit = 1 if role in ONE_SENTENCE else 3 if role in THREE_SENTENCES else 2
            if count > limit or (count >= 3 and role not in THREE_SENTENCES):
                add("R-SENT", "block", "%d 句" % count, "%s最多 %d 句" % (ROLE_NAMES[role], limit))
            if role == "button" and re.search(r"[。.]$", raw.strip()):
                add("R-SENT", "warn", raw.strip()[-1], "按钮不带句号")

        if lexical:
            if role != "email":
                for rule in ("R-WHY", "R-HEDGE"):
                    for level, snip, note in self._lex(rule, lang, text):
                        if lang == "th" and rule == "R-WHY" and role == "error" and re.match(r"เนื่องจาก|เพราะ", snip):
                            level = "warn"  # Frappe / ERPNext 官方泰文报错就这样写
                        add(rule, level, snip, note)
                we_text = self._strip_allowed("R-WE-OK", lang, text)
                for level, snip, note in self._lex("R-WE", lang, we_text):
                    add("R-WE", level, snip, note)
                for level, snip, note in self._lex("R-HELLO", lang, text):
                    add("R-HELLO", level, snip, note)
            for level, snip, note in self._lex("R-TALK", lang, text):
                add("R-TALK", level, snip, note)
            for level, snip, note in self._lex("R-HINT", lang, text):
                add("R-HINT", level, snip, note)
            narr = self._lex("R-NARR", lang, text)
            if narr:
                many = (sentences(lang, raw, role) if lang != "th" else 0) >= 2 or (
                    clauses(lang, raw) >= 2 and self._escalates(lang, text))
                if role in ("guide", "email") or (role == "error" and _NARR_ERROR_OK.search(text)):
                    many = False
                level, snip, note = narr[0]
                add("R-NARR", "block" if many else "warn", snip, note + ("（多句 / 多分句解释机制）" if many else ""))
        elif role != "email":
            for level, snip, note in self._lex("R-HELLO", lang, text):
                add("R-HELLO", level, snip, note)

        # 以中文为准：英文 / 泰文的 R-WHY / R-HEDGE / R-NARR 拦截，中文不拦时降为标黄
        if authority is not None and lang in ("en", "th"):
            for finding in out:
                if finding.rule in AUTHORITY_RULES and finding.level == "block" and authority.get(finding.rule) != "block":
                    finding.level = "warn"
                    finding.message += "（中文未命中，以中文为准）"
        if only is not None:
            return out

        # R-EMOJI
        if role != "email":
            found = []
            for pattern in self._patterns("R-EMOJI", lang):
                found += [m.group(0) for m in pattern.regex.finditer(text)]
            if found:
                def is_status(symbol):
                    return self._allow("R-EMOJI-STATUS", lang, symbol) and (entry.source == "table" or (
                        role in ("status", "label", "option") and raw.strip().startswith(symbol)))
                faces = [symbol for symbol in found if not is_status(symbol)]
                if faces:
                    add("R-EMOJI", "block", faces[0], "表情")
                else:
                    add("R-EMOJI", "warn", found[0], "状态符号（导出表 / 状态打头，配图例）")

        # R-DEV
        hard = [] if (role == "option" and lang == "en") else self._lex("R-DEV-HARD", lang, text)
        for level, snip, note in hard:
            add("R-DEV", "block", snip, note)
        if "\n\n" in raw and role not in ("line", "email"):
            add("R-DEV", "block", "⏎⏎", "空行分段（Markdown / 开发说明）")
        stripped = self._strip_terms(entry, text)
        for level, snip, note in self._dev_terms(entry, lang, stripped):
            add("R-DEV", level, snip, note)

        # R-MIX：中 / 泰文里夹的生僻英文（命中 R-DEV 英文术语的已在上面判过）
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
                add("R-EMPTY", "block", text.strip()[:12], "空态只写事实（暂无… / 没有… / No … / ไม่มี…）")
            guide = self._lex("R-EMPTY-GUIDE", lang, text)
            if guide:
                add("R-EMPTY", "block", guide[0][1], "空态后不接操作引导")

        # R-NAME
        if self.name_guard is not None and (entry.source, entry.key) not in self.name_allowed:
            bad = self.name_guard.offending(self._strip_allowed("R-NAME-OK", lang, raw), self.name_guard.SHOWN)
            if bad:
                add("R-NAME", "block", bad, "Windows 端只叫「CC 桌面版」，CC 不叫助手")

        # R-LINE20：按码位数（LINE 平台的 20 字符限制怎么数泰文组合符没有核实，按严的算）
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


# ── 代码引用（R-HARD / R-CONCAT）──────────────────────────────────────────────

BRANDS = re.compile(r"^(CC|Pearnly|Express|MR\.ERP|DMS|LINE|Cowork|ERP|PDF|CSV|Excel|POS|VAT|OCR|×|·|—|…|/)$")
REF_NAMES = {
    "py-msgid": "_() 的 msgid 不在术语表 / 翻译包 / Frappe 自带词里",
    "desk-msgid": "__() 的 msgid 不在术语表 / 翻译包 / Frappe 自带词里",
    "html-msgid": "页面 _() 的 msgid 不在术语表 / 翻译包 / Frappe 自带词里",
    "tool-label": "工具声明的 label / ask / 进行中说法不在术语表里",
    "py-bare": "frappe.throw / msgprint / publish_realtime / frappe.*Error 写死文案（要包 _()）",
    "py-dynamic": "_() 的 msgid 是拼出来的（f-string / + / format），翻译层查不到",
    "desk-bare": "frappe.msgprint / throw / confirm / show_alert / set_intro 写死文案（要包 __()）",
    "desk-dynamic": "__() 的 msgid 是模板串拼出来的，翻译层查不到",
    "desk-key": "t(\"键\") 的键不在术语表里",
    "tsx-tr": "tr(\"…\") 的词不在术语表里",
    "tsx-t": "t(\"键\") 的键不在 i18n.ts 的 MESSAGES 里",
    "tsx-attr": "JSX 属性 / toast / alert / confirm 写死文案（走 i18n.ts / tr()）",
    "tsx-text": "JSX 文本写死文案（走 i18n.ts / tr()）",
    "html-cjk": "页面 / 邮件模板写死中文或泰文（走 _()）",
    "html-hard": "页面 / 邮件模板写死英文句子或 title / placeholder（走 _()）",
    "cc-desktop-hard": "CC 桌面版界面写死文案（走 i18n.py 的 t(\"键\")）",
    "cc-desktop-key": "CC 桌面版 t(\"键\") 的键不在 i18n.py 的 _M 里",
    "doctype-text": "DocType / Workspace / Report 新写的 label / description / 选项不在术语表 / Frappe 自带词里",
    "csv-manual": "翻译包新增了术语表里没有的行（手工行或改写后的残留；术语表才是唯一来源）",
    "py-concat": "_() 与字符串或另一个 _() 用 + 拼成一句（改成一条带占位符的词条）",
    "desk-concat": "__() 与字符串或另一个 __() 用 + 拼成一句（改成一条带占位符的词条）",
    "tsx-concat": "相邻的 t() / tr() 拼成一句（改成一条带占位符的词条）",
}
CONCAT_KINDS = {"py-concat", "desk-concat", "tsx-concat"}


def check_ref(ref, snap, known: set = None, upstream: set = frozenset()) -> list:
    """代码引用：在不在字典里、有没有写死、是不是拼出来的。

    known：认作"在字典里"的 msgid。差异模式传「当前术语表 + 基线翻译包 + Frappe / ERPNext 自带词」：本次新加的
    翻译包手工行不能拿来给新代码兜底（新代码用的词必须进术语表）。默认用当前快照的全部（术语表 + 翻译包）。
    upstream：锁定镜像里 Frappe / ERPNext 自带的 msgid —— 只给 _() / __() / 页面 / DocType 用（Frappe 的翻译层认它们）；
    tr() / t("键") / 工具声明走本仓术语表，不认。
    """
    kind, text = ref.kind, ref.text
    known = snap.msgids if known is None else known
    level = None
    if kind in ("py-msgid", "desk-msgid", "html-msgid", "doctype-text"):
        if text not in known and text not in upstream:
            level = "block"
    elif kind in ("tool-label", "desk-key", "tsx-tr"):
        if text not in known:
            level = "block"
    elif kind == "tsx-t":
        if text not in snap.assistant_keys:
            level = "block"
    elif kind == "cc-desktop-key":
        if text not in getattr(snap, "companion_keys", set()):
            level = "block"
    elif kind in ("tsx-attr", "tsx-text", "cc-desktop-hard"):
        value = text.split("=", 1)[1] if kind == "tsx-attr" and "=" in text else text
        value = re.sub(r"\{x\}|[\s·:—\-/()]+", " ", value).strip()
        if value and not all(BRANDS.match(word) for word in value.split()):
            level = "block"
    elif kind in ("py-bare", "html-cjk", "html-hard", "py-dynamic", "desk-bare", "desk-dynamic") or kind in CONCAT_KINDS:
        level = "block"
    elif kind == "csv-manual":
        # 生成器只合并不删：术语表里改写过的 msgid 会在翻译包留下旧行，分不清是手工加的还是残留，所以只标黄；
        # 新代码引用它时由上面的 msgid 判据拦（差异模式的 known 不含本次新加的翻译包行）
        level = "warn"
    if not level:
        return []
    rule = "R-CONCAT" if kind in CONCAT_KINDS else "R-HARD"
    return [Finding(rule, level, "", text[:80], REF_NAMES[kind], "代码:%s" % ref.path, "%s:%d" % (ref.path, ref.line))]


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
