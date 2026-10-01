"""一个识别进程同时跑几份识别（2026-10-01 生产压测，用户「一个进程同时挂多个谷歌请求」）。

生产上一张发票约 13 秒，我们自己的 CPU 只占约 2.4 秒，其余都在等谷歌回复；以前一份识别占住一个进程（每个约 300–450MB），
进程数被内存卡住，同时在识别的份数也就跟着卡住，多出来的只能排队。这里让不 fork 的识别进程开 N 个线程：RQ 取到任务交给
空闲线程后立即返回去取下一个；N 个线程都忙时 RQ 这边原地等，不多取（多余的任务留在队列里给别的进程）。

每个线程按 Frappe 原生 `execute_job` 跑这份识别：自己的站点上下文与数据库连接、出错回滚、死锁重试、before_job / after_job
钩子都和原来一样。`frappe.local` 按 ContextVar 隔离，与 gunicorn 多线程处理网页请求是同一种用法；线程用全新的上下文起跑。
只改同时跑几份，不改读法、调用顺序与结果。

- 线程数：容器环境变量 `PEARNLY_OCR_THREADS`，不设或 1 照旧一份一进程（本模块什么都不做）。只对不 fork 的识别队列生效。
- 没有 RQ 的硬时限（任务交出后 RQ 那边已结束）：每次外部调用自带超时（连接 10 秒、读 ≤180 秒），流程超过 420 秒主动让出；
  线程真卡住时租约（JOB_TIMEOUT + 60 秒）过期由调度补投，和进程被杀时一样，检查点防止重复付费。
- 进程收到停止信号（发版、按内存回收）时不再取新任务，已在跑的线程跑完再退出（非守护线程，进程退出时等它们）。
"""
from contextlib import contextmanager
import contextvars
import os
import threading

MAX_THREADS = 32
# RQ 这边等空闲线程最多这么久：超时就在 RQ 这个线程里照原样跑这一份（等同多一个线程），不让任务因等位失败。
SLOT_WAIT_SECONDS = 300

_slots = None
_local = None
_slots_lock = threading.Lock()
_running = 0
_worker = threading.local()


def threads():
    try:
        value = int(os.environ.get("PEARNLY_OCR_THREADS") or 1)
    except ValueError:
        return 1
    return max(1, min(MAX_THREADS, value))


def no_fork():
    return str(os.environ.get("FRAPPE_BACKGROUND_WORKERS_NOFORK") or "").lower() in ("1", "true", "yes")


def ocr_queue_job():
    """当前是不是 RQ 识别队列（`ocr`）派来的任务。线程里没有 RQ 任务，返回 False。"""
    from rq import get_current_job

    job = get_current_job()
    # Frappe 的队列名带 bench 前缀（`home-frappe-frappe-bench:ocr`）
    return bool(job) and str(job.origin or "").rsplit(":", 1)[-1] == "ocr"


def in_thread():
    return bool(getattr(_worker, "active", False))


def busy():
    """正在跑的线程数。"""
    return _running


def thread_mb():
    """开多线程时每多一个线程预留的内存（容器环境变量 ``PEARNLY_OCR_THREAD_MB``，默认 150）。

    识别交给 Cloud Run 的线程只是带着原件等回复（几 MB），可以设得很小；本机识别靠 :func:`local_slot` 限住同时几份。"""
    try:
        value = int(os.environ.get("PEARNLY_OCR_THREAD_MB") or 150)
    except ValueError:
        return 150
    return max(10, min(1024, value))


def local_slots():
    """一个进程同时最多跑几份本机识别（容器环境变量 ``PEARNLY_OCR_LOCAL_SLOTS``，默认 1）。"""
    try:
        value = int(os.environ.get("PEARNLY_OCR_LOCAL_SLOTS") or 1)
    except ValueError:
        return 1
    return max(1, min(MAX_THREADS, value))


def release_db():
    """要长时间等外部（Cloud Run 回复、本机识别名额）之前：提交并断开本线程的数据库连接，回来后第一条查询自动重连。
    等待中的线程不占连接（MariaDB ``max_connections``），也不占着事务。"""
    import frappe

    frappe.db.commit()
    frappe.db.close()


@contextmanager
def local_slot():
    """本机跑一份识别（渲染、模型、整理都在本进程）：开了多线程时每个进程同时最多 ``local_slots()`` 份，
    交给 Cloud Run 的不受限（ADR-015）。没开多线程时什么都不做。等名额前先放开数据库连接。"""
    global _local
    if threads() <= 1:
        yield
        return
    with _slots_lock:
        if _local is None:
            _local = threading.BoundedSemaphore(local_slots())
        slots = _local
    if not slots.acquire(blocking=False):
        release_db()
        slots.acquire()
    try:
        yield
    finally:
        slots.release()


def _semaphore():
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(threads())
        return _slots


def handoff(method, kwargs):
    """在 RQ 识别任务里调用：交给空闲线程跑 `method(**kwargs)` 就返回 True，调用方随即结束这个 RQ 任务；
    不满足条件（没开线程、fork 模式、不是 RQ 识别任务、已经在线程里）返回 False，调用方照原样就地跑。"""
    if threads() <= 1 or not no_fork() or in_thread() or not ocr_queue_job():
        return False
    import frappe

    job = getattr(frappe.local, "job", None)
    if job is None:
        return False
    slots = _semaphore()
    if not slots.acquire(timeout=SLOT_WAIT_SECONDS):
        return False
    _count(1)
    try:
        thread = threading.Thread(target=contextvars.Context().run,
            args=(_run, slots, frappe.local.site, method, job.job_name, kwargs, job.user),
            name="pearnly-ocr", daemon=False)
        thread.start()
    except BaseException:
        _count(-1)
        slots.release()
        raise
    return True


def _count(delta):
    global _running
    with _slots_lock:
        _running += delta


def _run(slots, site, method, job_name, kwargs, user):
    _worker.active = True
    try:
        from frappe.utils.background_jobs import execute_job

        # 原生执行：init / connect / set_user / before_job / 调用 / 提交或回滚并记错 / after_job / destroy。
        execute_job(site, method, None, job_name, kwargs, user=user, is_async=True)
    except BaseException:
        pass  # execute_job 已回滚并记进 Error Log；线程到此结束，不把异常抛到线程外
    finally:
        _worker.active = False
        _count(-1)
        slots.release()
