"""不 fork 的识别进程按内存高水位温和重启（2026-10-01，生产压测 pA）。

识别容器开了 `FRAPPE_BACKGROUND_WORKERS_NOFORK`（任务在 worker 进程里直接跑，省掉每个任务 fork、重新导入、重新换令牌），
代价是进程内存停在它处理过的最大那一单：真实大文件跑过后每进程 330–390MB，Cowork 12 进程把 4g 顶满、被内核杀了 2 个。
Frappe 16 只按任务数（`RQ_MAX_JOBS` 5000，模块常量、没有配置入口）重启不 fork 的 worker。

这里在每个任务结束后（Frappe 的 `after_job` 钩子，任务收尾的 finally 里调用）看本进程 RSS：只在不 fork 的识别队列 worker 里，
超过阈值就给自己发一次 SIGTERM —— RQ 在忙时只记下「停止」，当前任务收尾后退出，worker-pool 发现少了一个就补起新的
（新进程只多付约 1 秒导入开销）。按内存判断：小单不白白重启，大单一跑完就释放。
阈值默认 300MB，容器环境变量 `PEARNLY_OCR_WORKER_RECYCLE_MB` 可调。
"""
import os
import signal

from . import pool

DEFAULT_LIMIT_MB = 300
# 开了多线程时每多一个线程多留的内存：默认 150MB（一份手机大图 / 多页 PDF 在跑时约 100MB 上下），
# 识别交给 Cloud Run 的产品用 PEARNLY_OCR_THREAD_MB 调小（见 pool.thread_mb）。
PER_THREAD_MB = 150
_requested = False


def _rss_mb():
    with open("/proc/self/statm") as statm:
        pages = int(statm.read().split()[1])
    return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)


def _limit_mb():
    try:
        value = int(os.environ.get("PEARNLY_OCR_WORKER_RECYCLE_MB") or DEFAULT_LIMIT_MB)
    except ValueError:
        return DEFAULT_LIMIT_MB
    return value if value > 0 else DEFAULT_LIMIT_MB


def after_job(method=None, kwargs=None, result=None):
    """每个任务收尾时调用（所有队列、所有 App）：先用最便宜的判断把绝大多数任务放过去。只作释放内存，出错一律忽略。

    开了多线程（pool.py）时只在 RQ 这边判断（线程里没有 RQ 任务，`ocr_queue_job` 为假），阈值按线程数放大：
    每多一个同时在跑的线程多留 PER_THREAD_MB。收到停止后进程不再取新任务，在跑的线程跑完才退出。"""
    global _requested
    if _requested or not pool.no_fork():
        return
    try:
        if not pool.ocr_queue_job():
            return
        rss = _rss_mb()
        if rss < _limit_mb() + pool.thread_mb() * (pool.threads() - 1):
            return
        _requested = True
        import frappe

        frappe.logger("pearnly.capacity").info(f"OCR worker {os.getpid()} at {rss:.0f}MB; restarting after this job")
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception:
        pass
