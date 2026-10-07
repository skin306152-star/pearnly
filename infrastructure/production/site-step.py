#!/usr/bin/env python3
"""Private one-shot maintenance container; secrets arrive on stdin, not argv."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import runpy
import urllib.error
import urllib.parse
import urllib.request


LINE_WEBHOOK_API = "https://api.line.me/v2/bot/channel/webhook/"
# 开户单上要让人看得懂的失败原因：执行器只把这一行带回控制面，其余输出留在服务器受限日志。
STEP_HINT = "PEARNLY_STEP_HINT "
# DMS 改单编辑器直接打开 LIFF 落地地址，必须一字不差；ERP / Cowork 只要在本站下面（填站点根）。
LIFF_PATHS = {"dms": "/liff/dms-booking"}


class StepHint(Exception):
    """要人去 LINE 后台改的配置。消息是写给开户的人看的，不含任何凭据。"""
# ERP / Cowork 的按权限富菜单（DMS 走自己的 `rich_menu`）。发布后站点配置里才有菜单表，
# 没发布过绑定的人就只看到空白官方号（mrerp / erpcowork 开户后都没有，2026-09-30）。
LINE_MENUS = {"erp": "pearnly_erp_portal.integrations.line_menu", "cowork": "pearnly_cowork.line.menu"}
COWORK_OCR = {"pearnly_cowork_ocr_environment": {"OCR_LLM_BACKEND": "vertex"},
              "pearnly_cowork_ocr_policy": {"mode": "economy"}}


def line_login(p, group, runtime):
    """在该产品的账号池（ERP / DMS 业务 realm，Cowork 自己的 realm）建/更新 LIFF 所属 LINE Login 频道的
    共用隐藏入口，返回别名；不开就返回空串。"""
    from pearnly_core.account.line_login_contract import idp_alias, login_channel_of

    channel, secret = login_channel_of(p.get("liff_id")), p.get("login_channel_secret") or ""
    if not secret or not channel:
        return ""
    alias = idp_alias(channel)
    keycloak = runpy.run_path("/opt/pearnly/deploy-src/scripts/production/configure-line-login.py")
    api = keycloak["Keycloak"]("http://keycloak:8080", keycloak["REALMS"][group], "pearnly-bootstrap",
                               runtime["KEYCLOAK_ADMIN_PASSWORD"])
    # 账号池级设置（拒绝未关联的首次登录、登录来源作用域）幂等，每次都对齐：以前靠运维手跑脚本，
    # Cowork 拆出自己的账号池后从没跑过，LINE 登录在那边根本不成立（2026-09-30 回读）。
    keycloak["configure_realm"](api)
    keycloak["configure_site"](api, alias=alias, login_channel_id=channel, login_channel_secret=secret)
    return alias


def webhook_url(site):
    return "https://" + site + "/api/method/pearnly_core.line.webhook"


def line_webhook(token, method, path="endpoint", body=None):
    request = urllib.request.Request(
        LINE_WEBHOOK_API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token.strip(), "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read() or b"{}")


def set_webhook(site, url, token):
    """把租户 OA 的 Webhook 地址指到本站点。

    凭据写进站点不等于 LINE 会把消息投过来：地址以前全靠人去 LINE 后台手填，
    erpcowork 漏填后发绑定码毫无反应（2026-09-30）。这一步在上线之前跑，只设地址不发测试。
    """
    assert url == webhook_url(site), "LINE webhook URL must be this tenant's own endpoint"
    line_webhook(token, "PUT", body={"endpoint": url})
    assert line_webhook(token, "GET").get("endpoint") == url, "LINE did not keep the webhook URL"


def check_webhook(site, token):
    """上线后回读：地址是本站、"Use webhook" 已打开、LINE 的测试推送能拿到 200。

    "Use webhook" 开关 LINE 没有开放接口，只能在 LINE Developers → Messaging API 手动打开。
    """
    url = webhook_url(site)
    info = line_webhook(token, "GET")
    assert info.get("endpoint") == url, "LINE webhook URL does not point to this tenant"
    if not info.get("active"):
        raise StepHint("LINE 后台没开 Use webhook：LINE Developers → 该 OA 的 Messaging API 频道 → "
                       "Messaging API 页打开 Use webhook，然后重试这一步")
    result = line_webhook(token, "POST", "test", {"endpoint": url})
    assert result.get("success"), "LINE webhook test failed: %s %s" % (result.get("statusCode"), result.get("reason"))


def stateless_token(channel, secret):
    """用频道号 + 密钥向 LINE 换一张 15 分钟的短时令牌（Messaging API 与 LINE Login 频道都适用）。"""
    request = urllib.request.Request("https://api.line.me/oauth2/v3/token", method="POST", data=urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": channel, "client_secret": secret}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())["access_token"]


def channel_token(p):
    """开户下发的 Messaging API 令牌：渠道带了长期令牌（10-05 之前开的租户）就用它，否则用频道号 + 密钥现取。"""
    token = (p.get("channel_access_token") or "").strip()
    if token:
        return token
    if not p.get("channel_id"):
        raise StepHint("开户单上没有 LINE 渠道 ID：在 LINE 渠道里补上 Channel ID 后重试这一步")
    try:
        return stateless_token(p["channel_id"], p["channel_secret"])
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401):
            raise StepHint("LINE 不认这组 Channel ID / Channel secret：到 LINE Developers → 该 OA 的频道 → "
                           "Basic settings 重新复制两个值，改好后重试这一步") from None
        raise


def check_liff(site, group, liff_id, secret):
    """LIFF 落地地址必须指向本站。LIFF 由控制面开户时建好（`pearnly_control.onboarding.line_liff`），
    这里在站点上再读回核对一次（2026-10-04 手建时查到 testcowork 少了 `.com`、testerp 填成了 DMS 的地址）。
    用 LINE Login 频道的临时令牌只读。"""
    from pearnly_core.account.line_login_contract import login_channel_of

    channel = login_channel_of(liff_id)
    if not liff_id or not channel or not secret:
        return
    token = stateless_token(channel, secret)
    request = urllib.request.Request("https://api.line.me/liff/v1/apps", headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=20) as response:
        apps = json.loads(response.read() or b"{}").get("apps") or []
    app = next((a for a in apps if a.get("liffId") == liff_id), None)
    if not app:
        raise StepHint(f"LIFF {liff_id} 不在 LINE Login 频道 {channel} 下：开户单上的 LIFF ID 填错了")
    url = str((app.get("view") or {}).get("url") or "")
    root = "https://" + site + "/"
    if group in LIFF_PATHS:
        expected, ok = "https://" + site + LIFF_PATHS[group], url == "https://" + site + LIFF_PATHS[group]
    else:
        expected, ok = root, (url.rstrip("/") + "/").startswith(root)
    if not ok:
        raise StepHint(f"LIFF {liff_id} 的 Endpoint URL 是 {url or '空'}，应为 {expected}：LINE Developers → "
                       f"LINE Login 频道 {channel} → LIFF 里改好后重试这一步")


# 共用 OCR 限额：各产品必须一字不差，否则 budget.permit 报 policy_mismatch 全部延后。
# 2026-09-27 用户定每分钟 120 次。压测后要调就在 runtime.env 写 PEARNLY_OCR_BUDGET（JSON），
# 三个产品读同一份 runtime.env，下发的值自然一致。
OCR_BUDGET = {"concurrency": 8, "tenant_concurrency": 3, "rpm": 120}
OCR_BUDGET_KEYS = ("concurrency", "tenant_concurrency", "rpm", "pending", "tenant_pending", "max_age")


def ocr_budget(runtime):
    raw = (runtime.get("PEARNLY_OCR_BUDGET") or "").strip()
    if not raw:
        return dict(OCR_BUDGET)
    value = json.loads(raw)
    assert isinstance(value, dict) and value, "PEARNLY_OCR_BUDGET must be a JSON object"
    assert set(value) <= set(OCR_BUDGET_KEYS), "PEARNLY_OCR_BUDGET has unknown keys: %s" % sorted(set(value) - set(OCR_BUDGET_KEYS))
    assert all(type(v) is int and v >= 1 for v in value.values()), "PEARNLY_OCR_BUDGET values must be positive integers"
    budget = {**OCR_BUDGET, **value}
    assert budget["tenant_concurrency"] <= budget["concurrency"], "PEARNLY_OCR_BUDGET tenant_concurrency exceeds concurrency"
    return budget


def endpoints(runtime):
    """数据库与 Redis 的地址（runtime.env 可改；默认是同一台机器 compose 里的服务名）。扩容时把数据库或 Redis 挪到别的机器，
    只改 runtime.env 的 PEARNLY_DB_HOST / PEARNLY_DB_PORT / PEARNLY_REDIS_CACHE_URL / PEARNLY_REDIS_QUEUE_URL，不改代码；
    开户与计费初始化不再把它们写回服务名（2026-10-02 可拆分自检）。"""
    return {"db_host": (runtime.get("PEARNLY_DB_HOST") or "mariadb").strip(),
            "db_port": int((runtime.get("PEARNLY_DB_PORT") or "3306").strip()),
            "cache": (runtime.get("PEARNLY_REDIS_CACHE_URL") or "redis://redis-cache:6379").strip().rstrip("/"),
            "queue": (runtime.get("PEARNLY_REDIS_QUEUE_URL") or "redis://redis-queue:6379").strip().rstrip("/")}


def document_ai_regions(runtime):
    """Document AI 区域顺序与各区处理器（pearnly_document_ai_regions）：runtime.env 有值才下发，没值用代码默认。"""
    raw = (runtime.get("PEARNLY_DOCUMENT_AI_REGIONS") or "").strip()
    if not raw:
        return None
    value = json.loads(raw)
    assert isinstance(value, list) and value, "PEARNLY_DOCUMENT_AI_REGIONS must be a non-empty JSON list"
    for row in value:
        assert isinstance(row, dict) and all(isinstance(row.get(k), str) and row[k] for k in ("location", "processor", "version")), \
            "Each Document AI region needs location, processor and version"
        assert type(row.get("rpm")) is int and row["rpm"] >= 1, "Each Document AI region needs a positive rpm"
    assert len({row["location"] for row in value}) == len(value), "Document AI regions must not repeat"
    return value


GCS_API = "https://storage.googleapis.com/storage/v1/b/"


def purge_site_objects(bucket, site, token, *, opener=None):
    """删租户时把它在 Cowork 原件桶里的东西全删掉（`<站点>/originals/…`、`<站点>/previews/…`，含所有版本）。

    隐私政策承诺"停用或要求删除后 30 天内删除"（2026-10-05）：以前删租户只删站点和数据库，桶里的原件一直留着。
    只认 `<站点>/` 这一个前缀：站点名为空、带斜杠或不像域名一律拒绝，绝不会退化成"删整个桶"。
    已经不在的对象（404）算删过，所以中途失败可以整步重跑。返回删掉的对象数。
    """
    if not bucket:
        return 0
    if not site or site != site.strip() or "/" in site or "." not in site:
        raise ValueError("Refusing to purge objects for an unsafe site prefix")
    opener = opener or urllib.request.urlopen
    prefix, base = site + "/", GCS_API + urllib.parse.quote(bucket, safe="") + "/o"
    headers = {"Authorization": "Bearer " + token}
    deleted, page = 0, ""
    while True:
        query = {"prefix": prefix, "versions": "true", "fields": "items(name,generation),nextPageToken"}
        if page:
            query["pageToken"] = page
        with opener(urllib.request.Request(base + "?" + urllib.parse.urlencode(query), headers=headers), timeout=30) as response:
            listing = json.load(response)
        targets = []
        for item in listing.get("items", []):
            name = item["name"]
            if not name.startswith(prefix):
                raise ValueError("Storage listing returned an object outside the site prefix")
            targets.append(f"{base}/{urllib.parse.quote(name, safe='')}?generation={int(item['generation'])}")

        def delete(target):
            try:
                opener(urllib.request.Request(target, method="DELETE", headers=headers), timeout=30).close()
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise

        # 一页最多 1000 个对象，逐个删一个大租户要几十分钟（会撞上执行器单步时限），同时删 16 个。
        with ThreadPoolExecutor(max_workers=16) as pool:
            for _done in pool.map(delete, targets):
                deleted += 1
        page = listing.get("nextPageToken") or ""
        if not page:
            return deleted


def storage_token(credentials_info):
    """识别服务账号（对原件桶有对象读写）换一个短期访问令牌。"""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    credentials.refresh(Request())
    return credentials.token


def main():
    job = json.load(sys.stdin)
    action, p = job["action"], job["payload"]
    site, group = p["site"], p["group"]
    runtime = dict(line.split("=", 1) for line in Path("/run/secrets/runtime.env").read_text().splitlines() if "=" in line)
    # 删租户要清原件桶：密钥在降权前读进内存（容器里只读挂载，属主不一定是 frappe）。
    storage_key = (json.loads(Path("/run/secrets/ocr.json").read_text())
                   if action == "drop_site" and Path("/run/secrets/ocr.json").is_file() else None)
    os.setgid(1000)
    os.setuid(1000)
    os.chdir("/home/frappe/frappe-bench/sites")
    common = Path("common_site_config.json")
    values = json.loads(common.read_text()) if common.exists() else {}
    queue_db = {"dms": 1, "erp": 2, "cowork": 3}[group]
    hosts = endpoints(runtime)
    values.update(db_host=hosts["db_host"], db_port=hosts["db_port"], redis_cache=hosts["cache"],
                  redis_queue=f"{hosts['queue']}/{queue_db}", redis_socketio=f"{hosts['queue']}/{queue_db}",
                  socketio_port=9000, serve_default_site=False,
                  pearnly_ocr_budget_redis=f"{hosts['queue']}/6",
                  pearnly_ocr_budget=ocr_budget(runtime),
                  workers={**values.get("workers", {}), "ocr": {"timeout": 600}})
    regions = document_ai_regions(runtime)
    if regions:
        values["pearnly_document_ai_regions"] = regions
    if group == "dms":
        configure = runpy.run_path("/opt/pearnly/deploy-src/scripts/production/configure-dms-runtime.py")
        values = configure["configured"](values)
    if group == "cowork":
        # Cowork 发票识别只认显式配置（`pearnly_cowork.ocr.execution.settings`），开户从没写过，
        # 所以每个 Cowork 租户识别都报“请先配置 Cowork 发票识别参数”（testcowork 2026-09-27 真机）。
        # 档位取旧线上的兜底档 economy（`engine_policy._FAILSAFE_MODE`），模型走 Vertex（与 `set_ocr` 同一连接）。
        values.update(COWORK_OCR)
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
            # 先清桶再删库：桶清到一半失败时站点还在，整步可以重跑。
            bucket = str(frappe.conf.get("pearnly_cowork_originals_bucket") or "").strip()
            if bucket:
                if not storage_key:
                    raise RuntimeError("Cannot delete the tenant's original files: storage key is missing")
                print(f"PURGED_OBJECTS {purge_site_objects(bucket, site, storage_token(storage_key))}")
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
        _new_site(db_name=None, site=site, db_type="mariadb", db_host=hosts["db_host"], db_port=hosts["db_port"],
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
        # 每个产品组签到自己的账号池：Cowork 是 pearnly-cowork，ERP / DMS 是业务账号池。
        realm = "pearnly-cowork" if group == "cowork" else "pearnly-business"
        assert p["issuer"] == "https://auth.pearnly.com/realms/" + realm
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
                   pearnly_line_channel_access_token="", pearnly_line_liff_id="",
                   pearnly_line_login_idp="")
        else:
            assert p["configured"] and p["channel_secret"]
            # 先换令牌：频道号 / 密钥不对就停在这里，站点配置不写进半套凭据。
            token = channel_token(p)
            config(pearnly_line_channel_id=p.get("channel_id", ""), pearnly_line_channel_secret=p["channel_secret"],
                   pearnly_line_channel_access_token=(p.get("channel_access_token") or "").strip(),
                   pearnly_line_liff_id=p.get("liff_id", ""), pearnly_line_product=group)
            # 控制面没带地址（09-30 前重排 set_line 的载荷）就按站点名算，和 verify_site 核对的是同一个地址。
            set_webhook(site, p.get("webhook_url") or webhook_url(site), token)
            # 持久菜单：凭据写进去**不等于交付**。用户看到的是 OA 上那条真实菜单，
            # 而 2026-09-20 现场正是"新平台动作已切换、OA 默认菜单还指着旧 pearnly.com
            # 公共路径与旧 Purchase LIFF"。发布是幂等的（按内容散列命名），所以每次
            # 下发渠道配置都对齐一次；没有 LIFF 就不发布 —— 菜单 3/4 本来就依赖它。
            if group == "dms" and p.get("liff_id"):
                from pearnly_dms.menu import rich_menu

                rich_menu.publish()
            if group in LINE_MENUS:
                importlib.import_module(LINE_MENUS[group]).publish()
            check_liff(site, group, p.get("liff_id"), p.get("liff_check_secret") or "")
            # LINE 绑定即登录：渠道带了 LINE Login 频道密钥才开；没带就关（站点侧回落密码登录）。
            config(pearnly_line_login_idp=line_login(p, group, runtime))
    elif action == "finalize_site":
        from pearnly_core.provisioning import finalize_site
        finalize_site()
        # site_config.enable_scheduler is not the native scheduler switch.
        # Finalization is an explicit activation step, after apps are installed.
        from frappe.utils.scheduler import enable_scheduler
        enable_scheduler()
        if group == "cowork":
            # ERP 推送总闸：推送到 MR.ERP / Express、给小助手发配对码都要它。开户从没写过，
            # 新租户点 Express「连接」一律"没有权限"（testcowork 09-27 手工打开，erpcowork 09-30 又踩）。
            config(pearnly_cowork_erp_push_enabled=1)
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
        if group == "dms":
            assert frappe.conf.get("workers", {}).get("dms_sync", {}).get("timeout") == 420
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
        if group == "cowork":
            assert frappe.conf.get("pearnly_cowork_erp_push_enabled") in (True, 1), "Cowork ERP push is not enabled"
        if group == "erp":
            assert frappe.db.exists("ERP Company", {"is_default": 1, "is_active": 1}), \
                "The ERP tenant has no default company"
        from pearnly_core import line

        if group in LINE_MENUS and line.can_send():
            assert importlib.import_module(LINE_MENUS[group])._published(), "This tenant's LINE menus are not published"
        if line.can_send():
            check_webhook(site, line.access_token())
    else:
        raise ValueError("Unsupported Site action")
    frappe.db.commit()
    frappe.destroy()
    print("SITE_STEP_OK")


if __name__ == "__main__":
    try:
        main()
    except StepHint as hint:
        print(STEP_HINT + str(hint).replace("\n", " "))
        sys.exit(1)
