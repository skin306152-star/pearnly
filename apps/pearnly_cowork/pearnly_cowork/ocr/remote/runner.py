"""Cloud Run 上跑一份 Cowork 发票识别（ADR-0015）。不依赖 Frappe、不连我们的数据库或 Redis。

和服务器本机执行（``ocr.execution.workflow._recognize`` + ``providers.core.build_client``）的差别只在外部能力的实现：
- 付费调用的检查点存在存储桶（``Store``），键与本机同一算法；已完成的直接取回、残留「started」判结果不明，
  付费防重语义不变。同一份单据同一时刻只允许一个实例在跑（``Store.lease``）。
- 调谷歌用 Cloud Run 自己的服务账号令牌；不经我们的额度 Redis：被谷歌限流就把暂缓交回服务器（不原地等），
  由服务器按租户 / 全站名额和区域额度重新放行（见 ``forward``）。
- 每次付费调用前不再查站点权限（这里没有站点）：服务器在发出前和存结果前各复核一次（ADR-0015）。
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
import queue
import threading
import time
from datetime import date
from functools import partial

import requests

from pearnly_core.execution.errors import Deferred, Uncertain
from pearnly_cowork.ocr.engine import run_invoice
from pearnly_cowork.ocr.engine.config import config_from_environment
from pearnly_cowork.ocr.engine.contracts import ProviderFailure
from pearnly_cowork.ocr.engine.errors import EngineError
from pearnly_cowork.ocr.engine.fewshot import fewshot_for_text
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.providers.client import ProviderClient
from pearnly_cowork.ocr.remote import contract
from pearnly_cowork.ocr import originals as original_files, recon_reading

UPLOAD_STEP_SECONDS = 30  # 与 pearnly_core.execution.provider 同一口径
# 被谷歌限流时原地等：与 pearnly_core.execution.provider.post 在识别任务里同一规则（每步最多 20 秒、每次调用累计最多 60 秒；
# 调用方要求交回的（Gemini 全球入口被拒 → 转美国入口）不等）。超过就把暂缓交回服务器，整份稍后续跑。
WAIT_STEP_LIMIT = 20
WAIT_PER_CALL = 60
LEASE_SECONDS = contract.YIELD_SECONDS + 240
TICK_SECONDS = 20
TERMINAL = "cowork.terminal:"


def log(**fields):
    """一行结构化运行日志（Cloud Logging 按 JSON 解析）：只有计量与状态，没有单据内容、令牌或请求体。"""
    print(json.dumps({"severity": "INFO", **fields}, ensure_ascii=False), flush=True)


def checkpoint_key(payload, model):
    """与 ``pearnly_core.execution.work.checkpoint_key`` 同一算法。"""
    return hashlib.sha256((model + "\0" + json.dumps(payload, sort_keys=True)).encode()).hexdigest()


class Busy(Exception):
    """同一份单据已有另一个实例在跑（上一次请求断开后它可能还在跑）。"""


class Store:
    """一份识别任务（site + work）的检查点与租约。

    ``backend`` 提供四个原子操作（见 ``gcs.Bucket``）：``get(name) -> (value, generation) | None``、
    ``create(name, value) -> generation | None``（已存在就不写）、``put(name, value, if_generation=None) -> generation | None``
    （给了 generation 就只在没被别人改过时写）、``delete(name)``。
    """

    def __init__(self, backend, site, work):
        self._backend, self._prefix = backend, f"{site}/{work}/"
        self._cache, self._generation, self._lease = {}, {}, None
        self._lock = threading.Lock()  # 引擎可能多页同时读：缓存的读写要线程安全（每个调用各自一个对象）

    def _name(self, key):
        return self._prefix + "cp/" + hashlib.sha256(key.encode()).hexdigest() + ".json"

    def get(self, key):
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        found = self._backend.get(self._name(key))
        with self._lock:
            self._cache.setdefault(key, found[0] if found else None)
            self._generation.setdefault(key, found[1] if found else None)
            return self._cache[key]

    def put(self, key, state, body=None):
        value = {"key": key, "state": state, "body": body}
        if state == "started":
            # 只在记录没被别人动过时才能开始：两个实例同时跑同一调用，后到的写不进，判结果不明，不重复付费。
            self.get(key)
            with self._lock:
                known = self._generation.get(key)
            generation = (self._backend.put(self._name(key), value, if_generation=known) if known
                          else self._backend.create(self._name(key), value))
            if generation is None:
                raise Uncertain("Provider call already started elsewhere")
        else:
            generation = self._backend.put(self._name(key), value)
        with self._lock:
            self._cache[key], self._generation[key] = value, generation
        return value

    def acquire(self, now=time.time, seconds=LEASE_SECONDS):
        """``seconds``：这次最多读多久 + 余量（任务派发时让出得晚，租约跟着长，见 ``recognize``）。"""
        name, value = self._prefix + "lease", {"until": now() + seconds}
        generation = self._backend.create(name, value)
        if generation is not None:
            self._lease = (name, generation)
            return
        found = self._backend.get(name)
        if found and found[0].get("until", 0) > now():
            raise Busy()
        # 上一个实例没收尾就没了（实例被回收）：租约过期后接手；同时有两个来接手，只有一个写得进。
        generation = None if found is None else self._backend.put(name, value, if_generation=found[1])
        if generation is None:
            raise Busy()
        self._lease = (name, generation)

    def release(self):
        """只删自己那一版租约：租约过期后被别的实例接手了，就不去删别人的（2026-10-01 复查）。"""
        if self._lease:
            name, generation = self._lease
            self._lease = None
            remove = getattr(self._backend, "delete_generation", None)
            if remove:
                remove(name, generation)
            else:
                self._backend.delete(name)


class NotWarm(Exception):
    """预热抽取时发现某块读字还没完成（上一步预热没做成）：这一页不预热，留给最后那一遍。"""


class Provider:
    """``ProviderClient`` 的 post：与 ``pearnly_core.execution.provider._post`` 同一套检查点与超时规则，去掉额度 Redis。"""

    def __init__(self, store, http=None, sleep=None, cached_only=None):
        self._store = store
        self._local = threading.local()
        self._http = http
        self._sleep = sleep
        self._cached_only = cached_only  # 预热：这类调用只取检查点里已完成的，没有就不读（``NotWarm``）

    def _session(self):
        if self._http is not None:
            return self._http
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()  # 每个线程一个会话：证书库只解析一次，连接复用
        return self._local.session

    def post(self, url, headers, payload, timeout_s, model, bucket, *, rpm=None, wait=True, label=None,
             hand_back=(), shared_quota=False):
        waited = 0.0
        while True:
            try:
                return self._post(url, headers, payload, timeout_s, model)
            except Deferred as exc:
                delay = float(exc.delay or 1)
                if (not wait or delay > WAIT_STEP_LIMIT or waited + delay > WAIT_PER_CALL
                        or str(exc.reason).startswith(tuple(hand_back))):
                    raise
                log(event="provider_wait", reason=str(exc.reason), seconds=delay)
                (self._sleep or time.sleep)(delay)
                waited += delay

    def _post(self, url, headers, payload, timeout_s, model):
        key = checkpoint_key(payload, model)
        saved = self._store.get(key)
        if saved and saved["state"] == "done":
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(saved["body"]).encode()
            response._content_consumed = True
            return response
        if saved and saved["state"] == "started":
            raise Uncertain("Previous provider result is unknown")
        if self._cached_only and self._cached_only(url):
            if saved and saved["state"] == "rejected" and (saved.get("body") or {}).get("http") in (429, 503):
                raise Deferred("provider_" + str(saved["body"]["http"]), 5)  # 这个区域当时被限流：照原路换下一个区域
            raise NotWarm()
        timeout = max(1, min(180, float(timeout_s)))
        self._store.put(key, "started")
        sent = time.monotonic()
        deadline = sent + timeout
        host = url.split("/")[2] if "://" in url else ""
        try:
            response = self._session().post(url, headers=headers, json=payload,
                                            timeout=(UPLOAD_STEP_SECONDS, timeout), stream=True)
        except requests.RequestException as exc:
            log(event="provider_call", host=host, status="transport_error", seconds=round(time.monotonic() - sent, 2))
            raise Uncertain("Provider transport outcome unknown") from exc
        log(event="provider_call", host=host, status=response.status_code, seconds=round(time.monotonic() - sent, 2))
        if response.status_code in (429, 503):
            self._store.put(key, "rejected", {"http": response.status_code})
            response.close()
            raise Deferred("provider_" + str(response.status_code), _retry_after(response))
        try:
            raw = bytearray()
            for chunk in response.iter_content(65536):
                if time.monotonic() > deadline:
                    raise Uncertain("Provider response exceeded elapsed time limit")
                if len(raw) + len(chunk) > 8 * 1024 * 1024:
                    raise Uncertain("Provider response exceeded safe memory limit")
                raw.extend(chunk)
            response._content = bytes(raw)
            response._content_consumed = True
        except requests.RequestException as exc:
            raise Uncertain("Provider response was interrupted") from exc
        finally:
            response.close()
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                self._store.put(key, "rejected", {"http": 200, "error": "invalid_json"})
            else:
                self._store.put(key, "done", body)
        else:
            self._store.put(key, "rejected", {"http": response.status_code})
        return response


def _retry_after(response):
    try:
        return max(5.0, min(300.0, float(response.headers.get("Retry-After", ""))))
    except (TypeError, ValueError):
        return 5.0


def terminal_read(store, key):
    """与 ``providers.core.build_client`` 的 terminal_read 同一口径。"""
    saved = store.get(TERMINAL + key)
    if saved is None:
        checkpoint = store.get(key)
        if checkpoint and checkpoint.get("state") == "rejected":
            body = checkpoint.get("body") or {}
            status = body.get("http")
            if status not in (429, 503) and not body.get("deferred"):
                return {"refusal": {"code": "ocr.provider_invalid_response" if status == 200 else
                        "ocr.provider_auth" if status in (401, 403) else "ocr.provider_http_error",
                        "http_status": status, "provider_code": body.get("provider_code")},
                        "raw": None, "recovered_from_core_rejection": True}
        return None
    if saved.get("state") != "terminal":
        raise RuntimeError("invalid provider checkpoint")
    return saved["body"]


def build_client(environment, store, access_token, *, http=None, regions=None, cached_only=None):
    project = environment.get("GCP_PROJECT")
    provider = Provider(store, http, cached_only=cached_only)

    def require_context():
        return None  # 站点权限由服务器在发出前、存结果前复核（ADR-0015）

    return ProviderClient(environment, project=project, post=provider.post, checkpoint_key=checkpoint_key,
        reject_checkpoint=lambda key, body: store.put(key, "rejected", body),
        terminal_read=partial(terminal_read, store),
        terminal_write=lambda key, body: store.put(TERMINAL + key, "terminal", body),
        feedback=lambda *args: None, scope=lambda provider, project, location, model: f"{provider}:{location}",
        access_token=access_token, require_context=require_context, checkpoint_read=store.get,
        regions=regions, in_queue_job=lambda: False)


def page_threads():
    """一份多页文件同时读几页（Cloud Run 环境变量 ``OCR_PAGE_THREADS``，默认 1 = 一页一页读）。"""
    try:
        return max(1, min(16, int(os.environ.get("OCR_PAGE_THREADS") or 1)))
    except ValueError:
        return 1


def page_mapper(limit, on_done):
    """``run_invoice`` 的 ``map_pages``：与 ``pearnly_core.execution.pages.mapper`` 同一约定（不含站点上下文）——
    每页一个线程，按页码顺序交回结果；任一页出错等其余页结束后抛出页码最小的那个；每读完一页回调 ``on_done(已读完页数)``。"""
    done, lock = [0], threading.Lock()

    def one(fn, number):
        result = fn(number)
        with lock:
            done[0] += 1
            count = done[0]
        on_done(count)
        return result

    def run(fn, numbers):
        numbers = list(numbers)
        with ThreadPoolExecutor(max_workers=min(limit, len(numbers)), thread_name_prefix="ocr-page") as pool:
            futures = [pool.submit(one, fn, number) for number in numbers]
        error = next((future.exception() for future in futures if future.exception() is not None), None)
        if error is not None:
            raise error
        return [future.result() for future in futures]

    return run


def original_bytes(body, originals):
    """原件在原件桶里（浏览器直传，ADR-016）：按版本号读出、核对 sha256。读不到 / 对不上返回错误代码。"""
    value = body["original"]
    if originals is None:
        return None, "original_unavailable"
    try:
        found = originals.read_bytes(value["object"], value["generation"])
    except requests.RequestException:
        return None, "original_unavailable"
    if found is None:
        return None, "original_missing"
    content = found[0]
    if hashlib.sha256(content).hexdigest() != value["sha256"]:
        return None, "original_mismatch"
    return content, None


def inspect(body, originals):
    """浏览器直传的原件读一遍：与服务器收原件同一份检查（``ocr.originals.inspect``）。

    通过返回文件信息、版本号（之后按它读，内容不会变）和 Frappe 用的 MD5（``native_content_hash``）；
    不通过就删掉这个版本、返回错误代码（同一个对象名只能写一次，删掉后页面才能用同一个键重传）。"""
    try:
        value = contract.parse_inspect(body)
    except ValueError as exc:
        return {"status": "error", "code": "invalid_request", "detail": str(exc)}
    try:
        found = originals.read_bytes(value["object"])
    except requests.RequestException:
        return {"status": "error", "code": "original_unavailable"}
    if found is None:
        return {"status": "rejected", "code": "original_missing"}
    content, generation = found
    try:
        meta = original_files.inspect(body["filename"], content)
    except (original_files.Rejected, original_files.OcrInputError) as error:
        originals.delete_generation(value["object"], generation)
        return {"status": "rejected", "code": error.code}
    return {"status": "ok", "meta": meta, "generation": generation, "md5": hashlib.md5(content).hexdigest()}


def preview(body, originals):
    """原件桶里一份原件的一页预览：``(PNG 字节, None)`` 或 ``(None, 错误代码)``（与服务器渲染同一份 ``render_page``）。"""
    try:
        contract.parse_preview(body)
    except ValueError:
        return None, "invalid_request"
    content, failure = original_bytes(body, originals)
    if failure:
        return None, failure
    try:
        return original_files.render_page(body["file_kind"], content, body["page"]), None
    except original_files.Rejected as error:
        return None, error.code


# 识别读完后最多再等预览图存好多久（通常早就存完：画一页零点几秒，识别一份十几秒）。没存完的页服务器照旧现画。
PREVIEW_WAIT_SECONDS = 20


class Previews:
    """识别的同时把各页预览图存进原件桶（与服务器现画同一份 ``render_page``，同一原件同一页字节一致）。

    单独一个线程，只读原件字节，不碰识别用的页面对象和调用：识别结果与计费不受影响；这里出任何错都只是少存几页
    （服务器照旧现画）。已经存过的页（上一次让出前存的）只查一下版本号，不重画。"""

    def __init__(self, body, content, originals):
        spec = body["previews"]
        self._names = [(page, contract.preview_object(body["site"], spec["source"], spec["sha256"], page))
                       for page in range(1, body["page_count"] + 1)]
        self._kind, self._content, self._originals = spec["kind"], content, originals
        self._stored, self._lock = [], threading.Lock()
        self._thread = threading.Thread(target=self._run, name="ocr-previews", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        started = time.monotonic()
        try:
            for page, name in self._names:
                try:
                    found = self._originals.metadata(name)
                    if found is None:
                        picture = original_files.render_page(self._kind, self._content, page)
                        kind = original_files.preview_type(picture)
                        generation = self._originals.create_bytes(name, picture, kind)
                        found = (generation, kind) if generation is not None else self._originals.metadata(name)
                except original_files.Rejected:
                    continue  # 这一页本来就不能预览（太大 / 不支持）：服务器现画时给出同样的提示
                except requests.RequestException:
                    continue
                if found and found[1] in contract.PREVIEW_TYPES:
                    with self._lock:
                        self._stored.append({"page": page, "object": name, "generation": int(found[0]),
                                             "type": found[1]})
        except Exception:  # noqa: BLE001 - 预览只是省服务器的事，出错不影响识别
            import traceback

            traceback.print_exc()
        log(event="previews_stored", pages=len(self._stored), of=len(self._names),
            seconds=round(time.monotonic() - started, 2))

    def collect(self, wait=PREVIEW_WAIT_SECONDS):
        """存好的页（没存完的不等）；出错返回 ``None``（不报预览，识别结果照常交回）。"""
        try:
            self._thread.join(wait)
            with self._lock:
                pages = sorted(self._stored, key=lambda item: item["page"])
            return {"bucket": str(self._originals.name), "pages": pages}
        except Exception:  # noqa: BLE001
            return None


def recognize(body, backend, access_token, emit, *, orient, page_source, http=None, clock=time.monotonic,
              threads=None, originals=None):
    """跑一份发票识别，进度和结果都通过 ``emit(event)`` 交出。从不抛出：异常都变成 result 事件。"""
    try:
        content = contract.parse_request(body)
    except ValueError as exc:
        emit(contract.result("error", code="invalid_request", detail=str(exc)))
        return
    if content is None:
        content, failure = original_bytes(body, originals)
        if failure == "original_unavailable":
            emit(contract.result("deferred", reason="remote_original_unavailable", delay=15))
            return
        if failure:
            emit(contract.result("error", code=failure))
            return
    store = Store(backend, body["site"], body["work"])
    try:
        store.acquire(seconds=max(LEASE_SECONDS, int(body.get("yield_seconds", contract.YIELD_SECONDS)) + 600))
    except Busy:
        emit(contract.result("deferred", reason="remote_busy", delay=15))
        return
    if body["kind"] == "cowork.document":
        return _document(body, content, store, access_token, emit, orient=orient, page_source=page_source, http=http,
                         clock=clock)
    previews = None
    if body.get("previews") and originals:
        try:
            previews = Previews(body, content, originals).start()
        except Exception:  # noqa: BLE001 - 预览出错不影响识别
            previews = None
    try:
        environment, anchor = dict(body["environment"]), date.fromisoformat(body["anchor_date"])
        selected = invoice_policy(environment, body["policy"])
        config = config_from_environment(environment, selected)
        examples = body.get("examples")
        selector = partial(fewshot_for_text, examples_by_tax=examples["examples_by_tax"]) if examples else None
        started, cpu = clock(), time.thread_time()
        yield_seconds = body.get("yield_seconds", contract.YIELD_SECONDS)
        client = build_client(environment, store, access_token, http=http)
        model_call, vision_call = client.model_call, client.vision_call
        if body.get("variant") == "vat_check":
            model_call, vision_call = recon_reading.vat_callbacks(client)  # 与服务器本机执行同一份包装
        reached = [0]
        limit = page_threads() if threads is None else threads
        # 多页同时读时进度按「已读完几页」推（与服务器本机执行同一口径：读完 N 页 = 正在读第 N+1 页）。
        page_map = (page_mapper(limit, lambda count: emit(contract.page(count + 1)))
                    if body["page_count"] > 1 and limit > 1 else None)

        def guarded(callback):
            def invoke(*args, **kwargs):
                if clock() - started > yield_seconds:
                    raise Deferred("progress_yield", 1)
                page = kwargs.get("page_number")
                if page is None and args:
                    page = getattr(args[0], "page_number", None)
                if page_map is None and isinstance(page, int) and not isinstance(page, bool) and page > reached[0]:
                    reached[0] = page
                    emit(contract.page(page))
                return callback(*args, **kwargs)
            return invoke

        with page_source(content, body["filename"], expected_page_count=body["page_count"],
                         enable_text_path=selected.text_fast_path,
                         image_max_long_edge=int(environment.get("OCR_IMG_MAX_LONG_EDGE", "2400")),
                         orient=orient) as pages:
            result = run_invoice(pages, guarded(model_call), guarded(vision_call), config=config,
                                 anchor_year=anchor.year, today=anchor, fewshot_selector=selector, map_pages=page_map)
        # 线程 CPU 只在一页一页读时准（多页同时读时 CPU 花在页线程里）。
        log(event="invoice_done", pages=body["page_count"], seconds=round(clock() - started, 2), calls=len(result.calls),
            **({"cpu_seconds": round(time.thread_time() - cpu, 2)} if page_map is None else {"page_threads": limit}))
        stored = previews.collect() if previews else None
        emit(contract.result("done", pipeline=result.pipeline.model_dump_json(),
                             evidence=contract.evidence(result.calls), **({"previews": stored} if stored else {})))
    except Deferred as exc:
        emit(contract.result("deferred", reason=str(exc.reason), delay=exc.delay))
    except Uncertain as exc:
        emit(contract.result("uncertain", reason=str(exc)[:200]))
    except ProviderFailure as exc:
        emit(contract.result("refused", code=exc.code, http_status=exc.http_status, provider_code=exc.provider_code))
    except requests.RequestException as exc:
        # 检查点桶 / 原件桶重试后仍连不上（谷歌存储一时故障）：交回稍后续跑，不判程序错误（2026-10-01 半量压测）
        emit(contract.result("deferred", reason="remote_store_unavailable", delay=15))
        log(event="store_unavailable", detail=type(exc).__name__)
    except EngineError as exc:
        # 引擎的内容错误（整份读不出、不是发票……）：照原类型交回，服务器照本机执行同样处理（不是程序错误）
        emit(contract.result("error", code=type(exc).__name__, error_code=exc.code, stage=exc.stage,
                             page_number=exc.page_number, detail=exc.detail[:500]))
    except Exception as exc:  # noqa: BLE001 - 引擎 / 输入错误照本机的「workflow_error」处理，不重放付费调用
        import traceback

        traceback.print_exc()
        emit(contract.result("error", code=type(exc).__name__, detail=str(exc)[:500]))
    finally:
        try:
            store.release()
        except Exception:  # noqa: BLE001 - 租约到期自然释放
            pass


def _document(body, content, store, access_token, emit, *, orient, page_source, http, clock):
    """对账文档：与服务器本机 ``recon_center.document_ocr._read`` 同一流程；看板要的进度改成事件交回。"""
    from pearnly_cowork.ocr.engine.errors import ContentError
    from pearnly_cowork.ocr.enterprise import pipeline as enterprise

    try:
        environment, started = dict(body["environment"]), clock()
        client = build_client(environment, store, access_token, http=http, regions=body.get("regions"))
        yield_seconds = body.get("yield_seconds", contract.YIELD_SECONDS)

        def guarded(callback):
            def invoke(*args, **kwargs):
                if clock() - started > yield_seconds:
                    raise Deferred("progress_yield", 1)
                return callback(*args, **kwargs)
            return invoke

        def reading(callback):
            def invoke(*args, **kwargs):
                result = callback(*args, **kwargs)
                emit(contract.read(int(kwargs.get("expected_pages") or 1)))
                return result
            return invoke

        def extracting(callback):
            def invoke(request, *args, **kwargs):
                try:
                    response = callback(request, *args, **kwargs)
                except ContentError as error:
                    if error.code == enterprise.UNREAD_PAGE:
                        emit(contract.unread(int(getattr(request, "page_number", 0) or 0)))
                    raise
                emit(contract.extracted(int(getattr(request, "page_number", 0) or 0), response.text or "",
                                        reread=getattr(request, "stage", "") == enterprise.REREAD_STAGE))
                return response
            return invoke

        is_pdf = body["filename"].lower().endswith(".pdf")
        # 多页文件各页的第一遍抽取同时做（OCR_PAGE_THREADS，与发票同一个开关；自核与再读照旧按页序，见 enterprise.run）。
        limit = page_threads()
        page_map = page_mapper(limit, lambda count: None) if body["page_count"] > 1 and limit > 1 else None
        with page_source(content, body["filename"], expected_page_count=body["page_count"], enable_text_path=False,
                         image_max_long_edge=int(environment.get("OCR_IMG_MAX_LONG_EDGE", "2400")),
                         orient=orient) as source:
            legacy = recon_reading.read_document(source, content, is_pdf,
                None if is_pdf else recon_reading.image_mime(content), body["document_type"],
                reading(guarded(partial(client.document_ai_call, retry_unknown=True))),
                extracting(recon_reading.document_page_failure(guarded(partial(client.model_call, retry_unknown=True)))),
                map_pages=page_map)
        log(event="document_done", pages=body["page_count"], seconds=round(clock() - started, 2),
            page_threads=limit if page_map else 1)
        emit(contract.result("done", legacy=legacy))
    except Deferred as exc:
        emit(contract.result("deferred", reason=str(exc.reason), delay=exc.delay))
    except Uncertain as exc:
        emit(contract.result("uncertain", reason=str(exc)[:200]))
    except ProviderFailure as exc:
        emit(contract.result("refused", code=exc.code, http_status=exc.http_status, provider_code=exc.provider_code))
    except requests.RequestException as exc:
        emit(contract.result("deferred", reason="remote_store_unavailable", delay=15))
        log(event="store_unavailable", detail=type(exc).__name__)
    except EngineError as exc:
        emit(contract.result("error", code=type(exc).__name__, error_code=exc.code, stage=exc.stage,
                             page_number=exc.page_number, detail=exc.detail[:500]))
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        emit(contract.result("error", code=type(exc).__name__, detail=str(exc)[:500]))
    finally:
        try:
            store.release()
        except Exception:  # noqa: BLE001
            pass


def stream(body, backend, access_token, **options):
    """把 :func:`recognize` 变成按行输出的迭代器（WSGI 回复体）；长时间没新事件就补一行 tick 保持连接。"""
    events = queue.Queue()
    worker = threading.Thread(target=recognize, args=(body, backend, access_token, events.put), kwargs=options,
                              daemon=True)
    worker.start()
    while True:
        try:
            event = events.get(timeout=TICK_SECONDS)
        except queue.Empty:
            yield contract.line(contract.tick())
            continue
        yield contract.line(event)
        if event["event"] == "result":
            return


# ── 任务派发（Cloud Tasks / Cloud Workflows 交来的一份；见 ``contract`` 的「任务派发」）─────────────────────────────
PROGRESS_SECONDS = 5


class ProgressSender:
    """读的过程中每隔 ``PROGRESS_SECONDS`` 把新事件回调给站点（只作展示：送不到就算了，结果里带着全部事件）。"""

    def __init__(self, envelope, send, clock=time.monotonic):
        self._envelope, self._send, self._clock = envelope, send, clock
        self._events, self._sent, self._lock = [], 0, threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ocr-progress", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def add(self, event):
        with self._lock:
            self._events.append(event)

    def _flush(self):
        with self._lock:
            batch, seq = self._events[self._sent:self._sent + contract.MAX_PROGRESS_EVENTS], self._sent
        if not batch:
            return
        body = contract.callback("progress", site=self._envelope["site"], work=self._envelope["work"],
                                 dispatch=self._envelope["dispatch"], events=batch, seq=seq)
        try:
            delivered = self._send(body) == 200
        except Exception:  # noqa: BLE001 - 进度只作展示
            delivered = False
        if delivered:  # 没送到就不往前推，下次连同新事件一起再送（站点按顺序号去重）
            with self._lock:
                self._sent = seq + len(batch)

    def _run(self):
        while not self._stop.wait(PROGRESS_SECONDS):
            self._flush()

    def stop(self):
        self._stop.set()


def _task_parts(envelope, backend):
    """读回服务器存好的请求，核对它是这份任务的：``(请求, None)`` 或 ``(None, 错误代码)``。"""
    found = backend.get(envelope["request"]["object"])
    if found is None:
        return None, "task_request_missing"
    request, generation = found
    if generation != envelope["request"]["generation"]:
        return None, "task_request_changed"
    try:
        return contract.task_request(envelope, request), None
    except ValueError:
        return None, "task_request_invalid"


def task(envelope, backend, access_token, *, id_token, post=None, orient, page_source, originals=None, http=None,
         clock=time.monotonic):
    """Cloud Tasks（或 Workflows 最后一步）交来的一份：读完把结果写进检查点桶、回调站点。返回 HTTP 状态码：
    200 = 已交回（结果写好且站点收到了）；503 = 回调没送到（让 Cloud Tasks 稍后重试：结果已写好，重试时只补回调、不重读）；
    400 = 信封不合规（我们自己的程序错误）。识别本身的暂缓 / 结果不明 / 拒绝 / 出错都写进结果交回，不算失败。"""
    try:
        contract.parse_task(envelope)
    except ValueError as exc:
        log(event="task_invalid", detail=str(exc)[:200])
        return 400
    name = contract.task_object(envelope["site"], envelope["work"], envelope["dispatch"], "reply.json")
    try:
        return _task(envelope, name, backend, access_token, id_token=id_token, post=post, orient=orient,
                     page_source=page_source, originals=originals, http=http, clock=clock)
    except (requests.RequestException, OSError) as exc:  # 桶 / 元数据服务器一时连不上：让 Cloud Tasks / Workflows 稍后重试
        log(event="task_retry", detail=type(exc).__name__)
        return 503


# 站点收到回调后，这两种回复算送达：200（已记下）、404（站点已关任务派发，这份它会按原路自己接着读）。
# 其余（401 验签对不上、403 / 429 / 5xx、连不上）都让 Cloud Tasks / Workflows 稍后重试，不悄悄丢掉结果（2026-10-01 复查）。
DELIVERED = (200, 404)


def _task(envelope, name, backend, access_token, *, id_token, post, orient, page_source, originals, http, clock):
    found = backend.get(name)
    if found is None:
        body, failure = _task_parts(envelope, backend)
        events = []
        if failure:
            final = contract.result("error", code=failure)
        else:
            sender = None
            if envelope["progress"]:
                sender = ProgressSender(envelope, lambda payload: _callback(envelope, payload, id_token, post)).start()

            def emit(event):
                if event["event"] in ("result", "tick"):
                    return
                events.append(event)
                if sender:
                    sender.add(event)

            outcome = []
            recognize(body, backend, access_token, lambda event: outcome.append(event) if event["event"] == "result"
                      else emit(event), orient=orient, page_source=page_source, http=http, clock=clock,
                      originals=originals)
            if sender:
                sender.stop()
            final = outcome[-1] if outcome else contract.result("error", code="task_no_result")
            if final["status"] == "deferred" and final.get("reason") == "remote_busy":
                # 同一份另一个实例正在读（重复投递、上一次还没收尾）：不写结果（先写的赢，会把正在读的那份的真结果挡在外面），
                # 让 Cloud Tasks / Workflows 稍后再投；到时结果已写好就只补回调（2026-10-01 复查）。
                log(event="task_busy")
                return 503
        generation = backend.create(name, contract.reply(final, events))
        if generation is None:  # 同一份任务被重试、另一次先写好了
            found = backend.get(name)
            generation = found[1] if found else None
        if generation is None:
            return 503
        log(event="task_done", status=final["status"], events=len(events))
    else:
        generation = found[1]
    status = _callback(envelope, contract.callback("reply", site=envelope["site"], work=envelope["work"],
                       dispatch=envelope["dispatch"], reply={"object": name, "generation": generation}), id_token, post)
    log(event="task_callback", status=status)
    return 200 if status in DELIVERED else 503


def _callback(envelope, body, id_token, post):
    from pearnly_cowork.ocr.remote import gcs

    url = envelope["callback"]
    return (post or gcs.post_callback)(url, body, id_token(url))


def warm(body, backend, access_token, *, orient, page_source, originals=None, http=None, id_token=None, post=None):
    """Workflows 的预热一步：多页对账文档的第 n 块读字或第 n 页第一遍抽取，存进这份任务的检查点（不拿租约：同一份的
    各块 / 各页同时预热；同一个调用两边同时发，只有一边写得进「started」，另一边判结果不明后不付费、留给最后那一遍）。
    不交回结果，出错也只记日志：最后那一遍（``task``）照常把缺的补上。返回 ``{"status": …}``。"""
    try:
        contract.parse_warm(body)
    except ValueError as exc:
        return {"status": "invalid", "detail": str(exc)[:200]}
    envelope = body["task"]
    request, failure = _task_parts(envelope, backend)
    if failure:
        return {"status": "skipped", "reason": failure}
    content = None
    try:
        content = contract.parse_request(request)
    except ValueError:
        return {"status": "skipped", "reason": "task_request_invalid"}
    store = Store(backend, request["site"], request["work"])
    environment = dict(request["environment"])
    is_pdf = request["filename"].lower().endswith(".pdf")
    client = build_client(environment, store, access_token, http=http, regions=request.get("regions"),
                          cached_only=(lambda url: "documentai" in url) if body["stage"] == "extract" else None)
    started = time.monotonic()
    try:
        with page_source(content, request["filename"], expected_page_count=request["page_count"], enable_text_path=False,
                         image_max_long_edge=int(environment.get("OCR_IMG_MAX_LONG_EDGE", "2400")),
                         orient=orient) as source:
            sent = recon_reading.warm_document(source, content, is_pdf,
                None if is_pdf else recon_reading.image_mime(content), request["document_type"],
                # 预热不重发结果不明的调用（重发就是新的付费调用）：碰上正在进行的同一调用或结果不明就跳过，留给整份那一遍
                # 按它自己的规则处理（2026-10-01 复查）。
                partial(client.document_ai_call, retry_unknown=False),
                recon_reading.document_page_failure(partial(client.model_call, retry_unknown=0)),
                body["stage"], body["index"])
        status = "done" if sent else "skipped"
    except NotWarm:
        status = "not_ready"
    except Exception as exc:  # noqa: BLE001 - 预热出错不影响最后那一遍
        status = "error:" + type(exc).__name__
    log(event="warm", stage=body["stage"], index=body["index"], status=status,
        seconds=round(time.monotonic() - started, 2))
    if id_token and status == "done":
        # 告诉站点云上还在读（预热期间没有别的回调，对账等识别的时限要认得这段进展）；送不到无所谓
        try:
            _callback(envelope, contract.callback("alive", site=envelope["site"], work=envelope["work"],
                                                  dispatch=envelope["dispatch"]), id_token, post)
        except Exception:  # noqa: BLE001
            pass
    return {"status": status}


def report_failed(envelope, backend, *, id_token, post=None):
    """Workflows 这次执行重试用完也没交回：转告站点（回调 ``failed``），站点立刻重新派发，不再干等兜底。
    结果已经写好的（最后那次其实读完了、只是回调没送到）就改送结果。返回 200 / 503 / 400。"""
    try:
        contract.parse_task(envelope)
    except ValueError:
        return 400
    name = contract.task_object(envelope["site"], envelope["work"], envelope["dispatch"], "reply.json")
    try:
        found = backend.get(name)
    except (requests.RequestException, OSError):
        return 503
    if found is not None:
        body = contract.callback("reply", site=envelope["site"], work=envelope["work"], dispatch=envelope["dispatch"],
                                 reply={"object": name, "generation": found[1]})
    else:
        body = contract.callback("failed", site=envelope["site"], work=envelope["work"], dispatch=envelope["dispatch"])
    status = _callback(envelope, body, id_token, post)
    log(event="task_failed_reported", status=status)
    return 200 if status in DELIVERED else 503
