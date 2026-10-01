"""一份多页文件各页同时读（2026-10-01，用户「100 / 1000 人都只等识别的时间」）。

引擎（Cowork ``run_invoice`` 的 ``map_pages``）把每页交给这里：每页一个线程，各自的站点上下文与数据库连接，带上同一个识别
工作单（检查点按行存，各页同时写互不覆盖）、提交人与界面语言；按页码顺序交回结果，任一页出错时等其余页结束后把页码最小的
那个异常抛出（已完成的调用都有检查点，续跑取回、不重复付费）。每读完一页回调 ``on_done(已读完页数)``，在该页线程的站点
上下文里调用，用于推按页进度。

开关：容器环境变量 ``PEARNLY_OCR_PAGE_THREADS``（默认 1 = 照旧一页一页读，``mapper`` 返回 None）。每个页线程占一个数据库
连接：开之前按 MariaDB ``max_connections`` 与同时在跑的识别份数核算。
"""
import contextvars
from concurrent.futures import ThreadPoolExecutor
import os
import threading

import frappe

MAX_PAGE_THREADS = 16


def page_threads():
    try:
        value = int(os.environ.get("PEARNLY_OCR_PAGE_THREADS") or 1)
    except ValueError:
        return 1
    return max(1, min(MAX_PAGE_THREADS, value))


def mapper(on_done=None):
    """当前识别工作单里用的按页执行方式；没开就返回 None（调用方照旧一页一页读）。"""
    limit = page_threads()
    if limit <= 1:
        return None
    from .work import context

    site, user = frappe.local.site, frappe.session.user
    lang = getattr(frappe.local, "lang", None)
    work_name = frappe.flags.get("pearnly_ocr_work")
    done, lock = [0], threading.Lock()

    def one(fn, number):
        frappe.init(site=site)
        frappe.connect()
        frappe.set_user(user)
        if lang:
            frappe.local.lang = lang
        try:
            with context(work_name):
                result = fn(number)
            frappe.db.commit()
            if on_done:
                with lock:
                    done[0] += 1
                    count = done[0]
                try:
                    on_done(count)
                except Exception:
                    pass  # 进度只作展示
            return result
        except BaseException:
            frappe.db.rollback()
            raise
        finally:
            frappe.destroy()

    def run(fn, numbers):
        numbers = list(numbers)
        with ThreadPoolExecutor(max_workers=min(limit, len(numbers)), thread_name_prefix="pearnly-ocr-page") as pool:
            futures = [pool.submit(contextvars.Context().run, one, fn, number) for number in numbers]
        error = next((future.exception() for future in futures if future.exception() is not None), None)
        if error is not None:
            raise error
        return [future.result() for future in futures]

    return run
