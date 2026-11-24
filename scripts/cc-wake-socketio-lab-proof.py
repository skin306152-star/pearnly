#!/usr/bin/env python3
"""CC 桌面版 socket.io 唤醒的实验栈证明（轮询降量第二步，只是证明、不进产品；记录 docs/work/2026-11-24-cc-wake-socketio-proof.md）。

访客身份（``Cookie: sid=Guest``）连站点的 socket.io，``task_subscribe`` 一个由机器令牌派生的房间，收到 ``cc_wake`` 打一行；
断线按 15 秒起翻倍、最多 5 分钟、±20% 退避重连。只连给定的实验栈地址，不写任何数据，不连线上。

  python3 scripts/cc-wake-socketio-lab-proof.py --base http://erp.maclab.localhost:19121 --site erp.maclab.localhost \
      --token pcc_lab_fake --seconds 60 [--bearer]

``--bearer``：另试一次直接带机器令牌（``Authorization: Bearer``）握手，看现在是否被拒。服务端推一次「去领」：
``frappe.publish_realtime(event="cc_wake", message={...}, room="task_progress:<打印出的房间号>")``。
依赖：websocket-client 1.9.2（实验用，未进 CC 的锁定依赖）。
"""
import argparse
import hashlib
import json
import random
import threading
import time

import websocket  # noqa: E402  websocket-client
def room_id(token: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()           # 服务端库里存的就是它（contracts.token_digest）
    return "cc-wake-" + hashlib.sha256(("pearnly-cc-wake|" + digest).encode()).hexdigest()

class Wake:
    def __init__(self, base, site, token, on_wake, log=print, first=15.0, most=300.0, origin=None, headers=()):
        self.origin, self.extra = origin or base, list(headers)
        self.base, self.site, self.room, self.on_wake, self.log = base, site, room_id(token), on_wake, log
        self.first, self.most, self.stop = first, most, threading.Event()
        self.connects = []

    def run(self):
        fails = 0
        while not self.stop.is_set():
            ok = self._session()
            fails = 0 if ok else fails + 1
            if self.stop.is_set():
                return
            wait = min(self.most, self.first * 2 ** max(0, fails - 1)) * (1 + 0.2 * (2 * random.random() - 1))
            self.log(f"WS_RETRY in {wait:.1f}s (fails={fails})")
            self.stop.wait(wait)

    def _session(self) -> bool:
        url = self.base.replace("http", "ws", 1) + "/socket.io/?EIO=4&transport=websocket"
        self.connects.append(time.time())
        try:
            ws = websocket.create_connection(url, timeout=70, header=[f"Origin: {self.origin}", *(self.extra or ["Cookie: sid=Guest"])])
        except Exception as error:
            self.log(f"WS_CONNECT_FAIL {type(error).__name__}: {error}")
            return False
        ns, joined = "/" + self.site, False
        try:
            while not self.stop.is_set():
                frame = ws.recv()
                if frame.startswith("0"):
                    ws.send(f"40{ns},")
                elif frame == "2":
                    ws.send("3")
                elif frame.startswith(f"40{ns},"):
                    ws.send(f"42{ns}," + json.dumps(["task_subscribe", self.room]))
                    joined = True
                    self.log(f"WS_JOINED {frame[:80]}")
                elif frame.startswith(f"44{ns},"):
                    self.log(f"WS_REFUSED {frame}")
                    return False
                elif frame.startswith(f"42{ns},"):
                    event = json.loads(frame[len(f"42{ns},"):])
                    if event and event[0] == "cc_wake":
                        self.on_wake(event[1] if len(event) > 1 else None)
                else:
                    self.log(f"WS_FRAME {frame[:80]}")
        except Exception as error:
            self.log(f"WS_LOST {type(error).__name__}: {error}")
        finally:
            try: ws.close()
            except Exception: pass
        return joined


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--origin")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--bearer", action="store_true")
    args = parser.parse_args()
    log = lambda line: print(f"{time.time():.3f} {line}", flush=True)  # noqa: E731
    got = []
    wake = Wake(args.base, args.site, args.token, lambda message: (got.append((time.time(), message)), log(f"WAKE {message}")),
                log=log, origin=args.origin)
    log(f"ROOM {wake.room}")
    threading.Thread(target=wake.run, daemon=True).start()
    if args.bearer:
        try:
            ws = websocket.create_connection(args.base.replace("http", "ws", 1) + "/socket.io/?EIO=4&transport=websocket",
                                             timeout=10, header=[f"Origin: {args.origin or args.base}",
                                                                 f"Authorization: Bearer {args.token}"])
            ws.recv()
            ws.send(f"40/{args.site},")
            log(f"BEARER {ws.recv()[:160]}")
            ws.close()
        except Exception as error:  # noqa: BLE001
            log(f"BEARER_ERR {error}")
    time.sleep(args.seconds)
    wake.stop.set()
    log(f"DONE received={len(got)} connects={len(wake.connects)}")


if __name__ == "__main__":
    main()
