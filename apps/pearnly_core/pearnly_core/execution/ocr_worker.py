"""识别队列（``ocr``）的 RQ worker：取到 ``work.run`` 直接交给空闲线程，不先在 RQ 线程里进一次站点（见 ``pool.relay``）。

只换任务的执行体，RQ 的登记（开始 / 完成登记表、结果保留、心跳、时限）照原生 ``FrappeWorkerNoFork``。
由 ``python -m pearnly_core.execution.worker --queue ocr --num-workers N`` 在不 fork、开了多线程时选用；其余照原生。
类必须在模块顶层：WorkerPool 起子进程时按模块路径传这个类。
"""
from frappe.utils.background_jobs import FrappeWorkerNoFork

from . import pool


class OcrWorker(FrappeWorkerNoFork):
    def perform_job(self, job, queue):
        relayed = pool.relay(job)
        if relayed is not None:
            job._execute = relayed  # RQ 在开始登记、时限之内调用它，与原来调用任务函数的位置相同
        return super().perform_job(job, queue)
