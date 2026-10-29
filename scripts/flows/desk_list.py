"""流程用例共用的页面片段：等请求回来、读共用列表（`pearnly_core/public/desk/list.js`）上看到的、查页面发过的接口请求。

不是流程（登记表里没有它）；各流程模块 `from desk_list import …`（运行器按文件加载流程模块，本目录在 sys.path 上）。
"""
from __future__ import annotations

import json

# 按条件等，不按固定时长：jQuery 在途请求数连续两次为 0，最多 20 秒（同 `erp_stocktake_scan.IDLE`）
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")

# 列表画好（有行或出了空态）后读：表头计数「n / 总数」、看得见的行数、空态是否显示
READ_LIST = (
    "for(let k=0;k<80&&!document.querySelector('.list-row-head .list-count')&&![...document.querySelectorAll('.no-result')].some(e=>e.offsetParent);k++)"
    " await new Promise(r=>setTimeout(r,250)); await idle();"
    "const shown=[...document.querySelectorAll('.result .list-row-container')].filter(e=>e.offsetParent&&!e.querySelector('.list-row-head'));"
    "const list={count:(document.querySelector('.list-row-head .list-count')?.innerText||'').trim(), rows:shown.length,"
    " empty:[...document.querySelectorAll('.no-result')].some(e=>e.offsetParent)};")


# 点页面上的一个动作（`page.add_inner_button`）：电脑宽度是页头按钮；手机宽度框架把它收进页头的「…」菜单
# （frappe `ui/page.js`「Add actions as menu item in Mobile View」），先展开菜单再点那一项。回 'button' / 'menu'，找不到回 ''
ACTION = (
    "const action=async(label)=>{"
    "const b=[...document.querySelectorAll('.page-head button, .page-actions button')].find(e=>e.offsetParent&&e.innerText.trim()===label);"
    "if(b){b.click();return 'button';}"
    "const m=[...document.querySelectorAll('.page-head .menu-btn-group > button')].find(e=>e.offsetParent);"
    "if(!m) return ''; m.click(); await new Promise(r=>setTimeout(r,300));"
    "const i=[...document.querySelectorAll('.page-head .menu-btn-group .dropdown-item')].find(e=>e.offsetParent&&e.innerText.trim()===label);"
    "if(!i){m.click();return '';} i.click(); return 'menu';};")


def requests(method_path: str) -> str:
    """页面到这一步为止请求过这个接口的网址（`performance` 的资源记录）：GET 的参数在网址上、POST 的不在。"""
    return (f"const calls=performance.getEntriesByType('resource').map(e=>e.name)"
            f".filter(u=>u.includes('/api/method/'+{json.dumps(method_path)}));")


def list_matches(seen: dict, total: int, page_length: int = 20) -> bool:
    """列表上看到的与库里的一致：没有记录时出空态、不出行；有记录时行数是第一页的条数、表头计数的总数对得上。"""
    if not total:
        return bool(seen.get("empty")) and not seen.get("rows")
    return seen.get("rows") == min(total, page_length) and str(seen.get("count", "")).endswith(f"/ {total}")
