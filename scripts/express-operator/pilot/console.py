"""手动操作入口：给执行代理发一批动作、拉回截图（选公司这类一次性的准备步骤，由人看图决定下一步）。不调 AI、不花钱。

    python -m pilot.console '[{"do":"activate"},{"do":"screenshot","size":[1920,1080]}]' 名字
截图存 ~/pearnly-devtools/xp-pilot/runs/console/<名字>.png；同样守前台必须是 Express 的规矩。
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from .agent_client import AgentClient, Tunnel, remote_token

OUT = Path.home() / "pearnly-devtools" / "xp-pilot" / "runs" / "console"


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    actions = json.loads(args[0])
    name = args[1] if len(args) > 1 else "shot"
    OUT.mkdir(parents=True, exist_ok=True)
    with Tunnel():
        reply = AgentClient(remote_token(), timeout=120).act(actions)
    shots = 0
    for i, res in enumerate(reply["results"]):
        if res.get("png"):
            path = OUT / (f"{name}.png" if shots == 0 else f"{name}-{shots}.png")
            path.write_bytes(base64.b64decode(res.pop("png")))
            res["saved"] = str(path)
            shots += 1
        print(i, actions[i].get("do"), json.dumps(res, ensure_ascii=False)[:300])
    print("前台：", json.dumps(reply.get("fg"), ensure_ascii=False))
    for w in reply.get("windows", []):
        print("窗口：", json.dumps({k: w[k] for k in ("title", "cls", "rect", "texts")}, ensure_ascii=False)[:400])
    return 0 if all(r.get("ok") or r.get("skipped") for r in reply["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
