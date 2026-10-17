"""原件存在谷歌存储时请 Cloud Run 读原件（ADR-016 / ADR-024）：上传后核对、出一页预览。

请求格式与解析（纯约定）和站点侧调用都在这里：Cowork 与 ERP 的站点共用，Cloud Run 那一侧（``pearnly_cowork.ocr.remote.runner``）
用同一份约定解析。站点配置 ``pearnly_cowork_ocr_remote`` = {"enabled": 1, "url": "https://…run.app"}：识别服务的地址，
原件核对 / 预览也在那里。对象名 ``<站点>/originals/<id><扩展名>``，``<id>`` 是 Cowork ``CWS-`` 或 ERP ``ERO-`` 加 48 位十六进制。
"""
import re
import threading

VERSION = 1
SITE = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}")
ORIGINAL = re.compile(r"originals/(?:CWS|ERO)-[0-9a-f]{48}\.[a-z0-9]{1,5}")
SHA256 = re.compile(r"[0-9a-f]{64}")
CONFIG = "pearnly_cowork_ocr_remote"
PATHS = {"inspect": "/v1/cowork/inspect", "preview": "/v1/cowork/preview"}
_tokens, _lock = {}, threading.Lock()


def original(*, object, generation, sha256):
    """存在原件桶里的原件：Cloud Run 按版本号读出、核对 sha256 后再读，不经过我们服务器。"""
    return {"object": object, "generation": int(generation), "sha256": sha256}


def _original(body):
    value = body.get("original")
    if not isinstance(value, dict) or not isinstance(value.get("object"), str):
        raise ValueError("invalid original")
    site, _sep, rest = value["object"].partition("/")
    if site != body["site"] or not ORIGINAL.fullmatch(rest):
        raise ValueError("invalid original")
    if type(value.get("generation")) is not int or value["generation"] < 1:
        raise ValueError("invalid original")
    if not isinstance(value.get("sha256"), str) or not SHA256.fullmatch(value["sha256"]):
        raise ValueError("invalid original")
    return value


def inspect_request(*, site, object, filename):
    """浏览器直传的原件：让 Cloud Run 读一遍，核对类型 / 页数、算 sha256（同 ``originals.checks.inspect``），并告诉
    服务器这个对象的版本号（同一个对象名只能写一次，所以只有一个版本）。"""
    body = {"version": VERSION, "kind": "cowork.inspect", "site": site, "original": {"object": object},
            "filename": filename}
    parse_inspect(body)
    return body


def preview_request(*, site, object, generation, sha256, kind, page):
    """原件桶里一份原件的一页预览（ADR-016）：Cloud Run 按版本号读、核对 sha256、生成预览图，只传回图。"""
    body = {"version": VERSION, "kind": "cowork.preview", "site": site,
            "original": original(object=object, generation=generation, sha256=sha256), "file_kind": kind, "page": page}
    parse_preview(body)
    return body


def parse_preview(body):
    if not isinstance(body, dict) or body.get("version") != VERSION or body.get("kind") != "cowork.preview":
        raise ValueError("unsupported request")
    if not isinstance(body.get("site"), str) or not SITE.fullmatch(body["site"]):
        raise ValueError("invalid site")
    if body.get("file_kind") not in ("pdf", "image"):
        raise ValueError("invalid file_kind")
    if type(body.get("page")) is not int or not 1 <= body["page"] <= 500:
        raise ValueError("invalid page")
    return _original(body)


def parse_inspect(body):
    if not isinstance(body, dict) or body.get("version") != VERSION or body.get("kind") != "cowork.inspect":
        raise ValueError("unsupported request")
    if not isinstance(body.get("site"), str) or not SITE.fullmatch(body["site"]):
        raise ValueError("invalid site")
    if not isinstance(body.get("filename"), str) or not 1 <= len(body["filename"]) <= 255:
        raise ValueError("invalid filename")
    value = body.get("original")
    if not isinstance(value, dict) or not isinstance(value.get("object"), str):
        raise ValueError("invalid original")
    site, _sep, rest = value["object"].partition("/")
    if site != body["site"] or not ORIGINAL.fullmatch(rest) or set(value) != {"object"}:
        raise ValueError("invalid original")
    return value



# ── 站点侧调用（要 Frappe 站点上下文）──────────────────────────────────────────────────────────────────────


class RemoteError(RuntimeError):
    """Cloud Run 报的程序错误（不是引擎的内容错误）：照本机执行的程序错误处理（workflow_error），不重放付费调用。"""


def settings():
    """开着就返回 ``{"url"}``，否则 ``None``（没有站点上下文时当作没开）。"""
    import frappe

    value = (frappe.conf or {}).get(CONFIG)
    if not isinstance(value, dict) or not value.get("enabled"):
        return None
    url = str(value.get("url") or "").rstrip("/")
    if not url.startswith("https://") or not url.endswith(".run.app"):
        return None
    return {"url": url}


def _config():
    config = settings()
    if config is None:
        raise RuntimeError("Remote OCR is not enabled")
    return config


def identity_token(audience):
    """用识别服务账号（``ocr.credentials_path``）签身份令牌，Cloud Run IAM 只认这个身份。快过期才重签。"""
    from google.auth.transport.requests import Request
    from pearnly_core import ocr
    from google.oauth2 import service_account

    with _lock:
        credentials = _tokens.get(audience)
        if credentials is None:
            credentials = service_account.IDTokenCredentials.from_service_account_file(
                ocr.credentials_path(), target_audience=audience)
            _tokens[audience] = credentials
        if not credentials.valid:
            credentials.refresh(Request())
        return credentials.token


def inspect(object_name, filename, *, post=None, id_token=None):
    """浏览器直传到原件桶的原件交给 Cloud Run 读一遍（ADR-016）：返回 Cloud Run 的结论
    （``{"status": "ok", "meta": …, "generation": …, "md5": …}`` 或 ``{"status": "rejected", "code": …}``）。

    不是付费调用，不拿识别名额；等回复时不占数据库连接。连不上 / 对方忙抛 ``Deferred``（调用方让用户重试）。"""
    import frappe
    import requests
    from pearnly_core.execution.errors import Deferred

    config = _config()
    body = inspect_request(site=frappe.local.site, object=object_name, filename=filename)
    _release_db()
    try:
        token = (id_token or identity_token)(config["url"])
        response = (post or _http_post())(config["url"] + PATHS["inspect"], json=body, timeout=(10, 60),
                                                 headers={"Authorization": "Bearer " + token})
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise Deferred("remote_unreachable", 5) from exc
    except Exception as exc:  # 令牌签不出来：稍后重试
        raise Deferred("remote_token_unavailable", 5) from exc
    try:
        if response.status_code in (429, 500, 502, 503, 504):
            raise Deferred("remote_http_" + str(response.status_code), 5)
        if response.status_code != 200:
            raise RemoteError("remote_http_" + str(response.status_code))
        try:
            result = response.json()
        except ValueError as exc:
            raise RemoteError("remote_bad_reply") from exc
    finally:
        response.close()
    if not isinstance(result, dict) or result.get("status") not in ("ok", "rejected", "error") or (
            result["status"] == "ok" and (type(result.get("generation")) is not int or not isinstance(result.get("meta"), dict))):
        raise RemoteError("remote_bad_reply")
    if result["status"] == "error":
        if result.get("code") == "original_unavailable":
            raise Deferred("remote_original_unavailable", 5)
        raise RemoteError(str(result.get("code") or "remote_error"))
    return result


def preview(object_name, generation, sha256, kind, page, *, post=None, id_token=None):
    """原件桶里一份原件的一页预览（ADR-016）：``{"png": 字节}`` 或 ``{"code": 原件检查的错误代码}``。

    在 Cloud Run 上生成，只传回生成好的图（不把整份原件下载到服务器）。连不上 / 忙抛 ``Deferred``。"""
    import frappe
    import requests
    from pearnly_core.execution.errors import Deferred

    config = _config()
    body = preview_request(site=frappe.local.site, object=object_name, generation=generation, sha256=sha256,
                                    kind=kind, page=page)
    _release_db()
    try:
        token = (id_token or identity_token)(config["url"])
        response = (post or _http_post())(config["url"] + PATHS["preview"], json=body, timeout=(30, 60),
                                                 headers={"Authorization": "Bearer " + token})
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise Deferred("remote_unreachable", 5) from exc
    except Exception as exc:
        raise Deferred("remote_token_unavailable", 5) from exc
    try:
        if response.status_code in (429, 500, 502, 503, 504):
            raise Deferred("remote_http_" + str(response.status_code), 5)
        if response.status_code == 422:
            try:
                code = response.json().get("code")
            except (ValueError, AttributeError) as exc:
                raise RemoteError("remote_bad_reply") from exc
            if code == "original_unavailable":
                raise Deferred("remote_original_unavailable", 5)
            if code in ("preview_too_large", "preview_unsupported"):
                return {"code": code}
            raise RemoteError(str(code or "remote_error"))
        if response.status_code != 200 or response.headers.get("Content-Type") not in ("image/png", "image/jpeg"):
            raise RemoteError("remote_http_" + str(response.status_code))
        return {"png": response.content}
    finally:
        response.close()




def _http_post():
    from pearnly_core.execution import provider

    return provider.http_post


def _release_db():
    from pearnly_core.execution import pool

    pool.release_db()
