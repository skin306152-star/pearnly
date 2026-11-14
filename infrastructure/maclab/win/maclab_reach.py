"""测试机上看能不能连到本机 Mac 实验栈（以桌面用户跑，同 CC 桌面版的身份；本机 scripts/acceptance/maclab_pc.py prepare 调用）。
只用标准库。

    python maclab_reach.py <ERP 端口> <Cowork 端口>

每个站点：系统解析（走 hosts）得到的地址，对 ``http://<站点>:<端口>/api/method/ping`` 发一次 GET（不走系统代理，同 CC 用的 httpx
默认只认环境变量代理），记结果：``pong`` / ``没起``（连接被拒、超时：那套栈没起）/ 其它（解析不了、别的回复）。末行一行 JSON。
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request

SITES = ("erp.maclab.localhost", "cowork.maclab.localhost")


def check(name: str, port: int) -> dict:
    out = {"site": name, "port": port}
    try:
        out["addresses"] = sorted(set(socket.gethostbyname_ex(name)[2]))
    except OSError as error:
        out.update(state="unresolved", detail=str(error)[:120])
        return out
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{name}:{port}/api/method/ping", timeout=10) as response:
            body = response.read(200).decode("utf-8", "replace")
            out.update(state="pong" if response.status == 200 and "pong" in body else "unexpected", status=response.status)
    except urllib.error.HTTPError as error:
        out.update(state="unexpected", status=error.code)
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        refused = isinstance(reason, (ConnectionRefusedError, TimeoutError, socket.timeout))
        out.update(state="down" if refused else "error", detail=str(reason)[:120])
    return out


def main(argv) -> int:
    ports = [int(argv[0]) if argv else 18991, int(argv[1]) if len(argv) > 1 else 18992]
    print(json.dumps({"reach": [check(name, port) for name, port in zip(SITES, ports)]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
