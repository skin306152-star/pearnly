"""产品地图看板雏形（G 块）：一张自带样式、不连外网的静态页——各产品 × 渠道的红黄绿、对照组矩阵、全量入口、接口分诊与本次影响清单。

由 `scripts/check-product-map.py --html <文件>` 生成；CI 在 free-arm-2 的 runner 上落到本机目录，要看时按需 `ssh -L` 或拷回本机打开，
不常驻任何服务（评估第 9 节、主会话审定第 6 条）。
"""
from __future__ import annotations

import html
from collections import Counter

import product_map as pm

END_NAMES = {"desktop": "电脑", "mobile": "手机"}
CHANNEL_NAMES = {"desk": "网页", "line": "LINE", "cc-chat": "CC 对话", "cc-operator": "CC 代录", "cc-desktop": "CC 桌面端"}
KIND_NAMES = {"dynamic": "拼名调用（在用，待核实）", "compat": "兼容保留（待核实期限）", "internal": "只给内部用", "delete": "待删候选"}
LEVEL_CLASS = {pm.GREEN: "g", pm.DEVICE: "d", pm.YELLOW: "y", pm.RED: "r"}
CSS = """
:root{--bg:#fff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--g:#1a7f37;--y:#9a6700;--r:#cf222e;--d:#8250df;--chip:#f6f8fa}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#30363d;--g:#3fb950;--y:#d29922;--r:#f85149;--d:#a371f7;--chip:#161b22}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,"PingFang SC","Noto Sans Thai",sans-serif}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}.muted{color:var(--muted)}
table{border-collapse:collapse;width:100%;margin:4px 0}th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
th{font-weight:600;white-space:nowrap}.num{text-align:right;font-variant-numeric:tabular-nums}
.g{color:var(--g)}.y{color:var(--y)}.r{color:var(--r)}.d{color:var(--d)}.chip{background:var(--chip);border-radius:4px;padding:0 4px;font-family:ui-monospace,monospace;font-size:12px}
.wrap{overflow-x:auto}input{font:inherit;padding:4px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);width:min(420px,100%)}
"""
FILTER = """
<script>
document.getElementById('q').addEventListener('input',function(){var q=this.value.toLowerCase();
document.querySelectorAll('#rows tbody tr').forEach(function(tr){tr.style.display=tr.textContent.toLowerCase().indexOf(q)<0?'none':''})})
</script>
"""


def _e(text) -> str:
    return html.escape(str(text))


def _cell(level: str) -> str:
    return f'<span class="{LEVEL_CLASS[level]}">{_e(level)}</span>' if level else '<span class="muted">—</span>'


def render(product_map: pm.Map, flows: dict, impact: dict, endpoints: dict, *, commit: str = "", hits: dict | None = None) -> str:
    levels = {row.id: pm.status(row, flows, impact) for row in product_map.rows.values()}
    out = ["<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
           "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>产品地图</title>",
           f"<style>{CSS}</style></head><body>", "<h1>产品地图</h1>",
           f'<div class="muted">提交 {_e(commit or "工作树")} · {len(product_map.rows)} 个入口 · 没有测试的存量 {len(product_map.baseline)} 条（行@端）</div>']
    # 汇总：产品 × 渠道 × 颜色（按端计）
    tally = Counter()
    for row in product_map.rows.values():
        for level in levels[row.id].values():
            tally[(row.product, row.channel, level)] += 1
    out += ["<h2>按产品、渠道（每个入口每端算一格）</h2><div class=\"wrap\"><table><tr><th>产品</th><th>渠道</th>"
            + "".join(f'<th class="num {LEVEL_CLASS[l]}">{l}</th>' for l in (pm.GREEN, pm.DEVICE, pm.YELLOW, pm.RED)) + "</tr>"]
    for product, channel in sorted({(p, c) for p, c, _ in tally}):
        out.append(f"<tr><td>{_e(product)}</td><td>{_e(CHANNEL_NAMES.get(channel, channel))}</td>"
                   + "".join(f'<td class="num">{tally[(product, channel, l)] or ""}</td>' for l in (pm.GREEN, pm.DEVICE, pm.YELLOW, pm.RED))
                   + "</tr>")
    out.append("</table></div>")
    if hits:
        out += [f"<h2>本次改动命中的入口（{len(hits)} 个）</h2><div class=\"wrap\"><table><tr><th>入口</th><th>位置</th><th>电脑</th><th>手机</th><th>为什么命中</th></tr>"]
        for row_id in sorted(hits):
            row = product_map.rows[row_id]
            out.append(f'<tr><td><span class="chip">{_e(row_id)}</span></td><td>{_e(row.where)}</td>'
                       f"<td>{_cell(levels[row_id].get('desktop', ''))}</td><td>{_cell(levels[row_id].get('mobile', ''))}</td>"
                       f"<td>{_e('；'.join(hits[row_id]))}</td></tr>")
        out.append("</table></div>")
    # 对照组矩阵：同一功能 × ERP / Cowork × 电脑 / 手机
    groups = {}
    for row in product_map.rows.values():
        if row.group:
            groups.setdefault(row.group, []).append(row)
    products = ("ERP", "Cowork")
    out += ["<h2>对照组（同一功能跨产品、跨端）</h2><div class=\"wrap\"><table><tr><th>功能</th>"
            + "".join(f"<th>{p} {END_NAMES[e]}</th>" for p in products for e in pm.ENDS) + "</tr>"]
    for group, rows in sorted(groups.items()):
        cells = []
        na = {k: v for r in rows for k, v in r.na.items()}
        for product in products:
            mine = [r for r in rows if r.product == product]
            for end in pm.ENDS:
                if not mine:
                    cells.append(f'<td class="muted" title="{_e(na.get(product, ""))}">{"不适用" if product in na else "缺"}</td>')
                    continue
                values = [levels[r.id].get(end, "") for r in mine]
                order = (pm.RED, pm.YELLOW, pm.DEVICE, pm.GREEN)
                worst = min((v for v in values if v), key=order.index, default="")
                cells.append(f"<td>{_cell(worst)}</td>")
        out.append(f'<tr><td><span class="chip">{_e(group)}</span></td>{"".join(cells)}</tr>')
    out.append("</table></div>")
    # 全量入口
    out += ['<h2>全部入口</h2><input id="q" placeholder="筛选：id、位置、颜色、渠道……">',
            '<div class="wrap"><table id="rows"><thead><tr><th>入口</th><th>产品</th><th>渠道</th><th>位置</th><th>电脑</th><th>手机</th>'
            "<th>测试</th><th>上线必验</th></tr></thead><tbody>"]
    for row in sorted(product_map.rows.values(), key=lambda r: (r.product, r.channel, r.id)):
        level = levels[row.id]
        out.append(f'<tr><td><span class="chip">{_e(row.id)}</span></td><td>{_e(row.product)}</td>'
                   f"<td>{_e(CHANNEL_NAMES.get(row.channel, row.channel))}</td><td>{_e(row.where)}</td>"
                   f"<td>{_cell(level.get('desktop', ''))}</td><td>{_cell(level.get('mobile', ''))}</td>"
                   f"<td>{_e(' '.join(row.tests)) or '<span class=\"r\">无</span>'}</td>"
                   f"<td>{_e(row.probe.get('type', '')) if row.must_verify else ''}</td></tr>")
    out.append("</tbody></table></div>")
    # 接口分诊
    kinds = Counter(kind for anchor, kind in product_map.kinds.items() if anchor.startswith("endpoint:"))
    no_caller = sum(1 for e in endpoints.values() if not e.callers)
    out += [f"<h2>白名单接口</h2><div>{len(endpoints)} 个，找不到调用方的 {no_caller} 个（都已在 not_entries.toml 登记原因）</div>",
            '<div class="wrap"><table><tr><th>分诊</th><th class="num">个数</th></tr>']
    out += [f"<tr><td>{_e(KIND_NAMES.get(k, k))}</td><td class=\"num\">{n}</td></tr>" for k, n in kinds.most_common()]
    out.append("</table></div>")
    out.append(FILTER + "</body></html>")
    return "\n".join(out)
