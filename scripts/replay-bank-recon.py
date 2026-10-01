"""银行对账重放评测：用已存的识别结果和压测语料原件，按当前代码重算已跑过的对账，新旧判定逐行对比（2026-10-02）。

只读、不调模型、不花钱：识别结果取自每次运行留下的 OCR Work 检查点（``recon_center.document_ocr`` 的 RESULT），
带文字层的 PDF / 表格按原件免费解析（原件在站点上已按规矩删除，取自压测语料目录，同名同内容）；走真实的
``create_from_documents``，只把最后写库的 ``create`` 换成记录参数 + 纯计算匹配；数据库会话设成只读，误写直接报错。

对比的"变体"在 ``VARIANTS`` 里登记：``old`` = 改动前的判定，``new`` = 当前代码。``old`` 先和库里存的结果比（行数、匹配数），
对得上说明重放本身可信，再看 ``new`` 与 ``old`` 的差异。以后改读法（拼行、紧凑输出）照样加一个变体。

用法（生产机一次性容器，挂被测分支的代码、只读挂语料，跑完删）：
  docker run --rm --user 0 --memory 2g --network pearnly-production_runtime \\
    -v pearnly-production_sites-cowork:/sites-ro:ro \\
    -v <分支>/apps/pearnly_cowork/pearnly_cowork:/home/frappe/frappe-bench/apps/pearnly_cowork/pearnly_cowork:ro \\
    -v <分支>/scripts/replay-bank-recon.py:/replay.py:ro -v /root/pearnly-corpus:/corpus:ro \\
    --entrypoint /home/frappe/frappe-bench/env/bin/python <生产镜像> /replay.py \\
    --sites testcowork.pearnly.com,testcowork2.pearnly.com,testcowork3.pearnly.com --since 2026-10-01 \\
    --title-like B05 --title-like B08 --title-like B09 --corpus /corpus --sites-from /sites-ro
"""
import argparse
import collections
import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

SITES = "/home/frappe/frappe-bench/sites"


def old_account_conflict(statements, ledgers=()):
    """改动前（main ed23acf9）的判定：份数取多数、打平按出现先后，同一笔按日期完全相同认。"""
    from pearnly_cowork.reconciliation import bank_parse, bank_recon_service as service

    keys = {}
    for name, result in statements:
        identity = result.get("identity") or {} if result.get("ok") else {}
        bank = bank_parse.detect_bank(identity.get("bank") or "")
        keys[name] = (bank if bank != "generic" else "", identity.get("last4") or "")
    lines = {name: {service._line_key(row) for row in (result.get("rows") or []) if row.balance}
             for name, result in statements if result.get("ok")}

    def overlaps(name):
        return any(lines.get(name, set()) & keys_ for other, keys_ in lines.items() if other != name)

    def near(first, second):
        return len(first) == len(second) == 4 and sum(a != b for a, b in zip(first, second)) <= 1

    for index in (0, 1):
        values = collections.Counter(key[index] for key in keys.values() if key[index])
        if len(values) > 1:
            common = values.most_common(1)[0][0]
            return [name for name, key in keys.items() if key[index] and key[index] != common
                    and not overlaps(name) and not (index == 1 and near(key[index], common))], True
    return [], True


VARIANTS = {"old": {"account_conflict": old_account_conflict}, "new": {}}


def corpus_index(root):
    """语料目录里每个文件名 → 路径列表（同名多份时按对账名挑）。"""
    index = collections.defaultdict(list)
    for path in Path(root).rglob("*"):
        if path.is_file():
            index[path.name].append(path)
    return index


def original(index, case, filename):
    """按上传时的文件名在语料里找原件；早几轮上传同名文件时文件名末尾多了 6 位十六进制（``05.69222cb0.pdf``），去掉再找。"""
    import re

    paths = index.get(filename) or []
    if not paths:
        plain = re.sub(r"[0-9a-f]{6}(\.[^.]+)$", r"\1", filename)
        paths = index.get(plain) or []
    named = [path for path in paths if case and case in str(path)]
    chosen = (named or paths)
    return chosen[0].read_bytes() if len(named or paths) >= 1 else None


def recon_jobs(site):
    """这个站点所有对账识别任务：``{request_key: (对账号, 用户, {(文件名, 文书类型): Work 名})}``。"""
    import frappe
    from pearnly_cowork.recon_center import document_ocr

    jobs = collections.defaultdict(lambda: {"user": "", "works": {}})
    for row in frappe.db.sql("SELECT name, reference, arguments, user, status FROM `tabPearnly OCR Work` WHERE kind=%s",
                             (document_ocr.KIND,), as_dict=True):
        job, _side, document_type = row.reference.split(":")
        arguments = json.loads(row.arguments or "{}")
        jobs[job]["user"] = row.user
        jobs[job]["works"][(arguments.get("filename"), document_type)] = (row.name, row.status)
    return {hashlib.sha256(f"{site}|{value['user']}|{job}".encode()).hexdigest(): (job, value["user"], value["works"])
            for job, value in jobs.items()}


def replay(doc, works, index, variant):
    """按变体重算一次：回 ``{rows, matched, files, warnings, row_keys}``；读不全回 ``{"missing": [...]}``。"""
    import frappe
    from pearnly_core.execution import work as core_work
    from pearnly_cowork.ocr.enterprise import text_layer
    from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
        document_model_rows,
    )
    from pearnly_cowork.reconciliation import bank_recon_contracts, bank_recon_service as service
    from pearnly_cowork.recon_center import document_ocr

    info = json.loads(doc.parse_info_json or "{}")
    case = doc.title.split("] ", 1)[-1].strip()
    sides = {"left": [item["file"] for item in info.get("stmt_files") or []],
             "right": [item["file"] for item in info.get("gl_files") or []]}
    files, missing = {}, []
    for side, names in sides.items():
        for name in names:
            content = original(index, case, name)
            if content is None:
                missing.append(name)
            files[name] = content
    if missing:
        return {"missing": missing}

    def read(document_type, content, filename):
        free = text_layer.read(content, document_type)
        if free is not None:
            return free
        work, status = works.get((filename, document_type), (None, None))
        if not work or status != "succeeded":
            return None
        return ((core_work.saved_checkpoint(work, document_ocr.RESULT) or {}).get("body") or {}).get("legacy")

    captured = {}

    def create(workspace, title, request_id, statement_rows, gl_rows, header=None, reconciliation_type="bank",
               source_format=None, parse_info=None, warnings=None, anchors=None, lang="th", **_kwargs):
        rows = bank_recon_contracts.decode_rows(statement_rows, gl_rows)
        values = bank_recon_contracts.header(header)
        _results, summary = service._match(rows, values, service.preset_rules(workspace, service.preset_scope(
            values["bank_code"], bank_recon_contracts.parse_info(parse_info), values["gl_account_code"], rows[1])))
        captured.update(rows=len(rows[0]), matched=summary.get("matched_count", 0), warnings=list(warnings or []),
                        files=[(item["file"], item.get("rows"), item.get("error_code")) for item in parse_info["stmt_files"]],
                        row_keys=sorted((str(row.get("date")), str(row.get("deposit")), str(row.get("withdrawal")),
                                         str(row.get("balance"))) for row in statement_rows))
        return {"name": "replay", "status": "replay"}

    # 压测提交不指定科目（由程序按流水挑）；原提交参数不入库，按空重放，与库里结果对不上的会在 faithful 里看出来。
    gl_account = ""
    with contextlib.ExitStack() as stack:
        for attribute, value in {"create": create, **VARIANTS[variant]}.items():
            stack.enter_context(patch.object(service, attribute, value))
        try:
            service.create_from_documents(doc.workspace, doc.title, "replay", files[sides["left"][0]], sides["left"][0],
                files[sides["right"][0]], sides["right"][0], gl_account_code=gl_account,
                model_rows=document_model_rows(gl_account, read=read), lang="zh",
                statement_files=[(files[name], name) for name in sides["left"]],
                gl_files=[(files[name], name) for name in sides["right"]])
        except Exception as error:  # 原来就失败的（拒收、读不出）照样记下来对比
            return {"error": f"{type(error).__name__}: {str(error)[:160]}"}
    return captured


def replay_job(site, job, case, index, variants, corpus):
    """失败的运行没有对账结果可比：按对账号找它的识别任务、按语料目录找原件，照提交时的文件顺序重算一遍。"""
    import frappe
    from types import SimpleNamespace

    found = next((value for key, value in recon_jobs(site).items() if value[0] == job), None)
    if not found:
        return {"job": job, "skip": "no recognition job"}
    _job, user, works = found
    order = {}
    for row in frappe.db.sql("SELECT reference, arguments FROM `tabPearnly OCR Work` WHERE reference LIKE %s",
                             (f"{job}:%",), as_dict=True):
        side = row.reference.split(":")[1]
        order[int(side[4:]) if side.startswith("left") else 1000 + int(side[5:])] = json.loads(row.arguments)["filename"]
    left = [order[key] for key in sorted(order) if key < 1000]
    folder = next((path for path in Path(corpus).rglob(case) if path.is_dir()), None) if case else None
    right = sorted(path.name for path in folder.rglob("*") if path.is_file() and "右" in str(path)) if folder else []
    same = frappe.db.get_value("Cowork Bank Reconciliation", {"title": ["like", f"%{case}%"]}, ["workspace", "owner"],
                               as_dict=True) or frappe._dict(workspace="", owner="Administrator")
    workspace = same.workspace
    if not frappe.db.exists("User", user):
        user = same.owner  # 压测用户测完已删：用同一工作区里跑过同一套的人重放
    doc = SimpleNamespace(title=f"] {case}", workspace=workspace, parse_info_json=json.dumps(
        {"stmt_files": [{"file": name} for name in left], "gl_files": [{"file": name} for name in right]}))
    frappe.set_user(user)
    results = {variant: replay(doc, works, index, variant) for variant in variants}
    return {"job": job, "case": case, "left": left, "right": right,
            **{variant: {k: v for k, v in result.items() if k != "row_keys"} for variant, result in results.items()}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", required=True)
    parser.add_argument("--since", default="2026-10-01")
    parser.add_argument("--title-like", action="append", default=[])
    parser.add_argument("--name", action="append", default=[], help="只跑这几套（CBR 名前缀）")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--variants", default="old,new")
    parser.add_argument("--job", help="按对账号重放一次没留下结果的运行（失败的）：配 --case 语料里的对账名，只看第一个站点")
    parser.add_argument("--case", help="--job 用：语料目录名，如 B08-冰厂SINCERE-BBL0555-2026-01")
    parser.add_argument("--sites-from", help="只读挂载的站点卷：只把配置拷进容器自己的 sites（Frappe 要往站点目录写日志）")
    args = parser.parse_args()
    if args.sites_from:
        import shutil

        for name in ("common_site_config.json", "apps.txt"):
            shutil.copy(Path(args.sites_from, name), Path(SITES, name))
        for site in args.sites.split(","):
            Path(SITES, site, "logs").mkdir(parents=True, exist_ok=True)
            shutil.copy(Path(args.sites_from, site, "site_config.json"), Path(SITES, site, "site_config.json"))
    os.chdir(SITES)
    import frappe

    index = corpus_index(args.corpus)
    if not index:
        raise SystemExit(f"corpus {args.corpus} is empty or not readable (run the container with --user 0)")
    variants = args.variants.split(",")
    totals = collections.Counter()
    if args.job:
        site = args.sites.split(",")[0]
        frappe.init(site, sites_path=SITES)
        frappe.connect()
        frappe.db.sql("SET SESSION TRANSACTION READ ONLY")
        print(json.dumps(replay_job(site, args.job, args.case, index, variants, args.corpus), ensure_ascii=False, default=str))
        frappe.destroy()
        return
    for site in args.sites.split(","):
        frappe.init(site, sites_path=SITES)
        frappe.connect()
        frappe.db.sql("SET SESSION TRANSACTION READ ONLY")
        frappe.db.commit()
        jobs = recon_jobs(site)
        likes = " OR ".join(["title LIKE %s"] * len(args.title_like)) or "1=1"
        docs = frappe.db.sql(f"SELECT name FROM `tabCowork Bank Reconciliation` WHERE creation >= %s AND ({likes}) "
                             "ORDER BY creation", [args.since, *[f"%{t}%" for t in args.title_like]], as_dict=True)
        for row in docs:
            if args.name and not any(row.name.startswith(prefix) for prefix in args.name):
                continue
            doc = frappe.get_doc("Cowork Bank Reconciliation", row.name)
            # 全是表格 / 电子版 PDF 的对账没有识别任务：只用原件免费解析重放（发起人取任务的所有者）。
            _job, user, works = jobs.get(doc.request_key) or (None, doc.owner, {})
            frappe.set_user(user)
            results = {variant: replay(doc, works, index, variant) for variant in variants}
            frappe.set_user("Administrator")
            statement = json.loads(doc.inputs_json or "{}").get("statement")
            stored = {"rows": len(statement) if isinstance(statement, list) else None}
            old, new = results.get("old") or {}, results.get("new") or {}
            # 待复核的对账建单时不跑匹配（库里匹配数是 0），所以按行数和每份文件的去留判断重放是否还原了当时那次。
            stored["files"] = [[item["file"], item.get("rows"), item.get("error_code")]
                               for item in json.loads(doc.parse_info_json or "{}").get("stmt_files") or []]
            faithful = old.get("rows") is not None and old.get("rows") == stored["rows"] and \
                [list(item) for item in old.get("files") or []] == stored["files"]
            same = {key: old.get(key) == new.get(key) for key in ("rows", "matched", "row_keys", "files", "error")}
            totals["tasks"] += 1
            totals["faithful"] += faithful
            totals["identical"] += all(same.values())
            print(json.dumps({"site": site, "task": doc.name[:16], "title": doc.title, "stored": stored,
                              "faithful": faithful, "identical": all(same.values()),
                              **{variant: {k: v for k, v in result.items() if k != "row_keys"}
                                 for variant, result in results.items()},
                              "rows_only_old": len(set(map(tuple, old.get("row_keys") or [])) -
                                                   set(map(tuple, new.get("row_keys") or []))),
                              "rows_only_new": len(set(map(tuple, new.get("row_keys") or [])) -
                                                   set(map(tuple, old.get("row_keys") or [])))},
                             ensure_ascii=False, default=str))
            sys.stdout.flush()
        frappe.db.rollback()
        frappe.destroy()
    print(json.dumps({"totals": totals}, ensure_ascii=False))


if __name__ == "__main__":
    main()
