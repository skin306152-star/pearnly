# -*- coding: utf-8 -*-
"""文案来源的抽取器。

两类产物：

  词条（Entry）   —— 字典里的一条文案：来源、键、四种语言的值。内容判据（口语、原因解释、长度……）只对它判。
  代码引用（CodeRef）—— 代码把文案交给字典的那一处（`_("…")`、`__("…")`、`tr("…")`）或绕开字典
                    写死的那一处（JSX 文本、`frappe.throw("…")`、页面里的中文 / 泰文）。只判 R-HARD：
                    引用的词在不在字典里、有没有写死。

字典（内容在这里判）：

  1. 术语表 apps/<app>/<app>/i18n/messages.tsv            来源 = App 名，键 = key 列，没有就用 en（msgid）
  2. CC 网页 frontend/assistant/src/i18n.ts              来源 = assistant，键 = 对象名.键路径（不含语言段）
  3. CC 桌面版 companion/src/companion/i18n.py 的 _M      来源 = companion
  4. Keycloak 主题 messages_*.properties                  来源 = keycloak，键 = 主题.键
  5. keep-cjk 整文件豁免、但本身给用户看的四语表（TABLE_MODULES） 来源 = table

代码（只扫本次改动的文件；全量审计时扫全部）：

  6. Python：_()/_lt()/N_() 的第一个常量参数（f-string / + / format 拼出来的 msgid 直接拦）；frappe.throw / msgprint
     （位置参数与 msg= / title=，含 f-string）、publish_realtime("msgprint", …)、frappe.*Error(…) 的写死文案；
     _() 与字符串或另一个 _() 用 + 拼接；assistant/ 下工具声明里 label / ask / ask_when 的值；*STEP_NAMES 的值
  7. Desk JS（public/、doctype/、page/）：__("…") 与 t("key")；frappe.msgprint / throw / confirm / show_alert / prompt、
     set_intro / set_headline 的写死首参（含 {message: "…"}）；__() 与字面量或另一个 __() 用 + 拼接
  8. CC 网页 TSX / TS：JSX 文本与 {"…"} 子节点、title / aria-label / placeholder / alt / label / content / description /
     message / tooltip / helperText 属性（含 {`…`} 与 {cond ? "a" : "b"}）、toast.*() / alert() / confirm() 的字面量、
     tr("…")、t("…")；相邻 {t(…)}{t(…)} 与模板串里 ≥ 2 个 t( / tr( 插值（R-CONCAT）
  9. www/*.html、templates/emails/*.html：{{ _("…") }}、写死的中文 / 泰文、标签外 ≥ 2 个英文词的文本与 title / placeholder；
     页内 <script> 的字符串字面量（中文 / 泰文一律算；英文句子写进 textContent / innerHTML / innerText 或交给 msgprint / alert 时算）
 10. 翻译包 translations/*.csv 里新增的手工行（msgid 在术语表里找不到）
 11. CC 桌面版 companion/src/companion/*.py（i18n.py 除外）：Qt 控件与通知（QLabel / setText / setWindowTitle /
     setToolTip / addAction / showMessage / _notify …）、{"message": "…"} 的写死文案；t("键") 的键不在 _M 里
 12. DocType / Workspace / Report JSON：新写的 label / description 必须在术语表或 Frappe 自带词里（内容由术语表那一行判）

不扫（模型读的、写进外部系统的、日志与测试）：见 NOT_SCANNED。
DocType JSON 同时给同 msgid 的术语表行定角色（label → 标签，description → 字段说明，Select 选项 → 选项），
只有 System Manager 能读的 DocType 的标签与说明、以及 hidden 字段（Desk 表单不显示）的标签与说明算管理后台；
hidden 字段新写的标签 / 说明不在术语表只标黄。只被控制面代码（ADMIN_CODE）调用的 msgid 也算管理后台。调用点（frappe.throw(_())、placeholder=、subject=、
aria-* …）也给词条定角色（callsite_roles），优先于按键名猜。
"""

from __future__ import annotations

import ast
import csv
import io
import json
import re
from dataclasses import dataclass, field

LANGS = ("zh", "en", "th", "ja")

GLOSSARY = re.compile(r"^apps/([^/]+)/[^/]+/i18n/messages\.tsv$")
TRANSLATION = re.compile(r"^apps/([^/]+)/[^/]+/translations/(en|zh|th|ja)\.csv$")
ASSISTANT_I18N = "frontend/assistant/src/i18n.ts"
COMPANION_I18N = "companion/src/companion/i18n.py"
KEYCLOAK = re.compile(r"^infrastructure/keycloak/themes/([^/]+)/login/messages/messages_([A-Za-z_]+)\.properties$")
DOCTYPE_JSON = re.compile(r"^apps/.*/(doctype|workspace|report)/[^/]+/[^/]+\.json$")
PYTHON = re.compile(r"^apps/.*\.py$")
DESK_JS = re.compile(r"^apps/[^/]+/[^/]+/(public/.*|.*/(doctype|page)/[^/]+/[^/]+)\.js$")
FRONTEND = re.compile(r"^frontend/assistant/src/.*\.(tsx|ts)$")
HTML = re.compile(r"^apps/[^/]+/[^/]+/(www/.*|templates/emails/[^/]+)\.html$")
COMPANION_CODE = re.compile(r"^companion/src/companion/(?!i18n\.py$)[^/]+\.py$")
# 调用点角色：这些文件里看 frappe.throw(_())、placeholder=、subject=、aria-* … 怎么用一条词条
CALLSITE_FILES = re.compile(r"^(apps/.*\.(py|js)|frontend/(src|assistant/src)/.*\.(vue|ts|tsx|js))$")
ADMIN_ROLES = {"System Manager", "Administrator"}
# 只给控制面用的代码：这里调用的 msgid（且别处没有调用）按管理后台判（开发术语只标黄）。共用层模块登记在这里要写原因，
# tests/test_check_copy.py 守着它确实只被控制面引用
ADMIN_CODE = {
    "apps/pearnly_control/": "控制面 App（发号、开户、部署、钱包），只装在管理后台站点",
    "apps/pearnly_core/pearnly_core/keycloak_admin.py": "身份服务管理客户端：只有控制面开户 / 拆户与开发脚本调用",
}
# CC 网页 tr() 的词条由这里的 terms() 按当前语言给出（TERM_WORDS + 按前缀取的术语表键）：
# TERM_WORDS 里的 Frappe 自带 msgid 运行时是 Frappe 官方译文，tr() 引用它不算不在字典里
TERMS_FILE = "apps/pearnly_cowork/pearnly_cowork/folder_intake/api.py"
# frappe.*Error(…) 里只作重试信号、不会到界面的异常（ocr/remote/callback.py 的 QueryDeadlockError 让任务重排）
RETRY_SIGNALS = {"QueryDeadlockError", "QueryTimeoutError"}

# keep-cjk.tsv 里整文件豁免（`*`）、但内容本身是给用户看的四语表：按 AST 取模块里以语言为键的 dict。
# 以后 keep-cjk 再加"规格："整文件豁免，必须登记在这里或 NOT_COPY_SPEC（tests/test_check_copy.py 守着）。
TABLE_MODULES = (
    "apps/pearnly_cowork/pearnly_cowork/reconciliation/bank_warnings.py",
    "apps/pearnly_cowork/pearnly_cowork/reconciliation/export/summary.py",
    "apps/pearnly_cowork/pearnly_cowork/reconciliation/export/labels.py",
    "apps/pearnly_cowork/pearnly_cowork/reconciliation/export/usage.py",
    "apps/pearnly_cowork/pearnly_cowork/reconciliation/export/income_workbook.py",
    "apps/pearnly_cowork/pearnly_cowork/reconciliation/export/vat_check_i18n.py",
    "apps/pearnly_cowork/pearnly_cowork/reconciliation/vat_check_core.py",
    "apps/pearnly_cowork/pearnly_cowork/contracts/errors.py",
)

# keep-cjk 里"规格："整文件豁免、但不是界面文案的：写明为什么不扫
NOT_COPY_SPEC = {
    "product_manifest.py": "产品清单的禁止事项，给开发与审计看，不进界面",
    "apps/pearnly_cowork/pearnly_cowork/roundtrip/spec.py": "MR.ERP 泰文导入格式的表名与列名，导出再导回的合同，按原字面认",
    "apps/pearnly_cowork/pearnly_cowork/roundtrip/export_writer.py": "同上，复核工作簿随 MR.ERP 泰文格式",
    "apps/pearnly_cowork/pearnly_cowork/roundtrip/contracts.py": "同上，回导行的泰文兜底说明随 MR.ERP 格式",
    "apps/pearnly_cowork/pearnly_cowork/stocktake/export.py": "盘点导出表的泰文默认值；界面语言的文案走 cowork.stocktake.export.* 词条",
    "apps/pearnly_cowork/pearnly_cowork/readback/layout.py": "照 Express 报表泰文版式出表，解析器按原字面认列",
    "apps/pearnly_cowork/pearnly_cowork/ocr/schema/results.py": "结构化结果的字段说明，给开发与模型看",
}

# 不扫的代码：模型读的提示词与工具说明、写进外部系统的值。按路径片段匹配。
NOT_SCANNED = {
    "/assistant/definition.py": "给模型的工具说明与系统提示词，模型读，不进界面",
    "/ocr/engine/prompts.py": "识别模型提示词",
    "/ocr/engine/fewshot.py": "识别模型示例",
    "/ocr/schema/": "识别结构的字段说明，给模型与开发看",
    "/reconciliation/bank_prompts.py": "对账单抽取提示词",
    "/recognition/checks.py": "给识别模型的校验提示",
    "/patches/": "数据迁移补丁，不进界面",
    "/tests/": "测试样例",
}

TRANSLATE_CALLS = {"_", "_lt", "N_"}
TOOL_KEYS = {"label", "ask"}

CJK_THAI = re.compile(r"[一-鿿ก-ฺเ-๛]")


@dataclass
class Entry:
    """字典里的一条文案。"""

    source: str
    key: str
    texts: dict
    loc: str = ""
    hint_role: str = ""
    role: str = ""
    surface: str = "user"
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return "%s:%s" % (self.source, self.key)

    @property
    def primary_lang(self) -> str:
        return "zh" if self.texts.get("zh") else "en"

    @property
    def primary(self) -> str:
        return self.texts.get(self.primary_lang, "")


@dataclass(frozen=True)
class CodeRef:
    """代码里的一处文案引用。kind 见 rules.check_ref。"""

    kind: str
    text: str
    path: str
    line: int

    @property
    def ident(self) -> tuple:
        return (self.path, self.kind, self.text)


def excluded(path: str) -> bool:
    if "/test_" in path or path.endswith("_test.py"):
        return True
    return any(part in path for part in NOT_SCANNED) or path in TABLE_MODULES


# ── 1. 术语表 ────────────────────────────────────────────────────────────────


def glossary_entries(path: str, text: str) -> list:
    app = GLOSSARY.match(path).group(1)
    out, seen = [], set()
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    for raw in reader:
        row = {k: str(raw.get(k) or "").replace("\\n", "\n").strip() for k in ("en", "zh", "th", "ja", "key")}
        if not row["en"] and not row["key"]:
            continue
        key = row["key"] or row["en"]
        if key in seen:
            continue
        seen.add(key)
        texts = {lang: row[lang] for lang in LANGS if row[lang]}
        entry = Entry(app, key, texts, loc="%s:%d" % (path, reader.line_num))
        entry.extra["keyed"] = bool(row["key"])
        entry.extra["line"] = reader.line_num
        out.append(entry)
    return out


def translation_msgids(text: str) -> set:
    found = set()
    for row in csv.reader(io.StringIO(text)):
        if row and row[0].strip():
            found.add(row[0])
    return found


# ── JS / TS 词法 ─────────────────────────────────────────────────────────────


@dataclass
class Tok:
    kind: str  # id / str / tpl / num / re / p
    value: str
    line: int


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_REGEX_PREV = set("(,=:[!&|?{};+-*%~^<>")


def _unescape(src: str, j: int):
    """src[j] 是反斜杠：返回 (字符, 下一个位置)。"""
    nxt = src[j + 1] if j + 1 < len(src) else ""
    if nxt == "u":
        if src[j + 2:j + 3] == "{":
            end = src.find("}", j + 3)
            try:
                return chr(int(src[j + 3:end], 16)), end + 1
            except ValueError:
                return "", end + 1
        try:
            return chr(int(src[j + 2:j + 6], 16)), j + 6
        except ValueError:
            return "", j + 2
    if nxt == "\n":
        return "", j + 2
    return _ESCAPES.get(nxt, nxt), j + 2


def _string(src: str, i: int):
    quote, j, out = src[i], i + 1, []
    while j < len(src) and src[j] != quote and src[j] != "\n":
        if src[j] == "\\":
            ch, j = _unescape(src, j)
            out.append(ch)
            continue
        out.append(src[j])
        j += 1
    return "".join(out), j + 1


def _template(src: str, i: int):
    """src[i] 是反引号：返回 (文字（${…} 记作 {x}）, 结束位置, 换行数)。"""
    j, out, lines, n = i + 1, [], 0, len(src)
    while j < n:
        ch = src[j]
        if ch == "\\":
            ch, j = _unescape(src, j)
            out.append(ch)
            continue
        if ch == "`":
            return "".join(out), j + 1, lines
        if src.startswith("${", j):
            depth, k = 1, j + 2
            while k < n and depth:
                c = src[k]
                if c in "\"'":
                    _, k = _string(src, k)
                    continue
                if c == "`":
                    _, k, more = _template(src, k)
                    lines += more
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                elif c == "\n":
                    lines += 1
                k += 1
            out.append("{x}")
            j = k
            continue
        if ch == "\n":
            lines += 1
        out.append(ch)
        j += 1
    return "".join(out), j, lines


def js_tokens(src: str) -> list:
    """够用的 JS / TS 词法：注释、字符串、模板串、正则字面量都认得，JSX 文本不认（TSX 另用正则）。"""
    toks, i, n, line = [], 0, len(src), 1
    prev = None
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            end = n if j < 0 else j + 2
            line += src.count("\n", i, end)
            i = end
            continue
        if c in "\"'":
            value, j = _string(src, i)
            prev = Tok("str", value, line)
            toks.append(prev)
            i = j
            continue
        if c == "`":
            value, j, lines = _template(src, i)
            prev = Tok("tpl", value, line)
            toks.append(prev)
            line += lines
            i = j
            continue
        if c == "/" and (
            prev is None
            or (prev.kind == "p" and prev.value in _REGEX_PREV)
            or (prev.kind == "id" and prev.value in ("return", "typeof", "case", "in", "of"))
        ):
            j, in_class = i + 1, False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    break
                j += 1
            j += 1
            while j < n and src[j].isalpha():
                j += 1
            prev = Tok("re", src[i:j], line)
            toks.append(prev)
            i = j
            continue
        if c.isalpha() or c in "_$":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            prev = Tok("id", src[i:j], line)
            toks.append(prev)
            i = j
            continue
        if c.isdigit():
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] in "._"):
                j += 1
            prev = Tok("num", src[i:j], line)
            toks.append(prev)
            i = j
            continue
        for op in ("...", "=>", "?.", "??", "===", "!==", "==", "!=", "<=", ">="):
            if src.startswith(op, i):
                prev = Tok("p", op, line)
                break
        else:
            prev = Tok("p", c, line)
        toks.append(prev)
        i += len(prev.value)
    return toks


def _is(tok, value) -> bool:
    return tok is not None and tok.kind == "p" and tok.value == value


def _ts_value(toks, i, path, leaves):
    """从 toks[i] 开始读一个属性值，返回结束处（指向 , 或 }，不吃掉）。"""
    if _is(toks[i], "{"):
        i = _ts_object(toks, i, path, leaves)
    depth, strings = 0, []
    while i < len(toks):
        tok = toks[i]
        if tok.kind == "p":
            if tok.value in ("(", "[", "{"):
                depth += 1
            elif tok.value in (")", "]", "}"):
                if depth == 0:
                    break
                depth -= 1
            elif tok.value == "," and depth == 0:
                break
        elif tok.kind in ("str", "tpl"):
            strings.append(tok)
        i += 1
    for index, tok in enumerate(strings):
        leaves[path if index == 0 else path + ("#%d" % (index + 1),)] = (tok.value, tok.line)
    return i


def _ts_object(toks, i, path, leaves):
    """toks[i] 是 {：读完对象，返回 } 之后的位置。"""
    i += 1
    while i < len(toks):
        tok = toks[i]
        if _is(tok, "}"):
            return i + 1
        if _is(tok, ",") or _is(tok, "..."):
            i += 1
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if tok.kind in ("id", "str", "num") and (_is(nxt, ":") or _is(nxt, "(")):
            i = _ts_value(toks, i + 2 if _is(nxt, ":") else i + 1, path + (tok.value,), leaves)
            continue
        i += 1
    return i


def ts_lang_tables(src: str) -> dict:
    """`const NAME = { en: {...}, zh: {...} }` 这类以语言为键的对象：{NAME: {(语言, 键…): (文字, 行)}}。"""
    toks = js_tokens(src)
    tables = {}
    for i in range(len(toks) - 3):
        if (
            toks[i].kind == "id"
            and toks[i].value == "const"
            and toks[i + 1].kind == "id"
            and _is(toks[i + 2], "=")
            and _is(toks[i + 3], "{")
        ):
            leaves: dict = {}
            _ts_object(toks, i + 3, (), leaves)
            langs = {path[0] for path in leaves}
            if len(langs & set(LANGS)) >= 2:
                tables[toks[i + 1].value] = leaves
    return tables


def assistant_entries(path: str, text: str) -> list:
    entries: dict = {}
    for name, leaves in ts_lang_tables(text).items():
        for keypath, (value, line) in leaves.items():
            lang, rest = keypath[0], keypath[1:]
            if lang not in LANGS or not rest:
                continue
            key = ".".join([name] + [part for part in rest if not part.startswith("#")])
            key += "".join(part for part in rest if part.startswith("#"))
            entry = entries.get(key)
            if entry is None:
                entry = entries[key] = Entry("assistant", key, {}, loc="%s:%d" % (path, line))
            entry.texts[lang] = value
            if lang == "zh":
                entry.loc = "%s:%d" % (path, line)
    return list(entries.values())


# ── 3. CC 桌面版 ─────────────────────────────────────────────────────────────


def companion_entries(path: str, text: str) -> list:
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in tree.body:
        targets = getattr(node, "targets", None) or ([node.target] if isinstance(node, ast.AnnAssign) else [])
        if not any(isinstance(t, ast.Name) and t.id == "_M" for t in targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for k, v in zip(node.value.keys, node.value.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                continue
            if isinstance(v, ast.Tuple) and len(v.elts) >= 2:
                try:
                    zh, th = ast.literal_eval(v.elts[0]), ast.literal_eval(v.elts[1])
                except ValueError:
                    continue
                if isinstance(zh, str) and isinstance(th, str):
                    out.append(Entry("companion", k.value, {"zh": zh, "th": th}, loc="%s:%d" % (path, k.lineno)))
    return out


# ── 4. Keycloak ──────────────────────────────────────────────────────────────

_KEYCLOAK_LANG = {"zh_CN": "zh", "zh": "zh", "en": "en", "th": "th", "ja": "ja"}


def properties(text: str) -> dict:
    """Java properties：{键: (值, 行)}。认续行与 \\uXXXX。"""
    out = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        start = i
        line = lines[i].strip()
        i += 1
        if not line or line[0] in "#!":
            continue
        while line.endswith("\\") and not line.endswith("\\\\") and i < len(lines):
            line = line[:-1] + lines[i].strip()
            i += 1
        match = re.match(r"((?:\\.|[^=:\s])+)\s*[=:\s]\s*(.*)$", line)
        if not match:
            continue
        key = match.group(1).replace("\\", "")
        value = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), match.group(2))
        value = value.replace("\\n", "\n").replace("\\:", ":").replace("\\=", "=").replace("\\\\", "\\")
        out[key] = (value, start + 1)
    return out


def keycloak_entries(files: dict) -> list:
    """files: {路径: 文本}。同一主题的各语言文件合成一条词条。"""
    entries: dict = {}
    for path, text in sorted(files.items()):
        match = KEYCLOAK.match(path)
        lang = _KEYCLOAK_LANG.get(match.group(2))
        if not lang:
            continue
        theme = match.group(1)
        for key, (value, line) in properties(text).items():
            ident = "%s.%s" % (theme, key)
            entry = entries.get(ident)
            if entry is None:
                entry = entries[ident] = Entry("keycloak", ident, {}, loc="%s:%d" % (path, line))
            entry.texts[lang] = value
            if lang == "zh":
                entry.loc = "%s:%d" % (path, line)
    return list(entries.values())


# ── 5. 四语表模块 ────────────────────────────────────────────────────────────


def _table_walk(node, path, leaves, lang=None):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if lang:
            leaves.setdefault(path, {})[lang] = (node.value, node.lineno)
        return
    if isinstance(node, ast.Dict):
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        by_lang = len(set(keys) & set(LANGS)) >= 2
        for k, v in zip(node.keys, node.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, (str, int))):
                continue
            if by_lang and k.value in LANGS:
                _table_walk(v, path, leaves, k.value)
            else:
                _table_walk(v, path + (str(k.value),), leaves, lang)
        return
    if isinstance(node, (ast.List, ast.Tuple)):
        for index, item in enumerate(node.elts):
            _table_walk(item, path + (str(index),), leaves, lang)
        return
    if isinstance(node, ast.Call):
        for arg in node.args:
            _table_walk(arg, path, leaves, lang)


def table_entries(path: str, text: str) -> list:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    leaves: dict = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    _table_walk(node.value, (target.id,), leaves)
    short = path.split("/", 2)[-1] if path.startswith("apps/") else path
    out = []
    for keypath, by_lang in leaves.items():
        if len(by_lang) < 2:
            continue
        texts = {lang: value for lang, (value, _) in by_lang.items()}
        line = (by_lang.get("zh") or next(iter(by_lang.values())))[1]
        out.append(Entry("table", "%s#%s" % (short, ".".join(keypath)), texts, loc="%s:%d" % (path, line)))
    return out


# ── DocType JSON：给术语表行定角色；新写的 label / description / 选项要在字典里 ──────


def _hidden(node) -> bool:
    return node.get("hidden") in (1, "1", True)


def _doctype_values(data, with_hidden=False):
    """[(文字, 角色)]：label → 标签，description → 字段说明，Select 的 options 逐行 → 选项。
    with_hidden：[(文字, 角色, 是否 hidden 字段)]（hidden 字段 Desk 表单不显示）。"""
    out = []

    def visit(node):
        if isinstance(node, dict):
            select = node.get("fieldtype") == "Select"
            hidden = _hidden(node)
            for key, value in node.items():
                if isinstance(value, str) and value.strip():
                    if key == "description":
                        out.append((value.strip(), "field_desc", hidden))
                    elif key == "label":
                        out.append((value.strip(), "label", hidden))
                    elif key == "options" and select:
                        for option in value.split("\n"):
                            if option.strip() and LETTERS.search(option):
                                out.append((option.strip(), "option", hidden))
                elif not isinstance(value, str):
                    visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(data)
    return out if with_hidden else [(value, role) for value, role, _hidden_field in out]


def _only_hidden(values) -> set:
    """这份 DocType 里只出现在 hidden 字段上的文字。"""
    shown, hidden = set(), set()
    for value, _role, is_hidden in values:
        (hidden if is_hidden else shown).add(value)
    return hidden - shown


def doctype_info(text: str, with_hidden=False):
    """(角色表 {文字: 角色}, 是否只有 System Manager / Administrator 能读)；with_hidden 时另交回只在 hidden 字段上的文字。"""
    try:
        data = json.loads(text)
    except ValueError:
        return ({}, False, set()) if with_hidden else ({}, False)
    roles = {}
    values = _doctype_values(data, with_hidden=True)
    for value, role, _hidden_field in values:
        roles.setdefault(value, role)
    perms = {p.get("role") for p in data.get("permissions", []) if isinstance(p, dict)} if isinstance(data, dict) else set()
    admin = bool(perms) and perms <= ADMIN_ROLES
    return (roles, admin, _only_hidden(values)) if with_hidden else (roles, admin)


def doctype_roles(text: str) -> dict:
    return doctype_info(text)[0]


def doctype_refs(path: str, text: str) -> list:
    """DocType JSON 里的 label / description / Select 选项：差异模式只看新写的，要求在术语表或 Frappe 自带词里。"""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out, seen = [], set()
    values = _doctype_values(data, with_hidden=True)
    hidden_only = _only_hidden(values)
    for value, role, _hidden_field in values:
        # Select 选项多是取值码（cowork / erp / zh），Desk 有译文才显示译文：只要求 label 与 description 进字典
        if role == "option" or value in seen or not LETTERS.search(value):
            continue
        seen.add(value)
        pos = text.find(json.dumps(value, ensure_ascii=False)[1:-1])
        # 只在 hidden 字段上的（Desk 表单不显示）：不在字典里只标黄
        kind = "doctype-hidden" if value in hidden_only else "doctype-text"
        out.append(CodeRef(kind, value, path, _line_of(text, pos) if pos >= 0 else 0))
    return out


# ── 6. Python ────────────────────────────────────────────────────────────────


def _call_name(node) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _joined_text(node) -> str:
    """f-string 的文字部分，{…} 记作 {x}。"""
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append("{x}")
    return "".join(parts)


def _literal_text(node):
    """字符串常量或 f-string 的文字；别的返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return _joined_text(node)
    return None


def _is_frappe_attr(node, names) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in names
        and isinstance(func.value, ast.Name)
        and func.value.id == "frappe"
    )


def _is_translate(node) -> bool:
    return isinstance(node, ast.Call) and _call_name(node) in TRANSLATE_CALLS and isinstance(node.func, ast.Name)


def _add_leaves(node, leaves, inner):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        inner.add(id(node))
        _add_leaves(node.left, leaves, inner)
        _add_leaves(node.right, leaves, inner)
    else:
        leaves.append(node)


def python_refs(path: str, text: str) -> list:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    tool_file = "/assistant/" in path
    out = []
    seen_binops: set = set()

    def bare(node, value, line):
        if value is not None and LETTERS.search(value) and not KEY_LIKE.match(value.strip()):
            out.append(CodeRef("py-bare", value, path, line))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            first = node.args[0] if node.args else None
            literal = isinstance(first, ast.Constant) and isinstance(first.value, str)
            if name in TRANSLATE_CALLS and isinstance(node.func, ast.Name):
                if literal:
                    out.append(CodeRef("py-msgid", first.value, path, node.lineno))
                elif isinstance(first, (ast.JoinedStr, ast.BinOp)) or (
                    isinstance(first, ast.Call) and isinstance(first.func, ast.Attribute) and first.func.attr == "format"
                ):
                    shown = _literal_text(first) or ast.unparse(first)
                    if not KEY_PREFIX.match(shown):
                        out.append(CodeRef("py-dynamic", shown[:80], path, node.lineno))
            elif _is_frappe_attr(node, ("throw", "msgprint")):
                bare(node, _literal_text(first) if first is not None else None, node.lineno)
                for keyword in node.keywords:
                    if keyword.arg in ("msg", "title"):
                        bare(node, _literal_text(keyword.value), node.lineno)
            elif _is_frappe_attr(node, ("publish_realtime",)):
                if literal and first.value == "msgprint" and len(node.args) > 1:
                    bare(node, _literal_text(node.args[1]), node.lineno)
                for keyword in node.keywords:
                    if keyword.arg == "message" and literal and first.value == "msgprint":
                        bare(node, _literal_text(keyword.value), node.lineno)
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "frappe"
                and node.func.attr.endswith("Error")
                and node.func.attr not in RETRY_SIGNALS
                and first is not None
            ):
                bare(node, _literal_text(first), node.lineno)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) and id(node) not in seen_binops:
            leaves: list = []
            _add_leaves(node, leaves, seen_binops)
            translated = [leaf for leaf in leaves if _is_translate(leaf)]
            literals = [leaf for leaf in leaves
                        if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str) and LETTERS.search(leaf.value)]
            if len(translated) >= 2 or (translated and literals):
                parts = []
                for leaf in leaves:
                    if _is_translate(leaf) and leaf.args and isinstance(leaf.args[0], ast.Constant):
                        parts.append("_(%s)" % leaf.args[0].value)
                    elif isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
                        parts.append(repr(leaf.value))
                    else:
                        parts.append("…")
                out.append(CodeRef("py-concat", " + ".join(parts)[:80], path, node.lineno))
        elif isinstance(node, ast.Dict) and tool_file:
            for k, v in zip(node.keys, node.values):
                if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                    continue
                if k.value in TOOL_KEYS and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append(CodeRef("tool-label", v.value, path, v.lineno))
                elif k.value == "ask_when" and isinstance(v, ast.Dict):
                    for sub in v.values:
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                            out.append(CodeRef("tool-label", sub.value, path, sub.lineno))
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            if any(isinstance(t, ast.Name) and t.id.endswith("STEP_NAMES") for t in node.targets):
                for sub in node.value.values:
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        out.append(CodeRef("tool-label", sub.value, path, sub.lineno))
    return out


# ── CC 桌面版程序（i18n.py 以外）─────────────────────────────────────────────

# Qt 控件与通知：这些调用的字符串参数是界面文案，要走 i18n.py 的 t("键")
COMPANION_UI_CALLS = {
    "QLabel", "QPushButton", "QToolButton", "QAction", "QCheckBox", "QRadioButton", "QGroupBox", "QMenu",
    "setText", "setWindowTitle", "setToolTip", "setPlaceholderText", "setInformativeText", "setStatusTip",
    "setDetailedText", "addAction", "addMenu", "showMessage", "_notify", "notify", "show_toast",
    "showinfo", "showerror", "showwarning", "askyesno", "askokcancel",
}
# QMessageBox.information / warning / critical / question：只认 QMessageBox 的静态调用（logger.warning 是日志）
COMPANION_BOX_CALLS = {"information", "warning", "critical", "question", "about"}
COMPANION_UI_KEYWORDS = {"text", "title", "message", "body", "tooltip"}
COMPANION_BRANDS = re.compile(r"^(CC|CC Desktop|Pearnly|Express|MR\.ERP|DMS|LINE)$")


def companion_refs(path: str, text: str) -> list:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []

    def hard(value, line):
        if value is None or not LETTERS.search(value) or COMPANION_BRANDS.match(value.strip()):
            return
        if not re.search(r"[A-Za-z]{2,}.*\s|" + CJK_THAI.pattern, value):
            return  # 单个英文词多半是对象名 / 样式名
        out.append(CodeRef("cc-desktop-hard", value[:80], path, line))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "t" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                out.append(CodeRef("cc-desktop-key", node.args[0].value, path, node.lineno))
            elif name in COMPANION_UI_CALLS or (
                name in COMPANION_BOX_CALLS and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "QMessageBox"
            ):
                for arg in node.args:
                    hard(_literal_text(arg), node.lineno)
                for keyword in node.keywords:
                    if keyword.arg in COMPANION_UI_KEYWORDS:
                        hard(_literal_text(keyword.value), node.lineno)
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "message":
                    hard(_literal_text(v), v.lineno)
    return out


# ── 7. Desk JS ───────────────────────────────────────────────────────────────

KEY_LIKE = re.compile(r"^[A-Za-z][\w-]*(\.[\w-]+)+$")
# 按键族取词条（`_(f"cowork.work.status.{status}")`、`'erp.detail.' + code`）：拼的是语义键，不是句子，放行
KEY_PREFIX = re.compile(r"^['\"]?[a-z][\w-]*(\.[\w-]+)*\.['\"]?(\{x\}|\s*\+\s*\w+)$")


DESK_BARE_CALLS = {"msgprint", "throw", "confirm", "show_alert", "prompt", "set_intro", "set_headline",
                   "set_headline_alert", "show_progress"}
DESK_BARE_KEYS = {"message", "title", "msg", "primary_action_label", "secondary_action_label"}
_WORDS2 = re.compile(r"[A-Za-z]{2,}\W+[A-Za-z]{2,}")
_HTML_TAG = re.compile(r"<[^<>]*>?|&\w+;")
_NOT_PROSE = re.compile(r"\S+@\S+|https?://\S+|\b(Pearnly|DMS|ERP|MR\.ERP|CC|LINE|Cowork|Express)\b")


def _first_object_strings(toks, i):
    """toks[i] 是 {：对象第一层 message / title … 键的字面量值。"""
    depth, out = 0, []
    j = i
    while j < len(toks):
        tok = toks[j]
        if tok.kind == "p" and tok.value in "{[(":
            depth += 1
        elif tok.kind == "p" and tok.value in "}])":
            depth -= 1
            if depth == 0:
                break
        elif (depth == 1 and tok.kind == "id" and tok.value in DESK_BARE_KEYS and j + 2 < len(toks)
              and _is(toks[j + 1], ":") and toks[j + 2].kind in ("str", "tpl")):
            out.append(toks[j + 2])
        j += 1
    return out


def desk_js_refs(path: str, text: str) -> list:
    toks = js_tokens(text)
    out = []
    n = len(toks)
    for i in range(n - 2):
        tok, paren, arg = toks[i], toks[i + 1], toks[i + 2]
        if tok.kind != "id" or not _is(paren, "("):
            continue
        dotted = i > 0 and _is(toks[i - 1], ".")
        if tok.value in DESK_BARE_CALLS and (dotted or tok.value == "msgprint"):
            if arg.kind in ("str", "tpl") and LETTERS.search(arg.value.replace("{x}", " ")):
                out.append(CodeRef("desk-bare", arg.value, path, arg.line))
            elif _is(arg, "{"):
                for value in _first_object_strings(toks, i + 2):
                    if LETTERS.search(value.value.replace("{x}", " ")):
                        out.append(CodeRef("desk-bare", value.value, path, value.line))
            continue
        if dotted or arg.kind not in ("str", "tpl"):
            continue
        if tok.value == "__":
            if "{x}" in arg.value and arg.kind == "tpl":
                if not KEY_PREFIX.match(arg.value):
                    out.append(CodeRef("desk-dynamic", arg.value, path, arg.line))
            else:
                out.append(CodeRef("desk-msgid", arg.value, path, arg.line))
        elif tok.value == "t" and KEY_LIKE.match(arg.value):
            out.append(CodeRef("desk-key", arg.value, path, arg.line))
    # 拼接：__() 与字面量或另一个 __() 用 + 连成一句
    for i, tok in enumerate(toks):
        if not _is(tok, "+"):
            continue
        neighbours = [toks[j] for j in (i - 1, i + 1) if 0 <= j < n]
        literal = next((x for x in neighbours if x.kind == "str" and _WORDS2.search(_HTML_TAG.sub(" ", x.value))), None)
        window = toks[max(0, i - 12):i + 13]
        translated = sum(1 for k, x in enumerate(window) if x.kind == "id" and x.value == "__"
                         and k + 1 < len(window) and _is(window[k + 1], "("))
        if (literal is not None and translated) or (translated >= 2 and (_is(toks[i - 1], ")") and i + 1 < n and (
                (toks[i + 1].kind == "id" and toks[i + 1].value == "__")
                or (toks[i + 1].kind == "str" and i + 3 < n and _is(toks[i + 2], "+")
                    and toks[i + 3].kind == "id" and toks[i + 3].value == "__")))):
            out.append(CodeRef("desk-concat", (literal.value if literal else "__() + __()")[:80], path, tok.line))
    return out


# ── 8. CC 网页 TSX / TS ──────────────────────────────────────────────────────

_TSX_BLOCK_COMMENT = re.compile(r"\{\s*/\*.*?\*/\s*\}|/\*.*?\*/", re.S)
_TSX_LINE_COMMENT = re.compile(r"(?<![:\"'\w/])//[^\n]*")
TSX_TEXT_ATTRS = ("title", "aria-label", "aria-description", "placeholder", "alt", "label", "content", "description",
                  "message", "tooltip", "helperText", "emptyText", "confirmText", "cancelText")
_TSX_ATTR = re.compile(r"(?<![\w-])(%s)=" % "|".join(re.escape(a) for a in TSX_TEXT_ATTRS))
_TSX_TEXT = re.compile(r"(?<=[^\s=\-])>([^<>{}]+)<")
_TSX_CHILD_LITERAL = re.compile(r"(?<![=\w$])\{\s*(\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`[^`]*`)\s*\}")
# JSX 文本里像代码的片段（泛型、比较、箭头函数、三元）不算文案；英文问句、括号说明、& 照样算文案
_TSX_CODEISH = re.compile(
    r"[;={}\[\]]|=>|&&|\|\||\b(const|return|let|if|else|null|undefined|true|false|typeof|void)\b"
    r"|^[\s:,?&|]|\w:\s*$|\?\s*[\w.]+\s*:|\w\(|^\s*\)|^\s*\((\.\.\.)?\w+\??:"
)
_TSX_TR = re.compile(r"\btr\(\s*(?:\"([^\"]+)\"|'([^']+)')\s*[,)]")
_TSX_T = re.compile(r"(?<![\w.])t\(\s*\"([A-Za-z][\w.]*)\"\s*\)")
_TSX_CONCAT = re.compile(
    r"\{\s*(?:t|tr)\([^{}]*\)\s*\}\s*(?:\{\s*[\"'][^\"'A-Za-z\u4e00-\u9fff]*[\"']\s*\}\s*)?\{\s*(?:t|tr)\("
    r"|`[^`]*\$\{\s*(?:t|tr)\([^`]*\$\{\s*(?:t|tr)\([^`]*`"
)
_TOAST_CALLS = {"alert", "confirm", "prompt"}
LETTERS = re.compile(r"[A-Za-zก-๛一-鿿]")
SHORTCUT = re.compile(r"^(Ctrl|⌘|Shift|Alt)[ +].{1,3}$")
_TRANSLATORS = {"t", "tr", "__", "_", "cn", "clsx", "twMerge"}


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _brace_end(src: str, i: int) -> int:
    """src[i] 是 {：返回配对的 } 之后的位置（跳过字符串与模板串）。"""
    depth, j, n = 0, i, len(src)
    while j < n:
        c = src[j]
        if c in "\"'":
            _, j = _string(src, j)
            continue
        if c == "`":
            _, j, _lines = _template(src, j)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def _shown(value: str) -> bool:
    """去掉占位 {x} 与分隔符后还有字母，且不是快捷键、点分键名。"""
    rest = value.replace("{x}", " ")
    return bool(LETTERS.search(rest)) and not SHORTCUT.match(value.strip()) and not KEY_LIKE.match(value.strip())


def _display_literals(expr: str) -> list:
    """属性表达式里会显示出来的字面量：不在 t() / tr() / cn() 调用里（含 t(a ? "x" : "y")），不是比较对象、
    对象键、下标，也不是表达式里嵌套 JSX 的属性值（className="…"）。"""
    toks = js_tokens(expr)
    out = []
    calls: list = []  # 每层括号是谁的调用
    for k, tok in enumerate(toks):
        prev = toks[k - 1] if k else None
        if _is(tok, "("):
            calls.append(prev.value if prev is not None and prev.kind == "id" else None)
            continue
        if _is(tok, ")"):
            if calls:
                calls.pop()
            continue
        if tok.kind not in ("str", "tpl") or not _shown(tok.value):
            continue
        if any(name in _TRANSLATORS for name in calls):
            continue
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        if (prev is not None and prev.kind == "p" and prev.value in ("===", "!==", "==", "!=", "=")) or (
                nxt is not None and nxt.kind == "p" and nxt.value in ("===", "!==", "==", "!=")):
            continue
        if _is(nxt, ":") and (prev is None or (prev.kind == "p" and prev.value in "{,")):
            continue  # 对象键（三元的 a ? "x" : "y" 不算）
        if _is(prev, "[") or _is(nxt, "]"):
            continue
        out.append(tok.value)
    return out


def frontend_refs(path: str, text: str) -> list:
    src = _TSX_BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    src = _TSX_LINE_COMMENT.sub("", src)
    out = []
    for match in _TSX_TR.finditer(src):
        out.append(CodeRef("tsx-tr", match.group(1) or match.group(2), path, _line_of(src, match.start())))
    for match in _TSX_T.finditer(src):
        out.append(CodeRef("tsx-t", match.group(1), path, _line_of(src, match.start())))
    for match in _TSX_CONCAT.finditer(src):
        out.append(CodeRef("tsx-concat", " ".join(match.group(0).split())[:80], path, _line_of(src, match.start())))
    # toast.*() / alert() / confirm()：首参写死
    toks = js_tokens(src)
    for k in range(len(toks) - 2):
        tok, paren, arg = toks[k], toks[k + 1], toks[k + 2]
        if tok.kind != "id" or not _is(paren, "(") or arg.kind not in ("str", "tpl") or not _shown(arg.value):
            continue
        dotted = k > 0 and _is(toks[k - 1], ".")
        owner = toks[k - 2] if dotted and k > 1 else None
        if (dotted and owner is not None and owner.kind == "id" and owner.value == "toast") or (
                tok.value in _TOAST_CALLS and (not dotted or (owner is not None and owner.value == "window"))):
            name = "toast.%s" % tok.value if dotted and owner is not None and owner.value == "toast" else tok.value
            out.append(CodeRef("tsx-attr", "%s=%s" % (name, arg.value), path, arg.line))
    if not path.endswith(".tsx"):
        return out
    for match in _TSX_ATTR.finditer(src):
        start = match.end()
        name = match.group(1)
        if start >= len(src):
            continue
        if src[start] in "\"'":
            value, _ = _string(src, start)
            values = [value]
        elif src[start] == "{":
            values = _display_literals(src[start + 1:_brace_end(src, start) - 1])
        else:
            continue
        for value in values:
            if _shown(value):
                out.append(CodeRef("tsx-attr", "%s=%s" % (name, value), path, _line_of(src, match.start())))
    for match in _TSX_TEXT.finditer(src):
        value = " ".join(match.group(1).split())
        if not value or not LETTERS.search(value) or _TSX_CODEISH.search(value):
            continue
        if SHORTCUT.match(value):
            continue
        out.append(CodeRef("tsx-text", value, path, _line_of(src, match.start())))
    for match in _TSX_CHILD_LITERAL.finditer(src):
        raw = match.group(1)
        value = _template(raw, 0)[0] if raw[0] == "`" else _string(raw, 0)[0]
        value = " ".join(value.split())
        if value and _shown(value):
            out.append(CodeRef("tsx-text", value, path, _line_of(src, match.start())))
    return out


# ── 9. 页面与邮件模板 ────────────────────────────────────────────────────────

_HTML_DROP = re.compile(r"<!--.*?-->|\{#.*?#\}|<script\b.*?</script>|<style\b.*?</style>", re.S | re.I)
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_JINJA_MSGID = re.compile(r"(?<![\w.])(?:_|t)\(\s*(?:\"([^\"]+)\"|'([^']+)')")
_HTML_TEXT = re.compile(r">([^<>]+)<")
_HTML_ATTR = re.compile(r"(?<![\w-])(title|placeholder|alt|aria-label)=\"([^\"{}]*)\"")
_CJK_RUN = re.compile(r"[一-鿿ก-๛　-〿！-｠][^<>\n{}]*")


_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
# 页内脚本里会显示出来的英文：赋给这些属性，或交给这些调用
_SCRIPT_SINKS = {"textContent", "innerHTML", "innerText", "outerHTML"}
_SCRIPT_CALLS = {"msgprint", "alert", "confirm", "show_alert", "throw", "prompt"}
# 页内脚本自己的取词函数（参数是 msgid / 键，不是写死的文案）
_SCRIPT_TRANSLATORS = {"t", "tr", "__", "_", "text", "term"}


def script_refs(path: str, text: str) -> list:
    """页内 <script>（不带 src）的字符串字面量：中文 / 泰文一律算写死；英文句子（≥ 2 个词）写进 textContent / innerHTML /
    innerText 或交给 msgprint / alert / confirm 时算写死。脚本里的 Jinja（{{ _("…") | tojson }}）先换成占位再分词。"""
    out = []
    for block in _SCRIPT.finditer(text):
        if re.search(r"\bsrc\s*=", block.group(1), re.I):
            continue
        body = _JINJA.sub(lambda m: "0" + "\n" * m.group(0).count("\n"), block.group(2))
        offset = _line_of(text, block.start(2)) - 1
        toks = js_tokens(body)
        sink = set()
        for k, tok in enumerate(toks):
            prev = toks[k - 1] if k else None
            if tok.kind == "id" and tok.value in _SCRIPT_SINKS and _is(prev, "."):
                nxt = toks[k + 1] if k + 1 < len(toks) else None
                if _is(nxt, "=") or (nxt is not None and nxt.kind == "p" and nxt.value == "+"):
                    j = k + 2
                    while j < len(toks) and not _is(toks[j], ";") and toks[j].line == tok.line:
                        sink.add(j)
                        j += 1
            elif tok.kind == "id" and tok.value in _SCRIPT_CALLS and k + 2 < len(toks) and _is(toks[k + 1], "("):
                sink.add(k + 2)
        for k, tok in enumerate(toks):
            if tok.kind not in ("str", "tpl"):
                continue
            value = " ".join(tok.value.split())
            if not value or KEY_LIKE.match(value):
                continue  # 词条键（text("dms.edit.loading")、t('stock.mobile.noTask')）
            called = toks[k - 2] if k > 1 and _is(toks[k - 1], "(") else None
            if called is not None and called.kind == "id" and called.value in _SCRIPT_TRANSLATORS:
                continue
            if CJK_THAI.search(value):
                out.append(CodeRef("html-cjk", value[:80], path, offset + tok.line))
            elif k in sink and _WORDS2.search(_NOT_PROSE.sub(" ", _HTML_TAG.sub(" ", value.replace("{x}", " ")))):
                out.append(CodeRef("html-hard", value[:80], path, offset + tok.line))
    return out


def html_refs(path: str, text: str) -> list:
    src = _HTML_DROP.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    out = script_refs(path, text)
    for block in _JINJA.finditer(src):
        for match in _JINJA_MSGID.finditer(block.group(0)):
            out.append(CodeRef("html-msgid", match.group(1) or match.group(2), path, _line_of(src, block.start())))
    plain = _JINJA.sub(lambda m: " " * len(m.group(0)), src)
    for match in _CJK_RUN.finditer(plain):
        value = match.group(0).strip()
        if CJK_THAI.search(value):
            out.append(CodeRef("html-cjk", value[:80], path, _line_of(plain, match.start())))
    # 写死的英文：标签外 ≥ 2 个英文词的文本，与 title / placeholder / alt / aria-label 属性
    for match in _HTML_TEXT.finditer(plain):
        value = " ".join(match.group(1).split())
        if value and _WORDS2.search(_NOT_PROSE.sub(" ", value)) and not CJK_THAI.search(value):
            out.append(CodeRef("html-hard", value[:80], path, _line_of(plain, match.start())))
    for match in _HTML_ATTR.finditer(plain):
        value = " ".join(match.group(2).split())
        if value and re.search(r"[A-Za-z]{2,}", _NOT_PROSE.sub(" ", value)) and not CJK_THAI.search(value):
            out.append(CodeRef("html-hard", "%s=%s" % (match.group(1), value[:70]), path, _line_of(plain, match.start())))
    return out


def code_refs(path: str, text: str) -> list:
    if text is None:
        return []
    if PYTHON.match(path):
        return [] if excluded(path) or path == COMPANION_I18N else python_refs(path, text)
    if DESK_JS.match(path):
        return desk_js_refs(path, text)
    if FRONTEND.match(path):
        return [] if path == ASSISTANT_I18N or path.endswith((".test.ts", ".test.tsx")) else frontend_refs(path, text)
    if HTML.match(path):
        return html_refs(path, text)
    if COMPANION_CODE.match(path):
        return [] if excluded(path) else companion_refs(path, text)
    if DOCTYPE_JSON.match(path):
        return doctype_refs(path, text)
    return []


def is_code(path: str) -> bool:
    return bool(PYTHON.match(path) or DESK_JS.match(path) or FRONTEND.match(path) or HTML.match(path)
                or COMPANION_CODE.match(path) or DOCTYPE_JSON.match(path))


# ── 汇总：一棵树里的全部字典 ────────────────────────────────────────────────


@dataclass
class Snapshot:
    entries: dict  # id -> Entry
    msgids: set  # 术语表 en / key 与翻译包 msgid 的并集（判"在不在字典里"）
    glossary_msgids: dict  # app -> 术语表能生成的 msgid（判翻译包手工行）
    translations: dict  # 路径 -> msgid 集合
    assistant_keys: set  # i18n.ts 的 MESSAGES 键（TSX 里 t("…") 用）
    roles: dict  # en 文案 -> 角色（DocType JSON）
    glossary_lines: dict  # 字典文件路径 -> 行列表（配对 msgid 改写、换键改字用；术语表以外的字典文件也在里面）
    glossary_line_ids: dict  # 字典文件路径 -> {行号: 词条 id}
    companion_keys: set = field(default_factory=set)  # CC 桌面版 i18n.py 的 _M 键
    admin_values: set = field(default_factory=set)  # 只出现在 System Manager 专用 DocType 里的 label / description / 选项
    email_msgids: set = field(default_factory=set)  # 邮件模板用到的 msgid
    term_words: set = field(default_factory=set)  # CC 网页 tr() 词条里直接列出的 msgid（TERMS_FILE 的 TERM_WORDS）


def term_words(text: str) -> set:
    """TERMS_FILE 里 TERM_WORDS = (…) 的字符串。"""
    try:
        tree = ast.parse(text or "")
    except SyntaxError:
        return set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TERM_WORDS" for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return set()
            return {item for item in value if isinstance(item, str)}
    return set()


def snapshot(tree) -> Snapshot:
    paths = tree.paths()
    dict_paths = [
        p for p in paths
        if GLOSSARY.match(p) or TRANSLATION.match(p) or KEYCLOAK.match(p) or DOCTYPE_JSON.match(p)
        or p in (ASSISTANT_I18N, COMPANION_I18N, TERMS_FILE) or p in TABLE_MODULES
    ]
    tree.prefetch(dict_paths)
    entries: dict = {}
    msgids: set = set()
    glossary_msgids: dict = {}
    translations: dict = {}
    roles: dict = {}
    admin_seen: dict = {}  # 文字 -> 是否每次都出现在管理员专用 DocType 里
    keycloak_files: dict = {}
    glossary_lines: dict = {}
    glossary_line_ids: dict = {}
    assistant_keys: set = set()
    companion_keys: set = set()

    def add(items):
        for item in items:
            entries.setdefault(item.id, item)

    for path in dict_paths:
        text = tree.read(path)
        if text is None:
            continue
        if GLOSSARY.match(path):
            items = glossary_entries(path, text)
            add(items)
            app = GLOSSARY.match(path).group(1)
            own = glossary_msgids.setdefault(app, set())
            for item in items:
                own.add(item.key)
                own.add(item.texts.get("en", ""))
            msgids |= own
            glossary_lines[path] = text.split("\n")
            glossary_line_ids[path] = {item.extra["line"]: item.id for item in items}
        elif TRANSLATION.match(path):
            found = translation_msgids(text)
            translations[path] = found
            msgids |= found
        elif KEYCLOAK.match(path):
            keycloak_files[path] = text
        elif DOCTYPE_JSON.match(path):
            found, admin, hidden_only = doctype_info(text, with_hidden=True)
            for value, role in found.items():
                roles.setdefault(value, role)
                # 管理员专用 DocType，或只在 hidden 字段上（Desk 表单不显示）：按管理后台判
                admin_seen[value] = admin_seen.get(value, True) and (admin or value in hidden_only)
        elif path == ASSISTANT_I18N:
            items = assistant_entries(path, text)
            add(items)
            assistant_keys = {item.key.split(".", 1)[1] for item in items if item.key.startswith("MESSAGES.")}
        elif path == COMPANION_I18N:
            items = companion_entries(path, text)
            add(items)
            companion_keys = {item.key for item in items}
        elif path in TABLE_MODULES:
            add(table_entries(path, text))
    add(keycloak_entries(keycloak_files))
    # 其他字典文件（i18n.ts、CC 桌面版、Keycloak、四语表）也记下每行对应的词条：换键同时改字按位置配对
    for entry in entries.values():
        path, _, line = entry.loc.rpartition(":")
        if not path or path in glossary_line_ids or not line.isdigit():
            continue
        if path not in glossary_lines:
            text = tree.read(path)
            if text is None:
                continue
            glossary_lines[path] = text.split("\n")
        glossary_line_ids.setdefault("~" + path, {})[int(line)] = entry.id
    for key in [k for k in glossary_line_ids if k.startswith("~")]:
        glossary_line_ids[key[1:]] = glossary_line_ids.pop(key)
    admin_values = {value for value, admin in admin_seen.items() if admin}
    for entry in entries.values():
        if entry.source.startswith("pearnly_") and entry.texts.get("en") in admin_values:
            entry.extra["doctype_admin"] = True
    # 邮件模板里 t("…") / _("…") 用到的词条：只被邮件模板引用时按「邮件」角色判（见 callsite_roles 的 elsewhere）
    email_paths = [p for p in paths if HTML.match(p) and "/templates/emails/" in p]
    tree.prefetch(email_paths)
    in_email = {ref.text for p in email_paths for ref in html_refs(p, tree.read(p) or "") if ref.kind == "html-msgid"}
    for entry in entries.values():
        if entry.source.startswith("pearnly_") and (entry.texts.get("en") in in_email or entry.key in in_email):
            entry.extra["in_email"] = True
    msgids.discard("")
    return Snapshot(entries, msgids, glossary_msgids, translations, assistant_keys, roles,
                    glossary_lines, glossary_line_ids, companion_keys, admin_values, in_email,
                    term_words(tree.read(TERMS_FILE)) if TERMS_FILE in paths else set())


# ── 调用点角色 ───────────────────────────────────────────────────────────────

_Q = r"(?:\"((?:[^\"\\\n]|\\.)*)\"|'((?:[^'\\\n]|\\.)*)')"
_TR = r"(?:_|__|_lt|t|tr|\$t)\(\s*" + _Q
CALLSITE_PATTERNS = (
    (re.compile(r"frappe\.(?:throw|msgprint)\(\s*(?:msg\s*=\s*)?" + _TR), "error"),
    (re.compile(r"(?<![\w.])msgprint\(\s*" + _TR), "error"),
    (re.compile(r"frappe\.confirm\(\s*" + _TR), "confirm"),
    (re.compile(r"show_alert\(\s*(?:\{\s*message\s*:\s*)?" + _TR), "hint"),
    (re.compile(r"\bsubject\s*[=:]\s*(?:[\w.$]+\(\s*)?" + _Q), "email"),
    (re.compile(r"(?<![\w-])(?::placeholder|placeholder)\s*[=:]\s*[\"{]?\s*" + _TR), "placeholder"),
    (re.compile(r"[\"']?\bdescription[\"']?\s*:\s*" + _TR), "field_desc"),
    (re.compile(r"(?<![\w-])aria-[\w-]+\s*=\s*[\"{]?\s*" + _TR), "sr"),
)
_ANY_TR = re.compile(r"(?<![\w.$])" + _TR)
_SR_PROPS = re.compile(r"(?:screenReaderInstructions|announcements|aria-[\w-]+)\s*[:=]\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")
# 出错优先：同一条词条既在 throw 里又在别处，按最宽的角色判（防止误拦）
_ROLE_RANK = {"email": 0, "confirm": 6, "error": 5, "field_desc": 4, "hint": 3, "placeholder": 2, "sr": 1}


def _unquote(match) -> str:
    raw = match.group(1) if match.group(1) is not None else match.group(2)
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), raw)


def admin_code(path: str) -> bool:
    return any(path == prefix or (prefix.endswith("/") and path.startswith(prefix)) for prefix in ADMIN_CODE)


def callsite_roles(tree) -> dict:
    """{msgid 或键: 角色}；另有 "prop:sr"（交给读屏的对象属性名）、"elsewhere"（邮件模板以外也用到的 msgid）、
    "admin_only"（只在控制面代码 ADMIN_CODE 里调用的 msgid）。"""
    paths = [p for p in tree.paths() if CALLSITE_FILES.match(p) and not excluded(p)]
    tree.prefetch(paths)
    roles: dict = {}
    sr_props: set = set()
    elsewhere: set = set()
    admin_calls: set = set()
    other_calls: set = set()
    for path in paths:
        text = tree.read(path)
        if not text:
            continue
        for pattern, role in CALLSITE_PATTERNS:
            for match in pattern.finditer(text):
                value = _unquote(match)
                current = roles.get(value)
                if current is None or _ROLE_RANK.get(role, 0) > _ROLE_RANK.get(current, 0):
                    roles[value] = role
        for match in _ANY_TR.finditer(text):
            value = _unquote(match)
            elsewhere.add(value)
            (admin_calls if admin_code(path) else other_calls).add(value)
        for match in _SR_PROPS.finditer(text):
            sr_props |= set(re.findall(r"\.(\w+)", match.group(1)))
    roles["prop:sr"] = sr_props
    roles["elsewhere"] = elsewhere
    roles["admin_only"] = admin_calls - other_calls
    return roles
