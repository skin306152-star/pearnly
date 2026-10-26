"""标定固定流程的坐标：对着「已最大化、刚点了新建」的空白单据截一张图，请 Opus 5.5 找出各格位置，
画上标记存图，由人看图确认后再写进 flows.json（verified 改 true）。一次约 0.03 美元，记进同一本账。

    python -m pilot.calibrate --type RR        （先让主会话请用户登录、打开试点公司；单据窗口由脚本从菜单打开）
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import time
from pathlib import Path

from . import pricing
from .ledger import Ledger

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["anchors", "notes"],
          "properties": {"notes": {"type": "string"},
                         "anchors": {"type": "array", "items": {
                             "type": "object", "additionalProperties": False,
                             "required": ["name", "x", "y", "label_seen", "sure"],
                             "properties": {"name": {"type": "string"}, "x": {"type": "integer"},
                                            "y": {"type": "integer"}, "label_seen": {"type": "string"},
                                            "sure": {"type": "boolean"}}}}}}


def ask(claude, png_b64: str, doc_type: str, flows: dict) -> dict:
    wanted = [(k.split(".", 1)[1], a.get("label", "")) for k, a in flows["anchors"].items()
              if k.startswith(doc_type + ".")]
    lines = "\n".join(f"- {name}：{label or '（见名称）'}" for name, label in wanted)
    prompt = (f"这是 Express 会计软件刚点了「新建」的空白{doc_type}单据（窗口已最大化，截图 1280×720）。"
              f"给出下面每个输入格的点击位置（输入框本身的中心，不是标签文字），用截图坐标：\n{lines}\n"
              "grid = 明细表格第一行的品码格。找不到就 sure=false 并说明。另外在 notes 里说明表头各格按回车的跳转顺序（看得出的话）"
              "和明细一行有多高（像素）。")
    resp = claude.client.messages.create(
        model=pricing.OPUS, max_tokens=4000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}},
            {"type": "text", "text": prompt}]}])
    return json.loads("".join(b.text for b in resp.content if b.type == "text")), resp.usage


def mark(png: bytes, anchors: list, out: Path) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        out.write_bytes(png)
        return
    img = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(img)
    for a in anchors:
        x, y = a["x"], a["y"]
        draw.line([(x - 10, y), (x + 10, y)], fill=(255, 0, 0), width=2)
        draw.line([(x, y - 10), (x, y + 10)], fill=(255, 0, 0), width=2)
        draw.text((x + 6, y - 16), a["name"], fill=(255, 0, 0))
    img.save(out)


def main(argv=None) -> int:
    from .agent_client import AgentClient, Tunnel, remote_token
    from .models import Claude
    from .run_pilot import HERE, RUNS, load_key

    p = argparse.ArgumentParser()
    p.add_argument("--type", required=True, choices=["RR", "HP", "IV", "HS"])
    args = p.parse_args(argv)
    flows = json.loads((HERE / "flows.json").read_text(encoding="utf-8"))
    ledger = Ledger()
    ledger.guard(pricing.OPUS, 4000, 4000)
    out = RUNS / "calibrate"
    out.mkdir(parents=True, exist_ok=True)
    claude = Claude(load_key())
    with Tunnel():
        agent = AgentClient(remote_token())
        new = flows["anchors"]["toolbar.new"]["xy"]
        acts = [{"do": "activate"}, {"do": "wait", "ms": 300}, {"do": "us_layout"}]
        for k in flows["menus"][args.type]:
            acts += [{"do": "keys", "text": k}, {"do": "wait", "ms": 350}]
        acts += [{"do": "wait", "ms": flows["form_open_ms"]}, {"do": "click", "x": new[0], "y": new[1]},
                 {"do": "wait", "ms": 1500}, {"do": "screenshot", "size": [1280, 720]}]
        reply = agent.act(acts)
        if not all(r.get("ok") for r in reply["results"]):
            print("动作没做完：", reply["results"])
            return 1
        png_b64 = reply["results"][-1]["png"]
        t = time.monotonic()
        answer, usage = ask(claude, png_b64, args.type, flows)
        ledger.record(model=pricing.OPUS, usage=usage, purpose=f"calibrate:{args.type}", doc_id="-",
                      mode="calibrate", seconds=time.monotonic() - t)
        print("注意：单据现在停在新建状态，标定完由人决定取消（工具栏取消键）还是留着给第一张单用。")
    png = base64.b64decode(png_b64)
    (out / f"{args.type}.png").write_bytes(png)
    mark(png, answer["anchors"], out / f"{args.type}-marked.png")
    for a in answer["anchors"]:
        a["screen_xy"] = [round(a["x"] * 1.5), round(a["y"] * 1.5)]
    (out / f"{args.type}.json").write_text(json.dumps(answer, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(answer, ensure_ascii=False, indent=1))
    print(f"标注图：{out / (args.type + '-marked.png')}；累计 {ledger.total} 美元")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
