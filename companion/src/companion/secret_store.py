# -*- coding: utf-8 -*-
"""「连 Express 设置」读取口(方法无关 seam)。

DBF 直写不需要 Express 登录密码,故本模块只读所选账套整组(位置 + 账套目录/公司/名/行号),
不再存取登录凭证。配对/心跳/直写都从这里取同一份设置,`load_express_settings` 是唯一入口。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpressSettings:
    """「连 Express 设置」整组:Express 位置 + 所选账套(写盘目标)。"""
    method: str  # dbf(直写)—— 唯一录入方式
    express_root: str  # Express 数据根(账套父目录)
    express_exe: str  # Express 程序
    account_dir: str  # 所选账套目录(DBF 写文件目标)
    account_company: str  # 所选账套公司名(三重一致性闸期望公司)
    account_set: str  # 所选账套名(C3 期望值 · 与云端同源)
    account_set_row: int  # 公司 grid 行号(由所选账套推导)
    express_workdate: str = ""  # 工作日 dd/mm/yy 佛历(空=今天 · 客户不可见)
    pack_backup_enabled: bool = True  # 写盘前账套快照开关(直写回滚安全网)
    pack_backup_dir: str = ""  # 快照根(空=默认 APPDATA/Pearnly/pack_backups)
    pack_backup_keep: int = 3  # 成功后保留最近 K 份非保护快照

    def has_selection(self) -> bool:
        """所选账套是否齐备(目录 + 账套名)→ 缺则 fail-safe 拒落账。"""
        return bool((self.account_dir or "").strip() and (self.account_set or "").strip())


def load_express_settings(cfg) -> ExpressSettings:
    """共享「读设置」口(方法无关):DBF 直写从这里取整组连 Express 设置。"""
    return ExpressSettings(
        method=str(getattr(cfg, "method", "") or "dbf"),
        express_root=str(getattr(cfg, "express_root", "") or ""),
        express_exe=str(getattr(cfg, "express_exe", "") or ""),
        account_dir=str(getattr(cfg, "account_dir", "") or ""),
        account_company=str(getattr(cfg, "account_company", "") or ""),
        account_set=str(getattr(cfg, "account_set", "") or ""),
        account_set_row=int(getattr(cfg, "account_set_row", 0) or 0),
        express_workdate=str(getattr(cfg, "express_workdate", "") or ""),
        pack_backup_enabled=bool(getattr(cfg, "pack_backup_enabled", True)),
        pack_backup_dir=str(getattr(cfg, "pack_backup_dir", "") or ""),
        pack_backup_keep=int(getattr(cfg, "pack_backup_keep", 3) or 3),
    )
