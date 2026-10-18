// CC 画面比对：两次 shoot.mjs 的输出（前 / 后）逐场景比「计算样式快照」与截图，出差异报告（docs/work/2026-10-17-cc-frontend-standards.md 5.2）。
//
//   node scripts/cc-visual/compare.mjs --before <前的输出> --after <后的输出> --report <报告目录>
//        [--noise N]                    排查用：各通道相差 ≤ N 不算差异（默认 0）
//        [--git-range <基点>..<分支>]   覆盖检查：这段 diff 里 frontend/assistant/src 改到的 cc-* 类与 data-slot，有没有场景渲染到
//        [--no-reshoot]                 只有像素差的场景不复拍（排查用）
//        [--expect-root 名,…]            这几个 <html> 属性的变化是本次提交预期的 DOM 标记（例：data-cc-touch），单列、不算样式差异
//
// · 快照：按元素路径对齐，比外框（0.01px）和每个受管属性；节点只在一边也算差异。
// · 截图：字节相同直接算 0 差；不同就在无界面 Chrome 里用 canvas getImageData 逐像素比（不装 PIL / pixelmatch），
//   默认 0 容差（--noise N 只供排查），差异像素在「后」的图上标红，写成 <报告目录>/<组>/<场景>.diff.png。
// · 两遍要可比：两份 run.json 都在（缺 = 那一遍没跑完），环境、深浅色、场景筛选和每组的场景清单一致；场景只在一边跑过也算差异。
//   不一致或缺场景时退出码 1，不能拿一个不完整的「后」当通过。
// · 只有像素差、样式 0 差的场景：按 run.json 里的产物目录把前、后各复拍（同一套 shoot），前、后出过的图里有一模一样的一对就记成
//   「光栅抖动」单列（泰文字形、窄屏长对话输入框那一条渐变在机器忙时偶发不同），最多 3 轮仍没有才算差异；不调容差。产物目录不在了就照原样算差异。
// · 覆盖检查：diff 里删掉的行（-）上的类按「前」的快照核对，新加的行（+）按「后」的快照核对。
// · 报告：<报告目录>/report.md（中文）与 report.json；有任何差异退出码 1，全部 0 差退出码 0。

import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { launch, shoot } from "./shoot.mjs";

const LIMIT = 30; // 每个场景报告里最多列多少条样式差异
const RESHOOTS = 3; // 只有像素差的场景前、后最多各复拍几轮
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

/** 覆盖检查：git diff 里 src 改到的 cc-* 类与 data-slot，删掉的（- 行）与新加的（+ 行）分开记。 */
export function changedHooks(diffText) {
  const removed = new Set();
  const added = new Set();
  for (const line of diffText.split("\n")) {
    if (!/^[+-]/.test(line) || /^(\+\+\+|---)/.test(line)) continue;
    const into = line[0] === "-" ? removed : added;
    for (const m of line.matchAll(/(?<![\w-])(cc-[a-z0-9-]+)/g)) into.add("." + m[1]);
    for (const m of line.matchAll(/data-slot="([\w-]+)"/g)) into.add(`[${m[1]}]`);
  }
  return { removed, added };
}

/** 每个 cc-* 类 / data-slot：删掉的要在「前」渲染过，新加的要在「后」渲染过。 */
export function coverageOf({ removed, added }, seenBefore, seenAfter) {
  return [...new Set([...removed, ...added])].sort().map((hook) => {
    const need = [];
    if (removed.has(hook) && !seenBefore.has(hook)) need.push("前");
    if (added.has(hook) && !seenAfter.has(hook)) need.push("后");
    return { hook, rendered: need.length === 0, side: removed.has(hook) && added.has(hook) ? "改" : removed.has(hook) ? "删" : "加", missing: need };
  });
}

const gitDiff = (range) => execFileSync("git", ["diff", "--unified=0", range, "--", "frontend/assistant/src"],
  { cwd: resolve(fileURLToPath(new URL("../..", import.meta.url))), encoding: "utf8" });

/** 两份 run.json 能不能比：都在、环境 / 深浅色 / 场景筛选 / 每组场景清单一致。返回不一致的说明。 */
export function runMismatch(ra, rb) {
  const out = [];
  if (!ra) out.push("「前」没有 run.json（那一遍没跑完或不是 shoot.mjs 的输出）");
  if (!rb) out.push("「后」没有 run.json（那一遍没跑完或不是 shoot.mjs 的输出）");
  if (!ra || !rb) return out;
  for (const key of ["envs", "schemes", "only"]) {
    if (JSON.stringify(ra[key] ?? null) !== JSON.stringify(rb[key] ?? null)) out.push(`${key} 不同：${JSON.stringify(ra[key] ?? null)} / ${JSON.stringify(rb[key] ?? null)}`);
  }
  const lanes = (r) => new Map(r.lanes.map((l) => [l.lane, l.scenes.join(",")]));
  const la = lanes(ra);
  const lb = lanes(rb);
  for (const name of new Set([...la.keys(), ...lb.keys()])) {
    if (!la.has(name)) out.push(`组 ${name} 只在「后」`);
    else if (!lb.has(name)) out.push(`组 ${name} 只在「前」`);
    else if (la.get(name) !== lb.get(name)) out.push(`组 ${name} 的场景清单不同`);
  }
  return out;
}

const seenIn = (snap, into) => {
  for (const n of snap.nodes) {
    if (n.hidden) continue;
    for (const m of n.l.matchAll(/\.(cc-[\w-]+)/g)) into.add("." + m[1]);
    const slot = n.l.match(/\[([\w-]+)\]/);
    if (slot) into.add(`[${slot[1]}]`);
  }
};

export async function compare({ before, after, report, range, noise = NOISE_DEFAULT, reshoot = true, expectRoot = [] }) {
  before = resolve(before);
  after = resolve(after);
  report = resolve(report);
  mkdirSync(report, { recursive: true });
  const runs = [before, after].map((d) => (existsSync(join(d, "run.json")) ? load(join(d, "run.json")) : null));
  const mismatch = runMismatch(runs[0], runs[1]);
  const groups = new Set([...readdirSync(before), ...readdirSync(after)].filter((d) => !d.startsWith(".") && d !== "run.json"));
  const rows = [];
  const b = await launch(join(report, ".profile"));
  try {
    return await compareWith(b, { before, after, report, range, noise, reshoot, runs, mismatch, groups, rows, expectRoot });
  } finally {
    await b.close();
    rmSync(join(report, ".profile"), { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
}

async function compareWith(b, { before, after, report, range, noise, reshoot, runs, mismatch, groups, rows, expectRoot }) {
  await b.send("Runtime.enable");
  await b.send("Page.navigate", { url: "about:blank" });
  await b.ev(PIXEL_PAGE);
  const pixels = (pa, pb) => b.ev(`window.__ccvPixels("data:image/png;base64,${pa.toString("base64")}", "data:image/png;base64,${pb.toString("base64")}", ${noise})`);
  const seenBefore = new Set();
  const seenAfter = new Set();
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
        // 只在一边跑过：算差异（「后」缺组、缺场景时不能判 0 差）
        row.missing = existsSync(ja) ? "后" : "前";
        row.notes.push(existsSync(ja) ? "只在前跑过（「后」缺这个场景）" : "只在后跑过（「前」缺这个场景）");
        rows.push(row);
        continue;
      }
      const sa = load(ja);
      const sb = load(jb);
      seenIn(sa, seenBefore);
      seenIn(sb, seenAfter);
      const all = diffSnapshots(sa, sb);
      // 预期的根节点标记（--expect-root）单列：只是 <html> 上多 / 少一个属性，不是样式差异
      const expected = all.filter((d) => d.kind === "根节点" && expectRoot.includes(d.label));
      if (expected.length) row.notes.push("预期的根节点变化：" + expected.map((d) => `${d.label} ${d.before ?? "（无）"} → ${d.after ?? "（无）"}`).join("；"));
      const diffs = all.filter((d) => !expected.includes(d));
      row.styles = diffs.length;
      row.styleDiffs = diffs.slice(0, LIMIT);
      if (sa.missed?.length || sb.missed?.length) row.notes.push("动作没做完：" + [...new Set([...(sa.missed ?? []), ...(sb.missed ?? [])])].join("；"));
      if (sb.running) row.running = sb.running;
      const pa = readFileSync(join(before, group, scene + ".png"));
      const pb = readFileSync(join(after, group, scene + ".png"));
      if (!pa.equals(pb)) {
        const out = await pixels(pa, pb);
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
  // 只有像素差（样式 0 差）的场景：前、后各复拍，直到前、后各出过的图里有一模一样的一对（同一份画面在机器忙时偶尔光栅成另一个样子，
  // 实测窄屏长对话输入框那一条带），最多 RESHOOTS 轮；一直没有一样的一对才算差异
  const pixelOnly = rows.filter((r) => !r.styles && r.pixels > 0 && !r.missing);
  if (reshoot && pixelOnly.length) {
    const dists = runs.map((r) => r?.dist);
    if (dists.every((d) => d && existsSync(join(d, "index.html")))) {
      for (const row of pixelOnly) {
        const scheme = row.group.slice(row.group.lastIndexOf("-") + 1);
        const env = row.group.slice(0, row.group.lastIndexOf("-"));
        const seen = [[readFileSync(join(before, row.group, row.scene + ".png"))], [readFileSync(join(after, row.group, row.scene + ".png"))]];
        let rounds = 0;
        let matched = false;
        let last = null;
        while (!matched && rounds < RESHOOTS) {
          rounds++;
          for (const [i, dist] of dists.entries()) {
            const out = join(report, ".reshoot", String(i));
            await shoot({ dist, out, envs: [env], schemes: [scheme], only: [row.scene], jobs: 1, quiet: true });
            seen[i].push(readFileSync(join(out, row.group, row.scene + ".png")));
          }
          rmSync(join(report, ".reshoot"), { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
          matched = seen[0].some((a) => seen[1].some((b) => a.equals(b)));
          if (!matched) last = await pixels(seen[0].at(-1), seen[1].at(-1));
        }
        if (matched) {
          row.jitter = row.pixels;
          row.notes.push(`只有像素差 ${row.pixels}，前后各复拍 ${rounds} 次后出现一模一样的一对：光栅抖动，不计差异`);
          row.pixels = 0;
          delete row.overlay;
        } else {
          row.notes.push(`只有像素差，前后各复拍 ${rounds} 次仍没有一样的一对（最后一次差 ${last.diff === -1 ? "尺寸不同" : last.diff}）：算差异`);
        }
      }
    } else {
      for (const row of pixelOnly) row.notes.push("只有像素差；run.json 里的产物目录不在了，没能复拍");
    }
  }
  const coverage = range ? coverageOf(changedHooks(gitDiff(range)), seenBefore, seenAfter) : null;
  const changed = rows.filter((r) => r.styles || r.pixels || r.missing);
  const result = { before, after, at: new Date().toISOString(), tolerance: noise, scenes: rows.length, changed: changed.length, mismatch,
    jitter: rows.filter((r) => r.jitter).length, noise: rows.reduce((n, x) => n + (x.noise ?? 0), 0), rows, coverage,
    unmocked: runs.map((r) => r?.unmocked ?? []) };
  writeFileSync(join(report, "report.json"), JSON.stringify(result, null, 1));
  writeFileSync(join(report, "report.md"), markdown(result));
  return result;
}

function markdown(r) {
  const lines = [];
  lines.push("# CC 画面比对报告", "");
  if (r.mismatch.length) lines.push("## 两遍不可比（退出码 1）", "", ...r.mismatch.map((m) => `- ${m}`), "");
  lines.push(`- 前：\`${r.before}\``, `- 后：\`${r.after}\``, `- 场景：${r.scenes} 个（环境 × 深浅 × 场景），有差异 ${r.changed} 个（含只在一边跑过的），复拍后判为光栅抖动 ${r.jitter} 个`,
    r.tolerance ? `- 像素容差：各通道相差 ≤ ${r.tolerance} 记为光栅噪声，不计差异（本次 ${r.noise} 个像素）；样式快照不设容差` : "- 像素容差：0（逐像素一模一样）；样式快照不设容差", "");
  lines.push("| 组 | 场景 | 样式差异 | 差异像素 | 备注 |", "| :- | :- | -: | -: | :- |");
  for (const row of r.rows) {
    if (!row.styles && !row.pixels && !row.notes.length && !row.running) continue;
    const notes = [...row.notes, row.running ? `截图前在跑的动画：${row.running.join(", ") || "无"}` : ""].filter(Boolean).join("；");
    lines.push(`| ${row.group} | ${row.scene} | ${row.styles} | ${row.pixels} | ${notes.replace(/\|/g, "\\|")} |`);
  }
  lines.push("");
  for (const row of r.rows.filter((x) => x.styles || x.pixels || x.missing)) {
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
    lines.push(`本段 diff 改到 ${r.coverage.length} 个 cc-* 类 / data-slot（删掉的按「前」核对、新加的按「后」核对），场景渲染到 ${r.coverage.length - missing.length} 个。`);
    if (missing.length) lines.push("", "没覆盖到（补场景或人工点，并写明是哪一种）：", ...missing.map((c) => `- \`${c.hook}\`（${c.side}；「${c.missing.join("」「")}」那一遍没渲染到）`));
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
  const r = await compare({ before, after, report, range: get("--git-range"), noise: Number(get("--noise") ?? NOISE_DEFAULT), reshoot: !argv.includes("--no-reshoot"),
    expectRoot: (get("--expect-root") ?? "").split(",").filter(Boolean) });
  const styles = r.rows.reduce((n, x) => n + x.styles, 0);
  const pixels = r.rows.reduce((n, x) => n + Math.max(0, x.pixels), 0);
  const missing = r.rows.filter((x) => x.missing).length;
  for (const m of r.mismatch) console.log(`两遍不可比：${m}`);
  console.log(`${r.scenes} 个场景，有差异 ${r.changed} 个（样式差异 ${styles} 条，差异像素 ${pixels}，只在一边跑过 ${missing} 个，复拍判为光栅抖动 ${r.jitter} 个，光栅噪声 ${r.noise} 像素不计）；报告：${join(resolve(report), "report.md")}`);
  process.exit(r.changed || r.mismatch.length ? 1 : 0);
}
