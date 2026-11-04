#!/usr/bin/env python3
"""产品地图闸门（评估与审定 docs/work/2026-11-04-product-map.md；地图 scripts/impact/product-map/）。

四条规则（离线、只读）：
1. 未登记：代码里发现的入口（锚点）不在任何地图行、也不在 not_entries.toml → 失败；site_config 键没在 config.toml 分类 → 失败；
   白名单接口找不到调用方（前端、Python、CC 桌面版、站外配置、部署脚本都没调它）又没在 not_entries.toml 登记 → 失败。
2. 已过期：地图行的锚点在代码里找不到、引用的流程 / 入口 / 共用件 / 单测不存在 → 失败；not_entries 里的锚点已不在代码里也失败。
3. 没有测试：某行某端不是绿（流程 / 单测 / 离线入口 / 业务探针）也不是真机（标了只能真机、写了理由与清单）→ 失败，
   除非「行 id@端」在 untested_baseline.txt。基线只许减少：新加的条目、已经有测试或已不存在的条目都失败。
   「上线必验」的行每端都要有测试，或者有探针。
4. 影响清单：相对 merge-base 的改动命中的行（入口文件、共用件、读到的配置键）：要跑的流程、要点的入口、真机单、
   部署后要逐租户核的上线必验行、对照组缺格。只出清单，不判失败。

    python3 scripts/check-product-map.py                      # 闸门 + 影响清单（相对 origin/main 的分叉点，含未提交）
    python3 scripts/check-product-map.py --markdown --out F   # 清单按 Markdown 写进 F（CI 摘要）
    python3 scripts/check-product-map.py --files a.py b.js    # 按给定文件算影响（演练、审查用）
    python3 scripts/check-product-map.py --suggest            # 打印没登记的入口的起草行（自动推断测试），人工核过再贴进地图
    python3 scripts/check-product-map.py --export F.json      # 部署用的导出（镜像构建时生成，随部署工具同步到生产机）：
                                                              # 配置分类、上线必验行与探针、只能真机的行；只读地图文件，不扫代码
    python3 scripts/check-product-map.py --batch --files …    # 本批（上次放上生产到这次）命中的上线必验行与真机单，给 deploy.sh 打印

退出码：0 = 地图与代码一致；1 = 有问题（逐条打印）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "impact"))
import product_map as pm  # noqa: E402

END_NAMES = {"desktop": "电脑", "mobile": "手机"}


def check(product_map: pm.Map, repo: pm.Repo, found: dict, keys: dict, families: dict, flows, impact, parts,
          endpoints: dict | None = None) -> list[str]:
    errors = list(product_map.errors)
    errors += pm.validate(product_map, repo, flows, impact, parts)
    anchors = {row.anchor: row for row in product_map.rows.values()}
    # 1. 未登记
    for anchor, item in sorted(found.items()):
        if anchor not in anchors and anchor not in product_map.not_entries:
            errors.append(f"未登记：{item.product} {item.kind} {anchor}（{item.where}；{', '.join(item.files[:2])}）"
                          " —— 在 scripts/impact/product-map/ 加一行并写测试（--suggest 出草稿），或写进 not_entries.toml 并说明原因")
    for key in sorted(keys):
        if key not in product_map.config:
            errors.append(f"未登记：site_config 键 {key}（{', '.join(keys[key][:2])}）—— 在 config.toml 的 [key.{key}] 写 class")
    for prefix in sorted(families):
        if prefix + "*" not in product_map.config:
            errors.append(f"未登记：site_config 前缀族 {prefix}*（{', '.join(families[prefix][:2])}）—— 在 config.toml 的 [family.{prefix}] 写 class")
    # 2. 已过期
    for row in product_map.rows.values():
        if row.kind not in pm.UNSCANNED_KINDS and row.anchor not in found:
            errors.append(f"已过期：行 {row.id} 的锚点 {row.anchor} 在代码里找不到 —— 入口删了就删这一行，改了就改锚点")
        for ref in row.config:
            kind, _, name = ref.partition(":")
            if kind == "site" and name not in product_map.config and not any(
                    name.startswith(spec[:-1]) for spec in product_map.config if spec.endswith("*")):
                errors.append(f"已过期：行 {row.id} 的配置依赖 {ref} 不在 config.toml")
    endpoints = endpoints or {}
    for dotted, endpoint in sorted(endpoints.items()):
        if not endpoint.callers and f"endpoint:{dotted}" not in product_map.not_entries:
            errors.append(f"未登记：白名单接口 {dotted}（{endpoint.file}）找不到调用方 —— 用不到就删掉；"
                          "在用但扫描器认不出（拼名、站外调用）或只给内部用，写进 not_entries.toml 的 endpoint:{dotted} 并说明原因")
    for anchor in sorted(product_map.not_entries):
        if anchor.startswith("endpoint:"):
            endpoint = endpoints.get(anchor[len("endpoint:"):])
            if endpoint is None:
                errors.append(f"已过期：not_entries.toml 里的 {anchor} 接口已经不在了，删掉这一条")
            elif endpoint.callers:
                errors.append(f"已过期：not_entries.toml 里的 {anchor} 现在有调用方（{endpoint.callers[0]}），删掉这一条")
        elif anchor not in found:
            errors.append(f"已过期：not_entries.toml 里的 {anchor} 在代码里找不到，删掉这一条")
    for name in sorted(product_map.config):
        base = name[:-1] if name.endswith("*") else name
        if (name.endswith("*") and base not in families) or (not name.endswith("*") and base not in keys):
            errors.append(f"已过期：config.toml 里的 {name} 代码里已经不读了，删掉这一条")
    # 3. 没有测试
    gaps = set()
    for row in product_map.rows.values():
        levels = pm.status(row, flows, impact)
        for end, level in levels.items():
            if not pm.tested(level):
                gaps.add(f"{row.id}@{end}")
        if row.must_verify and not row.probe and not all(pm.tested(level) for level in levels.values()):
            errors.append(f"没有测试：行 {row.id} 标了上线必验，要每端都有测试，或者写 probe（正式租户只读探针）")
    baseline = set(product_map.baseline)
    if len(baseline) != len(product_map.baseline):
        errors.append("untested_baseline.txt 有重复条目")
    for cell in sorted(gaps - baseline):
        row_id, end = cell.rsplit("@", 1)
        row = product_map.rows[row_id]
        errors.append(f"没有测试：{row.product} {row.where}（行 {row_id}，{END_NAMES[end]}）—— 补流程 / 单测 / 业务冒烟；"
                      "确实只能真机的写 device_only（理由）与 device（步骤与预期）")
    for cell in sorted(baseline - gaps):
        errors.append(f"基线该删：untested_baseline.txt 里的 {cell} 已经有测试或行已不在，删掉这一条（基线只许减少）")
    return errors


def changed(base: str, root=ROOT) -> list[str]:
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout
    base = base if base != "auto" else (os.environ.get("PRODUCT_MAP_BASE") or "origin/main")
    try:
        merge_base = git("merge-base", base, "HEAD").strip()
    except subprocess.CalledProcessError:
        print(f"（取不到 {base} 与 HEAD 的 merge-base，只核地图）", file=sys.stderr)
        return []
    names = set(git("diff", "--name-only", merge_base).splitlines()) | set(git("ls-files", "--others", "--exclude-standard").splitlines())
    return sorted(name for name in names if name)


def impact_rows(product_map, found, keys, changes, root=ROOT, endpoints=None):
    """改动 → 命中的行与原因（入口文件、它调用的接口所在的模块、共用件、读到的配置键）。"""
    called = {}
    for endpoint in (endpoints or {}).values():
        for caller in endpoint.callers:
            called.setdefault(caller, set()).add(endpoint.file)
    shared = tomllib.loads((root / "scripts/impact/shared-parts.toml").read_text(encoding="utf-8"))
    part_paths = {p["id"]: p["paths"] for p in shared["part"]}
    hit_parts = {pid for pid, paths in part_paths.items() if any(pm._match(c, p) for c in changes for p in paths)}
    read_keys = {key for key, files in keys.items() if set(files) & set(changes)}
    hits = {}
    for row in product_map.rows.values():
        why = []
        files = found[row.anchor].files if row.anchor in found else []
        if set(files) & set(changes):
            why.append("改到入口文件")
        elif {module for path in files for module in called.get(path, ())} & set(changes):
            why.append("改到它调用的接口")
        if hit_parts & set(row.parts):
            why.append("改到共用件 " + "、".join(sorted(hit_parts & set(row.parts))))
        if read_keys & {ref.partition(":")[2] for ref in row.config if ref.startswith("site:")}:
            why.append("改到读这项配置的代码")
        if why:
            hits[row.id] = why
    return hits, hit_parts


def render(product_map, hits, flows, impact, markdown: bool) -> str:
    if not hits:
        return "没有改到地图里的入口。"
    rows = [product_map.rows[row_id] for row_id in hits]
    lines = []
    flow_ids = sorted({ref.partition(":")[2].partition("@")[0] for row in rows for ref in row.tests if ref.startswith("flow:")})
    entries = sorted({ref.partition(":")[2] for row in rows for ref in row.tests if ref.startswith("impact:")})
    must = [row for row in rows if row.must_verify]
    device = [row for row in rows if row.device_only]
    red = [row for row in rows if not all(pm.tested(v) for v in pm.status(row, flows, impact).values())]
    if markdown:
        lines += [f"命中地图入口 {len(rows)} 个，其中 {len(red)} 个至少一端没有测试（红 / 黄）：", "",
                  "| 入口 | 产品 | 渠道 | 位置 | 电脑 | 手机 | 为什么命中 |", "|---|---|---|---|---|---|---|"]
        for row in rows:
            levels = pm.status(row, flows, impact)
            lines.append(f"| `{row.id}` | {row.product} | {row.channel} | {row.where} | {levels.get('desktop', '—')} | "
                         f"{levels.get('mobile', '—')} | {'；'.join(hits[row.id])} |")
        lines.append("")
        if flow_ids:
            lines.append("要跑的流程：`python3 scripts/flows/run.py " + " ".join(f"--flow {f}" for f in flow_ids) + "`")
        if entries:
            lines.append("要点的共用件入口：" + "、".join(f"`{e}`" for e in entries))
        if must:
            lines.append("部署后逐个正式租户核（上线必验）：" + "、".join(f"`{row.id}`" for row in must))
        if device:
            lines.append("")
            lines.append("本批真机单：")
            lines += [f"- `{row.id}`：{row.device}（只能真机：{row.device_only}）" for row in device]
    else:
        for row in rows:
            levels = pm.status(row, flows, impact)
            lines.append(f"  {row.id}  {row.product} · {row.where} · " + " ".join(f"{END_NAMES[e]}{v}" for e, v in levels.items())
                         + f"  ← {'；'.join(hits[row.id])}")
        if flow_ids:
            lines.append("要跑的流程：" + " ".join(flow_ids))
        if must:
            lines.append("部署后逐个正式租户核：" + " ".join(row.id for row in must))
    groups = {}
    for row in rows:
        if row.group:
            groups.setdefault(row.group, set())
    for row in product_map.rows.values():
        if row.group in groups:
            groups[row.group].add(row.product)
    gaps = [f"{group}（只有 {'、'.join(sorted(products))}）" for group, products in sorted(groups.items())
            if len(products) < 2 and not any(r.na for r in product_map.rows.values() if r.group == group)]
    if gaps:
        lines.append("")
        lines.append("对照组缺格（同一功能另一个产品没有登记，也没写 na）：" + "、".join(gaps))
    return "\n".join(lines)


def suggest(product_map, found) -> str:
    anchors = {row.anchor for row in product_map.rows.values()}
    out = []
    for anchor, item in sorted(found.items()):
        if anchor in anchors or anchor in product_map.not_entries:
            continue
        out.append("\n".join(["[[row]]", f'id = "{pm.CODE.get(item.product, "shared")}.TODO"', f'product = "{item.product}"',
                              f'channel = "{item.channel}"', "ends = [" + ", ".join(f'"{e}"' for e in item.ends) + "]",
                              f'kind = "{item.kind}"', f'anchor = "{anchor}"', f'where = "{item.where}"', "tests = []", ""]))
    return "\n".join(out) or "没有没登记的入口。"


EXPORT_VERSION = 1


def export(product_map: pm.Map) -> dict:
    """部署脚本读的那一份（scripts/production/check-tenant-config.py、check-tenant-entries.py）：只放生产机要用的。"""
    rows = [{"id": row.id, "product": row.product, "products": row.products, "channel": row.channel, "where": row.where,
             "config": row.config, "must_verify": row.must_verify, "probe": row.probe, "device_only": row.device_only,
             "device": row.device}
            for row in product_map.rows.values() if row.must_verify or row.device_only]
    return {"version": EXPORT_VERSION, "config": product_map.config, "rows": sorted(rows, key=lambda row: row["id"])}


def batch(product_map, hits) -> str:
    """本批命中的上线必验行与真机单（deploy.sh 在回读之后打印）。"""
    rows = [product_map.rows[row_id] for row_id in sorted(hits)]
    must = [row for row in rows if row.must_verify]
    device = [row for row in rows if row.device_only]
    lines = [f"本批改到地图入口 {len(rows)} 个；上线必验 {len(must)} 个（回读已逐个正式租户走过探针），真机单 {len(device)} 项"]
    lines += [f"  上线必验 {row.id}：{row.where}" for row in must]
    lines += [f"  真机 {row.id}：{row.device}（只能真机：{row.device_only}）" for row in device]
    if not device:
        lines.append("  真机单：本批没有只能真机的入口")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="auto", help="影响清单的基线（默认 PRODUCT_MAP_BASE 或 origin/main 的分叉点）")
    parser.add_argument("--files", nargs="*", help="不看 diff，按这些文件算影响")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--out", help="清单写到这个文件（GITHUB_STEP_SUMMARY 追加）")
    parser.add_argument("--suggest", action="store_true", help="打印没登记的入口的起草行")
    parser.add_argument("--export", metavar="FILE", help="写部署用的导出（只读地图文件，不扫代码、不判闸门）")
    parser.add_argument("--batch", action="store_true", help="只打印 --files 命中的上线必验行与真机单，不判闸门")
    args = parser.parse_args(argv)
    started = time.monotonic()
    if args.export:
        product_map = pm.load()
        if product_map.errors:
            for error in product_map.errors:
                print("  - " + error, file=sys.stderr)
            return 1
        Path(args.export).write_text(json.dumps(export(product_map), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return 0
    repo = pm.Repo(ROOT)
    product_map = pm.load()
    found = pm.discover(repo)
    keys, families = pm.discover_config(repo)
    endpoints = pm.discover_endpoints(repo)
    flows, impact, parts = pm.test_sources(ROOT)
    if args.suggest:
        print(suggest(product_map, found))
        return 0
    changes = args.files if args.files is not None else changed(args.base)
    hits, _ = impact_rows(product_map, found, keys, changes, endpoints=endpoints)
    if args.batch:
        print(batch(product_map, hits))
        return 0
    errors = check(product_map, repo, found, keys, families, flows, impact, parts, endpoints)
    report = render(product_map, hits, flows, impact, args.markdown)
    print(report)
    if args.out:
        mode = "a" if args.out == os.environ.get("GITHUB_STEP_SUMMARY") else "w"
        with open(args.out, mode, encoding="utf-8") as handle:
            handle.write("## 产品地图影响清单\n\n" + report + "\n")
    elapsed = time.monotonic() - started
    if errors:
        print(f"\n地图与代码对不上（共 {len(errors)} 处）：", file=sys.stderr)
        for error in errors:
            print("  - " + error, file=sys.stderr)
        return 1
    print(f"\n地图与代码一致：{len(product_map.rows)} 行、{len(found)} 个发现到的锚点、"
          f"没有测试的存量 {len(product_map.baseline)} 条（{elapsed:.2f} 秒）。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
