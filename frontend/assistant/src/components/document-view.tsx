// 右侧结果面板里 Word（.docx）的预览，由 results-panel 按需载入（不进首屏包）。
//
// 内容是服务端读出的纯数据（pearnly_core.assistant.docx_preview）：段落、表格、文档里嵌着的图片。这里只把文字当文字渲染，
// 不拼 HTML、不设链接地址；标签名、对齐方式只从固定的几种里选，图片只收 data: 位图地址（服务端已核过，这里再核一遍）。
// 文档里的脚本、宏、外链因此都到不了页面。手机上整篇竖着滚，宽表格在自己的框里横着滚，不把面板撑宽。

import type { CSSProperties, ReactNode } from "react";
import type { DocBlock, DocCell, DocRun } from "@/lib/managed-agents/api";
import { cn } from "@/lib/utils";

const IMAGE = /^data:image\/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+=*$/;

/** 能显示的图片地址：只有 PNG / JPEG / GIF / WebP 的 data: 地址，其余（网址、javascript:、SVG……）一律不显示。 */
export function safeImage(src: unknown): string | null {
  return typeof src === "string" && IMAGE.test(src) ? src : null;
}

// 段落样式 → 标签与字号（同对话里 Markdown 标题的层级感）；不认识的样式按正文
const STYLES: Record<string, { tag: "p" | "h1" | "h2" | "h3" | "h4" | "h5" | "h6"; className: string }> = {
  title: { tag: "h1", className: "text-2xl font-semibold" },
  subtitle: { tag: "p", className: "text-lg text-ink-3" },
  h1: { tag: "h1", className: "text-xl font-semibold" },
  h2: { tag: "h2", className: "text-lg font-semibold" },
  h3: { tag: "h3", className: "text-prose font-semibold" },
  h4: { tag: "h4", className: "text-prose font-medium" },
  h5: { tag: "h5", className: "text-body font-semibold" },
  h6: { tag: "h6", className: "text-body font-medium" },
};
const BODY = { tag: "p" as const, className: "" };
// 块之间的距离同对话里的 Markdown（V26）：只加上外边距、按 em——标题前 --cc-md-heading，段落、表格前 --cc-md-block，第一块 0；
// 单元格里的段落一个接一个，不加块距
const BLOCK_GAP = "mt-(--cc-md-block) first:mt-0";
const HEADING_GAP = "mt-(--cc-md-heading) first:mt-0";
const ALIGN: Record<string, string> = { center: "text-center", right: "text-right", justify: "text-justify" };

/** 只认表里自己的键（「__proto__」这类键取不到原型上的东西）。 */
function pick<T>(table: Record<string, T>, key: unknown): T | undefined {
  return typeof key === "string" && Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
}

function count(value: unknown, max: number): number | undefined {
  const n = Number(value);
  return Number.isFinite(n) && n >= 1 ? Math.min(Math.floor(n), max) : undefined;
}

function Run({ run }: { run: DocRun }) {
  if ("image" in run) {
    const src = safeImage(run.image);
    if (!src) return null;
    const width = count(run.width, 4000);
    const height = count(run.height, 4000);
    const style: CSSProperties | undefined = width && height ? { width, aspectRatio: `${width} / ${height}` } : undefined;
    return <img src={src} alt="" draggable={false} className="inline-block h-auto max-w-full align-bottom" style={style} />;
  }
  if (typeof run.text !== "string" || !run.text) return null;
  let node: ReactNode = run.text;
  if (run.sup) node = <sup>{node}</sup>;
  else if (run.sub) node = <sub>{node}</sub>;
  const className = cn(
    run.bold && "font-bold",
    run.italic && "italic",
    (run.underline || run.link) && "underline underline-offset-(--cc-link-offset)",
    run.strike && "line-through",
  );
  return className ? <span className={className}>{node}</span> : <>{node}</>;
}

function Paragraph({ block, inCell }: { block: Extract<DocBlock, { type: "paragraph" }>; inCell: boolean }) {
  const look = pick(STYLES, block.style) ?? BODY;
  const Tag = look.tag;
  const runs = Array.isArray(block.runs) ? block.runs : [];
  const content = runs.length ? runs.map((run, i) => <Run key={i} run={run} />) : " ";
  const gap = inCell ? undefined : look.tag === "p" ? BLOCK_GAP : HEADING_GAP;
  const className = cn(gap, look.className, pick(ALIGN, block.align)) || undefined;
  if (block.list && typeof block.list === "object") {
    const level = Math.min(Math.max(Number(block.list.level) || 0, 0), 8);
    const marker = typeof block.list.marker === "string" ? block.list.marker : "";
    // 每一级缩进同对话里 Markdown 的列表缩进
    return (
      <Tag className={cn(className, "flex gap-(--cc-space-8)")} style={{ paddingInlineStart: `calc(var(--cc-md-list-indent) * ${level})` }}>
        {marker && <span className="shrink-0 tabular-nums">{marker}</span>}
        <span className="min-w-0 flex-1">{content}</span>
      </Tag>
    );
  }
  return <Tag className={className}>{content}</Tag>;
}

function Cell({ cell, header }: { cell: DocCell; header: boolean }) {
  return (
    <td
      colSpan={count(cell.colspan, 64)}
      rowSpan={count(cell.rowspan, 1000)}
      className={cn("border border-line align-top", header && "bg-subtle font-medium")}
    >
      <Blocks blocks={cell.blocks} inCell />
    </td>
  );
}

function Table({ block }: { block: Extract<DocBlock, { type: "table" }> }) {
  const rows = Array.isArray(block.rows) ? block.rows : [];
  return (
    <div className={cn(BLOCK_GAP, "max-w-full overflow-x-auto")}>
      <table className="cc-table border-collapse text-(length:--cc-text-sm)" data-kind="doc">
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {(Array.isArray(row.cells) ? row.cells : []).map((cell, c) => (
                <Cell key={c} cell={cell} header={row.header === true} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Blocks({ blocks, inCell = false }: { blocks: readonly DocBlock[]; inCell?: boolean }) {
  return (
    <>
      {(Array.isArray(blocks) ? blocks : []).map((block, i) =>
        block.type === "table" ? (
          <Table key={i} block={block} />
        ) : block.type === "paragraph" ? (
          <Paragraph key={i} block={block} inCell={inCell} />
        ) : null,
      )}
    </>
  );
}

export default function DocumentView({ blocks }: { blocks: readonly DocBlock[] }) {
  return (
    <div className="h-full overflow-x-hidden overflow-y-auto">
      <article className="mx-auto max-w-(--cc-doc-w) px-(--cc-space-24) py-(--cc-space-24) text-(length:--cc-body-size) leading-(--cc-body-lh) break-words whitespace-pre-wrap [tab-size:4] max-md:px-(--cc-space-16)">
        <Blocks blocks={blocks} />
      </article>
    </div>
  );
}
