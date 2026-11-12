"""磁盘上的一份上传（多产品共用，ADR-028 第 2 步）：上传接口不再把文件读成 bytes，按块写到磁盘，往下各层按路径 / 按块读。

一份 40MB 上传原来在网页进程里被读成 bytes 再一路传下去（交引擎、存原件、读页数、留存），整个请求都占着 40MB；
现在各层同时接受 ``bytes`` 与 :class:`LocalFile`：
- 原件检查（`checks.inspect`）按路径打开 PDF / 图片，摘要按块算；
- 交甲骨文存原件（`oracle.JsonUpload`）按块编码；
- 交引擎（Anthropic Files）交文件句柄，SDK 边读边发；
- 本站留存按块拷贝。
只有确实要整份字节的老路（HEIC 照片转 JPEG、站点没配原件层时存本机 File、谷歌存储）才 `as_bytes` 整份读，那些都是小文件或不在生产走的路。
"""
import hashlib
import os
import shutil
import tempfile

CHUNK = 1024 * 1024


class LocalFile:
    """磁盘上的一份文件：路径与大小，``len()`` 是字节数；按块读，不整份进内存。``owned`` 为真时由本对象负责删掉临时文件。"""

    def __init__(self, path, owned=False):
        self.path = os.fspath(path)
        self.size = os.path.getsize(self.path)
        self.owned = owned

    def __len__(self):
        return self.size

    def __bool__(self):
        return self.size > 0

    def reader(self):
        """以二进制只读打开（调用方负责关）。"""
        return open(self.path, "rb")

    def head(self, size=64) -> bytes:
        with self.reader() as handle:
            return handle.read(size)

    def chunks(self, size=CHUNK):
        with self.reader() as handle:
            for block in iter(lambda: handle.read(size), b""):
                yield block

    def read(self) -> bytes:
        with self.reader() as handle:
            return handle.read()

    def copy_to(self, target):
        with self.reader() as source, open(target, "wb") as sink:
            shutil.copyfileobj(source, sink, CHUNK)

    def remove(self):
        if self.owned:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass


def is_local(content) -> bool:
    return isinstance(content, LocalFile)


def as_bytes(content) -> bytes:
    """整份字节（只给确实要整份的老路用，见文件头）。"""
    return content.read() if is_local(content) else content


def head(content, size=64) -> bytes:
    return content.head(size) if is_local(content) else bytes(content[:size])


def digest(content, algorithm="sha256") -> str:
    found = hashlib.new(algorithm)
    if is_local(content):
        for block in content.chunks():
            found.update(block)
    else:
        found.update(content)
    return found.hexdigest()


def from_upload(file_storage, max_bytes):
    """把请求里的一份上传（werkzeug ``FileStorage``，它自己已按块落在临时文件里）按块拷到本进程的临时文件，回 ``LocalFile``；
    超过 ``max_bytes`` 回 ``None``（调用方照原来那句报「太大」）。临时文件在响应发出后删掉（`request.after_response`），
    没有请求上下文时由调用方 ``remove()``。"""
    import frappe

    stream = file_storage.stream
    handle = tempfile.NamedTemporaryFile(prefix="pearnly-upload-", delete=False)
    written = 0
    try:
        with handle:
            for block in iter(lambda: stream.read(CHUNK), b""):
                written += len(block)
                if written > max_bytes:
                    break
                handle.write(block)
    except BaseException:
        os.remove(handle.name)
        raise
    if written > max_bytes:
        os.remove(handle.name)
        return None
    local = LocalFile(handle.name, owned=True)
    callbacks = getattr(getattr(frappe.local, "request", None), "after_response", None)
    if callbacks is not None:
        callbacks.add(local.remove)
    return local
