"""原件存在谷歌存储（新加坡）时的服务器一侧（ADR-016）：签发上传 / 下载地址、按需读回原件。

- 开关：站点配置 ``pearnly_cowork_originals_bucket``（桶名）；同时要开着 Cloud Run 识别（``ocr.remote.forward.settings``），
  因为上传后的检查（页数、sha256）由 Cloud Run 读一遍完成。任一没配就照旧经服务器上传。
- 身份：识别服务账号的密钥文件（``ocr.credentials_path``）。签名在本机算（RSA），不调谷歌接口。
- 对象名 ``<站点>/originals/<原件名><扩展名>``，原件名由工作空间 + 上传人 + 幂等键算出（``service.source_name``），
  别人拿不到也猜不出；上传带 ``x-goog-if-generation-match: 0``，写过一次就不能覆盖。记录里存
  ``gs://桶/对象#版本号``，读回时按版本号取，内容不会被换掉。
- 桶没有自动删除（原件和本机一样一直保留）。
"""
from datetime import datetime, timezone
import hashlib
import threading
from urllib.parse import quote

import frappe

SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
HOST = "storage.googleapis.com"
UPLOAD_SECONDS = 15 * 60
DOWNLOAD_SECONDS = 5 * 60
_lock = threading.Lock()
_credentials = {}


def bucket():
    """开着就返回桶名，否则 ``None``。"""
    value = str((frappe.conf or {}).get("pearnly_cowork_originals_bucket") or "").strip()
    return value or None


def object_name(site, source_name, suffix):
    return f"{site}/originals/{source_name}{suffix}"


def url_of(bucket_name, name, generation):
    return f"gs://{bucket_name}/{name}#{int(generation)}"


def parse_url(value):
    """``gs://桶/对象#版本号`` → ``(桶, 对象, 版本号)``；不是这种地址返回 ``None``。"""
    if not isinstance(value, str) or not value.startswith("gs://") or "#" not in value:
        return None
    location, _sep, generation = value[5:].rpartition("#")
    bucket_name, _sep, name = location.partition("/")
    if not bucket_name or not name or not generation.isdigit():
        return None
    return bucket_name, name, int(generation)


def _service_account():
    from google.oauth2 import service_account
    from pearnly_core import ocr

    path = ocr.credentials_path()
    with _lock:
        credentials = _credentials.get(path)
        if credentials is None:
            credentials = service_account.Credentials.from_service_account_file(path, scopes=[SCOPE])
            _credentials[path] = credentials
        return credentials


def _token():
    from google.auth.transport.requests import Request

    credentials = _service_account()
    with _lock:
        if not credentials.valid:
            credentials.refresh(Request())
        return credentials.token


def sign(method, bucket_name, name, *, seconds, headers=None, query=None, now=None, credentials=None):
    """V4 签名地址（GOOG4-RSA-SHA256）。``headers`` 里的头要原样随请求发出（签进去了）。"""
    credentials = credentials or _service_account()
    now = now or datetime.now(timezone.utc)
    timestamp, datestamp = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{datestamp}/auto/storage/goog4_request"
    path = "/" + bucket_name + "/" + quote(name, safe="/~")
    signed = {"host": HOST, **{key.lower(): str(value) for key, value in (headers or {}).items()}}
    signed_headers = ";".join(sorted(signed))
    params = {"X-Goog-Algorithm": "GOOG4-RSA-SHA256",
              "X-Goog-Credential": f"{credentials.service_account_email}/{scope}",
              "X-Goog-Date": timestamp, "X-Goog-Expires": str(int(seconds)),
              "X-Goog-SignedHeaders": signed_headers, **(query or {})}
    canonical_query = "&".join(f"{quote(key, safe='~')}={quote(str(value), safe='~')}"
                               for key, value in sorted(params.items()))
    canonical_headers = "".join(f"{key}:{' '.join(signed[key].split())}\n" for key in sorted(signed))
    canonical = "\n".join((method, path, canonical_query, canonical_headers, signed_headers, "UNSIGNED-PAYLOAD"))
    to_sign = "\n".join(("GOOG4-RSA-SHA256", timestamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    signature = credentials.signer.sign(to_sign.encode()).hex()
    return f"https://{HOST}{path}?{canonical_query}&X-Goog-Signature={signature}"


def upload_target(bucket_name, name, byte_size, content_type="application/octet-stream"):
    """浏览器直传：签名 PUT 地址和必须带上的头（大小锁定为声明的字节数，已有对象不覆盖）。"""
    headers = {"Content-Type": content_type, "x-goog-if-generation-match": "0",
               "x-goog-content-length-range": f"{int(byte_size)},{int(byte_size)}"}
    return {"url": sign("PUT", bucket_name, name, seconds=UPLOAD_SECONDS, headers=headers), "headers": headers}


def download_url(stored, filename):
    """原件下载：短时签名 GET 地址（按版本号取、作为附件下载）。"""
    bucket_name, name, generation = stored
    disposition = "attachment; filename*=UTF-8''" + quote(filename, safe="")
    return sign("GET", bucket_name, name, seconds=DOWNLOAD_SECONDS,
                query={"generation": str(generation), "response-content-disposition": disposition,
                       "response-content-type": "application/octet-stream"})


def preview_url(bucket_name, name, generation, content_type):
    """识别时存好的一页预览图：短时签名 GET 地址（按版本号取、页面里直接显示），同原件下载一样 5 分钟有效。"""
    filename = "invoice-page." + ("jpg" if content_type == "image/jpeg" else "png")
    return sign("GET", bucket_name, name, seconds=DOWNLOAD_SECONDS,
                query={"generation": str(int(generation)), "response-content-type": content_type,
                       "response-content-disposition": f"inline; filename={filename}"})


def read(stored, *, http=None):
    """按版本号读回原件字节（预览、本机识别兜底用）。"""
    import requests

    bucket_name, name, generation = stored
    response = (http or requests).get(f"https://{HOST}/storage/v1/b/{bucket_name}/o/{quote(name, safe='')}",
        params={"alt": "media", "generation": str(generation)},
        headers={"Authorization": "Bearer " + _token()}, timeout=(10, 120))
    response.raise_for_status()
    return response.content
