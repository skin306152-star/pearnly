
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
  RotateCcwIcon,
} from "lucide-react";
import {
  AttachmentPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  useAui,
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
import {
  ACCEPT,
  canRetry,
  composerPageThumb,
  localMeta,
  localPreview,
  takeRetry,
  uploadFileId,
  useUploadProgress,
} from "@/lib/managed-agents/attachments";
import { withReport } from "@/lib/caught";
import { cn } from "@/lib/utils";
import { t } from "@/i18n";
import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentDescription,
  AttachmentMedia,
  AttachmentTitle,
  AttachmentTrigger,
} from "@/components/ui/attachment";
import { Progress } from "@/components/ui/progress";
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

/** 点方块看大图。方块的悬停只有 .cc-compose-tile 那一层（变淡），不再叠组件库的底色（V35）。 */
const AttachmentPreviewDialog: FC<PropsWithChildren> = ({ children }) => {
  const src = useAttachmentSrc();

  if (!src) return children;

  return (
    <Dialog>
      <DialogTrigger
        className="aui-attachment-preview-trigger"
        asChild
      >
        {children}
      </DialogTrigger>
      <DialogContent showCloseButton={false} className="aui-attachment-preview-dialog-content p-(--cc-space-8) md:max-w-(--cc-preview-w)">
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
const AttachmentThumb: FC<{ name: string; page?: string }> = ({ name, page }) => {
  const src = useAttachmentSrc() ?? page;
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  if (src && !failed) {
    // PDF 传完：服务端画的第一页小图（同 Claude，上传研究 U1b），从页顶显示；载不出退回文件名 + 类型
    return (
      <img
        src={src}
        alt=""
        draggable={false}
        className="aui-attachment-tile-image"
        data-page={page && src === page ? "" : undefined}
        onError={() => setFailed(true)}
      />
    );
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
  const aui = useAui();
  const name = useAuiState((s) => s.attachment.name);
  const id = useAuiState((s) => s.attachment.id);
  // 上传进度是真数（已传字节 / 整份，lib/frappe.ts 的 XMLHttpRequest）：没传完方块底部一条细进度条，传完等服务端回话时中间转圈
  const progress = useAuiState((s) =>
    s.attachment.status.type === "running" ? (s.attachment.status as { progress?: number }).progress : undefined,
  );

  const uploadState = useAuiState((s) =>
    s.attachment.status.type === "running"
      ? "uploading"
      : s.attachment.status.type === "incomplete" &&
          s.attachment.status.reason === "error"
        ? "error"
        : undefined,
  );
  const isUploading = uploadState === "uploading";
  // 字节还没传完（< 1）：细进度条；= 1 是传完了在等服务端回话：转圈
  const bytesLeft = isUploading && (progress ?? 0) < 1;

  const errorMessage = useAuiState((s) =>
    s.attachment.status.type === "incomplete" &&
    s.attachment.status.reason === "error"
      ? (s.attachment.status.message ?? t("uploadFailed"))
      : undefined,
  );

  // 没传上（断网、服务端拒收）：标准附件卡的出错样子（components/ui/attachment），卡上直接看得出是哪份文件、为什么，
  // 右边 × 移除；不用悬停才看得到（11-12）。输入框里只有上传的状态：识别等点了发送才开始，进度在对话里的上传卡片（11-14）。
  // 断线、超时、服务端出错（能再传）：整张卡点一下重试，图标是重试箭头（上传研究 U5，不加字）；服务端说了原因拒收的只能移除
  if (uploadState === "error") {
    const retry = canRetry(id);
    const again = () => {
      const file = takeRetry(id);
      if (!file) return;
      // 组件库没有「同一项重新上传」：移除这一项、用同一份文件重新放进输入框（重新传、重新出进度）
      void Promise.resolve(aui.attachment().remove())
        .then(() => aui.composer().addAttachment(file))
        .catch(withReport(() => undefined, "attachments"));
    };
    return (
      <AttachmentPrimitive.Root className="aui-attachment-root relative">
        <Attachment state="error" size="sm">
          {retry && <AttachmentTrigger aria-label={`${t("retry")} · ${name}`} title={t("retry")} onClick={again} />}
          <AttachmentMedia>
            {retry ? <RotateCcwIcon /> : <AlertCircleIcon />}
          </AttachmentMedia>
          <AttachmentContent>
            <AttachmentTitle>{name}</AttachmentTitle>
            <AttachmentDescription className="aui-attachment-error-message">{errorMessage}</AttachmentDescription>
          </AttachmentContent>
          <AttachmentActions>
            <AttachmentPrimitive.Remove asChild>
              <AttachmentAction className="aui-attachment-tile-remove" aria-label={t("removeFile")} title={t("removeFile")}>
                <XIcon />
              </AttachmentAction>
            </AttachmentPrimitive.Remove>
          </AttachmentActions>
        </Attachment>
      </AttachmentPrimitive.Root>
    );
  }

  return (
    <Tooltip>
      <AttachmentPrimitive.Root className="aui-attachment-root relative">
        <AttachmentPreviewDialog>
          <TooltipTrigger asChild>
            <div
              className="aui-attachment-tile cc-compose-tile"
              role="button"
              tabIndex={0}
              aria-label={[
                t("attachment").replace("{0}", name),
                isUploading ? (bytesLeft ? `${t("uploading")} ${Math.round((progress ?? 0) * 100)}%` : t("uploading")) : "",
              ]
                .filter(Boolean)
                .join(" · ")}
              data-uploading={isUploading || undefined}
            >
              <AttachmentThumb name={name} page={isUploading ? undefined : composerPageThumb(id)} />
              {isUploading && !bytesLeft && (
                <div
                  aria-hidden="true"
                  className="aui-attachment-tile-uploading bg-app/60 absolute inset-0 flex items-center justify-center"
                >
                  <Loader2Icon className="cc-spinner size-(--cc-icon) text-muted-foreground" />
                </div>
              )}
              {bytesLeft && (
                <span aria-hidden="true" className="cc-compose-progress">
                  <Progress value={(progress ?? 0) * 100} />
                </span>
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
        {loading ? <Skeleton block /> : <AttachmentThumbnail src={image} filename={name} />}
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
export const PendingAttachments: FC<{ names: readonly string[]; ids?: readonly string[] }> = ({ names, ids }) => {
  return (
    <div className="aui-user-message-attachments-end cc-attach-list col-span-full col-start-1 row-start-1">
      {names.map((name, i) => (
        <PendingAttachment key={i} name={name} id={ids?.[i]} />
      ))}
    </div>
  );
};

/** 临时气泡里的一张卡：还在传的接着显示同一份上传进度（输入框里点了发送时没传完的，上传研究 U1 / U4）。 */
const PendingAttachment: FC<{ name: string; id?: string }> = ({ name, id }) => {
  const progress = useUploadProgress(id || undefined);
  const uploading = progress !== undefined && progress < 1;
  return (
    <div className="cc-attach-pending">
      <AttachmentCard
        name={name}
        label={[t("attachment").replace("{0}", name), uploading ? `${t("uploading")} ${Math.round(progress * 100)}%` : ""]
          .filter(Boolean)
          .join(" · ")}
        typeLine={attachmentTypeLine(name)}
        loading
      />
      {uploading && (
        <span aria-hidden="true" className="cc-attach-progress">
          <Progress value={progress * 100} />
        </span>
      )}
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

/** 「+」：开系统的选文件框（只列收的类型 ACCEPT，可多选），选回来的每一份交给输入框；收不收由附件适配器判
 *  （lib/managed-agents/attachments.ts 的 refusalOf：超限、空的、类型不收当场拒收，原因写在输入框上方）。 */
export const ComposerAddAttachment: FC = () => {
  const aui = useAui();
  // 运行时不可用（会话已结束、链接正在打开别的对话）时「+」同输入框一起禁用
  const disabled = useAuiState((s) => !s.composer.isEditing || s.thread.isDisabled);
  const pick = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.hidden = true;
    input.accept = ACCEPT;
    document.body.appendChild(input);
    input.onchange = () => {
      for (const file of input.files ?? []) {
        void aui.composer().addAttachment(file).catch(withReport(() => undefined, "attachments"));
      }
      input.remove();
    };
    input.oncancel = () => input.remove();
    input.click();
  };
  return (
    <button type="button" className="aui-composer-add-attachment cc-attach" aria-label={t("attach")} title={t("attach")} disabled={disabled} onClick={pick}>
      <PlusIcon className="aui-attachment-add-icon stroke-(length:--cc-icon-stroke)" />
    </button>
  );
};
