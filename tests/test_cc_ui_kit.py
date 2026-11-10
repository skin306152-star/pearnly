"""CC 标准组件库（frontend/assistant/src/components/ui，docs/development/cc-ui-kit.md）：

· 登记表（components/NOTICE.md）与文件一一对应，shadcn 来源只认锁定的提交与 bases/radix；
· 运行依赖锁精确版本；
· 组件里没有写死的英文界面字（走 i18n.ts）；
· 展示页 kit.html 只在开发环境：默认构建只打 index.html，CC_KIT=1 才带上；
· 组件库不反向依赖业务代码（只引 lib/utils、i18n、hooks 与库内的件）。
"""
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "frontend/assistant"
UI = APP / "src/components/ui"
PIN = "8ab27715521cbd4f87ae6886cd989cbb678f6de7"


class ComponentKit(unittest.TestCase):
    def test_registry_matches_files_and_pins_one_upstream(self):
        notice = (APP / "src/components/NOTICE.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| `([\w-]+\.tsx)` \| ([^|]+) \|", notice, re.M)
        self.assertEqual(sorted(name for name, _ in rows), sorted(p.name for p in UI.glob("*.tsx")))
        self.assertIn(PIN, notice)
        for name, source in rows:
            if "bases/radix/ui/" in source:
                self.assertIn(f"bases/radix/ui/{name}", source, name)

    def test_runtime_packages_are_pinned(self):
        package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
        for dep in ("@shadcn/react", "class-variance-authority", "radix-ui", "lucide-react", "react", "react-dom"):
            self.assertRegex(package["dependencies"][dep], r"^\d+\.\d+\.\d+$", dep)
        self.assertEqual(package["dependencies"]["@shadcn/react"], "0.3.1")
        lock = json.loads((APP / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["packages"]["node_modules/@shadcn/react"]["version"], "0.3.1")

    def test_no_hard_coded_english_in_components(self):
        # 组件自带的字（上一项 / 下一步 / 跳过 / 提交、复制、关闭、加载中、请选择一项、第 n 项共 m 项）一律走 i18n.ts
        english = re.compile(r"""(?:aria-label|title|placeholder)=["'][A-Za-z][^"']*["']|>\s*[A-Z][a-z]+(?: [a-z]+)*\s*</|\?\?\s*["'][A-Z][a-z]""")
        for path in sorted(UI.glob("*.tsx")):
            text = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)
            self.assertIsNone(english.search(text), f"{path.name}: {english.search(text)}")
        questionnaire = (UI / "questionnaire.tsx").read_text(encoding="utf-8")
        for word in ("ask.prev", "ask.skip", "ask.next", "ask.submit", "ask.nth(", "ui.chooseAnswer"):
            self.assertIn(word, questionnaire)
        i18n = (APP / "src/i18n.ts").read_text(encoding="utf-8")
        block = i18n.split("const UI = {", 1)[1].split("};", 1)[0]
        for lang in ("en", "zh", "th", "ja"):
            self.assertRegex(block, rf"\n  {lang}: {{ copied: .+, loading: .+, chooseAnswer: .+ }},")

    def test_gallery_is_dev_only(self):
        config = (APP / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn('process.env.CC_KIT === "1"', config)
        self.assertRegex(config, r'kit: fileURLToPath\(new URL\("\./kit\.html"')
        index = (APP / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("kit", index)
        self.assertNotIn("src/kit", "".join(p.read_text(encoding="utf-8") for p in (APP / "src").glob("*.tsx")))
        for path in (APP / "src").rglob("*.ts*"):
            if "/kit/" not in path.as_posix():
                self.assertNotIn("@/kit/", path.read_text(encoding="utf-8"), path)

    def test_library_does_not_depend_on_business_code(self):
        # 组件库在最底层：不引业务组件、对话状态层、页面入口（业务引组件库，反过来不行）
        business = re.compile(r'^@/(?:components/(?!ui/)|lib/managed-agents|assistant|kit/)')
        for path in sorted(UI.glob("*.tsx")):
            for module in re.findall(r'from "([^"]+)"', path.read_text(encoding="utf-8")):
                self.assertNotRegex(module, business, f"{path.name} 引了 {module}")

if __name__ == "__main__":
    unittest.main()
