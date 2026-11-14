"""回退用：把 ERP 主档同步专用队列 ``erp_masters`` 里还排着的任务挪回 ``long``（只动 ERP bench 的排队，不连站点库）。

公司接入重做第 1 批（2026-11-14）把主档同步 / 落库 / 按账套建公司挪到 ``erp_masters``，生产由 ``erp-masters-worker`` 取。
回退到没有这条道的旧镜像时，旧代码照旧往 ``long`` 排，但已经排在 ``erp_masters`` 里的任务没人取：这些公司会一直停在
「同步中」（每分钟的 `masters.advance` 看任务还排着，不会收回）。回退后在 ERP 的 web 容器里跑一次：

    docker compose ... run --rm --no-deps erp-web env/bin/python /opt/pearnly/deploy-src/scripts/production/requeue-erp-masters.py

挪的是 RQ 任务本身（任务号、函数、参数不变，旧代码里同名函数照样能跑），所以按任务号去重、`is_job_enqueued` 照旧认得。
幂等：队列空了再跑什么也不做。
"""
import json
import sys

SOURCE, TARGET = "erp_masters", "long"


def move(get_queue) -> int:
    """``get_queue(name)`` → RQ 队列。把 SOURCE 里排着的任务按原顺序挪到 TARGET，回挪了几件。"""
    source, target = get_queue(SOURCE), get_queue(TARGET)
    moved = 0
    for job in list(source.jobs):
        source.remove(job)
        target.enqueue_job(job)
        moved += 1
    return moved


def main():
    sys.path.insert(0, "/home/frappe/frappe-bench/apps/frappe")
    import frappe
    from frappe.utils.background_jobs import generate_qname, get_redis_conn
    from rq import Queue

    frappe.init(site="", sites_path="/home/frappe/frappe-bench/sites")
    # 直接按 RQ 原生队列名取（`generate_qname` 同 Frappe 排队时用的名字）：旧镜像的 bench 配置里可能已经没有 erp_masters，
    # Frappe 的 `get_queue` 不校验名字，但这里不依赖它
    connection = get_redis_conn()
    moved = move(lambda name: Queue(generate_qname(name), connection=connection))
    print(json.dumps({"moved": moved, "from": SOURCE, "to": TARGET}))


if __name__ == "__main__":
    main()
