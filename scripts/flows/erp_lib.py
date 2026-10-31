"""ERP 流程用例共用的几样东西（不是流程，不登记）：从数据清单取公司 / 仓 / 商品，页面步骤里的等待与操作片段。

数据一律取 `scripts/regression/datasets.py` 的清单：新开户 `products` 是 {认法: {product, scan}}，老数据是商品列表（编码、条码）。
页面片段拼进步骤 JS 用：`IDLE` 等请求都回来，`JS` 是取元素、填值、点下拉项这些小函数。
"""
from __future__ import annotations

import json

# 按条件等，不按固定时长：请求都回来（jQuery 在途数连续两次为 0），最多 20 秒（同 erp_stocktake_scan.py）
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
JS = IDLE + """
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const vis=el=>!!el&&el.getClientRects().length>0&&getComputedStyle(el).visibility!=='hidden';
const all=(sel,root=document)=>[...root.querySelectorAll(sel)].filter(vis);
const until=async(fn,ms=15000)=>{const end=Date.now()+ms;let v;while(Date.now()<end){try{v=await fn();}catch(e){v=null;}if(v)return v;await sleep(200);}return v;};
const setInput=(el,value)=>{if(!el)return false;el.focus();el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));return true;};
// 只发 input 不发 change：change 会让 Link 控件先按单据名校验，码不是单据名就清空、按空串搜，下拉只剩最近改过的 10 件（10-31）
const pick=async(input,text,match)=>{input.focus();input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}));
  input.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true,key:'a'}));
  const own=()=>{const id=input.getAttribute('aria-owns');const ul=id&&document.getElementById(id);return ul?[...ul.querySelectorAll('[role=option], li')]:[];};
  // 有商品名时按名称**整段相等**认（原生下拉名称在 <strong>，组合框是「名称 · 码 · 单号」的第一段）：只看「包含」会把「… รหัส」认成「… รหัสภายใน」，
  // 搜索结果回来前下拉里还是旧列表，先命中的就是错的那件（10-31）
  const head=l=>{const s=l.querySelector('strong');return (s?s.textContent:l.textContent.split(' · ')[0]).trim();};
  const li=await until(()=>[...own(),...document.querySelectorAll('.awesomplete li, ul[role=listbox] li')].find(l=>match?head(l)===match:l.textContent.includes(text)),10000);
  if(!li)return false;li.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));li.click();await sleep(400);return true;};
const call=(method,args,type)=>frappe.call({method,args,type:type||'POST'}).then(r=>r.message);
const pageOf=route=>all('.page-container').find(el=>el.getAttribute('data-page-route')===route);
const alerts=()=>all('.alert-message, .desk-alert .alert-message').map(e=>e.textContent.trim()).join(' | ').slice(0,300);
const msg=()=>all('.modal.show .msgprint, .modal.show .modal-body').map(e=>e.textContent.trim()).join(' | ').slice(0,300);
"""


def company(manifest) -> str:
    return manifest.get("company") or manifest["companies"][0]


def warehouses(manifest) -> list[dict]:
    co = company(manifest)
    return [w for w in manifest["warehouses"] if w.get("company", co) == co]


def codes(manifest) -> dict:
    """{认法: {product, scan}}：新开户照清单（去掉按台那件）；老数据第一件按编码、第二件按条码。"""
    if manifest["dataset"] == "new":
        return {key: item for key, item in manifest["products"].items() if key != "by_unit"}
    first, second = manifest["products"][0], manifest["products"][1]
    return {"by_code": {"product": first["name"], "scan": first["code"]},
            "by_barcode": {"product": second["name"], "scan": second["barcode"]}}


def as_user(lab, site, user, body):
    """以某岗位账号在站点里跑一段（库存岗、收银岗等），结尾自己打印 JSON。"""
    return lab.python(site, f"frappe.set_user({user!r})\n" + body)


def stock_of(lab, site, product, warehouse) -> float:
    return lab.python(site, f"""
qty = frappe.db.sql("select coalesce(sum(qty_on_hand), 0) from `tabERP Stock Balance` where product=%s and warehouse=%s", ({product!r}, {warehouse!r}))[0][0]
print(json.dumps({{"qty": float(qty)}}))
""")["qty"]


def js_value(value) -> str:
    return json.dumps(value, ensure_ascii=False)
