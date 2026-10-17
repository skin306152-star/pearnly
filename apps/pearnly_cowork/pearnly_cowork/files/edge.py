"""原件存在甲骨文时的服务器一侧（ADR-023）：选了 Claude 识别的租户，Cowork 直传的原件（PDF / 图片）存甲骨文、页数与预览在
甲骨文算，与谷歌存储那一套（``files.cloud``，ADR-016）同一个流程：生产只做权限、幂等、记录、签地址，不经手原件字节。

- 开关：站点选了 Claude（``claude_ocr.client.uses_claude``）且配了甲骨文原件存储（运行配置 ``PEARNLY_ORIGINALS_PUBLIC_URL``、
  ``PEARNLY_ORIGINALS_SIGNING_KEY``，以及到甲骨文的内部通道 ``PEARNLY_CLAUDE_OCR_*``）。
- 浏览器经 ``originals.pearnly.com``（Cloudflare 隧道）直传 / 取预览 / 下载；生产经内部通道（只放行生产 IP）请甲骨文核对、
  删除。甲骨文连不上时页面提示稍后再试（不退回生产存）。
- 记录里存 ``oracle://对象名#sha256``（``edge_links.url_of``）。
"""
import base64
import hashlib
import os

from pearnly_cowork.files import edge_links


def _settings():
    base = os.environ.get("PEARNLY_ORIGINALS_PUBLIC_URL", "").strip()
    key = os.environ.get("PEARNLY_ORIGINALS_SIGNING_KEY", "").strip()
    return (base, key) if base and key else None


def enabled():
    """这个站点的直传原件存甲骨文：选了 Claude 识别、配了甲骨文原件存储。"""
    from pearnly_core.claude_ocr import client

    return client.uses_claude() and _settings() is not None


def upload_target(name, byte_size):
    """浏览器直传：签名 PUT 地址（大小锁定为声明的字节数；同一对象只写一次，写过回 412）。"""
    base, key = _settings()
    return {"url": edge_links.upload_url(base, key, name, byte_size),
            "headers": {"Content-Type": "application/octet-stream"}}


def inspect(name, filename):
    """请甲骨文读一遍刚传上去的原件（与服务器收原件同一份检查）：``{"status": "ok", "meta", "md5"}`` 或
    ``{"status": "rejected", "code"}``。连不上抛 ``Deferred``（页面过几秒再来确认）。"""
    from pearnly_core.claude_ocr import client

    return client.internal("/v1/originals/inspect", {"object": name, "filename": filename})


def download_url(stored, filename):
    base, key = _settings()
    name, sha256 = stored
    return edge_links.download_url(base, key, name, sha256, filename)


def preview_url(stored, kind, page):
    base, key = _settings()
    name, sha256 = stored
    return edge_links.preview_url(base, key, name, sha256, kind, page)


def read(stored):
    """取回整份原件字节（导出打包等少数地方）：经内部通道、核对 sha256。"""
    from pearnly_core.claude_ocr import client

    name, sha256 = stored
    data = base64.b64decode(client.internal("/v1/originals/fetch", {"object": name, "sha256": sha256},
                                            timeout=(5, 180))["data"])
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("original checksum mismatch")
    return data


def delete(names):
    """删掉甲骨文上这些原件与预览（删票后，后台任务）。"""
    from pearnly_core.claude_ocr import client

    return client.internal("/v1/originals/delete", {"objects": list(names)})


__all__ = ["delete", "download_url", "enabled", "inspect", "preview_url", "read", "upload_target"]
