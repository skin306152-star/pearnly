"""Cowork 录入的回放识别（回放压测，开关与时延见 ``pearnly_core.execution.replay``）。

开着时 ``workflow._recognize`` 不调谷歌、不交 Cloud Run，改从这里取结果：

1. 第一次进来：按原件内容（``Cowork Source File.sha256``）找这个租户以前**真识别过**的同一份原件、同样页数的识别单，
   记进检查点（「回放自哪一份、何时可取」），按时延分布抽一个等待秒数，抛 ``Deferred("replay_waiting", 秒数)``：识别线程空出来，
   到点由共用层调度放回队列 —— 与真识别交给 Cloud Tasks 后等回调同一种等法；
2. 到点再进来：把那一份存下的识别结果与证据原样交回，之后的校验、复核、入库、扣费、推送与真识别完全相同。

找不到以前真识别过的同一份原件：按谷歌明确拒绝收尾（``ocr.replay_missing``，不扣费），不编结果、也不去调谷歌。
"""
import json
import time

import frappe

from pearnly_core.execution import replay as core_replay
from pearnly_core.execution import work as core_work
from pearnly_core.execution.errors import Deferred
from pearnly_cowork.files import access as files_access
from pearnly_cowork.ocr.execution import access
from pearnly_cowork.ocr.providers.client import ProviderError

KEY = "cowork.replay"
CACHE = "cowork:ocr-replay:"
MISSING = "ocr.replay_missing"


def active():
    """这一份要回放：开关开着，或者这份已经开始回放（开关中途关掉也回放完，不转去真识别花钱）。"""
    return core_replay.active() or core_work.checkpoint(KEY) is not None


def recognize(doc, source, progress=None, *, now=time.time):
    """返回 ``(pipeline_json, evidence)``，与 ``forward.recognize`` 同一形状。"""
    marker = core_work.checkpoint(KEY)
    if marker is None:
        found = recorded(source.sha256, int(doc.page_count or 0), doc.name)
        if not found:
            raise ProviderError(MISSING)
        wait = core_replay.delay(core_replay.settings())
        # 状态 external：与交给 Cloud Run 同样当作「可能已付费」，排队时限按付过费的算（``work.run``）
        core_work.checkpoint(KEY, core_work.EXTERNAL, {"from": found, "due": now() + wait})
        raise Deferred("replay_waiting", wait)
    remaining = float(marker["body"]["due"]) - now()
    if remaining > 0:
        raise Deferred("replay_waiting", max(1, remaining))
    row = frappe.db.get_value(access.DOCTYPE, marker["body"]["from"], ["result_json", "provider_evidence"], as_dict=True)
    if not row or not row.result_json:
        raise ProviderError(MISSING)
    if progress and int(doc.page_count or 0) > 1:
        progress(int(doc.page_count))  # 读到最后一页（真识别的进度事件随回调补上，也是最后一下子到）
    # 证据末尾记上「回放自哪一份」：一眼看得出这份没真识别，之后找回放来源时也不会把它当真件
    return row.result_json, [*json.loads(row.provider_evidence or "[]"), {"replay": marker["body"]["from"]}]


def recorded(sha256, page_count, exclude):
    """这个租户以前真识别过的同一份原件（同样页数）的识别单名；没有返回 ``None``。

    回放出来的识别单不算（结果是抄来的），只认真识别过的；同一内容记一小时缓存，压测时几百份同一批原件不反复查库。"""
    if not sha256:
        return None
    key = f"{CACHE}{sha256}:{page_count}"
    try:
        cached = frappe.cache.get_value(key)
    except Exception:
        cached = None
    if cached:
        return cached
    rows = frappe.db.sql(f"""SELECT b.name FROM `tab{access.DOCTYPE}` b
        JOIN `tab{files_access.SOURCE}` s ON s.name = b.source_file
        WHERE s.sha256 = %s AND b.page_count = %s AND b.name != %s AND b.phase IN ('saved', 'not_invoice')
          AND IFNULL(b.result_json, '') != '' AND IFNULL(b.provider_evidence, '') NOT LIKE %s
        ORDER BY b.creation DESC LIMIT 1""", (sha256, page_count, exclude, '%"replay"%'))
    name = rows[0][0] if rows else None
    if name:
        try:
            frappe.cache.set_value(key, name, expires_in_sec=3600)
        except Exception:
            pass
    return name
