# -*- coding: utf-8 -*-
"""这台电脑在池里的身份（多台电脑，ADR-021；CC 桌面版 1.7.0 起）：设备密钥、认电脑（``device_hello``）、断开（``device_leave``）。

  POST /api/method/pearnly_cowork.companion.device_api.hello   {device_key, name, host_names}
  POST /api/method/pearnly_cowork.companion.device_api.leave   {}

- **设备密钥**：首次运行生成 32 字节随机数，和配对密钥一起用当前 Windows 用户的 DPAPI 加密存在本机配置里
  （``ProfileStore.device_key``）。它只用来把这台电脑的几把密钥归成一台、证明「同一台重连」；机器身份仍只来自 ``exp_`` 密钥。
  对话页经桥拿到它（``tomo_bridge`` 的 ``state().device``），每次打开都带它登记（register v2，对话页一侧在第 6 批）。
- **认电脑**：每个成员（Express 与 MR.ERP 的只读文件夹成员都算）启动后各发一次；之后名字、网卡地址（十分钟重查一次）
  变了再发，内容没变也每 12 小时再发一次；换了密钥（重新配对）或上次被说不能再用、现在标记清掉了，马上再发。回复：
  - ``attached``：挂上了（服务端下一次心跳要整份账套名单，`worker` 照常处理）；
  - ``superseded``：这台电脑在这条连接上已有自己的密钥，这把（旧槽位）已被服务端吊销 → 本机丢掉它：同一条连接还有别的成员就删掉
    这个，没有就标出来等对话页重新登记（``rekey``）；
  - ``claimed_elsewhere``：这把密钥已被另一台电脑认领（同一配对码抄到了两台）→ 这台不再用它，标出来等对话页重新登记；
  - ``{ok: false, code}``：报文问题，过一阵再试。
- **拒绝码按类分（1.9.1）**：401 是这把密钥服务端不认（被吊销、电脑被移除、重新连接前留下的旧密钥、不是这个站点发的），
  抛 ``KeyRejected``，五分钟后再试、不当成服务端旧版；提示重新连接走现有的那条路（Express 成员的心跳 / MR.ERP 成员的文件夹
  同步回 401 → 托盘显示「连接已失效，请在设置中重新连接」），重新配对换了密钥马上用新的认。这里**从不自己删成员、也不停用它**：
  不影响取任务、写账。404（服务端没有这条方法）与 403（总闸关着 / 连接停用）才当旧服务端或暂不可用，半小时后再问。
  1.7.0–1.9.0 把 401 也当旧服务端（半小时后再问，日志写「服务端还不认电脑（旧版）」）：录制机重连时旧密钥被拒就是这样误判的。
  断开时旧服务端、密钥早已失效都退回只删本机成员。
"""
from __future__ import annotations

import base64
import secrets
import threading
import time
from typing import Callable, Optional

import httpx

from . import device_facts, http_tls
from .logger import get_logger
from .queue_client import _device_name, unwrap

log = get_logger(__name__)

_METHOD = "/api/method/pearnly_cowork.companion.device_api."
HELLO = _METHOD + "hello"
LEAVE = _METHOD + "leave"
# 服务端 device_contracts 的状态（tests/test_companion_contract.py 守两边一致）
ATTACHED = "attached"
CLAIMED_ELSEWHERE = "claimed_elsewhere"
LEFT = "left"
SUPERSEDED = "superseded"
# 要人在对话页重新登记的两种：这个成员停用（不取任务、不同步、不领请求），直到换上新的密钥
NEEDS_REGISTER = (SUPERSEDED, CLAIMED_ELSEWHERE)
REJECTED = (401,)           # 这把密钥服务端不认：配对失效（见文件头），不是服务端旧版
REFUSED = (403, 404)        # 服务端没有这两条方法（旧版）、总闸关着或连接停用
OLD_SERVER_RETRY = 1800.0   # 旧服务端：半小时后再问一次（服务端可能已升级）
FAILED_RETRY = 300.0        # 网络、报文问题、密钥被拒：五分钟后再试
# 挂上了、内容也没变，最多这么久也再认一次（服务端那边把这台摘掉了而本机不知道时，不必等进程重启才恢复）
REFRESH_SECONDS = 12 * 3600.0


class OldServer(Exception):
    """服务端没有这两条方法（旧版，404）、总闸关着或连接停用（403）：过一阵再问。"""


class KeyRejected(Exception):
    """这把密钥服务端不认（401）：配对失效，等重新连接（托盘由心跳 / 文件夹同步提示），不是服务端旧版。"""


def new_device_key() -> str:
    """32 字节随机数的 URL 安全写法（43 个字符，服务端 ``contracts.device_key`` 收 32–128 个 ``[A-Za-z0-9_-]``）。"""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def _post(backend_url: str, token: str, path: str, body: dict, timeout: float) -> dict:
    with httpx.Client(base_url=backend_url, headers={"Authorization": f"Bearer {token}"},
                      timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)), verify=http_tls.context()) as client:
        response = client.post(path, json=body)
    if response.status_code in REJECTED:
        raise KeyRejected(f"device key rejected: {response.status_code}")
    if response.status_code in REFUSED:
        raise OldServer(f"device method refused: {response.status_code}")
    response.raise_for_status()
    return unwrap(response)


def hello(backend_url: str, token: str, body: dict, timeout: float = 15.0) -> dict:
    return _post(backend_url, token, HELLO, body, timeout)


def leave(backend_url: str, token: str, timeout: float = 15.0) -> bool:
    """断开这台电脑（吊销这把密钥本身）。服务端确实吊销了回 True；旧服务端、连不上、密钥早已失效回 False（照样删本机成员）。"""
    if not str(backend_url or "").strip() or not str(token or "").startswith("exp_"):
        return False
    try:
        reply = _post(backend_url, token, LEAVE, {}, timeout)
    except KeyRejected as error:
        log.info("断开时这把密钥早已失效（被吊销或电脑已移除），只删本机成员: %s", error)
        return False
    except OldServer as error:
        log.info("断开时服务端不认电脑方法（旧版或连接停用），只删本机成员: %s", error)
        return False
    except Exception as error:
        log.warning("断开时连不上服务端，只删本机成员: %r", error)
        return False
    return bool(isinstance(reply, dict) and reply.get("ok") and reply.get("status") == LEFT)


def settle_old_key(backend_url: str, token: str, *, timeout: float = 15.0) -> str:
    """配对要把成员手里的旧密钥换成对话页刚登记来的这台电脑自己的新密钥时，先拿旧密钥认一次电脑（device_hello）。

    升级到 1.7.0 后第一次打开：对话页的登记可能先于这个成员用旧槽位密钥认电脑，服务端就为这台电脑新建了记录并发新钥，
    旧槽位密钥还挂在没人认领的「旧电脑」上，换掉以后再也不会去认电脑——那台「旧电脑」永远离线却不吊销，写账可能分派给它
    （第 6 批审查第 5 条）。先认一次：服务端发现这台电脑在这条连接上已有自己的密钥 → ``superseded``，吊销旧钥，把旧电脑的
    可达行、读文件夹任务转给这台再让旧电脑退役（`credentials.hello`）。旧钥是别的电脑认领过的（同一配对码抄到两台）回
    ``claimed_elsewhere``、什么都不改；旧钥早已吊销（401）、旧服务端（404）等一律不管。从不抛，回服务端的状态（没发、失败是空串）。"""
    if not str(backend_url or "").strip() or not str(token or "").startswith("exp_"):
        return ""
    key = device_key()
    if not key:
        return ""
    try:
        reply = hello(backend_url, token, hello_body(key), timeout)
    except KeyRejected as error:
        log.info("换密钥前旧密钥认电脑被拒（旧密钥已失效，照常换上新密钥）: %s", error)
        return ""
    except OldServer as error:
        log.info("换密钥前旧密钥认电脑被拒（服务端不认电脑方法）: %s", error)
        return ""
    except Exception as error:
        log.info("换密钥前旧密钥认电脑失败: %r", error)
        return ""
    return str(reply.get("status") or "") if isinstance(reply, dict) and reply.get("ok") else ""


# ── 设备密钥（进程内只取一次） ─────────────────────────────────────────────────────

_key_lock = threading.Lock()
_key_cache: Optional[str] = None


def device_key(load_store: Optional[Callable] = None) -> str:
    """这台电脑的设备密钥（没有就生成并存下）；取不到（不是 Windows、DPAPI 不可用）回空串，从不抛。"""
    global _key_cache
    with _key_lock:
        if _key_cache:
            return _key_cache
        try:
            if load_store is None:
                from .profile_store import ProfileStore

                load_store = ProfileStore.load
            _key_cache = load_store().device_key()
        except Exception:
            log.warning("取不到设备密钥：这台电脑先按旧方式连接", exc_info=True)
            return ""
        return _key_cache


def identity(load_store: Optional[Callable] = None) -> dict:
    """给对话页的（桥的 ``state().device``）：设备密钥与电脑名；取不到设备密钥就是空字典（页面按旧版处理）。"""
    key = device_key(load_store)
    return {"key": key, "name": _device_name()} if key else {}


def _reset_for_tests() -> None:
    global _key_cache
    with _key_lock:
        _key_cache = None


# ── 认电脑 ──────────────────────────────────────────────────────────────────

def hello_body(key: str, *, names: Optional[list] = None) -> dict:
    """认电脑的内容：设备密钥、电脑名、网卡地址。1.7.0 起不再报「常驻」（ADR-021 第 56 条；服务端对多出的键本来就忽略）。"""
    return {"device_key": key, "name": _device_name(),
            "host_names": list(device_facts.host_names() if names is None else names)}


class Announcer:
    """每把密钥何时该（再）认一次电脑。同一进程里取任务线程（Express 成员）与文件夹线程（MR.ERP 成员）共用一个。

    ``on_result(cfg, status)``：回 ``superseded`` / ``claimed_elsewhere`` 时调（托盘据此改本机成员、重启各线程）。"""

    def __init__(self, *, key: Callable[[], str] = device_key, send: Callable = hello,
                 names: Callable[[], list] = device_facts.host_names, clock: Callable[[], float] = time.monotonic,
                 on_result: Optional[Callable] = None) -> None:
        self._key = key
        self._send = send
        self._names = names
        self._clock = clock
        self.on_result = on_result
        self._lock = threading.Lock()
        self._memory: dict = {}   # 密钥 → (下次最早何时, 上次报的内容, 上次的结果)

    def announce(self, cfg) -> Optional[str]:
        """到时候就认一次电脑，返回服务端的状态（没发、旧服务端、失败回 None）。从不抛：认电脑失败不影响取任务。"""
        token = str(getattr(cfg, "bearer_token", "") or "")
        url = str(getattr(cfg, "backend_url", "") or "")
        if not token.startswith("exp_") or not url or getattr(cfg, "device_state", ""):
            return None
        now = self._clock()
        with self._lock:
            due, sent, last_status = self._memory.get(token, (0.0, None, None))
        if sent is None and now < due:
            return None   # 上次没成（旧服务端、连不上）：到点再试
        key = self._key()
        if not key:
            with self._lock:
                self._memory[token] = (now + OLD_SERVER_RETRY, None, None)
            return None
        try:
            # 网卡地址本身十分钟才重查一次（device_facts.host_names），这里每轮比一下很便宜
            body = hello_body(key, names=self._names())
        except Exception:
            log.warning("整理认电脑的内容失败", exc_info=True)
            return None
        if sent is not None and body == sent and now < due and last_status not in NEEDS_REGISTER:
            return None   # 挂上了、内容没变、没到再认的时候：不再发
        try:
            reply = self._send(url, token, body)
        except KeyRejected as error:
            # 配对失效不是服务端旧版：不停用这个成员（心跳 / 文件夹同步照样回 401，托盘提示重新连接），五分钟后再认；
            # 重新连接换了密钥，下一轮按新密钥马上认
            log.warning("这把密钥服务端不认（已吊销、电脑已移除或不是这个站点发的），等重新连接 · profile=%s: %s",
                        getattr(cfg, "profile_id", ""), error)
            with self._lock:
                self._memory[token] = (now + FAILED_RETRY, None, None)
            return None
        except OldServer as error:
            log.info("服务端还不认电脑（旧版或连接停用），半小时后再问: %s", error)
            with self._lock:
                self._memory[token] = (now + OLD_SERVER_RETRY, None, None)
            return None
        except Exception as error:
            log.info("认电脑失败，过一会儿再试: %r", error)
            with self._lock:
                self._memory[token] = (now + FAILED_RETRY, None, None)
            return None
        status = str(reply.get("status") or "") if isinstance(reply, dict) and reply.get("ok") else ""
        if not status:
            log.warning("认电脑被拒: %s", (reply or {}).get("code") if isinstance(reply, dict) else reply)
            with self._lock:
                self._memory[token] = (now + FAILED_RETRY, None, None)
            return None
        with self._lock:
            self._memory[token] = (now + REFRESH_SECONDS, body, status)
        if status in NEEDS_REGISTER:
            log.warning("这把密钥不再用（%s），等对话页重新登记 · profile=%s", status, getattr(cfg, "profile_id", ""))
            try:
                cfg.device_state = status
            except Exception:
                pass
            if self.on_result is not None:
                try:
                    self.on_result(cfg, status)
                except Exception:
                    log.exception("认电脑结果回调失败")
        return status


    def forget(self, token: str) -> None:
        """忘掉这把密钥上次认电脑的记录（换了密钥、或「不能再用」的标记清掉了）：下一轮马上再认一次。"""
        with self._lock:
            self._memory.pop(str(token or ""), None)


def settle(store, profile_id: str, status: str) -> str:
    """把 ``superseded`` / ``claimed_elsewhere`` 落到本机成员上（托盘在界面线程调）：

    - ``superseded`` 且同一条连接还有别的成员（拿着这台电脑自己的密钥）→ 删掉这个成员，回 ``removed``；
    - 其余 → 标上 ``device_state``（这个成员停用，等对话页重新登记换上新密钥时清掉），回 ``marked``；
    - 成员已不在 → ``gone``。"""
    record = next((item for item in store.profiles if item.profile_id == profile_id), None)
    if record is None or status not in NEEDS_REGISTER:
        return "gone"
    if status == SUPERSEDED and record.endpoint_id and any(
            item.profile_id != profile_id and item.endpoint_id == record.endpoint_id and not item.device_state
            for item in store.profiles):
        store.remove_profile(profile_id)
        return "removed"
    store.set_device_state(profile_id, status)
    return "marked"


ANNOUNCER = Announcer()

__all__ = ["ANNOUNCER", "ATTACHED", "Announcer", "CLAIMED_ELSEWHERE", "HELLO", "KeyRejected", "LEAVE", "LEFT",
           "NEEDS_REGISTER", "OldServer", "REFUSED", "REJECTED", "SUPERSEDED", "device_key", "hello", "hello_body", "identity",
           "leave", "settle_old_key", "new_device_key", "settle"]
