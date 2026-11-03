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


# 在列表里按标题逐个找行（不截前 N 个字）：标题格里有一个元素的文字与标题完全相同才算，记下那一行的单号；
# 没找齐就点列表自带的「加载更多」（`.btn-more`，服务端下一页）接着找，直到找齐或没有下一页。
# 用法：`await find_rows(titles)` → {found: {标题: 单号}, pages: 读了几页}。要在 READ_LIST 之后用（第一页的行数先读完）
FIND_ROWS = (
    "const find_rows=async(titles)=>{const found={};let pages=1;"
    "const rows=()=>[...document.querySelectorAll('.result .list-row-container')].filter(e=>!e.querySelector('.list-row-head'));"
    "const scan=()=>{for(const row of rows()){const cells=[...row.querySelectorAll('.list-subject *')];"
    " for(const t of titles){if(!(t in found)&&cells.some(e=>e.textContent.trim()===t))"
    "  found[t]=row.querySelector('.list-row-checkbox')?.dataset.name||row.querySelector('[data-name]')?.dataset.name||'';}}};"
    "scan();"
    "for(let k=0;k<500&&titles.some(t=>!(t in found));k++){"
    " const more=[...document.querySelectorAll('.list-paging-area .btn-more')].find(e=>e.offsetParent); if(!more) break;"
    " const before=rows().length; more.click();"
    " for(let j=0;j<80&&rows().length===before;j++) await new Promise(r=>setTimeout(r,250)); await idle();"
    " if(rows().length===before) break; pages++; scan();}"
    "return {found, pages};};")


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


# 抓页面的导出下载（`p.export_file` 用 fetch POST）：每次导出记下网址、方法、状态、类型和文件内容（base64，实验室的文件很小）
CAPTURE_EXPORTS = (
    "if(!window.__exports){window.__exports=[];const _f=window.fetch;window.fetch=async function(u,o){const r=await _f.apply(this,arguments);"
    "const url=String(u&&u.url||u);if(url.includes('/api/method/')&&/export/.test(url)){const b=new Uint8Array(await r.clone().arrayBuffer());"
    "let s='';for(let i=0;i<b.length;i+=8192)s+=String.fromCharCode.apply(null,b.subarray(i,i+8192));"
    "window.__exports.push({url,method:String(o&&o.method||'GET').toUpperCase(),status:r.status,type:r.headers.get('Content-Type')||'',"
    "size:b.length,b64:btoa(s)});}return r;};}")
# 等这次导出回来（`__exports` 多出一条），最多 20 秒；回最后一条
AWAIT_EXPORT = ("const before=window.__exports.length;"
                "for(let k=0;k<80&&window.__exports.length===before;k++) await new Promise(r=>setTimeout(r,250));"
                "const got=window.__exports.length>before?window.__exports[window.__exports.length-1]:null;")


def workbook_text(b64: str) -> tuple[list[str], str]:
    """导出下载的文件 → (表名, 全部单元格的文字)；xlsx 读各表，CSV（库存导出）按 UTF-8 读成一张表；都不是抛错。
    只用标准库（流程的判断在本机 Python 里跑）。"""
    import base64
    import io
    import re
    import zipfile
    from xml.etree import ElementTree

    content = base64.b64decode(b64)
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return ["csv"], content.decode("utf-8-sig")
    with zipfile.ZipFile(io.BytesIO(content)) as book:
        names = [node.get("name") for node in ElementTree.fromstring(book.read("xl/workbook.xml")).iter()
                 if node.tag.endswith("}sheet")]
        texts = []
        for item in book.namelist():
            if item == "xl/sharedStrings.xml" or re.fullmatch(r"xl/worksheets/sheet\d+\.xml", item):
                texts += [node.text for node in ElementTree.fromstring(book.read(item)).iter()
                          if node.tag.rsplit("}", 1)[-1] in {"t", "v"} and node.text]
    return names, "\n".join(texts)


def requests(method_path: str) -> str:
    """页面到这一步为止请求过这个接口的网址（`performance` 的资源记录）：GET 的参数在网址上、POST 的不在。"""
    return (f"const calls=performance.getEntriesByType('resource').map(e=>e.name)"
            f".filter(u=>u.includes('/api/method/'+{json.dumps(method_path)}));")


def list_matches(seen: dict, total: int, page_length: int = 20) -> bool:
    """列表上看到的与库里的一致：没有记录时出空态、不出行；有记录时行数是第一页的条数、表头计数的总数对得上。"""
    if not total:
        return bool(seen.get("empty")) and not seen.get("rows")
    return seen.get("rows") == min(total, page_length) and str(seen.get("count", "")).endswith(f"/ {total}")
