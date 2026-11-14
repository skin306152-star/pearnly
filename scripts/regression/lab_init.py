"""回归基线实验室：建三个产品站点（各装 pearnly_core + 本产品 App，功能全开），再给每个岗位建一个测试账号（界面中文、泰国时区），
最后接上实验室计费（`lab_billing.py`）。只在任务自有的回归实验室里跑（PEARNLY_REGRESSION_LAB=1），拒绝陌生的站点卷。重复跑只补缺的。

    env/bin/python /workspace/scripts/regression/lab_init.py [站点…]
"""
import json
import os
import subprocess
import sys
from pathlib import Path

PASSWORD = "regression-lab-only"
SITES = {"erp.regression.localhost": ("erp", "pearnly_erp_portal"),
         "cowork.regression.localhost": ("cowork", "pearnly_cowork"),
         "dms.regression.localhost": ("dms", "pearnly_dms"),
         # 两套固定数据各自的站点（`datasets.py new|old` 往里填）：照 polymer 新开户、照 mrerp 有存量
         "erp-new.regression.localhost": ("erp", "pearnly_erp_portal"),
         "erp-old.regression.localhost": ("erp", "pearnly_erp_portal"),
         # Cowork 的两套（`datasets.py new|old cowork`）：新开户空站点、有盘点与对账预设的老站点
         "cowork-new.regression.localhost": ("cowork", "pearnly_cowork"),
         "cowork-old.regression.localhost": ("cowork", "pearnly_cowork"),
         # DMS 的两套（`datasets.py new|old dms`）：新开户只接好一个 DMS 连接、没有客户；老站点 DMS 里已有客户
         "dms-new.regression.localhost": ("dms", "pearnly_dms"),
         "dms-old.regression.localhost": ("dms", "pearnly_dms")}
CONTROL = "control.regression.localhost"  # 实验室自己的计费控制面（`lab_billing.py` 建），不是产品站点
# 接计费的站点：(租户名后缀, 产品, 站点)。基础站点的后缀就是产品名（沿用原来的租户）；两套固定数据的站点各自一个租户（10-30：CC 记对话路由要它）。
# 没接上时首页报「计费服务暂不可用」、导出按钮不出（11-01 新栈 exports 0/8）；`lab_billing.py` 照这张表接，`run.py` 跑前照它核对
BILLED = [("erp", "erp", "erp.regression.localhost"), ("cowork", "cowork", "cowork.regression.localhost"),
          ("dms", "dms", "dms.regression.localhost"), ("erp-new", "erp", "erp-new.regression.localhost"),
          ("erp-old", "erp", "erp-old.regression.localhost"), ("cowork-new", "cowork", "cowork-new.regression.localhost"),
          ("cowork-old", "cowork", "cowork-old.regression.localhost")]
# 岗位账号：产品 → {岗位名: 角色}（与开通 / 团队管理发的角色同名；管理员 = 产品管理员 + 全部功能角色）
ROLES = {
    "erp": {"admin": ["ERP Admin", "ERP Products", "ERP Stock", "ERP Purchase", "ERP Sales", "ERP Cashier", "ERP Tasks"],
            # 员工岗位照团队管理真实发的「ERP Menu <菜单>」角色（`team.service.set_member_roles`）；旧的粗角色只给管理员
            "products": ["ERP Menu products", "ERP Menu profile"],
            "stock": ["ERP Menu inventory", "ERP Menu stock-in", "ERP Menu stock-out", "ERP Menu stock-card",
                      "ERP Menu stock-count", "ERP Menu warehouses", "ERP Menu profile"],
            "purchase": ["ERP Menu purchase", "ERP Menu suppliers", "ERP Menu profile"],
            "sales": ["ERP Menu sales", "ERP Menu clients", "ERP Menu profile"],
            "cashier": ["ERP Menu cashier", "ERP Menu pos-receipts", "ERP Menu pos-shifts", "ERP Menu profile"],
            "tasks": ["ERP Menu tasks", "ERP Menu profile"],
            # 收款员（11-11）：只在 LINE / CC 录收款（不是侧栏岗位），LINE 要开；页面只有左下角「我的收款」
            "collector": ["ERP Collector", "ERP LINE"], "none": []},
    "cowork": {"admin": ["Cowork Admin", "Cowork Intake", "Cowork Bank Reconciliation", "Cowork Income Reconciliation",
                         "Cowork VAT Check", "Cowork Tax Filing", "Cowork Stocktake", "Cowork Work", "Cowork Profile", "Cowork LINE"],
               "intake": ["Cowork Intake", "Cowork Profile"], "recon": ["Cowork Bank Reconciliation", "Cowork Income Reconciliation",
                                                                     "Cowork VAT Check", "Cowork Profile"],
               "stocktake": ["Cowork Stocktake", "Cowork Profile"], "work": ["Cowork Work", "Cowork Profile"], "none": []},
    "dms": {"admin": ["DMS Admin", "DMS ID Card Intake", "DMS Customer Match", "DMS Operator"],
            "operator": ["DMS Operator", "DMS ID Card Intake", "DMS Customer Match"], "none": []},
}


def bench(*args):
    result = subprocess.run(["bench", *args], text=True, capture_output=True)
    if result.returncode:
        print(result.stdout[-4000:]); print(result.stderr[-4000:]); result.check_returncode()
    print("PASS bench", " ".join(args[:4]), flush=True)


def main(only):
    if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
        raise SystemExit("Requires the task-owned regression laboratory")
    sites = Path("sites")
    existing = {p.parent.name for p in sites.glob("*/site_config.json")}
    if existing - set(SITES) - {CONTROL}:
        raise SystemExit("Refusing a foreign Site volume")
    config_file = sites / "common_site_config.json"
    config = json.loads(config_file.read_text() or "{}") if config_file.exists() else {}
    config.update(db_host="db", redis_cache="redis://redis:6379/0", redis_queue="redis://redis:6379/1",
                  redis_socketio="redis://redis:6379/2", pause_scheduler=1, disable_scheduler=1, mute_emails=1,
                  serve_default_site=False)
    from pearnly_core.originals import upload_limits

    config = upload_limits.bench_values(config)  # 请求体上限同线上（activate-product.sh）
    # ERP 主档同步的队列同线上（configure-erp-runtime.py；实验室的后台进程也听它）
    config.setdefault("workers", {}).setdefault("erp_masters", {})["timeout"] = 3900
    config_file.write_text(json.dumps(config, indent=2) + "\n")
    (sites / "apps.txt").write_text("frappe\npearnly_core\npearnly_control\npearnly_erp_portal\npearnly_cowork\npearnly_dms\n")
    assets = sites / "assets"
    if not assets.exists():
        assets.symlink_to("/home/frappe/frappe-bench/assets")
    for site, (product, app) in SITES.items():
        if only and site not in only:
            continue
        site_file = sites / site / "site_config.json"
        if not site_file.exists():
            bench("new-site", site, "--db-host", "db", "--db-root-password", PASSWORD, "--admin-password", PASSWORD,
                  "--mariadb-user-host-login-scope", "%")
        site_config = json.loads(site_file.read_text())
        features = json.loads(Path(f"apps/{app}/{app}/features.json").read_text())["features"]
        site_config.update(pearnly_product=product, pause_scheduler=1, disable_scheduler=1, mute_emails=1,
                           pearnly_features=["home_subscription", *[item["code"] for item in features]],
                           pearnly_company_name="Regression Lab", developer_mode=0, allow_tests=True,
                           # 服务端自己拼的地址（PDF 回取字体 / 图片）走容器里的网页口；浏览器那边只用相对地址
                           host_name=f"http://{site}:8000",
                           # CC 对话页：引擎照线上（Managed Agents），请求由编排把 ANTHROPIC_BASE_URL 指到假服务 agents（lab_agents.py）；
                           # 密钥是格式对的假值，假服务不验、也到不了真接口
                           pearnly_assistant_engine="anthropic-managed-agents",
                           pearnly_assistant_api_key="sk-ant-regression-lab-" + "0" * 24,
                           # Cowork 的电脑通道总闸（同线上开着）：CC 机器方法的流程（`cowork.machine.operator-ai`）建站时就打开，
                           # 不在流程里临时改配置（网页进程把站点配置缓存 60 秒，改了不会马上生效）
                           **({"pearnly_cowork_erp_push_enabled": True} if product == "cowork" else {}))
        site_file.write_text(json.dumps(site_config, indent=2) + "\n")
        if not (sites / site / ".installed").exists():
            bench("--site", site, "install-app", "pearnly_core", app)
            (sites / site / ".installed").write_text("1")
        bench("--site", site, "migrate")
        users = json.dumps(ROLES[product])
        subprocess.run(["../env/bin/python", "/workspace/scripts/regression/lab_users.py", site, product, users], check=True, cwd="sites")
        print("READY", site, flush=True)
    # 建完站接计费（可重复跑）：新起的实验栈不用再记着手动跑一遍
    subprocess.run(["env/bin/python", "/workspace/scripts/regression/lab_billing.py"], check=True)


if __name__ == "__main__":
    main(sys.argv[1:])
