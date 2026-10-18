
// 附件（同 Claude）。
// - 输入框里：方块（照 Claude 桌面版：128 见方，手机 72；图片整张放进方块，其他文件是文件名 + 类型），右上角圆形移除，上传中转圈，
//   上传失败标红并在提示里说原因；图片点开看大图（右上角圆形关闭）。
// - 你发出的消息里：每个文件一张卡片（缩略图、文件名一行、类型说明「PDF · N 页」/ 大小），点开在右侧面板预览
//   （与 CC 产出的文件同一个预览；服务端取本站留存的那份，PDF 也能看）。读屏名字跟页面语言。
//   缩略图同 Claude：图片是图片本身，PDF 是第一页（服务端画的小图，page_image thumb，第 5 轮测试 1），载不出退回文件图标。
import { type PropsWithChildren, useEffect, useState, type FC } from "react";
import {
  XIcon,
  PlusIcon,
  Loader2Icon,
  AlertCircleIcon,
} from "lucide-react";
import {
  AttachmentPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { useShallow } from "zustand/shallow";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Dialog,
  DialogClose,
  DialogTitle,
  DialogContent,
  DialogTrigger,
} from "@/components/ui/dialog";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { FileTypeIcon, fileTypeLabel, kindOf, useResults, type Results } from "@/components/results-panel";
import { fileUrl, pageUrl, type SessionFile } from "@/lib/managed-agents/api";
import { localMeta, localPreview, uploadFileId } from "@/lib/managed-agents/attachments";
import { cn } from "@/lib/utils";
import { t } from "@/i18n";
import { Marquee } from "@/components/ui/marquee";
import { Skeleton } from "@/components/ui/skeleton";

const useFileSrc = (file: File | undefined) => {
  const [src, setSrc] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!file) {
      setSrc(undefined);
      return;
    }

    const objectUrl = URL.createObjectURL(file);
    setSrc(objectUrl);

    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [file]);

  return src;
};

const useAttachmentSrc = () => {
  const { file, src } = useAuiState(
    useShallow((s): { file?: File; src?: string } => {
      if (s.attachment.type !== "image") return {};
      if (s.attachment.file) return { file: s.attachment.file };
      // 发送没成功放回输入框的图片（lib/managed-agents/send-failure.ts）：没有原文件了，按它在会话里的位置取本页留的预览
      const placed = s.attachment.content?.find((c) => c.type === "file")?.data;
      const src =
        s.attachment.content?.filter((c) => c.type === "image")[0]?.image ??
        localPreview(s.attachment.id) ??
        (placed ? localPreview(placed) : undefined);
      if (!src) return {};
      return { src };
    }),
  );

  return useFileSrc(file) ?? src;
};

type AttachmentPreviewProps = {
  src: string;
};

const AttachmentPreview: FC<AttachmentPreviewProps> = ({ src }) => {
  const [isLoaded, setIsLoaded] = useState(false);
  return (
    <img
      src={src}
      alt=""
      className={cn(
        "block h-auto max-h-[calc(var(--cc-viewport-h)*var(--cc-preview-share))] w-auto max-w-full object-contain",
        isLoaded
          ? "aui-attachment-preview-image-loaded"
          : "aui-attachment-preview-image-loading invisible",
      )}
      onLoad={() => setIsLoaded(true)}
    />
  );
};

const AttachmentPreviewDialog: FC<PropsWithChildren> = ({ children }) => {
  const src = useAttachmentSrc();

  if (!src) return children;

  return (
    <Dialog>
      <DialogTrigger
        className="aui-attachment-preview-trigger hover:bg-accent/50 cursor-pointer transition-colors"
        asChild
      >
        {children}
      </DialogTrigger>
      <DialogContent showCloseButton={false} className="aui-attachment-preview-dialog-content p-(--cc-space-8) sm:max-w-(--cc-preview-w)">
        <DialogTitle className="aui-sr-only sr-only">{t("imagePreview")}</DialogTitle>
        <div className="aui-attachment-preview bg-app relative mx-auto flex max-h-[calc(var(--cc-viewport-h)*var(--cc-preview-share))] w-full items-center justify-center overflow-hidden">
          <AttachmentPreview src={src} />
        </div>
        {/* 关闭：浮在图片右上角的圆钮（电脑 32、手机 44） */}
        <DialogClose className="cc-icon-btn cc-tap cc-preview-close" aria-label={t("close")} title={t("close")}>
          <XIcon />
        </DialogClose>
      </DialogContent>
    </Dialog>
  );
};

/** 方块里面：图片整张放进方块（不裁，同 Claude）；其他文件（或图片载不出）左上文件名、左下类型图标 + 类型。 */
const AttachmentThumb: FC<{ name: string }> = ({ name }) => {
  const src = useAttachmentSrc();
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  if (src && !failed) {
    return <img src={src} alt="" draggable={false} className="aui-attachment-tile-image" onError={() => setFailed(true)} />;
  }
  return (
    <span className="cc-compose-file aui-attachment-tile-fallback">
      <span className="cc-compose-name">{name}</span>
      <span className="cc-compose-type">
        <FileTypeIcon filename={name} />
        {fileTypeLabel(name)}
      </span>
    </span>
  );
};

/** 输入框里的一个附件：方块 + 移除。 */
const ComposerAttachment: FC = () => {
  const name = useAuiState((s) => s.attachment.name);

  const uploadState = useAuiState((s) =>
    s.attachment.status.type === "running"
      ? "uploading"
      : s.attachment.status.type === "incomplete" &&
          s.attachment.status.reason === "error"
        ? "error"
        : undefined,
  );
  const isUploading = uploadState === "uploading";
  const isError = uploadState === "error";

  const errorMessage = useAuiState((s) =>
    s.attachment.status.type === "incomplete" &&
    s.attachment.status.reason === "error"
      ? (s.attachment.status.message ?? t("uploadFailed"))
      : undefined,
  );

  return (
    <Tooltip>
      <AttachmentPrimitive.Root className="aui-attachment-root relative">
        <AttachmentPreviewDialog>
          <TooltipTrigger asChild>
            <div
              className="aui-attachment-tile cc-compose-tile"
              data-error={isError || undefined}
              role="button"
              tabIndex={0}
              aria-label={[t("attachment").replace("{0}", name), isError ? t("uploadFailed") : isUploading ? t("uploading") : ""]
                .filter(Boolean)
                .join(" · ")}
            >
              <AttachmentThumb name={name} />
              {isUploading && (
                <div
                  aria-hidden="true"
                  className="aui-attachment-tile-uploading bg-app/60 absolute inset-0 flex items-center justify-center backdrop-blur-[1px]"
                >
                  <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
                </div>
              )}
              {isError && (
                <div
                  aria-hidden="true"
                  className="aui-attachment-tile-error bg-danger-soft absolute inset-0 flex items-center justify-center"
                >
                  <AlertCircleIcon className="text-destructive size-(--cc-icon)" />
                </div>
              )}
            </div>
          </TooltipTrigger>
        </AttachmentPreviewDialog>
        <AttachmentRemove />
      </AttachmentPrimitive.Root>
      <TooltipContent side="top">
        <AttachmentPrimitive.Name />
        {errorMessage && (
          <p className="aui-attachment-error-message">{errorMessage}</p>
        )}
      </TooltipContent>
    </Tooltip>
  );
};

/** 方块右上角 20 的圆形移除：白底、轻投影、× 悬停变红（V71：不再套 shadcn Button，只过渡颜色，按下同图标按钮 94%）。 */
const AttachmentRemove: FC = () => {
  return (
    <AttachmentPrimitive.Remove asChild>
      <TooltipIconButton
        tooltip={t("removeFile")}
        className="aui-attachment-tile-remove cc-compose-x absolute inline-flex size-(--cc-control-2xs) items-center justify-center rounded-full bg-surface shadow-sm outline-none transition-colors focus-visible:shadow-focus active:scale-(--cc-press-scale) [&_svg]:text-ink hover:[&_svg]:text-danger"
        side="top"
      >
        <XIcon className="aui-attachment-remove-icon size-(--cc-icon-2xs) dark:stroke-(length:--cc-compose-x-stroke)" />
      </TooltipIconButton>
    </AttachmentPrimitive.Remove>
  );
};

/** 文件大小（同 Claude 的写法：B / KB / MB，不随语言变）。 */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** 卡片的类型说明：PDF 带页数（「PDF · 3 页」），其余带大小（「XLSX · 12 KB」）；页数、大小不知道时只写类型。 */
export function attachmentTypeLine(filename: string, size?: number, pages?: number): string {
  const type = fileTypeLabel(filename);
  if (kindOf(filename) === "pdf") {
    if (!pages) return type;
    return `${type} · ${pages === 1 ? t("onePage") : t("pageCount").replace("{0}", String(pages))}`;
  }
  return size ? [type, formatSize(size)].filter(Boolean).join(" · ") : type;
}

/** 你发出的消息里的一个附件该怎么显示（纯计算，测试直接调）：读屏名字、类型说明、缩略图地址，以及点开时交给右侧面板的文件
 *  （没有会话、沙箱位置里认不出文件编号时没有，卡片不可点）。附件的编号是它在沙箱里的位置（events.ts 的 splitAttachmentNote）。
 *  大小、页数先看本会话文件列表（服务端按留存的那份补的），刚发出、列表还没重取时用本页上传时记下的。 */
export function attachmentCard(
  mountPath: string,
  name: string,
  results: Pick<Results, "sessionId" | "uploads">,
): { label: string; typeLine: string; image?: string; page?: boolean; open?: SessionFile } {
  const { sessionId, uploads } = results;
  const fileId = uploadFileId(mountPath);
  const listed = fileId ? uploads.find((f) => f.id === fileId) : undefined;
  const local = localMeta(mountPath);
  const size = listed?.size_bytes || local?.size;
  const pages = listed?.pages ?? local?.pages;
  const typeLine = attachmentTypeLine(name, size, pages);
  // 图片：本页刚发的用本地预览；刷新后、别的设备上用服务端留存的那份（内联地址）。PDF：服务端画的第一页（page = 是页面图，
  // 卡片里从页顶显示）。只在本站确有留存时取（本页刚发的，或列表带了大小 = 服务端读到了那份）：很早以前没留存的附件
  // 引擎也不给下载，取缩略图只会白问一趟引擎再 404（第 5 轮审查），直接是类型方块
  const kind = kindOf(name);
  const remote = sessionId && fileId ? { sessionId, fileId } : null;
  const page = kind === "pdf" && !!remote && !!(listed?.size_bytes || local);
  const image =
    kind === "image"
      ? (localPreview(mountPath) ?? (remote ? fileUrl(remote.sessionId, remote.fileId, true) : undefined))
      : page
        ? pageUrl(remote!.sessionId, remote!.fileId, 1, "thumb")
        : undefined;
  const label = t("attachment").replace("{0}", [name, typeLine].filter(Boolean).join(" · "));
  const open: SessionFile | undefined =
    sessionId && fileId
      ? {
          id: fileId,
          filename: name,
          size_bytes: size ?? 0,
          mime_type: listed?.mime_type ?? "",
          downloadable: false,
          created_at: listed?.created_at ?? "",
          kind: "upload",
          ...(pages ? { pages } : {}),
        }
      : undefined;
  return { label, typeLine, ...(image ? { image } : {}), ...(page ? { page } : {}), ...(open ? { open } : {}) };
}

/** 你发出的消息里的一个附件：卡片（缩略图、文件名一行、类型说明），点开在右侧面板预览。 */
const MessageAttachment: FC = () => {
  const name = useAuiState((s) => s.attachment.name);
  const mountPath = useAuiState((s) => s.attachment.id);
  const results = useResults();
  const card = attachmentCard(mountPath, name, results);
  const open = card.open;
  return <AttachmentCard name={name} {...card} onOpen={open ? () => results.openUpload(open) : undefined} />;
};

/** 附件卡片本身（不读状态）：能打开时是按钮，否则只是一张卡片；loading（「发送中」临时气泡里）缩略图位置是骨架。 */
export const AttachmentCard: FC<{ name: string; label: string; typeLine: string; image?: string; page?: boolean; loading?: boolean; onOpen?: () => void }> = ({
  name,
  label,
  typeLine,
  image,
  page,
  loading,
  onOpen,
}) => {
  const body = (
    <>
      <span className="cc-attach-thumb" aria-hidden data-page={page || undefined}>
        {loading ? <Skeleton className="size-full rounded-none" /> : <AttachmentThumbnail src={image} filename={name} />}
      </span>
      <span className="cc-attach-meta" aria-hidden>
        <Marquee className="cc-attach-name">{name}</Marquee>
        {typeLine && <Marquee className="cc-attach-type">{typeLine}</Marquee>}
      </span>
    </>
  );
  if (!onOpen) {
    return (
      <div className="cc-attach-card" role="img" aria-label={label} title={name}>
        {body}
      </div>
    );
  }
  return (
    <button type="button" className="cc-attach-card" aria-label={label} title={name} onClick={onOpen}>
      {body}
    </button>
  );
};

/** 卡片上半部分：图片显示图片、PDF 显示第一页（载不出就退回文件图标）；其余类型是文件图标 + 类型。 */
const AttachmentThumbnail: FC<{ src?: string; filename: string }> = ({ src, filename }) => {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  if (src && !failed) {
    return <img src={src} alt="" loading="lazy" decoding="async" draggable={false} onError={() => setFailed(true)} />;
  }
  return (
    <span className="cc-attach-tile">
      <FileTypeIcon filename={filename} />
      <span>{fileTypeLabel(filename)}</span>
    </span>
  );
};

export const UserMessageAttachments: FC = () => {
  return (
    <div className="aui-user-message-attachments-end cc-attach-list col-span-full col-start-1 row-start-1">
      <MessagePrimitive.Attachments>
        {() => <MessageAttachment />}
      </MessagePrimitive.Attachments>
    </div>
  );
};

/** 「发送中」临时气泡里的附件（thread.tsx 的 PendingSendBubble）：与发出后的那一排同一个位置、同一种卡片，缩略图位置是骨架。 */
export const PendingAttachments: FC<{ names: readonly string[] }> = ({ names }) => {
  return (
    <div className="aui-user-message-attachments-end cc-attach-list col-span-full col-start-1 row-start-1">
      {names.map((name, i) => (
        <AttachmentCard key={i} name={name} label={t("attachment").replace("{0}", name)} typeLine={attachmentTypeLine(name)} loading />
      ))}
    </div>
  );
};

export const ComposerAttachments: FC = () => {
  return (
    <div className="aui-composer-attachments flex w-full flex-row items-center gap-(--cc-space-8) overflow-x-auto empty:hidden">
      <ComposerPrimitive.Attachments>
        {() => <ComposerAttachment />}
      </ComposerPrimitive.Attachments>
    </div>
  );
};

export const ComposerAddAttachment: FC = () => {
  return (
    <ComposerPrimitive.AddAttachment asChild>
      <button type="button" className="aui-composer-add-attachment cc-attach" aria-label={t("attach")} title={t("attach")}>
        <PlusIcon className="aui-attachment-add-icon stroke-(length:--cc-icon-stroke)" />
      </button>
    </ComposerPrimitive.AddAttachment>
  );
};
