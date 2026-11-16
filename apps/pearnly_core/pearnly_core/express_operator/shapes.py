"""代录接口回包的纯整理（多产品共用，Code 版 CC 评估 2026-11-04 第 4、5、6 节）：任务里上传的资料各自什么状态、开始前写进哪份
账套。无框架、无 IO：产品给原料，这里只按同一个形状整理，页面只认这里的字段。

上传状态（对话里每份文件一张卡片，第 5 节）：页面自己显示「上传中」（字节进度只有浏览器知道），之后都按服务端：

- ``queued``：排队中（交了识别，还没开始读）；
- ``reading``：识别中，``pages_done / page_count`` 是读到第几页；
- ``done``：已识别，``documents`` 是识别出的单（可能 0 张：不是单据）；
- ``failed``：识别失败（``code`` 是产品的失败码）；读不到这份识别记录（删了、没权限了）也落这里，``code`` = ``unavailable``。
"""
from __future__ import annotations

UPLOAD_STATES = ("queued", "reading", "done", "failed")
UNAVAILABLE = "unavailable"
UPLOADS_MAX = 200  # 一个任务最多记这么多份上传（同一批最多交 200 张）
TEXT_MAX = 140
TARGET_FIELDS = ("account_set", "account_set_title", "account_set_path", "connection", "connection_title")


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _text(value, limit: int = TEXT_MAX) -> str:
    return str(value or "").strip()[:limit]


def document_view(row, period: str) -> dict:
    """识别出的一张单：编号、单号、日期、对方；``in_period`` 单据日期在任务月份内；``listed`` 会出现在这个任务的资料里（月份内、
    还在等着录入，同产品资料来源 ``sources`` 的口径）。"""
    date = _text(row.get("date"), 10)
    in_period = bool(date) and bool(period) and date[:7] == str(period)
    return {"doc_id": _text(row.get("doc_id")), "ref": _text(row.get("ref")), "date": date, "party": _text(row.get("party")),
            "in_period": in_period, "listed": in_period and bool(row.get("waiting"))}


def upload_view(row, found, period: str) -> dict:
    """一份上传：子表那一行（``row``）加产品现读的识别状态（``found``，没有 = 读不到）。"""
    found = found if isinstance(found, dict) else {}
    state = found.get("state")
    code = _text(found.get("code"), 80)
    if state not in UPLOAD_STATES:
        state, code = "failed", code or UNAVAILABLE
    page_count = _int(found.get("page_count"))
    pages_done = page_count if state == "done" else min(_int(found.get("pages_done")), page_count)
    documents = [document_view(item, period) for item in found.get("documents") or []
                 if isinstance(item, dict) and item.get("doc_id")] if state == "done" else []
    return {"request_key": _text(row.get("request_key")), "source": _text(row.get("source")),
            "filename": _text(row.get("filename"), 255), "size": _int(row.get("size")), "batch": _text(row.get("batch")),
            "uploaded_at": str(row.get("uploaded_at") or ""), "state": state, "pages_done": pages_done,
            "page_count": page_count, "code": code if state in ("failed", "queued") else "", "documents": documents}


def unstarted(row) -> dict | None:
    """已发出、还没交成识别的一行此刻的状态（``upload_view`` 的 ``found``）：交不成有原因 → 失败；还在等后台交 → 排队中。
    已交过识别（有任务号）或还没发出的回 None（照产品现读 / 不出卡片）。"""
    if row.get("batch") or not row.get("uploaded_at"):
        return None
    code = str(row.get("start_code") or "")
    return {"state": "failed", "code": code} if code else {"state": "queued"}


GROUPS = ("purchase", "sales", "expense", "payment", "skip")  # 资料区的组（页面 DOC_GROUPS 同一份）


def moves(row, movable: bool) -> list:
    """资料区这张单能拖到哪几组：产品没声明 ``regroup`` 的一律不能拖；声明了的只认已知的组、去掉它自己这组。"""
    if not movable or not isinstance(row.get("moves"), (list, tuple)):
        return []
    own = str(row.get("group") or "")
    return [group for group in dict.fromkeys(str(item) for item in row["moves"]) if group in GROUPS and group != own]


FAILED_WORK = ("failed", "uncertain", "cancelled")  # 共用识别队列落定没出结果的状态


def upload_state(*, done: bool, failed: bool, reading: bool, pages_done=0, page_count=0, code="") -> dict:
    """上传卡片的状态（两产品同一个判法，A 批 1）：产品只给「识别完了 / 没出结果 / 已经开始读」与页数，形状在这里定。纯函数。"""
    page_count = _int(page_count)
    if done:
        return {"state": "done", "pages_done": page_count, "page_count": page_count, "code": ""}
    if failed:
        return {"state": "failed", "pages_done": _int(pages_done), "page_count": page_count, "code": _text(code) or UNAVAILABLE}
    return {"state": "reading" if reading else "queued", "pages_done": _int(pages_done), "page_count": page_count,
            "code": _text(code)}


def work_state(record_status: str, work_status: str, code: str = "") -> dict:
    """产品的识别记录状态 + 共用识别队列那件的状态 → 上传卡片状态（ERP 收单记录：queued / ready / confirmed / failed）。纯函数。"""
    record_status, work_status = str(record_status or ""), str(work_status or "")
    return upload_state(done=record_status in ("ready", "confirmed"),
                        failed=record_status == "failed" or work_status in FAILED_WORK,
                        reading=work_status == "running", code=code or (work_status if work_status in FAILED_WORK else ""))


def target_view(target) -> dict | None:
    """一张单开始后写进哪份账套（产品预检给的原料）：没有账套键回 None。``writers``：报过能写这份账套的电脑（只有 ERP 给）。"""
    if not isinstance(target, dict) or not _text(target.get("account_set"), 255):
        return None
    out = {key: _text(target.get(key), 255) for key in TARGET_FIELDS}
    # 没有账套名写位置；账套键是内部编号（ERP / Cowork 都是 book_key），不给人看（测试机1 11-13）
    out["account_set_title"] = out["account_set_title"] or out["account_set_path"]
    writers = target.get("writers")
    if isinstance(writers, (list, tuple)):
        out["writers"] = sorted({_text(item) for item in writers if _text(item)})
    return out


def precheck_targets(documents) -> list:
    """预检里各张单要写进的账套合成几份（同一连接、同一账套算一份），张数多的在前；``count`` 是几张单写进这份。"""
    found = {}
    for doc in documents or []:
        target = target_view(doc.get("target")) if isinstance(doc, dict) else None
        if target is None:
            continue
        entry = found.setdefault((target["connection"], target["account_set"]), {**target, "count": 0})
        entry["count"] += 1
    return sorted(found.values(), key=lambda item: -item["count"])
