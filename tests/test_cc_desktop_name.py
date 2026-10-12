"""旧称守卫：Windows 端对用户只叫 CC 桌面版，CC 自称 CC（用户 2026-10-13 定）。

正式名字：CC Desktop / CC 桌面版 / CC เดสก์ท็อป / CC デスクトップ。旧称「小助手 / Companion / companion / helper /
ヘルパー / コンパニオン / ผู้ช่วย」（指 Windows 程序时）和把 CC 叫作「助手 / ผู้ช่วย / アシスタント / assistant」都不许
出现在用户看得到的地方。改名之后翻译包里的残留行（生成器只合并不删）和工具说明里的 companion 让旧称一直露出来，
这里把它们都拦在 CI 里。扫的都是用户、或替用户说话的 CC（模型）读得到的文字：

  1. 各 App 术语表的四列文案（key 列是 msgid，不显示，不扫）与翻译包的译文列；
  2. 代码里交给翻译层的英文 msgid 与 DocType 名若带旧称，必须有不带旧称的英文译文（en.csv）；
  3. CC 读的：各产品智能体工具里给模型的字符串（说明、步骤名、问句、结果里的说明与键；AST，去掉文档字符串），
     系统提示词让 CC 自称 CC；
  4. CC 网页文案（frontend/assistant/src/i18n.ts；其余 ts / tsx 只查中、泰、日文旧称）；
  5. CC 桌面版程序文案（companion/src/companion/i18n.py）、回报成推送原因的原文、安装包里显示的名字；
  6. 官网源文件（website/ 的生成源与页面，第三方库除外）；
  7. DocType JSON 的 label / description。

内部标识不算：英文只拦独立成词的 companion / helper / assistant，与 `_ . / \\ - :` 相连的（cowork_companion_status、
pearnly_cowork.companion.api、/downloads/companion/、assistant:）不拦。指 DMS / ERP 的 LINE 机器人的「DMS 助手 /
ผู้ช่วย LINE / LINE assistant」是别的产品，保留（PHRASES）；其余明确例外列在 ALLOWED，写清理由。
"""
import ast
import csv
import importlib.util
import json
import os
import pathlib
import re
import unittest
import warnings

unittest.TestCase.maxDiff = None
ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = sorted(p.parent.parent for p in ROOT.glob("apps/*/*/i18n/messages.tsv"))

OLD = r"小助手|助手|ヘルパー|コンパニオン|アシスタント|ผู้ช่วย"
WORDS = r"(?<![\w./\\:-])(?:companions?|helpers?{extra})(?![\w./\\:-])"
# 用户看的文案：连把 CC 叫作 assistant 也拦
SHOWN = re.compile(OLD + "|(?i:" + WORDS.format(extra="|assistants?") + ")")
# 代码里的字符串（工具说明、程序文案、网页源）：assistant 也是 CC 自己的内部前缀（`assistant:`），这里只拦 Windows 端旧称
NAMED = re.compile(OLD + "|(?i:" + WORDS.format(extra="") + ")")
# 指 DMS / ERP 的 LINE 机器人，不是 Windows 程序（用户 2026-10-13：保留）
PHRASES = ("ผู้ช่วย DMS", "ผู้ช่วย LINE", "DMS 助手", "LINE 助手", "DMSアシスタント", "DMS アシスタント",
           "LINE アシスタント", "LINE assistant", "DMS assistant")
# (App, msgid) → 理由：这几条里的 companion 是取值码（Select 的选项值 / 接口参数），不是程序名
ALLOWED = {
    ("pearnly_core", "The push channel must be companion or cloud"): "推送渠道取值码 companion / cloud（接口参数）",
    ("pearnly_core", "companion = pulled outbound by CC Desktop on the local machine, then stored here"):
        "录入记录 push_channel 的取值码说明",
}


def offending(text, pattern=SHOWN):
    """去掉保留的 LINE 机器人说法后，第一处旧称；没有就是 None。"""
    for phrase in PHRASES:
        text = text.replace(phrase, "")
    found = pattern.search(text)
    return found.group(0) if found else None


def _docstrings(tree) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def python_strings(path):
    """文件里的字符串常量（行号, 文字），不含文档字符串与注释。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def en_table() -> dict:
    table = {}
    for app in APPS:
        path = app / "translations" / "en.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            table.update({(app.name, row[0]): row[1] for row in csv.reader(handle) if len(row) >= 2 and row[0]})
    return table


class Translations(unittest.TestCase):
    def test_glossaries_show_only_the_new_names(self):
        bad = []
        for app in APPS:
            path = app / "i18n" / "messages.tsv"
            with path.open(encoding="utf-8", newline="") as handle:
                for line, row in enumerate(csv.DictReader(handle, delimiter="\t"), start=2):
                    msgid = row.get("key") or row["en"]
                    if (app.name, msgid) in ALLOWED or (app.name, row["en"]) in ALLOWED:
                        continue
                    for lang in ("en", "zh", "th", "ja"):
                        hit = offending(row.get(lang) or "")
                        if hit:
                            bad.append(f"{path.relative_to(ROOT)}:{line} {lang} «{hit}»")
        self.assertEqual(bad, [], "术语表文案里有旧称；Windows 端写 CC Desktop / CC 桌面版 / CC เดสก์ท็อป / CC デスクトップ")

    def test_translation_packs_have_no_leftover_old_rows(self):
        # 生成器只合并、不删行：msgid 改名后旧行会一直留在包里被打进发布（10-13 用户看到「查看了小助手」）
        bad = []
        for app in APPS:
            for path in sorted((app / "translations").glob("*.csv")):
                with path.open(encoding="utf-8", newline="") as handle:
                    for row in csv.reader(handle):
                        if len(row) < 2 or (app.name, row[0]) in ALLOWED:
                            continue
                        hit = offending(row[1])
                        if hit:
                            bad.append(f"{path.relative_to(ROOT)} {row[0][:60]!r} «{hit}»")
        self.assertEqual(bad, [], "翻译包里有带旧称的行：术语表已改的，按 msgid 从四个 CSV 删掉残留行再生成")

    def test_old_named_msgids_and_doctypes_are_shown_with_the_new_name(self):
        # msgid / DocType 名是标识，可以带 companion，但界面显示的英文译文不能带。
        # 代码里的 msgid 连 assistant 也查（界面上 CC 叫 CC）；DocType 名只查 Windows 端旧称：
        # 「Pearnly Assistant Session」这类是智能体自己的私有表，只有系统管理员在 Desk 看得到。
        spec = importlib.util.spec_from_file_location("check_i18n", ROOT / "scripts/check-i18n.py")
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            msgids = {msgid: (sorted(files)[0], SHOWN) for msgid, files in check.english_msgids().items()}
        for path in ROOT.glob("apps/*/*/*/doctype/*/*.json"):
            name = json.loads(path.read_text(encoding="utf-8")).get("name")
            if isinstance(name, str):
                msgids.setdefault(name, (str(path.relative_to(ROOT)), NAMED))
        en = en_table()
        shown = {}
        for (app, msgid), text in en.items():
            shown.setdefault(msgid, []).append((app, text))
        bad = []
        for msgid, (where, pattern) in sorted(msgids.items()):
            if not offending(msgid, pattern):
                continue
            texts = [(app, text) for app, text in shown.get(msgid, []) if (app, msgid) not in ALLOWED]
            if any((app, msgid) in ALLOWED for app, _ in shown.get(msgid, [])):
                continue
            if not texts or any(offending(text) for _, text in texts):
                bad.append(f"{where}: {msgid!r} → {[text for _, text in texts] or '没有英文译文'}")
        self.assertEqual(bad, [], "带旧称的 msgid 要有不带旧称的英文译文（术语表 en 列写新名，key 列留原 msgid）")


class WhatCcReads(unittest.TestCase):
    def tool_modules(self):
        """各产品在 hooks 里登记的工具表所在文件，加共用层的工具表。"""
        paths = [ROOT / "apps/pearnly_core/pearnly_core/assistant/tools.py"]
        for hooks in ROOT.glob("apps/*/*/hooks.py"):
            tree = ast.parse(hooks.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pearnly_assistant_tools"
                                                        for t in node.targets):
                    for item in ast.literal_eval(node.value):
                        module = item.rsplit(".", 1)[0]
                        paths.append(ROOT / "apps" / module.split(".")[0] / (module.replace(".", "/") + ".py"))
        return paths

    def test_tool_texts_use_the_new_name(self):
        modules = self.tool_modules()
        self.assertIn(ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/tools.py", modules)
        bad = [f"{path.relative_to(ROOT)}:{line} «{hit}» {text[:80]!r}"
               for path in modules for line, text in python_strings(path)
               for hit in [offending(text, NAMED)] if hit]
        self.assertEqual(bad, [], "CC 读到旧称就会照着转述（10-12 对话里说「小助手」）；写 CC Desktop")

    def test_cc_calls_itself_cc(self):
        source = (ROOT / "apps/pearnly_core/pearnly_core/assistant/definition.py").read_text(encoding="utf-8")
        system = ast.literal_eval(next(node.value for node in ast.parse(source).body if isinstance(node, ast.Assign)
                                       and getattr(node.targets[0], "id", "") == "SYSTEM"))
        self.assertTrue(system.startswith("You are CC, "))
        self.assertNotRegex(system, r"(?i)pearnly assistant|you are the assistant")


class Frontends(unittest.TestCase):
    LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`')

    def literals(self, path):
        src = path.read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        src = re.sub(r"(?m)^\s*//.*$", "", src)
        return [match.group(0)[1:-1] for match in self.LITERAL.finditer(src)]

    def test_cc_page_copy(self):
        i18n = ROOT / "frontend/assistant/src/i18n.ts"
        bad = [f"i18n.ts «{hit}» {text[:60]!r}" for text in self.literals(i18n) for hit in [offending(text)] if hit]
        # 其余组件里的英文 "helper" 之类是状态值（内部标识），只查中、泰、日文旧称
        sources = []
        for base, dirs, files in os.walk(ROOT / "frontend"):
            dirs[:] = [d for d in dirs if d not in ("node_modules", "dist")]
            sources += [pathlib.Path(base) / name for name in files if name.endswith((".ts", ".tsx", ".vue"))]
        for path in sorted(sources):
            if path == i18n:
                continue
            bad += [f"{path.relative_to(ROOT)} «{hit}»" for text in self.literals(path)
                    for hit in [offending(text, re.compile(OLD))] if hit]
        self.assertEqual(bad, [])

    def test_website_sources(self):
        website = ROOT / "website"
        paths = sorted(website.glob("*.py")) + sorted(website.glob("*/*.html")) + sorted(website.glob("static/*.js"))
        bad = [f"{path.relative_to(ROOT)}:{n} «{hit}»" for path in paths
               for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
               for hit in [offending(line)] if hit]
        self.assertEqual(bad, [], "官网写 CC Desktop / CC 桌面版 / CC เดสก์ท็อป；只改仓库，不发布")


class CcDesktop(unittest.TestCase):
    COMPANION = ROOT / "companion"

    def test_app_texts(self):
        path = self.COMPANION / "src/companion/i18n.py"
        bad = [f"i18n.py:{line} «{hit}» {text[:60]!r}" for line, text in python_strings(path)
               for hit in [offending(text)] if hit]
        self.assertEqual(bad, [])

    def test_texts_sent_back_as_push_reasons(self):
        # ack_failure 的原文会存进推送原因、交给 CC：只用正式名字
        bad = []
        for path in sorted((self.COMPANION / "src/companion").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "ack_failure":
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and offending(sub.value, NAMED):
                            bad.append(f"{path.name}:{sub.lineno} {sub.value[:60]!r}")
                    for name in (sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)):
                        for assigned in ast.walk(tree):
                            if isinstance(assigned, ast.Assign) and any(getattr(t, "id", "") == name
                                                                        for t in assigned.targets):
                                for const in ast.walk(assigned.value):
                                    if isinstance(const, ast.Constant) and isinstance(const.value, str) \
                                            and offending(const.value, NAMED):
                                        bad.append(f"{path.name}:{const.lineno} {const.value[:60]!r}")
        self.assertEqual(bad, [])

    def test_installer_shows_only_cc(self):
        script = (self.COMPANION / "packaging/installer.iss").read_text(encoding="utf-8")
        setup = script.split("[Setup]", 1)[1].split("\n[", 1)[0]
        values = {line.split("=", 1)[0].strip().lower(): line.split("=", 1)[1].strip()
                  for line in setup.splitlines() if "=" in line and not line.strip().startswith(";")}
        self.assertEqual(values.get("disabledirpage", "").lower(), "yes")  # 选目录页会显示「…\Pearnly Companion」
        shown = [values[key] for key in ("appname", "uninstalldisplayname", "versioninfoproductname",
                                         "versioninfodescription", "defaultgroupname") if key in values]
        for section in ("[Icons]", "[Tasks]", "[Run]"):
            body = script.split(section, 1)[1].split("\n[", 1)[0]
            shown += re.findall(r'(?<![A-Za-z])(?:Name|Description|GroupDescription): "([^"]*)"', body)
        self.assertTrue(shown)
        self.assertEqual([text for text in shown if offending(text)], [])
        self.assertIn('Description: "{cm:LaunchProgram,CC}"', script)  # 完成页不是「Run companion.exe」


class DesktopMetadata(unittest.TestCase):
    def test_doctype_labels_and_descriptions(self):
        bad = []
        for path in sorted(ROOT.glob("apps/*/*/*/doctype/*/*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict) or doc.get("doctype") != "DocType":
                continue
            items = [("", doc)] + [(f.get("fieldname", ""), f) for f in doc.get("fields") or [] if isinstance(f, dict)]
            for owner, item in items:
                for key in ("label", "description"):
                    text = str(item.get(key) or "")
                    if text in {msgid for _, msgid in ALLOWED}:  # 译文落在 core（每个站点都装）
                        continue
                    hit = offending(text)
                    if hit:
                        bad.append(f"{path.relative_to(ROOT)} {owner}.{key} «{hit}»")
        self.assertEqual(bad, [])


class Guard(unittest.TestCase):
    """守卫本身要拦得住：几种真出现过的写法。"""

    def test_it_catches_what_users_saw(self):
        for text in ("查看了小助手", "ดาวน์โหลดผู้ช่วย Pearnly", "ヘルパーが接続されていません", "コンパニオンを接続",
                     "Checked the companion", "The helper is out of date", "Your personal AI assistant"):
            self.assertTrue(offending(text), text)
        for text in ("CC 桌面版未连接", "เชื่อมต่อ CC เดสก์ท็อป", "pearnly_cowork.companion.api.installer",
                     "/downloads/companion/CC-Setup.exe", "cowork_companion_status", "assistant:pos:1",
                     "นี่คือผู้ช่วย DMS", "MR.ERP DMS 的 LINE 助手", "A LINE assistant for MR.ERP DMS"):
            self.assertIsNone(offending(text), text)
        self.assertTrue(offending("ผู้ช่วย Pearnly ยังไม่เชื่อมต่อ"))


if __name__ == "__main__":
    unittest.main()
