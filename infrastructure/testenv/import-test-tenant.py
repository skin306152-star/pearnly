#!/usr/bin/env python3
"""把一个测试租户整站导入测试环境：数据库、文件、站点配置。

站点名与线上一致，因此线上那份 site_config.json（含 encryption_key 与各渠道凭据）
可以原样使用；数据库名与账号也沿用线上的，避免出现"库里密文解不开"这类假验证。

导入是幂等的：同名站点已存在且带 marker 时直接重建数据库内容。
不触碰正式服务器，也不导入身份库（Keycloak）；登录仍用本机管理员账号。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BENCH = Path("/home/frappe/frappe-bench")
SITES = BENCH / "sites"
MARKER = SITES / ".pearnly-testenv"
PRODUCT_BY_GROUP = {"dms": "dms", "erp": "erp", "cowork": "cowork"}

# 账本（控制面）站点不是产品租户：它没有 pearnly_product，靠 pearnly_billing_only 标识。
# **必须整站导入**，不能只建空站：产品站点用的是**记账本库里的**分租户服务令牌，
# 空站的库里没有那行，所有计费请求都会被拒（实测表现为 billing 404 / 一直重试）。
BILLING_GROUP = "billing"
BILLING_SITE = "billing.internal"


def shell(cmd, **kwargs):
    result = subprocess.run(cmd, text=True, capture_output=True, **kwargs)
    if result.returncode:
        print(result.stdout[-4000:])
        print(result.stderr[-4000:])
        raise SystemExit(f"failed: {' '.join(map(str, cmd))}")
    return result.stdout


def mysql(root_password, database=None, sql=None, stdin=None):
    cmd = ["mysql", "-h", "mariadb", "-u", "root", f"-p{root_password}"]
    if database:
        cmd += [database]
    if sql:
        cmd += ["-e", sql]
    return shell(cmd, stdin=stdin)


def main():
    site = os.environ["TESTENV_SITE"]
    group = os.environ["TESTENV_GROUP"]
    root_password = os.environ["TESTENV_DB_ROOT_PASSWORD"]
    source = Path(os.environ.get("TESTENV_IMPORT_DIR", f"/workspace/import/{site}"))

    config_file = source / f"{site}-site_config.json"
    if not config_file.is_file():
        raise SystemExit(f"missing {config_file}")
    config = json.loads(config_file.read_text())
    if group == BILLING_GROUP:
        if site != BILLING_SITE:
            raise SystemExit(f"refusing {site} into group {group} (only {BILLING_SITE})")
        if not config.get("pearnly_billing_only"):
            raise SystemExit(f"refusing {site}: not a billing-only site")
    elif config.get("pearnly_product") != PRODUCT_BY_GROUP.get(group):
        raise SystemExit(f"refusing {config.get('pearnly_product')} into group {group}")
    db_name, db_user, db_password = config["db_name"], config["db_user"], config["db_password"]

    def newest(pattern, exclude=None):
        matches = sorted(source.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        matches = [m for m in matches if not exclude or exclude not in m.name]
        if not matches:
            raise SystemExit(f"no {pattern} in {source}")
        return matches[0]

    dump = newest("*-database.sql.gz")
    public = newest("*-files.tar", exclude="private-files")
    private = newest("*-private-files.tar")
    print(f"site={site} group={group} db={db_name} tenant={config.get('pearnly_tenant')}")
    print(f"dump={dump.name} public={public.name} private={private.name}")

    if (SITES / site).exists() and not MARKER.exists():
        raise SystemExit("refusing an existing Site without the testenv marker")
    if Path(site).exists() and (SITES / site / "site_config.json").exists():
        existing = json.loads((SITES / site / "site_config.json").read_text())
        if existing.get("db_name") != db_name:
            raise SystemExit("existing Site has a different database; refusing")

    mysql(root_password, sql=(
        f"CREATE DATABASE IF NOT EXISTS `{db_name}`; "
        f"CREATE USER IF NOT EXISTS '{db_user}'@'%' IDENTIFIED BY '{db_password}'; "
        f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'%'; FLUSH PRIVILEGES;"))
    mysql(root_password, sql=f"DROP DATABASE `{db_name}`; CREATE DATABASE `{db_name}`;")

    decompress = subprocess.Popen(["zcat", str(dump)], stdout=subprocess.PIPE)
    mysql(root_password, database=db_name, stdin=decompress.stdout)
    decompress.wait()
    if decompress.returncode:
        raise SystemExit("zcat failed")

    site_dir = SITES / site
    # 备份归档只带 files / private-files，站点目录的其余标准子目录要自己补齐，
    # 否则 Frappe 写日志时会直接抛 FileNotFoundError。
    for sub in ("logs", "private/backups", "private/files", "public/files"):
        (site_dir / sub).mkdir(parents=True, exist_ok=True)
    for archive in (public, private):
        shell(["tar", "xf", str(archive), "-C", str(SITES)])
    shutil.copy2(config_file, site_dir / "site_config.json")
    os.chmod(site_dir / "site_config.json", 0o600)
    MARKER.write_text("pearnly-testenv\n")

    tables = mysql(root_password, database=db_name,
                   sql="SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE();")
    files = (mysql(root_password, database=db_name, sql="SELECT COUNT(*) FROM `tabFile`;")
             if "tabFile" in mysql(root_password, database=db_name,
                                   sql="SHOW TABLES LIKE 'tabFile';") else "n/a")
    print(json.dumps({"site": site, "db": db_name, "tenant": config.get("pearnly_tenant"),
                      "tables": tables.strip().splitlines()[-1] if tables else "?",
                      "files": str(files).strip()}, ensure_ascii=False))
    print("IMPORT_OK")


if __name__ == "__main__":
    sys.exit(main())
