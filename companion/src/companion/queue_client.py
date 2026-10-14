"""Companion 队列客户端 → Pearnly 新平台的原生接口(出站拉取)。

新平台(本仓 `apps/pearnly_cowork/pearnly_cowork/companion/`)的三个机器接口,地址是租户自己的域名
(配对码 `exp_…@<租户域名>` 带来,见 `pairing.split_pairing_code`):
  POST /api/method/pearnly_cowork.companion.heartbeat.report        心跳 + 上报账套
  POST /api/method/pearnly_cowork.companion.queue_api.lease         领取待录入发票
  POST /api/method/pearnly_cowork.companion.queue_api.acknowledge   回报录入结果
请求体与旧线上相同;回应是 Frappe 的 `{"message": {...}}` 封套,这里取出里面那一层。

鉴权:Authorization: Bearer <exp_ 密钥>。站点 ERP 推送总闸关着时服务端拒绝(403)。

契约小适配,公开接口(health/poll/ack_success/ack_failure + IncomingTask 字段)保持不变,
故 main.py / 其余 companion 模块零改动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from .logger import get_logger

log = get_logger(__name__)

_METHOD = "/api/method/pearnly_cowork.companion."
_HEARTBEAT = _METHOD + "heartbeat.report"
_LEASE = _METHOD + "queue_api.lease"
_ACK = _METHOD + "queue_api.acknowledge"
_NOT_SENT = object()  # health() 的 update_blocked 没传：不带这个键（None 是「没被拦」，要照发）


def unwrap(response: httpx.Response) -> dict:
    """原生接口回 `{"message": {...}}`;取出里面那一层(其余形状原样返回,由调用方判 ok)。"""
    data = response.json()
    if isinstance(data, dict) and isinstance(data.get("message"), dict):
        return data["message"]
    return data if isinstance(data, dict) else {}


def _device_name() -> str:
    """本机名(给网页显示是哪台电脑在连)。取不到则空串(不影响心跳)。"""
    try:
        import socket

        return (socket.gethostname() or "")[:60]
    except Exception:
        return ""


@dataclass
class QueueClientConfig:
    backend_url: str
    bearer_token: str
    companion_id: str  # 上报为 agent_id(租约归属 + 审计)
    timeout: float = 10.0


@dataclass
class IncomingTask:
    id: str  # = erp_push_logs 行 id(log_id),ack 时回传
    invoice_no: Optional[str]
    payload: dict
    attempt_count: int = 0  # P1 lease 不下发尝试次数,保留字段兼容 main.py 日志


class QueueClient:
    def __init__(self, cfg: QueueClientConfig) -> None:
        self.cfg = cfg
        self._master_refresh: Optional[dict] = None
        self._client = httpx.Client(
            base_url=cfg.backend_url,
            headers={"Authorization": f"Bearer {cfg.bearer_token}"},
            # 显式分项超时:连接/读/写/连接池取用都封顶。任一处卡住即抛(被 poll loop 的
            # fail_streak 接住转离线),绝不无限期阻塞在 HTTP 上。非 HTTP 的僵死(账套文件
            # I/O 等)另由看门狗兜底。
            timeout=httpx.Timeout(cfg.timeout, connect=min(5.0, cfg.timeout)),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "QueueClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def health(
        self,
        *,
        account_sets: Optional[list] = None,
        accounts: Optional[list] = None,
        method: Optional[str] = None,
        selected: Optional[dict] = None,
        mapping: Optional[dict] = None,
        catalog: Optional[dict] = None,
        companion_version: Optional[str] = None,
        max_payload_version: Optional[int] = None,
        master_refresh_request_id: Optional[str] = None,
        master_refresh_scope: Optional[str] = None,
        master_refresh_account_set: Optional[str] = None,
        master_refresh_error: Optional[str] = None,
        offline: bool = False,
        account_sets_unchanged: bool = False,
        update_blocked: object = _NOT_SENT,
    ) -> dict:
        """心跳 · 上报探测到的账套列表 + 科目表 + 录入方式 + **客户所选账套**(供 FE 选/镜像)· 返回连接状态。

        account_sets:account_probe 探到的 [{code,name,company,path,tax_id,writable,row}, ...]。
        accounts:所选账套科目表 [{code,name,type}, ...](GLACC·供 FE「科目映射」下拉按名字选)。
        selected:客户所选账套整组 {account_set, account_dir, account_company,
                 account_set_row, express_root}
                 —— **放 body 顶层**(云端 _clean_selected/store_selected_account 读顶层这 4 键),
                 让网页镜像 + 端点 config 锁定一致。唯一键 = account_set(= 账套 PATH·见 pairing)。
        max_payload_version:本机 payload_contract.SUPPORTED_PAYLOAD_VERSIONS 上限 → 云端据此判断
                 能否安全下发新版载荷(见 payload_contract)。
        update_blocked:自动更新被 Windows 拦下 {version, at}(update_block.reported),没被拦是 None;
                 None 也照发(云端据此清掉旧值),只有不传这个参数才不带这个键(云端原样保留)。
        云端旧版忽略请求体仍正常。
        """
        body: dict = {}
        if account_sets is not None:
            body["account_sets"] = account_sets
        elif account_sets_unchanged:
            # 账套名单与上次相同：只说「没变」，不整份上传（服务端回过 `account_sets_unchanged_ok` 才这样发）
            body["account_sets_unchanged"] = True
        if accounts is not None:
            body["accounts"] = accounts
        if method is not None:
            body["method"] = method
        if mapping is not None:
            body["mapping"] = mapping  # 6 个科目码 → 云端写进 config(网页只读镜像)
        if catalog is not None:
            body["catalog"] = (
                catalog  # {products,customers,fingerprint} → 云端喂目录解析/记账画像
            )
        if companion_version is not None:
            body["companion_version"] = companion_version
        if max_payload_version is not None:
            body["max_payload_version"] = max_payload_version
        if update_blocked is not _NOT_SENT:
            body["update_blocked"] = update_blocked
        if master_refresh_request_id:
            body["master_refresh_request_id"] = str(master_refresh_request_id)
        if master_refresh_scope:
            body["master_refresh_scope"] = str(master_refresh_scope)
        if master_refresh_account_set:
            body["master_refresh_account_set"] = str(master_refresh_account_set)
        if master_refresh_error:
            body["master_refresh_error"] = str(master_refresh_error)
        if offline:
            body["offline"] = True  # 优雅退出信号 → 云端立即标离线(不等 3 分钟)
        else:
            body["device"] = (
                _device_name()
            )  # 上报本机名 → 网页显示"已连接:<电脑名>"(链接设备身份)
        if selected:
            body.update(selected)  # 整组所选账套并入 body 顶层(对齐云端契约)
        r = self._client.post(_HEARTBEAT, json=body)
        r.raise_for_status()
        return unwrap(r)

    def poll(self) -> Optional[IncomingTask]:
        """领取首个待录入发票(max=1)。空队列返回 None。"""
        r = self._client.post(
            _LEASE, json={"max": 1, "agent_id": self.cfg.companion_id}
        )
        r.raise_for_status()
        response = unwrap(r)
        command = response.get("master_refresh")
        if isinstance(command, dict) and command.get("request_id"):
            self._master_refresh = command
        jobs = response.get("jobs") or []
        if not jobs:
            return None
        job = jobs[0]
        return IncomingTask(
            id=str(job["log_id"]),
            invoice_no=job.get("invoice_no"),
            payload=job.get("payload") or {},
        )

    def take_master_refresh(self) -> Optional[dict]:
        command = self._master_refresh
        self._master_refresh = None
        return command

    def ack_success(
        self,
        task_id: str,
        extracted_invoice_no: Optional[str] = None,
        line_modes: Optional[list] = None,
        meta: Optional[dict] = None,
    ) -> bool:
        return self._ack(
            task_id,
            "success",
            express_docnum=extracted_invoice_no,
            line_modes=line_modes,
            meta=meta,
        )

    def ack_failure(
        self,
        task_id: str,
        error: str,
        error_code: Optional[str] = None,
        outcome: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> bool:
        # P1 ack 只收 error 文本;把 error_code 前缀进去避免信息丢失。
        # outcome(V3)= waiting_lock/rolled_back/needs_review/needs_mapping(覆盖 failed)。
        detail = f"[{error_code}] {error}" if error_code else error
        return self._ack(task_id, "failed", error=detail, outcome=outcome, meta=meta)

    def _ack(
        self,
        task_id: str,
        result: str,
        *,
        express_docnum: Optional[str] = None,
        error: Optional[str] = None,
        line_modes: Optional[list] = None,
        outcome: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> bool:
        body = {
            "log_id": str(task_id),
            "result": result,
            "express_docnum": express_docnum,
            "error": error,
            "agent_id": self.cfg.companion_id,
        }
        if line_modes:  # V2 逐行 mode(兜底原因)回传云端记日志
            body["line_modes"] = line_modes
        if outcome:  # V3 细粒度 outcome(云端据此分流·旧云端忽略)
            body["outcome"] = outcome
        if meta:  # V3 富元数据(写了哪些表/建客户商品/CDX 回查等)
            body["meta"] = meta
        r = self._client.post(_ACK, json=body)
        r.raise_for_status()
        return bool(unwrap(r).get("ok", False))
