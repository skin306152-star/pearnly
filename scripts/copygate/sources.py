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

  6. Python：_()/_lt()/N_() 的第一个常量参数；frappe.throw / msgprint 的裸字符串；
     assistant/ 下工具声明里 label / ask / ask_when 的值；*STEP_NAMES 的值
  7. Desk JS（public/、doctype/、page/）：__("…") 与 t("key")
  8. CC 网页 TSX / TS：JSX 文本、title / aria-label / placeholder / alt / label 属性、tr("…")
  9. www/*.html、templates/emails/*.html：{{ _("…") }} 与写死的中文 / 泰文
 10. 翻译包 translations/*.csv 里新增的手工行（msgid 在术语表里找不到）

不扫（模型读的、写进外部系统的、日志与测试）：见 NOT_SCANNED。
DocType / Workspace / Report 的 JSON 只用来给同 msgid 的术语表行定角色（label → 标签，description → 字段说明）。
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


# ── DocType JSON：给术语表行定角色 ──────────────────────────────────────────


def doctype_roles(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    roles = {}

    def visit(node, depth=0):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and value.strip():
                    if key == "description":
                        roles.setdefault(value.strip(), "field_desc")
                    elif key == "label":
                        roles.setdefault(value.strip(), "label")
                else:
                    visit(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                visit(item, depth + 1)

    visit(data)
    return roles


# ── 6. Python ────────────────────────────────────────────────────────────────


def _call_name(node) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def python_refs(path: str, text: str) -> list:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    tool_file = "/assistant/" in path
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            first = node.args[0] if node.args else None
            literal = isinstance(first, ast.Constant) and isinstance(first.value, str)
            if name in TRANSLATE_CALLS and literal:
                out.append(CodeRef("py-msgid", first.value, path, node.lineno))
            elif (
                name in ("throw", "msgprint")
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "frappe"
                and literal
                and " " in first.value.strip()
            ):
                out.append(CodeRef("py-bare", first.value, path, node.lineno))
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
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            if any(isinstance(t, ast.Name) and t.id.endswith("STEP_NAMES") for t in node.targets):
                for sub in node.value.values:
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        out.append(CodeRef("tool-label", sub.value, path, sub.lineno))
    return out


# ── 7. Desk JS ───────────────────────────────────────────────────────────────

KEY_LIKE = re.compile(r"^[A-Za-z][\w-]*(\.[\w-]+)+$")


def desk_js_refs(path: str, text: str) -> list:
    toks = js_tokens(text)
    out = []
    for i in range(len(toks) - 2):
        tok, paren, arg = toks[i], toks[i + 1], toks[i + 2]
        if tok.kind != "id" or not _is(paren, "(") or arg.kind not in ("str", "tpl"):
            continue
        if i > 0 and _is(toks[i - 1], "."):
            continue
        if "{x}" in arg.value and arg.kind == "tpl":
            continue
        if tok.value == "__":
            out.append(CodeRef("desk-msgid", arg.value, path, arg.line))
        elif tok.value == "t" and KEY_LIKE.match(arg.value):
            out.append(CodeRef("desk-key", arg.value, path, arg.line))
    return out


# ── 8. CC 网页 TSX / TS ──────────────────────────────────────────────────────

_TSX_BLOCK_COMMENT = re.compile(r"\{\s*/\*.*?\*/\s*\}|/\*.*?\*/", re.S)
_TSX_LINE_COMMENT = re.compile(r"(?<![:\"'\w/])//[^\n]*")
_TSX_ATTR = re.compile(
    r"(?<![\w-])(title|aria-label|placeholder|alt|label)=(?:\"([^\"]*)\"|'([^']*)'|\{\s*\"([^\"]*)\"\s*\})"
)
_TSX_TEXT = re.compile(r"(?<=[^\s=\-])>([^<>{}]+)<")
_TSX_CODEISH = re.compile(
    r"[;=()&|?\[\]]|\b(const|return|let|if|else|null|undefined|true|false)\b|=>|^[\s:,]|\w:\s*$"
)
_TSX_TR = re.compile(r"\btr\(\s*(?:\"([^\"]+)\"|'([^']+)')\s*[,)]")
_TSX_T = re.compile(r"(?<![\w.])t\(\s*\"([A-Za-z][\w.]*)\"\s*\)")
LETTERS = re.compile(r"[A-Za-zก-๛一-鿿]")
SHORTCUT = re.compile(r"^(Ctrl|⌘|Shift|Alt)[ +].{1,3}$")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def frontend_refs(path: str, text: str) -> list:
    src = _TSX_BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    src = _TSX_LINE_COMMENT.sub("", src)
    out = []
    for match in _TSX_TR.finditer(src):
        out.append(CodeRef("tsx-tr", match.group(1) or match.group(2), path, _line_of(src, match.start())))
    for match in _TSX_T.finditer(src):
        out.append(CodeRef("tsx-t", match.group(1), path, _line_of(src, match.start())))
    if not path.endswith(".tsx"):
        return out
    for match in _TSX_ATTR.finditer(src):
        value = next(g for g in match.groups()[1:] if g is not None)
        if LETTERS.search(value) and not SHORTCUT.match(value.strip()):
            out.append(CodeRef("tsx-attr", "%s=%s" % (match.group(1), value), path, _line_of(src, match.start())))
    for match in _TSX_TEXT.finditer(src):
        value = " ".join(match.group(1).split())
        if not value or not LETTERS.search(value) or _TSX_CODEISH.search(value):
            continue
        if SHORTCUT.match(value):
            continue
        out.append(CodeRef("tsx-text", value, path, _line_of(src, match.start())))
    return out


# ── 9. 页面与邮件模板 ────────────────────────────────────────────────────────

_HTML_DROP = re.compile(r"<!--.*?-->|\{#.*?#\}|<script\b.*?</script>|<style\b.*?</style>", re.S | re.I)
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_JINJA_MSGID = re.compile(r"(?<![\w.])(?:_|t)\(\s*(?:\"([^\"]+)\"|'([^']+)')")
_CJK_RUN = re.compile(r"[一-鿿ก-๛　-〿！-｠][^<>\n{}]*")


def html_refs(path: str, text: str) -> list:
    src = _HTML_DROP.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    out = []
    for block in _JINJA.finditer(src):
        for match in _JINJA_MSGID.finditer(block.group(0)):
            out.append(CodeRef("html-msgid", match.group(1) or match.group(2), path, _line_of(src, block.start())))
    plain = _JINJA.sub(lambda m: " " * len(m.group(0)), src)
    for match in _CJK_RUN.finditer(plain):
        value = match.group(0).strip()
        if CJK_THAI.search(value):
            out.append(CodeRef("html-cjk", value[:80], path, _line_of(plain, match.start())))
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
    return []


def is_code(path: str) -> bool:
    return bool(PYTHON.match(path) or DESK_JS.match(path) or FRONTEND.match(path) or HTML.match(path))


# ── 汇总：一棵树里的全部字典 ────────────────────────────────────────────────


@dataclass
class Snapshot:
    entries: dict  # id -> Entry
    msgids: set  # 术语表 en / key 与翻译包 msgid 的并集（判"在不在字典里"）
    glossary_msgids: dict  # app -> 术语表能生成的 msgid（判翻译包手工行）
    translations: dict  # 路径 -> msgid 集合
    assistant_keys: set  # i18n.ts 的 MESSAGES 键（TSX 里 t("…") 用）
    roles: dict  # en 文案 -> 角色（DocType JSON）
    glossary_lines: dict  # 术语表路径 -> 行列表（配对 msgid 改写用）
    glossary_line_ids: dict  # 术语表路径 -> {行号: 词条 id}


def snapshot(tree) -> Snapshot:
    paths = tree.paths()
    dict_paths = [
        p for p in paths
        if GLOSSARY.match(p) or TRANSLATION.match(p) or KEYCLOAK.match(p) or DOCTYPE_JSON.match(p)
        or p in (ASSISTANT_I18N, COMPANION_I18N) or p in TABLE_MODULES
    ]
    tree.prefetch(dict_paths)
    entries: dict = {}
    msgids: set = set()
    glossary_msgids: dict = {}
    translations: dict = {}
    roles: dict = {}
    keycloak_files: dict = {}
    glossary_lines: dict = {}
    glossary_line_ids: dict = {}
    assistant_keys: set = set()

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
            for value, role in doctype_roles(text).items():
                roles.setdefault(value, role)
        elif path == ASSISTANT_I18N:
            items = assistant_entries(path, text)
            add(items)
            assistant_keys = {item.key.split(".", 1)[1] for item in items if item.key.startswith("MESSAGES.")}
        elif path == COMPANION_I18N:
            add(companion_entries(path, text))
        elif path in TABLE_MODULES:
            add(table_entries(path, text))
    add(keycloak_entries(keycloak_files))
    # 邮件模板里 t("…") / _("…") 用到的词条按「邮件」角色判（正文可以长、可以有致谢与问候）
    email_paths = [p for p in paths if HTML.match(p) and "/templates/emails/" in p]
    tree.prefetch(email_paths)
    in_email = {ref.text for p in email_paths for ref in html_refs(p, tree.read(p) or "") if ref.kind == "html-msgid"}
    for entry in entries.values():
        if entry.source.startswith("pearnly_") and (entry.texts.get("en") in in_email or entry.key in in_email):
            entry.hint_role = "email"
    msgids.discard("")
    return Snapshot(entries, msgids, glossary_msgids, translations, assistant_keys, roles,
                    glossary_lines, glossary_line_ids)
