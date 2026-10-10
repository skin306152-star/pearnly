#!/usr/bin/env python3
"""智能体对话页的品牌图（ADR-019）：从宣传页同一份源头生成，不另画。

- 产品形象：`website/scenes.py` 的 `badge()`（首页产品卡片上的手绘小单：Cowork 拿铅笔、DMS 举相机、
  ERP 提购物篮），连同它用的铅笔描边滤镜写成独立 SVG；眨眼与眼睛跟随鼠标在前端照搬 site.js / style.css。

用法：`python3 scripts/build-assistant-brand.py`；`--check` 只比对不写（tests/test_assistant.py 也会比对）。
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend/assistant/src/brand"
PRODUCTS = ("cowork", "erp", "dms")


def render() -> dict[str, str]:
    sys.path.insert(0, str(ROOT / "website"))
    try:
        import scenes
    finally:
        sys.path.pop(0)
    pencil = re.search(r"<filter id=\"hd-pencil\".*?</filter>", scenes.DEFS).group(0)
    files = {}
    for kind in PRODUCTS:
        svg = scenes.badge(kind, "pearnly-mascot")
        svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
        svg = svg.replace('focusable="false">', f'focusable="false"><defs>{pencil}</defs>', 1)
        files[f"{kind}.svg"] = svg + "\n"
    return files


def main() -> int:
    files = render()
    if "--check" in sys.argv:
        stale = [name for name, body in files.items()
                 if not (OUT / name).is_file() or (OUT / name).read_text(encoding="utf-8") != body]
        print("stale: " + ", ".join(stale) if stale else "brand files are current")
        return 1 if stale else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (OUT / name).write_text(body, encoding="utf-8")
    print("wrote " + ", ".join(sorted(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
