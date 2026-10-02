"""把一个目录里的备份文件上传到 GCS 的若干前缀（offsite-backup.sh 在生产镜像的一次性容器里调用）。

只新建、不覆盖（ifGenerationMatch=0；服务账号也只有 objectCreator）。断点续传会话上传，传完按 md5 回读校验。
用法：upload.py --key KEY.json --bucket NAME --source DIR PREFIX [PREFIX ...]
"""
import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
CHUNK = 16 * 1024 * 1024


def md5_b64(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return base64.b64encode(digest.digest()).decode()


def upload(session, token, bucket, name, path):
    start = session.post(f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
                         params={"uploadType": "resumable", "name": name, "ifGenerationMatch": "0"},
                         headers={"Authorization": f"Bearer {token()}", "X-Upload-Content-Length": str(path.stat().st_size)},
                         json={"name": name}, timeout=60)
    start.raise_for_status()
    url, size, offset = start.headers["Location"], path.stat().st_size, 0
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK)
            end = offset + len(block) - 1
            headers = {"Content-Range": f"bytes {offset}-{end}/{size}" if block else f"bytes */{size}"}
            response = session.put(url, data=block, headers=headers, timeout=300)
            if response.status_code in (200, 201):
                return response.json()
            if response.status_code != 308:
                response.raise_for_status()
            offset += len(block)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("prefixes", nargs="+")
    args = parser.parse_args()
    credentials = service_account.Credentials.from_service_account_file(args.key, scopes=[SCOPE])

    def token():
        if not credentials.valid:
            credentials.refresh(Request())
        return credentials.token

    session, uploaded = requests.Session(), 0
    for path in sorted(Path(args.source).iterdir()):
        if not path.is_file():
            continue
        local = md5_b64(path)
        for prefix in args.prefixes:
            name = f"{prefix.rstrip('/')}/{path.name}"
            result = upload(session, token, args.bucket, name, path)
            if result.get("md5Hash") != local:
                print(f"md5 mismatch: {name}", file=sys.stderr)
                return 1
            uploaded += 1
    print(json.dumps({"uploaded": uploaded, "bucket": args.bucket, "prefixes": args.prefixes}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
