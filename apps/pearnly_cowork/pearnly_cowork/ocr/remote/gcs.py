"""Cloud Run 上的外部能力：服务账号令牌、存储桶里的检查点对象（Cloud Storage JSON API，直接走 HTTPS）、
回调站点用的身份令牌（任务派发，见 ``contract`` 的「任务派发」）。

只在 Cloud Run 镜像里用。检查点桶在新加坡，设了 7 天自动删除（ADR-0015）；对象里只有谷歌的回复（读出的文字 / 字段），
没有原件图片。原件桶（ADR-016）：按版本号读出浏览器直传的原件；核对不通过的那个版本删掉；识别时把各页预览图存进去（只新建、不覆盖）。
"""
import json
import threading
from urllib.parse import quote

import requests

SCOPE = "https://www.googleapis.com/auth/cloud-platform"
API = "https://storage.googleapis.com"


class Token:
    """Cloud Run 服务账号的访问令牌（元数据服务器），快过期才刷新，线程安全。"""

    def __init__(self):
        import google.auth

        self._credentials, _project = google.auth.default(scopes=[SCOPE])
        self._lock = threading.Lock()

    def __call__(self):
        from google.auth.transport.requests import Request

        with self._lock:
            if not self._credentials.valid:
                self._credentials.refresh(Request())
            return self._credentials.token


ATTEMPTS = 3


class Bucket:
    def __init__(self, name, token, http=None, sleep=None):
        import time

        self._name, self._token = name, token
        self._local = threading.local()
        self._http = http
        self._sleep = sleep or time.sleep

    @property
    def name(self):
        return self._name

    def _session(self):
        if self._http is not None:
            return self._http
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        return self._local.session

    def _headers(self):
        return {"Authorization": "Bearer " + self._token()}

    def _call(self, method, url, **kwargs):
        """一次存储请求：连接被重置 / 超时 / 谷歌回 429、5xx 时换一条新连接原地再试（最多 ``ATTEMPTS`` 次，间隔
        0.5、1 秒）。2026-10-01 半量压测：抽取付费返回后写「已完成」检查点时连接被对方重置，整份对账文档判了程序错误。
        返回 ``(回复, 前面是否有一次结果不明)``：带条件的写入重试时回 412，可能是前一次其实写进去了（见 ``_upload``）。"""
        unsure = False
        for attempt in range(ATTEMPTS):
            if attempt:
                self._sleep(0.5 * attempt)
                if self._http is None:
                    self._local.session = requests.Session()  # 被重置的那条连接不再用
            options = dict(kwargs)
            headers = self._headers()
            if "content_type" in options:
                headers["Content-Type"] = options.pop("content_type")
            try:
                response = getattr(self._session(), method)(url, headers=headers, **options)
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
                unsure = True
                if attempt == ATTEMPTS - 1:
                    raise
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < ATTEMPTS - 1:
                continue
            return response, unsure

    def get(self, name):
        response, _unsure = self._call("get", f"{API}/storage/v1/b/{self._name}/o/{quote(name, safe='')}",
                                       params={"alt": "media"}, timeout=(10, 30))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return json.loads(response.content), int(response.headers["x-goog-generation"])

    def names(self, prefix):
        """这个前缀下现有的全部对象名（一份读取开头列一次检查点目录，之后不存在的不再逐个去问；见 ``runner.Store.prefetch``）。"""
        found, token = set(), None
        while True:
            params = {"prefix": prefix, "fields": "items(name),nextPageToken", "maxResults": "1000"}
            if token:
                params["pageToken"] = token
            response, _unsure = self._call("get", f"{API}/storage/v1/b/{self._name}/o", params=params, timeout=(10, 30))
            response.raise_for_status()
            value = response.json()
            found.update(item["name"] for item in value.get("items") or [])
            token = value.get("nextPageToken")
            if not token:
                return found

    def read_bytes(self, name, generation=None):
        """读一个对象的原始字节和版本号（原件桶，ADR-016）；给了版本号就只读那个版本。不存在返回 ``None``。"""
        params = {"alt": "media", **({"generation": str(generation)} if generation is not None else {})}
        response, _unsure = self._call("get", f"{API}/storage/v1/b/{self._name}/o/{quote(name, safe='')}",
                                       params=params, timeout=(10, 120))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content, int(response.headers["x-goog-generation"])

    def delete_generation(self, name, generation):
        """只删这个版本（核对不通过的直传原件）；已经没有了也算删掉。"""
        response, _unsure = self._call("delete", f"{API}/storage/v1/b/{self._name}/o/{quote(name, safe='')}",
                                       params={"generation": str(generation)}, timeout=(10, 30))
        if response.status_code not in (204, 404):
            response.raise_for_status()

    def metadata(self, name):
        """对象的版本号与类型（识别时存的预览图，看是不是已经存过）；不存在返回 ``None``。"""
        response, _unsure = self._call("get", f"{API}/storage/v1/b/{self._name}/o/{quote(name, safe='')}",
                                       params={"fields": "generation,contentType"}, timeout=(10, 30))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        value = response.json()
        return int(value["generation"]), value.get("contentType")

    def create_bytes(self, name, data, content_type):
        """写一个新对象（预览图）：已经有了就不写，返回 ``None``；写成返回版本号。"""
        response, _unsure = self._call("post", f"{API}/upload/storage/v1/b/{self._name}/o",
                                       params={"uploadType": "media", "name": name, "ifGenerationMatch": "0"},
                                       data=data, content_type=content_type, timeout=(10, 60))
        if response.status_code == 412:
            return None  # 已经有了（或前一次其实写进去了）：调用方再查一次版本号
        response.raise_for_status()
        return int(response.json()["generation"])

    def _upload(self, name, value, precondition):
        params = {"uploadType": "media", "name": name}
        if precondition is not None:
            params["ifGenerationMatch"] = str(precondition)
        response, unsure = self._call("post", f"{API}/upload/storage/v1/b/{self._name}/o", params=params,
                                      data=json.dumps(value, ensure_ascii=False).encode(),
                                      content_type="application/json", timeout=(10, 60))
        if response.status_code == 412:
            if unsure:
                # 前一次连接断在半路、重试时说「已被改过」：读回来看，内容正是这次要写的就是前一次写进去了
                found = self.get(name)
                if found and found[0] == value:
                    return found[1]
            return None
        response.raise_for_status()
        return int(response.json()["generation"])

    def create(self, name, value):
        return self._upload(name, value, 0)

    def put(self, name, value, if_generation=None):
        return self._upload(name, value, if_generation)

    def delete(self, name):
        response, _unsure = self._call("delete", f"{API}/storage/v1/b/{self._name}/o/{quote(name, safe='')}",
                                       timeout=(10, 30))
        if response.status_code not in (204, 404):
            response.raise_for_status()


CALLBACK_HEADER = "X-Pearnly-Callback"
METADATA = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"


class IdTokens:
    """回调站点用的身份令牌：Cloud Run 服务账号经元数据服务器签发，受众 = 回调地址（站点据此核对，见
    ``ocr.remote.callback``）。按受众缓存，离过期不到 5 分钟才重签。"""

    def __init__(self, http=None, clock=None):
        import time

        self._http, self._clock = http or requests, clock or time.time
        self._cache, self._lock = {}, threading.Lock()

    def __call__(self, audience):
        with self._lock:
            found = self._cache.get(audience)
            if found and found[1] - 300 > self._clock():
                return found[0]
        response = self._http.get(METADATA, params={"audience": audience, "format": "full"},
                                  headers={"Metadata-Flavor": "Google"}, timeout=(5, 10))
        response.raise_for_status()
        token = response.text.strip()
        with self._lock:
            self._cache[audience] = (token, self._clock() + 3000)  # 谷歌签 1 小时；按 50 分钟算
        return token


def post_callback(url, body, token, http=None):
    """回调站点：返回 HTTP 状态码；连不上返回 0（调用方决定要不要让 Cloud Tasks 重试）。"""
    try:
        # 不用 Authorization：Frappe 会把 Bearer 当成自己的 OAuth 令牌去查、查不到直接 401（frappe.auth.validate_oauth）。
        response = (http or requests).post(url, json=body, headers={CALLBACK_HEADER: token}, timeout=(10, 30))
    except requests.RequestException:
        return 0
    status = response.status_code
    response.close()
    return status
