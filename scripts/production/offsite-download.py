"""下载 GCS 备份里最新（或指定日期）的一份到本地目录（restore-drill.sh 在生产镜像的一次性容器里调用；只读服务账号）。
用法：download.py --key KEY.json --bucket NAME --dest DIR [--prefix daily] [--day YYYY-MM-DD]；打印下载的日期。"""
import argparse
import sys
from pathlib import Path
from urllib.parse import quote

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--prefix", default="daily")
    parser.add_argument("--day")
    args = parser.parse_args()
    credentials = service_account.Credentials.from_service_account_file(args.key, scopes=[SCOPE])
    credentials.refresh(Request())
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {credentials.token}"
    api = f"https://storage.googleapis.com/storage/v1/b/{args.bucket}/o"
    names, token = [], None
    while True:
        listing = session.get(api, params={"prefix": args.prefix + "/", **({"pageToken": token} if token else {})},
                              timeout=60).json()
        names += [item["name"] for item in listing.get("items") or []]
        token = listing.get("nextPageToken")
        if not token:
            break
    days = sorted({name.split("/")[1] for name in names if name.count("/") >= 2})
    if not days:
        print("no backups", file=sys.stderr)
        return 1
    day = args.day or days[-1]
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        if name.split("/")[1] != day:
            continue
        with session.get(f"{api}/{quote(name, safe='')}", params={"alt": "media"}, stream=True, timeout=300) as response:
            response.raise_for_status()
            with open(dest / name.rsplit("/", 1)[1], "wb") as handle:
                for block in response.iter_content(1024 * 1024):
                    handle.write(block)
    print(day)
    return 0


if __name__ == "__main__":
    sys.exit(main())
