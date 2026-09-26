"""启动自检:后端连通性。

(原依赖/Tesseract/校准/Express 屏幕探测等自检项随模拟录入 RPA 引擎一并移除;
直写 + 后台索引不依赖这些。Express 数据目录的探测走 express_locate.detect_express_root,
基于文件系统,与本模块无关。)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import CompanionConfig
from .logger import get_logger

log = get_logger(__name__)


@dataclass
class CheckItem:
    name: str
    ok: bool
    detail: str


@dataclass
class HealthReport:
    overall_ok: bool
    items: list[CheckItem] = field(default_factory=list)
    express_detected: bool = False
    express_reason: str = ""


def _check_backend(cfg: CompanionConfig) -> CheckItem:
    # 与 queue_client 统一:打 P1 Agent 心跳(Bearer agent token)。
    try:
        with httpx.Client(timeout=3.0) as c:
            r = c.post(
                f"{cfg.backend_url}/api/erp/agent/heartbeat",
                headers={"Authorization": f"Bearer {cfg.bearer_token}"},
            )
            if r.status_code == 200:
                return CheckItem("backend", True, cfg.backend_url)
            return CheckItem("backend", False, f"status={r.status_code}")
    except Exception as e:
        return CheckItem("backend", False, str(e))


def run_health_check(
    cfg: (
        CompanionConfig | list[CompanionConfig] | tuple[CompanionConfig, ...] | None
    ) = None,
    local_tessdata: Path | None = None,
    detect_express: bool = False,
) -> HealthReport:
    """后端连通自检。local_tessdata/detect_express 入参保留仅为兼容旧调用,已无作用。"""
    if cfg is None:
        from .profile_store import ProfileStore

        configs = ProfileStore.load().active_runtime_configs()
    elif isinstance(cfg, CompanionConfig):
        configs = [cfg]
    else:
        configs = [item for item in cfg if not item.paused]
    if not configs:
        items = [CheckItem("profiles", False, "no active profile")]
    else:
        items = []
        for item in configs:
            check = _check_backend(item)
            if len(configs) > 1:
                check.name = f"backend:{item.profile_id}"
            items.append(check)
    overall = all(i.ok for i in items)
    return HealthReport(overall_ok=overall, items=items)


def main() -> int:
    """CLI 入口:python -m src.companion.health_check"""
    rep = run_health_check()
    print(f"Overall: {'OK' if rep.overall_ok else 'FAIL'}")
    for it in rep.items:
        status = "[OK]" if it.ok else "[FAIL]"
        print(f"  {status:8} {it.name:24} {it.detail}")
    return 0 if rep.overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
