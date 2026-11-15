"""回归实验室容器里的常驻 Python（流程用例 `scripts/flows/run.py` 按格复用，11-15）。

每格要在容器里跑约 6 段 Python（准备、建 / 删会话、核对、报错计数前后）。每段都新起一个进程的话，光加载 frappe、第一次连站点就要约 2.8 秒，
全套约 6600 工作秒。这里起一次、按请求执行，每段照样自己 init / connect / destroy（同新进程），只省加载与首连。

协议：标准输入一行一个 JSON 请求，标准输出一行一个 JSON 回复（都走启动时复制出来的专用描述符；请求执行期间 0 号是 /dev/null、
1 号指到 2 号，执行的代码或它拉起的子进程打印什么都不会混进协议）。
    {"code": "<python 源码>"}                         同 `python -c <源码>`
    {"script": "/workspace/…/x.py", "argv": [...]}    同 `python x.py argv…`
    回复 {"returncode": int, "stdout": str, "stderr": str}

每个请求前后都把进程收回到「刚起的新进程」那样：
- 事务：没提交的回滚，再 frappe.destroy()（释放 frappe.local：站点、当前用户、会话、flags、语言与翻译、form_dict、conf 都在里面）；
- 进程内缓存：@site_cache（含站点配置 60 秒缓存）与 client_cache 清空，不读到上一格留下的值；
- 模块属性：frappe / 各产品 App 模块里被请求替换掉的函数、类、常量（例如 `frappe.enqueue = inline`、`line_collection.WAIT_SECONDS = 0`）
  还原成模块刚导入时的值（导入那一刻就记下，不是请求结束时记）；本仓 App 模块另把懒加载槽（`_x = None` 后来填上的）、
  导入时为空的模块级 dict / list / set（进程内记忆，例如 `_repaired = {}`）的内容和 lru_cache 也还原 / 清空（同新进程，本仓约 20 处）。
  空容器的「导入时内容」在之后每导入一个本仓模块时刷新：别的模块导入时往里登记的（注册表）算导入时状态，留着；
- 工作目录、sys.argv、环境变量还原。
还原不了的（进程被杀、os._exit）由 run.py 发现进程没了，这一段照旧报错，之后退回每段新起一个进程（结果里标出来）。
"""
import builtins
import contextlib
import importlib.abc
import io
import json
import os
import runpy
import sys
import traceback
import types

SITES = os.environ.get("LAB_WORKER_SITES", "/home/frappe/frappe-bench/sites")  # 测试里换成临时目录
WATCHED = ("frappe", "pearnly")  # frappe 与本仓各 App（pearnly_core / pearnly_erp / pearnly_cowork / pearnly_dms …）
OURS = ("pearnly",)
PLAIN = (int, float, str, bool, bytes, tuple, frozenset)
CONTAINERS = (dict, list, set)
# 进程级连接，留着复用（同新进程里第一次连上后的样子；每次清掉要重连 Redis、多起一个失效监听线程）
KEEP = {("frappe", "cache"), ("frappe", "client_cache")}
_MISSING = object()
SNAPSHOT: dict[str, dict[str, object]] = {}
CONTENTS: dict[tuple[str, str], tuple[object, object]] = {}  # 本仓模块导入时为空的容器：(容器, 导入时内容的浅拷贝)


def restorable(name, key, value):
    """记哪些模块属性。frappe：函数、类、子模块与简单常量（猴子补丁换的就是这些）。本仓 App：再加 None 槽与导入时为空的容器。"""
    if key.startswith("__") or (name, key) in KEEP:
        return False
    if name.startswith(OURS) and (value is None or (isinstance(value, CONTAINERS) and not value)):
        return True
    return value is not None and (callable(value) or isinstance(value, (types.ModuleType, *PLAIN)))


def refresh_contents():
    """又导入了一个本仓模块：它导入时往别的模块容器里登记的内容算「导入时状态」。"""
    for slot, (value, _) in list(CONTENTS.items()):
        CONTENTS[slot] = (value, value.copy())


def remember(module):
    name = getattr(module, "__name__", "") or ""
    if name in SNAPSHOT or not name.startswith(WATCHED):
        return
    try:
        items = list(vars(module).items())
    except TypeError:
        return
    SNAPSHOT[name] = {key: value for key, value in items if restorable(name, key, value)}
    if name.startswith(OURS):
        refresh_contents()
        for key, value in SNAPSHOT[name].items():
            if isinstance(value, CONTAINERS):
                CONTENTS[(name, key)] = (value, value.copy())


class Remember(importlib.abc.MetaPathFinder):
    """模块执行完导入的那一刻记下它的属性：请求里导入、随后又被同一请求改掉的，还原到导入时的样子。"""

    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith(WATCHED):
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is None:
                continue
            loader = spec.loader
            if loader is not None and hasattr(loader, "exec_module") and not getattr(loader, "_lab_wrapped", False):
                real = loader.exec_module

                def exec_module(module, real=real):
                    real(module)
                    remember(module)
                with contextlib.suppress(AttributeError):
                    loader.exec_module = exec_module
                    loader._lab_wrapped = True
            return spec
        return None


def restore_modules():
    restored = 0
    for name, saved in SNAPSHOT.items():
        module = sys.modules.get(name)
        if module is None:
            continue
        current = vars(module)
        for key, value in saved.items():
            if current.get(key, _MISSING) is not value:
                with contextlib.suppress(AttributeError, TypeError):
                    setattr(module, key, value)
                    restored += 1
        if name.startswith(OURS):
            for value in saved.values():
                clear = getattr(value, "cache_clear", None)
                if callable(clear):
                    with contextlib.suppress(Exception):
                        clear()
    for value, content in CONTENTS.values():
        with contextlib.suppress(Exception):
            if value != content:
                value.clear()
                if isinstance(value, list):
                    value.extend(content)
                else:
                    value.update(content)
                restored += 1
    return restored


def clear_process_caches(frappe):
    try:
        from frappe.utils import caching

        for cache in caching._SITE_CACHE.values():
            cache.clear()
    except Exception:  # noqa: BLE001 - 缓存结构变了也不挡流程，最坏等于不清
        pass
    client = getattr(frappe, "client_cache", None)
    if client is not None:
        with contextlib.suppress(Exception):
            client.clear_cache()


def settle(frappe):
    """回到新进程的样子：没提交的事务回滚、释放 frappe.local、清进程内缓存、还原模块属性。"""
    try:
        db = getattr(frappe.local, "db", None)
        if db is not None:
            with contextlib.suppress(Exception):
                db.rollback()
        with contextlib.suppress(Exception):
            frappe.destroy()
    finally:
        clear_process_caches(frappe)
        restore_modules()


def run(request, frappe, environ):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    os.chdir(SITES)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                if "script" in request:
                    sys.argv = [request["script"], *request.get("argv", [])]
                    runpy.run_path(request["script"], run_name="__main__")
                else:
                    sys.argv = ["-c"]
                    exec(compile(request["code"], "<string>", "exec"), {"__name__": "__main__", "__builtins__": builtins})
            except SystemExit as stop:
                if stop.code is None or isinstance(stop.code, int):
                    code = stop.code or 0
                else:
                    print(stop.code, file=sys.stderr)
                    code = 1
            except BaseException:  # noqa: BLE001 - 同 python -c：打印回溯、退出码 1
                traceback.print_exc()
                code = 1
    finally:
        settle(frappe)
        os.environ.clear()
        os.environ.update(environ)
        os.chdir(SITES)
        sys.argv = ["lab_worker"]
    return {"returncode": code, "stdout": out.getvalue(), "stderr": err.getvalue()}


def main():
    # 协议走复制出来的描述符；0 号换成 /dev/null（同 run.py 原来的 stdin=DEVNULL），1 号指到 2 号
    proto_in = os.fdopen(os.dup(0), "r", encoding="utf-8")
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    os.dup2(2, 1)
    sys.stdin = io.StringIO("")
    sys.meta_path.insert(0, Remember())
    os.chdir(SITES)
    import frappe

    for module in list(sys.modules.values()):
        if module is not None:
            remember(module)
    environ = dict(os.environ)
    proto_out.write(json.dumps({"ready": True}) + "\n")
    proto_out.flush()
    for line in proto_in:
        if not line.strip():
            continue
        try:
            reply = run(json.loads(line), frappe, environ)
        except BaseException as error:  # noqa: BLE001 - 协议层出错照样回一条，让 run.py 判这一段失败
            reply = {"returncode": 1, "stdout": "", "stderr": f"lab worker: {error!r}"}
        proto_out.write(json.dumps(reply, ensure_ascii=False) + "\n")
        proto_out.flush()


if __name__ == "__main__":
    main()
