"""LINE 菜单发布、绑定码与团队成员的服务端实测（回归实验室，不出网）。
实验室站点没有 LINE 频道：这里把 `pearnly_core.line` 的出站换成记录器（`can_send` 为真，请求只记下来、回假的 200），
所以测得到「发布走的是哪把锁、多长、整次发布是不是同一个连接、发了几次请求」，测不到 LINE 那边真的建出菜单。
- 菜单发布（ERP `integrations.line_menu`、Cowork `line.menu`、DMS `menu.rich_menu` 的 `publish`）：锁 900 秒、全部请求共用一个 Session；
- 绑定码（共用层 `line_binding`，三产品同一套）：给岗位账号发码 → 模拟 LINE 号用码 → 绑上 → 解绑后查不到；
- 团队成员（ERP / Cowork）：管理员新建成员，账号带所选岗位与 Desk User、没有 System Manager。
每条一行 JSON；最后一行 SUMMARY。发布会把记录器回的假菜单 ID 写进实验站点的 site_config（只在实验站点）。

    ../env/bin/python /workspace/scripts/regression/lab_check_line.py <站点> <产品>
"""
import json
import os
import sys
import uuid

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

import frappe  # noqa: E402

RESULTS = []


def case(name, expect, fn):
    detail = ""
    try:
        detail = fn()
        actual = "ok"
    except Exception as error:  # noqa: BLE001 - 每条都要记结果
        frappe.db.rollback()
        detail, actual = f"{type(error).__name__}: {str(error)[:160]}", "error"
    passed = actual == expect and (not isinstance(detail, dict) or detail.get("pass", True))
    RESULTS.append({"case": name, "expect": expect, "actual": actual, "pass": passed, "detail": detail})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, default=str), flush=True)


class Response:
    def __init__(self, url, method):
        self.url, self.method = url, method
        self.status_code = 404 if (method == "GET" and url.endswith("/content")) else 200
        body = {"richmenus": []} if url.endswith("richmenu/list") else (
            {"richMenuId": "richmenu-" + uuid.uuid4().hex[:16]} if url.endswith("/v2/bot/richmenu") else {})
        self.text = json.dumps(body)
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


def main():
    site, product = sys.argv[1], sys.argv[2]
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        from pearnly_core import line, line_binding

        domain = f"{product}.regression"
        admin = f"admin@{domain}"
        calls, locks = [], []
        line.can_send = lambda *args, **kwargs: True
        line.request = lambda method, url, *, headers=None, session=None, **kwargs: (
            calls.append({"method": method, "url": url.split("/v2/bot/")[-1][:40], "session": id(session) if session else None})
            or Response(url, method))

        def publish():
            module = {"erp": "pearnly_erp_portal.integrations.line_menu", "cowork": "pearnly_cowork.line.menu",
                      "dms": "pearnly_dms.menu.rich_menu"}[product]
            menu = frappe.get_module(module)
            store = menu.pearnly_state.store() if hasattr(menu, "pearnly_state") else menu._state_store()
            real_lock = type(store).lock

            def lock(self, name, *args, **kwargs):
                locks.append({"name": name, "timeout": kwargs.get("timeout")})
                return real_lock(self, name, *args, **kwargs)
            type(store).lock = lock
            try:
                frappe.set_user("Administrator")
                menu.publish()
            finally:
                type(store).lock = real_lock
            sessions = {c["session"] for c in calls}
            return {"requests": len(calls), "sessions": len(sessions), "no_session_calls": sum(1 for c in calls if c["session"] is None),
                    "locks": locks, "pass": bool(calls) and len(sessions) == 1 and None not in sessions
                    and any(l["timeout"] == 900 for l in locks)}
        case("LINE 菜单首次发布：锁 900 秒、整次发布同一个连接", "ok", publish)

        employee = {"erp": "purchase", "cowork": "intake", "dms": "operator"}[product] + "@" + domain

        def bind():
            fake = "U" + uuid.uuid4().hex
            code = line_binding.generate_bind_code(employee, 10)
            frappe.db.commit()
            line_binding.consume_bind_code(str(code.get("code")), line_user_id=fake)
            frappe.db.commit()
            bound = line_binding.user_of(fake)
            line_binding.unbind(fake)
            frappe.db.commit()
            after = line_binding.user_of(fake)
            return {"user": employee, "bound_to": bound, "after_unbind": after, "pass": bound == employee and not after}
        case("绑定码：发码 → LINE 号用码 → 绑上 → 解绑后查不到", "ok", bind)

        if product in ("erp", "cowork"):
            if product == "erp":
                from pearnly_erp_portal.team import service as team
                roles = ["ERP Menu purchase", "ERP Menu profile"]
            else:
                from pearnly_cowork.team import service as team
                roles = ["Cowork Intake", "Cowork Profile"]

            def add_member():
                frappe.set_user(admin)
                tag = uuid.uuid4().hex[:6]
                email = f"m{tag}@{domain}"
                kwargs = {"username": f"m{tag}", "email": email, "roles": roles, "password": "lab-only-" + tag + "A1!"}
                if product == "erp":
                    kwargs["companies"] = []
                team.add_member(**kwargs)
                frappe.db.commit()
                held = set(frappe.get_roles(email))
                return {"email": email, "roles": sorted(held),
                        "pass": "Desk User" in held and "System Manager" not in held and set(roles) <= held}
            case("管理员新建成员 → 有岗位与 Desk User、没有 System Manager", "ok", add_member)

            def employee_adds():
                frappe.set_user(employee)
                return team.add_member(username="x" + uuid.uuid4().hex[:6], email=f"x{uuid.uuid4().hex[:6]}@{domain}", roles=roles)
            case("普通岗位新建成员 → 拒", "error", employee_adds)
        print("SUMMARY", json.dumps({"pass": sum(1 for r in RESULTS if r["pass"]), "total": len(RESULTS)}))
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
