"""在站点的网页容器里给一个账号建一次 Desk 会话，打印 sid（冒烟用；不碰 Keycloak 密码）。

    env/bin/python /tmp/smoke-session.py <站点> <账号>             # 打印 SID <sid>
    env/bin/python /tmp/smoke-session.py <站点> <账号> company <公司>   # 先把这个账号的当前公司切到 <公司>（ERP 大账套跑时限），
                                                                     # 另打印 PREV <原公司>，收尾时还原
    env/bin/python /tmp/smoke-session.py <站点> <账号> drop <sid> [<原公司>]  # 冒烟收尾：删掉这次建的会话，还原当前公司

只给测试租户用。

建会话时先清掉这个账号的产品列表记忆（`pearnly_core/public/desk/list.js` 存进个人设置、按 `pearnly:<路由>:<公司>` 分的那几份）：
每次测试都当一个新的人第一次打开列表，不带上一格留下的搜索、筛选、排序（11-24：全套流程里 erp.inventory.by-product 桌面那格
点开物料加的「物料 = …」被存下，手机那格进页面取回，叠上新搜索成空表；那件物料已被收尾删掉）。只清 `pearnly:` 开头的，原生列表设置不动。
"""
import base64
import sys

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]  # run.py 传参用 base64

site, user = sys.argv[1], sys.argv[2]


def forget_lists(user):
    """去掉这个账号个人设置里 `pearnly:` 开头的列表记忆：库里（`__UserSettings`）和缓存（原生先写缓存、浏览器再叫同步进库）都清。"""
    import json

    # 先把这个账号缓存里的设置同步进库（同原生 `sync_user_settings` 的写法，只取本账号的键），再从库里改，免得缓存那份之后又被同步回来
    for key, data in (frappe.cache.hgetall("_user_settings") or {}).items():
        key = key.decode() if isinstance(key, bytes) else key
        doctype, _, owner = key.rpartition("::")
        if owner != user or not doctype:
            continue
        frappe.db.sql("insert into `__UserSettings` (`user`, `doctype`, `data`) values (%s, %s, %s) "
                      "on duplicate key update `data`=%s", (user, doctype, data, data))
    for doctype, data in frappe.db.sql("select doctype, data from `__UserSettings` where user=%s", user):
        try:
            settings = json.loads(data or "{}")
        except ValueError:
            continue
        if not isinstance(settings, dict) or not any(str(key).startswith("pearnly:") for key in settings):
            continue
        kept = {key: value for key, value in settings.items() if not str(key).startswith("pearnly:")}
        frappe.db.sql("update `__UserSettings` set data=%s where user=%s and doctype=%s", (json.dumps(kept), user, doctype))
        frappe.cache.hdel("_user_settings", f"{doctype}::{user}")

drop = sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "drop" else ""
restore = sys.argv[5] if drop and len(sys.argv) > 5 else None
company = sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "company" else ""
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
try:
    if drop:
        from frappe.sessions import delete_session

        delete_session(drop, user=user, reason="smoke finished")
        if restore is not None:
            frappe.defaults.set_user_default("company", restore if restore != "-" else None, user)
        frappe.db.commit()
        print("DROPPED")
    else:
        if not frappe.db.exists("User", user) or not frappe.db.get_value("User", user, "enabled"):
            raise SystemExit(f"user not found or disabled: {user}")
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request

        forget_lists(user)
        if company:
            # 当前公司就是用户默认值里的 company（ERP 切公司的官方做法），冒烟收尾还原
            print("PREV", frappe.defaults.get_user_default("company", user) or "-")
            frappe.defaults.set_user_default("company", company, user)
        frappe.local.request_ip = "127.0.0.1"
        frappe.local.request = Request(EnvironBuilder(path="/", headers={"Host": site}).get_environ())
        frappe.local.cookie_manager = None
        frappe.local.session = frappe._dict()
        from frappe.sessions import Session

        session = Session(user, resume=False, full_name=user, user_type="System User")
        frappe.db.commit()
        print("SID", session.sid)
finally:
    frappe.destroy()
