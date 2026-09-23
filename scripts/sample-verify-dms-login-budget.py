# -*- coding: utf-8 -*-
"""用**假 DMS**证明"等多久"这套口径真的生效 —— 真浏览器、真适配器、零外部依赖。

## 为什么要有这个（不是多余的一层）

2026-09-23 的教训：我们做了一个"放宽超时 + 重试"的方案，可它在生产上**一次都没成功**
（48 小时 2492 次后台刷新全失败）。原因不是方案写错了，而是**改的地方不是真正等待的那一段**。
所以这一条不能只靠读代码断言，要让它真的跑一遍：

    起一个本地假 DMS（登录页故意慢 N 秒才回），用**真的** `MrerpDmsAdapter` 去登：

    · 页面慢 12 秒 + 预算 30 秒 → **登得上**（证明预算真的传到了 Playwright 的等待上）
    · 同样慢 12 秒 + 预算 5 秒  → 报"连不上"（`DmsUnreachableError`，可重试、计熔断）
    · 对方明确拒绝（`al::…`）    → 报"被拒"（`DmsAuthError`，不可重试、不计熔断）
    · 点了没回应（按钮不绑事件） → 报"连不上"（**老口径在这里错报成"账号/密码问题"**）

假 DMS 只实现登录那几步：`index.php`（慢）→ 点登录 → `checklogin.php` 返回 `lct::1::1`
→ 跳去 `home.php`（真实 DMS 登录成功也是离开 `index.php`）。

用法（要真浏览器，所以在容器里跑；宿主没有 playwright）：
    scripts/sample-verify-dms-login-budget.sh dms
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 登录页两种形态：正常绑点击（真 DMS 的 index.js 把处理绑在 window.load 上 —— 老产品
# 2026-05-31 的线上 bug 就是"绑晚了，点击是静默无操作"），以及**不绑**（模拟"点了没回应"）。
# 页面正文必须是纯 ASCII（Python 的 bytes 字面量规则），所以 JS 注释写英文。
_PAGE_HEAD = b"""<!doctype html><html><head><meta charset="utf-8"><title>fake dms</title></head>
<body>
<input id="txtusers"><input id="txtpasswords" type="password"><button id="btnlogin">login</button>
"""
_PAGE_BOUND = _PAGE_HEAD + b"""<script>
window.addEventListener('load', function () {
  document.getElementById('btnlogin').addEventListener('click', function () {
    fetch('/dms/checklogin.php', {method: 'POST', body: 'u=1'})
      .then(function (r) { return r.text(); })
      .then(function (t) {
        if (t.indexOf('lct') === 0) { window.location.href = '/dms/home.php'; }
      });
  });
});
</script></body></html>"""
# 按钮在，但**没有**点击处理：点了什么都不会发生（对方根本没答）。
_PAGE_UNBOUND = _PAGE_HEAD + b"</body></html>"


class FakeDms(BaseHTTPRequestHandler):
    slow_seconds = 0.0
    reject = False
    bind_late = False

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/dms/home.php"):
            self._send(b"ok")
        elif self.path.startswith("/dms/index.php"):
            time.sleep(FakeDms.slow_seconds)
            page = _PAGE_UNBOUND if FakeDms.bind_late else _PAGE_BOUND
            self._send(page, ctype="text/html; charset=utf-8")
        else:
            self._send(b"", status=404)

    def do_POST(self):  # noqa: N802
        if self.path.startswith("/dms/checklogin.php"):
            self._send(b"al::wrong password" if FakeDms.reject else b"lct::1::1")
        else:
            self._send(b"", status=404)

    def _send(self, body, status=200, ctype="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    from pearnly_dms.adapter.session import (
        DmsAuthError,
        DmsUnreachableError,
        MrerpDmsAdapter,
    )
    from pearnly_dms.config import pacing

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeDms)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/dms/index.php" % server.server_address[1]
    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def attempt(budget_ms, slow, reject=False, bind_late=False):
        """真登一次。返回 ('ok'|'unreachable'|'auth', 异常类名)。"""
        FakeDms.slow_seconds, FakeDms.reject, FakeDms.bind_late = slow, reject, bind_late
        adapter = MrerpDmsAdapter(system_url=url, username="probe", password="probe",
                                  headless=True)
        adapter.page_timeout_ms = budget_ms
        try:
            with adapter:
                adapter.login()
            return "ok", ""
        except DmsUnreachableError as exc:
            return "unreachable", type(exc).__name__
        except DmsAuthError as exc:
            return "auth", type(exc).__name__

    check("第一档预算是 30 秒（够跑完实测最慢的握手）", pacing.budget_ms(1) == 30_000,
          pacing.budget_ms(1))

    # A：慢页 + 宽预算 → 登得上。**这条就是"方案真的生效"的证据**
    started = time.time()
    status, detail = attempt(pacing.budget_ms(1), slow=12)
    check("登录页慢 12 秒 + 30 秒预算 → 登得上（预算真的传到了等待上）",
          status == "ok", (status, detail, "%.1fs" % (time.time() - started)))

    # B：同样慢，但预算不够 → 必须是"连不上"（可重试），不是"账号错"
    status, detail = attempt(5_000, slow=12)
    check("预算不够时 → 报「连不上」（可重试），而不是报账号/密码问题",
          status == "unreachable", (status, detail))

    # C：对方明确拒绝 → 仍然是"被拒"（不可重试），别把凭据问题说成对方挂了
    status, detail = attempt(pacing.budget_ms(1), 0.2, reject=True)
    check("对方明确拒绝 → 报「被拒」（不可重试），不误判成连不上",
          status == "auth", (status, detail))

    # D：点了没回应（处理函数没绑上）→ 也是"连不上"。老口径在这里报 DmsAuthError，
    #    用户看到的是"账号/密码有问题"—— 那正是让 2026-09-23 排查绕远路的错判。
    status, detail = attempt(6_000, 0.2, bind_late=True)
    check("点了没回应（对方没答）→ 报「连不上」（老口径会错报成账号问题）",
          status == "unreachable", (status, detail))

    server.shutdown()
    failed = [row for row in results if not row[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
