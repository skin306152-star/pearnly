"""Cloud Run 上的两样外部能力：服务账号令牌、存储桶里的检查点对象（Cloud Storage JSON API，直接走 HTTPS）。

只在 Cloud Run 镜像里用。检查点桶在新加坡，设了 7 天自动删除（ADR-0015）；对象里只有谷歌的回复（读出的文字 / 字段），
没有原件图片。原件桶（ADR-016）只读：按版本号读出浏览器直传的原件；核对不通过的那个版本删掉。
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


class Bucket:
    def __init__(self, name, token, http=None):
        self._name, self._token = name, token
        self._local = threading.local()
        self._http = http

    def _session(self):
        if self._http is not None:
            return self._http
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        return self._local.session

    def _headers(self):
        return {"Authorization": "Bearer " + self._token()}

    def get(self, name):
        response = self._session().get(f"{API}/storage/v1/b/{self._name}/o/{quote(name, safe='')}",
                                       params={"alt": "media"}, headers=self._headers(), timeout=(10, 30))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return json.loads(response.content), int(response.headers["x-goog-generation"])

    def read_bytes(self, name, generation=None):
        """读一个对象的原始字节和版本号（原件桶，ADR-016）；给了版本号就只读那个版本。不存在返回 ``None``。"""
        params = {"alt": "media", **({"generation": str(generation)} if generation is not None else {})}
        response = self._session().get(f"{API}/storage/v1/b/{self._name}/o/{quote(name, safe='')}",
                                       params=params, headers=self._headers(), timeout=(10, 120))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content, int(response.headers["x-goog-generation"])

    def delete_generation(self, name, generation):
        """只删这个版本（核对不通过的直传原件）；已经没有了也算删掉。"""
        response = self._session().delete(f"{API}/storage/v1/b/{self._name}/o/{quote(name, safe='')}",
                                          params={"generation": str(generation)},
                                          headers=self._headers(), timeout=(10, 30))
        if response.status_code not in (204, 404):
            response.raise_for_status()

    def _upload(self, name, value, precondition):
        params = {"uploadType": "media", "name": name}
        if precondition is not None:
            params["ifGenerationMatch"] = str(precondition)
        response = self._session().post(f"{API}/upload/storage/v1/b/{self._name}/o", params=params,
                                        data=json.dumps(value, ensure_ascii=False).encode(),
                                        headers={**self._headers(), "Content-Type": "application/json"},
                                        timeout=(10, 60))
        if response.status_code == 412:
            return None
        response.raise_for_status()
        return int(response.json()["generation"])

    def create(self, name, value):
        return self._upload(name, value, 0)

    def put(self, name, value, if_generation=None):
        return self._upload(name, value, if_generation)

    def delete(self, name):
        response = self._session().delete(f"{API}/storage/v1/b/{self._name}/o/{quote(name, safe='')}",
                                          headers=self._headers(), timeout=(10, 30))
        if response.status_code not in (204, 404):
            response.raise_for_status()
