"""Claude（Anthropic Messages API）单次调用适配层（ADR-017）。

与 ``client.ProviderClient`` 同一套注入能力与语义：付费调用经调用方的 ``post``（检查点防重付费、额度、限流），
发出前复核识别任务（``require_context``），明确拒绝冻结成终局（续跑不再付费），结果不明按调用方给的次数换尝试号重发。
本模块只讲 Anthropic 协议：不导入 HTTP 客户端，不读文件或环境变量，不重试格式错误，不换模型。

入口可换（``base_url``）：正式是 Anthropic 官方，测试期是聚合平台的 Anthropic 兼容入口；认证头按 ``auth``，
入口上的模型名按 ``models`` 映射。检查点身份带入口主机，两个入口互不复用对方的结果。
"""
import base64
from copy import deepcopy
import json
import re
from types import MappingProxyType
from urllib.parse import urlsplit

from pearnly_core.execution.errors import Deferred, Uncertain
from pearnly_cowork.ocr.engine.contracts import ModelRequest, ModelResponse
from pearnly_cowork.ocr.providers.client import (
    ProviderConfigurationError, ProviderError, _identity, _record_unknown, _reference)

PROVIDER = "anthropic"
FAMILY = "claude-"
API_VERSION = "2023-06-01"
LOCATION = "global"
AUTH_STYLES = ("x-api-key", "bearer")
# 读取档位：小样本实测 effort=low 思考 token 为 0、准确率不降（2026-10-07，工作包「Claude 识别引擎」）。
EFFORT = "low"
# 图在前、指令在 system 里；用户消息只放一句读取指令（与小样本实测同一份请求形状）。
READ_INSTRUCTION = "Read this document and output the JSON."
IMAGE_MIMES = ("image/jpeg", "image/png", "image/gif", "image/webp")
# Anthropic 过载（529）与 Core 已处理的 429 / 503 同样当成稍后重试；500 / 502 / 504 服务端临时故障同 Gemini 适配层。
OVERLOADED = 529
SERVER_TEMPORARY = (500, 502, 504)


def connection(value):
    """校验非密连接参数（入口、认证方式、模型名映射），返回冻结用的字典。不接受密钥。"""
    if not isinstance(value, dict) or set(value) - {"base_url", "auth", "models"}:
        raise ProviderConfigurationError("ocr.anthropic_not_configured")
    base_url = str(value.get("base_url") or "").rstrip("/")
    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
        raise ProviderConfigurationError("ocr.anthropic_not_configured")
    auth = value.get("auth") or "x-api-key"
    models = value.get("models") or {}
    if (auth not in AUTH_STYLES or not isinstance(models, dict)
            or any(not isinstance(k, str) or not k.startswith(FAMILY) or not isinstance(v, str)
                   or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", v) for k, v in models.items())):
        raise ProviderConfigurationError("ocr.anthropic_not_configured")
    return {"base_url": base_url, "auth": auth, "models": dict(sorted(models.items()))}


def is_claude(model):
    return isinstance(model, str) and model.startswith(FAMILY)


class AnthropicClient:
    """冻结的非密连接参数 + 注入的能力，全部必填。

    ``api_key``：每次调用前取一次密钥的函数（站点读挂载的文件、Cloud Run 读密钥环境变量），密钥不进检查点与证据。
    其余参数与 ``ProviderClient`` 同名同义。
    """
    def __init__(self, target, *, post, checkpoint_key, reject_checkpoint, terminal_read, terminal_write,
                 feedback, scope, api_key, require_context, clock, checkpoint_read=None):
        self.target = MappingProxyType(connection(target))
        self.host = urlsplit(self.target["base_url"]).hostname
        self._post, self._key, self._reject = post, checkpoint_key, reject_checkpoint
        self._terminal_read, self._terminal_write = terminal_read, terminal_write
        self._feedback, self._scope = feedback, scope
        self._api_key, self._require_context, self._clock = api_key, require_context, clock
        self._checkpoint_read = checkpoint_read or (lambda key: None)

    def _headers(self):
        try:
            secret = self._api_key()
        except ProviderConfigurationError:
            raise
        except Exception as error:  # 文件读不到 / 格式不对：没有发出任何付费调用，配置问题照实报
            raise ProviderConfigurationError("ocr.anthropic_credentials_not_configured") from error
        if not isinstance(secret, str) or not secret.strip():
            raise ProviderConfigurationError("ocr.anthropic_credentials_not_configured")
        secret = secret.strip()
        headers = {"anthropic-version": API_VERSION, "Content-Type": "application/json"}
        if self.target["auth"] == "bearer":
            headers["Authorization"] = "Bearer " + secret
        else:
            headers["x-api-key"] = secret
        return headers

    def _refuse(self, key, response, code):
        """明确的终局拒绝单独冻结（与 Core 可重试的拒绝分开），续跑不再付费；只留回复，不留请求头。"""
        try:
            raw = {"json": response.json()}
        except ValueError:
            content = getattr(response, "content", b"")
            if not isinstance(content, bytes):
                content = str(getattr(response, "text", "")).encode()
            raw = {"base64": base64.b64encode(content).decode("ascii")}
        refusal = {"code": code, "http_status": response.status_code, "provider_code": None}
        self._terminal_write(key, {"refusal": refusal, "raw": raw})
        raise ProviderError(**refusal)

    def _identity(self, model, stage, page_number, attempt):
        """检查点身份：入口主机 + 模型 + 阶段 + 页 + 尝试。实时调用与批处理（``batch_entry``）同一个算法，键一致。"""
        return json.dumps(["cowork", PROVIDER, self.host, LOCATION, model, stage, page_number, attempt])

    def headers(self):
        """调用用的请求头（含密钥，只给发出请求的代码用，不进检查点与证据）。批处理的提交 / 查询也用它。"""
        return self._headers()

    def batch_entry(self, request: ModelRequest):
        """批处理（Message Batches，同价减半，ADR-018）的一条：``(检查点键, 请求体)``，不发出、不复核、不付费。
        键与这条请求实时发出时的检查点键相同：批处理结果按这个键预先填进检查点，之后照常跑识别就直接取回。"""
        _reference(request.stage, request.page_number, request.attempt)
        body = self.payload(request)
        return self._key(body, self._identity(request.model, request.stage, request.page_number, request.attempt)), body

    def _send(self, *, payload, model, stage, page_number, attempt, timeout_s, retry_unknown):
        _reference(stage, page_number, attempt)
        if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or not 0 < timeout_s <= 180:
            raise ProviderConfigurationError("ocr.invalid_provider_timeout")
        self._require_context()
        identity = self._identity(model, stage, page_number, attempt)
        key = self._key(payload, identity)
        if retry_unknown:
            saved = self._checkpoint_read(key) or {}
            if saved.get("state") == "rejected" and (saved.get("body") or {}).get("outcome_unknown"):
                # 续跑：上回结果不明、已经换尝试号重发过 —— 直接走重发那次（完成的从检查点取回，不再付费）。
                return self._send(payload=payload, model=model, stage=stage, page_number=page_number,
                                  attempt=attempt + 1, timeout_s=timeout_s, retry_unknown=retry_unknown - 1)
        terminal = self._terminal_read(key)
        if terminal is not None:
            raise ProviderError(**terminal["refusal"])
        headers = self._headers()
        bucket = self._scope(PROVIDER, self.host, LOCATION, model)
        started = self._clock()
        try:
            response = self._post(url=self.target["base_url"] + "/v1/messages", headers=headers, payload=payload,
                                  timeout_s=timeout_s, model=identity, bucket=bucket,
                                  label=f"{PROVIDER}:{LOCATION}")
        except Uncertain as error:
            _record_unknown(PROVIDER, stage, page_number, attempt, error)
            if not retry_unknown:
                raise
            self._reject(key, {"outcome_unknown": True, "retried": True, "deferred": True})
            return self._send(payload=payload, model=model, stage=stage, page_number=page_number,
                              attempt=attempt + 1, timeout_s=timeout_s, retry_unknown=retry_unknown - 1)
        elapsed = max(0, int((self._clock() - started) * 1000))
        status = response.status_code
        if status != 200:
            if status == OVERLOADED:
                self._reject(key, {"http": status, "deferred": True})
                self._feedback(bucket, 503, 5)
                raise Deferred("provider_overloaded", 5)
            if status in SERVER_TEMPORARY:
                self._reject(key, {"http": status, "deferred": True})
                raise Deferred("provider_5xx", 5)  # 失败的请求不计费；不是额度问题，不放慢别的请求
            self._refuse(key, response, "ocr.provider_auth" if status in (401, 403) else "ocr.provider_http_error")
        try:
            body = response.json()
        except ValueError:
            self._refuse(key, response, "ocr.provider_invalid_response")
        if not isinstance(body, dict) or body.get("type") == "error":
            self._refuse(key, response, "ocr.provider_invalid_response")
        return deepcopy(body), elapsed, key

    def payload(self, request: ModelRequest):
        """组请求（纯函数，不发出）：实时调用与以后的批处理（Message Batches，同价减半）共用同一份请求体。"""
        return build_payload(request, self.target["models"])

    def model_call(self, request: ModelRequest, *, retry_unknown=1):
        """一次实时 Messages 调用：组请求 → 经注入的 ``post`` 发出（检查点 / 额度 / 限流）→ 解析结果。"""
        body = self.payload(request)
        raw, elapsed, key = self._send(payload=body, model=request.model, stage=request.stage,
                                       page_number=request.page_number, attempt=request.attempt,
                                       timeout_s=request.timeout_s, retry_unknown=retry_unknown)
        return parse_response(raw, request.model, elapsed_ms=elapsed, checkpoint_key=key)


def build_payload(request: ModelRequest, models=None):
    """``ModelRequest`` → Messages 请求体。``request.thinking`` / ``temperature`` 是 Gemini 的旋钮，这里不用
    （档位固定 ``EFFORT``；新一代模型非默认 temperature 会被拒）。"""
    if not isinstance(request, ModelRequest):
        raise ProviderConfigurationError("ocr.invalid_model_request")
    model = _identity(request.model, "ocr.invalid_model")
    if (not is_claude(model) or not isinstance(request.prompt, str) or not request.prompt
            or type(request.max_output_tokens) is not int or request.max_output_tokens < 1
            or request.response_schema is not None and not isinstance(request.response_schema, dict)):
        raise ProviderConfigurationError("ocr.invalid_model_request")
    content = []
    for data, mime in request.images:
        if not isinstance(data, bytes) or not data or mime not in IMAGE_MIMES:
            raise ProviderConfigurationError("ocr.invalid_model_image")
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                                                    "data": base64.b64encode(data).decode("ascii")}})
    output = {"effort": EFFORT}
    if request.response_schema is not None:
        # 对账抽取带严格结果格式（``enterprise.contract.SCHEMAS``）：交给官方结构化输出，回复一定是合这份格式的 JSON。
        output["format"] = {"type": "json_schema", "schema": deepcopy(request.response_schema)}
    body = {"model": (models or {}).get(model, model), "max_tokens": request.max_output_tokens,
            "messages": [{"role": "user", "content": content}], "output_config": output}
    if content:
        # 看图读：提示词放 system 打缓存标记（每页同一份，读缓存价约十分之一），图在前、读取指令在后。
        body["system"] = [{"type": "text", "text": request.prompt, "cache_control": {"type": "ephemeral"}}]
        content.append({"type": "text", "text": READ_INSTRUCTION})
    else:
        content.append({"type": "text", "text": request.prompt})
    return body


def parse_response(body, model, *, elapsed_ms=0, checkpoint_key=""):
    """Messages 回复（实时的回复体，或批处理结果里的 ``message``）→ ``ModelResponse``。纯函数。"""
    text = "".join(block.get("text", "") for block in body.get("content") or []
                   if isinstance(block, dict) and block.get("type") == "text").strip()
    usage = body.get("usage") or {}
    stop = str(body.get("stop_reason") or "")
    return ModelResponse(text=text, raw=body,
        input_tokens=sum(int(usage.get(name) or 0) for name in
                         ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
        output_tokens=int(usage.get("output_tokens") or 0), model=model, elapsed_ms=elapsed_ms,
        finish_reason="MAX_TOKENS" if stop == "max_tokens" else stop.upper(), checkpoint_key=checkpoint_key)
