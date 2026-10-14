"""Claude Managed Agents 适配器（ADR-019）。

- 智能体与运行环境不让人配编号：按站点密钥在其所属账号里按元数据找，找不到才建，说明变了就更新——
  同一产品在同一账号里始终只有一个智能体；密钥换成另一个账号时自动在新账号里备好。
- 统一对话事件就是 Managed Agents 的事件格式，这里原样进出。
- 附件存进 Anthropic 文件库，只读挂进会话沙箱；智能体写到 `/mnt/session/outputs/` 的文件作为结果列出。
"""
import hashlib
import json

import anthropic

from pearnly_core.assistant.engines import base

BETA = "managed-agents-2026-04-01"
# 思考强度：所有模型默认最低档（用户 2026-10-10）；四个可选模型都实测接受 low。
EFFORT = "low"
MANAGED_BY = "pearnly-assistant"
ENVIRONMENT = {"name": "pearnly-assistant", "config": {"type": "cloud", "networking": {"type": "limited"}}}
UPLOADS = "/mnt/session/uploads"
OUTPUTS = "/mnt/session/outputs"
# 产出约定只与本引擎的沙箱有关，所以写在适配器里：写进这个目录的文件才会出现在对话页右侧的结果面板。
WORKSPACE = f"""

Files in this workspace:
- Files the user attaches are in {UPLOADS}/ and are read-only.
- When the user asks for a table, list, report, spreadsheet, document, chart or any other result to keep, \
create it as a file in {OUTPUTS}/ (tables and lists as .xlsx, documents as .docx or .pdf, charts and pages as .html) \
and reply with a short summary of what the file contains instead of repeating its full content.
- When you mention a file, give only its file name (the user clicks it in the chat or opens it from the panel beside \
the chat, so do not call it a folder); never show paths such as {OUTPUTS} or {UPLOADS}.
- In spreadsheets write the calculated values; use formulas only when the user asks for them.
- Files can be large. Check a file's size first (pages, rows, bytes); never open a whole PDF of more than a few pages, \
a whole spreadsheet or a long text file with the read tool or by printing it. Extract it with a script in the sandbox \
into a working file (for example pdftotext, pypdf, openpyxl) and print only the totals, matches and the few rows you \
need, a few pages or a few hundred lines at a time. Never retype file contents yourself; let scripts copy and convert \
them."""
# 浏览器等着这些请求，不能挂太久；SDK 自己会对 429 / 5xx / 超时重试。
TIMEOUT = 30
RETRIES = 2
# 「列出事件」偶尔单次要 30 多秒才返回、换一次请求又是零点几秒（2026-10-10 测试机与本机同样实测），
# 所以单次只等几秒，超时由 SDK 立即重发。
LIST_TIMEOUT = 6
LIST_RETRIES = 3
# 事件流两条事件之间最长等这么久；模型长时间思考时超时由转发线程查状态后重接。
STREAM_TIMEOUT = 120
# 侧栏标题只要几个词（泰文、中日文每个字的 token 多些，留足）；后台任务里调用，等不到就放弃（保留临时标题）。
TITLE_TOKENS = 60
TITLE_TIMEOUT = 15


def _plain(obj) -> dict:
    return obj.to_dict(mode="json")


def _gone(err) -> bool:
    """会话在 Anthropic 那边已不存在（删了 / 过期 / 编号不对）。"""
    return isinstance(err, anthropic.APIStatusError) and err.status_code in (400, 404, 410)


def spec(brief: dict, model: str) -> dict:
    body = {
        "name": brief["name"],
        "model": {"id": model, "effort": EFFORT},
        "system": brief["system"] + WORKSPACE,
        "tools": [
            # 沙箱自带的读写文件、执行命令；联网工具关闭，业务数据只经 Pearnly 工具取得。
            # 一律放行（用户 2026-10-11）：auto 由平台逐次评估每个调用，10-10 测试夜每次约 4.5 万 token 的 Sonnet 用量；
            # 沙箱是本会话独享的、没有凭据、网络受限。Pearnly 工具不受这项管（自定义工具由站点执行），
            # 要授权的仍走站点自己的「允许」（`tools.needs_approval` → `toolrun.decide`）。
            {"type": "agent_toolset_20260401",
             "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
             "configs": [{"name": "web_fetch", "enabled": False}, {"name": "web_search", "enabled": False}]},
            *({"type": "custom", **tool} for tool in brief["tools"]),
        ],
    }
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    body["metadata"] = {"pearnly_managed": MANAGED_BY, "pearnly_product": brief["product"],
                        "pearnly_spec": fingerprint}
    return body


class Adapter(base.Adapter):
    def __init__(self, api_key: str, model: str):
        super().__init__(api_key, model)
        self.api = anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT, max_retries=RETRIES)

    def shape(self, brief: dict) -> str:
        return spec(brief, self.model)["metadata"]["pearnly_spec"]

    def prepare(self, brief: dict) -> dict:
        return {"agent": self._agent(spec(brief, self.model)), "environment": self._environment()}

    def _agent(self, body: dict) -> str:
        meta_wanted = body["metadata"]
        for agent in self.api.beta.agents.list():
            meta = agent.metadata or {}
            if meta.get("pearnly_managed") == MANAGED_BY and meta.get("pearnly_product") == meta_wanted["pearnly_product"]:
                if meta.get("pearnly_spec") != meta_wanted["pearnly_spec"]:
                    self.api.beta.agents.update(agent.id, **body)
                return agent.id
        return self.api.beta.agents.create(**body).id

    def _environment(self) -> str:
        for environment in self.api.beta.environments.list():
            if environment.name == ENVIRONMENT["name"]:
                return environment.id
        return self.api.beta.environments.create(**ENVIRONMENT).id

    def open(self, handle: dict, metadata: dict) -> tuple[str, str]:
        session = self.api.beta.sessions.create(agent=handle["agent"], environment_id=handle["environment"],
                                                title="", metadata=metadata)
        return session.id, session.status

    def refresh(self, conversation: str, brief: dict) -> bool:
        # 会话建好后绑定建时的智能体版本、系统提示词不能再改；工具可以整组替换（官方「会话中更新智能体配置」）。
        self.api.beta.sessions.update(conversation, agent={"tools": spec(brief, self.model)["tools"]})
        return True

    def status(self, conversation: str) -> str:
        return self.api.beta.sessions.retrieve(conversation).status

    def summarize(self, text: str, model: str) -> str | None:
        reply = self.api.with_options(timeout=TITLE_TIMEOUT).messages.create(
            model=model or self.model, max_tokens=TITLE_TOKENS, system=base.TITLE_INSTRUCTION,
            messages=[{"role": "user", "content": text}])
        return "".join(getattr(part, "text", "") for part in reply.content) or None

    # 控制台 Manage → Webhooks 订阅这几类；通知只带类型与会话编号，等的是哪些调用再读事件列表（`contracts.pending_calls`）
    WAKE_EVENTS = ("session.status_idled", "session.requires_action", "session.idled")

    @classmethod
    def wake(cls, event: dict) -> str | None:
        data = event.get("data") or {}
        return str(data.get("id") or "") or None if data.get("type") in cls.WAKE_EVENTS else None

    def metadata(self, conversation: str) -> dict:
        return dict(self.api.beta.sessions.retrieve(conversation).metadata or {})

    def rename(self, conversation: str, title: str) -> None:
        self.api.beta.sessions.update(conversation, title=title)

    def archive(self, conversation: str) -> None:
        self.api.beta.sessions.events.send(conversation, events=[{"type": "user.interrupt"}])
        self.api.beta.sessions.archive(conversation)

    def delete(self, conversation: str) -> None:
        try:
            self.api.beta.sessions.delete(conversation)
        except Exception as err:
            if not _gone(err):
                raise

    def events(self, conversation: str, newest_first: bool = False):
        order = "desc" if newest_first else "asc"
        listing = self.api.with_options(timeout=LIST_TIMEOUT, max_retries=LIST_RETRIES).beta.sessions.events
        return (_plain(e) for e in listing.list(conversation, order=order))

    def stream(self, conversation: str, opened):
        # 带上回复的逐字增量（只在流上、不入事件记录），对话页边生成边显示。
        with self.api.beta.sessions.events.stream(conversation, event_deltas=["agent.message", "agent.thinking"],
                                                  timeout=STREAM_TIMEOUT) as events:
            opened()
            for event in events:
                yield _plain(event)

    def post(self, conversation: str, events: list[dict]) -> None:
        self.api.beta.sessions.events.send(conversation, events=events)

    def attach(self, conversation: str, filename: str, content: bytes, mime: str) -> dict:
        stored = self.api.files.upload(file=(filename, content, mime))
        # 按文件编号分目录：同名文件不互相覆盖，最后一段保留可读的名字。
        path = f"{UPLOADS}/{stored.id}/{filename}"
        self.api.beta.sessions.resources.add(conversation, type="file", file_id=stored.id, mount_path=path)
        return {"file_id": stored.id, "path": path}

    def files(self, conversation: str) -> list[dict]:
        found = {}
        for f in self.api.beta.files.list(scope_id=conversation, betas=[BETA]):
            found[f.id] = {"id": f.id, "filename": f.filename, "size_bytes": f.size_bytes, "mime_type": f.mime_type,
                           "downloadable": bool(getattr(f, "downloadable", False)), "created_at": str(f.created_at),
                           "kind": "output"}
        for r in self.api.beta.sessions.resources.list(conversation):
            if getattr(r, "type", "") != "file" or r.file_id in found:
                continue
            found[r.file_id] = {"id": r.file_id, "filename": (r.mount_path or "").rsplit("/", 1)[-1] or r.file_id,
                                "size_bytes": 0, "mime_type": "application/octet-stream", "downloadable": False,
                                "created_at": str(getattr(r, "created_at", "")), "kind": "upload"}
        return list(found.values())

    def read_file(self, conversation: str, file_id: str) -> bytes:
        return self.api.files.download(file_id).read()
