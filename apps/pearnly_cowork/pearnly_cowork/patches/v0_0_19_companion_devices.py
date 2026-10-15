"""CC 桌面版多台电脑第 1 批（ADR-021，方案 4.1 第 1 步）：每把有效密钥补一台「旧电脑」。

登记人 = 签发人，名字取心跳报的电脑名 → 文件夹同步报的电脑名 → 连接名；设备密钥摘要留空，等新版 CC 桌面版首次
`device_hello` 认领；密钥行补上 `device`，**`active_key` 永远不改**（旧槽位，现有密钥原样有效，回退也认）。
只处理还没挂电脑的有效密钥，重跑什么都不做。账套登记、资料、定时任务读者等后续步骤在后面几批。
"""
import frappe

DEVICE = "Cowork CC Desktop Device"
CREDENTIAL = "Cowork Express Agent Credential"


def _folder_name(connection):
    # 文件夹同步报的电脑名存在不淘汰的短期状态里；读不到（迁移时状态服务不可用）就跳过，退到连接名。
    try:
        from pearnly_cowork.folder_intake import agent
        return (agent.read(connection) or {}).get("device") or ""
    except Exception:
        return ""


def execute():
    if not frappe.db.table_exists(DEVICE) or not frappe.db.has_column(CREDENTIAL, "device"):
        return
    from pearnly_cowork.companion import devices
    devices.seed_legacy(_folder_name)
