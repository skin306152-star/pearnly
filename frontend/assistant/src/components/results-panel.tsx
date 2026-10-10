// 右侧结果面板（同 claude.ai 的 Artifacts 面板）：智能体写到 /mnt/session/outputs/ 的文件在这里预览、下载。
//
// 预览方式沿用 assistant-ui 官方 Artifacts 示例：网页放进无同源权限的沙箱 iframe（srcDoc），并可切到代码；
// 图片、PDF 用服务端的内联地址；表格与文本按原文显示；其余类型只给下载。
// 新结果出现时自动打开；回看历史时不自动打开，标题栏的按钮可随时打开。

import { Tabs } from "radix-ui";
import { DownloadIcon, FileIcon, XIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { fileUrl, listSessionFiles, type SessionFile } from "@/lib/managed-agents/api";
import { cn } from "@/lib/utils";
import { t } from "@/i18n";

type Kind = "html" | "image" | "pdf" | "text" | "none";

function kindOf(file: SessionFile): Kind {
  const name = file.filename.toLowerCase();
  if (/\.html?$/.test(name)) return "html";
  if (/\.(png|jpe?g|gif|webp)$/.test(name)) return "image";
  if (name.endsWith(".pdf")) return "pdf";
  if (/\.(csv|tsv|txt|md|json|log)$/.test(name)) return "text";
  return "none";
}

/** 本会话的结果文件；会话从「运行中」变回空闲时重新取一次（结果文件有 1–3 秒的入库延迟，取不到再补一次）。 */
export function useResultFiles(sessionId: string | null, isRunning: boolean) {
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [fresh, setFresh] = useState(false);
  const wasRunning = useRef(false);
  const known = useRef<Set<string>>(new Set());

  useEffect(() => {
    known.current = new Set();
    setFiles([]);
    setFresh(false);
    if (!sessionId) return;
    let cancelled = false;
    void listSessionFiles(sessionId).then((list) => {
      if (cancelled) return;
      const outputs = list.filter((f) => f.kind === "output" && f.downloadable);
      outputs.forEach((f) => known.current.add(f.id));
      setFiles(outputs);
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
        if (added.length) setFresh(true);
        else if (retry > 0) setTimeout(() => void load(retry - 1), 2500);
      }, () => undefined);
    void load(1);
    return () => {
      cancelled = true;
    };
  }, [isRunning, sessionId]);

  const clearFresh = useCallback(() => setFresh(false), []);
  return { files, fresh, clearFresh };
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

function Preview({ sessionId, file }: { sessionId: string; file: SessionFile }) {
  const kind = kindOf(file);
  const text = useText(kind === "html" || kind === "text" ? fileUrl(sessionId, file.id) : null);

  if (kind === "image") {
    return <img src={fileUrl(sessionId, file.id, true)} alt={file.filename} className="mx-auto max-h-full max-w-full object-contain" />;
  }
  if (kind === "pdf") {
    return <iframe title={file.filename} src={fileUrl(sessionId, file.id, true)} className="h-full w-full" />;
  }
  if (kind === "text") {
    return <pre className="h-full overflow-auto p-4 font-mono text-[13px] whitespace-pre-wrap">{text ?? ""}</pre>;
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
          <iframe title={file.filename} sandbox="allow-scripts allow-forms" srcDoc={text ?? ""} className="h-full w-full bg-white" />
        </Tabs.Content>
        <Tabs.Content value="source" className="min-h-0 flex-1 overflow-auto">
          <pre className="p-4 font-mono text-[13px] whitespace-pre-wrap">{text ?? ""}</pre>
        </Tabs.Content>
      </Tabs.Root>
    );
  }
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground text-sm">
      <FileIcon className="size-8" />
      {t("noPreview")}
    </div>
  );
}

export function ResultsPanel({
  sessionId,
  files,
  onClose,
  className,
}: {
  sessionId: string;
  files: SessionFile[];
  onClose: () => void;
  className?: string;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const current = files.find((f) => f.id === selected) ?? files[files.length - 1];
  if (!current) return null;
  return (
    <aside className={cn("flex h-full min-w-0 flex-col border-l bg-white", className)}>
      <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
        <select
          value={current.id}
          onChange={(e) => setSelected(e.target.value)}
          className="min-w-0 flex-1 truncate bg-transparent font-medium text-sm outline-none"
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
          className="flex size-8 items-center justify-center rounded-md text-[#5b5950] hover:bg-muted"
        >
          <DownloadIcon className="size-4" />
        </a>
        <button
          type="button"
          onClick={onClose}
          title={t("close")}
          aria-label={t("close")}
          className="flex size-8 items-center justify-center rounded-md text-[#5b5950] hover:bg-muted"
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
