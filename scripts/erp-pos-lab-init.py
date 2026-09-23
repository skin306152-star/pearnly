"""Install isolated ERP/POS laboratory Sites. Writes only the named lab volume.

Run through infrastructure/compose/erp-pos-lab.yml, never a shared bench.
Installs pearnly_core + pearnly_erp_portal only: erpnext is deliberately absent so
that a hidden dependency on ERPNext business DocTypes fails loudly instead of
being papered over. Does not create production tenants and enables no scheduler.

Sites are the two-tenant fixture (A/B) used to prove tenant isolation.
"""
import json
import os
from pathlib import Path
import subprocess

PRODUCT_APPS = ("pearnly_core", "pearnly_erp_portal")

# 站点"买到了哪些功能"**从产品自己的清单读**，不在这里再抄一份。
#
# 这份清单原先写死在下面（`["products", "warehouses", "stockcard", "purchase",
# "sales", "clients"]`），于是产品后来加的 `stocktake`（盘点）与 `cashier`（收银）
# 没被写进站点 —— 菜单按"没买的功能不出现"把它们藏了，用户看到的是"容器里没有盘点"。
# 产品清单是 ADR-009 里产品自己声明的唯一来源（`apps/<app>/<app>/features.json`），
# 实验室只是把整份清单买下来（与开通口径一致：选了产品就全开）。
HOME_FEATURE = "home_subscription"   # 三个产品共有的首页，由控制面声明
CATALOG = Path("apps/pearnly_erp_portal/pearnly_erp_portal/features.json")


def feature_codes() -> list:
    """本产品的全部功能码 = 首页 + 产品清单里的每一条。"""
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    return [HOME_FEATURE, *[str(item["code"]) for item in data["features"]]]


def _bench(args, **kwargs):
    return subprocess.run(["bench", *args], check=True, **kwargs)


def main():
    if os.environ.get("PEARNLY_ERP_POS_LAB") != "1":
        raise SystemExit("This command requires the dedicated ERP/POS laboratory")

    sites = Path("sites")
    sites.mkdir(exist_ok=True)
    marker = sites / ".erp-pos-lab"
    allowed = {"erp-pos-a.localhost", "erp-pos-b.localhost"}
    requested = set(os.environ.get("PEARNLY_ERP_POS_LAB_SITES", "").split()) or allowed
    if not requested <= allowed:
        raise SystemExit("Refusing an unknown lab Site")

    existing = {p.parent.name for p in sites.glob("*/site_config.json")}
    if existing - allowed or (existing and not marker.exists()):
        raise SystemExit("Refusing an existing or foreign Site volume")

    config_file = sites / "common_site_config.json"
    config = json.loads(config_file.read_text()) if config_file.exists() else {}
    if config.get("db_host") not in (None, "db"):
        raise SystemExit("Refusing a foreign database")
    config.update(
        db_host="db",
        redis_cache="redis://redis:6379/0",
        redis_queue="redis://redis:6379/1",
        redis_socketio="redis://redis:6379/2",
        # 这个实验室**没有 worker**，迁移时排的搜索索引任务不会被消费，堆到 500 条就把
        # 之后的写入全挡住（`Too many queued background jobs`）。生产有 worker，不受影响；
        # 实验室把上限调高，并在需要时清一次队列：
        #   docker compose -f infrastructure/compose/erp-pos-lab.yml exec -T redis redis-cli -n 1 flushdb
        max_queued_jobs=100000,
        developer_mode=1,
        pause_scheduler=1,
        disable_scheduler=1,
        serve_default_site=False,
    )
    config_file.write_text(json.dumps(config, indent=2) + "\n")
    marker.write_text("pearnly-erp-pos-lab\n")
    (sites / "apps.txt").write_text("frappe\n" + "\n".join(PRODUCT_APPS) + "\n")

    for name in sorted(requested):
        site_file = sites / name / "site_config.json"
        if not site_file.exists():
            _bench(
                ["new-site", name, "--db-host", "db",
                 "--db-root-password", "erp-pos-lab-only",
                 "--admin-password", "erp-pos-lab-only",
                 "--mariadb-user-host-login-scope", "%"],
            )
        _bench(["--site", name, "install-app", *PRODUCT_APPS])
        site_config = json.loads(site_file.read_text())
        site_config.update(
            pearnly_product="erp",
            pearnly_erp_pos_lab=True,
            pearnly_features=feature_codes(),
            pause_scheduler=1,
            disable_scheduler=1,
            mute_emails=1,
        )
        site_file.write_text(json.dumps(site_config, indent=2) + "\n")
        _bench(["--site", name, "migrate"])

        installed = subprocess.run(
            ["bench", "--site", name, "list-apps"],
            check=True, capture_output=True, text=True,
        ).stdout.split()
        if "erpnext" in installed:
            raise SystemExit(f"{name}: erpnext must not be installed in this laboratory")
        print(f"{name}: apps={installed}")

    print("ERP/POS laboratory ready; two isolated tenants, no ERPNext, no scheduler")


if __name__ == "__main__":
    main()
