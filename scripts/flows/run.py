#!/usr/bin/env python3
"""流程用例（主会话 10-28 第 3 道闸）：按登记表 `tests/flows/registry.toml` 在回归实验室的两套固定数据上跑流程，断言页面结果与落库。

    python3 scripts/flows/run.py --all
    python3 scripts/flows/run.py --flow erp.stocktake.scan
    python3 scripts/flows/run.py --changed --base origin/main      # 只跑改动文件涉及的流程
    ... --out 结果.json --post-status                                # 结果作为提交状态 `pearnly/flows` 回写 GitHub
    python3 scripts/flows/run.py --rerun-failed 结果.json --out 合并.json --post-status   # 同一提交上只补跑失败格、并进上次结果

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
# 改了站点配置后等多久网页进程才都看到新值：Frappe 16 网页进程的站点配置缓存 60 秒（见 `Lab.site_config`），多等 1 秒
SITE_CONFIG_SETTLE = 61


def registry() -> list[dict]:
    return tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["flow"]


def merge_base(base: str) -> str:
    found = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if not found:
        raise SystemExit(f"找不到 {base} 与 HEAD 的分叉点")
    return found


def changed_files(base: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", base, "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
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


def changed_flows(flows, files) -> list[dict]:
    """改动涉及的流程：registry 的 covers，加上流程自己的脚本与它引用到的流程辅助脚本（`own_scripts`）。--changed 与补跑范围同一规则。"""
    return [flow for flow in flows
            if any(covers(flow, path) for path in files) or own_scripts(flow) & set(files)]


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

    def __init__(self):
        self.manifests = {}  # 每套数据一次运行只补齐一遍
        self._cell = threading.local()  # 并行时每个线程一次只跑一格：这一格临时改过的设置（`set_fields`）记在这里

    def exec(self, *args, timeout=900):
        return subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench/sites", CONTAINER, *args],
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def python(self, site, body):
        code = f"import frappe, json\nfrappe.init(site={site!r}, sites_path='.')\nfrappe.connect()\ntry:\n" + \
            "\n".join("    " + line for line in body.strip().splitlines()) + "\nfinally:\n    frappe.destroy()\n"
        result = self.exec("../env/bin/python", "-c", code)
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
        out = self.exec("../env/bin/python", "/workspace/scripts/smoke/session.py", site, user).stdout
        return next((line.split()[1] for line in out.splitlines() if line.startswith("SID ")), "")

    def drop(self, site, user, sid):
        self.exec("../env/bin/python", "/workspace/scripts/smoke/session.py", site, user, "drop", sid)

    def restart(self):
        """重启 bench 容器再等网页起来：网页进程用 --noreload 起，已导入的模块一直留在内存里，换了源码不重启就是新旧混跑
        （10-30 变基后 terminals.py 新、warehouses 还是旧的，报找不到 main_of）。"""
        subprocess.run(["docker", "restart", CONTAINER], capture_output=True, check=True, timeout=300)
        for _ in range(120):
            ping = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-H", "Host: erp.regression.localhost",
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

    def client_errors(self, site):
        return self.python(site, 'print(json.dumps({"n": frappe.db.count("Error Log", {"method": ["like", "Browser error%"]})}))')["n"]


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
    job = {"base": f"http://{site}:{PORT}", "cookie_domain": site, "sid": sid, "start": start, "mobile": mobile,
           "video": video or "", "steps": steps, **({"ready": ready} if ready else {}), **({"preload": preload} if preload else {}),
           **({"shots": shots} if shots else {})}
    env = dict(os.environ, SMOKE_CHROME_RULES=f"MAP {site} 127.0.0.1")
    # 自成进程组：超时连同它拉起的无头 Chrome 一起结束，不留残留进程
    proc = subprocess.Popen(["node", str(ROOT / "scripts/regression/ui_steps.mjs"), json.dumps(job)], stdout=subprocess.PIPE,
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
    before = lab.client_errors(site)
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
    checks, page_errors, below, icons = [], [], set(), set()
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
        label = "camera" if code else "typed"
        checks += [dict(c, run=label) for c in module.judge(run_params, results, code)]
        checks += [dict(c, run=label, saved_check=True) for c in module.verify(lab, manifest, run_params, code)]
    after = lab.client_errors(site)
    checks.append({"case": "page errors", "seen": page_errors, "ok": not page_errors})
    # ERP / Cowork 字段说明要在标题旁的说明图标里（11-02），还写在字段下方的就是漏了（登记的例外不算，见 `ui_steps.mjs`）
    checks.append({"case": "field descriptions below fields", "seen": sorted(below), "ok": not below})
    checks.append({"case": "field descriptions in the info icon (record only)", "seen": sorted(icons), "ok": True})
    checks.append({"case": "client errors in Error Log", "seen": after - before, "ok": after == before})
    return {"flow": flow["id"], "dataset": dataset, "viewport": viewport, "ok": all(c["ok"] for c in checks),
            "seconds": round(time.monotonic() - started, 1), "checks": checks}


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
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def product_dirty():
    return subprocess.run(["git", "status", "--porcelain", "apps", "frontend"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def post_status(ok, summary):
    """回写 GitHub 提交状态 `flows`；说明里带比较基线，部署闸据此确认覆盖到上次上线以来的全部改动。"""
    sha = head()
    description = summary if len(summary) <= 140 else summary[:139] + "…"
    subprocess.run(["gh", "api", f"repos/{{owner}}/{{repo}}/statuses/{sha}", "-f", f"state={'success' if ok else 'failure'}",
                    "-f", f"context={STATUS}", "-f", f"description={description}"], cwd=ROOT, check=True, capture_output=True)
    return sha


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
        return changed_flows(flows, files)
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
    parser.add_argument("--jobs", type=int, default=1, help="同时跑几个站点（同一站点的格依次跑；实验室 bench 有 3 个网页进程，建议不超过 3）")
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
            flows = changed_flows(flows, files)
        elif not args.all:
            parser.error("--all, --flow, --changed or --rerun-failed")
        cells, flow_count = expand(flows, args.dataset, args.viewport), len(flows)
    if args.post_status:
        lab_runs_this_tree()
    lab, results, started = Lab(), [], time.monotonic()
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
        try:
            result = run_flow(lab, flow, dataset, viewport)
        except Exception as error:  # noqa: BLE001 - 一格出错记不通过，其余照跑
            result = {"flow": flow["id"], "dataset": dataset, "viewport": viewport, "ok": False, "error": str(error)[:600],
                      "checks": [], **({"steps": getattr(error, "steps")} if getattr(error, "steps", None) else {})}
        bad = [c for c in result["checks"] if not c["ok"]]
        with printing:
            print(f"{'通过' if result['ok'] else '不通过'} {flow['id']} [{dataset} / {viewport}] {result.get('seconds', '')}s "
                  f"{result.get('error', '')} {json.dumps(bad, ensure_ascii=False)[:600] if bad else ''}", flush=True)
        return result

    # 按站点并行：同一站点的格依次跑（编号序列、同一岗位账号、按站点数的 Error Log 都不互相干扰），不同站点之间同时跑
    groups = {}
    for cell in (cells[::-1] if args.order == "reverse" else cells):
        groups.setdefault((cell[0].get("product", "erp"), cell[1]), []).append(cell)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        done = dict(zip(groups, pool.map(lambda group: [one(cell) for cell in group], groups.values())))
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
    if args.out:
        Path(args.out).write_text(json.dumps({
            "ok": ok, "summary": summary, "sha": head(), "dirty": bool(product_dirty()), "scope": scope, "flow_count": flow_count,
            "seconds": seconds, "order": args.order, **({"merged_from": str(Path(args.rerun_failed).resolve()), "reran": reran} if previous else {}),
            "results": results}, ensure_ascii=False, indent=1))
    if args.post_status:
        print("status posted for", post_status(ok, summary))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
