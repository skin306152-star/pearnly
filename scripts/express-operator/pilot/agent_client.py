"""本机这边连测试机执行代理：ssh 端口转发 + HTTP JSON。和 fake_express.FakeExpress 同一个接口（act / stop）。"""
from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.request
from typing import Dict, List, Optional

HOST = "lenovo-office-lan"
PORT = 18765
REMOTE_WORK = "C:/PearnlyStress/_xp_pilot"


class AgentClient:
    def __init__(self, token: str, port: int = PORT, timeout: float = 60):
        self.base = f"http://127.0.0.1:{port}"
        self.token = token
        self.timeout = timeout

    def _post(self, path: str, body: Dict) -> Dict:
        data = json.dumps({"token": self.token, **body}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def act(self, actions: List[Dict], windows: bool = True) -> Dict:
        return self._post("/act", {"actions": actions, "windows": windows})

    def stop(self) -> Dict:
        return self._post("/stop", {})


class Tunnel:
    """ssh -N -L 端口转发（一条长连接，之后每批动作走局域网毫秒级往返，不再每次起 ssh）。"""

    def __init__(self, host: str = HOST, port: int = PORT):
        self.host, self.port = host, port
        self.proc: Optional[subprocess.Popen] = None

    def __enter__(self):
        self.proc = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes",
                                      "-L", f"{self.port}:127.0.0.1:{self.port}", self.host])
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return self
            except OSError:
                time.sleep(0.2)
        self.__exit__()
        raise RuntimeError("端口转发没起来")

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)


def remote_token(host: str = HOST) -> str:
    out = subprocess.run(["ssh", host, f"Get-Content {REMOTE_WORK}/agent.token"], capture_output=True, text=True,
                         timeout=30, stdin=subprocess.DEVNULL)
    token = out.stdout.strip()
    if not token:
        raise RuntimeError("拿不到代理令牌：代理起了吗（start-agent.ps1）？")
    return token
