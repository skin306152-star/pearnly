"""把「库存体检」跑到某个站点上（只读）。

用户 2026-09-23："物料库存入库出库，采购销售 pos 客户供应商公司仓库，这些数据关联不上，
要么对不上，要么出错……我一点点的去试不知道要试到啥时候才发现问题"。

检查项写在服务端那一个模块里（`pearnly_erp_portal.inventory.audit`），这里只是把它跑起来 ——
不重写一遍逻辑，也不写任何数据。默认打本仓的实验室站点：

    python3 scripts/erp-pos-consistency-check.py
    python3 scripts/erp-pos-consistency-check.py --site 别的站点.localhost

换成别的机器/容器就传 `--container`（那个容器里要有这个 bench）。
"""
import argparse
import subprocess
import sys

DEFAULT_CONTAINER = "pearnly-purchase-lab-bench-1"
DEFAULT_SITE = "erp-pos-a.localhost"
BENCH = "/home/frappe/frappe-bench"
ENTRY = "pearnly_erp_portal.inventory.audit.run"


def run(container, site):
    command = f"cd {BENCH} && bench --site {site} execute {ENTRY}"
    result = subprocess.run(["docker", "exec", container, "bash", "-lc", command],
                            text=True, capture_output=True, timeout=1800)
    sys.stdout.write(result.stdout)
    if result.returncode:
        sys.stderr.write(result.stderr)
        return result.returncode
    # `bench execute` 会把返回值也打出来；有硬问题就非零退出，方便当闸用。
    # 提示（报告里 `!` 那几条）不算失败。
    return 1 if '"issues": [{' in result.stdout else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--site", default=DEFAULT_SITE)
    args = parser.parse_args()
    return run(args.container, args.site)


if __name__ == "__main__":
    raise SystemExit(main())
