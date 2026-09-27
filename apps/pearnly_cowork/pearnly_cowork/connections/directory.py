"""一条连接当前的账套目录（服务端存着的那份），给系统内部读：识别后自动挑账套、LINE 卡片写账套名。

调用方已按入口授权（识别落库、推卡片），这里不做会话权限判断、不加锁、不刷新目录。
"""
from pearnly_cowork.targets.identity import normalize_express_account_key
from . import model
from .state import CONNECTION, SNAPSHOT, heads, read, scope


def current(connection):
    """(adapter, 当前目录的账套行)；还没有目录或目录不属于这条连接当前版本 → 空列表。"""
    doc = read(CONNECTION, connection, lock=False)
    _, _, key = scope(None)
    head = heads(doc).get(key, {})
    if not head.get("snapshot_id"):
        return doc.adapter, []
    snapshot = read(SNAPSHOT, head["snapshot_id"], lock=False)
    if snapshot.connection != doc.name or snapshot.connection_revision != doc.record_revision:
        return doc.adapter, []
    return doc.adapter, model.parse(snapshot.body).get("account_sets") or []


def account_label(connection, account_set_key):
    """账套键 → 界面上的名字：Express「年度文件夹 · 登录列表名」，MR.ERP「公司 · 年度」；找不到返 ''。"""
    if not connection or not account_set_key:
        return ""
    adapter, rows = current(connection)
    key = normalize_express_account_key(account_set_key) if adapter == "express" else str(account_set_key).strip()
    for row in rows:
        if not isinstance(row, dict) or str(row.get("source_id") or "") != key:
            continue
        attributes = row.get("attributes") or {}
        parts = ((attributes.get("root_label"), row.get("label")) if adapter == "express"
                 else (attributes.get("company"), row.get("label")))
        return " · ".join(str(part).strip() for part in parts if str(part or "").strip())
    return ""
