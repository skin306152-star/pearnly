"""对账结果页的存档：结果页的数据生成一次存起来，打开时直接读（2026-10-01 用户经压测窗口："不要再改一点修一点"）。

原来每打开一次结果页都从存着的输入重算对账、生成整本复核工作簿、读回来逐格画成页面数据（`sheets.load`）：557 行的银行对账
空闲时一次 10 秒，压测时更慢，页面停在骨架屏上，用户以为"进不去"。现在：

- 对账出结果时（后台任务里，`jobs`）按发起时选的语言生成一次存起来；保存修改、回答预设问题后（它们都回 `sheets.load`）
  按新结果重新生成、存回；
- 打开结果页先按指纹找存档，对得上就直接回；对不上（结果变了、预设变了、代码换了）现算一次再存。没有存档的旧任务第一次打开时
  现算并补存。

指纹包括：对账类型、任务、语言、任务的修改时间与修订号、这个工作空间这类对账的预设（条数与最后修改时间）、要用原文件重跑的
问题记录的修改时间、生成结果页的代码本身（这些源文件的内容摘要）。所以存档只会在内容确实一样时被用上，与现算逐格一致。

导出 Excel 不走这里：导出读的是任务完成时存下的结果行（各自的 `export`），本来就不重算对账。

存在 ``Cowork Recon View``（没有任何角色的权限，只由这里读写）；任务删除时一起删（`drop`）。
"""
import hashlib
import json
from pathlib import Path

import frappe

DOCTYPE = "Cowork Recon View"
PRESET = "Cowork Recon Preset"
RERUN = "Cowork Recon Rerun"
_code = None


def _code_fingerprint():
    """生成结果页的代码（结果页、对账计算与工作簿、文案）的内容摘要：换了版本的代码不会用旧代码生成的存档。进程内算一次。"""
    global _code
    if _code is None:
        root = Path(__file__).resolve().parents[1]
        files = sorted([*root.joinpath("reconciliation").rglob("*.py"), root / "recon_center" / "sheets.py",
                        *root.joinpath("translations").glob("*.csv"), Path(__file__)])
        digest = hashlib.sha256()
        for path in files:
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
        _code = digest.hexdigest()
    return _code


def fingerprint(kind, doc, lang, version):
    presets = frappe.db.sql(f"SELECT COUNT(*), MAX(modified) FROM `tab{PRESET}` WHERE workspace=%s AND recon_kind=%s",
                            (doc.workspace, kind))[0]
    rerun = frappe.db.get_value(RERUN, doc.name, "modified") if frappe.db.table_exists(RERUN) else None
    return hashlib.sha256(json.dumps(
        [kind, doc.name, lang, str(doc.modified), str(version), int(presets[0] or 0), str(presets[1] or ""),
         str(rerun or ""), _code_fingerprint()], separators=(",", ":")).encode()).hexdigest()


def key(lang):
    """存档按「页面选的语言（工作簿）+ 界面语言（标题下的说明、预设问题用 ``_()``）」分开存。"""
    return f"{lang}-{str(frappe.local.lang or 'th')[:5]}"


def _name(kind, task, lang):
    return f"{kind}:{task}:{lang}"


def get(kind, task, lang, print_):
    """存档的结果页数据；没有或指纹对不上回 None。"""
    rows = frappe.db.sql(f"SELECT fingerprint, payload FROM `tab{DOCTYPE}` WHERE name=%s", (_name(kind, task, lang),))
    if not rows or rows[0][0] != print_ or not rows[0][1]:
        return None
    try:
        return json.loads(rows[0][1])
    except ValueError:
        return None


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

    页面请求的语言就是用户的界面语言（``recon.js`` 的 ``lang()``），所以按「界面语言 = 页面语言」的几种组合各生成一份；已有、指纹对得上
    的跳过。每套以它的发起人身份算（与本人打开一样过权限）。一套失败只记日志，接着下一套。部署时用后台进程（``cowork-worker``，
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
                        before = frappe.db.count(DOCTYPE, {"name": _name(kind, row.name, key(lang))})
                        sheets.load(kind, row.name, lang)
                        built += 0 if before else 1
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
