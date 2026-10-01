"""后台 worker 入口：先在父进程里把常用模块导入好，再交给 Frappe 原生 worker（2026-10-02，生产压测 q3）。

Frappe 的 worker 每个任务 fork 一个子进程去跑；父进程没导入过的模块，每个子进程都要重新导入一遍。
压测 py-spy：`bench worker` 的 short/default 进程 88% 的样本在导入（Frappe 自己的 `database.query`、`safe_exec`
占了大头 —— 原生 worker-pool 会预先导入它们，`bench worker` 不会）；Cowork 长任务进程 28% 在导入对账模块。
这里在父进程按清单导入一次、`gc.freeze()` 后再起原生 worker，fork 出的任务进程直接继承，任务照旧每次 fork
（内存随任务结束释放、任务之间互不污染），只是不再重复导入。给了 `--num-workers` 起原生 worker-pool（同 `bench worker-pool`），
没给起原生单 worker（同 `bench worker`，不在 worker 里带定时器线程），行为与原命令一致。

清单 = 下面这份 Frappe 公共模块 + `--preload <app>` 指定的产品在自己 `hooks.py` 里声明的 `pearnly_worker_preload`。
导入失败只记一行、跳过，不影响 worker 启动；导入时不开数据库 / Redis 连接（父进程没有 Site）。
用法：`python -m pearnly_core.execution.worker --queue long [--num-workers 2] [--preload pearnly_cowork]`
"""
import argparse
import gc
import importlib
import os

# 任务里几乎都会用到、但 `bench worker` 不预先导入的 Frappe 模块（按压测 py-spy 的导入热点）。
FRAPPE_PRELOAD = (
    "frappe.database.query",
    "frappe.query_builder",
    "frappe.utils.safe_exec",
    "frappe.utils.scheduler",
    "frappe.utils.typing_validations",
    "frappe.website.path_resolver",
    "frappe.database.mariadb.database",
    "frappe.database.mariadb.mysqlclient",
    "frappe.utils.redis_wrapper",
    "frappe.recorder",
    "frappe.model.create_new",
    "frappe.core.doctype.user.user",
    "frappe.core.doctype.scheduled_job_type.scheduled_job_type",
    "frappe.email.doctype.notification.notification",
    "frappe.email.doctype.email_queue.email_queue",
    "frappe.email.doctype.email_account.email_account",
    "frappe.integrations.doctype.google_calendar.google_calendar",
)


def modules(apps):
    """Frappe 公共清单 + 各产品 `hooks.pearnly_worker_preload`（去重、保持顺序）。"""
    names = list(FRAPPE_PRELOAD)
    for app in apps:
        try:
            hooks = importlib.import_module(f"{app}.hooks")
        except Exception as exc:
            print(f"worker preload: {app}.hooks not importable ({type(exc).__name__})", flush=True)
            continue
        names += list(getattr(hooks, "pearnly_worker_preload", ()))
    return list(dict.fromkeys(names))


def preload(names):
    loaded = 0
    for name in names:
        try:
            importlib.import_module(name)
            loaded += 1
        except Exception as exc:
            print(f"worker preload skipped {name}: {type(exc).__name__}: {exc}", flush=True)
    gc.freeze()  # 导入的对象移出 GC 追踪，fork 后少触碰共享页
    return loaded


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pearnly_core.execution.worker")
    parser.add_argument("--queue", required=True)
    parser.add_argument("--num-workers", type=int)
    parser.add_argument("--preload", action="append", default=[], help="产品 App 名，可重复")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--burst", action="store_true", help="队列空了就退出（部署前自检用）")
    args = parser.parse_args(argv)
    os.chdir(os.path.join(os.environ.get("FRAPPE_BENCH", "/home/frappe/frappe-bench"), "sites"))
    names = modules(args.preload)
    print(f"worker preload: {preload(names)}/{len(names)} modules", flush=True)
    from frappe.utils import background_jobs

    if args.num_workers is None:
        background_jobs.start_worker(queue=args.queue, quiet=args.quiet, burst=args.burst)
    else:
        background_jobs.start_worker_pool(queue=args.queue, num_workers=args.num_workers, quiet=args.quiet,
                                          burst=args.burst)


if __name__ == "__main__":
    main()
