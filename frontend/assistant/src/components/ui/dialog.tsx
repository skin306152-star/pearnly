// 组件库的弹窗（shadcn）：现在只有图片预览在用（attachment.tsx）。没人用的头部、脚部、说明已删（标准化记录 B2）；
// 默认最宽不再写（唯一的调用方自己给），新弹窗用 globals.css 的 .cc-dialog 一套（data-size 三档宽）。
import type * as React from "react";
import { XIcon } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";
import { t } from "@/i18n";

function Dialog({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Root>) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />;
}

function DialogTrigger({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Trigger>) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />;
}

function DialogPortal({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Portal>) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />;
}

function DialogClose({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Close>) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />;
}

function DialogOverlay({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      data-slot="dialog-overlay"
      className={cn(
        "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=open]:animate-in fixed inset-0 z-(--cc-z-sheet) bg-overlay",
        className,
      )}
      {...props}
    />
  );
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
  showCloseButton?: boolean;
}) {
  return (
    <DialogPortal data-slot="dialog-portal">
      <DialogOverlay />
      <DialogPrimitive.Content
        data-slot="dialog-content"
        className={cn(
          "data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 bg-background data-[state=closed]:animate-out data-[state=open]:animate-in cc-modal fixed start-1/2 z-(--cc-z-sheet) grid overflow-y-auto w-full max-w-[calc(100%-var(--cc-dialog-gutter)*2)] -translate-x-1/2 -translate-y-1/2 gap-(--cc-space-16) rounded-lg border p-(--cc-space-24) shadow-pop duration-200 outline-none rtl:translate-x-1/2",
          className,
        )}
        {...props}
      >
        {children}
        {showCloseButton && (
          // 默认关闭钮同其他弹窗的 ×：图标按钮（电脑 32、图标 20；手机 44）
          <DialogPrimitive.Close data-slot="dialog-close" className="cc-icon-btn cc-tap absolute end-(--cc-space-12) top-(--cc-space-12)">
            <XIcon />
            <span className="sr-only">{t("close")}</span>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPortal>
  );
}

function DialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn("text-lg leading-none font-semibold", className)}
      {...props}
    />
  );
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
  DialogTrigger,
};
