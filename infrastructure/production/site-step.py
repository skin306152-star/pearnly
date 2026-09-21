#!/usr/bin/env python3
"""Private one-shot maintenance container; secrets arrive on stdin, not argv."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sys


def main():
    job = json.load(sys.stdin)
    action, p = job["action"], job["payload"]
    site, group = p["site"], p["group"]
    runtime = dict(line.split("=", 1) for line in Path("/run/secrets/runtime.env").read_text().splitlines() if "=" in line)
    os.setgid(1000)
    os.setuid(1000)
    os.chdir("/home/frappe/frappe-bench/sites")
    common = Path("common_site_config.json")
    values = json.loads(common.read_text()) if common.exists() else {}
    queue_db = {"dms": 1, "erp": 2, "cowork": 3}[group]
    values.update(db_host="mariadb", db_port=3306, redis_cache="redis://redis-cache:6379",
                  redis_queue=f"redis://redis-queue:6379/{queue_db}", redis_socketio=f"redis://redis-queue:6379/{queue_db}",
                  socketio_port=9000, serve_default_site=False,
                  pearnly_ocr_budget_redis="redis://redis-queue:6379/6",
                  workers={**values.get("workers", {}), "ocr": {"timeout": 600}})
    common.write_text(json.dumps(values))
    Path("apps.txt").write_text("\n".join(["frappe", "erpnext", "pearnly_core", "pearnly_dms",
                                        "pearnly_cowork", "pearnly_erp_portal"]) + "\n")
    import frappe
    from frappe.installer import _new_site, install_app, update_site_config
    owner = {key: p[key] for key in ("site", "group", "tenant", "deployment")}
    Path(".pearnly-owners").mkdir(exist_ok=True, mode=0o700)
    marker = Path(".pearnly-owners", site + ".json")
    if Path(site).exists():
        if not marker.exists() or json.loads(marker.read_text()) != owner:
            raise ValueError("Existing Site is not owned by this deployment")
    if action == "drop_site":
        path = Path(site)
        if path.exists():
            # Validated single-site path, exact native database identity, no backup.
            frappe.init(site=site)
            frappe.flags.root_login = "root"
            frappe.flags.root_password = runtime["DB_ROOT_PASSWORD"]
            from frappe.database import drop_user_and_database
            drop_user_and_database(frappe.conf.db_name, frappe.conf.db_user)
            frappe.destroy()
            shutil.rmtree(path)
        marker.unlink(missing_ok=True)
        assert not path.exists()
        print("SITE_STEP_OK")
        return
    if action == "new_site" and not Path(site, "site_config.json").exists():
        marker.write_text(json.dumps(owner))
        marker.chmod(0o600)
        frappe.init(site=site, new_site=True)
        _new_site(db_name=None, site=site, db_type="mariadb", db_host="mariadb",
                  db_root_username="root", db_root_password=runtime["DB_ROOT_PASSWORD"],
                  admin_password=secrets.token_urlsafe(40), mariadb_user_host_login_scope="%")
        frappe.destroy()
    frappe.init(site=site)
    frappe.connect()
    frappe.set_user("Administrator")

    def config(**kwargs):
        for key, value in kwargs.items():
            update_site_config(key, value)
            frappe.conf[key] = value
        frappe.clear_cache()

    if action == "new_site":
        # 站点加密密钥**在接流量之前**固定下来，而且只写一次。
        #
        # 为什么必须在这一步做：Frappe 的 `get_encryption_key()` 是按"本进程看到的
        # site_config 里有没有 encryption_key"决定要不要新生成并写文件的
        # （frappe/utils/password.py），而站点配置在请求进程里有 60 秒缓存，写文件后的
        # 缓存清理只作用于本进程。新开户不先固定密钥，就会有两个请求各自生成一次、
        # 后写的覆盖先写的 —— 先写那次加密的凭据随后全部解不开（2026-09-20 正式租户现场：
        # EP-00001 的两条密文在秒级内失效，站点配置文件最后修改时间就在它们之后）。
        # 已有站点只校验、绝不重新生成：换整站密钥会让**现在还能解**的密文一起失效。
        if not frappe.conf.get("encryption_key"):
            from cryptography.fernet import Fernet

            config(encryption_key=Fernet.generate_key().decode())
        config(host_name="https://" + site, developer_mode=0, maintenance_mode=0,
               pause_scheduler=0, enable_scheduler=True)
    elif action == "install_app":
        # 只装该产品自己的 App。这里曾经在安装 pearnly_erp_portal 之前自动插入
        # erpnext，是更早「原生 ERPNext 打底、Pearnly 套壳」方向的遗留：产品合同
        # （`pearnly_erp_portal/product_manifest.py` 的 must_not 第一条）明确禁止默认
        # 安装 ERPNext 全套业务，控制面也从不请求它（provision-agent 只放行
        # pearnly_core 与该产品自己的 App），实测只装 Core + ERP 门户即可安装与迁移。
        # 用户 2026-09-21 明确要求不要它。
        if p["app"] not in frappe.get_installed_apps():
            install_app(p["app"])
    elif action == "set_oidc":
        assert p["issuer"] == "https://auth.pearnly.com/realms/pearnly-business"
        assert p["client_id"] == site and p["native_account"] is True
        assert p["redirect_uri"] == "https://" + site + "/api/method/pearnly_core.identity.callback"
        assert not any(p.get(key) for key in ("admin_client_secret", "password_check_client_secret"))
        config(pearnly_oidc_issuer=p["issuer"], pearnly_oidc_client_id=p["client_id"],
               pearnly_oidc_client_secret=p["client_secret"], pearnly_oidc_redirect_uri=p["redirect_uri"],
               pearnly_product=group, pearnly_native_account=True)
    elif action == "set_billing":
        assert p["api"] == "http://billing-web:8000" and p["host_header"] == "billing.internal"
        config(pearnly_tenant=p["tenant"], pearnly_billing_api=p["api"],
               pearnly_billing_host_header=p["host_header"], pearnly_billing_token=p["token"])
    elif action == "set_ocr":
        assert Path("/run/secrets/ocr.json").is_file()
        config(pearnly_ocr_backend="vertex", pearnly_ocr_credentials="/run/secrets/ocr.json",
               pearnly_ocr_project="pearnly", pearnly_ocr_location="asia-southeast1")
    elif action == "set_line":
        if p.get("clear_credentials"):
            config(pearnly_line_channel_id="", pearnly_line_channel_secret="",
                   pearnly_line_channel_access_token="", pearnly_line_liff_id="")
        else:
            assert p["configured"] and p["channel_secret"] and p["channel_access_token"]
            config(pearnly_line_channel_id=p["channel_id"], pearnly_line_channel_secret=p["channel_secret"],
                   pearnly_line_channel_access_token=p["channel_access_token"],
                   pearnly_line_liff_id=p.get("liff_id", ""), pearnly_line_product=group)
            # 持久菜单：凭据写进去**不等于交付**。用户看到的是 OA 上那条真实菜单，
            # 而 2026-09-20 现场正是"新平台动作已切换、OA 默认菜单还指着旧 pearnly.com
            # 公共路径与旧 Purchase LIFF"。发布是幂等的（按内容散列命名），所以每次
            # 下发渠道配置都对齐一次；没有 LIFF 就不发布 —— 菜单 3/4 本来就依赖它。
            if group == "dms" and p.get("liff_id"):
                from pearnly_dms.menu import rich_menu

                rich_menu.publish()
    elif action == "finalize_site":
        from pearnly_core.provisioning import finalize_site
        finalize_site()
        # site_config.enable_scheduler is not the native scheduler switch.
        # Finalization is an explicit activation step, after apps are installed.
        from frappe.utils.scheduler import enable_scheduler
        enable_scheduler()
    elif action == "bind_user":
        from pearnly_core.provisioning import ensure_local_user
        ensure_local_user(p["email"], p["full_name"], p["subject"], p["deployment"])
    elif action == "set_roles":
        from pearnly_core.provisioning import ensure_feature_roles
        ensure_feature_roles(json.dumps(p), admin_user=p["admin_user"])
    elif action == "set_workspace":
        from pearnly_core.provisioning import ensure_product_workspace
        ensure_product_workspace(json.dumps(p))
    elif action == "verify_site":
        assert frappe.conf.pearnly_tenant == p["tenant"]
        assert frappe.conf.pearnly_product == group
        assert frappe.conf.pearnly_native_account
        assert not frappe.conf.get("pearnly_oidc_admin_client_secret")
        assert frappe.db.get_single_value("System Settings", "disable_user_pass_login")
        from frappe.utils.scheduler import is_scheduler_inactive
        assert not is_scheduler_inactive(verbose=False)
        assert frappe.db.count("Pearnly Identity", {"status": "active"}) > 0
        from pearnly_core.billing_client import wallet
        wallet()
        # "14/14 全 done"只证明步骤跑完，不证明产品能力落地。下面这几项都是**新建时
        # 就该存在**的东西：密钥固定、账户个人资料布局、以及租户 OA 上真实生效的菜单。
        assert frappe.conf.get("encryption_key"), "The site encryption key must be fixed before traffic"
        for fieldname in ("pearnly_company", "pearnly_domain", "pearnly_change_password"):
            assert frappe.db.exists("Custom Field", {"dt": "User", "fieldname": fieldname}), \
                "Account profile fields must be created when the site is opened"
        assert frappe.db.exists("Property Setter", {"doc_type": "User", "field_name": "first_name",
                                                    "property": "label", "value": "Username"}), \
            "The account name field must be labelled Username"
        if group == "dms" and frappe.conf.get("pearnly_line_liff_id"):
            from pearnly_dms.menu import rich_menu

            state = rich_menu.published_state()
            assert state["basic"] and state["query"], "This tenant's DMS rich menus are not published"
            assert state["default"] == state["basic"], "The OA default rich menu is not this tenant's menu"
    else:
        raise ValueError("Unsupported Site action")
    frappe.db.commit()
    frappe.destroy()
    print("SITE_STEP_OK")


if __name__ == "__main__":
    main()
