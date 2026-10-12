"""对账结果页的存档：结果页的数据生成一次存起来，打开时直接读（2026-10-01 用户经压测窗口："不要再改一点修一点"）。

原来每打开一次结果页都从存着的输入重算对账、生成整本复核工作簿、读回来逐格画成页面数据（`sheets.load`）：557 行的银行对账
空闲时一次 10 秒，压测时更慢，页面停在骨架屏上，用户以为"进不去"。现在：

- 对账出结果时（后台任务里，`jobs`）按发起时选的语言生成一次存起来；保存修改、回答预设问题后（它们都回 `sheets.load`）
  按新结果重新生成、存回；
- 打开结果页先按指纹找存档，对得上就直接回；只是代码换了（部署）先回旧存档、后台重建一份；结果或预设变了现算一次再存（同一套
  同一时间只算一份）。没有存档的旧任务第一次打开时现算并补存。每次激活 Cowork 后补算一遍（`activate-product.sh`）。

指纹包括：对账类型、任务、语言、任务的修改时间与修订号、可能影响这一套的预设（同一家银行、通用版式按账号尾号 / 同一个本公司税号，条数与最后修改时间）、要用原文件重跑的
问题记录的修改时间、生成结果页的代码本身（这些源文件的内容摘要）。所以存档只会在内容确实一样时被用上，与现算逐格一致。

导出 Excel 不走这里：导出读的是任务完成时存下的结果行（各自的 `export`），本来就不重算对账。

存在 ``Cowork Recon View``（没有任何角色的权限，只由这里读写）；任务删除时一起删（`drop`）。
"""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import frappe

DOCTYPE = "Cowork Recon View"
PRESET = "Cowork Recon Preset"
RERUN = "Cowork Recon Rerun"
_code = None


def _code_fingerprint():
    """生成结果页的代码（结果页、对账计算与工作簿、文案）的内容摘要：换了版本的代码不会用旧代码生成的存档。进程内算一次。
    只算影响页面内容的代码；存档怎么存、怎么补（本文件）不算 —— 以前把本文件也算进去，2026-10-02 本文件加一行提交就让全部存档
    作废、部署后补算把所有对账重算一遍，和压测重叠吃了约 490 CPU 秒。"""
    global _code
    if _code is None:
        root = Path(__file__).resolve().parents[1]
        files = sorted([*root.joinpath("reconciliation").rglob("*.py"), root / "recon_center" / "sheets.py",
                        *root.joinpath("translations").glob("*.csv")])
        digest = hashlib.sha256()
        for path in files:
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
        _code = digest.hexdigest()
    return _code


def _preset_scope(kind, doc):
    """指纹里只算可能影响这一套的预设（``LIKE`` 模式）：银行按同一家银行（``bank|<银行>|…``，账户、科目对应都在下面），收入按
    同一个本公司税号（``income|<税号>|…``）；销项税没有预设（回 ``None``）。认不出银行 / 税号时退回本工作区这类对账的全部预设。
    原来是整个工作区同类预设：同事答一次别的账户的预设，同类全部存档失效（独立复查 2026-10-01）。
    认不出银行的通用版式按尾号收窄（``bank|GENERIC|<尾号>|…``）：表格账单多是这一类，按银行算会让一个账户答题就作废全部通用版式
    的存档；没有尾号的通用版式本来就没有预设（回 ``None``，复查 2026-10-13）。"""
    if kind == "vat":
        return None
    from pearnly_cowork.reconciliation import bank_recon_service, presets, revision

    if kind == "bank":
        bank = str(doc.get("bank_code") or "").strip().upper().replace("\\", "").replace("%", "").replace("_", "\\_")
        if bank == presets.GENERIC:
            tail = bank_recon_service.task_tail(doc)
            return f"bank|{presets.GENERIC}|{tail}|%" if tail else None
        return f"bank|{bank}|%" if bank else "%"

    try:
        prefix = presets.income_prefix(revision.income_tax_id(doc))
    except Exception:
        prefix = ""
    return f"{prefix}%" if prefix else "%"


def fingerprint(kind, doc, lang, version):
    """「数据:代码」两段：数据段是任务、语言、修订、预设、重跑记录；代码段是生成结果页的代码与文案。"""
    scope = _preset_scope(kind, doc)
    if scope is None:
        presets = (0, "")
    else:
        presets = frappe.db.sql(f"""SELECT COUNT(*), MAX(modified) FROM `tab{PRESET}`
            WHERE workspace=%s AND recon_kind=%s AND scope LIKE %s""", (doc.workspace, kind, scope))[0]
    rerun = frappe.db.get_value(RERUN, doc.name, "modified") if frappe.db.table_exists(RERUN) else None
    data = hashlib.sha256(json.dumps(
        [kind, doc.name, lang, str(doc.modified), str(version), int(presets[0] or 0), str(presets[1] or ""),
         str(rerun or "")], separators=(",", ":")).encode()).hexdigest()
    return f"{data}:{_code_fingerprint()[:16]}"


def key(lang):
    """存档按「页面选的语言（工作簿）+ 界面语言（标题下的说明、预设问题用 ``_()``）」分开存。"""
    return f"{lang}-{str(frappe.local.lang or 'th')[:5]}"


def _name(kind, task, lang):
    return f"{kind}:{task}:{lang}"


def get(kind, task, lang, print_):
    """``(结果页数据, 是否最新)``：指纹整段对得上是最新；只有代码段不同（部署换了代码、数据没变）回旧存档、标成不是最新，
    由调用方先给用户、后台重建一份（每次部署都让全部存档失效、早上第一次打开又要现算——独立复查 2026-10-01）；数据段
    也不同（改过、预设变了、重跑过）或没有存档回 ``(None, False)``。"""
    rows = frappe.db.sql(f"SELECT fingerprint, payload FROM `tab{DOCTYPE}` WHERE name=%s", (_name(kind, task, lang),))
    if not rows or not rows[0][1] or rows[0][0].split(":")[0] != print_.split(":")[0]:
        return None, False
    try:
        return json.loads(rows[0][1]), rows[0][0] == print_
    except ValueError:
        return None, False


@contextmanager
def building(kind, task, lang):
    """同一套同一语言同一时间只算一份：几个人同时打开一套没有存档的对账，后来的等前面那份算完直接读，不各算各的。
    等不到锁（队列 Redis 一时不可用、前面那份太久）就自己算。"""
    from frappe.utils.background_jobs import get_redis_conn

    lock = None
    try:
        lock = get_redis_conn().lock(f"cowork-recon-view:{frappe.local.site}:{_name(kind, task, lang)}",
                                     timeout=300, blocking_timeout=120)
        if not lock.acquire():
            lock = None
    except Exception:
        lock = None
    try:
        yield
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass


def refresh_later(kind, task, lang):
    """后台按当前代码重建一份（同一套同一语言只排一个）。排不进去不要紧：下次打开再排，或部署时的补算会做。"""
    try:
        frappe.enqueue("pearnly_cowork.recon_center.sheets.rebuild_view", queue="short", kind=kind, task_id=task,
                       lang=lang, ui=str(frappe.local.lang or "th"), user=frappe.session.user, deduplicate=True,
                       job_id=f"cowork-recon-view-{frappe.local.site}-{_name(kind, task, key(lang))}")
    except Exception:
        pass


def put(kind, task, lang, print_, view):
    """存（覆盖同一任务同一语言的旧存档）。存不下不影响这一次打开：下次再现算。"""
    try:
        payload = frappe.as_json(view, indent=None)
        frappe.db.sql(f"""INSERT INTO `tab{DOCTYPE}` (name, task, recon_kind, lang, fingerprint, payload, creation, modified,
                owner, modified_by, docstatus, idx)
            VALUES (%(name)s, %(task)s, %(kind)s, %(lang)s, %(print)s, %(payload)s, NOW(6), NOW(6), 'Administrator',
                'Administrator', 0, 0)
            ON DUPLICATE KEY UPDATE fingerprint=VALUES(fingerprint), payload=VALUES(payload), modified=NOW(6)""",
                      {"name": _name(kind, task, lang), "task": task, "kind": kind, "lang": lang, "print": print_,
                       "payload": payload})
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="cowork recon view store")


def drop(task):
    frappe.db.sql(f"DELETE FROM `tab{DOCTYPE}` WHERE task=%s", (task,))


TASKS = (("bank", "Cowork Bank Reconciliation"), ("income", "Cowork Income Reconciliation"), ("vat", "Cowork VAT Check"))


def backfill(langs=("th", "zh", "en")):
    """一次性：给本站点已有的对账逐套生成结果页存档（部署时跑一次，不是定时任务；用户经压测窗口："不留给用户第一次打开去等"）。

    页面请求的语言就是用户的界面语言（``recon.js`` 的 ``lang()``），所以按「界面语言 = 页面语言」的几种组合各生成一份。只补没有存档的
    和数据变了的；只是代码换了的旧存档不在部署时重算 —— 打开时先给旧的、后台重建（``sheets.load`` → ``refresh_later``），
    部署时不再把所有对账重算一遍（2026-10-02 第二轮压测：补算跑了 18 分钟以上、和压测重叠吃了约 490 CPU 秒）。每套以它的发起人身份算（与本人打开一样过权限）。一套失败只记日志，接着下一套。部署时用后台进程（``cowork-worker``，
    CPU 低权重）跑：``bench --site all execute pearnly_cowork.recon_center.view_store.backfill``。``bench execute`` 遇到异常会把方法
    再跑一遍，所以这里绝不往外抛。"""
    import time

    from pearnly_cowork.recon_center import sheets

    started, built, failed, total = time.monotonic(), 0, 0, 0
    previous_user, previous_lang = frappe.session.user, getattr(frappe.local, "lang", None)
    try:
        for kind, doctype in TASKS:
            if not frappe.db.table_exists(doctype):
                continue
            for row in frappe.get_all(doctype, fields=["name", "owner"], order_by="modified desc", limit_page_length=0):
                total += 1
                for lang in langs:
                    try:
                        frappe.set_user(row.owner or "Administrator")
                        frappe.local.lang = lang
                        built += 1 if sheets.rebuild_view(kind, row.name, lang, stale_ok=True) else 0
                        # 已是最新的跳过时没有写入、也就没有提交：不结束的话整个补算开着同一个读快照（几分钟），
                        # 拖住 purge、MariaDB 内存上涨（2026-10-02 压测锁采样：最长事务 287 秒、无锁等待）。
                        frappe.db.commit()
                    except Exception:
                        failed += 1
                        frappe.db.rollback()
                        frappe.log_error(title=f"cowork recon view backfill {kind} {row.name}")
                        frappe.db.commit()
    except Exception:
        frappe.log_error(title="cowork recon view backfill")
    finally:
        frappe.set_user(previous_user)
        frappe.local.lang = previous_lang
    try:
        stored = frappe.db.count(DOCTYPE)
    except Exception:
        stored = "?"
    print(f"{getattr(frappe.local, 'site', '')}: tasks={total} languages={len(langs)} archives={stored} new={built} "
          f"failed={failed} {time.monotonic() - started:.0f}s")
