#!/usr/bin/env python3
"""给 `ocr-policy-bench.py` 的同一批图补上 Google Vision 的 OCR 原文（DOCUMENT_TEXT_DETECTION），供离线「数字校对」组合用。

**付费外部读取**（Vision 每月前 1,000 页免费，之后约 $1.50 / 1,000 页），不进默认回归，不写业务数据。
用法（bench 环境）：env/bin/python scripts/ocr-vision-bench.py --site <site> --corpus <dir> --out <dir>
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import frappe
import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    frappe.init(site=args.site, sites_path=os.environ.get("SITES_PATH", "."))
    frappe.connect()
    from pearnly_core import ocr

    corpus, out = Path(args.corpus), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for entry in json.loads((corpus / "manifest.json").read_text(encoding="utf-8")):
        target = out / f"{entry['id']}.json"
        if target.exists():
            continue
        content = (corpus / entry["file"]).read_bytes()
        mime = mimetypes.guess_type(entry["file"])[0] or "image/jpeg"
        image, _mime = ocr.upright(content, mime)  # 与模型读的是同一张（本地转正后）
        payload = {"requests": [{"image": {"content": base64.b64encode(image).decode()},
                                 "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                                 "imageContext": {"languageHints": ["th", "en"]}}]}
        row = {"id": entry["id"]}
        for attempt in range(5):
            started = time.monotonic()
            response = requests.post("https://vision.googleapis.com/v1/images:annotate", json=payload, timeout=60,
                                     headers={"Authorization": "Bearer " + ocr.access_token(),
                                              "x-goog-user-project": ocr.project()})
            row.update(ms=int((time.monotonic() - started) * 1000), status=response.status_code)
            if response.status_code in (429, 503):
                time.sleep(10 * (attempt + 1))
                continue
            break
        if response.status_code == 200:
            annotation = (response.json().get("responses") or [{}])[0].get("fullTextAnnotation") or {}
            row["text"] = annotation.get("text", "")
        else:
            row["error"] = response.text[:300]
        target.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
        print(f"{entry['id']:45} {row['ms']:6}ms {row.get('status')} {len(row.get('text', ''))} chars", flush=True)
    frappe.destroy()


if __name__ == "__main__":
    sys.exit(main())
