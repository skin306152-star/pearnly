# -*- coding: utf-8 -*-
"""权威只读到底登几次 —— 对**真** MR.ERP DMS 验证（只读，不写任何东西）。

## 为什么要有这一条

2026-09-23 的教训：我们做过一个"放宽超时 + 重试"的方案，可它在生产上**一次都没成功**，
因为改的地方不是真正等待的那一段。所以"少登一次"这种事不能靠读代码断言，要让它真的跑。

这里拿真系统跑三个场景（都走 `run_logged_in(..., authoritative_read=True)`，也就是
识别查重 / 客户全字段 / 地址级联 / 编辑页快照 / 主档刷新真正用的那个入口）：

| 场景 | 配置 | 应该发生什么 |
|---|---|---|
| A | 销售号 + **独立**管理员号 | 只登录 **1** 次，而且是**管理员**那次；读取非空 |
| B | 只有销售号（没配管理员） | 照旧登录 1 次（销售那次）；读取非空 —— 单凭据路径没被改坏 |
| C | 销售号 + **登录不上**的管理员号 | `ERR_DMS_ADMIN_AUTH` 明确失败关闭，**读取函数一次都没跑** |
| D | 同 A，但跑**生产那个真实入口**（身份证查重 `recognize_lookup`） | 同样只登 1 次；且整条读链都过得了管理员只读闸 |

场景 C 用**不存在的管理员账号**而不是"把真账号密码写错"：DMS 对两种情况的回答同样
是 `al::"รหัสผู้ใช้" หรือ "รหัสผ่าน" ไม่ถูกต้อง`，效果一样，但不会给真测试账号攒失败次数。

只读：查府级联 + 称谓主档。不建客户、不建单、不改任何数据；每次会话退出即登出。

用法（要真浏览器 + 真外网，所以在**有外网的 bench 容器**里跑）：
    scripts/sample-verify-dms-authoritative-read-real.sh <容器> <站点>
凭据从 `legacy-endpoints.json` 读（容器里挂在 `/etc/pearnly/credentials/`），不打印密码。
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

SYSTEM_URL = "https://www.mrerp4sme.com/dms/index.php"
CRED_FILES = (
    os.environ.get("PEARNLY_DMS_CREDS") or "",
    "/etc/pearnly/credentials/legacy-endpoints.json",
    str(Path.home() / ".config" / "pearnly" / "credentials" / "legacy-endpoints.json"),
)

RESULTS: list = []
LOGIN_CALLS: list = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))


def note(label, detail):
    print("  " + label + ": " + str(detail))


def find_creds(username: str) -> dict:
    """按用户名找回一条 `mrerp_dms` 连接（密码只进内存，绝不打印）。"""
    for raw in CRED_FILES:
        if not raw:
            continue
        path = Path(raw)
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for e in data.get("endpoints") or []:
            if (e.get("adapter") == "mrerp_dms" and e.get("username") == username
                    and e.get("password")):
                return {
                    "username": username,
                    "password": e["password"],
                    "system_url": (e.get("config_plain") or {}).get("system_url") or SYSTEM_URL,
                    "source": str(path),
                }
    raise SystemExit("凭据文件里找不到 adapter=mrerp_dms 且 username=%s 的连接（试过 %s）"
                     % (username, [p for p in CRED_FILES if p]))


def install_login_counter():
    """把"真登了几次、每次多久"记下来 —— 这是本脚本唯一的硬证据来源。

    计在 `_perform_login` 上而不是 `login()`：主会话和管理员会话都走它，所以数得到
    **每一次真实登录**，而不是"调了几次 login()"（后者有幂等短路，会少算）。
    """
    from pearnly_dms.adapter.session import MrerpDmsAdapter

    original = MrerpDmsAdapter._perform_login

    def counting(self, page, username, password):
        t0 = time.time()
        try:
            return original(self, page, username, password)
        finally:
            LOGIN_CALLS.append({"username": username, "ms": int((time.time() - t0) * 1000)})

    MrerpDmsAdapter._perform_login = counting


@contextmanager
def patched_adapter(sales: dict, admin: dict | None):
    """把"端点文档 → 适配器"这一段换成直接构造的真适配器。

    脚本在容器里跑，站点上不一定装了这个 App，所以只替换这一段 ——
    被测的 `run_logged_in` / `authoritative_read_session` / 登录跳过判断全是真代码。
    """
    from pearnly_dms.adapter.session import MrerpDmsAdapter
    from pearnly_dms.config import pacing
    import pearnly_dms.intake.service as service

    def fake_build_adapter(_name):
        adapter = MrerpDmsAdapter(
            system_url=sales["system_url"],
            username=sales["username"],
            password=sales["password"],
            admin_username=(admin or {}).get("username"),
            admin_password=(admin or {}).get("password"),
            headless=True,
        )
        adapter.page_timeout_ms = pacing.budget_ms(1)
        return adapter, None

    original = service.build_adapter
    service.build_adapter = fake_build_adapter
    try:
        yield service
    finally:
        service.build_adapter = original


def read_provinces_and_prefixes(client):
    """真页面解析：府级联（`cus/form.php` 的 selprovinces）+ 称谓主档（双来源 merge）。"""
    provinces = client.list_geo("provinces", "") or []
    prefixes = client.list_prefixes() or []
    return {
        "ok": True,
        "provinces": len(provinces),
        "prefixes": len(prefixes),
        "province_sample": [row[1] for row in provinces[:3]],
        "prefix_sample": [row[1] for row in prefixes[:3]],
    }


def run_read_block(endpoint_name: str, sales: dict, admin: dict | None):
    """跑一次真 DMS 的权威只读。回 `(out, logins, read_calls, elapsed_ms)`。"""
    read_calls = {"n": 0}

    def do_read(client, _adapter):
        read_calls["n"] += 1
        return read_provinces_and_prefixes(client)

    LOGIN_CALLS.clear()
    t0 = time.time()
    with patched_adapter(sales, admin) as service:
        out = service.run_logged_in({"name": endpoint_name}, do_read, authoritative_read=True)
    return out, list(LOGIN_CALLS), read_calls["n"], int((time.time() - t0) * 1000)


def run_recognize_lookup(sales: dict, admin: dict | None):
    """跑**生产上每次录入都会走**的那个入口（身份证查重），不是合成读。

    它顺带验一件只有真系统才知道的事：这一整条读链（查客户 → 读客户 → 表单回显 →
    地址级联 → 称谓）**每一跳都要能过管理员只读闸**；有哪一跳不在闸的允许表里，
    这里会明说 `refused a non read-only request`（离线假 DMS 不会暴露这种问题）。
    """
    LOGIN_CALLS.clear()
    t0 = time.time()
    with patched_adapter(sales, admin) as service:
        out = service.recognize_lookup(
            {"name": "pearnly-verify-authread-d"},
            people_id="9999999999999",
            name="เพิร์นลี่ เธสท์",
            ocr_address={},
            phone="",
        )
    return out, list(LOGIN_CALLS), int((time.time() - t0) * 1000)


def main():
    install_login_counter()

    sales_user = os.environ.get("PEARNLY_DMS_SALES_USER") or "sale02"
    admin_user = os.environ.get("PEARNLY_DMS_ADMIN_USER") or "dmstest"
    sales = find_creds(sales_user)
    admin = find_creds(admin_user)
    note("销售账号", sales["username"])
    note("管理员账号", admin["username"])
    check("销售号与管理员号是两个不同账号（权威只读的前提）",
          sales["username"] != admin["username"],
          "%s / %s" % (sales["username"], admin["username"]))

    # ── A：销售号 + 独立管理员 → 只登管理员那一次 ──────────────────────────
    print("")
    print("[场景 A] 权威只读 + 独立管理员凭据")
    out_a, logins_a, reads_a, ms_a = run_read_block("pearnly-verify-authread-a", sales, admin)
    note("登录次数", "%d 次 %s" % (len(logins_a), [(x["username"], "%dms" % x["ms"]) for x in logins_a]))
    note("读取调用", "%d 次，整块耗时 %dms" % (reads_a, ms_a))
    note("读取结果", out_a)
    check("A 只真登录 1 次（跳过了销售会话）", len(logins_a) == 1, len(logins_a))
    check("A 登的是管理员账号", bool(logins_a) and logins_a[0]["username"] == admin["username"],
          logins_a[0]["username"] if logins_a else "(没登)")
    check("A 读取成功且真拿到数据",
          out_a.get("ok") is True and out_a.get("provinces", 0) > 0 and out_a.get("prefixes", 0) > 0,
          {"provinces": out_a.get("provinces"), "prefixes": out_a.get("prefixes")})

    # ── B：单凭据（没配管理员）→ 照旧登销售那一次 ─────────────────────────
    print("")
    print("[场景 B] 权威只读 + 只有销售凭据（没配管理员）")
    out_b, logins_b, reads_b, ms_b = run_read_block("pearnly-verify-authread-b", sales, None)
    note("登录次数", "%d 次 %s" % (len(logins_b), [(x["username"], "%dms" % x["ms"]) for x in logins_b]))
    note("读取调用", "%d 次，整块耗时 %dms" % (reads_b, ms_b))
    check("B 登录 1 次（销售会话，单凭据路径没被改坏）", len(logins_b) == 1, len(logins_b))
    check("B 登的是销售账号", bool(logins_b) and logins_b[0]["username"] == sales["username"],
          logins_b[0]["username"] if logins_b else "(没登)")
    check("B 读取成功且真拿到数据",
          out_b.get("ok") is True and out_b.get("provinces", 0) > 0 and out_b.get("prefixes", 0) > 0,
          {"provinces": out_b.get("provinces"), "prefixes": out_b.get("prefixes")})

    # ── C：管理员登录不上 → 明确失败关闭，读取函数一次都不许跑 ─────────────
    print("")
    print("[场景 C] 权威只读 + 管理员凭据登不上（不存在的账号）→ 必须失败关闭")
    bad_admin = {"username": "pearnly-no-such-admin", "password": "pearnly-not-a-password",
                 "system_url": sales["system_url"]}
    out_c, logins_c, reads_c, ms_c = run_read_block("pearnly-verify-authread-c", sales, bad_admin)
    note("登录次数", "%d 次 %s" % (len(logins_c), [(x["username"], "%dms" % x["ms"]) for x in logins_c]))
    note("读取调用", "%d 次（必须是 0：没管理员会话就不许读）" % reads_c)
    note("返回", {"ok": out_c.get("ok"), "error_code": out_c.get("error_code")})
    check("C 明确失败关闭（ERR_DMS_ADMIN_AUTH）",
          out_c.get("ok") is False and out_c.get("error_code") == "ERR_DMS_ADMIN_AUTH",
          out_c.get("error_code"))
    check("C 读取函数一次都没跑（没在'没登录的主会话'上读出空数据）", reads_c == 0, reads_c)
    check("C 没有错登销售会话", all(x["username"] != sales["username"] for x in logins_c),
          [x["username"] for x in logins_c])

    # ── D：生产那个真实入口（身份证查重）也走同一档 ─────────────────────────
    print("")
    print("[场景 D] 生产入口「身份证查重」对真 DMS（不是合成读）")
    out_d, logins_d, ms_d = run_recognize_lookup(sales, admin)
    note("登录次数", "%d 次 %s" % (len(logins_d), [(x["username"], "%dms" % x["ms"]) for x in logins_d]))
    note("整块耗时", "%dms" % ms_d)
    note("返回", {"ok": out_d.get("ok"), "scenario": out_d.get("scenario"),
                  "error_code": out_d.get("error_code")})
    check("D 只真登录 1 次（管理员）", len(logins_d) == 1
          and logins_d[0]["username"] == admin["username"],
          [x["username"] for x in logins_d])
    check("D 整条读链没有被只读闸拦下",
          out_d.get("ok") is True and "refused" not in str(out_d.get("raw_error") or ""),
          str(out_d.get("raw_error") or "")[:160])
    check("D 查重跑通（未知证号 → 判为新客，不把整个系统误报成查不到）",
          out_d.get("scenario") in ("none", "similar"), out_d.get("scenario"))

    failed = [x for x in RESULTS if not x[1]]
    print("")
    print("整块耗时: A=%dms B=%dms C=%dms D=%dms" % (ms_a, ms_b, ms_c, ms_d))
    print("合计: 通过 %d / 失败 %d" % (len(RESULTS) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    import sys

    import frappe

    site = sys.argv[1] if len(sys.argv) > 1 else "qerp.localhost"
    sites_path = os.environ.get("PEARNLY_SITES_PATH") or "/home/frappe/frappe-bench/sites"
    frappe.init(site=site, sites_path=sites_path)
    frappe.connect()
    try:
        main()
    finally:
        frappe.destroy()
