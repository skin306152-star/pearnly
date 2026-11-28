#!/usr/bin/env python3
"""流程用例（主会话 10-28 第 3 道闸）：按登记表 `tests/flows/registry.toml` 在回归实验室的两套固定数据上跑流程，断言页面结果与落库。

    python3 scripts/flows/run.py --all
    python3 scripts/flows/run.py --flow erp.stocktake.scan
    python3 scripts/flows/run.py --changed --base origin/main      # 只跑改动文件涉及的流程
    ... --out 结果.json --post-status                                # 结果作为提交状态 `pearnly/flows` 回写 GitHub
    python3 scripts/flows/run.py --rerun-failed 结果.json --out 合并.json --post-status   # 同一提交上只补跑失败格、并进上次结果

发布日：集成头还会变时只跑受影响的（`--changed --base <上一个头>`），头冻结后在发布提交上只跑一次 `--all --post-status`；
全套不带 `--post-status` 时开跑前与结束时各提醒一次「结果不登记、部署闸不认」（`unposted_note`）。
分支：`--changed --post-status` 只跑改动文件涉及的流程；部署前：在要部署的 main 提交上 `--all --post-status` 跑全套，
`scripts/production/deploy.sh` 只认全套通过（说明以 `all flows` 开头）。回写前核对实验室挂的就是本工作树的源码、产品代码没有未提交改动。
本机睡眠或实验室没起来时没有这个状态，部署闸照样拦；补跑方法见 `tests/flows/README.md`。
每条流程只靠 `datasets.py` 的固定数据和自己 `prepare` 备的数据，不靠排在前面的流程留下的；`--order reverse` 倒过来跑全套查这一点。
全套里个别格因环境不通过时 `--rerun-failed` 只重跑那几格，与上次结果合并后回写，说明里写明补跑了哪几格（范围仍是上次的）。

每个流程 × 每套数据（datasets.py new / old，先跑一遍保证数据齐，拿清单）× 每个宽度（desktop / mobile）：
建会话 → 流程模块 `prepare`（在站点里开单等）→ 无头 Chrome 按 `steps` 点页面（`scripts/regression/ui_steps.mjs`，手机宽度、假摄像头）
→ `judge`（页面上看到的）→ `verify`（库里落的）→ 页面不能有报错、站点 Error Log 不能多出前端回传。全部通过才算过。
实验室：`infrastructure/compose/regression-lab.yml`（站点由 `scripts/regression/lab_init.py` 建，数据由 `datasets.py` 填）。
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "scripts" / "production"))
import doctype_guard  # noqa: E402  迁移前后数 DocType（与生产激活同一份）
REGISTRY = ROOT / "tests" / "flows" / "registry.toml"
STATUS = "pearnly/flows"
# 实验栈名（docker compose -p）：每个窗口用自己的栈跑、回写同一个 pearnly/flows 状态，不抢一套（主会话 10-29）
LAB = os.environ.get("PEARNLY_FLOWS_LAB", "pearnly-regression-lab")
CONTAINER = os.environ.get("REGRESSION_LAB_CONTAINER", f"{LAB}-bench-1")
PORT = int(os.environ.get("REGRESSION_LAB_PORT", "18771"))
# 实验室 bench 起三个网页进程（8000 / 8001 / 8002，各是单进程多线程、受 GIL 限制，任一口都能开任一站点）。11-15 前所有格都连 8000，
# 并行的几路挤在一个进程里、另两个一直空着；现在每一路（一个站点组）占一个口，路数超过 3 才有两路共用（`port_slots`）
PORTS = [PORT, int(os.environ.get("REGRESSION_LAB_PORT_COWORK", "18772")), int(os.environ.get("REGRESSION_LAB_PORT_DMS", "18773"))]
_PORT = threading.local()


def lab_port():
    """这一路的浏览器连哪个网页口（没分配时同旧：REGRESSION_LAB_PORT）。"""
    return getattr(_PORT, "port", PORT)
# 改了站点配置后等多久网页进程才都看到新值：Frappe 16 网页进程的站点配置缓存 60 秒（见 `Lab.site_config`），多等 1 秒
SITE_CONFIG_SETTLE = 61
# 容器里的常驻 Python（11-15）：每格约 6 段 Python 不再各起一个进程（每次加载 frappe + 首连约 2.8 秒），见 `scripts/regression/lab_worker.py`。
# PEARNLY_FLOWS_LAB_WORKER=0 关掉、退回每段新起一个进程（对照用）
WORKER = "/workspace/scripts/regression/lab_worker.py"
WORKER_START_SECONDS = 180


class LabWorkerGone(RuntimeError):
    """常驻进程没了（被杀、崩溃、协议断了）：这一段照旧报错，这个线程之后退回每段新起一个进程。"""


class LabWorker:
    """一个线程一个：`docker exec -i` 起 `lab_worker.py`，一行 JSON 请求、一行 JSON 回复（回复由读线程放进队列，好按秒超时）。"""

    def __init__(self, command=None):
        import queue

        command = command or ["docker", "exec", "-i", "-w", "/home/frappe/frappe-bench/sites", CONTAINER, "../env/bin/python", WORKER]
        self.proc = subprocess.Popen(command,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     encoding="utf-8", start_new_session=True)
        self.replies = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        ready = self._next(WORKER_START_SECONDS)
        if not ready.get("ready"):
            self.close()
            raise LabWorkerGone(f"worker did not start: {ready}")

    def _read(self):
        for line in self.proc.stdout:
            if line.startswith("{"):
                self.replies.put(json.loads(line))
        self.replies.put(None)  # 输出断了 = 进程没了

    def _next(self, timeout):
        import queue

        try:
            reply = self.replies.get(timeout=timeout)
        except queue.Empty:
            raise subprocess.TimeoutExpired(WORKER, timeout) from None
        if reply is None:
            raise LabWorkerGone(f"worker exited ({self.proc.poll()})")
        return reply

    def call(self, request, timeout):
        try:
            self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            raise LabWorkerGone(f"worker stdin closed: {error}") from None
        return self._next(timeout)

    def close(self):
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        if self.proc.stdout:
            try:
                self.proc.stdout.close()
            except (OSError, ValueError):
                pass


def registry() -> list[dict]:
    return tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["flow"]


def merge_base(base: str) -> str:
    found = subprocess.run(["git", "-c", "core.quotepath=off", "merge-base", base, "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if not found:
        raise SystemExit(f"找不到 {base} 与 HEAD 的分叉点")
    return found


def changed_files(base: str) -> list[str]:
    out = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "--name-only", base, "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def covers(flow, path) -> bool:
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.replace("**/", ""))
               for pattern in flow.get("covers", []))


# 跑法本身（不是哪条流程的辅助）：改它们不因「引用」把流程选进来
RUNNER = ("run", "coverage")


def own_scripts(flow) -> set[str]:
    """流程自己的脚本，以及它经 `scripts/flows/` 同级模块引用到的全部脚本（cc_lib、erp_lib、desk_list…，也有引用别的流程的，
    层层引用都算）。照语法树里的 import 找，不靠 covers 手写（11-11 第 9 车：改了 cc_send_while_waiting.py，--changed 没选中它）。"""
    folder = ROOT / "scripts" / "flows"
    found, todo = set(), [ROOT / flow["module"]]
    while todo:
        path = todo.pop()
        name = path.relative_to(ROOT).as_posix()
        if name in found or not path.is_file():
            continue
        found.add(name)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=name)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            else:
                continue
            for module in names:
                top = module.split(".")[0]
                if top not in RUNNER and (folder / f"{top}.py").is_file():
                    todo.append(folder / f"{top}.py")
    return found


def product_map_flows(base, files=None) -> set[str]:
    """产品地图连带出的要跑的流程（`check-product-map.py` 的影响清单：入口文件、它调用的接口函数、共用件、读到的配置键）。
    给了 base 按相对它的分叉点算（接口按函数算）；没给 base 就按 files 整个文件算。"""
    spec = importlib.util.spec_from_file_location("check_product_map", ROOT / "scripts" / "check-product-map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.map_flows(base, root=ROOT) if base else module.map_flows(files=list(files or []), root=ROOT))


def changed_flows(flows, files, base=None) -> list[dict]:
    """改动涉及的流程，两套规则取并集（11-16：只用第一套，集成分支漏 22 条、第二列车漏 13 条，靠人工对照影响清单补跑）：
    ① registry 的 covers，加上流程自己的脚本与它引用到的流程辅助脚本（`own_scripts`）——改流程脚本、改到 covers 写的产品文件；
    ② 再加产品地图按连带算出的流程（同 `check-product-map.py` 的「要跑的流程」，相对 base 的分叉点）——
       改到页面调用的接口、共用件、配置键，covers 没写到的也选上。没给 base（模块里单独调用）就按 files 整个文件算地图，
       同样取并集（11-16 合入队列：单独调用只算了第一套，列车 E 比地图少 4 条）。
    --changed、补跑范围、部署闸认范围都用这一个函数。"""
    mapped = product_map_flows(base, files) if (base or files) else set()
    return [flow for flow in flows
            if any(covers(flow, path) for path in files) or own_scripts(flow) & set(files) or flow["id"] in mapped]


# 前端单页：源码目录 → 构建产物。`frontend/` 是 Frappe UI 单页（DMS 的 LINE 一次性票据编辑器），`frontend/assistant` 是 CC 对话页
SPA = [(ROOT / "frontend", ROOT / "apps/pearnly_core/pearnly_core/public/pearnly-app/index.html"),
       (ROOT / "frontend/assistant", ROOT / "apps/pearnly_core/pearnly_core/public/assistant/index.html")]
SPA_SOURCE = (".ts", ".tsx", ".vue", ".js", ".mjs", ".css", ".html", ".json")


def spa_fresh():
    """前端单页的构建产物不进 git、实时源码也盖不到：产物要在、且比源码新，否则页面测的不是这个提交
    （新工作树没建 `frontend/` 产物时 dms.booking.editor 只看到「找不到网页」，10-31）。"""
    nested = [source for source, _ in SPA]
    for source, built in SPA:
        others = [n for n in nested if n != source and source in n.parents]  # 嵌在里面的另一个单页各算各的
        newest = max((f.stat().st_mtime for f in source.rglob("*") if f.is_file() and "node_modules" not in f.parts
                      and f.suffix in SPA_SOURCE and not any(o in f.parents for o in others)), default=0)
        build = f"cd {source.relative_to(ROOT)} && npm ci && npm run build"
        if not built.exists():
            raise SystemExit(f"没有 {built.relative_to(ROOT)}（前端产物不进 git，新工作树要先构建）：{build}")
        if built.stat().st_mtime < newest:
            raise SystemExit(f"{built.relative_to(ROOT)} 比 {source.relative_to(ROOT)} 的源码旧：{build}")


def lab_billing_module():
    """建站脚本里的计费站点表（`lab_init.BILLED`，只用标准库，可以在本机导入）。"""
    spec = importlib.util.spec_from_file_location("lab_init", ROOT / "scripts/regression/lab_init.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lab_runs_this_tree():
    """回写状态前：实验室挂的产品源码就是本工作树，且产品代码没有未提交改动（结果才算这个提交的）。"""
    mounts = subprocess.run(["docker", "inspect", CONTAINER, "--format", "{{range .Mounts}}{{.Source}}\n{{end}}"],
                            capture_output=True, text=True).stdout.split()
    apps = [m for m in mounts if "/apps/" in m]
    if not apps or not all(m.startswith(str(ROOT / "apps") + "/") for m in apps):
        raise SystemExit(f"实验室挂的不是本工作树的源码（{apps[:1]}），用 PEARNLY_REGRESSION_SRC={ROOT} 重建实验室后再回写状态")
    dirty = product_dirty()
    if dirty:
        raise SystemExit("产品代码有未提交改动，结果不代表这个提交，不回写状态：\n" + dirty[:600])


class Lab:
    """回归实验室容器：在站点里跑一段 Python（已 init / connect，结尾 commit），回它最后一行打印的 JSON。"""

    def __init__(self, persistent=False):
        self.manifests = {}  # 每套数据一次运行只补齐一遍
        self._cell = threading.local()  # 并行时每个线程一次只跑一格：这一格临时改过的设置（`set_fields`）记在这里
        # 常驻 Python 只给 run.py 自己开（其余调用方照旧每段新起一个进程）；一个线程一个，崩了这个线程退回老路
        self.persistent = persistent and os.environ.get("PEARNLY_FLOWS_LAB_WORKER", "1") != "0"
        self._workers = threading.local()
        self._all_workers, self._workers_lock = [], threading.Lock()
        self.fallbacks = []  # (什么时候, 原因)：结果里写明哪些格退回了老路
        # 重启后用哪个站点探网页起来没有：GitHub 上每份只建自己那几个站点（`--shard`），没有基础站点 erp.regression.localhost
        self.ping_site = "erp.regression.localhost"

    def _worker(self):
        state = self._workers
        if not self.persistent or getattr(state, "off", False):
            return None
        if getattr(state, "worker", None) is None:
            try:
                state.worker = LabWorker()
            except (LabWorkerGone, subprocess.TimeoutExpired, OSError) as error:
                self._fall_back(f"start: {error}")
                return None
            with self._workers_lock:
                self._all_workers.append(state.worker)
        return state.worker

    def _fall_back(self, reason):
        self._workers.off, self._workers.worker = True, None
        self.fallbacks.append(reason)
        events = getattr(self._cell, "fallbacks", None)
        if events is not None:
            events.append(reason)

    def _python_call(self, request, argv, timeout=900):
        """容器里跑一段 Python：有常驻进程走它，没有（关了、起不来、崩过）照旧 `docker exec` 新起一个。回 CompletedProcess。"""
        worker = self._worker()
        if worker is None:
            return self.exec(*argv, timeout=timeout)
        try:
            reply = worker.call(request, timeout)
        except subprocess.TimeoutExpired:
            # 同老路：这一段超时就报超时；卡住的进程收掉，下一段再起一个新的
            self._workers.worker = None
            worker.close()
            raise subprocess.TimeoutExpired(argv, timeout) from None
        except LabWorkerGone as error:
            worker.close()
            self._fall_back(str(error))
            # 同老路里进程半路死掉：这一段算失败（不重跑，它可能已经写过库），之后这个线程每段新起一个进程
            return subprocess.CompletedProcess(argv, 1, "", f"lab worker gone: {error}")
        return subprocess.CompletedProcess(argv, reply["returncode"], reply["stdout"], reply["stderr"])

    def close_workers(self):
        with self._workers_lock:
            workers, self._all_workers = self._all_workers, []
        for worker in workers:
            worker.close()
        self._workers = threading.local()

    def cell_started(self):
        self._cell.fallbacks = []
        self._cell.mode = "per-call" if not self.persistent or getattr(self._workers, "off", False) else "persistent"

    def cell_mode(self):
        """这一格容器里的 Python 怎么跑的：persistent（常驻进程）/ per-call（每段新起）/ fallback: 原因（中途退回老路）。"""
        events = getattr(self._cell, "fallbacks", None) or []
        return f"fallback: {events[0][:200]}" if events else getattr(self._cell, "mode", "per-call")

    def exec(self, *args, timeout=900):
        return subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench/sites", CONTAINER, *args],
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def python(self, site, body):
        code = f"import frappe, json\nfrappe.init(site={site!r}, sites_path='.')\nfrappe.connect()\ntry:\n" + \
            "\n".join("    " + line for line in body.strip().splitlines()) + "\nfinally:\n    frappe.destroy()\n"
        result = self._python_call({"code": code}, ("../env/bin/python", "-c", code))
        lines = [line for line in result.stdout.splitlines() if line.startswith(("{", "["))]
        if result.returncode or not lines:
            raise RuntimeError(f"lab python failed on {site}: {result.stderr[-800:]}")
        return json.loads(lines[-1])

    def site_config(self, site, values):
        """流程临时改站点配置（site_config.json）一律走这里：写入 values（值为 None = 删掉这一项），回写之前的原值，
        等网页进程都看到新值才返回。还原也调这里、传回原值（放在流程的 cleanup 里，成功失败都还原）。

        Frappe 16 网页请求读站点配置走进程内缓存（`frappe/config.py`：`site_cache(ttl=60)`），每个网页进程各一份；
        这里在 bench 进程里改文件，网页进程最多 60 秒内还用旧值。11-09 `--jobs 3` 全套：上一格刚请求过同一站点，
        `cowork.operator.send-while-waiting` 写好识别配置马上挂附件，碰上旧缓存报「请先配置 Cowork 发票识别参数。」，单跑时站点空闲才不出。"""
        before = self.python(site, f"""
from frappe.installer import update_site_config
wanted = {values!r}
before = {{key: frappe.conf.get(key) for key in wanted}}
for key, value in wanted.items():
    update_site_config(key, "None" if value is None else value)
print(json.dumps(before))
""")
        time.sleep(SITE_CONFIG_SETTLE)
        return before

    def set_fields(self, site, doctype, name, values):
        """流程临时改公司等设置（ERP Company 的接 Express、公司类型、收银开关等）一律走这里：写入 values（不改 modified），
        回之前的原值，并记在这一格上；这一格结束时（成功、失败、步骤超时、准备中途出错）`run_flow` 统一还原成第一次改之前的值
        （`restore_fields`），流程自己不再写还原。同一格里可以多次改同一项（例如先关后开），还原只认最早的原值。

        11-09 全套：erp.purchase.create 页面步骤 900 秒超时，它写在 verify 里的还原没跑到，公司一直「没接 Express」，
        同一站点后面 6 个流程跟着失败。"""
        return self._record(site, doctype, name, list(values), values)

    def keep_fields(self, site, doctype, name, fields):
        """只记原值、不写：这一格要由页面或产品代码去改的设置（例如页面上改公司备注、同步 Express 写的同步状态），
        或流程在同一段代码里先改后还原的，先记在这一格上，格结束时同样还原（见 `set_fields`）。"""
        return self._record(site, doctype, name, list(fields), None)

    def _record(self, site, doctype, name, fields, values):
        before = self.python(site, f"""
fields, values = {fields!r}, {values!r}
row = frappe.db.get_value({doctype!r}, {name!r}, fields, as_dict=True)
if row is None:
    raise RuntimeError("数据集缺前置：没有 " + {doctype!r} + " " + {name!r} + "（scripts/regression/datasets.py）")
if values is not None:
    frappe.db.set_value({doctype!r}, {name!r}, values, update_modified=False)
    frappe.db.commit()
print(json.dumps({{key: row.get(key) for key in fields}}, default=str))
""")
        self.changed().append((site, doctype, name, before))
        return before

    def changed(self):
        """这一格到现在临时改过的设置：[(站点, DocType, 名称, 改之前的值)]，按改的先后。"""
        if not hasattr(self._cell, "changed"):
            self._cell.changed = []
        return self._cell.changed

    def restore_fields(self):
        """把这一格 `set_fields` 改过的设置还原成最早的原值（每条记录一次写回）；一条还原失败也接着还原其余的，最后报错。"""
        changed, self._cell.changed = self.changed(), []
        original = {}
        for site, doctype, name, before in changed:
            fields = original.setdefault((site, doctype, name), {})
            for field, value in before.items():
                fields.setdefault(field, value)
        failed = []
        for (site, doctype, name), values in original.items():
            try:
                self.python(site, f"""
frappe.db.set_value({doctype!r}, {name!r}, {values!r}, update_modified=False)
frappe.db.commit()
print("{{}}")
""")
            except Exception as error:  # noqa: BLE001 - 一条没还原上也要接着还原其余的
                failed.append(f"{doctype} {name}: {error}")
        if failed:
            raise RuntimeError("restore_fields failed: " + "; ".join(failed)[:600])

    def dataset(self, which, product="erp"):
        """这套数据的清单（每个产品各两套：`datasets.py new|old [erp|cowork]`）。"""
        key = (product, which)
        if key in self.manifests:
            return self.manifests[key]
        result = self.exec("../env/bin/python", "/workspace/scripts/regression/datasets.py", which, product)
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        if result.returncode or not lines:
            raise RuntimeError(f"dataset {product} {which} failed: {result.stderr[-800:]}")
        self.manifests[key] = json.loads(lines[-1])
        return self.manifests[key]

    def session(self, site, user):
        out = self._python_call({"script": "/workspace/scripts/smoke/session.py", "argv": [site, user]},
                                ("../env/bin/python", "/workspace/scripts/smoke/session.py", site, user)).stdout
        return next((line.split()[1] for line in out.splitlines() if line.startswith("SID ")), "")

    def drop(self, site, user, sid):
        self._python_call({"script": "/workspace/scripts/smoke/session.py", "argv": [site, user, "drop", sid]},
                          ("../env/bin/python", "/workspace/scripts/smoke/session.py", site, user, "drop", sid))

    def restart(self):
        """重启 bench 容器再等网页起来：网页进程用 --noreload 起，已导入的模块一直留在内存里，换了源码不重启就是新旧混跑
        （10-30 变基后 terminals.py 新、warehouses 还是旧的，报找不到 main_of）。常驻 Python 同理先收掉，下一段按新源码再起。"""
        self.close_workers()
        subprocess.run(["docker", "restart", CONTAINER], capture_output=True, check=True, timeout=300)
        for _ in range(120):
            ping = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-H", f"Host: {self.ping_site}",
                                   f"http://127.0.0.1:{PORT}/api/method/ping"], capture_output=True, text=True)
            if ping.stdout == "200":
                return
            time.sleep(2)
        raise SystemExit("实验室网页重启后没起来")

    def bench(self, *args, timeout=3600):
        return subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench", CONTAINER, *args],
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def migrate(self):
        """全部站点逐个迁移到挂上来的源码（表结构、补丁、hooks）：站点比源码旧时流程结果不代表这个提交。
        每站迁移前后各数一次 DocType，变少就停、不跑流程不回写（同生产 activate-product.sh，`scripts/production/doctype_guard.py`）：
        10-31 本机实验栈迁移时一个控制器导入失败，Frappe 把一批 DocType 当孤儿删了 75 个，迁移本身照样成功退出。"""
        guard = "/workspace/scripts/production/doctype_guard.py"
        listed = self.exec("sh", "-c", 'for d in */; do [ -f "$d/site_config.json" ] && echo "${d%/}"; done').stdout.split()
        for site in [name for name in listed if "." in name]:
            counted = []
            for step in ("before", "migrate", "after"):
                result = self.bench("bench", "--site", site, "migrate") if step == "migrate" else \
                    self.bench("env/bin/python", guard, "snapshot", site, timeout=300)
                if result.returncode:
                    # stderr、stdout 都带上（bench 把错误打在 stdout）；137 = 进程被杀，多半是 bench 容器被重启了
                    killed = "；bench 容器在这期间被重启（多半是另一个 run.py 在用同一套实验栈）" if result.returncode == 137 else ""
                    raise SystemExit(f"实验室站点 {site} {'迁移' if step == 'migrate' else '数 DocType'}失败（退出码 {result.returncode}"
                                     f"{killed}）：{(result.stderr or '')[-800:]}{(result.stdout or '')[-800:]}")
                if step != "migrate":
                    counted.append(doctype_guard.parse(result.stdout, f"{site} {step}"))
            lost = doctype_guard.lost(*counted, doctype_guard.allowed_removals())
            if lost:
                raise SystemExit(f"实验室站点 {site} 迁移后少了 {len(lost)} 个 DocType（{len(counted[0])} → {len(counted[1])}），"
                                 f"停下、不跑流程不回写：{', '.join(lost)}")

    def unbilled(self):
        """实验栈里已建、却没接上实验室计费的站点（首页报「计费服务暂不可用」、导出按钮不出，结果都是假的不通过）。"""
        init = lab_billing_module()
        sites = [site for _, _, site in init.BILLED]
        code = ("import json, pathlib\n"
                f"sites = {sites!r}\n"
                "configs = {s: pathlib.Path(s, 'site_config.json') for s in sites}\n"
                "print(json.dumps([s for s, c in configs.items() if c.exists() and not json.loads(c.read_text()).get('pearnly_billing_token')]))")
        result = self.exec("../env/bin/python", "-c", code, timeout=120)
        if result.returncode:
            raise SystemExit(f"读不到实验室站点配置（{CONTAINER} 起来了吗）：{result.stderr[-400:]}")
        return json.loads(result.stdout.strip().splitlines()[-1])

    def sweep(self, site, start_print):
        """这一格新建的行和文件删掉（`SWEEP_CODE`），回 {swept: {表: 行数}, kept: {行: 原因}, forced: {行: 产品拒删的原因}, rebuilt, files}。"""
        files = sorted(key for key in start_print if key.startswith("file: "))
        counts = {key[6:]: n for key, n in start_print.items() if key.startswith("rows: ")}
        return self.python(site, SWEEP_CODE.format(since=start_print["_now"], db_since=start_print.get("_db_now"), counts=counts,
                                                   roles=start_print.get("_roles"), before_files=files, extra=FOOTPRINT_EXTRA,
                                                   skip=FOOTPRINT_SKIP_DIRS))

    def footprint(self, site):
        """这个站点里后面的流程看得到的那部分（`FOOTPRINT_CODE`）：本仓 App 的单据表与 Single、用户与岗位的行数，站点私有目录
        （账套读回的资料等，不含上传原件与备份）每个文件的摘要。一格跑前跑后各取一次，不一样就是这条流程留下了数据。"""
        return self.python(site, FOOTPRINT_CODE)

    def client_errors(self, site):
        return self.python(site, 'print(json.dumps({"n": frappe.db.count("Error Log", {"method": ["like", "Browser error%"]})}))')["n"]

    def server_errors(self, site, since=None):
        """站点此刻的时间（``since`` 不给时）或 ``since`` 之后服务端记下的 Error Log 标题（前端回传的不算，那一路另有断言）。
        只记录不判：同一站点的格依次跑，这段时间里记下的就是这一格的（11-23 Flows cc.operator 手机格：开始前卡片不出，CI 只留
        bench 日志最后 300 行，服务端那一侧没有证据）。"""
        if since is None:
            return self.python(site, 'print(json.dumps({"at": str(frappe.utils.now_datetime())}))')["at"]
        return self.python(site, f"""
rows = frappe.get_all("Error Log", filters={{"creation": [">=", {since!r}], "method": ["not like", "Browser error%"]}},
                      pluck="method", order_by="creation asc", limit_page_length=50)
print(json.dumps({{"titles": rows}}))""")["titles"]


# 流程结束要和开始一样的那部分（11-16：收款流程在账套原来没有资料时留下一份只有收款类型的资料，同一实验室后面的
# cc.operator 新开户格开始卡没有目标、两格必挂；清卷重建才过）。日志类（名字以 Log 结尾）不算：记录本身就是要留的痕迹。
FOOTPRINT_EXTRA = ("User", "Has Role", "User Permission", "File")
FOOTPRINT_SKIP_DIRS = ("files", "backups")
FOOTPRINT_CODE = f"""
import hashlib, os
modules = frappe.get_all("Module Def", filters={{"app_name": ["like", "pearnly%"]}}, pluck="name")
rows = frappe.get_all("DocType", filters={{"module": ["in", modules], "istable": 0, "is_virtual": 0}}, fields=["name", "issingle"])
tables = sorted({{row.name for row in rows if not row.issingle and not row.name.endswith(" Log")}} | set({FOOTPRINT_EXTRA!r}))
singles = sorted(row.name for row in rows if row.issingle)
out = {{}}
if tables:
    sql = " UNION ALL ".join("SELECT %s, COUNT(*) FROM `tab" + t.replace("`", "") + "`" for t in tables)
    out.update({{"rows: " + t: n for t, n in frappe.db.sql(sql, tables)}})
for name, value in frappe.db.sql("SELECT doctype, GROUP_CONCAT(CONCAT(field, '=', IFNULL(value, '')) ORDER BY field SEPARATOR '\\n') "
                                 "FROM tabSingles WHERE doctype IN %s GROUP BY doctype", [tuple(singles) or ("",)]):
    out["settings: " + name] = hashlib.sha1((value or "").encode()).hexdigest()[:12]
root = frappe.get_site_path("private")
for folder, dirs, files in os.walk(root):
    if folder == root:
        dirs[:] = [d for d in dirs if d not in {FOOTPRINT_SKIP_DIRS!r}]
    for name in files:
        path = os.path.join(folder, name)
        with open(path, "rb") as handle:
            out["file: " + os.path.relpath(path, root)] = hashlib.sha1(handle.read()).hexdigest()[:12]
out["_now"] = str(frappe.utils.now_datetime())
out["_db_now"] = str(frappe.db.sql("SELECT NOW(6)")[0][0])  # 产品有用 SQL NOW() 写 creation 的（数据库时区，与站点时区可能差几小时）
out["_roles"] = sorted(p + "|" + r for p, r in frappe.db.sql("SELECT parent, role FROM `tabHas Role` WHERE parenttype='User'"))
print(json.dumps(out))
"""

# 一格结束后的通用收尾（11-16 列车 D：79 / 137 格跑完和跑前不一样，前一格的单、公司、权限被后面的格看到）：
# 本格期间新建的行（守护数的那些表，creation 不早于这一格开始）按新到旧用框架的 delete_doc 删——查链接（原有数据还连着它的不删、
# 照样列进 left_data）、连子表和附件一起删；产品自己的 on_trash 拒删的（已作废的单据等，流程已冲回库存）跳过 on_trash 再删一次。
# 删掉的库存流水所在的「商品 + 仓库 + 仓位」原来就有结存的，用产品的 rebuild_balance 按剩下的流水重算。
# private 下这格新出现的文件（守护的文件清单里跑前没有的）删掉；跑前就有、被改了的文件内容没法还原，照样列出。
SWEEP_CODE = """
import os, re, time, urllib.parse
since, db_since, counts, roles, before_files = {since!r}, {db_since!r}, {counts!r}, {roles!r}, set({before_files!r})
site = frappe.local.site
# 先等这个站点排着、在跑的后台任务做完（最多 60 秒）：任务在收尾之后才建的行会漏到下一格（11-16 GitHub 全套：导出、AI 记账）
from frappe.utils.background_jobs import get_queues
from rq.job import Job
from rq.registry import StartedJobRegistry
def busy():
    n = 0
    for q in get_queues():
        ids = list(q.job_ids) + list(StartedJobRegistry(queue=q).get_job_ids())
        for job in Job.fetch_many(ids, connection=q.connection):
            if job and (job.kwargs or {{}}).get("site") == site:
                n += 1
    return n
waited = 0
while waited < 60 and busy():
    time.sleep(1)
    waited += 1
modules = frappe.get_all("Module Def", filters={{"app_name": ["like", "pearnly%"]}}, pluck="name")
rows = frappe.get_all("DocType", filters={{"module": ["in", modules], "istable": 0, "is_virtual": 0, "issingle": 0}}, pluck="name")
tables = sorted({{t for t in rows if not t.endswith(" Log")}} | set({extra!r}) - {{"Has Role"}})
frappe.set_user("Administrator")
made = []
for t in tables:
    mine = frappe.get_all(t, filters={{"creation": [">=", since]}}, fields=["name", "creation"], limit_page_length=0)
    grown = frappe.db.count(t) - counts.get(t, 0)
    if db_since and db_since[:16] != since[:16] and grown > len(mine):
        # 产品用 SQL NOW() 写的行，creation 是数据库时钟（11-16 GitHub：Cowork Recon View 缓存，库是 UTC、站点是曼谷时间）：
        # 只补这张表比跑前多出来、站点时钟又没认到的那几行，按数据库时钟从新到旧取，不会多删原有的行
        seen = {{r.name for r in mine}}
        extra_rows = frappe.get_all(t, filters={{"creation": [">=", db_since]}}, fields=["name", "creation"], order_by="creation desc",
                                    limit_page_length=grown - len(mine) + len(seen))
        mine += [r for r in extra_rows if r.name not in seen][:grown - len(mine)]
    made += [(r.creation, t, r.name) for r in mine]
made.sort(key=lambda r: str(r[0]), reverse=True)
stock = set()
if frappe.db.exists("DocType", "ERP Stock Movement"):
    for name in [n for _, t, n in made if t == "ERP Stock Movement"]:
        # 库存只到「物料 × 仓库」（v1_33_warehouse_only_stock，11-19）：流水、结存表上已没有仓位列
        row = frappe.db.get_value("ERP Stock Movement", name, ["product", "warehouse"], as_dict=True)
        if row:
            stock.add((row.product, row.warehouse))
# 每删一行框架都排一个 delete_dynamic_links 后台任务；一格删上千行（开户默认分类等）会把队列撑满（QueueOverloaded），
# 收尾这段改成当场做，结束还原
enqueue = frappe.enqueue
def inline(method, *args, **kwargs):
    if method == "frappe.model.delete_doc.delete_dynamic_links":
        from frappe.model.delete_doc import delete_dynamic_links
        return delete_dynamic_links(kwargs["doctype"], kwargs["name"])
    return enqueue(method, *args, **kwargs)
def raw_delete(t, name):
    # 最后一档：产品在 on_trash 以外（附件的 File 钩子、权限钩子等）拒删的测试数据，直接删本行与子表；本行的附件 File 是这一格建的，下一轮按 File 删
    for df in frappe.get_meta(t).get_table_fields():
        frappe.db.delete(df.options, {{"parent": name, "parenttype": t}})
    frappe.db.delete(t, {{"name": name}})
swept, kept, forced = {{}}, {{}}, {{}}
frappe.enqueue = inline
try:
    pending, stalled = list(made), False
    names = {{name for _, _, name in made}}
    def linked_to_mine(error):
        # 「… is linked with <DocType> <a href=".../<name>">」：链着它的是这一格自己建的行（两张单互相链着）才强删
        found = re.findall(r'is linked with [^<]*<a href="[^"]*/([^/"]+)"', error)
        return bool(found) and all(urllib.parse.unquote(n) in names for n in found)
    def attempt_row(t, name, tiers):
        # normal → skip on_trash → linked（只在被这一格自己的行链着时：force 跳过链接检查）/ raw（只在拒删与链接无关时）
        error, link = None, None
        for tier in tiers:
            if tier == "linked" and not (link and linked_to_mine(link)):
                continue
            if tier == "raw" and link:
                break  # 有行链着它：不绕过链接检查直接删表
            try:
                if tier == "raw":
                    raw_delete(t, name)
                else:
                    frappe.delete_doc(t, name, ignore_permissions=True, ignore_on_trash=tier != "normal", delete_permanently=True,
                                      force=tier == "linked")
                frappe.db.commit()
                swept[t] = swept.get(t, 0) + 1
                if tier != "normal":
                    forced[t + " " + name] = tier + ": " + str(error)[:200]  # 产品拒删、绕过才删掉的：照样列出，不悄悄吞掉
                return None
            except frappe.LinkExistsError as e:
                frappe.db.rollback()
                error = link = "linked: " + str(e)[:400]
            except Exception as e:  # noqa: BLE001
                frappe.db.rollback()
                error = type(e).__name__ + ": " + str(e)[:160]
                if tier == "linked":
                    break
        return error
    # 按新到旧删；前一轮因为被这一格别的行链着删不掉的，下一轮再试（附件 File 比单据新、单据又链着它）；
    # 一轮什么都没删掉（或到第 6 轮）做最后一轮：互相链着的强删、on_trash 以外拒删的 raw；最后一轮放出来的行（raw 删掉单据后它的附件）再补一轮
    rounds = []
    for attempt in range(8):
        final = stalled or attempt >= 5
        tiers = ("normal", "skip on_trash", "linked", "raw") if final and "final" not in rounds else ("normal", "skip on_trash")
        rounds.append("final" if len(tiers) > 2 else "normal")
        progress, left = 0, []
        for row in pending:
            _, t, name = row
            if not frappe.db.exists(t, name):
                kept.pop(t + " " + name, None)
                continue  # 跟着父行一起删掉了
            error = attempt_row(t, name, tiers)
            if error:
                left.append(row)
                kept[t + " " + name] = error[:200]
            else:
                kept.pop(t + " " + name, None)
                progress += 1
        pending = left
        if not pending or (rounds[-1] == "normal" and "final" in rounds and not progress):
            break
        stalled = not progress
    # 这一格给原有用户加的岗位（Has Role 是 User 的子表，不在上面；跟用户一起存，creation 不可靠）：比跑前的清单，多出来的删掉
    if roles is not None:
        before_roles = set(roles)
        for role in frappe.get_all("Has Role", filters={{"parenttype": "User"}}, fields=["name", "parent", "role"], limit_page_length=0):
            if role.parent + "|" + role.role not in before_roles and frappe.db.exists("User", role.parent):
                frappe.db.delete("Has Role", {{"name": role.name}})
                frappe.clear_cache(user=role.parent)
                swept["Has Role"] = swept.get("Has Role", 0) + 1
    frappe.db.commit()
finally:
    frappe.enqueue = enqueue
rebuilt = 0
if stock:
    from pearnly_erp_portal.inventory import stock as inventory
    from pearnly_erp_portal.masters import company as companies
    was = companies.active_company()
    for product, warehouse in sorted(stock):
        if not frappe.db.exists("ERP Stock Balance", {{"product": product, "warehouse": warehouse}}):
            continue  # 这一格新建的结存行，已经删掉
        company = frappe.db.get_value("ERP Warehouse", warehouse, "company")
        if not company:
            continue
        try:
            companies.set_active_company(company)  # 仓库按当前公司认（warehouses.required）：别家公司的仓要先切过去
            inventory.rebuild_balance(product, warehouse)
            frappe.db.commit()
            rebuilt += 1
        except Exception as e:  # noqa: BLE001
            frappe.db.rollback()
            kept["rebuild " + product + " " + warehouse] = type(e).__name__ + ": " + str(e)[:160]
    if was:
        companies.set_active_company(was)
        frappe.db.commit()
root = frappe.get_site_path("private")
removed = 0
for folder, dirs, files in os.walk(root):
    if folder == root:
        dirs[:] = [d for d in dirs if d not in {skip!r}]
    for name in files:
        path = os.path.join(folder, name)
        if "file: " + os.path.relpath(path, root) not in before_files:
            os.remove(path)
            removed += 1
print(json.dumps({{"swept": swept, "kept": kept, "forced": forced, "rebuilt": rebuilt, "files": removed, "waited": waited}}))
"""

# 登记过的「跑完不一样也不算留数据」：写明为什么不影响后面的格
FOOTPRINT_ALLOWED = {
    "settings: Pearnly Assistant Push Key": "助手推送密钥在站点第一次用到时生成一次，之后不再变（每个站点只在第一格出现一次）",
}


def footprint_diff(before, after):
    """跑前跑后不一样的项：行数写增减，设置与文件写「改了 / 新增 / 删了」。"""
    changed = {}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new or key.startswith("_") or key in FOOTPRINT_ALLOWED:
            continue
        if key.startswith("rows: "):
            changed[key] = f"{(new or 0) - (old or 0):+d}"
        else:
            changed[key] = "新增" if old is None else "删了" if new is None else "改了"
    return changed


def load_module(path):
    # 流程模块可以用同目录的共用片段（`desk_list.py`）
    if str(ROOT / "scripts" / "flows") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts" / "flows"))
    spec = importlib.util.spec_from_file_location(Path(path).stem, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 设了就把流程步骤里标了 shot 的截图存到这里（每格一个子目录：流程-数据集-宽度），人工核对截图用；不设不截
SHOTS = os.environ.get("PEARNLY_FLOWS_SHOTS", "")


class StepsTimeout(RuntimeError):
    """整格超时：``steps`` 是超时前已跑完的各步结果（驱动逐步打印的那些行），随失败格一起记下，查卡在哪不用重跑。"""
    steps: list = []


def drive(site, sid, start, steps, mobile, video, ready=None, preload=None, shots=""):
    job = {"base": f"http://{site}:{lab_port()}", "cookie_domain": site, "sid": sid, "start": start, "mobile": mobile,
           "video": video or "", "steps": steps, **({"ready": ready} if ready else {}), **({"preload": preload} if preload else {}),
           **({"shots": shots} if shots else {})}
    env = dict(os.environ, SMOKE_CHROME_RULES=f"MAP {site} 127.0.0.1")
    # 步骤放文件里传（`@文件`）：Linux 单个命令行参数最长 128 KB，步骤多的流程直接传会 Argument list too long（11-16）
    with tempfile.NamedTemporaryFile("w", suffix=".json", prefix="flow-job-", delete=False, encoding="utf-8") as handle:
        json.dump(job, handle)
    # 自成进程组：超时连同它拉起的无头 Chrome 一起结束，不留残留进程
    proc = subprocess.Popen(["node", str(ROOT / "scripts/regression/ui_steps.mjs"), "@" + handle.name], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        # 说出卡在哪一步（驱动每步开始前打一行 progress）：只报「超时」查不了根因（11-12）
        lines = [json.loads(line) for line in (out or "").splitlines() if line.startswith("{")]
        stuck = [line["progress"] for line in lines if "progress" in line]
        error = StepsTimeout(f"page steps timed out after 900s{f' in step {stuck[-1]!r}' if stuck else ' before the first step'}")
        error.steps = [line for line in lines if "progress" not in line]  # 已跑完的各步结果照样写进这一格（11-13）
        raise error
    finally:
        os.unlink(handle.name)
    return [json.loads(line) for line in out.splitlines() if line.startswith("{") and not line.startswith('{"progress"')]


_VIDEO_LOCK = threading.Lock()  # 并行时两格可能要同一个码的视频：只生成一次


def barcode_video(code):
    spec = importlib.util.spec_from_file_location("make_barcode_video", ROOT / "scripts/smoke/make_barcode_video.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # 码值或 `scripts/smoke/codes/` 下的一张码图（Data Matrix 等，10-30）：码图按文件名起名、按仓库路径读
    picture = str(code).endswith(".png")
    path = Path(tempfile.gettempdir()) / f"flow-{Path(code).name if picture else code}.y4m"
    with _VIDEO_LOCK:
        if not path.exists():
            module.write(str(path), str(ROOT / code) if picture else code)
    return str(path)


def run_flow(lab, flow, dataset, viewport):
    module = load_module(flow["module"])
    product = flow.get("product", "erp")
    manifest = lab.dataset(dataset, product)
    site, user = manifest["site"], f"{flow.get('user', 'admin')}@{product}.regression"
    before = {"client": lab.client_errors(site), "at": lab.server_errors(site)}
    lab.changed().clear()
    prepared = []
    try:
        params = module.prepare(lab, manifest)
        prepared.append(params)
        return _run_prepared(lab, flow, module, manifest, dataset, viewport, site, user, before, params, prepared)
    finally:
        try:
            # 流程临时改了站点数据的（例如把一张单挪进这个月），声明 cleanup：成功、失败、步骤本身出错都还原（11-05 cc.operator）。
            # 后准备的先收（相机那一遍的准备在第一遍之后，它记的「原值」是第一遍改过的）
            cleanup = getattr(module, "cleanup", None)
            if cleanup:
                for one in reversed(prepared):
                    cleanup(lab, manifest, one)
        finally:
            # 临时改的公司等设置（`Lab.set_fields`）：准备中途出错、步骤超时也还原，不带给同一站点的下一格
            lab.restore_fields()


def _run_prepared(lab, flow, module, manifest, dataset, viewport, site, user, before, params, prepared):
    started = time.monotonic()
    runs = [(None, None)]
    camera = flow.get("camera") and next((c for c in params.get("cases", []) if c["key"] == "by_barcode"), None)
    if camera and viewport == "mobile":
        params_camera = module.prepare(lab, manifest)
        prepared.append(params_camera)
        # 相机那一遍读的是它自己那份准备里的码（每次准备都换新码的流程，例如盘点当场新建物料）
        runs.append((params_camera, next(c for c in params_camera["cases"] if c["key"] == "by_barcode")["scan"]))
    checks, page_errors, below, icons, hscroll = [], [], set(), set(), set()
    for run_params, code in runs:
        run_params = run_params or params
        sid = lab.session(site, user)
        try:
            start = flow["start"].format(**run_params)
            shots = ""
            if SHOTS:
                shots = os.path.join(SHOTS, f"{flow['id']}-{dataset}-{viewport}")
                os.makedirs(shots, exist_ok=True)
            results = drive(site, sid, start, module.steps(run_params, code), viewport == "mobile",
                            barcode_video(code) if code else None, getattr(module, "READY", None),
                            getattr(module, "PRELOAD", None), shots)
        finally:
            lab.drop(site, user, sid)
        # 页面报错、步骤本身出错、步骤自己判出的错（返回 {error}）都算
        page_errors += [e for r in results for e in (r.get("page_errors") or []) + ([r["error"]] if r.get("error") else [])
                        + ([f"{r.get('step')}: {r['value']['error']}"] if isinstance(r.get("value"), dict) and r["value"].get("error") else [])]
        below |= {item for r in results for item in r.get("field_help") or []}
        icons |= {item for r in results for item in r.get("field_icons") or []}
        hscroll |= {f"{r.get('step')}: {item}" for r in results for item in r.get("hscroll") or []}
        label = "camera" if code else "typed"
        checks += [dict(c, run=label) for c in module.judge(run_params, results, code)]
        checks += [dict(c, run=label, saved_check=True) for c in module.verify(lab, manifest, run_params, code)]
    after = lab.client_errors(site)
    checks.append({"case": "page errors", "seen": page_errors, "ok": not page_errors})
    # ERP / Cowork 字段说明要在标题旁的说明图标里（11-02），还写在字段下方的就是漏了（登记的例外不算，见 `ui_steps.mjs`）
    checks.append({"case": "field descriptions below fields", "seen": sorted(below), "ok": not below})
    checks.append({"case": "field descriptions in the info icon (record only)", "seen": sorted(icons), "ok": True})
    # CC 手机页不许横向滚动（`ui_steps.mjs` 每一步量根节点与半屏以上的滚动区；电脑宽度不量）
    checks.append({"case": "no horizontal scroll on the CC phone page", "seen": sorted(hscroll), "ok": not hscroll})
    checks.append({"case": "client errors in Error Log", "seen": after - before["client"], "ok": after == before["client"]})
    checks.append({"case": "server errors in Error Log (record only)", "seen": lab.server_errors(site, before["at"]), "ok": True})
    return {"flow": flow["id"], "dataset": dataset, "viewport": viewport, "ok": all(c["ok"] for c in checks),
            "seconds": round(time.monotonic() - started, 1), "checks": checks}


def port_slots(jobs):
    """每一路拿一个网页口：前 3 路各占一个进程，再多的轮着共用（队列里同一个口放多份）。"""
    import queue

    slots = queue.Queue()
    for index in range(max(1, jobs)):
        slots.put(PORTS[index % len(PORTS)])
    return slots


def lab_lock(folder=None):
    """同一套实验栈（`PEARNLY_FLOWS_LAB`）同一时刻只让一个 run.py 用：每次开头都重启 bench 容器再迁移，两个同时跑会互相把对方
    正在跑的迁移、流程杀掉（11-12 两个窗口同时补跑同一套栈，报「迁移失败」却没有原因）。拿不到锁就停，说是谁占着。
    锁随进程退出自动放掉，不留残余。
    用的是占用锁库（`scripts/acceptance/locks.py`，资源名 `lab:<栈名>`，锁文件仍是 `~/.cache/pearnly-flows/<栈名>.lock`）：
    别的窗口对这套栈的约期占用（人工起栈调试、真机联调）这里也认，不只认另一个 run.py。返回锁文件的句柄，关掉即放锁。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("acceptance_locks", ROOT / "scripts" / "acceptance" / "locks.py")
    locks = sys.modules.get("acceptance_locks") or importlib.util.module_from_spec(spec)
    if "acceptance_locks" not in sys.modules:
        sys.modules["acceptance_locks"] = locks
        spec.loader.exec_module(locks)
    # 锁目录固定在用户目录（各窗口的 TMPDIR 不同，放临时目录锁不住别的窗口）；folder 只给单测用
    before = os.environ.get("PEARNLY_LOCK_DIR")
    if folder:
        os.environ["PEARNLY_LOCK_DIR"] = str(folder)
    try:
        held = locks.hold(f"lab:{LAB}", f"run.py（进程 {os.getpid()}）", "流程用例")
        try:
            held.__enter__()
        except locks.Busy as busy:
            record = busy.record
            who = f"另一个 run.py（进程 {record.get('pid', '?')}）" if record.get("mode") != "lease" else f"{record.get('holder')}（约期占用：{record.get('purpose', '')}）"
            raise SystemExit(f"{who}正在用实验栈 {LAB}：等它用完再跑，或换一套实验栈（PEARNLY_FLOWS_LAB）")
    finally:
        if folder:
            if before is None:
                os.environ.pop("PEARNLY_LOCK_DIR", None)
            else:
                os.environ["PEARNLY_LOCK_DIR"] = before
    # 交回文件句柄本身：关掉它或它被回收（main 返回、进程退出）就放锁，和原来一样；不留环形引用，否则同一进程里再跑一次会被自己挡住
    return held.handle


def head():
    return subprocess.run(["git", "-c", "core.quotepath=off", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def product_dirty():
    return subprocess.run(["git", "-c", "core.quotepath=off", "status", "--porcelain", "apps", "frontend"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def post_status(ok, summary):
    """回写 GitHub 提交状态 `flows`；说明里带比较基线，部署闸据此确认覆盖到上次上线以来的全部改动。"""
    sha = head()
    description = summary if len(summary) <= 140 else summary[:139] + "…"
    subprocess.run(["gh", "api", f"repos/{{owner}}/{{repo}}/statuses/{sha}", "-f", f"state={'success' if ok else 'failure'}",
                    "-f", f"context={STATUS}", "-f", f"description={description}"], cwd=ROOT, check=True, capture_output=True)
    return sha


UNPOSTED = ("注意：这一轮是全套但没带 --post-status，结果不会登记到这个提交，部署闸（scripts/production/deploy.sh）不认。"
            "发布用：头冻结后在发布提交上跑一次 run.py --all --post-status；头还会变时只跑受影响的 run.py --changed --base <上一个头>。")


def unposted_note(scope: str, post: bool) -> str:
    """全套（含补跑全套）不回写状态时的提醒；其余范围、已带 --post-status 的不提醒。11-15 发布日：在还会变的头上分段跑了
    210 格全套、没带 --post-status，部署闸不认，头冻结后又整轮重跑。只提醒不拦：调试时跑全套看结果是正常用法。"""
    return UNPOSTED if scope.startswith("all flows") and not post else ""


def expand(flows, dataset=None, viewport=None):
    """流程 × 数据集 × 宽度 = 格。"""
    return [(flow, d, v) for flow in flows
            for d in [d for d in flow.get("datasets", ["new", "old"]) if not dataset or d == dataset]
            for v in [v for v in flow.get("viewports", ["desktop", "mobile"]) if not viewport or v == viewport]]


def key(cell):
    return (cell["flow"], cell["dataset"], cell["viewport"]) if isinstance(cell, dict) else (cell[0]["id"], cell[1], cell[2])


def scope_flows(flows, scope):
    """范围说明对应的流程：`all flows` 全部、`base <分叉点>` 改动涉及的；部分范围（--flow / --dataset / --viewport）认不出，回 None。"""
    if scope == "all flows":
        return flows
    if scope.startswith("base ") and " " not in scope[5:]:
        files = changed_files(scope[5:])
        return changed_flows(flows, files, scope[5:])
    return None


def previous_failures(path, flows, post):
    """`--rerun-failed`：上次结果必须是同一提交、产品代码干净时跑的，格与登记表对得上；回 (上次结果, 要补跑的格)。"""
    previous = json.loads(Path(path).read_text(encoding="utf-8"))
    sha = head()
    if previous.get("sha") != sha:
        raise SystemExit(f"上次结果是 {(previous.get('sha') or '没记提交')[:12]} 上跑的，当前是 {sha[:12]}：只能和同一提交的结果合并，换了提交要跑全套")
    if previous.get("dirty"):
        raise SystemExit("上次结果跑的时候产品代码有未提交改动，不代表这个提交，不能合并")
    expected = scope_flows(flows, previous["scope"])
    if expected is None and post:
        raise SystemExit(f"上次结果是部分范围（{previous['scope']}），合并后也不能代表这个提交，不回写状态")
    if expected is not None and sorted(map(key, expand(expected))) != sorted(map(key, previous["results"])):
        raise SystemExit("上次结果的格与登记表对不上（登记表改过、或上次不是完整跑完），要重跑这个范围")
    by_id = {f["id"]: f for f in flows}
    return previous, [(by_id[r["flow"]], r["dataset"], r["viewport"]) for r in previous["results"] if not r["ok"]]


def report_leftovers(results):
    """结束汇总后列出留下了数据的格（left_data）与收尾时产品拒删 / 收尾出错的格（swept）。本机一次跑完和 GitHub 分份汇总（`--merge`）共用。"""
    left = [r for r in results if r.get("left_data")]
    if left:
        print(f"留下了数据的格 {len(left)} 个（跑完和跑前不一样；各格的 left_data 字段）：", flush=True)
        for r in left:
            print(f"  {r['flow']} [{r['dataset']} / {r['viewport']}] {json.dumps(r['left_data'], ensure_ascii=False)[:400]}", flush=True)
    forced = [r for r in results if (r.get("swept") or {}).get("forced") or (r.get("swept") or {}).get("error")]
    if forced:
        print(f"收尾时产品拒删、跳过 on_trash 才删掉，或收尾本身出错的格 {len(forced)} 个（各格的 swept 字段）：", flush=True)
        for r in forced:
            what = r["swept"].get("forced") or {"error": r["swept"].get("error")}
            print(f"  {r['flow']} [{r['dataset']} / {r['viewport']}] {json.dumps(what, ensure_ascii=False)[:400]}", flush=True)


# GitHub 上分份跑（`.github/workflows/flows.yml`，11-16 仓库公开、标准机器免费）：每份一台机器、一套实验室，只建本份的站点。
# 每份大约几格、最多几份（公开仓库一个账号同时最多 20 台；Development checks 等也占位，留一点余量由 GitHub 排队）
PER_SHARD = int(os.environ.get("PEARNLY_FLOWS_PER_SHARD", "14"))
MAX_SHARDS = int(os.environ.get("PEARNLY_FLOWS_MAX_SHARDS", "20"))


def dataset_site(product, dataset):
    """这套数据的站点（同 `scripts/regression/datasets.py` 的 SITES / COWORK_SITES / DMS_SITES）。"""
    return f"{product}-{dataset}.regression.localhost"


def cell_site(cell):
    return dataset_site(cell[0].get("product", "erp"), cell[1])


def shards(cells, per_shard=None, max_shards=None):
    """把格切成几份，每份是一串格（登记表顺序）。同一站点的格依次跑，所以先按站点分组：
    比一份还小的组装在一起（一份里几个站点，`--jobs` 并行），大组按格数比例分到余下的份、组内连续切（每份只建一个站点）；
    余下的份不够每个大组一份时，整组装进格数最少的那份。同一参数总切出同一结果：`--plan` 和各台机器上的 `--shard I/N` 各算一遍、对得上。"""
    per_shard, max_shards = per_shard or PER_SHARD, max_shards or MAX_SHARDS
    if not cells:
        return []
    groups = {}
    for cell in cells:
        groups.setdefault(cell_site(cell), []).append(cell)
    order = sorted(groups, key=lambda site: (-len(groups[site]), site))
    count = max(1, min(max_shards, -(-len(cells) // max(1, per_shard))))
    target = -(-len(cells) // count)
    small = [site for site in order if len(groups[site]) < target]
    big = [site for site in order if site not in small]
    packed = []
    for site in small:  # 从大到小，放进第一份还装得下的
        room = next((b for b in packed if len(b) + len(groups[site]) <= target), None)
        if room is None:
            packed.append(room := [])
        room.extend(groups[site])
    spare = count - len(packed)
    if spare < len(big):
        bins = [[] for _ in range(count)]
        for site in order:
            min(bins, key=len).extend(groups[site])
        return [b for b in bins if b]
    share = {site: 1 for site in big}
    for _ in range(spare - len(big)):  # 每次给「每份格数」最多的那组再加一份
        site = max(big, key=lambda site: (len(groups[site]) / share[site], site))
        share[site] += 1
    out = []
    for site in big:
        group, k = groups[site], min(share[site], len(groups[site]))
        out += [group[i * len(group) // k:(i + 1) * len(group) // k] for i in range(k)]
    return out + packed


def plan_of(scope, flow_count, cells):
    """`--plan`：范围、格数、各份的格数与要建的站点（工作流的矩阵）。"""
    parts = shards(cells)
    return {"scope": scope, "flows": flow_count, "cells": len(cells),
            "shards": [{"shard": f"{i}/{len(parts)}", "cells": len(part), "sites": " ".join(sorted({cell_site(c) for c in part}))}
                       for i, part in enumerate(parts, 1)]}


def merge_parts(scope, flow_count, cells, files):
    """`--merge`：汇总各份的 --out。份要齐、都是这个提交和这个范围；没报上来的格记不通过（`error: not reported`），
    好让 `--rerun-failed` 照常补跑它们。回与普通 --out 同样形状的结果。"""
    sha, problems = head(), []
    parts = [json.loads(Path(f).read_text(encoding="utf-8")) for f in files]
    wanted = {key(cell): cell for cell in cells}
    got = {}
    for part in parts:
        if part.get("sha") != sha or part.get("scope") != scope:
            problems.append(f"shard {part.get('shard')} ran {str(part.get('sha'))[:12]} {part.get('scope')}")
            continue
        for result in part.get("results", []):
            if key(result) in wanted:
                got[key(result)] = result
    missing = [k for k in wanted if k not in got]
    if missing:
        problems.append(f"{len(missing)} runs not reported")
    results = [got.get(k) or {"flow": k[0], "dataset": k[1], "viewport": k[2], "ok": False, "checks": [],
                              "error": "not reported (shard missing or cancelled)"} for k in wanted]
    ok = all(r["ok"] for r in results) and not problems
    datasets = ",".join(sorted({r["dataset"] for r in results})) or "-"
    seconds = max((part.get("seconds", 0) for part in parts), default=0)
    summary = (f"{scope}; {flow_count} flows; datasets {datasets}; {sum(r['ok'] for r in results)}/{len(results)} runs passed; "
               f"{seconds}s in {len(parts)} shards" + (f"; {'; '.join(problems)}" if problems else ""))
    return {"ok": ok, "summary": summary, "sha": sha, "dirty": any(part.get("dirty") for part in parts), "scope": scope,
            "flow_count": flow_count, "seconds": seconds, "shards": len(parts), "problems": problems,
            "lab_python_fallbacks": [f for part in parts for f in part.get("lab_python_fallbacks", [])], "results": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--flow", action="append", default=[])
    parser.add_argument("--changed", action="store_true")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--dataset", choices=["new", "old"], help="只跑这套数据（调试用）")
    parser.add_argument("--viewport", choices=["desktop", "mobile"], help="只跑这个宽度（调试用）")
    parser.add_argument("--rerun-failed", metavar="结果.json",
                        help="只补跑这份结果里不通过的格，与它合并（同一提交；范围沿用它的，不配 --all / --flow / --changed）")
    parser.add_argument("--order", choices=["registry", "reverse"], default="registry",
                        help="同一站点的格按登记表顺序还是倒过来跑（reverse 查流程有没有靠排在前面的流程留下的数据；结果仍按登记表顺序记）")
    parser.add_argument("--out")
    parser.add_argument("--post-status", action="store_true")
    parser.add_argument("--migrate", action="store_true", help="先重启实验室网页进程、把全部站点迁移到挂上来的源码（--post-status 时总是做）")
    parser.add_argument("--plan", action="store_true", help="只打印范围、格数和各份要建的站点（JSON，GitHub 工作流分份用），不连实验室")
    parser.add_argument("--shard", metavar="I/N", help="只跑第 I 份（共 N 份，切法同 --plan）；结果用 --out 交给 --merge")
    parser.add_argument("--merge", nargs="*", metavar="份.json", help="汇总各份的 --out（同范围参数）；配 --out 写合并结果、--post-status 回写")
    parser.add_argument("--jobs", type=int, default=1, help="同时跑几个站点（同一站点的格依次跑；每一路占实验室一个网页进程，共 3 个，超过 3 路两路共用一个）")
    args = parser.parse_args(argv)
    flows = registry()
    previous = None
    if args.rerun_failed:
        if args.all or args.flow or args.changed or args.dataset or args.viewport:
            parser.error("--rerun-failed 的范围就是上次结果的范围，不配 --all / --flow / --changed / --dataset / --viewport")
        previous, cells = previous_failures(args.rerun_failed, flows, args.post_status)
        scope, flow_count = previous["scope"], previous["flow_count"]
        if not cells:
            print("上次结果没有不通过的格，不用补跑")
            return 0
    else:
        if args.post_status and (args.flow or args.dataset or args.viewport):
            parser.error("--post-status 只配 --changed、--all 或 --rerun-failed（部分流程的结果不能代表这个提交）")
        scope = "all flows"
        if args.dataset or args.viewport:
            scope = "partial"
        if args.flow:
            scope = "flows " + ",".join(args.flow) + (" partial" if scope == "partial" else "")
            flows = [f for f in flows if f["id"] in args.flow]
        elif args.changed:
            base = merge_base(args.base)
            scope = f"base {base[:12]}"
            files = changed_files(base)
            flows = changed_flows(flows, files, base)
        elif not args.all:
            parser.error("--all, --flow, --changed or --rerun-failed")
        cells, flow_count = expand(flows, args.dataset, args.viewport), len(flows)
    if (args.plan or args.shard or args.merge is not None) and previous:
        parser.error("--plan / --shard / --merge 不配 --rerun-failed（补跑在汇总之后做）")
    if args.shard and (args.post_status or args.merge is not None or args.plan):
        parser.error("--shard 只跑一份，汇总与回写由 --merge 做")
    if args.plan:
        print(json.dumps(plan_of(scope, flow_count, cells), ensure_ascii=False))
        return 0
    if args.merge is not None:
        merged = merge_parts(scope, flow_count, cells, args.merge)
        print(("流程用例：通过 " if merged["ok"] else "流程用例：不通过 ") + merged["summary"])
        report_leftovers(merged["results"])
        if args.out:
            Path(args.out).write_text(json.dumps(merged, ensure_ascii=False, indent=1))
        if args.post_status:
            print("status posted for", post_status(merged["ok"], merged["summary"]))
        return 0 if merged["ok"] else 1
    if args.shard:
        index, _, count = args.shard.partition("/")
        parts = shards(cells)
        if not count.isdigit() or int(count) != len(parts) or not index.isdigit() or not 1 <= int(index) <= len(parts):
            raise SystemExit(f"--shard {args.shard} 对不上：这个范围切成 {len(parts)} 份")
        cells = parts[int(index) - 1]
    if args.post_status or args.shard:
        lab_runs_this_tree()
    note = unposted_note(scope, args.post_status) if cells and not args.shard else ""
    if note:
        print(note, flush=True)   # 开跑前说一次：几小时的全套别白跑
    lab, results, started = Lab(persistent=True), [], time.monotonic()
    if cells:
        lab.ping_site = cell_site(cells[0])  # 本次一定建了的站点（分份时没有基础站点）
    held = lab_lock() if cells else None  # noqa: F841 —— 进程退出时放锁
    if cells:
        spa_fresh()
        unbilled = lab.unbilled()
        if unbilled:
            raise SystemExit(f"这些站点没接实验室计费（首页报计费服务暂不可用，结果不可信）：{', '.join(unbilled)}。先跑 "
                             f"docker exec -w /home/frappe/frappe-bench {CONTAINER} env/bin/python /workspace/scripts/regression/lab_billing.py")
    if cells and (args.migrate or args.post_status):
        lab.restart()
        lab.migrate()
        lab.restart()  # 迁移后再起一次：迁移里导入的模块与缓存不带进流程
        print(f"实验室站点已迁移（{round(time.monotonic() - started)}s）", flush=True)
    # 各套数据先串行补齐（datasets.py 不并发跑），之后各格可以并行
    for product, dataset in sorted({(flow.get("product", "erp"), dataset) for flow, dataset, _ in cells}):
        lab.dataset(dataset, product)
    printing = threading.Lock()

    def one(cell):
        flow, dataset, viewport = cell
        lab.cell_started()
        site = lab.dataset(dataset, flow.get("product", "erp")).get("site")
        try:
            start_print = lab.footprint(site) if site else None
            footprint_error = "" if site else "no site"
        except Exception as error:  # noqa: BLE001 - 取不到只是这一格不比对，不挡流程
            start_print, footprint_error = None, str(error)[:200]
        try:
            result = run_flow(lab, flow, dataset, viewport)
        except Exception as error:  # noqa: BLE001 - 一格出错记不通过，其余照跑
            result = {"flow": flow["id"], "dataset": dataset, "viewport": viewport, "ok": False, "error": str(error)[:600],
                      "checks": [], **({"steps": getattr(error, "steps")} if getattr(error, "steps", None) else {})}
        # 收尾：这一格新建的行和文件删掉（流程自己的 cleanup / verify 之后）；删不掉的留在 left_data 里
        if start_print is not None and start_print.get("_now"):
            try:
                swept = lab.sweep(site, start_print)
                if swept.get("swept") or swept.get("files") or swept.get("kept"):
                    result["swept"] = swept
            except Exception as error:  # noqa: BLE001 - 收尾出错记下，不挡别的格
                result["swept"] = {"error": str(error)[:300]}
        # 留下了数据：只记不判（后面的流程看得到、可能被它带歪；判过与否仍只看这一格自己的检查项）
        try:
            left = (footprint_diff(start_print, lab.footprint(site)) if start_print is not None
                    else {"error": footprint_error} if site else {})
        except Exception as error:  # noqa: BLE001
            left = {"error": str(error)[:200]}
        if left:
            result["left_data"] = left
        result["lab_python"] = lab.cell_mode()
        result["port"] = lab_port()
        bad = [c for c in result["checks"] if not c["ok"]]
        if bad:  # 不通过的格把这段时间服务端记下的错误标题排在最前（只截 600 字时也看得到）
            bad = [c for c in result["checks"] if c["case"].startswith("server errors") and c["seen"]] + bad
        with printing:
            print(f"{'通过' if result['ok'] else '不通过'} {flow['id']} [{dataset} / {viewport}] {result.get('seconds', '')}s "
                  f"{result.get('error', '')} {json.dumps(bad, ensure_ascii=False)[:600] if bad else ''}", flush=True)
        return result

    # 按站点并行：同一站点的格依次跑（编号序列、同一岗位账号、按站点数的 Error Log 都不互相干扰），不同站点之间同时跑
    groups = {}
    for cell in (cells[::-1] if args.order == "reverse" else cells):
        groups.setdefault((cell[0].get("product", "erp"), cell[1]), []).append(cell)
    # 大组先排（11-15）：墙钟由最大的一组决定，小组排在它后面就白等；只改先后，同一站点仍依次跑、结果仍按登记表顺序记
    groups = dict(sorted(groups.items(), key=lambda item: -len(item[1])))
    slots = port_slots(args.jobs)

    def site_group(group):
        port = slots.get()
        _PORT.port = port
        try:
            return [one(cell) for cell in group]
        finally:
            slots.put(port)
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            done = dict(zip(groups, pool.map(site_group, groups.values())))
    finally:
        lab.close_workers()
    results = [done[group_key][group.index(cell)] for group_key, group in groups.items() for cell in group]
    order = [key(cell) for cell in cells]
    results.sort(key=lambda r: order.index(key(r)))
    seconds = round(time.monotonic() - started)
    reran = []
    if previous:
        # 补跑的格替换上次的结果、标上 rerun；其余沿用上次。秒数是两次之和
        fresh = {key(r): dict(r, rerun=True) for r in results}
        # 上次本身是合并结果时，之前补跑过的格也照样列出
        reran = list(dict.fromkeys(previous.get("reran", []) + [f"{flow} {dataset}/{viewport}" for flow, dataset, viewport in fresh]))
        results = [fresh.get(key(r), r) for r in previous["results"]]
        seconds += previous.get("seconds", 0)
    ok = all(r["ok"] for r in results)  # 没有涉及的流程 = 通过（覆盖闸另管改到的入口有没有登记）
    datasets = ",".join(sorted({r["dataset"] for r in results})) or "-"
    summary = (f"{scope}; {flow_count} flows; datasets {datasets}; {sum(r['ok'] for r in results)}/{len(results)} runs passed; "
               f"{seconds}s" + (f"; reran {len(reran)}: {', '.join(reran)}" if reran else ""))
    print(("流程用例：通过 " if ok else "流程用例：不通过 ") + summary)
    report_leftovers(results)
    if lab.fallbacks:
        print(f"常驻 Python 退回每段新起进程 {len(lab.fallbacks)} 次（各格的 lab_python 字段）：{lab.fallbacks[0][:200]}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps({
            "ok": ok, "summary": summary, "sha": head(), "dirty": bool(product_dirty()), "scope": scope, "flow_count": flow_count,
            "seconds": seconds, "shard": args.shard or "1/1", "order": args.order, "jobs": args.jobs, "ports": PORTS[:max(1, min(args.jobs, len(PORTS)))], "lab_python_fallbacks": lab.fallbacks, **({"merged_from": str(Path(args.rerun_failed).resolve()), "reran": reran} if previous else {}),
            "results": results}, ensure_ascii=False, indent=1))
    if args.post_status:
        print("status posted for", post_status(ok, summary))
    elif note:
        print(note)               # 结束汇总里再说一次
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
