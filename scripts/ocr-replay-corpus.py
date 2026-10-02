"""回放压测的语料池：从一个测试租户里以前**真识别过**的真实件导出共享回放库与原件池（不调谷歌，不改任何业务数据）。

- 共享回放库：``<sites>/pearnly-ocr-replay/<sha256>-<页数>.json``（识别结果与证据），各测试租户回放时按内容取
  （``pearnly_cowork.ocr.execution.replay``）。压测机往哪个测试租户传都能回放。
- 原件池：``<--pool>/<sha256>.<扩展名>``，加一份 ``manifest.jsonl``（sha256、文件名、类型、页数、字节数），不带原文件名、
  不带识别内容；压测机从这里取件上传。
- 只收真实件：跳过压测工具现画的合成票（文件名以 ``--skip-prefix`` 开头，默认 stress_ / mu_ / ms_），跳过回放出来的单。
- 同一内容、同样页数只导一份（取最近一次真识别的结果）。重复跑只补新的，已有的不重写。

在测试租户所在的 bench 里跑（生产机上用一次性容器挂 Cowork 的 sites 卷，与其它站点脚本相同）：

    env/bin/python /candidate/scripts/ocr-replay-corpus.py --site testcowork.pearnly.com --pool /pool [--dry-run]
"""
import argparse
import collections
import json
import os
import sys

SKIP = ("stress_", "mu_", "ms_")
EXTENSIONS = {"image": None, "pdf": ".pdf", "document": None}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ocr-replay-corpus")
    parser.add_argument("--site", required=True)
    parser.add_argument("--pool", required=True, help="原件池目录")
    parser.add_argument("--skip-prefix", action="append", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    skip = tuple(args.skip_prefix or SKIP)

    import frappe
    from pearnly_core.execution import replay as core_replay

    if not core_replay.TEST_SITES.fullmatch(args.site):
        print(f"refused: {args.site} is not a test tenant", file=sys.stderr)
        return 2
    os.chdir(os.path.join(os.environ.get("FRAPPE_BENCH", "/home/frappe/frappe-bench"), "sites"))
    frappe.init(site=args.site)
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        return export(frappe, args.site, args.pool, skip, args.dry_run)
    finally:
        frappe.destroy()


def export(frappe, site, pool, skip, dry_run):
    from pearnly_cowork.files import service as files
    from pearnly_cowork.files import access as files_access
    from pearnly_cowork.ocr.execution import access, replay

    clauses = " AND ".join("s.filename NOT LIKE %s" for _ in skip)
    rows = frappe.db.sql(f"""SELECT b.name batch, b.page_count, s.name source, s.sha256, s.kind, s.filename, s.byte_size
        FROM `tab{access.DOCTYPE}` b JOIN `tab{files_access.SOURCE}` s ON s.name = b.source_file
        WHERE b.phase IN ('saved', 'not_invoice') AND IFNULL(b.result_json, '') != ''
          AND IFNULL(b.provider_evidence, '') NOT LIKE %s AND {clauses}
        ORDER BY b.creation DESC""", ('%"replay"%', *(prefix.replace("_", "\\_") + "%" for prefix in skip)), as_dict=True)
    chosen = {}
    for row in rows:
        chosen.setdefault((row.sha256, int(row.page_count)), row)  # 最近一次真识别
    library = os.path.join(frappe.local.sites_path, replay.LIBRARY)
    counts = collections.Counter()
    if not dry_run:
        os.makedirs(library, exist_ok=True)
        os.makedirs(pool, exist_ok=True)
    manifest = []
    for (sha256, pages), row in sorted(chosen.items()):
        counts["files"] += 1
        counts["multipage" if pages > 1 else "single"] += 1
        counts["kind:" + str(row.kind)] += 1
        extension = EXTENSIONS.get(row.kind) or os.path.splitext(row.filename or "")[1].lower() or ".bin"
        entry, original = f"{sha256}-{pages}", f"{sha256}{extension}"
        manifest.append({"sha256": sha256, "file": original, "kind": row.kind, "page_count": pages,
                         "byte_size": int(row.byte_size or 0)})
        if dry_run:
            continue
        target = os.path.join(library, entry + ".json")
        if not os.path.exists(target):
            stored = frappe.db.get_value(access.DOCTYPE, row.batch, ["result_json", "provider_evidence"], as_dict=True)
            _write(target, json.dumps({"result_json": stored.result_json, "provider_evidence": stored.provider_evidence,
                                       "site": site, "batch": row.batch}, ensure_ascii=False))
            counts["library_written"] += 1
        destination = os.path.join(pool, original)
        if not os.path.exists(destination):
            try:
                data = files.read_bytes(frappe.get_doc(files_access.SOURCE, row.source))
            except Exception as error:  # 原件读不到（被删、校验对不上）：这份不进原件池，库里照样留着
                counts["original_unreadable:" + type(error).__name__] += 1
                manifest.pop()
                continue
            _write(destination, data, binary=True)
            counts["pool_written"] += 1
    if not dry_run:
        _write(os.path.join(pool, "manifest.jsonl"), "".join(json.dumps(item) + "\n" for item in manifest))
    print(json.dumps({"site": site, "pool_files": len(manifest), **counts}, ensure_ascii=False, sort_keys=True))
    return 0


def _write(path, content, binary=False):
    temporary = path + ".tmp"
    with open(temporary, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as handle:
        handle.write(content)
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())
