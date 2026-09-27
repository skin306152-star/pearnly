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

from pearnly_core.execution.errors import Deferred
from pearnly_cowork.ocr.engine.contracts import ModelRequest, ModelResponse, ProviderFailure, VisionResponse
from pearnly_cowork.ocr.policy import vertex_location
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


class ProviderClient:
    """Explicit frozen target plus injectable capabilities, all required.

    post/checkpoint_key/reject_checkpoint/feedback/scope are Core execution
    capabilities. require_context checks the live work and target before auth.
    Tests inject these functions; production uses providers.core.build_client.
    """
    def __init__(self, environment, *, project, post, checkpoint_key, reject_checkpoint,
                 terminal_read, terminal_write, feedback, scope, access_token, require_context,
                 clock=time.monotonic):
        self.environment = MappingProxyType(dict(environment))
        self.project = _identity(project, "ocr.project_not_configured")
        self._post, self._key, self._reject = post, checkpoint_key, reject_checkpoint
        self._terminal_read, self._terminal_write = terminal_read, terminal_write
        self._feedback, self._scope = feedback, scope
        self._token, self._require_context, self._clock = access_token, require_context, clock

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

    def _send(self, *, url, payload, model, provider, location, stage, page_number, attempt, timeout_s):
        _reference(stage, page_number, attempt)
        if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or not 0 < timeout_s <= 120:
            raise ProviderConfigurationError("ocr.invalid_provider_timeout")
        self._require_context()
        # Core's model argument participates only in its checkpoint identity.
        # Budget uses the real model. Nothing private is added to provider JSON.
        identity = json.dumps(["cowork", provider, self.project, location, model, stage, page_number, attempt])
        key = self._key(payload, identity)
        terminal = self._terminal_read(key)
        if terminal is not None:
            raise ProviderError(**terminal["refusal"])
        token = self._token()
        if not isinstance(token, str) or not token:
            raise ProviderConfigurationError("ocr.credentials_not_configured")
        bucket = self._scope(provider, self.project, location, model)
        started = self._clock()
        response = self._post(url=url, headers={"Authorization": "Bearer " + token,
            "Content-Type": "application/json"}, payload=payload, timeout_s=timeout_s,
            model=identity, bucket=bucket)
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
            if status in (429, 503) or "RESOURCE_EXHAUSTED" in reason.upper() or "quota" in reason.lower():
                self._reject(key, {"http": status, "deferred": True})
                self._feedback(bucket, 503 if status == 503 else 429, 5)
                raise Deferred("provider_quota", 5)
            self._refuse(key, response, "ocr.provider_auth" if status in (401, 403) else "ocr.provider_http_error")
        try:
            body = response.json()
        except ValueError:
            self._refuse(key, response, "ocr.provider_invalid_response")
        if not isinstance(body, dict):
            self._refuse(key, response, "ocr.provider_invalid_response")
        return deepcopy(body), elapsed, key, bucket

    def model_call(self, request: ModelRequest):
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
        payload = {"contents": [{"role": "user", "parts": parts}], "generationConfig": config}
        host = "aiplatform.googleapis.com" if location == "global" else location + "-aiplatform.googleapis.com"
        url = f"https://{host}/v1/projects/{quote(self.project, safe='')}/locations/{quote(location, safe='')}/publishers/google/models/{quote(model, safe='')}:generateContent"
        body, elapsed, key, _ = self._send(url=url, payload=payload, model=model, provider="vertex",
            location=location, stage=request.stage, page_number=request.page_number, attempt=request.attempt,
            timeout_s=request.timeout_s)
        candidates = body.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        text = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", [])
                       if not part.get("thought")).strip()
        usage = body.get("usageMetadata") or {}
        return ModelResponse(text=text, raw=body, input_tokens=int(usage.get("promptTokenCount", 0) or 0),
            output_tokens=int(usage.get("candidatesTokenCount", 0) or 0) + int(usage.get("thoughtsTokenCount", 0) or 0),
            model=model, elapsed_ms=elapsed, finish_reason=str(candidate.get("finishReason", "")), checkpoint_key=key)

    def vision_call(self, frame, *, stage, page_number):
        content = frame.vision_bytes if frame.vision_bytes is not None else frame.image_bytes
        if not isinstance(content, bytes) or not content:
            raise ProviderConfigurationError("ocr.invalid_vision_image")
        payload = {"requests": [{"image": {"content": base64.b64encode(content).decode("ascii")},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}], "imageContext": {"languageHints": ["th", "en"]}}]}
        timeout = int(self.environment.get("OCR_L1_TIMEOUT_SECONDS") or "60")
        body, elapsed, key, bucket = self._send(url="https://vision.googleapis.com/v1/images:annotate",
            payload=payload, model="DOCUMENT_TEXT_DETECTION", provider="vision", location="global", stage=stage,
            page_number=page_number, attempt=0, timeout_s=timeout)
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
