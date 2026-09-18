# -*- coding: utf-8 -*-
"""OCR 引擎（共用底座，三个产品都用）。

用户说得很明确：**"OCR 是所有 app 的共享底座，不是可选项"**。所以模型调用、
档位、密钥这些放在共用层；"某个产品要抽哪些字段"放各自产品的 App 里
（例如 DMS 的身份证在 `pearnly_dms/ocr_id_card.py`）。

照老产品搬（`services/ocr/model_client.py` + `gemini_models.py` + `engine_policy.py`），
**两个档原样保留**（用户口中的 A 档 / B 档）：

| 档 | 老产品叫法 | 读取臂 | 升级/兜底臂 |
|---|---|---|---|
| **A 档** | `enterprise`（企业档） | `gemini-3.8-flash` | `gemini-3.8-flash` |
| **B 档** | `economy`（省钱档） | `gemini-3.1-flash-lite` | `gemini-3.8-flash` |

⚠️ **一处与老产品的差异，必须记住**：老产品的 `enterprise` 档除了换模型，还把
**后端切到 Vertex**（`MODE_BACKENDS: {"enterprise": "vertex"}`），那是"数据驻留"
的合规选择。新平台现在只有一把 **Gemini API key**（从 Secret Manager
`pearnly-erp-gemini-api-key` 取的），走的是公开的 `generativelanguage.googleapis.com`。
也就是说：**模型档位照搬了，数据驻留这条路还没接**。将来要合规时，在
`_call_model()` 里加 Vertex 分支即可（老产品 `providers/vertex.py` 有实现可参考）。

密钥与档位都放站点配置（`frappe.conf`），不硬编码：

  pearnly_ocr_api_key    Gemini API key
  pearnly_ocr_mode       enterprise（默认）| economy
"""

import base64
import json

import frappe
import requests
from frappe import _

# mode → 档位 → 模型名。**照抄老产品的 MODE_MODEL_MAPS**，不改数字。
MODE_MODEL_MAPS: dict[str, dict[str, str]] = {
    "enterprise": {   # A 档
        "flash_lite": "gemini-3.8-flash",
        "fallback": "gemini-3.8-flash",
    },
    "economy": {      # B 档
        "flash_lite": "gemini-3.1-flash-lite",
        "fallback": "gemini-3.8-flash",
    },
}

DEFAULT_MODE = "enterprise"

DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_TIMEOUT_SECONDS = 120


class OcrError(RuntimeError):
    """OCR 调用失败。调用方据此决定是回"待人工核对"还是报错。"""


def api_key() -> str:
    return str(frappe.conf.get("pearnly_ocr_api_key") or "").strip()


def endpoint() -> str:
    """调用地址。可换（站点配置 `pearnly_ocr_endpoint`）有两个实打实的用处：

    1. 以后接 **Vertex**（老产品生产就是 Vertex@asia-southeast1）时换地址即可，
       不用改代码；
    2. 验证时指到一个本地桩服务器，就能在**没有可用凭据**的情况下断言
       "请求长什么样、两档是怎么升的" —— 这是这一层能独立验的部分。
    """
    return str(frappe.conf.get("pearnly_ocr_endpoint") or "").strip() or DEFAULT_ENDPOINT


def mode() -> str:
    m = str(frappe.conf.get("pearnly_ocr_mode") or "").strip().lower()
    return m if m in MODE_MODEL_MAPS else DEFAULT_MODE


def model_for(tier: str) -> str:
    """档位 → 模型名。tier 取 `flash_lite`（读取臂）或 `fallback`（升级臂）。"""
    return MODE_MODEL_MAPS[mode()].get(tier) or MODE_MODEL_MAPS[DEFAULT_MODE][tier]


def is_configured() -> bool:
    """没配 key 就是没配 —— 界面上要如实说"OCR 不可用"，不要假装能识别。"""
    return bool(api_key())


def detect_image_mime(data: bytes) -> str:
    """按魔数判图片类型（老产品的 `_detect_image_mime`，一字未改）。"""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"%PDF":
        return "application/pdf"
    return "image/jpeg"


def json_from_images(prompt: str, images: list[tuple[bytes, str]], *,
                     model: str, timeout_s: int = DEFAULT_TIMEOUT_SECONDS,
                     max_tokens: int = 16384) -> dict | None:
    """图片（或 PDF）+ 提示词 → JSON dict。失败返回 None，由调用方决定是否升档。

    与老产品一致的三点：temperature=0、要求模型直接吐 JSON
    （`responseMimeType: application/json`）、一次调用不做内部重试
    （重试交给上层的"升档再读一次"）。
    """
    key = api_key()
    if not key:
        raise OcrError(_("没有配 OCR 密钥（站点配置 pearnly_ocr_api_key）"))

    parts: list[dict] = [{"text": prompt}]
    for data, mime in images:
        parts.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(data).decode("ascii"),
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }

    try:
        resp = requests.post(
            endpoint().format(model=model),
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=payload,
            timeout=timeout_s,
        )
    except requests.RequestException as exc:
        frappe.logger("pearnly.ocr").warning(f"ocr request failed model={model}: {exc}")
        return None

    if resp.status_code != 200:
        frappe.logger("pearnly.ocr").warning(
            f"ocr http {resp.status_code} model={model}: {resp.text[:300]}"
        )
        return None

    try:
        body = resp.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        frappe.logger("pearnly.ocr").warning(f"ocr unparsable model={model}: {exc}")
        return None


def try_with_fallback(call, *, ok, label: str = "ocr"):
    """先读取臂、不合格再升级臂读一次（老产品 `try_with_fallback` 的语义）。

    `call(model_name)` 返回 dict 或 None；`ok(data)` 判断这次读得算不算数。
    两档都不行返回 None —— **由调用方决定**是回"待人工核对"还是报错，
    这一层不替业务下结论。
    """
    primary = model_for("flash_lite")
    data = call(primary)
    if data is not None and ok(data):
        return data

    fallback = model_for("fallback")
    if fallback and fallback != primary:
        frappe.logger("pearnly.ocr").info(f"{label}: 读取臂不合格，升档 {primary} → {fallback}")
        data2 = call(fallback)
        if data2 is not None and ok(data2):
            return data2
        return data2 if data2 is not None else data

    return data


@frappe.whitelist()
def api_status():
    """OCR 现在到底能不能用。界面据此显示"可用/没配密钥"，不猜。"""
    return {
        "configured": is_configured(),
        "mode": mode(),
        "models": MODE_MODEL_MAPS[mode()],
        "modes": sorted(MODE_MODEL_MAPS.keys()),
    }


@frappe.whitelist(methods=["POST"])
def api_selftest():
    """真发一次最小请求，验证密钥与模型名都对。

    为什么不做成"只检查有没有 key"：key 存在但**模型名过期**是很常见的事
    （实际踩到：`gemini-2.5-flash` 对新建用户已经不可用，返回 404），
    只看"有没有配"会给出一个错误的"可用"。
    """
    if not is_configured():
        return {"ok": False, "reason": "no_key"}
    model = model_for("flash_lite")
    try:
        resp = requests.post(
            endpoint().format(model=model),
            headers={"x-goog-api-key": api_key(), "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": "reply with the single word: ok"}]}]},
            timeout=30,
        )
    except requests.RequestException as exc:
        return {"ok": False, "reason": "network", "detail": str(exc)[:200], "model": model}

    if resp.status_code != 200:
        return {
            "ok": False,
            "reason": "http",
            "http_status": resp.status_code,
            "model": model,
            "detail": resp.text[:300],
        }
    return {"ok": True, "model": model, "mode": mode()}
