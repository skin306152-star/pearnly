"""站点侧入口（ADR-022）：本站点选了 Claude 时，三个产品都从这里读。先交甲骨文的 claude-ocr 服务；连不上时生产本机
用同一份引擎直接调 Anthropic（仍是 Claude 路线，不转谷歌）。Anthropic 出错抛 ``Deferred``，由各产品的工作单 / 队列按时重试。

运行配置（runtime.env，经 compose 进容器）：PEARNLY_CLAUDE_OCR_URL、PEARNLY_CLAUDE_OCR_TOKEN、PEARNLY_CLAUDE_OCR_CERT_SHA256、
PEARNLY_ANTHROPIC_API_KEY。站点配置：pearnly_ocr_engine（google | claude，控制面租户设置写入）。
"""
import base64
import os

import frappe

from pearnly_core.claude_ocr import engine as claude_engine, pages as page_prep, segments

ENGINES = ("google", "claude")
SEND_TIMEOUT = (5, 240)
_direct = None


def engine():
    try:
        value = str(frappe.conf.get("pearnly_ocr_engine") or "google").strip().lower()
    except RuntimeError:  # 没有站点上下文（离线调用、测试）：按默认的谷歌路线
        return "google"
    return value if value in ENGINES else "google"


def uses_claude():
    return engine() == "claude"


def prepare(files, first=1, last=None):
    """files：[(bytes, media_type), ...] → 第 ``first`` 到 ``last`` 页的 JPEG（每页转正、缩图，见 ``claude_ocr.pages``）。"""
    return page_prep.prepare(files, skipped=lambda: frappe.logger("pearnly.ocr").info("orient skipped", exc_info=True),
                             first=first, last=last)


class GoogleRefused(RuntimeError):
    """本站点选了 Claude 识别，却有入口要调谷歌（Vertex / Vision / Document AI / Cloud Run）：一律拒绝，漏接的入口当场报错，
    不会悄悄走谷歌（ADR-022）。"""


def refuse_google(label):
    """付费谷歌识别调用之前调用（``pearnly_core.execution.provider.post`` 与 Cowork 交 Cloud Run 的 ``forward``）。"""
    if uses_claude():
        raise GoogleRefused(f"this site reads with Claude (pearnly_ocr_engine = claude); refusing a Google call ({label})")


def _session():
    """到甲骨文服务的会话（只认钉死的证书指纹、带令牌）；没配返回 ``None``。"""
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
    # 自签证书只认钉死的指纹：requests 默认的 CA 校验会盖掉上面的 CERT_NONE，把甲骨文的自签证书拒掉（10-18 闸门实测：
    # 每次都退回本机直调）。关掉 CA 校验后指纹照样逐次核对，指纹不对照样拒绝连接。
    session.verify = False
    session.headers["Authorization"] = f"Bearer {token}"
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return session, url.rstrip("/")


def internal(path, body, timeout=(5, 60)):
    """调甲骨文服务的内部接口（只放行生产 IP）：回 JSON。没配、连不上、对方出错都抛 ``Deferred``（调用方让页面 / 队列稍后再来）。
    ``body`` 是 dict 就按 JSON 发；是可读的流（知道长度，例如存原件的 ``originals.oracle.JsonUpload``）就原样边读边发。"""
    import requests
    from pearnly_core.execution.errors import Deferred

    found = _session()
    if found is None:
        raise Deferred("oracle_not_configured", 30)
    session, base = found
    try:
        sending = ({"data": body, "headers": {"Content-Type": "application/json"}} if hasattr(body, "read")
                   else {"json": body})
        response = session.post(base + path, timeout=timeout, **sending)
    except requests.RequestException as error:
        frappe.logger("pearnly.ocr").warning("oracle edge unreachable (%s) %s", type(error).__name__, path)
        raise Deferred("oracle_unreachable", 10) from error
    if response.status_code != 200:
        frappe.logger("pearnly.ocr").warning("oracle edge answered HTTP %s %s", response.status_code, path)
        raise Deferred("oracle_error", 10)
    return response.json()


def _remote(kind, pages, own_tax, text=None):
    import requests

    found = _session()
    if found is None:
        return None
    session, base = found
    body = {"kind": kind, "own_tax": own_tax, "text": text,
            "images": [{"media_type": m, "data": base64.standard_b64encode(d).decode()} for m, d in pages]}
    try:
        response = session.post(base + "/v1/read", json=body, timeout=SEND_TIMEOUT)
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


def _local(kind, pages, own_tax, text=None):
    global _direct
    import anthropic
    key = os.environ.get("PEARNLY_ANTHROPIC_API_KEY", "").strip()
    if not key:
        from pearnly_core.execution.errors import Deferred
        raise Deferred("claude_not_configured")
    if _direct is None:
        _direct = anthropic.Anthropic(api_key=key, max_retries=3, timeout=180)
    try:
        result = claude_engine.run(_direct, kind, pages, own_tax, text)
    except anthropic.APIError as error:
        from pearnly_core.execution.errors import Deferred
        frappe.logger("pearnly.ocr").warning("Anthropic error %s", type(error).__name__)
        raise Deferred("claude_unavailable") from error
    result["where"] = "local"
    return result


def _checkpointed(key, call):
    """同一个检查点的结果直接用（不重复付费）；没有就调 ``call()``，读出来了存下。"""
    from pearnly_core.execution import work

    done = work.checkpoint(key)
    if done and done.get("state") == "done" and done.get("body"):
        return dict(done["body"], where="checkpoint")
    result = call()
    if result.get("read") is not None:
        work.checkpoint(key, "done", result)
    return result


def read(kind, files, own_tax=None, text=None, on_pages=None):
    """读一份单据（一个文件的所有页；对账文档一次一页）。``text``：表格文件导出的文字，代替 ``files``。
    返回 claude_ocr.engine.run 的结果加 ``where``；read 为 None 表示读不出。超过一次调用页数的文件按段读完再合并
    （``segments``，每段一次调用、一个检查点）；不超过的与原来一次调用完全相同。
    在识别工作单里时每段按内容存断点：同一份重跑直接用上次读到的，不重复付费（与谷歌路线的检查点同一张表）。
    ``on_pages(已读完几页, 共几页)``：分段读时每段读完报一次真实进度（``segments.read_all``）。"""
    import hashlib

    total = page_prep.total(files) if files else 0
    if not total and not text:
        return {"read": None, "stages": [], "escalated": False, "why": ["no_pages"], "usd": 0, "seconds": 0, "where": "none"}

    def part(first, last):
        pages = prepare(files, first, last) if total else []
        digest = hashlib.sha256(b"".join(data for _media, data in pages) + (text or "").encode()
                                + (own_tax or "").encode()).hexdigest()
        return _checkpointed(f"claude:{kind}:{digest}",
                             lambda: _remote(kind, pages, own_tax, text) or _local(kind, pages, own_tax, text))

    return segments.read_all(max(total, 1), part, on_pages)


def _edge_part(key, body):
    """原件在甲骨文的一段：第一段请求与检查点同原来一次读整份的（不带页范围），后面的段带 ``page_range``、检查点键加页范围。
    甲骨文回的页范围对不上（还是旧版，不认页范围）抛 ``Deferred``，稍后重试，不把前几页当成后面的段记账。"""
    from pearnly_core.execution import work
    from pearnly_core.execution.errors import Deferred

    def part(first, last):
        ranged = first > 1
        here = key + (f":p{first}-{last}" if ranged else "")

        def call():
            return dict(internal("/v1/read", body | ({"page_range": [first, last]} if ranged else {}), timeout=SEND_TIMEOUT),
                        where="oracle")

        result = _checkpointed(here, call)
        if "page_total" not in result and result.get("where") == "checkpoint":
            result = call()  # 部署前存的检查点不知道一共几页：这一段重读
            if result.get("read") is not None:
                work.checkpoint(here, "done", result)
        if "page_total" not in result or (ranged and result.get("page_range") != [first, last]):
            raise Deferred("oracle_outdated", 60)
        return result
    return part


def read_originals(kind, originals, own_tax=None, on_pages=None):
    """一张单据的几份原件都存在甲骨文（ERP 一次识别带几个附件）：交 ``[(对象名, sha256), …]``，甲骨文按次序读、每份先转正
    （同 ERP 在生产上先 ``ocr.orient`` 再交 :func:`read`）、再渲染，原件字节不经过生产。几份接起来超过一次调用的页数时按段读
    （同 :func:`read`）。检查点同 :func:`read`。"""
    key = f"claude:{kind}:originals:{','.join(sha for _name, sha in originals)}:{own_tax or ''}"
    body = {"kind": kind, "own_tax": own_tax, "orient": True,
            "originals": [{"object": name, "sha256": sha} for name, sha in originals]}
    return segments.read_all(None, _edge_part(key, body), on_pages)


def read_original(kind, object_name, sha256, own_tax=None, page=None, on_pages=None):
    """读一份存在甲骨文上的原件（原件层 ``pearnly_core.originals``）：只交对象名与 sha256，甲骨文自己读文件、
    转正、渲染、交 Claude —— 原件字节不经过生产。只走甲骨文：连不上抛 ``Deferred``，工作单稍后重试（原件本来就在那台上）。
    ``page``：对账文档一次读一页（同 :func:`read` 逐页交）；不给时整份读，超过一次调用的页数按段读（同 :func:`read`）。
    检查点同 :func:`read`，同一份重跑不重复付费。"""
    key = f"claude:{kind}:original:{sha256}:{own_tax or ''}"
    original = {"object": object_name, "sha256": sha256}
    if page is not None:
        return _checkpointed(key + f":p{int(page)}", lambda: dict(
            internal("/v1/read", {"kind": kind, "own_tax": own_tax, "original": original | {"page": int(page)}},
                     timeout=SEND_TIMEOUT), where="oracle"))
    return segments.read_all(None, _edge_part(key, {"kind": kind, "own_tax": own_tax, "original": original}), on_pages)
