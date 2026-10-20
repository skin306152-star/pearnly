"""账套整份上报的计时（实验站点 / 测试机导入的测试租户副本专用，不连 Express、不连任何外部服务）。

在站点里建一条带合成标签的 Express 连接，按 CC 桌面版心跳的同一条路（`reports.plan_report` → `collector.ingest`，
与 `companion.heartbeat._receive` 一样先锁连接行）报几轮合成的账套名单，每轮单独提交，记：

- 这一轮持连接行锁的时长（锁上 → 提交）与执行的 SQL 条数；
- 带 ``watch`` 的轮次：另开一条数据库连接，照领取（`companion.queue_api.lease`）第一步对同一连接行 ``FOR UPDATE``，
  量它被挡了多久（领取被整份上报拖住的那一段）。

结束时删掉这条合成连接在池里的全部行（账套登记、可达、账套资料、目录快照、刷新行）和连接本身，再提交。

跑法（测试机 cowork 栈，实时源码挂好之后）::

    docker cp scripts/cowork-account-set-report-lab-timing.py <backend 容器>:/tmp/timing.py
    docker exec -w /home/frappe/frappe-bench/sites <backend 容器> ../env/bin/python /tmp/timing.py \
        --site testcowork.pearnly.com --rows 679
"""
import argparse
import cProfile
import hashlib
import io
import json
import pstats
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import frappe

LABEL = "Synthetic account-set report timing"


def account_rows(count, *, mark="2026-10-20/100", drive="S", relabel=(), skip=(), extra=0, legacy=False, volume=0x5A5A):
    """合成的账套名单（CC 桌面版 1.9.1 心跳 ``account_sets`` 的形状）；``legacy``：1.7.0 之前的旧版，不报文件号与网络路径。"""
    rows = []
    for index in list(range(count)) + list(range(100_000, 100_000 + extra)):
        if index in skip:
            continue
        year = 2560 + index % 10
        folder = r"%d\EXP%02d\C%05d" % (year, index % 100, index)
        rows.append({"code": "C%05d" % index, "name": ("Company %d" % index) + (" (renamed)" if index in relabel else ""),
                     "company": "Company %d" % index, "tax_id": "0105559%06d" % index,
                     "path": "%s:\\%s" % (drive, folder), "root": "%s:\\%d\\EXP%02d" % (drive, year, index % 100),
                     "row": index + 1, "writable": True, "uid": "v1:%08x:%08x" % (volume, index),
                     "share": "\\\\accserver\\account\\" + folder, "entry_mark": mark})
        if legacy:
            for key in ("uid", "share", "entry_mark"):
                rows[-1].pop(key)
    return rows


def digest(name, base):
    """这条连接池里的登记、可达（按 ``creation, name`` 的顺序，去掉随连接名变的名称与时刻，时刻换成离开头几分钟）和
    池目录的摘要：改动前后同一组上报应得到同一个摘要。"""
    from pearnly_cowork.connections import pool

    def minutes(value):
        return None if value is None else round((value - base).total_seconds() / 60, 3)

    registrations = frappe.db.sql(
        f"SELECT account_key, root_key, path, root, label, attributes_json, uid, location, content_key, scope_device, "
        f"registered_at FROM `tab{pool.ACCOUNT_SET}` WHERE connection=%s ORDER BY creation, name", (name,), as_dict=True)
    reach = frappe.db.sql(
        f"SELECT device, account_key, path, root, registry_row, writable, state, uid, share, content_key, details_json, "
        f"seen_at FROM `tab{pool.REACH}` WHERE connection=%s ORDER BY creation, name", (name,), as_dict=True)
    body = {"registrations": [{**row, "uid": row.uid or "", "registered_at": minutes(row.registered_at)}
                              for row in registrations],
            "reach": [{**row, "seen_at": minutes(row.seen_at)} for row in reach],
            "catalog": pool.listing(name)[0]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Counter:
    def __init__(self):
        self.count, self.heads = 0, {}
        self.original = frappe.db.sql

    def __enter__(self):
        def counted(*args, **kwargs):
            self.count += 1
            head = " ".join(str(args[0] if args else kwargs.get("query", "")).split())[:70]
            self.heads[head] = self.heads.get(head, 0) + 1
            return self.original(*args, **kwargs)

        frappe.db.sql = counted
        return self

    def __exit__(self, *exc):
        frappe.db.sql = self.original


def waiter(connection, conf, started, result):
    import pymysql

    db = pymysql.connect(host=conf["db_host"] or "127.0.0.1", port=int(conf["db_port"] or 3306),
                         user=conf["db_user"] or conf["db_name"], password=conf["db_password"],
                         database=conf["db_name"], autocommit=False)
    try:
        with db.cursor() as cursor:
            cursor.execute("SET SESSION innodb_lock_wait_timeout=120")
            cursor.execute("START TRANSACTION")
            started.set()
            begin = time.perf_counter()
            cursor.execute("SELECT name FROM `tabCowork ERP Connection` WHERE name=%s FOR UPDATE", (connection,))
            result["wait"] = time.perf_counter() - begin
        db.rollback()
    finally:
        db.close()


def run(site, count, profile=False):
    from pearnly_cowork.connections import access, collector, pool
    from pearnly_cowork.connections.member import Member
    from pearnly_cowork.connections.reports import plan_report
    from pearnly_cowork.push.native import service as ledger
    from pearnly_cowork.targets import identity

    frappe.set_user("Administrator")
    names = []
    for _ in range(2):
        record = frappe.get_doc({"doctype": "Cowork ERP Connection", "record_key": "LAB-" + uuid4().hex,
                                 "workspace": access.default_workspace(), "label": LABEL, "adapter": "express",
                                 "enabled": 1, "binding_generation": 0, "config_json": "{}", "report_json": "{}",
                                 "projection_heads": "{}"})
        access.write(record)
        names.append(record.name)
    frappe.db.commit()
    aliases = identity.host_aliases({"LAB-A": ["lab-a"], "LAB-B": ["lab-b"], "LAB-L": ["lab-l"], "LAB-C": ["lab-c"]})
    base = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    renamed, dropped = set(range(0, count, 10)), set(range(5, count, 20))
    # （说明，连接，设备，主机名，旧版，名单，量领取等锁）
    steps = [
        ("A 首次整份", 0, "LAB-A", "lab-a", False, account_rows(count), True),
        ("A 重复（无变化）", 0, "LAB-A", "lab-a", False, account_rows(count), False),
        ("A 录入标记全变", 0, "LAB-A", "lab-a", False, account_rows(count, mark="2026-10-21/101"), False),
        ("A 部分变化（改名 10%、少 5%、新 5 份）", 0, "LAB-A", "lab-a", False,
         account_rows(count, mark="2026-10-21/101", relabel=renamed, skip=dropped, extra=5), False),
        ("B 首次整份（同一批共享，换盘符）", 0, "LAB-B", "lab-b", False, account_rows(count, drive="Z"), True),
        ("B 重复（无变化）", 0, "LAB-B", "lab-b", False, account_rows(count, drive="Z"), False),
        # 第二条连接：旧版电脑先登记（没有文件号），新版电脑报同一批 → 全部待认定；旧版升级后带文件号整份重报 → 那台等着的
        # 全部在这一次心跳里认上（`reconsider(others=True)`，一次认上整份）
        ("L 旧版首次整份（无文件号）", 1, "LAB-L", "lab-l", True, account_rows(count, legacy=True), False),
        ("C 新版首次整份（全部待认定）", 1, "LAB-C", "lab-c", False, account_rows(count, drive="Z", volume=0x6B6B), True),
        ("C 重复（仍待认定，每次心跳重核）", 1, "LAB-C", "lab-c", False, account_rows(count, drive="Z", volume=0x6B6B),
         False),
        ("L 升级后整份（带文件号，C 等着的整份认上）", 1, "LAB-L", "lab-l", False, account_rows(count, volume=0x6B6B), True),
    ]
    results = []
    try:
        for minute, (label, which, device, host, legacy, rows, watch) in enumerate(steps):
            name = names[which]
            now = base + timedelta(minutes=minute)
            locked = time.perf_counter()
            doc = frappe.get_doc("Cowork ERP Connection", name, for_update=True)
            blocked, started, thread = {}, threading.Event(), None
            if watch:
                conf = {key: frappe.conf.get(key) for key in ("db_host", "db_port", "db_user", "db_name", "db_password")}
                thread = threading.Thread(target=waiter, args=(name, conf, started, blocked))
                thread.start()
                started.wait(10)
                time.sleep(0.2)  # 让领取那一侧真的排在锁上
            member = Member(device=device, legacy=legacy, host=host, aliases=aliases,
                            version="1.5.1" if legacy else "1.9.1", payload_version=None if legacy else 2)
            profiler = cProfile.Profile() if profile else None
            with Counter() as counter:
                if profiler:
                    profiler.enable()
                begin = time.perf_counter()
                plan = plan_report({"companion_version": member.version, "account_sets": rows}, {},
                                   now.replace(tzinfo=timezone.utc))
                collector.ingest(doc, plan, credential_id="LAB-" + device, token_generation=1, issuer="Administrator",
                                 member=member, protected=lambda: ledger.open_account_keys(name), now=now)
                work = time.perf_counter() - begin
                if profiler:
                    profiler.disable()
            frappe.db.commit()
            held = time.perf_counter() - locked
            if thread:
                thread.join(130)
            registrations = frappe.db.count(pool.ACCOUNT_SET, {"connection": name})
            reach = frappe.db.count(pool.REACH, {"connection": name})
            pending = frappe.db.count(pool.REACH, {"connection": name, "state": pool.PENDING_IDENTITY})
            results.append({"step": label, "rows": len(rows), "pending": pending, "ingest_s": round(work, 3), "lock_held_s": round(held, 3),
                            "sql": counter.count, "lease_wait_s": round(blocked["wait"] - 0.2, 3) if "wait" in blocked
                            else None, "registrations": registrations, "reach": reach,
                            "digest": digest(name, base)})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
            if profiler:
                print(json.dumps(sorted(counter.heads.items(), key=lambda item: -item[1])[:12], ensure_ascii=False))
                out = io.StringIO()
                pstats.Stats(profiler, stream=out).sort_stats("cumulative").print_stats(
                    r"pool|identity|collector|snapshots|database|bulk|base_document|document", 25)
                print(out.getvalue(), flush=True)
    finally:
        frappe.db.rollback()
        for name in names:
            for doctype in (pool.REACH, pool.ACCOUNT_SET, pool.FACTS, "Cowork ERP Snapshot", "Cowork ERP Refresh"):
                frappe.db.delete(doctype, {"connection": name})
            frappe.db.delete("Cowork ERP Connection", {"name": name})
        frappe.db.commit()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--rows", type=int, default=679)
    parser.add_argument("--profile", action="store_true", help="每轮打印热点（cProfile 本身会拖慢）")
    args = parser.parse_args()
    frappe.init(site=args.site, sites_path=".")
    frappe.connect()
    try:
        if frappe.conf.get("pearnly_product") != "cowork":
            raise SystemExit("not a Cowork site")
        run(args.site, args.rows, args.profile)
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
