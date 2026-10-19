"""CC 定时任务页的词条四语齐全（10-19 用户在 Windows 测试机截图：删除确认框标题是英文「Delete 1 item permanently?」）。

CC 网页的定时任务页（frontend/assistant/src/components/scheduled-tasks.tsx）不另存翻译：`tr(key)` 取的是
`pearnly_cowork.folder_intake.api.terms()` 按当前语言给出的 `frappe._(key)`，缺译时原样回英文 msgid，构建和类型检查都不拦。
这里离线守住两头：

  1. 接口列给前端的每个词条（TERM_WORDS + 按 TERM_PREFIXES 从术语表取的键）在 Cowork 站点装的 App 翻译包
     （运行时读的就是它）里英 / 中 / 泰 / 日四语都有、不为空，占位符与英文一致；
  2. 页面里 `tr()` 用到的每个词条（字面量、条件里的两个分支、按状态 / 菜单 / 默认值拼出来的键）都在接口列出的范围里。
"""
import ast
import csv
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
API = ROOT / "apps/pearnly_cowork/pearnly_cowork/folder_intake/api.py"
PAGE = ROOT / "frontend/assistant/src/components/scheduled-tasks.tsx"
# Cowork 站点一定装的两个 App；terms() 按已装 App 的术语表取键，frappe._ 读它们的翻译包
APPS = ("pearnly_core", "pearnly_cowork")
LANGS = ("en", "zh", "th", "ja")


def _constants():
    tree = ast.parse(API.read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("TERM_WORDS", "TERM_PREFIXES"):
                values[node.targets[0].id] = ast.literal_eval(node.value)
    return values["TERM_WORDS"], values["TERM_PREFIXES"]


def _term_keys():
    """同 api._term_keys()：固定词 + 各 App 术语表里带这些前缀的键（无键列的行以英文为键）。"""
    words, prefixes = _constants()
    keys = set(words)
    for app in APPS:
        with (ROOT / "apps" / app / app / "i18n/messages.tsv").open(encoding="utf-8") as handle:
            rows = [line.rstrip("\n").split("\t") for line in handle][1:]
        for row in rows:
            key = row[4] if len(row) > 4 and row[4] else row[0]
            if key.startswith(prefixes):
                keys.add(key)
    return keys


def _translations():
    out = {}
    for lang in LANGS:
        table = {}
        for app in APPS:
            with (ROOT / "apps" / app / app / "translations" / (lang + ".csv")).open(encoding="utf-8", newline="") as handle:
                table.update({row[0]: row[1] for row in csv.reader(handle) if len(row) >= 2})
        out[lang] = table
    return out


def _page_keys():
    source = PAGE.read_text(encoding="utf-8")
    keys = set(re.findall(r'\btr\(\s*"([^"]+)"', source))
    # 条件里的两个分支、先放进变量再交给 tr 的（folderHint 一类）：页面里所有这几个命名空间的字面量
    keys |= set(re.findall(r'"((?:cowork\.folder|intake|connect\.state)\.[\w.]+)"', source))
    # 拼出来的键：tr(`cowork.folder.state.${n}`) / tr(`intake.mrerpMenu.${kind}`) / tr(`intake.mrerpDefault.${field}`)
    states = re.search(r"const STATES = \[([^\]]*)\]", source).group(1)
    keys |= {"cowork.folder.state." + name for name in re.findall(r'"(\w+)"', states)}
    kinds = re.search(r"const MENU_KINDS[^=]*= (\{[^;]*\});", source).group(1)
    keys |= {"intake.mrerpMenu." + name for name in re.findall(r'"(\w+)"', kinds)}
    fields = re.search(r"const DEFAULT_FIELDS[^=]*= (\{.*?\n\});", source, re.S).group(1)
    keys |= {"intake.mrerpDefault." + name for name in re.findall(r'"(\w+)"', fields)}
    keys |= {"cowork.folder.trigger." + name for name in ("schedule", "manual", "catch_up")}
    return keys


class ScheduledTaskTermsTest(unittest.TestCase):
    def test_every_listed_term_in_four_languages(self):
        keys = _term_keys()
        tables = _translations()
        self.assertGreater(len(keys), 150)
        missing = sorted((key, lang) for key in keys for lang in LANGS if not tables[lang].get(key, "").strip())
        self.assertEqual(missing, [])
        for key in keys:
            placeholders = sorted(set(re.findall(r"\{\d\}", tables["en"][key])))
            for lang in LANGS[1:]:
                self.assertEqual(sorted(set(re.findall(r"\{\d\}", tables[lang][key]))), placeholders, (key, lang))

    def test_page_uses_only_listed_terms(self):
        self.assertEqual(sorted(_page_keys() - _term_keys()), [])

    def test_delete_confirmation_follows_the_confirm_form(self):
        tables = _translations()
        for msgid in ("Delete {0} item permanently?", "Delete {0} items permanently?"):
            self.assertEqual(tables["zh"][msgid], "确定要永久删除 {0} 项吗？")
            self.assertEqual(tables["th"][msgid], "ต้องการลบ {0} รายการถาวรใช่หรือไม่")
            self.assertEqual(tables["ja"][msgid], "{0} 件を完全に削除しますか？")


if __name__ == "__main__":
    unittest.main()
