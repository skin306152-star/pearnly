"""收 multipart 请求不再把整个请求体读进内存（ADR-028，多产品共用）。

Frappe 16.33.0 的 `frappe.app.make_form_dict` 在任何钩子之前执行，第一行 `request.get_data(as_text=True)` 把整个请求体读进内存、
缓存到请求结束，还转成一份文本。multipart 请求用不上这份文本（只有 JSON 才用），只是顺带缓存住：一份 40MB 上传峰值 +152MB，
常驻 40MB（11-12 生产镜像实测；压测第二轮 15 份同时传，网页容器撞上限进 swap）。

这里只换 multipart 那一路，结果与原实现逐项相同（`tests/test_web_multipart.py` 逐项比对）：
- 解析表单时把读到的原始字节顺手写进临时文件（1MB 以内留在内存），解析完把剩下没读的尾巴也读进去——原实现缓存的就是这些字节；
- 这个请求的 `get_data` 换成按需从临时文件读（`request.data`、OAuth、REST v1、机器通道读原始请求体的地方拿到的与原来一字不差）；
- `frappe.form_dict` 照原实现 else 那一支：查询串 + 表单，去掉 `_`；超限一样是 413（werkzeug 在读流时抛）；
- 临时文件在响应发出后关掉（`request.after_response`）。
其余请求（含不带 Content-Length 的流式 multipart）原样交给原函数。Frappe 的这段源码一变（`MAKE_FORM_DICT_SHA256`），`install` 不替换、照原样收并在 stderr 说明，
候选离线车道的测试同时变红，逼着人按 ADR-028 重新核对。
"""
import functools
import hashlib
import io
import inspect
import sys
import tempfile

# Frappe 16.33.0 `frappe.app.make_form_dict` 源码的 sha256（inspect.getsource）
MAKE_FORM_DICT_SHA256 = "61f9f1293d9ce522f48493080e97a0d533c2bc2c76e23ffcea9e7916fa0fe2ff"
MULTIPART = "multipart/form-data"
# 原始字节在内存里最多留这么多，超了落临时文件（同 werkzeug 自己存上传文件的做法，阈值放宽一点给普通表单）
SPOOL_IN_MEMORY = 1024 * 1024
_SPOOL = "pearnly_multipart_raw"
_original = None


class _Tee:
    """包住 WSGI 输入流：谁读走多少字节，原样写一份进 sink。只提供 read / readline（WSGI 规定的），不提供 readinto，
    werkzeug 的 LimitedStream 于是走 read 那一支，每一块都经过这里。"""

    def __init__(self, source, sink):
        self._source, self._sink = source, sink

    def read(self, *args):
        data = self._source.read(*args)
        if data:
            self._sink.write(data)
        return data

    def readline(self, *args):
        data = self._source.readline(*args)
        if data:
            self._sink.write(data)
        return data

    def __iter__(self):
        return iter(self.readline, b"")

    def close(self):
        close = getattr(self._source, "close", None)
        if close:
            close()


def _get_data(request, spool, cache=True, as_text=False, parse_form_data=False):
    """同 werkzeug `Request.get_data` 在原实现缓存了整个请求体之后的行为：总是回完整的原始字节（cache、parse_form_data 都不改结果）。"""
    spool.seek(0)
    raw = spool.read()
    return raw.decode(errors="replace") if as_text else raw


def _spool_of(request):
    return request.environ.get(_SPOOL)


def make_form_dict(request):
    import frappe

    if request.mimetype != MULTIPART or request.content_length is None:
        # 不带 Content-Length 的流式上传（浏览器不会这样传）也原样交回：原实现在那里按上限静默截断再解析，逐字照搬它不划算
        return _original(request)
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_IN_MEMORY)
    request.environ[_SPOOL] = spool
    request.environ["wsgi.input"] = _Tee(request.environ["wsgi.input"], spool)
    request.get_data = functools.partial(_get_data, request, spool)
    callbacks = getattr(request, "after_response", None)
    if callbacks is not None:
        callbacks.add(spool.close)
    # 与原实现 else 那一支相同（multipart 不是 JSON）
    args = {}
    args.update(request.args or {})
    args.update(request.form or {})
    # 解析器没读到的尾巴（结尾附言、解析失败后剩下的）也读进临时文件：原实现缓存的是整个请求体。werkzeug 解析完把
    # `request.stream` 换成「停下处之后的剩余」，原实现那份剩余在缓存里还读得到，这里同样留一份给 `request.stream`
    rest = request.stream.read()
    request.__dict__["stream"] = io.BytesIO(rest)
    frappe.local.form_dict = frappe._dict(args)
    frappe.local.form_dict.pop("_", None)


def _fingerprint(function):
    return hashlib.sha256(inspect.getsource(function).encode()).hexdigest()


def install():
    """把 `frappe.app.make_form_dict` 换成本模块的；已换过回 True。Frappe 的这段源码与审过的不同就不换、回 False。"""
    global _original
    import frappe.app

    current = frappe.app.make_form_dict
    if current is make_form_dict:
        return True
    if _fingerprint(current) != MAKE_FORM_DICT_SHA256:
        print("pearnly: frappe.app.make_form_dict is not the reviewed Frappe 16.33.0 source; multipart requests are "
              "read into memory as before until it is reviewed again (ADR-028)", file=sys.stderr)
        return False
    _original = current
    frappe.app.make_form_dict = make_form_dict
    return True
