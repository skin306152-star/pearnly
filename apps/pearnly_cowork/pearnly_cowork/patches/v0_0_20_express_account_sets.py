"""CC 桌面版多台电脑第 2 批（ADR-021，方案 4.1 第 2–3 步）：Express 连接的老数据进池。

2. 账套登记：从当前端点快照和上报记录的 ``reported_account_sets`` 播种，**规范键 = 今天的 ``source_id``，规范根 = 今天
   的根**；建旧电脑（这条连接当前上报那把密钥所在的电脑）的可达行。未了结的推送记录、启用的定时任务引用到、目录里
   已经没有的键也补成登记（没有可达行，保证引用不悬空）。
3. 账套资料：默认账套那份、旧的 ``_account_caches``、各账套目录快照里按账套读回的那份，按账套搬进账套资料。
   旧电脑接过连接配置里它当时选的账套与 6 个科目映射（按连接存在电脑行上）、它的版本。

另：这些连接上每把有效的 Express 密钥打上「整份重报」（`full_report_due`，第 1 批的同一个标记）：上线后每台电脑的下一次
心跳不许只报「名单没变」，各自整份报一次，可达行以电脑此刻的名单为准（补丁按的是上一次存下的目录）。

**不动**：连接配置 ``config_json``（Express 的默认账套由代码中和）、``record_revision``、目录快照、推送记录的各种键、
密钥的 ``active_key``。幂等：已登记的、已有资料的、已经报过新名单的电脑都不再动；整份重报收到一次就清（重跑最多让每台
电脑多整份报一次）。
"""
from datetime import datetime, timezone

import frappe

CONNECTION = "Cowork ERP Connection"
CREDENTIAL = "Cowork Express Agent Credential"
DEVICE = "Cowork CC Desktop Device"
TABLES = ("Cowork Express Account Set", "Cowork Express Reach", "Cowork Express Account Facts")


def _old_computer(connection, report_credential):
    """旧电脑：当前上报那把有效密钥所在的电脑；没有就取这条连接上最早的一把有效密钥的电脑。"""
    rows = frappe.db.sql(f"""SELECT name, device FROM `tab{CREDENTIAL}` WHERE connection=%s AND revoked_at IS NULL
        AND COALESCE(active_key,'')<>'' AND COALESCE(device,'')<>'' ORDER BY creation, name""", (connection,),
        as_dict=True)
    for row in rows:
        if row.name == report_credential:
            return row.device
    return rows[0].device if rows else None


def _referenced(connection):
    """未了结的推送记录（不是成功、也不是作废）与启用的定时任务引用到的账套键 → 推送时冻结的根（定时任务不知道根）。"""
    import json
    from pearnly_core.express_rules.targets.identity import normalize_express_account_key as identity

    found = {}
    for row in frappe.db.sql("""SELECT account_key, target_json FROM `tabCowork Push Record`
            WHERE connection=%s AND status NOT IN ('success', 'cancelled') ORDER BY creation, name""", (connection,),
            as_dict=True):
        try:
            target = json.loads(row.target_json or "{}")
        except ValueError:
            target = {}
        key = identity(row.account_key or (target.get("account_set") if isinstance(target, dict) else ""))
        if key:
            found.setdefault(key, str((target.get("root_key") if isinstance(target, dict) else "") or ""))
    for row in frappe.db.sql("""SELECT account_set_key FROM `tabCowork Folder Intake Rule`
            WHERE connection=%s AND enabled=1""", (connection,), as_dict=True):
        key = identity(row.account_set_key)
        if key:
            found.setdefault(key, "")
    return found


def execute():
    if (not all(frappe.db.table_exists(table) for table in TABLES)
            or not frappe.db.has_column(DEVICE, "selection_json") or not frappe.db.has_column(CREDENTIAL, "full_report_due")):
        return
    from pearnly_cowork.companion import devices
    from pearnly_cowork.connections import model, pool

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in frappe.db.sql(f"""SELECT name, report_credential, config_json, report_json FROM `tab{CONNECTION}`
            WHERE adapter='express' AND COALESCE(binding_generation,0)=0 ORDER BY creation, name""", as_dict=True):
        device = _old_computer(row.name, row.report_credential)
        referenced = _referenced(row.name)
        pool.seed_legacy(row.name, device=device, referenced=referenced, now=now, protected=set(referenced))
        if device:
            devices.seed_state(device, row.name, model.parse(row.config_json, {}) or {}, model.parse(row.report_json, {}) or {})
        frappe.db.sql(f"""UPDATE `tab{CREDENTIAL}` SET full_report_due=1 WHERE connection=%s AND revoked_at IS NULL
            AND COALESCE(active_key,'')<>'' AND COALESCE(full_report_due,0)=0""", (row.name,))
