#!/usr/bin/env python3
"""登录后端到端（11-05，方案 docs/work/2026-11-05-product-map-next.md §2；场景登记 tests/e2e/registry.toml）。

部署回读通过后在本机跑：用专用 e2e 账号，经真实的 fatcc.com 入口与 Keycloak 登录，对生产上的测试租户走一遍登录之后的流程
（回归实验室没有身份服务，测不到这一段）。正式租户一个都不登录。

    python3 scripts/e2e/run.py --all                         # 全部场景 × 各自的跑法
    python3 scripts/e2e/run.py --scenario e2e.erp.switch-site --run desktop
    python3 scripts/e2e/run.py --product erp --out 结果.json

每个场景 × 跑法得到「通过 / 不通过 / 没测到（原因）」：
- 不通过：断言的业务结果不对（落错站、出了登录页、开了新窗口……），或登录被拒；登录被拒**不重试**（Keycloak 10 次锁号）；
- 只有页面导航超时才把这一格重跑一次；
- 没测到：前置自检不过（测试租户此刻不在生产、账号还没建、Playwright 版本与产品镜像不同），或场景步骤还没写。没测到不算通过。
失败时截图（先清空密码框），存 ~/pearnly-devtools/e2e-runs/<时间>/，每次跑顺手删 14 天前的；不开 trace / HAR（会记下密码与 Cookie）。

凭据只从本机 ~/.config/pearnly-e2e/accounts.env 读（权限必须是 600）：E2E_ERP_USER、E2E_ERP_PASSWORD、E2E_COWORK_USER、E2E_COWORK_PASSWORD。
不打印、不写日志、不放命令行参数。

退出码：0 全部通过；1 有不通过；3 没有不通过但有没测到；2 用法或本机环境不对。
"""
from __future__ import annotations

import argparse
import datetime
import importlib.metadata
import json
import os
import re
import shutil
import stat
import sys
import time
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenarios  # noqa: E402  场景步骤（每个场景一个函数）
from scenarios import Failed, LoginRejected, NavigationTimeout, NotTested  # noqa: E402

REGISTRY = ROOT / "tests" / "e2e" / "registry.toml"
DOCKERFILE = ROOT / "infrastructure" / "images" / "frappe.Dockerfile"
TARGET = ROOT / "infrastructure" / "production" / "target.json"
CREDENTIALS = Path(os.environ.get("PEARNLY_E2E_CREDENTIALS", Path.home() / ".config" / "pearnly-e2e" / "accounts.env"))
CLOUDFLARE = Path(os.environ.get("PEARNLY_TESTENV_CFG", Path.home() / ".config" / "pearnly-testenv"))
RUNS_DIR = Path(os.environ.get("PEARNLY_E2E_RUNS", Path.home() / "pearnly-devtools" / "e2e-runs"))
KEEP_DAYS = 14
PRODUCTS = {"ERP": "erp", "Cowork": "cowork"}
# 生产上的测试租户：同池第二站用 test01（11-05 只读核查：在业务池、已在 fatcc.com 登记）
SITES = {"erp": "testerp.pearnly.com", "erp-second": "test01.pearnly.com", "cowork": "testcowork.pearnly.com"}
ENTRY = "https://fatcc.com"
ACCOUNT_KEYS = ("E2E_ERP_USER", "E2E_ERP_PASSWORD", "E2E_COWORK_USER", "E2E_COWORK_PASSWORD")
VIEWPORTS = {"desktop": {"width": 1280, "height": 800}, "mobile": {"width": 390, "height": 844}}
PASSED, FAILED, NOT_TESTED = "通过", "不通过", "没测到"


@dataclass
class Account:
    user: str
    password: str = field(repr=False)

    def __repr__(self) -> str:  # 不让密码出现在任何报错与日志里
        return f"Account(user={self.user!r})"


@dataclass
class Result:
    scenario: str
    run: str
    outcome: str
    detail: str = ""
    seconds: float = 0.0
    screenshot: str = ""


# ---------------------------------------------------------------- 登记表与凭据

def registry(path=REGISTRY) -> list[dict]:
    return tomllib.loads(Path(path).read_text(encoding="utf-8")).get("scenario", [])


def select(all_scenarios: list[dict], ids=None, product=None, run=None) -> list[tuple[dict, str]]:
    cells = []
    for scenario in all_scenarios:
        if ids and scenario["id"] not in ids:
            continue
        if product and PRODUCTS.get(scenario["product"]) != product:
            continue
        for name in scenario["runs"]:
            if not run or name == run:
                cells.append((scenario, name))
    return cells


def load_accounts(path=CREDENTIALS) -> dict:
    """{"erp": Account, "cowork": Account}；文件不在、权限不是 600、缺键 → NotTested（说清楚缺什么，不带任何值）。"""
    path = Path(path)
    if not path.exists():
        raise NotTested(f"e2e 账号还没建：{path} 不存在（建账号要授权，见方案 §12 第 4 问）")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise NotTested(f"{path} 的权限要是 600（现在别人也能读）")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    missing = [key for key in ACCOUNT_KEYS if not values.get(key)]
    if missing:
        raise NotTested(f"{path} 缺 {', '.join(missing)}")
    return {"erp": Account(values["E2E_ERP_USER"], values["E2E_ERP_PASSWORD"]),
            "cowork": Account(values["E2E_COWORK_USER"], values["E2E_COWORK_PASSWORD"])}


# ---------------------------------------------------------------- 前置自检

def pinned_playwright(dockerfile=DOCKERFILE) -> str:
    """产品镜像锁的 Playwright 版本（按兼容矩阵，不另选）。"""
    found = re.search(r"^ARG PLAYWRIGHT_VERSION=(\S+)", Path(dockerfile).read_text(encoding="utf-8"), re.M)
    if not found:
        raise SystemExit(f"{dockerfile} 里找不到 PLAYWRIGHT_VERSION")
    return found.group(1)


def check_playwright(installed=None, pinned=None) -> None:
    pinned = pinned or pinned_playwright()
    try:
        installed = installed or importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        installed = ""
    if installed != pinned:
        raise NotTested(f"本机 Playwright 是 {installed or '没装'}，产品镜像锁的是 {pinned}："
                        f"在 ~/pearnly-devtools/e2e-venv/ 装 playwright=={pinned} 后用那个 python 跑")


def production_ip(target=TARGET) -> str:
    return json.loads(Path(target).read_text(encoding="utf-8"))["public_ipv4"]


def cloudflare_records(config=CLOUDFLARE, get=None) -> list[dict]:
    """pearnly.com 区的解析记录（只发 GET；令牌只从本机文件读）。"""
    token = (Path(config) / "cloudflare.token").read_text(encoding="utf-8").strip()
    zone = ""
    env = Path(config) / "cloudflare.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("CF_ZONE_ID="):
                zone = line.partition("=")[2].strip().strip('"')
    get = get or _cloudflare_get
    if not zone:
        zone = get("/zones?name=pearnly.com", token)["result"][0]["id"]
    return get(f"/zones/{zone}/dns_records?per_page=500", token)["result"]


def _cloudflare_get(path: str, token: str) -> dict:
    request = urllib.request.Request("https://api.cloudflare.com/client/v4" + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def where_is(host: str, records: list[dict], prod_ip: str) -> str:
    """测试租户此刻在哪：自己的 CNAME 指向隧道 = 测试机（testenv lab-start 建的）；没有自己的记录就落到通配记录。"""
    own = [r for r in records if r.get("name") == host]
    chosen = own or [r for r in records if r.get("name") == "*." + host.split(".", 1)[1]]
    if not chosen:
        return "没有解析记录"
    record = chosen[0]
    content = str(record.get("content") or "")
    if record.get("type") == "CNAME" and content.endswith(".cfargotunnel.com"):
        return "测试机"
    if record.get("type") == "A" and content == prod_ip:
        return "生产"
    return f"别处（{record.get('type')} {content}）"


def preflight(products: set, records_of=cloudflare_records, prod_ip=None) -> dict:
    """{产品: 没测到的原因}；空 = 都能跑。"""
    prod_ip = prod_ip or production_ip()
    try:
        records = records_of()
    except Exception as error:  # noqa: BLE001  读不到解析就没法确定跑的是不是这次部署的代码
        return {p: f"读不到 Cloudflare 解析（{type(error).__name__}）" for p in products}
    problems = {}
    for product in products:
        hosts = [SITES[product]] + ([SITES["erp-second"]] if product == "erp" else [])
        for host in hosts:
            place = where_is(host, records, prod_ip)
            if place != "生产":
                problems[product] = f"{host} 现在在{place}，跑的不是这次部署的代码"
    return problems


# ---------------------------------------------------------------- 跑

def run_cell(scenario: dict, run_name: str, accounts: dict, open_page, out_dir: Path, steps=None) -> Result:
    """跑一格。open_page(run_name) 返回 (page, close)；步骤从 scenarios.STEPS 取。"""
    steps = scenarios.STEPS if steps is None else steps
    step = steps.get(scenario["id"])
    if step is None:
        return Result(scenario["id"], run_name, NOT_TESTED, "场景步骤还没写（scripts/e2e/scenarios.py）")
    attempts = 0
    while True:
        attempts += 1
        started = time.monotonic()
        page, close = open_page(run_name)
        try:
            context = scenarios.Context(product=PRODUCTS[scenario["product"]], run=run_name, sites=SITES, entry=ENTRY,
                                        accounts=accounts)
            step(page, context)
            return Result(scenario["id"], run_name, PASSED, seconds=time.monotonic() - started)
        except NavigationTimeout as error:
            if attempts == 1:
                continue
            return _failed(scenario, run_name, page, out_dir, f"导航两次超时：{error}", started)
        except NotTested as error:
            return Result(scenario["id"], run_name, NOT_TESTED, str(error), time.monotonic() - started)
        except (LoginRejected, Failed) as error:
            return _failed(scenario, run_name, page, out_dir, str(error), started)
        except Exception as error:  # noqa: BLE001  页面异常、选择器找不到等，一律算不通过并截图
            return _failed(scenario, run_name, page, out_dir, f"{type(error).__name__}: {error}", started)
        finally:
            close()


def _failed(scenario, run_name, page, out_dir: Path, detail: str, started: float) -> Result:
    shot = ""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for box in page.query_selector_all("input[type=password]"):
            box.fill("")
        target = out_dir / f"{scenario['id']}-{run_name}.png"
        page.screenshot(path=str(target), full_page=True)
        shot = str(target)
    except Exception:  # noqa: BLE001  截不了图不影响结论
        pass
    return Result(scenario["id"], run_name, FAILED, detail, time.monotonic() - started, shot)


def prune(runs_dir=RUNS_DIR, keep_days=KEEP_DAYS, now=None) -> list[str]:
    """删 keep_days 天前的截图目录（只删本脚本建的 <年月日T时分秒> 目录）。"""
    now = now or time.time()
    removed = []
    if not Path(runs_dir).is_dir():
        return removed
    for child in Path(runs_dir).iterdir():
        if child.is_dir() and re.fullmatch(r"\d{8}T\d{6}", child.name) and now - child.stat().st_mtime > keep_days * 86400:
            shutil.rmtree(child)
            removed.append(child.name)
    return removed


def exit_code(results: list[Result]) -> int:
    outcomes = {r.outcome for r in results}
    if FAILED in outcomes:
        return 1
    if NOT_TESTED in outcomes or not results:
        return 3
    return 0


def report(results: list[Result]) -> str:
    lines = []
    for r in results:
        line = f"  {r.outcome}  {r.scenario} [{r.run}]"
        if r.seconds:
            line += f"  {r.seconds:.1f} 秒"
        if r.detail:
            line += f"  —— {r.detail}"
        if r.screenshot:
            line += f"（截图 {r.screenshot}）"
        lines.append(line)
    counts = {o: sum(r.outcome == o for r in results) for o in (PASSED, FAILED, NOT_TESTED)}
    lines.append(f"登录后 e2e：通过 {counts[PASSED]}，不通过 {counts[FAILED]}，没测到 {counts[NOT_TESTED]}")
    return "\n".join(lines)


def playwright_pages():  # pragma: no cover - 真浏览器，B2 块对着测试租户调
    from playwright.sync_api import sync_playwright

    driver = sync_playwright().start()
    browsers = {}

    def open_page(run_name):
        engine = "webkit" if run_name == "iphone" else "chromium"
        if engine not in browsers:
            browsers[engine] = getattr(driver, engine).launch(headless=True)
        if run_name == "iphone":
            options = dict(driver.devices["iPhone 13"])
        elif run_name == "mobile":   # 安卓手机标识与触屏，宽度同流程用例的手机宽度
            options = {**driver.devices["Pixel 7"], "viewport": VIEWPORTS["mobile"]}
        else:
            options = {"viewport": VIEWPORTS[run_name]}
        context = browsers[engine].new_context(**options)
        page = context.new_page()
        return page, context.close

    def stop():
        for browser in browsers.values():
            browser.close()
        driver.stop()

    return open_page, stop


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--product", choices=sorted(PRODUCTS.values()))
    parser.add_argument("--run", choices=["desktop", "mobile", "iphone"])
    parser.add_argument("--out", help="结果写成 JSON")
    args = parser.parse_args(argv)
    if not (args.all or args.scenario or args.product):
        parser.error("给 --all、--scenario 或 --product")
    cells = select(registry(), args.scenario, args.product, args.run)
    if not cells:
        parser.error("没有选中任何场景")
    out_dir = RUNS_DIR / datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    prune()
    results: list[Result] = []
    blocked = {}
    try:
        check_playwright()
        accounts = load_accounts()
        blocked = preflight({PRODUCTS[s["product"]] for s, _ in cells})
    except NotTested as error:
        results = [Result(s["id"], r, NOT_TESTED, str(error)) for s, r in cells]
    if not results:
        open_page, stop = playwright_pages()
        try:
            for scenario, run_name in cells:
                product = PRODUCTS[scenario["product"]]
                if product in blocked:
                    results.append(Result(scenario["id"], run_name, NOT_TESTED, blocked[product]))
                    continue
                results.append(run_cell(scenario, run_name, accounts, open_page, out_dir))
        finally:
            stop()
    print(report(results))
    if args.out:
        Path(args.out).write_text(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
