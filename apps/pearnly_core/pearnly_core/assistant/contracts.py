"""智能体对话的纯约定（ADR-019）：编号、消息、批准与事件增量的校验。

不依赖 Frappe、网络或 SDK，离线测试直接加载本文件。会话归谁、能不能看，由 `sessions` 按
站点数据库判定；这里只保证进出的形状不越界。编号由引擎生成，各家格式不同，这里只限定为安全的不透明字符串；
事件沿用统一对话事件的形状（见 `engines.base`）。
"""
import re

# 会话、事件、文件编号：引擎生成的不透明编号。
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}$")
TITLE_MAX = 60
# 首条消息比这长才概括成短标题（`summary_source`）；短的原文本身就是标题，不多花一次模型调用。
TITLE_SHORT = 12
# 概括时只给模型看开头这么多字：标题只看开头就够，输入短、花费小。
TITLE_SOURCE_MAX = 1_000
TEXT_MAX = 20_000
DENY_MAX = 2_000
TOOL_RESULT_MAX = 50_000
# 增量拉取一次最多回溯这么多条；超过说明客户端离线太久，改为整段重放。
TAIL_MAX = 500
# 附件：票据照片、PDF、表格、文档。限制类型和大小，不让可执行文件或大视频进沙箱和文件额度。
UPLOAD_MAX_BYTES = 30 * 1024 * 1024
UPLOAD_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".pdf",
                               ".xlsx", ".xls", ".csv", ".tsv", ".docx", ".txt", ".md", ".json"})


# 侧栏列表的翻页位置：上一页最后一条的「最后活动时间|记录名」（同一时间的按记录名再排一次，不漏不重）。
LIST_CURSOR = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)\|([A-Za-z0-9_-]{1,140})$")


class ContractError(ValueError):
    pass


def session_id(value) -> str:
    text = str(value or "").strip()
    if not OPAQUE_ID.match(text):
        raise ContractError("invalid session id")
    return text


def list_cursor(value) -> tuple[str, str]:
    match = LIST_CURSOR.match(str(value or "").strip())
    if not match:
        raise ContractError("invalid list cursor")
    return match.group(1), match.group(2)


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


# 对话页给智能体附在消息末尾的附件说明（frontend/assistant/src/lib/managed-agents/events.ts）：标题、接续的前文里都不要它。
ATTACHMENT_NOTE = re.compile(r"(?:^|\n\n)\[Attached files?, mounted read-only in the sandbox:\n((?:- .+ → .+\n?)+)\]$")


def title_from(text) -> str:
    """标题取用户原文；只发了附件就用第一个附件的文件名。"""
    text = str(text or "")
    note = ATTACHMENT_NOTE.search(text)
    if note:
        body = text[:note.start()]
        first = re.match(r"- (.+) → ", note.group(1))
        text = body if body.strip() else (first.group(1) if first else "")
    return " ".join(text.split())[:TITLE_MAX]


def summary_source(text) -> str:
    """要概括成短标题的原文（去掉附件说明、合并空白）；原文已经够短或只有附件时回空串，直接用 `title_from`。"""
    text = str(text or "")
    note = ATTACHMENT_NOTE.search(text)
    body = " ".join((text[:note.start()] if note else text).split())
    return body[:TITLE_SOURCE_MAX] if len(body) > TITLE_SHORT else ""


# 模型给的标题常带引号、书名号、句末标点或「标题：」前缀，这里统一去掉。
TITLE_WRAP = re.compile(r"^[\s\"'“”‘’「」『』《》*#`]+|[\s\"'“”‘’「」『』《》*#`。．.!！?？,，、;；:：]+$")
# 「title / 标题 / 題名 / ชื่อเรื่อง:」前缀，用转义写（这是模型输出的清理规则，不是界面文案）
TITLE_LABEL = re.compile(r"^(?:title|\u6807\u9898|\u984c\u540d|\u0e0a\u0e37\u0e48\u0e2d\u0e40\u0e23\u0e37\u0e48\u0e2d\u0e07)\s*[:\uff1a]\s*", re.I)


def short_title(value) -> str:
    """模型概括出的标题 → 侧栏标题：只取第一行，去掉包装与前缀；不像标题（空的、过长的）回空串，由调用方保留原标题。"""
    line = next((part for part in str(value or "").splitlines() if part.strip()), "")
    line = TITLE_WRAP.sub("", TITLE_LABEL.sub("", TITLE_WRAP.sub("", line)))
    line = " ".join(line.split())
    return line if 0 < len(line) <= TITLE_MAX else ""


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


def newer_than(events_newest_first, after: str, keep_anchor: bool = False):
    """从「新 → 旧」的事件流里取出 `after` 之后的事件（`keep_anchor` 时连同它本身），按「旧 → 新」返回。

    找不到 `after`（客户端掉线太久或编号不属于本会话）返回 None，调用方改为整段重放。
    """
    newer = []
    for event in events_newest_first:
        if event.get("id") == after:
            if keep_anchor:
                newer.append(event)
            newer.reverse()
            return newer
        newer.append(event)
        if len(newer) >= TAIL_MAX:
            return None
    return None


def since(events_newest_first, target: str):
    """从「新 → 旧」的事件流里取出 `target` 本身及其后的事件，按「旧 → 新」返回；找不到返回 None。"""
    return newer_than(events_newest_first, target, keep_anchor=True)


# 引擎回调（ADR-019：页面关着也由服务端接手工具调用）按 Standard Webhooks 签名：
# 签名内容 `{webhook-id}.{webhook-timestamp}.{原文}`，密钥是 `whsec_` 后的 base64，HMAC-SHA256 后 base64，
# 头里可带多个 `v1,<签名>`（换密钥期间）。时间戳超过 5 分钟的拒收（防重放）。
WEBHOOK_TOLERANCE = 300


def verify_webhook(secret: str, headers: dict, body: str, now: float) -> None:
    """验不过抛 ContractError。`headers` 的键不分大小写。"""
    import base64
    import hashlib
    import hmac

    lower = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    msg_id, stamp, signatures = lower.get("webhook-id"), lower.get("webhook-timestamp"), lower.get("webhook-signature")
    if not (secret and msg_id and stamp and signatures):
        raise ContractError("missing webhook signature")
    try:
        sent_at = int(stamp)
        key = base64.b64decode(secret[len("whsec_"):] if secret.startswith("whsec_") else secret)
    except ValueError:
        raise ContractError("invalid webhook signature") from None
    if abs(now - sent_at) > WEBHOOK_TOLERANCE:
        raise ContractError("webhook timestamp out of range")
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{stamp}.{body}".encode(), hashlib.sha256).digest()).decode()
    for item in signatures.split():
        version, _, value = item.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return
    raise ContractError("invalid webhook signature")


SITE_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def site_host(value) -> bool:
    """回调转发的目标只能是一个普通域名（站点名）：不带协议、端口、路径。"""
    return bool(SITE_HOST.match(str(value or "").strip().lower()))


def pending_calls(events_newest_first) -> list[dict]:
    """最近一次「空闲、等待处理」所等的自定义工具调用（从「新 → 旧」的事件里找，找齐即停）。
    最近一次空闲不是在等（已结束 / 已在跑），回空。"""
    wanted, found = None, {}
    for event in events_newest_first:
        kind = event.get("type")
        if wanted is None:
            if kind == "session.status_running":
                return []
            if kind == "session.status_idle":
                stop = event.get("stop_reason") or {}
                if stop.get("type") != "requires_action":
                    return []
                wanted = [str(i) for i in stop.get("event_ids") or []]
                if not wanted:
                    return []
            continue
        if kind == "agent.custom_tool_use" and event.get("id") in wanted:
            found[event["id"]] = event
            if len(found) == len(wanted):
                break
    return [found[i] for i in wanted or [] if i in found]


def answered_tool_uses(events) -> set[str]:
    """已经回过结果的自定义工具调用（防止同一次调用被执行两遍）。"""
    return {e.get("custom_tool_use_id") for e in events if e.get("type") == "user.custom_tool_result"}


# 接续新上下文时带过去的前文（见 `sessions` 的接续）：只取双方的文字，去掉附件说明（旧附件在新上下文里读不到）。
CARRY_MAX_CHARS = 24_000
CARRY_INTRO = ("This conversation continues from an earlier context that was closed to keep it short. "
               "Files attached earlier are no longer readable unless the user attaches them again. "
               "Earlier messages, most recent last:\n\n")


def _text(event) -> str:
    return "".join(b.get("text", "") for b in event.get("content") or [] if b.get("type") == "text").strip()


def carry_over(events, max_chars: int = CARRY_MAX_CHARS) -> str:
    """按顺序取用户与智能体的文字，只保留最近、合计不超过 `max_chars` 的部分。"""
    lines = []
    for event in events:
        kind = event.get("type")
        if kind not in ("user.message", "agent.message"):
            continue
        text = ATTACHMENT_NOTE.sub("", _text(event)).strip() if kind == "user.message" else _text(event)
        if text:
            lines.append(("User: " if kind == "user.message" else "Assistant: ") + text)
    kept, size = [], 0
    for line in reversed(lines):
        if size + len(line) > max_chars:
            if not kept:
                kept.append(line[-max_chars:])
            break
        kept.append(line)
        size += len(line) + 2
    return "\n\n".join(reversed(kept))


def carry_message(transcript: str) -> dict:
    return {"type": "system.message", "content": [{"type": "text", "text": CARRY_INTRO + transcript}]}


def context_size(events):
    """最近一次模型请求的输入 token 数（含缓存读写，计价分档按它算）；事件里没有就返回 None。"""
    size = None
    for event in events:
        if event.get("type") == "span.model_request_end":
            usage = event.get("model_usage") or {}
            size = sum(int(usage.get(k) or 0) for k in
                       ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return size


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


def deep_link(session_id="") -> str:
    """提醒点开去哪（手机推送、电脑通知共用）：有合法对话编号就打开那段对话，否则打开定时任务页。"""
    from urllib.parse import quote

    text = str(session_id or "").strip()
    if OPAQUE_ID.match(text):
        return "/assistant?c=" + quote(text, safe="")
    return "/assistant?view=tasks"
