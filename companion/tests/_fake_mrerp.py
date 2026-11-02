# -*- coding: utf-8 -*-
"""假 MR.ERP 站点（MR.ERP 代录离线回放用，ADR-026 第 2 步）：只监听 127.0.0.1，页面结构照 MR.ERP 源码只读快照
（/Users/skin/mrerp-source/src）做：

- ``login/login.php``（表单提交 ``checklogin.php``）→ 成功 ``location=selectdb.php`` / 失败 alert「User หรือ Password ไม่ถูกต้อง」；
  登录时把本会话号记进「在用」表（``tbl_useruse.browserid``），业务页核对、对不上就 alert 后跳 ``logoutkick.php``
  （照 ``checkuseruse.php``：打出踢人脚本后**不停**，``allsave.php`` 照样写库）；
- ``selectdb.php``（每行一家公司、每个年度一条 ``mainmenu.php?comidyear=N&seldb=1``）、``mainmenu.php``（底栏
  ``Company : 全称``、``Database Name : 简称+年度``）、``logout.php`` / ``logoutkick.php``；
- 三个模块（``aptran`` 赊购、``artran`` 赊销、``arse`` 现销）的 ``allview.php``（搜索框 + Search 按页面原本方式 ``?searchdata=``、
  「Add」、``#showdata`` 由页面脚本向 ``component/showdata.php`` 要行再追加）、``allform.php``（新增 / ``?id=N&status=vie`` 查看）、
  ``allsave.php``（写库、显示「Save Success」、过一会儿回列表）；
- 选择框按 bshlistbox 的样子：聚焦格子弹出 ``#bshlistbox``（搜索框 ``#bshlistboxinpsearch`` 按 keyup 筛、行在
  ``#bshlistboxdetailshow`` 里、每行第一列是隐藏的内部编号），点一行把各列填进各自的目标格、再跑「选完以后」：选了对方页面
  过一会儿带出信用天数，选了商品过一会儿带出标准成本价覆盖单价；合计由页面自己的 ``calcrow`` / ``calcgrandtotal`` 算；
  「Save」先跑页面自己的必填检查（alert 拦下、不提交）。

能模拟：存档成功、存档失败弹框（``fail_save``）、被踢（``kick_now`` / ``kick_on_save``）、断网（``down``：一律断开连接；
``down_on_save_s``：存档那一下断网几秒、请求没到库）、写库后回包前断开（``drop_after_commit``）、慢响应（``slow_s``）、
认不出的弹框（``alert_on_form``）、读回不符（``tamper``）、自动编号（``autono``）。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import secrets
import threading
import time
from decimal import ROUND_HALF_UP, Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

COMPANIES = [
    {"comid": "1", "comidyear": "12", "comname": "TEST", "comfullname": "TEST Trading Co., Ltd.", "years": "2569"},
    {"comid": "2", "comidyear": "21", "comname": "OTHER", "comfullname": "Other Co., Ltd.", "years": "2569"},
]
MENUS = {"363": ("aptran", "PN"), "453": ("aptran", "PG"), "370": ("artran", "IV"), "371": ("arse", "HS")}
SUPPLIERS = [["1", "S001", "บริษัท", "ผู้ขาย หนึ่ง จำกัด", "30"], ["2", "S002", "ห้าง", "ผู้ขาย สอง", "15"],
             ["3", "12", "ร้าน", "รหัสตัวเลข", "0"]]
CUSTOMERS = [["7", "C001", "บริษัท", "ลูกค้า หนึ่ง จำกัด", "30"], ["8", "CASH", "", "ลูกค้าเงินสด", "0"]]
ITEMS = [["11", "P001", "สินค้า หนึ่ง", "1", "ชิ้น", "95.00"], ["12", "P002", "สินค้า สอง", "2", "กล่อง", "40.00"],
         ["13", "SRV1", "ค่าบริการ", "3", "ครั้ง", "0.00"]]
VATRATES = [["1", "7%", "7.00", "1"], ["2", "0%", "0.00", "1"], ["3", "7% รวม", "7.00", "2"]]
SIMPLE = {"txtothcombrh": [["1", "00000", "สำนักงานใหญ่"]], "txtothdept_h": [["1", "00", "ทั่วไป"]],
          "txtothsuborder_h": [["1", "00", "ทั่วไป"]], "txtemp": [["1", "สมชาย ใจดี", ""]],
          "txtothcombrhcus": [["1", "00000", "สำนักงานใหญ่"]], "store": [["1", "WH1", "คลังหลัก"], ["2", "WH2", "คลังสอง"]]}
CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(str(value or "0").replace(",", "") or "0").quantize(CENT, ROUND_HALF_UP)


def fmt(value) -> str:
    return f"{money(value):,.2f}"


def thai(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d:02d}/{m:02d}/{y + 543}"


def eng(thai_date: str) -> str:
    d, m, y = (int(x) for x in thai_date.split("/"))
    return dt.date(y - 543, m, d).isoformat()


class FakeMrerp:
    def __init__(self, *, users: Optional[Dict[str, str]] = None, allowed: Optional[List[str]] = None):
        self.users = users or {"ccbot": "S3cret-pass!"}
        self.allowed = allowed or ["12"]  # 这个账号看得到哪些年度账套
        self.sessions: Dict[str, Dict] = {}
        self.useruse: Dict[str, str] = {}
        self.docs: List[Dict] = []
        self.next_id = 100
        self.autono: Dict[str, int] = {}  # 菜单号 → 下一个自动编号（没有 = 手填单号）
        self.log: List[str] = []  # 路径（不带参数值）
        self.logins = 0
        self.logouts = 0
        self.lock = threading.Lock()
        # 模拟开关
        self.fail_save: Optional[str] = None
        self.kick_on_save = False
        self.drop_after_commit = False
        self.down = False
        self.slow_s = 0.0
        self.alert_on_form: Optional[str] = None
        self.after_select_ms = 250
        self.tamper = None  # (doc) → None：存进库之前改一下（模拟读回不符）
        self.down_on_save_s = 0.0  # 存档那一下断网这么多秒（请求没到库）
        self.down_after_commit_s = 0.0  # 写了库、回包前断开以后，再断网这么多秒
        self.on_request = None  # (path) → None：测试挂钩（比如在打开表单时模拟有人在窗口里动）
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="fake-mrerp", daemon=True)

    # ── 生命周期 ────────────────────────────────────────────────────────
    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> "FakeMrerp":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def kick_now(self, user: str = "ccbot") -> None:
        """会计本人用同一个账号在别处登录了。"""
        self.useruse[user] = "elsewhere-" + secrets.token_hex(4)

    def saved(self, module: Optional[str] = None) -> List[Dict]:
        return [d for d in self.docs if module is None or d["module"] == module]

    # ── HTTP ────────────────────────────────────────────────────────────
    def _handler(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                return None

            def do_GET(self):
                site._serve(self, "GET")

            def do_POST(self):
                site._serve(self, "POST")

        return Handler

    def _serve(self, req: BaseHTTPRequestHandler, method: str) -> None:
        parts = urlsplit(req.path)
        path = parts.path
        with self.lock:
            self.log.append(f"{method} {path}")
        if self.down:
            req.close_connection = True
            return  # 一律断开、不回包：浏览器那边是加载失败
        if self.slow_s:
            time.sleep(self.slow_s)
        if self.on_request:
            self.on_request(path)
        query = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        form: Dict[str, List[str]] = {}
        if method == "POST":
            size = int(req.headers.get("Content-Length") or 0)
            form = parse_qs(req.rfile.read(size).decode("utf-8"), keep_blank_values=True)
        sid = self._sid(req)
        sess = self.sessions.setdefault(sid, {})
        try:
            body = self._route(path, method, query, form, sess, sid)
        except _Drop:
            req.close_connection = True
            return
        if body is None:
            body = "<html><body>Not Found</body></html>"
            status = 404
        else:
            status = 200
        data = body.encode("utf-8")
        req.send_response(status)
        req.send_header("Content-Type", "text/html; charset=utf-8")
        req.send_header("Content-Length", str(len(data)))
        if req.headers.get("Cookie", "").find("PHPSESSID=" + sid) < 0:
            req.send_header("Set-Cookie", f"PHPSESSID={sid}; path=/")
        req.end_headers()
        req.wfile.write(data)

    @staticmethod
    def _sid(req) -> str:
        for part in (req.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "PHPSESSID" and value:
                return value
        return secrets.token_hex(12)

    # ── 页面 ────────────────────────────────────────────────────────────
    def _route(self, path, method, query, form, sess, sid):
        if path in ("/", "/index.php"):
            return _page("Mr.erp", '<a href="login/login.php">เข้าสู่ระบบ</a>')
        if path == "/login/login.php":
            return _page("Mr.erp", LOGIN_FORM)
        if path == "/login/checklogin.php" and method == "POST":
            user, password = form.get("txtusers", [""])[-1], form.get("txtpasswords", [""])[-1]
            if not user or self.users.get(user) != password:
                return _script('alert("User หรือ Password ไม่ถูกต้อง");location = "../index.php";')
            self.logins += 1
            sess.clear()
            sess["user"] = user
            self.useruse[user] = sid
            return _script('location = "selectdb.php";')
        if path == "/login/logout.php":
            if sess.get("user"):
                self.logouts += 1
                self.useruse.pop(sess["user"], None)
            sess.clear()
            return _script('location = "login.php";')
        if path == "/login/logoutkick.php":
            sess.clear()
            return _script('location = "login.php";')
        kick = self._checkuseruse(sess, sid)
        if path == "/login/selectdb.php":
            rows = "".join(
                f'<p><span>{c["comname"]}</span><span>{c["comfullname"]}</span><span></span><span>'
                f'<a href="mainmenu.php?comidyear={c["comidyear"]}&seldb=1">{c["comname"]}{c["years"]}</a></span></p>'
                for c in COMPANIES if c["comidyear"] in self.allowed)
            return _page("Mr.erp", kick + '<div id="showdata"><p><span>Comname</span><span>Comfullname</span>'
                         f'<span>Remark</span><span>Database name</span></p>{rows}</div>')
        if path == "/login/mainmenu.php":
            company = next((c for c in COMPANIES if c["comidyear"] == query.get("comidyear")), None)
            if company is None:
                return _page("Mr.erp", kick + "Error")
            sess["company"] = company
            links = "".join(f'<li><a href="../{m}/allview.php?idmenu={k}">{k}</a></li>' for k, (m, _p) in MENUS.items())
            return _page("Mr.erp", kick + f'<nav id="mainmenu"><ul>{links}</ul></nav>' + self._footer(sess))
        module = path.strip("/").split("/")[0]
        if module not in ("aptran", "artran", "arse"):
            return None
        rest = path.strip("/").split("/", 1)[1] if "/" in path.strip("/") else ""
        if rest == "allview.php":
            if query.get("idmenu"):
                sess["menu"] = query["idmenu"]
            sess["search"] = query.get("searchdata", "")
            return self._list(module, sess, kick)
        if rest == "component/showdata.php":
            return self._rows(module, sess, form.get("searchdataval", [""])[-1])
        if rest == "allform.php":
            if query.get("id"):
                doc = next((d for d in self.docs if str(d["id"]) == query["id"]), None)
                return self._form(module, sess, kick, doc) if doc else None
            return self._form(module, sess, kick, None)
        if rest == "allsave.php" and method == "POST":
            return self._save(module, sess, sid, form, kick)
        if rest == "component/topcode.php":
            party = next((p for p in SUPPLIERS + CUSTOMERS if p[0] == form.get("id", [""])[-1]), None)
            return party[4] if party else "0"
        if rest == "component/checkcoststd.php":
            item = next((i for i in ITEMS if i[0] == form.get("id", [""])[-1]), None)
            return item[5] if item else "0.00"
        return None

    def _checkuseruse(self, sess, sid) -> str:
        """照 ``checkuseruse.php``：没登录 / 会话号对不上就打出踢人脚本——但**不停**，页面照样往下跑。"""
        if not sess.get("user"):
            return '<script>alert("กรุณา Login เข้าสู่ระบบ");location = "../login/logoutkick.php";</script>'
        if self.useruse.get(sess["user"]) != sid:
            return ('<script>alert("\\"User\\" ดังกล่าวมีผู้ Login ซ้ำ จะทำการออกจากระบบโดยอัตโนมัติ");'
                    'location = "../login/logoutkick.php";</script>')
        return ""

    @staticmethod
    def _footer(sess) -> str:
        c = sess.get("company") or {}
        return (f'<div id="footer"><p><span>Username : </span>{html.escape(sess.get("user", ""))}'
                f'<span>Status : </span>user<span>Company : </span>{html.escape(c.get("comfullname", ""))}'
                f'<span>Database Name : </span>{html.escape(c.get("comname", "") + c.get("years", ""))}</p></div>')

    def _list(self, module, sess, kick) -> str:
        return _page("Mr.erp", kick + f"""
<div id="header"><span><span id="labelsearch">ค้นหารายการ</span><input type="text" name="txtsearch" id="txtsearch" size="40"
 onkeyup="searchdata(1);"><button name="btnsearch" id="btnsearch" onclick="searchdata(2);">Search</button>
<input type="hidden" name="searchdataval" id="searchdataval" value="{html.escape(sess.get('search', ''))}">
<button name="btnadd" id="btnadd" onclick="location='allform.php';">Add</button></span></div>
<div id="body"><div id="detail-box"><div id="showdata"><p><span>เลขที่เอกสาร</span><span>วันที่เอกสาร</span>
<span>ชื่อ</span><span>จำนวนเงิน</span><span>URA</span><span>View</span></p></div></div></div>
{self._footer(sess)}
<script>
function searchdata(ctl){{ if((ctl==1&&event.keyCode==13)||ctl==2){{ location="?searchdata="+document.getElementById("txtsearch").value; }} }}
function showdata(){{ var x=new XMLHttpRequest(); x.open("POST","component/showdata.php");
 x.setRequestHeader("Content-Type","application/x-www-form-urlencoded");
 x.onload=function(){{ document.getElementById("showdata").insertAdjacentHTML("beforeend", x.responseText); }};
 setTimeout(function(){{ x.send("showdata_numrows=30&showdata_pages=1&searchdataval="+encodeURIComponent(document.getElementById("searchdataval").value)); }}, 120); }}
showdata();
</script>""")

    def _rows(self, module, sess, search) -> str:
        menu = sess.get("menu", "")
        term = search.replace(",", "")
        found = [d for d in self.docs if d["module"] == module and d["menu"] == menu]
        if term:
            found = [d for d in found if term in d["docno"] or term in d["name"] or term in f"{d['net']:.2f}"
                     or term in d["date"]]
        found.sort(key=lambda d: (d["date"], d["docno"]), reverse=True)
        if not found:
            return '<p id="nodata">No Data</p>'
        return "".join(
            f'<p><span>{html.escape(d["docno"])}</span><span>{thai(d["date"])}</span><span>{html.escape(d["name"])}</span>'
            f'<span>{fmt(d["net"])}</span><span><a></a><span class="showura"><span><span>รายการ</span></span></span></span>'
            f'<span><a href="allform.php?id={d["id"]}&status=vie"></a></span></p>' for d in found[:30])

    # ── 表单 ────────────────────────────────────────────────────────────
    def _form(self, module, sess, kick, doc) -> str:
        menu = sess.get("menu", "")
        prefix = MENUS.get(menu, (module, "XX"))[1]
        view = doc is not None
        party_id = "txtapmas" if module == "aptran" else "txtarmas"
        parties = SUPPLIERS if module == "aptran" else CUSTOMERS
        ro = " readonly" if view else ""
        auto = menu in self.autono and not view
        number = (doc["docno"][len(prefix):] if view else (f"{self.autono[menu]:06d}" if auto else ""))
        v = (lambda key, default="": html.escape(str(doc.get(key, default)))) if view else (lambda key, default="": default)
        lines = doc["lines"] if view else [None]
        rows = "".join(_row(n, line, view) for n, line in enumerate(lines, 1))
        receipt = ""
        if module == "arse":
            receipt = (f'<p><label>เงินสด</label><input type="text" name="txtcashpay" id="txtcashpay" value="{v("cashpay", "0.00")}"'
                       f' onchange="calccashpay();"{ro}></p><p><label>รวมรับชำระ</label><input type="text" name="txttotalrec"'
                       f' id="txttotalrec" value="{v("cashpay", "0.00")}" readonly></p>')
        # 对方清单每行：内部编号、码、称谓、名称、信用天数，再带「开票对方」的编号、码、名称（选了对方页面一并填进开票对方）
        party_rows = [[p[0], p[1], p[2], p[3], p[4], p[0], p[1], p[3]] for p in parties]
        data = {"txtothvatrate": VATRATES, party_id: party_rows, "txtcode": ITEMS, "store": SIMPLE["store"],
                **{k: SIMPLE[k] for k in ("txtothcombrh", "txtothdept_h", "txtothsuborder_h", "txtemp", "txtothcombrhcus")}}
        alert = f'<script>alert({json.dumps(self.alert_on_form)});</script>' if self.alert_on_form and not view else ""
        cash = "checked" if (doc.get("cash") if view else True) else ""
        return _page("Mr.erp", kick + alert + f"""
<form name="frm" id="frm" method="post" action="allsave.php">
<input type="hidden" name="status" id="status" value="{'vie' if view else ''}">
<p><label>เลขที่</label><input type="text" name="txtdoctype" id="txtdoctype" size="2" value="{prefix}" readonly><input type="text"
 name="txtdocnum" id="txtdocnum" maxlength="18" value="{html.escape(number)}"{' readonly class="disabled"' if auto or view else ''}>
<label>สาขา</label><input type="text" name="txtothcombrh" id="txtothcombrh" value="00000" onfocus="bshlistbox(this);" readonly>
<input type="hidden" name="othcombrhval" id="othcombrhval" value="1">
<label>โอนข้อมูล</label><input type="checkbox" name="chktran" id="chktran" value="1" checked></p>
<p><label>วันที่</label><input type="text" name="txtdate" id="txtdate" readonly value="{thai(doc['date']) if view else ''}">
<label>เครดิต</label><input type="text" name="txttopcode" id="txttopcode" maxlength="3" onblur="chkduedate();" value="{v('topcode')}"{ro}>
<label>ครบกำหนด</label><input type="text" name="txtduedate" id="txtduedate" readonly>
<label>เงินสด</label><input type="checkbox" name="chkcash" id="chkcash" value="1" {cash}{' disabled' if view else ''}>
<label>อัตราภาษี</label><input type="text" name="txtothvatrate" id="txtothvatrate" readonly onfocus="bshlistbox(this);" value="{v('vatname')}">
<input type="hidden" name="othvatrateval" id="othvatrateval" value="{v('vatid')}">
<input type="hidden" name="othvatrateinex" id="othvatrateinex" value="{v('inex', '1')}">
<label>แผนก</label><input type="text" name="txtothdept_h" id="txtothdept_h" readonly onfocus="bshlistbox(this);" value="00">
<input type="hidden" name="othdept_hval" id="othdept_hval" value="1">
<label>งาน</label><input type="text" name="txtothsuborder_h" id="txtothsuborder_h" readonly onfocus="bshlistbox(this);" value="00">
<input type="hidden" name="othsuborder_hval" id="othsuborder_hval" value="1"></p>
<p><label>พนักงาน</label><input type="text" name="txtemp" id="txtemp" readonly onfocus="bshlistbox(this);" value="สมชาย ใจดี">
<input type="hidden" name="empval" id="empval" value="1">
<label>กำหนดส่งสินค้า</label><input type="text" name="txtdeliverydate" id="txtdeliverydate" readonly value="{thai(doc['date']) if view else ''}"></p>
<p><span><label>รหัส</label><input type="text" name="{party_id}" id="{party_id}" readonly onfocus="bshlistbox(this);" value="{v('party')}"><input
 type="text" name="{party_id}detail" id="{party_id}detail" readonly value="{v('name')}"><input type="hidden" name="partyval" id="partyval" value="{v('partyid')}"></span>
<span><label>รหัส (บิล)</label><input type="text" name="{party_id}_inv" id="{party_id}_inv" readonly value="{v('party')}"><input
 type="text" name="{party_id}_invdetail" id="{party_id}_invdetail" readonly value="{v('name')}"><input type="hidden" name="party_invval" id="party_invval" value="{v('partyid')}"></span>
<label>สาขา</label><input type="text" name="txtothcombrhcus" id="txtothcombrhcus" readonly value="{'00000' if view else ''}">
<input type="hidden" name="othcombrhcusval" id="othcombrhcusval" value="{'1' if view else ''}"></p>
<p><label>เลขที่บิล</label><input type="text" name="txtvatnum" id="txtvatnum" maxlength="20" value="{v('ref')}"{ro}><label>วันที่</label><input
 type="text" name="txtvatdate" id="txtvatdate" readonly value="{thai(doc['ref_date']) if view and doc.get('ref_date') else ''}"></p>
<input type="button" name="btn-add" id="btn-add" value="เพิ่ม" onclick="addmain();">
<div id="data-main">{rows}</div>
<p><label>ยอดรวม</label><input type="text" name="txtgrandtotal" id="txtgrandtotal" readonly value="{v('total', '0.00')}"></p>
<p><label>ส่วนลด</label><input type="text" name="txtdiscount" id="txtdiscount" onblur="calcgrandtotal();" value="{v('discount', '0.00')}"{ro}></p>
<p><label>ยอดรวมก่อนภาษี</label><input type="text" name="txtbeforevat" id="txtbeforevat" readonly value="{fmt(doc['before_vat']) if view else '0.00'}"></p>
<p><label>ภาษี</label><input type="text" name="txtvatrate" id="txtvatrate" readonly value="{v('rate', '0.00')}"><input type="text" name="txtvat"
 id="txtvat" readonly value="{fmt(doc['vat']) if view else '0.00'}"></p>
<p><label>ยอดสุทธิ</label><input type="text" name="txtnet" id="txtnet" readonly value="{fmt(doc['net']) if view else '0.00'}"></p>
{receipt}
</form>
{'' if view else '<button name="btnsave" id="btnsave" onclick="checknull();">Save</button><button name="btncancel" id="btncancel">Cancel</button>'}
{self._footer(sess)}
<script>var DATA={json.dumps(data, ensure_ascii=False)};var PARTY="{party_id}";var MODULE="{module}";var AFTER={self.after_select_ms};</script>
<script>{FORM_JS}</script>""")

    def _save(self, module, sess, sid, form, kick) -> str:
        if self.kick_on_save:
            self.kick_on_save = False
            self.kick_now(sess.get("user", "ccbot"))
            kick = self._checkuseruse(sess, sid)
        if self.down_on_save_s:
            seconds, self.down_on_save_s = self.down_on_save_s, 0.0
            self.down = True
            threading.Timer(seconds, lambda: setattr(self, "down", False)).start()
            raise _Drop()
        if self.fail_save:
            message, self.fail_save = self.fail_save, None
            return _page("Mr.erp", kick + f"<script>alert({json.dumps(message)});</script>")
        f = {k: v[-1] for k, v in form.items()}
        menu = sess.get("menu", "")
        prefix = MENUS.get(menu, (module, "XX"))[1]
        party_id = "txtapmas" if module == "aptran" else "txtarmas"
        lines = []
        for code, qty, price, total in zip(form.get("txtcode[]", []), form.get("txtamount[]", []),
                                           form.get("txtprice[]", []), form.get("txttotal[]", [])):
            lines.append({"code": code, "qty": str(money(qty)), "price": str(money(price)), "total": str(money(total))})
        vat = next((r for r in VATRATES if r[0] == f.get("othvatrateval")), VATRATES[0])
        with self.lock:
            self.next_id += 1
            if menu in self.autono:
                self.autono[menu] += 1
            doc = {"id": self.next_id, "module": module, "menu": menu, "docno": prefix + f.get("txtdocnum", ""),
                   "date": eng(f["txtdate"]), "party": f.get(party_id, ""), "partyid": f.get("partyval", ""),
                   "name": f.get(party_id + "detail", ""), "ref": f.get("txtvatnum", ""),
                   "ref_date": eng(f["txtvatdate"]) if f.get("txtvatdate") else "", "topcode": f.get("txttopcode", ""),
                   "cash": f.get("chkcash") == "1", "vatname": vat[1], "vatid": vat[0], "inex": vat[3], "rate": vat[2],
                   "lines": lines, "total": f.get("txtgrandtotal", ""), "discount": f.get("txtdiscount", "0.00"),
                   "before_vat": money(f.get("txtbeforevat")), "vat": money(f.get("txtvat")), "net": money(f.get("txtnet")),
                   "cashpay": f.get("txtcashpay", ""), "user": sess.get("user")}
            if self.tamper:
                self.tamper(doc)
            self.docs.append(doc)
        if self.drop_after_commit:
            self.drop_after_commit = False
            if self.down_after_commit_s:  # 断开后网络一段时间不通（浏览器的自动重发也到不了）
                self.down = True
                threading.Timer(self.down_after_commit_s, lambda: setattr(self, "down", False)).start()
            raise _Drop()
        return _page("Mr.erp", kick + '<div id="showprocess"><div id="process"></div><span>Save Success</span></div>'
                     '<script>setTimeout(function() { location = "allview.php"; }, 1000);</script>')


class _Drop(Exception):
    """写了库、回包前断开连接（网络断在存档那一下）。"""


def _row(n: int, line: Optional[Dict], view: bool) -> str:
    value = (lambda key, default="": html.escape(str(line.get(key, default)))) if line else (lambda key, default="": default)
    ro = " readonly" if view else ""
    return (f'<p class="row"><span><input type="text" name="txtno[]" id="txtno{n}" value="{n}" readonly></span>'
            f'<span><input type="text" name="txtcode[]" id="txtcode{n}" {"" if view else "onfocus=\"bshlistbox(this);\""} readonly'
            f' value="{value("code")}"><input type="hidden" name="txtcodeval[]" id="txtcodeval{n}"></span>'
            f'<span><input type="text" name="txtname[]" id="txtname{n}" readonly></span>'
            f'<span><input type="text" name="txtstore[]" id="txtstore{n}" {"" if view else "onfocus=\"bshlistbox(this);\""} readonly value="WH1">'
            f'<input type="hidden" name="txtstoreval[]" id="txtstoreval{n}" value="1"></span>'
            f'<span><input type="text" name="txtamount[]" id="txtamount{n}" value="{value("qty", "0.00")}" onblur="calcrow(this);"{ro}></span>'
            f'<span><input type="text" name="txtunit[]" id="txtunit{n}" readonly><input type="hidden" name="txtunitval[]" id="txtunitval{n}"></span>'
            f'<span><input type="text" name="txtprice[]" id="txtprice{n}" value="{value("price", "0.00")}" onblur="calcrow(this);"{ro}></span>'
            f'<span><input type="text" name="txttotal[]" id="txttotal{n}" value="{value("total", "0.00")}" readonly></span></p>')


def _page(title: str, body: str) -> str:
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head><body>{body}</body></html>'


def _script(code: str) -> str:
    return _page("Mr.erp", f'<script type="text/javascript">{code}</script>')


LOGIN_FORM = """<form name="frm" id="frm" method="post" action="checklogin.php"><h2>Login</h2>
<p><label>User</label><input type="text" name="txtusers" id="txtusers" size="15" maxlength="20"></p>
<p><label>Password</label><input type="password" name="txtpasswords" id="txtpasswords" size="15" maxlength="20"></p>
<p><input type="submit" name="btnsubmit" id="btnsubmit" value="Submit"><input type="reset" name="btncancel" id="btncancel" value="Cancel"></p>
</form>"""

# 页面自己的脚本（照 aptran/component/javascript/allform.js 与 bshlistbox.js 的做法缩写）：选择框、合计、必填检查、提交
FORM_JS = r"""
function onlynumber(v){ return String(v||'0').replace(/,/g,''); }
function nf(v){ return (Math.round(parseFloat(v)*100)/100).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
var BOX=null, ELEM=null;
function cfg(elem){
  var id=elem.id, row=(id.match(/^(txtcode|txtstore)(\d+)$/)||[])[2];
  if(id==='txtothvatrate'){ return {data:DATA.txtothvatrate, cols:[['othvatrateval',0],[id,1],['txtvatrate',1],['othvatrateinex',1]], hide:[0,2,3]}; }
  if(id===PARTY){ return {data:DATA[PARTY], cols:[['partyval'],[id],[null],[PARTY+'detail'],[null],['party_invval'],[PARTY+'_inv'],[PARTY+'_invdetail']], hide:[0,4,5,6,7], extra:true}; }
  if(row && id.indexOf('txtcode')===0){ return {data:DATA.txtcode, cols:[['txtcodeval'+row,0],[id,1],['txtname'+row,1],['txtunitval'+row,0],['txtunit'+row,1],[null,1]], hide:[0,3,5]}; }
  if(row && id.indexOf('txtstore')===0){ return {data:DATA.store, cols:[['txtstoreval'+row,0],[id,1],[null,1]], hide:[0]}; }
  var simple={txtothcombrh:'othcombrhval',txtothdept_h:'othdept_hval',txtothsuborder_h:'othsuborder_hval',txtemp:'empval',txtothcombrhcus:'othcombrhcusval'};
  if(simple[id]){ return {data:DATA[id], cols:[[simple[id],0],[id,1],[null,1]], hide:[0]}; }
  return null;
}
function bshlistbox(elem){
  var c=cfg(elem); if(!c){ return; }
  bshhidelistbox(); ELEM=elem;
  BOX=document.createElement('div'); BOX.id='bshlistbox';
  BOX.innerHTML='<div id="bshlistboxsearch"><span>ค้นหา</span><input id="bshlistboxinpsearch" onkeyup="bshdatalistbox();" tabindex="-1"></div>'+
    '<div id="bshlistboxtitle"></div><div id="bshlistboxdetail"><div id="bshlistboxdetailshow"></div></div>'+
    '<div id="bshlistboxcontrol"><button id="bshlistboxcontrolhide" onclick="bshhidelistbox();" tabindex="-1">ปิด</button></div>';
  document.body.appendChild(BOX); BOX.style.display='block'; bshdatalistbox();
}
function bshdatalistbox(){
  var c=cfg(ELEM), q=document.getElementById('bshlistboxinpsearch').value.trim(), out='';
  c.data.forEach(function(r){ var hit=!q||r.some(function(x){return String(x).indexOf(q)>=0;}); if(!hit){return;}
    out+='<p onclick="bshlistboxdata2field(this);">'+r.map(function(x,i){ return '<span'+(c.hide.indexOf(i)>=0?' style="display: none;"':'')+'>'+x+'</span>'; }).join('')+'</p>'; });
  setTimeout(function(){ document.getElementById('bshlistboxdetailshow').innerHTML=out; }, 60);
}
function bshhidelistbox(){ var b=document.getElementById('bshlistbox'); if(b){ b.parentNode.removeChild(b); } }
function bshlistboxdata2field(p){
  var c=cfg(ELEM), cells=p.childNodes, elem=ELEM;
  c.cols.forEach(function(t,i){ if(t[0]&&document.getElementById(t[0])){ document.getElementById(t[0]).value=cells[i].innerText.trim()||cells[i].textContent.trim(); } });
  if(c.extra){ document.getElementById('txtothcombrhcus').value='00000'; document.getElementById('othcombrhcusval').value='1'; }
  bshhidelistbox(); after(elem);
}
function post(url, data, done){ var x=new XMLHttpRequest(); x.open('POST', url); x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');
  x.onload=function(){ done(x.responseText); }; setTimeout(function(){ x.send(data); }, AFTER); }
function after(elem){
  if(elem.id===PARTY){ post('component/topcode.php', 'id='+document.getElementById('partyval').value, function(r){ document.getElementById('txttopcode').value=r; chkduedate(); calcgrandtotal(); }); }
  else if(elem.id==='txtothvatrate'){ calcgrandtotal(); }
  else if(/^txtcode\d+$/.test(elem.id)){ var n=elem.id.replace('txtcode',''); post('component/checkcoststd.php', 'id='+document.getElementById('txtcodeval'+n).value, function(r){ var p=document.getElementById('txtprice'+n); p.value=nf(r); calcrow(p); }); }
}
function chkduedate(){ document.getElementById('txtduedate').value=document.getElementById('txtdate').value; }
function calcrow(elem){ var row=elem.id.match(/\d+$/)[0];
  var q=document.getElementById('txtamount'+row), p=document.getElementById('txtprice'+row);
  if(q.value===''){q.value='0.00';} if(p.value===''){p.value='0.00';}
  document.getElementById('txttotal'+row).value=nf(parseFloat(onlynumber(q.value))*parseFloat(onlynumber(p.value))); calcgrandtotal(); }
function calcgrandtotal(){
  var inex=document.getElementById('othvatrateinex').value, rate=parseFloat(onlynumber(document.getElementById('txtvatrate').value)), sum=0, remvat=0;
  document.querySelectorAll("input[name='txttotal[]']").forEach(function(e){ var t=parseFloat(onlynumber(e.value));
    if(inex=='2'){ var r=parseFloat((t*rate/(100+rate)).toFixed(2)); remvat+=r; sum+=t-r; } else { sum+=t; } });
  document.getElementById('txtgrandtotal').value=nf(sum);
  var disc=parseFloat(onlynumber(document.getElementById('txtdiscount').value)), before=sum-disc;
  document.getElementById('txtbeforevat').value=nf(before);
  var vat = inex=='2' ? remvat-(disc>0?parseFloat((disc*rate/100).toFixed(2)):0) : before*rate/100;
  document.getElementById('txtvat').value=nf(vat); document.getElementById('txtnet').value=nf(before+parseFloat(onlynumber(nf(vat)))); }
function calccashpay(){ var c=document.getElementById('txtcashpay'); if(c.value===''){c.value='0.00';} document.getElementById('txttotalrec').value=nf(onlynumber(c.value)); }
function addmain(){ var n=document.querySelectorAll("input[name='txtcode[]']").length+1;
  var p=document.querySelector('#data-main p.row').cloneNode(true);
  p.innerHTML=p.innerHTML.replace(/(id="txt[a-z]+)1"/g, '$1'+n+'"').replace(/value="1" readonly/, 'value="'+n+'" readonly');
  p.querySelectorAll('input').forEach(function(e){ if(/^txt(code|name|unit|codeval|unitval)\d+$/.test(e.id)){ e.value=''; } if(/^txt(amount|price|total)\d+$/.test(e.id)){ e.value='0.00'; } });
  document.getElementById('data-main').appendChild(p); }
function req(id, msg){ var e=document.getElementById(id); if(e && e.value===''){ alert(msg); return false; } return true; }
function checknull(){
  if(!req('txtdocnum','กรุณาป้อนเลขที่เอกสาร')||!req('txtothcombrh','กรุณาป้อนสาขา')||!req('txtdate','กรุณาป้อนวันที่เอกสาร')||
     !req('txtothvatrate','กรุณาป้อนอัตราภาษี')||!req('txtothdept_h','กรุณาป้อนแผนก')||!req('txtothsuborder_h','กรุณาป้อนงาน')||
     !req('txtemp','กรุณาป้อนพนักงาน')||!req('txtdeliverydate','กรุณาป้อนกำหนดส่งสินค้า')||!req(PARTY,'กรุณาป้อนรหัสลูกค้า')||
     !req(PARTY+'_inv','กรุณาป้อนรหัสลูกค้า (บิล)')||(MODULE==='aptran'&&!req('txtvatnum','กรุณาป้อนเลขที่บิล'))||
     !req('txtvatdate','กรุณาป้อนวันที่')||!req('txtothcombrhcus','กรุณาป้อนสาขา')){ return false; }
  var codes=document.getElementsByName('txtcode[]'), qty=document.getElementsByName('txtamount[]');
  for(var i=0;i<codes.length;i++){ if(codes[i].value===''){ alert('ป้อนรหัสสินค้าให้ครบ'); return false; }
    if(parseFloat(onlynumber(qty[i].value))==0){ alert('ป้อนจำนวนให้ครบ'); return false; } }
  if(MODULE==='arse'&&onlynumber(document.getElementById('txttotalrec').value)!==onlynumber(document.getElementById('txtnet').value)){ alert('ยอดรับชำระไม่เท่ากับยอดสุทธิ'); return false; }
  document.getElementById('frm').submit(); }
"""
