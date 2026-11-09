"""被停掉的半段回复（批 A ④，2026-11-09；主会话定只追加的存法）。

点了停止，引擎不会为被打断的那条回复出正式的 `agent.message`：逐字预览只在页面内存里，刷新、换设备就没了（对照文档第 4 项）。
推送线程（`relay`）一直收着逐字，这一轮确实被停了、那一段又没有正式回复时，在这一轮结束时记一次（`record`），不边收边写。
读事件时并成本站事件 `pearnly.stopped`（同提醒的并法，`feed`），页面据此在那条回复上标「已停止」、给「重试」；只给页面看，
不交给模型。表 `Pearnly Assistant Stopped Reply` 只插入、不改，不碰对话那一行（那一行已有好几处并发写，11-09 两次 1020）。
每段对话只留最近 `KEEP` 段，超出的由插入的地方顺手删掉，不另做定时清理；删对话时一起删（`forget`）。
"""
import frappe

from pearnly_core.assistant import contracts

DOCTYPE = "Pearnly Assistant Stopped Reply"
KEEP = 20
TYPE = "pearnly.stopped"
TEXT_MAX = 20000  # 一段最多存这么多字（逐字预览很少超过；超出的截掉）
FIELDS = ["name", "event_id", "text", "creation"]


def record(conversation: str, user: str, event_id: str, text: str) -> dict | None:
    """记一段被停掉的回复（一轮结束时，推送线程调），回它的本站事件（推给开着的页面）。空的不记；同一段（同一个事件编号）
    只记一次；超出 `KEEP` 段删最旧的。"""
    text = str(text or "").strip()
    if not conversation or not event_id or not text:
        return None
    if frappe.db.exists(DOCTYPE, {"session_id": conversation, "event_id": event_id}):
        return None
    doc = frappe.get_doc({"doctype": DOCTYPE, "session_id": conversation, "user": user, "event_id": event_id,
                          "text": text[:TEXT_MAX]}).insert(ignore_permissions=True)
    old = frappe.get_all(DOCTYPE, filters={"session_id": conversation}, order_by="creation desc, name desc",
                         pluck="name", limit_start=KEEP, limit_page_length=100)
    if old:
        frappe.db.delete(DOCTYPE, {"name": ["in", old]})
    return event(doc)


def event(row) -> dict:
    """一段记下的半段回复 → 本站事件（编号带前缀，不会撞引擎的事件编号；`event_id` 是原来那条回复的编号）。"""
    return {"id": f"pstop_{row.name}", "type": TYPE, "event_id": row.event_id,
            "content": [{"type": "text", "text": row.text}],
            "processed_at": contracts.utc_iso(row.creation, frappe.utils.get_system_timezone())}


def feed(conversation: str, since=None) -> list[dict]:
    """这段对话记下的半段回复，旧 → 新（整段重放给全部，增量补拉给锚点之后的，前推几分钟由页面按编号去重）。"""
    if not conversation:
        return []
    filters = {"session_id": conversation}
    if since is not None:
        from pearnly_core.assistant import notices

        filters["creation"] = [">=", contracts.local_time(since, frappe.utils.get_system_timezone()) - notices.FEED_SLACK]
    rows = frappe.get_all(DOCTYPE, filters=filters, fields=FIELDS, order_by="creation asc, name asc", limit_page_length=KEEP)
    return [event(row) for row in rows]


def forget(conversation: str) -> None:
    """删对话时一起删。"""
    if conversation:
        frappe.db.delete(DOCTYPE, {"session_id": conversation})


class Previews:
    """推送线程里这一轮的逐字预览（只在内存）：`event_start` 开一段、`event_delta` 接上、同编号的正式 `agent.message` 收掉；
    看到 `user.interrupt` 记下这一轮被停了。一轮结束时 `leftover` 给出被停掉、又没有正式回复的那几段。"""

    def __init__(self):
        self.open: dict[str, str] = {}
        self.stopped = False

    def see(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "event_start":
            inner = event.get("event") or {}
            if inner.get("type") == "agent.message" and inner.get("id"):
                self.open[str(inner["id"])] = ""
        elif kind == "event_delta":
            key = str(event.get("event_id") or "")
            content = ((event.get("delta") or {}).get("content") or {})
            if key in self.open and content.get("type") == "text":
                self.open[key] += str(content.get("text") or "")
        elif kind == "agent.message":
            self.open.pop(str(event.get("id") or ""), None)
        elif kind == "user.interrupt":
            self.stopped = True

    def leftover(self) -> list[tuple[str, str]]:
        """这一轮被停了：还没收掉的几段（编号、文字）；没停就空（出错没写完的不算「已停止」）。"""
        if not self.stopped:
            return []
        return [(key, text) for key, text in self.open.items() if text.strip()]
