"""原件存在甲骨文时的服务器一侧（ADR-023 / ADR-024）：选了 Claude 识别的租户，各产品的原件存甲骨文、页数与预览在甲骨文算，
与谷歌存储那一套（``originals.gcs``，ADR-016）同一个流程：生产只做权限、幂等、记录、签地址，不经手浏览器上传的字节。
由 ``originals.store`` 按租户路线选用，产品不直接调这里。

- 配置：运行配置 ``PEARNLY_ORIGINALS_PUBLIC_URL``、``PEARNLY_ORIGINALS_SIGNING_KEY``，以及到甲骨文的内部通道 ``PEARNLY_CLAUDE_OCR_*``。
- 浏览器经 ``originals.pearnly.com``（Cloudflare 隧道）直传 / 取预览 / 下载；生产经内部通道（只放行生产 IP）请甲骨文核对、
  存（LINE、CC 交来的字节）、删除。甲骨文连不上时抛 ``Deferred``：页面稍后再试、队列重试（不退回生产存）。
- 记录里存 ``oracle://对象名#sha256``（``links.url_of``）。
"""
import base64
import hashlib
import json
import os

from pearnly_core.originals import links


def _settings():
    base = os.environ.get("PEARNLY_ORIGINALS_PUBLIC_URL", "").strip()
    key = os.environ.get("PEARNLY_ORIGINALS_SIGNING_KEY", "").strip()
    return (base, key) if base and key else None


def enabled():
    """配了甲骨文原件存储（选不选用由 ``store.where`` 按租户路线定）。"""
    return _settings() is not None


def upload_target(name, byte_size):
    """浏览器直传：签名 PUT 地址（大小锁定为声明的字节数；同一对象只写一次，写过回 412）。"""
    base, key = _settings()
    return {"url": links.upload_url(base, key, name, byte_size),
            "headers": {"Content-Type": "application/octet-stream"}}


def inspect(name, filename):
    """请甲骨文读一遍刚传上去的原件（与服务器收原件同一份检查）：``{"status": "ok", "meta", "md5"}`` 或
    ``{"status": "rejected", "code"}``。连不上抛 ``Deferred``（页面过几秒再来确认）。"""
    from pearnly_core.claude_ocr import client

    return client.internal("/v1/originals/inspect", {"object": name, "filename": filename})


def put(name, filename, content):
    """生产手上已有字节的原件（LINE、CC 对话交来的文件，ADR-024）：经内部通道交给甲骨文存，存完同一份检查核对。
    回 ``inspect`` 同样的结果；连不上抛 ``Deferred``。请求体边读边编码（`JsonUpload`），网页进程不另存整份 base64。"""
    from pearnly_core.claude_ocr import client

    return client.internal("/v1/originals/put", JsonUpload({"object": name, "filename": filename}, content),
                           timeout=(5, 180))


class JsonUpload:
    """存原件的请求体 ``{"object": …, "filename": …, "data": "<base64>"}``：读的时候才按段做 base64，整份 base64 文本不进内存。
    11-10 实测一份 40MB 原件原来要多占约 130MB（base64 文本、json.dumps、编码成字节各一份）。知道总长度（``__len__``），
    requests 照常带 Content-Length，甲骨文按长度读，收到的与原来逐字节相同。只能读一遍（客户端不重试）。"""

    STEP = 3 * 1024 * 1024  # 3 的倍数：分段编码拼起来与整份编码相同

    def __init__(self, head: dict, content: bytes):
        self._prefix = (json.dumps(head)[:-1] + ', "data": "').encode()
        self._suffix = b'"}'
        self._content = memoryview(content)
        self._length = len(self._prefix) + 4 * ((len(content) + 2) // 3) + len(self._suffix)
        self._next = 0  # 下一段原件从哪里编码
        self._buffer, self._at = self._prefix, 0
        self._ended = False

    def __len__(self):
        return self._length

    def _refill(self) -> bool:
        if self._next < len(self._content):
            piece = self._content[self._next:self._next + self.STEP]
            self._next += len(piece)
            self._buffer, self._at = base64.b64encode(piece), 0
            return True
        if not self._ended:
            self._buffer, self._at, self._ended = self._suffix, 0, True
            return True
        return False

    def read(self, size=-1):
        out = []
        wanted = self._length if size is None or size < 0 else size
        while wanted > 0:
            if self._at >= len(self._buffer) and not self._refill():
                break
            take = self._buffer[self._at:self._at + wanted]
            self._at += len(take)
            wanted -= len(take)
            out.append(take)
        return b"".join(out)


def download_url(stored, filename):
    base, key = _settings()
    name, sha256 = stored
    return links.download_url(base, key, name, sha256, filename)


def preview_url(stored, kind, page):
    base, key = _settings()
    name, sha256 = stored
    return links.preview_url(base, key, name, sha256, kind, page)


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
