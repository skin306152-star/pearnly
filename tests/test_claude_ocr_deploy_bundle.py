"""甲骨文 claude-ocr 服务的构建包（``scripts/claude-ocr/deploy.sh``）要带上 ``pearnly_core.claude_ocr`` 用到的全部共用层包。

2026-10-27：票面自洽表（``claude_ocr.consistency``）引用了 ``pearnly_core.tax_documents``，部署脚本没带，甲骨文服务一部署就会导入失败；
生产识别平时走甲骨文那份引擎，所以只换生产镜像、不重新部署甲骨文，新自检也不生效。这里按源码里的 import 核对，不靠人记。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_OCR = ROOT / "apps/pearnly_core/pearnly_core/claude_ocr"
DEPLOY = (ROOT / "scripts/claude-ocr/deploy.sh").read_text()
IMPORT = re.compile(r"^\s*(?:from\s+pearnly_core(?:\.(\w+)[\w.]*)?\s+import\s+([\w ,]+)|import\s+pearnly_core\.(\w+))", re.M)
# 服务里不需要、也不该带的：client.py 在生产站点上调服务，用到的 Frappe 侧模块不进服务（服务入口是 server.py / engine.py）
SITE_ONLY = {"client.py"}


def needed():
    packages = set()
    for path in CLAUDE_OCR.glob("*.py"):
        if path.name in SITE_ONLY:
            continue
        for sub, names, plain in IMPORT.findall(path.read_text()):
            if plain:
                packages.add(plain)
            elif sub:
                packages.add(sub)
            else:
                packages.update(name.strip() for name in names.split(","))
    return packages - {"claude_ocr"}


class Bundle(unittest.TestCase):
    def test_every_shared_package_claude_ocr_imports_is_shipped(self):
        for package in sorted(needed()):
            with self.subTest(package):
                shipped = f"pearnly_core/pearnly_core/{package}\"" in DEPLOY or f"pearnly_core/pearnly_core/{package}/" in DEPLOY \
                    or f"pearnly_core/pearnly_core/{package} " in DEPLOY
                self.assertTrue(shipped, f"scripts/claude-ocr/deploy.sh 没带 pearnly_core.{package}")

    def test_the_consistency_table_dependency_is_found(self):
        self.assertIn("tax_documents", needed())


if __name__ == "__main__":
    unittest.main()
