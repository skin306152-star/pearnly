#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 `legacy-endpoints.json` 里挑一条连接，导出成"只含这一条"的 JSON。

给 `sample-seed-dms-endpoint.sh` 用：种子脚本要**明确知道自己在用哪个账号**，
所以这里强制 `--require-username`，挑不到就报错退出，绝不放任"随便挑第一条"。

需要 python3（只用标准库）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True, type=Path)
    ap.add_argument("--out", dest="dst", required=True, type=Path)
    ap.add_argument("--adapter", default="mrerp_dms")
    ap.add_argument("--require-username", required=True,
                    help="必须精确等于这个账号名，防止误用客户账号")
    args = ap.parse_args()

    data = json.loads(args.src.read_text(encoding="utf-8"))
    eps = data.get("endpoints") or []

    hits = [e for e in eps
            if e.get("adapter") == args.adapter
            and e.get("username") == args.require_username
            and e.get("password")]
    if not hits:
        print("在 %s 里找不到 adapter=%s 且 username=%s 且有密码的连接"
              % (args.src, args.adapter, args.require_username), file=sys.stderr)
        return 1

    e = hits[0]
    out = {
        "system_url": (e.get("config_plain") or {}).get("system_url"),
        "username": e["username"],
        "password": e["password"],
        "admin_username": e.get("admin_username") or "",
        "admin_password": e.get("admin_password") or "",
    }
    if not out["system_url"]:
        print("这条连接没有 system_url，不敢用", file=sys.stderr)
        return 1

    args.dst.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("  选中 %s 的 %s（共 %d 条同账号连接，取第一条）"
          % (args.adapter, args.require_username, len(hits)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
