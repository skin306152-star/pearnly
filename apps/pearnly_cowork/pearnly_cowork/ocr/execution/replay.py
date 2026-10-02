"""Cowork 录入的回放识别（回放压测，开关与时延见 ``pearnly_core.execution.replay``）。

开着时 ``workflow._recognize`` 不调谷歌、不交 Cloud Run，改从这里取结果：

1. 第一次进来：按原件内容（``Cowork Source File.sha256``）找以前**真识别过**的同一份原件、同样页数的结果 —— 先找这个租户
   自己的识别单，再找共享回放库（``scripts/ocr-replay-corpus.py`` 从测试租户的真实件导出，各测试租户共用，见 :data:`LIBRARY`），
   记进检查点（「回放自哪一份、何时可取」），按时延分布抽一个等待秒数，抛 ``Deferred("replay_waiting", 秒数)``：识别线程空出来，
   到点由共用层调度放回队列 —— 与真识别交给 Cloud Tasks 后等回调同一种等法；
2. 到点再进来：把那一份存下的识别结果与证据原样交回，之后的校验、复核、入库、扣费、推送与真识别完全相同。

找不到以前真识别过的同一份原件：按谷歌明确拒绝收尾（``ocr.replay_missing``，不扣费），不编结果、也不去调谷歌。
"""
import json
import os
import re
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
# 共享回放库：bench 的 sites 目录下（各 Cowork 容器共用这个卷），一份真实件一个文件 ``<sha256>-<页数>.json``，
# 内容是 ``{"result_json", "provider_evidence", "site", "batch"}``。只有导出脚本往里写。
LIBRARY = "pearnly-ocr-replay"
ENTRY = re.compile(r"[0-9a-f]{64}-[0-9]{1,3}")


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
    row = _load(marker["body"]["from"])
    if not row or not row.get("result_json"):
        raise ProviderError(MISSING)
    if progress and int(doc.page_count or 0) > 1:
        progress(int(doc.page_count))  # 读到最后一页（真识别的进度事件随回调补上，也是最后一下子到）
    # 证据末尾记上「回放自哪一份」：一眼看得出这份没真识别，之后找回放来源时也不会把它当真件
    return row["result_json"], [*json.loads(row.get("provider_evidence") or "[]"), {"replay": marker["body"]["from"]}]


def recorded(sha256, page_count, exclude):
    """以前真识别过的同一份原件（同样页数）：``"batch:<识别单>"``（本租户）或 ``"library:<sha256>-<页数>"``（共享回放库）；
    都没有返回 ``None``。

    回放出来的识别单不算（结果是抄来的），只认真识别过的；同一内容记一小时缓存，压测时几百份同一批原件不反复查库。"""
    if not sha256 or not re.fullmatch(r"[0-9a-f]{64}", sha256):
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
    name = f"batch:{rows[0][0]}" if rows else None
    entry = f"{sha256}-{int(page_count)}"
    if not name and os.path.isfile(_library_path(entry)):
        name = f"library:{entry}"
    if name:
        try:
            frappe.cache.set_value(key, name, expires_in_sec=3600)
        except Exception:
            pass
    return name


def _library_path(entry):
    return os.path.join(frappe.local.sites_path, LIBRARY, entry + ".json")


def _load(reference):
    kind, _, value = str(reference).partition(":")
    if kind == "batch":
        return frappe.db.get_value(access.DOCTYPE, value, ["result_json", "provider_evidence"], as_dict=True)
    if kind == "library" and ENTRY.fullmatch(value):
        try:
            with open(_library_path(value), encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None
    return None
