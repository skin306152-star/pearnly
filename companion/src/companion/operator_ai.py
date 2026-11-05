# -*- coding: utf-8 -*-
"""AI 接手的模型调用经产品站点（2026-11-03；放在根包，代录包 `express_operator` 不引用联网库，`test_operator_parts`）：把 Messages API 请求体交到成员通道的 ``operator_ai`` 机器方法
（Cowork ``companion.operator_api.operator_ai`` / 共用机器通道 ``cc_desktop.machine.operator_ai``），站点用自己的密钥转发、
按批次把费用上限关（到了回 ``{"error": "budget"}``，``helper.ComputerUseHelper`` 认它）。这台电脑不存 Claude 密钥。"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from . import channel
from .logger import get_logger

TIMEOUT_S = 180.0  # 一轮电脑操作（Opus、带截图）几十秒
log = get_logger(__name__)
_NOTED: set = set()  # 接不上 AI 的原因每种只记一次（每批装配都会问，不刷日志）


def _note_missing(reason: str, base: str) -> None:
    if (reason, base) in _NOTED:
        return
    _NOTED.add((reason, base))
    log.info(f"operator ai not connected · reason={reason} · site={base or '-'}")


def transport_for(settings, batch_id: str) -> Optional[Callable[[str, Dict], Dict]]:
    """这个成员的 ``transport(kind, request) -> response``；没有站点或这种通道没有这个方法回 None（照旧判断不了就交人）。
    ``settings`` 是成员配置（``CompanionConfig``：``backend_url`` / ``bearer_token``），不是 ``ExpressSettings``；没有成员配置传 None。"""
    token = str(getattr(settings, "bearer_token", "") or "")
    base = str(getattr(settings, "backend_url", "") or "")
    path = channel.path(token, "operator_ai") if token else ""
    if not token or not base or not path:
        # 代录照样录，只是认不出的画面判断不了、交给人：说明里写「ai not connected」，这里记下是哪一种
        _note_missing("no_token" if not token else "no_site" if not base else "channel_has_no_operator_ai", base)
        return None

    def transport(kind: str, request: Dict) -> Dict:
        import httpx  # 用到时才加载：代录真碰到认不出的画面才连站点

        from . import http_tls

        try:
            with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, verify=http_tls.context(),
                              timeout=httpx.Timeout(TIMEOUT_S, connect=10.0)) as client:
                response = client.post(path, json={"batch": batch_id, "kind": kind, "request": request})
        except httpx.HTTPError:
            return {"error": "unreachable"}
        if response.status_code != 200:
            return {"error": f"http_{response.status_code}"}
        data = response.json()
        data = data.get("message") if isinstance(data, dict) and isinstance(data.get("message"), dict) else data
        if not isinstance(data, dict) or not data.get("ok"):
            return {"error": str((data or {}).get("code") or "rejected")}
        found = data.get("response")
        return found if isinstance(found, dict) else {"error": "empty"}

    return transport
