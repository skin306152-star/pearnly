"""Claude 批处理识别（ADR-018）：同价减半，结果几分钟到几小时回来，给文件夹自动录入这类不急的入口用。

流程（只多出「交一批、取结果」这一层，识别、检查闸、入库、扣费与实时完全同一套）：

1. ``service.submit(..., delivery="batch")`` 受理：冻结配置照常写在识别单上，阶段 ``batch_waiting``，**不建工作单**
   （不占租户在途名额，等几小时也不会过期），排一个 ``collect``。
2. ``collect``：用冻结的配置在本机跑一遍引擎（``workflow.run_local``，与实时同一段），每页要发的 Claude 请求只收集不发出，
   键与实时调用的检查点键相同（``AnthropicClient.batch_entry``）；交给 Anthropic 一批，批号记在识别单上。
3. ``tick``（每 5 分钟）：查批次；结束了就把成功的结果按键预先写进这份识别的检查点（``prefill``），再建工作单（``release``）。
4. 工作单照常跑（批处理交付固定在本机，``workflow._recognize``）：每页从检查点取回结果，不再付费；没拿到结果的页
   （出错、过期、取消）在这一遍里照常实时调用，结果照出、按原价算。

任何一步出不了批（引擎不是 Claude、要谷歌读字、收集失败、提交结果不明、超过 26 小时）都退回实时：直接建工作单。
"""
import json
import time

import frappe
import requests

from pearnly_core.execution import work as core_work
from pearnly_core.execution.errors import QueueFull
from pearnly_cowork.ocr.execution import service, store, workflow
from pearnly_cowork.ocr.providers import anthropic_batch
from pearnly_cowork.ocr.providers.anthropic import is_claude
from pearnly_cowork.ocr.providers.client import ProviderError

DOCTYPE = "Cowork OCR Batch"
# 收集任务没跑（部署 / 重启时丢了）多久后重排；提交结果不明（交了但没记下批号）多久后改走实时。
STALE_SECONDS = 30 * 60
# Anthropic 保证 24 小时内结束；再久还没结束就不等了，改走实时。
GIVE_UP_SECONDS = 26 * 3600
TICK_LIMIT = 200


class _Collected(Exception):
    """这一页的请求已收下（不发出）：让引擎停在这一页。"""


class _NeedsRealtime(Exception):
    """这份要调 Claude 以外的接口（谷歌读字 / 模型）：不能整份走批处理。"""


def _now():
    return time.time()


def _state(doc):
    try:
        value = json.loads(doc.get("provider_batch") or "{}")
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _save_state(doc, **values):
    state = _state(doc)
    state.update({key: value for key, value in values.items()})
    doc.provider_batch = json.dumps({key: value for key, value in state.items() if value is not None},
                                    sort_keys=True)
    store.save(doc)
    frappe.db.commit()
    return state


def _claude(frozen):
    client = workflow.local_client(frozen).claude
    if client is None:
        raise _NeedsRealtime()
    return client


def collect(batch_name):
    """收集这份识别每页要发的 Claude 请求并交一批。只在 ``batch_waiting`` 且还没交过时做。"""
    doc = store.read(batch_name, lock=True, write=True, authorize=False)
    state = _state(doc)
    if doc.phase != service.BATCH_WAITING or state.get("id") or state.get("released"):
        return {"skipped": doc.phase}
    if state.get("submitting"):
        # 上一次交到一半（进程被杀）：Anthropic 收没收到不知道，不再交一次，改走实时（最坏多付一次批处理的钱）。
        return release(doc, "submission_unknown")
    frozen = json.loads(doc.settings_json)
    examples = json.loads(doc.examples_json) if doc.examples_json else None
    entries = {}
    try:
        with store.actor(doc.actor):
            source = workflow.verify(doc, frozen, examples)
            claude = _claude(frozen)

            def take(request):
                if not is_claude(getattr(request, "model", None)):
                    raise _NeedsRealtime()
                key, body = claude.batch_entry(request)
                entries[key] = body
                raise _Collected()

            def no_vision(*args, **kwargs):
                raise _NeedsRealtime()

            def every_page(fn, numbers):
                # 每页都收一遍（不并行：渲染库不是线程安全的，收集也不急），收完再停下引擎。
                for number in numbers:
                    try:
                        fn(number)
                    except _Collected:
                        pass
                raise _Collected()

            try:
                workflow.run_local(doc, frozen, examples, source, take, no_vision, every_page)
            except _Collected:
                pass
            else:
                return release(doc, "nothing_to_batch")  # 引擎没调模型就出了结果：直接照常跑
    except _NeedsRealtime:
        return release(doc, "not_batchable")
    except frappe.PermissionError:
        raise
    except Exception:
        frappe.log_error(title="cowork ocr batch collect", message=frappe.get_traceback())
        return release(doc, "collect_failed")
    if not entries:
        return release(doc, "nothing_to_batch")
    _save_state(doc, submitting=_now())
    try:
        created = anthropic_batch.create(requests.post, claude.target, claude.headers(), list(entries.items()))
    except ProviderError as error:
        # 明确拒绝（额度、格式）：没交上，改走实时（实时那边同样被拒会照常报出来）。
        return release(doc, "batch_refused:" + str(error.code))
    except (requests.RequestException, ValueError):
        # 结果不明：留着 submitting，等 ``tick`` 过了时限改走实时（不在这里重交，免得交两批）。
        return {"submitting": doc.name}
    _save_state(doc, id=created.get("id"), keys=sorted(entries), submitted=_now(), submitting=None)
    return {"batch": created.get("id"), "requests": len(entries)}


def tick():
    """每 5 分钟（hooks ``scheduler_events``）：推进等批处理的识别。没有等着的就只有一次查询。"""
    names = frappe.get_all(DOCTYPE, filters={"phase": service.BATCH_WAITING}, pluck="name",
                           order_by="creation asc", limit=TICK_LIMIT)
    for name in names:
        try:
            advance(name)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="cowork ocr batch", message=frappe.get_traceback())


def advance(name):
    doc = store.read(name, lock=True, write=True, authorize=False)
    if doc.phase != service.BATCH_WAITING:
        return None
    state = _state(doc)
    if state.get("ready"):
        return release(doc, state.get("reason"))  # 上次结果已填好、只是建工作单时队列满：再试
    age = _now() - frappe.utils.get_datetime(doc.creation).timestamp()
    if not state.get("id"):
        if state.get("submitting") and _now() - float(state["submitting"]) > STALE_SECONDS:
            return release(doc, "submission_unknown")
        if not state.get("submitting") and age > STALE_SECONDS:
            frappe.db.commit()
            frappe.enqueue("pearnly_cowork.ocr.execution.batch.collect", queue="long", timeout=600,
                           job_id="cowork-ocr-batch-" + doc.name, deduplicate=True, batch_name=doc.name)
        return None
    if age > GIVE_UP_SECONDS:
        return release(doc, "batch_timeout")
    claude = _claude(json.loads(doc.settings_json))
    batch = anthropic_batch.retrieve(requests.get, claude.target, claude.headers(), state["id"])
    if batch.get("processing_status") != anthropic_batch.ENDED:
        return None
    found = anthropic_batch.results(requests.get, claude.target, claude.headers(), batch)
    keys = set(state.get("keys") or ())
    messages = {key: content for key, (kind, content) in found.items()
                if kind == anthropic_batch.SUCCEEDED and key in keys}
    filled = prefill(doc.work_name, messages)
    return release(doc, None, filled=filled, missing=len(keys) - len(messages))


def prefill(work_name, messages):
    """把批处理结果按检查点键写进这份识别（还没建的）工作单的检查点：之后实时那条路发同一个请求时直接取回、不付费。
    用 Core 的 ``checkpoint``（与实时调用写检查点同一个入口），只是把当前工作单临时指到这一份。"""
    previous = frappe.flags.get("pearnly_ocr_work"), frappe.flags.get("pearnly_ocr_checkpoints")
    frappe.flags.pearnly_ocr_work, frappe.flags.pearnly_ocr_checkpoints = work_name, None
    filled = 0
    try:
        for key, message in sorted(messages.items()):
            saved = core_work.checkpoint(key)
            if not saved or saved.get("state") != "done":
                core_work.checkpoint(key, "done", message)
                filled += 1
    finally:
        frappe.flags.pearnly_ocr_work, frappe.flags.pearnly_ocr_checkpoints = previous
    return filled


def release(doc, reason, **info):
    """结束等待、建工作单照常识别（结果已预填的页直接取回，其余页实时）。队列满就留在等待里，下一轮再建。"""
    _save_state(doc, ready=True, reason=reason, released=None, **info)
    doc.phase = "admitted"
    store.save(doc)
    frappe.db.commit()
    try:
        service.enqueue_work(doc)
    except QueueFull:
        frappe.db.rollback()
        doc = store.read(doc.name, lock=True, write=True, authorize=False)
        doc.phase = service.BATCH_WAITING
        store.save(doc)
        frappe.db.commit()
        return {"queued": False, "reason": "queue_full"}
    doc = store.read(doc.name, lock=True, write=True, authorize=False)
    _save_state(doc, ready=None, released=_now())
    return {"queued": True, "reason": reason}


def status(batch_name):
    """调用方（文件夹自动录入）看进度：识别单的状态投影（``state`` 先是 ``batch_waiting``，之后与实时相同）。"""
    return store.project(store.read(batch_name))
