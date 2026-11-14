#!/usr/bin/env python3
"""在本机模拟一次 CC 桌面版的心跳（本机 Mac 实验栈的冒烟用；只用标准库，在 Mac 上跑，不进容器）。

    printf '%s' "$配对码" | python3 infrastructure/maclab/probe.py --via http://192.168.0.93:18991 [--leave]

配对码从标准输入读（不放命令行，免得出现在进程列表里）；请求按 CC 桌面版自己的写法（``companion/src/companion/channel.py`` 的路径、
``Authorization: Bearer <密钥>``、请求体与 ``queue_client.health`` 一样，不带账套名单），发到 ``--via``（走 Mac 的局域网地址，
证明测试机那样连得进来），Host 头取配对码 @ 后面的「主机:端口」（站点按它认，同测试机 hosts 指过来的效果）。
``--leave``：报完再按 CC 删成员时那样断开（作废这把密钥），冒烟造的电脑不留在线。

打印一行 JSON：心跳的 HTTP 状态与回复里的几个键（不打印密钥）。退出码 0 = 心跳被认。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPANION = ROOT / "companion" / "src" / "companion"
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::\d{1,5})?$")
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


def split_code(code: str) -> tuple[str, str]:
    """配对码 → (密钥, 主机:端口)；与 CC 桌面版 ``pairing.split_pairing_code`` 同一规则（tests/test_maclab.py 逐例对照），
    只是这里回主机而不是整个 URL。格式不对回 ("", "")。"""
    value = "".join(str(code or "").split())
    token, sep, host = value.rpartition("@")
    name = host.split(":")[0].lower()
    local = name in _LOCAL_HOSTS or name.endswith(".localhost")
    if not sep or not token.startswith(("exp_", "pcc_")) or not _HOST.match(host) or ("." not in name and not local):
        return "", ""
    return token, host.lower()


def channel_paths() -> dict:
    """CC 桌面版的通道路径表（原样读 channel.py，不另抄一份）。"""
    namespace: dict = {}
    exec(compile((COMPANION / "channel.py").read_text(encoding="utf-8"), "channel.py", "exec"), namespace)
    return namespace


def companion_version() -> str:
    match = re.search(r'^VERSION = "([^"]+)"', (COMPANION / "version.py").read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else "0"


def post(via: str, host: str, path: str, token: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(via.rstrip("/") + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Host": host, "Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {"raw": raw[:200].decode("utf-8", "replace")}
    if isinstance(data, dict) and isinstance(data.get("message"), dict):
        data = data["message"]
    return status, data if isinstance(data, dict) else {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--via", required=True, help="http://<Mac 局域网地址>:<端口>")
    parser.add_argument("--leave", action="store_true")
    args = parser.parse_args(argv)
    token, host = split_code(sys.stdin.read())
    if not token:
        print(json.dumps({"ok": False, "why": "pairing code not understood"}))
        return 2
    paths = channel_paths()
    body = {"companion_version": companion_version(), "method": "dbf", "device": "maclab-probe",
            "max_payload_version": max(channel_paths_payload_versions())}
    status, reply = post(args.via, host, paths["path"](token, "heartbeat"), token, body)
    out = {"channel": paths["of_token"](token), "host": host, "heartbeat_status": status,
           "reply_keys": sorted(reply)[:20], "ok": status == 200 and bool(reply.get("ok"))}
    if args.leave:
        leave_status, _ = post(args.via, host, paths["path"](token, "leave"), token, {})
        out["leave_status"] = leave_status
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


def channel_paths_payload_versions() -> list:
    """CC 桌面版这一版支持的载荷版本（payload_contract.SUPPORTED_PAYLOAD_VERSIONS，原样读）。"""
    text = (COMPANION / "payload_contract.py").read_text(encoding="utf-8")
    match = re.search(r"^SUPPORTED_PAYLOAD_VERSIONS = frozenset\(\{([0-9, ]+)\}\)", text, re.M)
    return [int(item) for item in match.group(1).split(",")] if match else [1]


if __name__ == "__main__":
    sys.exit(main())
