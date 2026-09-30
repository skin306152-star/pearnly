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

DEFAULT_LIMIT_MB = 300
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


def _ocr_queue_job():
    from rq import get_current_job

    job = get_current_job()
    # Frappe 的队列名带 bench 前缀（`home-frappe-frappe-bench:ocr`）
    return bool(job) and str(job.origin or "").rsplit(":", 1)[-1] == "ocr"


def after_job(method=None, kwargs=None, result=None):
    """每个任务收尾时调用（所有队列、所有 App）：先用最便宜的判断把绝大多数任务放过去。只作释放内存，出错一律忽略。"""
    global _requested
    if _requested or str(os.environ.get("FRAPPE_BACKGROUND_WORKERS_NOFORK") or "").lower() not in ("1", "true", "yes"):
        return
    try:
        if not _ocr_queue_job():
            return
        rss = _rss_mb()
        if rss < _limit_mb():
            return
        _requested = True
        import frappe

        frappe.logger("pearnly.capacity").info(f"OCR worker {os.getpid()} at {rss:.0f}MB; restarting after this job")
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception:
        pass
