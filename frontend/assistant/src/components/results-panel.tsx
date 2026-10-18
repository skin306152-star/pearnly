// 右侧结果面板（同 claude.ai 的 Artifacts 面板）：智能体产出的文件在这里预览、下载。
//
// 预览方式沿用 assistant-ui 官方 Artifacts 示例：网页放进无同源权限的沙箱 iframe（srcDoc），并可切到代码；
// 图片、PDF 用服务端的内联地址（PDF 在不能内嵌显示的设备上——安卓 Chrome、iPhone / iPad——改由服务端逐页画成图，
// 第 5 轮测试 2）；Excel 由服务端读成表格后显示；Word（.docx）由服务端读成段落与表格的纯数据，
// 按需载入的 document-view 按文字渲染（2026-10-13 教程第 3 条）；文本按原文显示；其余类型、读不出时给下载。
// 新结果出现时自动打开；对话里的文件卡片点一下打开对应文件（ResultsContext）。
// 用户发的附件（消息气泡里的附件卡片）也在这里打开：服务端取本站留存的那份（引擎不让下载用户上传的文件），预览方式同结果文件。

import { Tabs } from "radix-ui";
import {
  DownloadIcon,
  FileIcon,
  FileImageIcon,
  FileSpreadsheetIcon,
  FileTextIcon,
  GlobeIcon,
  LoaderIcon,
  XIcon,
} from "lucide-react";
import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useRef, useState } from "react";
import { fileUrl, listSessionFiles, pageUrl, previewFile, type FilePreview, type SessionFile } from "@/lib/managed-agents/api";
import { watchOverflow } from "@/lib/overflow-fade";
import { touchOnly } from "@/lib/touch";
import { cn } from "@/lib/utils";
import { ErrorBoundary } from "@/components/error-boundary";
import { SplitHandle, resultsSize, useStoredSize } from "@/components/split-handle";
import { t } from "@/i18n";
import { Marquee } from "@/components/ui/marquee";

// Word 预览的渲染按需载入：打开 .docx 时才下载这一小块，首屏包不变
const DocumentView = lazy(() => import("@/components/document-view"));

type Kind = "html" | "image" | "pdf" | "sheet" | "doc" | "text" | "none";

export function kindOf(filename: string): Kind {
  const name = filename.toLowerCase();
  if (/\.html?$/.test(name)) return "html";
  if (/\.(png|jpe?g|gif|webp)$/.test(name)) return "image";
  if (name.endsWith(".pdf")) return "pdf";
  if (/\.xlsx?m?$/.test(name)) return "sheet";
  if (name.endsWith(".docx")) return "doc";
  if (/\.(csv|tsv|txt|md|json|log)$/.test(name)) return "text";
  return "none";
}

/** 文件类型图标（对话里的文件卡片与面板共用）。 */
export function FileTypeIcon({ filename, className }: { filename: string; className?: string }) {
  const kind = kindOf(filename);
  const Icon =
    kind === "sheet" || /\.(csv|tsv)$/i.test(filename)
      ? FileSpreadsheetIcon
      : kind === "image"
        ? FileImageIcon
        : kind === "html"
          ? GlobeIcon
          : kind === "none"
            ? FileIcon
            : FileTextIcon;
  return <Icon className={className} />;
}

/** 扩展名大写，作文件卡片的类型说明（XLSX、PDF…）。 */
export function fileTypeLabel(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot > 0 ? filename.slice(dot + 1).toUpperCase() : "";
}

export type Results = {
  sessionId: string | null;
  files: SessionFile[];
  /** 用户发的附件（本会话文件列表里 kind = upload 的，带大小与 PDF 页数）：气泡里的附件卡片显示说明行 */
  uploads: SessionFile[];
  openFile: (id: string) => void;
  /** 在右侧面板打开用户发的附件：它不在结果文件里，面板临时多列这一项 */
  openUpload: (file: SessionFile) => void;
};

export const ResultsContext = createContext<Results>({
  sessionId: null,
  files: [],
  uploads: [],
  openFile: () => undefined,
  openUpload: () => undefined,
});

export function useResults(): Results {
  return useContext(ResultsContext);
}

/** 本会话的结果文件与用户发的附件；会话从「运行中」变回空闲时重新取一次（结果文件有 1–3 秒的入库延迟，取不到再补一次）。 */
export function useResultFiles(sessionId: string | null, isRunning: boolean) {
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [uploads, setUploads] = useState<SessionFile[]>([]);
  const [fresh, setFresh] = useState(false);
  const wasRunning = useRef(false);
  const known = useRef<Set<string>>(new Set());

  useEffect(() => {
    known.current = new Set();
    setFiles([]);
    setUploads([]);
    setFresh(false);
    if (!sessionId) return;
    let cancelled = false;
    void listSessionFiles(sessionId).then((list) => {
      if (cancelled) return;
      const outputs = list.filter((f) => f.kind === "output" && f.downloadable);
      outputs.forEach((f) => known.current.add(f.id));
      setFiles(outputs);
      setUploads(list.filter((f) => f.kind === "upload"));
    }, () => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    const finished = wasRunning.current && !isRunning;
    wasRunning.current = isRunning;
    if (!finished || !sessionId) return;
    let cancelled = false;
    const load = (retry: number) =>
      listSessionFiles(sessionId).then((list) => {
        if (cancelled) return;
        const outputs = list.filter((f) => f.kind === "output" && f.downloadable);
        const added = outputs.filter((f) => !known.current.has(f.id));
        outputs.forEach((f) => known.current.add(f.id));
        setFiles(outputs);
        setUploads(list.filter((f) => f.kind === "upload"));
        if (added.length) setFresh(true);
        else if (retry > 0) setTimeout(() => void load(retry - 1), 2500);
      }, () => undefined);
    void load(1);
    return () => {
      cancelled = true;
    };
  }, [isRunning, sessionId]);

  const clearFresh = useCallback(() => setFresh(false), []);
  return { files, uploads, fresh, clearFresh };
}

function useText(url: string | null) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    setText(null);
    if (!url) return;
    let cancelled = false;
    void fetch(url, { credentials: "same-origin" })
      .then((res) => (res.ok ? res.text() : ""))
      .then((body) => !cancelled && setText(body));
    return () => {
      cancelled = true;
    };
  }, [url]);
  return text;
}

/** 服务端的结构化预览（Excel、Word）；请求失败（网络、读不出）当作没有预览，页面给下载。 */
export function loadPreview(sessionId: string, fileId: string): Promise<FilePreview> {
  return previewFile(sessionId, fileId).then(
    (body) => body ?? { type: "none" },
    () => ({ type: "none" }),
  );
}

// 结果按文件记：在面板里换一个文件时，不会先拿上一个文件的预览渲染一下
function usePreview(sessionId: string, fileId: string | null) {
  const [loaded, setLoaded] = useState<{ key: string; preview: FilePreview } | null>(null);
  const key = fileId ? `${sessionId}/${fileId}` : "";
  useEffect(() => {
    if (!fileId) return;
    let cancelled = false;
    void loadPreview(sessionId, fileId).then((preview) => !cancelled && setLoaded({ key: `${sessionId}/${fileId}`, preview }));
    return () => {
      cancelled = true;
    };
  }, [sessionId, fileId]);
  return loaded && key && loaded.key === key ? loaded.preview : null;
}

function Loading() {
  return (
    <div className="flex h-full items-center justify-center">
      <LoaderIcon className="cc-spinner size-(--cc-icon) text-muted-foreground" />
    </div>
  );
}

// 数额靠右；收据号、税号这类以 0 开头或很长的数字编号仍按文字靠左。
const isAmount = (cell: string) => cell.length < 13 && /^-?(0|[1-9][\d,]*)(\.\d+)?$/.test(cell);

function SheetTable({ rows }: { rows: string[][] }) {
  // 格子放不下时末尾渐隐（同所有单行截断的文字，用户 10-16「统一渐隐」）：最多 500 × 40 格，不给每格各挂一个，整张表一处登记，
  // 表的尺寸变了（换表、字体载入）就把所有格子重量一遍
  const table = useRef<HTMLTableElement>(null);
  useEffect(() => {
    const el = table.current;
    return el ? watchOverflow([el], () => el.querySelectorAll<HTMLElement>("td.cc-fade-cell")) : undefined;
  }, [rows]);
  return (
    <div className="h-full overflow-auto">
      <table ref={table} className="cc-table border-collapse text-(length:--cc-text-sm)" data-kind="sheet">
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className={r === 0 ? "bg-subtle font-medium" : undefined}>
              {row.map((cell, c) => (
                <td
                  key={c}
                  className={cn(
                    "cc-fade-cell border border-line",
                    isAmount(cell) && "text-right tabular-nums",
                  )}
                  title={cell}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NoPreview({ sessionId, file }: { sessionId: string; file: SessionFile }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-(--cc-space-12) text-muted-foreground text-body">
      <FileTypeIcon filename={file.filename} className="size-(--cc-icon-2xl)" />
      <Marquee className="max-w-4/5 truncate text-foreground">{file.filename}</Marquee>
      <a
        href={fileUrl(sessionId, file.id)}
        className="flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-foreground hover:bg-muted"
      >
        <DownloadIcon className="size-4" />
        {t("download")}
      </a>
    </div>
  );
}

/** Word 预览：取预览中转圈；读不出、没有正文，或渲染那一块载不进来（例如站点刚更新、旧页面要的文件已换名）都给下载。 */
export function DocumentPreview({ sessionId, file, preview }: { sessionId: string; file: SessionFile; preview: FilePreview | null }) {
  if (!preview) return <Loading />;
  const fallback = <NoPreview sessionId={sessionId} file={file} />;
  if (preview.type !== "document" || !preview.blocks?.length) return fallback;
  return (
    <ErrorBoundary key={file.id} fallback={fallback}>
      <Suspense fallback={<Loading />}>
        <DocumentView blocks={preview.blocks} />
      </Suspense>
    </ErrorBoundary>
  );
}

/** 这台设备能不能在页面里直接显示 PDF（浏览器自带的查看器）：电脑上的 Chrome / Edge / Firefox / Safari 能；安卓 Chrome
 *  不能（iframe 里只有图标、内部地址和「打开」按钮，第 5 轮测试 2），iPhone / iPad 的 Safari 在 iframe 里只显示第一页、
 *  不能滚——触屏设备一律按页看图。 */
export function nativePdf(): boolean {
  return typeof navigator !== "undefined" && navigator.pdfViewerEnabled !== false && !touchOnly();
}

// 手机上一份 PDF 最多列这么多页，再多的下载看
export const PDF_PAGES_SHOWN = 100;

/** 不能内嵌 PDF 的设备：服务端逐页画成图（files.page_image），一页一张往下排、可双指放大。看得见才取（loading="lazy"，
 *  取到前按 A4 比例占位，免得一打开就把每页都取了）；同一页浏览器有缓存。页数先看列表里的（附件），没有再问服务端；
 *  读不出页数、第一页画不出给下载。 */
export function PdfPages({ sessionId, file }: { sessionId: string; file: SessionFile }) {
  const [pages, setPages] = useState<number | null>(file.pages ?? null);
  const [broken, setBroken] = useState(false);
  useEffect(() => {
    setBroken(false);
    if (file.pages) {
      setPages(file.pages);
      return;
    }
    setPages(null);
    let cancelled = false;
    void loadPreview(sessionId, file.id).then((preview) => !cancelled && setPages(preview.type === "pdf" ? preview.pages : 0));
    return () => {
      cancelled = true;
    };
  }, [sessionId, file.id, file.pages]);
  if (pages === null) return <Loading />;
  if (!pages || broken) return <NoPreview sessionId={sessionId} file={file} />;
  const shown = Math.min(pages, PDF_PAGES_SHOWN);
  return (
    <div className="cc-pdf-pages" data-slot="cc-pdf-pages">
      {Array.from({ length: shown }, (_, i) => (
        <img
          key={i}
          className="cc-pdf-page"
          src={pageUrl(sessionId, file.id, i + 1, "page")}
          alt={`${file.filename} · ${i + 1}/${pages}`}
          loading="lazy"
          decoding="async"
          draggable={false}
          onError={i === 0 ? () => setBroken(true) : undefined}
        />
      ))}
      {pages > shown && (
        <a href={fileUrl(sessionId, file.id)} className="cc-btn self-center">
          <DownloadIcon />
          {t("download")}
        </a>
      )}
    </div>
  );
}

function Preview({ sessionId, file }: { sessionId: string; file: SessionFile }) {
  const kind = kindOf(file.filename);
  const preview = usePreview(sessionId, kind === "sheet" || kind === "doc" ? file.id : null);
  const text = useText(kind === "html" || kind === "text" ? fileUrl(sessionId, file.id) : null);

  if (kind === "image") {
    return <img src={fileUrl(sessionId, file.id, true)} alt={file.filename} className="mx-auto max-h-full max-w-full object-contain" />;
  }
  if (kind === "pdf") {
    if (!nativePdf()) return <PdfPages sessionId={sessionId} file={file} />;
    return <iframe title={file.filename} src={fileUrl(sessionId, file.id, true)} className="h-full w-full" />;
  }
  if (kind === "text") {
    return <pre className="h-full overflow-auto p-(--cc-space-16) font-mono text-(length:--cc-text-sm) whitespace-pre-wrap">{text ?? ""}</pre>;
  }
  if (kind === "doc") return <DocumentPreview sessionId={sessionId} file={file} preview={preview} />;
  if (kind === "sheet") {
    if (!preview) return <Loading />;
    if (preview.type !== "sheets" || !preview.sheets.length) return <NoPreview sessionId={sessionId} file={file} />;
    const sheets = preview;
    if (sheets.sheets.length === 1) return <SheetTable rows={sheets.sheets[0]!.rows} />;
    // 先打开第一张有数据的表（只有表头的跳过）：复核工作簿进项单据的数据在后面的表里
    const first = Math.max(0, sheets.sheets.findIndex((sheet) => sheet.rows.length > 1));
    return (
      <Tabs.Root defaultValue={String(first)} className="flex h-full flex-col">
        {sheets.sheets.map((sheet, i) => (
          <Tabs.Content key={i} value={String(i)} className="min-h-0 flex-1">
            <SheetTable rows={sheet.rows} />
          </Tabs.Content>
        ))}
        <Tabs.List className="flex gap-1 overflow-x-auto border-t px-2 text-sm">
          {sheets.sheets.map((sheet, i) => (
            <Tabs.Trigger
              key={i}
              value={String(i)}
              className="-mt-px border-transparent border-t-2 px-2 py-1.5 text-muted-foreground data-[state=active]:border-foreground data-[state=active]:text-foreground"
            >
              {sheet.name}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </Tabs.Root>
    );
  }
  if (kind === "html") {
    return (
      <Tabs.Root defaultValue="preview" className="flex h-full flex-col">
        <Tabs.List className="flex gap-4 border-b px-4 text-sm">
          {(["preview", "source"] as const).map((tab) => (
            <Tabs.Trigger
              key={tab}
              value={tab}
              className="-mb-px border-transparent border-b-2 py-2 text-muted-foreground data-[state=active]:border-foreground data-[state=active]:text-foreground"
            >
              {t(tab)}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="preview" className="min-h-0 flex-1">
          {/* 无 allow-same-origin：页面里的脚本拿不到本站会话与接口 */}
          <iframe title={file.filename} sandbox="allow-scripts allow-forms" srcDoc={text ?? ""} className="h-full w-full bg-surface" />
        </Tabs.Content>
        <Tabs.Content value="source" className="min-h-0 flex-1 overflow-auto">
          <pre className="p-(--cc-space-16) font-mono text-(length:--cc-text-sm) whitespace-pre-wrap">{text ?? ""}</pre>
        </Tabs.Content>
      </Tabs.Root>
    );
  }
  return <NoPreview sessionId={sessionId} file={file} />;
}

export function ResultsPanel({
  sessionId,
  files,
  selected,
  onSelect,
  onClose,
  className,
}: {
  sessionId: string;
  files: SessionFile[];
  selected: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
  className?: string;
}) {
  const current = files.find((f) => f.id === selected) ?? files[files.length - 1];
  const width = useStoredSize(resultsSize.key, resultsSize.cssVar, resultsSize.unit);
  if (!current) return null;
  return (
    <aside className={cn("cc-results flex h-full min-w-0 flex-col border-l bg-surface", className)} style={width.style}>
      <SplitHandle size={resultsSize} edge="start" onChange={width.update} />
      <header className="flex h-(--cc-results-head-h) shrink-0 items-center gap-(--cc-space-8) border-b px-(--cc-space-12)">
        <select
          value={current.id}
          onChange={(e) => onSelect(e.target.value)}
          className="min-w-0 flex-1 truncate bg-transparent font-medium text-body outline-none"
        >
          {files.map((f) => (
            <option key={f.id} value={f.id}>
              {f.filename}
            </option>
          ))}
        </select>
        <a
          href={fileUrl(sessionId, current.id)}
          title={t("download")}
          aria-label={t("download")}
          className="flex size-8 items-center justify-center rounded-md text-ink-2 hover:bg-muted"
        >
          <DownloadIcon className="size-4" />
        </a>
        <button
          type="button"
          onClick={onClose}
          title={t("close")}
          aria-label={t("close")}
          className="flex size-8 items-center justify-center rounded-md text-ink-2 hover:bg-muted"
        >
          <XIcon className="size-4" />
        </button>
      </header>
      <div className="min-h-0 flex-1">
        <Preview sessionId={sessionId} file={current} />
      </div>
    </aside>
  );
}
