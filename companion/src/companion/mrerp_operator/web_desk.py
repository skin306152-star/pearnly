# -*- coding: utf-8 -*-
"""批次线程这一侧的 MR.ERP 网页驱动（ADR-026、评估 §14.2）。本模块不导入 Qt：托盘进程只在这里开子进程、走管道。

- ``ProcessDesk``：每批拉起一个 MR.ERP 桌面子进程（``companion.exe --mrerp-desk``，``web_driver.child_main``），经本机一条
  带一次性口令的管道（``multiprocessing.connection``：Windows 命名管道 / 其他系统本机套接字，口令做 HMAC 挑战）一问一答。
  网页内核只活在子进程里；``close`` 关窗、子进程退出，渲染进程随之退出，托盘里不留网页内核。每条消息只带动作名与参数，
  日志只记动作名（登录页的密码经管道交给子进程填格，不进任何日志）；
- ``WebPage``：在任一实现（子进程 / 测试里同进程的 ``web_driver.LocalDesk``）之上提供引擎用的动作：打开地址并等加载完、
  填格（触发页面自己的 ``input / change / blur``）、勾选、按页面原本方式用选择框（bshlistbox：聚焦 → 搜索框打码 → 点那一行）、
  点按钮、读格子、读页面文字 / 片段、等条件、截图。动作之间不轮询常驻：只在一张单录的过程中等页面。
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

ENV_ADDRESS = "PEARNLY_MRERP_DESK_ADDRESS"
ENV_FAMILY = "PEARNLY_MRERP_DESK_FAMILY"
ENV_KEY = "PEARNLY_MRERP_DESK_KEY"
DESK_FLAG = "--mrerp-desk"
CONNECT_WAIT_S = 60.0  # 拉起后等子进程把管道建好
STEP_S = 0.15  # 等页面时两次查看的间隔
LOAD_S = 45.0  # 一次页面加载最多等多久（MR.ERP 往返实测前按宽的给，第 0 步给真数）


class DeskGone(Exception):
    """子进程没了 / 管道断了 / 一个动作超时没回：当作网络断（交批次等恢复后续录）。"""


def desk_command(*, frozen: bool, executable: str) -> List[str]:
    """拉起 MR.ERP 桌面子进程的命令：打包后是同一个 companion.exe 加 --mrerp-desk；开发时用同一个 Python 跑本包。"""
    if frozen:
        return [executable, DESK_FLAG]
    return [executable, "-m", __package__.rsplit(".", 1)[0] + ".main", DESK_FLAG]


def desk_cwd(*, frozen: bool, executable: str) -> str:
    if frozen:
        return os.path.dirname(executable)
    return str(Path(__file__).resolve().parents[3])  # companion/（-m src.companion.main 从这里找包）


class ProcessDesk:
    """每批一个 MR.ERP 桌面子进程。``call(op, **args)`` 一问一答；``close()`` 关窗、等子进程退出（不退就结束它）。"""

    def __init__(self, *, command: Optional[List[str]] = None, cwd: Optional[str] = None,
                 env: Optional[Dict[str, str]] = None, log: Callable[[str], None] = lambda _m: None):
        frozen = bool(getattr(sys, "frozen", False))
        self.command = command or desk_command(frozen=frozen, executable=sys.executable)
        self.cwd = cwd or desk_cwd(frozen=frozen, executable=sys.executable)
        self.env = env
        self.log = log
        self.process: Optional[subprocess.Popen] = None
        self.conn = None
        self._seq = 0
        self._lock = threading.Lock()  # 一问一答不能交叉：批次线程与 CC 窗口（实时画面、「查看」）共用一条管道

    @property
    def pid(self) -> Optional[int]:
        return self.process.pid if self.process is not None else None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None and self.conn is not None

    def start(self) -> None:
        if self.alive:
            return
        from multiprocessing.connection import Client, arbitrary_address

        family = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
        address = arbitrary_address(family)
        key = secrets.token_bytes(32)
        env = dict(self.env if self.env is not None else os.environ)
        env.update({ENV_ADDRESS: address, ENV_FAMILY: family, ENV_KEY: key.hex()})
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW：不闪控制台
        self.process = subprocess.Popen(self.command, cwd=self.cwd, env=env, close_fds=True, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        deadline = time.monotonic() + CONNECT_WAIT_S
        while True:
            try:
                self.conn = Client(address, family=family, authkey=key)
                break
            except (OSError, EOFError):
                if self.process.poll() is not None or time.monotonic() > deadline:
                    self._kill()
                    raise DeskGone("MR.ERP desk did not start") from None
                time.sleep(0.2)
        self.log(f"mrerp desk started pid={self.process.pid}")

    def call(self, op: str, timeout: float = 30.0, *, wait: Optional[float] = None, **args) -> Dict:
        """``wait``：最多等前一个动作多久（CC 窗口来要实时画面时只等一会儿，等不到回 ``busy``，不卡窗口）。"""
        if not self.alive:
            raise DeskGone("MR.ERP desk is not running")
        if not self._lock.acquire(timeout=-1 if wait is None else wait):
            return {"ok": False, "error": "busy"}
        try:
            self._seq += 1
            message = {"id": self._seq, "op": op, "args": args, "timeout": timeout}
            try:
                self.conn.send_bytes(json.dumps(message, ensure_ascii=False).encode("utf-8"))
                if not self.conn.poll(timeout + 10):
                    raise DeskGone(f"no answer to {op}")
                reply = json.loads(self.conn.recv_bytes().decode("utf-8"))
            except (OSError, EOFError, ValueError) as error:
                raise DeskGone(f"{op}: {type(error).__name__}") from None
        finally:
            self._lock.release()
        reply.pop("id", None)
        return reply

    def close(self) -> None:
        try:
            if self.alive:
                self.call("close", timeout=10)
        except DeskGone:
            pass
        finally:
            if self.conn is not None:
                try:
                    self.conn.close()
                except OSError:
                    pass
                self.conn = None
            if self.process is not None:
                try:
                    self.process.wait(15)
                except subprocess.TimeoutExpired:
                    self._kill()
            self.log("mrerp desk closed")

    def _kill(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                pass


# ── 页面动作（隔离脚本环境里的 DOM 操作）──────────────────────────────────────
def _script(body: str, args: Dict) -> str:
    return "(function(a){" + body + "})(" + json.dumps(args, ensure_ascii=False) + ")"


# 聚焦 / 失焦：先用元素自己的 focus()/blur()；窗口收起时浏览器可能不发焦点事件，这时补发同名事件，页面的 onfocus / onblur 照样跑
_FOCUS = """
function focusOn(el){var seen=false;var f=function(){seen=true;};el.addEventListener('focus',f,{once:true});
 el.focus();if(!seen){el.dispatchEvent(new FocusEvent('focus'));}el.removeEventListener('focus',f);}
function blurOff(el){var seen=false;var f=function(){seen=true;};el.addEventListener('blur',f,{once:true});
 el.blur();if(!seen){el.dispatchEvent(new FocusEvent('blur'));}el.removeEventListener('blur',f);}
"""
JS_FILL = _FOCUS + """
var el=document.getElementById(a.id);if(!el){return JSON.stringify({ok:false,error:'missing'});}
if(el.disabled){return JSON.stringify({ok:false,error:'disabled'});}
if(a.focus){focusOn(el);}
el.value=a.value;['input','change'].forEach(function(t){el.dispatchEvent(new Event(t,{bubbles:true}));});
if(a.focus){blurOff(el);}
if(a.secret){return JSON.stringify({ok:el.value.length===a.value.length});}
return JSON.stringify({ok:true,value:el.value});
"""
JS_CHECK = """
var el=document.getElementById(a.id);if(!el){return JSON.stringify({ok:false,error:'missing'});}
if(el.checked!==a.on){el.click();}return JSON.stringify({ok:true,value:el.checked});
"""
JS_CLICK = """
var el=a.id?document.getElementById(a.id):document.querySelector(a.selector);
if(!el){return JSON.stringify({ok:false,error:'missing'});}
if(el.disabled){return JSON.stringify({ok:false,error:'disabled'});}
el.click();return JSON.stringify({ok:true});
"""
JS_READ = """
var out={};a.ids.forEach(function(id){var el=document.getElementById(id);var v=null;
 if(el&&el.type==='checkbox'){v=el.checked?'1':'';}
 else if(el&&(el.tagName==='INPUT'||el.tagName==='SELECT'||el.tagName==='TEXTAREA')){v=String(el.value);}
 else if(el){v=String(el.innerText||'');}
 out[id]=v;});
return JSON.stringify(out);
"""
JS_ATTRS = """
var out={};a.ids.forEach(function(id){var el=document.getElementById(id);
 out[id]=el?{readonly:!!el.readOnly,disabled:!!el.disabled,type:el.type||el.tagName.toLowerCase()}:null;});
return JSON.stringify(out);
"""
JS_TEXT = """
var el=a.selector?document.querySelector(a.selector):document.body;
return JSON.stringify(el?String(el.innerText||'').slice(0,a.limit):null);
"""
JS_HTML = """
var el=document.querySelector(a.selector);return JSON.stringify(el?String(el.innerHTML).slice(0,a.limit):null);
"""
JS_COUNT = """return JSON.stringify(document.querySelectorAll(a.selector).length);"""
JS_FORM_OF = """
var el=document.getElementById(a.id);var f=el?el.form:null;
return JSON.stringify(f?{action:String(f.getAttribute('action')||''),method:String(f.method||'')}:null);
"""
# 选择框（MR.ERP 的 bshlistbox）：聚焦格子 → 页面自己弹出清单与搜索框；每行第一列是隐藏的内部编号，按第一个看得见的列
# （码 / 税率名）完全相同认那一行
JS_PICK_OPEN = _FOCUS + """
var el=document.getElementById(a.id);if(!el){return JSON.stringify({ok:false,error:'missing'});}
var old=document.getElementById('bshlistbox');if(old&&old.style.display!=='none'){var h=document.getElementById('bshlistboxcontrolhide');if(h){h.click();}}
focusOn(el);return JSON.stringify({ok:true});
"""
JS_PICK_SEARCH = """
var s=document.getElementById('bshlistboxinpsearch');var box=document.getElementById('bshlistbox');
if(!s||!box||box.style.display==='none'){return JSON.stringify({ok:false,error:'no list'});}
s.value=a.code;s.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true,key:'Unidentified'}));
return JSON.stringify({ok:true});
"""
JS_PICK_ROW = """
var show=document.getElementById('bshlistboxdetailshow');if(!show){return JSON.stringify({ok:false,error:'no list'});}
var hits=[];show.querySelectorAll('p').forEach(function(p){var spans=p.querySelectorAll('span');
 for(var i=0;i<spans.length;i++){if(spans[i].style.display==='none'){continue;}
  if(String(spans[i].textContent).trim()===a.code){hits.push(p);}break;}});
if(hits.length!==1){return JSON.stringify({ok:false,error:hits.length?'ambiguous':'not found',rows:show.querySelectorAll('p').length});}
hits[0].click();return JSON.stringify({ok:true});
"""


class PageError(Exception):
    """页面上找不到要的东西 / 动作没成：交给引擎判断（通常按配方对不上或认不出的页面处理）。"""


class WebPage:
    """引擎用的页面动作。``desk`` 是 ``ProcessDesk`` 或 ``web_driver.LocalDesk``；``presence`` 每次要动页面前调（有人在用就等）。"""

    def __init__(self, desk, *, presence: Optional[Callable[[], bool]] = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, load_s: float = LOAD_S):
        self.desk, self.presence, self.sleep, self.clock = desk, presence, sleep, clock
        self.load_s = load_s
        self.dialogs: List[Dict] = []  # 收到还没被引擎认领的弹框
        self.last: Dict = {}

    # ── 基础 ────────────────────────────────────────────────────────────
    def _person(self) -> bool:
        return bool(self.presence()) if self.presence else False

    def state(self) -> Dict:
        st = self.desk.call("state", timeout=10)
        if not st.get("ok"):
            raise PageError(str(st.get("error") or "state"))
        self.dialogs += st.get("dialogs") or []
        self.last = st
        return st

    def take_dialogs(self) -> List[Dict]:
        self.state()
        found, self.dialogs = self.dialogs, []
        return found

    def js(self, body: str, args: Dict, timeout: float = 15.0):
        reply = self.desk.call("js", timeout=timeout, code=_script(body, args))
        if not reply.get("ok"):
            raise PageError(str(reply.get("error") or "js"))
        return reply.get("value")

    # ── 导航 ────────────────────────────────────────────────────────────
    def open(self, url: str, *, base: str = "") -> Dict:
        """打开地址并等加载完（或超时）。回最后的状态（``last_ok`` 假 = 加载失败，多半是网络断了）。"""
        before = self.state()["loads"]
        reply = self.desk.call("open", timeout=10, url=url, **({"base": base} if base else {}))
        if not reply.get("ok"):
            raise PageError(str(reply.get("error") or "open"))
        return self.wait_load(before)

    def wait_load(self, before: int, seconds: Optional[float] = None) -> Dict:
        """等页面在 ``before`` 之后至少加载完一次、且现在没在加载。超时回当时的状态（``timeout`` 真）。"""
        deadline = self.clock() + (self.load_s if seconds is None else seconds)
        while True:
            st = self.state()
            if st["loads"] > before and not st["loading"]:
                return {**st, "timeout": False}
            if self.clock() > deadline:
                return {**st, "timeout": True}
            self.sleep(STEP_S)

    def wait_for(self, check: Callable[[], object], seconds: float) -> object:
        """限时一次次看（``check`` 回真值即成）；中途有人在窗口里动过就等人停手后重新计时。"""
        deadline = self.clock() + seconds
        while True:
            found = check()
            if found:
                return found
            if self._person():
                deadline = self.clock() + seconds
                continue
            if self.clock() > deadline:
                return found
            self.sleep(STEP_S)

    # ── 格子 ────────────────────────────────────────────────────────────
    def fill(self, field_id: str, value: str, *, focus: bool = True, secret: bool = False) -> str:
        """填一格。``secret``：密码这种，页面不回读值（只回填进去没有），值不出现在任何回执里。"""
        self._person()
        out = self.js(JS_FILL, {"id": field_id, "value": str(value), "focus": focus, "secret": bool(secret)})
        if not out or not out.get("ok"):
            raise PageError(f"fill {field_id}: {(out or {}).get('error')}")
        return str(out.get("value") or "")

    def check(self, field_id: str, on: bool) -> bool:
        self._person()
        out = self.js(JS_CHECK, {"id": field_id, "on": bool(on)})
        if not out or not out.get("ok"):
            raise PageError(f"check {field_id}: {(out or {}).get('error')}")
        return bool(out.get("value"))

    def click(self, field_id: str = "", *, selector: str = "") -> None:
        self._person()
        out = self.js(JS_CLICK, {"id": field_id, "selector": selector})
        if not out or not out.get("ok"):
            raise PageError(f"click {field_id or selector}: {(out or {}).get('error')}")

    def read(self, ids: List[str]) -> Dict[str, Optional[str]]:
        return dict(self.js(JS_READ, {"ids": list(ids)}) or {})

    def attrs(self, ids: List[str]) -> Dict[str, Optional[Dict]]:
        return dict(self.js(JS_ATTRS, {"ids": list(ids)}) or {})

    def text(self, selector: str = "", limit: int = 6000) -> str:
        return str(self.js(JS_TEXT, {"selector": selector, "limit": limit}) or "")

    def html(self, selector: str, limit: int = 400_000) -> Optional[str]:
        return self.js(JS_HTML, {"selector": selector, "limit": limit})

    def form_of(self, field_id: str) -> Optional[Dict]:
        """这一格所在表单的提交地址（登录前核对是不是 MR.ERP 自己的登录表单）。"""
        return self.js(JS_FORM_OF, {"id": field_id})

    def count(self, selector: str) -> int:
        return int(self.js(JS_COUNT, {"selector": selector}) or 0)

    def pick(self, field_id: str, code: str, *, seconds: float = 15.0) -> None:
        """按页面原本方式用选择框选一项：聚焦格子（页面弹出清单）→ 搜索框打码（页面自己筛）→ 点码完全相同的那一行（页面自己
        填格子和隐藏值、跑选完以后的脚本）。不改隐藏值。清单里没有 / 不止一行都不选，回错。"""
        self._person()
        out = self.js(JS_PICK_OPEN, {"id": field_id})
        if not out or not out.get("ok"):
            raise PageError(f"pick {field_id}: {(out or {}).get('error')}")
        searched = self.wait_for(lambda: (self.js(JS_PICK_SEARCH, {"code": str(code)}) or {}).get("ok"), seconds)
        if not searched:
            raise PageError(f"pick {field_id}: list did not open")
        last: Dict = {}

        def chosen():
            nonlocal last
            last = self.js(JS_PICK_ROW, {"code": str(code)}) or {}
            return last.get("ok") or last.get("error") == "ambiguous"

        self.wait_for(chosen, seconds)
        if not last.get("ok"):
            raise PageError(f"pick {field_id}: {last.get('error') or 'not found'}")

    # ── 其他 ────────────────────────────────────────────────────────────
    def confirm_next(self, answer: bool) -> None:
        self.desk.call("confirm", timeout=5, answer=bool(answer))

    def screenshot(self, size=(1280, 800), *, wait: Optional[float] = None) -> str:
        reply = self.desk.call("screenshot", timeout=20, size=list(size), **({"wait": wait} if wait is not None else {}))
        return str(reply.get("png") or "") if reply.get("ok") else ""

    def show(self, *, wait: Optional[float] = None) -> bool:
        return bool(self.desk.call("show", timeout=10, **({"wait": wait} if wait is not None else {})).get("ok"))
