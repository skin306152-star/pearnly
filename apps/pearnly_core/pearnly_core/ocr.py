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

**后端**也照老产品分两种（`OCR_LLM_BACKEND`）：

| 后端 | 认证 | 地址 |
|---|---|---|
| `vertex`（**默认**，同生产） | GCP 服务账号 | `{location}-aiplatform.googleapis.com` |
| `aistudio` | Gemini API key | `generativelanguage.googleapis.com` |

生产的配置是 `OCR_LLM_BACKEND=vertex` + `GCP_PROJECT=pearnly` +
`VERTEX_LOCATION=asia-southeast1`（见本机 `~/.config/pearnly-migration/20260905/web.env`），
所以新平台默认也走 Vertex —— 那不只是换地址，是**数据驻留**这条合规属性的来源。

⚠️ **区域规则不能自己想当然**：老产品 `_GLOBAL_ONLY_PREFIXES` 记录了一批只在
**global 端点**发布的模型（2.5 / 3.1 / 3.6 / 3.8），就近区域（asia-southeast1）
会 404。实测确认：`gemini-3.8-flash`、`gemini-3.1-flash-lite` 必须走 global，
而 `gemini-3.5-flash` 走 asia-southeast1。所以区域要**按模型**选，不是全域一个值。

密钥与档位都放站点配置（`frappe.conf`），不硬编码：

  pearnly_ocr_backend      vertex（默认）| aistudio
  pearnly_ocr_api_key      aistudio 用
  pearnly_ocr_credentials  vertex 用：服务账号 JSON 的路径
  pearnly_ocr_project      vertex 用：GCP 项目（默认 pearnly）
  pearnly_ocr_location     vertex 用：就近区域（默认 asia-southeast1）
  pearnly_ocr_mode         enterprise（默认）| economy
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

DEFAULT_BACKEND = "vertex"
DEFAULT_PROJECT = "pearnly"
DEFAULT_LOCATION = "asia-southeast1"

# 只在 **global 端点**发布的模型前缀（照抄老产品 `providers/vertex.py` 的
# `_GLOBAL_ONLY_PREFIXES`）。落错区域的表现是 404 或空 JSON —— 很难从报错看出来。
_GLOBAL_ONLY_PREFIXES = ("gemini-2.5", "gemini-3.1", "gemini-3.6", "gemini-3.8")


class OcrError(RuntimeError):
    """OCR 调用失败。调用方据此决定是回"待人工核对"还是报错。"""


class OcrUnavailable(OcrError):
    """**调不动模型**：网络不通、限流、配额耗尽、模型名不存在。

    为什么要跟"模型读了但没读出内容"分开：那是两件完全不同的事 ——

    · 读不出内容 → 业务上的"这张图不合格"，该标 `needs_review` 让人工看；
    · 调不动模型 → 基础设施问题（配额/网络），**不该伪装成"图不合格"**，
      否则运维看不到、用户只会以为"系统认不出我的卡"。

    实测踩到：连续跑 8 张图，后几张被 Vertex 限流返回 **429**；
    当时的实现一律返回 None，于是限流被当成"读不出"—— 一次真实的配额问题
    被显示成了"证件识别不了"。
    """


def api_key() -> str:
    return str(frappe.conf.get("pearnly_ocr_api_key") or "").strip()


def backend() -> str:
    b = str(frappe.conf.get("pearnly_ocr_backend") or "").strip().lower()
    return b if b in ("vertex", "aistudio") else DEFAULT_BACKEND


def project() -> str:
    return str(frappe.conf.get("pearnly_ocr_project") or "").strip() or DEFAULT_PROJECT


def location() -> str:
    return str(frappe.conf.get("pearnly_ocr_location") or "").strip() or DEFAULT_LOCATION


def credentials_path() -> str:
    import os

    return (
        str(frappe.conf.get("pearnly_ocr_credentials") or "").strip()
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    )


def location_for_model(model: str) -> str:
    """按模型选区域。global-only 的那批必须走 `global`，其余留就近区域。"""
    m = (model or "").lower()
    if m.startswith(_GLOBAL_ONLY_PREFIXES):
        return "global"
    return location()


def vertex_url(model: str) -> str:
    loc = location_for_model(model)
    host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/projects/{project()}/locations/{loc}"
        f"/publishers/google/models/{model}:generateContent"
    )


_token_cache: dict = {}


def access_token() -> str:
    """服务账号 → access token（带缓存，缓存到快过期为止）。

    需要 `google-auth`（已随镜像装）。**没有凭据就明确报错**，
    不要悄悄退回一个不可用的路径。
    """
    path = credentials_path()
    if not path:
        raise OcrError(_("No service account configured (site config pearnly_ocr_credentials)"))

    cached = _token_cache.get(path)
    now = frappe.utils.now_datetime()
    if cached and cached["expiry"] > now:
        return cached["token"]

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            path, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(Request())
    except ImportError as exc:
        raise OcrError(_("google-auth is missing (the image should have it): {0}").format(exc)) from exc
    except Exception as exc:  # 文件不存在/格式不对/被拒
        raise OcrError(_("The service account could not exchange its token: {0}").format(str(exc)[:200])) from exc

    # 提前 5 分钟过期，避免边界上刚好用到失效令牌
    expiry = creds.expiry
    if expiry is not None:
        from datetime import timedelta

        expiry = expiry - timedelta(minutes=5)
    _token_cache[path] = {"token": creds.token, "expiry": expiry or now}
    return creds.token


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
    """能不能用。**按后端判**，不是一律看 key。

    vertex 后端用的是服务账号文件、`api_key` 对它毫无意义（老产品那句
    "api_key 参数对 Vertex 无意义(忽略)"）；反过来只配了 key 而没配服务账号时，
    vertex 后端也是不可用的。所以这里必须分叉，否则会给出一个错误的"可用"。
    """
    if backend() == "vertex":
        import os

        path = credentials_path()
        return bool(path and os.path.exists(path))
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
    if backend() == "vertex":
        url = vertex_url(model)
        headers = {
            "Authorization": "Bearer " + access_token(),
            "Content-Type": "application/json",
        }
    else:
        key = api_key()
        if not key:
            raise OcrError(_("No OCR key configured (site config pearnly_ocr_api_key)"))
        url = endpoint().format(model=model)
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

    parts: list[dict] = [{"text": prompt}]
    for data, mime in images:
        parts.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(data).decode("ascii"),
            }
        })

    payload = {
        # ⚠️ `role: "user"` 不能省：AI Studio 端点容忍缺 role，**Vertex 会直接 400**
        # （`Please use a valid role: user, model.`）。实测踩到。
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
    except requests.RequestException as exc:
        frappe.logger("pearnly.ocr").warning(f"ocr request failed model={model}: {exc}")
        raise OcrUnavailable(_("Cannot reach the OCR service: {0}").format(str(exc)[:200])) from exc

    if resp.status_code != 200:
        frappe.logger("pearnly.ocr").warning(
            f"ocr http {resp.status_code} model={model}: {resp.text[:300]}"
        )
        raise OcrUnavailable(
            _("The OCR service returned HTTP {0}: {1}").format(resp.status_code, resp.text[:200])
        )

    try:
        body = resp.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        # 走到这里说明**服务是通的**（HTTP 200），只是这次输出没法用
        # （被截断、不是 JSON、空候选）。返回 None 让上层决定要不要升档。
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
        "backend": backend(),
        "mode": mode(),
        "models": MODE_MODEL_MAPS[mode()],
        "modes": sorted(MODE_MODEL_MAPS.keys()),
        # 每个模型实际会打哪个区域 —— 这就是"落错区域 404"那条坑的可观测面
        "locations": {
            tier: location_for_model(m) for tier, m in MODE_MODEL_MAPS[mode()].items()
        },
    }


@frappe.whitelist(methods=["POST"])
def api_selftest():
    """真发一次最小请求，验证密钥与模型名都对。

    为什么不做成"只检查有没有 key"：key 存在但**模型名过期**是很常见的事
    （实际踩到：`gemini-2.5-flash` 对新建用户已经不可用，返回 404），
    只看"有没有配"会给出一个错误的"可用"。
    """
    model = model_for("flash_lite")
    if backend() == "vertex":
        if not is_configured():
            return {"ok": False, "reason": "no_credentials", "backend": "vertex",
                    "hint": _("Site config pearnly_ocr_credentials must point at an existing service-account JSON")}
        try:
            token = access_token()
        except OcrError as exc:
            return {"ok": False, "reason": "credentials", "backend": "vertex",
                    "detail": str(exc)[:300]}
        url = vertex_url(model)
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    else:
        if not is_configured():
            return {"ok": False, "reason": "no_key", "backend": "aistudio"}
        url = endpoint().format(model=model)
        headers = {"x-goog-api-key": api_key(), "Content-Type": "application/json"}

    payload = {"contents": [{"role": "user", "parts": [{"text": "reply with the single word: ok"}]}]}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.RequestException as exc:
        return {"ok": False, "reason": "network", "detail": str(exc)[:200], "model": model}

    if resp.status_code != 200:
        return {
            "ok": False,
            "reason": "http",
            "http_status": resp.status_code,
            "model": model,
            "location": location_for_model(model),
            "detail": resp.text[:300],
        }
    return {
        "ok": True,
        "backend": backend(),
        "model": model,
        "location": location_for_model(model),
        "mode": mode(),
    }
