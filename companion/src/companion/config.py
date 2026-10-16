"""Companion 持久化配置

存储位置:
  Windows: %APPDATA%/Pearnly/companion.json
  macOS:   ~/Library/Application Support/Pearnly/companion.json (Phase 3 适配)
  Linux:   ~/.config/Pearnly/companion.json

配置项:
  backend_url         主 repo 后端 URL(Phase 2 默认指本地 mock)
  bearer_token        鉴权 token
  poll_interval_ms    拉队列间隔
  debug_mode          调试模式
  dpi_simulation      模拟 DPI(1.0 / 1.25 / 1.5 / 2.0,None = 用真实)
  log_level           日志级别
  max_retry           失败重试上限
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


def _appdata_root() -> Path:
    """跨平台 appdata 目录"""
    system = platform.system()
    if system == "Windows":
        return (
            Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            / "Pearnly"
        )
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Pearnly"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Pearnly"


APPDATA_DIR = _appdata_root()
APPDATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APPDATA_DIR / "companion.json"
LOG_DIR = APPDATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _default_backend_url() -> str:
    """Honor PEARNLY_COMPANION_PORT env var (set by launcher when 8765 is busy)"""
    port = os.environ.get("PEARNLY_COMPANION_PORT", "8765")
    return f"http://127.0.0.1:{port}"


@dataclass
class CompanionConfig:
    backend_url: str = field(default_factory=_default_backend_url)
    bearer_token: str = "dev-mock-token"
    poll_interval_ms: int = 3000
    debug_mode: bool = False
    dpi_simulation: Optional[float] = None
    log_level: str = "INFO"
    max_retry: int = 3
    companion_id: str = field(default_factory=lambda: f"comp-{os.urandom(4).hex()}")
    method: str = "dbf"  # dbf(直写账套 DBF)—— 唯一录入方式
    account_dir: Optional[str] = (
        None  # DBF 直写目标账套目录 = 配对时客户从探测列表所选(C2 写盘期望)
    )
    # 客户所选账套 = 默认写盘目标。任务可改选同一连接已授权根中的账套；端侧仍逐次复核。
    account_set: str = (
        ""  # 所选账套名(C3 期望 · 与云端 endpoint.config.account_set 同源 · heartbeat 同步)
    )
    default_depcod: str = ""  # 单据默认部门码 DEPCOD(用部门的公司填·否则留空)
    express_root: Optional[str] = (
        None  # Express 数据根目录(探测账套用·默认取 account_dir 父目录)
    )
    authorized_roots: list[str] = field(
        default_factory=list
    )  # 此连接允许网页/LINE选择的数据根；旧配置空值时仅放行当前根
    heartbeat_interval_s: int = 60  # 心跳间隔(上报账套列表)
    # 写盘前账套快照(直写回滚安全网):备份不成功不写·保留最近 K 份·失败快照不清。
    pack_backup_enabled: bool = True
    pack_backup_dir: str = ""  # 快照根(空=APPDATA/Pearnly/pack_backups)
    pack_backup_keep: int = 3  # 成功后保留最近 K 份非保护快照
    # P4 打包/配对/自启
    express_exe: Optional[str] = (
        None  # Express 程序 exe(默认 express_root/ExpressI.exe)
    )
    account_company: str = ""  # 所选账套公司名(ISINFO.THINAM · 三重一致性闸期望公司)
    account_set_row: int = 1  # 公司 grid 行号(1-based · 由所选账套推导)
    # 科目映射:配对时从所选账套科目表(GLACC)按名字选的 6 个会计科目码(随心跳上报云端·网页只读镜像)。
    revenue_acc: str = ""  # 收入科目(销项)
    ar_acc: str = ""  # 应收科目(销项)
    vat_output_acc: str = ""  # 销项税科目
    fallback_acc: str = ""  # 采购/进货科目(进项)
    ap_acc: str = ""  # 应付科目(进项)
    vat_input_acc: str = ""  # 进项税科目
    express_workdate: str = (
        ""  # 工作日 dd/mm/yy 佛历(空=今天·客户不可见·仅测试账套高级项填历史账期)
    )
    paired: bool = False  # 是否完成首次配对
    autostart: bool = True  # 开机自启(配对成功后写注册表 Run)
    # CC 开着时保持电脑唤醒 / 用电池时也保持（keep_awake.py，同 Claude 桌面版，用户 10-16；旧 always_on 迁过来，profile_store）
    keep_awake: bool = True
    keep_awake_on_battery: bool = True
    ui_lang: str = ""  # 界面语言 th/zh(空=按 Windows 语言 · 配对窗可切换并记住)
    # F2 运行时身份。由 ProfileStore 持久化，旧 flat 配置下留空。
    profile_id: str = ""
    paused: bool = False
    endpoint_id: str = ""
    endpoint_name: str = ""
    pearnly_account: str = ""
    # 成员类型：express（读写 Express 账套、领推送任务、读文件夹）/ folder（MR.ERP 的小助手：只读文件夹，定时任务用，2026-10-09）
    kind: str = "express"
    # 认电脑时服务端说这把密钥不能再用（superseded / claimed_elsewhere，见 device_link）：这个成员停用，等对话页重新登记
    device_state: str = ""
    # 没有 Express、按只读文件夹的成员加入事务所 Express 池的（pairing.pair_folder(pool=True) 且服务端说是 Express 连接）：
    # 以后找到 Express 只按这个标记重新配对成能写账的成员（desktop_settings._upgrade_pool_members），不靠联网查连接类型
    pool: bool = False

    def resolved_express_root(self) -> Optional[str]:
        """探测账套用的数据根:显式 express_root 优先,否则取 account_dir 的父目录。"""
        if self.express_root:
            return self.express_root
        if self.account_dir:
            return str(Path(self.account_dir).parent)
        return None

    def save(self, path: Path | None = None) -> Path:
        profile_save = getattr(self, "_profile_save", None)
        source = Path(getattr(self, "_source_path", CONFIG_FILE))
        if callable(profile_save) and (path is None or Path(path) == source):
            return Path(profile_save(self))
        target = path or getattr(self, "_source_path", None) or CONFIG_FILE
        target = Path(target)
        # 直接构造的 config 也必须走 v2，不允许密钥回退为明文落盘。
        from .profile_store import ProfileStore

        return ProfileStore.load(target).save_profile(self)

    @classmethod
    def load(cls, path: Path | None = None) -> "CompanionConfig":
        target = path or CONFIG_FILE
        target = Path(target)
        if not target.exists():
            from .profile_store import ProfileStore

            return ProfileStore.load(target).new_runtime_config()
        # 旧的单 Profile 调用点在 F2 切换期仍可读第一个 Profile。
        # ProfileStore.load 同时把 flat 旧文件原子迁移到 schema v2。
        from .profile_store import ProfileStore

        store = ProfileStore.load(target)
        runtime = store.runtime_configs(include_paused=True)
        return runtime[0] if runtime else store.new_runtime_config()

    def update(self, **changes: Any) -> "CompanionConfig":
        for k, v in changes.items():
            if hasattr(self, k):
                setattr(self, k, v)
        return self


def member_active(cfg) -> bool:
    """这个成员此刻要不要干活：没暂停、配对密钥没被服务端说不能再用（device_state，见 device_link）。"""
    return not bool(getattr(cfg, "paused", False)) and not str(getattr(cfg, "device_state", "") or "")
