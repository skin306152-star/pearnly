"""实验室实证（11-05 删对话 500）：在回归实验室的真 MariaDB 11.8（快照隔离开着）上，删 / 归档 / 改名 / 星标一段对话时，引擎调用期间
另一个连接改了这一行并提交（推送线程记活动时间、起标题任务写标题就是这样），本站这一半照样写成；修前的顺序在同一情形下报
「暂时不能删除」（QueryTimeoutError）。

只在回归实验室跑（`infrastructure/compose/regression-lab.yml`，源码挂在 /workspace），只建、只删自己的测试会话（编号带 racelab），
不调引擎（适配器换成只记一笔、顺带让另一个连接改这一行的替身），不碰别的数据：

    docker compose -p <栈名> -f infrastructure/compose/regression-lab.yml exec -T bench \\
        env/bin/python /workspace/scripts/cc-delete-race-lab-verify.py cowork-new.regression.localhost

末行打印 ``RESULT {...}``，全部 ok 才算通过（退出码 0）。
"""
import json
import os
import secrets
import sys
import types

import frappe

SITES = "/home/frappe/frappe-bench/sites"
SESSION = "Pearnly Assistant Session"


def connect(site):
    if not site.endswith(".regression.localhost"):
        raise SystemExit("regression lab sites only")
    os.chdir(SITES)
    frappe.init(site=site, sites_path=SITES)
    frappe.connect()


def other_connection():
    """另一个数据库连接（另一个事务），同站点库。"""
    import pymysql

    conf = frappe.conf
    return pymysql.connect(host=conf.db_host or "db", port=int(conf.db_port or 3306), user=conf.db_name,
                           password=conf.db_password, database=conf.db_name, autocommit=False)


def make_row(user, code, product):
    doc = frappe.get_doc({"doctype": SESSION, "session_id": f"sesn_racelab{secrets.token_hex(6)}", "user": user,
                          "engine_code": code, "product": product, "title": "race",
                          "title_source": "summarizing"}).insert(ignore_permissions=True)
    frappe.db.commit()
    return doc


def main(site):
    connect(site)
    from pearnly_core.assistant import config, engine, sessions, sidebar

    user = frappe.db.get_value("User", {"name": ["like", "admin@%"], "enabled": 1}, "name") or "Administrator"
    frappe.set_user(user)
    code = config.engine_code()
    product = config.product() if hasattr(config, "product") else "lab"
    product = product if isinstance(product, str) else "lab"
    other = other_connection()

    def commit_elsewhere(remote):
        with other.cursor() as cursor:
            cursor.execute(f"UPDATE `tab{SESSION}` SET title=%s, title_source='summary' WHERE session_id=%s",
                           ("สรุปจากงานอื่น", remote))
        other.commit()

    api = types.SimpleNamespace(delete=commit_elsewhere, archive=commit_elsewhere,
                                rename=lambda remote, title: commit_elsewhere(remote))
    engine.adapter = lambda: api
    sidebar.changed = lambda *a, **kw: None
    results = {}

    # 修前的顺序：读这一行 → 别处改并提交 → Frappe 删除（不等锁的加锁读）
    old = make_row(user, code, product)
    sessions.owned(old.session_id)
    commit_elsewhere(old.session_id)
    try:
        frappe.delete_doc(SESSION, old.name, ignore_permissions=True)
        results["old_order_fails"] = False
    except frappe.QueryTimeoutError:
        results["old_order_fails"] = True
    frappe.db.rollback()

    for action in ("delete", "archive", "rename", "star"):
        row = make_row(user, code, product)
        try:
            if action == "delete":
                sessions.delete(row.session_id)
            elif action == "archive":
                sessions.archive(row.session_id)
            elif action == "rename":
                sessions.rename(row.session_id, "ชื่อใหม่")
            else:
                sessions.owned(row.session_id)  # 星标不调引擎：先读（旧快照），别处改并提交，再星标
                commit_elsewhere(row.session_id)
                sessions.star(row.session_id, True)
            frappe.db.commit()
            after = frappe.db.get_value(SESSION, row.name, ["archived", "title", "title_source", "starred"], as_dict=True)
            results[action] = {"delete": after is None, "archive": bool(after and after.archived),
                               "rename": bool(after and after.title == "ชื่อใหม่" and after.title_source == "user"),
                               "star": bool(after and after.starred)}[action]
        except Exception as err:  # noqa: BLE001  实证脚本：记下失败的类型，照常收尾
            frappe.db.rollback()
            results[action] = f"{type(err).__name__}: {err}"[:200]
    frappe.db.rollback()
    for name in frappe.get_all(SESSION, filters={"session_id": ["like", "sesn_racelab%"]}, pluck="name"):
        frappe.delete_doc(SESSION, name, ignore_permissions=True)
    frappe.db.commit()
    other.close()
    ok = all(value is True for value in results.values())
    print("RESULT " + json.dumps({"ok": ok, **results}, ensure_ascii=False))
    frappe.destroy()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "cowork-new.regression.localhost"))
