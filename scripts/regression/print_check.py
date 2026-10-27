#!/usr/bin/env python3
"""打印入口按岗位核对（回归实验室）：每个岗位账号建一次会话，逐张开框架原生打印页 `/printview`（与页面「打印」打开的同一地址），
再取一次 PDF（`download_pdf`），记「出版面 / 被拒 / 报错」、PDF 页数，以及版面里有没有预期的标题字样。单据名取
`lab_seed_erp.py` 最后一行的 JSON。

    python3 scripts/regression/print_check.py <夹具.json> > 打印矩阵.jsonl
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE = "erp.regression.localhost"
BASE = "http://127.0.0.1:18771"
ROLES = ("admin", "purchase", "sales", "cashier", "stock", "products", "tasks", "none")
# 单据键 → (doctype, 格式, 预期标题字样)
DOCS = {
    "purchase_order_a": ("Purchase Document", "Pearnly Purchase Order", "ใบสั่งซื้อ"),
    "purchase_order_a_admin": ("Purchase Document", "Pearnly Purchase Order", "ใบสั่งซื้อ"),  # 同公司管理员开的：员工应拒
    "purchase_order_a_void": ("Purchase Document", "Pearnly Purchase Order", "ใบสั่งซื้อ"),
    "purchase_order_b": ("Purchase Document", "Pearnly Purchase Order", "ใบสั่งซื้อ"),
    "stock_in_a": ("Purchase Document", "Pearnly Purchase Order", "ใบสั่งซื้อ"),  # 采购发票套采购订单格式：应拒
    "quotation_a": ("ERP Sales Document", "Pearnly Sales Document", "ใบเสนอราคา"),
    "sales_a": ("ERP Sales Document", "Pearnly Sales Document", "ใบเสร็จรับเงิน"),
    "sales_a_void": ("ERP Sales Document", "Pearnly Sales Document", "ใบเสร็จรับเงิน"),
    "sales_b": ("ERP Sales Document", "Pearnly Sales Document", "ใบเสร็จรับเงิน"),
    "pos_sale_a": ("ERP Sale", "Pearnly POS Receipt", "ใบเสร็จรับเงิน"),
    "pos_refund_a": ("ERP Sale", "Pearnly POS Receipt", "ใบรับคืนสินค้า"),
    "pos_sale_b": ("ERP Sale", "Pearnly POS Receipt", "ใบเสร็จรับเงิน"),
    "shift_a": ("ERP Shift", "Pearnly Shift Summary", ""),
    "shift_b": ("ERP Shift", "Pearnly Shift Summary", ""),
    "count_open_a": ("ERP Stock Count", "Pearnly Stock Count", ""),
    "count_posted_a": ("ERP Stock Count", "Pearnly Stock Count", ""),
}


def lab(*args) -> str:
    return subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench/sites", "pearnly-regression-lab-bench-1",
                           "../env/bin/python", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def fetch(path: str, sid: str) -> tuple[int, bytes]:
    request = urllib.request.Request(BASE + path, headers={"Host": SITE, "Cookie": f"sid={sid}"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def classify(status: int, body: bytes, title: str) -> str:
    text = body.decode("utf-8", "replace")
    if status in (401, 403) or "PermissionError" in text or "Not Permitted" in text or "ไม่ได้รับอนุญาต" in text:
        return "denied"
    if status != 200 or 'class="print-format' not in text:
        return f"error {status}"
    return "ok" if not title or title in text else "ok-no-title"


def main(fixture: str) -> None:
    names = json.loads(Path(fixture).read_text().strip().splitlines()[-1])
    for role in ROLES:
        user = f"{role}@erp.regression"
        sid = next((line.split()[1] for line in lab("/workspace/scripts/smoke/session.py", SITE, user).splitlines()
                    if line.startswith("SID ")), "")
        try:
            for key, (doctype, fmt, title) in DOCS.items():
                query = urllib.parse.urlencode({"doctype": doctype, "name": names[key], "format": fmt, "no_letterhead": 1})
                status, body = fetch(f"/printview?{query}", sid)
                html = classify(status, body, title)
                status, pdf = fetch(f"/api/method/frappe.utils.print_format.download_pdf?{query}", sid)
                pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf)) if pdf.startswith(b"%PDF") else 0
                void = "ยกเลิก" in body.decode("utf-8", "replace")
                print(json.dumps({"role": role, "doc": key, "name": names[key], "format": fmt, "printview": html,
                                  "pdf": f"{pages} 页" if pages else f"无（{status}）", "void_mark": void},
                                 ensure_ascii=False), flush=True)
        finally:
            lab("/workspace/scripts/smoke/session.py", SITE, user, "drop", sid)


if __name__ == "__main__":
    main(sys.argv[1])
