"""流程用例：ERP 客户、收银小票、班次三个列表页照库里列出（清存量基线 11-08）。

管理员依次打开三页：
- 客户：prepare 按正式函数建一位客户；列表的行数与表头计数等于服务端按同一份权限与公司范围数出来的客户数（`count_customers`），
  列表里找得到新建的这一位；
- 收银小票、班次：列表的行数与表头计数等于服务端数出来的条数（`count_sales` / `count_shifts`，同一份权限与当前公司范围）。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import FIND_ROWS, IDLE, list_matches  # noqa: E402

TITLE = "ERP 客户 / 收银小票 / 班次列表：照库里列出"
PAGES = ["pearnly-clients", "pearnly-pos-receipts", "pearnly-pos-shifts"]


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import customers
from pearnly_erp_portal.cashier import sales, shifts
counts = {{}}
frappe.set_user("admin@erp.regression")
made = customers.create_customer(customer_name="flow client {tag}")
frappe.db.commit()
counts["pearnly-clients"] = customers.count_customers()
counts["pearnly-pos-receipts"] = sales.count_sales()
counts["pearnly-pos-shifts"] = shifts.count_shifts()
print(json.dumps({{"counts": counts, "client": made.get("name") if isinstance(made, dict) else made}}))
""")
    return {**out, "pages": PAGES, "client_name": f"flow client {tag}", "cases": []}


def steps(params, camera_code=None):
    out = []
    for page in params.get("pages") or []:
        out.append({"name": f"列表 {page}", "js": IDLE + f"""
frappe.set_route({json.dumps(page)}); await new Promise(r=>setTimeout(r,800));
for(let k=0;k<80&&!(frappe.get_route()[0]==={json.dumps(page)});k++) await new Promise(r=>setTimeout(r,250));
await idle();
// 只读当前这一页的容器：连开几页时前一页的列表还藏在页面里，整页找表头计数会读到它的
const root=()=>[...document.querySelectorAll('.page-container')].find(e=>e.getAttribute('data-page-route')==={json.dumps(page)});
for(let k=0;k<80&&!(root()?.querySelector('.list-row-head .list-count'))&&![...(root()?.querySelectorAll('.no-result')||[])].some(e=>e.offsetParent);k++)
  await new Promise(r=>setTimeout(r,250)); await idle();
const shown=[...root().querySelectorAll('.result .list-row-container')].filter(e=>e.offsetParent&&!e.querySelector('.list-row-head'));
const list={{count:(root().querySelector('.list-row-head .list-count')?.innerText||'').trim(), rows:shown.length,
  empty:[...root().querySelectorAll('.no-result')].some(e=>e.offsetParent)}};
""" + (FIND_ROWS + f"const hit=await find_rows([{json.dumps(params['client_name'])}]); return {{...list, hit}};"
                                 if page == "pearnly-clients" else "return list;")})
    return out


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    checks = []
    for page in params.get("pages") or []:
        seen = by.get(f"列表 {page}", {})
        total = params["counts"][page]
        checks.append({"case": f"{page} lists what the account can see", "seen": {k: seen.get(k) for k in ("count", "rows", "empty")},
                       "expect": total, "ok": list_matches(seen, total)})
        if page == "pearnly-clients":
            checks.append({"case": "the new client is in the list", "seen": seen.get("hit"),
                           "ok": bool(((seen.get("hit") or {}).get("found") or {}).get(params["client_name"]))})
    return checks


def verify(lab, manifest, params, camera_code=None):
    return []
