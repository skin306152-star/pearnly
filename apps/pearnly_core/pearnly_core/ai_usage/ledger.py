"""写账：本站的 `Pearnly AI Usage`。

- 单次调用（标题、代录、识别）：调用方拿到用量后调 :func:`record`，**只排一个后台任务**（Redis 入队，毫秒级），任务里插行并提交。
  这样写账不拖慢调用方，也不跟调用方的事务走：识别付了费、后面的步骤出错回滚，这笔账照样记下。
- CC 对话：每轮结束（转发线程收线、引擎回调叫醒）由 `assistant.usage` 排任务读一次会话的累计用量，交 :func:`write_session`
  写「当天增量」到这段远端会话今天那一行（`cc-<远端会话>-<日期>`，反复覆盖，幂等）。
- 一切出错只记日志，不打断业务（账是附带的）。
"""
import frappe

from pearnly_core.ai_usage import contracts

DOCTYPE = "Pearnly AI Usage"
JOB = "pearnly_core.ai_usage.ledger"
QUEUE = "short"
JOB_TIMEOUT = 120
EARLIER_DAYS = 1000  # 一段远端会话最多跨这么多天（实际几天）


def _who() -> str:
    try:
        user = frappe.session.user
    except AttributeError:
        return ""
    return "" if user in (None, "", "Guest") else str(user)


def record(purpose: str, rows: list[dict], user: str | None = None, conversation: str = "", segment: str = "",
           ref: str = "") -> None:
    """排一个任务把这几行记进账（``rows`` 由 `contracts.call` / `contracts.ocr_stages` 给）。没有站点上下文、没有行就不记。"""
    if purpose not in contracts.PURPOSES or not rows:
        return
    try:
        if not getattr(frappe.local, "site", None):
            return
        base = {"purpose": purpose, "user": user if user is not None else _who(), "conversation": conversation or "",
                "segment": segment or "", "ref": str(ref or "")[:140]}
        frappe.enqueue(f"{JOB}.write", queue=QUEUE, timeout=JOB_TIMEOUT, enqueue_after_commit=False,
                       rows=[{**base, **row} for row in rows], day=frappe.utils.nowdate())
    except Exception:  # noqa: BLE001 — 账是附带的
        _log("record")


def _site_columns() -> dict:
    return {"product": str(frappe.conf.get("pearnly_product") or ""),
            "tenant": str(frappe.conf.get("pearnly_tenant") or frappe.local.site or "")}


def _doc(row: dict, day: str) -> dict:
    keep = ("purpose", "user", "conversation", "segment", "ref", "model", "usd_source") + contracts.METRICS
    values = {k: row.get(k) for k in keep}
    values["usd"] = None if values.get("usd") is None else str(values["usd"])  # Decimal 原样进 decimal 列，不经过浮点
    if values.get("user") and not frappe.db.exists("User", values["user"]):
        values["user"] = None
    return {"doctype": DOCTYPE, "day": day, **_site_columns(), **values}


def write(rows: list[dict], day: str) -> None:
    """后台任务：插入单次调用的行。"""
    for row in rows:
        frappe.get_doc(_doc(row, day)).insert(ignore_permissions=True)
    frappe.db.commit()


def write_session(conversation: str, remote: str, user: str, session) -> None:
    """CC 对话：按这段远端会话的累计用量（Managed Agents 会话对象，`contracts.session_snapshot`）覆盖写今天那一行
    （今天 = 累计 − 别的日子已记的），并提交。没有用量就不记。由 `assistant.usage.sync` 在后台任务里调。"""
    snapshot = contracts.session_snapshot(session)
    if not snapshot:
        return
    day = frappe.utils.nowdate()
    name = f"cc-{remote}-{day}"
    earlier = frappe.get_all(DOCTYPE, filters={"purpose": "cc_chat", "segment": remote, "name": ["!=", name]},
                             fields=list(contracts.METRICS), limit_page_length=EARLIER_DAYS)
    row = contracts.day_delta(snapshot, earlier)
    doc = _doc({"purpose": "cc_chat", "user": user, "conversation": conversation, "segment": remote, **row}, day)
    if frappe.db.exists(DOCTYPE, name):
        frappe.db.set_value(DOCTYPE, name, {k: v for k, v in doc.items() if k != "doctype"}, update_modified=True)
    else:
        frappe.get_doc(doc).insert(ignore_permissions=True, set_name=name, ignore_if_duplicate=True)
    frappe.db.commit()


def _log(where: str) -> None:
    try:
        frappe.logger("pearnly.ai_usage").warning("ai usage %s failed", where, exc_info=True)
    except Exception:  # noqa: BLE001
        pass
