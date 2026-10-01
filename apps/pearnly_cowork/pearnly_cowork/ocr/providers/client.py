"""Single-call Vertex Gemini / Vision adapters with injected Core capabilities.

This module never imports an HTTP client or credential loader. A completed raw
response belongs to the engine's parse/schema policy; this adapter has no JSON
retry, model fallback, sleep or transport exception handler.
"""
import base64
from copy import deepcopy
import json
import re
import time
from types import MappingProxyType
from urllib.parse import quote

from pearnly_core.execution.errors import Deferred, Uncertain
from pearnly_cowork.ocr.engine.contracts import ModelRequest, ModelResponse, ProviderFailure, VisionResponse
from pearnly_cowork.ocr.policy import vertex_host, vertex_location, vertex_overflows
from pearnly_cowork.ocr.schema.evidence import Block, BoundingBox, Page, Paragraph, Word


class ProviderError(ProviderFailure):
    """Explicit provider refusal; never a Core Deferred or Uncertain result."""


class ProviderConfigurationError(RuntimeError):
    """Local prerequisite failure, never permission for a paid fallback."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _identity(value, code):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
        raise ProviderConfigurationError(code)
    return value


def _reference(stage, page_number, attempt):
    _identity(stage, "ocr.invalid_stage")
    if type(page_number) is not int or page_number < 1 or type(attempt) is not int or attempt < 0:
        raise ProviderConfigurationError("ocr.invalid_provider_reference")


def _record_unknown(provider, stage, page_number, attempt, error):
    """结果不明的原因写进错误日志（共用层只留下「started」检查点，原因丢了；2026-09-29 压测查不出是上传超时
    还是回复太大）。记日志失败不影响调用本身。"""
    cause = getattr(error, "__cause__", None)
    try:
        import frappe

        frappe.log_error(title="cowork provider outcome unknown",
                         message=f"{provider} {stage} page {page_number} attempt {attempt}: {error}"
                                 + (f" ({type(cause).__name__}: {cause})" if cause else ""))
    except Exception:
        pass


def _bbox(value):
    vertices = [(int(vertex.get("x", 0)), int(vertex.get("y", 0)))
                for vertex in (value or {}).get("vertices", [])]
    vertices = vertices or [(0, 0)]
    return BoundingBox(vertices=(vertices + [vertices[-1]] * 4)[:4])


def vision_page(annotation, page_number):
    """Source layer1_vision._response_to_page, using equivalent REST dictionaries."""
    text = annotation.get("text") or ""
    if not text or not annotation.get("pages"):
        return Page(page_number=page_number, width=0, height=0, full_text=text,
                    avg_confidence=0.0, blocks=[])
    physical = annotation["pages"][0]
    blocks, confidences = [], []
    for block in physical.get("blocks", []):
        paragraphs = []
        for paragraph in block.get("paragraphs", []):
            words = []
            for word in paragraph.get("words", []):
                confidence = float(word.get("confidence", 0))
                confidences.append(confidence)
                words.append(Word(text="".join(symbol.get("text", "") for symbol in word.get("symbols", [])),
                                  confidence=confidence, bbox=_bbox(word.get("boundingBox"))))
            paragraphs.append(Paragraph(text=" ".join(word.text for word in words),
                confidence=float(paragraph.get("confidence", 0)), bbox=_bbox(paragraph.get("boundingBox")), words=words))
        blocks.append(Block(text="\n".join(paragraph.text for paragraph in paragraphs),
            confidence=float(block.get("confidence", 0)), bbox=_bbox(block.get("boundingBox")), paragraphs=paragraphs))
    return Page(page_number=page_number, width=int(physical.get("width", 0)),
        height=int(physical.get("height", 0)), full_text=text, blocks=blocks,
        avg_confidence=sum(confidences) / len(confidences) if confidences else 0.0)


VISION_FIRST_TIMEOUT = 15
VISION_5XX_RETRIES, VISION_5XX_PAUSE = 2, 2


class ProviderClient:
    """Explicit frozen target plus injectable capabilities, all required.

    post/checkpoint_key/reject_checkpoint/feedback/scope are Core execution
    capabilities. require_context checks the live work and target before auth.
    Tests inject these functions; production uses providers.core.build_client.
    """
    def __init__(self, environment, *, project, post, checkpoint_key, reject_checkpoint,
                 terminal_read, terminal_write, feedback, scope, access_token, require_context,
                 clock=time.monotonic, checkpoint_read=None, regions=None, sleep=time.sleep, in_queue_job=None):
        self.environment = MappingProxyType(dict(environment))
        self.project = _identity(project, "ocr.project_not_configured")
        self._post, self._key, self._reject = post, checkpoint_key, reject_checkpoint
        self._terminal_read, self._terminal_write = terminal_read, terminal_write
        self._feedback, self._scope = feedback, scope
        self._token, self._require_context, self._clock = access_token, require_context, clock
        self._checkpoint_read = checkpoint_read or (lambda key: None)
        from pearnly_cowork.ocr.enterprise import pages as enterprise_pages

        self._regions = enterprise_pages.regions(regions, self.environment)
        self._sleep, self._in_queue_job = sleep, in_queue_job or (lambda: False)

    def _refuse(self, key, response, code):
        """Freeze an explicit completed refusal separately from Core's retryable rejection.

        A later Deferred elsewhere must not turn this known outcome into a new
        paid request. Keep raw response evidence; never persist request headers.
        """
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

    def _send(self, *, url, payload, model, provider, location, stage, page_number, attempt, timeout_s,
              retry_unknown=False, defer_5xx=True, rpm=None, wait=True, hand_back=(), resend_timeout_s=None):
        # 500 / 502 / 504 是谷歌服务端临时故障，一律稍后重试（失败的请求不计费）。原来只有对账读取这样，录入发票当终局拒绝：
        # 2026-10-01 测试租户一次 Vision 502 让单页发票直接判失败（用户：5xx、传输错误都要重试，一次改到位）。
        # ``resend_timeout_s``：结果不明重发那一次的等待上限（第一次短、重发用原上限，见 ``vision_call``）。
        # 结果不明时还能重发几次：读字最多 2 次（每页约 0.05 泰铢，90 秒 × 3 仍在识别任务时限内；2026-09-29 压测
        # B01 一张照片连续两次不明、整张丢失），抽取 1 次（每页约 0.5 泰铢、单次可长达 180 秒）。
        resends = ((2 if provider == "documentai" else 1) if retry_unknown is True else int(retry_unknown or 0))
        _reference(stage, page_number, attempt)
        if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or not 0 < timeout_s <= 180:
            raise ProviderConfigurationError("ocr.invalid_provider_timeout")
        self._require_context()
        # Core's model argument participates only in its checkpoint identity.
        # Budget uses the real model. Nothing private is added to provider JSON.
        identity = json.dumps(["cowork", provider, self.project, location, model, stage, page_number, attempt])
        key = self._key(payload, identity)
        if resends:
            saved = self._checkpoint_read(key) or {}
            if saved.get("state") == "rejected" and (saved.get("body") or {}).get("outcome_unknown"):
                # 续跑：这次上回结果不明、已经重发过 —— 直接取重发那次（完成的从检查点取回，不再付费）。
                return self._send(url=url, payload=payload, model=model, provider=provider, location=location,
                    stage=stage, page_number=page_number, attempt=attempt + 1, timeout_s=resend_timeout_s or timeout_s,
                    retry_unknown=resends - 1, defer_5xx=defer_5xx, rpm=rpm, wait=wait, hand_back=hand_back)
        terminal = self._terminal_read(key)
        if terminal is not None:
            raise ProviderError(**terminal["refusal"])
        try:
            token = self._token()
        except Exception as error:
            # 换令牌时连不上 / 超时（2026-09-29 压测：oauth2.googleapis.com 读超时 120 秒，被当成程序错误整张读空）：
            # 还没发出任何付费调用，稍后重试即可。凭据缺失、格式不对照旧报错。
            cause = getattr(error, "__cause__", None)
            if cause is not None and any(word in type(cause).__name__ for word in ("Transport", "Timeout", "Connection")):
                raise Deferred("token_unavailable", 15) from error
            raise
        if not isinstance(token, str) or not token:
            raise ProviderConfigurationError("ocr.credentials_not_configured")
        bucket = self._scope(provider, self.project, location, model)
        started = self._clock()
        # Document AI 按区域分桶、限流不原地等（换下一个区域）；其余调用照旧。
        routing = {"label": f"{provider}:{location}"}
        if provider == "documentai":
            routing.update(rpm=rpm, wait=wait)
        elif provider == "vertex":
            # Gemini 是共享额度（DSQ）：零星拒绝不让整个模型停下；``hand_back`` 让被谷歌拒绝的这次交回来转入口（_vertex）。
            routing.update(shared_quota=True, hand_back=hand_back)
        try:
            response = self._post(url=url, headers={"Authorization": "Bearer " + token,
                "Content-Type": "application/json"}, payload=payload, timeout_s=timeout_s,
                model=identity, bucket=bucket, **routing)
        except Uncertain as error:
            _record_unknown(provider, stage, page_number, attempt, error)
            if not resends:
                raise
            # 调用方选了「结果不明重发一次」（对账读字 / 抽取，2026-09-29 用户同意）：这次记成已拒（识别任务续跑时
            # 不会因为它判结果不明），换一个 attempt 重发，得到新的检查点；再不明就照旧交出去。
            # 「deferred」让它不被当成对方明确拒绝的终局结果（Cowork 的 terminal_read 只把非暂缓的拒绝当终局）。
            self._reject(key, {"outcome_unknown": True, "retried": True, "deferred": True})
            return self._send(url=url, payload=payload, model=model, provider=provider, location=location,
                stage=stage, page_number=page_number, attempt=attempt + 1, timeout_s=resend_timeout_s or timeout_s,
                retry_unknown=resends - 1, defer_5xx=defer_5xx, rpm=rpm, wait=wait, hand_back=hand_back)
        elapsed = max(0, int((self._clock() - started) * 1000))
        status = response.status_code
        if status != 200:
            # Core already handles explicit HTTP 429/503 as Deferred. The
            # additional quota spelling is source vertex._error_kind's 403 case.
            try:
                error = response.json().get("error", {})
                reason = str(error.get("status", "")) + " " + str(error.get("message", ""))
            except (ValueError, TypeError, AttributeError):
                reason = ""
            # 500 / 502 / 504 是服务端临时故障（2026-09-29 压测：Document AI 读一张照片 90 秒没回，重发又回 5xx，
            # 被当成终局失败、整张照片读空）：与 429 / 503 一样稍后重试。失败的请求不计费。
            if defer_5xx and status in (500, 502, 504):
                self._reject(key, {"http": status, "deferred": True})
                raise Deferred("provider_5xx", 5)  # 不放慢别的请求（不是额度问题）；``provider_`` 开头：Gemini 换入口、读字换区域
            if (status in (429, 503) or "RESOURCE_EXHAUSTED" in reason.upper() or "quota" in reason.lower()):
                self._reject(key, {"http": status, "deferred": True})
                self._feedback(bucket, 503 if status == 503 else 429, 5, *((True,) if provider == "vertex" else ()))
                raise Deferred("provider_quota", 5)
            self._refuse(key, response, "ocr.provider_auth" if status in (401, 403) else "ocr.provider_http_error")
        try:
            body = response.json()
        except ValueError:
            self._refuse(key, response, "ocr.provider_invalid_response")
        if not isinstance(body, dict):
            self._refuse(key, response, "ocr.provider_invalid_response")
        return deepcopy(body), elapsed, key, bucket

    def model_call(self, request: ModelRequest, *, retry_unknown=1):
        """``retry_unknown``：结果不明（读超时、连接断）时重发几次，默认 1（录入发票原来不重发、整份判结果不明；
        2026-10-01 用户：传输错误也要重试）。重发是新的检查点，续跑时直接取重发那次，不重复付费。"""
        if not isinstance(request, ModelRequest):
            raise ProviderConfigurationError("ocr.invalid_model_request")
        model = _identity(request.model, "ocr.invalid_model")
        location = _identity(vertex_location(model, self.environment), "ocr.invalid_location")
        if (not isinstance(request.prompt, str) or not request.prompt
                or type(request.max_output_tokens) is not int or request.max_output_tokens < 1
                or request.thinking not in ("", "MINIMAL", "LOW", "MEDIUM", "HIGH")):
            raise ProviderConfigurationError("ocr.invalid_model_request")
        parts = []
        for content, mime in request.images:
            if not isinstance(content, bytes) or not content or not isinstance(mime, str) or not mime:
                raise ProviderConfigurationError("ocr.invalid_model_image")
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(content).decode("ascii")}})
        parts.append({"text": request.prompt})  # Source multimodal order: images, then prompt.
        config = {"temperature": request.temperature, "maxOutputTokens": request.max_output_tokens}
        if request.response_mime:
            config["responseMimeType"] = "application/json"
        if request.images or model.lower().startswith("gemini-3.8"):
            if model.lower().startswith("gemini-2.5"):
                config["thinkingConfig"] = {"thinkingBudget": 0}
            else:
                config["thinkingConfig"] = {"thinkingLevel": "LOW" if model.lower().startswith("gemini-3.8") else "MINIMAL"}
        if request.thinking and not model.lower().startswith("gemini-2.5"):
            config["thinkingConfig"] = {"thinkingLevel": request.thinking}
        if request.response_schema is not None:
            if not isinstance(request.response_schema, dict) or not request.response_mime:
                raise ProviderConfigurationError("ocr.invalid_model_request")
            config["responseJsonSchema"] = deepcopy(request.response_schema)
        payload = {"contents": [{"role": "user", "parts": parts}], "generationConfig": config}
        body, elapsed, key = self._vertex(payload, model, location, request, retry_unknown)
        candidates = body.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        text = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", [])
                       if not part.get("thought")).strip()
        usage = body.get("usageMetadata") or {}
        return ModelResponse(text=text, raw=body, input_tokens=int(usage.get("promptTokenCount", 0) or 0),
            output_tokens=int(usage.get("candidatesTokenCount", 0) or 0) + int(usage.get("thoughtsTokenCount", 0) or 0),
            model=model, elapsed_ms=elapsed, finish_reason=str(candidate.get("finishReason", "")), checkpoint_key=key)

    def _vertex(self, payload, model, location, request, retry_unknown):
        """Gemini：全球入口被谷歌拒绝（429 / 503 / 额度，``provider_*``）时这一次请求立刻依次改发备用多区域入口
        （``policy.vertex_overflows``，默认美国 → 欧洲），不原地等；最后一个备用入口再被拒才按原规则原地短等。
        自家额度的等待照旧原地等。续跑时已在任一备用入口读完的直接取回，不再付费。"""
        def send(where, attempt=request.attempt, hand_back=(), resend=retry_unknown):
            url = (f"https://{vertex_host(where)}/v1/projects/{quote(self.project, safe='')}/locations/"
                   f"{quote(where, safe='')}/publishers/google/models/{quote(model, safe='')}:generateContent")
            body, elapsed, key, _ = self._send(url=url, payload=payload, model=model, provider="vertex",
                location=where, stage=request.stage, page_number=request.page_number, attempt=attempt,
                timeout_s=request.timeout_s, retry_unknown=resend, hand_back=hand_back)
            return body, elapsed, key

        spares = vertex_overflows(location, self.environment)
        if not spares:
            return send(location)
        self._require_context()  # 先确认识别任务仍有效，再读任何检查点（与 _send 同一顺序）
        for spare in spares:
            for attempt in range(request.attempt, request.attempt + 3):
                identity = json.dumps(["cowork", "vertex", self.project, spare, model, request.stage,
                                       request.page_number, attempt])
                if (self._checkpoint_read(self._key(payload, identity)) or {}).get("state") == "done":
                    return send(spare, attempt)
        try:
            return send(location, hand_back=("provider_",))
        except Deferred as error:
            if not str(error.reason).startswith("provider_"):
                raise
        # 转到备用入口的这一次结果不明（读超时、连接断）时重发一次，不让整张作废（用户 2026-10-01 同意；部署后 128 张复压
        # 有 1 张转美国入口后 60 秒没回、整张判结果不明）。重发是新的检查点，极少数情况下这一次多付一遍。
        for spare in spares[:-1]:
            try:
                return send(spare, hand_back=("provider_",), resend=retry_unknown or 1)
            except Deferred as error:
                if not str(error.reason).startswith("provider_"):
                    raise
        return send(spares[-1], resend=retry_unknown or 1)

    def document_ai_call(self, content, mime, *, expected_pages, stage="enterprise", page_number=1,
                         retry_unknown=False):
        """Pinned Document AI OCR of one PDF chunk (≤ ``MAX_PAGES_PER_READ`` pages) or one image, in the durable Work.

        Source ``services/ai_gateway/providers/enterprise.process_raw`` + ``services/ocr/enterprise_reader.read``:
        the pinned processor version, the deployed request body, the page-count check. The deployed
        database request spacing (9 per minute) is replaced by Core's shared budget, which also defers
        on 429 (a per-process fallback would multiply the quota, as the deployed comment warns).
        """
        from pearnly_cowork.ocr.enterprise import pages as enterprise_pages

        if not isinstance(content, bytes) or not content or not isinstance(mime, str) or not mime:
            raise ProviderConfigurationError("ocr.invalid_enterprise_input")
        if type(expected_pages) is not int or not 1 <= expected_pages <= enterprise_pages.MAX_PAGES_PER_READ:
            raise ProviderConfigurationError("ocr.invalid_enterprise_input")
        payload = {**enterprise_pages.request_body(content, mime), "fieldMask": enterprise_pages.FIELD_MASK}
        body, elapsed, key = self._document_ai_regions(payload, stage, page_number, retry_unknown)
        texts = enterprise_pages.page_texts(body)  # validate before exposing geometry to reconstruction
        if len(texts) != expected_pages:
            raise ProviderError("ocr.enterprise_page_count_mismatch")
        return body, elapsed, key

    # 三个区域都被限流时原地等的上限：与共用层 ``provider.post`` 同一口径（单次最多等 20 秒、一次调用累计 60 秒）。
    REGION_WAIT_STEP, REGION_WAIT_TOTAL = 20, 60
    # 这些暂缓换个区域有用：对方 429 / 503 / 额度用完（``provider_*``），或这个区域的每分钟额度（桶）还没轮到、并发已满
    # （``capacity``）。额度服务连不上、配置不一致（``capacity_unavailable`` / ``capacity_policy_mismatch``）换区域也没用。
    @staticmethod
    def _switchable(reason):
        return str(reason).startswith("provider_") or str(reason) == "capacity"

    def _document_ai_regions(self, payload, stage, page_number, retry_unknown):
        """按顺序试各区域：被限流立刻换下一个，不原地等；都被限流才短等一轮（或交给识别任务稍后续跑）。
        换区域后续跑时，已经在别的区域读完的这一块直接取回，不再付费（先查各区域的检查点）。"""
        from pearnly_cowork.ocr.enterprise import pages as enterprise_pages

        def request(region, attempt=0, wait=False):
            name = enterprise_pages.processor_name(self.project, region["processor"], region["location"],
                                                   region["version"])
            return self._send(url=f"https://{region['location']}-documentai.googleapis.com/v1/{name}:process",
                payload=payload, model=region["version"], retry_unknown=retry_unknown, provider="documentai",
                location=region["location"], stage=stage, page_number=page_number, attempt=attempt, timeout_s=90,
                rpm=region["rpm"], wait=wait)

        for region in self._regions:
            for attempt in range(3):
                identity = json.dumps(["cowork", "documentai", self.project, region["location"], region["version"],
                                       stage, page_number, attempt])
                saved = self._checkpoint_read(self._key(payload, identity)) or {}
                if saved.get("state") == "done":
                    body, elapsed, key, _ = request(region, attempt)
                    return body, elapsed, key
        waited = 0.0
        while True:
            delays, last = [], None
            for index, region in enumerate(self._regions):
                try:
                    # 只剩一个区域时照旧让共用层按原规则处理（原地短等）
                    body, elapsed, key, _ = request(region, wait=len(self._regions) == 1)
                    return body, elapsed, key
                except Deferred as error:
                    if not self._switchable(error.reason):
                        raise
                    delays.append(float(error.delay or 1))
                    last = error
            delay = min(delays)
            if (not self._in_queue_job() or delay > self.REGION_WAIT_STEP
                    or waited + delay > self.REGION_WAIT_TOTAL):
                raise Deferred(last.reason, delay)
            self._sleep(delay)
            waited += delay

    def vision_call(self, frame, *, stage, page_number):
        content = frame.vision_bytes if frame.vision_bytes is not None else frame.image_bytes
        if not isinstance(content, bytes) or not content:
            raise ProviderConfigurationError("ocr.invalid_vision_image")
        payload = {"requests": [{"image": {"content": base64.b64encode(content).decode("ascii")},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}], "imageContext": {"languageHints": ["th", "en"]}}]}
        timeout = int(self.environment.get("OCR_L1_TIMEOUT_SECONDS") or "60")
        # Vision 正常不到 1 秒：第一次最多等 VISION_FIRST_TIMEOUT 秒，没回就按「结果不明重发」再发一次（内容逐字相同、
        # 新的检查点），重发那次用原上限，不误判真的慢的回复。2026-10-01 测试租户：一次 Vision 等了 38 秒、一次 42 秒后 502。
        # 被放弃的那次谷歌可能照样收费（约 0.05 泰铢），只在出现长尾时发生。谷歌临时 5xx 原地隔 2 秒再试（失败不计费）。
        for retry in range(VISION_5XX_RETRIES + 1):
            try:
                return self._vision_once(payload, stage, page_number, timeout)
            except Deferred as error:
                # 谷歌临时故障（5xx、回复里带 8 / 14「资源用完 / 服务暂不可用」）：原地隔 2 秒再试两次，再不行交回稍后续跑
                # （2026-10-01 夜 Vision 整体回 14 时，原来每十几秒整份重派一次）。失败的请求不计费。
                if error.reason not in ("provider_5xx", "vision_8", "vision_14") or retry == VISION_5XX_RETRIES:
                    raise
                self._sleep(VISION_5XX_PAUSE)

    def _vision_once(self, payload, stage, page_number, timeout):
        body, elapsed, key, bucket = self._send(url="https://vision.googleapis.com/v1/images:annotate",
            payload=payload, model="DOCUMENT_TEXT_DETECTION", provider="vision", location="global", stage=stage,
            page_number=page_number, attempt=0, timeout_s=min(timeout, VISION_FIRST_TIMEOUT),
            retry_unknown=1, resend_timeout_s=timeout)
        responses = body.get("responses")
        if not isinstance(responses, list) or len(responses) != 1 or not isinstance(responses[0], dict):
            raise ProviderError("ocr.provider_invalid_response")
        response = responses[0]
        error = response.get("error") or {}
        code = int(error.get("code", 0) or 0)
        if code:
            useful = bool((response.get("fullTextAnnotation") or {}).get("text") or response.get("textAnnotations"))
            if code in (8, 14) and not useful:
                # A completed HTTP response with only an explicit rejection is
                # safe to defer. Do not replay a cached done/error forever.
                self._reject(key, {"http": 200, "provider_code": code, "deferred": True})
                self._feedback(bucket, 429 if code == 8 else 503, 5)
                raise Deferred("vision_" + str(code), 5)
            raise ProviderError("ocr.provider_partial_response" if useful else
                "ocr.provider_auth" if code in (7, 16) else
                "ocr.invalid_vision_image" if code == 3 else "ocr.provider_response_error", provider_code=code)
        return VisionResponse(page=vision_page(response.get("fullTextAnnotation") or {}, page_number),
                              raw=body, elapsed_ms=elapsed, checkpoint_key=key)
