#!/usr/bin/env python3
"""真跑前置自检（A 线 11-10，通用；真模型场景开跑前用，也可单独跑）：实验栈两个产品要识别、要写账的配置一项不缺才开跑。

11-09 第 1 批真模型场景 7 次里 6 次白花在缺配置上（Cowork 缺识别准入、没登记电脑、缺容量 Redis、ocr 队列没登记、识别进程没起；
ERP 缺识别配置「尚未配置文字识别」），每补一项再真跑一遍。这里把查过的项收成一份，两个产品同一套：

  - 识别配置：Cowork 识别准入（`ocr.execution.settings.snapshot` 的 mode）；ERP `pearnly_core.ocr.is_configured()`；
    两个站点识别引擎 ``pearnly_ocr_engine=claude``、容量计数 Redis（`execution.budget`）通。
  - 识别进程与队列：全局 ``workers`` 登记了 ocr 队列；容器里识别进程（`pearnly_core.execution.worker`）与派发进程
    （`pearnly_core.execution.dispatch`）都在跑。
  - 计费放行：`billing_client.status("pdf", 页数)` 放行。
  - 合成电脑连接：Cowork 这条连接「可录」（`destinations.intake_state == ready`，本人有电脑够得到这份账套）；ERP 这家公司接了
    Express、有电脑够得到它的账套（`cc_desktop.books.reachers`）。
  - 引擎：``fake`` 模式（干跑）对话与识别都指实验栈的假服务（`scripts/flows/lab_agents.py`：假智能体 + 假识别），不花钱；
    ``real`` 模式对话指真 Anthropic、站点是真密钥、识别密钥在容器环境里。

缺一项 ``ok`` 为假，调用方就不开跑。干跑与真跑走同一条配置路径，差别只在引擎指哪里（`engine`）。

    PEARNLY_FLOWS_LAB=pearnly-parity-lab REGRESSION_LAB_PORT=18941 \\
      python3 scripts/scenarios/real_run_preflight.py --mode fake --cowork-connection <连接> --erp-company <公司>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITES = {"cowork": "cowork.regression.localhost", "erp": "erp.regression.localhost"}
FAKE_KEY = "sk-ant-regression-lab-" + "0" * 24
AGENTS_URL = "http://agents:18780"
REAL_URL = "https://api.anthropic.com"
WORKER, DISPATCH = "pearnly_core.execution.worker", "pearnly_core.execution.dispatch"

# 识别要的站点配置（合成值，只为过准入 / 「已配置」的判断；真读票按 pearnly_ocr_engine=claude 交 Claude，谷歌路线被
# `claude_ocr.client.refuse_google` 挡住，不会真调谷歌）。Cowork 的准入要写明识别配置与一套 Vertex 连接
# （同 `scripts/cowork-execution-lab-verify.py`）；ERP 的「已配置」按后端判（`pearnly_core.ocr.is_configured`：aistudio 看密钥）
OCR_CONFIG = {
    "cowork": {"pearnly_cowork_ocr_environment": {"OCR_LLM_BACKEND": "vertex"}, "pearnly_cowork_ocr_policy": {"mode": "economy"},
               "pearnly_ocr_credentials": "/synthetic/credential-reference.json", "pearnly_ocr_backend": "vertex",
               "pearnly_ocr_project": "synthetic-lab", "pearnly_ocr_location": "asia-southeast1"},
    "erp": {"pearnly_ocr_backend": "aistudio", "pearnly_ocr_api_key": "synthetic-lab-not-a-key"},
}
# 两个产品都要的：识别引擎、容量计数 Redis（值取站点自己的队列 Redis，见 `configure`）
SHARED_CONFIG = ("pearnly_ocr_engine", "pearnly_ocr_budget_redis")

OVERRIDE = """services:
  bench:
    env_file: [{env}]
    environment:
      ANTHROPIC_BASE_URL: {base}
      FRAPPE_BACKGROUND_WORKERS_NOFORK: "1"
      PEARNLY_OCR_THREADS: "1"
    command: [bash, -lc, "for p in 8000 8001 8002; do (cd sites && ../env/bin/python -m pearnly_core.web.wsgi $$p) & done; bench worker --queue short,default,long & (cd sites && ../env/bin/python -m pearnly_core.execution.worker --queue ocr --num-workers 1) & (cd sites && ../env/bin/python -m pearnly_core.execution.dispatch) & wait"]
"""


def lab_module():
    """流程框架（实验室容器、站点里跑 Python）：`scripts/flows/run.py` 的 ``Lab``。"""
    sys.path.insert(0, str(ROOT / "scripts" / "flows"))
    import run  # noqa: E402

    return run


def _site_config(lab, site, values: dict, remove=()):
    """直接改站点的 site_config.json（``bench remove-from-config`` 11-09 实测没生效，这里不用它）。"""
    return lab.python(site, f'''
import json as _json
path = frappe.get_site_path("site_config.json")
conf = _json.load(open(path))
conf.update({json.dumps(values)})
for key in {json.dumps(list(remove))}:
    conf.pop(key, None)
open(path, "w").write(_json.dumps(conf, indent=1, sort_keys=True))
print(_json.dumps({{"keys": sorted(conf)}}))
''')


def configure(lab, on: bool, key: str = FAKE_KEY):
    """两个站点的识别配置与对话密钥：``on`` 写上（识别引擎 claude、容量 Redis、各自的识别准入 / 已配置项、对话密钥 ``key``），
    否则去掉识别项、对话密钥换回假值。全局 ``workers`` 登记 ocr 队列（同生产；不登记识别进程起不来，11-09 实测 24 页排了 6 分钟）。"""
    if on:
        lab.bench("bench", "set-config", "-g", "--parse", "workers", '{"ocr": {"timeout": 1800}}', timeout=300)
    for product, site in SITES.items():
        if on:
            redis = lab.python(site, 'print(json.dumps({"url": frappe.conf.get("redis_queue") or ""}))')["url"]
            values = {**OCR_CONFIG[product], "pearnly_ocr_engine": "claude", "pearnly_ocr_budget_redis": redis,
                      "pearnly_assistant_api_key": key}
            _site_config(lab, site, values)
        else:
            _site_config(lab, site, {"pearnly_assistant_api_key": FAKE_KEY}, remove=[*OCR_CONFIG[product], *SHARED_CONFIG])


def compose(src, *extra, env=None):
    import subprocess

    run = lab_module()
    files = ["-f", str(Path(src) / "infrastructure/compose/regression-lab.yml"), *extra]
    return subprocess.run(["docker", "compose", "-p", run.LAB, *files, "up", "-d", "--wait", "bench"], cwd=src, env=env,
                          capture_output=True, text=True, timeout=900)


def lab_env():
    run = lab_module()
    env = dict(os.environ)
    env.setdefault("REGRESSION_LAB_PORT", str(run.PORT))
    for suffix, offset in (("_COWORK", 1), ("_DMS", 2)):
        env.setdefault("REGRESSION_LAB_PORT" + suffix, str(run.PORT + offset))
    return env


def engine(lab, src, mode: str, key_file: str = ""):
    """接引擎：``fake``（对话与识别都指实验栈假服务，识别进程与派发进程照真跑起）、``real``（指真 Anthropic，密钥从文件读，
    只进站点配置与容器环境，不打印）、``off``（回到流程用的样子：假智能体、没有识别配置、bench 不带覆盖文件）。"""
    if mode == "off":
        configure(lab, False)
        result = compose(src, env=lab_env())
        if result.returncode:
            raise SystemExit(f"重起 bench 失败：{result.stderr[-600:]}")
        lab_module().Lab.restart(lab)
        return
    if mode == "real":
        key = Path(key_file).read_text().strip()
        if not key.startswith("sk-ant-") or key.startswith("sk-ant-regression-lab-"):
            raise SystemExit("密钥文件内容不像 Anthropic 密钥")
        base = REAL_URL
    else:
        key, base = FAKE_KEY, AGENTS_URL
    configure(lab, True, key)
    with tempfile.TemporaryDirectory() as folder:
        env_file = Path(folder) / "engine.env"
        env_file.write_text(f"PEARNLY_ANTHROPIC_API_KEY={key}\n")
        env_file.chmod(0o600)
        override = Path(folder) / "engine.override.yml"
        override.write_text(OVERRIDE.format(env=env_file, base=base))
        result = compose(src, "-f", str(override), env=lab_env())
        if result.returncode:
            raise SystemExit(f"重起 bench 失败：{result.stderr[-600:]}")
    lab_module().Lab.restart(lab)


def _processes(lab) -> dict:
    running = lab.exec("sh", "-c", 'for f in /proc/[0-9]*/cmdline; do tr "\\0" " " < "$f"; echo; done 2>/dev/null').stdout
    lines = [line for line in running.splitlines() if "python" in line and "bash" not in line]  # 启动它们的那行 bash 不算
    return {name: any(f"-m {name}" in line for line in lines) for name in (WORKER, DISPATCH)}


COMMON = '''
import os
from pearnly_core import billing_client
from pearnly_core.execution import budget
out = {}
key = str(frappe.conf.get("pearnly_assistant_api_key") or "")
out["engine"] = {"assistant_key": "fake" if key.startswith("sk-ant-regression-lab-") else ("real" if key else "none"),
                 "base_url": os.environ.get("ANTHROPIC_BASE_URL", ""),
                 "ocr_key": "fake" if os.environ.get("PEARNLY_ANTHROPIC_API_KEY", "").startswith("sk-ant-regression-lab-")
                            else ("real" if os.environ.get("PEARNLY_ANTHROPIC_API_KEY", "").strip() else "none"),
                 "ocr_engine": str(frappe.conf.get("pearnly_ocr_engine") or "")}
try:
    out["capacity"] = bool(budget.redis().ping())
except Exception as error:
    out["capacity"] = "refused: " + str(error)[:160]
workers = frappe.get_conf().get("workers") or {}
out["ocr_queue"] = "ocr" in workers
out["billing"] = billing_client.status("pdf", PAGES, tier="B").get("allowed")
'''

COWORK = '''
from pearnly_cowork.assistant import tools
from pearnly_cowork.ocr.execution import settings
try:
    out["recognition"] = settings.snapshot().get("mode")
except Exception as error:
    out["recognition"] = "refused: " + str(error)[:160]
out["computer"] = tools.destinations.intake_state("express", CONNECTION) if CONNECTION else "no connection given"
print(json.dumps(out, default=str))
'''

ERP = '''
from pearnly_core import ocr
from pearnly_core.cc_desktop import books
from pearnly_erp_portal.express import push, target
out["recognition"] = bool(ocr.is_configured())
if COMPANY:
    linked = bool(push.linked(COMPANY))
    try:
        key = target.pick(push._company(COMPANY), DAY)["book"]["key"]
    except Exception as error:
        key = ""
        out["book"] = "refused: " + str(error)[:160]
    out["computer"] = {"linked": linked, "book": key, "reachers": (books.reachers([key]) or {}).get(key, []) if key else []}
else:
    out["computer"] = "no company given"
print(json.dumps(out, default=str))
'''


def check(lab, product: str, mode: str, *, connection: str = "", company: str = "", pages: int = 1,
          day: str = "2026-10-15", user: str = "") -> dict:
    """一个产品此刻齐不齐：``{"ok", "missing": [缺的项], "seen": {...}}``。``mode`` 是 fake / real（引擎该指哪里）。"""
    site = SITES[product]
    head = f"PAGES = {int(pages)}\nCONNECTION = {connection!r}\nCOMPANY = {company!r}\nDAY = {day!r}\n"
    if user:
        head += f"frappe.set_user({user!r})\n"
    seen = lab.python(site, head + COMMON + (COWORK if product == "cowork" else ERP))
    seen["processes"] = _processes(lab)
    want_base = AGENTS_URL if mode == "fake" else REAL_URL
    engine_seen = seen.get("engine") or {}
    missing = []
    if engine_seen.get("assistant_key") != ("fake" if mode == "fake" else "real") or want_base not in engine_seen.get("base_url", ""):
        missing.append("engine")
    if engine_seen.get("ocr_key") != ("fake" if mode == "fake" else "real") or engine_seen.get("ocr_engine") != "claude":
        missing.append("recognition_engine")
    if seen.get("capacity") is not True:
        missing.append("capacity")
    if not seen.get("ocr_queue"):
        missing.append("ocr_queue")
    if not all(seen["processes"].values()):
        missing.append("processes")
    if seen.get("billing") is not True:
        missing.append("billing")
    if product == "cowork":
        if seen.get("recognition") != "economy":
            missing.append("recognition")
        if seen.get("computer") != "ready":
            missing.append("computer")
    else:
        if seen.get("recognition") is not True:
            missing.append("recognition")
        computer = seen.get("computer") if isinstance(seen.get("computer"), dict) else {}
        if not (computer.get("linked") and computer.get("reachers")):
            missing.append("computer")
    return {"product": product, "mode": mode, "ok": not missing, "missing": missing, "seen": seen}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["fake", "real"], required=True)
    parser.add_argument("--product", choices=["cowork", "erp", "all"], default="all")
    parser.add_argument("--cowork-connection", default="")
    parser.add_argument("--erp-company", default="")
    parser.add_argument("--pages", type=int, default=28)
    args = parser.parse_args(argv)
    run = lab_module()
    lab = run.Lab()
    products = ["cowork", "erp"] if args.product == "all" else [args.product]
    found = [check(lab, product, args.mode, connection=args.cowork_connection, company=args.erp_company, pages=args.pages)
             for product in products]
    print(json.dumps(found, ensure_ascii=False, indent=1, default=str))
    return 0 if all(item["ok"] for item in found) else 1


if __name__ == "__main__":
    sys.exit(main())
