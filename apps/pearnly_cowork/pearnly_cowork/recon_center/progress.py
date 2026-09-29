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


# 每份文件的进度按字段分开存（缓存里的哈希）：识别任务与对账后台任务在不同进程里同时写，整份读改写会把别人刚写的
# 新状态盖回旧的（2026-09-30 压测第 25 轮 B01：格子从绿变回灰再变绿）。
STATES = {"waiting": 0, "queued": 1, "reading": 2, "done": 3, "failed": 3}


def get(file):
    try:
        raw = frappe.cache.hgetall(_key(file)) or {}
    except Exception:
        return {}
    return {(key.decode() if isinstance(key, bytes) else key): value for key, value in raw.items()}


def update(file, **values):
    """直接写这些字段（上传时的初值）。"""
    if not file:
        return
    try:
        for field, value in values.items():
            frappe.cache.hset(_key(file), field, value)
        frappe.cache.expire(frappe.cache.make_key(_key(file)), TTL)
    except Exception:
        pass


def advance(file, **values):
    """只往前走：页数取较大的（续跑时从检查点取回的调用照样算一遍，不翻倍、不倒退），状态只升不降，其余照写。"""
    if not file:
        return
    current = get(file)
    changed = {}
    for field, value in values.items():
        if isinstance(value, int) and not isinstance(value, bool):
            if value > int(current.get(field) or 0):
                changed[field] = value
        elif field == "state":
            if STATES.get(value, 0) >= STATES.get(current.get("state"), 0) and value != current.get("state"):
                changed[field] = value
        elif value != current.get(field):
            changed[field] = value
    update(file, **changed)


def unread(file, page):
    current = get(file)
    update(file, unread=sorted(set(current.get("unread") or []) | {int(page)}))


def latest(job, item):
    """「刚读出」：这一页读出几笔、最后一笔的日期 / 方向 / 金额（最多留 LATEST 条，新的在前）。"""
    if not job or not item:
        return
    items = [item] + [old for old in _read(_latest_key(job), []) if old != item]
    _write(_latest_key(job), items[:LATEST])


def latest_items(job):
    return _read(_latest_key(job), [])


# 本地拼行给行打的「这一笔是补算 / 纠正出来的」标记。
ADJUSTED = ("chain_amount_imputed", "chain_repaired", "review_required")


def _plain_date(value):
    """规范成 年-月-日；读出来的年份不像（两位年没换算成 1983 之类、差出一年以上）就不显示。"""
    import datetime

    text = str(value or "").strip()[:10]
    try:
        day = datetime.date.fromisoformat(text)
    except ValueError:
        return ""
    return text if abs(day.year - datetime.date.today().year) <= 1 else ""


def page_summary(document, name, page, amounts=True):
    """一页的结果 → 滚动条的一条：``{name, page, rows, date, kind, amount}``；没有流水回 None。

    ``amounts``：金额只在余额链已经算平的页上给（文字层、本地拼行）；3.8 逐页抽取的金额还没经余额链校正，
    可能是后来被纠正掉的读错值（2026-09-30 压测第 25 轮 B01：「存入 200,449.37」其实是 450），只说这一页读出几笔。"""
    entries = (document or {}).get("entries") or []
    if not entries:
        return None
    if any(entry.get(flag) for entry in entries for flag in ADJUSTED):
        # 这一页有余额链补算 / 自动更正的行：链「算平」是补出来的，金额不一定是原件上的（2026-09-30 压测第 26 轮 B01
        # IMG_2485：200,449.37 是为接上读错的余额补出来的），只说读出几笔。
        amounts = False
    last = entries[-1]
    kind, amount = "", ""
    for field in ("deposit", "withdrawal", "debit", "credit", "total", "subtotal", "amount") if amounts else ():
        value = str(last.get(field) or "").strip()
        if value and value not in ("0", "0.00"):
            kind, amount = field, value
            break
    return {"name": name, "page": int(page or 0), "rows": len(entries),
            "date": _plain_date(last.get("transaction_date") or last.get("date")) if amounts else "",
            "kind": kind, "amount": amount}


def legacy_summaries(legacy, name):
    """整份结果里余额链已平的页（文字层、本地拼行）→ 每页一条带金额；逐页抽取的页不在这里（抽取时已按页给过）。"""
    found = []
    for page in (legacy or {}).get("pages") or []:
        if not ((legacy or {}).get("text_layer") or (page.get("extraction_audit") or {}).get("local")):
            continue
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
            started = entry["read"] or entry["done"] or entry["state"] == "reading" or (row and row.status == "running")
            if row and row.status == "running" and entry["state"] != "reading":
                advance(item.get("file"), state="reading")  # 到过「读取中」就记下，让出续跑时不退回排队
            # 已经开始读的，识别任务到点让出续跑时照旧算「读取中」，不闪回「排队中」（2026-09-30 压测第 25 轮 BAY）。
            if row and row.status in ("queued", "deferred") and not started:
                entry["state"] = "queued"
                # 前面还有几份：本站点比它早建、还没做完的识别任务（真数，不估算）。
                entry["ahead"] = frappe.db.count(WORK, {"status": ["in", ("queued", "deferred", "running")],
                                                        "creation": ["<", row.creation]})
            elif row and (row.status == "running" or started):
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
