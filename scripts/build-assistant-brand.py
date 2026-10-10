#!/usr/bin/env python3
"""智能体对话页的品牌图（ADR-019）：从宣传页同一份源头生成，不另画。

- 智能体自己的形象：CC 的吐司胖橘（`website/scenes.py` 的 `cc_loaf`，与登录页底部那排同一只；
  用户 2026-10-10 晚定，换掉原来的云朵），连同它用的铅笔描边滤镜写成独立 SVG；眨眼、笑、尾巴与铃铛在
  globals.css，眼睛跟随鼠标在 components/mascot.tsx。对话页的欢迎处不分产品，都用它。

用法：`python3 scripts/build-assistant-brand.py`；`--check` 只比对不写（tests/test_assistant.py 也会比对）。
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend/assistant/src/brand"


def render() -> dict[str, str]:
    sys.path.insert(0, str(ROOT / "website"))
    try:
        import scenes
    finally:
        sys.path.pop(0)
    pencil = re.search(r"<filter id=\"hd-pencil\".*?</filter>", scenes.DEFS).group(0)
    # 胖橘原稿：身子中线 x=0、鞋底 y=0；上边留到泡泡顶，下边留到影子
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" class="pearnly-mascot" viewBox="-136 -306 270 324" '
           f'aria-hidden="true" focusable="false"><defs>{pencil}</defs>'
           f'<g filter="url(#hd-pencil)" {scenes.PEN}>{scenes.cc_loaf(0, 0, 1, 0)}</g></svg>')
    return {"cc.svg": svg + "\n"}


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
