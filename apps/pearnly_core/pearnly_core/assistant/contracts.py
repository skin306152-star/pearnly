"""智能体对话的纯约定（ADR-019）：编号、消息、批准与事件增量的校验。

不依赖 Frappe、网络或 SDK，离线测试直接加载本文件。会话归谁、能不能看，由 `sessions` 按
站点数据库判定；这里只保证进出的形状不越界。编号由引擎生成，各家格式不同，这里只限定为安全的不透明字符串；
事件沿用统一对话事件的形状（见 `engines.base`）。
"""
import re

# 会话、事件、文件编号：引擎生成的不透明编号。
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}$")
TITLE_MAX = 60
TEXT_MAX = 20_000
DENY_MAX = 2_000
TOOL_RESULT_MAX = 50_000
# 增量拉取一次最多回溯这么多条；超过说明客户端离线太久，改为整段重放。
TAIL_MAX = 500
# 附件：票据照片、PDF、表格、文档。限制类型和大小，不让可执行文件或大视频进沙箱和文件额度。
UPLOAD_MAX_BYTES = 30 * 1024 * 1024
UPLOAD_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".pdf",
                               ".xlsx", ".xls", ".csv", ".tsv", ".docx", ".txt", ".md", ".json"})


class ContractError(ValueError):
    pass


def session_id(value) -> str:
    text = str(value or "").strip()
    if not OPAQUE_ID.match(text):
        raise ContractError("invalid session id")
    return text


def event_id(value) -> str:
    text = str(value or "").strip()
    if not OPAQUE_ID.match(text):
        raise ContractError("invalid event id")
    return text


def message_text(value) -> str:
    text = str(value or "").strip()
    if not text:
        raise ContractError("message is empty")
    if len(text) > TEXT_MAX:
        raise ContractError("message too long")
    return text


def title_from(text) -> str:
    return " ".join(str(text or "").split())[:TITLE_MAX]


def confirmations(items) -> list[dict]:
    """浏览器点的「允许 / 拒绝」→ `user.tool_confirmation` 事件；同一批一次发出。"""
    if not isinstance(items, list) or not items:
        raise ContractError("confirmations must be a non-empty list")
    events = []
    for item in items:
        if not isinstance(item, dict) or item.get("result") not in ("allow", "deny"):
            raise ContractError("each confirmation needs tool_use_id and result allow|deny")
        event = {"type": "user.tool_confirmation", "tool_use_id": event_id(item.get("tool_use_id")),
                 "result": item["result"]}
        note = str(item.get("deny_message") or "").strip()
        if item["result"] == "deny" and note:
            event["deny_message"] = note[:DENY_MAX]
        events.append(event)
    return events


def tool_result(tool_use_id, text, is_error=False) -> dict:
    return {"type": "user.custom_tool_result", "custom_tool_use_id": event_id(tool_use_id),
            "content": [{"type": "text", "text": str(text or "")[:TOOL_RESULT_MAX]}],
            "is_error": bool(is_error)}


def newer_than(events_newest_first, after: str):
    """从「新 → 旧」的事件流里取出 `after` 之后的事件，按「旧 → 新」返回。

    找不到 `after`（客户端掉线太久或编号不属于本会话）返回 None，调用方改为整段重放。
    """
    newer = []
    for event in events_newest_first:
        if event.get("id") == after:
            newer.reverse()
            return newer
        newer.append(event)
        if len(newer) >= TAIL_MAX:
            return None
    return None


def answered_tool_uses(events) -> set[str]:
    """已经回过结果的自定义工具调用（防止同一次调用被执行两遍）。"""
    return {e.get("custom_tool_use_id") for e in events if e.get("type") == "user.custom_tool_result"}


def upload_name(filename, size) -> str:
    """校验附件的名字与大小，返回只含安全字符的文件名（扩展名保留原样小写）。"""
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    stem, dot, ext = name.rpartition(".")
    ext = ("." + ext.lower()) if dot else ""
    if ext not in UPLOAD_EXTENSIONS:
        raise ContractError("unsupported file type")
    if not isinstance(size, int) or size <= 0:
        raise ContractError("file is empty")
    if size > UPLOAD_MAX_BYTES:
        raise ContractError("file too large")
    safe = re.sub(r'[\s\\/:*?"<>|\x00-\x1f\x7f]+', "_", stem).strip("._") or "upload"
    return safe[:120] + ext


def file_id(value) -> str:
    text = str(value or "").strip()
    if not OPAQUE_ID.match(text):
        raise ContractError("invalid file id")
    return text
