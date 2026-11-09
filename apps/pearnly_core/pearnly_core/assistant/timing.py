"""对话一轮的分段计时（批 A ②，2026-11-09）：新对话第一句约 24 秒后模型才收到（生产 nginx：建对话 13.7 秒、首次补拉 5.8 秒、
发送 3.9 秒），先量清每段再改流程。`Steps(日志名)` 用 `done(段名)` 记上一段到此刻，`log(开头, 事实…)` 写一行进这个日志，
只写段名、秒数和计数，不写消息原文、文件名、票面。

同上传口的 `files._Steps`（U0）：站点日志默认只记 ERROR（生产 `frappe.log_level` 没设），这一路要记下来，只把这个日志放到 INFO。
记不上不影响业务（计时失败不报错）。
"""
import logging
import time

import frappe


class Steps:
    def __init__(self, logger: str):
        self.logger = logger
        self._began = self._last = time.monotonic()
        self.marks = {}

    def done(self, step: str) -> None:
        now = time.monotonic()
        self.marks[step] = round(now - self._last, 3)
        self._last = now

    def log(self, head: str, **facts) -> None:
        total = round(time.monotonic() - self._began, 3)
        parts = [f"{key}={value}" for key, value in {**facts, **self.marks, "total": total}.items() if value is not None]
        try:
            logger = frappe.logger(self.logger)
            logger.setLevel(logging.INFO)
            logger.info(head + " " + " ".join(parts))
        except Exception:
            pass
