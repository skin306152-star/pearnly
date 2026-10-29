#!/usr/bin/env python3
"""回归实验室用的假 Claude Managed Agents 服务（只用 Python 标准库）。

用途：CC 对话页的流程用例在实验室里跑时，模型调用这一层换成本服务，页面、站点接口、转发线程、
真实适配器 `pearnly_core/assistant/engines/anthropic_managed_agents.py` 照真的跑、一行不改——
适配器用 anthropic SDK（1.8.0）发出的请求全部打到这里。不调用任何付费模型、不连外网。

启动：

    python3 scripts/flows/lab_agents.py --port 18780                  # 只监听 127.0.0.1
    python3 scripts/flows/lab_agents.py --host 0.0.0.0 --port 18780   # 给实验室容器用

然后在跑站点的进程环境里设 `ANTHROPIC_BASE_URL=http://<本服务地址>:18780`；密钥随便填一个格式对的（本服务不验）。
`GET /healthz` 回 `{"ok": true}`，可用来等它起来。

模拟的接口（路径、查询参数、返回体照 SDK 1.8.0 的 `resources/beta`、`types/beta`）：
- 智能体 `/v1/agents`：列出（翻页）、新建、取一个、更新（元数据按键合并，值为 null 删键）、归档；
- 运行环境 `/v1/environments`：列出、新建、取一个、更新、删除、归档；
- 会话 `/v1/sessions`：新建（可带 resources、initial_events）、列出、取一个、更新（title、metadata、agent.tools）、
  归档、删除（删后再取回 404）；
- 会话事件 `/v1/sessions/{id}/events`：列出（order asc/desc、limit、page 游标、types、created_at[...] 过滤）、
  发送；`/events/stream`：SSE 事件流，带 `event_deltas=agent.message` 时先推 `event_start` / `event_delta` 预览；
- 会话资源 `/v1/sessions/{id}/resources`：挂文件、列出、取一个、删除；
- 文件 `/v1/files`：上传（multipart）、列出（`scope_id`、`ids` 过滤）、取元数据、下载 `/content`、删除；
- 单次问答 `POST /v1/messages`（起侧栏标题用）：回一段 stop_reason=end_turn 的短文字。

行为：
- 收到 `user.message`：先存这条用户事件并记 `session.status_running`（会话状态立即变 running），稍等 `--delay` 秒后
  记 `agent.message`（文字 = 固定前缀 + 用户原文前 40 个字），再记 `session.status_idle`（stop_reason end_turn）。
  同一批里的其他事件（`system.message` 等）照存。所有事件既进事件列表，也推给正连着的事件流；流在 idle 后
  保持连接（每 10 秒一次 ping），直到客户端断开、会话被删或到 `--stream-timeout` 秒。
- `user.interrupt`：照存，丢掉还没回的回复；正在 running 就补一条 idle（end_turn）。其他用户事件只存不回。
- 挂文件时照真实引擎的做法在会话范围里另存一份（另一个编号、不可下载），挂载记录指向那一份，
  会话范围的文件列表会列出它——适配器的 `files()` 要把它认成附件、不列成结果。
- 归档：记 `archived_at`，之后不再接受 `user.message`（400）；状态字段保持 idle。
- 所有状态只在内存里，进程重启就清空；编号按类自增，前缀仿真实写法（agent_、env_、sesn_、sevt_、sesrsc_、file_、msg_）。

它不做什么：
- 不模拟智能体的工具调用（`agent.tool_use`、`agent.custom_tool_use`、requires_action）、思考、沙箱与产出文件，
  也不发 `span.model_request_*`（没有 token 用量）、不发 webhook；`user.custom_tool_result`、`user.tool_confirmation`
  只存不触发后续回合。
- 不校验密钥、beta 头、工作区，不限流，不实现 `messages.create` 的流式与工具，不做智能体版本历史。
- 文件下载不看 `downloadable`（上传的文件也能下，便于站点读回）。
"""
from __future__ import annotations

import argparse
import email.parser
import email.policy
import json
import queue
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

REPLY_PREFIX = "[lab-agent]"
REPLY_CHARS = 40
DEFAULT_LIMIT = 20
MAX_LIMIT = 1000
PING_SECONDS = 10
DELTA_CHARS = 8

OPTIONS = {"delay": 0.3, "stream_timeout": 600.0, "prefix": REPLY_PREFIX}
LOCK = threading.RLock()
STORE = {"agents": {}, "environments": {}, "sessions": {}, "files": {}, "blobs": {}}
COUNTERS = {}
_LAST_STAMP = [datetime.fromtimestamp(0, timezone.utc)]


class ApiError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


def not_found(what: str, ident: str) -> ApiError:
    return ApiError(404, "not_found_error", f"{what} {ident} not found")


def bad_request(message: str) -> ApiError:
    return ApiError(400, "invalid_request_error", message)


def new_id(prefix: str) -> str:
    with LOCK:
        COUNTERS[prefix] = COUNTERS.get(prefix, 0) + 1
        return f"{prefix}_011LAB{COUNTERS[prefix]:018d}"


def stamp() -> str:
    """RFC3339 UTC 时间，严格递增（同一微秒内的事件也能按时间排序）。"""
    with LOCK:
        now = datetime.now(timezone.utc)
        if now <= _LAST_STAMP[0]:
            now = _LAST_STAMP[0] + timedelta(microseconds=1)
        _LAST_STAMP[0] = now
        return now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def merge_metadata(current: dict, change) -> dict:
    out = dict(current or {})
    for key, value in (change or {}).items():
        if value is None:
            out.pop(key, None)
        else:
            out[str(key)] = str(value)
    return out


# ---------- 对象形状（照 SDK 1.8.0 types/beta） ----------

def _effort(value):
    return {"type": value} if isinstance(value, str) else value


def _model(value) -> dict:
    model = {"id": value} if isinstance(value, str) else dict(value or {})
    if "effort" in model:
        model["effort"] = _effort(model["effort"])
    return model


def _toolset_config(config: dict) -> dict:
    out = {"permission_policy": {"type": "always_allow"}, **config}
    out.setdefault("type", out.get("name"))
    out.setdefault("enabled", True)
    return out


def _tools(tools) -> list:
    out = []
    for tool in tools or []:
        tool = dict(tool)
        if tool.get("type") == "agent_toolset_20260401":
            tool.setdefault("default_config", {"enabled": True, "permission_policy": {"type": "always_allow"}})
            tool["configs"] = [_toolset_config(c) for c in tool.get("configs") or []]
        out.append(tool)
    return out


def agent_object(body: dict, ident: str | None = None, old: dict | None = None) -> dict:
    now = stamp()
    agent = dict(old) if old else {"id": ident, "type": "agent", "created_at": now, "archived_at": None,
                                    "version": 0, "metadata": {}, "mcp_servers": [], "skills": [], "tools": [],
                                    "description": None, "system": None, "multiagent": None}
    for key in ("name", "description", "system", "multiagent"):
        if key in body:
            agent[key] = body[key]
    if "model" in body:
        agent["model"] = _model(body["model"])
    if "tools" in body:
        agent["tools"] = _tools(body["tools"])
    for key in ("mcp_servers", "skills"):
        if key in body:
            agent[key] = list(body[key] or [])
    if "metadata" in body:
        agent["metadata"] = merge_metadata(agent["metadata"], body["metadata"])
    agent["version"] += 1
    agent["updated_at"] = now
    return agent


def environment_object(body: dict, ident: str | None = None, old: dict | None = None) -> dict:
    now = stamp()
    env = dict(old) if old else {"id": ident, "type": "environment", "created_at": now, "archived_at": None,
                                  "description": None, "metadata": {}, "scope": None}
    if "name" in body:
        env["name"] = body["name"]
    if "description" in body:
        env["description"] = body["description"]
    if "scope" in body:
        env["scope"] = body["scope"]
    if "config" in body or not old:
        config = dict(body.get("config") or {"type": "cloud"})
        if config.get("type", "cloud") == "cloud":
            config["type"] = "cloud"
            networking = dict(config.get("networking") or {"type": "unrestricted"})
            if networking.get("type") == "limited":
                networking.setdefault("allow_mcp_servers", False)
                networking.setdefault("allow_package_managers", False)
                networking.setdefault("allowed_hosts", [])
            config["networking"] = networking
            config["packages"] = {"type": "packages", "apt": [], "cargo": [], "gem": [], "go": [], "npm": [],
                                  "pip": [], **(config.get("packages") or {})}
        env["config"] = config
    if "metadata" in body:
        env["metadata"] = merge_metadata(env["metadata"], body["metadata"])
    env["updated_at"] = now
    return env


def session_agent(agent: dict) -> dict:
    return {key: agent.get(key) for key in ("id", "description", "mcp_servers", "model", "multiagent", "name",
                                             "skills", "system", "tools", "type", "version")}


def file_object(ident: str, filename: str, mime: str, size: int, downloadable: bool, scope: str | None) -> dict:
    return {"id": ident, "type": "file", "created_at": stamp(), "filename": filename, "mime_type": mime,
            "size_bytes": size, "downloadable": downloadable, "expires_at": None,
            "scope": {"id": scope, "type": "session"} if scope else None}


def public_session(state: dict) -> dict:
    return {key: value for key, value in state.items() if not key.startswith("_")}


# ---------- 会话与事件 ----------

def get_session(ident: str) -> dict:
    state = STORE["sessions"].get(ident)
    if not state or state["_deleted"]:
        raise not_found("session", ident)
    return state


def publish(state: dict, event: dict, preview: bool = False) -> None:
    """记一条事件并推给正连着的流；`preview` 时先给要了 agent.message 预览的流推 event_start / event_delta。"""
    with LOCK:
        if "processed_at" not in event:
            event["processed_at"] = stamp()
        state["_events"].append(event)
        state["updated_at"] = event["processed_at"]
        subscribers = list(state["_streams"])
    for wanted, inbox in subscribers:
        if preview and event["type"] in wanted:
            inbox.put({"type": "event_start", "event": {"id": event["id"], "type": event["type"]}})
            text = "".join(b.get("text", "") for b in event.get("content") or [] if b.get("type") == "text")
            for start in range(0, len(text), DELTA_CHARS):
                inbox.put({"type": "event_delta", "event_id": event["id"],
                           "delta": {"type": "content_delta", "index": 0,
                                     "content": {"type": "text", "text": text[start:start + DELTA_CHARS]}}})
        inbox.put(event)


def set_status(state: dict, status: str) -> None:
    with LOCK:
        if status == "running" and state["status"] != "running":
            state["status"] = "running"
            publish(state, {"id": new_id("sevt"), "type": "session.status_running"})
        elif status == "idle" and state["status"] != "idle":
            state["status"] = "idle"
            publish(state, {"id": new_id("sevt"), "type": "session.status_idle", "stop_reason": {"type": "end_turn"}})


def _user_text(event: dict) -> str:
    return "".join(b.get("text", "") for b in event.get("content") or [] if isinstance(b, dict) and b.get("type") == "text")


def _turns(state: dict) -> None:
    """一个会话一条回复线程：按收到的顺序逐条回复，回完全部就记 idle。"""
    while True:
        time.sleep(OPTIONS["delay"])
        with LOCK:
            if state["_deleted"] or not state["_pending"]:
                state["_worker"] = None
                if not state["_deleted"]:
                    set_status(state, "idle")
                return
            text = state["_pending"].pop(0)
            reply = f"{OPTIONS['prefix']} {text[:REPLY_CHARS]}"
            publish(state, {"id": new_id("sevt"), "type": "agent.message",
                            "content": [{"type": "text", "text": reply}]}, preview=True)


def receive(state: dict, events: list) -> list:
    """存用户一侧发来的事件；有 user.message 就排一轮假回复。返回存下的事件（发送接口的 data）。"""
    if not isinstance(events, list) or not events:
        raise bad_request("events: expected a non-empty list")
    stored = []
    with LOCK:
        if state["archived_at"] and any(isinstance(e, dict) and e.get("type") == "user.message" for e in events):
            raise bad_request("session is archived")
        for raw in events:
            if not isinstance(raw, dict) or not str(raw.get("type") or ""):
                raise bad_request("event: missing type")
            event = {"id": new_id("sevt"), **{k: v for k, v in raw.items() if k != "id"}}
            kind = event["type"]
            if kind == "user.interrupt":
                state["_pending"].clear()
                publish(state, event)
                set_status(state, "idle")
            elif kind == "user.message":
                set_status(state, "running")
                publish(state, event)
                state["_pending"].append(_user_text(event))
                if not state["_worker"]:
                    state["_worker"] = threading.Thread(target=_turns, args=(state,), daemon=True)
                    state["_worker"].start()
            else:
                publish(state, event)
            stored.append(event)
    return stored


def add_resource(state: dict, body: dict) -> dict:
    if body.get("type") != "file":
        raise bad_request("only file resources are supported")
    original = STORE["files"].get(str(body.get("file_id") or ""))
    if not original:
        raise not_found("file", str(body.get("file_id")))
    # 照真实引擎：在会话范围里另存一份（另一个编号、不可下载），挂载记录指向那一份。
    copy_id = new_id("file")
    STORE["files"][copy_id] = file_object(copy_id, original["filename"], original["mime_type"],
                                          original["size_bytes"], False, state["id"])
    STORE["blobs"][copy_id] = STORE["blobs"][original["id"]]
    now = stamp()
    resource = {"id": new_id("sesrsc"), "type": "file", "file_id": copy_id,
                "mount_path": body.get("mount_path") or f"/mnt/session/uploads/{original['filename']}",
                "created_at": now, "updated_at": now}
    state["resources"].append(resource)
    return resource


def create_session(body: dict) -> dict:
    ref = body.get("agent")
    agent_id = ref if isinstance(ref, str) else (ref or {}).get("id")
    agent = STORE["agents"].get(str(agent_id or ""))
    if not agent:
        raise not_found("agent", str(agent_id))
    env_id = str(body.get("environment_id") or "")
    if env_id not in STORE["environments"]:
        raise not_found("environment", env_id)
    now = stamp()
    ident = new_id("sesn")
    state = {"id": ident, "type": "session", "agent": session_agent(agent), "archived_at": None,
             "budget": body.get("budget"), "created_at": now, "updated_at": now, "environment_id": env_id,
             "metadata": merge_metadata({}, body.get("metadata")), "outcome_evaluations": [], "resources": [],
             "stats": {"active_seconds": 0.0, "duration_seconds": 0.0}, "status": "idle",
             "title": body.get("title"), "usage": {"input_tokens": 0, "output_tokens": 0,
                                                    "cache_read_input_tokens": 0, "active_seconds": 0.0},
             "vault_ids": list(body.get("vault_ids") or []), "deployment_id": None,
             "_events": [], "_streams": [], "_pending": [], "_worker": None, "_deleted": False}
    STORE["sessions"][ident] = state
    for resource in body.get("resources") or []:
        add_resource(state, resource)
    if body.get("initial_events"):
        receive(state, list(body["initial_events"]))
    return state


def update_session(state: dict, body: dict) -> None:
    if "title" in body:
        state["title"] = body["title"]
    if "metadata" in body:
        state["metadata"] = merge_metadata(state["metadata"], body["metadata"])
    if "budget" in body:
        state["budget"] = body["budget"]
    if "vault_ids" in body:
        state["vault_ids"] = list(body["vault_ids"] or [])
    change = body.get("agent") or {}
    if "tools" in change:
        state["agent"]["tools"] = _tools(change["tools"])
    if "mcp_servers" in change:
        state["agent"]["mcp_servers"] = list(change["mcp_servers"] or [])
    state["updated_at"] = stamp()


def delete_session(state: dict) -> None:
    with LOCK:
        state["_pending"].clear()
        publish(state, {"id": new_id("sevt"), "type": "session.deleted"})
        state["_deleted"] = True
        for _wanted, inbox in state["_streams"]:
            inbox.put(None)


# ---------- 翻页与过滤 ----------

def _one(query: dict, name: str, default=None):
    values = query.get(name)
    return values[-1] if values else default


def page(items: list, query: dict) -> dict:
    """游标翻页（SyncPageCursor）：`page` 是上一页给的 `next_page`，`limit` 每页条数。"""
    try:
        limit = max(1, min(MAX_LIMIT, int(_one(query, "limit", DEFAULT_LIMIT))))
    except ValueError:
        raise bad_request("limit: expected an integer")
    cursor = _one(query, "page", "")
    match = re.fullmatch(r"page_(\d+)", cursor or "")
    if cursor and not match:
        raise bad_request("page: invalid cursor")
    start = int(match.group(1)) if match else 0
    chunk = items[start:start + limit]
    more = start + limit < len(items)
    return {"data": chunk, "next_page": f"page_{start + limit}" if more else None,
            "has_more": more, "first_id": chunk[0]["id"] if chunk else None,
            "last_id": chunk[-1]["id"] if chunk else None}


def _since_filters(items: list, query: dict, field: str) -> list:
    checks = {"gt": lambda a, b: a > b, "gte": lambda a, b: a >= b, "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b}
    for op, check in checks.items():
        bound = _one(query, f"created_at[{op}]")
        if bound:
            moment = _parse_time(bound)
            items = [i for i in items if check(_parse_time(i.get(field)), moment)]
    return items


def _parse_time(text):
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        raise bad_request(f"invalid time: {text}")
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def list_events(state: dict, query: dict) -> dict:
    order = _one(query, "order", "asc")
    if order not in ("asc", "desc"):
        raise bad_request("order: expected asc or desc")
    with LOCK:
        items = list(state["_events"])
    types = set(query.get("types") or []) | set(query.get("types[]") or [])
    if types:
        items = [e for e in items if e["type"] in types]
    items = _since_filters(items, query, "processed_at")
    if order == "desc":
        items.reverse()
    return page(items, query)


def list_agents(query: dict) -> dict:
    items = list(STORE["agents"].values())
    if _one(query, "include_archived", "false") != "true":
        items = [a for a in items if not a["archived_at"]]
    return page(_since_filters(items, query, "created_at"), query)


def list_environments(query: dict) -> dict:
    items = list(STORE["environments"].values())
    if _one(query, "include_archived", "false") != "true":
        items = [e for e in items if not e["archived_at"]]
    return page(items, query)


def list_sessions(query: dict) -> dict:
    items = [public_session(s) for s in STORE["sessions"].values() if not s["_deleted"]]
    if _one(query, "include_archived", "false") != "true":
        items = [s for s in items if not s["archived_at"]]
    agent = _one(query, "agent_id")
    if agent:
        items = [s for s in items if s["agent"]["id"] == agent]
    if _one(query, "order", "desc") == "desc":
        items.reverse()
    body = page(items, query)
    body["prev_page"] = None
    return body


def list_files(query: dict) -> dict:
    items = list(STORE["files"].values())
    scope = _one(query, "scope_id")
    if scope:
        items = [f for f in items if (f.get("scope") or {}).get("id") == scope]
    ids = set(query.get("ids") or []) | set(query.get("ids[]") or [])
    if ids:
        items = [f for f in items if f["id"] in ids]
    items.reverse()
    return page(items, query)


# ---------- 上传与单次问答 ----------

def parse_upload(content_type: str, body: bytes) -> tuple[str, str, bytes]:
    if not content_type.lower().startswith("multipart/form-data"):
        raise bad_request("expected multipart/form-data")
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") == "file":
            data = part.get_payload(decode=True) or b""
            return part.get_filename() or "upload", part.get_content_type() or "application/octet-stream", data
    raise bad_request("file: missing")


def message_reply(body: dict) -> dict:
    """侧栏标题：取 <message> 里（没有就取整条用户消息）的前 20 个字，回成一段正常结束的文字。"""
    if body.get("stream"):
        raise bad_request("streaming is not supported by the lab stub")
    text = ""
    for message in body.get("messages") or []:
        if message.get("role") == "user":
            content = message.get("content")
            text = content if isinstance(content, str) else "".join(
                b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
            break
    found = re.search(r"<message>([\s\S]*?)</message>", text)
    title = " ".join((found.group(1) if found else text).split())[:20] or "Lab chat"
    return {"id": new_id("msg"), "type": "message", "role": "assistant", "model": body.get("model") or "lab",
            "content": [{"type": "text", "text": title}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0}}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pearnly-lab-agents/1"

    def log_message(self, fmt, *args):  # 只记方法、路径与状态，不记请求体
        if not OPTIONS.get("quiet"):
            super().log_message(fmt, *args)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _body(self) -> bytes:
        return self._raw

    def _json(self) -> dict:
        raw = self._raw
        if not raw:
            return {}
        try:
            body = json.loads(raw)
        except ValueError:
            raise bad_request("invalid JSON body")
        if not isinstance(body, dict):
            raise bad_request("expected a JSON object")
        return body

    def _send(self, status: int, payload, content_type="application/json") -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("request-id", new_id("req"))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        query = parse_qs(url.query, keep_blank_values=True)
        parts = [p for p in url.path.split("/") if p]
        # 先把请求体读完：出错提前返回时，保持连接上的下一个请求不会读到残留的请求体。
        length = int(self.headers.get("Content-Length") or 0)
        self._raw = self.rfile.read(length) if length else b""
        try:
            with_stream = method == "GET" and len(parts) == 5 and parts[:2] == ["v1", "sessions"] \
                and parts[3:] == ["events", "stream"]
            if with_stream:
                return self._stream(parts[2], query)
            status, payload, content_type = self._route(method, parts, query)
            self._send(status, payload, content_type)
        except ApiError as err:
            self._send(err.status, {"type": "error", "error": {"type": err.kind, "message": err.message}})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _route(self, method: str, parts: list, query: dict):
        ok = lambda payload: (200, payload, "application/json")  # noqa: E731
        if parts == ["healthz"]:
            return ok({"ok": True})
        if not parts or parts[0] != "v1" or len(parts) < 2:
            raise not_found("route", self.path)
        kind, rest = parts[1], parts[2:]
        with LOCK:
            if kind == "agents":
                return ok(self._agents(method, rest, query))
            if kind == "environments":
                return ok(self._environments(method, rest, query))
            if kind == "sessions":
                return ok(self._sessions(method, rest, query))
            if kind == "files":
                if method == "GET" and len(rest) == 2 and rest[1] == "content":
                    meta = STORE["files"].get(rest[0])
                    if not meta:
                        raise not_found("file", rest[0])
                    return 200, STORE["blobs"][rest[0]], meta["mime_type"]
                return ok(self._files(method, rest, query))
            if kind == "messages" and method == "POST" and not rest:
                return ok(message_reply(self._json()))
        raise not_found("route", self.path)

    def _agents(self, method, rest, query):
        if not rest and method == "GET":
            return list_agents(query)
        if not rest and method == "POST":
            body = self._json()
            if not body.get("name") or not body.get("model"):
                raise bad_request("name and model are required")
            ident = new_id("agent")
            STORE["agents"][ident] = agent_object(body, ident)
            return STORE["agents"][ident]
        agent = STORE["agents"].get(rest[0]) if rest else None
        if not agent:
            raise not_found("agent", rest[0] if rest else "")
        if len(rest) == 1 and method == "GET":
            return agent
        if len(rest) == 1 and method == "POST":
            body = self._json()
            if "version" in body and body["version"] != agent["version"]:
                raise ApiError(409, "conflict_error", "agent version mismatch")
            STORE["agents"][agent["id"]] = agent_object(body, old=agent)
            return STORE["agents"][agent["id"]]
        if rest[1:] == ["archive"] and method == "POST":
            agent["archived_at"] = agent["updated_at"] = stamp()
            return agent
        raise not_found("route", self.path)

    def _environments(self, method, rest, query):
        if not rest and method == "GET":
            return list_environments(query)
        if not rest and method == "POST":
            body = self._json()
            if not body.get("name"):
                raise bad_request("name is required")
            ident = new_id("env")
            STORE["environments"][ident] = environment_object(body, ident)
            return STORE["environments"][ident]
        env = STORE["environments"].get(rest[0]) if rest else None
        if not env:
            raise not_found("environment", rest[0] if rest else "")
        if len(rest) == 1 and method == "GET":
            return env
        if len(rest) == 1 and method == "POST":
            STORE["environments"][env["id"]] = environment_object(self._json(), old=env)
            return STORE["environments"][env["id"]]
        if len(rest) == 1 and method == "DELETE":
            del STORE["environments"][env["id"]]
            return {"id": env["id"], "type": "environment_deleted"}
        if rest[1:] == ["archive"] and method == "POST":
            env["archived_at"] = env["updated_at"] = stamp()
            return env
        raise not_found("route", self.path)

    def _sessions(self, method, rest, query):
        if not rest and method == "GET":
            return list_sessions(query)
        if not rest and method == "POST":
            return public_session(create_session(self._json()))
        state = get_session(rest[0])
        if len(rest) == 1:
            if method == "GET":
                return public_session(state)
            if method == "POST":
                update_session(state, self._json())
                return public_session(state)
            if method == "DELETE":
                delete_session(state)
                return {"id": state["id"], "type": "session_deleted"}
        if rest[1:] == ["archive"] and method == "POST":
            state["archived_at"] = state["updated_at"] = stamp()
            return public_session(state)
        if rest[1:] == ["events"]:
            if method == "GET":
                return list_events(state, query)
            if method == "POST":
                return {"data": receive(state, self._json().get("events"))}
        if len(rest) >= 2 and rest[1] == "resources":
            if len(rest) == 2 and method == "GET":
                return page(list(state["resources"]), query)
            if len(rest) == 2 and method == "POST":
                return add_resource(state, self._json())
            found = next((r for r in state["resources"] if len(rest) == 3 and r["id"] == rest[2]), None)
            if not found:
                raise not_found("resource", rest[2] if len(rest) == 3 else "")
            if method == "GET":
                return found
            if method == "POST":
                found.update({k: v for k, v in self._json().items() if k in ("mount_path",)}, updated_at=stamp())
                return found
            if method == "DELETE":
                state["resources"].remove(found)
                return {"id": found["id"], "type": "session_resource_deleted"}
        raise not_found("route", self.path)

    def _files(self, method, rest, query):
        if not rest and method == "GET":
            return list_files(query)
        if not rest and method == "POST":
            filename, mime, data = parse_upload(self.headers.get("Content-Type") or "", self._body())
            ident = new_id("file")
            STORE["files"][ident] = file_object(ident, filename, mime, len(data), False, None)
            STORE["blobs"][ident] = data
            return STORE["files"][ident]
        meta = STORE["files"].get(rest[0]) if len(rest) == 1 else None
        if not meta:
            raise not_found("file", rest[0] if rest else "")
        if method == "GET":
            return meta
        if method == "DELETE":
            del STORE["files"][meta["id"]]
            STORE["blobs"].pop(meta["id"], None)
            return {"id": meta["id"], "type": "file_deleted"}
        raise not_found("route", self.path)

    def _stream(self, session: str, query: dict) -> None:
        wanted = set(query.get("event_deltas") or []) | set(query.get("event_deltas[]") or [])
        inbox: queue.Queue = queue.Queue()
        with LOCK:
            state = get_session(session)
            entry = (wanted, inbox)
            state["_streams"].append(entry)
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            deadline = time.monotonic() + OPTIONS["stream_timeout"]
            while time.monotonic() < deadline:
                try:
                    event = inbox.get(timeout=min(PING_SECONDS, max(0.0, deadline - time.monotonic())))
                except queue.Empty:
                    self.wfile.write(b'event: ping\ndata: {"type": "ping"}\n\n')
                    self.wfile.flush()
                    continue
                if event is None:
                    break
                data = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"event: {event['type']}\ndata: {data}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with LOCK:
                if entry in state["_streams"]:
                    state["_streams"].remove(entry)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="回归实验室用的假 Claude Managed Agents 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址；给容器用传 0.0.0.0")
    parser.add_argument("--port", type=int, default=18780)
    parser.add_argument("--delay", type=float, default=OPTIONS["delay"], help="收到消息后过多少秒回复")
    parser.add_argument("--stream-timeout", type=float, default=OPTIONS["stream_timeout"],
                        help="事件流最长保持多少秒")
    parser.add_argument("--reply-prefix", default=REPLY_PREFIX, help="假回复的固定前缀")
    parser.add_argument("--quiet", action="store_true", help="不打印逐条请求日志")
    args = parser.parse_args(argv)
    OPTIONS.update(delay=args.delay, stream_timeout=args.stream_timeout, prefix=args.reply_prefix, quiet=args.quiet)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"lab agents listening on http://{args.host}:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
