"""对账识别看板的真实进度（用户 2026-09-30：「不想看这个大白屏，起码要知道他在干嘛，有点动态好玩的东西展示」）。

只记真实发生的事，不估算、不按时间走：
- 每份文件几页（上传时数出）；
- 读字读了几页（Document AI 每块读完加这一块的页数）、整理完几页（本地拼行一次整份、或 3.8 逐页抽取每页完成加一）；
- 哪几页没读出（抽取重发后仍结果不明）；电子 PDF 走文字层（不花钱）一次整份完成；
- 每页读出的最后一笔（日期、方向、金额）做「刚读出」滚动条，是账单上的真数。

存在缓存里（24 小时），键按上传后存成的私有 File 名；识别任务（Work）与对账后台任务都往里写，页面经 ``jobs.status`` 读。
"""
import frappe

TTL = 24 * 3600


def _read(key, default):
    """看板进度只作展示：缓存不可用（离线、清缓存中）当作没有，不影响识别与对账。"""
    try:
        value = frappe.cache.get_value(key)
    except Exception:
        return default
    return value if value is not None else default


def _write(key, value):
    try:
        frappe.cache.set_value(key, value, expires_in_sec=TTL)
    except Exception:
        pass
LATEST = 6
SPREADSHEETS = ("xlsx", "xls", "xlsm", "csv", "tsv", "txt")


def _key(file):
    return f"cowork:recon-file:{file}"


def _latest_key(job):
    return f"cowork:recon-latest:{job}"


def page_count(filename, content):
    """上传的这份有几页：PDF 数页，照片 1 页，表格不按页（0）。"""
    extension = str(filename or "").lower().rsplit(".", 1)[-1]
    if extension in SPREADSHEETS:
        return 0
    if extension == "pdf" or b"%PDF" in content[:1024]:
        import pymupdf

        try:
            with pymupdf.open(stream=content, filetype="pdf") as document:
                return document.page_count
        except Exception:
            return 0
    return 1


def get(file):
    return _read(_key(file), {}) or {}


def update(file, **values):
    if not file:
        return {}
    current = get(file)
    current.update(values)
    _write(_key(file), current)
    return current


def add(file, field, count=1):
    """读字 / 整理的页数往上加（只加不减：续跑时从检查点取回的调用照样算一遍，所以取较大的）。"""
    current = get(file)
    return update(file, **{field: int(current.get(field) or 0) + int(count)})


def unread(file, page):
    current = get(file)
    pages = sorted(set(current.get("unread") or []) | {int(page)})
    return update(file, unread=pages)


def latest(job, item):
    """「刚读出」：这一页读出几笔、最后一笔的日期 / 方向 / 金额（最多留 LATEST 条，新的在前）。"""
    if not job or not item:
        return
    items = [item] + [old for old in _read(_latest_key(job), []) if old != item]
    _write(_latest_key(job), items[:LATEST])


def latest_items(job):
    return _read(_latest_key(job), [])


def page_summary(document, name, page):
    """一页抽取 / 拼行的结果 → 滚动条的一条：``{name, page, rows, date, kind, amount}``；没有流水回 None。"""
    entries = (document or {}).get("entries") or []
    if not entries:
        return None
    last = entries[-1]
    kind, amount = "", ""
    for field in ("deposit", "withdrawal", "debit", "credit", "total", "subtotal", "amount"):
        value = str(last.get(field) or "").strip()
        if value and value not in ("0", "0.00"):
            kind, amount = field, value
            break
    return {"name": name, "page": int(page or 0), "rows": len(entries),
            "date": str(last.get("transaction_date") or last.get("date") or "")[:10], "kind": kind, "amount": amount}


def legacy_summaries(legacy, name):
    """整份结果（本地拼行 / 文字层一次出来的）→ 每页一条。"""
    found = []
    for page in (legacy or {}).get("pages") or []:
        item = page_summary(page.get("document"), name, page.get("page_number"))
        if item:
            found.append(item)
    return found


def view(job, files):
    """页面要的进度：每份文件的页数、读字 / 整理到第几页、没读出的页、是不是走的文字层、识别排队情况；外加「刚读出」。"""
    from pearnly_cowork.ocr.execution.access import WORK

    shown = []
    for item in files or []:
        current = get(item.get("file"))
        entry = {"name": item.get("name"), "side": item.get("side"), "pages": int(item.get("pages") or 0),
                 "read": min(int(current.get("read") or 0), int(item.get("pages") or 0) or 10 ** 6),
                 "done": int(current.get("done") or 0), "unread": current.get("unread") or [],
                 "via": current.get("via") or "", "state": current.get("state") or "waiting", "ahead": 0}
        work = current.get("work")
        if work and entry["state"] not in ("done", "failed"):
            row = frappe.db.get_value(WORK, work, ["status", "creation"], as_dict=True)
            if row and row.status in ("queued", "deferred"):
                entry["state"] = "queued"
                # 前面还有几份：本站点比它早建、还没做完的识别任务（真数，不估算）。
                entry["ahead"] = frappe.db.count(WORK, {"status": ["in", ("queued", "deferred", "running")],
                                                        "creation": ["<", row.creation]})
            elif row and row.status == "running":
                entry["state"] = "reading"
        shown.append(entry)
    return {"files": shown, "latest": latest_items(job)}


# ── 进行中的对账（列表页顶上显示，离开页面再回来也能接上）──────────────────────────


def _active_key(user):
    return f"cowork:recon-active:{user}"


def active_add(user, job, kind, title):
    items = [item for item in _read(_active_key(user), []) if item.get("job") != job]
    items.insert(0, {"job": job, "kind": kind, "title": str(title or "")[:140]})
    _write(_active_key(user), items[:20])


def active_remove(user, job):
    items = [item for item in _read(_active_key(user), []) if item.get("job") != job]
    _write(_active_key(user), items)


def active(user, kind):
    return [item for item in _read(_active_key(user), []) if item.get("kind") == kind]


LATER_STAGES = ("matching", "saving", "done")


def job_view(job, state):
    """对账后台任务的看板：阶段 + 每份文件的真实进度 + 刚读出。不经识别的文件（表格、带文字层按行读得出的 PDF）
    读完就算完成：核对阶段开始时一定都读完了。"""
    files = []
    for item in state.get("files") or []:
        current = get(item.get("file"))
        files.append({**item, "name": item.get("name") or current.get("name") or "",
                      "pages": int(item.get("pages") or current.get("pages") or 0)})
    found = view(job, files)
    if state.get("stage") in LATER_STAGES or state.get("state") == "done":
        for entry in found["files"]:
            if entry["state"] in ("waiting", "reading", "queued"):
                entry.update(state="done", read=entry["pages"], done=max(entry["done"], entry["pages"] - len(entry["unread"])))
    return found
