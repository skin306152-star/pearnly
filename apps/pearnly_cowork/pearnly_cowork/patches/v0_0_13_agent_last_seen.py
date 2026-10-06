"""小助手报到时间从连接的上报记录搬到当前密钥那一小行（2026-10-07）。

心跳每分钟一次，报到时间留在上报记录里会让整份记录（生产每条几百 KB）每分钟重写、全量进数据库日志。
新代码在密钥行的 `last_seen_at` / `last_seen_generation` 记报到；这里把升级前最后一次报到搬过去，
免得升级后到下一次心跳之前（最多一分钟）连接被判成离线。上报记录里的旧时间戳由下一次心跳去掉。
只搬「上报凭据就是当前这把、代次一致」的，其余留空（新代码同样判离线）。重跑只覆盖成同一个值。
"""
import json
from datetime import datetime, timezone

import frappe

CONNECTION = "Cowork ERP Connection"
CREDENTIAL = "Cowork Express Agent Credential"


def execute():
    if not frappe.db.has_column(CREDENTIAL, "last_seen_at"):
        return
    rows = frappe.db.sql(f"""SELECT c.name, c.token_generation, n.report_json, n.report_credential, n.report_generation
        FROM `tab{CREDENTIAL}` c JOIN `tab{CONNECTION}` n ON n.name = c.connection
        WHERE c.active_key IS NOT NULL AND c.active_key != '' AND c.revoked_at IS NULL""", as_dict=True)
    for row in rows:
        if row.report_credential != row.name or int(row.report_generation or 0) != int(row.token_generation or 0):
            continue
        try:
            seen = datetime.fromisoformat(str(json.loads(row.report_json or "{}").get("agent_last_seen_at") or ""))
        except (TypeError, ValueError, AttributeError):
            continue
        if seen.tzinfo is None:
            continue
        frappe.db.set_value(CREDENTIAL, row.name, {
            "last_seen_at": seen.astimezone(timezone.utc).replace(tzinfo=None),
            "last_seen_generation": int(row.token_generation)}, update_modified=False)
