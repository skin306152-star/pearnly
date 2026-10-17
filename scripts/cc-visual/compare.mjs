// CC 画面比对：两次 shoot.mjs 的输出（前 / 后）逐场景比「计算样式快照」与截图，出差异报告（docs/work/2026-10-17-cc-frontend-standards.md 5.2）。
//
//   node scripts/cc-visual/compare.mjs --before <前的输出> --after <后的输出> --report <报告目录>
//        [--noise N]                    排查用：各通道相差 ≤ N 不算差异（默认 0）
//        [--git-range <基点>..<分支>]   覆盖检查：这段 diff 里 frontend/assistant/src 改到的 cc-* 类与 data-slot，有没有场景渲染到
//
// · 快照：按元素路径对齐，比外框（0.01px）和每个受管属性；节点只在一边也算差异。
// · 截图：字节相同直接算 0 差；不同就在无界面 Chrome 里用 canvas getImageData 逐像素比（不装 PIL / pixelmatch），
//   默认 0 容差（--noise N 只供排查），差异像素在「后」的图上标红，写成 <报告目录>/<组>/<场景>.diff.png。
// · 报告：<报告目录>/report.md（中文）与 report.json；有任何差异退出码 1，全部 0 差退出码 0。

import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { launch } from "./shoot.mjs";

const LIMIT = 30; // 每个场景报告里最多列多少条样式差异
// 逐像素比的容差，默认 0（纯替换必须 0 像素差）。--noise N：各通道相差 ≤ N 记为光栅噪声单独报数、不算差异——
// 只在排查时用；shoot.mjs 已让过渡与动画平时归零，同一构建连跑两遍是 0 差、0 噪声。颜色、尺寸、位置的变化由样式快照逐值精确比。
const NOISE_DEFAULT = 0;

function load(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

/** 两份快照的差异：[{ kind, key, label, prop?, before?, after? }] */
export function diffSnapshots(a, b) {
  const out = [];
  const props = a.props;
  const index = (s) => new Map(s.nodes.map((n) => [n.k, n]));
  const A = index(a);
  const B = index(b);
  for (const [k, na] of A) {
    const nb = B.get(k);
    if (!nb) { out.push({ kind: "只在前", key: k, label: na.l }); continue; }
    if (!!na.hidden !== !!nb.hidden) { out.push({ kind: "显示", key: k, label: na.l, before: na.hidden ? "none" : "显示", after: nb.hidden ? "none" : "显示" }); continue; }
    if (na.hidden) continue;
    if (na.l !== nb.l) out.push({ kind: "节点", key: k, label: na.l, before: na.l, after: nb.l });
    if (na.r && nb.r && na.r.join() !== nb.r.join()) out.push({ kind: "外框", key: k, label: na.l, before: na.r.join(" "), after: nb.r.join(" ") });
    if (na.c !== nb.c) out.push({ kind: "content", key: k, label: na.l, before: na.c, after: nb.c });
    for (let i = 0; i < props.length; i++) {
      if (na.v[i] !== nb.v[i]) out.push({ kind: "样式", key: k, label: na.l, prop: props[i], before: na.v[i], after: nb.v[i] });
    }
  }
  for (const [k, nb] of B) if (!A.has(k)) out.push({ kind: "只在后", key: k, label: nb.l });
  for (const name of new Set([...Object.keys(a.root ?? {}), ...Object.keys(b.root ?? {})])) {
    if ((a.root ?? {})[name] !== (b.root ?? {})[name]) out.push({ kind: "根节点", key: "html", label: name, before: a.root[name], after: b.root[name] });
  }
  if (JSON.stringify(a.running ?? null) !== JSON.stringify(b.running ?? null)) {
    out.push({ kind: "运行中的动画", key: "document", label: "getAnimations", before: (a.running ?? []).join(","), after: (b.running ?? []).join(",") });
  }
  return out;
}

const PIXEL_PAGE = `window.__ccvPixels = async (a, b, tolerance) => {
  const img = (src) => new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = src; });
  const [ia, ib] = await Promise.all([img(a), img(b)]);
  if (ia.width !== ib.width || ia.height !== ib.height) return { size: [ia.width, ia.height, ib.width, ib.height], diff: -1 };
  const w = ia.width, h = ia.height;
  const ca = new OffscreenCanvas(w, h).getContext("2d"); ca.drawImage(ia, 0, 0);
  const cb = new OffscreenCanvas(w, h); const xb = cb.getContext("2d"); xb.drawImage(ib, 0, 0);
  const da = ca.getImageData(0, 0, w, h).data; const imgB = xb.getImageData(0, 0, w, h); const db = imgB.data;
  let diff = 0; let noise = 0; let box = [w, h, -1, -1];
  for (let p = 0; p < da.length; p += 4) {
    const delta = Math.max(Math.abs(da[p] - db[p]), Math.abs(da[p + 1] - db[p + 1]), Math.abs(da[p + 2] - db[p + 2]), Math.abs(da[p + 3] - db[p + 3]));
    if (delta > 0 && delta <= tolerance) noise++;
    if (delta > tolerance) {
      diff++; const i = p / 4, x = i % w, y = (i - x) / w;
      box = [Math.min(box[0], x), Math.min(box[1], y), Math.max(box[2], x), Math.max(box[3], y)];
      db[p] = 255; db[p + 1] = 0; db[p + 2] = 0; db[p + 3] = 255;
    } else { db[p] = (db[p] + 255 * 2) / 3; db[p + 1] = (db[p + 1] + 255 * 2) / 3; db[p + 2] = (db[p + 2] + 255 * 2) / 3; }
  }
  if (!diff) return { diff: 0, noise };
  xb.putImageData(imgB, 0, 0);
  const blob = await cb.convertToBlob({ type: "image/png" });
  const buf = new Uint8Array(await blob.arrayBuffer());
  let s = ""; for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return { diff, noise, box, overlay: btoa(s) };
};`;

/** 覆盖检查：git diff 里 src 改到的 cc-* 类与 data-slot。 */
function changedHooks(range) {
  const root = resolve(fileURLToPath(new URL("../..", import.meta.url)));
  const text = execFileSync("git", ["diff", "--unified=0", range, "--", "frontend/assistant/src"], { cwd: root, encoding: "utf8" });
  const hooks = new Set();
  for (const line of text.split("\n")) {
    if (!/^[+-]/.test(line) || /^(\+\+\+|---)/.test(line)) continue;
    for (const m of line.matchAll(/(?<![\w-])(cc-[a-z0-9-]+)/g)) hooks.add("." + m[1]);
    for (const m of line.matchAll(/data-slot="([\w-]+)"/g)) hooks.add(`[${m[1]}]`);
  }
  return [...hooks].sort();
}

export async function compare({ before, after, report, range, noise = NOISE_DEFAULT }) {
  before = resolve(before);
  after = resolve(after);
  report = resolve(report);
  mkdirSync(report, { recursive: true });
  const groups = new Set([...readdirSync(before), ...readdirSync(after)].filter((d) => !d.startsWith(".") && d !== "run.json"));
  const rows = [];
  const b = await launch(19890, join(report, ".profile"));
  await b.send("Runtime.enable");
  await b.send("Page.navigate", { url: "about:blank" });
  await b.ev(PIXEL_PAGE);
  const seen = new Set();
  for (const group of [...groups].sort()) {
    const scenes = new Set();
    for (const dir of [before, after]) {
      if (!existsSync(join(dir, group))) continue;
      for (const f of readdirSync(join(dir, group))) if (f.endsWith(".json")) scenes.add(f.slice(0, -5));
    }
    for (const scene of [...scenes].sort()) {
      const row = { group, scene, styles: 0, pixels: 0, notes: [] };
      const ja = join(before, group, scene + ".json");
      const jb = join(after, group, scene + ".json");
      if (!existsSync(ja) || !existsSync(jb)) {
        row.notes.push(existsSync(ja) ? "只在前跑过" : "只在后跑过");
        rows.push(row);
        continue;
      }
      const sa = load(ja);
      const sb = load(jb);
      for (const n of sb.nodes) {
        if (n.hidden) continue;
        for (const m of n.l.matchAll(/\.(cc-[\w-]+)/g)) seen.add("." + m[1]);
        const slot = n.l.match(/\[([\w-]+)\]/);
        if (slot) seen.add(`[${slot[1]}]`);
      }
      const diffs = diffSnapshots(sa, sb);
      row.styles = diffs.length;
      row.styleDiffs = diffs.slice(0, LIMIT);
      if (sa.missed?.length || sb.missed?.length) row.notes.push("动作没做完：" + [...new Set([...(sa.missed ?? []), ...(sb.missed ?? [])])].join("；"));
      if (sb.running) row.running = sb.running;
      const pa = readFileSync(join(before, group, scene + ".png"));
      const pb = readFileSync(join(after, group, scene + ".png"));
      if (!pa.equals(pb)) {
        const out = await b.ev(`window.__ccvPixels("data:image/png;base64,${pa.toString("base64")}", "data:image/png;base64,${pb.toString("base64")}", ${noise})`);
        row.noise = out.noise ?? 0;
        if (out.diff === -1) {
          row.pixels = -1;
          row.notes.push(`截图尺寸不同：${out.size.join(" / ")}`);
        } else if (out.diff > 0) {
          row.pixels = out.diff;
          row.box = out.box;
          mkdirSync(join(report, group), { recursive: true });
          const overlay = join(report, group, scene + ".diff.png");
          writeFileSync(overlay, Buffer.from(out.overlay, "base64"));
          row.overlay = overlay;
        }
      }
      rows.push(row);
    }
  }
  b.close();
  await new Promise((r) => setTimeout(r, 300));
  rmSync(join(report, ".profile"), { recursive: true, force: true });
  const coverage = range ? changedHooks(range).map((hook) => ({ hook, rendered: seen.has(hook) })) : null;
  const runs = [before, after].map((d) => (existsSync(join(d, "run.json")) ? load(join(d, "run.json")) : null));
  const changed = rows.filter((r) => r.styles || r.pixels);
  const result = { before, after, at: new Date().toISOString(), tolerance: noise, scenes: rows.length, changed: changed.length,
    noise: rows.reduce((n, x) => n + (x.noise ?? 0), 0), rows, coverage, unmocked: runs.map((r) => r?.unmocked ?? []) };
  writeFileSync(join(report, "report.json"), JSON.stringify(result, null, 1));
  writeFileSync(join(report, "report.md"), markdown(result));
  return result;
}

function markdown(r) {
  const lines = [];
  lines.push("# CC 画面比对报告", "");
  lines.push(`- 前：\`${r.before}\``, `- 后：\`${r.after}\``, `- 场景：${r.scenes} 个（环境 × 深浅 × 场景），有差异 ${r.changed} 个`,
    r.tolerance ? `- 像素容差：各通道相差 ≤ ${r.tolerance} 记为光栅噪声，不计差异（本次 ${r.noise} 个像素）；样式快照不设容差` : "- 像素容差：0（逐像素一模一样）；样式快照不设容差", "");
  lines.push("| 组 | 场景 | 样式差异 | 差异像素 | 备注 |", "| :- | :- | -: | -: | :- |");
  for (const row of r.rows) {
    if (!row.styles && !row.pixels && !row.notes.length && !row.running) continue;
    const notes = [...row.notes, row.running ? `截图前在跑的动画：${row.running.join(", ") || "无"}` : ""].filter(Boolean).join("；");
    lines.push(`| ${row.group} | ${row.scene} | ${row.styles} | ${row.pixels} | ${notes.replace(/\|/g, "\\|")} |`);
  }
  lines.push("");
  for (const row of r.rows.filter((x) => x.styles || x.pixels)) {
    lines.push(`## ${row.group} / ${row.scene}`, "");
    if (row.overlay) lines.push(`叠图：\`${row.overlay}\`，差异范围 ${row.box.join(", ")}（截图像素）`, "");
    for (const d of row.styleDiffs ?? []) {
      lines.push(`- ${d.kind} \`${d.label}\`${d.prop ? ` ${d.prop}` : ""}：${d.before ?? ""} → ${d.after ?? ""}  （${d.key}）`);
    }
    if (row.styles > LIMIT) lines.push(`- ……另有 ${row.styles - LIMIT} 条`);
    lines.push("");
  }
  if (r.coverage) {
    lines.push("## 覆盖检查", "");
    const missing = r.coverage.filter((c) => !c.rendered);
    lines.push(`本段 diff 改到 ${r.coverage.length} 个 cc-* 类 / data-slot，场景渲染到 ${r.coverage.length - missing.length} 个。`);
    if (missing.length) lines.push("", "没覆盖到（补场景或人工点，并写明是哪一种）：", ...missing.map((c) => `- \`${c.hook}\``));
    lines.push("");
  }
  const unmocked = [...new Set(r.unmocked.flat())];
  if (unmocked.length) lines.push("## 假服务端没模拟的接口", "", ...unmocked.map((u) => `- \`${u}\``), "");
  return lines.join("\n");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const argv = process.argv.slice(2);
  const get = (name) => {
    const i = argv.indexOf(name);
    return i >= 0 ? argv[i + 1] : undefined;
  };
  const before = get("--before");
  const after = get("--after");
  const report = get("--report");
  if (!before || !after || !report) {
    console.error("用法：node scripts/cc-visual/compare.mjs --before <前的输出> --after <后的输出> --report <报告目录> [--git-range A..B]");
    process.exit(2);
  }
  const r = await compare({ before, after, report, range: get("--git-range"), noise: Number(get("--noise") ?? NOISE_DEFAULT) });
  const styles = r.rows.reduce((n, x) => n + x.styles, 0);
  const pixels = r.rows.reduce((n, x) => n + Math.max(0, x.pixels), 0);
  console.log(`${r.scenes} 个场景，有差异 ${r.changed} 个（样式差异 ${styles} 条，差异像素 ${pixels}，光栅噪声 ${r.noise} 像素不计）；报告：${join(resolve(report), "report.md")}`);
  process.exit(r.changed ? 1 : 0);
}
