"""站点侧入口（ADR-022）：本站点选了 Claude 时，三个产品都从这里读。先交甲骨文的 claude-ocr 服务；连不上时生产本机
用同一份引擎直接调 Anthropic（仍是 Claude 路线，不转谷歌）。Anthropic 出错抛 ``Deferred``，由各产品的工作单 / 队列按时重试。

运行配置（runtime.env，经 compose 进容器）：PEARNLY_CLAUDE_OCR_URL、PEARNLY_CLAUDE_OCR_TOKEN、PEARNLY_CLAUDE_OCR_CERT_SHA256、
PEARNLY_ANTHROPIC_API_KEY。站点配置：pearnly_ocr_engine（google | claude，控制面租户设置写入）。
"""
import base64
import io
import os

import frappe

from pearnly_core.claude_ocr import engine as claude_engine

ENGINES = ("google", "claude")
SEND_TIMEOUT = (5, 240)
_direct = None


def engine():
    value = str(frappe.conf.get("pearnly_ocr_engine") or "google").strip().lower()
    return value if value in ENGINES else "google"


def uses_claude():
    return engine() == "claude"


def _jpeg(data, media_type):
    """图片统一转正、缩到长边 2000、JPEG（评测同一口径）；PDF 每页按 170 DPI 渲染。"""
    from PIL import Image, ImageOps
    out = []
    if media_type == "application/pdf":
        import pymupdf
        for page in list(pymupdf.open(stream=data, filetype="pdf"))[:claude_engine.MAX_PAGES]:
            img = Image.open(io.BytesIO(page.get_pixmap(dpi=170).tobytes("png"))).convert("RGB")
            out.append(_save(img))
        return out
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    return [_save(img)]


def _save(img):
    img.thumbnail((2000, 2000))
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=85)
    return ("image/jpeg", buffer.getvalue())


def prepare(files):
    """files：[(bytes, media_type), ...]（产品原有的「字节 + 类型」次序）→ 按页的 JPEG。"""
    pages = []
    for data, media_type in files:
        pages.extend(_jpeg(data, media_type))
    return pages[:claude_engine.MAX_PAGES]


def _remote(kind, pages, own_tax):
    import requests
    from requests.adapters import HTTPAdapter

    url, token, pin = (os.environ.get(k, "").strip() for k in
                       ("PEARNLY_CLAUDE_OCR_URL", "PEARNLY_CLAUDE_OCR_TOKEN", "PEARNLY_CLAUDE_OCR_CERT_SHA256"))
    if not (url and token and pin):
        return None

    class Pinned(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            kwargs["assert_fingerprint"] = pin
            kwargs["cert_reqs"] = "CERT_NONE"
            return super().init_poolmanager(*args, **kwargs)

    session = requests.Session()
    session.mount("https://", Pinned())
    body = {"kind": kind, "own_tax": own_tax,
            "images": [{"media_type": m, "data": base64.standard_b64encode(d).decode()} for m, d in pages]}
    try:
        response = session.post(url.rstrip("/") + "/v1/read", json=body, timeout=SEND_TIMEOUT,
                                headers={"Authorization": f"Bearer {token}"})
    except requests.RequestException as error:
        frappe.logger("pearnly.ocr").warning("claude-ocr unreachable (%s); reading on this server", type(error).__name__)
        return None
    if response.status_code == 502:
        from pearnly_core.execution.errors import Deferred
        raise Deferred("claude_unavailable")
    if response.status_code != 200:
        frappe.logger("pearnly.ocr").warning("claude-ocr answered HTTP %s; reading on this server", response.status_code)
        return None
    result = response.json()
    result["where"] = "oracle"
    return result


def _local(kind, pages, own_tax):
    global _direct
    import anthropic
    key = os.environ.get("PEARNLY_ANTHROPIC_API_KEY", "").strip()
    if not key:
        from pearnly_core.execution.errors import Deferred
        raise Deferred("claude_not_configured")
    if _direct is None:
        _direct = anthropic.Anthropic(api_key=key, max_retries=3, timeout=180)
    try:
        result = claude_engine.run(_direct, kind, pages, own_tax)
    except anthropic.APIError as error:
        from pearnly_core.execution.errors import Deferred
        frappe.logger("pearnly.ocr").warning("Anthropic error %s", type(error).__name__)
        raise Deferred("claude_unavailable") from error
    result["where"] = "local"
    return result


def read(kind, files, own_tax=None):
    """读一份单据（一个文件的所有页）。返回 claude_ocr.engine.run 的结果加 ``where``；read 为 None 表示读不出。
    在识别工作单里时按内容存断点：同一份重跑直接用上次读到的，不重复付费（与谷歌路线的检查点同一张表）。"""
    import hashlib

    from pearnly_core.execution import work

    pages = prepare(files)
    if not pages:
        return {"read": None, "stages": [], "escalated": False, "why": ["no_pages"], "usd": 0, "seconds": 0, "where": "none"}
    digest = hashlib.sha256(b"".join(data for _media, data in pages) + (own_tax or "").encode()).hexdigest()
    key = f"claude:{kind}:{digest}"
    done = work.checkpoint(key)
    if done and done.get("state") == "done" and done.get("body"):
        return dict(done["body"], where="checkpoint")
    result = _remote(kind, pages, own_tax) or _local(kind, pages, own_tax)
    if result.get("read") is not None:
        work.checkpoint(key, "done", result)
    return result
